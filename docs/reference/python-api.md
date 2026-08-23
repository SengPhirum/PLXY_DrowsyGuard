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
| `facedetect` | YuNet detection plus short-term tracking |
| `behavior` | multi-cue fusion: PERCLOS, long blinks, yawn, nod, sneeze suppression |
| `risk` | `RiskFilter` — the Python mirror of the firmware filter |
| `live` | `LiveEngine`: capture, inference and state for the dashboard |
| `server` | the FastAPI app |

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

Sneezes are detected in order to be **suppressed**, not scored: a sneeze slams
the eyes shut for about a second while the head jerks, which an eye-closure
detector would otherwise record as a microsleep.

## `facedetect` — why YuNet

OpenCV 5 removed `CascadeClassifier` and ships no bundled cascades, so Haar is
not an option. The YuNet ONNX file is fetched once by `drowsyguard fetch-models`.

Tracking is not bare per-frame detection: the box is EMA-smoothed and held for 15
frames when detection drops, because detectors lose the face at exactly the
moment of interest — eyes closing, head nodding.

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
| `make_voice_clips.py` | render the alert audio |

Regenerate every figure with `./plxy.sh diagrams`;
`tests/test_tutorial_diagrams.py` checks they stay in step with `pinmap.py`.
