---
title: Security and safety
---

# Security and safety

DrowsyGuard is a research prototype that carries a camera pointed at a person's
face, an open radio, and an unauthenticated HTTP API. None of that is an
accident, and none of it is safe to deploy unchanged.

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

That last one matters: **an unauthenticated client can silence the alarm.** Treat
the AP password as the only access control there is, and change it.

In station mode the board is exposed to everything on that network instead. Only
join networks you control.

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

There are no runtime secrets in this project, and there should not be any:

- The Wi-Fi AP password is a **compiled-in default**, not a secret, and is
  documented as such.
- Nothing in the build reads an API key or a token.
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
