/* Browser test of the detail view against tests/ui_preview.py; loopback data only.
 * Review POSTs use the browser session, never fetch from the Node process.
 */
async function checkDetailReviewAutosave(page) {
  const expect=(ok,message)=>{if(!ok)throw new Error(message);};
  const base='http://127.0.0.1:8765';
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:1440,height:1000});
  await page.goto(base+'/login');
  if(await page.locator('input[name=username]').count()){
    await page.locator('input[name=username]').fill('preview-user');
    await page.locator('input[name=password]').fill('preview-only-2026');
    await page.locator('button[type=submit]').click();
  }
  await page.locator('[data-nav=oportunidades]').waitFor();
  const api=(path,body)=>page.evaluate(async({path,body})=>{
    const headers={'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name=csrf-token]').content};
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers,body:JSON.stringify(body)});
    if(!response.ok)throw new Error('Fixture API HTTP '+response.status);
    return response.json();
  },{path,body});
  const items=(await api('/api/items?status=all')).items;
  const ids={house:items.find(item=>item.title.startsWith('Vivienda de prueba')).id,
             van:items.find(item=>item.asset_type==='vehicles').id,
             boat:items.find(item=>item.asset_type==='boats').id};
  const reset=async()=>{for(const item of items)await api('/api/items/'+item.id+'/review',{status:'pending',notes:'Server note '+item.id});};
  const gridIds=()=>page.locator('.item-card').evaluateAll(cards=>cards.map(card=>card.dataset.itemId).sort());
  const filter=async status=>{
    const response=page.waitForResponse(r=>r.url().includes('/api/items?')&&r.url().includes('status='+status));
    const button=page.locator('[data-status-value="'+status+'"]');
    if(await button.getAttribute('aria-pressed')==='true')await page.locator('[data-action=refresh]').click();else await button.click();
    const payload=await(await response).json();
    await page.waitForFunction(expected=>JSON.stringify([...document.querySelectorAll('.item-card')].map(card=>card.dataset.itemId).sort())===JSON.stringify(expected),payload.items.map(item=>item.id).sort());
  };
  const open=async id=>{await page.locator('.item-card[data-item-id="'+id+'"] .item-card-open').click();await page.locator('#review-notes').waitFor();};
  const saveStatus=async status=>{
    const response=page.waitForResponse(r=>r.url().endsWith('/review')&&r.request().method()==='POST');
    await page.locator('#item-drawer button[data-review-status="'+status+'"]').click();
    const result=await(await response).json();
    await page.waitForFunction(()=>![...document.querySelectorAll('#item-drawer button[data-review-status]')].some(button=>button.disabled));
    return result;
  };
  const review=async id=>(await api('/api/items/'+id)).item.review;
  await reset();
  for(const [family,id] of Object.entries(ids)){
    await filter('pending');await open(id);
    const response=await saveStatus('ko');
    expect(response.item.review.status==='ko',family+' KO must persist without another save');
    expect(response.item.review.notes==='Server note '+id,family+' status autosave must preserve notes');
    await page.waitForFunction(id=>!document.querySelector('.item-card[data-item-id="'+id+'"]'),id);
    await page.keyboard.press('Escape');
    expect(!(await gridIds()).includes(id),family+' card must leave the pending grid');
    await page.reload();await page.locator('[data-status-value=pending]').waitFor();await filter('pending');
    expect(!(await gridIds()).includes(id),family+' KO must survive reload');
    await filter('ko');expect((await gridIds()).includes(id),family+' KO filter must retain the card');
    await filter('all');expect((await gridIds()).includes(id),family+' All filter must retain the card');
    await api('/api/items/'+id+'/review',{status:'pending',notes:'Server note '+id});
  }
  await filter('all');await open(ids.house);
  await page.locator('#review-notes').fill('Unsaved local draft');
  await saveStatus('ko');
  expect(await page.locator('#review-notes').inputValue()==='Unsaved local draft','Autosave must retain unsaved notes');
  expect((await review(ids.house)).notes==='Server note '+ids.house,'Only explicit note save may change notes');
  let extraPosts=0;const listener=request=>{if(request.method()==='POST'&&request.url().endsWith('/review'))extraPosts++;};
  page.on('request',listener);
  await page.locator('#review-notes').focus();
  await page.locator('#review-notes').evaluate(notes=>notes.setSelectionRange(3,8));
  await page.evaluate(()=>{
    window.__languageChangeEvents=[];
    document.addEventListener('auctionradar:languagechange',event=>window.__languageChangeEvents.push(event.detail.lang));
    window.AuctionRadarUI.setLanguage('es');
  });
  const firstLanguageChange=await page.evaluate(()=>({
    events:window.__languageChangeEvents,
    activeId:document.activeElement&&document.activeElement.id,
    selection:[document.querySelector('#review-notes').selectionStart,document.querySelector('#review-notes').selectionEnd],
  }));
  expect(firstLanguageChange.events.length===1&&firstLanguageChange.events[0]==='es','One language change must emit exactly one notification');
  expect(firstLanguageChange.activeId==='review-notes','Language change preserves input focus');
  expect(firstLanguageChange.selection[0]===3&&firstLanguageChange.selection[1]===8,'Language change preserves the draft selection');
  expect(await page.locator('#review-notes').inputValue()==='Unsaved local draft','Locale change preserves draft');
  await page.evaluate(()=>window.AuctionRadarUI.setLanguage('en'));
  const secondLanguageChange=await page.evaluate(()=>window.__languageChangeEvents);
  expect(JSON.stringify(secondLanguageChange)===JSON.stringify(['es','en']),'Each locale change must emit one notification');
  expect(await page.locator('#review-notes').inputValue()==='Unsaved local draft','Returning locale preserves draft');
  expect(extraPosts===0,'Restoring a draft must never autosave a status');page.off('request',listener);
  const noteSave=page.waitForResponse(r=>r.url().endsWith('/review')&&r.request().method()==='POST');
  await page.locator('[data-save-review]').click();await noteSave;
  expect((await review(ids.house)).status==='ko'&&(await review(ids.house)).notes==='Unsaved local draft','Explicit notes save retains the acknowledged KO');
  await page.keyboard.press('Escape');await reset();
  // A later status response must not overwrite the item opened in the meantime.
  await filter('pending');await open(ids.house);
  let release;let entered;const arrived=new Promise(resolve=>entered=resolve);
  const path='**/api/items/'+ids.house+'/review';
  await page.route(path,async route=>{if(route.request().method()!=='POST')return route.continue();entered();await new Promise(resolve=>release=resolve);return route.continue();});
  const late=page.waitForResponse(r=>r.url().endsWith('/api/items/'+ids.house+'/review')&&r.request().method()==='POST');
  await page.locator('#item-drawer button[data-review-status=ko]').click();await arrived;
  await page.locator('#review-notes').fill('Draft during pending request');
  await page.evaluate(()=>window.AuctionRadarUI.setLanguage('es'));
  expect(await page.locator('#item-drawer button[data-review-status=ko]').isDisabled(),'Locale re-render must keep pending controls disabled');
  expect(await page.locator('#review-notes').inputValue()==='Draft during pending request','Draft survives an in-flight locale change');
  await page.keyboard.press('Escape');await open(ids.van);
  release();await late;
  expect((await page.locator('[data-drawer-title]').innerText()).includes('Furgoneta'),'Late house response cannot replace another drawer');
  expect(await page.locator('#review-notes').inputValue()==='Server note '+ids.van,'Late house response cannot replace another item notes');
  await page.unroute(path);await page.keyboard.press('Escape');
  await filter('ko');await open(ids.house);
  expect(await page.locator('#item-drawer button[data-review-status=ko]').getAttribute('aria-pressed')==='true','Reopening uses actual persisted status');
  expect(await page.locator('#review-notes').inputValue()==='Draft during pending request','Reopening retains only unsaved notes');
  await page.keyboard.press('Escape');await reset();
  await page.reload();await page.locator('[data-status-value=pending]').waitFor();
  // It remains open in All; test concurrent note updates.
  await filter('all');await open(ids.house);
  await api('/api/items/'+ids.house+'/review',{status:'pending',notes:'Concurrent server note'});
  await saveStatus('ko');
  expect(await page.locator('#review-notes').inputValue()==='Concurrent server note','Untouched notes must refresh from server, not become a stale draft');
  await page.keyboard.press('Escape');await reset();
  // A failed save cannot show success or hide the still-pending item.
  await filter('pending');await open(ids.boat);
  await page.route('**/api/items/'+ids.boat+'/review',route=>route.fulfill({status:500,json:{error:'forced_failure'}}));
  await saveStatus('ko');
  expect(await page.locator('#item-drawer button[data-review-status=pending]').getAttribute('aria-pressed')==='true','Failed request rolls back the selector');
  expect((await review(ids.boat)).status==='pending','Forced failure does not alter server state');
  await page.unroute('**/api/items/'+ids.boat+'/review');await page.keyboard.press('Escape');
  expect((await gridIds()).includes(ids.boat),'Failed request cannot delete the pending card');
  await reset();
  expect(errors.length===0,errors.join('; '));
  const result={autosave:'housing, vans and boats; persistent after reload',filters:'Pending hides KO; All and KO retain it',notes:'server notes, drafts, explicit saves and concurrent edits protected',races:'locale changes and late cross-item responses protected',failedSave:'no false success or deletion',errors};
  await page.evaluate(result=>{window.__detailReviewRegression=result;},result);
  return result;
}
