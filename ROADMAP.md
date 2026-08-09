# Roadmap

## Goal
Build and scientifically evaluate an affordable retrofit driver-drowsiness warning device for older cars using an ESP32-S3-class microcontroller and camera.

## Phase 0 — Research definition
- [x] Define retrofit/low-cost objective
- [x] Choose MCU-first architecture
- [x] Require subject-independent evaluation
- [ ] Finalize thesis title and university proposal
- [ ] Ethics approval for collecting human-subject driving/drowsiness data

## Phase 1 — Dataset + baseline
- [x] Subject-based dataset layout
- [x] Dataset split CLI
- [x] Tiny CNN baseline
- [ ] Integrate NTHU-DDD/DROZY conversion scripts after confirming dataset licenses
- [ ] Collect local pilot dataset under safe, non-driving conditions or simulator

## Phase 2 — Training science
- [x] Train/evaluate CLI
- [x] ONNX export
- [ ] Add class weighting and augmentation
- [ ] Add ROC-AUC, sensitivity, specificity, latency and false-alarm/hour metrics
- [ ] Compare image-only vs temporal aggregation
- [ ] Perform ablation study

## Phase 3 — ESP32-S3 deployment
- [x] Firmware scaffold and temporal alarm logic
- [ ] Pin ESP-IDF + ESP-DL + ESP-PPQ versions
- [ ] Implement exact `.espdl` model adapter against pinned release
- [ ] Validate camera pin map on selected board
- [ ] Flash and profile RAM/latency/power
- [ ] Verify INT8 accuracy drop <= agreed threshold

## Phase 4 — Hardware prototype
- [ ] Camera mount for dashboard/windscreen
- [ ] IR illumination option for night driving
- [ ] Buzzer/vibration alert
- [ ] 12V-to-5V automotive-safe regulated power stage
- [ ] Enclosure and heat testing

## Phase 5 — Thesis experiments
- [ ] Day/night
- [ ] Eyeglasses
- [ ] Head pose variation
- [ ] Different subjects
- [ ] False-alarm study
- [ ] Cross-dataset generalization
- [ ] Statistical analysis and thesis figures

## Phase 6 — Release
- [ ] Reproducibility checklist
- [ ] Model card
- [ ] Dataset statement
- [ ] Safety disclaimer
- [ ] Demo video
