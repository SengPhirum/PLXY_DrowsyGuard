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
  addEventListener(ev, fn) { (this._ev ??= {})[ev] = fn; },
  prepend() {}, append() {}, remove() {},
  // The MQTT modal hides whole rows by id, so the stub has to carry a `hidden`
  // property and a closest() - a missing one would throw on a path a browser renders
  // fine, which is exactly the class of bug this harness exists for.
  hidden: false,
  closest() { return mkEl('closest'); },
  select() {}, setSelectionRange() {}, focus() {}, blur() {},
  placeholder: '', checked: false, type: '',
  children: {length: 0}, lastChild: null,
  clientWidth: 320, clientHeight: 320, width: 320, height: 320,
  // A 2D context stub. Every method the page calls has to be here or the page throws
  // on a path a browser would have rendered - which is the whole point of this
  // harness, so a missing method is a real failure rather than a stub to widen
  // without thinking. `arc` and `fillRect` were added for the landmark overlay.
  getContext: () => ({
    clearRect() {}, strokeRect() {}, fillRect() {}, fillText() {}, measureText: () => ({width: 0}),
    beginPath() {}, moveTo() {}, lineTo() {}, arc() {}, ellipse() {}, rect() {},
    stroke() {}, fill() {}, closePath() {}, setLineDash() {},
    save() {}, restore() {}, translate() {}, rotate() {}, scale() {}, drawImage() {},
  }),
  removeAttribute() {}, setAttribute() {}, dataset: {},
  // The scan list is built by assigning innerHTML and then wired by querying the
  // result, which a string-valued stub cannot answer. Parsing the `data-i` attributes
  // back out is enough to reproduce the browser's behaviour for the one markup shape
  // the page writes - and it means a click handler that is wired onto the wrong row,
  // or onto nothing, is a test failure rather than something to notice on a phone.
  querySelectorAll(sel) {
    if (!String(sel).includes('button')) return [];
    // Re-parsed only when the markup changed, so two queries of the same list return
    // the same objects - which is what a browser does, and what the page depends on
    // when it writes rows and then wires them.
    if (this._rowsFor !== this.innerHTML) {
      this._rowsFor = this.innerHTML;
      this._rows = [...String(this.innerHTML).matchAll(/data-i="(\d+)"/g)].map(m => {
        const b = mkEl('net-' + m[1]);
        b.dataset = {i: m[1]};
        return b;
      });
    }
    return this._rows;
  },
});
const store = new Map(ids.map(i => [i, mkEl(i)]));
// Selector-aware, unlike the first version of this stub: the page now wires two
// different sets of buttons by attribute (`button.say` and `button[data-copy]`), and
// returning the speak buttons for both would have wired the copy handlers onto the
// wrong elements and tested nothing.
const copyButtons = ['topicDevice', 'topicFleet', 'topicStatus'].map(id => {
  const b = mkEl('copy-' + id);
  b.dataset = {copy: id};
  return b;
});
globalThis.document = {
  getElementById: (i) => store.get(i) ?? null,
  createElement: () => mkEl('created'),
  querySelectorAll: (sel) => {
    if (String(sel).includes('data-copy')) return copyButtons;
    // The per-reason speak buttons are wired by class, not by id.
    return [mkEl('say0'), mkEl('say1'), mkEl('say2'), mkEl('say3')];
  },
  addEventListener() {},
  body: {appendChild() {}, removeChild() {}},
  execCommand: () => true,
  hidden: false,
  activeElement: null,
};
globalThis.window = {addEventListener() {}, isSecureContext: false};
// Node defines globalThis.navigator itself, and it is getter-only - so the copy
// helper's `navigator.clipboard` check has to be satisfied by defining the property
// rather than assigning it. Left without a clipboard on purpose: the device is served
// over plain HTTP, so the execCommand fallback is the path that actually runs on a
// real phone and therefore the one worth exercising.
Object.defineProperty(globalThis, 'navigator', {value: {}, configurable: true});
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
  js + '\nreturn {render, drawOverlay, drawTrend, logLine, openShot, loadHistory, '
     + 'drawFrame, startLive, startPhotos, stopVideo, renderMqtt, mqttFill, '
     + 'mqttTopics, mqttBody, mqttSave, mqttLoad, mqttRefreshTopics, copyText, '
     + 'openMqtt, mqttTest, wifiFill, wifiRenderNets, wifiScan, wifiLoad, '
     + 'openWifi, renderWifiCard, esc, barsHtml};');
const page = factory();

// --- a payload shaped exactly like web_server.cpp emits ------------------- //
const status = {
  uptime_ms: 3723456, frames: 55842, fps: 15.2,
  ms: {detect: 39.4, eye: 21.8},
  camera: true, models: true, eye_model: false,
  frame: {w: 240, h: 240},
  face: {found: true, held: false, x: 62, y: 48, w: 118, h: 118, score: 0.87,
         roi: true, roi_w: 188, rejected: 0, reject: 'ok'},
  driver: true,
  presence: {state: 'present', health: 'ok', absent_s: 0.0, alert_after_s: 3.0,
             alerts: 0},
  // Canonical order and frame pixels: image-left eye, image-right eye, nose, then
  // the two mouth corners. Placed consistently with the face box above.
  lm: {valid: true, x: [90, 133, 111, 96, 127], y: [86, 86, 108, 132, 132]},
  risk: {score: 0.41, trigger: 0.55, streak: 3, required: 8},
  eyes: {closed: 0.12, smooth: 0.11, shut: false, perclos: 0.07, closure_s: 0.13},
  cues: {mouth_open: false, head_down: false,
         baselines_ready: true, stale: false, events: 8,
         open_index: 0.014, pitch_dev: -0.006},
  rates: {blink: 14.5, long_blink: 1.2, yawn: 0.9, nod: 0.0},
  geom: {valid: true, roll: -3.4, jaw_drop: 0.612, nose_frac: 0.481,
         nose_norm: 0.294, mouth_ratio: 0.583, eye_dist: 46.2},
  alert: {active: false, text: 'DROWSY', reason: 'drowsy', count: 1, muted: false,
          lang: 'en', lang_stored: true,
          counts: {drowsy: 1, microsleep: 0, yawning: 0, head_nod: 0,
                   no_driver: 0},
          clips: {drowsy: 'embedded', microsleep: 'embedded',
                  yawning: 'embedded', head_nod: 'embedded',
                  no_driver: 'embedded'}},
  stream: {viewers: 1, quality: 80, fps: 12, port: 81},
  net: {ssid: 'DrowsyGuard-A1B2C3', ip: '192.168.4.1', clients: 1, sta: false,
        sta_ip: '0.0.0.0', rssi: 0, sta_state: 'idle', sta_bars: 0, sta_retry_ms: 0,
        button_armed: true},
  mem: {heap: 187392, psram: 7654321},
  image: {luma: 132, min: 6, max: 251},
  card: {mounted: true, events: 7, free_mb: 14812, stored: 7},
  mqtt: {enabled: true, state: 'online', published: 12, acked: 12, queued: 0,
         dropped: 0, suppressed: 0, rejected: 0, retry_ms: 0, error: ''},
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
  net: {...status.net, sta: true, sta_ip: '10.0.0.42', rssi: -58,
        sta_state: 'connected', sta_bars: 4}}));
run('station failing, backing off', () => page.render({...status,
  net: {...status.net, sta_state: 'failed', sta_retry_ms: 16000}}));
// Firmware built before the provisioning change sends none of the station fields.
// The page has to survive that rather than throw inside render() and take every
// later line of the dashboard down with it.
run('older firmware - no station fields at all', () => {
  const net = {...status.net};
  delete net.sta_state; delete net.sta_bars; delete net.sta_retry_ms;
  delete net.button_armed;
  page.render({...status, net});
});
run('every event bit set', () => page.render({...status,
  cues: {...status.cues, events: 63}}));
run('zeros everywhere', () => page.render({
  ...status, fps: 0, frames: 0,
  ms: {detect: 0, eye: 0},
  face: {found: false, held: false, x: 0, y: 0, w: 0, h: 0, score: 0,
         roi: false, roi_w: 240, rejected: 0},
  risk: {score: 0, trigger: 0.55, streak: 0, required: 8},
  eyes: {closed: 0, smooth: 0, shut: false, perclos: 0, closure_s: 0},
  cues: {...status.cues, open_index: 0, pitch_dev: 0},
  rates: {blink: 0, long_blink: 0, yawn: 0, nod: 0},
  mem: {heap: 0, psram: 0}}));

// The states the new cues exist to distinguish. Each one used to be reported
// wrongly or not at all - see behavior.h - so each gets a render.
run('landmarks held, geometry frozen', () => page.render({...status,
  face: {...status.face, held: true},
  cues: {...status.cues, stale: true}}));
run('eyes shut, mid-microsleep', () => page.render({...status,
  eyes: {closed: 0.96, smooth: 0.95, shut: true, perclos: 0.62, closure_s: 1.34},
  cues: {...status.cues, events: 4}}));
run('mouth wide open, head still', () => page.render({...status,
  cues: {...status.cues, mouth_open: true, open_index: 0.24, pitch_dev: -0.11},
  geom: {...status.geom, jaw_drop: 0.86, mouth_ratio: 0.42, nose_norm: 0.294}}));
run('head genuinely down', () => page.render({...status,
  cues: {...status.cues, head_down: true, pitch_dev: -0.12},
  geom: {...status.geom, nose_frac: 0.36, nose_norm: 0.21}}));
run('full-frame sweep, gate rejecting', () => page.render({...status,
  face: {...status.face, roi: false, roi_w: 240, rejected: 2,
         reject: 'roll-too-steep'}}));

// The states the landmark overlay and the idle gate exist for.
run('no driver present', () => page.render({...status, driver: false,
  face: {...status.face, found: false},
  lm: {...status.lm, valid: false}}));
run('driver gone but box still held', () => page.render({...status, driver: false,
  face: {...status.face, held: true}}));
run('eyes shut - lids drawn', () => page.render({...status,
  eyes: {...status.eyes, closed: 0.96, smooth: 0.95, shut: true, closure_s: 1.2}}));
run('mouth open - corners drop and close in', () => page.render({...status,
  cues: {...status.cues, mouth_open: true, open_index: 0.24},
  lm: {valid: true, x: [90, 133, 111, 101, 122], y: [86, 86, 108, 145, 145]}}));
run('landmarks absent (older firmware)', () => {
  const o = {...status}; delete o.lm; delete o.driver; page.render(o);
});
run('landmarks invalid', () => page.render({...status,
  lm: {valid: false, x: [0, 0, 0, 0, 0], y: [0, 0, 0, 0, 0]}}));
run('tiny face, marker floor applies', () => page.render({...status,
  face: {...status.face, x: 100, y: 100, w: 18, h: 18},
  lm: {valid: true, x: [103, 114, 109, 105, 112], y: [104, 104, 109, 114, 114]}}));
run('negative opening index', () => page.render({...status,
  cues: {...status.cues, open_index: -0.08}}));

// 300 ticks, to make sure the sparkline ring buffer and the event-log trim behave.
run('no card', () => page.render({...status, card: {mounted: false, events: 0, free_mb: 0, stored: 0}}));
run('card field absent (older firmware)', () => { const o = {...status}; delete o.card; page.render(o); });
// The page guards every field added after it shipped, so the guards are exercised
// too rather than being taken on trust.
run('timing and cue fields absent', () => {
  const o = {...status, cues: {...status.cues}, geom: {...status.geom},
             face: {...status.face}, eyes: {...status.eyes}};
  delete o.ms;
  delete o.cues.stale; delete o.cues.open_index; delete o.cues.pitch_dev;
  delete o.geom.nose_norm; delete o.geom.mouth_ratio;
  delete o.face.roi; delete o.face.roi_w; delete o.face.rejected;
  delete o.eyes.smooth; delete o.eyes.shut;
  delete o.lm; delete o.driver; delete o.face.reject;
  page.render(o);
});
run('stream slot taken - viewers 0', () => page.render({...status, stream: {...status.stream, viewers: 0}}));
run('openShot lightbox', () => page.openShot(
  {id: '0000042', uptime_ms: 3723456, size: 12345, risk: 0.71, perclos: 0.42, reason: 'microsleep'}));

const clipsAll = (src) => ({drowsy: src, microsleep: src, yawning: src,
                            head_nod: src, no_driver: src});
run('khmer selected, clips off the card', () => page.render({...status,
  alert: {...status.alert, lang: 'km', clips: clipsAll('card')}}));
run('khmer selected but no clips - falls back to tones', () => page.render({...status,
  alert: {...status.alert, lang: 'km', clips: clipsAll('tone')}}));
run('mixed clip sources', () => page.render({...status,
  alert: {...status.alert, lang: 'km',
          clips: {...clipsAll('card'), microsleep: 'tone', head_nod: 'embedded'}}}));
// The clip readout is built from Object.values, so a reason the page has never heard
// of has to pass through rather than throw or be silently dropped - it is the one
// readout whose job is to report a missing clip.
run('a reason the page does not know about', () => page.render({...status,
  alert: {...status.alert, clips: {...clipsAll('embedded'), something_new: 'tone'}}}));
run('alert.lang and clips absent (older firmware)', () => {
  const a = {...status.alert}; delete a.lang; delete a.clips;
  page.render({...status, alert: a});
});

// Presence. Every state the firmware can publish, including the two that are NOT
// an empty seat - a page that shows a camera fault as "no driver" would send someone
// looking for a missing person instead of a loose ribbon cable.
console.log('presence states:');
run('counting down to the no-driver alert', () => page.render({...status,
  driver: false, face: {...status.face, found: false},
  presence: {state: 'absent', health: 'ok', absent_s: 1.8, alert_after_s: 3.0,
             alerts: 0}}));
run('no driver announced', () => page.render({...status,
  driver: false, face: {...status.face, found: false},
  presence: {state: 'no-driver', health: 'ok', absent_s: 7.4, alert_after_s: 3.0,
             alerts: 1},
  alert: {...status.alert, active: true, text: 'NO DRIVER DETECTED',
          reason: 'no_driver', count: 2,
          counts: {...status.alert.counts, no_driver: 1}}}));
run('camera fault, not an empty seat', () => page.render({...status,
  camera: false, driver: false, face: {...status.face, found: false},
  presence: {state: 'fault', health: 'camera-fault', absent_s: 0.0,
             alert_after_s: 3.0, alerts: 0}}));
run('model fault, not an empty seat', () => page.render({...status,
  models: false, driver: false,
  presence: {state: 'fault', health: 'model-fault', absent_s: 0.0,
             alert_after_s: 3.0, alerts: 0}}));
run('still settling after boot', () => page.render({...status,
  presence: {state: 'warmup', health: 'ok', absent_s: 0.0, alert_after_s: 3.0,
             alerts: 0}}));
run('presence field absent (older firmware)', () => {
  const o = {...status}; delete o.presence; page.render(o);
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

// --- the MQTT settings modal --------------------------------------------- //
// The modal is twenty fields and the only place on this device where a credential is
// ever typed, so what is checked here is not "does it render" but the three
// properties that matter:
//
//   1. it survives every shape /api/mqtt can return, including an older firmware
//      that omits the object entirely;
//   2. a blank password box submits NOTHING for that field, which is what makes
//      "leave blank to keep the stored one" true rather than aspirational;
//   3. the topic preview matches what the firmware builds, including the wildcard
//      form a manual topic produces.
console.log('\nMQTT status pill:');
const mqttStates = [
  ['online, idle', {state: 'online'}],
  ['online, flushing a backlog', {state: 'online', queued: 5}],
  ['connecting', {state: 'connecting', published: 0, acked: 0}],
  ['backing off after a refusal', {state: 'backoff', retry_ms: 8000,
    error: 'broker refused the connection (return code 5)'}],
  ['transport error', {state: 'backoff', retry_ms: 32000,
    error: 'transport error (esp-tls 0x8006, socket errno 113)'}],
  ['fault - unusable configuration', {state: 'fault', error: 'invalid broker address'}],
  ['disabled', {enabled: false, state: 'disabled'}],
  ['dropping from a full outbox', {state: 'backoff', queued: 16, dropped: 9,
    suppressed: 2, rejected: 1, retry_ms: 60000, error: ''}],
];
for (const [label, over] of mqttStates) {
  run(label, () => page.render({...status, mqtt: {...status.mqtt, ...over}}));
}
run('mqtt object absent (older firmware)', () => {
  const o = {...status};
  delete o.mqtt;
  page.render(o);
});

// A settings document shaped exactly like mqtt_config_json() emits.
const mqttCfg = (over) => ({
  config: {
    enabled: true, transport: 'tls', protocol: '3.1.1', host: 'broker.emqx.io',
    port: 8883, ws_path: '/mqtt', client_id: 'drowsyguard-drowsyguard-c5e019',
    client_id_auto: true, username_masked: 'fl****er', username_set: true,
    password_set: true, qos: 1, keepalive: 30, lwt: true, retain_status: true,
    tls_insecure: false, ca_present: false, ca_bytes: 0,
    topic_mode: 'auto', topic: '', uri: 'mqtts://broker.emqx.io:8883',
    device_id: 'drowsyguard-c5e019', fleet_id: 'demo-fleet', remark: 'Driver A',
    topics: {
      alerts: 'plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/alerts',
      status: 'plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/status',
      fleet_alerts: 'plxy/drowsyguard/demo-fleet/+/alerts',
      fleet_status: 'plxy/drowsyguard/demo-fleet/+/status',
    },
    sta: {enabled: false, ssid: '', password_set: false},
    demo_broker: {host: 'broker.emqx.io', tcp: 1883, tls: 8883, ws: 8083, wss: 8084,
                  path: '/mqtt', public: true},
    ...(over || {}),
  },
  status: {
    state: 'online', client_up: true, connects: 1, disconnects: 0, attempt: 0,
    retry_ms: 0, published: 12, acked: 12, queued: 0, capacity: 16, dropped: 0,
    suppressed: 0, rejected: 0, boot_id: '9f1c2ab3', seq: 13,
    last_publish_ms: 3720000, error: '',
  },
  nvs: true,
});

console.log('\nMQTT modal against /api/mqtt:');
run('nominal', () => page.mqttFill(mqttCfg()));
run('websocket secure', () => page.mqttFill(mqttCfg({
  transport: 'wss', port: 8084, uri: 'wss://broker.emqx.io:8084/mqtt'})));
run('plain tcp - no CA field, no insecure row', () => page.mqttFill(mqttCfg({
  transport: 'tcp', port: 1883, uri: 'mqtt://broker.emqx.io:1883'})));
run('verification disabled', () => page.mqttFill(mqttCfg({tls_insecure: true})));
run('private broker with a stored CA', () => page.mqttFill(mqttCfg({
  host: 'mqtt.example.internal', ca_present: true, ca_bytes: 2114,
  uri: 'mqtts://mqtt.example.internal:8883'})));
run('manual topic', () => page.mqttFill(mqttCfg({
  topic_mode: 'manual', topic: 'fleet/lorries/lorry-7/drowsiness'})));
run('mqtt 5, qos 0, no will', () => page.mqttFill(mqttCfg({
  protocol: '5', qos: 0, lwt: false})));
run('pinned client id', () => page.mqttFill(mqttCfg({
  client_id: 'lorry-7', client_id_auto: false})));
run('no credentials stored', () => page.mqttFill(mqttCfg({
  username_masked: '', username_set: false, password_set: false})));
run('a completely empty document', () => page.mqttFill({}));
run('topics object missing', () => {
  const j = mqttCfg();
  delete j.config.topics;
  page.mqttFill(j);
});

// The password boxes must open empty whatever the device said about them, and the
// placeholder is the only thing that may mention a stored secret.
{
  page.mqttFill(mqttCfg());
  const pass = store.get('mqPass');
  const user = store.get('mqUser');
  const ok = pass.value === '' && user.value === ''
      && /leave blank/.test(pass.placeholder);
  if (!ok) failures++;
  console.log(`  ${ok ? 'ok   ' : 'FAIL '} credential boxes open empty `
      + `(pass "${pass.value}", placeholder "${pass.placeholder}")`);
}

console.log('\ntopic preview:');
const topicCase = (label, fields, want) => {
  Object.entries(fields).forEach(([k, v]) => { store.get(k).value = v; });
  const got = page.mqttTopics();
  const ok = got.device === want.device && got.fleet === want.fleet
      && got.status === want.status;
  if (!ok) failures++;
  console.log(`  ${ok ? 'ok   ' : 'FAIL '} ${label}`
      + (ok ? '' : `\n         got ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`));
};
topicCase('auto', {mqTopicMode: 'auto', mqFleetId: 'demo-fleet',
                   mqDeviceId: 'drowsyguard-c5e019'}, {
  device: 'plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/alerts',
  fleet: 'plxy/drowsyguard/demo-fleet/+/alerts',
  status: 'plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/status',
});
// Typed as a human would type it. The preview slugs it the same way the firmware
// does, so what is shown is what will be published to - not what was typed.
topicCase('auto, ids need slugging', {mqTopicMode: 'auto', mqFleetId: 'KDSB Fleet 1',
                                      mqDeviceId: 'Van 3'}, {
  device: 'plxy/drowsyguard/kdsb-fleet-1/van-3/alerts',
  fleet: 'plxy/drowsyguard/kdsb-fleet-1/+/alerts',
  status: 'plxy/drowsyguard/kdsb-fleet-1/van-3/status',
});
topicCase('auto, ids empty', {mqTopicMode: 'auto', mqFleetId: '', mqDeviceId: ''},
          {device: '', fleet: '', status: ''});
topicCase('manual ending in /alerts', {mqTopicMode: 'manual',
                                       mqTopic: 'fleet/lorries/lorry-7/alerts'}, {
  device: 'fleet/lorries/lorry-7/alerts',
  fleet: 'fleet/lorries/+/alerts',
  status: 'fleet/lorries/lorry-7/status',
});
topicCase('manual not ending in /alerts', {mqTopicMode: 'manual',
                                           mqTopic: 'sites/depot/drowsiness'}, {
  device: 'sites/depot/drowsiness',
  fleet: 'sites/+/drowsiness',
  status: 'sites/depot/drowsiness/status',
});
topicCase('manual, single level', {mqTopicMode: 'manual', mqTopic: 'drowsiness'},
          {device: 'drowsiness', fleet: 'drowsiness', status: 'drowsiness/status'});

console.log('\nsubmitted body:');
{
  page.mqttFill(mqttCfg());
  store.get('mqTopicMode').value = 'auto';
  store.get('mqHost').value = 'broker.emqx.io';
  store.get('mqPort').value = '8883';
  store.get('mqDeviceId').value = 'drowsyguard-c5e019';
  store.get('mqFleetId').value = 'demo-fleet';
  store.get('mqRemark').value = 'Driver A';
  store.get('mqPass').value = '';
  store.get('mqUser').value = '';
  store.get('mqCa').value = '';

  const body = page.mqttBody();
  const noCreds = !/(^|&)password=/.test(body) && !/(^|&)username=/.test(body)
      && !/(^|&)ca_cert=/.test(body);
  if (!noCreds) failures++;
  console.log(`  ${noCreds ? 'ok   ' : 'FAIL '} blank boxes submit no credential fields`);

  const hasCore = /(^|&)host=broker\.emqx\.io(&|$)/.test(body)
      && /(^|&)remark=Driver%20A(&|$)/.test(body)
      && /(^|&)topic_mode=auto(&|$)/.test(body);
  if (!hasCore) failures++;
  console.log(`  ${hasCore ? 'ok   ' : 'FAIL '} the other fields are present and encoded`);

  // A typed password IS sent, and percent-encoded rather than left to break the
  // body at the first '&'.
  store.get('mqPass').value = 'p&ss=w/rd +1';
  const body2 = page.mqttBody();
  const enc = body2.includes('password=' + encodeURIComponent('p&ss=w/rd +1'));
  if (!enc) failures++;
  console.log(`  ${enc ? 'ok   ' : 'FAIL '} a typed password is percent-encoded`);
  store.get('mqPass').value = '';

  // A manual topic with a wildcard in it: the page sends it and the firmware
  // rejects it. What is checked here is that the page renders that rejection against
  // the right field rather than as an unattributed sentence.
  const bodyExtra = page.mqttBody({clear_password: 1});
  const cleared = /(^|&)clear_password=1(&|$)/.test(bodyExtra);
  if (!cleared) failures++;
  console.log(`  ${cleared ? 'ok   ' : 'FAIL '} an explicit clear flag is carried`);

  // The station record has one owner. Two forms writing it meant that saving the
  // broker from a stale page put the old SSID back, silently.
  const noSta = !/(^|&)sta_/.test(body);
  if (!noSta) failures++;
  console.log(`  ${noSta ? 'ok   ' : 'FAIL '} the mqtt form carries no station fields`);
}

console.log('\nsave and test:');
const saveCase = async (label, response, ok) => {
  globalThis.fetch = () => Promise.resolve({ok: ok, json: () => response});
  try {
    const r = await page.mqttSave();
    const good = r === ok;
    if (!good) failures++;
    console.log(`  ${good ? 'ok   ' : 'FAIL '} ${label}`);
  } catch (e) {
    failures++;
    console.log('  FAIL ', label, '->', e.message);
  }
};
await saveCase('accepted', mqttCfg(), true);
await saveCase('rejected - a field the page knows',
               {error: 'no \'+\' or \'#\' wildcards', field: 'topic'}, false);
await saveCase('rejected - a field the page does not know',
               {error: 'something new', field: 'not_a_field_here'}, false);
await saveCase('rejected - no field named', {error: 'nope'}, false);
globalThis.fetch = () => Promise.reject(new Error('offline'));
await saveCase('device unreachable', {}, false);

globalThis.fetch = () => Promise.resolve({ok: true, json: () => ({
  queued: true, state: 'online', queued_depth: 1, published: 13, acked: 12, error: ''})});
run('test publish queued', () => page.mqttTest());
globalThis.fetch = () => Promise.resolve({ok: false, json: () => ({
  queued: false, state: 'disabled', queued_depth: 0, published: 0, acked: 0,
  error: ''})});
run('test publish refused - publishing is off', () => page.mqttTest());
globalThis.fetch = () => Promise.reject(new Error('offline'));
run('test publish, device unreachable', () => page.mqttTest());
globalThis.fetch = () => Promise.resolve({ok: true, json: () => mqttCfg()});
run('open the modal', () => page.openMqtt());

// The copy helper, on the path a phone on 192.168.4.1 actually takes: plain HTTP is
// not a secure context, so navigator.clipboard is unavailable and the execCommand
// fallback is what runs.
console.log('\ncopy to clipboard:');
{
  const btn = {textContent: 'Copy'};
  const done = await page.copyText('plxy/drowsyguard/demo-fleet/+/alerts', btn);
  if (!done) failures++;
  console.log(`  ${done ? 'ok   ' : 'FAIL '} execCommand fallback on plain HTTP`);
  const empty = await page.copyText('', null);
  console.log(`  ok    empty text is harmless (${empty})`);
}

// --- Wi-Fi provisioning --------------------------------------------------- //
// A document shaped exactly like wifi_respond() in web_server.cpp emits.
const wifiDoc = (sta, over) => ({
  ap: {ssid: 'DrowsyGuard-A1B2C3', ip: '192.168.4.1', clients: 1, up: true},
  sta: {
    state: 'connected', ssid: 'KDSB-Office', stored: true, connected: true,
    ip: '10.0.0.42', rssi: -58, bars: 4, attempts: 0, retry_ms: 0, reason: 0,
    reason_text: '', password_set: true, auth_failed: false,
    ...(sta || {}),
  },
  button: {gpio: 0, armed: true, held_ms: 0, hold_ms: 5000},
  nvs: true,
  ...(over || {}),
});

console.log('\nWi-Fi modal against /api/wifi:');
run('connected', () => page.wifiFill(wifiDoc()));
run('never provisioned', () => page.wifiFill(wifiDoc({
  state: 'disabled', ssid: '', stored: false, connected: false, ip: '0.0.0.0',
  rssi: 0, bars: 0, password_set: false})));
run('connecting', () => page.wifiFill(wifiDoc({
  state: 'connecting', connected: false, ip: '0.0.0.0', rssi: 0, bars: 0})));
run('wrong password', () => page.wifiFill(wifiDoc({
  state: 'failed', connected: false, ip: '0.0.0.0', rssi: 0, bars: 0,
  attempts: 3, retry_ms: 8000, reason: 15, reason_text: 'the password was refused',
  auth_failed: true})));
run('network out of range', () => page.wifiFill(wifiDoc({
  state: 'failed', connected: false, ip: '0.0.0.0', rssi: 0, bars: 0, attempts: 12,
  retry_ms: 60000, reason: 201, reason_text: 'no access point with that name is in '
      + 'range'})));
run('settings partition unavailable', () => page.wifiFill(wifiDoc({}, {nvs: false})));
run('button held down by a serial adapter', () => page.wifiFill(
    wifiDoc({}, {button: {gpio: 0, armed: false, held_ms: 0, hold_ms: 5000}})));
run('a completely empty document', () => page.wifiFill({}));
run('sta object missing entirely', () => page.wifiFill({ap: {ssid: 'x'}}));

// The password box opens empty whatever the device said, exactly as the broker's does.
{
  page.wifiFill(wifiDoc());
  const pass = store.get('wfPass');
  const ok = pass.value === '' && /leave blank/.test(pass.placeholder);
  if (!ok) failures++;
  console.log(`  ${ok ? 'ok   ' : 'FAIL '} the password box opens empty `
      + `(value "${pass.value}", placeholder "${pass.placeholder}")`);
}

// Forget and Reconnect are meaningless with nothing stored, and a button that does
// nothing when pressed is worse than one that is visibly unavailable.
{
  page.wifiFill(wifiDoc({state: 'disabled', ssid: '', stored: false,
                         connected: false, password_set: false}));
  const off = store.get('wfForget').disabled && store.get('wfReconnect').disabled;
  page.wifiFill(wifiDoc());
  const on = !store.get('wfForget').disabled && !store.get('wfReconnect').disabled;
  const ok = off && on;
  if (!ok) failures++;
  console.log(`  ${ok ? 'ok   ' : 'FAIL '} forget/reconnect follow whether anything `
      + `is stored`);
}

console.log('\nscan list:');
const scanDoc = (nets, scanning) => ({
  scanning: !!scanning, age_ms: 1200,
  networks: nets.map(n => ({
    ssid: n[0], rssi: n[1], bars: n[2], channel: 6,
    auth: n[3] ? 'open' : 'wpa2-psk', open: !!n[3],
  })),
});
run('several networks', () => page.wifiRenderNets(scanDoc([
  ['KDSB-Office', -46, 4, false], ['KDSB-Guest', -61, 3, true],
  ['Pixel_Hotspot', -72, 2, false], ['neighbour', -88, 1, false]])));
run('a scan in progress', () => page.wifiRenderNets(scanDoc([], true)));
run('nothing found', () => page.wifiRenderNets(scanDoc([])));
run('a document with no networks key', () => page.wifiRenderNets({scanning: false}));
run('an empty document', () => page.wifiRenderNets({}));
run('undefined', () => page.wifiRenderNets(undefined));

// An SSID is 32 bytes chosen by whoever owns the access point, and anybody in radio
// range of this device can broadcast one. The firmware escapes it for JSON; that is a
// different job from escaping it for HTML, and this is the page's half.
{
  const hostile = '<img src=x onerror="alert(1)">';
  page.wifiRenderNets(scanDoc([[hostile, -50, 4, false]]));
  const html = store.get('wfNets').innerHTML;
  const safe = !html.includes('<img') && html.includes('&lt;img')
      && !html.includes('onerror="');
  if (!safe) failures++;
  console.log(`  ${safe ? 'ok   ' : 'FAIL '} a hostile SSID is escaped into text`);

  const quoted = 'a" onclick="steal()';
  page.wifiRenderNets(scanDoc([[quoted, -50, 4, false]]));
  const html2 = store.get('wfNets').innerHTML;
  const safe2 = !html2.includes('onclick="') && html2.includes('&quot;');
  if (!safe2) failures++;
  console.log(`  ${safe2 ? 'ok   ' : 'FAIL '} a quote in an SSID cannot break out of `
      + `an attribute`);
}

// Selecting a row fills the two boxes - and must NOT carry the previous network's
// passphrase across to the one just chosen.
{
  page.wifiRenderNets(scanDoc([
    ['KDSB-Office', -46, 4, false], ['KDSB-Guest', -61, 3, true]]));
  store.get('wfPass').value = 'the-office-password';
  const rows = store.get('wfNets').querySelectorAll('button');
  const wired = rows.length === 2 && typeof rows[1].onclick === 'function';
  if (!wired) failures++;
  console.log(`  ${wired ? 'ok   ' : 'FAIL '} every row is wired (${rows.length} rows)`);
  if (wired) {
    rows[1].onclick();
    const ok = store.get('wfSsid').value === 'KDSB-Guest'
        && store.get('wfPass').value === '' && store.get('wfOpen').checked === true;
    if (!ok) failures++;
    console.log(`  ${ok ? 'ok   ' : 'FAIL '} picking an open network clears the `
        + `password box (ssid "${store.get('wfSsid').value}", pass `
        + `"${store.get('wfPass').value}")`);
  }
}

console.log('\nWi-Fi actions:');
const wifiCase = async (label, fn, response, ok, check) => {
  globalThis.fetch = () => Promise.resolve({ok: ok, json: () => response});
  try {
    await fn();
    const good = check ? check() : true;
    if (!good) failures++;
    console.log(`  ${good ? 'ok   ' : 'FAIL '} ${label}`);
  } catch (e) {
    failures++;
    console.log('  FAIL ', label, '->', e.message);
  }
};
// A typed password is sent percent-encoded, and a blank one is not sent at all -
// which is what makes "leave blank to keep" work against a device that never hands
// the secret back.
{
  let seen = '';
  globalThis.fetch = (url, opt) => {
    const post = opt && opt.method === 'POST';
    // Only the POST bodies to /api/wifi. A save also triggers a status poll, and
    // recording that GET afterwards blanked the body this is checking.
    if (post && String(url) === '/api/wifi') seen = opt.body || '';
    if (String(url).startsWith('/api/status')) {
      return Promise.resolve({ok: true, json: () => status});
    }
    return Promise.resolve({ok: true, json: () => wifiDoc()});
  };
  page.wifiFill(wifiDoc());
  store.get('wfSsid').value = 'KDSB-Office';
  store.get('wfPass').value = '';
  store.get('wfOpen').checked = false;
  await store.get('wfConnect').onclick();
  const blank = /(^|&)ssid=KDSB-Office(&|$)/.test(seen) && !/(^|&)password=/.test(seen);
  if (!blank) failures++;
  console.log(`  ${blank ? 'ok   ' : 'FAIL '} a blank box submits no password `
      + `("${seen}")`);

  store.get('wfPass').value = 'p&ss=w/rd +1';
  await store.get('wfConnect').onclick();
  const enc = seen.includes('password=' + encodeURIComponent('p&ss=w/rd +1'));
  if (!enc) failures++;
  console.log(`  ${enc ? 'ok   ' : 'FAIL '} a typed password is percent-encoded`);

  // An open network is explicit rather than inferred, so the stored passphrase from
  // the last network is not handed to an access point anybody can stand up.
  store.get('wfOpen').checked = true;
  store.get('wfPass').value = 'still-here';
  await store.get('wfConnect').onclick();
  const open = /(^|&)open=1(&|$)/.test(seen) && !/(^|&)password=/.test(seen);
  if (!open) failures++;
  console.log(`  ${open ? 'ok   ' : 'FAIL '} an open network sends open=1 and no `
      + `password ("${seen}")`);
  store.get('wfOpen').checked = false;
  store.get('wfPass').value = '';

  await store.get('wfForget').onclick();
  const forget = /(^|&)action=forget(&|$)/.test(seen);
  if (!forget) failures++;
  console.log(`  ${forget ? 'ok   ' : 'FAIL '} forget posts action=forget`);

  await store.get('wfReconnect').onclick();
  const again = /(^|&)action=reconnect(&|$)/.test(seen);
  if (!again) failures++;
  console.log(`  ${again ? 'ok   ' : 'FAIL '} reconnect posts action=reconnect`);
}
// An empty SSID never reaches the device: it would be stored and then retried
// forever against a network that does not exist.
{
  let called = false;
  globalThis.fetch = () => { called = true; return Promise.reject(new Error('x')); };
  store.get('wfSsid').value = '   ';
  await store.get('wfConnect').onclick();
  if (called) failures++;
  console.log(`  ${called ? 'FAIL ' : 'ok   '} an empty SSID is refused locally`);
}
await wifiCase('rejected - a field the page knows', () => page.wifiFill(wifiDoc()),
               {error: 'at most 32 characters', field: 'ssid'}, false);
globalThis.fetch = () => Promise.resolve({ok: true, json: () => wifiDoc()});
await wifiCase('open the modal', () => page.openWifi(), wifiDoc(), true);
await wifiCase('start a scan', () => page.wifiScan(),
               {scanning: true, age_ms: 0, networks: []}, true);
globalThis.fetch = () => Promise.reject(new Error('offline'));
await wifiCase('device unreachable during a scan', () => page.wifiScan(), {}, false);
await wifiCase('device unreachable during a load', () => page.wifiLoad(), {}, false);
globalThis.fetch = () => Promise.resolve({ok: true, json: () => ({})});

console.log(failures ? `\n${failures} failure(s)` : '\nall paths clean');
process.exit(failures ? 1 : 0);
