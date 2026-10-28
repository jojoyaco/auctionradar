/* Actual Flask share CSP, synthetic public origins, and an audit-owned loopback receiver. */
'use strict';
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright-core');
const root = path.resolve(__dirname, '..');
const python = process.env.AUCTIONRADAR_TEST_PYTHON || path.join(root, '.venv/bin/python');
const fixture = spawnSync(python, ['-c', `
import json
from tests.test_public_share_images import PublicShareImageTests
case = PublicShareImageTests()
case.setUp()
try:
    token = case._share()
    response = case.app.test_client().get('/share/' + token)
    print(json.dumps({'csp': response.headers['Content-Security-Policy']}))
finally:
    case.tearDown()
`], {cwd: root, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, encoding: 'utf8', timeout: 15000});
assert.equal(fixture.status, 0, fixture.stderr);
const {csp} = JSON.parse(fixture.stdout);
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1cAAAAASUVORK5CYII=', 'base64');
let privateRequests = 0;
const receiver = http.createServer((_request, response) => {
  privateRequests++;
  response.writeHead(200, {'Content-Type': 'image/png'});
  response.end(pixel);
});
(async () => {
  await new Promise(resolve => receiver.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: process.env.AUCTIONRADAR_TEST_CHROME || '/usr/bin/google-chrome',
      headless: true,
      args: process.getuid && process.getuid() === 0 ? ['--no-sandbox'] : [],
    });
    const context = await browser.newContext();
    const origin = 'https://share-fixture.example';
    await context.grantPermissions(['local-network-access'], {origin});
    const page = await context.newPage();
    const violations = [];
    page.on('console', message => {
      if (message.type() === 'error') violations.push(message.text());
    });
    const local = 'http://127.0.0.1:' + receiver.address().port;
    await page.route(origin + '/**', route => {
      if (new URL(route.request().url()).pathname === '/image.png') {
        return route.fulfill({contentType: 'image/png', body: pixel});
      }
      return route.fulfill({
        contentType: 'text/html', headers: {'Content-Security-Policy': csp},
        body: '<!doctype html><img id="same" src="/image.png">' +
          '<img id="approved" src="https://ngb-repositorio.urbaniza.com/ok.png">' +
          '<img src="' + local + '/direct">' +
          '<img src="https://ngb-repositorio.urbaniza.com/redirect.png">',
      });
    });
    await page.route('https://ngb-repositorio.urbaniza.com/**', route => {
      if (route.request().url().endsWith('/redirect.png')) {
        return route.fulfill({status: 302, headers: {Location: local + '/redirect-target'}});
      }
      return route.fulfill({contentType: 'image/png', body: pixel});
    });
    await page.goto(origin + '/');
    await page.waitForFunction(() => ['same', 'approved'].every(id => {
      const image = document.getElementById(id);
      return image.complete && image.naturalWidth > 0;
    }), null, {timeout: 10000});
    assert.equal(privateRequests, 0, 'Neither direct nor redirected private image requests may leave the browser');
    assert.ok(violations.some(message => message.includes('img-src')), 'The actual share CSP must reject unapproved destinations');
    console.log('Public share images: same-origin/approved images load; CSP blocks direct and redirected private requests even with local-network permission');
    await context.close();
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => receiver.close(resolve));
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
