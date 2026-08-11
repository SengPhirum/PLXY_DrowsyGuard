# Changelog

## 2026-08-11 — real hardware selected, firmware wired to it
- Board bought: **ESP32-S3-WROOM-1 N16R8 CAM + OV3660** (16 MB flash, 8 MB octal PSRAM)
  and a 1.8" **128x160 ST7735S** SPI panel. The board's DVP pin map turned out to be
  byte-for-byte the ESP32-S3-EYE map, cross-checked against keyestudio's pin table for
  the same board and arduino-esp32's `camera_pins.h`, so the frame budget and the
  ESP-DL model choice in `docs/FIRMWARE_PIPELINE.md` carry over unchanged.
- `main/board_camera.h`: the verified pin map, a 240x240 RGB565 `camera_config_t`, and
  driver-facing sensor tuning (hmirror, AGC/AEC on, brightness lifted for a backlit
  windscreen). Also records which GPIOs an N16R8 module makes unusable - 33-37 are
  flash/PSRAM, and reaching for them is the classic way to hang this board.
- `main/board_display.h/.cpp`: ST7735S bring-up over SPI2 and a chunked blit that
  byte-swaps RGB565 on the way out, since the panel latches high byte first. Pins
  chosen from what the camera leaves free: SCK 14, MOSI 21, CS 47, DC 41, RST 42.
- `main/main.cpp`: capture loop enabled end to end. It now runs **preview-only** when
  `model_init()` fails instead of returning, so the camera, panel, PSRAM and power
  supply can be validated before ESP-DL exists. A missing camera is drawn on screen
  rather than only logged.
- `model_adapter` reshaped to what the pipeline actually needs - `model_detect_face()`
  and `model_eye_closed_prob()` - replacing the stale whole-face
  `model_predict_drowsy(gray64x64)` left over from the abandoned classifier design.
- `display_ui` adapts to panels under 200 px wide: shorter PERCLOS label, computed
  right-alignment instead of hardcoded offsets, single-size banner text, and a shorter
  preview so all seven status rows still fit in 160 px.
- Added `sdkconfig.defaults`, `partitions.csv` (6 MB app for FLASH_RODATA models) and
  `main/idf_component.yml`.
- New `docs/HARDWARE_SETUP.md`: wiring tables, Windows toolchain install, which of the
  two USB-C ports to use, the three-stage bring-up, and a troubleshooting table keyed
  to the symptoms these two parts actually produce.
- Still not compiled or flashed: no board exists in this environment.

## 2026-08-10 — on-device pipeline, driver screen and reason-specific alerts
- Ported the behaviour logic to firmware: `behavior.h/.cpp` mirrors
  `src/drowsyguard/behavior.py` (geometry, rolling baselines, event state machines,
  PERCLOS, fusion). `tests/test_firmware_parity.py` parses the header and fails if any
  constant, the fusion weights or the `RiskFilter` defaults drift from Python — verified
  by injecting a deliberate change and watching it fail.
- Documented the frame budget in `docs/FIRMWARE_PIPELINE.md` using Espressif's published
  ESP-DL latencies: detector amortised to ~13 ms by running every 3rd frame with
  tracking in between, ~6 ms for both eyes, ~4 ms UI => ~23 ms/frame, 15-20 fps on S3.
- **ESP32-S2 ruled out**: no AI vector instructions and ESP-DL's face detection models
  support only S3/P4. Recommended ESP32-S3-EYE, which already has camera + 240x240 LCD
  + mic + 8 MB PSRAM.
- Caught a portability trap: ESP-DL's keypoint order (left eye, left mouth, nose, right
  eye, right mouth) differs from YuNet's. Added `behavior_from_espdl_keypoints()` and a
  test asserting the reorder table, since indexing it directly would silently corrupt
  every geometric cue.
- Added `display_ui.h/.cpp`: driver-facing RGB565 UI (no LVGL) with camera preview,
  tracked face box, eye state, PERCLOS bar, risk bar with the trigger marked, yawn/nod
  counts, named events including `SNEEZE IGNORED`, and an alert banner.
- `voice_alert` now takes an `AlertReason` (Drowsy / Microsleep / Yawning / HeadNod),
  each mapping to its own clip and banner text, plus `voice_alert_is_active()` for the UI.
- None of the firmware is compiled or flashed; no board exists in this environment.

## 2026-08-10 — multi-cue behaviour analysis
- Added `src/drowsyguard/behavior.py`: risk is now a fusion of PERCLOS (0.55),
  long/slow blinks (0.20), yawning (0.15) and head nodding (0.10) rather than eye
  closure alone. Yawn, nod and roll are derived geometrically from the five YuNet
  landmarks, so they add no model weight and remain ESP32-affordable.
- Every geometric cue is measured against a rolling per-driver baseline, so face shape
  and camera angle cancel out instead of becoming signal.
- Sneeze detection added as a **false-alarm suppressor**, not a drowsiness cue: a
  sneeze shuts the eyes for ~1 s with a head jerk and would otherwise be scored as a
  microsleep. A detected sneeze suppresses the behaviour contribution to risk.
- Validated the geometry against real faces: measured roll tracks applied rotation
  monotonically, jaw drop rises as the jaw opens (1.067 -> 1.228), and the pitch proxy
  responds while staying roll-stable. `yaw` is computed but NOT validated.
- Event thresholds are literature-informed defaults, not tuned on labelled
  yawn/nod/sneeze video, which the project does not yet have. Logic is unit-tested
  against synthetic traces (blink vs microsleep, speech vs yawn, nod vs sustained head
  drop, sneeze vs microsleep, one-event-per-occurrence).
- Dashboard shows mouth/head state, per-minute blink/yawn/nod rates, sneeze count and
  a behaviour event log.

## 2026-08-10 — eye-state + PERCLOS replaces whole-face classification
- Reworked detection to measure eyelid closure instead of classifying whole faces:
  YuNet eye landmarks -> 32x32 eye crops -> eye-state model -> PERCLOS -> RiskFilter
  (`src/drowsyguard/eyestate.py`). `live --mode eye` is now the default and needs no
  drowsiness checkpoint; `--mode face --checkpoint` keeps the old path.
- Base model: `open-closed-eye-0001` (OpenVINO Model Zoo, Intel, Apache-2.0), 11.3k
  parameters / 46 KB / 0.0014 GFLOPs — plausible for ESP32-S3 INT8, and ~0.9 ms per
  frame for both eyes here (about 10x faster than the 64x64 face CNN).
- Three corrections to that model's published contract, established empirically:
  input must be `(pixel-127)/255` (raw 0-255 overflows it to NaN); its output is
  already softmaxed; and its card claims `[open, closed]` but index 0 tracks *closed*.
- **Measured limitation:** the model does not transfer to DDD's visible-light, ~45 px
  eye crops — AUC 0.62 versus its claimed 95.84% in-domain. Tested BGR/RGB/grayscale/
  hist-eq/CLAHE/inverted inputs and four patch scales; none recovered the signal. It
  is IR-trained, so it should suit a sharp live camera and the planned IR illumination
  far better. Fine-tuning on visible-light eye labels is the open task.
- PERCLOS is the risk signal fed to the RiskFilter, so blinks cannot alert while
  sustained closure can; verified by unit test and by an open/blink/closed/open clip.
- Fixed `export-onnx`, which failed on a plain install because torch>=2.9 defaults to
  the dynamo exporter and needs the optional `onnxscript`; now falls back.

## 2026-08-10 — removed trained models; selecting a pretrained base
- Deleted all trained drowsiness checkpoints (`models/*.pt`) and the stale results
  document. The from-scratch `TinyDrowsyNet` did not generalize across drivers, so it
  is not a useful starting point. Kept only the YuNet face detector, which is
  detection infrastructure rather than a drowsiness model; also dropped the downloaded
  Haar cascade, unusable because OpenCV 5 removed `CascadeClassifier`.
- Model selection is open; a pretrained base from Hugging Face is being evaluated.
  The durable finding is retained in `PROJECT_STATE.md`: judge any candidate by
  per-driver accuracy on held-out subjects, never by an average.
- Added `evaluate --per-subject [--split]` so per-driver accuracy is a first-class
  measurement rather than an ad-hoc script.
- `export-onnx` now writes a `.preprocess.json` sidecar recording input size and
  normalization, and the dashboard reads it. An `.onnx` carries weights but not
  preprocessing, so without this a model trained on standardized input is silently
  served raw input.

## 2026-08-10 — face detection and tracking; anti-bias training
- Added YuNet face detection with tracking to the live dashboard
  (`src/drowsyguard/facedetect.py`, `drowsyguard fetch-models`). The crop now follows
  the face automatically; previously the dashboard used a fixed centre crop and a
  manual zoom, which fed a face-in-a-room to a model trained on tight face crops.
  Verified 60/60 detection on known faces and 130/130 on frames of a test clip that
  contain a face. Note OpenCV 5 removed `CascadeClassifier` and bundles no cascades,
  so Haar is unavailable on this stack.
- Tracking holds the last box for 15 frames when detection drops, because detectors
  tend to lose the face precisely when the eyes close or the head nods.
- Measured the DDD framing rather than guessing it: the detected face box is ~1.02x
  the image side across 400 images, so `--face-margin` defaults to 0.
- Added `augment` and `normalize` training options targeting per-driver appearance
  bias; `normalize` is recorded in the checkpoint and reapplied automatically at
  inference, so a normalized model cannot be run on raw input.
- Overlay readouts moved outside the face box; added a face-status pill and a
  face-detection toggle to the dashboard.

## 2026-08-10 — DDD ingestion and video support
- `prepare` now accepts videos as well as images in each class directory and
  decodes them to frames (`--stride N` keeps every Nth frame). Frames of a clip
  always stay with their subject in one split.
- `prepare` gained `--link` (hardlink instead of copy) and now reports per-split
  subject and class counts instead of a bare dict.
- Added `drowsyguard import-ddd`. DDD ships as flat `Drowsy` / `Non Drowsy`
  folders, but subject identity survives in the filename: the alphabetic prefix is
  the subject and its case is the label, so `A0001.png` and `a0001.png` are the
  same person. The importer rebuilds the subject layout; without it a random split
  would put the same face and adjacent video frames in both train and test.
- Imported DDD: 28 subjects, 41,793 images. Subjects F and T are drowsy-only.
- Promoted `opencv-python` from the `live` extra to a core dependency, since
  video ingestion now needs it.
- `prepare` now refuses to write a new split over an existing one unless
  `--overwrite` is passed; the old files would otherwise survive and place a
  subject in two splits.
- Added `--zoom` to the live dashboard. DDD is tightly cropped faces, so feeding a
  full webcam frame to a DDD-trained model is out of distribution; zoom makes the
  live input match the training crop.
- Training progress now flushes, so redirected logs show epochs as they finish.
- Trained the first subject-independent DDD baseline (`configs/train_ddd.yaml`):
  val 0.8096, **test 0.5677 on 5 unseen drivers**. The model fitted the training
  drivers (train loss 0.004) but did not transfer; per-driver analysis showed it keyed
  on driver appearance. Checkpoint deleted in the entry above; kept here as the record
  of why from-scratch training on DDD was abandoned.
- Deleted the raw DDD corpus after import at the user's request; `data/raw` and
  `data/processed` retain all 41,793 images via hardlinks.

## 2026-08-10 — live development dashboard
- Fixed README workflow: it installed dependencies but never the package, so the
  `drowsyguard` command did not exist. Added `pip install -e .` and the Windows
  venv activation line.
- Added `drowsyguard live`: browser dashboard for real-time webcam testing
  (MJPEG feed, model-input view, p(drowsy) chart, streak/cooldown meters,
  alert log, and live trigger/required/cooldown tuning).
- Added `drowsyguard.risk.RiskFilter`, a Python mirror of the firmware filter, so
  thresholds tuned in the dashboard transfer to the device unchanged. Locked to
  the C++ semantics by `tests/test_risk.py`.
- Extracted `preprocess_gray` so training and live inference share one
  preprocessing path and cannot drift apart.
- Added `drowsyguard camera-test` and extended `doctor` with a live-UI check.
- Dashboard states plainly when no checkpoint is loaded; an untrained model's
  probabilities must not be read as detection.

## 2026-08-09 — v0.1.0 scaffold
- Defined MCU-first retrofit drowsiness research architecture.
- Added subject-independent dataset preparation.
- Added tiny grayscale CNN and training/evaluation CLI.
- Added ONNX export.
- Added guarded ESP-DL quantization adapter placeholder pending version pinning.
- Added ESP32-S3 firmware scaffold with temporal alert logic.
- Added roadmap, project state, thesis outline, and AI handoff protocol.
