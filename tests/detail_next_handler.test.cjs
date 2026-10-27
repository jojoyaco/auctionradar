'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../auctionradar/web/static/app.js'),'utf8');
const helpers=source.slice(source.indexOf('  function captureDetailReviewNavigation('),source.indexOf('  function openDrawer('));
function fixture(){
  const calls={opened:[],closed:0,drafts:0,loads:0};
  const state={feed:{initial:false,queryVersion:3,hasMore:false},lastItems:{items:[{id:'before'},{id:'next'}]}};
  const drawerState={itemId:'current',navigationVersion:8};
  const context={state,drawerState,Promise,document:{querySelector:()=>null},cssEscape:id=>id,
    rememberDrawerDraft:()=>calls.drafts++,lightboxVisible:()=>false,closeLightbox:()=>{},
    openDrawer:id=>{calls.opened.push(id);drawerState.itemId=id;drawerState.navigationVersion++;},
    closeDrawer:()=>{calls.closed++;drawerState.itemId=null;drawerState.navigationVersion++;},
    loadMoreItems:()=>{calls.loads++;state.lastItems.items.push({id:'later'});return Promise.resolve();}};
  vm.createContext(context);vm.runInContext(helpers,context);
  return {context,state,drawerState,calls};
}
async function run(){
  let f=fixture(),token=f.context.captureDetailReviewNavigation('current');
  await f.context.advanceReviewedDetail('current',{removed:true,index:1},token);
  assert.deepEqual(f.calls.opened,['next']);assert.equal(f.calls.drafts,1);
  f=fixture();token=f.context.captureDetailReviewNavigation('current');
  await f.context.advanceReviewedDetail('current',{removed:false,index:1},token);
  assert.deepEqual(f.calls.opened,[]);assert.equal(f.calls.closed,0);
  f=fixture();token=f.context.captureDetailReviewNavigation('current');f.state.feed.hasMore=true;
  await f.context.advanceReviewedDetail('current',{removed:true,index:2},token);
  assert.deepEqual(f.calls.opened,['later']);assert.equal(f.calls.loads,1);
  f=fixture();token=f.context.captureDetailReviewNavigation('current');
  await f.context.advanceReviewedDetail('current',{removed:true,index:2},token);
  assert.equal(f.calls.closed,1);
  for(const change of ['close','switch','reopen','filter']){
    f=fixture();token=f.context.captureDetailReviewNavigation('current');
    if(change==='close')f.drawerState.itemId=null;
    if(change==='switch')f.drawerState.itemId='other';
    if(change==='reopen')f.drawerState.navigationVersion++;
    if(change==='filter')f.state.feed.queryVersion++;
    await f.context.advanceReviewedDetail('current',{removed:true,index:1},token);
    assert.deepEqual(f.calls.opened,[],change);assert.equal(f.calls.closed,0,change);
  }
  f=fixture();token=f.context.captureDetailReviewNavigation('current');f.state.feed.hasMore=true;
  let release;f.context.loadMoreItems=()=>new Promise(resolve=>release=resolve);
  const pending=f.context.advanceReviewedDetail('current',{removed:true,index:2},token);
  f.drawerState.itemId='manually-opened';f.drawerState.navigationVersion++;
  f.state.lastItems.items.push({id:'later'});release();await pending;
  assert.deepEqual(f.calls.opened,[]);assert.equal(f.calls.closed,0);
  f=fixture();f.state.feed.initial=true;
  assert.equal(f.context.captureDetailReviewNavigation('current'),null);
  console.log('Detail next: in-order advance, All/no-op, next batch, end closes, draft capture and late navigation/filter guards verified against actual app.js helpers');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
