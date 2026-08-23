# Changelog

## 2026-08-23 — the panel is gone: live preview over Wi-Fi

The SPI display was removed from the build and replaced by a web page the board
serves itself. Join the access point it broadcasts, open `http://192.168.4.1/`, and
the phone or laptop in your hand is the display.

**Why.** The panel cost five GPIOs, a 150 KB PSRAM framebuffer, a per-frame software
blit and one managed driver component per panel variant (ST7735S, then ILI9341). In
exchange it showed 240x320 pixels of 8-pixel-tall text to one person sitting directly
in front of it. The browser shows the frame *and* the fused risk score with its
trigger, the PERCLOS window, per-eye closure probability, blink/yawn/nod rates, head
geometry, an event log and frame timing — at a size that can be read from the
passenger seat — and `GET /api/status` returns the same numbers as JSON, so the
hardware acceptance tests in `docs/DEPLOYMENT.md` can be scripted instead of read off
glass. The build went from **15 wires to 7**, and from 2 spare GPIOs to 7.

**Firmware — new.**
- `main/board_wifi.h/.cpp`: SoftAP bring-up. SSID is `DrowsyGuard-XXXXXX` (last three
  bytes of the AP MAC, so two boards on one bench stay distinguishable), WPA2 with
  `drowsyguard`, and `esp_wifi_set_ps(WIFI_PS_NONE)` because with power save on the
  MJPEG stream arrives in bursts a couple of hundred milliseconds apart, which looks
  exactly like a camera that cannot keep up. Optional AP+STA: fill in `WIFI_STA_SSID`
  and the device also joins a named network, which is how it becomes reachable from a
  development machine without leaving the lab Wi-Fi.
- `main/web_server.h/.cpp`: **two** `esp_http_server` instances — control on port 80,
  MJPEG stream on port 81. One instance serves one request at a time and a stream
  never ends, so a stream on port 80 would block the page and the API for as long as
  anyone watched. Consequence, documented rather than hidden: one live viewer at a
  time, with a still-image fallback on port 80 for everyone else.
- `main/web/index.html`: the page, linked into the binary as flash rodata
  (`EMBED_TXTFILES`) so it can never be out of step with the JSON it parses. The face
  box is drawn client-side in a canvas over the video rather than burned into the
  JPEG — the device keeps encoding pixels it already has, and the box stays crisp at
  any zoom. Automatic fallback to polled stills if the stream slot is taken.
- Endpoints: `/`, `/stream` (:81), `/api/snapshot`, `/api/status`, `/api/settings`
  (`?quality=`, `?fps=`, `?muted=`), `/api/alert-test` (`?reason=0..3`).

**Firmware — the frame handoff, which is the part that had to be right.**
`web_server_publish_frame()` copies one frame into one of two PSRAM snapshot buffers
and returns; it does no encoding, and it returns without copying at all when no
browser is connected. The ~20 ms JPEG encode runs in the stream task, pinned to core
1, on the buffer the capture loop is no longer writing to. With one consumer, two
buffers are provably enough: at most one can be held for encoding, which always
leaves one free to write into. A drowsiness detector that stutters because someone
opened a web page would be a bad trade.

**Firmware — removed.**
- `main/board_display.h/.cpp` and `main/display_ui.h/.cpp` (four files, ~830 lines).
- The `waveshare/esp_lcd_st7735` and `espressif/esp_lcd_ili9341` dependencies. Nothing
  was added in their place: Wi-Fi and `esp_http_server` ship with ESP-IDF, and the
  JPEG encoder (`fmt2jpg_cb`) comes with `esp32-camera`, which was already a
  dependency.
- `main.cpp`: the bring-up loop that cycled panel fills and test tones, the LCD pin
  diagnostics, the base64 frame dumper (`/api/snapshot` returns the actual frame the
  detector was handed, which is strictly better), and the 150 KB framebuffer
  allocation. The ESP-DL self-test over `test_frames.h` survives behind
  `#define MODEL_SELFTEST 0`.

**Firmware — a real bug found on the way.** `voice_alert`'s `max_repeat_count` was a
lifetime budget, not a per-episode one: after three announcements the device went
silent for the rest of the power cycle. With the panel gone the speaker is the only
output the driver perceives, so that is a safety defect rather than an annoyance. The
counter now resets after `repeat_reset_ms` (5 min) without an alert. Also added:
`voice_alert_set_muted()`, `voice_alert_count()` and `voice_alert_test()` — the last
one is what the page's **Test speaker** button calls, because with no display "no
alert fired" and "the amplifier is dead" would otherwise be indistinguishable.

**Build.** `sdkconfig.defaults` gained the esp32-camera web-server tuning
(`ESP_WIFI_STATIC_TX_BUFFER_NUM`, `LWIP_TCP_SND_BUF_DEFAULT`, `LWIP_MAX_SOCKETS`,
AMPDU) — with stock buffer counts an MJPEG stream stalls for hundreds of milliseconds
at a time. `sdkconfig` was deleted so it re-seeds from defaults. Note that TX buffers
are *static* here and that is forced, not chosen: ESP-IDF removes the dynamic option
whenever `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` is set, and the derived
`CONFIG_ESP_WIFI_TX_BUFFER_TYPE` is silently ignored if you set it directly — a trap
this project walked into once and now documents in `sdkconfig.defaults` itself.

**The firmware now builds.** `idf.py build` against ESP-IDF v5.5.5 completes with no
warnings from project code: 2.2 MB app, 65 % of the 6 MB partition free. This is the
first time anything in `firmware/` has been compiled — it still has never been run on
hardware.

**Tooling and diagrams.**
- `scripts/diagram_fonts.py` (new): the four DejaVu faces every diagram is drawn with
  are now looked up rather than hard-coded to `/usr/share/fonts/truetype/dejavu`, so
  the artwork can be regenerated on the same Windows machine the firmware is flashed
  from (`pip install matplotlib` supplies all four). Still DejaVu only, deliberately:
  every text width in these diagrams is measured to lay the drawing out, so swapping
  the metrics would move labels.
- `scripts/pinmap.py` no longer reads a display header; `FREED_BY_WEB_PREVIEW` records
  the five GPIOs that came back. All eleven reference figures, the three step diagrams
  and the one-page poster were regenerated. Figure 5 changed from "wiring the display,
  8 wires" to "joining the board's Wi-Fi, 0 wires" and now carries the HTTP surface,
  which is the nearest thing a headless build has to a connector pinout.
- `tests/test_tutorial_diagrams.py`: the display assertions are replaced by ones that
  matter now — that no artefact still configures the removed panel, that the page's
  hard-coded stream port matches `web_server.h`, that `WIFI_AP_PASSWORD` is a legal
  WPA2 key (8+ characters, or empty for a deliberately open network), and that the
  JPEG quality default is on `fmt2jpg`'s 1-100 scale rather than the sensor's inverted
  0-63 one. 92 tests pass.

**Documentation.** The tutorial, `docs/HARDWARE_SETUP.md`, `docs/FIRMWARE_PIPELINE.md`,
`docs/DEPLOYMENT.md`, `firmware/esp32s3/README.md`, `README.md`, `PROJECT_STATE.md`
and `ROADMAP.md` all follow. Two new acceptance tests were added: frame rate recorded
with and without a browser watching (the delta is the evidence the preview costs the
detector nothing it needs), and 20 stream open/close cycles with heap sampled after
each.

## 2026-08-13 — audio hardware wired, beginner setup tutorial, repo cleanup

**Hardware.** The remaining three parts of the order were identified and are now
first-class in the project: a **MAX98357A** I2S filterless class-D amplifier
(khmeres item 2724), a **4 ohm / 3 W** speaker (2554) and an **MB102** 830-point
breadboard (371). Together with the board (2991) and panel (1885) that is the whole
five-item bill of materials, $15.25.

**Firmware — the alert path is real.**
- New `main/board_audio.h/.cpp`: ESP-IDF v5 `i2s_std` bring-up on **BCLK 39,
  LRCLK 38, DIN 40** - the microSD pins, which are what the DVP camera and the octal
  PSRAM leave free. 16-bit stereo at 16 kHz, matching the recording format in
  `assets/audio/README.md`. Provides PCM playback, a ramped tone generator and a
  silence flush.
- Mono samples are written into **both** I2S slots on purpose. The MAX98357A's `SD`
  pin selects left / right / (L+R)/2 depending on what a given breakout pulls it to;
  duplicating the sample makes every non-shutdown variant sound identical, so the
  board revision stops mattering.
- `voice_alert.cpp`: playback moved onto its own FreeRTOS task behind a queue.
  Previously `voice_alert_trigger()` was called straight from the capture loop, whose
  frame budget is ~23 ms - inline playback would have dropped roughly twenty frames
  and frozen the preview at the exact moment the driver needed it.
- Each alert reason now plays a distinct tone pattern (microsleep highest and
  fastest), so the amplifier is testable on the bench before any speech is recorded.
  This replaces a `TODO(HW)` that only wrote a log line.
- Fixed the buzzer fallback: `buzzer_pulse()` set the GPIO high and immediately low
  with no delay in between, so the "fallback alert" was silent. It now holds for
  120 ms.
- `main.cpp` plays an 880 Hz chirp at boot. It costs 120 ms and it is the only way to
  distinguish an amplifier that is wired but silent from one that never initialized.

**Documentation — `docs/tutorials/hardware-setup/`.**
- A complete beginner tutorial: what is being built, the five parts, required
  software, component identification, power architecture, a 15-row wiring table,
  toolchain install, project configuration, first power-on, per-component tests, a
  full system test, and a troubleshooting section split by subsystem.
- **Eleven generated wiring diagrams**, produced by
  `scripts/generate_tutorial_diagrams.py` from the same constants the firmware uses.
  `tests/test_tutorial_diagrams.py` fails if a drawing and a firmware header ever
  disagree, so the images cannot silently drift.
- The diagrams deliberately do **not** draw the physical top-to-bottom order of the
  board's header pins: it varies between batches of this board and could not be
  verified against a datasheet. Every connection is keyed to the printed silkscreen
  label instead.
- Recorded that the item-1885 listing calls the panel an "OLED" when it is a TFT LCD
  (its own silkscreen reads `RGB_TFT`), which is why it needs the `BLK` backlight pin.

**Cleanup.**
- `eyestate.py`: `max(face_box[2], face_box[2])` took the maximum of one value with
  itself - a leftover from when the box was `(x, y, w, h)`. Simplified to the single
  side, which is what `FaceTracker` actually returns.
- `.vscode/settings.json` pointed `cmake.sourceDirectory` at an absolute
  `E:/Personal/Project/...` path, so it was wrong for every other machine. Now
  `${workspaceFolder}`-relative.
- `docs/HARDWARE_SETUP.md` had the same absolute path hardcoded in a build command.
- `requirements.txt` duplicated the dependency list in `pyproject.toml`; it now
  defers to it, so the two cannot drift.
- Root `README.md` still advertised "OV2640/OV5640 class camera" as the target; the
  actual hardware is an OV3660. Added the target display and audio parts, a link to
  the new tutorial, and a repository layout section.
- `docs/VOICE_ALERT_HARDWARE.md` still said "OV2640 camera for the initial prototype"
  and left the I2S GPIO numbers to be decided later. Both are now settled.
- Stored the task brief this work was done against in `docs/prompts/`.

**Not done.** The firmware still has never been compiled - no ESP-IDF toolchain has
existed in any environment this project has been worked in, so `idf.py build` remains
unrun and the I2S code is reviewed-but-unbuilt. See `PROJECT_STATE.md` gap 1.

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
