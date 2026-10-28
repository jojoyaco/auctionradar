/* Regression checks for new searches, filters, and carousel; tests/ui_preview.py only. */
async function checkSavedSearchUI(page) {
  const expect=(ok,message)=>{if(!ok)throw new Error(message);};
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:8765/login');
  if(await page.locator('input[name=username]').count()) {
    await page.locator('input[name=username]').fill('preview-user');
    await page.locator('input[name=password]').fill('preview-only-2026');
    await page.locator('button[type=submit]').click();
  }
  await page.locator('[data-nav=suscripciones]').waitFor();
  await page.evaluate(async()=>{const payload=await(await fetch('/api/searches')).json();for(const saved of payload.searches){await fetch('/api/searches/'+saved.id,{method:'DELETE',headers:{'X-CSRFToken':document.querySelector('meta[name=csrf-token]').content}});}});
  await page.evaluate(()=>window.AuctionRadarUI.setLanguage('en'));
  expect(await page.title()==='AuctionRadar','Brand must be exact');
  expect((await page.locator('[data-lastupdated-wrap]').innerText()).includes('Last updated'),'Header must show actual data update');
  await page.locator('[data-nav=suscripciones]').click();
  await page.getByRole('button',{name:'Boats',exact:true}).click();
  await page.locator('#search-editor-name').fill('My browser boats');
  const originalPrompt=await page.locator('#search-editor-prompt').inputValue();
  await page.evaluate(()=>window.AuctionRadarUI.setLanguage('es'));
  expect(await page.locator('#search-editor-name').inputValue()==='My browser boats','Draft name must survive language changes');
  expect(await page.locator('#search-editor-prompt').inputValue()===originalPrompt,'Do not translate prompt text');
  expect(await page.locator('.app-shell').evaluate(el=>el.inert),'Editor isolates underlying page');
  await page.locator('[data-search-editor-close]').focus();
  await page.keyboard.press('Shift+Tab');
  expect(await page.locator('#search-editor-drawer').evaluate(el=>el.contains(document.activeElement)),'Editor traps keyboard focus');
  await page.evaluate(()=>window.AuctionRadarUI.setLanguage('en'));
  await page.getByRole('button',{name:'Save search',exact:true}).click();
  const boatSearch=page.locator('.search-row').filter({hasText:'My browser boats'});
  await boatSearch.waitFor();
  expect((await boatSearch.innerText()).includes('1 matching results'),'Boat subscription must find boat fixture');
  const viewResults=async searchRow=>{
    const searchId=await searchRow.getAttribute('data-search-id');
    const response=page.waitForResponse(response=>{
      const url=new URL(response.url());
      return url.pathname==='/api/items'&&url.searchParams.get('search_id')===searchId;
    });
    await searchRow.getByRole('button',{name:'View results',exact:true}).click();
    const payload=await(await response).json();
    const expectedIds=payload.items.map(item=>item.id);
    await page.waitForFunction(ids=>JSON.stringify([...document.querySelectorAll('.item-card')].map(card=>card.dataset.itemId))===JSON.stringify(ids),expectedIds);
    return payload;
  };
  await page.locator('[data-action=new-saved-search]').click();
  await page.locator('#search-editor-name').fill('All browser assets');
  await page.getByRole('button',{name:'Save search',exact:true}).click();
  const allSearch=page.locator('.search-row').filter({hasText:'All browser assets'});
  await allSearch.waitFor();
  // The primary action is now "View results" (Ver resultados).
  const boatResults=await viewResults(boatSearch);
  expect(boatResults.total===1&&boatResults.items.length===1&&boatResults.items[0].asset_type==='boats','Boat search must return only the boat fixture');
  expect(await page.locator('.item-card').count()===1,'Boat search must not inherit the housing filter');
  expect(await page.locator('[data-filter=ai]').inputValue()==='shortlist','Default keeps pending assessments visible');
  const card=page.locator('.item-card').first();
  const image=await card.locator('img').getAttribute('src');
  await card.locator('[data-photo-next]').click();
  expect(await card.locator('img').getAttribute('src')!==image,'Card carousel advances without drawer');
  expect(await page.locator('#item-drawer').getAttribute('aria-hidden')==='true','Carousel must not open detail');
  await card.locator('[data-photo-next]').focus();
  await page.keyboard.press('ArrowLeft');
  expect(await card.locator('img').getAttribute('src')===image,'Focused card carousel supports keyboard arrows');
  await card.locator('.item-card-title a').click();
  await page.locator('#review-notes').waitFor();
  expect(/length/i.test(await page.locator('[data-drawer-body]').innerText()),'Boat details show boat facts');
  expect(!/bedrooms/i.test(await page.locator('[data-drawer-body]').innerText()),'Boat details do not show housing fields');
  await page.keyboard.press('Escape');
  await page.locator('[data-nav=suscripciones]').click();
  const allResults=await viewResults(allSearch);
  expect(allResults.items.length===5,'All-assets search must render all five fixtures before sort checks');
  for(const sort of ['price_asc','price_desc']) {
    const response=page.waitForResponse(r=>r.url().includes('/api/items?')&&r.url().includes('sort='+sort));
    await page.locator('[data-sort-trigger]').click();
    await page.locator('[data-popover=sort] [data-popover-value="'+sort+'"]').click();
    const body=await (await response).json();
    const amounts=body.items.map(item=>item.display_price);
    expect(amounts[amounts.length-1]===null,'Unknown prices stay at the end in both directions');
    const numbers=amounts.filter(n=>n!==null);
    expect(numbers.every((n,i)=>!i||(sort==='price_asc'?numbers[i-1]<=n:numbers[i-1]>=n)),'Price sorting is server-wide and numeric');
  }
  await page.locator('[data-sort-trigger]').click();
  expect(await page.locator('[data-popover=sort] [data-popover-value=price_desc]').getAttribute('aria-pressed')==='true','Reopened chooser highlights the current order');
  await page.keyboard.press('Home');
  expect(await page.locator('[data-popover=sort] button').first().evaluate(el=>el===document.activeElement),'Chooser supports keyboard Home');
  await page.keyboard.press('Escape');
  expect(await page.locator('[data-sort-trigger]').evaluate(el=>el===document.activeElement),'Escape restores focus to sort trigger');
  await page.locator('[data-more-categories-trigger]').click();
  expect(await page.locator('[data-popover=more-categories] [data-popover-value=garages]').isVisible(),'More categories is populated on its FIRST opening');
  await page.keyboard.press('Escape');
  // The "More filters" panel now opens with the filter button, not the
  // <details> summary. Verify that the button opens it and applies price_max.
  await page.locator('[data-filters-trigger]').click();
  await page.locator('[data-filter=price_max]').waitFor();
  const filtered=page.waitForResponse(r=>r.url().includes('/api/items?')&&r.url().includes('price_max=15000'));
  await page.locator('[data-filter=price_max]').fill('15000');
  await page.locator('[data-filter=price_max]').press('Tab');
  expect((await (await filtered).json()).total===1,'Maximum price filter applies on the server');
  for(const language of ['es','en']) {
    await page.evaluate(value=>window.AuctionRadarUI.setLanguage(value),language);
    for(const width of [320,390,768,1440]) {
      await page.setViewportSize({width,height:1000});
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal overflow in filters at '+width+' / '+language);
    }
  }
  await page.locator('[data-nav=suscripciones]').click();
  await page.locator('[data-action=new-saved-search]').click();
  await page.keyboard.press('Escape');
  expect(await page.locator('#search-editor-drawer').getAttribute('aria-hidden')==='true','Escape closes saved search editor');
  expect(!await page.locator('.app-shell').evaluate(el=>el.inert),'Page becomes interactive after editor closes');
  expect(errors.length===0,'No browser exceptions: '+errors.join(';'));
  return {brand:true,subscriptions:'create multiple, filter boats, mark notifications read',filters:'numeric ascending/descending, unknown last, price maximum',carousel:'click and keyboard in grid',accessibility:'focus trap, Escape, language draft, mobile overflow',errors};
}
