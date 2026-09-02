// Exercise docs/assets/js/fleet.js against real MQTT bytes and hostile payloads,
// with a stub DOM. Run by tests/test_fleet_page.py; also runnable directly:
//
//     node tests/fleet_page_harness.mjs
//
// The fleet monitor is a hand-written MQTT client and a renderer for strings a
// stranger chose, published on the project's own documentation site. Those are the two
// things this exists for:
//
//   * the codec. Every packet below is checked byte for byte against the MQTT 3.1.1
//     and MQTT 5 specifications rather than against "it worked once with EMQX". A
//     wrong reserved bit or a mis-skipped MQTT 5 property length produces a client
//     that connects and then silently receives nothing, which is indistinguishable
//     from a device that is not publishing;
//   * the sanitising. The default broker is shared with the entire internet, so every
//     field is attacker-controlled. What is tested is not "does it render" but that a
//     document which is not a DrowsyGuard alert is refused, that a remark cannot
//     escape into markup or reverse the page's text direction, and that a risk of
//     1e308 becomes a number.
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..');
const JS = fs.readFileSync(path.join(ROOT, 'docs/assets/js/fleet.js'), 'utf8');
const PAGE = fs.readFileSync(path.join(ROOT, 'docs/fleet-monitoring.md'), 'utf8');

// --- minimal DOM ---------------------------------------------------------- //
// Built from the ids the page actually contains, so a rename in the markdown breaks
// the harness rather than being papered over by a stub that answers every id.
const ids = [...PAGE.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
const copyTargets = [...PAGE.matchAll(/data-fleet-copy="([^"]+)"/g)].map((m) => m[1]);

let created = 0;
const mkEl = (id) => {
  const el = {
    id,
    tagName: 'DIV',
    className: '',
    title: '',
    placeholder: '',
    checked: false,
    hidden: false,
    readOnly: false,
    dataset: {},
    children: [],
    style: new Proxy({}, {set: () => true, get: () => ''}),
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      toggle(c) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); },
      contains(c) { return this._set.has(c); },
    },
    _listeners: {},
    addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); },
    // Tracked rather than ignored: the tests below count rendered cards and read
    // their text back, which is the only way to check that a hostile remark reached
    // the page as text.
    append(...kids) { for (const k of kids) this.children.push(k); },
    appendChild(k) { this.children.push(k); return k; },
    removeChild(k) { this.children = this.children.filter((c) => c !== k); },
    remove() {},
    setAttribute(k, v) { this['_attr_' + k] = v; if (k === 'hidden') this.hidden = true; },
    getAttribute(k) {
      if (k === 'data-fleet-copy') return this.dataset.copy ?? null;
      return this['_attr_' + k] ?? null;
    },
    removeAttribute() {},
    select() {}, setSelectionRange() {}, focus() {},
    fire(ev, arg) { for (const fn of this._listeners[ev] || []) fn(arg ?? {target: el}); },
  };
  // Two DOM behaviours the first version of this stub got wrong, and both of them
  // made a test pass that should have failed:
  //
  //   * `el.textContent = ''` REMOVES the children. renderDevices() clears the grid
  //     that way, so a plain property left every previous render's cards in place and
  //     the card counts below were the running total;
  //   * `input.value = 65535` stores the STRING "65535". loadSettings() assigns
  //     numbers, and code that reads them back has to see what a browser would.
  let _text = '';
  let _value = '';
  Object.defineProperty(el, 'textContent', {
    get() { return _text; },
    set(v) {
      _text = v === undefined || v === null ? '' : String(v);
      if (_text === '') el.children = [];
    },
    enumerable: true,
  });
  Object.defineProperty(el, 'value', {
    get() { return _value; },
    set(v) { _value = v === undefined || v === null ? '' : String(v); },
    enumerable: true,
  });
  return el;
};

const store = new Map(ids.map((i) => [i, mkEl(i)]));
const copyButtons = copyTargets.map((target) => {
  const b = mkEl('copy-' + target);
  b.dataset.copy = target;
  b.textContent = 'Copy';
  return b;
});

globalThis.document = {
  getElementById: (i) => store.get(i) ?? null,
  createElement: (tag) => {
    created++;
    const el = mkEl('created-' + created);
    el.tagName = String(tag).toUpperCase();
    return el;
  },
  querySelectorAll: (sel) =>
    String(sel).includes('data-fleet-copy') ? copyButtons : [],
  addEventListener() {},
  body: {appendChild() {}, removeChild() {}},
  execCommand: () => true,
};

globalThis.window = {isSecureContext: false};
globalThis.location = {protocol: 'https:', hostname: 'sengphirum.github.io'};
// Getter-only in Node, so it has to be defined rather than assigned. Left without a
// clipboard so the execCommand fallback - the path taken by `mkdocs serve` over plain
// HTTP - is the one exercised.
Object.defineProperty(globalThis, 'navigator', {value: {}, configurable: true});
const stored = new Map();
globalThis.localStorage = {
  getItem: (k) => (stored.has(k) ? stored.get(k) : null),
  setItem: (k, v) => stored.set(k, String(v)),
  removeItem: (k) => stored.delete(k),
};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.setTimeout = (fn) => { if (typeof fn === 'function') fn(); return 0; };
globalThis.clearTimeout = () => {};
// Deliberately absent, so render() takes its synchronous path and the assertions
// below can read the DOM immediately after the call that changed it.
delete globalThis.requestAnimationFrame;

// --- load the page's script ---------------------------------------------- //
new Function(JS)();
const F = store.get('fleet-app').__fleet;
if (!F) {
  console.log('  FAIL  fleet.js exported nothing - did #fleet-app change name?');
  process.exit(1);
}

let failures = 0;
const run = (label, fn) => {
  try { fn(); console.log('  ok   ', label); }
  catch (e) { failures++; console.log('  FAIL ', label, '->', e.message); }
};
const eq = (got, want, what) => {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) throw new Error(`${what}: got ${a}, want ${b}`);
};
const truthy = (v, what) => { if (!v) throw new Error(what); };
const bytes = (...v) => v;
const hex = (a) => [...a].map((b) => b.toString(16).padStart(2, '0')).join(' ');
const eqBytes = (got, want, what) => {
  const g = hex(got);
  const w = hex(want);
  if (g !== w) throw new Error(`${what}:\n         got  ${g}\n         want ${w}`);
};
const str = (s) => [...new TextEncoder().encode(s)];
const flatten = (el) => {
  let out = el.textContent || '';
  for (const k of el.children || []) out += ' ' + flatten(k);
  return out;
};

// ========================================================================== //
// the codec, against the specifications rather than against one broker
// ========================================================================== //
console.log('remaining-length varint:');
run('every boundary the spec names', () => {
  eq(F.encodeVarInt(0), [0x00], '0');
  eq(F.encodeVarInt(1), [0x01], '1');
  eq(F.encodeVarInt(127), [0x7f], '127');
  eq(F.encodeVarInt(128), [0x80, 0x01], '128');
  eq(F.encodeVarInt(16383), [0xff, 0x7f], '16383');
  eq(F.encodeVarInt(16384), [0x80, 0x80, 0x01], '16384');
  eq(F.encodeVarInt(2097151), [0xff, 0xff, 0x7f], '2097151');
  eq(F.encodeVarInt(2097152), [0x80, 0x80, 0x80, 0x01], '2097152');
  eq(F.encodeVarInt(268435455), [0xff, 0xff, 0xff, 0x7f], 'the maximum');
});
run('a value past the maximum is refused', () => {
  let threw = false;
  try { F.encodeVarInt(268435456); } catch (e) { threw = true; }
  truthy(threw, 'encodeVarInt accepted an out-of-range length');
});
run('decoding round-trips', () => {
  for (const n of [0, 1, 127, 128, 16383, 16384, 2097151, 268435455]) {
    const enc = new Uint8Array(F.encodeVarInt(n));
    eq(F.decodeVarInt(enc, 0), {value: n, bytes: enc.length}, `varint ${n}`);
  }
});
run('an incomplete varint returns null rather than guessing', () => {
  // Normal, not exceptional: a WebSocket frame boundary has nothing to do with a
  // packet boundary, so a partial header arrives regularly.
  eq(F.decodeVarInt(new Uint8Array([0x80]), 0), null, 'one continuation byte');
  eq(F.decodeVarInt(new Uint8Array([0x80, 0x80]), 0), null, 'two');
  eq(F.decodeVarInt(new Uint8Array([]), 0), null, 'empty');
});
run('a five-byte varint is malformed and says so', () => {
  let threw = false;
  try { F.decodeVarInt(new Uint8Array([0x80, 0x80, 0x80, 0x80, 0x01]), 0); }
  catch (e) { threw = true; }
  truthy(threw, 'a 5-byte length was accepted');
});

console.log('\nCONNECT:');
run('MQTT 3.1.1, no credentials', () => {
  eqBytes(F.encodeConnect({clientId: 'abc', keepalive: 60, version: 4}),
      bytes(0x10, 0x0f,
            0x00, 0x04, 0x4d, 0x51, 0x54, 0x54,   // "MQTT"
            0x04,                                  // level 4 = 3.1.1
            0x02,                                  // clean session
            0x00, 0x3c,                            // keepalive 60
            0x00, 0x03, 0x61, 0x62, 0x63),         // "abc"
      'CONNECT 3.1.1');
});
run('MQTT 5 adds exactly one property-length byte', () => {
  const v5 = F.encodeConnect({clientId: 'abc', keepalive: 60, version: 5});
  eqBytes(v5,
      bytes(0x10, 0x10,
            0x00, 0x04, 0x4d, 0x51, 0x54, 0x54,
            0x05,                                  // level 5
            0x02,
            0x00, 0x3c,
            0x00,                                  // no properties
            0x00, 0x03, 0x61, 0x62, 0x63),
      'CONNECT 5');
});
run('credentials set both flags and appear in order', () => {
  const p = F.encodeConnect({clientId: 'c', username: 'u', password: 'p',
                             keepalive: 60, version: 4});
  eqBytes(p,
      bytes(0x10, 0x13,
            0x00, 0x04, 0x4d, 0x51, 0x54, 0x54, 0x04,
            0xc2,                                  // clean + user + password
            0x00, 0x3c,
            0x00, 0x01, 0x63,                      // "c"
            0x00, 0x01, 0x75,                      // "u"
            0x00, 0x01, 0x70),                     // "p"
      'CONNECT with credentials');
});
run('a password with no username is not sent', () => {
  // The MQTT 3.1.1 spec forbids it, and a broker's answer to it is a bare
  // "not authorized" that sends people looking at their credentials.
  const p = F.encodeConnect({clientId: 'c', password: 'p', keepalive: 60, version: 4});
  eq(p[9], 0x02, 'connect flags');
  eq(p.length, 15, 'no password in the payload');
});
run('the keepalive is clamped into the wire range', () => {
  // 0 means "no keepalive" in MQTT, so it becomes the default rather than being
  // clamped up: the ping timer is half of whatever is announced here, and a client
  // that announced 0 and then pinged anyway is a client relying on the broker's
  // patience.
  eq(F.encodeConnect({clientId: 'c', keepalive: 0, version: 4})[11], 60, 'default');
  eq(F.encodeConnect({clientId: 'c', keepalive: 5, version: 4})[11], 10, 'floor');
  const big = F.encodeConnect({clientId: 'c', keepalive: 999999, version: 4});
  eq([big[10], big[11]], [0xff, 0xff], 'ceiling');
});
run('a non-ASCII client id is length-prefixed in BYTES', () => {
  // The length field counts UTF-8 bytes, not characters. Getting this wrong makes
  // every packet after it unparseable, and only for people with non-ASCII names.
  const p = F.encodeConnect({clientId: 'vän', keepalive: 60, version: 4});
  eq([p[12], p[13]], [0x00, 0x04], 'four bytes for three characters');
});

console.log('\nSUBSCRIBE, PUBACK, PING, DISCONNECT:');
run('SUBSCRIBE sets the reserved bits the spec requires', () => {
  const s = F.encodeSubscribe('a/b', 1, 1, 4);
  eqBytes(s,
      bytes(0x82,                                  // SUBSCRIBE | 0010, not 0x80
            0x08,
            0x00, 0x01,                            // packet id
            0x00, 0x03, 0x61, 0x2f, 0x62,          // "a/b"
            0x01),                                 // qos 1
      'SUBSCRIBE 3.1.1');
});
run('SUBSCRIBE in MQTT 5 carries a property length', () => {
  eqBytes(F.encodeSubscribe('a/b', 1, 1, 5),
      bytes(0x82, 0x09, 0x00, 0x01, 0x00, 0x00, 0x03, 0x61, 0x2f, 0x62, 0x01),
      'SUBSCRIBE 5');
});
run('a wildcard subscription encodes unchanged', () => {
  const topic = 'plxy/drowsyguard/demo-fleet/+/alerts';
  const s = F.encodeSubscribe(topic, 1, 42, 4);
  eq([...s.subarray(6, 6 + topic.length)], str(topic), 'the topic bytes');
});
run('PUBACK is the two bytes both versions accept', () => {
  eqBytes(F.encodePuback(7), bytes(0x40, 0x02, 0x00, 0x07), 'PUBACK');
  eqBytes(F.encodePuback(65535), bytes(0x40, 0x02, 0xff, 0xff), 'PUBACK max id');
});
run('PINGREQ and DISCONNECT', () => {
  eqBytes(F.encodePingreq(), bytes(0xc0, 0x00), 'PINGREQ');
  eqBytes(F.encodeDisconnect(), bytes(0xe0, 0x00), 'DISCONNECT');
});

console.log('\nincoming packets:');
run('CONNACK accepted', () => {
  const out = F.decodePackets(new Uint8Array([0x20, 0x02, 0x00, 0x00]), 4);
  eq(out.consumed, 4, 'consumed');
  eq(out.packets[0], {type: F.T.CONNACK, code: 0, sessionPresent: false}, 'CONNACK');
});
run('CONNACK refused, with the code', () => {
  const out = F.decodePackets(new Uint8Array([0x20, 0x02, 0x00, 0x05]), 4);
  eq(out.packets[0].code, 5, 'not authorised');
});
run('CONNACK with a session already present', () => {
  const out = F.decodePackets(new Uint8Array([0x20, 0x02, 0x01, 0x00]), 4);
  eq(out.packets[0].sessionPresent, true, 'session present');
});
run('SUBACK granted', () => {
  const out = F.decodePackets(new Uint8Array([0x90, 0x03, 0x00, 0x01, 0x01]), 4);
  eq(out.packets[0], {type: F.T.SUBACK, granted: 1, ok: true}, 'SUBACK');
});
run('SUBACK refused - 0x80 in 3.1.1, and anything above it in 5', () => {
  // A broker that refuses a subscription answers here rather than by closing the
  // socket. A client that ignored it would sit there looking connected and receiving
  // nothing, which is the failure this whole page would then be blamed for.
  for (const code of [0x80, 0x87, 0x8f, 0x9f]) {
    const out = F.decodePackets(new Uint8Array([0x90, 0x03, 0x00, 0x01, code]), 4);
    eq(out.packets[0].ok, false, `code 0x${code.toString(16)}`);
  }
});
run('PINGRESP', () => {
  const out = F.decodePackets(new Uint8Array([0xd0, 0x00]), 4);
  eq(out.packets[0].type, F.T.PINGRESP, 'PINGRESP');
});
run('PUBLISH at QoS 1 in MQTT 3.1.1', () => {
  const body = [...str('t').length ? [] : []];
  void body;
  const pkt = new Uint8Array([
    0x32, 0x07,               // PUBLISH, qos 1
    0x00, 0x01, 0x74,         // topic "t"
    0x00, 0x05,               // packet id 5
    0x7b, 0x7d,               // "{}"
  ]);
  const out = F.decodePackets(pkt, 4);
  eq(out.packets[0].topic, 't', 'topic');
  eq(out.packets[0].qos, 1, 'qos');
  eq(out.packets[0].packetId, 5, 'packet id');
  eq(out.packets[0].payload, '{}', 'payload');
});
run('PUBLISH at QoS 0 has no packet id to skip', () => {
  const out = F.decodePackets(new Uint8Array([
    0x30, 0x05, 0x00, 0x01, 0x74, 0x7b, 0x7d]), 4);
  eq(out.packets[0].packetId, 0, 'no packet id');
  eq(out.packets[0].payload, '{}', 'payload');
});
run('PUBLISH in MQTT 5 skips the property block exactly', () => {
  // The failure this catches is silent and total: one byte of drift turns the first
  // byte of the payload into a property and the rest into a document that will not
  // parse, on every message.
  const out = F.decodePackets(new Uint8Array([
    0x30, 0x06, 0x00, 0x01, 0x74,
    0x00,                       // property length 0
    0x7b, 0x7d]), 5);
  eq(out.packets[0].payload, '{}', 'no properties');

  const withProps = F.decodePackets(new Uint8Array([
    0x30, 0x0a, 0x00, 0x01, 0x74,
    0x04,                       // property length 4
    0x01, 0x01, 0x23, 0x00,     // payload-format-indicator + a byte we ignore
    0x7b, 0x7d]), 5);
  eq(withProps.packets[0].payload, '{}', 'with properties');
});
run('retain and dup flags are reported', () => {
  const out = F.decodePackets(new Uint8Array([
    0x3b, 0x05, 0x00, 0x01, 0x74, 0x7b, 0x7d]), 4);
  eq(out.packets[0].retain, true, 'retain');
  eq(out.packets[0].dup, true, 'dup');
  eq(out.packets[0].qos, 1, 'qos');
});
run('a long payload uses a multi-byte remaining length', () => {
  const payload = 'x'.repeat(300);
  const topic = str('t');
  const body = [0x00, 0x01, ...topic, ...str(payload)];
  const pkt = new Uint8Array([0x30, ...F.encodeVarInt(body.length), ...body]);
  const out = F.decodePackets(pkt, 4);
  eq(out.packets[0].payload.length, 300, 'payload length');
  eq(out.consumed, pkt.length, 'consumed');
});

console.log('\nframing:');
run('a packet split across three WebSocket frames', () => {
  const whole = new Uint8Array([0x20, 0x02, 0x00, 0x00, 0xd0, 0x00]);
  for (const cut of [1, 2, 3, 4, 5]) {
    const first = F.decodePackets(whole.subarray(0, cut), 4);
    const rest = whole.subarray(first.consumed);
    const second = F.decodePackets(rest, 4);
    const total = first.packets.length + second.packets.length;
    if (total !== 2) throw new Error(`cut at ${cut}: got ${total} packets`);
  }
});
run('two packets in one frame', () => {
  const out = F.decodePackets(new Uint8Array([
    0x20, 0x02, 0x00, 0x00, 0x90, 0x03, 0x00, 0x01, 0x01]), 4);
  eq(out.packets.length, 2, 'both packets');
  eq(out.consumed, 9, 'consumed everything');
});
run('a trailing partial packet is left in the buffer', () => {
  const out = F.decodePackets(new Uint8Array([
    0x20, 0x02, 0x00, 0x00, 0x30, 0x20, 0x00]), 4);
  eq(out.packets.length, 1, 'one whole packet');
  eq(out.consumed, 4, 'the partial one is not consumed');
});
run('a truncated string inside a complete packet throws', () => {
  // The offsets are lost at that point, so continuing would present noise as data.
  let threw = false;
  try {
    F.decodePackets(new Uint8Array([0x30, 0x02, 0x00, 0x09]), 4);
  } catch (e) { threw = true; }
  truthy(threw, 'a lying string length was accepted');
});
run('invalid UTF-8 in a topic becomes U+FFFD rather than throwing', () => {
  const out = F.decodePackets(new Uint8Array([
    0x30, 0x04, 0x00, 0x02, 0xff, 0xfe]), 4);
  truthy(out.packets[0].topic.length > 0, 'the topic was dropped entirely');
});

// ========================================================================== //
// sanitising - the default broker is shared with the entire internet
// ========================================================================== //
console.log('\nclean():');
run('control characters, C1 codes and bidi overrides are removed', () => {
  eq(F.clean('Driver A', 48), 'Driver A', 'plain');
  eq(F.clean('Driver A', 48), 'DriverA', 'NUL');
  eq(F.clean('line1\nline2', 48), 'line1line2', 'newline');
  eq(F.clean('tab\there', 48), 'tabhere', 'tab');
  eq(F.clean('ab', 48), 'ab', 'C1');
  // The one worth naming: U+202E reverses the rendering of everything after it, so a
  // remark containing one rewrites the rest of the card.
  eq(F.clean('Driver‮A', 48), 'DriverA', 'right-to-left override');
  eq(F.clean('a⁦b⁩c', 48), 'abc', 'bidi isolates');
  eq(F.clean('a​b', 48), 'ab', 'zero-width space');
  eq(F.clean('  padded  ', 48), 'padded', 'trimmed');
  eq(F.clean(undefined, 48), '', 'undefined');
  eq(F.clean(null, 48), '', 'null');
  eq(F.clean(42, 48), '', 'a number is not a string');
  eq(F.clean({}, 48), '', 'an object is not a string');
});
run('length is capped', () => {
  eq(F.clean('x'.repeat(500), 10).length, 10, 'capped');
});
run('markup survives as text, because it is only ever rendered as text', () => {
  // It is NOT stripped, and that is deliberate: stripping is a blocklist and
  // textContent is a structural guarantee. What matters is that it stays a string.
  eq(F.clean('<script>alert(1)</script>', 48), '<script>alert(1)</script>', 'kept');
  eq(F.clean('"><img src=x onerror=alert(1)>', 48), '"><img src=x onerror=alert(1)>',
     'kept');
});

console.log('\nnum():');
run('numbers are coerced and clamped', () => {
  eq(F.num(0.5, 0, 1, 0), 0.5, 'in range');
  eq(F.num('0.5', 0, 1, 0), 0.5, 'a numeric string');
  eq(F.num(1e308, 0, 1, 0), 1, 'clamped high');
  eq(F.num(-5, 0, 1, 0), 0, 'clamped low');
  eq(F.num(NaN, 0, 1, -1), -1, 'NaN falls back');
  // Non-finite falls back rather than clamping, matching the firmware's own
  // json_num(): an infinity in a risk field is not a confident reading to clamp, it
  // is arithmetic that produced nothing. (JSON cannot carry one anyway - this
  // matters for the direct callers.)
  eq(F.num(Infinity, 0, 1, -1), -1, 'infinity falls back');
  eq(F.num(-Infinity, 0, 1, -1), -1, 'negative infinity falls back');
  eq(F.num('not a number', 0, 1, -1), -1, 'garbage falls back');
  eq(F.num(null, 0, 1, -1), -1, 'null falls back');
  eq(F.num([], 0, 1, -1), -1, 'an array falls back');
});

console.log('\nparseAlert():');
const ALERT = {
  schema: 'drowsyguard.alert.v1',
  event_id: 'drowsyguard-c5e019-9f1c2ab3-000042',
  seq: 42,
  device_id: 'drowsyguard-c5e019',
  fleet_id: 'demo-fleet',
  remark: 'Driver A',
  alert: 'microsleep',
  severity: 'critical',
  risk: 0.712,
  perclos: 0.421,
  alert_count: 12,
  uptime_ms: 3723456,
  ts: '2026-09-01T11:15:03Z',
  ts_source: 'sntp',
};
const TOPIC = 'plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/alerts';
const parse = (over) => F.parseAlert(TOPIC, JSON.stringify({...ALERT, ...over}));

run('the firmware payload parses to every field', () => {
  const ev = parse();
  eq(ev.deviceId, 'drowsyguard-c5e019', 'device id');
  eq(ev.remark, 'Driver A', 'remark');
  eq(ev.alert, 'microsleep', 'alert');
  eq(ev.severity, 'critical', 'severity');
  eq(ev.risk, 0.712, 'risk');
  eq(ev.perclos, 0.421, 'perclos');
  eq(ev.alertCount, 12, 'alert count');
  eq(ev.uptimeMs, 3723456, 'uptime');
  eq(ev.ts, '2026-09-01T11:15:03Z', 'timestamp');
  eq(ev.eventId, ALERT.event_id, 'event id');
  truthy(ev.seenAt > 0, 'seenAt was not set');
});
run('a device with no clock reports uptime instead of a timestamp', () => {
  const ev = parse({ts: '', ts_source: 'uptime'});
  eq(ev.ts, '', 'empty timestamp');
  eq(ev.tsSource, 'uptime', 'source');
});
run('a payload with no device_id falls back to the topic', () => {
  // What a hand-crafted `mosquitto_pub` test message usually is.
  const ev = F.parseAlert(TOPIC, JSON.stringify({alert: 'drowsy', risk: 0.6}));
  eq(ev.deviceId, 'drowsyguard-c5e019', 'from the topic');
});
run('an unknown alert type passes through as text', () => {
  eq(parse({alert: 'something-new'}).alert, 'something-new', 'kept');
});
run('an unknown severity becomes info rather than being trusted', () => {
  eq(parse({severity: 'apocalyptic'}).severity, 'info', 'coerced');
  eq(parse({severity: 42}).severity, 'info', 'not even a string');
});

console.log('\nparseAlert() rejects:');
const rejects = [
  ['not JSON at all', 'hello'],
  ['empty', ''],
  ['a bare number', '42'],
  ['a bare string', '"hello"'],
  ['null', 'null'],
  ['an array', '[{"device_id":"a"}]'],
  ['a foreign schema', JSON.stringify({schema: 'someone.else.v1', device_id: 'a'})],
  ['a future major schema', JSON.stringify({schema: 'drowsyguardX.alert.v1',
                                            device_id: 'a'})],
  ['no device id and a topic with no device level',
   null],
];
for (const [label, text] of rejects) {
  run(label, () => {
    const got = text === null
        ? F.parseAlert('alerts', JSON.stringify({alert: 'drowsy'}))
        : F.parseAlert(TOPIC, text);
    truthy(got === null, 'it was accepted');
  });
}
run('a v2 schema is accepted - the prefix is what is checked', () => {
  // Deliberate: a minor schema bump should keep rendering the fields it still has,
  // rather than blanking a fleet because one field was added.
  truthy(F.parseAlert(TOPIC, JSON.stringify({...ALERT, schema: 'drowsyguard.alert.v2'}))
      !== null, 'v2 was rejected');
});

console.log('\nhostile payloads:');
run('a remark full of markup arrives as text and renders as text', () => {
  const nasty = '<img src=x onerror="alert(1)">';
  const ev = parse({remark: nasty});
  eq(ev.remark, nasty, 'preserved as a string');
  F.state.devices.clear();
  F.state.events.length = 0;
  F.state.seenEventIds.clear();
  F.ingestAlert(ev);
  F.render();
  const text = flatten(store.get('fleet-devices'));
  truthy(text.includes(nasty), 'the remark did not reach the card');
  // The structural guarantee, not a filter: every node was made with createElement
  // and filled with textContent, so there is no parse step for markup to survive.
  // A property ACCESS, not the word: the file's own comments say "there is no
  // innerHTML in this file", and a check that matched those would be a check nobody
  // could keep green.
  truthy(!/\.\s*innerHTML\s*=/.test(JS), 'fleet.js assigns innerHTML');
  truthy(!/\.\s*outerHTML\s*=/.test(JS), 'fleet.js assigns outerHTML');
  truthy(!/insertAdjacentHTML\s*\(/.test(JS), 'fleet.js calls insertAdjacentHTML');
  truthy(!/document\.write/.test(JS), 'fleet.js calls document.write');
});
run('a risk of 1e308 becomes 1 and a NaN becomes 0', () => {
  eq(parse({risk: 1e308}).risk, 1, 'clamped');
  eq(F.parseAlert(TOPIC, '{"device_id":"a","risk":"nope"}').risk, 0, 'coerced');
});
run('a kilometre-long device id is capped', () => {
  eq(parse({device_id: 'd'.repeat(5000)}).deviceId.length, 32, 'capped');
});
run('a deeply nested payload does not crash the parser', () => {
  let deep = '1';
  for (let i = 0; i < 200; i++) deep = `[${deep}]`;
  truthy(F.parseAlert(TOPIC, deep) === null, 'it was accepted');
});
run('a payload whose fields are objects', () => {
  const got = F.parseAlert(TOPIC, JSON.stringify({
    device_id: {}, remark: [], risk: {}, alert: null, severity: {}}));
  // device_id is unusable, so the topic supplies it; everything else degrades.
  eq(got.deviceId, 'drowsyguard-c5e019', 'device id from the topic');
  eq(got.remark, '', 'remark');
  eq(got.risk, 0, 'risk');
});

console.log('\nparseStatus():');
run('the online document', () => {
  const st = F.parseStatus('plxy/drowsyguard/demo-fleet/dev-1/status', JSON.stringify({
    schema: 'drowsyguard.status.v1', device_id: 'dev-1', fleet_id: 'demo-fleet',
    remark: 'Driver A', online: true, reason: 'connected', uptime_ms: 100,
    alert_count: 3}));
  eq(st.online, true, 'online');
  eq(st.reason, 'connected', 'reason');
});
run('the will document', () => {
  const st = F.parseStatus('plxy/drowsyguard/demo-fleet/dev-1/status', JSON.stringify({
    schema: 'drowsyguard.status.v1', device_id: 'dev-1', online: false,
    reason: 'last-will', uptime_ms: 0, alert_count: 0}));
  eq(st.online, false, 'offline');
  eq(st.reason, 'last-will', 'reason');
});
run('online is only true when it is literally true', () => {
  // A truthy string would otherwise mark a vanished device as present, which is the
  // exact confusion the Last Will exists to remove.
  for (const v of ['true', 1, 'yes', {}]) {
    const st = F.parseStatus('a/b/status', JSON.stringify({device_id: 'd', online: v}));
    eq(st.online, false, `online: ${JSON.stringify(v)}`);
  }
});
run('a foreign status schema is rejected', () => {
  truthy(F.parseStatus('a/b/status',
      JSON.stringify({schema: 'other.status.v1', device_id: 'd'})) === null,
      'accepted');
});

// ========================================================================== //
// state, de-duplication, filters, rendering
// ========================================================================== //
const reset = () => {
  F.state.devices.clear();
  F.state.events.length = 0;
  F.state.seenEventIds.clear();
  F.state.seenOrder.length = 0;
  F.state.counters.received = 0;
  F.state.counters.rejected = 0;
  F.state.counters.critical = 0;
  F.state.counters.duplicates = 0;
  F.state.filterText = '';
  F.state.filterSeverity = 'all';
};

console.log('\nde-duplication:');
run('the same event_id is counted once', () => {
  reset();
  const ev = parse();
  truthy(F.ingestAlert(ev) === true, 'the first was rejected');
  truthy(F.ingestAlert(ev) === false, 'the duplicate was accepted');
  truthy(F.ingestAlert({...ev}) === false, 'a copy was accepted');
  eq(F.state.counters.received, 1, 'received');
  eq(F.state.counters.duplicates, 2, 'duplicates');
  eq(F.state.events.length, 1, 'timeline entries');
});
run('an event with no id is never treated as a duplicate', () => {
  // Two different alerts published without ids are two alerts, and collapsing them
  // would hide one.
  reset();
  const a = parse({event_id: ''});
  truthy(F.ingestAlert(a), 'first');
  truthy(F.ingestAlert({...a, alert: 'drowsy'}), 'second');
  eq(F.state.counters.received, 2, 'received');
});
run('the dedup set is bounded', () => {
  reset();
  for (let i = 0; i < 700; i++) F.ingestAlert(parse({event_id: 'e-' + i}));
  truthy(F.state.seenEventIds.size <= 500, `set grew to ${F.state.seenEventIds.size}`);
  // The oldest id has been forgotten, so it counts as new again - which is the cost
  // of the bound and is stated rather than hidden.
  truthy(F.ingestAlert(parse({event_id: 'e-0'})), 'the oldest id was still remembered');
});
run('the timeline is capped', () => {
  reset();
  for (let i = 0; i < 400; i++) F.ingestAlert(parse({event_id: 'x-' + i}));
  truthy(F.state.events.length <= 200, `timeline grew to ${F.state.events.length}`);
  eq(F.state.events[0].eventId, 'x-399', 'newest first');
});

console.log('\ndevice state:');
run('a fleet of devices, worst first', () => {
  reset();
  F.ingestAlert(parse({event_id: 'a1', device_id: 'van-1', remark: 'Driver A',
                       risk: 0.31, severity: 'medium', alert: 'yawning'}));
  F.ingestAlert(parse({event_id: 'b1', device_id: 'van-2', remark: 'Driver B',
                       risk: 0.88, severity: 'critical', alert: 'microsleep'}));
  F.ingestAlert(parse({event_id: 'c1', device_id: 'van-3', remark: 'Driver C',
                       risk: 0.61, severity: 'high', alert: 'drowsy'}));
  F.render();
  eq(F.state.devices.size, 3, 'devices');
  const grid = store.get('fleet-devices');
  eq(grid.children.length, 3, 'cards');
  const first = flatten(grid.children[0]);
  truthy(first.includes('Driver B'), `the worst driver is not first: ${first}`);
  eq(store.get('fleet-count-devices').textContent, '3', 'device counter');
  eq(store.get('fleet-count-critical').textContent, '1', 'critical counter');
});
run('a status message marks a device offline without inventing an alert', () => {
  reset();
  F.ingestStatus(F.parseStatus('plxy/drowsyguard/demo-fleet/van-9/status',
      JSON.stringify({schema: 'drowsyguard.status.v1', device_id: 'van-9',
                      remark: 'Driver Z', online: false, reason: 'last-will'})));
  F.render();
  eq(F.state.devices.size, 1, 'the device appeared');
  eq(F.state.counters.received, 0, 'no alert was counted');
  truthy(flatten(store.get('fleet-devices')).includes('Driver Z'), 'not rendered');
});
run('the device count and this page count are both shown', () => {
  reset();
  F.ingestAlert(parse({event_id: 'p1', alert_count: 57}));
  F.render();
  const text = flatten(store.get('fleet-devices'));
  truthy(text.includes('1 here / 57 on device'), `counts not rendered: ${text}`);
});

console.log('\nfilters:');
run('search matches the remark, the device id and the alert', () => {
  reset();
  F.ingestAlert(parse({event_id: 'f1', device_id: 'van-1', remark: 'Alice',
                       alert: 'drowsy', severity: 'high'}));
  F.ingestAlert(parse({event_id: 'f2', device_id: 'lorry-7', remark: 'Bob',
                       alert: 'microsleep', severity: 'critical'}));
  const shown = () => { F.render(); return store.get('fleet-devices').children.length; };
  F.state.filterText = '';
  eq(shown(), 2, 'no filter');
  F.state.filterText = 'alice';
  eq(shown(), 1, 'by remark, case-insensitively');
  F.state.filterText = 'lorry';
  eq(shown(), 1, 'by device id');
  F.state.filterText = 'microsleep';
  eq(shown(), 1, 'by alert');
  F.state.filterText = 'nobody';
  eq(shown(), 0, 'no match');
  truthy(store.get('fleet-devices-empty').hidden === false, 'the empty note is hidden');
  F.state.filterText = '';
});
run('the severity filter applies to cards and to the timeline', () => {
  reset();
  F.ingestAlert(parse({event_id: 'g1', device_id: 'v1', severity: 'critical'}));
  F.ingestAlert(parse({event_id: 'g2', device_id: 'v2', severity: 'medium'}));
  F.state.filterSeverity = 'critical';
  F.render();
  eq(store.get('fleet-devices').children.length, 1, 'cards');
  eq(store.get('fleet-timeline').children.length, 1, 'timeline');
  F.state.filterSeverity = 'all';
  F.render();
  eq(store.get('fleet-devices').children.length, 2, 'cards, unfiltered');
});

console.log('\nrendering edges:');
run('an empty fleet renders the empty note', () => {
  reset();
  F.render();
  eq(store.get('fleet-devices').children.length, 0, 'no cards');
  eq(store.get('fleet-devices-empty').hidden, false, 'note shown');
  eq(store.get('fleet-timeline-empty').hidden, false, 'timeline note shown');
});
run('twenty devices and two hundred alerts', () => {
  reset();
  for (let i = 0; i < 200; i++) {
    F.ingestAlert(parse({
      event_id: 'load-' + i,
      device_id: 'van-' + (i % 20),
      remark: 'Driver ' + (i % 20),
      risk: (i % 100) / 100,
      severity: ['critical', 'high', 'medium', 'info'][i % 4],
      alert: ['drowsy', 'microsleep', 'yawning', 'sneeze'][i % 4],
    }));
  }
  F.render();
  eq(F.state.devices.size, 20, 'devices');
  eq(store.get('fleet-devices').children.length, 20, 'cards');
  truthy(store.get('fleet-timeline').children.length <= 60, 'the timeline is capped');
});
run('risk banding', () => {
  eq(F.riskBand(0.1, 'info'), 'ok', 'low');
  eq(F.riskBand(0.6, 'high'), 'warn', 'middling');
  eq(F.riskBand(0.9, 'critical'), 'bad', 'high');
  // Severity escalates the band even when the score is low: a microsleep is critical
  // whatever the fused score says, and the card has to look like it.
  eq(F.riskBand(0.1, 'critical'), 'bad', 'critical at a low score');
});
run('relative times', () => {
  eq(F.ago(0), 'never', 'never');
  eq(F.ago(Date.now()), 'just now', 'now');
  eq(F.ago(Date.now() - 65000), '1m ago', 'a minute');
  eq(F.ago(Date.now() - 3700000), '1h ago', 'an hour');
  eq(F.ago(Date.now() - 90000000), '1d ago', 'a day');
});
run('uptime formatting', () => {
  eq(F.uptime(0), '—', 'zero');
  eq(F.uptime(3723456), '01:02:03', 'an hour and change');
  eq(F.uptime(1000), '00:00:01', 'a second');
});

console.log('\nsettings:');
run('the URL is built from the fields', () => {
  eq(F.wsUrl({secure: true, host: 'broker.emqx.io', port: 8084, path: '/mqtt'}),
     'wss://broker.emqx.io:8084/mqtt', 'wss');
  eq(F.wsUrl({secure: false, host: 'localhost', port: 8083, path: '/mqtt'}),
     'ws://localhost:8083/mqtt', 'ws');
  eq(F.wsUrl({secure: true, host: 'h', port: 1, path: 'mqtt'}),
     'wss://h:1/mqtt', 'a missing leading slash is added');
});
run('nothing writes a password to storage', () => {
  stored.clear();
  store.get('fleet-password').value = 'super-secret-value';
  store.get('fleet-username').value = 'fleet-reader';
  const s = F.settings();
  eq(s.password, 'super-secret-value', 'read from the field');
  F.saveSettings(s);
  const blob = [...stored.values()].join('|');
  truthy(!blob.includes('super-secret-value'),
      `the password reached localStorage: ${blob}`);
  truthy(blob.includes('fleet-reader'), 'the username was not persisted');
  // And a reload does not put one back.
  F.loadSettings();
  eq(store.get('fleet-password').value, '', 'the field was repopulated');
});
run('corrupt stored settings fall back to the defaults', () => {
  stored.set('drowsyguard.fleet.v1', 'not json at all');
  F.loadSettings();
  eq(store.get('fleet-host').value, 'broker.emqx.io', 'host');
  stored.set('drowsyguard.fleet.v1', '[1,2,3]');
  F.loadSettings();
  eq(store.get('fleet-host').value, 'broker.emqx.io', 'host from an array');
  stored.set('drowsyguard.fleet.v1', JSON.stringify({host: 'a b', port: 1e9}));
  F.loadSettings();
  eq(store.get('fleet-host').value, 'ab', 'host sanitised');
  eq(store.get('fleet-port').value, '65535', 'port clamped');
  stored.clear();
  F.loadSettings();
});
run('the mixed-content warning follows the scheme', () => {
  store.get('fleet-secure').value = 'ws';
  F.refreshDerived();
  eq(store.get('fleet-mixed-note').hidden, false, 'shown for ws on an https page');
  store.get('fleet-secure').value = 'wss';
  F.refreshDerived();
  eq(store.get('fleet-mixed-note').hidden, true, 'hidden for wss');
});
run('the public-broker warning follows the host', () => {
  store.get('fleet-host').value = 'broker.emqx.io';
  F.refreshDerived();
  eq(store.get('fleet-demo-note').hidden, false, 'shown');
  store.get('fleet-host').value = 'mqtt.example.internal';
  F.refreshDerived();
  eq(store.get('fleet-demo-note').hidden, true, 'hidden for a private broker');
  store.get('fleet-host').value = 'broker.emqx.io';
});

console.log('\nmessage routing:');
run('an alert topic and a status topic go different ways', () => {
  reset();
  F.handleMessage({topic: TOPIC, payload: JSON.stringify(ALERT), qos: 1, packetId: 1});
  eq(F.state.counters.received, 1, 'the alert counted');
  F.handleMessage({
    topic: 'plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/status',
    payload: JSON.stringify({schema: 'drowsyguard.status.v1',
                             device_id: 'drowsyguard-c5e019', online: false,
                             reason: 'last-will'}),
    qos: 1, packetId: 2});
  eq(F.state.counters.received, 1, 'the status did not count as an alert');
  eq(F.state.devices.get('drowsyguard-c5e019').online, false, 'marked offline');
});
run('a rejected message is counted rather than hidden', () => {
  reset();
  F.handleMessage({topic: TOPIC, payload: 'not json', qos: 0, packetId: 0});
  F.handleMessage({topic: TOPIC, payload: '{"schema":"other.v1"}', qos: 0, packetId: 0});
  eq(F.state.counters.rejected, 2, 'rejected');
  eq(store.get('fleet-count-rejected').textContent, '2', 'shown on the page');
});

console.log('\nclipboard:');
run('the execCommand fallback runs when there is no secure context', async () => {
  const btn = {textContent: 'Copy'};
  const ok = await F.copyToClipboard('plxy/drowsyguard/demo-fleet/+/alerts', btn);
  truthy(ok, 'copy reported failure');
});
run('an empty or placeholder value is not copied', async () => {
  truthy((await F.copyToClipboard('', null)) === false, 'empty was copied');
  truthy((await F.copyToClipboard('—', null)) === false, 'the em dash was copied');
});

console.log(failures ? `\n${failures} failure(s)` : '\nall paths clean');
process.exit(failures ? 1 : 0);
