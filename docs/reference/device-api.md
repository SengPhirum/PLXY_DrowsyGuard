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
`snprintf` into a static 3584-byte buffer rather than a cJSON tree: the control
server has a 6 kB task stack, only one task ever serves port 80, and truncation
returns **500** rather than letting the page parse half an object.

| Object | Fields |
| --- | --- |
| top level | `uptime_ms`, `frames`, `fps`, `camera`, `models`, `eye_model`, `driver` |
| `ms` | `detect`, `eye` — per-stage milliseconds |
| `frame` | `w`, `h` |
| `face` | `found`, `held`, `x`, `y`, `w`, `h`, `score`, `roi`, `roi_w`, `rejected`, `reject` |
| `lm` | `valid`, `x[5]`, `y[5]` — the five landmarks |
| `risk` | `score`, `trigger`, `streak`, `required` |
| `eyes` | `closed`, `smooth`, `shut`, `perclos`, `closure_s` |
| `cues` | `mouth_open`, `head_down`, `suppressed`, `baselines_ready`, `stale`, `events`, `open_index`, `pitch_dev` |
| `rates` | `blink`, `long_blink`, `yawn`, `nod` (per minute), `sneeze` (count) |
| `geom` | `valid`, `roll`, `jaw_drop`, `nose_frac`, `nose_norm`, `mouth_ratio`, `eye_dist` |
| `alert` | `active`, `text`, `reason`, `count`, `muted`, `lang`, `lang_stored`, `clips{drowsy,microsleep,yawning,head_nod}` |
| `stream` | `viewers`, `quality`, `fps`, `port` |
| `net` | `ssid`, `ip`, `clients`, `sta`, `sta_ip`, `rssi` |
| `image` | `luma`, `min`, `max`, `peak` |
| `mem` | `heap`, `psram` |
| `card` | `mounted`, `events`, `free_mb`, `stored` |

The fields worth watching first:

| Field | Reading |
| --- | --- |
| `camera`, `models`, `eye_model` | all three must be `true` before any number below means anything |
| `risk.streak` / `risk.required` | "5 of 8 frames" — a bare score is not diagnosable |
| `face.held` | detection dropped and the box is being held; expected at the moment of interest |
| `face.reject` | why a detection was rejected (`ok` when it was not) |
| `cues.baselines_ready` | the per-driver baselines have not converged yet if `false` |
| `cues.stale` | the cue inputs are older than they should be |

```bash
curl http://192.168.4.1/api/status | python -m json.tool
./plxy.sh status        # the same, formatted
./plxy.sh watch         # one line per second
```

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

| `reason` | Meaning |
| --- | --- |
| `0` | drowsy |
| `1` | microsleep |
| `2` | yawning |
| `3` | head nod |

Out-of-range values are clamped to 0–3. The response:

```json
{"played": true, "text": "...", "reason": "microsleep",
 "lang": "km", "source": "sdcard"}
```

`source` is not decoration. With no display, "it spoke Khmer off the card" and
"it fell back to the embedded English" sound identical to anyone who does not
speak one of the two, so the API reports where the audio came from.

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

Served so the browser stops asking.

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
| **400** | `lang` outside `en`/`km`; `/api/event` without `id` |
| **404** | `/api/event` with an unknown `id` |
| **500** | the status object would not fit its buffer — deliberately preferred over serving half an object |

There is **no authentication on any endpoint**. See
[Security](../security.md#the-device-api-is-unauthenticated).
