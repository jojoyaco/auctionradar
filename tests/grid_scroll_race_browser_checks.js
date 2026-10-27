async function checkGridReviewRace(page){
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  await page.setViewportSize({width:1440,height:900});
  await page.goto('http://127.0.0.1:8767/');
  await page.locator('[data-nav=oportunidades]').waitFor();
  const pending=page.waitForResponse(response=>response.url().includes('/api/items?')&&response.url().includes('status=pending'));
  await page.locator('[data-status-value=pending]').click();await pending;
  await page.waitForFunction(()=>document.querySelectorAll('.item-card').length===25);
  const prefix=await page.locator('.item-card').evaluateAll(cards=>cards.map(card=>card.dataset.itemId));
  let entered,release,once=false;
  const arrived=new Promise(resolve=>entered=resolve);
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/items?**',async route=>{
    if(!once&&new URL(route.request().url()).searchParams.get('offset')==='25'){
      once=true;entered();await held;
      try{await route.continue();}catch(error){/* request was cancelled by review */}
    }else await route.continue();
  });
  await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));await arrived;
  const id=prefix[22];
  const post=page.waitForResponse(response=>response.url().includes('/api/items/'+id+'/review')&&response.request().method()==='POST');
  await page.locator('.item-card[data-item-id="'+id+'"] [data-review-ko]').click();await post;
  release();
  await page.waitForFunction(id=>!document.querySelector('.item-card[data-item-id="'+id+'"]'),id);
  await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));
  await page.waitForFunction(()=>document.querySelectorAll('.item-card').length>=49);
  await page.unrouteAll();
  const actual=await page.locator('.item-card').evaluateAll(cards=>cards.map(card=>card.dataset.itemId));
  const expected=await page.evaluate(async()=>{
    const first=await (await fetch('/api/items?asset=all&status=pending&offset=0')).json();
    const second=await (await fetch('/api/items?asset=all&status=pending&offset=25')).json();
    return first.items.concat(second.items).map(item=>item.id);
  });
  check(JSON.stringify(actual)===JSON.stringify(expected.slice(0,actual.length)),'Review during append must neither skip nor resurrect items');
  check(new Set(actual).size===actual.length,'Review during append must not duplicate IDs');

  // El POST fallido no puede borrar la tarjeta ni vaciar la lista.
  const failedId=actual[20];const count=actual.length;
  await page.locator('.item-card[data-item-id="'+failedId+'"] [data-review-ok]').scrollIntoViewIfNeeded();
  const scrollBefore=await page.evaluate(()=>window.scrollY);
  await page.route('**/api/items/'+failedId+'/review',route=>route.fulfill({status:503,json:{error:'test_unavailable'}}));
  const failed=page.waitForResponse(response=>response.url().endsWith('/'+failedId+'/review'));
  await page.locator('.item-card[data-item-id="'+failedId+'"] [data-review-ok]').click();await failed;
  await page.waitForFunction(id=>!document.querySelector('.item-card[data-item-id="'+id+'"] [data-review-ok]').disabled,failedId);
  check(await page.locator('.item-card').count()===count,'Failed POST must keep all loaded cards');
  check(await page.locator('.item-card[data-item-id="'+failedId+'"]').getAttribute('data-review-status')==='pending','Failed POST must retain persisted status');
  check(Math.abs((await page.evaluate(()=>window.scrollY))-scrollBefore)<80,'Failed POST must retain scroll');
  await page.unrouteAll();
  return {reviewDuringAppendNoSkippedRows:true,noDuplicates:true,failedPostKeepsGrid:true};
}
