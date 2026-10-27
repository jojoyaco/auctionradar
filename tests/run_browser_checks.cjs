/* Requires playwright-core on NODE_PATH and a local Chrome/Chromium binary.
 * Starts and stops only its own loopback preview and browser processes.
 */
'use strict';
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright-core');

const root = path.resolve(__dirname, '..');
const python = process.env.AUCTIONRADAR_TEST_PYTHON || path.join(root, '.venv/bin/python');
const chrome = process.env.AUCTIONRADAR_TEST_CHROME || '/usr/bin/google-chrome';
const STARTUP_TIMEOUT_MS = 10000;
const SUITE_TIMEOUT_MS = 90000;
const SHUTDOWN_TIMEOUT_MS = 5000;

// --all includes deterministic loopback fixtures; photo_browser_checks.js is
// deliberately excluded because it depends on externally hosted media.
const suites = [
  {
    name: 'private-product', preview: 'product_preview.py', port: 8768,
    args: ['--port', '8768'], checker: 'product_browser_checks.js', fn: 'checkPrivateProductUI',
  },
  {
    name: 'legacy-ui', preview: 'ui_preview.py', port: 8765,
    args: ['--port', '8765'], checker: 'ui_browser_checks.js', fn: 'checkOpportunityUI',
  },
  {
    name: 'grid-scroll', preview: 'grid_scroll_preview.py', port: 8767,
    args: ['--port', '8767'], checker: 'grid_scroll_browser_checks.js', fn: 'checkGridScroll',
  },
  {
    name: 'grid-scroll-race', preview: 'grid_scroll_preview.py', port: 8767,
    args: ['--port', '8767'], checker: 'grid_scroll_race_browser_checks.js', fn: 'checkGridReviewRace',
    loginBeforeCheck: true,
  },
  {
    name: 'detail-next', preview: 'grid_scroll_preview.py', port: 8767,
    args: ['--port', '8767'], checker: 'detail_next_browser_checks.js', fn: 'checkDetailNext',
  },
  {
    name: 'review-detail', preview: 'ui_preview.py', port: 8765,
    args: ['--port', '8765'], checker: 'review_detail_browser_checks.js', fn: 'checkDetailReviewAutosave',
  },
  {
    name: 'saved-searches', preview: 'ui_preview.py', port: 8765,
    args: ['--port', '8765'], checker: 'searches_browser_checks.js', fn: 'checkSavedSearchUI',
  },
  {
    name: 'boe-cached-media', preview: 'boe_media_preview.py', port: 8766,
    args: [], checker: 'boe_media_browser_checks.js', fn: 'checkBoeCachedPhotos',
  },
];

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function withTimeout(promise, timeoutMs, description) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(description + ' timed out after ' + timeoutMs + 'ms')), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

async function assertPortAvailable(port) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', error => {
      reject(new Error('Loopback test port ' + port + ' is unavailable: ' + error.message));
    });
    server.listen({host: '127.0.0.1', port, exclusive: true}, () => {
      server.close(error => error ? reject(error) : resolve());
    });
  });
}

function childExited(child) {
  return child.pid === undefined || child.exitCode !== null || child.signalCode !== null;
}

function waitForChildExit(child, timeoutMs) {
  if (childExited(child)) return Promise.resolve(true);
  return new Promise(resolve => {
    let timer;
    const done = () => {
      clearTimeout(timer);
      child.removeListener('exit', done);
      resolve(true);
    };
    timer = setTimeout(() => {
      child.removeListener('exit', done);
      resolve(false);
    }, timeoutMs);
    child.once('exit', done);
  });
}

async function stopPreview(child) {
  if (!child || childExited(child)) return;
  // Python preview launchers use TemporaryDirectory; SIGINT unwinds their
  // context managers so disposable fixture databases are removed cleanly.
  child.kill('SIGINT');
  if (await waitForChildExit(child, SHUTDOWN_TIMEOUT_MS)) return;
  child.kill('SIGTERM');
  if (await waitForChildExit(child, SHUTDOWN_TIMEOUT_MS)) return;
  child.kill('SIGKILL');
  if (!await waitForChildExit(child, SHUTDOWN_TIMEOUT_MS)) {
    throw new Error('Preview child ' + child.pid + ' remained alive after SIGKILL');
  }
}

async function loginPreviewUser(page, port) {
  await page.goto('http://127.0.0.1:' + port + '/login');
  if (await page.locator('input[name=username]').count()) {
    await page.locator('input[name=username]').fill('preview-user');
    await page.locator('input[name=password]').fill('preview-only-2026');
    await page.locator('button[type=submit]').click();
    await page.locator('[data-nav=oportunidades]').waitFor();
  }
}

async function runSuite(suite) {
  const outcome = {name: suite.name, status: 'failed'};
  const pageErrors = [];
  let stderr = '';
  let child;
  let browser;
  let page;
  let browserStopped = true;
  let previewStopped = true;
  const tempParent = path.resolve(process.env.TMPDIR || os.tmpdir());
  fs.mkdirSync(tempParent, {recursive: true});
  // Keep this path short: Chrome creates Unix-domain sockets beneath TMPDIR.
  const tempDirectory = fs.mkdtempSync(path.join(tempParent, 'ar-'));

  try {
    await assertPortAvailable(suite.port);
    child = spawn(python, ['tests/' + suite.preview, ...suite.args], {
      cwd: root,
      env: {...process.env, PYTHONDONTWRITEBYTECODE: '1', TMPDIR: tempDirectory},
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-5000); });
    let spawnError;
    child.on('error', error => { spawnError = error; });

    let healthy = false;
    const deadline = Date.now() + STARTUP_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (spawnError) throw spawnError;
      if (childExited(child)) throw new Error('Preview exited before becoming healthy: ' + stderr);
      try {
        const response = await fetch('http://127.0.0.1:' + suite.port + '/healthz');
        if (response.ok) { healthy = true; break; }
      } catch {}
      await delay(100);
    }
    if (!healthy) throw new Error('Preview health check timed out: ' + stderr);
    // Do not accept a pre-existing listener if another process won the small
    // gap between the free-port probe and the preview bind.
    await delay(100);
    if (spawnError) throw spawnError;
    if (childExited(child)) throw new Error('Preview exited after health check: ' + stderr);

    browser = await chromium.launch({
      executablePath: chrome,
      env: {...process.env, TMPDIR: tempDirectory},
      headless: true,
      args: process.getuid && process.getuid() === 0 ? ['--no-sandbox'] : [],
    });
    const seenPages = new WeakSet();
    const observePage = candidate => {
      if (seenPages.has(candidate)) return;
      seenPages.add(candidate);
      candidate.on('pageerror', error => pageErrors.push({
        url: candidate.url(),
        error: String(error && error.stack || error),
      }));
    };
    browser.on('context', context => {
      context.on('page', observePage);
      context.pages().forEach(observePage);
    });
    const context = await browser.newContext();
    context.on('page', observePage);
    page = await context.newPage();
    observePage(page);
    page.setDefaultTimeout(15000);
    page.setDefaultNavigationTimeout(20000);

    if (suite.loginBeforeCheck) await loginPreviewUser(page, suite.port);
    const source = fs.readFileSync(path.join(__dirname, suite.checker), 'utf8');
    vm.runInThisContext(source, {filename: suite.checker});
    const check = globalThis[suite.fn];
    if (typeof check !== 'function') throw new Error('Browser check function missing: ' + suite.fn);
    const result = await withTimeout(check(page), SUITE_TIMEOUT_MS, suite.name + ' browser suite');
    await new Promise(resolve => setImmediate(resolve));
    if (pageErrors.length) {
      throw new Error('Unhandled browser pageerror(s): ' + pageErrors.map(entry => entry.error).join('\n'));
    }
    outcome.status = 'passed';
    outcome.result = result;
  } catch (error) {
    outcome.error = String(error && error.stack || error);
  } finally {
    if (browser) {
      try {
        await withTimeout(browser.close(), SHUTDOWN_TIMEOUT_MS, 'Browser shutdown');
        browserStopped = true;
      } catch (error) {
        browserStopped = false;
        outcome.cleanupError = String(error && error.stack || error);
      }
    }
    try {
      await stopPreview(child);
      previewStopped = !child || childExited(child);
    } catch (error) {
      previewStopped = !child || childExited(child);
      outcome.cleanupError = [outcome.cleanupError, String(error && error.stack || error)].filter(Boolean).join('\n');
    }
    if (browserStopped && previewStopped) {
      try {
        // This path was created uniquely by this suite; no caller data is stored here.
        fs.rmSync(tempDirectory, {recursive: true, force: true});
      } catch (error) {
        outcome.cleanupError = [outcome.cleanupError, String(error && error.stack || error)].filter(Boolean).join('\n');
      }
    } else {
      outcome.cleanupError = [outcome.cleanupError, 'Leaving suite temp directory because a child process may still be using it: ' + tempDirectory].filter(Boolean).join('\n');
    }
    if (pageErrors.length) {
      outcome.pageErrors = pageErrors;
      if (outcome.status === 'passed') {
        outcome.status = 'failed';
        outcome.error = 'Unhandled browser pageerror(s): ' + pageErrors.map(entry => entry.error).join('\n');
      }
    } else {
      outcome.pageErrors = [];
    }
    if (stderr && outcome.status !== 'passed') outcome.previewStderr = stderr;
    if (outcome.cleanupError) outcome.status = 'failed';
  }

  return outcome;
}

function selectSuites(args) {
  const known = new Set(['--all', '--legacy', '--help', '-h']);
  const unknown = args.filter(arg => !known.has(arg));
  if (unknown.length) throw new Error('Unknown argument(s): ' + unknown.join(', '));
  if (args.includes('--help') || args.includes('-h')) {
    console.log('Usage: node tests/run_browser_checks.cjs [--legacy | --all]');
    return [];
  }
  if (args.includes('--all') && args.includes('--legacy')) {
    throw new Error('Choose either --all or --legacy.');
  }
  if (args.includes('--all')) return suites;
  if (args.includes('--legacy')) return [suites[1]];
  return [suites[0]];
}

(async () => {
  let selected;
  try {
    selected = selectSuites(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 2;
    return;
  }
  if (!selected.length) return;

  const outcomes = [];
  for (const suite of selected) {
    console.log('Running browser suite: ' + suite.name);
    const outcome = await runSuite(suite);
    outcomes.push(outcome);
    console.log(JSON.stringify(outcome, null, 2));
  }
  const failed = outcomes.filter(outcome => outcome.status !== 'passed');
  if (failed.length) {
    console.error(failed.length + ' of ' + outcomes.length + ' browser suite(s) failed.');
    process.exitCode = 1;
  } else {
    console.log('Passed ' + outcomes.length + ' isolated browser suite(s).');
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
