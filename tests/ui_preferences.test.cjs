/* Pure preference/i18n checks with Node built-ins; no browser or dependencies. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../auctionradar/web/static/ui.js'), 'utf8');

function boot(storage = new Map()) {
  const listeners = new Map();
  const rootAttrs = {};
  const events = [];
  function element(tagName) {
    const callbacks = {};
    return {tagName, dataset: {}, callbacks,
      setAttribute(key,value) {this[key] = value;},
      addEventListener(type, callback) {(callbacks[type] ||= []).push(callback);},
      querySelector() {return this.label;}, label: {textContent:''}};
  }
  const select = element('SELECT');
  const button = element('BUTTON');
  const document = {
    readyState: 'loading',
    documentElement: {setAttribute(k,v) {rootAttrs[k]=v;}},
    querySelector() {return null;},
    querySelectorAll(selector) {
      if (selector === '[data-language-select]') return [select];
      if (selector === '[data-theme-toggle]') return [button];
      return [];
    },
    addEventListener(type, callback) {(listeners.get(type) || listeners.set(type, []).get(type)).push(callback);},
    dispatchEvent(event) {events.push(event); for (const callback of listeners.get(event.type) || []) callback(event);},
  };
  const sandbox = {document, Intl, URL,
    localStorage: {getItem: key=>storage.get(key) ?? null, setItem:(key,value)=>storage.set(key,value)},
    matchMedia() {return {matches:false,addEventListener(){}};},
    CustomEvent: class {constructor(type,options){this.type=type;this.detail=options.detail;}},
  };
  sandbox.window = sandbox;
  vm.runInNewContext(source, sandbox);
  for (const callback of listeners.get('DOMContentLoaded') || []) callback();
  return {ui:sandbox.AuctionRadarUI,storage,select,button,events,rootAttrs};
}

const first = boot();
assert.equal(first.storage.has('auctionradar.theme'), false, 'System fallback is not an explicit saved choice');
assert.equal(first.ui.locale(), 'en-GB');
first.ui.setupLanguagePicker();
first.ui.setupThemeToggle();
assert.equal(first.select.callbacks.change.length, 1, 'Language listeners must be idempotent');
assert.equal(first.button.callbacks.click.length, 1, 'Theme listeners must be idempotent');
first.select.value = 'en'; first.select.callbacks.change[0]();
assert.equal(first.ui.locale(), 'en-GB');
assert.equal(first.ui.t('Programación:'), 'Schedule:');
assert.equal(first.ui.t('Foto {current} de {total}', {current:2,total:3}), 'Photo 2 of 3');
assert.equal(first.ui.t('Original user note / nota original'), 'Original user note / nota original');
assert.equal(first.ui.errorMessage('username_taken'), 'That username is already registered.');
assert.equal(first.ui.errorMessage('invalid_current_password'), 'The current password is incorrect.');
assert.equal(first.events.at(-1).type, 'auctionradar:languagechange', 'Language change must reach document listeners');
assert.equal(first.events.filter(event => event.type === 'auctionradar:languagechange').length, 1, 'A locale update emits one canonical language event');
first.button.callbacks.click[0]();
assert.equal(first.ui.theme(), 'dark');
assert.equal(first.storage.get('auctionradar.theme'), 'dark');
const second = boot(first.storage);
assert.equal(second.rootAttrs.lang, 'en', 'Language must be applied before CSS loads');
assert.equal(second.rootAttrs['data-theme'], 'dark', 'Theme must be applied before CSS loads');
second.ui.setLanguage('es');
assert.equal(second.ui.errorMessage('username_taken'), 'Ese nombre de usuario ya está registrado.');
assert.equal(second.ui.t('Foto {current} de {total}', {current:2,total:3}), 'Foto 2 de 3');
assert.equal(second.events.filter(event => event.type === 'auctionradar:languagechange').length, 1, 'Each explicit locale update emits one canonical language event');
const maps = new URL(second.ui.mapsURL({address:'Calle Prueba 12',municipality:'Gijón',province:'Asturias'}));
assert.equal(maps.origin,'https://www.google.com');
assert.equal(maps.pathname,'/maps/search/');
assert.equal(maps.searchParams.get('api'),'1');
assert.equal(maps.searchParams.get('query'),'Calle Prueba 12, Gijón, Asturias');
assert.equal(second.ui.mapsURL(null),null);
assert.equal(second.ui.mapsURL({address:'unknown',province:null}),null);
assert.equal(new URL(second.ui.mapsURL({municipality:'Oviedo'})).searchParams.get('query'),'Oviedo','Partial locations are searches, never invented addresses');
assert.ok(second.ui.mapsURL('街'.repeat(1000)).length<=2048,'Maps URLs have a documented size limit');
assert.equal(new URL(second.ui.mapsURL('javascript:alert(1)')).hostname,'www.google.com','Source data cannot change the destination host');
console.log('UI preferences: persistence, idempotent controls, document events, translations and safe errors OK');
