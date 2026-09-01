---
title: Fleet monitor
description: >-
  A live MQTT dashboard for DrowsyGuard devices - connection status, device cards,
  risk, alerts and an event timeline - running entirely in this page.
---

# Fleet monitor

This page is a working MQTT dashboard. It connects from your browser to a broker over
**WSS**, subscribes to a DrowsyGuard alert topic, and shows one card per device with
the driver's current risk, when it was last seen, and what it last alerted about -
plus a live timeline, counters and filters.

It exists for two jobs: **testing**, because the alternative way to check that a board
is publishing is to fall asleep in front of it, and **demonstration**, because a
supervisor with a phone can watch the device work without installing anything.

!!! warning "The default broker is public, and so is everything on it"
    `broker.emqx.io` is the [official EMQX public broker][emqx]. It is preconfigured
    here and on the device so that a demonstration works in a room with no broker in
    it, and it is **demonstration and testing only**: there is no authentication and
    no isolation, so anyone who subscribes to the topic - which they can guess - reads
    every alert your device publishes, including the driver remark. Point both ends at
    your own broker before any real vehicle. See
    [Security](security.md#mqtt-alerting-leaves-the-vehicle).

[emqx]: https://www.emqx.com/en/mqtt/public-mqtt5-broker

<div id="fleet-app">

<div class="fleet-bar-top">
  <span class="fleet-pill state-idle" id="fleet-state">not connected</span>
  <button type="button" class="fleet-btn is-primary" id="fleet-configure">Configure MQTT</button>
  <button type="button" class="fleet-btn" id="fleet-connect">Connect</button>
  <span class="fleet-spacer"></span>
  <label class="fleet-toggle"><input type="checkbox" id="fleet-notify">
    browser notifications</label>
  <label class="fleet-toggle"><input type="checkbox" id="fleet-sound">
    sound</label>
</div>

<p class="fleet-note" id="fleet-conn-note">Press Configure MQTT, then Connect.
Nothing is sent anywhere until you do.</p>

<div class="fleet-counters">
  <div class="fleet-counter"><b id="fleet-count-devices">0</b><span>devices</span></div>
  <div class="fleet-counter"><b id="fleet-count-active">0</b><span>active alerts</span></div>
  <div class="fleet-counter"><b id="fleet-count-alerts">0</b><span>alerts received</span></div>
  <div class="fleet-counter"><b id="fleet-count-critical">0</b><span>critical</span></div>
  <div class="fleet-counter"><b id="fleet-count-duplicates">0</b><span>duplicates dropped</span></div>
  <div class="fleet-counter"><b id="fleet-count-rejected">0</b><span>messages rejected</span></div>
</div>

<div class="fleet-filters">
  <input type="search" id="fleet-search" placeholder="Search a driver, device or fleet id"
         autocomplete="off" spellcheck="false">
  <select id="fleet-severity" aria-label="Filter by severity">
    <option value="all">every severity</option>
    <option value="critical">critical only</option>
    <option value="high">high only</option>
    <option value="medium">medium only</option>
    <option value="info">info only</option>
  </select>
  <span class="fleet-note">showing <b id="fleet-count-shown">0</b></span>
  <span class="fleet-spacer"></span>
  <button type="button" class="fleet-btn" id="fleet-clear">Clear</button>
</div>

<h2>Drivers and devices</h2>

<div class="fleet-grid" id="fleet-devices"></div>
<p class="fleet-empty" id="fleet-devices-empty">No devices yet. Nothing has published
to this topic since you connected.</p>

<h2>Event timeline</h2>

<ul class="fleet-timeline" id="fleet-timeline"></ul>
<p class="fleet-empty" id="fleet-timeline-empty">No alerts yet.</p>

<div class="fleet-modal" id="fleet-modal" role="dialog" aria-modal="true"
     aria-hidden="true" aria-label="MQTT connection settings">
  <div class="fleet-sheet">
    <h2>MQTT connection</h2>
    <p class="fleet-note">A browser cannot open a raw MQTT socket, so this connects
    over WebSocket. A page served over HTTPS - which this one is - may only use
    <b>WSS</b>.</p>

    <p class="fleet-warn-box" id="fleet-demo-note">You are pointed at the public EMQX
    broker. Anyone can read and publish to any topic on it, so treat everything you
    see here as public and never send real driver data through it.</p>

    <p class="fleet-bad-box" id="fleet-mixed-note" hidden>This page is served over
    HTTPS, so a plaintext <code>ws://</code> connection will be blocked by your
    browser before it is attempted. Use WSS.</p>

    <div class="fleet-row">
      <label class="fleet-field"><span>Scheme</span>
        <select id="fleet-secure">
          <option value="wss">WSS (encrypted)</option>
          <option value="ws">WS (plaintext)</option>
        </select>
      </label>
      <label class="fleet-field"><span>Port</span>
        <input id="fleet-port" type="number" min="1" max="65535" inputmode="numeric">
        <span class="fleet-hint">EMQX: 8084 for WSS, 8083 for WS.</span>
      </label>
    </div>

    <label class="fleet-field"><span>Broker host</span>
      <input id="fleet-host" autocomplete="off" spellcheck="false"
             placeholder="broker.emqx.io"></label>

    <label class="fleet-field"><span>WebSocket path</span>
      <input id="fleet-path" autocomplete="off" spellcheck="false" placeholder="/mqtt">
      <span class="fleet-hint">EMQX serves <code>/mqtt</code>.</span></label>

    <label class="fleet-field"><span>Alert topic</span>
      <input id="fleet-topic" autocomplete="off" spellcheck="false">
      <span class="fleet-hint">Copy this from the device's MQTT modal - it offers a
      fleet-wide form with a <code>+</code> where the device id goes, so one
      subscription covers every board.</span></label>

    <label class="fleet-field"><span>Status topic</span>
      <input id="fleet-status-topic" autocomplete="off" spellcheck="false">
      <span class="fleet-hint">The retained online/offline document. Follows the alert
      topic until you edit it.</span></label>

    <div class="fleet-row">
      <label class="fleet-field"><span>Protocol</span>
        <select id="fleet-version">
          <option value="4">MQTT 3.1.1</option>
          <option value="5">MQTT 5</option>
        </select>
      </label>
      <label class="fleet-field"><span>Subscribe QoS</span>
        <select id="fleet-qos">
          <option value="1">1 - at least once</option>
          <option value="0">0 - fire and forget</option>
        </select>
      </label>
    </div>

    <label class="fleet-field"><span>Client ID</span>
      <input id="fleet-client-id" autocomplete="off" spellcheck="false">
      <span class="fleet-hint">Must be unique on the broker. Two clients sharing one
      disconnect each other in turn.</span></label>

    <div class="fleet-row">
      <label class="fleet-field"><span>Username</span>
        <input id="fleet-username" autocomplete="off" spellcheck="false"></label>
      <label class="fleet-field"><span>Password</span>
        <input id="fleet-password" type="password" autocomplete="new-password">
        <span class="fleet-hint">Kept in memory for this tab only. It is never
        written to browser storage and the box is emptied when you connect.</span>
      </label>
    </div>

    <label class="fleet-field"><span>Connection URL</span>
      <input id="fleet-url" readonly>
      <span class="fleet-hint">Built from the fields above.</span></label>
    <div class="fleet-actions">
      <button type="button" class="fleet-btn" data-fleet-copy="fleet-url">Copy URL</button>
      <button type="button" class="fleet-btn" data-fleet-copy="fleet-topic">Copy topic</button>
      <button type="button" class="fleet-btn" id="fleet-demo-preset">Load EMQX demo</button>
      <span class="fleet-spacer"></span>
      <button type="button" class="fleet-btn" id="fleet-modal-close">Close</button>
    </div>
  </div>
</div>

</div>

## Using it

1. **On the device**, open `http://192.168.4.1/`, press **Configure MQTT**, fill in
   the broker and switch publishing on. Copy the **fleet subscription topic** - the
   middle one of the three, with a `+` where the device id goes.
2. **Here**, press **Configure MQTT**, paste that topic, check the host and port, and
   press **Connect**.
3. **On the device**, press **Test publish**. A card should appear within a second or
   two.

If the device is on its own access point with no route to the internet, it cannot
reach a broker at all - give it station credentials in the same modal, or point both
ends at a broker on the same network.

## What each card shows

| Field | Meaning |
| --- | --- |
| the name | the device's **remark** - "Driver A", "Van 3" - or its device id when it has no remark |
| the dot | green online, red offline, grey no status message seen yet. Offline comes from the Last Will: the broker publishes it when the device stops answering, so it distinguishes "no alerts because the driver is fine" from "no alerts because the device fell off the network" |
| the number | the fused drowsiness risk from the device's last alert, `0.00`-`1.00` |
| last alert | the alert type and its severity |
| last seen | when **this page** last received something from that device |
| PERCLOS | share of the measurement window the eyes were closed for, at the moment of the alert |
| alerts | how many this page has seen, and the device's own running total since its last boot. The second number is larger because it counts alerts nobody was watching for |
| uptime | the device's uptime at the moment of the alert |

The **event timeline** is the same information in arrival order, with the device's own
timestamp when it had one. It usually does not: there is no real-time clock on the
board, so unless something has set the clock the device publishes an empty `ts` and
the page shows the uptime instead. This is deliberate - see the
[payload schema](reference/device-api.md#the-alert-payload).

## What it does not do

* **It never publishes.** This page subscribes and acknowledges; it sends no commands
  to any device. A drowsiness alarm a dashboard can reconfigure - or mute - is a much
  larger safety argument than anything here needs.
* **It stores nothing on a server.** Everything is in the tab. Closing it loses the
  history; there is no backend to lose it from.
* **It never stores a password.** The host, port, path, topic and client id are
  remembered in your browser's local storage so you do not retype them at every
  demonstration. The password is held in a variable for the life of the tab, the field
  is emptied when you connect, and nothing writes it anywhere.

## How it works, and why there is no MQTT library in it

A browser cannot open a raw MQTT socket, so this speaks MQTT over WebSocket - and it
speaks it itself, in about two hundred lines of `docs/assets/js/fleet.js`, rather than
loading a client library from a CDN. Three reasons:

1. This page is published under the project's own domain and its only job is to
   display a driver's alertness state. A third-party script on that page means a third
   party can change what it does, for every reader, forever.
2. It has to be testable. `tests/fleet_page_harness.mjs` drives the codec against byte
   sequences from the MQTT 3.1.1 and 5 specifications; a wrapped library would have
   moved that test to "we called the library correctly".
3. The subset needed is small and closed: CONNECT, SUBSCRIBE, receive PUBLISH,
   acknowledge it, PINGREQ, DISCONNECT. This page never publishes, which is most of
   what an MQTT client is for.

### Everything from the broker is treated as hostile

On the default broker, anyone can publish anything to the topic you are watching. So:

* every string is length-capped and stripped of control characters, C1 codes,
  zero-width characters and the bidirectional overrides that would otherwise let a
  remark reverse the rendering of the rest of the page;
* every number is coerced and clamped - a `risk` of `1e308` becomes `1`, a `NaN`
  becomes `0`;
* a payload that is not a JSON object, or whose `schema` is not
  `drowsyguard.alert.*`, is **rejected and counted** rather than rendered. A steady
  rejection rate is information: it means somebody else is publishing to that topic;
* every value reaches the page through `textContent`. There is no `innerHTML` in
  `fleet.js`, which is the only structural guarantee available against a page whose
  job is to render strings a stranger chose;
* duplicates are dropped by `event_id`. QoS 1 is at-least-once, and a reconnect
  redelivers whatever the broker still holds, so the same alert arrives more than once
  as a matter of course.

### Troubleshooting

| Symptom | Cause |
| --- | --- |
| "The connection failed" immediately | wrong port or path, or `ws://` from an HTTPS page. The browser deliberately does not say which - that would be a cross-origin information leak |
| connects, then nothing arrives | the topic does not match. Copy it from the device rather than retyping it; a `+` in the wrong level matches nothing |
| "The broker refused the subscription" | a shared broker restricting the topic. Try your own |
| "messages rejected" climbing | something else is publishing to that topic. On the public broker, that is somebody else's project |
| a device stuck at "no status message seen" | its Last Will is off, or its status topic is not the one this page subscribed to |

More in [Troubleshooting](troubleshooting.md#mqtt-and-the-fleet-monitor).
