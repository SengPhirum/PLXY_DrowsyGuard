---
title: Configuration
---

# Configuration

DrowsyGuard has five places where behaviour is configured. Nothing else in the
tree holds a tunable number.

| Layer | Lives in | Applied |
| --- | --- | --- |
| [Training](#training-configuration) | `configs/*.yaml` | at `train` / `evaluate` / `export-onnx` |
| [Firmware pins](#firmware-pins) | `firmware/esp32s3/main/board_*.h` | at compile time |
| [Firmware behaviour](#firmware-behaviour) | `main.cpp`, `risk_filter.h`, `face_gate.h`, `presence.h`, `behavior.h` | at compile time |
| [Build](#build-configuration) | `sdkconfig.defaults` | at `idf.py set-target` |
| [MQTT alerting](#mqtt-alerting) | the device's NVS partition | at runtime, from the web UI |

The last row is the only one that is **not** in the repository, and that is
deliberate: it holds a broker password. See
[Persisted settings](#persisted-settings).

!!! warning "Several of these are mirrored, and the mirrors are tested"
    `behavior.h`, `face_gate.h`, `presence.h` and `risk_filter.h` each have a Python
    counterpart in `src/drowsyguard/`, because the dashboard is where thresholds get
    tuned and a threshold tuned against different logic is worse than no tuning at
    all. `tests/test_firmware_parity.py` compares the constants;
    `tests/test_facegate_parity.py` and `tests/test_presence.py` go further and drive
    both implementations through identical sequences, requiring identical answers.
    Change one side only and `./plxy.sh test` fails.

Plus the [environment variables](#environment-variables) `plxy.sh` reads, and the
handful of [runtime settings](#runtime-settings) the device accepts over HTTP.

## Training configuration

`configs/train.yaml` is the default; `configs/train_ddd.yaml` is the DDD variant.

```yaml
seed: 42
image_size: 64
batch_size: 64
epochs: 20
learning_rate: 0.001
weight_decay: 0.0001
num_workers: 0
train_dir: data/processed/train
val_dir: data/processed/val
test_dir: data/processed/test
checkpoint: models/best.pt
onnx_output: models/drowsyguard.onnx
subject_split:
  train: 0.70
  val: 0.15
  test: 0.15
class_names: [alert, drowsy]
```

| Field | Notes |
| --- | --- |
| `image_size` | side of the square grayscale input to the whole-face model |
| `num_workers` | `0` is right for small sets; DDD uses `8` because PNG decoding dominates on CPU |
| `subject_split` | fractions of **subjects**, not of images — that is the whole point |
| `class_names` | order defines the label indices; `alert` is class 0 |

The DDD config differs only in `batch_size: 128`, `epochs: 15`, `num_workers: 8`
and the output paths (`models/ddd_best.pt`, `models/drowsyguard_ddd.onnx`).

## Firmware pins

Pin assignments live in exactly two headers, and nowhere else in the firmware.

### Camera — `main/board_camera.h`

| Signal | GPIO | | Signal | GPIO |
| --- | --- | --- | --- | --- |
| XCLK | 15 | | D7 (Y9) | 16 |
| SIOD (SCCB SDA) | 4 | | D6 (Y8) | 17 |
| SIOC (SCCB SCL) | 5 | | D5 (Y7) | 18 |
| VSYNC | 6 | | D4 (Y6) | 12 |
| HREF | 7 | | D3 (Y5) | 10 |
| PCLK | 13 | | D2 (Y4) | 8 |
| PWDN | not routed | | D1 (Y3) | 9 |
| RESET | not routed (reset via SCCB) | | D0 (Y2) | 11 |

Frame geometry and orientation live in the same header:

| Define | Default | Meaning |
| --- | --- | --- |
| `CAM_FRAME_W` / `CAM_FRAME_H` | `240` × `240` | capture size |
| `CAM_RGB565_BYTE_SWAP` | `1` | the sensor's byte order vs. the DL stack's |
| `CAM_ROTATE_180` | `1` | how the module is mounted |
| `CAM_SELFIE_MIRROR` | `1` | mirror the preview only |

### Audio — `main/board_audio.h`

| Amplifier pin | GPIO |
| --- | --- |
| BCLK (bit clock) | 41 |
| LRC (word select) | 42 |
| DIN (data, S3 → amp) | 2 |

| Define | Default |
| --- | --- |
| `AUDIO_SAMPLE_RATE_HZ` | `16000` |
| `AUDIO_TONE_AMPLITUDE` | `0.35` |

Wiring these is [step 6 of the hardware tutorial](../tutorials/hardware-setup/README.md#6-wiring).

## Firmware behaviour

### Risk and timing — `main/main.cpp`

```cpp
static constexpr float PERCLOS_WINDOW_S = 3.0f;
static constexpr float RISK_REQUIRED_S  = 0.55f;
static constexpr float RISK_COOLDOWN_S  = 4.0f;
static constexpr float RISK_TRIGGER     = 0.55f;
```

These are **durations in seconds**, deliberately. `RiskFilter` counts frames, and
frame counts silently couple the alarm's sensitivity to the frame rate: 8 frames
is half a second at 15 fps and a third of a second at 25, so making the capture
loop faster made the alarm twitchier without anyone editing a threshold.
`retune_for_fps()` converts these durations to frame counts once a second from
the measured rate, so the intended half second stays half a second.

`RISK_TRIGGER` is a level of the **fused behaviour score**, not a raw model
probability. Tune it in the [live dashboard](../guide/live-dashboard.md) and
paste it here.

### The filter itself — `main/risk_filter.h`

```cpp
RiskFilter(float trigger=0.72f, int required=8, int cooldown=60)
```

!!! warning "Load-bearing text"
    `tests/test_firmware_parity.py` **parses this constructor signature** and
    compares it with `src/drowsyguard/risk.py`. The defaults above are not just
    defaults — changing one without the other fails the test suite. Run
    `./plxy.sh test` after any edit.

`MODEL_SELFTEST` in `main.cpp` (default `0`) runs the detector over the fixed
frames in `test_frames.h` at boot, which separates "the ESP-DL binding is wrong"
from "nobody was in front of the camera". Leave it off: `/api/snapshot` returns
the actual frame the detector was handed, which is strictly better.

### The detection gate — `main/face_gate.h`

What counts as a face, and when a sequence of detections counts as a driver. Every
constant is mirrored in `src/drowsyguard/facegate.py`, and
`tests/test_facegate_parity.py` drives both implementations through the same
sequences and requires the same answers — so these cannot be changed on one side
alone.

| Constant | Default | What it rejects |
| --- | --- | --- |
| `FACE_MIN_SCORE` | `0.55` | Weak candidates. The coarse detector stage runs at 0.10 on this camera (it has to — see `model_adapter.cpp`), so genuinely weak boxes arrive; a real face on this board scores 1.00. This is also what rejects most low-light false positives, because low light collapses confidence rather than distorting geometry. |
| `FACE_MIN_SIDE_FRAC` / `FACE_MAX_SIDE_FRAC` | `0.10` / `0.95` | Faces too small for the eye crop to contain an eye, and things up against the lens. At 240 px the floor is a 24 px box, whose eye patch is already at `eyestate.py`'s 8 px minimum. |
| `FACE_ASPECT_MIN` / `FACE_ASPECT_MAX` | `0.55` / `1.80` | Boxes that are not head-shaped. This is what catches a raised hand even when its keypoints happen to land plausibly. |
| `FACE_YAW_MAX` | `0.75` | A nose outside the eye pair horizontally. No head pose does this; a landmark set fitted to a hand does it readily. |
| `FACE_MOUTH_MIN` / `FACE_MOUTH_MAX` | `0.25` / `1.60` | A collapsed mouth (a headrest — nothing in the image separates the corners) and a mouth wider than the head (a bright rectangle). |
| `FACE_EYE_DIST_*`, `FACE_MAX_ROLL_DEG`, `FACE_JAW_*`, `FACE_NOSE_FRAC_*` | see header | The original checks: interocular distance, tilt, and the mouth-below-eyes / nose-between ordering. |

Temporal, and these are the ones that decide *presence*:

| Constant | Default | Effect |
| --- | --- | --- |
| `FACE_CONFIRM_DETECTIONS` | `2` | Consecutive accepted detections before a driver counts as present. One frame is not evidence: the static checks are geometric and a single frame of landmark noise on a non-face can satisfy all of them. At `DETECT_EVERY = 3` and 15 fps this costs about 0.4 s at acquisition. |
| `FACE_HOLD_DETECTIONS` | `5` | Detection attempts a confirmed track survives with nothing accepted — about a second. This is what covers the moment the eyes close, which is when detectors drop faces and is exactly the moment of interest. |
| `FACE_JUMP_MAX_FRAC` | `1.20` | Centre movement between detections, as a fraction of box size. Refuses a candidate that has teleported: that is a different object, and accepting it is how a track steps off the driver onto a passenger. |
| `FACE_SCALE_MAX_RATIO` | `1.80` | Same idea for size. A face does not double in 200 ms. |
| `FACE_REACQUIRE_AFTER` | `2` | Misses after which a *discontinuous* candidate is allowed in — the driver may genuinely have moved. It starts a new, pending track: presence is re-earned, never inherited. |

`FACE_GATE_ENFORCE` (default `1`) makes the checks advisory when set to `0`: they
still run and still log, they just stop excluding. That is the first thing to try if
detection ever dies after a change here — it happened once, and the cause was a
mirrored frame rather than a bad limit. The escape hatch is built and tested, not a
branch nobody has run.

### The no-driver alert — `main/presence.h` { #the-no-driver-alert }

| Constant | Default | Meaning |
| --- | --- | --- |
| `PRESENCE_ALERT_S` | `3.0` | Continuous absence, **after** the tracking hold has already expired, before the alert fires. The wall-clock delay from a driver leaving is therefore the hold (~1 s) plus this. |
| `PRESENCE_CLEAR_S` | `0.5` | Continuous presence before the alert re-arms. Much shorter than the above, and it only has to outlast a single flickering detection — without it, one spurious detection every couple of seconds resets the countdown forever and the alert never fires, silently. |
| `PRESENCE_REPEAT_S` | `0.0` | Seconds between repeat announcements while the seat stays empty. Zero means exactly one per absence episode, which is the documented behaviour. Set it non-zero only where a continuously unattended camera is itself a fault condition. |
| `PRESENCE_WARMUP_S` | `5.0` | Healthy seconds required before any absence is believed. Covers boot, where the camera, the models and the auto-exposure are all still settling, and covers recovery from a fault for the same reason. |

The monitor takes a **health** argument as well as a presence boolean, and that is
the point of the module: a camera that has stopped and a cabin with nobody in it are
the same observation and opposite conclusions. In `camera-fault` or `model-fault` the
absence episode is discarded rather than frozen, so a countdown that started before
the fault cannot be resumed against a cabin that may now hold something else.

On the desktop these are runtime-settable — `no_driver_after` and `no_driver_alert`
on the dashboard's `POST /config`, which is how you turn the alert off for a bench
session where an empty seat is the normal state.

### Sneeze detection — `main/behavior.h`

| Constant | Default | Meaning |
| --- | --- | --- |
| `SNEEZE_JAW_DELTA` | `0.13` | How far the opening index must exceed this driver's baseline. |
| `SNEEZE_MOUTH_LEAD_S` | `0.50` | How long the mouth may **already** have been open when the eyes closed. This is what separates a sneeze from a yawn that also shuts the eyes — in a yawn the mouth has been wide for a second by then. Getting it wrong is worse than missing a sneeze, because the suppression window would silence a genuine drowsiness cue for `SNEEZE_MAX_S`. |
| `SNEEZE_MAX_S` | `1.20` | Longest closure that can still be a sneeze. Past this it is a microsleep whatever the mouth is doing. |
| `SNEEZE_ALERT_COOLDOWN_S` | `2.50` | Minimum spacing between sneeze *announcements*. Detection stays per-closure, so a fit of three sneezes a second apart is counted three times and announced once. |

### Wi-Fi — `main/board_wifi.h` { #wi-fi }

| Define | Default | Meaning |
| --- | --- | --- |
| `WIFI_AP_SSID_PREFIX` | `"DrowsyGuard"` | the MAC suffix is appended |
| `WIFI_AP_PASSWORD` | `"drowsyguard"` | **change this before any road test** |
| `WIFI_AP_CHANNEL` | `6` | |
| `WIFI_AP_MAX_CLIENTS` | `4` | |
| `WIFI_STA_SSID` | `""` | set to join an existing network instead |
| `WIFI_STA_PASSWORD` | `""` | |

`./plxy.sh wifi` reads these straight out of the header, so it always prints what
the firmware will actually do. See [Security](../security.md#the-access-point-is-open-by-default).

`WIFI_STA_SSID` is now a **default rather than the only route**: station credentials
stored in NVS take precedence over it, and the settings modal writes them. That is
what MQTT needed — the board's own access point has no route to the internet, so a
device that could only be its own AP could only ever publish to something on the same
island, and a demonstration has to be able to point it at a phone hotspot in the room
without a rebuild.

The access point comes up either way and never comes down. A wrong hotspot password
costs you the broker, never the dashboard and never the alarm.

## MQTT alerting { #mqtt-alerting }

Every field here is set from the device's web UI and stored in its NVS partition. None
of it is compiled in, and none of it is in this repository — one of the values is a
broker password.

Full walkthrough: [Fleet alerting over MQTT](../guide/mqtt.md). API:
[`POST /api/mqtt`](../reference/device-api.md#post-apimqtt).

### Connection

| Setting | Default | Range / notes |
| --- | --- | --- |
| enabled | **off** | Off by default and deliberately: switching it on sends a named driver's alertness state to a third party, which has to be an act rather than an inherited setting |
| transport | `tls` | `tcp`, `tls`, `ws`, `wss`. Changing it moves the port, but only when the port was still the default for the old transport |
| protocol | `3.1.1` | or `5`. Both are compiled in (`CONFIG_MQTT_PROTOCOL_5`) |
| host | `broker.emqx.io` | hostname or IPv4, ≤ 95 chars. IPv6 is not supported and says so rather than failing at connect time |
| port | `8883` | 1–65535. EMQX: 1883 TCP, 8883 TLS, 8083 WS, 8084 WSS |
| WebSocket path | `/mqtt` | required for `ws`/`wss`, must start with `/` |
| client ID | derived | `drowsyguard-{device_id}` when blank. Two devices sharing one knock each other off the broker in turn, and both look intermittently broken rather than misconfigured |
| username / password | empty | write-only through the API; an empty field on save keeps the stored value |
| QoS | `1` | `0` or `1`. **2 is refused**: the alert path is at-least-once with event-ID de-duplication at both ends, which is what QoS 2 would be paying for |
| keepalive | `30` s | 5–300 |
| Last Will | on | the retained online/offline document. The only way a dashboard can tell "no alerts because the driver is fine" from "no alerts because the device is in a tunnel" |
| skip TLS verification | off | encrypted but unauthenticated. Reachable on purpose, labelled in red everywhere — see [Security](../security.md#mqtt-alerting-leaves-the-vehicle) |
| CA certificate | none | one PEM root, ≤ 4 kB. With none, TLS verifies against the Mozilla root bundle in flash |

### Identity

| Setting | Default | Notes |
| --- | --- | --- |
| device ID | a slug of the SoftAP name | e.g. `drowsyguard-c5e019`. Already MAC-derived, so unique per board with no provisioning step |
| fleet ID | `demo-fleet` | the level a dashboard subscribes to with one wildcard |
| remark | `Driver A` | printable ASCII, ≤ 47 chars. The only field that says who is being monitored, and **not** part of any topic |

`device_id` and `fleet_id` are constrained to lowercase letters, digits, `-`, `_` and
`.`, starting and ending alphanumeric. That is not pedantry: they become topic
segments, and a `/`, `+` or `#` in either one silently restructures the tree. A topic
is the one field where a wrong value does not fail — it goes somewhere else.

### Topics

| Setting | Default |
| --- | --- |
| topic mode | `auto` |
| topic (manual mode) | empty |

Auto mode builds `plxy/drowsyguard/{fleet-id}/{device-id}/alerts` and `/status`, plus
the `+`-wildcard forms a dashboard subscribes to. Manual mode takes a topic verbatim,
refusing `+` and `#` (subscription syntax), a leading `$` (broker-reserved), a leading
or trailing `/`, and empty levels. It also refuses a topic long enough that the
derived `/status` topic would not fit — the Last Will uses it, and a silently
truncated will topic means a retained message on a topic nobody subscribes to.

### Wi-Fi station

| Setting | Default | Notes |
| --- | --- | --- |
| enabled | off | falls back to `WIFI_STA_SSID` |
| SSID | empty | ≤ 32 chars |
| password | empty | empty (open network) or 8–63 chars. 1–7 is always a typo, and the radio would otherwise reject it with an error that reads like a bad SSID |

### Not settable, on purpose

| Constant | Value | Where |
| --- | --- | --- |
| `MQTT_OUTBOX_DEPTH` | 16 | `mqtt_config.h`. About eight minutes of drowsiness alerts at the 30 s channel cooldown, and 1.6 kB. Past it the **oldest** is discarded: after twenty minutes offline what an operator needs is the last few minutes of a deteriorating driver, not the first |
| `MQTT_DEDUP_SLOTS` | 24 | `mqtt_config.h`. Full strings rather than hashes — under a kilobyte, and a hash collision would silently drop a real alert |
| `MQTT_BACKOFF_BASE_MS` / `MQTT_BACKOFF_MAX_MS` | 1000 / 60000 | `mqtt_config.h`. Doubling, capped, with up to 25 % additive jitter. esp-mqtt's own auto-reconnect is switched off in favour of it |
| `MQTT_TOPIC_ROOT` | `plxy/drowsyguard` | `mqtt_config.h`. Two fixed levels so a shared broker cannot collide with us by accident |
| `MQTT_PAYLOAD_MAX` | 768 | `mqtt_config.h`. A document that will not fit produces nothing rather than half of one |

These are compile-time because they are safety and memory bounds rather than
preferences, and because a bound that can drift at runtime cannot be reported in a
thesis. Every one of them is exercised by `tests/test_mqtt_config.py`.

## Persisted settings { #persisted-settings }

Three records and a certificate, in the NVS namespace `dgsettings` — kept separate
from `voice_alert.cpp`'s `drowsyguard` namespace so that erasing one subsystem to
recover it cannot silently reset the others.

| Key | Holds |
| --- | --- |
| `device` | device ID, fleet ID, remark |
| `wifi` | station enabled, SSID, passphrase |
| `mqtt` | everything in [Connection](#mqtt-alerting) above except the certificate |
| `mqtt_ca` | the CA certificate, up to 4 kB |

Each record is a **versioned byte stream with a magic and a CRC**, not a struct
`memcpy`. A raw struct in flash is a promise never to reorder a field, never to change
a capacity and never to compile with different padding, and the first time one of
those is broken the device reads a plausible-looking hostname out of the middle of a
password. A record that fails any of the three checks — or that no longer passes
validation — is discarded and the defaults are used, which is also what a fresh board
does. `tests/test_mqtt_config.py` flips **every byte** of each record and requires
every one to be rejected.

NVS is not encrypted on this build: anybody holding the board holds the passwords.
`./plxy.sh erase` clears them.

## Build configuration

`firmware/esp32s3/sdkconfig.defaults` seeds `sdkconfig` at
`idf.py set-target esp32s3`. Delete `sdkconfig` — not this file — to start over,
or run `./plxy.sh fullclean`.

The settings that are not optional:

| Setting | Why |
| --- | --- |
| `CONFIG_SPIRAM=y`, `CONFIG_SPIRAM_MODE_OCT=y` | camera framebuffers (`CAMERA_FB_IN_PSRAM`) and the ESP-DL models both live in PSRAM. Get `MODE_OCT` wrong and the board boots but finds 0 bytes of PSRAM, which surfaces later as an unexplained `esp_camera_init()` failure |
| `CONFIG_ESPTOOLPY_FLASHSIZE_16MB` | the N16R8 module |
| `CONFIG_PARTITION_TABLE_CUSTOM` | `partitions.csv` |
| `CONFIG_OV5640_SUPPORT` + `CONFIG_OV3660_SUPPORT` | the board is sold with an OV3660 but ships an OV5640; both are pinned so a driver change cannot look like a dead camera |
| `CONFIG_MQTT_TRANSPORT_SSL`, `CONFIG_MQTT_TRANSPORT_WEBSOCKET`, `CONFIG_MQTT_TRANSPORT_WEBSOCKET_SECURE` | three of the four transports are behind Kconfig options that default to off, and a transport that is compiled out fails at connect time with a generic transport error rather than "not built" |
| `CONFIG_MQTT_PROTOCOL_5` | MQTT 5 is offered in the settings modal, so it has to be in the binary |
| `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE` | the Mozilla root bundle in flash. This is what makes the preconfigured public broker reachable over TLS **with** verification and nothing pasted in — the alternative is shipping a default that only works with verification off |

The `ov3660: Mismatch PID=0x5640` line on the way to a working camera is
expected, not a fault.

## Environment variables

Read by `plxy.sh`:

| Variable | Default | Effect |
| --- | --- | --- |
| `PLXY_PORT` | auto-detected | force a serial port, e.g. `COM9` |
| `PLXY_HOST` | `192.168.4.1` | talk to the board over station mode |
| `PLXY_DOCS_PYTHON` | discovered | interpreter used for the docs commands |
| `PLXY_DOCS_VENV` | `.venv-docs` | where the docs toolchain is installed |
| `PLXY_DOCS_PORT` | `8001` | `docs-preview` port — not 8000, which the live dashboard uses |
| `PLXY_DOCS_NO_VENV` | unset | set to `1` to use the ambient interpreter and never create a venv (this is what CI does) |
| `PLXY_DOCS_SITE_URL` | the published Pages URL | base URL the site is built for; `docs-preview` sets it to the local address so the preview serves at `/` |

## Runtime settings

The only things settable on a running device, over HTTP:

| Setting | Range | Endpoint |
| --- | --- | --- |
| JPEG quality | 10–95, higher is better | `POST /api/settings?quality=N` |
| Stream rate | 1–20 fps | `POST /api/settings?fps=N` |
| Mute | `0` / `1` | `POST /api/settings?muted=N` |
| Alert language | `en` or `km` | `POST /api/settings?lang=xx` |
| Everything in [MQTT alerting](#mqtt-alerting) | see the tables above | `POST /api/mqtt` (form-encoded body) |
| One test publish | — | `POST /api/mqtt/test` |

Risk thresholds are **not** runtime-settable — they are compiled in, by design:
a threshold that can drift at runtime cannot be reported in a thesis. Full
parameter detail in the [Device HTTP API](../reference/device-api.md#post-apisettings).

The desktop dashboard, which is a development tool rather than a device, is more
permissive: `POST /config` accepts every threshold plus `no_driver_after` and
`no_driver_alert`. See the
[Dashboard HTTP API](../reference/dashboard-api.md#post-config).
