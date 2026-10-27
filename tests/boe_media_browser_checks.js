async function checkBoeCachedPhotos(page) {
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  const base='http://127.0.0.1:8766';
  await page.goto(base+'/login');
  if(await page.locator('[name=username]').count()){
    await page.locator('[name=username]').fill('preview-user');
    await page.locator('[name=password]').fill('preview-only-2026');
    await page.locator('button[type=submit]').click();
  }
  await page.locator('[data-nav=oportunidades]').waitFor();
  await page.goto(base+'/?item=boe-media-fixture');
  await page.locator('#review-notes').waitFor();
  await page.waitForFunction(()=>[...document.querySelectorAll('[data-drawer-body] img')].some(image=>image.src.includes('/media/')&&image.complete&&image.naturalWidth>0));
  const dimensions=await page.locator('[data-drawer-body] img').evaluateAll(images=>images.filter(image=>image.src.includes('/media/')).map(image=>[image.naturalWidth,image.naturalHeight]));
  check(dimensions.length===1,'Actual private-media endpoint must load the image');
  check(await page.locator('[data-photo-notice]').count()===0,'No stale login notice after authenticated import');
  const share=await page.evaluate(async()=>{
    const headers={'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content};
    return (await fetch('/api/items/boe-media-fixture/share',{method:'POST',headers,body:'{}'})).json();
  });
  const anonymous=await page.context().browser().newContext();
  try {
    const shared=await anonymous.newPage();await shared.goto(base+share.url);
    await shared.waitForFunction(()=>[...document.images].some(image=>image.src.includes('/api/public/')&&image.complete&&image.naturalWidth>0));
    const mediaUrl=await shared.locator('img').evaluateAll(images=>images.find(image=>image.src.includes('/api/public/')).src);
    let response=await anonymous.request.get(mediaUrl);
    check(response.status()===200,'Anonymous active share must retrieve the actual cached image');
    check((response.headers()['cache-control']||'').includes('no-store'),'No cache after revocation');
    const revoked=await page.evaluate(async id=>(await fetch('/api/shares/'+id+'/revoke',{method:'POST',headers:{'X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content}})).status,share.share_id||share.id);
    check(revoked===200,'Revocation must succeed');
    response=await anonymous.request.get(mediaUrl);check(response.status()===404,'Revoked token must immediately deny image access');
    return {privateImageLoaded:dimensions,anonymousReadOnlyImage:true,revocationImmediate:true};
  } finally {await anonymous.close();}
}
