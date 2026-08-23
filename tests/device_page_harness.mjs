// Exercise firmware/esp32s3/main/web/index.html against realistic API payloads,
// with a stub DOM. Run by tests/test_device_page.py; also runnable directly:
//
//     node tests/device_page_harness.mjs
//
// The page is the only user interface this device has, and it is one file of
// hand-written JS with no build step and no framework to catch mistakes. The class
// of bug this exists for is the one that takes the *whole* dashboard down rather
// than one field: a key the firmware does not send, a method called on the wrong
// type, an exception in render() that stops every later line from running. Each
// case below is a payload shape the device can really produce.
//
// It also pins the UI-stability work, which is otherwise untestable by eye: the
// last section feeds a deliberately noisy signal and asserts the displayed digits
// settle instead of flickering.
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'firmware/esp32s3/main/web/index.html'), 'utf8');

// --- minimal DOM ---------------------------------------------------------- //
const ids = [...HTML.matchAll(/id="([^"]+)"/g)].map(m => m[1]);
const mkEl = (id) => ({
  id, textContent: '', innerHTML: '', className: '', value: '', disabled: false,
  style: new Proxy({}, {set: () => true, get: () => ''}),
  classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
  addEventListener() {}, prepend() {}, append() {}, remove() {},
  children: {length: 0}, lastChild: null,
  clientWidth: 320, clientHeight: 320, width: 320, height: 320,
  getContext: () => ({
    clearRect() {}, strokeRect() {}, fillText() {}, beginPath() {}, moveTo() {},
    lineTo() {}, stroke() {}, fill() {}, closePath() {}, setLineDash() {},
    save() {}, restore() {}, drawImage() {},
  }),
  removeAttribute() {}, setAttribute() {}, dataset: {},
});
const store = new Map(ids.map(i => [i, mkEl(i)]));
globalThis.document = {
  getElementById: (i) => store.get(i) ?? null,
  createElement: () => mkEl('created'),
  // The per-reason speak buttons are wired by class, not by id.
  querySelectorAll: () => [mkEl('say0'), mkEl('say1'), mkEl('say2'), mkEl('say3')],
  addEventListener() {},
  hidden: false,
  activeElement: null,
};
globalThis.window = {addEventListener() {}};
globalThis.location = {hostname: '192.168.4.1', protocol: 'http:'};
globalThis.fetch = () => Promise.resolve({ok: true, json: () => ({})});
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.setTimeout = () => 0;
globalThis.AbortController = class { constructor() { this.signal = null; } abort() {} };
globalThis.confirm = () => true;
globalThis.createImageBitmap = async () => ({width: 240, height: 240, close() {}});
globalThis.URL = {createObjectURL: () => 'blob:x', revokeObjectURL() {}};
globalThis.Image = class {
  set src(v) { this._s = v; if (this.onload) this.onload(); }
  get src() { return this._s; }
};

// --- the page's own script ------------------------------------------------ //
const js = HTML.slice(HTML.indexOf('<script>') + 8, HTML.indexOf('</script>'));
// render() and the handlers are function-scoped in the module; expose render.
const factory = new Function(
  js + '\nreturn {render, drawOverlay, drawTrend, logLine, openShot, loadHistory, drawFrame, startLive, startPhotos, stopVideo};');
const page = factory();

// --- a payload shaped exactly like web_server.cpp emits ------------------- //
const status = {
  uptime_ms: 3723456, frames: 55842, fps: 15.2,
  camera: true, models: true, eye_model: false,
  frame: {w: 240, h: 240},
  face: {found: true, held: false, x: 62, y: 48, w: 118, h: 118, score: 0.87},
  risk: {score: 0.41, trigger: 0.55, streak: 3, required: 8},
  eyes: {closed: 0.12, perclos: 0.07, closure_s: 0.13},
  cues: {mouth_open: false, head_down: false, suppressed: false,
         baselines_ready: true, events: 8},
  rates: {blink: 14.5, long_blink: 1.2, yawn: 0.9, nod: 0.0, sneeze: 2},
  geom: {valid: true, roll: -3.4, jaw_drop: 0.612, nose_frac: 0.481, eye_dist: 46.2},
  alert: {active: false, text: 'DROWSY', reason: 'drowsy', count: 1, muted: false,
          lang: 'en', lang_stored: true,
          clips: {drowsy: 'embedded', microsleep: 'embedded',
                  yawning: 'embedded', head_nod: 'embedded'}},
  stream: {viewers: 1, quality: 80, fps: 12, port: 81},
  net: {ssid: 'DrowsyGuard-A1B2C3', ip: '192.168.4.1', clients: 1, sta: false,
        sta_ip: '0.0.0.0', rssi: 0},
  mem: {heap: 187392, psram: 7654321},
  image: {luma: 132, min: 6, max: 251},
  card: {mounted: true, events: 7, free_mb: 14812, stored: 7},
};

let failures = 0;
const run = (label, fn) => {
  try { fn(); console.log('  ok   ', label); }
  catch (e) { failures++; console.log('  FAIL ', label, '->', e.message); }
};

console.log('render() against the firmware payload:');
run('nominal', () => page.render(status));
run('alerting', () => page.render({...status,
  alert: {...status.alert, active: true, count: 4},
  risk: {...status.risk, score: 0.71, streak: 8}}));
run('muted', () => page.render({...status, alert: {...status.alert, muted: true}}));
run('no camera', () => page.render({...status, camera: false,
  face: {...status.face, found: false}}));
run('no models, no geometry', () => page.render({...status, models: false,
  geom: {...status.geom, valid: false}}));
run('station mode joined', () => page.render({...status,
  net: {...status.net, sta: true, sta_ip: '10.0.0.42', rssi: -58}}));
run('every event bit set', () => page.render({...status,
  cues: {...status.cues, events: 63}}));
run('zeros everywhere', () => page.render({
  ...status, fps: 0, frames: 0,
  face: {found: false, held: false, x: 0, y: 0, w: 0, h: 0, score: 0},
  risk: {score: 0, trigger: 0.55, streak: 0, required: 8},
  eyes: {closed: 0, perclos: 0, closure_s: 0},
  rates: {blink: 0, long_blink: 0, yawn: 0, nod: 0, sneeze: 0},
  mem: {heap: 0, psram: 0}}));

// 300 ticks, to make sure the sparkline ring buffer and the event-log trim behave.
run('no card', () => page.render({...status, card: {mounted: false, events: 0, free_mb: 0, stored: 0}}));
run('card field absent (older firmware)', () => { const o = {...status}; delete o.card; page.render(o); });
run('stream slot taken - viewers 0', () => page.render({...status, stream: {...status.stream, viewers: 0}}));
run('openShot lightbox', () => page.openShot(
  {id: '0000042', uptime_ms: 3723456, size: 12345, risk: 0.71, perclos: 0.42, reason: 'microsleep'}));

run('khmer selected, clips off the card', () => page.render({...status,
  alert: {...status.alert, lang: 'km',
          clips: {drowsy: 'card', microsleep: 'card', yawning: 'card', head_nod: 'card'}}}));
run('khmer selected but no clips - falls back to tones', () => page.render({...status,
  alert: {...status.alert, lang: 'km',
          clips: {drowsy: 'tone', microsleep: 'tone', yawning: 'tone', head_nod: 'tone'}}}));
run('mixed clip sources', () => page.render({...status,
  alert: {...status.alert, lang: 'km',
          clips: {drowsy: 'card', microsleep: 'tone', yawning: 'card', head_nod: 'embedded'}}}));
run('alert.lang and clips absent (older firmware)', () => {
  const a = {...status.alert}; delete a.lang; delete a.clips;
  page.render({...status, alert: a});
});

run('blown-out frame', () => page.render({...status, image: {luma: 244, min: 180, max: 255}}));
run('very dark frame', () => page.render({...status, image: {luma: 12, min: 0, max: 40}}));
run('image field absent', () => { const o = {...status}; delete o.image; page.render(o); });

run('300 consecutive polls', () => {
  for (let i = 0; i < 300; i++) {
    page.render({...status,
      risk: {...status.risk, score: (i % 100) / 100},
      alert: {...status.alert, count: Math.floor(i / 10)},
      cues: {...status.cues, events: i % 64}});
  }
});

// --- the history browser, against a realistic /api/events payload -------- //
const eventsPayload = (n, total) => ({
  card: {mounted: true, name: 'SD32G', total: 31914983424, free: 31520000000, error: ''},
  total, skip: 0, stored: total, dropped: 0,
  events: Array.from({length: n}, (_, i) => ({
    id: String(total - i).padStart(7, '0'),
    uptime_ms: 3723456 - i * 61000,
    size: 11800 + i * 37,
    risk: 0.55 + (i % 5) * 0.07,
    perclos: (i % 4) * 0.13,
    reason: ['drowsy', 'microsleep', 'yawning', 'head_nod'][i % 4],
  })),
});

const runAsync = async (label, payload) => {
  globalThis.fetch = () => Promise.resolve({ok: true, json: () => payload});
  try { await page.loadHistory(); console.log('  ok   ', label); }
  catch (e) { failures++; console.log('  FAIL ', label, '->', e.message); }
};

console.log('\nloadHistory() against /api/events:');
await runAsync('full page of 12', eventsPayload(12, 137));
await runAsync('partial page', eventsPayload(5, 5));
await runAsync('empty history', eventsPayload(0, 0));
await runAsync('no card', {
  card: {mounted: false, name: '', total: 0, free: 0, error: 'no card (ESP_ERR_TIMEOUT)'},
  total: 0, skip: 0, stored: 0, dropped: 0, events: [],
});
globalThis.fetch = () => Promise.reject(new Error('offline'));
try { await page.loadHistory(); console.log('  ok    device unreachable'); }
catch (e) { failures++; console.log('  FAIL  device unreachable ->', e.message); }

// --- the canvas video path ---------------------------------------------- //
console.log('\nvideo path:');
run('drawFrame paints a decoded bitmap', () =>
  page.drawFrame({width: 240, height: 240, close() {}}));
run('drawFrame with a differently sized frame', () =>
  page.drawFrame({width: 320, height: 240, close() {}}));
run('drawFrame from a bitmap with no close()', () =>
  page.drawFrame({width: 240, height: 240}));
run('switch to stills and back', () => { page.startPhotos(false); page.startLive(); });
run('stopVideo is idempotent', () => { page.stopVideo(); page.stopVideo(); });

// --- the damping actually damps ----------------------------------------- //
// The complaint this addresses was "brightness flashing up and down too fast":
// the raw mean luminance moves every frame, and at two polls a second the digits
// were unreadable. Feed it a noisy signal and check what the pill ends up saying.
console.log('\nreadout damping:');
{
  const seen = new Set();
  for (let i = 0; i < 60; i++) {
    // +/-15 of jitter around 120, which is roughly what the sensor produces.
    const luma = 120 + (i % 2 ? 15 : -15) + (i % 7) - 3;
    page.render({...status, fps: 16 + (i % 2 ? 3 : -3), image: {luma, min: 0, max: 250, peak: 133}});
    seen.add(store.get('lumaPill').textContent);
  }
  // Undamped this would print ~30 distinct strings; smoothed and quantised to
  // steps of 5 it should settle on one or two.
  const ok = seen.size <= 3;
  if (!ok) failures++;
  console.log(`  ${ok ? 'ok   ' : 'FAIL '} brightness settles (${seen.size} distinct values over 60 polls)`);

  const fpsSeen = new Set();
  for (let i = 0; i < 40; i++) {
    page.render({...status, fps: 16 + (i % 2 ? 3 : -3),
                 image: {luma: 120, min: 0, max: 250, peak: 133}});
    fpsSeen.add(store.get('fpsPill').textContent);
  }
  const ok2 = fpsSeen.size <= 2;
  if (!ok2) failures++;
  console.log(`  ${ok2 ? 'ok   ' : 'FAIL '} fps settles (${fpsSeen.size} distinct values over 40 polls)`);
}

console.log(failures ? `\n${failures} failure(s)` : '\nall paths clean');
process.exit(failures ? 1 : 0);
