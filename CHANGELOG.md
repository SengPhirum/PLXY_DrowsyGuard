# Changelog

## 2026-09-05 - saving MQTT settings no longer reboots the board

First hardware contact for the whole MQTT path (gap 13), and it found what host
tests structurally cannot: a task stack. Saving the MQTT settings - and sometimes
merely opening them - rebooted the board, *after* the new configuration had reached
NVS, so every save "took" and crashed at the same time. Publishing itself worked on
the very first try: three real alerts reached `broker.emqx.io` with distinct event
ids on the first boot with a route to it.

The crash: the control httpd task ran on a 6144-byte stack, and the `/api/mqtt`
handler chain is the deepest on the device - `mqtt_config_json()` is a 2768-byte
frame, `mqtt_respond()` 1632, the POST handler another 1024 (all measured with
`-fstack-usage` on the xtensa toolchain), plus httpd's own frames: ~6 kB for a GET,
~7 kB for a POST. GET overflowed by inches and intermittently; POST - the save -
overflowed every time. The stack is 8192 now, with the measured arithmetic in the
comment next to it.

Verified on hardware the same day, after the fix and a Wi-Fi re-provision: `state:
online` against `broker.emqx.io:1883` (TCP, QoS 1), **50 alerts published and
PUBACKed over 2+ hours with no reboot**, `dropped` 0, and `GET /api/mqtt` answering
in 0.17 s. TLS/WSS and the M3/M9 fps measurements are the remaining hardware gate -
see gap 13.

Also observed on hardware, worth knowing: attaching a serial listener to the CH343
port while the app runs can read as a BOOT press (the bridge drives GPIO0), and
five seconds of that cleared the stored Wi-Fi credentials through the button path -
exactly the W10 hazard, from software instead of a finger. The credentials then had
to be re-entered from the device page. The BOOT watcher's arming rules protect a
monitor attached *at boot*, not one attached mid-run.

## 2026-09-04 - the MQTT client stops being able to freeze the detector

An audit of the MQTT runtime ahead of its first hardware run against a broker
(gap 13) found one mechanism that stalls the capture loop outright, one that keeps
the client from ever connecting under load, two that silently lose an alert, and an
eight-second delay on every reconnect. All five are fixed; none changes the API, the
payload schema, the NVS format, a topic, or any behaviour a subscriber can see.

### The freeze: the TLS handshake could run on the inference core

esp-mqtt's client task - where DNS, the TCP connect and the **entire TLS handshake**
run - is created at priority 5 with **no core affinity** by default. When the
scheduler placed it on core 0, a second or more of mbedtls arithmetic sat above
`app_main` (priority 1), and the capture loop froze for the duration of every
handshake. Worse, the reconnect backoff *repeats* that handshake for as long as a
broker is unreachable, so a dead broker read as a detector that periodically hangs
forever. Every other network task in this firmware was already pinned to core 1
(both httpd servers, the event writer, the publisher task); the one that does the
most CPU-bound work was the one left floating. `CONFIG_MQTT_TASK_CORE_SELECTION_ENABLED`
+ `CONFIG_MQTT_USE_CORE_1` pin it with the rest. DEPLOYMENT.md gained test M9, which
measures `fps` *during* connection attempts rather than between them.

### The connect abort: an alert mid-handshake tore the client down

`mqtt_publish_alert()` notifies the publisher task for every queued alert, and the
publisher's "wait for the CONNACK" was a single 8 s `ulTaskNotifyTake`. An alert
arriving mid-handshake woke it early; the code read "still not connected", destroyed
the half-connected client and went to backoff. Under a burst of alerts - the one load
pattern this feature exists for - the client could never finish connecting. The wait
is now a loop over the connection state with a deadline, the event handler notifies
on CONNECTED/DISCONNECTED (which also removes a silent 8 s before every post-connect
flush - test M12), and a reconfigure breaks the wait instead of queueing behind it.

### Two ways to lose an alert without a counter moving

* **De-duplication recorded ids it had not delivered.** `seen_or_add()` put the
  event id into the ring *before* `esp_mqtt_client_enqueue()` ran, so when the
  enqueue failed (client busy, outbox full) the retry matched its own first attempt
  and was committed as a "duplicate" - dropped, unsent, uncounted. The ring now has
  the two halves the publisher actually needs: `already_published()` asks,
  `mark_published()` records only after the transport accepted the message.
  `seen_or_add()` survives, expressed on the two.
* **The outbox could commit an event it never published.** Between the publisher's
  peek and its commit, the capture loop can push into the full ring and evict that
  same head; the commit then removed whatever replaced it - an event nobody had
  sent. `commit_if_seq()` removes the head only if it is still the event the
  publisher just handled. Both are pure `mqtt_config.cpp` changes, and both have the
  race written out as host tests (`test_mqtt_config.py` is 162 cases now, five new).

### Smaller

* The publisher task's stack goes 5120 → 6144: `client_stop()`'s goodbye publish
  runs `mbedtls_ssl_write` on that stack, on top of ~1 kB of topic+body locals, and
  a publisher stack overflow is a panic.
* The esp-mqtt event handler no longer reads `s_live` (the publisher task's private
  config copy) for its log line, and no longer reads `s_error` outside its lock -
  both were benign-looking cross-task races.
* `CONFIG_MBEDTLS_SSL_KEEP_PEER_CERTIFICATE=n`: the broker's certificate chain is
  freed once the handshake has verified it, ~4 kB of heap back per live TLS
  connection. Nothing reads the peer certificate after connect.
* `CONFIG_LWIP_MAX_SOCKETS` was defined twice in `sdkconfig.defaults` (16, then 20);
  one definition now, at 20. A stale comment still describing the Wi-Fi TX buffer
  count as 12 (it is 8) is fixed.

### Measured

IDF v5.5.5, `sdkconfig` regenerated from the updated defaults (so the delta includes
that regeneration, not only the code):

|  | before (2026-09-02 build) | after |
| --- | --- | --- |
| app image | 3 560 496 B | 3 574 928 B (+14.4 kB, 43 % of the partition free) |
| static D/IRAM | — | 235 575 B used, 106 185 B free (68.9 %) |
| host tests | 158 passed | 588 passed, 5 failed — the same five UTF-8-SSID cases that fail on this Windows harness before the change (codepage artefact, not firmware) |

The runtime numbers this change is actually about - `fps` during a TLS reconnect
(M9), flush latency after a CONNACK (M12), heap over a soak (M13) - need a broker
and the board, and are the first thing to record when gap 13's hardware run happens.

## 2026-09-02 - the board stops rebooting when you join it, and the sneeze feature is gone

Two things, found in that order: the device rebooted the moment anything associated
with its access point, and once that was fixed it turned out to be short of internal
RAM in a way that was crashing the HTTP handlers.

### The reboot loop

A phone joining `DrowsyGuard-XXXXXX` panicked the board:

```
I (5096) wifi:station: 92:01:f3:ca:38:24 join, AID=1, bgn, 20
I (5123) wifi: client joined: 92:01:f3:ca:38:24 (aid 1)

***ERROR*** A stack overflow in task sys_evt has been detected.
```

`on_wifi_event()` declared `WifiScanEntry staged[WIFI_SCAN_MAX]` - 24 x 36 = 864
bytes - on the event task's stack. Xtensa allocates a function's frame in its
prologue, so **every** Wi-Fi event paid for the scan buffer whether or not a scan had
happened. Measured with `-fstack-usage`: 944 bytes of frame against a 2304-byte task
stack, before `ESP_LOGI`'s `vsnprintf` and the netif frames on top. The array is
`static` now (the default event loop is single-threaded, so this handler is its only
writer) and the frame is 144 bytes. The irony is three lines above it, where `records`
was already static with a comment saying the event task's stack "is not generous".

### Then it would not let anyone in

With the crash gone, associations started failing outright - `wifi:m f auth`,
`removing station after unsuccessful auth/assoc` - because the driver could not
allocate per-station state. A boot-time census found why:

```
At 0x3fcc8814 len 32767 free 32031 allocated 0      <- the DMA reserve, untouched
At 0x3fcc2180 len 161168 free 10916 allocated 148288
At 0x3fce9710 len 22308 free 4 allocated 21132      <- exhausted
```

`CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL` was IDF's default 32768, and that pool is
created with DMA/internal capabilities only - so generic `malloc`, which is what
newlib, lwIP and esp-mqtt use, cannot draw on it however starved it gets. It sat at
`allocated 0` while `fopen()` aborted inside newlib's `lock_init_generic()` and
`lwip_send()` faulted on a socket mutex. Now 8192.

Also from measurement rather than estimate: `main` had **136 bytes** of stack
headroom out of 8192 while running the capture loop and behaviour fusion, so
`CONFIG_ESP_MAIN_TASK_STACK_SIZE` went **up** to 12288. Wi-Fi static TX buffers 24 ->
8, lwIP TCP window and send buffer 65534 -> 16384, recv mailboxes 64 -> 16/32.

One thing that looked like a win and was not: dropping
`CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL` to 1024 frees a useful amount of internal RAM
and crashes the detector within seconds, in `dl_tie728_s8_conv2d.S` and
`eye_model_infer()`. ESP-DL allocates its layer buffers with plain `malloc` and its
SIMD kernels cannot run against PSRAM. It stays at 4096, with a comment saying so.

### Sneeze detection is removed

The feature is gone: the detector, both counters, the `sneeze_alert` edge, the alert
channel, the reason, the two voice clips, the API fields, the page readouts, the MQTT
severity case and the docs. `AlertReason::NoDriver` is now `4`, not `5`, and
`ALERT_REASON_COUNT` is `5` - so `/api/alert-test?reason=N` and `./plxy.sh alert`
renumber with it. The firmware binary is 126 kB smaller.

**One piece deliberately survives.** Sneeze detection did two jobs: it was a feature,
and it was a false-alarm guard. A closure that begins with the mouth flung wide open
still has to outlast `REFLEX_MAX_S` (1.2 s) rather than `MICROSLEEP_MIN_S` (1.0 s)
before it counts as a microsleep, because an involuntary reflex shuts the eyes and
opens the mouth in one movement and duration alone cannot tell it from a microsleep.
Removing that too would have made every sneeze a "microsleep" announcement aimed at a
driver who is wide awake. It keeps no state, counts nothing and reports nothing;
`REFLEX_MAX_S` and `REFLEX_JAW_DELTA` in `behavior.h` are all that is left, mirrored
in `behavior.py` and checked by `test_firmware_parity.py`.

## 2026-09-02 - the board can be told which network to join, and told to forget it

Wi-Fi provisioning. The device page scans, joins, reports, and forgets; a five-second
hold on BOOT erases the stored credentials from the outside. Nothing here can take the
access point down, and nothing here runs on the detector's core.

### The constraint, first

A device configured for a network that no longer exists is not a broken device unless
you cannot reach it. So the access point is up **during** a scan, **during** a join and
**after a failed one** — which meant changing how the radio starts: it now comes up
`WIFI_MODE_APSTA` with the station netif created whether or not anything is stored. It
used to create the STA interface only when credentials existed, which made first-boot
provisioning impossible without a reboot and made scanning impossible at all.

Everything else follows from that:

| Requirement | How |
| --- | --- |
| A scan must not stall the page | `esp_wifi_scan_start(&cfg, false)` and a `WIFI_EVENT_SCAN_DONE` handler. The HTTP handler starts it and returns; the results are collected by a later request |
| A scan must not touch detection | It never did — the radio is on-die, the capture loop and ESP-DL are on core 0, and the web servers are on core 1. The scan does interrupt the *access point* for two to three seconds, because there is one antenna, and the page says so |
| A wrong password must cost nothing but the network | The AP is a separate interface with its own netif. `sta.state` goes to `failed`, `reason_text` says why, the backoff doubles from 2 s to a minute, and `192.168.4.1` never blinks |
| A reboot must rejoin | The credentials were already an NVS blob with a magic, a version and a CRC. Now they are written by a form that owns them alone |

### The physical reset, and the hazard that shaped it

`ButtonWatch` in `wifi_provision.cpp` is pure C++ with no ESP-IDF headers, driven by an
injected clock in `tests/test_wifi_provision.py`. Three rules:

1. nothing counts in the first three seconds after boot;
2. **a debounced release must be seen before any press is believed**;
3. a press that never releases stops counting after thirty seconds.

Rule 2 is not defensive programming. This board's auto-reset lines are inverted, so
pyserial pulls GPIO0 low when it opens a port — anyone attaching `idf.py monitor` is,
electrically, holding BOOT down for as long as the terminal is open. Without that rule
the first five seconds of every debugging session would erase the device's Wi-Fi.
`test_a_pin_held_low_from_boot_never_fires` is the test that pins it.

What the button does when it fires is `settings_clear_wifi()` plus `board_wifi_forget()`
and nothing else. One NVS key erased by name from a namespace holding four; the broker
settings, the device identity, the CA certificate and the captures all survive. It does
not reboot, either — the access point, the camera and the detector keep running, so the
page stays open in your hand while the reset happens.

It also deliberately makes **no sound**. `board_audio_play_tone()` blocks until its
samples are queued and neither `board_audio.cpp` nor `voice_alert.cpp` holds a lock, so
a convenience beep from the button task would put a second writer on the I2S path the
alert uses. On a device whose speaker is the only output a drowsy driver perceives,
that is not a trade worth making. Feedback goes to the serial log and to the page.

### An SSID is 32 bytes chosen by a stranger

Anybody in radio range can stand up an access point and name it whatever they like, and
that name lands in the device's own recovery page. Two things came out of taking that
seriously:

- `settings_json_escape()` passes bytes ≥ 0x80 through untouched, which is right for
  every field it was written for — they are all validated to printable ASCII first —
  and wrong for a scanned SSID. A high byte that is not part of a well-formed UTF-8
  sequence makes the whole document undecodable, so `JSON.parse` throws and the
  operator sees an **empty network list on the page they are using to recover the
  device**. That is the cheapest denial of service available against this feature and
  it costs one beacon frame. `settings_json_escape_utf8()` passes well-formed sequences
  through — a network really called `café` reads as itself — and escapes everything
  else as `\u00XX`;
- the scan buffer is sized for the worst case rather than the likely one: 24 networks
  of 32 bytes that all need six characters each is about 6.3 kB, so it is 8 kB and in
  PSRAM rather than 4 kB on the control task's doorstep.

The page escapes independently for HTML. Two alphabets, two defences, neither
substituting for the other.

Relatedly, the SSID rule was **loosened**: 802.11 says an SSID is 32 arbitrary octets,
and networks named in Khmer or with an accent are ordinary things this device has to be
able to join. Control bytes are still refused — the serial log is a terminal. The
*passphrase* stays printable ASCII, because that is what WPA2-PSK itself allows.

### One record, one owner

The station credentials used to be editable from the MQTT modal, since MQTT was what
first needed them. They are not any more: `/api/wifi` owns that NVS record and
`/api/mqtt` no longer reads or writes it. Two forms over one record meant that saving
the broker from a page opened before a network change put the old SSID back without
saying so. `test_the_mqtt_modal_is_wired_to_the_firmware_api` now fails if those fields
come back.

### New surface

| Endpoint | Does |
| --- | --- |
| `GET /api/wifi` | where it is joined, why it is not, and whether the reset button is armed. No passphrase — `password_set` and nothing else |
| `POST /api/wifi` | connect, `action=forget`, `action=reconnect`. Empty password means keep |
| `GET /api/wifi/scan` | the last scan: strongest first, de-duplicated, hidden ones dropped, at most 24 |
| `POST /api/wifi/scan` | start one, return immediately. **409** while the radio is busy |

`/api/status` gained `net.sta_state`, `sta_bars`, `sta_retry_ms` and `button_armed` —
four scalars, no SSID and no credential, because that document is what a screenshot of
the device page shows.

### Files

| New | Owns |
| --- | --- |
| `main/wifi_provision.{h,cpp}` | pure. The button state machine, scan preparation and de-duplication, the scan JSON, signal bars, station states, and the sentence for each 802.11 disconnect reason |
| `main/board_button.{h,cpp}` | the GPIO0 polling task. Core 1, priority 2, 50 ms. No logic of its own |
| `tests/test_wifi_provision.py` | 74 cases against both files, host-compiled with `-Wall -Wextra -Werror` |

### Verification

- ESP-IDF v5.5.5, real xtensa-esp32s3 toolchain: builds clean;
- 74 Wi-Fi cases, 158 MQTT/settings cases, the device-page harness (a stub DOM against
  the real page, now including the scan list and every Wi-Fi action), `./plxy.sh
  docs-check` green;
- **not run on hardware.** `docs/DEPLOYMENT.md` has W1–W11 with pass criteria that do
  not depend on anybody's judgement. W4 and W8 are the two to record: a wrong password
  must leave `192.168.4.1` serving, and a five-second hold must clear the network and
  nothing else.

## 2026-09-01 - the alerts leave the vehicle, without the detector noticing

MQTT alerting. Every confirmed alert is now also published to a broker as one
versioned JSON document, there is a settings modal on the device to configure it, and
the documentation site carries a live fleet dashboard that subscribes to it. The
detection loop is byte-for-byte unaffected on the paths that matter, and that was the
constraint the whole design was built around rather than a claim made afterwards.

### The rule, first, because everything else follows from it

This device has one job: to wake a drowsy driver. The capture loop has a ~23 ms frame
budget; a TLS handshake is seconds and a blocked socket write is unbounded. So the
capture loop's entire involvement in publishing is one call that takes a mutex with a
**zero** tick timeout, copies a 96-byte struct into a 16-deep ring, and returns:

```text
capture loop ──push──▶ MqttOutbox ──publisher task──▶ esp-mqtt ──▶ broker
  (bounded memcpy)      (16 deep)   (connect, backoff, retry, dedup)
```

`mqtt_publish_alert()` cannot allocate, cannot fail in a way the caller has to handle,
and does not care whether there is a broker, a network or a configuration. It sits
third at each of the three alert sites in `main.cpp`, after `voice_alert_trigger()` and
after the SD capture, and the order is the documentation: the speaker is the safety
output, the card is the evidence, this is telemetry.

The same rule the browser preview already followed (`web_server.h`), for the same
reason. `docs/DEPLOYMENT.md` now has an acceptance test for it - M3 - whose pass
criterion is that `fps` does not move with the broker dead.

### Where the logic lives, and why the split is the point

Two new files hold everything about this feature that can be wrong without a broker in
the room, and neither includes a single ESP-IDF header:

| File | Owns |
| --- | --- |
| `main/device_config.{h,cpp}` | device identity, station credentials, validation, percent-decoding, the versioned NVS blob codec |
| `main/mqtt_config.{h,cpp}` | the broker config, validation, topic and URI generation, the alert and status payloads, redaction, the backoff schedule, `MqttDedup`, `MqttOutbox` |

Two more hold what genuinely cannot run on a host, and they own no logic of their own:
`main/mqtt_publisher.{h,cpp}` is a state machine over the three data structures above,
and `main/settings_nvs.{h,cpp}` is `nvs_set_blob` around the serialisers.

`tests/test_mqtt_config.py` compiles the two pure files with the host compiler and
drives them through 155 cases. It is not a smoke test - it is where the decisions are
checked:

- **failure isolation.** 10 000 pushes into the outbox with nothing draining it. Every
  one is accepted, memory stays bounded at 16, and the count of discards is exactly
  9 984. A broker that has been down for a week costs the detection loop one memcpy per
  alert.
- **the blob format.** Every single byte of each stored record is flipped, and every
  one has to be rejected; then every truncation length; then a version bump. A raw
  struct in flash is a promise never to reorder a field, and the first time it is broken
  the device reads a hostname out of the middle of a password.
- **topic generation**, including the injection attempts. `a/b`, `a+b`, `#` and an empty
  id all produce **no topic at all** rather than a plausible one, because a topic is the
  one field where a wrong value does not fail - it goes somewhere else, possibly
  somewhere a stranger is subscribed to.
- **no secrets through the API.** The redacted document is searched for the password
  rather than inspected field by field, so a future field that happened to include it
  would fail this test and pass a field-by-field one.
- **the payload schema**, field by field, plus a remark full of quotes and backslashes,
  plus a NaN, plus a buffer too small (which produces nothing rather than half a
  document).
- **the backoff**, the de-duplication ring's eviction, the CA shape check, and the
  form decoder against the malformed escapes a hand-written client sends.

### What was found by building it, rather than after

Two bugs the ESP-IDF build caught that a host compile could not: `snprintf` into the
radio's 32-byte SSID field is a `-Werror=format-truncation` error once the source is a
runtime string rather than a literal (fixed with the bounded-copy idiom the AP path
already used), and a `%s` in the status JSON with no argument behind it.

One the host tests caught in the *tests*: `printf`'s argument evaluation order is
unspecified, so reading a counter in the same call that mutates it reported the state
from before the call as often as after it - which made the de-duplication counter look
permanently stuck at zero.

And one in `fleet.js`, from a test written to fail: `Number(null)`, `Number([])` and
`Number('')` are all `0`, so the obvious coercion turns a missing `risk` into a
confident zero - which on a dashboard reads as "this driver is fine" rather than "this
message told us nothing".

### The device UI

An MQTT card and a settings modal on the device page: enable, transport
(TCP/TLS/WS/WSS), MQTT 3.1.1 or 5, host, port, WebSocket path, client id, credentials,
CA certificate, QoS, keepalive, Last Will, the driver remark, automatic or manual
topics with copy buttons for all three, Wi-Fi station credentials, and a test publish.

The credential handling is the part worth reading. `GET /api/mqtt` returns
`password_set` and **no password field at all** - not masked, absent; the username
comes back masked (`fl*********r`) so an operator can tell which account is configured
without the value being readable off a screenshot. The page's password boxes therefore
open empty with a placeholder, an empty box submits nothing (which the firmware reads
as "keep the stored one"), and erasing takes an explicit Clear button. The page never
holds a secret it did not just receive from the operator's own keyboard.

`POST /api/mqtt` takes a form-encoded body rather than a query string, because a CA
certificate is 1-2 kB and `CONFIG_HTTPD_MAX_URI_LEN` is 512 - and rather than JSON,
because every field is a scalar and the percent-decoder is then a pure function a host
test can drive on malformed input.

### Wi-Fi station mode became a runtime setting

It had to. The board's own access point has no route to the internet, so a device that
could only be its own AP could only ever publish to something on the same island, and a
demonstration has to be able to point it at a phone hotspot in the room without a
rebuild. `WIFI_STA_SSID` is now a default rather than the only route; credentials in
NVS win, and are applied without a reboot when the radio came up in AP+STA mode.

**The access point comes up either way and never comes down.** A wrong hotspot
password, a dead uplink or a broker refusing the credentials all degrade to "no
telemetry", never to "no dashboard" and never to "no alarm".

### The fleet monitor, in the documentation

`docs/fleet-monitoring.md` is a working dashboard: WSS connection, a Configure MQTT
modal, connection status, searchable driver cards with risk and last-seen, an event
timeline, counters, severity filters, browser and audio notifications, and copy-topic
controls. It runs entirely in the reader's browser - no backend, nothing proxied.

It speaks MQTT over WebSocket itself, in about two hundred lines, rather than loading a
client from a CDN. The page's only job is to display a driver's alertness state, and a
third-party script on it means a third party can change what it does for every reader,
forever; SRI pins a version but not the decision to depend on one. The subset needed is
small and closed - CONNECT, SUBSCRIBE, receive PUBLISH, acknowledge, ping, disconnect -
and `tests/fleet_page_harness.mjs` drives it against byte sequences from the MQTT 3.1.1
and MQTT 5 specifications, which a wrapped library would have turned into "we called
the library correctly".

Everything below the socket is treated as hostile, because on the default broker it is:
strings are length-capped and stripped of control characters, C1 codes and the
bidirectional overrides that would otherwise let a remark reverse the rendering of the
rest of the page; numbers are clamped; a document that is not a `drowsyguard.alert.*`
object is rejected and **counted** (a steady rejection rate means somebody else is
publishing to that topic); and every value reaches the DOM through `textContent`. There
is no `innerHTML` in the file and `tests/test_fleet_page.py` fails the build if one
appears. It never publishes, and it never stores a password.

### The public broker is labelled everywhere it appears

`broker.emqx.io` is preconfigured - TLS 8883, TCP 1883, WS 8083/mqtt, WSS 8084/mqtt -
because a thesis demonstration has to work in a room with no broker in it. It is
**demonstration and testing only** and says so on the device modal, in the API
response, on the fleet page, in the security document and in the deployment
checklist: there is no authentication, no isolation, the topics are guessable, and
anyone who subscribes reads every alert including the driver remark.

No images are ever published. The JPEG captures stay on the card, and the remark - the
one field that names a person - is deliberately **not** part of any topic, where it
would be visible to any wildcard subscriber and retained in broker state.

### Everything unchanged, stated explicitly

The sneeze suppression is untouched. A closure `BehaviorAnalyzer` reclassified as a
sneeze still never reaches the risk filter, so it still produces no drowsiness alert -
here or on the speaker. What is published is the sneeze itself with `severity: "info"`,
because a second-long eye closure went by and the alarm deliberately did not fire, and
without a record of that decision the silence looks like a fault.

Face detection, the eye model, PERCLOS, the behaviour fusion, the risk filter, the
voice alerts, the event captures and the MJPEG preview are all byte-for-byte as they
were. The only changes outside the four new modules are: three `mqtt_publish_alert()`
calls in `main.cpp`, the settings load before `board_wifi_init()`, the station override
in `board_wifi.cpp`, and the `/api/mqtt` handlers plus a compact `mqtt` object in
`/api/status`.

## 2026-09-01 - what counts as a driver, saying so when there is none, and a browser installer

Four things, and the first three are one argument: the pipeline could not tell the
difference between *seeing nothing*, *seeing something that is not a person*, and
*not being able to see*. All three produced the same output, and all three have
different correct responses.

### The detection gate now has to be convinced

`face_gate.cpp` took the highest-scoring plausible box and believed it. That is fine
when the only thing in frame is a face and wrong in every other case: a hand, a
headrest, a phone or a patch of low-light noise all produce boxes, and **one** believed
frame is enough to push a measurement of a non-person into a ten-second rolling
baseline and a PERCLOS window.

Static checks added, each aimed at a specific object rather than at "robustness":

| Check | What it actually rejects |
| --- | --- |
| `FACE_MIN_SCORE` 0.55 | Low light. It collapses the detector's confidence rather than distorting its geometry, so the score is the only thing separating a dark face from a dark wall. The coarse stage runs at 0.10 on this camera and has to, so weak candidates genuinely arrive. |
| `FACE_MIN_SIDE_FRAC` / `FACE_MAX_SIDE_FRAC` | A face too small for the eye crop to contain an eye - at 240 px the floor is a 24 px box, whose patch is already at `eyestate.py`'s 8 px minimum - and anything up against the lens. |
| `FACE_ASPECT_MIN` / `FACE_ASPECT_MAX` | A raised hand, whose box is roughly 1:3. This is the check that fires even when the keypoints happen to land plausibly, which the landmark checks cannot see. |
| `FACE_YAW_MAX` | A nose outside the eye pair horizontally. No head pose does that; a landmark set fitted to a hand does it readily. |
| `FACE_MOUTH_MIN` / `FACE_MOUTH_MAX` | A collapsed mouth (a headrest - nothing in the image separates the corners) and a mouth wider than the head (a bright rectangle). |

And a temporal layer, `FaceTrack`, which is the more important half. A single frame is
not evidence: one implausibly-lucky detection on a headrest passes every static check
often enough to matter at five detections a second. So presence now requires
`FACE_CONFIRM_DETECTIONS` consecutive detections that agree **with each other
spatially**, a candidate that has moved further than a head can move between
detections is refused, and - the part that took two attempts to get right - a new
track never inherits the old one's confirmation.

That last property is what stops a track sliding off the driver and onto a passenger.
The first version conflated "is this candidate continuous" with "is continuity
required", which meant a driver reappearing in exactly the place they had left - the
ordinary end of an occlusion - was treated as a brand new track and lost presence for
a detection interval every time the detector blinked. Continuity is now always
computed; the reacquisition window only decides whether a *discontinuous* candidate is
allowed in at all.

`FACE_GATE_ENFORCE=0` still makes all of it advisory, and that escape hatch is built
and tested rather than a branch nobody has run. It was needed once.

### "No driver detected"

New `presence.{h,cpp}` / `presence.py`, and the reason it is a module rather than a
timer is the health argument. A camera that has stopped delivering frames and a cabin
with nobody in it are the same observation and opposite conclusions, and announcing
the wrong one is worse than saying nothing: "no driver detected" aimed at a driver
sitting right there teaches them the device is broken, and it is - just not about
them.

- Absence, measured from **after** the tracking hold has already expired, produces
  exactly one announcement per episode at `PRESENCE_ALERT_S` (3.0 s, configurable).
- `PipelineHealth::CameraFault` (ten consecutive failed grabs) and `ModelFault` (the
  detector never loaded) suppress it entirely and *discard* the absence episode rather
  than freezing it - when the camera comes back the cabin may hold something else.
- Two debounces, in opposite directions. Absence must persist before it is announced,
  so a mirror check is not an alarm. Presence must persist for `PRESENCE_CLEAR_S`
  before the alert re-arms, so a single flickering detection on an empty seat cannot
  cancel a real absence and restart the countdown from zero forever. That second
  failure mode is silent, which is what makes it the dangerous one.
- A five-second warm-up after boot and after any fault clears, because the camera, the
  models and the auto-exposure are all still settling and a face found - or missed - in
  that window is luck.

The capture loop keeps publishing status while the camera is down, so the page reads
`camera fault` instead of freezing on the last good frame.

### Sneezes are announced, and no longer confusable with a yawn

The suppression is unchanged and is still the point: a sneeze slams the eyes shut for
about a second, which an eye-closure detector records as a microsleep.

What was wrong is that `SNEEZE_JAW_DELTA` alone cannot separate a sneeze from a yawn
that also closes the eyes - a yawn opens the mouth wide, so it clears the threshold
comfortably. A yawn misread as a sneeze is worse than a missed sneeze, because the
suppression window then silences a genuine drowsiness cue for `SNEEZE_MAX_S`. The
discriminator added is `SNEEZE_MOUTH_LEAD_S`: how long the mouth had **already** been
open when the eyes closed. In a sneeze they go together; in a yawn the mouth has been
wide for a second by then.

The obvious alternative was tried first and is wrong, which is worth recording.
Requiring the opening index to *rise during* the closure fails because `EyeGate`'s
median-of-3 delays the closure decision by two frames, so a mouth that opened
simultaneously with the eyes has already finished opening by the time the closure is
declared and the measured rise is zero. It would have rejected precisely the sneezes it
was meant to find.

A confirmed sneeze now also gets one short announcement of its own. The driver has
just closed their eyes for a second and heard nothing; without a word from the device,
a system that decided correctly is indistinguishable from one that missed it.
Detection stays per closure - `sneeze_count` is honest - and the announcement is
edge-triggered with `SNEEZE_ALERT_COOLDOWN_S`, so a fit of three sneezes a second
apart is counted three times and announced once. `sneeze_alerts` is reported
separately.

### Alerts have channels now

`AlertReason::Sneeze` and `AlertReason::NoDriver` (4 and 5; the numbering is API and
is appended to, never renumbered), with English and Khmer clips embedded for both -
twelve clips total, all generated by `scripts/make_voice_clips.py` and all validating
as 16 kHz mono 16-bit.

Rate limiting moved from one global cooldown to one per channel. With a single shared
cooldown a sneeze acknowledgement or a no-driver warning could be swallowed by a
drowsiness cooldown that had nothing to do with it, and the symptom of that is
silence - the hardest failure to notice on a device whose only output is a speaker.
`voice_alert_count_for()` and `alert.counts` report per reason, because forty
microsleep announcements and forty no-driver announcements describe completely
different drives and one of them is not about the driver at all.

### Install to ESP32 from a browser

`docs/getting-started/install-esp32.md` runs ESP Web Tools over Web Serial: Chrome or
Edge, a data cable, one click. The alternative it replaces - clone the repository,
install a 2 GB toolchain, resolve managed components - is a reasonable ask of somebody
developing the firmware and an unreasonable one of somebody who wants to try the
device.

`.github/workflows/firmware-release.yml` builds in Espressif's container at a pinned
IDF, runs the host-compilable logic tests against the same sources, checks the app
fits its partition with 5% to spare, verifies every image named in the build's own
`flasher_args.json` exists, merges them, and then **checks the merged image
byte-for-byte against the parts it was made from** - esptool exits 0 for any merge
that produced a file, and a mis-offset merge is indistinguishable from a good one
until a board will not boot.

No offset is ever typed. `scripts/make_manifest.py` reads them from
`flasher_args.json`, and asserts the lowest image is at 0 before emitting a merged
manifest - on an original ESP32 the bootloader is at `0x1000` and a merged image
written at 0 would put every byte 4 kB early. `flash-offsets.json` publishes the
offsets and a SHA-256 per image so a download can be checked.

`docs-deploy.yml` copies the release assets into `site/firmware/` so the installer
fetches its manifest same-origin. Cross-origin would work only for as long as whatever
CDN serves release assets keeps sending a permissive CORS header, which is not
something this repository controls or would find out about until a visitor's install
silently failed.

### Parity is now behavioural, not just numeric

`tests/test_firmware_parity.py` compared constants, which is necessary and nowhere
near sufficient: the gate and the presence monitor are ordered (which check is
reported first), stateful, and full of boundary cases, and every one of those is
somewhere a transcription can drift without a single number changing.

`tests/test_facegate_parity.py` and `tests/test_presence.py` compile the C++ on the
host and drive **both** implementations through identical inputs - a 400-candidate
randomised sweep, eleven named detection sequences, a 120-step random walk, eight
presence timelines - requiring the same verdict, the same reject reason, the same
state and the same alert edges at every step. The parity test also asserts the sweep
reaches at least ten distinct reject reasons, because a parity test over inputs that
all take the same branch proves nothing.

The presence timelines use a 1/16 s step rather than the device's nominal 1/15.
Both are ~15 fps, but 0.0625 is exactly representable in binary and 1/15 is not - and
the firmware accumulates in float32 while Python accumulates in double, so with an
inexact step the two cross a threshold on different frames. That is a floating-point
artefact, and letting it into every assertion would mean every assertion needed slack,
which would also hide a real one-frame disagreement. The inexact case gets one test of
its own with the slack it actually needs.

`test_firmware_parity.py` also gained a test that fails when a constant is added to
both headers and to neither comparison list, which is exactly how the hand-maintained
`PAIRS` table would have rotted.

### Measured

Host suite: **353 passed, 2 skipped** (the two skips need `data/processed/`, which
this repository does not ship). Documentation: strict build clean, 33 pages.

Firmware, ESP-IDF v5.5.5, against a build of the same tree with these changes
reverted:

| | before | after | delta |
| --- | --- | --- | --- |
| App binary | 3,128,864 B (50% of the 6 MB partition free) | 3,404,992 B (46% free) | +276,128 B |
| Flash `.text` | 1,797,232 B | 1,802,632 B | +5,400 B |
| Flash `.rodata` | 1,197,236 B | 1,466,884 B | +269,648 B |
| DIRAM used | 217,283 B (63.58%) | 218,403 B (63.91%) | +1,120 B |
| DIRAM free | 124,477 B | 123,357 B | -1,120 B |

Almost all of the flash growth is the four new voice clips (259 kB); the logic itself
is 5.4 kB of code. The internal-RAM cost is 1,120 B, of which 1,024 B is the status
JSON buffer growing from 3584 to 4608 bytes to fit the new `presence` and
`alert.counts` objects. Nothing new is allocated from PSRAM.

On hardware - N16R8, OV5640, 240x240 RGB565, flashed with exactly the binary above:

| | measured |
| --- | --- |
| Frame rate, nobody in frame | 19.7 fps |
| Frame rate, tracking, one browser attached | 11.5-17.2 fps |
| `detect`, 0-1 candidates | 39.2-39.6 ms |
| `detect`, a full multi-candidate frame | up to 70.3 ms |
| `eye` | 17.8-18.5 ms per eye |
| Detection hit rate with someone in frame | 20/20 at score 1.00 |
| Gate rejections on real candidates | **0**, every logged interval |
| Internal heap free | 17,475-21,807 B (lower with a viewer attached) |
| PSRAM free | 7.01-7.03 MB |

`detect` is the whole of `model_detect_face()`, gate included. At 0-1 candidates it
matches the 39.6 ms recorded before any of these checks existed; the 70 ms figure is
the pre-existing behaviour noted in `main.cpp` - the refinement stage runs once per
candidate, so a busy frame has always cost more. The gate adds a few float comparisons
per candidate on top of that, at most eight candidates, every third frame.

`gate would drop 0 ok` across every interval is the regression that mattered: an
earlier version of this gate rejected **100%** of real candidates on hardware, and
reported it as a bare count that was indistinguishable from an empty frame.

Every alert path was observed firing on the board, in Khmer, from embedded clips:

```
W voice_alert: ALERT (NO DRIVER DETECTED)
W drowsyguard: no driver for 3.0 s (announcement 1)
...
W voice_alert: ALERT (DROWSY)                       risk 0.69  perclos 0.55
...
W voice_alert: ALERT (NO DRIVER DETECTED)
W drowsyguard: no driver for 3.0 s (announcement 2)
I voice_alert: spoke km_no_driver from embedded
I sdcard: stored event 0000439 (5128 B, risk 0.23, no_driver)
```

The numbered announcements are the re-arm path working: the alert fires at exactly the
configured 3.0 s, once, and only announces again after a driver has been confirmed in
between. Over a ~50 s stretch of continuous `face 0/20` in an earlier run it fired
**once**, not once per frame. Sneeze announcements were observed 5.3 s apart -
therefore past the 2.5 s cooldown, so counted as separate episodes - each stored to the
card with `reason: sneeze`. All twelve clips resolve to `embedded` at boot, so a board
with no SD card speaks every reason in either language.

### Not fixed, and visible in that same run

The sneeze detections in the hardware run above were **not** staged sneezes - the
camera was pointed at a moving, poorly-lit scene, PERCLOS read 0.63, and the closure
detector was firing on noise. That is the known eye-model limitation (IR-trained,
AUC 0.62 on visible light, gap 6 in `PROJECT_STATE.md`) reaching a new cue rather than
a fault in the sneeze logic, and it is worth stating plainly: better closure timing
does not make a bad closure signal good. The sneeze thresholds remain
literature-informed defaults, untuned against labelled sneeze video, which this
project still does not have.

## 2026-08-26 (later) - the docs build, and the page that was silently overwriting another

`Deploy documentation` went red on `main` at `e5effdb`. The message named one
strict-mode warning - `presentation/README.md` is not in the nav - but the warning
was the symptom, not the fault.

**mkdocs treats `README.md` as a directory index.** `docs/presentation/README.md`
therefore built to `site/presentation/index.html`, which is exactly where
`docs/presentation.md` builds. The README won, so the nav entry "Presentation
slides" served the browser deck's author guide and the six slide previews were
absent from the published site. Nothing warned about that; the build output is
where it showed, with `<title>Presentation deck</title>` sitting on a URL that
should have carried `Presentation slides`.

Adding the file to `not_in_nav` would have made the build pass and left the page
shadowed, so the fix is the rename: `presentation/browser-deck.md`, which is not a
directory index, plus a nav entry of its own. That drops the strict warning without
new configuration, and the site goes from 31 pages to 32 because a page that had
been overwritten is now built.

Also linked the browser deck from the Presentation slides page. It had no inbound
link from anywhere in the site and was reachable only by typing its URL.

The two decks - the generated `.pptx` and the eight-slide HTML file - were written
in parallel and overlap almost completely. Both are kept here; choosing between
them is a separate call.

## 2026-08-26 - a six-slide deck for the proposal defence

`scripts/presentation/` generates `docs/assets/documents/DrowsyGuard-Presentation.pptx`:
concept, flow, equipment, current results, conclusion. Generated rather than
hand-drawn, for the same reason the wiring poster is - a threshold that changes in
`behavior.py` has to be changeable on a slide without reopening PowerPoint.

Nothing on it is invented. The cue durations on the concept slide are the constants
in `src/drowsyguard/behavior.py` (`MICROSLEEP_MIN_S` 1.0, `BLINK_MAX_S` 0.4,
`YAWN_MIN_S` 1.2, nod 0.3-1.5 s) and the confirm step is `risk.py`'s trigger 0.72
held for 8 frames. The results slide carries the measured numbers from the
2026-08-23 hardware run - 19.7 fps idle, 10.7-13.6 fps tracking, 15.7 ms per eye,
39 ms per detection, 20/20 detections at score 1.00, PERCLOS 0.00-0.22 at rest -
next to what is still unexercised and to the AUC 0.62 the eye model scores in
visible light. The conclusion slide is about that gap and nothing else.

The results slide ships with **one deliberate empty panel**, a dashed frame where a
live-preview screenshot goes. There is no such screenshot in the repository yet, and
the tutorial's phone mock is a drawing - putting it on a results slide would be
presenting an illustration as evidence.

`gen_assets.js` draws the component art and cue icons as SVG and rasterises them
with sharp, and crops the two photographs out of the existing cover illustration, so
the deck adds one tracked binary (748 KB) rather than eighteen. `build/` and
`node_modules/` are ignored.

## 2026-08-23 (night) - on hardware, and the mirror that inverted every cue

Flashed and run. Three things came out of it that no amount of host testing would
have: one regression I had just introduced, one bug that had been live for a long
time, and one piece of received wisdom about this board that turned out to be wrong.

### The regression, and how it was found in one flash cycle

The new plausibility gate rejected **100% of real candidates**: `face 0/20 ... gate
dropped 2`, with detection time climbing 39.6 -> 73.9 ms in the same intervals,
because the refinement stage runs per candidate - so the candidates were certainly
real. Face detection went from working to never working.

The first version of the gate reported only a count, which is indistinguishable from
an empty frame. It now reports **which check failed, with the numbers it failed on**,
rate-limited to once a second. That single log line is what turned a guess into a
diagnosis:

    gate: 1 cand, #0 would fail roll-too-steep | box 14,35 87x118 score 0.71
        | eye_dist 36.0 (0.41 of box w) roll -178.4 jaw -1.16 nose_frac 0.47

### The bug it uncovered: the frame is mirrored

Every magnitude there is right and every sign is wrong. The sensor is mounted upside
down, so `board_camera.h` applies vflip and leaves hmirror off - and a vertical flip of
an upside-down image is an upright image that is **horizontally mirrored**. The preview
looks perfectly correct, which is exactly why this survived.

In a mirrored frame the detector's "right eye" is at the larger x, so the canonical
order is reversed, roll reads +/-170 instead of 0, and `behavior_face_geometry()`
de-rotates by 180 degrees - which negates every vertical measurement:

- `jaw_drop` read about **-1.2 instead of +1.2**, and it *fell* as the mouth opened.
  `mouth_open` requires a rise of `JAW_OPEN_DELTA`, so **the yawn cue could never fire
  at all** - not in the new code and not in any version before it.
- `nose_frac` survived as a ratio of two negatives. That is why this hid so well: the
  one geometry number on the page that looked sane was the only one that could not
  reveal it.

`behavior_orient_landmarks()` orders the eye pair by x instead of trusting the label,
so it is correct for a mirrored frame, an unmirrored one, and any later change to the
flip settings. What it gives up is the anatomical meaning of "right eye" - index 0 is
now the image-left eye - and nothing downstream depends on it.

After the fix the device reports `gate would drop 0 ok` on every detection, so the gate
is enabled, with the measured values now well inside its limits.

### The BOOT button is not actually required

`plxy.sh` and the firmware README both said this board cannot be put into download mode
by any reset-line sequence. Not so: **its auto-reset lines are inverted** relative to
esptool's convention. Found by trying all four combinations of assignment and polarity:

    dtr = False -> GPIO0 LOW    dtr = True -> GPIO0 high
    rts = False -> EN LOW       rts = True -> EN high

New `scripts/board_reset.py` drives that, and `./plxy.sh flash` now uses it before
falling back to the printed instructions: *"put it in the ROM loader over the serial
lines - no BOOT press needed"*. Verified end to end from a board running the
application.

It also explains a symptom that had looked like a dead board: pyserial de-asserts both
lines on open, which on this board means "hold in reset with BOOT pressed", so simply
opening the port to read the log dropped the chip into the loader.

The script deliberately does **not** certify the result. An early version tried, and
got it wrong in both directions - reporting the application while the board was in the
loader, because a single unflushed read returned the *previous* reset's bytes, and then
reporting failure on a successful run because a reset caught mid-byte prints garbage a
cp1252 console cannot display. esptool is the authority on whether the chip is in the
loader, and `plxy.sh` asks it immediately afterwards.

### Measured, at last

| | |
| --- | --- |
| eye model, boot benchmark on a fixed tensor | **15.7 ms per eye** |
| eye model, in the capture loop | 17.8-18.4 ms |
| face detect, full frame | 39.1-39.5 ms |
| face detect, with candidates to refine | 46-74 ms |
| frame rate, no face | 19.7 fps |
| frame rate, tracking a face | 10.7-13.6 fps |
| detection hit rate, face present | 20/20, score 1.00 |

The old figure was "roughly 45 ms for the pair". One eye per frame plus the accumulator
split puts the per-frame eye cost at ~18 ms, and the ROI engages as expected
(`roi 156`, `roi 164`, `roi 168`).

### A second bug of mine, from the log

`retuned for 109.3 fps: perclos window 45 -> 300 frames, risk needs 60 frames`. The
camera's DMA queue starts full, so the first frames arrive far faster than the loop can
sustain, and an average seeded at `TARGET_FPS` was dragged along with them - setting the
PERCLOS window to its 20-second ceiling and making the alarm demand 60 consecutive
frames. Retuning now ignores anything outside 5-40 fps, waits two log intervals for the
estimate to settle, and only acts on a change of more than 10%.

### Also in this pass

- **Eye and mouth tracking is drawn on the preview.** The box only says a face was
  found somewhere; the landmarks say the eyes and mouth were found in the right places,
  which is what decides whether a PERCLOS or jaw-drop reading means anything. Eyes get
  a lid stroke when shut, the mouth corners are joined so an opening is visible as the
  line dropping and shortening, and the nose is marked as the pitch reference.
- **Nothing is measured when nobody is there.** The loop was feeding `p_closed = 0`
  into PERCLOS every frame with no face, and a zero there does not mean "no driver", it
  means "eyes wide open" - so an empty cabin was recorded as the most alert possible
  driver, and the first seconds after someone sat down were averaged against it. The
  analyzer is now frozen while no face is present, the risk streak decays so a
  confirmation cannot survive the driver leaving, and the page says `no driver` and
  `idle` rather than showing zeros that look like measurements.
- The page harness's canvas stub was missing `arc`, which the landmark overlay needs.
  It failed loudly, which is what that stub is for.
- 191 tests pass, up from 185.

## 2026-08-23 (late) - the cues, measured against what they used to do

Four detection defects, each reproduced on a synthetic trace before and after, by
running the traces through the previous version of `behavior.py` straight out of git.
Counts are events fired:

| trace | before | after |
| --- | --- | --- |
| eyes shut 1.5 s and never reopened | nothing | 1 microsleep |
| the same closure with one frame reading "open" | 2 long blinks | 1 microsleep |
| probability dithering across 0.5 for 2 s | 30 blinks | nothing |
| mouth open 1.0 s, head perfectly still | 1 nod | nothing |
| mouth open 1.4 s, head perfectly still | 1 yawn + 1 nod | 1 yawn |
| jaw barely drops but the mouth narrows | nothing | 1 yawn |
| eight single-frame pitch spikes | 8 nods | nothing |
| one head drop with a frame of noise mid-way | 2 nods | 1 nod |

### The one that mattered most

**A microsleep waited for the driver to wake up.** Closures were only classified when
the eyes reopened, so the alarm for "the driver is asleep" was gated on the event it
exists to interrupt: eyes shut for five seconds produced five seconds of silence. It
now fires at `MICROSLEEP_MIN_S` into the closure, while the eyes are still shut. The
wait is longer only when the mouth says it could still be a sneeze - a sneeze resolves
inside `SNEEZE_MAX_S`, so outlasting that is itself the proof it was not one.

### The rest

**Speech was being reported as head nods.** `nose_frac` divides by the eye-to-mouth
distance, so opening the mouth lowers it with the head perfectly still - a 0.25 jaw
drop reads as a 0.11 pitch drop, well past the threshold. Measured against the old
code, a mouth open for 0.5 s or 1.0 s fired a nod, and 1.4 s fired a yawn *and* a nod,
announced as the nod because that bit is tested first. Openings past `NOD_MAX_S`
escaped only by outlasting it. `nose_norm` divides by eye distance instead, which the
jaw cannot change, and both channels must now agree.

**One noisy frame destroyed the event it landed in.** A microsleep is 15 frames at
15 fps and one frame reading "open" split it into two long blinks; the same single
frame reset a yawn's 1.2 s timer, and split one nod into two counted nods - twice the
contribution to the fused score of the one that happened. `CUE_GAP_S` tolerates a
brief lapse in all three. The closure's duration is still measured to where the eyes
reopened, not to where the tolerance expired, so the tolerance cannot promote a 0.9 s
long blink into a 1.1 s microsleep.

**Half the yawn signal was unused.** Five landmarks give the mouth corners and nothing
else, so there is no lip gap and no mouth aspect ratio - but the corners carry two
signals, not one: they drop *and* they pull inward as the jaw opens wide. `jaw_drop`
used only the first. `MOUTH_NARROW_W` combines them, and a yawn now has to reach a
peak (`YAWN_PEAK_DELTA`) rather than merely cross the threshold that starts it.

**Landmark jitter was a head nod.** The pitch proxy is a ratio of two five-point
distances, so a single frame of keypoint noise carries it past the threshold and back.
With `NOD_RATE_FULL` at 6/min, six such frames pegged the nod cue on their own. A nod
now needs `NOD_MIN_S` of duration and `NOD_PEAK_DELTA` of depth.

**A held box was being counted as fresh evidence.** When detection missed, the last
landmarks were re-fed for up to a second - identical values, pushing duplicate samples
into a 10 s baseline window and keeping the mouth and nod timers running on a pose
that no longer existed. A head pitching far enough down to lose the detector is
exactly when that happened. The geometric cues now pause on held frames; the eye path
still runs, because the eye crop comes from the current frame.

**A probability sitting on the threshold emitted a blink per frame.** `EyeGate` adds a
3-tap median and hysteresis. A median rather than an EMA on purpose: an EMA smooths
the edges of a closure and so distorts its duration, and duration is exactly what
`MICROSLEEP_MIN_S` and `BLINK_MAX_S` measure. PERCLOS is deliberately left on a plain
threshold - it is a fraction over a window, already an averaging operation, so
filtering its input would only add lag and shift the numbers the risk trigger was
tuned against.

### Which detection to believe

New `face_gate.cpp`, kept free of ESP-IDF headers so `tests/test_face_gate.py` can
compile it on the host - the crop-and-map-back is the kind of arithmetic where an
origin applied twice still produces plausible-looking boxes, and the only symptom
would be every eye crop landing a few pixels off, which reads as a weak eye model.

- **Landmark plausibility.** The coarse stage has to run at a score threshold of 0.10
  on this camera, so weak candidates arrive. Five points have structure - eyes roughly
  level, mouth below, nose between, interocular distance a fair fraction of face width
  - and anything violating it is rejected before anything is measured from it.
- **The driver, not the biggest face.** A passenger leaning forward is a bigger face.
  Candidates are now chosen by overlap with the previously accepted box.
- **A region of interest.** While tracking, the detector searches a padded square
  around the last box: 1.4x more linear resolution at an 80-pixel box, 2.5x for a
  small one, nothing past ~93 pixels where the crop is not worth taking - so it helps
  exactly where landmark precision is worst. Every tenth detection sweeps the whole
  frame anyway, or a drifted crop would keep confirming itself inside its own window.

### Speed, and one honest failure

**One eye per frame, alternating**, halves the dominant cost. The two eyes of a face
close together, so there is still one closure measurement per frame - it is the
per-eye refresh that goes to two frames, not the closure's time resolution.

**Three accumulators in the convolution inner loop**: 8% on the host, bit-identical
output. Three other restructurings were tried and measured slower. The interesting one
gathered each patch into a contiguous buffer to cut input re-reads by an order of
magnitude, and lost 20% - the premise was wrong, because the inner loop was never a
serial accumulate, so there was no stall to remove. `eye_model.h` records all four
with their numbers so they are not tried again.

**The loop measures itself now.** `ms.detect` and `ms.eye` are in `/api/status`, on the
Device card and in the log line, and the eye model is benchmarked once at boot on a
fixed tensor. The frame budget in this repo carried an estimate for a long time that
turned out to be wrong by a factor of six.

**Frame rate was silently a threshold.** The PERCLOS window and the risk filter's
confirmation and cooldown were frame counts chosen as durations, so making the loop
faster made the alarm twitchier and the PERCLOS window shorter without anyone editing
a threshold. They are durations now, converted from the measured rate once a second.
`Perclos::set_window()` keeps its samples across the resize rather than clearing,
which is what `PerclosTracker.resize()` already did on the desktop - a PERCLOS of zero
is indistinguishable from eyes wide open.

### Also

- 185 tests pass, up from 149: 12 new behaviour regressions and a 17-case host test
  for the detection gate.
- `behavior.cpp` no longer uses `M_PI`, a POSIX extension libc++ does not define under
  strict conformance - that is what lets it host-compile.
- `FaceDetection` carries canonical `Landmarks` instead of a raw ESP-DL keypoint array,
  so the reorder happens once inside the adapter and no caller can index it wrongly.

**Not yet run on hardware.** The board was disconnected when this was written: the
build is clean and the host tests pass, but the on-device numbers - the boot
benchmark, `ms.eye`, `ms.detect` and the resulting frame rate - have not been read
back yet. Everything in the speed section above is a host measurement or an
arithmetic consequence of one, and is labelled as such.

## 2026-08-23 (night) — Khmer alerts, synthesised online

All four Khmer clips now ship embedded alongside the English, so the language
selector on the page actually changes what the speaker says rather than falling
back to a tone.

| Reason | Khmer |
| --- | --- |
| `drowsy` | ប្រយឰត្ន! អ្នកងងុយគេង។ |
| `microsleep` | ភ្ញាក់ឡើង! ភ្ញាក់ឡើង! |
| `yawning` | អ្នកអស់កម្លាំង។ សូមសម្រាក។ |
| `head_nod` | ប្រុងប្រយឰត្ន! មើលផ្លូវ។ |

`scripts/make_voice_clips.py` gained a second engine. SAPI stays the default and
covers English offline; `--engine google` reaches Google Translate's TTS endpoint,
which is the only thing available on this machine that speaks Khmer at all. The
generated files are committed, so a normal build never touches the network.

Three things that came out of doing it rather than planning it:

- **The console killed the script before the audio did.** A Windows console is
  cp1252 and raises on Khmer, so it died printing its own progress. `stdout` is
  reconfigured to UTF-8 now.
- **The first Khmer set ran 3.9 s.** Khmer is more syllable-dense than the English
  and the endpoint reads it slowly, so the phrases were cut down. Past a few
  seconds a warning stops being an alarm.
- **Levels were all over the place**, and in the worst possible direction: the
  *microsleep* warning, the most urgent of the four, came back at a third of the
  amplitude of the others. Every clip is now normalised to −3 dBFS, which is a
  property of the alarm rather than a nicety.

Verification, since nobody in CI can listen: each clip is checked for peak level,
for not being silent (a TTS endpoint that does not know a language answers 200 with
valid silent audio), and for the fraction of frames actually carrying speech. The
new `test_clip_is_in_the_lookup_table` covers a gap the previous tests had —
`EMBED_FILES` only makes the bytes available, and a clip missing from `kEmbedded` in
`voice_clips.cpp` links, ships, and never plays. 149 tests pass.

**The Khmer needs a native check before it is more than a prototype.** The wording
is a translation of the English, not necessarily what a Cambodian driver expects to
hear in an emergency, and the voice is Google's under Google's terms. Neither is a
code problem: a hand recording dropped into `/audio/` on the card wins over the
embedded clip with no rebuild, which is exactly why the card is checked first.

## 2026-08-23 (evening) — the alert speaks, in a language the page chooses

The speaker works on hardware, so the tone patterns have been replaced by what they
were always standing in for. Each alert reason has its own recorded phrase, and the
reason is the point: a driver who hears *"you appear drowsy"* knows what to do with
it, where three beeps have to be remembered.

| Reason | English |
| --- | --- |
| `drowsy` | "Warning. You appear drowsy." |
| `microsleep` | "Wake up! Wake up!" |
| `yawning` | "You seem tired. Take a break." |
| `head_nod` | "Stay alert. Eyes on the road." |

**Clips resolve from three places, in order** (`main/voice_clips.cpp`):

1. `/sdcard/audio/<lang>_<reason>.wav` — first, because it is the only one that can
   be changed without a toolchain. That is what makes Khmer practical: the recording
   has to come from a fluent speaker, and asking them to rebuild firmware is not
   reasonable.
2. embedded in flash via `EMBED_FILES` — English only, and the reason a board with
   no card speaks rather than beeps.
3. the old tone pattern — never silence. An alarm that says nothing because a file
   is missing has failed at its only job.

The WAV reader is a real chunk walk rather than "assume a 44-byte header": plenty of
tools emit a `LIST` or `fact` chunk before `data`, and assuming the offset would play
metadata as audio — loud, alarming, and exactly the wrong thing for this device to
do. A clip in the wrong format is rejected with a log line naming what was wrong.

**Language is chosen on the web page and persists.** `POST /api/settings?lang=en|km`,
stored in NVS under its own namespace so a Wi-Fi-driven `nvs_flash_erase` cannot
reset it. A driver who set the warnings to Khmer should not find them back in
English after a power cycle.

**Four speak buttons, one per reason**, replacing the single test button and its
dropdown — the only way to know a clip is right is to hear *that* clip. The line
above them reports where the clips are coming from (`km · card`, `en · embedded`,
`km · tone`), because "it spoke Khmer off the card" and "it fell back to embedded
English" sound identical to anyone who does not speak one of the two. `/api/status`
carries the same per-reason breakdown, and the boot log prints it.

**English is generated, Khmer is not.** `scripts/make_voice_clips.py` drives Windows
SAPI, needs no network, and writes mono 16-bit 16 kHz directly. The machine has only
`en-US` voices (David, Zira) — so Khmer is left to a recording, with the pipeline and
the instructions in place for it (`assets/audio/README.md`). Machine-translating a
spoken safety warning into a language the toolchain cannot pronounce is not a
trade this project makes.

`tests/test_voice_clips.py` (new) holds the clips to the format the I2S path
streams, checks each one is actually in `EMBED_FILES` — a clip nobody links in is a
clip that silently never plays — bounds the flash cost, and asserts the filename
stems still match `voice_alert_clip_name()`, since a rename there breaks the lookup
without breaking the build. 120 tests pass.

Also fixed: the SD card now mounts **before** the alert controller initialises. It
did not, so the boot-time report of which clip each reason resolves to could not see
the card — a Khmer recording sitting on it would have been reported as English.

## 2026-08-23 (later still) — everything wired by hand is on one header row

The board's physical header order is now **known**, read off a photograph of the
actual part. Every revision before today said it could not be verified and keyed
each instruction to the printed silkscreen label instead — which was the right call
while it was unknown, and is why nothing downstream had to change when it became
known:

```
top     5V  14  13  12  11  10   9  46   3   8  18  17  16  15   7   6   5   4  EN 3V3
bottom  GND 19  20  21  47  48  45   0  35  36  37  38  39  40  41  42   2   1  RX  TX
```

That answers a question the design could not previously address: can a mini
breadboard be wired without reaching across the board? Yes, and only one way. The
top row is almost entirely the DVP camera bus — GPIO 14 and 3 are the only free pins
on it, and it carries no `GND` at all — so hand wiring has to live on the bottom
row, where `41 42 2 1` is the single run of consecutive free pins.

**The amplifier moved to GPIO 41, 42 and 2**, three adjacent holes, and the buzzer
fallback moved from 2 to 1 so it still sits beside them. Third and final move: the
signals were on 39/38/40 until the microSD card claimed that bus, then briefly on
14/21/47, which worked electrically but straddled both rows. `5V` is the one
unavoidable exception — it exists only at the top-left, and goes to the breadboard's
`+` rail that this build sets up anyway.

Three new invariants in `tests/test_tutorial_diagrams.py`, each covering a failure
this project could not previously detect:

- every wired GPIO is actually brought out to a header. Nothing else could catch
  `#define AUDIO_PIN_DIN 34`: the build would succeed, the diagram would draw a
  label, and the only symptom would be a module that never responds;
- every hand-wired signal lands on the bottom row, so a future pin change cannot
  quietly undo the one-row layout;
- the three I2S pins are physically adjacent, which is the actual goal rather than a
  side effect.

**Also corrected: GPIO 3 is not free.** It is the ESP32-S3's JTAG-source strapping
pin. It had been listed as spare in `pinmap.py`, the GPIO figure and the tutorial
since those were written. Free now: 14, 21 and 47.

## 2026-08-23 (later) — first hardware run: eye model bound, microSD history, preview rebuilt

Flashed and running on the board with MAC `80:b5:4e:c5:e0:18`. Everything below was
measured on it, not estimated.

**The eye model is bound, and PERCLOS moves for the first time.** Not through
ESP-DL: that needs a quantized `.espdl`, and **esp-ppq is not on PyPI at all**
(`pip index versions esp-ppq` finds nothing), while this repo has no calibration
set either. Both blockers turned out to be irrelevant - the network is four
convolutions and 11,250 parameters, so `main/eye_model.cpp` runs it directly in
float32 from weights exported by `scripts/export_eye_model.py`. Skipping
quantization also takes quantization error off the table.

`tests/test_eye_model_parity.py` holds that transcription to the ONNX graph on the
host to within 1e-5, across normal, saturated and flat inputs, and pins the two
things easiest to get wrong: conv3 is followed directly by conv4 with no ReLU
between them, and conv4 has no bias. It host-compiles the firmware file itself,
falling back to Zig's bundled clang (`pip install ziglang`) where no system
compiler exists - which is every Git Bash checkout on Windows.

The accuracy caveat is unchanged and is now the real gap: this model is IR-trained
and scores AUC 0.62 on visible light. PERCLOS moving is "the pipeline is complete",
not "the detector is accurate".

**Cost, measured:** ~45 ms for both eyes, against an old estimate of 4-8 ms. The
estimate assumed ESP-DL's int8 vector kernels; scalar float is about 7.7 cycles per
MAC. Effect on the loop: 19.7 fps with no face in frame, ~10 fps while tracking
one. Two optimisation routes are written up in `docs/FIRMWARE_PIPELINE.md`.

**microSD history.** Every alert now files the frame that caused it, browsable
afterwards in the page. `main/board_sdcard.cpp` mounts the slot over SDMMC 1-line
and keeps a plain-text index - deliberately not JSON, because a car can lose power
mid-write and a truncated last line of a text file costs one event while a
truncated JSON array costs the whole history. Ring-buffered at 1000 events. New
endpoints: `GET /api/events`, `GET /api/event?id=`, `POST /api/events/clear`.

Encoding and the card write happen on a background task; the capture loop only
stages a copy of the frame, about 3 ms, because an SD write can block for tens of
milliseconds and a drowsiness alert firing is the worst possible moment to stall.

**The amplifier moved to GPIO 14/21/47.** The microSD slot's SDMMC bus is
hard-wired to 38/39/40, which is where the amplifier had been sitting while the
slot was empty. The bus cannot move, so the amplifier did - onto three of the pins
the SPI panel gave back, chosen because they are the only free pins already proven
as fast digital outputs on this board. Still seven wires; three of them land
somewhere new. `tests/test_tutorial_diagrams.py` now asserts the amplifier never
lands back on the card's bus, because the symptom would be a history page that is
simply always empty.

**The preview no longer flashes white.** It was an `<img>` pointed at an MJPEG
stream; mobile browsers blank that element between parts of a
`multipart/x-mixed-replace` response. Diagnosed rather than guessed: a peak-hold of
mean frame luminance, added for the purpose, showed the camera never left 115-135
while the preview was visibly flashing white - so the frames were fine and the
rendering was not. The page now fetches discrete JPEGs from `GET /frame` on port 81
and draws them into a `<canvas>`, which cannot blank, tells the page when a frame
actually arrived, and lets two phones share the port instead of the second waiting
for the first to close its tab. `/stream` is kept for `curl` and generic MJPEG
clients.

**Frame delivery is demand-driven**, which was a separate bug with the same
symptom. The capture loop used to copy 115 kB into a snapshot buffer on every frame
the moment a viewer existed - 19 fps of copying to feed a 12 fps stream - and the
old pacing loop advanced its deadline before checking whether a new frame existed,
so a producer fractionally slower than the stream rate burned a whole extra period
and frames left in irregular bursts. The streamer now asks for a frame when it is
ready for one. Measured: with a phone streaming, the detector recovered from a
pinned 10.0 fps to 16-19.7.

**UI stability.** Reported as "brightness flashing up and down too fast" and "too
much move UI element", and both were real:

- pills are sized by their widest possible content, so a value change cannot resize
  one and reflow the row beneath it;
- the alert banner has a reserved slot instead of `display:none`, which used to
  shove the whole page down 48 px whenever an alert fired or cleared;
- the risk figure, the meta columns and the log have fixed widths and heights, so
  nothing grows as it fills;
- frame rate and brightness are exponentially smoothed and quantised - the raw
  values move every frame and the last digit was noise;
- colour bands have hysteresis, so a value sitting on a threshold cannot strobe;
- polling slowed from 300 ms to 500 ms, and every text write skips no-op updates.

`tests/test_device_page.py` and `tests/device_page_harness.mjs` (new) drive the page
under a stub DOM across 26 payload shapes - no camera, no card, a field an older
firmware would not send, a device that stops answering - and assert the damping
measurably damps: 60 polls of a deliberately noisy brightness signal now produce 3
distinct readings instead of ~30.

**Camera.** Rotated 180 degrees in the sensor (`CAM_ROTATE_180`), composed with the
selfie mirror rather than replacing it. In the sensor and not in CSS on purpose:
the models read these same bytes, and a face detector does not find upside-down
faces - it would simply stop detecting anyone, which reads as a broken camera. An
earlier revision of `board_camera.h` said "rotate the panel, never the sensor";
that advice died with the panel.

Also: `aec2` explicitly off. On the OV5640 it enables a long-exposure night mode
whose overshoot arrives as blown-out frames, and it was the first suspect for the
white flashing. `set_gainceiling` is deliberately left alone - on this sensor it
writes a raw 10-bit ceiling but takes the OV2640-era enum, so passing
`GAINCEILING_4X` would cap gain at 1x and make the image nearly black.

**A race worth naming.** The snapshot buffers were guarded by a per-buffer `bool`.
Two consumers can encode at once - the stream task on port 81 and `/api/snapshot`
on port 80 - and the second to finish would clear the flag while the first was
still reading, letting the capture loop overwrite a buffer mid-encode. It is now a
hold count. Rare in practice, and a corrupt half-old half-new JPEG is exactly the
kind of fault that gets misattributed to the camera.

## 2026-08-23 — the panel is gone: live preview over Wi-Fi

The SPI display was removed from the build and replaced by a web page the board
serves itself. Join the access point it broadcasts, open `http://192.168.4.1/`, and
the phone or laptop in your hand is the display.

**Why.** The panel cost five GPIOs, a 150 KB PSRAM framebuffer, a per-frame software
blit and one managed driver component per panel variant (ST7735S, then ILI9341). In
exchange it showed 240x320 pixels of 8-pixel-tall text to one person sitting directly
in front of it. The browser shows the frame *and* the fused risk score with its
trigger, the PERCLOS window, per-eye closure probability, blink/yawn/nod rates, head
geometry, an event log and frame timing — at a size that can be read from the
passenger seat — and `GET /api/status` returns the same numbers as JSON, so the
hardware acceptance tests in `docs/DEPLOYMENT.md` can be scripted instead of read off
glass. The build went from **15 wires to 7**, and from 2 spare GPIOs to 7.

**Firmware — new.**
- `main/board_wifi.h/.cpp`: SoftAP bring-up. SSID is `DrowsyGuard-XXXXXX` (last three
  bytes of the AP MAC, so two boards on one bench stay distinguishable), WPA2 with
  `drowsyguard`, and `esp_wifi_set_ps(WIFI_PS_NONE)` because with power save on the
  MJPEG stream arrives in bursts a couple of hundred milliseconds apart, which looks
  exactly like a camera that cannot keep up. Optional AP+STA: fill in `WIFI_STA_SSID`
  and the device also joins a named network, which is how it becomes reachable from a
  development machine without leaving the lab Wi-Fi.
- `main/web_server.h/.cpp`: **two** `esp_http_server` instances — control on port 80,
  MJPEG stream on port 81. One instance serves one request at a time and a stream
  never ends, so a stream on port 80 would block the page and the API for as long as
  anyone watched. Consequence, documented rather than hidden: one live viewer at a
  time, with a still-image fallback on port 80 for everyone else.
- `main/web/index.html`: the page, linked into the binary as flash rodata
  (`EMBED_TXTFILES`) so it can never be out of step with the JSON it parses. The face
  box is drawn client-side in a canvas over the video rather than burned into the
  JPEG — the device keeps encoding pixels it already has, and the box stays crisp at
  any zoom. Automatic fallback to polled stills if the stream slot is taken.
- Endpoints: `/`, `/stream` (:81), `/api/snapshot`, `/api/status`, `/api/settings`
  (`?quality=`, `?fps=`, `?muted=`), `/api/alert-test` (`?reason=0..3`).

**Firmware — the frame handoff, which is the part that had to be right.**
`web_server_publish_frame()` copies one frame into one of two PSRAM snapshot buffers
and returns; it does no encoding, and it returns without copying at all when no
browser is connected. The ~20 ms JPEG encode runs in the stream task, pinned to core
1, on the buffer the capture loop is no longer writing to. With one consumer, two
buffers are provably enough: at most one can be held for encoding, which always
leaves one free to write into. A drowsiness detector that stutters because someone
opened a web page would be a bad trade.

**Firmware — removed.**
- `main/board_display.h/.cpp` and `main/display_ui.h/.cpp` (four files, ~830 lines).
- The `waveshare/esp_lcd_st7735` and `espressif/esp_lcd_ili9341` dependencies. Nothing
  was added in their place: Wi-Fi and `esp_http_server` ship with ESP-IDF, and the
  JPEG encoder (`fmt2jpg_cb`) comes with `esp32-camera`, which was already a
  dependency.
- `main.cpp`: the bring-up loop that cycled panel fills and test tones, the LCD pin
  diagnostics, the base64 frame dumper (`/api/snapshot` returns the actual frame the
  detector was handed, which is strictly better), and the 150 KB framebuffer
  allocation. The ESP-DL self-test over `test_frames.h` survives behind
  `#define MODEL_SELFTEST 0`.

**Firmware — a real bug found on the way.** `voice_alert`'s `max_repeat_count` was a
lifetime budget, not a per-episode one: after three announcements the device went
silent for the rest of the power cycle. With the panel gone the speaker is the only
output the driver perceives, so that is a safety defect rather than an annoyance. The
counter now resets after `repeat_reset_ms` (5 min) without an alert. Also added:
`voice_alert_set_muted()`, `voice_alert_count()` and `voice_alert_test()` — the last
one is what the page's **Test speaker** button calls, because with no display "no
alert fired" and "the amplifier is dead" would otherwise be indistinguishable.

**Build.** `sdkconfig.defaults` gained the esp32-camera web-server tuning
(`ESP_WIFI_STATIC_TX_BUFFER_NUM`, `LWIP_TCP_SND_BUF_DEFAULT`, `LWIP_MAX_SOCKETS`,
AMPDU) — with stock buffer counts an MJPEG stream stalls for hundreds of milliseconds
at a time. `sdkconfig` was deleted so it re-seeds from defaults. Note that TX buffers
are *static* here and that is forced, not chosen: ESP-IDF removes the dynamic option
whenever `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` is set, and the derived
`CONFIG_ESP_WIFI_TX_BUFFER_TYPE` is silently ignored if you set it directly — a trap
this project walked into once and now documents in `sdkconfig.defaults` itself.

**The firmware now builds.** `idf.py build` against ESP-IDF v5.5.5 completes with no
warnings from project code: 2.2 MB app, 65 % of the 6 MB partition free. This is the
first time anything in `firmware/` has been compiled — it still has never been run on
hardware.

**Tooling and diagrams.**
- `scripts/diagram_fonts.py` (new): the four DejaVu faces every diagram is drawn with
  are now looked up rather than hard-coded to `/usr/share/fonts/truetype/dejavu`, so
  the artwork can be regenerated on the same Windows machine the firmware is flashed
  from (`pip install matplotlib` supplies all four). Still DejaVu only, deliberately:
  every text width in these diagrams is measured to lay the drawing out, so swapping
  the metrics would move labels.
- `scripts/pinmap.py` no longer reads a display header; `FREED_BY_WEB_PREVIEW` records
  the five GPIOs that came back. All eleven reference figures, the three step diagrams
  and the one-page poster were regenerated. Figure 5 changed from "wiring the display,
  8 wires" to "joining the board's Wi-Fi, 0 wires" and now carries the HTTP surface,
  which is the nearest thing a headless build has to a connector pinout.
- `tests/test_tutorial_diagrams.py`: the display assertions are replaced by ones that
  matter now — that no artefact still configures the removed panel, that the page's
  hard-coded stream port matches `web_server.h`, that `WIFI_AP_PASSWORD` is a legal
  WPA2 key (8+ characters, or empty for a deliberately open network), and that the
  JPEG quality default is on `fmt2jpg`'s 1-100 scale rather than the sensor's inverted
  0-63 one. 92 tests pass.

**Documentation.** The tutorial, `docs/HARDWARE_SETUP.md`, `docs/FIRMWARE_PIPELINE.md`,
`docs/DEPLOYMENT.md`, `firmware/esp32s3/README.md`, `README.md`, `PROJECT_STATE.md`
and `ROADMAP.md` all follow. Two new acceptance tests were added: frame rate recorded
with and without a browser watching (the delta is the evidence the preview costs the
detector nothing it needs), and 20 stream open/close cycles with heap sampled after
each.

## 2026-08-13 — audio hardware wired, beginner setup tutorial, repo cleanup

**Hardware.** The remaining three parts of the order were identified and are now
first-class in the project: a **MAX98357A** I2S filterless class-D amplifier
(khmeres item 2724), a **4 ohm / 3 W** speaker (2554) and an **MB102** 830-point
breadboard (371). Together with the board (2991) and panel (1885) that is the whole
five-item bill of materials, $15.25.

**Firmware — the alert path is real.**
- New `main/board_audio.h/.cpp`: ESP-IDF v5 `i2s_std` bring-up on **BCLK 39,
  LRCLK 38, DIN 40** - the microSD pins, which are what the DVP camera and the octal
  PSRAM leave free. 16-bit stereo at 16 kHz, matching the recording format in
  `assets/audio/README.md`. Provides PCM playback, a ramped tone generator and a
  silence flush.
- Mono samples are written into **both** I2S slots on purpose. The MAX98357A's `SD`
  pin selects left / right / (L+R)/2 depending on what a given breakout pulls it to;
  duplicating the sample makes every non-shutdown variant sound identical, so the
  board revision stops mattering.
- `voice_alert.cpp`: playback moved onto its own FreeRTOS task behind a queue.
  Previously `voice_alert_trigger()` was called straight from the capture loop, whose
  frame budget is ~23 ms - inline playback would have dropped roughly twenty frames
  and frozen the preview at the exact moment the driver needed it.
- Each alert reason now plays a distinct tone pattern (microsleep highest and
  fastest), so the amplifier is testable on the bench before any speech is recorded.
  This replaces a `TODO(HW)` that only wrote a log line.
- Fixed the buzzer fallback: `buzzer_pulse()` set the GPIO high and immediately low
  with no delay in between, so the "fallback alert" was silent. It now holds for
  120 ms.
- `main.cpp` plays an 880 Hz chirp at boot. It costs 120 ms and it is the only way to
  distinguish an amplifier that is wired but silent from one that never initialized.

**Documentation — `docs/tutorials/hardware-setup/`.**
- A complete beginner tutorial: what is being built, the five parts, required
  software, component identification, power architecture, a 15-row wiring table,
  toolchain install, project configuration, first power-on, per-component tests, a
  full system test, and a troubleshooting section split by subsystem.
- **Eleven generated wiring diagrams**, produced by
  `scripts/generate_tutorial_diagrams.py` from the same constants the firmware uses.
  `tests/test_tutorial_diagrams.py` fails if a drawing and a firmware header ever
  disagree, so the images cannot silently drift.
- The diagrams deliberately do **not** draw the physical top-to-bottom order of the
  board's header pins: it varies between batches of this board and could not be
  verified against a datasheet. Every connection is keyed to the printed silkscreen
  label instead.
- Recorded that the item-1885 listing calls the panel an "OLED" when it is a TFT LCD
  (its own silkscreen reads `RGB_TFT`), which is why it needs the `BLK` backlight pin.

**Cleanup.**
- `eyestate.py`: `max(face_box[2], face_box[2])` took the maximum of one value with
  itself - a leftover from when the box was `(x, y, w, h)`. Simplified to the single
  side, which is what `FaceTracker` actually returns.
- `.vscode/settings.json` pointed `cmake.sourceDirectory` at an absolute
  `E:/Personal/Project/...` path, so it was wrong for every other machine. Now
  `${workspaceFolder}`-relative.
- `docs/HARDWARE_SETUP.md` had the same absolute path hardcoded in a build command.
- `requirements.txt` duplicated the dependency list in `pyproject.toml`; it now
  defers to it, so the two cannot drift.
- Root `README.md` still advertised "OV2640/OV5640 class camera" as the target; the
  actual hardware is an OV3660. Added the target display and audio parts, a link to
  the new tutorial, and a repository layout section.
- `docs/VOICE_ALERT_HARDWARE.md` still said "OV2640 camera for the initial prototype"
  and left the I2S GPIO numbers to be decided later. Both are now settled.
- Stored the task brief this work was done against in `docs/prompts/`.

**Not done.** The firmware still has never been compiled - no ESP-IDF toolchain has
existed in any environment this project has been worked in, so `idf.py build` remains
unrun and the I2S code is reviewed-but-unbuilt. See `PROJECT_STATE.md` gap 1.

## 2026-08-11 — real hardware selected, firmware wired to it
- Board bought: **ESP32-S3-WROOM-1 N16R8 CAM + OV3660** (16 MB flash, 8 MB octal PSRAM)
  and a 1.8" **128x160 ST7735S** SPI panel. The board's DVP pin map turned out to be
  byte-for-byte the ESP32-S3-EYE map, cross-checked against keyestudio's pin table for
  the same board and arduino-esp32's `camera_pins.h`, so the frame budget and the
  ESP-DL model choice in `docs/FIRMWARE_PIPELINE.md` carry over unchanged.
- `main/board_camera.h`: the verified pin map, a 240x240 RGB565 `camera_config_t`, and
  driver-facing sensor tuning (hmirror, AGC/AEC on, brightness lifted for a backlit
  windscreen). Also records which GPIOs an N16R8 module makes unusable - 33-37 are
  flash/PSRAM, and reaching for them is the classic way to hang this board.
- `main/board_display.h/.cpp`: ST7735S bring-up over SPI2 and a chunked blit that
  byte-swaps RGB565 on the way out, since the panel latches high byte first. Pins
  chosen from what the camera leaves free: SCK 14, MOSI 21, CS 47, DC 41, RST 42.
- `main/main.cpp`: capture loop enabled end to end. It now runs **preview-only** when
  `model_init()` fails instead of returning, so the camera, panel, PSRAM and power
  supply can be validated before ESP-DL exists. A missing camera is drawn on screen
  rather than only logged.
- `model_adapter` reshaped to what the pipeline actually needs - `model_detect_face()`
  and `model_eye_closed_prob()` - replacing the stale whole-face
  `model_predict_drowsy(gray64x64)` left over from the abandoned classifier design.
- `display_ui` adapts to panels under 200 px wide: shorter PERCLOS label, computed
  right-alignment instead of hardcoded offsets, single-size banner text, and a shorter
  preview so all seven status rows still fit in 160 px.
- Added `sdkconfig.defaults`, `partitions.csv` (6 MB app for FLASH_RODATA models) and
  `main/idf_component.yml`.
- New `docs/HARDWARE_SETUP.md`: wiring tables, Windows toolchain install, which of the
  two USB-C ports to use, the three-stage bring-up, and a troubleshooting table keyed
  to the symptoms these two parts actually produce.
- Still not compiled or flashed: no board exists in this environment.

## 2026-08-10 — on-device pipeline, driver screen and reason-specific alerts
- Ported the behaviour logic to firmware: `behavior.h/.cpp` mirrors
  `src/drowsyguard/behavior.py` (geometry, rolling baselines, event state machines,
  PERCLOS, fusion). `tests/test_firmware_parity.py` parses the header and fails if any
  constant, the fusion weights or the `RiskFilter` defaults drift from Python — verified
  by injecting a deliberate change and watching it fail.
- Documented the frame budget in `docs/FIRMWARE_PIPELINE.md` using Espressif's published
  ESP-DL latencies: detector amortised to ~13 ms by running every 3rd frame with
  tracking in between, ~6 ms for both eyes, ~4 ms UI => ~23 ms/frame, 15-20 fps on S3.
- **ESP32-S2 ruled out**: no AI vector instructions and ESP-DL's face detection models
  support only S3/P4. Recommended ESP32-S3-EYE, which already has camera + 240x240 LCD
  + mic + 8 MB PSRAM.
- Caught a portability trap: ESP-DL's keypoint order (left eye, left mouth, nose, right
  eye, right mouth) differs from YuNet's. Added `behavior_from_espdl_keypoints()` and a
  test asserting the reorder table, since indexing it directly would silently corrupt
  every geometric cue.
- Added `display_ui.h/.cpp`: driver-facing RGB565 UI (no LVGL) with camera preview,
  tracked face box, eye state, PERCLOS bar, risk bar with the trigger marked, yawn/nod
  counts, named events including `SNEEZE IGNORED`, and an alert banner.
- `voice_alert` now takes an `AlertReason` (Drowsy / Microsleep / Yawning / HeadNod),
  each mapping to its own clip and banner text, plus `voice_alert_is_active()` for the UI.
- None of the firmware is compiled or flashed; no board exists in this environment.

## 2026-08-10 — multi-cue behaviour analysis
- Added `src/drowsyguard/behavior.py`: risk is now a fusion of PERCLOS (0.55),
  long/slow blinks (0.20), yawning (0.15) and head nodding (0.10) rather than eye
  closure alone. Yawn, nod and roll are derived geometrically from the five YuNet
  landmarks, so they add no model weight and remain ESP32-affordable.
- Every geometric cue is measured against a rolling per-driver baseline, so face shape
  and camera angle cancel out instead of becoming signal.
- Sneeze detection added as a **false-alarm suppressor**, not a drowsiness cue: a
  sneeze shuts the eyes for ~1 s with a head jerk and would otherwise be scored as a
  microsleep. A detected sneeze suppresses the behaviour contribution to risk.
- Validated the geometry against real faces: measured roll tracks applied rotation
  monotonically, jaw drop rises as the jaw opens (1.067 -> 1.228), and the pitch proxy
  responds while staying roll-stable. `yaw` is computed but NOT validated.
- Event thresholds are literature-informed defaults, not tuned on labelled
  yawn/nod/sneeze video, which the project does not yet have. Logic is unit-tested
  against synthetic traces (blink vs microsleep, speech vs yawn, nod vs sustained head
  drop, sneeze vs microsleep, one-event-per-occurrence).
- Dashboard shows mouth/head state, per-minute blink/yawn/nod rates, sneeze count and
  a behaviour event log.

## 2026-08-10 — eye-state + PERCLOS replaces whole-face classification
- Reworked detection to measure eyelid closure instead of classifying whole faces:
  YuNet eye landmarks -> 32x32 eye crops -> eye-state model -> PERCLOS -> RiskFilter
  (`src/drowsyguard/eyestate.py`). `live --mode eye` is now the default and needs no
  drowsiness checkpoint; `--mode face --checkpoint` keeps the old path.
- Base model: `open-closed-eye-0001` (OpenVINO Model Zoo, Intel, Apache-2.0), 11.3k
  parameters / 46 KB / 0.0014 GFLOPs — plausible for ESP32-S3 INT8, and ~0.9 ms per
  frame for both eyes here (about 10x faster than the 64x64 face CNN).
- Three corrections to that model's published contract, established empirically:
  input must be `(pixel-127)/255` (raw 0-255 overflows it to NaN); its output is
  already softmaxed; and its card claims `[open, closed]` but index 0 tracks *closed*.
- **Measured limitation:** the model does not transfer to DDD's visible-light, ~45 px
  eye crops — AUC 0.62 versus its claimed 95.84% in-domain. Tested BGR/RGB/grayscale/
  hist-eq/CLAHE/inverted inputs and four patch scales; none recovered the signal. It
  is IR-trained, so it should suit a sharp live camera and the planned IR illumination
  far better. Fine-tuning on visible-light eye labels is the open task.
- PERCLOS is the risk signal fed to the RiskFilter, so blinks cannot alert while
  sustained closure can; verified by unit test and by an open/blink/closed/open clip.
- Fixed `export-onnx`, which failed on a plain install because torch>=2.9 defaults to
  the dynamo exporter and needs the optional `onnxscript`; now falls back.

## 2026-08-10 — removed trained models; selecting a pretrained base
- Deleted all trained drowsiness checkpoints (`models/*.pt`) and the stale results
  document. The from-scratch `TinyDrowsyNet` did not generalize across drivers, so it
  is not a useful starting point. Kept only the YuNet face detector, which is
  detection infrastructure rather than a drowsiness model; also dropped the downloaded
  Haar cascade, unusable because OpenCV 5 removed `CascadeClassifier`.
- Model selection is open; a pretrained base from Hugging Face is being evaluated.
  The durable finding is retained in `PROJECT_STATE.md`: judge any candidate by
  per-driver accuracy on held-out subjects, never by an average.
- Added `evaluate --per-subject [--split]` so per-driver accuracy is a first-class
  measurement rather than an ad-hoc script.
- `export-onnx` now writes a `.preprocess.json` sidecar recording input size and
  normalization, and the dashboard reads it. An `.onnx` carries weights but not
  preprocessing, so without this a model trained on standardized input is silently
  served raw input.

## 2026-08-10 — face detection and tracking; anti-bias training
- Added YuNet face detection with tracking to the live dashboard
  (`src/drowsyguard/facedetect.py`, `drowsyguard fetch-models`). The crop now follows
  the face automatically; previously the dashboard used a fixed centre crop and a
  manual zoom, which fed a face-in-a-room to a model trained on tight face crops.
  Verified 60/60 detection on known faces and 130/130 on frames of a test clip that
  contain a face. Note OpenCV 5 removed `CascadeClassifier` and bundles no cascades,
  so Haar is unavailable on this stack.
- Tracking holds the last box for 15 frames when detection drops, because detectors
  tend to lose the face precisely when the eyes close or the head nods.
- Measured the DDD framing rather than guessing it: the detected face box is ~1.02x
  the image side across 400 images, so `--face-margin` defaults to 0.
- Added `augment` and `normalize` training options targeting per-driver appearance
  bias; `normalize` is recorded in the checkpoint and reapplied automatically at
  inference, so a normalized model cannot be run on raw input.
- Overlay readouts moved outside the face box; added a face-status pill and a
  face-detection toggle to the dashboard.

## 2026-08-10 — DDD ingestion and video support
- `prepare` now accepts videos as well as images in each class directory and
  decodes them to frames (`--stride N` keeps every Nth frame). Frames of a clip
  always stay with their subject in one split.
- `prepare` gained `--link` (hardlink instead of copy) and now reports per-split
  subject and class counts instead of a bare dict.
- Added `drowsyguard import-ddd`. DDD ships as flat `Drowsy` / `Non Drowsy`
  folders, but subject identity survives in the filename: the alphabetic prefix is
  the subject and its case is the label, so `A0001.png` and `a0001.png` are the
  same person. The importer rebuilds the subject layout; without it a random split
  would put the same face and adjacent video frames in both train and test.
- Imported DDD: 28 subjects, 41,793 images. Subjects F and T are drowsy-only.
- Promoted `opencv-python` from the `live` extra to a core dependency, since
  video ingestion now needs it.
- `prepare` now refuses to write a new split over an existing one unless
  `--overwrite` is passed; the old files would otherwise survive and place a
  subject in two splits.
- Added `--zoom` to the live dashboard. DDD is tightly cropped faces, so feeding a
  full webcam frame to a DDD-trained model is out of distribution; zoom makes the
  live input match the training crop.
- Training progress now flushes, so redirected logs show epochs as they finish.
- Trained the first subject-independent DDD baseline (`configs/train_ddd.yaml`):
  val 0.8096, **test 0.5677 on 5 unseen drivers**. The model fitted the training
  drivers (train loss 0.004) but did not transfer; per-driver analysis showed it keyed
  on driver appearance. Checkpoint deleted in the entry above; kept here as the record
  of why from-scratch training on DDD was abandoned.
- Deleted the raw DDD corpus after import at the user's request; `data/raw` and
  `data/processed` retain all 41,793 images via hardlinks.

## 2026-08-10 — live development dashboard
- Fixed README workflow: it installed dependencies but never the package, so the
  `drowsyguard` command did not exist. Added `pip install -e .` and the Windows
  venv activation line.
- Added `drowsyguard live`: browser dashboard for real-time webcam testing
  (MJPEG feed, model-input view, p(drowsy) chart, streak/cooldown meters,
  alert log, and live trigger/required/cooldown tuning).
- Added `drowsyguard.risk.RiskFilter`, a Python mirror of the firmware filter, so
  thresholds tuned in the dashboard transfer to the device unchanged. Locked to
  the C++ semantics by `tests/test_risk.py`.
- Extracted `preprocess_gray` so training and live inference share one
  preprocessing path and cannot drift apart.
- Added `drowsyguard camera-test` and extended `doctor` with a live-UI check.
- Dashboard states plainly when no checkpoint is loaded; an untrained model's
  probabilities must not be read as detection.

## 2026-08-09 — v0.1.0 scaffold
- Defined MCU-first retrofit drowsiness research architecture.
- Added subject-independent dataset preparation.
- Added tiny grayscale CNN and training/evaluation CLI.
- Added ONNX export.
- Added guarded ESP-DL quantization adapter placeholder pending version pinning.
- Added ESP32-S3 firmware scaffold with temporal alert logic.
- Added roadmap, project state, thesis outline, and AI handoff protocol.
