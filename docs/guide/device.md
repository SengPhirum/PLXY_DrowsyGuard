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

!!! tip "The access point never comes down"
    Not while the board joins another network, not when that fails, and not while a
    scan is running. It is the recovery path: a device with a wrong password saved is
    still a device you can open `http://192.168.4.1/` on and fix. The two headers
    still exist as a compile-time default for a bench board, but the Wi-Fi card below
    is the normal way in.

## Joining an existing network

The board runs as an access point **and** a station at the same time. Joining a
network gets it a route to an MQTT broker and makes it reachable from a development
machine; nothing else needs one — detection, the voice alerts, the captures and this
page all work on the access point alone.

Open the **Wi-Fi** card on the dashboard and press **Wi-Fi settings**:

1. **Scan for networks.** The list is strongest-first, de-duplicated by name, with
   signal bars and a lock for anything that is not open. A hidden network is not
   listed — it does not broadcast its name — so type it in instead.
2. **Pick one**, type the password, press **Save and connect**. Joining takes a few
   seconds and the status block updates as it goes.
3. **Reconnect now** retries immediately instead of waiting out the backoff, which
   grows from 2 s to a minute across repeated failures.
4. **Forget network** erases the saved credentials after a confirmation.

The credentials are stored in the settings partition and rejoined automatically on
every boot. The password box is write-only: the device answers `password_set` and
never a password, so it opens empty and leaving it blank keeps what is stored.

!!! warning "Scanning interrupts the access point"
    One radio, one antenna. A scan walks every channel, so this page stalls for two
    or three seconds and then carries on. It does not disconnect anyone, and it does
    not touch the camera, the detector or the alert path — those run on the other
    core and never wait on the radio.

An SSID is 32 bytes, not 32 characters: a name in Khmer or with an accent costs two
or three bytes per character. Passwords are 8–63 characters of ASCII, which is what
WPA2-PSK itself allows, or blank for an open network — tick **This network is open**
rather than clearing the box, so the passphrase from the last network is never handed
to an access point anyone can stand up.

Point the tooling at the joined address with `PLXY_HOST=10.0.0.5 ./plxy.sh watch`,
and read it back from the `net.sta_ip` field of
[`/api/status`](../reference/device-api.md#get-apistatus).

### Resetting Wi-Fi from the BOOT button

If the saved network is gone and the access point password has also been changed —
or you would simply rather not type on a phone — **hold the BOOT button for five
seconds**. The serial log warns at two seconds and confirms at five:

```text
W (48213) button: BOOT held - keep holding for 3 more seconds to CLEAR THE WI-FI
                  CREDENTIALS, or release now to cancel
W (51218) button: BOOT held 5 s - clearing the Wi-Fi credentials
W (51221) settings: station credentials erased; mqtt, device identity and the CA
                    certificate are untouched
W (51224) main: Wi-Fi reset by the BOOT button: credentials erased. The access point
                is still up - reconnect to it and open http://192.168.4.1/ to set a
                network. Detection, alerts and MQTT settings are unchanged.
```

Let go before the five seconds and it says `BOOT released - nothing was changed`.

It clears **the saved network and nothing else**. The broker settings, the device and
fleet identity, the CA certificate, the alert language and the stored captures all
survive, and the device does not reboot: the access point, the camera and the detector
keep running throughout, so the page stays open in your hand while the reset happens.

Three rules stop it firing by accident:

- nothing counts in the first three seconds after boot;
- the button must be seen **released** before any press is believed. This matters on
  this board specifically: its auto-reset lines are inverted, so opening a serial
  terminal pulls GPIO0 low — anyone with a terminal attached is, electrically, holding
  BOOT down. The watcher stays disarmed and the Wi-Fi card says so rather than going
  quiet;
- a press that never releases stops counting after 30 seconds, so a wedged button
  cannot erase the credentials on every boot.

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
| **Live view** | the MJPEG feed with the face box and five landmarks drawn over it, plus pills for **driver presence**, face score, detection source and image luma |
| **Fused drowsiness risk** | the fused score, the trigger, the current streak (`5 of 8 frames`) and a trend line |
| **Eyes & cues** | P(closed) smoothed, PERCLOS, closure duration, and the mouth-open / head-down / baselines-ready pills |
| **Controls** | mute, speak a warning, JPEG quality, stream rate, still-photo mode, save frame |
| **Event log** | recent alerts |
| **Drowsiness history** | JPEG captures written to the SD card at alert time |
| **Device** | uptime, frames, fps, viewers, clients, frame size, heap, PSRAM and how long the seat has looked empty |

### The driver pill

The one to read first, because it answers a question the face pill cannot. The face
pill says what the detector saw on *this frame*; the driver pill says what the device
concluded about the cabin, and when the two disagree it says why.

| Pill | Meaning |
| --- | --- |
| `driver present` | A confirmed driver: two consecutive agreeing detections, still within the tracking hold. Everything else on the page is about them. |
| `settling` | The first five healthy seconds after boot, or after a fault clears. Absence is measured but not acted on — the camera and the auto-exposure are still converging. |
| `empty Ns` | Nobody, counting toward the announcement. |
| `NO DRIVER` | Nobody, and the device has said so. The state pill also reads **NOT MONITORING**, which is the honest description: this device is not watching anyone. |
| `camera fault` / `model fault` | The device *cannot tell*. Not an empty seat, and deliberately never announced as one. |

When no driver is found and something was in frame, the reject reason is shown instead
of a bare "no face" — `score-too-low`, `box-not-head-shaped`, `nose-outside-eye-pair`
and so on. Each names a different fix; the table in
[Troubleshooting](../troubleshooting.md#the-page-says-no-driver-while-somebody-is-sitting-there)
maps them.

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
./plxy.sh alert drowsy        # or: microsleep | yawn | nod | no_driver
./plxy.sh mute
./plxy.sh unmute
```

Five reasons, and they do not all mean the same kind of thing:

| Reason | It is saying |
| --- | --- |
| `drowsy`, `microsleep`, `yawn`, `nod` | A drowsiness threshold was crossed. These share one rate limit — 30 s apart, three per episode, resetting after five quiet minutes — because they are one conversation. |
| `no_driver` | The device has stopped monitoring anyone. Its own rate limit too, and no cap — "nobody is driving" must never be a message the device has used up its allowance of. |

`./plxy.sh status` reports `alert.counts` per reason. That distinction matters: forty
microsleep announcements and forty no-driver announcements describe completely
different drives, and one of them is not about the driver at all.

The response says whether the clip played, which language was used, and
**where the audio came from** — an SD-card clip or the embedded English
fallback. With no display, "it spoke Khmer off the card" and "it fell back to
the embedded English" sound identical to anyone who does not speak one of the
two, so the API reports the source explicitly.

See [Voice alert hardware](../VOICE_ALERT_HARDWARE.md) for the amplifier, the
clip set and the alert state machine.

## Fleet alerting

The **Fleet alerting (MQTT)** card publishes every confirmed alert to a broker, so a
supervisor can watch a fleet without being in the cab. It is off until you switch it
on, and nothing about the speaker, the dashboard or the captures depends on it.

Press **Configure MQTT**, and the modal covers the whole feature: transport
(TCP/TLS/WS/WSS), MQTT 3.1.1 or 5, host, port, WebSocket path, client id, credentials,
a CA certificate, QoS, the driver remark, and automatic or manual topics.

The network the device publishes *over* is set separately, on the Wi-Fi card above —
the board's own access point has no route to the internet, so a broker on one needs
the device joined to something. One NVS record, one form.

Three things on that card are worth watching:

- the state pill: anything but `online` while it is enabled means alerts are being
  buffered rather than published;
- `published / acked`: a gap that does not close means the broker is not acknowledging
  QoS 1 messages;
- `buffered`: alerts waiting. Sixteen is the limit, past which the oldest is dropped
  and counted.

**Test publish** sends one synthetic alert through the real path, which is the only way
to test a broker that does not involve making somebody fall asleep. The **copy** buttons
beside the three topics are there because the fleet-wide one is sixty characters of
slashes — paste it into the [fleet monitor](../fleet-monitoring.md).

Full walkthrough: [Fleet alerting over MQTT](mqtt.md).

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
