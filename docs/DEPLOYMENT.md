# ESP32-S3 Deployment Notes

Espressif currently recommends ESP32-S3/ESP32-P4-class boards for ESP-DL and supports camera sensors through `esp32-camera`. ESP-DL deploys quantized `.espdl` models produced with ESP-PPQ.

## Version policy
Intended versions, chosen 2026-08-11 when the hardware was bought. Replace each with
the version actually resolved by `idf.py reconfigure` (see
`firmware/esp32s3/dependencies.lock`) once the first build succeeds - the resolved
numbers are what the thesis reports.

- ESP-IDF: v5.4.x intended (>=5.3 required by esp-dl 3.x) — **confirm after first build**
- esp32-camera: ^2.1.7 — **confirm** (also supplies the JPEG encoder the web
  preview uses; no separate dependency)
- ESP-DL: ^3.1.2 — **confirm at stage 3**
- ESP-PPQ: TBD (pin before running `scripts/quantize_espdl.py`)
- Board: ESP32-S3-WROOM-1 **N16R8** CAM dev board (16 MB flash, 8 MB octal PSRAM)
- Camera sensor: **OV3660**, DVP, pin map identical to ESP32-S3-EYE
- Display: **none.** The preview is served over the board's own Wi-Fi access
  point (SoftAP, `esp_http_server`, MJPEG on port 81). Both ship with ESP-IDF,
  so there is no version to pin here — record the ESP-IDF version instead.

Do not leave these floating for thesis experiments. Setup and flashing procedure:
`docs/HARDWARE_SETUP.md`.

## Firmware pipeline
Camera (240x240 RGB565) -> ESP-DL face detect + 5 landmarks, every 3rd frame ->
**plausibility gate and track confirmation** -> one 32x32 eye crop per frame,
alternating -> eye-closed probability -> PERCLOS + long blinks + yawn + nod fused
into a risk score -> temporal risk filter -> reason-specific voice
alert (buzzer fallback), with the frame and every intermediate number served to a
browser over Wi-Fi.

One decision runs alongside that chain rather than inside it, because it is not a
drowsiness measurement:

- **Presence**: `PresenceMonitor` takes the track's verdict *and* a `PipelineHealth`,
  and announces "no driver detected" once per absence episode - never when the camera
  or the models are down, because that would be a claim about the cabin drawn from a
  fact about the firmware.

This replaced the earlier whole-face 64x64 grayscale classifier, which learned driver
identity rather than eyelid state; see `PROJECT_STATE.md`.

## MQTT alerting

Publishing is a runtime setting in NVS, not part of the image, so a flashed board
publishes nothing until somebody configures it. What the *build* has to carry is the
four transports and the root bundle, and all four are `sdkconfig.defaults` entries
rather than menuconfig choices someone has to remember:

| Setting | Why it is not optional |
| --- | --- |
| `CONFIG_MQTT_TRANSPORT_SSL` | TLS, the default transport |
| `CONFIG_MQTT_TRANSPORT_WEBSOCKET` / `_SECURE` | a browser cannot open a raw MQTT socket, so the documentation's fleet monitor needs WSS |
| `CONFIG_MQTT_PROTOCOL_5` | MQTT 5 is offered in the settings modal |
| `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE` | verifies the public broker with nothing pasted in. Without it the shipped default would only work with verification switched off |

A transport that is compiled out does not fail at build time — it fails at connect
time, with a generic transport error rather than "not built". Check them after any
`fullclean`.

### Broker checklist before a demonstration

1. Set the **fleet ID**, **device ID** and **remark** before anything else: the topics
   are built from the first two, and the third is what a dashboard shows.
2. Point it at **your own broker** if the remark is a real person's name. The
   preconfigured `broker.emqx.io` is public — see
   [Security](security.md#mqtt-alerting-leaves-the-vehicle).
3. Give the board **station credentials**. Its own access point has no route to a
   broker on the internet.
4. Press **Test publish** and confirm the message arrives at a subscriber. Do this
   before the demonstration, not during it.
5. Note `mqtt.published` and `mqtt.acked` from `/api/status` at the start, so the
   delta afterwards is the evidence rather than an impression.

### Acceptance tests for the MQTT path

Each of these has a pass criterion that does not depend on anybody's judgement:

| # | Test | Pass |
| --- | --- | --- |
| M1 | Configure the broker, save, poll `/api/status` | `mqtt.state` reaches `online` within 15 s; `mqtt.error` is empty |
| M2 | `POST /api/mqtt/test`, watch a subscriber | one `drowsyguard.alert.v1` document with `alert: "test"` arrives; `published` and `acked` both increase by one |
| M3 | Kill the broker (or pull the uplink), fire three alerts | `mqtt.queued` reaches 3, `dropped` stays 0, and **`fps` in the log line does not change** — that last part is the isolation claim |
| M4 | Restore the broker | the three buffered alerts arrive; the retained `status` document flips to `online` with `reason: "connected"` |
| M5 | Power-cycle the board | it reconnects with no reconfiguration: the settings survived NVS. `boot_id` in the event ids changes, so nothing is de-duplicated against the previous boot |
| M6 | Pull power without a clean shutdown | the broker publishes the will: `online: false`, `reason: "last-will"` |
| M7 | Reconfigure while connected | the client is torn down and rebuilt on the publisher task; no alert is lost from the outbox |
| M8 | `GET /api/mqtt` and search the response | no password anywhere in it, and the username is masked |
| M9 | Point TLS at a host that drops SYNs (a LAN address with no broker) and watch the log for ten minutes | reconnect attempts follow the 1 s → 60 s jittered backoff; **`fps` holds through every attempt** — the handshake runs on core 1 (`CONFIG_MQTT_USE_CORE_1`), so a retrying TLS client must cost the detector nothing |
| M10 | With the broker dead, fire more than 16 alerts (`/api/mqtt/test` in a loop) | `depth` caps at 16, `dropped` counts every eviction, the board neither reboots nor slows; restore the broker and the **newest** 16 arrive |
| M11 | Fire `/api/mqtt/test` repeatedly *while* the client is mid-connect (during the TLS handshake window) | the connection still completes: queued alerts must not abort a handshake in progress, and all of them flush once `online` |
| M12 | Buffered alerts + a reconnect | the flush starts within a second of `mqtt.state` reaching `online` (the CONNACK wakes the publisher; there is no fixed 8 s wait), and no event id arrives twice at the subscriber |
| M13 | Soak: broker live, viewer streaming, 8+ hours | the `heap`/`psram` figures in the once-a-second log line are flat after the first minutes; `connects` is not climbing while the network is stable |

M3 is the one to run first and the one to record. It is the whole safety argument:
`fps`, `ms_detect` and `ms_eye` must be the same with a dead broker as with a live one.
M9 is its CPU-side twin: the same three numbers must hold *during* connection attempts,
not just between them, because the TLS handshake is a second or more of arithmetic and
only core placement keeps it off the capture loop.

### Acceptance tests for Wi-Fi provisioning

| # | Test | Pass |
| --- | --- | --- |
| W1 | First boot on an erased NVS | the access point comes up, the Wi-Fi card reads `ap only`, and `GET /api/wifi` reports `sta.stored: false`. Detection and alerts run |
| W2 | Scan from the page | `POST /api/wifi/scan` returns immediately; results appear within ~3 s, strongest first, no duplicate SSIDs, no unnamed rows. **`fps` in the log line does not change** |
| W3 | Save a correct password | `sta.state` reaches `connected` within 15 s and `sta.ip` is a real address. The access point never dropped: the page stayed open throughout |
| W4 | Save a wrong password | `sta.state` is `failed`, `auth_failed` is `true`, `reason_text` names the passphrase, and `retry_ms` counts down. **`192.168.4.1` is still serving this page** — that is the requirement, not a side effect |
| W5 | Power-cycle after W3 | it rejoins with no interaction: `sta.state` is `connected` and `sta.ssid` is what was saved |
| W6 | Take the network away while joined | `sta.state` goes to `failed`, attempts climb, and the backoff doubles to a 60 s ceiling. Bring it back: it rejoins without a reboot |
| W7 | **Forget network** on the page | `sta.stored` and `password_set` both go `false`; after a power cycle it is still forgotten. `GET /api/mqtt` is unchanged — same host, same topics, same `password_set` |
| W8 | Hold BOOT for five seconds | the log warns at 2 s and erases at 5 s; the device does **not** reboot, the stream keeps running, and `/api/mqtt` is again unchanged |
| W9 | Tap BOOT briefly, and hold it during the first three seconds after reset | nothing is erased in either case (`BOOT released - nothing was changed`, or silence) |
| W10 | Attach a serial monitor and hold BOOT | `button.armed` is `false` and nothing is erased — the inverted auto-reset lines hold GPIO0 low, and the watcher refuses a press it never saw begin |
| W11 | `GET /api/wifi` and search the response | no passphrase anywhere in it |

W4 and W8 are the two to record. W4 is the recovery guarantee — a wrong password must
never cost the dashboard — and W8 is the scope guarantee: the physical reset clears
the network and nothing else.

## Hardware acceptance tests
- Camera initializes 100 consecutive boots.
- No heap exhaustion after 1 hour.
- Inference latency recorded over >=1000 frames.
- Peak RAM and flash usage recorded.
- Alert output verified physically (`POST /api/alert-test` makes this a one-liner
  rather than a firmware edit).
- Quantized predictions compared with Python on a shared test-image set.
- **Frame rate recorded twice: with a browser watching the preview and with
  none.** The delta is the JPEG encode, and it is the evidence that the
  preview costs the detector nothing it needs. `GET /api/status` reports `fps`
  and `stream.viewers`, so this test scripts cleanly.
- Preview opened and closed 20 times with heap sampled after each, to rule out
  a leak in the stream path.
- **The gate rejects nothing on a real face.** `gate would drop 0 ok` in the
  once-a-second log line, over a run with someone in front of the camera. This is a
  regression test, not a feature test: an earlier version of the gate rejected 100% of
  real candidates on hardware and reported it as a bare count.
- **The no-driver alert fires once per absence, and re-arms.** Leave the frame empty
  and confirm exactly one `ALERT (NO DRIVER DETECTED)`; then sit down, leave again, and
  confirm a second. `/api/status` reports `presence.alerts`.
- **A camera fault is not reported as an absence.** Unseat the ribbon while running:
  `presence.health` must read `camera-fault`, `presence.state` must read `fault`, and
  no announcement may follow.
- **Every alert clip is audible in both languages.** `./plxy.sh alert <reason>` for all
  six reasons, in `en` and `km`; the response's `source` field must read `embedded` or
  `card`, never `tone`.

### Measured, 2026-09-01 (IDF v5.5.5, N16R8 + OV5640)

| | value |
| --- | --- |
| App binary | 3,404,992 B, 46% of the 6 MB partition free |
| Flash `.text` / `.rodata` | 1,802,632 B / 1,466,884 B |
| DIRAM used | 218,403 B (63.91%), 123,357 B free |
| Internal heap free, running | 17,475-21,807 B (lower with a browser attached) |
| PSRAM free, running | 7.01-7.03 MB |
| Frame rate, nobody in frame | 19.7 fps |
| Frame rate, tracking + one viewer | 11.5-17.2 fps |
| Face detection incl. gate, 0-1 candidates | 39.2-39.6 ms every 3rd frame |
| Face detection incl. gate, busy frame | up to 70.3 ms |
| Eye model | 17.8-18.5 ms per eye, one eye per frame |
| Detection hit rate with someone in frame | 20/20 at score 1.00 |
| Gate rejections on real candidates | 0 over every logged interval |

Against the same tree before the gate and the presence monitor: **+276,128 B of
flash** (259 kB of that is the new voice clips;
the logic is 5.4 kB of code) and **+1,120 B of internal RAM** (1,024 B of that is the
status JSON buffer, grown to fit the new `presence` and `alert.counts` objects).
Nothing new is allocated from PSRAM, and there is no measurable change in detection
latency at equal candidate counts.

### Measured, 2026-09-05 (first MQTT hardware run, after the reliability fixes)

| | value |
| --- | --- |
| App binary | 3,574,928 B, 43% of the 6 MB partition free |
| DIRAM used | 235,575 B (68.9%), 106,185 B free |
| MQTT | `online` against broker.emqx.io:1883, TCP, QoS 1, MQTT 3.1.1 |
| Published / acked | 50 / 51 over 2+ hours, `dropped` 0, no reboot |
| `GET /api/mqtt` | HTTP 200 in 0.17 s (crashed the board before the 8 kB stack fix) |
| Frame rate during the run | 19.7 fps idle, matching the 2026-09-01 baseline |
| Host tests | 588 passed; the 5 known-red Windows UTF-8 SSID cases unchanged |

M1 and M2 have effectively passed over TCP. Still to record: the TLS repeat of the
same, and **M3/M9** — `fps`, `ms_detect`, `ms_eye` with the broker dead and during
TLS reconnect attempts, which are the safety argument for the whole feature.
