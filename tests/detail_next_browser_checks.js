async function checkDetailNext(page){
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  const base='http://127.0.0.1:8767';
  await page.setViewportSize({width:1440,height:900});await page.goto(base+'/login');
  if(await page.locator('[name=username]').count()){
    await page.locator('[name=username]').fill('preview-user');await page.locator('[name=password]').fill('preview-only-2026');await page.locator('button[type=submit]').click();
  }
  await page.locator('[data-nav=oportunidades]').waitFor();
  const api=path=>page.evaluate(async path=>(await (await fetch(path)).json()),path);
  const form=id=>page.locator('[data-review-item-id="'+id+'"]');
  const close=async()=>{if(await page.locator('#item-drawer').getAttribute('aria-hidden')==='false')await page.keyboard.press('Escape');};
  const filter=async(status,q='')=>{
    await close();
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.locator('[data-filter=q]').fill(q);
    const response=page.waitForResponse(response=>response.url().includes('/api/items?')&&new URL(response.url()).searchParams.get('status')===status);
    await page.locator('[data-status-value="'+status+'"]').click();await response;
    // The search field is debounced; wait for its filter if the text changed.
    await page.waitForTimeout(380);
    await page.waitForFunction(()=>![...document.querySelectorAll('[data-pagination]')].some(node=>node.getAttribute('aria-busy')==='true'));
    return await api('/api/items?asset=all&status='+status+(q?'&q='+encodeURIComponent(q):''));
  };
  const open=async id=>{await page.locator('.item-card[data-item-id="'+id+'"] .item-card-open').click();await form(id).waitFor();};
  // Open the last item without Playwright's automatic scrolling to avoid
  // accidentally preloading the block this test expects to remain empty.
  const openAtBoundary=async id=>{await page.locator('.item-card[data-item-id="'+id+'"] .item-card-open').evaluate(button=>button.click());await form(id).waitFor();};
  const decide=async(status,id)=>{
    const response=page.waitForResponse(response=>response.url().endsWith('/api/items/'+id+'/review')&&response.request().method()==='POST');
    await form(id).locator('[data-review-status="'+status+'"]').click();return await (await response).json();
  };
  let payload=await filter('pending');
  const [first,second,third]=payload.items;
  const secondNode=await page.locator('.item-card[data-item-id="'+second.id+'"]').elementHandle();
  await open(first.id);await page.locator('#review-notes').fill('Synthetic draft stays with first item');
  const firstResult=await decide('ko',first.id);await form(second.id).waitFor();
  check(firstResult.item.review.status==='ko','KO must be acknowledged before advancing');
  check(firstResult.item.review.notes!=='Synthetic draft stays with first item','Draft must not be saved implicitly');
  check(await page.locator('#review-notes').inputValue()!=='Synthetic draft stays with first item','Draft must not leak to the next item');
  check(await secondNode.evaluate(node=>node.isConnected),'Advancing must not rebuild other grid cards');
  await decide('ok',second.id);await form(third.id).waitFor();
  await filter('all');await open(first.id);
  check(await page.locator('#review-notes').inputValue()==='Synthetic draft stays with first item','Reopening must recover the original draft');
  await decide('ok',first.id);await form(first.id).locator('[data-review-status=ok][aria-pressed=true]').waitFor();
  check(await form(first.id).count()===1,'All filter must keep the same detail open');

  payload=await filter('pending');const failed=payload.items[0];await open(failed.id);
  await page.route('**/api/items/'+failed.id+'/review',route=>route.fulfill({status:500,json:{error:'forced_failure'}}));
  await decide('ko',failed.id);
  await page.waitForFunction(id=>!document.querySelector('[data-review-item-id="'+id+'"] [data-review-status=ko]').disabled,failed.id);
  check(await form(failed.id).locator('[data-review-status=pending]').getAttribute('aria-pressed')==='true','Failed save must stay on the same item');
  await page.unrouteAll();

  payload=await filter('pending');const boundary=payload.items.at(-1);
  const following=(await api('/api/items?asset=all&status=pending&offset=25')).items[0];
  await openAtBoundary(boundary.id);await decide('ok',boundary.id);await form(following.id).waitFor();
  check((await page.locator('.item-card').count())>=49,'Boundary advance must load the next block, not restart page one');

  // Loading the next block must not reopen or change a detail view the user closed.
  payload=await filter('pending');const delayed=payload.items.at(-1),manual=payload.items[0];
  let entered,release,heldOnce=false;
  const arrived=new Promise(resolve=>entered=resolve),held=new Promise(resolve=>release=resolve);
  await page.route('**/api/items?**',async route=>{
    if(!heldOnce && new URL(route.request().url()).searchParams.get('offset')==='24'){
      heldOnce=true;entered();await held;
      try{await route.continue();}catch(error){/* cancelled request */}
    }else await route.continue();
  });
  await openAtBoundary(delayed.id);await decide('ko',delayed.id);await arrived;
  await close();await open(manual.id);release();
  await page.waitForTimeout(180);
  check(await form(manual.id).count()===1,'Late next-page response must not hijack manual navigation');
  await page.unrouteAll();

  payload=await filter('pending');const last=payload.items[0];
  const exact=last.listing_url||last.url;
  payload=await filter('pending',exact);check(payload.total===1,'Last-item fixture must select exactly one item');
  await open(last.id);await decide('ko',last.id);
  await page.waitForFunction(()=>document.querySelector('#item-drawer').getAttribute('aria-hidden')==='true');
  check(await page.locator('.item-card').count()===0,'Last matching item must leave the grid');
  return {koAdvancesInOrder:true,okAdvancesInOrder:true,allStaysOpen:true,draftPreservedPerItem:true,nextBlockLoaded:true,lastCloses:true,failedSaveStays:true,lateNavigationIgnored:true};
}
