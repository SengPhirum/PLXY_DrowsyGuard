// Fleet monitor for the DrowsyGuard documentation site.
//
// A live dashboard for whatever DrowsyGuard devices are publishing to a broker: one
// card per device, a timeline of alerts, counters, filters, and browser and audio
// notifications. It exists so that "the device publishes MQTT" can be demonstrated
// and tested from a laptop or a phone in a room, without installing anything.
//
// WHY THERE IS NO MQTT LIBRARY HERE
// --------------------------------
// A browser cannot open a raw MQTT socket, so this speaks MQTT over WebSocket - and
// it speaks it itself, in about two hundred lines, rather than pulling mqtt.js off a
// CDN. Three reasons, in order of how much they matter:
//
//   1. This page is published on GitHub Pages under the project's own domain, and
//      the only thing it does is display a driver's alertness state. Adding a
//      third-party script to that page means a third party can change what it does,
//      for every reader, forever. Subresource integrity pins a version but not the
//      decision to depend on one.
//   2. It has to be testable. tests/fleet_page_harness.mjs drives the codec below
//      against byte sequences from the MQTT 3.1.1 and 5 specifications; a wrapped
//      library would have moved that test to "we called the library correctly".
//   3. The subset needed is small and closed: CONNECT, SUBSCRIBE, receive PUBLISH,
//      acknowledge it, ping, disconnect. This page never publishes anything, which
//      is most of what an MQTT client is for.
//
// SANITISING
// ----------
// Everything below the WebSocket is hostile input. The public broker this page
// defaults to is shared with the entire internet, so anyone can publish anything to
// the topic being watched - a remark containing markup, a device id a kilometre
// long, a risk of 1e308, a JSON document that is not an object at all. Every field
// is therefore passed through clean()/num() before it is stored, and every value
// reaches the DOM through textContent. There is no innerHTML in this file and there
// should never be one: this page's whole job is to render strings a stranger chose.
//
// PASSWORDS
// ---------
// Held in a variable and nowhere else. The host, port, path and topic are remembered
// in localStorage because retyping them at every demonstration is the difference
// between a tool and a chore; the password is not, and the field is emptied after a
// connect. A documentation page that persisted broker credentials in a browser
// profile would be a worse idea than the feature it is documenting.
'use strict';

(function () {
  // Every page on this site loads this script (see mkdocs.yml - a <script src> in
  // the page body cannot work, because python-markdown stashes raw HTML before
  // MkDocs rewrites relative paths). So the first thing it does is leave.
  const root = document.getElementById('fleet-app');
  if (!root) return;

  const $ = (id) => document.getElementById(id);

  // --- the official EMQX public broker ------------------------------------ //
  // https://www.emqx.com/en/mqtt/public-mqtt5-broker
  // Demonstration and testing only: no authentication, no isolation, and everything
  // published to it is readable by anyone who guesses the topic. Said again on the
  // page itself, next to the field.
  const DEMO = {host: 'broker.emqx.io', wss: 8084, ws: 8083, path: '/mqtt'};
  const DEFAULT_TOPIC = 'plxy/drowsyguard/demo-fleet/+/alerts';

  // ======================================================================== //
  // MQTT codec
  // ======================================================================== //
  const T = {
    CONNECT: 1, CONNACK: 2, PUBLISH: 3, PUBACK: 4, SUBSCRIBE: 8, SUBACK: 9,
    PINGREQ: 12, PINGRESP: 13, DISCONNECT: 14,
  };

  // The variable-length integer MQTT uses for every remaining-length field. Four
  // bytes maximum, 268 435 455 the largest value - which is also the reason a
  // malformed stream cannot make the decoder loop forever.
  function encodeVarInt(n) {
    const out = [];
    if (n < 0 || n > 268435455) throw new Error('length out of range');
    do {
      let byte = n % 128;
      n = Math.floor(n / 128);
      if (n > 0) byte |= 0x80;
      out.push(byte);
    } while (n > 0);
    return out;
  }

  // Returns {value, bytes} or null when the buffer does not hold a whole one yet -
  // which is normal: a WebSocket frame boundary has nothing to do with a packet
  // boundary, so a partial header arrives regularly rather than exceptionally.
  function decodeVarInt(buf, at) {
    let value = 0;
    let mult = 1;
    for (let i = 0; i < 4; i++) {
      if (at + i >= buf.length) return null;
      const byte = buf[at + i];
      value += (byte & 0x7f) * mult;
      if ((byte & 0x80) === 0) return {value: value, bytes: i + 1};
      mult *= 128;
    }
    throw new Error('malformed remaining length');
  }

  function encodeString(s) {
    const bytes = new TextEncoder().encode(s);
    if (bytes.length > 65535) throw new Error('string too long');
    return [bytes.length >> 8, bytes.length & 0xff, ...bytes];
  }

  function decodeString(buf, at) {
    if (at + 2 > buf.length) throw new Error('truncated string length');
    const n = (buf[at] << 8) | buf[at + 1];
    if (at + 2 + n > buf.length) throw new Error('truncated string');
    return {
      // fatal:false rather than throwing on bad UTF-8: a topic with an invalid byte
      // in it is a topic to display as U+FFFD, not a reason to drop the connection.
      value: new TextDecoder('utf-8', {fatal: false}).decode(buf.subarray(at + 2, at + 2 + n)),
      bytes: 2 + n,
    };
  }

  // CONNECT. `version` is 4 for MQTT 3.1.1 or 5 for MQTT 5 - the wire difference is
  // one protocol-level byte plus, for 5, a property-length byte after the keepalive.
  function encodeConnect(opts) {
    const version = opts.version === 5 ? 5 : 4;
    const flags = 0x02                                    // clean session / start
        | (opts.username ? 0x80 : 0)
        | (opts.username && opts.password ? 0x40 : 0);
    // An absent or zero keepalive becomes the 60 s default rather than 0. MQTT
    // reads 0 as "no keepalive at all", which would leave this page relying on the
    // broker never timing it out - and the ping timer below is set to half of
    // whatever is announced here, so the two have to agree.
    const requested = Number(opts.keepalive) > 0 ? Number(opts.keepalive) : 60;
    const keepalive = Math.min(65535, Math.max(10, requested));
    const vh = [
      ...encodeString('MQTT'), version, flags,
      keepalive >> 8, keepalive & 0xff,
    ];
    if (version === 5) vh.push(0x00);                     // no properties
    const payload = [...encodeString(opts.clientId)];
    if (opts.username) {
      payload.push(...encodeString(opts.username));
      if (opts.password) payload.push(...encodeString(opts.password));
    }
    const body = [...vh, ...payload];
    return new Uint8Array([(T.CONNECT << 4), ...encodeVarInt(body.length), ...body]);
  }

  function encodeSubscribe(topic, qos, packetId, version) {
    const vh = [packetId >> 8, packetId & 0xff];
    if (version === 5) vh.push(0x00);                     // no properties
    const body = [...vh, ...encodeString(topic), qos & 0x03];
    // 0x82: SUBSCRIBE requires the reserved bits to be 0010, and a broker that
    // enforces it (most do) closes the connection on anything else.
    return new Uint8Array([(T.SUBSCRIBE << 4) | 0x02, ...encodeVarInt(body.length),
                           ...body]);
  }

  // QoS 1 acknowledgement. Two bytes of packet id and nothing else, which is valid
  // in both versions: in MQTT 5 a PUBACK whose remaining length is 2 means success
  // with no properties.
  function encodePuback(packetId) {
    return new Uint8Array([(T.PUBACK << 4), 0x02, packetId >> 8, packetId & 0xff]);
  }

  const encodePingreq = () => new Uint8Array([(T.PINGREQ << 4), 0x00]);
  const encodeDisconnect = () => new Uint8Array([(T.DISCONNECT << 4), 0x00]);

  // Pulls whole packets out of an accumulating buffer. Returns the packets found and
  // how many bytes were consumed, so the caller keeps the remainder for next time.
  function decodePackets(buf, version) {
    const packets = [];
    let at = 0;
    for (;;) {
      if (at >= buf.length) break;
      const header = buf[at];
      const len = decodeVarInt(buf, at + 1);
      if (len === null) break;                            // header still incomplete
      const start = at + 1 + len.bytes;
      if (start + len.value > buf.length) break;          // body still incomplete
      const body = buf.subarray(start, start + len.value);
      packets.push(parsePacket(header, body, version));
      at = start + len.value;
    }
    return {packets: packets, consumed: at};
  }

  function parsePacket(header, body, version) {
    const type = header >> 4;
    if (type === T.CONNACK) {
      // Byte 0 is the acknowledge flags, byte 1 the return code (3.1.1) or reason
      // code (5). Zero means accepted in both.
      return {type: type, code: body.length > 1 ? body[1] : 0,
              sessionPresent: body.length > 0 ? (body[0] & 1) === 1 : false};
    }
    if (type === T.SUBACK) {
      // The last byte is the granted QoS, or a failure code: 0x80 in 3.1.1, and
      // anything >= 0x80 in 5. A broker that refuses the subscription answers here
      // rather than by closing the socket, and a page that ignored it would sit
      // there looking connected and receiving nothing.
      const code = body.length ? body[body.length - 1] : 0x80;
      return {type: type, granted: code, ok: code < 0x80};
    }
    if (type === T.PINGRESP) return {type: type};
    if (type === T.PUBLISH) {
      const qos = (header >> 1) & 0x03;
      let at = 0;
      const topic = decodeString(body, at);
      at += topic.bytes;
      let packetId = 0;
      if (qos > 0) {
        if (at + 2 > body.length) throw new Error('truncated packet id');
        packetId = (body[at] << 8) | body[at + 1];
        at += 2;
      }
      if (version === 5) {
        // Properties, which this page has no use for but must step over exactly:
        // getting the length wrong turns the first bytes of the payload into a
        // property and the rest into a document that will not parse.
        const props = decodeVarInt(body, at);
        if (props === null) throw new Error('truncated properties');
        at += props.bytes + props.value;
        if (at > body.length) throw new Error('properties overrun the packet');
      }
      return {
        type: type, topic: topic.value, qos: qos, packetId: packetId,
        retain: (header & 1) === 1, dup: ((header >> 3) & 1) === 1,
        payload: new TextDecoder('utf-8', {fatal: false}).decode(body.subarray(at)),
      };
    }
    return {type: type};
  }

  // ======================================================================== //
  // sanitising
  // ======================================================================== //
  // Everything from the broker goes through one of these before it is stored. The
  // control-character strip is not cosmetic: a device id containing a newline would
  // break every log line that mentions it, and one containing U+202E reverses the
  // rendering of everything after it on the page.
  function clean(v, max) {
    if (typeof v !== 'string') return '';
    let out = '';
    for (const ch of v) {
      const code = ch.codePointAt(0);
      if (code < 0x20 || code === 0x7f) continue;                 // C0 and DEL
      if (code >= 0x80 && code <= 0x9f) continue;                 // C1
      if (code >= 0x200b && code <= 0x200f) continue;             // zero width, marks
      if (code >= 0x202a && code <= 0x202e) continue;             // bidi overrides
      if (code >= 0x2066 && code <= 0x2069) continue;             // bidi isolates
      out += ch;
      if (out.length >= max) break;
    }
    return out.trim();
  }

  // Deliberately narrower than Number(). `Number(null)`, `Number([])` and
  // `Number('')` are all 0, so the obvious one-liner turns a missing field into a
  // confident zero - which for `risk` reads as "this driver is fine" rather than
  // "this message told us nothing". Only a finite number, or a string that is one,
  // counts as a value.
  function num(v, lo, hi, fallback) {
    if (typeof v === 'number') {
      return Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback;
    }
    if (typeof v === 'string' && v.trim() !== '') {
      const n = Number(v);
      if (Number.isFinite(n)) return Math.min(hi, Math.max(lo, n));
    }
    return fallback;
  }

  const SEVERITIES = ['critical', 'high', 'medium', 'info'];
  const ALERTS = ['drowsy', 'microsleep', 'yawning', 'head_nod', 'sneeze',
                  'no_driver', 'test'];

  // One published document into one alert, or null. Rejecting is the normal path for
  // anything unexpected: this topic is shared, and a page that rendered every
  // message it received would be a display for whatever a stranger felt like sending.
  function parseAlert(topic, text) {
    let j;
    try {
      j = JSON.parse(text);
    } catch (e) {
      return null;
    }
    if (!j || typeof j !== 'object' || Array.isArray(j)) return null;
    const schema = clean(j.schema, 48);
    // Versioned on purpose. A v2 alert with a renamed field would otherwise render
    // as a device with no risk and no remark, which looks like a broken device
    // rather than a page that needs updating.
    if (schema && !schema.startsWith('drowsyguard.alert.')) return null;
    const deviceId = clean(j.device_id, 32) || deviceFromTopic(topic);
    if (!deviceId) return null;
    const alert = clean(j.alert, 24).toLowerCase();
    return {
      schema: schema || 'drowsyguard.alert.v1',
      eventId: clean(j.event_id, 64),
      seq: num(j.seq, 0, 4294967295, 0),
      deviceId: deviceId,
      fleetId: clean(j.fleet_id, 32),
      remark: clean(j.remark, 48),
      alert: ALERTS.includes(alert) ? alert : (alert || 'unknown'),
      severity: SEVERITIES.includes(clean(j.severity, 16)) ? clean(j.severity, 16)
                                                           : 'info',
      risk: num(j.risk, 0, 1, 0),
      perclos: num(j.perclos, 0, 1, 0),
      alertCount: num(j.alert_count, 0, 4294967295, 0),
      uptimeMs: num(j.uptime_ms, 0, 4294967295 * 1000, 0),
      ts: clean(j.ts, 32),
      tsSource: clean(j.ts_source, 16),
      topic: clean(topic, 200),
      // When the browser saw it. Kept separately from the device's own timestamp
      // because the device may not have one at all - it has no RTC - and "last seen"
      // has to work either way.
      seenAt: Date.now(),
    };
  }

  function parseStatus(topic, text) {
    let j;
    try {
      j = JSON.parse(text);
    } catch (e) {
      return null;
    }
    if (!j || typeof j !== 'object' || Array.isArray(j)) return null;
    const schema = clean(j.schema, 48);
    if (schema && !schema.startsWith('drowsyguard.status.')) return null;
    const deviceId = clean(j.device_id, 32) || deviceFromTopic(topic);
    if (!deviceId) return null;
    return {
      deviceId: deviceId,
      fleetId: clean(j.fleet_id, 32),
      remark: clean(j.remark, 48),
      online: j.online === true,
      reason: clean(j.reason, 24),
      uptimeMs: num(j.uptime_ms, 0, 4294967295 * 1000, 0),
      alertCount: num(j.alert_count, 0, 4294967295, 0),
      seenAt: Date.now(),
    };
  }

  // plxy/drowsyguard/{fleet}/{device}/alerts -> {device}. The fallback for a payload
  // with no device_id in it, which is what a hand-crafted `mosquitto_pub` test
  // message usually is.
  function deviceFromTopic(topic) {
    const parts = String(topic || '').split('/');
    return parts.length >= 2 ? clean(parts[parts.length - 2], 32) : '';
  }

  // ======================================================================== //
  // state
  // ======================================================================== //
  const state = {
    devices: new Map(),        // deviceId -> {...}
    events: [],                // newest first, capped
    counters: {received: 0, rejected: 0, critical: 0, duplicates: 0},
    seenEventIds: new Set(),   // de-duplication, see below
    seenOrder: [],
    filterText: '',
    filterSeverity: 'all',
    connection: 'idle',
    error: '',
    notify: false,
    sound: false,
  };

  const EVENT_CAP = 200;
  const DEDUP_CAP = 500;

  // The device de-duplicates on its side; QoS 1 and a retained message mean the
  // browser sees repeats anyway - a reconnect redelivers whatever the broker still
  // holds. Same identifier, same rule: an event_id already on the timeline is
  // counted and dropped.
  function isDuplicate(id) {
    if (!id) return false;
    if (state.seenEventIds.has(id)) return true;
    state.seenEventIds.add(id);
    state.seenOrder.push(id);
    while (state.seenOrder.length > DEDUP_CAP) {
      state.seenEventIds.delete(state.seenOrder.shift());
    }
    return false;
  }

  function ingestAlert(ev) {
    if (isDuplicate(ev.eventId)) {
      state.counters.duplicates++;
      return false;
    }
    state.counters.received++;
    if (ev.severity === 'critical') state.counters.critical++;

    const d = state.devices.get(ev.deviceId) || {
      deviceId: ev.deviceId, alerts: 0, online: null, counts: {},
    };
    d.remark = ev.remark || d.remark || '';
    d.fleetId = ev.fleetId || d.fleetId || '';
    d.risk = ev.risk;
    d.perclos = ev.perclos;
    d.lastAlert = ev.alert;
    d.lastSeverity = ev.severity;
    d.uptimeMs = ev.uptimeMs;
    d.topic = ev.topic;
    d.seenAt = ev.seenAt;
    d.alerts++;
    d.counts[ev.alert] = (d.counts[ev.alert] || 0) + 1;
    // The device's own running total wins when it sent one: it counts alerts this
    // page was not open for, which is most of them.
    d.deviceCount = ev.alertCount || d.deviceCount || 0;
    state.devices.set(ev.deviceId, d);

    state.events.unshift(ev);
    while (state.events.length > EVENT_CAP) state.events.pop();
    return true;
  }

  function ingestStatus(st) {
    const d = state.devices.get(st.deviceId) || {
      deviceId: st.deviceId, alerts: 0, counts: {},
    };
    d.remark = st.remark || d.remark || '';
    d.fleetId = st.fleetId || d.fleetId || '';
    d.online = st.online;
    d.statusReason = st.reason;
    d.statusAt = st.seenAt;
    if (!d.seenAt) d.seenAt = st.seenAt;
    if (st.alertCount) d.deviceCount = st.alertCount;
    state.devices.set(st.deviceId, d);
  }

  // ======================================================================== //
  // rendering
  // ======================================================================== //
  const AGO_STEPS = [[86400, 'd'], [3600, 'h'], [60, 'm'], [1, 's']];

  function ago(ms) {
    if (!ms) return 'never';
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 2) return 'just now';
    for (const [unit, label] of AGO_STEPS) {
      if (s >= unit) return `${Math.floor(s / unit)}${label} ago`;
    }
    return 'just now';
  }

  function uptime(ms) {
    if (!ms) return '—';
    const t = Math.floor(ms / 1000);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(Math.floor(t / 3600))}:${p(Math.floor(t / 60) % 60)}:${p(t % 60)}`;
  }

  // Every DOM node this page builds is built here, with textContent. There is no
  // innerHTML in this file: the strings being rendered were chosen by whoever is
  // publishing to the topic, and on the default broker that is anybody.
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function matchesFilter(d) {
    if (state.filterSeverity !== 'all' && d.lastSeverity !== state.filterSeverity) {
      return false;
    }
    if (!state.filterText) return true;
    const q = state.filterText.toLowerCase();
    return [d.deviceId, d.remark, d.fleetId, d.lastAlert]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q));
  }

  function riskBand(risk, severity) {
    if (severity === 'critical' || risk >= 0.75) return 'bad';
    if (severity === 'high' || risk >= 0.55) return 'warn';
    return 'ok';
  }

  function renderDevices() {
    const grid = $('fleet-devices');
    if (!grid) return;
    const all = [...state.devices.values()];
    const shown = all.filter(matchesFilter)
        // Worst first, then most recent. A dashboard sorted by name makes the
        // operator look for the problem; this one puts it at the top.
        .sort((a, b) => (b.risk || 0) - (a.risk || 0) || (b.seenAt || 0) - (a.seenAt || 0));

    grid.textContent = '';
    for (const d of shown) {
      const card = el('article', 'fleet-card' + (d.online === false ? ' is-offline' : ''));
      const head = el('header');
      const name = el('h3', null, d.remark || d.deviceId);
      const dot = el('span', 'fleet-dot ' + (d.online === false ? 'off'
          : d.online === true ? 'on' : 'unknown'));
      dot.title = d.online === false
          ? `Offline${d.statusReason ? ' (' + d.statusReason + ')' : ''}`
          : d.online === true ? 'Online' : 'No status message seen';
      head.append(dot, name);
      card.append(head);
      card.append(el('p', 'fleet-id', d.deviceId
          + (d.fleetId ? ' · ' + d.fleetId : '')));

      const risk = el('div', 'fleet-risk');
      const value = el('strong',
          'fleet-risk-value fleet-band-' + riskBand(d.risk, d.lastSeverity),
          (d.risk || 0).toFixed(2));
      risk.append(value, el('span', 'fleet-risk-label', 'risk'));
      card.append(risk);

      const bar = el('div', 'fleet-bar');
      const fill = el('i', 'fleet-band-' + riskBand(d.risk, d.lastSeverity));
      fill.style.width = Math.round((d.risk || 0) * 100) + '%';
      bar.append(fill);
      card.append(bar);

      const meta = el('dl', 'fleet-meta');
      const add = (k, v, title) => {
        const dt = el('dt', null, k);
        const dd = el('dd', null, v);
        if (title) dd.title = title;
        meta.append(dt, dd);
      };
      add('last alert', d.lastAlert ? `${d.lastAlert} (${d.lastSeverity})` : '—');
      add('last seen', ago(d.seenAt), d.seenAt ? new Date(d.seenAt).toISOString() : '');
      add('PERCLOS', d.perclos === undefined ? '—'
          : Math.round(d.perclos * 100) + '%');
      add('alerts', `${d.alerts} here / ${d.deviceCount || 0} on device`,
          'Seen by this page since it was opened, and the running total the device '
          + 'reports since its last boot.');
      add('uptime', uptime(d.uptimeMs));
      card.append(meta);

      const topics = el('div', 'fleet-copy');
      const code = el('code', null, d.topic || '—');
      const btn = el('button', 'fleet-mini', 'Copy topic');
      btn.type = 'button';
      btn.addEventListener('click', () => copyToClipboard(d.topic || '', btn));
      topics.append(code, btn);
      card.append(topics);
      grid.append(card);
    }

    $('fleet-devices-empty').hidden = shown.length > 0;
    if (!shown.length) {
      $('fleet-devices-empty').textContent = all.length
          ? 'No device matches the filter.'
          : 'No devices yet. Nothing has published to this topic since you connected.';
    }
    setText('fleet-count-devices', all.length);
    setText('fleet-count-shown', shown.length);
  }

  const SEV_ORDER = {critical: 3, high: 2, medium: 1, info: 0};

  function renderTimeline() {
    const list = $('fleet-timeline');
    if (!list) return;
    const shown = state.events.filter((ev) => {
      if (state.filterSeverity !== 'all' && ev.severity !== state.filterSeverity) {
        return false;
      }
      if (!state.filterText) return true;
      const q = state.filterText.toLowerCase();
      return [ev.deviceId, ev.remark, ev.alert, ev.fleetId]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(q));
    }).slice(0, 60);

    list.textContent = '';
    for (const ev of shown) {
      const li = el('li', 'fleet-sev-' + ev.severity);
      li.append(el('time', null, new Date(ev.seenAt).toLocaleTimeString()));
      li.append(el('span', 'fleet-badge fleet-sev-' + ev.severity, ev.alert));
      li.append(el('span', 'fleet-who', ev.remark || ev.deviceId));
      li.append(el('span', 'fleet-num', 'risk ' + ev.risk.toFixed(2)));
      // The device's own timestamp when it had one. It says so when it did not -
      // there is no clock on the board unless something set it.
      li.append(el('span', 'fleet-when',
          ev.ts ? ev.ts : `uptime ${uptime(ev.uptimeMs)}`));
      list.append(li);
    }
    $('fleet-timeline-empty').hidden = shown.length > 0;
  }

  function renderCounters() {
    const c = state.counters;
    setText('fleet-count-alerts', c.received);
    setText('fleet-count-critical', c.critical);
    setText('fleet-count-rejected', c.rejected);
    setText('fleet-count-duplicates', c.duplicates);
    const active = [...state.devices.values()].filter(
        (d) => d.lastSeverity && SEV_ORDER[d.lastSeverity] >= 2
               && Date.now() - (d.seenAt || 0) < 120000).length;
    setText('fleet-count-active', active);
  }

  function setText(id, v) {
    const e = $(id);
    if (e && e.textContent !== String(v)) e.textContent = String(v);
  }

  function renderConnection() {
    const pill = $('fleet-state');
    if (!pill) return;
    const labels = {
      idle: 'not connected', connecting: 'connecting…', open: 'handshaking…',
      subscribed: 'live', closed: 'disconnected', error: 'error',
      retrying: 'reconnecting…',
    };
    pill.textContent = labels[state.connection] || state.connection;
    pill.className = 'fleet-pill state-' + state.connection;
    const note = $('fleet-conn-note');
    if (note) {
      note.textContent = state.error || {
        idle: 'Press Configure MQTT, then Connect. Nothing is sent anywhere until '
            + 'you do.',
        connecting: 'Opening the WebSocket…',
        open: 'Socket open, waiting for the broker to accept the connection.',
        subscribed: 'Subscribed. Alerts appear as devices publish them.',
        closed: 'The connection closed.',
        retrying: 'Reconnecting with an increasing delay.',
      }[state.connection] || '';
    }
    $('fleet-connect').textContent = (state.connection === 'subscribed'
        || state.connection === 'open' || state.connection === 'connecting')
        ? 'Disconnect' : 'Connect';
  }

  let renderPending = false;
  // Coalesced into an animation frame. A fleet of twenty devices under a burst of
  // alerts would otherwise rebuild the whole grid several times per broker message,
  // which on a phone is visible as a stutter.
  function render() {
    if (renderPending) return;
    renderPending = true;
    const run = () => {
      renderPending = false;
      renderDevices();
      renderTimeline();
      renderCounters();
      renderConnection();
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
    else run();
  }

  // ======================================================================== //
  // notifications
  // ======================================================================== //
  // Two channels, both off until asked for: a browser notification for when the tab
  // is not in front, and a tone for when it is. A dashboard nobody is looking at is
  // the normal case, which is the whole reason for the first one.
  function notify(ev) {
    if (state.notify && typeof Notification === 'function'
        && Notification.permission === 'granted') {
      try {
        // The body is composed from already-sanitised fields. A notification renders
        // as plain text, but it is still a string a stranger chose.
        new Notification(`${ev.alert.toUpperCase()} — ${ev.remark || ev.deviceId}`, {
          body: `risk ${ev.risk.toFixed(2)} · ${ev.severity}`,
          tag: ev.eventId || undefined,
        });
      } catch (e) { /* a browser that refuses is not an error worth showing */ }
    }
    if (state.sound) beep(ev.severity);
  }

  let audioCtx = null;
  function beep(severity) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = audioCtx || new Ctx();
      const now = audioCtx.currentTime;
      // Two rising notes for critical, one for anything else. Deliberately not the
      // device's own voice clips: this page is a monitor, and a dashboard that says
      // "microsleep" out loud in an office is a dashboard people mute.
      const notes = severity === 'critical' ? [660, 990] : [520];
      notes.forEach((freq, i) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.0001, now + i * 0.18);
        gain.gain.exponentialRampToValueAtTime(0.18, now + i * 0.18 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.18 + 0.16);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(now + i * 0.18);
        osc.stop(now + i * 0.18 + 0.18);
      });
    } catch (e) { /* audio is a nicety; never a failure */ }
  }

  // ======================================================================== //
  // clipboard
  // ======================================================================== //
  // navigator.clipboard needs a secure context. This page is served over HTTPS from
  // GitHub Pages so it normally has one, but the same file is opened from `file://`
  // and from `mkdocs serve` on plain HTTP during development, where it does not - so
  // the fallback is a supported path rather than a legacy afterthought.
  async function copyToClipboard(text, btn) {
    if (!text || text === '—') return false;
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        ok = true;
      }
    } catch (e) { ok = false; }
    if (!ok) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, text.length);
        ok = document.execCommand('copy');
        document.body.removeChild(ta);
      } catch (e) { ok = false; }
    }
    if (btn) {
      const was = btn.textContent;
      // Says which way it went. A button that claims success it did not have sends
      // the operator to paste an empty clipboard into their broker.
      btn.textContent = ok ? 'Copied' : 'Select it';
      setTimeout(() => { btn.textContent = was; }, 1400);
    }
    return ok;
  }

  // ======================================================================== //
  // the connection
  // ======================================================================== //
  const conn = {
    ws: null,
    buf: new Uint8Array(0),
    version: 4,
    packetId: 1,
    ping: null,
    retry: 0,
    retryTimer: null,
    wanted: false,
    password: '',          // in memory only, and cleared on disconnect
  };

  function settings() {
    return {
      host: clean($('fleet-host').value, 96) || DEMO.host,
      port: num($('fleet-port').value, 1, 65535, DEMO.wss),
      path: clean($('fleet-path').value, 64) || DEMO.path,
      secure: $('fleet-secure').value !== 'ws',
      topic: clean($('fleet-topic').value, 200) || DEFAULT_TOPIC,
      statusTopic: clean($('fleet-status-topic').value, 200),
      clientId: clean($('fleet-client-id').value, 64)
          || 'drowsyguard-fleet-' + Math.random().toString(16).slice(2, 10),
      username: clean($('fleet-username').value, 64),
      password: $('fleet-password').value || '',
      version: $('fleet-version').value === '5' ? 5 : 4,
      qos: num($('fleet-qos').value, 0, 1, 1),
    };
  }

  // Everything except the password, which is held in memory for the life of the tab
  // and no longer. A documentation page that wrote broker credentials into a browser
  // profile would be a worse idea than the feature it documents.
  const STORE_KEY = 'drowsyguard.fleet.v1';
  const PERSISTED = ['host', 'port', 'path', 'secure', 'topic', 'statusTopic',
                     'clientId', 'username', 'version', 'qos'];

  function saveSettings(s) {
    try {
      const out = {};
      for (const k of PERSISTED) out[k] = s[k];
      localStorage.setItem(STORE_KEY, JSON.stringify(out));
    } catch (e) { /* private windows and blocked storage are both fine */ }
  }

  function loadSettings() {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    } catch (e) { saved = null; }
    const s = saved && typeof saved === 'object' ? saved : {};
    $('fleet-host').value = clean(s.host, 96) || DEMO.host;
    $('fleet-secure').value = s.secure === false ? 'ws' : 'wss';
    $('fleet-port').value = num(s.port, 1, 65535, s.secure === false ? DEMO.ws : DEMO.wss);
    $('fleet-path').value = clean(s.path, 64) || DEMO.path;
    $('fleet-topic').value = clean(s.topic, 200) || DEFAULT_TOPIC;
    $('fleet-status-topic').value = clean(s.statusTopic, 200)
        || DEFAULT_TOPIC.replace(/\/alerts$/, '/status');
    $('fleet-client-id').value = clean(s.clientId, 64)
        || 'drowsyguard-fleet-' + Math.random().toString(16).slice(2, 10);
    $('fleet-username').value = clean(s.username, 64);
    $('fleet-version').value = s.version === 5 ? '5' : '4';
    $('fleet-qos').value = String(num(s.qos, 0, 1, 1));
    // Never restored. There is nothing to restore it from.
    $('fleet-password').value = '';
    refreshDerived();
  }

  function wsUrl(s) {
    const scheme = s.secure ? 'wss' : 'ws';
    const path = s.path.startsWith('/') ? s.path : '/' + s.path;
    return `${scheme}://${s.host}:${s.port}${path}`;
  }

  function setState(name, error) {
    state.connection = name;
    state.error = error || '';
    render();
  }

  function connect() {
    const s = settings();
    saveSettings(s);
    conn.password = s.password;
    // Emptied immediately. The value lives in conn.password for the life of the tab
    // and the field stops being a place a screenshot can disclose it from.
    $('fleet-password').value = '';
    conn.version = s.version;
    conn.wanted = true;
    openSocket(s);
  }

  function openSocket(s) {
    let ws;
    setState('connecting');
    try {
      // 'mqtt' is the subprotocol the MQTT-over-WebSocket binding requires. A broker
      // that does not see it will refuse the upgrade, which surfaces here as an
      // immediate close with no explanation - so it is not optional.
      ws = new WebSocket(wsUrl(s), 'mqtt');
    } catch (e) {
      setState('error', 'That address is not a valid WebSocket URL.');
      return;
    }
    ws.binaryType = 'arraybuffer';
    conn.ws = ws;
    conn.buf = new Uint8Array(0);

    ws.onopen = () => {
      setState('open');
      send(encodeConnect({
        clientId: s.clientId, username: s.username, password: conn.password,
        keepalive: 60, version: s.version,
      }));
    };
    ws.onmessage = (msg) => {
      let chunk;
      try {
        chunk = new Uint8Array(msg.data instanceof ArrayBuffer ? msg.data
            : new Uint8Array(0));
      } catch (e) {
        return;
      }
      // A WebSocket frame boundary has nothing to do with a packet boundary, so the
      // remainder is carried between messages rather than assumed away.
      const merged = new Uint8Array(conn.buf.length + chunk.length);
      merged.set(conn.buf, 0);
      merged.set(chunk, conn.buf.length);
      let out;
      try {
        out = decodePackets(merged, conn.version);
      } catch (e) {
        // A malformed stream is not something to keep parsing: the offsets are
        // already lost, and everything after would be noise presented as data.
        state.counters.rejected++;
        conn.buf = new Uint8Array(0);
        try { ws.close(); } catch (err) { /* already closing */ }
        setState('error', 'The broker sent something this client could not parse.');
        return;
      }
      conn.buf = merged.subarray(out.consumed);
      for (const p of out.packets) handlePacket(p, s);
    };
    ws.onerror = () => {
      // The browser deliberately does not say why - it would be a cross-origin
      // information leak - so this cannot be more specific than it is, and pretending
      // otherwise would send people looking in the wrong place.
      setState('error', 'The connection failed. Check the host, the port and the '
          + 'path; a page served over HTTPS can only use WSS.');
    };
    ws.onclose = () => {
      stopPing();
      if (!conn.wanted) {
        setState('closed');
        return;
      }
      // Exponential backoff, capped, exactly as the firmware does it and for the
      // same reason: a broker that is down should not be dialled every second by
      // every dashboard watching it.
      conn.retry = Math.min(conn.retry + 1, 6);
      const wait = Math.min(30000, 1000 * Math.pow(2, conn.retry - 1));
      setState('retrying', `Disconnected. Retrying in ${Math.round(wait / 1000)} s.`);
      clearTimeout(conn.retryTimer);
      conn.retryTimer = setTimeout(() => {
        if (conn.wanted) openSocket(s);
      }, wait);
    };
  }

  function send(bytes) {
    try {
      if (conn.ws && conn.ws.readyState === 1) conn.ws.send(bytes);
    } catch (e) { /* the close handler deals with it */ }
  }

  const CONNACK_REASONS = {
    1: 'the broker refused this protocol version',
    2: 'the broker rejected the client id',
    3: 'the broker is unavailable',
    4: 'bad username or password',
    5: 'not authorised',
    132: 'the broker refused this protocol version',
    133: 'the broker rejected the client id',
    134: 'bad username or password',
    135: 'not authorised',
    151: 'the broker applied a quota limit',
  };

  function handlePacket(p, s) {
    if (p.type === T.CONNACK) {
      if (p.code !== 0) {
        conn.wanted = false;
        setState('error', 'Connection refused: '
            + (CONNACK_REASONS[p.code] || `reason code ${p.code}`));
        try { conn.ws.close(); } catch (e) { /* already going */ }
        return;
      }
      conn.retry = 0;
      subscribe(s);
      startPing();
      return;
    }
    if (p.type === T.SUBACK) {
      if (!p.ok) {
        setState('error', 'The broker refused the subscription. On a shared broker '
            + 'that usually means the topic is not permitted.');
        return;
      }
      setState('subscribed');
      return;
    }
    if (p.type === T.PUBLISH) {
      // QoS 1 has to be acknowledged or the broker redelivers it forever, which on
      // this page would look like a device alerting in a loop.
      if (p.qos === 1) send(encodePuback(p.packetId));
      handleMessage(p);
      return;
    }
    if (p.type === T.PINGRESP) return;
  }

  function subscribe(s) {
    const topics = [s.topic];
    // The status topic is subscribed separately rather than folded into one wildcard:
    // they carry different documents, and a single filter broad enough for both would
    // also match anything else a shared broker has under that prefix.
    if (s.statusTopic && s.statusTopic !== s.topic) topics.push(s.statusTopic);
    for (const t of topics) {
      const id = conn.packetId = (conn.packetId % 65535) + 1;
      send(encodeSubscribe(t, s.qos, id, conn.version));
    }
  }

  function handleMessage(p) {
    const isStatus = /status$/.test(p.topic);
    if (isStatus) {
      const st = parseStatus(p.topic, p.payload);
      if (!st) {
        state.counters.rejected++;
      } else {
        ingestStatus(st);
      }
      render();
      return;
    }
    const ev = parseAlert(p.topic, p.payload);
    if (!ev) {
      // Counted rather than hidden. On a shared broker a steady rejection rate is
      // information: it means somebody else is publishing to the topic being watched.
      state.counters.rejected++;
      render();
      return;
    }
    if (ingestAlert(ev)) notify(ev);
    render();
  }

  function startPing() {
    stopPing();
    // Half the 60 s keepalive announced in CONNECT. A broker disconnects a client
    // that has been silent for 1.5x the keepalive, and this page sends nothing else.
    conn.ping = setInterval(() => send(encodePingreq()), 30000);
  }

  function stopPing() {
    if (conn.ping) clearInterval(conn.ping);
    conn.ping = null;
  }

  function disconnect() {
    conn.wanted = false;
    clearTimeout(conn.retryTimer);
    stopPing();
    send(encodeDisconnect());
    try {
      if (conn.ws) conn.ws.close();
    } catch (e) { /* nothing to do */ }
    // The password does not outlive the connection.
    conn.password = '';
    setState('closed');
  }

  // ======================================================================== //
  // wiring
  // ======================================================================== //
  function refreshDerived() {
    const s = settings();
    setText('fleet-url', wsUrl(s));
    // The status topic follows the alert topic unless it has been edited: they are
    // the same tree, and keeping them in step by hand is a step nobody remembers.
    const derived = s.topic.replace(/\/alerts$/, '/status');
    const box = $('fleet-status-topic');
    if (box && !box.dataset.touched) box.value = derived;
    const demo = s.host === DEMO.host;
    $('fleet-demo-note').hidden = !demo;
    // A page served over HTTPS may not open a plaintext WebSocket. Saying so here is
    // the difference between a five-second fix and an afternoon: the browser's own
    // error for it is a bare "connection failed".
    const mixed = typeof location !== 'undefined' && location.protocol === 'https:'
        && !s.secure;
    $('fleet-mixed-note').hidden = !mixed;
  }

  function openModal() {
    $('fleet-modal').classList.add('is-open');
    $('fleet-modal').setAttribute('aria-hidden', 'false');
    refreshDerived();
  }

  function closeModal() {
    $('fleet-modal').classList.remove('is-open');
    $('fleet-modal').setAttribute('aria-hidden', 'true');
  }

  function wire() {
    $('fleet-configure').addEventListener('click', openModal);
    $('fleet-modal-close').addEventListener('click', closeModal);
    $('fleet-modal').addEventListener('click', (e) => {
      if (e.target === $('fleet-modal')) closeModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeModal();
    });

    $('fleet-connect').addEventListener('click', () => {
      if (conn.wanted) disconnect();
      else { connect(); closeModal(); }
    });
    $('fleet-demo-preset').addEventListener('click', () => {
      $('fleet-host').value = DEMO.host;
      $('fleet-secure').value = 'wss';
      $('fleet-port').value = String(DEMO.wss);
      $('fleet-path').value = DEMO.path;
      $('fleet-username').value = '';
      $('fleet-password').value = '';
      refreshDerived();
    });

    for (const id of ['fleet-host', 'fleet-port', 'fleet-path', 'fleet-secure',
                      'fleet-topic']) {
      $(id).addEventListener('input', refreshDerived);
      $(id).addEventListener('change', refreshDerived);
    }
    $('fleet-status-topic').addEventListener('input', (e) => {
      e.target.dataset.touched = '1';
    });
    $('fleet-secure').addEventListener('change', () => {
      const now = num($('fleet-port').value, 1, 65535, DEMO.wss);
      // Move the port with the scheme, but only from one default to the other: a
      // port somebody typed should survive.
      if (now === DEMO.wss || now === DEMO.ws) {
        $('fleet-port').value = String($('fleet-secure').value === 'ws' ? DEMO.ws
            : DEMO.wss);
      }
      refreshDerived();
    });

    $('fleet-search').addEventListener('input', (e) => {
      state.filterText = clean(e.target.value, 64);
      render();
    });
    $('fleet-severity').addEventListener('change', (e) => {
      state.filterSeverity = e.target.value;
      render();
    });
    $('fleet-clear').addEventListener('click', () => {
      state.devices.clear();
      state.events.length = 0;
      state.counters = {received: 0, rejected: 0, critical: 0, duplicates: 0};
      state.seenEventIds.clear();
      state.seenOrder.length = 0;
      render();
    });

    $('fleet-notify').addEventListener('change', async (e) => {
      if (!e.target.checked) { state.notify = false; return; }
      if (typeof Notification !== 'function') {
        state.notify = false;
        e.target.checked = false;
        setState(state.connection, 'This browser has no notification API.');
        return;
      }
      let granted = Notification.permission === 'granted';
      if (!granted && Notification.permission !== 'denied') {
        try {
          granted = (await Notification.requestPermission()) === 'granted';
        } catch (err) { granted = false; }
      }
      state.notify = granted;
      e.target.checked = granted;
    });
    $('fleet-sound').addEventListener('change', (e) => {
      state.sound = e.target.checked;
      // A first tone on the click, because most browsers only allow audio to start
      // from a user gesture - and a switch that silently does nothing until the next
      // alert is a switch people toggle twice and then distrust.
      if (state.sound) beep('info');
    });

    for (const btn of document.querySelectorAll('[data-fleet-copy]')) {
      btn.addEventListener('click', () => {
        const src = $(btn.getAttribute('data-fleet-copy'));
        copyToClipboard(src ? (src.value || src.textContent || '').trim() : '', btn);
      });
    }
  }

  // "Last seen" is a moving number, so it is repainted on a timer rather than only
  // when a message arrives - otherwise a fleet that has gone quiet keeps claiming
  // every device was seen "just now".
  function startClock() {
    setInterval(() => {
      if (state.devices.size) render();
    }, 5000);
  }

  loadSettings();
  wire();
  render();
  startClock();

  // Exported for tests/fleet_page_harness.mjs. Attached to the app element rather
  // than to window so that nothing else on the documentation site can reach it, and
  // so that a page without the app never gains the property at all.
  root.__fleet = {
    encodeVarInt, decodeVarInt, encodeString, decodeString, encodeConnect,
    encodeSubscribe, encodePuback, encodePingreq, encodeDisconnect, decodePackets,
    parsePacket, clean, num, parseAlert, parseStatus, deviceFromTopic, ingestAlert,
    ingestStatus, isDuplicate, state, render, ago, uptime, riskBand, wsUrl,
    settings, loadSettings, saveSettings, handleMessage, handlePacket, copyToClipboard,
    refreshDerived, T,
  };
})();
