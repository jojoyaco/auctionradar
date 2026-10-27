/* Run with browser_run_code_unsafe(filename) and tests/ui_preview.py on :8765.
 * Test data and credentials only; never production state. No dependencies.
 */
async function checkOpportunityUI(page) {
  const expect = (condition, message) => { if (!condition) throw new Error(message); };
  const result = {};
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://127.0.0.1:8765/login');
  await page.evaluate(() => {
    window.AuctionRadarUI.setLanguage('es');
    window.AuctionRadarUI.setTheme('light');
  });
  if (!await page.locator('input[name="username"]').count()) {
    await page.locator('[data-action="logout"]').click();
    await page.waitForURL('**/login');
  }
  await page.locator('[data-language-select]').selectOption('en');
  expect(await page.locator('html').getAttribute('lang') === 'en', 'Login language must switch to English');
  expect(!/Entrar|Iniciar sesión/.test(await page.locator('h1').innerText()), 'English login heading is not translated');
  await page.locator('[data-theme-toggle]').click();
  expect(await page.locator('html').getAttribute('data-theme') === 'dark', 'Theme toggle must apply dark mode');
  await page.reload();
  expect(await page.locator('html').getAttribute('data-theme') === 'dark', 'Dark mode must survive reload');
  expect(await page.locator('html').getAttribute('lang') === 'en', 'Language must survive reload');
  await page.locator('input[name="username"]').fill('preview-user');
  await page.locator('input[name="password"]').fill('preview-only-2026');
  await page.locator('form button[type="submit"]').click();
  await page.locator('.item-card-open').first().waitFor();
  expect(await page.locator('[data-view="oportunidades"] h2').innerText() === 'Opportunities', 'App heading must be English');
  expect(await page.locator('html').getAttribute('data-theme') === 'dark', 'Theme must survive login redirect');
  result.preferences = 'EN and dark mode persist through reload and login';

  const allItems = page.waitForResponse(r => r.url().includes('/api/items?') && r.url().includes('status=all'));
  await page.locator('[data-filter="status"] [data-status-value="all"]').click();
  await allItems;
  await page.locator('.item-card').nth(2).waitFor();
  result.layout = [];
  for (const language of ['en', 'es']) {
    await page.evaluate(value => window.AuctionRadarUI.setLanguage(value), language);
    for (const width of [320, 390, 768, 1200, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      const layout = await page.evaluate(() => {
        const group = document.querySelector('[data-filter="status"]');
        const buttons = Array.from(group.querySelectorAll('button'));
        const first = buttons[0].getBoundingClientRect();
        return {
          width: innerWidth,
          right: group.getBoundingClientRect().right,
          oneRow: buttons.every(b => Math.abs(b.getBoundingClientRect().top - first.top) < 2),
          labelsFit: buttons.every(b => b.scrollWidth <= b.clientWidth + 1),
          cardsFit: Array.from(document.querySelectorAll('.item-card')).every(c => c.getBoundingClientRect().right <= innerWidth + 1),
        };
      });
      expect(layout.right <= width && layout.oneRow && layout.labelsFit && layout.cardsFit,
        `Status or cards overflow at ${width}px in ${language}: ${JSON.stringify(layout)}`);
      result.layout.push({ language, width, passed: true });
    }
  }
  const alignment = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('.item-card')).slice(0, 3);
    const rows = cards.map(card => {
      const outer = card.getBoundingClientRect();
      const photo = card.querySelector('.item-card-photo').getBoundingClientRect();
      return {
        photoLeft: photo.left - outer.left,
        photoRight: outer.right - photo.right,
        title: card.querySelector('.item-card-title').getBoundingClientRect().top,
        price: card.querySelector('.item-card-price').getBoundingClientRect().top,
        footer: card.querySelector('.item-card-footer').getBoundingClientRect().top,
        nativePadding: getComputedStyle(card.querySelector('.item-card-open')).padding,
      };
    });
    return rows;
  });
  expect(alignment.every(c => c.photoLeft <= 1.1 && c.photoRight <= 1.1 && c.nativePadding === '0px'), 'Card images must reach the border');
  for (const field of ['title', 'price', 'footer']) {
    expect(Math.max(...alignment.map(c => c[field])) - Math.min(...alignment.map(c => c[field])) < 2,
      `Card ${field} baselines must align`);
  }
  result.cardAlignment = 'Images, titles, prices and footers aligned';

  await page.locator('[data-nav="configuracion"]').click();
  await page.locator('[data-settings-form]').waitFor({state:'visible'});
  await page.locator('[data-field="housing_budget"]').fill('145000');
  await page.locator('[data-language-select]').selectOption('en');
  expect(await page.locator('[data-field="housing_budget"]').inputValue() === '145000', 'Language switching must not reset unsaved settings');
  result.formDrafts = 'Unsaved settings survive a language change';

  await page.evaluate(() => window.AuctionRadarUI.setLanguage('en'));
  await page.locator('[data-nav="busquedas"]').click();
  await page.locator('[data-runs-list] .run-row').first().waitFor();
  await page.locator('[data-runs-list] details summary').first().click();
  const searches = await page.locator('[data-runs-list]').innerText();
  expect(!searches.includes('[object Object]') && !searches.includes('"requests":') && !searches.includes('"ingest":'), 'Search history must show readable fields, not raw objects');
  expect(/Partial|partial|Limited|limited/.test(searches), 'Partial search status must be explicit in English');
  result.searches = 'Structured source limitations and counters rendered without raw objects';

  await page.locator('[data-nav="oportunidades"]').click();
  const firstCard = page.locator('.item-card').filter({ hasText: 'Vivienda de prueba — datos originales sin traducir' });
  await firstCard.locator('.item-card-open').click();
  await page.locator('#review-notes').waitFor();
  await page.locator('#review-notes').fill('Draft: do not translate or discard this text');
  await page.evaluate(() => window.AuctionRadarUI.setLanguage('es'));
  expect(await page.locator('#review-notes').inputValue() === 'Draft: do not translate or discard this text', 'Language change must preserve an unsaved note');
  await page.evaluate(() => window.AuctionRadarUI.setLanguage('en'));
  expect(await page.locator('#review-notes').inputValue() === 'Draft: do not translate or discard this text', 'Language round trip must preserve notes');
  expect((await page.locator('[data-drawer-title]').innerText()).includes('datos originales sin traducir'), 'Original listing titles must not be translated or replaced');
  await page.locator('#review-notes').focus();
  await page.keyboard.press('k');
  expect((await page.locator('#review-notes').inputValue()).endsWith('k'), 'Review shortcuts must not intercept typing');
  await page.locator('[data-drawer-body] .gallery-thumb').first().click();
  await page.locator('[data-lightbox]:not([hidden])').waitFor();
  const photoOne = await page.locator('[data-lightbox-img]').getAttribute('src');
  await page.keyboard.press('ArrowRight');
  expect(await page.locator('[data-lightbox-img]').getAttribute('src') !== photoOne, 'Right arrow must advance photo');
  expect(/2\s*(?:\/|of|de)\s*3/.test(await page.locator('[data-lightbox-counter]').innerText()), 'Gallery counter must advance');
  await page.keyboard.press('ArrowLeft');
  expect(await page.locator('[data-lightbox-img]').getAttribute('src') === photoOne, 'Left arrow must return to prior photo');
  await page.keyboard.press('End');
  expect(/3\s*(?:\/|of|de)\s*3/.test(await page.locator('[data-lightbox-counter]').innerText()), 'End must select the last photo');
  await page.keyboard.press('Home');
  expect(await page.locator('[data-lightbox-img]').getAttribute('src') === photoOne, 'Home must select the first photo');
  await page.keyboard.press('Escape');
  expect(await page.locator('[data-lightbox]').isHidden(), 'Escape must close the photo layer');
  expect(await page.locator('#item-drawer').getAttribute('aria-hidden') === 'false', 'Escape must not also close the parent drawer');
  await page.keyboard.press('Escape');
  expect(await page.locator('#item-drawer').getAttribute('aria-hidden') === 'true', 'Second Escape must close drawer');
  await firstCard.locator('.item-card-open').click();
  await page.locator('#review-notes').waitFor();
  expect((await page.locator('#review-notes').inputValue()).endsWith('textk'), 'Closing and reopening must retain an unsaved draft in memory');
  await page.keyboard.press('Escape');
  await page.keyboard.press('?');
  await page.locator('[data-shortcuts-dialog]').waitFor({ state: 'visible' });
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.querySelector('[data-shortcuts-dialog]').contains(document.activeElement)), 'Help dialog must retain keyboard focus');
  await page.keyboard.press('Escape');
  expect(await page.locator('[data-shortcuts-dialog]').isHidden(), 'Escape must close shortcut help');
  await page.keyboard.press('/');
  expect(await page.locator('[data-filter="q"]').evaluate(el => document.activeElement === el), 'Slash must focus search');
  result.keyboard = 'Photo arrows/Home/End, layered Escape, help focus and search shortcut verified';

  await page.locator('[data-filter="q"]').fill('');
  await page.locator('[data-filter="q"]').blur();
  const notedCard = page.locator('.item-card').filter({ hasText: 'Una vivienda con un título' });
  const itemId = await notedCard.getAttribute('data-item-id');
  const noteBefore = await page.evaluate(async id => (await (await fetch('/api/items/' + id)).json()).item.review.notes, itemId);
  const quickResponse = page.waitForResponse(r => r.url().endsWith('/review') && r.request().method() === 'POST');
  await notedCard.locator('[data-review-ok]').click();
  const quick = await (await quickResponse).json();
  expect(quick.item.review.status === 'ok' && quick.item.review.notes === noteBefore, 'Quick OK must save the decision without deleting notes');
  result.quickReview = 'Visible OK/KO actions preserve existing notes';

  const share = await page.evaluate(async () => {
    const items = await (await fetch('/api/items?asset=housing&status=all')).json();
    const id = items.items.find(i => i.title.startsWith('Vivienda de prueba')).id;
    const headers = {'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content};
    await fetch('/api/items/'+id+'/review',{method:'POST',headers,body:JSON.stringify({status:'pending',notes:'Private preview team note — never share'})});
    return await (await fetch('/api/items/'+id+'/share',{method:'POST',headers,body:'{}'})).json();
  });
  const viewerContext = await page.context().browser().newContext();
  try {
    const viewer = await viewerContext.newPage();
    await viewer.goto('http://127.0.0.1:8765'+share.url);
    await viewer.locator('[data-share-content]').waitFor({state:'visible'});
    await viewer.locator('[data-language-select]').selectOption('en');
    expect(await viewer.locator('html').getAttribute('lang') === 'en', 'Anonymous view language must switch');
    const publicText = await viewer.locator('body').innerText();
    expect(!publicText.includes('Private preview team note') && !publicText.includes(share.opaque), 'Shared page must not reveal private notes or print the bearer token');
    expect(publicText.includes('Read-only link'), 'Shared page label must be translated');
    const map = viewer.locator('[data-map-link]').first();
    expect(await map.getAttribute('target') === '_blank', 'Anonymous location opens Maps in a new tab');
    expect((await map.getAttribute('href')).startsWith('https://www.google.com/maps/search/?api=1&query='), 'Maps links use the official search URL');
    expect((await map.getAttribute('rel')).includes('noreferrer'), 'Shared tokens must not leak through the Maps referrer');
    await viewer.locator('[data-share-gallery] .gallery-thumb').first().click();
    expect((await viewer.locator('[data-lightbox-img]').getAttribute('src')).endsWith('/photo/1'), 'Clicked public thumbnail must open its own photo');
    await viewer.keyboard.press('ArrowRight');
    expect((await viewer.locator('[data-lightbox-img]').getAttribute('src')).endsWith('/photo/2'), 'Public lightbox supports arrow keys');
    await viewer.keyboard.press('Escape');
    expect(await viewer.locator('[data-lightbox]').isHidden(), 'Public lightbox closes with Escape');
    result.publicShare = 'Anonymous ES/EN view and gallery work without exposing notes or tokens';
  } finally {
    await page.evaluate(async id => {
      await fetch('/api/shares/'+id+'/revoke',{method:'POST',headers:{'X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content}});
    }, share.id);
    await viewerContext.close();
  }

  await page.emulateMedia({ reducedMotion: 'reduce' });
  const reduced = await page.locator('.item-card').first().evaluate(el => getComputedStyle(el).transitionDuration);
  expect(reduced.split(',').every(value => parseFloat(value) <= .001), 'Reduced-motion preference must disable decorative motion');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  result.reducedMotion = true;
  return result;
}
