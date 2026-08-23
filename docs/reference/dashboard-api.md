---
title: Dashboard HTTP API
---

# Dashboard HTTP API

The desktop live dashboard is a FastAPI app served by
`python -m drowsyguard.cli live`, on `http://127.0.0.1:8000` by default.

!!! danger "Local tool, no authentication"
    There is no auth on any endpoint and `/stream` is your webcam. The default
    bind is `127.0.0.1`; only pass `--host 0.0.0.0` on a network you trust.
    FastAPI's own `/docs` and `/redoc` are disabled. See
    [Security](../security.md#the-dashboard-streams-your-webcam).

## `GET /`

The dashboard page (`src/drowsyguard/static/index.html`).

## Streams

All three are `multipart/x-mixed-replace` MJPEG with the boundary
`drowsyguardframe`.

| Endpoint | Rate | Shows |
| --- | --- | --- |
| `GET /stream` | 20 fps | the annotated camera frame |
| `GET /input-stream` | 10 fps | the exact tensor the model receives |
| `GET /eye-stream` | 10 fps | the two eye crops |

`/input-stream` is the one to open when results look wrong: it shows what the
model actually sees, which is usually where the surprise is.

## `GET /snapshot.jpg`

One JPEG of the current annotated frame. Returns **503** before the first frame
has been captured.

## `GET /state`

The engine snapshot, polled by the page.

| Key | Meaning |
| --- | --- |
| `running` | the capture thread is alive |
| `error` | fatal error, or `null` |
| `warning` | non-fatal warnings joined into one string — tracker, eye model and capture warnings can all apply at once, and none silences the others |
| `p_drowsy` | the current fused probability |
| `state` | the `RiskFilter` state |
| `streak`, `cooldown_left` | frames over the trigger, and frames still held off |
| `config` | trigger, required, cooldown, zoom, PERCLOS window, eye threshold |
| `history` | the rolling p(drowsy) trace behind the chart |
| `alerts` | the last 10, newest first |
| `behavior_events` | the last 12 cue events, newest first |
| `alert_count`, `frames`, `fps`, `infer_ms` | counters |
| `mode` | `eye` or `face` |
| `eyes` | per-eye P(closed), PERCLOS, closure state |
| `model` | `kind`, `trained`, `source`, `normalize` — `trained: false` is why the page says the probabilities are meaningless |
| `image_size`, `crop` | preprocessing geometry |
| `camera` | backend, index/source, capture timing |
| `face`, `face_detect` | tracker state, and whether detection is on |

## `POST /config`

JSON body; every key optional. Applied live, which is what the sliders do.

```bash
curl -X POST http://127.0.0.1:8000/config \
     -H 'Content-Type: application/json' \
     -d '{"trigger": 0.6, "required": 8, "cooldown": 60}'
```

| Key | Effect |
| --- | --- |
| `trigger` | risk level that starts a streak |
| `required` | consecutive frames required to alert |
| `cooldown` | frames held off after an alert |
| `zoom` | centre-crop fraction (only when no face is detected) |
| `face_detect` | enable/disable detection and tracking |
| `perclos_window` | frames in the PERCLOS window |
| `eye_closed_threshold` | P(closed) above which an eye counts as shut |

Returns the resulting config. The page's **Copy as C++** button turns
`trigger`/`required`/`cooldown` into the `RiskFilter` constructor line for
`firmware/esp32s3/main/risk_filter.h`.

## `POST /reset`

Clears the filter state, the history and the counters. Returns `{"ok": true}`.

## Compared with the device

The [device API](device-api.md) is a fixed-shape object built by one `snprintf`
under a 6 kB stack; this one is whatever the engine snapshot holds. They are not
interchangeable, and only the decision logic beneath them is shared.
