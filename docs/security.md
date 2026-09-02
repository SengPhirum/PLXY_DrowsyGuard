---
title: Security and safety
---

# Security and safety

DrowsyGuard is a research prototype that carries a camera pointed at a person's
face, an open radio, and an unauthenticated HTTP API. None of that is an
accident, and none of it is safe to deploy unchanged.

Since [MQTT alerting](guide/mqtt.md) was added it can also send a named driver's
alertness state off the vehicle, which is a different kind of exposure from
everything above it: the others need somebody within radio range, and this one does
not. It is **off by default** and stays off until somebody turns it on, and the
section below is what to read before doing that.

## Safety and limitations

!!! danger "Not a safety device"
    DrowsyGuard is a thesis research prototype. It is **not** a certified
    automotive safety device, it has never been validated on real drivers, and
    it must not be relied on to keep anyone awake or alert. Its event detectors
    (yawn, nod, sneeze) use literature-informed thresholds that have **not** been
    tuned on labelled video, and its eye model is
    [known to transfer poorly](guide/training.md#the-eye-model) to visible-light
    footage.

Do not fit it in a vehicle in a way that could distract the driver, obstruct the
view, or come loose in a collision. Testing on public roads is out of scope for
this project.

### What each alert does and does not claim

The device now speaks about three different things, and they carry very different
amounts of evidence. Reading a low-evidence alert as a high-evidence one is the
specific way a research prototype gets over-trusted.

| Alert | What it means | What it does **not** mean |
| --- | --- | --- |
| **Drowsy / Microsleep / Yawning / Head nod** | Sustained eye closure, or a behavioural cue, crossed a threshold. | That the driver is impaired. The thresholds are literature-informed defaults, never tuned on labelled video, and the eye model is IR-trained with an [AUC of 0.62 on visible light](guide/training.md#the-eye-model). Both false alarms and silence are expected. |
| **Sneeze detected** | A ~1 s eye closure coincided with the mouth opening at the same moment, so the drowsiness alarm was deliberately suppressed. | That it was definitely a sneeze. It is a *discriminator*, not a classifier: what it actually establishes is that this closure does not look like a microsleep. A yawn with the eyes shut is explicitly ruled out (see `SNEEZE_MOUTH_LEAD_S`), but no labelled sneeze video exists for this project. |
| **No driver detected** | Nobody has been confirmed in front of the camera for the configured time, and the camera and models are working. | That the seat is empty. It equally means the driver is turned away, out of frame, badly lit, or wearing something the detector cannot see past. The honest reading is "this device is not monitoring anyone", which is exactly what the clip says. |

The no-driver alert exists because of an asymmetry worth stating plainly: a
monitoring system that has silently stopped monitoring is more dangerous than no
monitoring system at all, because the person relying on it does not know. It is
**not** a driver-presence sensor and must not be used as one.

### When the device says nothing

Silence has more than one cause, and the status page separates them so that
"working and quiet" is distinguishable from "not working":

- `presence.health` is `camera-fault` or `model-fault`. The device cannot see, and
  it deliberately does **not** announce a no-driver condition in that state — that
  would be a claim about the cabin drawn from a fact about the hardware.
- `eye_model` is false. PERCLOS is pinned at zero and the drowsiness alarm can only
  under-report, so the page says **EYE MODEL MISSING** rather than shipping an alarm
  that quietly never fires.
- `alert.muted` is true. Detection, counters and the event log all keep running; only
  the speaker is silent.

## The threat model

The board is designed to be used **on a bench or in a parked vehicle, by the
person who built it**, on a network with nobody else on it. Everything below is
acceptable in that setting and unacceptable outside it.

## The access point is open by default

The firmware raises a WPA2 access point with the password `drowsyguard`, hard
coded in `firmware/esp32s3/main/board_wifi.h`.

- It is in a **public git repository**. Anyone who reads this documentation knows it.
- Change `WIFI_AP_PASSWORD` before any test where someone else is in range.
- WPA2 requires **8 characters or more**; a shorter value makes phones refuse to
  join, which looks like a broken radio.

The AP is also capped at `WIFI_AP_MAX_CLIENTS` (4) on channel 6. Joining it puts
a device on the same L2 segment as the board and nothing else — the board does
not route.

## The device API is unauthenticated

Every endpoint on ports 80 and 81 is open to anyone who can reach the board.
There is no login, no token, and no TLS. Anyone on the AP can:

- watch the live camera stream (`/stream`)
- download stored alert captures of the driver's face (`/api/event`)
- delete the whole capture history (`POST /api/events/clear`)
- play alerts through the speaker (`POST /api/alert-test`)
- mute the alerts (`POST /api/settings?muted=1`)
- read the broker configuration, minus the passwords (`GET /api/mqtt`)
- **change** the broker configuration, including the topic and the Wi-Fi station
  credentials (`POST /api/mqtt`)

The mute matters most: **an unauthenticated client can silence the alarm.** The
second-worst is the one MQTT added - an unauthenticated client can redirect the
telemetry to a broker of its own, or read the SSID the device is joining. Both
follow from the same fact: the AP password is the only access control there is.
Change it.

In station mode the board is exposed to everything on that network instead. Only
join networks you control.

## MQTT alerting leaves the vehicle { #mqtt-alerting-leaves-the-vehicle }

Everything else on this page needs an attacker within Wi-Fi range. Publishing to a
broker does not: it takes the one genuinely sensitive fact this device produces -
that a named person is falling asleep - and puts it on the internet, addressed to
whoever holds the subscription.

**It is off by default, and turning it on is an act rather than an inherited
setting.** That is why there is no "enabled" default to override and no environment
variable that switches it on.

### The preconfigured broker is public

`broker.emqx.io` is the [official EMQX public broker][emqx]. It is filled in on the
device's settings modal and on the [fleet monitor](fleet-monitoring.md) so that a
demonstration works in a room with no broker in it, and it is
**demonstration and testing only**:

- there is **no authentication**. Anyone can subscribe, and anyone can publish;
- there is **no isolation**. The topic tree is shared with the whole internet;
- the topics are **guessable**. `plxy/drowsyguard/#` is one subscription;
- so anyone watching reads every alert, including the **driver remark** - which is
  the field that names a person.

Point both ends at a broker you control before any real vehicle, and before any
demonstration where the remark is a real person's name rather than "Driver A".

[emqx]: https://www.emqx.com/en/mqtt/public-mqtt5-broker

### What is in a published message, and what is not

| In the payload | In the topic |
| --- | --- |
| `device_id`, `fleet_id`, `remark`, `alert`, `severity`, `risk`, `perclos`, `alert_count`, `uptime_ms`, `ts`, `event_id`, `seq` | `plxy/drowsyguard/{fleet-id}/{device-id}/alerts` |

**No images.** The JPEG captures stay on the SD card and are never published; a
camera frame of somebody's face is the one thing on this device that must not leave
it over a shared broker, and there is no code path that would.

**The remark is deliberately not in the topic.** A driver's name has no business in a
broker's subscription tree, where it is visible to anyone holding a wildcard, is
retained in broker state, and cannot be changed without orphaning the topic. It
travels in the payload, where it is at least addressed to a subscriber rather than
published as an address.

`device_id` and `fleet_id` *are* in the topic, so treat them as public: use
`van-3`, not the registration plate.

### TLS, and the one setting that turns it off

| Configuration | Confidentiality | Authenticity |
| --- | --- | --- |
| TLS or WSS, no certificate pasted | yes | yes - verified against the Mozilla root bundle in flash |
| TLS or WSS, a CA certificate pasted | yes | yes - verified against that one root |
| **Skip certificate verification** | yes | **no** - anything on the path can present itself as your broker |
| TCP or WS | **no** | no |

The third row is reachable on purpose and labelled in red on the modal, in the API
response and here. The alternative is an operator with a self-signed broker turning
TLS off altogether, which is strictly worse. Use it to get a broker working, paste
that broker's CA, and turn it back on.

A pasted certificate is shape-checked before it is stored, and one check is a
security check rather than a convenience: **a private key in the CA box is refused**.
It would otherwise be stored, be useless, and leave the operator believing their
broker was authenticated.

### Credentials never come back out

The broker password and the Wi-Fi station password are write-only through the API:

- `GET /api/mqtt` returns `password_set: true` and **no password field at all** - not
  masked, not truncated, absent. The value that is never formatted is the value that
  cannot be logged by accident;
- the username comes back **masked** (`fl*********r`), because an operator has to be
  able to tell which account is configured without the value being readable over a
  shoulder or out of a screenshot. Below four characters nothing is kept: `a*b`
  discloses two thirds of `ab`;
- the device page's password boxes open **empty**, with a placeholder saying whether
  one is stored. An empty box submits nothing, which the firmware reads as "keep the
  stored one"; erasing takes an explicit Clear button;
- nothing is logged. `board_wifi.cpp` logs the SSID and never the passphrase,
  `mqtt_publisher.cpp` logs the URI and the masked username, and the error strings it
  publishes to the page are built from `esp_err_t` names and numeric codes only. That
  is a rule rather than a coincidence: `./plxy.sh monitor` puts the serial log on a
  screen in a room with other people in it, and it is what gets pasted into bug
  reports.

`tests/test_mqtt_config.py` checks the last point by searching the whole API document
for the secret rather than by inspecting named fields - a future field that happened
to include the password would pass a field-by-field check and fail that one.

### The fleet monitor is a page, not a service

The [fleet monitor](fleet-monitoring.md) in this documentation runs entirely in the
reader's browser. There is no backend, nothing is proxied, and no message reaches
this project's infrastructure. Two consequences worth being explicit about:

- **it never stores a password.** The host, port, path, topic and client id are kept
  in the browser's local storage so that a demonstration does not begin with
  retyping; the password is held in a variable for the life of the tab, the field is
  emptied on connect, and nothing writes it anywhere;
- **everything it receives is treated as hostile.** On a shared broker, anyone can
  publish anything to the topic being watched. Every string is length-capped and
  stripped of control characters, C1 codes and the bidirectional overrides that would
  otherwise let a remark reverse the rendering of the rest of the page; every number
  is clamped; a document that is not a `drowsyguard.alert.*` object is rejected and
  counted rather than rendered; and every value reaches the DOM through
  `textContent`. There is no `innerHTML` in `docs/assets/js/fleet.js`, and
  `tests/test_fleet_page.py` fails the build if one appears.

It also **never publishes**. The page subscribes and acknowledges; it sends no
commands to any device, which is the same decision the firmware makes in the other
direction - see below.

### The device does not subscribe

The firmware publishes and takes **no commands over MQTT**. There is no subscription,
no command topic, and nothing a broker can ask it to do. A drowsiness alarm a broker
can reconfigure - or mute - would put the mute button from the section above on the
whole internet, and on a shared broker that is not a threat model this project can
defend.

The unauthenticated HTTP mute is still there, and the AP password is still the only
control over it. MQTT does not widen that.

## The dashboard streams your webcam

`python -m drowsyguard.cli live` serves your webcam over HTTP with no
authentication. It binds to `127.0.0.1` for exactly that reason.

`--host 0.0.0.0` publishes your camera to the whole local network. Do it only on
a network you trust, and stop the process afterwards. FastAPI's `/docs` and
`/redoc` are disabled, but that is not a security control — the streams are the
exposure.

## The alerts are the only output the driver perceives

There is no screen. Anything that can silence the speaker permanently is a safety
defect rather than an annoyance, which is why the rate limits are per *channel*
rather than global:

| Channel | Reasons | Default limit | Why |
| --- | --- | --- | --- |
| Drowsiness | drowsy, microsleep, yawning, head nod | 30 s cooldown, 3 per episode, episode resets after 5 min quiet | A driver already pulling over should not be nagged. The 5-minute reset stops the cap becoming permanent for the rest of a long trip. |
| Sneeze | sneeze | 2 s cooldown, no cap | The behaviour analyzer already limits this to one per 2.5 s; the channel is a backstop. Capping it would go silent on a driver with a cold. |
| Presence | no driver | 5 s cooldown, no cap | The presence monitor fires exactly once per absence episode. "Nobody is driving" must never be a message the device has used up its allowance of. |

Before this split there was a single shared cooldown, and a sneeze acknowledgement
or a no-driver warning could be swallowed by a drowsiness cooldown that had nothing
to do with it. The symptom of that is silence, which is the hardest failure to
notice.

## Personal data

The camera points at a person's face, and the board **writes JPEG captures of
that face to the SD card** whenever an alert fires.

- Get consent from anyone recorded, and say what happens to the images.
- The card is not encrypted. Anyone holding the board holds the captures.
- `POST /api/events/clear` wipes them; do that before handing the board on.
- Datasets are never committed: `data/raw/`, `data/processed/`, model weights and
  the DDD corpus are all in `.gitignore`. Keep it that way.
- The DDD corpus has its own licence and consent terms. Do not redistribute it
  through this repository.

## Secrets and the repository

There are no runtime secrets **in the repository**, and there should not be any:

- The Wi-Fi AP password is a **compiled-in default**, not a secret, and is
  documented as such.
- Nothing in the build reads an API key or a token.
- The broker password, the broker username and the Wi-Fi station passphrase are
  genuine runtime secrets, and they live only in the device's NVS partition. They are
  never in a source file, never in `sdkconfig.defaults`, never returned by an API and
  never logged. NVS is **not encrypted** on this build, so anybody holding the board
  holds them - the same caveat as the SD-card captures above. Erase them with
  `./plxy.sh erase` before handing a board on.
- The documentation pipeline runs a **credential scan** over both the sources and
  the generated site, and fails the build on a hit — see
  [Documentation pipeline](operations/documentation.md#validation).

If you add a genuine secret, it belongs in a GitHub Actions secret or a local
untracked file, never in `board_wifi.h`, never in `docs/`, and never in a
generated page.

## Documentation deployment

The published site is built from public sources only and deployed with the
minimum GitHub Actions permissions (`contents: read`, `pages: write`,
`id-token: write`). It never builds or deploys the application, never touches
production infrastructure, and cannot publish a build that fails validation. The
full guarantees are in
[Documentation pipeline](operations/documentation.md#deployment-safety).

## Reporting a problem

Open an issue on
[the repository](https://github.com/SengPhirum/PLXY_DrowsyGuard/issues). Given
what this project is, please do not include images of anyone's face in the
report.
