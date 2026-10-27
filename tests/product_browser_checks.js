/* Run against tests/product_preview.py only. All accounts/data are synthetic.
 * The preview binds to loopback and is excluded from the product package.
 */
async function checkPrivateProductUI(page) {
  const base = 'http://127.0.0.1:8768';
  const expect = (condition, message) => { if (!condition) throw new Error(message); };
  const result = {};
  const browser = page.context().browser();
  const contexts = [];
  const password = 'browser-fixture-only-2026';
  const account = async (username) => {
    const context = await browser.newContext();
    contexts.push(context);
    const tab = await context.newPage();
    await tab.addInitScript(() => {
      window.productCSPViolations = [];
      document.addEventListener('securitypolicyviolation', event => window.productCSPViolations.push(event.violatedDirective));
    });
    await tab.goto(base + '/register');
    expect(await tab.locator('html').getAttribute('lang') === 'en', 'New visitors must start in English');
    expect(await tab.locator('h1').innerText() === 'Create account', 'Registration must render in English by default');
    await tab.locator('input[name="username"]').fill(username);
    const passwords = tab.locator('input[type="password"]');
    for (let i = 0; i < await passwords.count(); i++) await passwords.nth(i).fill(password);
    await Promise.all([
      tab.waitForURL(url => url.pathname !== '/register'),
      tab.locator('form button[type="submit"]').click(),
    ]);
    await tab.goto(base + '/');
    if (new URL(tab.url()).pathname === '/login') {
      await tab.locator('input[name="username"]').fill(username);
      await tab.locator('input[name="password"]').fill(password);
      await Promise.all([tab.waitForURL(base + '/'), tab.locator('form button[type="submit"]').click()]);
    }
    expect(await tab.locator('meta[name="workspace-private"]').getAttribute('content') === 'true', 'Private workspace not active');
    return tab;
  };
  const api = (tab, path, method = 'GET', body) => tab.evaluate(async ({path, method, body}) => {
    const headers = {};
    if (method !== 'GET') {
      headers['X-CSRFToken'] = document.querySelector('meta[name="csrf-token"]').content;
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
    return {status: response.status, body: await response.json()};
  }, {path, method, body});
  try {
    const alice = await account('browser_alice');
    const bob = await account('browser_bob');
    await alice.locator('[data-nav="suscripciones"]').click();
    await alice.getByRole('button', {name:'Housing', exact:true}).click();
    const englishPrompt = await alice.locator('#search-editor-prompt').inputValue();
    expect(englishPrompt.startsWith('I am looking'), 'English presets must contain English prompts');
    await alice.evaluate(() => window.AuctionRadarUI.setLanguage('es'));
    expect(await alice.locator('#search-editor-prompt').inputValue() === englishPrompt, 'Language changes must preserve editable drafts');
    await alice.locator('[data-search-editor-close]').click();
    await alice.getByRole('button', {name:'Viviendas', exact:true}).click();
    expect((await alice.locator('#search-editor-prompt').inputValue()).startsWith('Busco'), 'Spanish presets must remain available');
    await alice.locator('[data-search-editor-close]').click();
    await alice.evaluate(() => window.AuctionRadarUI.setLanguage('en'));
    await alice.locator('[data-nav="oportunidades"]').click();
    result.presetLanguage = 'English by default, optional Spanish, editable drafts preserved';
    for (const tab of [alice, bob]) {
      const sources = await api(tab, '/api/sources');
      expect(sources.status === 200 && sources.body.sources.length === 0, 'New account inherited configured sources');
      expect((await api(tab, '/api/users')).status === 403, 'Registration granted global administration');
    }
    result.registration = 'Two independent accounts; empty sources; no administrator privilege';
    const fixtures = [];
    for (const [tab, username] of [[alice, 'browser_alice'], [bob, 'browser_bob']]) {
      const seeded = await api(tab, '/__preview/seed', 'POST', {});
      expect(seeded.status === 200, 'Synthetic preview seed failed');
      fixtures.push(seeded.body.id);
      await tab.reload();
      await tab.locator('.item-card-open').first().waitFor();
      expect((await tab.locator('.item-card-title').first().innerText()).includes(username), 'Another account\'s auction is visible');
      await tab.locator('.item-card-open').first().click();
      await tab.locator('#review-notes').fill('PRIVATE-NOTE-' + username);
      const saved = tab.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/review'));
      await tab.locator('[data-save-review]').click();
      expect((await saved).status() === 200, 'Private notes could not be saved');
    }
    expect(fixtures[0] === fixtures[1], 'Preview should exercise identical IDs in different workspaces');
    const itemPath = '/api/items/' + fixtures[0];
    const batches = await Promise.all([alice, bob].map(async (tab, index) => {
      const user = index === 0 ? 'browser_alice' : 'browser_bob';
      for (let n = 0; n < 15; n++) {
        const detail = await api(tab, itemPath);
        expect(detail.status === 200 && detail.body.item.title.includes(user), 'Concurrent detail crossed workspace boundary');
        expect(detail.body.item.review.notes === 'PRIVATE-NOTE-' + user, 'Notes crossed workspace boundary');
      }
      return true;
    }));
    result.isolation = {sameItemIds: true, concurrentAccounts: batches.length, privateNotes: true};
    const aliceShare = await api(alice, itemPath + '/share', 'POST', {});
    const bobShare = await api(bob, itemPath + '/share', 'POST', {});
    expect(aliceShare.status === 200 && bobShare.status === 200, 'Sharing is not available');
    const guestContext = await browser.newContext();
    contexts.push(guestContext);
    const guest = await guestContext.newPage();
    const publicPath = '/api/public/' + encodeURIComponent(aliceShare.body.opaque);
    await guest.goto(base + '/share/' + encodeURIComponent(aliceShare.body.opaque));
    await guest.locator('[data-share-title]').waitFor();
    const publicItem = await api(guest, publicPath);
    expect(publicItem.status === 200, 'Anonymous shared auction is unavailable');
    const encoded = JSON.stringify(publicItem.body);
    expect(!encoded.includes('PRIVATE-NOTE-') && !encoded.includes('INTERNAL-FIXTURE-'), 'Sharing leaked private data');
    expect(!('review' in publicItem.body.item), 'Sharing exposed review metadata');
    // A signed-in visitor following someone else's link remains in their own
    // workspace on the next authenticated request.
    const bobPublic = await bob.context().newPage();
    await bobPublic.goto(base + '/share/' + encodeURIComponent(aliceShare.body.opaque));
    const bobAfter = await api(bob, itemPath);
    expect(bobAfter.body.item.title.includes('browser_bob'), 'Shared link changed authenticated workspace');
    expect((await api(bob, '/api/shares/' + bobShare.body.id + '/revoke', 'POST', {})).status === 200, 'Own share revocation failed');
    expect((await api(guest, publicPath)).status === 200, 'Same numeric share ID affected another workspace');
    expect((await api(alice, '/api/shares/' + aliceShare.body.id + '/revoke', 'POST', {})).status === 200, 'Owner revocation failed');
    expect((await api(guest, publicPath)).status === 404, 'Revoked public link still works');
    result.sharing = 'Anonymous readonly view, no private notes, independent revocation, safe signed-in visitor';
    await alice.goto(base + '/connections');
    await alice.locator('h1').waitFor();
    await alice.waitForFunction(() => getComputedStyle(document.querySelector('.connections-page')).maxWidth === '960px');
    expect((await alice.evaluate(() => window.productCSPViolations)).length === 0, 'Connections page violates its CSP');
    await alice.locator('#add-source').click();
    await alice.locator('#source-id').fill('browser_portal');
    await alice.locator('#source-name').fill('Synthetic public source');
    await alice.locator('#source-url').fill('https://example.org/auctions');
    await alice.locator('#source-types').selectOption(['housing']);
    const savedSource = alice.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/connections/sources'));
    await alice.locator('#source-form button[type="submit"]').click();
    expect((await savedSource).status() === 201, 'Source onboarding form failed');
    const configured = await api(alice, '/api/sources');
    expect(configured.body.sources.length === 1 && !configured.body.sources[0].enabled, 'New source must stay disabled until enabled');
    expect((await api(bob, '/api/sources')).body.sources.length === 0, 'A source leaked to another account');
    result.sources = 'Real onboarding form saves a disabled source in the correct workspace';
    result.connectionsLayout = [];
    for (const language of ['es', 'en']) {
      await alice.evaluate(value => window.AuctionRadarUI.setLanguage(value), language);
      for (const width of [320, 390, 1440]) {
        await alice.setViewportSize({width, height: 1000});
        const fits = await alice.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
        expect(fits, 'Connections page overflows at ' + width + ' in ' + language);
        result.connectionsLayout.push({language, width, fits});
      }
    }
    return result;
  } finally {
    for (const context of contexts) await context.close();
  }
}
