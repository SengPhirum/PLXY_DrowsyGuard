# DrowsyGuard MCU

Low-cost, camera-based driver drowsiness detection research project designed for retrofit use in older vehicles.

**Documentation: <https://sengphirum.github.io/PLXY_DrowsyGuard/>** — getting
started, the hardware build, configuration, the device and dashboard APIs,
operations, security and troubleshooting, with search.

Build it locally without any of the firmware toolchain:

```bash
./plxy.sh docs-preview     # http://127.0.0.1:8001/  (hot reload)
./plxy.sh docs-check       # strict validation - what CI runs on a PR
```

## Target
- MCU: ESP32-S3-WROOM-1 **N16R8** (16 MB flash, 8 MB octal PSRAM)
- Camera: **OV3660** on the board's DVP/FPC connector
- Audio: **MAX98357A** I2S class-D amplifier + 4 ohm / 3 W speaker
- Interface: **no display.** The board serves a live MJPEG preview and all its
  telemetry over its own Wi-Fi access point — join `DrowsyGuard-XXXXXX` and open
  `http://192.168.4.1/`
- Runtime: ESP-IDF + ESP-DL
- Model: tiny grayscale CNN, INT8 quantized to `.espdl`
- Safety logic: temporal smoothing + sustained-risk trigger + cooldown

## Hardware setup

For the complete assembly, wiring, installation and testing guide — starting from
unopened parts and ending at a working system — see:

**[Hardware Setup Tutorial](./docs/tutorials/hardware-setup/README.md)**

It covers all four purchased components, a full wiring table, eleven labelled
diagrams, first power-on, per-component tests and troubleshooting.
[`docs/HARDWARE_SETUP.md`](./docs/HARDWARE_SETUP.md) is the shorter reference for
the toolchain and the ESP-DL binding stages.

The whole build is **seven wires**: five to the amplifier and two to the speaker.
The camera is a ribbon and the preview is a web page, so neither needs any.

## Firmware dev loop

`plxy.sh` wraps everything the board needs. It works around two things that bite
on this hardware: ESP-IDF cannot run under Git Bash at all (it refuses on
`MSYSTEM`), and this board's UART bridge cannot put the chip into download mode
by itself, so `flash` detects that and tells you to press BOOT once.

```bash
./plxy.sh dev        # build, flash, monitor
./plxy.sh wifi       # how to reach the preview from a phone
./plxy.sh watch      # live risk/PERCLOS/fps from the device API
./plxy.sh doctor     # toolchain, port and device check
./plxy.sh help       # everything else
```

## Workflow
```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -e .               # runtime deps + the `drowsyguard` command

drowsyguard doctor
drowsyguard prepare --input data/raw --output data/processed
drowsyguard train --config configs/train.yaml
drowsyguard evaluate --config configs/train.yaml --checkpoint models/best.pt
drowsyguard export-onnx --config configs/train.yaml --checkpoint models/best.pt
drowsyguard quantize-espdl --onnx models/drowsyguard.onnx --calib data/processed/train --output models/drowsyguard.espdl
```

## Detection approach
Risk is measured from **eyelid closure over time**, not from a whole-face "does this
look drowsy" classifier. A face classifier trained on DDD learned to recognise
*drivers* rather than drowsiness (see `PROJECT_STATE.md`), whereas eye closure is the
mechanism itself. It is also cheaper on device: the model input is a 32x32 eye patch.

```
camera -> YuNet face + 5 landmarks -> crop both eyes (32x32)
       -> eye-state model: P(closed) per eye        -> PERCLOS
       -> face geometry: jaw drop, head pitch, roll -> yawn / nod
       -> behaviour fusion (+ sneeze suppression)   -> risk score
       -> RiskFilter (sustained + cooldown)         -> alert
```

### Behaviours, not just eye closure
Eye closure alone is not drowsiness, so the risk score fuses four cues
(`src/drowsyguard/behavior.py`):

| cue | weight | how it is measured |
| --- | --- | --- |
| PERCLOS | 0.55 | fraction of recent frames with eyes closed |
| long/slow blinks | 0.20 | closures over 0.4 s, and microsleeps over 1.0 s |
| yawning | 0.15 | jaw drop held above the driver's baseline for 1.2 s |
| head nodding | 0.10 | downward pitch excursion returning within 1.5 s |

The geometric cues come from the five landmarks already available, so they add **no
model weight** and stay affordable on an ESP32-S3. Each is measured against a rolling
**per-driver baseline**, so face shape and camera angle cancel out rather than becoming
signal — the same mistake that sank the whole-face classifier.

**Sneezes are detected but are not a drowsiness cue.** A sneeze slams the eyes shut for
about a second while the head jerks, which an eye-closure detector would score as a
microsleep. Detecting it (short closure + pronounced jaw movement) lets the system
*suppress* that false alert instead of counting it. Distinguishing involuntary events
from drowsiness is the point; classifying the sneeze itself is incidental.

Timing thresholds are literature-informed defaults in `behavior.py`, not tuned on
labelled yawn/nod/sneeze video — this project has none yet, so treat the event
detectors as unvalidated on real drivers even though their logic is unit-tested against
synthetic traces. `yaw` is computed but not validated; it needs a real head-turn test.

Base eye model: `open-closed-eye-0001` from the OpenVINO Model Zoo (Intel,
Apache-2.0) — **11.3k parameters**, 0.0014 GFLOPs, 46 KB, so it is realistic for an
ESP32-S3 at INT8. Inference measured at ~0.9 ms per frame for both eyes.

> **Known limitation.** That model was trained on the MRL *infrared* eye dataset and
> does not transfer to DDD's visible-light crops, where the eye region is only ~45 px
> and blurry: separating DDD alert/drowsy with it reaches only AUC 0.62 against its
> claimed 95.84% in-domain. It is expected to behave much better on a sharp live
> webcam, and the project already plans IR illumination for night use, which matches
> the model's training domain. Fine-tuning it on visible-light eye-state labels is the
> open task.

## Live development dashboard
Real-time webcam testing in the browser, before any board exists:
```bash
pip install -e ".[live]"
python -m drowsyguard.cli fetch-models    # one-off: YuNet detector + eye-state model
python -m drowsyguard.cli live            # eye mode is the default; no checkpoint needed
# then open http://127.0.0.1:8000
```
The Eyes panel shows both eye crops, P(closed) per eye, and the PERCLOS bar. To run
the old whole-face classifier instead: `--mode face --checkpoint models/<file>.pt`.

### Face detection and tracking
The dashboard detects the face with OpenCV's YuNet and crops to it automatically, so
the model receives a face-filling input like its training data instead of a face in a
room. Note OpenCV 5 removed `CascadeClassifier` and ships no bundled cascades, so
Haar is not an option; YuNet is downloaded by `fetch-models`.

Tracking is not bare per-frame detection: the box is EMA-smoothed and **held for 15
frames** when detection drops. That hold matters here, because detectors tend to lose
the face at exactly the moment of interest — eyes closing, head nodding — and without
it the crop would jump back to the whole frame right then. The status pill reads
`face 0.94`, `face held`, or `no face`, and the overlay draws the box plus YuNet's five
landmarks (both eyes, nose, mouth corners).

Measured over 400 DDD images the detected face box is ~1.02x the image side, i.e. DDD
crops are extremely tight, so `--face-margin` defaults to 0: the detected box *is* the
training framing. Raising it moves the live input out of distribution.

Use `--no-face-detect` to fall back to the centre crop, in which case `--zoom` applies.
It shows the camera feed, the exact 64x64 grayscale tensor the model receives,
p(drowsy) over time, and the streak/cooldown state of the **same** decision logic
the firmware runs (`drowsyguard.risk.RiskFilter` mirrors `risk_filter.cpp`). The
trigger/required/cooldown sliders tune that logic live, and "Copy as C++" emits the
constructor line to paste into `firmware/esp32s3/main/risk_filter.h`.

Run without `--checkpoint` and the model is untrained: the camera and decision
path are real but the probabilities are meaningless, and the page says so.

Use `--source path/to/clip.mp4` to replay a recording at its native frame rate
instead of a webcam, which makes threshold comparisons repeatable.

With face detection on (the default) framing is automatic. `--zoom` only applies when
no face is found, or when detection is disabled:
```bash
python -m drowsyguard.cli live --checkpoint models/<your-checkpoint>.pt
python -m drowsyguard.cli live --no-face-detect --zoom 0.45
```

On Windows, prefer `python -m drowsyguard.cli live` over the installed
`drowsyguard live` console script: the launcher can throttle webcam capture to
~1 fps. Check with `python -m drowsyguard.cli camera-test`, which benchmarks each
capture backend; the dashboard also warns when capture is abnormally slow.

## Datasets

### Input layout
`prepare` expects subject directories, and each class directory may hold images,
videos, or both:
```
data/raw/subject_01/alert/*.png        data/raw/subject_02/alert/session.mp4
data/raw/subject_01/drowsy/*.png       data/raw/subject_02/drowsy/session.mp4
```
Videos are decoded to frames during `prepare`, so every frame of a clip stays with
its subject in exactly one split. Use `--stride N` to keep every Nth frame, since
consecutive frames are near-duplicates:
```bash
drowsyguard prepare --input data/raw --output data/processed --stride 5
```
`--link` hardlinks instead of copying, which matters for multi-GB datasets. Because
hardlinks share storage, the raw corpus, `data/raw` and `data/processed` cost one
copy of the bytes between them, and deleting one tree leaves the others intact.

Re-splitting into a non-empty output is refused: the previous split's files would
remain and place one subject in two splits. Pass `--overwrite` to replace it.

### Driver Drowsiness Dataset (DDD)
DDD ships as two flat class folders, but subject identity is recoverable: the
alphabetic filename prefix is the subject and case is the label, so `A0001.png`
in `Drowsy/` and `a0001.png` in `Non Drowsy/` are the same person. The importer
rebuilds the subject layout so splits stay subject-independent:
```bash
drowsyguard import-ddd --input "Driver Drowsiness Dataset (DDD)" --output data/raw
drowsyguard prepare --input data/raw --output data/processed --link
drowsyguard train --config configs/train_ddd.yaml
```
This yields 28 subjects / 41,793 images. Subjects `F` and `T` have drowsy frames
only. **Do not** train on the raw `Drowsy` / `Non Drowsy` folders directly: a
random split over them puts the same face — and adjacent frames of one video — in
both train and test, which inflates accuracy and violates the thesis principle below.
This is why published DDD accuracies near 99% are usually not comparable to a
subject-independent number.

Inspect a checkpoint per driver, since an average hides drivers it fails on entirely:
```bash
python -m drowsyguard.cli evaluate --config configs/train_ddd.yaml \
    --checkpoint models/<your-checkpoint>.pt --per-subject
```

No trained drowsiness model ships with this repo. A `TinyDrowsyNet` trained from
scratch on DDD did not generalize across drivers, so model selection is open; see
`PROJECT_STATE.md`.

## Thesis principle
Use subject-independent train/validation/test splits. Never leak neighboring frames from the same driver across splits.

## Project memory
Read `PROJECT_STATE.md`, `ROADMAP.md`, `CHANGELOG.md`, and `docs/AI_HANDOFF.md` before major changes.

## Repository layout
```
src/drowsyguard/     desktop toolkit: dataset prep, training, export, live dashboard
firmware/esp32s3/    ESP-IDF application for the target board
  main/board_*.h     the only files that hold pin assignments
configs/             training configurations
scripts/             one-off tooling (ESP-DL quantization, tutorial diagrams)
tests/               pytest suite, including firmware/Python parity checks
docs/                the documentation site sources (mkdocs.yml at the root)
docs/prompts/        the task briefs this repository has been worked against
.github/workflows/   docs-only validation and GitHub Pages deployment
```

## Safety
Research prototype only; not a certified automotive safety device.
