/* Record a synthetic-only walkthrough against the real loopback Flask app.
 * Requires playwright-core, Chromium, and FFmpeg; emits only media/evidence.
 */
'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {execFileSync, spawn} = require('node:child_process');
const {chromium} = require('playwright-core');

const ROOT = path.resolve(__dirname, '..');
const VIEWPORT = {width: 1440, height: 900};
const STARTUP_TIMEOUT_MS = 15000;
const STOP_TIMEOUT_MS = 5000;
const MIN_VIDEO_DURATION_SECONDS = 20;
const MAX_VIDEO_DURATION_SECONDS = 35;

function optionsFromArgs(args) {
  const options = {};
  for (let index = 0; index < args.length; index += 1) {
    const name = args[index];
    if (!name.startsWith('--') || !args[index + 1]) {
      throw new Error('Expected --name value arguments.');
    }
    options[name.slice(2)] = args[index + 1];
    index += 1;
  }
  const defaults = {
    'work-root': path.join(os.tmpdir(), 'opencode/auctionradar-demo'),
    'media-dir': path.join(ROOT, 'docs/media'),
    python: path.join(ROOT, '.venv/bin/python'),
    chrome: '/usr/bin/google-chrome',
    ffmpeg: 'ffmpeg',
  };
  for (const [key, value] of Object.entries(defaults)) {
    if (!options[key]) options[key] = value;
  }
  if (!options['evidence-dir']) options['evidence-dir'] = path.join(options['work-root'], 'evidence');
  return options;
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function childExited(child) {
  return !child || child.exitCode !== null || child.signalCode !== null;
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
  child.kill('SIGINT');
  if (await waitForChildExit(child, STOP_TIMEOUT_MS)) return;
  child.kill('SIGTERM');
  if (await waitForChildExit(child, STOP_TIMEOUT_MS)) return;
  child.kill('SIGKILL');
  if (!await waitForChildExit(child, STOP_TIMEOUT_MS)) {
    throw new Error('The owned preview process remained alive after SIGKILL.');
  }
}

async function unusedLoopbackPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen({host: '127.0.0.1', port: 0, exclusive: true}, () => {
      const address = server.address();
      server.close(error => error ? reject(error) : resolve(address.port));
    });
  });
}

function cleanPreviewEnvironment() {
  const env = {...process.env};
  for (const name of Object.keys(env)) {
    if (name.startsWith('AUCTIONRADAR_') || name === 'CREDENTIALS_DIRECTORY') {
      delete env[name];
    }
  }
  env.PYTHONDONTWRITEBYTECODE = '1';
  return env;
}

function safeRequestPath(requestUrl) {
  const parsed = new URL(requestUrl);
  return parsed.pathname;
}

function isLoopbackUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
  const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  return host === 'localhost' || host.endsWith('.localhost') ||
    host === '::1' || (net.isIP(host) === 4 && host.startsWith('127.'));
}

async function attachNetworkGuard(context, audit, stage) {
  await context.route('**/*', async route => {
    const request = route.request();
    const url = request.url();
    const entry = {stage, method: request.method(), path: safeRequestPath(url)};
    audit.requests.push(entry);
    if (!isLoopbackUrl(url)) {
      const external = new URL(url);
      audit.non_loopback_requests.push({
        stage,
        method: request.method(),
        host: external.hostname,
        path: external.pathname,
      });
      await route.abort('blockedbyclient');
      return;
    }
    try {
      await route.continue();
    } catch (error) {
      audit.route_errors.push({stage, path: entry.path, error: String(error.message || error)});
      throw error;
    }
  });
  context.on('page', page => attachPageAudit(page, audit, stage));
}

function attachPageAudit(page, audit, stage) {
  page.on('pageerror', error => {
    audit.page_errors.push({stage, error: String(error && error.message || error)});
  });
  page.on('response', response => {
    audit.responses.push({
      stage,
      method: response.request().method(),
      path: safeRequestPath(response.url()),
      status: response.status(),
    });
  });
}

async function waitForHealthy(child, port, getStderr) {
  const url = `http://127.0.0.1:${port}/healthz`;
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (childExited(child)) {
      throw new Error('The loopback preview exited before health check: ' + getStderr());
    }
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await delay(100);
  }
  throw new Error('The loopback preview health check timed out: ' + getStderr());
}

async function expectResponse(page, pathName, action, audit) {
  const responsePromise = page.waitForResponse(response => {
    try {
      return new URL(response.url()).pathname === pathName && response.request().method() === 'POST';
    } catch {
      return false;
    }
  });
  await action();
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(error.message + '; recent responses=' + JSON.stringify(audit.responses.slice(-20)));
  }
}

function pause(ms) {
  return delay(ms);
}

function runFfmpeg(ffmpeg, args) {
  return execFileSync(ffmpeg, args, {
    cwd: ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    maxBuffer: 64 * 1024 * 1024,
  });
}

function ffmpegInfo(ffmpeg, filename) {
  const result = spawn(ffmpeg, ['-hide_banner', '-i', filename, '-map', '0:v:0', '-f', 'null', '-'], {
    cwd: ROOT,
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  let stderr = '';
  result.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-12000); });
  return new Promise((resolve, reject) => {
    result.once('error', reject);
    result.once('close', code => {
      if (code !== 0) return reject(new Error('FFmpeg media validation failed: ' + stderr));
      const durationText = (stderr.match(/Duration: (\d{2}:\d{2}:\d{2}\.\d+)/) || [])[1];
      const videoLine = (stderr.match(/Video: ([^\n]+)/) || [])[1] || '';
      const dimensions = (videoLine.match(/(\d{3,4})x(\d{3,4})/) || []).slice(1).map(Number);
      const rateText = (videoLine.match(/, (\d+(?:\.\d+)?) fps/) || [])[1];
      if (!durationText || dimensions.length !== 2) {
        return reject(new Error('FFmpeg could not report duration/dimensions: ' + stderr));
      }
      const [hours, minutes, seconds] = durationText.split(':').map(Number);
      resolve({
        codec: (videoLine.match(/^([^,]+)/) || [])[1].trim(),
        width: dimensions[0],
        height: dimensions[1],
        fps: rateText ? Number(rateText) : null,
        duration_seconds: hours * 3600 + minutes * 60 + seconds,
      });
    });
  });
}

function decodedFrameCount(ffmpeg, filename) {
  const output = runFfmpeg(ffmpeg, [
    '-v', 'error', '-i', filename, '-map', '0:v:0', '-f', 'framemd5', '-',
  ]);
  return output.split(/\r?\n/).filter(line => line && !line.startsWith('#')).length;
}

function pngDimensions(filename) {
  const buffer = fs.readFileSync(filename);
  if (buffer.toString('hex', 0, 8) !== '89504e470d0a1a0a') {
    throw new Error('Screenshot is not a PNG: ' + filename);
  }
  return {width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20)};
}

async function assertGrid(page, expectedCount) {
  try {
    await page.waitForFunction(
      count => document.querySelectorAll('.cards-grid .item-card').length === count,
      expectedCount,
      {timeout: 15000},
    );
  } catch (error) {
    const state = await page.evaluate(() => ({
      path: location.pathname,
      count: document.querySelectorAll('.cards-grid .item-card').length,
      list_text: (document.querySelector('[data-list-region]')?.innerText || '').slice(0, 300),
    }));
    throw new Error(error.message + '; grid state=' + JSON.stringify(state));
  }
  await page.waitForFunction(() => {
    const images = [...document.querySelectorAll('.cards-grid .item-card img')].slice(0, 3);
    return images.length >= 3 && images.every(image => image.complete && image.naturalWidth > 0);
  }, null, {timeout: 15000});
}

async function run() {
  const options = optionsFromArgs(process.argv.slice(2));
  const workRoot = path.resolve(options['work-root']);
  const mediaDir = path.resolve(options['media-dir']);
  const evidenceDir = path.resolve(options['evidence-dir']);
  fs.mkdirSync(workRoot, {recursive: true});
  fs.mkdirSync(mediaDir, {recursive: true});
  fs.mkdirSync(evidenceDir, {recursive: true});
  const runDir = fs.mkdtempSync(path.join(workRoot, 'run-'));
  const dataDir = path.join(runDir, 'data');
  const rawDir = path.join(runDir, 'raw-video');
  const stagingDir = path.join(runDir, 'staged-media');
  const sampleDir = path.join(evidenceDir, 'sample-frames');
  fs.mkdirSync(rawDir);
  fs.mkdirSync(stagingDir);
  fs.mkdirSync(sampleDir, {recursive: true});

  const audit = {
    requests: [],
    responses: [],
    non_loopback_requests: [],
    route_errors: [],
    page_errors: [],
  };
  let preview;
  let browser;
  let bootstrapContext;
  let captureContext;
  let previewStopped = true;
  let browserStopped = true;
  let previewStderr = '';
  let videoPath;
  let browserTmpAlias;
  const priorTmpDir = process.env.TMPDIR;
  let mediaCommitted = false;
  let result;

  try {
    const port = await unusedLoopbackPort();
    preview = spawn(options.python, [
      path.join(ROOT, 'tests/demo_preview.py'),
      '--port', String(port),
      '--data-dir', dataDir,
    ], {
      cwd: ROOT,
      env: {...cleanPreviewEnvironment(), TMPDIR: runDir},
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    preview.stderr.on('data', chunk => {
      previewStderr = (previewStderr + chunk.toString()).slice(-12000);
    });
    await waitForHealthy(preview, port, () => previewStderr);
    const baseUrl = `http://127.0.0.1:${port}`;

    browserTmpAlias = path.join(os.tmpdir(), 'opencode', `ar-${process.pid}`);
    fs.mkdirSync(path.dirname(browserTmpAlias), {recursive: true});
    fs.symlinkSync(runDir, browserTmpAlias, 'dir');
    process.env.TMPDIR = browserTmpAlias;
    const browserEnv = cleanPreviewEnvironment();
    browserEnv.TMPDIR = browserTmpAlias;
    browser = await chromium.launch({
      executablePath: options.chrome,
      env: browserEnv,
      headless: true,
      args: typeof process.getuid === 'function' && process.getuid() === 0
        ? ['--no-sandbox', '--disable-dev-shm-usage']
        : [],
    });

    bootstrapContext = await browser.newContext({
      viewport: VIEWPORT,
      serviceWorkers: 'block',
    });
    await attachNetworkGuard(bootstrapContext, audit, 'bootstrap');
    const bootstrapPage = await bootstrapContext.newPage();
    bootstrapPage.setDefaultTimeout(15000);
    await bootstrapPage.goto(`${baseUrl}/register`, {waitUntil: 'domcontentloaded'});
    let temporaryPassword = crypto.randomBytes(32).toString('base64url');
    await bootstrapPage.locator('#username').fill('synthetic-reviewer');
    await bootstrapPage.locator('#password').fill(temporaryPassword);
    await bootstrapPage.locator('button[type="submit"]').click();
    await bootstrapPage.locator('[data-user-name]').waitFor({state: 'visible'});
    const sessionCookies = await bootstrapContext.cookies(baseUrl);
    if (!sessionCookies.length) throw new Error('Local demo registration produced no session cookie.');

    const seedResult = await bootstrapPage.evaluate(async () => {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
      const response = await fetch('/__demo/seed', {
        method: 'POST',
        headers: {'X-CSRFToken': csrf},
      });
      return {status: response.status, body: await response.json()};
    });
    if (seedResult.status !== 200 || seedResult.body.ingested_count !== 5 ||
        seedResult.body.source_id !== 'synthetic-demo') {
      throw new Error('Synthetic fixture ingestion failed: ' + JSON.stringify(seedResult.body));
    }

    const jobGuard = await bootstrapPage.evaluate(async () => {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
      const response = await fetch('/api/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
        body: '{}',
      });
      return {status: response.status, body: await response.json()};
    });
    if (jobGuard.status !== 409 || jobGuard.body.error !== 'demo_jobs_disabled') {
      throw new Error('The demo preview did not block a queued search job.');
    }
    temporaryPassword = null;
    await bootstrapContext.close();
    bootstrapContext = null;

    const captureDir = path.join(runDir, 'capture');
    fs.mkdirSync(captureDir);
    captureContext = await browser.newContext({
      viewport: VIEWPORT,
      recordVideo: {dir: rawDir, size: VIEWPORT, fps: 25},
      serviceWorkers: 'block',
      locale: 'en-GB',
      timezoneId: 'Europe/Madrid',
    });
    await attachNetworkGuard(captureContext, audit, 'recording');
    await captureContext.addCookies(sessionCookies);
    await captureContext.addInitScript(() => {
      if (location.protocol !== 'http:' && location.protocol !== 'https:') return;
      try {
        localStorage.setItem('auctionradar.language', 'en');
        localStorage.setItem('auctionradar.theme', 'light');
      } catch {}
    });
    const page = await captureContext.newPage();
    page.setDefaultTimeout(15000);
    page.setDefaultNavigationTimeout(20000);
    const recordingStartedAt = Date.now();
    await page.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await assertGrid(page, seedResult.body.ingested_count);
    const demoItem = seedResult.body.items.find(item => item.fixture_id === 'demo-casa-patio-lumen');
    if (!demoItem) throw new Error('The primary synthetic fixture ID is missing.');
    const title = 'DEMO · Vivienda patio en Puerto Lumen';
    const firstCard = page.locator(`.item-card[data-item-id="${demoItem.item_id}"]`);
    if (await firstCard.locator('.item-card-title').innerText() !== title) {
      throw new Error('The real app UI did not render the expected synthetic listing title.');
    }
    await pause(1800);

    const languageSelect = page.locator('[data-language-select]');
    await languageSelect.selectOption('es');
    await page.waitForFunction(() => document.documentElement.lang === 'es');
    if (await firstCard.locator('.item-card-title').innerText() !== title) {
      throw new Error('The language switch altered the original synthetic listing title.');
    }
    await pause(1500);
    await languageSelect.selectOption('en');
    await page.waitForFunction(() => document.documentElement.lang === 'en');

    await page.locator('[data-theme-toggle]').click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await pause(1200);
    await page.mouse.move(1418, 870);
    const darkScreenshot = path.join(stagingDir, 'auctionradar-dark.png');
    await page.screenshot({path: darkScreenshot, type: 'png', animations: 'disabled', caret: 'hide'});
    await page.locator('[data-theme-toggle]').click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await pause(900);
    const lightScreenshot = path.join(stagingDir, 'auctionradar-overview.png');
    await page.screenshot({path: lightScreenshot, type: 'png', animations: 'disabled', caret: 'hide'});

    await firstCard.locator('[data-item-open]').click();
    await page.locator('#item-drawer[aria-hidden="false"] [data-drawer-title]').waitFor({state: 'visible'});
    await page.locator('#item-drawer .gallery-thumb').first().waitFor({state: 'visible'});
    await pause(1600);
    await page.locator('#item-drawer .gallery-thumb').first().click();
    await page.locator('[data-lightbox]:not([hidden])').waitFor({state: 'visible'});
    const galleryCaption = (await page.locator('[data-lightbox-caption]').innerText()).trim();
    if (!galleryCaption.includes('Ilustración sintética') ||
        !galleryCaption.includes('no es una fotografía')) {
      throw new Error('The detail gallery does not visibly identify its synthetic illustration.');
    }
    await pause(1600);
    await page.locator('[data-lightbox-close]').click();
    await page.locator('[data-lightbox]').waitFor({state: 'hidden'});

    const reviewPath = `/api/items/${encodeURIComponent(demoItem.item_id)}/review`;
    const note = 'Synthetic demo review: verify fictional possession and sample figures.';
    await page.locator('#review-notes').fill(note);
    const noteResponse = await expectResponse(page, reviewPath, () =>
      page.locator('#item-drawer [data-save-review]').click(), audit);
    if (!noteResponse.ok()) {
      throw new Error('The real app review-note API returned HTTP ' + noteResponse.status());
    }
    const notePayload = await noteResponse.json();
    if (notePayload.item.review.status !== 'pending' || notePayload.item.review.notes !== note) {
      throw new Error('The real app did not persist the synthetic review note.');
    }

    const decisionResponse = await expectResponse(page, reviewPath, () =>
      page.locator(`#item-drawer [data-review-status="ok"]`).click(), audit);
    if (!decisionResponse.ok()) {
      throw new Error('The real app review-decision API returned HTTP ' + decisionResponse.status());
    }
    const decisionPayload = await decisionResponse.json();
    if (decisionPayload.item.review.status !== 'ok' || decisionPayload.item.review.notes !== note) {
      throw new Error('The real app did not persist both the synthetic note and decision.');
    }
    await pause(1500);

    await page.reload({waitUntil: 'domcontentloaded'});
    await page.locator('[data-status-value="all"]').click();
    await assertGrid(page, seedResult.body.ingested_count);
    const itemsPayload = await page.evaluate(async () => {
      const response = await fetch('/api/items?status=all');
      return {status: response.status, body: await response.json()};
    });
    if (itemsPayload.status !== 200) {
      throw new Error('The reloaded app could not read its item list.');
    }
    const persisted = itemsPayload.body.items.find(item => item.id === demoItem.item_id);
    if (!persisted || persisted.review.status !== 'ok' || persisted.review.notes !== note) {
      throw new Error('Review decision/note were not present in the real API after reload: ' +
        JSON.stringify(persisted ? {
          id: persisted.id,
          status: persisted.review && persisted.review.status,
          note_length: (persisted.review && persisted.review.notes || '').length,
        } : {item_found: false}));
    }
    if (await page.locator(`.item-card[data-item-id="${demoItem.item_id}"]`)
        .getAttribute('data-review-status') !== 'ok') {
      throw new Error('The reloaded UI did not render the persisted review decision.');
    }
    await page.locator(`.item-card[data-item-id="${demoItem.item_id}"] [data-item-open]`).click();
    await page.locator('#item-drawer[aria-hidden="false"] #review-notes').waitFor({state: 'visible'});
    if (await page.locator('#item-drawer #review-notes').inputValue() !== note) {
      throw new Error('The reloaded detail UI did not render the persisted synthetic note.');
    }
    await pause(1300);

    const elapsed = Date.now() - recordingStartedAt;
    if (elapsed < MIN_VIDEO_DURATION_SECONDS * 1000) {
      await pause(MIN_VIDEO_DURATION_SECONDS * 1000 - elapsed + 250);
    }
    if (audit.non_loopback_requests.length || audit.route_errors.length || audit.page_errors.length) {
      throw new Error('Network/page safety audit failed: ' + JSON.stringify({
        non_loopback_requests: audit.non_loopback_requests,
        route_errors: audit.route_errors,
        page_errors: audit.page_errors,
      }));
    }

    const video = page.video();
    await captureContext.close();
    captureContext = null;
    videoPath = await video.path();
    await browser.close();
    browser = null;
    browserStopped = true;

    const stagedVideo = path.join(stagingDir, 'auctionradar-demo.mp4');
    runFfmpeg(options.ffmpeg, [
      '-y', '-hide_banner', '-loglevel', 'error', '-i', videoPath,
      '-an', '-vf', 'scale=1440:900:flags=lanczos', '-r', '25',
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '22', '-pix_fmt', 'yuv420p',
      '-movflags', '+faststart', stagedVideo,
    ]);

    const stagedGif = path.join(stagingDir, 'auctionradar-demo.gif');
    const gifFilter = 'fps=8,scale=960:600:flags=lanczos,split[a][b];' +
      '[a]palettegen=max_colors=128:stats_mode=diff[p];' +
      '[b][p]paletteuse=dither=none:diff_mode=rectangle';
    runFfmpeg(options.ffmpeg, [
      '-y', '-hide_banner', '-loglevel', 'error', '-i', stagedVideo,
      '-filter_complex', gifFilter, '-loop', '0', stagedGif,
    ]);
    if (fs.statSync(stagedGif).size > 5 * 1024 * 1024) {
      const smallerGifFilter = 'fps=7,scale=960:600:flags=lanczos,split[a][b];' +
        '[a]palettegen=max_colors=96:stats_mode=diff[p];' +
        '[b][p]paletteuse=dither=none:diff_mode=rectangle';
      runFfmpeg(options.ffmpeg, [
        '-y', '-hide_banner', '-loglevel', 'error', '-i', stagedVideo,
        '-filter_complex', smallerGifFilter, '-loop', '0', stagedGif,
      ]);
    }
    if (fs.statSync(stagedGif).size > 5 * 1024 * 1024) {
      throw new Error('GIF exceeds the 5 MiB asset ceiling after palette optimization.');
    }

    const mp4Info = await ffmpegInfo(options.ffmpeg, stagedVideo);
    const gifInfo = await ffmpegInfo(options.ffmpeg, stagedGif);
    const mp4Frames = decodedFrameCount(options.ffmpeg, stagedVideo);
    const gifFrames = decodedFrameCount(options.ffmpeg, stagedGif);
    if (mp4Info.width !== 1440 || mp4Info.height !== 900 ||
        mp4Info.codec.toLowerCase().indexOf('h264') < 0 ||
        mp4Info.duration_seconds < MIN_VIDEO_DURATION_SECONDS ||
        mp4Info.duration_seconds > MAX_VIDEO_DURATION_SECONDS) {
      throw new Error('MP4 codec/dimension/duration validation failed: ' + JSON.stringify(mp4Info));
    }
    if (gifInfo.width !== 960 || gifInfo.height !== 600 || gifFrames < 2) {
      throw new Error('GIF dimensions/frame validation failed: ' + JSON.stringify(gifInfo));
    }
    for (const [filename, asset] of [
      [lightScreenshot, 'auctionradar-overview.png'],
      [darkScreenshot, 'auctionradar-dark.png'],
    ]) {
      const dimensions = pngDimensions(filename);
      if (dimensions.width !== VIEWPORT.width || dimensions.height !== VIEWPORT.height) {
        throw new Error(asset + ' screenshot has unexpected dimensions.');
      }
    }

    const sampleTimes = [1, 5, 9, 14, 19, Math.max(1, Math.floor(mp4Info.duration_seconds - 1))];
    for (let index = 0; index < sampleTimes.length; index += 1) {
      const filename = path.join(sampleDir, `demo-frame-${String(index + 1).padStart(2, '0')}.png`);
      runFfmpeg(options.ffmpeg, [
        '-y', '-hide_banner', '-loglevel', 'error', '-ss', String(sampleTimes[index]),
        '-i', stagedVideo, '-frames:v', '1', '-f', 'image2', filename,
      ]);
    }

    const mediaFiles = [
      ['auctionradar-demo.mp4', stagedVideo],
      ['auctionradar-demo.gif', stagedGif],
      ['auctionradar-overview.png', lightScreenshot],
      ['auctionradar-dark.png', darkScreenshot],
    ];
    for (const [name, staged] of mediaFiles) {
      fs.copyFileSync(staged, path.join(mediaDir, name));
    }
    mediaCommitted = true;

    const media = {};
    for (const [name] of mediaFiles) {
      const filename = path.join(mediaDir, name);
      media[name] = {
        bytes: fs.statSync(filename).size,
        sha256: crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex'),
      };
    }
    result = {
      source_sha: execFileSync('git', ['rev-parse', 'HEAD'], {cwd: ROOT, encoding: 'utf8'}).trim(),
      captured_at: new Date().toISOString(),
      fixture_source_id: seedResult.body.source_id,
      fixture_ids: seedResult.body.items,
      fixture_ingested_count: seedResult.body.ingested_count,
      job_guard: {path: '/api/runs', status: jobGuard.status, blocked: true},
      api_writes: [
        {
          path: reviewPath,
          method: 'POST',
          status: noteResponse.status(),
          decision: notePayload.item.review.status,
          note_saved: true,
          note_sha256: crypto.createHash('sha256').update(note).digest('hex'),
        },
        {
          path: reviewPath,
          method: 'POST',
          status: decisionResponse.status(),
          decision: decisionPayload.item.review.status,
          note_verified_after_reload: true,
        },
      ],
      languages_verified: ['en', 'es'],
      themes_verified: ['light', 'dark'],
      network: {
        non_loopback_requests: audit.non_loopback_requests,
        route_errors: audit.route_errors,
        page_errors: audit.page_errors,
        request_count: audit.requests.length,
        requests: audit.requests,
        responses: audit.responses,
      },
      media: {
        mp4: {...mp4Info, frames: mp4Frames, audio: false, ...media['auctionradar-demo.mp4']},
        gif: {...gifInfo, frames: gifFrames, loop: true, ...media['auctionradar-demo.gif']},
        light_screenshot: {
          ...pngDimensions(path.join(mediaDir, 'auctionradar-overview.png')),
          theme: 'light',
          ...media['auctionradar-overview.png'],
        },
        dark_screenshot: {
          ...pngDimensions(path.join(mediaDir, 'auctionradar-dark.png')),
          theme: 'dark',
          ...media['auctionradar-dark.png'],
        },
      },
    };
    fs.writeFileSync(path.join(evidenceDir, 'capture-evidence.json'),
      JSON.stringify(result, null, 2) + '\n', {mode: 0o600});
    fs.copyFileSync(path.join(mediaDir, 'auctionradar-overview.png'),
      path.join(evidenceDir, 'light-grid.png'));
    fs.copyFileSync(path.join(mediaDir, 'auctionradar-dark.png'),
      path.join(evidenceDir, 'dark-grid.png'));
    console.log(JSON.stringify(result, null, 2));
  } finally {
    if (captureContext) {
      try {
        await captureContext.close();
      } catch {}
    }
    if (bootstrapContext) {
      try {
        await bootstrapContext.close();
      } catch {}
    }
    if (browser) {
      try {
        await browser.close();
      } catch {
        browserStopped = false;
      }
    }
    try {
      await stopPreview(preview);
      previewStopped = childExited(preview);
    } catch {
      previewStopped = childExited(preview);
    }
    if (browserStopped && previewStopped) {
      if (browserTmpAlias && fs.existsSync(browserTmpAlias)) fs.unlinkSync(browserTmpAlias);
      if (priorTmpDir === undefined) delete process.env.TMPDIR;
      else process.env.TMPDIR = priorTmpDir;
      fs.rmSync(runDir, {recursive: true, force: true});
    } else {
      console.error('Preserving the owned capture temp directory while a process may still use it: ' + runDir);
    }
    if (!mediaCommitted && result) {
      console.error('Capture evidence exists, but all four media files were not installed.');
    }
  }
}

run().catch(error => {
  console.error(error && error.stack || error);
  process.exitCode = 1;
});
