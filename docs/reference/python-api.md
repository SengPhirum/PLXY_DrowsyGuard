---
title: Python modules
---

# Python modules

`src/drowsyguard/` — installed by `pip install -e .`. Each module owns one stage,
and the ownership matters: it is what keeps the desktop and the firmware from
drifting apart.

| Module | Owns |
| --- | --- |
| `cli` | argument parsing and dispatch — no logic of its own |
| `data` | `prepare_dataset()`: subject-independent splits, video decoding, hardlinking |
| `ingest` | dataset importers; `import_ddd()` rebuilds DDD's flat folders into the subject layout |
| `model` | `TinyDrowsyNet`, the whole-face classifier |
| `train` | `train_model()`, `evaluate_checkpoint()` |
| `export` | `export_onnx()`, `quantize_espdl()` |
| `eyestate` | eye-state classification and PERCLOS — the drowsiness mechanism |
| `facedetect` | YuNet detection, gated and tracked |
| `facegate` | which detections to believe, and when a sequence of them is a driver |
| `presence` | is anyone there, and — separately — can the device tell |
| `behavior` | multi-cue fusion: PERCLOS, long blinks, yawn, nod |
| `alerts` | `AlertReason` and its channels, mirrored from `voice_alert.h` |
| `risk` | `RiskFilter` — the Python mirror of the firmware filter |
| `live` | `LiveEngine`: capture, inference and state for the dashboard |
| `server` | the FastAPI app |

## The mirrors

Four modules exist because the device has the same logic and the dashboard is where
it gets tuned. A threshold tuned against different logic is worse than no tuning at
all: it produces confident numbers about the wrong system.

| Python | Firmware | How the equality is enforced |
| --- | --- | --- |
| `risk` | `risk_filter.h` | `tests/test_firmware_parity.py` parses the C++ constructor signature |
| `behavior` | `behavior.h` | the same test compares every constant, and fails if a new one is added to only one side |
| `facegate` | `face_gate.{h,cpp}` | `tests/test_facegate_parity.py` compiles the C++ and drives **both** through the same 400-candidate sweep and eleven detection sequences, requiring the same verdict, reject reason and presence decision at every step |
| `presence` | `presence.{h,cpp}` | `tests/test_presence.py` does the same with eight timelines, comparing state, timers and alert edges frame by frame |

The last two are a stronger guarantee than matching constants, and deliberately so:
these decisions are ordered (which check is reported first), stateful (how many
detections have agreed, how many missed) and full of boundary cases. Every one of
those is a place a transcription can drift without a single number changing.

## The three that carry the design

### `risk` — the mirror

`drowsyguard.risk.RiskFilter` is kept behaviourally identical to
`firmware/esp32s3/main/risk_filter.cpp` so that thresholds tuned in the dashboard
transfer to the device unchanged.

`tests/test_firmware_parity.py` parses the C++ constructor signature and compares
the defaults. **If you change one, change both** — `./plxy.sh test` will tell you
if you did not.

```python
from drowsyguard.risk import RiskFilter, DEFAULT_TRIGGER, DEFAULT_REQUIRED, DEFAULT_COOLDOWN

f = RiskFilter(DEFAULT_TRIGGER, DEFAULT_REQUIRED, DEFAULT_COOLDOWN)
alert = f.update(0.81)      # True only after `required` consecutive frames
```

### `eyestate` — the mechanism

Instead of asking a CNN "does this face look drowsy" — which learns who the
driver is — this measures eyelid closure directly and integrates it over time.
That is the drowsiness mechanism itself, and it is what makes a 32×32 input
sufficient.

### `behavior` — the fusion

Eye closure alone is not drowsiness. This module adds the behaviours that
accompany it — yawning, long/slow blinks, head nodding — and fuses them into one
score, with every cue measured against a rolling **per-driver baseline** so face
shape and camera angle cancel out instead of becoming signal.

A closure that began with the mouth flung wide open has to outlast `REFLEX_MAX_S`
before it counts as a microsleep, rather than the ordinary `MICROSLEEP_MIN_S`. That is
a false-alarm guard rather than a feature: an involuntary reflex shuts the eyes and
opens the mouth in one movement, which duration alone cannot tell from a microsleep,
and a reflex resolves inside that window. `REFLEX_JAW_DELTA` sets how wide the mouth
has to be for the longer wait to apply — higher than `JAW_OPEN_DELTA`, so an ordinary
slack jaw buys no extra grace.

## `facegate` — what to believe

`facedetect` used to take the highest-scoring box YuNet returned and believe it. That
is fine when the only thing in frame is a face and wrong in every other case: a hand,
a headrest, a phone or a patch of low-light noise all produce boxes, and one believed
frame is enough to start feeding a rolling baseline and a PERCLOS window with
measurements of something that is not a person.

Two layers, and keeping them apart is the design:

- `check()` is **static** — one candidate on its own. Confidence, box size and shape,
  then the five landmarks. It is cheap, it is order-dependent (the first failure is
  the one reported, by name), and it cannot see time.
- `FaceTrack` is **temporal**. Consecutive detections must agree before a driver is
  present; a candidate that has moved further than a head can move between detections
  is refused; the last box is held briefly when the detector misses; and a new track
  never inherits the old one's confirmation. That last property is what stops a track
  sliding off the driver and onto a passenger.

```python
from drowsyguard.facegate import Box, FaceTrack, check

why, geom = check(landmarks, Box(x, y, w, h, True), score, frame_w, frame_h)
if why.value != 'ok':
    print('refused:', why.value)     # e.g. 'box-not-head-shaped'
```

## `presence` — is anyone there

The module that answers "no driver" without ever confusing it with "no camera". It
takes a health argument alongside the presence boolean, debounces in both directions,
and returns an **edge** — true on exactly the update that announces an absence — so
the caller needs no de-duplication of its own.

```python
from drowsyguard.presence import PipelineHealth, PresenceMonitor

mon = PresenceMonitor()
r = mon.update(driver_present, PipelineHealth.OK, dt_s)
if r.alert:
    announce()          # exactly once per absence episode
```

## `facedetect` — why YuNet

OpenCV 5 removed `CascadeClassifier` and ships no bundled cascades, so Haar is
not an option. The YuNet ONNX file is fetched once by `drowsyguard fetch-models`.

Tracking is not bare per-frame detection: every candidate goes through `facegate`,
the accepted box is EMA-smoothed, and it is held for `FACE_HOLD_DETECTIONS` attempts
when detection drops — because detectors lose the face at exactly the moment of
interest, eyes closing and head nodding.

The raw detector is injectable (`FaceTracker(detect_fn=...)`), which is what makes
the gate testable: a hand for two seconds, an empty frame, a driver occluded for
exactly the length of the hold, a passenger appearing mid-track, twelve frames of
low-confidence detections. None of those can be produced reliably in front of a
webcam, and a test that depends on producing them is a test that gets deleted.

## Scripts

`scripts/` holds one-off tooling that is not part of the package:

| Script | Does |
| --- | --- |
| `export_eye_model.py` | export `open-closed-eye-0001` for the device |
| `quantize_espdl.py` | ESP-DL quantization |
| `generate_tutorial_diagrams.py` | the eleven tutorial figures |
| `generate_step_diagrams.py` | the step-by-step figures |
| `generate_wiring_poster.py` | the one-page wiring poster |
| `diagram_kit.py`, `diagram_fonts.py`, `pinmap.py` | shared drawing helpers and the pin table the diagrams read |
| `board_reset.py` | drive the board into download mode |
| `make_voice_clips.py` | render the alert audio (six reasons × two languages) |
| `make_manifest.py` | turn a firmware build into an ESP Web Tools manifest, merged image and offset record for the [browser installer](../getting-started/install-esp32.md) |

Regenerate every figure with `./plxy.sh diagrams`;
`tests/test_tutorial_diagrams.py` checks they stay in step with `pinmap.py`.
