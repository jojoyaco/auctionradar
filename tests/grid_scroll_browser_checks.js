/* Infinite-scroll contract tests against tests/grid_scroll_preview.py.
 *
 * Asserts that the opportunities grid (real ``auctionradar/web/static/app.js``):
 *   1. Loads the first page via the existing paged API and renders the
 *      server's page-size number of cards (default 25, never more).
 *   2. Hides / removes the numbered-pagination controls
 *      (``[data-action=page-prev]``, ``[data-action=page-next]``).
 *   3. Autoloads the next page on scroll without losing the first card,
 *      without duplicating item IDs, and while keeping the global price
 *      ordering monotonic across at least three pages.
 *   4. A card's OK action persists to the server and refreshes the
 *      affected card in place — the scroll position and the surrounding
 *      cards (DOM identity) survive the round-trip.
 *   5. A card's KO action removes the offending card but keeps every
 *      other already-loaded row stable, and subsequent loads respect
 *      the current filter and sort without inserting duplicates.
 *   6. A filter change issued while a page request is in flight is not
 *      corrupted by the stale (old-filter) response.
 *   7. A failed page request surfaces a retry control
 *      (``button[data-load-more]``) and never wipes the cards already
 *      on screen.
 *
 * The driver does not copy any handlers; it only interacts with the
 * real ``app.js`` code path (login, native change events on the hidden
 * sort select, real buttons, real scroll). No production credentials are
 * read or written. The fixture credentials live in tests/ui_preview.py.
 */
async function checkGridScroll(page) {
  const expect = (ok, message) => { if (!ok) throw new Error(message); };
  const base = 'http://127.0.0.1:8767';
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(base + '/login');
  if (await page.locator('input[name=username]').count()) {
    await page.locator('input[name=username]').fill('preview-user');
    await page.locator('input[name=password]').fill('preview-only-2026');
    await page.locator('button[type=submit]').click();
  }
  await page.locator('[data-nav=oportunidades]').waitFor();

  // ---- Set up filters & sort via the same selectors the real app exposes.
  // ``status=all`` so every fixture row (all pending) stays in the grid.
  const firstAll = page.waitForResponse(
    r => r.url().includes('/api/items?') && r.url().includes('status=all') && r.request().method() === 'GET'
  );
  await page.locator('[data-status-value=all]').click();
  const firstBody = await (await firstAll).json();
  await page.waitForFunction(
    ids => JSON.stringify([...document.querySelectorAll('.item-card')].map(c => c.dataset.itemId)) === JSON.stringify(ids),
    firstBody.items.map(i => i.id),
  );

  // Sort: price_asc. The app already wires the hidden ``[data-filter=sort]``
  // selecto to a native ``change`` listener; firing the event is exactly
  // what the in-app sort popover does (see triggerSelectChange in app.js).
  const sortResponse = page.waitForResponse(
    r => r.url().includes('/api/items?') && r.url().includes('sort=price_asc')
  );
  await page.evaluate(() => {
    const sel = document.querySelector('[data-filter="sort"]');
    if (!sel) throw new Error('hidden sort select not found');
    sel.value = 'price_asc';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  });
  const sortBody = await (await sortResponse).json();
  await page.waitForFunction(
    ids => JSON.stringify([...document.querySelectorAll('.item-card')].map(c => c.dataset.itemId)) === JSON.stringify(ids),
    sortBody.items.map(i => i.id),
  );

  // ---- (1) Page size & absence of numbered pagination.
  const pageSize = sortBody.items.length;
  expect(pageSize > 0 && pageSize <= 25,
    `Initial page must hold server's page-size cards (got ${pageSize})`);
  expect(await page.locator('[data-action=page-prev]').count() === 0
      || await page.locator('[data-action=page-prev]').first().evaluate(n => n.hidden || n.offsetParent === null),
    'Numbered prev control must be hidden / removed');
  expect(await page.locator('[data-action=page-next]').count() === 0
      || await page.locator('[data-action=page-next]').first().evaluate(n => n.hidden || n.offsetParent === null),
    'Numbered next control must be hidden / removed');
  const initialCount = await page.locator('.item-card').count();
  expect(initialCount === pageSize,
    `Grid must show exactly ${pageSize} cards on first page (got ${initialCount})`);

  // The first card identity must survive every subsequent append.
  const firstCard = await page.locator('.item-card').first().elementHandle();

  // ---- (2) Autoload via scroll, monotonic price order, no duplicates.
  // Server responses carry ``display_price``; we assert the ordering there
  // (cleaner than DOM scraping) and then verify the DOM mirrors the IDs
  // returned across the three paged calls.
  const monotonic = (arr) => arr.every(
    (v, i) => i === 0 || (v != null && arr[i - 1] != null && arr[i - 1] <= v)
  );
  const pricesPage1 = sortBody.items.map(i => i.display_price);
  expect(monotonic(pricesPage1.filter(v => v != null)),
    `Page 1 server prices must be ascending: ${pricesPage1.join(',')}`);

  // The contract is "app responds to scroll by requesting the next page";
  // we do not pick a specific scroll container because the real layout
  // may use either window or a nested scroller.
  const loadMoreResponse = (pageIndex,offset=(pageIndex-1)*pageSize) => page.waitForResponse(
    r => r.url().includes('/api/items?') && new URL(r.url()).searchParams.get('offset') === String(offset)
  );
  const seenBodies = [sortBody];
  let lastCount = initialCount;
  for (let i = 2; i <= 3; i++) {
    const resp = loadMoreResponse(i);
    await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' }));
    await page.waitForTimeout(150);
    await page.evaluate(() => window.scrollBy(0, 600));
    await page.waitForTimeout(150);
    // Synthesize a scroll event so apps that key on ``scroll`` (instead of
    // or in addition to IntersectionObserver) wake up reliably.
    await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
    const body = await (await resp).json();
    seenBodies.push(body);
    await page.waitForFunction(
      ({ ids }) => JSON.stringify([...document.querySelectorAll('.item-card')].map(c => c.dataset.itemId).slice(0, ids.length))
        === JSON.stringify(ids),
       { ids: seenBodies.flatMap(body=>body.items.map(x=>x.id)) },
    );
    const newCount = await page.locator('.item-card').count();
    expect(newCount > lastCount,
      `Page ${i} append must add cards beyond ${lastCount} (got ${newCount})`);
    lastCount = newCount;
  }

  const idsAfter3 = await page.locator('.item-card').evaluateAll(
    cards => cards.map(c => c.dataset.itemId)
  );
  const uniqueIds = new Set(idsAfter3);
  expect(uniqueIds.size === idsAfter3.length,
    `No duplicate item IDs after 3 pages: ${idsAfter3.length - uniqueIds.size} dup`);

  // Same first-card DOM node must persist across all appends.
  const firstCardAfter = await page.locator('.item-card').first().elementHandle();
  expect(await firstCard.evaluate((n,other) => n.isSameNode(other),firstCardAfter),
    'First card DOM identity must survive infinite-scroll appends');

  // Concatenated server payloads across the three pages must be monotonic
  // ascending and contain every ID visible in the DOM (no skipped rows).
  const concatenatedPrices = seenBodies.flatMap(b =>
    b.items.map(i => i.display_price).filter(v => v != null)
  );
  expect(monotonic(concatenatedPrices),
    `Server prices across 3 pages must be ascending: ${concatenatedPrices.join(',')}`);
  const concatenatedIds = seenBodies.flatMap(b => b.items.map(i => i.id));
  expect(JSON.stringify(concatenatedIds) === JSON.stringify(idsAfter3),
    'DOM must contain exactly the IDs returned by the 3 paginated API calls');

  // ---- (3) OK deep on page 3 preserves scroll and neighbour identity.
  // Choose a card anchored on page 3 and a neighbour card whose DOM node
  // we will compare after the OK round-trip.
  const target = page.locator('.item-card').nth(40);
  const neighbour = page.locator('.item-card').nth(41);
  await target.scrollIntoViewIfNeeded();
  await page.waitForTimeout(50);
  const beforeNeighbour = await neighbour.elementHandle();
  const beforeNeighbourBounds = await neighbour.boundingBox();
  const beforeScroll = await page.evaluate(() => window.scrollY);
  const beforeLocation = page.url();
  const beforeNeighbourId = await neighbour.getAttribute('data-item-id');
  const targetId=await target.getAttribute('data-item-id');
  await page.evaluate(async id=>{
    const response=await fetch('/api/items/'+id+'/review',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify({status:'pending',notes:'Synthetic existing note: keep it'})});
    if(!response.ok)throw new Error('Fixture note setup failed');
  },targetId);

  const okResponse = page.waitForResponse(
    r => r.url().endsWith('/review') && r.request().method() === 'POST'
  );
  await target.locator('[data-review-ok]').click();
  const okBody = await (await okResponse).json();
  expect(okBody && okBody.item && okBody.item.review && okBody.item.review.status === 'ok',
    'OK POST must return persisted status');

  // The neighbour card must keep DOM identity (no full-grid rebuild).
  await page.waitForTimeout(80);
  const afterNeighbour = await page.locator(`.item-card[data-item-id="${beforeNeighbourId}"]`).elementHandle();
  expect(await beforeNeighbour.evaluate((n,other) => n.isSameNode(other),afterNeighbour),
    'Neighbour card DOM identity must survive OK round-trip');
  const afterScroll = await page.evaluate(() => window.scrollY);
  // Layout may shift a few px while the card re-renders, but we must not
  // jump back to the top of the page.
  expect(Math.abs(afterScroll - beforeScroll) <= 80,
    `Scroll must not reset on OK (before=${beforeScroll}, after=${afterScroll})`);
  // No navigation: page URL and main view must remain on ``oportunidades``.
  expect(page.url() === beforeLocation,
    'OK must not navigate away from the oportunidades view');
  const afterNeighbourBounds = await page.locator(
    `.item-card[data-item-id="${beforeNeighbourId}"]`
  ).boundingBox();
  expect(afterNeighbourBounds && beforeNeighbourBounds
    && Math.abs(afterNeighbourBounds.y - beforeNeighbourBounds.y) < 120,
    'Neighbour card must stay near its original y-position after OK');

  // Server-side check: GET /api/items/<id> still reports the same review.
  const persisted = await page.evaluate(async id => {
    const r = await fetch('/api/items/' + id);
    return r.ok ? (await r.json()) : null;
  }, targetId);
  expect(persisted && persisted.item && persisted.item.review && persisted.item.review.status === 'ok',
    'Server must persist OK on the selected card');
  expect(persisted.item.review.notes==='Synthetic existing note: keep it','Grid OK must preserve existing notes');

  // ---- (4) KO removes the card; rest of rows stable; no duplicates on reload.
  const pendingResp=page.waitForResponse(r=>r.url().includes('/api/items?')&&r.url().includes('status=pending'));
  await page.locator('[data-status-value=pending]').click();await pendingResp;
  await page.waitForFunction(()=>document.querySelectorAll('.item-card').length===25);
  for(let i=2;i<=3;i++){
    const next=loadMoreResponse(i);
    await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));
    await next;
    await page.waitForFunction(n=>document.querySelectorAll('.item-card').length>=n,i*pageSize);
  }
  const koTarget = page.locator('.item-card').nth(50);
  const koTargetId = await koTarget.getAttribute('data-item-id');
  const idsBeforeKo = await page.locator('.item-card').evaluateAll(
    cards => cards.map(c => c.dataset.itemId)
  );
  const koResponse = page.waitForResponse(
    r => r.url().endsWith('/review') && r.request().method() === 'POST'
  );
  await koTarget.locator('[data-review-ko]').click();
  await koResponse;
  await page.waitForFunction(
    id => !document.querySelector('.item-card[data-item-id="' + id + '"]'),
    koTargetId,
  );
  const idsAfterKo = await page.locator('.item-card').evaluateAll(
    cards => cards.map(c => c.dataset.itemId)
  );
  expect(!idsAfterKo.includes(koTargetId),
    'KO card must leave the grid immediately');
  // Every previously loaded row must remain visible — no full reload.
  const stillThere = idsBeforeKo.filter(id => id !== koTargetId).every(
    id => idsAfterKo.includes(id)
  );
  expect(stillThere,
    'Loaded rows must remain stable when one row is marked KO');

  // Scroll once more; the new append must not re-introduce the KO row
  // nor duplicate any row already loaded.
   const nextResp = loadMoreResponse(4,idsBeforeKo.length-1);
  await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight }));
  await page.waitForTimeout(150);
  await page.evaluate(() => window.scrollBy(0, 600));
  await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
  const fourthBody=await (await nextResp).json();
  await page.waitForFunction(n=>document.querySelectorAll('.item-card').length>=n,idsAfterKo.length+fourthBody.items.length);
  const idsAfter4 = await page.locator('.item-card').evaluateAll(
    cards => cards.map(c => c.dataset.itemId)
  );
  expect(!idsAfter4.includes(koTargetId),
    'KO row must not reappear after subsequent loads');
  expect(new Set(idsAfter4).size === idsAfter4.length,
    'No duplicate IDs after KO + subsequent append');
  const expectedPending=await page.evaluate(async()=>{
    let rows=[],offset=0,more=true;
    while(more){const data=await (await fetch('/api/items?asset=all&status=pending&sort=price_asc&offset='+offset)).json();rows.push(...data.items.map(item=>item.id));offset=data.next_offset;more=data.has_more;}
    return rows;
  });
  expect(JSON.stringify(idsAfter4)===JSON.stringify(expectedPending.slice(0,idsAfter4.length)),'No skipped rows after status-filter removal');

  // ---- (5) Filter reset while a page is in flight: stale page discarded.
  // Switch back to status=pending mid-request. The previous-status request
  // is delayed via page.route so the test can issue the new filter while
  // it is still in-flight, then assert the stale response does NOT append.
  await page.unrouteAll().catch(() => {});
  // Playwright URL globs: ``*`` matches any chars, ``**`` matches across
  // path separators. ``?`` is a single-char wildcard, so we anchor the
  // pattern with ``&status=pending`` and a literal marker.
  const pendingRoute = '**/api/items*&status=pending*';
  let releasePending, pendingEntered;
  const pendingArrived = new Promise(resolve => { pendingEntered = resolve; });
  await page.route(pendingRoute, async (route) => {
    pendingEntered();
    await new Promise(resolve => { releasePending = resolve; });
    try{return await route.continue();}catch(error){/* filter cancellation is expected */}
  });
  // Fire a status=pending load (background) and wait until the trap is hit.
  await page.locator('[data-status-value="pending"]').click();
  await pendingArrived;
  // While the pending request is held, switch to status=all and let it
  // complete; then release the pending response to test staleness.
  await page.unroute(pendingRoute);
  const allResp = page.waitForResponse(
    r => r.url().includes('/api/items?') && r.url().includes('status=all')
      && !r.url().includes('status=pending')
  );
  await page.locator('[data-status-value=all]').click();
  const allBody = await (await allResp).json();
  // Release the trapped pending request. Its response must not corrupt
  // the current "all" view.
  releasePending();
  await page.waitForFunction(
    ids => JSON.stringify([...document.querySelectorAll('.item-card')].map(c => c.dataset.itemId))
      === JSON.stringify(ids),
    allBody.items.map(x => x.id),
  );
  const idsAfterReset = await page.locator('.item-card').evaluateAll(
    cards => cards.map(c => c.dataset.itemId)
  );
  expect(JSON.stringify(idsAfterReset) === JSON.stringify(allBody.items.map(x => x.id)),
    'Stale in-flight page response must not corrupt the new filter view');

  // ---- (6) 503 retry: cards remain, retry button appears.
  await page.unrouteAll().catch(() => {});
  // Intercept the next /api/items request the scroll triggers. The exact
  // page number is irrelevant — the contract is "after a 503 the loaded
  // cards remain and a retry control is visible".
  const failRoute = '**/api/items*page=*';
  let failTriggered;
  const failArrived = new Promise(resolve => { failTriggered = resolve; });
  await page.route(failRoute, async (route) => {
    failTriggered();
    return route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'forced_503' }),
    });
  });
  const cardsBeforeFail = await page.locator('.item-card').count();
  await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight }));
  await page.waitForTimeout(150);
  await page.evaluate(() => window.scrollBy(0, 600));
  await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
  await failArrived;
  await page.waitForTimeout(150);
  const cardsAfterFail = await page.locator('.item-card').count();
  expect(cardsAfterFail === cardsBeforeFail,
    `Loaded cards must survive a 503 (before=${cardsBeforeFail}, after=${cardsAfterFail})`);
  const retryBtn = page.locator('button[data-load-more]');
  expect(await retryBtn.isVisible() && await retryBtn.isEnabled(),
    'A retry control [data-load-more] must be present after a failed append');
  await page.unroute(failRoute);
  await retryBtn.click();
  await page.waitForFunction(n=>document.querySelectorAll('.item-card').length>n,cardsBeforeFail);

  await page.setViewportSize({width:390,height:844});
  const mobileTarget=page.locator('.item-card').nth(12);
  await mobileTarget.scrollIntoViewIfNeeded();
  const mobileBefore=await page.evaluate(()=>window.scrollY);
  const mobilePost=page.waitForResponse(response=>response.url().endsWith('/review')&&response.request().method()==='POST');
  await mobileTarget.locator('[data-review-ok]').click();await mobilePost;
  await page.waitForTimeout(80);
  const mobileDelta=Math.abs((await page.evaluate(()=>window.scrollY))-mobileBefore);
  expect(mobileDelta<=80,'Mobile OK must retain scroll position');

  if (errors.length) throw new Error('browser errors: ' + errors.join('; '));
  return {
    pageSize,
    cardsAfter3Pages: idsAfter3.length,
    duplicateIds: idsAfter3.length - uniqueIds.size,
    monotonicPriceAsc: true,
    firstCardIdentityPreserved: true,
    okScrollDeltaPx: Math.abs(afterScroll - beforeScroll),
    mobileOkScrollDeltaPx:mobileDelta,
    koRemovedTargetId: koTargetId,
    newFilterNotCorruptedByStale: true,
    failed503RetainsCards: cardsAfterFail === cardsBeforeFail,
    retryControlPresent: await retryBtn.count() > 0,
    browserErrors: errors,
  };
}
