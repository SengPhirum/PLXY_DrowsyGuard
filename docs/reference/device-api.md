---
title: Device HTTP API
---

# Device HTTP API

The board runs **two** HTTP servers. Keeping them apart means a viewer holding
the MJPEG stream open cannot block the 5 Hz status polling the page depends on.

| Port | Server | Endpoints |
| --- | --- | --- |
| **80** | control | the page, and everything under `/api/` |
| **81** | stream | `/stream`, `/frame` |

Base URL on the board's own access point: `http://192.168.4.1`. In station mode,
use the address in `net.sta_ip`, or set `PLXY_HOST`.

## Control server (port 80)

### `GET /`

The single-page UI (`main/web/index.html`), compiled into the firmware.

### `GET /api/status`

The whole device state, polled by the page at 5 Hz. Hand-rolled with one
`snprintf` into a static 5376-byte buffer rather than a cJSON tree: the control
server has a 6 kB task stack, only one task ever serves port 80, and truncation
returns **500** rather than letting the page parse half an object.

| Object | Fields |
| --- | --- |
| top level | `uptime_ms`, `frames`, `fps`, `camera`, `models`, `eye_model`, `driver` |
| `ms` | `detect`, `eye` — per-stage milliseconds |
| `frame` | `w`, `h` |
| `face` | `found`, `held`, `x`, `y`, `w`, `h`, `score`, `roi`, `roi_w`, `rejected`, `reject` |
| `presence` | `state`, `health`, `absent_s`, `alert_after_s`, `alerts` |
| `lm` | `valid`, `x[5]`, `y[5]` — the five landmarks |
| `risk` | `score`, `trigger`, `streak`, `required` |
| `eyes` | `closed`, `smooth`, `shut`, `perclos`, `closure_s` |
| `cues` | `mouth_open`, `head_down`, `baselines_ready`, `stale`, `events`, `open_index`, `pitch_dev` |
| `rates` | `blink`, `long_blink`, `yawn`, `nod` (per minute) |
| `geom` | `valid`, `roll`, `jaw_drop`, `nose_frac`, `nose_norm`, `mouth_ratio`, `eye_dist` |
| `alert` | `active`, `text`, `reason`, `count`, `muted`, `lang`, `lang_stored`, `counts{…}`, `clips{…}` |
| `stream` | `viewers`, `quality`, `fps`, `port` |
| `net` | `ssid`, `ip`, `clients`, `sta`, `sta_ip`, `rssi`, `sta_state`, `sta_bars`, `sta_retry_ms`, `button_armed` |
| `image` | `luma`, `min`, `max`, `peak` |
| `mem` | `heap`, `psram` |
| `card` | `mounted`, `events`, `free_mb`, `stored` |
| `mqtt` | `enabled`, `state`, `published`, `acked`, `queued`, `dropped`, `suppressed`, `rejected`, `retry_ms`, `error` — a summary; the settings are a separate document |

The fields worth watching first:

| Field | Reading |
| --- | --- |
| `camera`, `models`, `eye_model` | all three must be `true` before any number below means anything |
| `risk.streak` / `risk.required` | "5 of 8 frames" — a bare score is not diagnosable |
| `face.held` | detection dropped and the box is being held; expected at the moment of interest |
| `face.reject` | why a detection was rejected (`ok` when it was not) |
| `cues.baselines_ready` | the per-driver baselines have not converged yet if `false` |
| `cues.stale` | the cue inputs are older than they should be |
| `presence.health` | `ok`, `camera-fault` or `model-fault`. Anything but `ok` means every other field below is stale and no absence is being judged |
| `presence.state` | `warmup`, `present`, `absent`, `no-driver` or `fault` |
| `mqtt.state` | `disabled`, `idle`, `connecting`, `online`, `backoff` or `fault`. Anything but `online` while `enabled` is true means alerts are being buffered |
| `mqtt.published` / `mqtt.acked` | handed to the broker, and PUBACKed. A gap that does not close means QoS 1 messages are not being acknowledged |

#### `presence` — is anyone there, and can the device tell

Two fields rather than one, because "nobody is in the seat" and "the camera stopped"
are the same observation and opposite conclusions. A monitor that reported the second
as the first would send someone looking for a missing person instead of a loose
ribbon cable.

| Field | Meaning |
| --- | --- |
| `state` | `warmup` — healthy but not yet trusted to judge (the first `PRESENCE_WARMUP_S` after boot, or after a fault clears). `present` — a confirmed driver. `absent` — nobody, counting down. `no-driver` — nobody, and the alert has been announced. `fault` — the device cannot tell. |
| `health` | `ok`, `camera-fault` (frames have stopped arriving) or `model-fault` (the face detector did not load). The no-driver alert is suppressed in both fault states, and the absence episode is discarded rather than frozen. |
| `absent_s` | Continuous absence so far, in seconds. Still measured during `warmup`, so the page can show what is happening while the alert is disarmed. |
| `alert_after_s` | The configured threshold, so a client does not have to know the firmware constant. |
| `alerts` | No-driver announcements since boot. |

`driver` at the top level is the raw presence boolean from the tracker;
`presence.state` is the debounced interpretation of it. Use the latter for anything
a person reads.

#### `alert.counts` — announcements per reason

`{"drowsy":N,"microsleep":N,"yawning":N,"head_nod":N,"no_driver":N}`.

`alert.count` alone is not diagnosable: forty microsleep announcements and forty
no-driver announcements describe completely different drives, and one of them is not
about the driver at all.

```bash
curl http://192.168.4.1/api/status | python -m json.tool
./plxy.sh status        # the same, formatted
./plxy.sh watch         # one line per second
```

#### `mqtt` — a summary, never the settings

Deliberately small, and deliberately without a host or a topic in it. `/api/status` is
polled at 5 Hz, is what a screenshot of the device page shows, and already carries
forty fields; the broker configuration is a separate, larger document that only the
settings modal asks for. Nothing in this object can carry a credential — there is
nowhere in it to put one.

| Field | Meaning |
| --- | --- |
| `enabled` | publishing is switched on. When `false` nothing is queued and nothing is sent |
| `state` | see the table above |
| `published` / `acked` | handed to the broker / PUBACKed |
| `queued` | alerts waiting in the 16-deep outbox. Non-zero means the broker is unreachable |
| `dropped` | evicted from a full outbox. The **oldest** goes, so this counts lost history rather than lost recent alerts |
| `suppressed` | duplicate `event_id`s not republished |
| `rejected` | alerts the capture loop could not hand over because the outbox lock was busy. Normally zero, always |
| `retry_ms` | remaining backoff before the next connection attempt |
| `error` | last error, built from `esp_err_t` names and numeric codes only |

### `GET /api/snapshot`

One JPEG — **the actual frame the detector was handed**, not a fresh capture.
That distinction is the point: it is what makes a bad detection reproducible.

```bash
curl http://192.168.4.1/api/snapshot -o frame.jpg
./plxy.sh snapshot frame.jpg
```

### `GET /api/settings`

Returns the current settings without changing anything:

```json
{"quality": 12, "fps": 10, "muted": false, "lang": "en"}
```

### `POST /api/settings`

Parameters are query-string, all optional, all clamped:

| Parameter | Range | Meaning |
| --- | --- | --- |
| `quality` | 10–95 | JPEG quality, higher is better. Below ~10 the eyelids are indistinguishable from JPEG ringing, which defeats the purpose of the preview |
| `fps` | 1–20 | stream rate, capped at the detection loop's own rate — asking for more only burns CPU re-encoding frames the camera has not replaced |
| `muted` | `0` / `1` | silence the speaker |
| `lang` | `en` or `km` | alert language; anything else returns **400** with `{"error":"lang must be en or km"}` |

Returns the settings object above.

```bash
curl -X POST 'http://192.168.4.1/api/settings?quality=20&fps=10'
```

### `POST /api/alert-test`

```bash
curl -X POST 'http://192.168.4.1/api/alert-test?reason=1'
./plxy.sh alert microsleep
```

| `reason` | Meaning | Clip |
| --- | --- | --- |
| `0` | drowsy | "Warning. You appear drowsy." |
| `1` | microsleep | "Wake up! Wake up!" |
| `2` | yawning | "You seem tired. Take a break." |
| `3` | head nod | "Stay alert. Eyes on the road." |
| `4` | no driver | "No driver detected." |

All five exist in English and Khmer, embedded in the firmware, and any of them can be
replaced by dropping `<lang>_<reason>.wav` on the SD card — no rebuild. The numbering
is part of this API and is appended to, never renumbered; out-of-range values are
clamped to the valid range rather than rejected, so a client written against an older
firmware still gets a sound.

The response:

```json
{"played": true, "text": "...", "reason": "microsleep",
 "lang": "km", "source": "sdcard"}
```

`source` is not decoration. With no display, "it spoke Khmer off the card" and
"it fell back to the embedded English" sound identical to anyone who does not
speak one of the two, so the API reports where the audio came from.

### `GET /api/mqtt`

The broker configuration and the live connection state.

```json
{
  "config": {
    "enabled": true, "transport": "tls", "protocol": "3.1.1",
    "host": "broker.emqx.io", "port": 8883, "ws_path": "/mqtt",
    "client_id": "drowsyguard-drowsyguard-c5e019", "client_id_auto": true,
    "username_masked": "fl*********r", "username_set": true, "password_set": true,
    "qos": 1, "keepalive": 30, "lwt": true, "retain_status": true,
    "tls_insecure": false, "ca_present": false, "ca_bytes": 0,
    "topic_mode": "auto", "topic": "", "uri": "mqtts://broker.emqx.io:8883",
    "device_id": "drowsyguard-c5e019", "fleet_id": "demo-fleet", "remark": "Driver A",
    "topics": {
      "alerts": "plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/alerts",
      "status": "plxy/drowsyguard/demo-fleet/drowsyguard-c5e019/status",
      "fleet_alerts": "plxy/drowsyguard/demo-fleet/+/alerts",
      "fleet_status": "plxy/drowsyguard/demo-fleet/+/status"
    },
    "sta": {"enabled": false, "ssid": "", "password_set": false},
    "demo_broker": {"host": "broker.emqx.io", "tcp": 1883, "tls": 8883,
                    "ws": 8083, "wss": 8084, "path": "/mqtt", "public": true}
  },
  "status": {
    "state": "online", "client_up": true, "connects": 1, "disconnects": 0,
    "attempt": 0, "retry_ms": 0, "published": 12, "acked": 12,
    "queued": 0, "capacity": 16, "dropped": 0, "suppressed": 0, "rejected": 0,
    "boot_id": "9f1c2ab3", "seq": 13, "last_publish_ms": 3720000, "error": ""
  },
  "nvs": true
}
```

**No password is in this document.** Not masked, not truncated — absent. The username
comes back masked so that an operator can confirm *which* account is configured
without the value being readable off a screenshot; below four characters nothing is
kept. `demo_broker` is the public EMQX broker's own endpoints, reported so the page can
offer the preset without hard-coding numbers that could then disagree with the
firmware. `nvs` is whether the settings partition is usable at all — a device whose NVS
is unavailable publishes perfectly well until it is power-cycled and then silently
reverts.

Sent chunked: the two halves together are up to ~3.5 kB and the control task has an
8 kB stack - raised from 6 kB on 2026-09-05, when this endpoint's handler chain
(~6-7 kB of measured frames) turned out to overflow the old size and reboot the
board on every save.

### `POST /api/mqtt`

Takes an **`application/x-www-form-urlencoded` body**, not a query string: a CA
certificate is 1–2 kB and `CONFIG_HTTPD_MAX_URI_LEN` is 512. Not JSON either — every
field is a scalar, ESP-IDF's own `httpd_query_key_value()` finds the pairs, and
`settings_form_field()` percent-decodes them, which is testable on a host in a way a
hand-rolled JSON parser on a device is not.

**Partial updates.** The handler starts from the configuration currently in force and
overlays only the fields the body carries. An absent field is unchanged. That is what
makes the credential handling work: an **empty `password` means "keep the stored
one"**, so the page can render a masked placeholder and submit the form without ever
holding the secret.

| Field | Values |
| --- | --- |
| `enabled` | `0` / `1` |
| `transport` | `tcp`, `tls`, `ws`, `wss`. Moves the port with it, but only when the port was still the default for the old transport |
| `protocol` | `3.1.1` (or `311`) / `5` |
| `host` | hostname or IPv4, ≤ 95 chars, `[A-Za-z0-9.-]` only. IPv6 is not supported |
| `port` | 1–65535 |
| `ws_path` | required for `ws`/`wss`; must start with `/` |
| `client_id` | ≤ 63 printable, no spaces. Empty derives `drowsyguard-{device_id}` |
| `username`, `password` | set only when non-empty. `clear_username=1` / `clear_password=1` erase |
| `qos` | `0` or `1`. **2 is refused** with a reason: the alert path is at-least-once with event-ID de-duplication at both ends |
| `keepalive` | 5–300 seconds |
| `lwt`, `retain_status` | `0` / `1` |
| `tls_insecure` | `0` / `1`. Only accepted for `tls`/`wss` |
| `ca_cert` | a PEM certificate, ≤ 4 kB. `clear_ca=1` removes the stored one |
| `topic_mode` | `auto` / `manual` |
| `topic` | manual mode only. No `+` or `#`, no leading `$` or `/`, no empty levels |
| `device_id`, `fleet_id` | lowercase letters, digits, `-`, `_`, `.`; must start and end alphanumeric |
| `remark` | printable ASCII, ≤ 47 chars. Escaped, not restricted — quotes and backslashes are fine |
| `preset` | `emqx` loads the public broker's host, path and default port |

Station credentials are **not** here. They were until the Wi-Fi card got its own form;
two endpoints writing one NVS record meant a broker save from a stale page put the old
SSID back, silently. See [`POST /api/wifi`](#post-apiwifi).

Everything is validated **before anything is applied**, whether or not `enabled` is
set — so switching publishing on can never fail on a field filled in three screens
ago, and a configuration is never half-stored. A rejection is **400** with the field
named, so the page can highlight the input rather than showing a sentence nobody can
act on:

```json
{"error": "no '+' or '#' wildcards, no leading '$' or '/', no empty levels",
 "field": "topic"}
```

On success the response is the same document as `GET`. A value that would have to be
**truncated** to fit is refused rather than stored short: a hostname silently cut at 95
characters resolves to nothing, and the operator then sees a connection error instead
of the field they got wrong.

```bash
curl -X POST http://192.168.4.1/api/mqtt \
  --data-urlencode enabled=1 \
  --data-urlencode transport=tls \
  --data-urlencode host=broker.emqx.io \
  --data-urlencode port=8883 \
  --data-urlencode fleet_id=demo-fleet \
  --data-urlencode device_id=drowsyguard-c5e019 \
  --data-urlencode 'remark=Driver A'
```

### `POST /api/mqtt/test`

Publishes one synthetic alert with `alert: "test"` and `severity: "info"`, queued
through exactly the same path as a real one — a test that took a shortcut would only
prove the shortcut works. Without it, the only way to test a broker is to make somebody
fall asleep.

```json
{"queued": true, "state": "online", "queued_depth": 1,
 "published": 13, "acked": 12, "error": ""}
```

**409** when publishing is disabled, with `queued: false`.

### `GET /api/wifi`

Where the station side is, why it is not somewhere else, and whether the reset button
would do anything. No credentials: `password_set` is a boolean, and there is nowhere
in the document to put a passphrase.

```json
{"ap": {"ssid": "DrowsyGuard-A1B2C3", "ip": "192.168.4.1", "clients": 1, "up": true},
 "sta": {"state": "connected", "ssid": "KDSB-Office", "stored": true,
         "connected": true, "ip": "10.0.0.42", "rssi": -58, "bars": 4,
         "attempts": 0, "retry_ms": 0, "reason": 0, "reason_text": "",
         "password_set": true, "auth_failed": false},
 "button": {"gpio": 0, "armed": true, "held_ms": 0, "hold_ms": 5000},
 "nvs": true}
```

| Field | Meaning |
| --- | --- |
| `ap` | the device's own access point, which is up in every one of these states |
| `sta.state` | `disabled`, `idle`, `connecting`, `connected`, `failed` |
| `sta.ssid` | the network the radio is **configured for**, which is a different question from whether it joined |
| `sta.stored` | credentials are in NVS and will be rejoined at the next boot |
| `sta.bars` | 0–4, from the same thresholds the page draws (`-55` / `-67` / `-75` / `-85` dBm) |
| `sta.attempts` | failures since the last success |
| `sta.retry_ms` | remaining backoff. A device waiting 60 s is not a device that gave up |
| `sta.reason` / `reason_text` | the 802.11 reason code and a sentence for it. `15` is the wrong passphrase; `201` is no such network in range |
| `sta.auth_failed` | the reason code was an authentication failure, so the page can say "retype the password" rather than "not connected" |
| `button.armed` | the BOOT watcher has seen a release and would act on a hold. **False with a serial monitor attached**: this board's reset lines are inverted, so opening a port pulls GPIO0 low |
| `nvs` | the settings partition is usable. `false` means anything saved is lost at the next boot |

### `POST /api/wifi`

Form-encoded, like `/api/mqtt`, and the same partial-update rule: **an empty
`password` keeps the stored one**, so the page can submit the form without ever having
held the secret.

| Field | Values |
| --- | --- |
| `action` | `forget` or `reconnect`. Without it the body is a connect |
| `ssid` | ≤ 32 **bytes**. Any byte except a control character — 802.11 says an SSID is 32 arbitrary octets, so a name in Khmer or with an accent is fine and costs two or three bytes per character |
| `password` | 8–63 characters of ASCII, which is what WPA2-PSK allows. Sent only when non-empty |
| `open` | `1` for a network with no passphrase. **Explicit rather than inferred**, so the stored passphrase from the last network is never handed to an access point anybody can stand up |

`action=forget` erases the credentials from NVS and blanks the radio's copy, in that
order — a reset interrupted between the two comes back provisioning rather than
rejoining the network somebody just asked it to forget. It touches **nothing else**:
the broker settings, the device identity, the CA certificate and the stored captures
survive, and the access point does not blink.

`action=reconnect` retries now instead of waiting out the backoff.

The response is the same document as `GET`. A rejection is **400** with the field
named (`ssid` or `password`), so the page highlights the input.

```bash
curl -X POST http://192.168.4.1/api/wifi \
  --data-urlencode ssid=KDSB-Office \
  --data-urlencode 'password=correct horse battery'

curl -X POST http://192.168.4.1/api/wifi --data-urlencode action=forget
```

### `GET /api/wifi/scan`

The last scan, and whether one is running. Never blocks: a scan takes two to three
seconds inside the radio, and this returns whatever the previous one found.

```json
{"scanning": false, "age_ms": 1200,
 "networks": [{"ssid": "KDSB-Office", "rssi": -46, "bars": 4, "channel": 6,
               "auth": "wpa2", "open": false}]}
```

Strongest first, de-duplicated by SSID (a mesh answers on several channels), hidden
networks dropped — one with no name in its beacon cannot be picked from a list, and
its SSID has to be typed in anyway. At most 24.

SSIDs are escaped twice on the way here, in two different alphabets: by the firmware
for JSON and by the page for HTML. An SSID is 32 bytes chosen by whoever owns the
access point and anybody in radio range can broadcast one, so bytes that are not valid
UTF-8 are escaped as `\u00XX` rather than passed through — a raw high byte would make
the whole document undecodable, and the operator would see an empty list on the one
page they are using to recover the device.

### `POST /api/wifi/scan`

Starts a scan and returns immediately with `{"scanning": true}`; poll the `GET` for
results. **409** when a scan is already running or the radio is mid-association —
a state to retry, not an error to report.

!!! warning "A scan interrupts the access point"
    One radio, one antenna, and a scan walks every channel. The page stalls for two or
    three seconds and then carries on. Nothing is disconnected, and the camera, the
    detector and the alert path are untouched — they run on the other core and never
    wait on the radio.

### The alert payload { #the-alert-payload }

What the device publishes to `topics.alerts`, one message per confirmed alert.
Versioned, so a subscriber can refuse a document it does not understand rather than
rendering a device with no risk and no remark.

```json
{"schema":"drowsyguard.alert.v1","event_id":"drowsyguard-c5e019-9f1c2ab3-000042",
 "seq":42,"device_id":"drowsyguard-c5e019","fleet_id":"demo-fleet","remark":"Driver A",
 "alert":"microsleep","severity":"critical","risk":0.712,"perclos":0.421,
 "alert_count":12,"uptime_ms":3723456,"ts":"2026-09-01T11:15:03Z","ts_source":"sntp"}
```

| Field | Meaning |
| --- | --- |
| `schema` | `drowsyguard.alert.v1` |
| `event_id` | `{device-id}-{boot-id}-{sequence}`. Stable across retries, unique across reboots — the boot id is what stops a device that restarted mid-drive from reusing sequence numbers it already published |
| `seq` | monotonic within one boot |
| `device_id`, `fleet_id` | the validated topic segments |
| `remark` | operator free text — "Driver A". JSON-escaped, so quotes and backslashes survive |
| `alert` | `drowsy`, `microsleep`, `yawning`, `head_nod`, `no_driver` or `test`. The same token as the SD-card filename and the spoken clip, so the three records of one event cannot disagree |
| `severity` | **derived** from `alert`, so it cannot disagree with it. `microsleep` and `no_driver` are `critical`; `drowsy` and `head_nod` are `high` (`drowsy` escalates to `critical` above 0.85); `yawning` is `medium` |
| `risk` | the fused behaviour score, 0–1. A non-finite value is published as `0` — `printf` emits `nan`, which is not JSON, and a subscriber whose parser throws loses every alert after the bad one |
| `perclos` | share of the window with the eyes closed, 0–1 |
| `alert_count` | announcements since boot, across every channel |
| `uptime_ms` | device uptime at the moment of the alert. **Always present**, and what to order events by |
| `ts` | ISO-8601 UTC, or `""` |
| `ts_source` | `sntp` when `ts` is real, `uptime` when the clock has never been set |

`ts` is empty on a device that has not reached a time server, which is the normal case:
there is no real-time clock on the board. A fabricated timestamp in an incident record
is worse than an absent one.

### The status payload

Published to `topics.status` with the retain flag, and used as the **Last Will**.

```json
{"schema":"drowsyguard.status.v1","device_id":"drowsyguard-c5e019",
 "fleet_id":"demo-fleet","remark":"Driver A","online":false,"reason":"last-will",
 "uptime_ms":0,"alert_count":0,"ts":""}
```

| `reason` | Who published it |
| --- | --- |
| `connected` | the device, on connecting. `online: true` |
| `shutdown` | the device, on a clean disconnect — a reconfigure, or the client being stopped |
| `last-will` | **the broker**. The device vanished without saying goodbye |

That distinction is the entire reason for the will: a dashboard that cannot tell "no
alerts because the driver is fine" from "no alerts because the device is in a tunnel"
is worse than no dashboard. The will body claims no uptime and no timestamp
deliberately — it is composed at connect time and delivered at an unknown moment
later, so any value it named would be a lie.

### `GET /api/events`

The index of alert captures on the SD card, one page at a time — 48 entries is
about 3 kB on the control task's stack, and the index could be a thousand.

| Parameter | Default | Range |
| --- | --- | --- |
| `skip` | `0` | ≥ 0 |
| `limit` | `24` | 1–48 |

```json
{
  "card":  {"mounted": true, "name": "SD", "total": 0, "free": 0, "error": ""},
  "total": 137, "skip": 0, "stored": 137, "dropped": 2,
  "events": [
    {"id": "000137", "uptime_ms": 942310, "size": 18422,
     "risk": 0.71, "perclos": 0.44, "reason": "microsleep"}
  ]
}
```

`dropped` is the count the board could not write — a full or slow card shows up
here rather than as a silent gap in the history.

### `GET /api/event`

```bash
curl 'http://192.168.4.1/api/event?id=000137' -o event.jpg
```

`id` is required (**400** without it) and must exist (**404** otherwise).

### `POST /api/events/clear`

Deletes every stored capture.

### `GET /favicon.ico`

Returns the DrowsyGuard multi-size icon (`image/vnd.microsoft.icon`). The device
page also uses it as the app mark in its sticky header. Browsers may cache it for
one day.

## Stream server (port 81)

### `GET /stream`

`multipart/x-mixed-replace` MJPEG at the configured stream rate. The page embeds
this; `stream.viewers` in the status object counts who is attached.

### `GET /frame`

A single JPEG from the stream server, for clients that cannot hold a multipart
connection open.

## Error behaviour

| Status | When |
| --- | --- |
| **400** | `lang` outside `en`/`km`; `/api/event` without `id`; any rejected `/api/mqtt` field, with `field` naming it; an `/api/mqtt` body that is empty or over 6 kB |
| **404** | `/api/event` with an unknown `id` |
| **409** | `POST /api/mqtt/test` while publishing is disabled |
| **500** | the status object would not fit its buffer — deliberately preferred over serving half an object; or `/api/mqtt` settings applied but not persisted, which is the one outcome worth distinguishing because the broker works until the next power cycle and then quietly reverts |
| **503** | `POST /api/mqtt` when the settings buffers could not be allocated. Publishing, if already configured, is unaffected |

There is **no authentication on any endpoint**. See
[Security](../security.md#the-device-api-is-unauthenticated).
