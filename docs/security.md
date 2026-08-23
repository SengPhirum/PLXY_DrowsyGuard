---
title: Security and safety
---

# Security and safety

DrowsyGuard is a research prototype that carries a camera pointed at a person's
face, an open radio, and an unauthenticated HTTP API. None of that is an
accident, and none of it is safe to deploy unchanged.

## Safety first

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
