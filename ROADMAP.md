# Roadmap

## Goal
Build and scientifically evaluate an affordable retrofit driver-drowsiness warning device for older cars using an ESP32-S3-class microcontroller and camera, with understandable multilingual audible warnings.

## Phase 0 — Research definition
- [x] Define retrofit/low-cost objective
- [x] Choose MCU-first architecture
- [x] Require subject-independent evaluation
- [x] Select prerecorded multilingual voice-alert architecture
- [ ] Finalize thesis title and university proposal
- [ ] Ethics approval for collecting human-subject driving/drowsiness data

## Phase 1 — Dataset + baseline
- [x] Subject-based dataset layout
- [x] Dataset split CLI
- [x] Tiny CNN baseline
- [x] Driver Drowsiness Dataset (DDD) importer with subject recovery (28 subjects, 41,793 images)
- [x] Video and image ingestion in dataset preparation
- [ ] Integrate NTHU-DDD/DROZY conversion scripts after confirming dataset licenses
- [ ] Collect local pilot dataset under safe, non-driving conditions or simulator

## Phase 2 — Training science
- [x] Train/evaluate CLI
- [x] ONNX export
- [x] Live webcam dashboard for real-time development testing and threshold tuning
- [x] Per-driver evaluation on held-out subjects
- [x] Resolve accuracy-vs-ESP32 tension: chose eye-closure + behaviour cues over large
      face classifiers, which are 70-343 MB and cannot run on the target
- [x] Integrate pretrained eye-state base model (open-closed-eye-0001, 11.3k params)
- [x] Multi-cue behaviour analysis: PERCLOS, long blinks, yawning, head nodding
- [x] Reflex guard: a mouth-open closure must outlast REFLEX_MAX_S to count as a
      microsleep (replaced the sneeze detector, removed 2026-09-02)
- [ ] Fine-tune the eye model for visible light (IR-trained model transfers poorly)
- [ ] Tune and validate behaviour thresholds on labelled yawn/nod video
- [ ] Validate head yaw against a real head-turn recording
- [ ] Add class weighting and augmentation
- [ ] Add ROC-AUC, sensitivity, specificity, latency and false-alarm/hour metrics
- [ ] Compare image-only vs temporal aggregation
- [ ] Perform ablation study

## Phase 3 — ESP32-S3 deployment
- [x] Firmware scaffold and temporal alarm logic
- [x] Voice-alert controller interface and cooldown/repeat policy
- [x] Behaviour/PERCLOS logic ported to C++ with a Python-parity test
- [x] Frame budget established for ESP32-S3 (~23 ms/frame, 15-20 fps)
- [x] Board decision: ESP32-S3-EYE; ESP32-S2 ruled out (no ESP-DL/vector support)
- [x] Browser UI over SoftAP (MJPEG preview, face box, PERCLOS, risk, event log,
      mute, speaker self-test) - replaced the on-panel UI on 2026-08-23
- [x] Reason-specific spoken alerts (drowsy / microsleep / yawning / head nod)
- [x] Board bought: ESP32-S3-WROOM-1 N16R8 CAM + OV3660, EYE-compatible DVP pin map
- [x] Camera pin map, build config and partition table committed
- [x] ~~LCD panel init + blit~~ - removed with the panel; the preview is a web page
- [x] Capture loop wired end to end, with a preview-only bring-up mode
- [x] Setup/flash guide (`docs/HARDWARE_SETUP.md`)
- [x] Audio parts bought: MAX98357A amplifier (item 2724) + 4 ohm/3 W speaker (2554)
- [x] I2S audio driver implemented (`main/board_audio.h/.cpp`), pins 39/38/40
- [x] Alert playback moved off the capture loop onto its own FreeRTOS task
- [x] Reason-specific tone patterns, so the alert path is testable before speech exists
- [x] Beginner setup tutorial with generated wiring diagrams (`docs/tutorials/hardware-setup/`)
- [ ] **First build**: `idf.py build` has never been run; the firmware is uncompiled
- [ ] First flash: confirm 8 MB PSRAM, camera init, SoftAP up, preview in a browser,
      boot chime
- [ ] Measure the real eye-model latency on hardware (budget uses an estimate)
- [ ] Wire ESP-DL `human_face_detect` call sites in `model_adapter.cpp`
- [ ] Pin ESP-IDF + ESP-DL + ESP-PPQ versions from `dependencies.lock`
- [ ] Implement exact `.espdl` model adapter against pinned release
- [ ] Validate the I2S audio GPIO map (38/39/40) on the bench
- [ ] Measure detection-to-audio-start latency on hardware
- [ ] Embed and validate approved English/Khmer PCM recordings
- [ ] Flash and profile RAM/latency/power
- [ ] Verify INT8 accuracy drop <= agreed threshold
- [ ] Confirm dashboard-tuned trigger/required/cooldown on-device against the desktop run

## Phase 4 — Hardware prototype
- [x] MAX98357A I2S amplifier + 4 ohm/3 W speaker acquired and driven by firmware
- [x] MB102 breadboard for the shared 5 V / 3.3 V / ground rails
- [ ] Camera mount for dashboard/windscreen
- [ ] IR illumination option for night driving
- [ ] Solder the header strip to the amplifier module
- [ ] Buzzer/vibration fallback alert (buzzer not yet purchased)
- [ ] 12V-to-5V automotive-safe regulated power stage
- [ ] Enclosure and heat testing

## Phase 5 — Thesis experiments
- [ ] Day/night
- [ ] Eyeglasses
- [ ] Head pose variation
- [ ] Different subjects
- [ ] False-alarm study
- [ ] Detection-to-audio-start alert latency
- [ ] Audio subsystem memory/power impact
- [ ] Cross-dataset generalization
- [ ] Statistical analysis and thesis figures

## Phase 6 — Release
- [ ] Reproducibility checklist
- [ ] Model card
- [ ] Dataset statement
- [ ] Safety disclaimer
- [ ] Demo video
