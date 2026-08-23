---
title: Using the device
---

# Using the device

The board has **no display**. Everything it sees and every number it computes is
served as a web page over its own Wi-Fi access point.

## Join the access point

```bash
./plxy.sh wifi       # prints SSID, password and URL
./plxy.sh open       # opens the preview in your browser
```

| Setting | Default | Defined in |
| --- | --- | --- |
| SSID | `DrowsyGuard-XXXXXX` (suffix from the MAC) | `WIFI_AP_SSID_PREFIX` |
| Password | `drowsyguard` | `WIFI_AP_PASSWORD` |
| Channel | 6 | `WIFI_AP_CHANNEL` |
| Max clients | 4 | `WIFI_AP_MAX_CLIENTS` |
| Address | `http://192.168.4.1/` | fixed by the AP |

All four live in `firmware/esp32s3/main/board_wifi.h` — see
[Configuration](../configuration/index.md#wi-fi).

!!! tip "Station mode"
    Setting `WIFI_STA_SSID` / `WIFI_STA_PASSWORD` in the same header joins an
    existing network instead. Then point the tooling at it with
    `PLXY_HOST=10.0.0.5 ./plxy.sh watch`, and read the assigned address back from
    the `net.sta_ip` field of [`/api/status`](../reference/device-api.md#get-apistatus).

## Two servers, two ports

| Port | Serves |
| --- | --- |
| **80** | the page and the whole JSON API |
| **81** | the MJPEG stream (`/stream`) and single frames (`/frame`) |

They are separate `httpd` instances on purpose: a viewer holding the MJPEG
stream open must not block the 5 Hz status polling that the page depends on.

## What the page shows

| Panel | Contents |
| --- | --- |
| **Live view** | the MJPEG feed with the face box and five landmarks drawn over it, plus pills for face score, detection source and image luma |
| **Fused drowsiness risk** | the fused score, the trigger, the current streak (`5 of 8 frames`) and a trend line |
| **Eyes & cues** | P(closed) smoothed, PERCLOS, closure duration, and the mouth-open / head-down / suppressed / baselines-ready pills |
| **Controls** | mute, speak a warning, JPEG quality, stream rate, still-photo mode, save frame |
| **Event log** | recent alerts |
| **Drowsiness history** | JPEG captures written to the SD card at alert time |
| **Device** | uptime, frames, fps, viewers, clients, frame size, heap and PSRAM |

## From the command line

```bash
./plxy.sh status              # pretty-print the whole status object
./plxy.sh watch               # one risk/PERCLOS line per second
./plxy.sh snapshot frame.jpg  # save a single JPEG
```

`watch` is the one to leave running while someone sits in front of the camera:
it is the fastest way to see whether the trigger and the streak requirement are
set sensibly for the frame rate you are actually getting.

## Alerts

The board speaks its warnings; there is nothing to read while driving.

```bash
./plxy.sh alert drowsy        # or: microsleep | yawn | nod
./plxy.sh mute
./plxy.sh unmute
```

The response says whether the clip played, which language was used, and
**where the audio came from** — an SD-card clip or the embedded English
fallback. With no display, "it spoke Khmer off the card" and "it fell back to
the embedded English" sound identical to anyone who does not speak one of the
two, so the API reports the source explicitly.

See [Voice alert hardware](../VOICE_ALERT_HARDWARE.md) for the amplifier, the
clip set and the alert state machine.

## Captures on the SD card

When an alert fires the board writes the frame to the SD card, and the page
lists them under **Drowsiness history**. Each entry carries the risk and PERCLOS
at the moment of capture, so a review pass can tell a real microsleep from a
detector artefact.

```bash
curl 'http://192.168.4.1/api/events?limit=24'         # list
curl 'http://192.168.4.1/api/event?id=<id>' -o e.jpg  # one image
curl -X POST http://192.168.4.1/api/events/clear      # delete all
```

Full field list: [Device HTTP API](../reference/device-api.md).

## Tuning without a rebuild

Stream quality and rate are settable at runtime:

```bash
curl -X POST 'http://192.168.4.1/api/settings?quality=12&fps=10'
```

Risk thresholds are **not** runtime-settable on the device — they are compiled
in. Tune them in the [live dashboard](live-dashboard.md), use its **Copy as C++**
button, and paste the constructor line into `risk_filter.h`.

!!! note "Frames, not seconds"
    `required` and `cooldown` are counted in **frames**, but were chosen as
    durations — 8 frames is about half a second at 15 fps. Anything that changes
    the frame rate changes the alarm's sensitivity without touching a threshold,
    so `main.cpp` re-derives both from the measured rate through `retune()` to
    keep the intended half second half a second.
