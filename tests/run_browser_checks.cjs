/* Requires playwright-core on NODE_PATH and a local Chrome/Chromium binary.
 * Starts and stops only its own loopback preview and browser processes.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright-core');
const root = path.resolve(__dirname, '..');
const legacy = process.argv.includes('--legacy');
const python = process.env.AUCTIONRADAR_TEST_PYTHON || path.join(root, '.venv/bin/python');
const port = legacy ? 8765 : 8768;
const child = spawn(python, [legacy ? 'tests/ui_preview.py' : 'tests/product_preview.py', '--port', String(port)], {
  cwd: root, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, stdio: ['ignore', 'ignore', 'pipe'],
});
let errors = '';
child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-5000); });
let spawnError;
child.on('error', error => { spawnError = error; });
(async () => {
  let browser;
  try {
    let healthy = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (spawnError) throw spawnError;
      if (child.exitCode !== null) throw new Error('Preview exited: ' + errors);
      try {
        const response = await fetch('http://127.0.0.1:' + port + '/healthz');
        if (response.ok) { healthy = true; break; }
      } catch {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!healthy) throw new Error('Preview health check timed out');
    browser = await chromium.launch({
      executablePath: process.env.AUCTIONRADAR_TEST_CHROME || '/usr/bin/google-chrome',
      headless: true, args: process.getuid && process.getuid() === 0 ? ['--no-sandbox'] : [],
    });
    const page = await browser.newPage();
    const checker = legacy ? 'ui_browser_checks.js' : 'product_browser_checks.js';
    vm.runInThisContext(fs.readFileSync(path.join(__dirname, checker), 'utf8'), {filename: checker});
    const result = legacy ? await checkOpportunityUI(page) : await checkPrivateProductUI(page);
    console.log(JSON.stringify(result, null, 2));
  } finally {
    if (browser) await browser.close();
    if (child.pid && child.exitCode === null) {
      const stopped = new Promise(resolve => child.once('exit', resolve));
      child.kill('SIGTERM');
      await stopped;
    }
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
