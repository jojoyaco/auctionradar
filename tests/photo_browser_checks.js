/* Opt-in browser check: local preview, synthetic listings, and the two public URLs
 * identified in Escogecasa's gallery. Never use production credentials.
 */
async function checkSourcePhotos(page) {
  const expect=(ok,message)=>{if(!ok)throw new Error(message);};
  await page.goto('http://127.0.0.1:8765/login');
  if(await page.locator('input[name=username]').count()){
    await page.locator('input[name=username]').fill('preview-user');
    await page.locator('input[name=password]').fill('preview-only-2026');
    await page.locator('button[type=submit]').click();
  }
  await page.locator('[data-nav=oportunidades]').waitFor();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const base={asset_type:'housing',category:'housing',source_id:'escogecasa',title:'Comprobación de galería pública',review:null,prices:{},availability:{status:'unknown'}};
  const photos=[1,2].map(i=>({url:'https://ngb-repositorio.urbaniza.com/inmuebles/fotos/grandes/1/51049_'+i+'.jpg',caption:i===1?'VIA':'FACHADA'}));
  await page.route('**/api/items/photo-check',route=>route.fulfill({json:{item:{...base,id:'photo-check',photos},history:[]}}));
  await page.goto('http://127.0.0.1:8765/?item=photo-check');
  await page.locator('#review-notes').waitFor();
  await page.waitForFunction(()=>{const images=[...document.querySelectorAll('[data-drawer-body] img')].filter(img=>img.src.includes('ngb-repositorio.urbaniza.com'));return images.length===2&&images.every(img=>img.complete&&img.naturalWidth>0);},{},{timeout:30000});
  const dimensions=await page.locator('[data-drawer-body] img').evaluateAll(images=>images.map(img=>({width:img.naturalWidth,height:img.naturalHeight})));
  await page.keyboard.press('Escape');
  const source='https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&ver=3';
  await page.route('**/api/items/boe-photo-check',route=>route.fulfill({json:{item:{...base,id:'boe-photo-check',source_id:'boe',asset_type:'boats',category:'boats',photos:[],photo_notice:{code:'boe_login_required',source_url:source}},history:[]}}));
  await page.goto('http://127.0.0.1:8765/?item=boe-photo-check');
  await page.locator('[data-photo-notice]').waitFor();
  const link=page.locator('[data-photo-notice] a');
  expect(await link.getAttribute('href')===source,'Auth notice must link to the actual BOE asset tab');
  expect(await link.getAttribute('target')==='_blank','BOE sign-in remains on the original portal in another tab');
  for(const language of ['es','en']){
    await page.evaluate(value=>window.AuctionRadarUI.setLanguage(value),language);
    const text=await page.locator('[data-photo-notice]').innerText();
    expect(language==='es'?text.includes('iniciar sesión'):text.includes('sign-in'),'Photo access notice is localized');
  }
  expect(errors.length===0,errors.join('; '));
  return {publicPhotosLoaded:dimensions,boeAuthenticationNotice:true,errors};
}
