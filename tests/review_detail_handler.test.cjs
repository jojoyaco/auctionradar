'use strict';

/* Handler test that executes the ACTUAL `buildReviewSection` source
 * extracted from `auctionradar/web/static/app.js` (between
 * `function buildReviewSection(item)` and `function buildDetailBody(item)`)
 * via fs + Node's vm. The function is loaded verbatim — no hand-inlined
 * copy of the click logic — so the test breaks if the real source
 * regresses (e.g. someone reverts the autosave POST, breaks rollback
 * or breaks the per-item coordinator that keeps a stale response from
 * overwriting a different drawer's state).
 *
 * Sandbox: minimal fake DOM with attribute / id selectors, plus injected
 * mocks for `apiFetch`, `updateGridReview`, `showToast`, `drawerState`,
 * `drawerDrafts`, `detailReviewSaves` (per-item controller introduced
 * by the parent refactor) and `window.AuctionRadarUI` (passthrough for
 * `t` / `formatDateTime`).
 *
 * Assertions only look at observable side effects (POST bodies, DOM
 * attributes, drawerState.data.item, drawerDrafts entries, grid-update
 * arguments) — never at internal local variable names — so the test
 * survives the parent's refactor.
 *
 * If the parent is mid-refactor and a test fails, that is a real
 * signal: do not silence it.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = path.join(__dirname, '..', 'auctionradar', 'web', 'static', 'app.js');

// ---------------------------------------------------------------------------
// Source extraction: grab the body of buildReviewSection verbatim.
// ---------------------------------------------------------------------------
function extractBody(source) {
  const startIdx = source.indexOf('function buildReviewSection(item)');
  if (startIdx === -1) throw new Error('buildReviewSection not found in app.js');
  const tailIdx = source.indexOf('function buildDetailBody(item)', startIdx);
  if (tailIdx === -1) throw new Error('buildDetailBody not found in app.js');
  const headerEnd = source.indexOf('{', startIdx) + 1;
  let depth = 1, i = headerEnd;
  while (i < tailIdx && depth > 0) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') depth--;
    i++;
  }
  return source.slice(headerEnd, i - 1);
}

// ---------------------------------------------------------------------------
// Minimal fake DOM: tree of nodes with attribute / id selectors, event
// listeners, classList (add/remove/contains/toggle), disabled flag and
// generic textContent/value setters.
// ---------------------------------------------------------------------------
function parseSelector(sel) {
  return sel.trim().split(/\s+/).map(part => {
    if (part.startsWith('[#') && part.endsWith(']')) {
      // synthetic escape; we never emit this.
      return null;
    }
    if (part.charAt(0) === '#') return { kind: 'id', name: part.slice(1) };
    if (part.charAt(0) === '[') {
      const body = part.slice(1, -1);
      const eq = body.indexOf('=');
      if (eq === -1) return { kind: 'attr', name: body, value: undefined };
      return { kind: 'attr', name: body.slice(0, eq), value: JSON.parse(body.slice(eq + 1)) };
    }
    return { kind: 'tag', name: part.toUpperCase() };
  });
}
function nodeMatches(node, t) {
  if (!node || !node.attrs) return false;
  if (t.kind === 'id') return node.attrs.id === t.name;
  if (t.kind === 'attr') {
    if (!(t.name in node.attrs)) return false;
    return t.value === undefined ? true : node.attrs[t.name] === t.value;
  }
  return node.tagName === t.name;
}
function findInTree(root, tokens, idx = 0, out = []) {
  for (const child of root.children || []) {
    if (nodeMatches(child, tokens[idx])) {
      if (idx === tokens.length - 1) out.push(child);
      else findInTree(child, tokens, idx + 1, out);
    }
    findInTree(child, tokens, idx, out);
  }
  return out;
}
function makeNode(tag) {
  const cls = {
    _s: new Set(),
    add(c) { cls._s.add(c); node.attrs.class = Array.from(cls._s).join(' '); },
    remove(c) { cls._s.delete(c); node.attrs.class = Array.from(cls._s).join(' '); },
    contains(c) { return cls._s.has(c); },
    toggle(c, force) {
      const want = force === undefined ? !cls._s.has(c) : !!force;
      if (want) cls.add(c); else cls.remove(c);
      return want;
    },
  };
  const node = {
    tagName: tag.toUpperCase(),
    attrs: {}, children: [], parentNode: null,
    classList: cls,
    _listeners: {},
    _text: '', _value: '', _disabled: false,
    get textContent() { return this._text; }, set textContent(v) { this._text = String(v); },
    get value() { return this._value; }, set value(v) { this._value = String(v); },
    get id() { return this.attrs.id || ''; }, set id(v) { this.attrs.id = v; },
    get disabled() { return this._disabled; }, set disabled(v) { this._disabled = !!v; },
    get type() { return this.attrs.type || ''; }, set type(v) { this.attrs.type = v; },
    get isConnected() { return true; },
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    click() { (this._listeners.click || []).forEach(fn => fn({ type: 'click', target: this })); },
    querySelector(sel) { const r = findInTree(this, parseSelector(sel)); return r[0] || null; },
    querySelectorAll(sel) { return findInTree(this, parseSelector(sel)); },
    replaceChildren(...kids) { this.children = []; kids.forEach(k => { this.children.push(k); k.parentNode = this; }); },
  };
  return node;
}
function makeDocument() {
  const root = { tagName: '#fragment', attrs: {}, children: [], isConnected: true,
    appendChild(c) { root.children.push(c); c.parentNode = root; return c; },
    querySelector(sel) { const r = findInTree(root, parseSelector(sel)); return r[0] || null; },
    querySelectorAll(sel) { return findInTree(root, parseSelector(sel)); },
    getElementById(id) { const r = findInTree(root, [{ kind: 'id', name: id }]); return r[0] || null; },
  };
  return {
    root,
    document: {
      createElement: t => makeNode(t),
      createTextNode: t => ({ nodeType: 3, textContent: String(t) }),
      querySelector(sel) { return root.querySelector(sel); },
      querySelectorAll(sel) { return root.querySelectorAll(sel); },
      getElementById(id) { return root.getElementById(id); },
      activeElement: null,
    },
  };
}

// ---------------------------------------------------------------------------
// Sandbox: load buildReviewSection into a fresh VM context with the
// shared state objects and mocked helpers it expects to find in the
// surrounding IIFE. Returns the sandbox plus a recorder for assertions.
// ---------------------------------------------------------------------------
function createSandbox(apiMock) {
  const fake = makeDocument();
  const calls = { api: [], gridUpdates: [], toast: [] };
  const drawerState = { itemId: null, data: { item: null } };
  const drawerDrafts = Object.create(null);
  const detailReviewSaves = Object.create(null);
  // Passthrough i18n + date helpers (AuctionRadarUI seam).
  const t = (s, p) => !p ? s : String(s).replace(/\{(\w+)\}/g,
    (_, k) => Object.prototype.hasOwnProperty.call(p, k) ? String(p[k]) : '{' + k + '}');
  const formatDateTime = v => {
    if (!v) return null;
    const d = new Date(v); if (isNaN(d.getTime())) return null;
    try { return new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short' }).format(d); }
    catch (e) { return d.toLocaleString('es-ES'); }
  };
  const window = { AuctionRadarUI: { t, formatDateTime } };
  const apiFetch = (p, o) => {
    calls.api.push({ path: p, body: o && o.body, method: o && o.method });
    return apiMock ? apiMock(p, o) :
      Promise.resolve({ item: { id: 'item-1', review: { status: (o && o.body && o.body.status) || 'pending', notes: 'srv' } } });
  };
  const loadItems = () => { throw new Error('Review must patch the grid, not reload it'); };
  const updateGridReview = item => { calls.gridUpdates.push(item); };
  const showToast = (msg, kind) => { calls.toast.push({ msg, kind }); };

  const body = extractBody(fs.readFileSync(APP_JS, 'utf8'));
  const script = new vm.Script('function buildReviewSection(item) {\n' + body + '\n}',
    { filename: 'app.js#buildReviewSection' });
  const sandbox = {
    document: fake.document, window,
    privateWorkspace: false,
    console, setTimeout, clearTimeout, setImmediate, clearImmediate,
    Promise, Object, Array, JSON, Math, Date, Intl,
    apiFetch, loadItems, updateGridReview, beginGridReview:()=>{}, endGridReview:()=>{}, captureDetailReviewNavigation:()=>null, advanceReviewedDetail:()=>Promise.resolve(), showToast, t, formatDateTime,
    drawerState, drawerDrafts, detailReviewSaves,
  };
  vm.createContext(sandbox);
  script.runInContext(sandbox);

  return { sandbox, calls, drawerState, drawerDrafts, detailReviewSaves, fake };
}
function mount(ctx, item) {
  ctx.drawerState.itemId = item.id;
  ctx.drawerState.data.item = item;
  const node = ctx.sandbox.buildReviewSection(item);
  ctx.fake.root.appendChild(node);
  return node.querySelector('[data-review-form]');
}
const flush = (n = 8) => Array.from({ length: n },
  () => new Promise(r => setImmediate(r))).reduce((p) => p.then(() => new Promise(r => setImmediate(r))), Promise.resolve());

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
async function testSingleKoPostOnce() {
  const ctx = createSandbox(async (p, o) => ({ item: { id: 'item-1', review: { status: o.body.status, notes: 'srv-note' } } }));
  const block = mount(ctx, { id: 'item-1', review: { status: 'pending', notes: 'srv-note' } });
  const ko = block.querySelector('[data-review-status="ko"]');
  assert.equal(ko.getAttribute('aria-pressed'), 'false', 'KO starts unpressed');
  ko.click();
  await flush();
  assert.equal(ctx.calls.api.length, 1, 'one POST per click');
  assert.equal(ctx.calls.api[0].path, '/api/items/item-1/review');
  assert.equal(ctx.calls.api[0].body.status, 'ko');
  assert.equal(ctx.calls.api[0].body.preserve_notes, true, 'autosave must send preserve_notes:true');
  assert.equal(ko.getAttribute('aria-pressed'), 'true', 'KO reflects latest intent');
  assert.equal(ko.disabled, false, 'buttons re-enabled after the POST resolves');
  assert.equal(ctx.calls.gridUpdates.length, 1, 'one in-place grid update after acknowledgement');
  assert.equal(ctx.calls.gridUpdates[0].review.status,'ko');
  assert.ok(ctx.calls.toast.some(t => t.msg === 'Decisión guardada.' && t.kind === 'ok'), 'success toast shown');
}

async function testClickOnSameStatusNoop() {
  const ctx = createSandbox(async () => ({ item: { id: 'item-1', review: { status: 'pending', notes: 'srv' } } }));
  const block = mount(ctx, { id: 'item-1', review: { status: 'pending', notes: 'srv' } });
  block.querySelector('[data-review-status="pending"]').click();
  await flush();
  assert.equal(ctx.calls.api.length, 0, 'clicking the persisted status must not POST');
}

async function testDirtyTextareaUntouched() {
  const ctx = createSandbox(async (p, o) => ({ item: { id: 'item-1', review: { status: o.body.status, notes: 'srv-note' } } }));
  const root = ctx.fake.root;
  mount(ctx, { id: 'item-1', review: { status: 'pending', notes: 'srv-note' } });
  const ta = ctx.fake.document.getElementById('review-notes');
  ta.value = 'borrador local sin guardar';
  root.querySelector('[data-review-status="ko"]').click();
  await flush();
  assert.equal(ta.value, 'borrador local sin guardar', 'autosave must not touch the textarea draft');
  assert.equal(ctx.calls.api[0].body.preserve_notes, true, 'autosave sends preserve_notes:true');
}

async function testErrorRollback() {
  const ctx = createSandbox(async (p, o) => o.body.status === 'ko'
    ? Promise.reject({ error: 'forced_failure' })
    : { item: { id: 'item-1', review: { status: o.body.status, notes: 'srv' } } });
  const block = mount(ctx, { id: 'item-1', review: { status: 'pending', notes: 'srv' } });
  const ko = block.querySelector('[data-review-status="ko"]');
  const pending = block.querySelector('[data-review-status="pending"]');
  ko.click();
  await flush();
  assert.equal(ko.getAttribute('aria-pressed'), 'false', 'KO rolled back after error');
  assert.equal(pending.getAttribute('aria-pressed'), 'true', 'pending restored after error');
  assert.equal(ko.disabled, false, 'buttons re-enabled');
  assert.ok(ctx.calls.toast.some(t => t.kind === 'error'), 'error toast shown');
}

async function testRapidQueueLastIntent() {
  const recorded = [];
  const ctx = createSandbox(async (p, o) => {
    recorded.push({ status: o.body.status, ts: Date.now() });
    return new Promise(r => setTimeout(() => r({ item: { id: 'item-1', review: { status: o.body.status, notes: 'srv' } } }), 25));
  });
  const block = mount(ctx, { id: 'item-1', review: { status: 'pending', notes: 'srv' } });
  block.querySelector('[data-review-status="ko"]').click();
  await new Promise(r => setTimeout(r, 5));
  block.querySelector('[data-review-status="ok"]').click();
  await new Promise(r => setTimeout(r, 120));
  assert.equal(recorded.length, 2, 'two clicks yield two POSTs');
  assert.equal(recorded[0].status, 'ko');
  assert.equal(recorded[1].status, 'ok');
  assert.ok(recorded[1].ts >= recorded[0].ts + 20, 'second POST runs after the first resolves (serialized)');
  assert.equal(block.querySelector('[data-review-status="ok"]').getAttribute('aria-pressed'), 'true', 'UI matches last click');
  assert.equal(ctx.drawerState.data.item.review.status, 'ok', 'drawerState reflects the last persisted status');
}

async function testDrawerSwitchRace() {
  // Drawer A's response must not overwrite drawer B once the user has
  // navigated to B. The fix is the per-item coordinator
  // detailReviewSaves[item.id]; the test only asserts the observable
  // outcome (drawerState stays on B with its own status).
  const delayed = (p, o) => new Promise(r => setTimeout(
    () => r({ item: { id: o.body._intendedFor || 'item-A', review: { status: o.body.status, notes: 'srv-late' } } }),
    40));
  const ctx = createSandbox(delayed);
  const aBlock = mount(ctx, { id: 'item-A', review: { status: 'pending', notes: 'srv-A' } });
  aBlock.querySelector('[data-review-status="ko"]').click();
  await new Promise(r => setImmediate(r));
  // Switch to B before A resolves.
  ctx.drawerState.itemId = 'item-B';
  ctx.drawerState.data.item = { id: 'item-B', review: { status: 'pending', notes: 'srv-B' } };
  mount(ctx, { id: 'item-B', review: { status: 'pending', notes: 'srv-B' } });
  await new Promise(r => setTimeout(r, 80));
  assert.equal(ctx.drawerState.data.item.id, 'item-B', 'drawerState stays on B');
  assert.equal(ctx.drawerState.data.item.review.status, 'pending', 'late A response must not overwrite B');
}

async function testExplicitSaveDecision() {
  const ctx = createSandbox(async (p, o) => ({
    item: { id: 'item-1', review: { status: o.body.status, notes: o.body.notes || '' } }
  }));
  const root = ctx.fake.root;
  mount(ctx, { id: 'item-1', review: { status: 'pending', notes: 'srv' } });
  const ta = ctx.fake.document.getElementById('review-notes');
  ta.value = 'nota explícita del usuario';
  root.querySelector('[data-review-status="ko"]').click();   // autosave (preserve_notes)
  await flush();
  const saveBtn = root.querySelector('[data-save-review]');
  assert.ok(saveBtn, 'Guardar notas button must exist');
  saveBtn.click();                                            // explicit save (with notes)
  await flush();
  const last = ctx.calls.api[ctx.calls.api.length - 1];
  assert.equal(last.body.status, 'ko');
  assert.equal(last.body.notes, 'nota explícita del usuario',
    'SaveDecision must send the explicit textarea content (no preserve_notes)');
  assert.equal(ctx.calls.gridUpdates.length, 2, 'both POSTs patch the grid without reloading');
}

async function testPrivateNotesLabel() {
  const ctx = createSandbox();
  ctx.sandbox.privateWorkspace = true;
  mount(ctx, { id: 'private-item', review: { status: 'pending', notes: '' } });
  const label = ctx.fake.root.querySelector('label');
  assert.equal(label.textContent, 'Mis notas privadas');
}

// ---------------------------------------------------------------------------
// Runner — fail loudly so the parent sees a real signal during refactor.
// ---------------------------------------------------------------------------
(async () => {
  const cases = [
    ['singleKoPostOnce', testSingleKoPostOnce],
    ['clickOnSameStatusNoop', testClickOnSameStatusNoop],
    ['dirtyTextareaUntouched', testDirtyTextareaUntouched],
    ['errorRollback', testErrorRollback],
    ['rapidQueueLastIntent', testRapidQueueLastIntent],
    ['drawerSwitchRace', testDrawerSwitchRace],
    ['explicitSaveDecision', testExplicitSaveDecision],
    ['privateNotesLabel', testPrivateNotesLabel],
  ];
  let failed = 0;
  for (const [name, fn] of cases) {
    try { await fn(); console.log('OK    ', name); }
    catch (e) { failed++; console.log('FAIL  ', name, '·', e.message.split('\n')[0]); }
  }
  if (failed) {
    console.log('Detail review handler: ' + (cases.length - failed) + '/' + cases.length + ' cases pass — failed cases expose real regressions or a refactor still in flight');
    process.exit(1);
  }
  console.log('Detail review handler (extracted from app.js): single KO POST, noop, dirty isolation, error rollback, queue order, drawer-switch race and explicit SaveDecision all verified against the real buildReviewSection source');
})().catch(err => { console.error('FAIL', err.stack || err); process.exit(1); });
