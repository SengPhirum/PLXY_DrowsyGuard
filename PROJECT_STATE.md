# Project State

Last updated: 2026-08-10
Status: Scaffold and live dashboard complete. Detection reworked from whole-face
classification to multi-cue behaviour analysis (PERCLOS + long blinks + yawn + nod,
with sneeze suppression) after the face CNN was found to key on driver identity. The
eye-state base model is integrated but not yet accurate in visible light, and the
behaviour thresholds are untuned; hardware validation pending.

## Locked design decisions
- Target platform: ESP32-S3 with PSRAM and camera. **ESP32-S2 is ruled out**: it has no
  AI vector instructions and ESP-DL's face detection models do not support it, so the
  detector + two eye inferences per frame is not achievable. Recommended board is
  ESP32-S3-EYE (camera + 240x240 LCD + mic + 8 MB PSRAM). See `docs/FIRMWARE_PIPELINE.md`.
- Device frame budget: detect the face every 3rd frame and track between, run eye state
  every frame, target 15 fps (~23 ms/frame). 15 fps is sufficient because PERCLOS needs
  temporal coverage, not frame rate.
- Landmark order differs between YuNet (desktop) and ESP-DL (device); the firmware must
  reorder via `behavior_from_espdl_keypoints()`. Guarded by `tests/test_firmware_parity.py`.
- The driver sees the detection on-device: preview, face box, eye state, PERCLOS, risk
  bar with the trigger marked, named events, and a reason-specific alert banner.
- Spoken alerts name their cause (`Drowsy`, `Microsleep`, `Yawning`, `HeadNod`), because a
  named warning is more actionable than a chime.
- Input: fixed driver-facing grayscale crop, 64x64.
- Detection mechanism: multi-cue behaviour analysis, not whole-face drowsy/alert
  classification. Whole-face classification on DDD learned driver identity. Risk fuses
  PERCLOS (0.55), long/slow blinks (0.20), yawning (0.15) and head nodding (0.10).
  Yawn/nod/roll are geometric from the five YuNet landmarks, so they cost no extra model.
- Sneeze detection exists to SUPPRESS false alerts, not as a drowsiness cue: a sneeze
  closes the eyes ~1 s with a head jerk and would otherwise read as a microsleep.
- All geometric cues are measured against a rolling per-driver baseline, deliberately,
  so anatomy and camera angle cannot become signal the way driver appearance did.
- Base eye model: `open-closed-eye-0001` (OpenVINO Model Zoo, Intel, Apache-2.0),
  11.3k params / 46 KB. Its published card is wrong in three ways, all handled in
  `eyestate.py`: input is `(pixel-127)/255`, output is pre-softmaxed, and index 0 is
  *closed* despite the card saying `[open, closed]`.
- Runtime decision logic: temporal risk accumulation rather than single-frame alarm.
- Evaluation: subject-independent splits only.
- Product intent: low-cost retrofit aid for older cars without built-in driver monitoring.
- Alert decision logic exists twice on purpose: `firmware/.../risk_filter.cpp` for the
  device and `src/drowsyguard/risk.py` for desktop testing. They must stay behaviourally
  identical; `tests/test_risk.py` guards the Python side.
- Training and live inference share one preprocessing function (`data.preprocess_gray`).
- DDD subject recovery: in the Kaggle Driver Drowsiness Dataset the alphabetic
  filename prefix identifies the subject and its case identifies the label
  (`A0001.png` in `Drowsy/` and `a0001.png` in `Non Drowsy/` are one person,
  verified visually across B/C/D/E/G/H/I/N/X/ZA/ZB/ZC). Splitting DDD without this
  mapping leaks subjects and adjacent video frames across train/test.
- Video inputs are decoded to frames at `prepare` time, not at training time, so
  that split assignment stays per-subject.

## Known gaps
1. No physical ESP32-S3 board has been flashed in this environment.
2. ESP-PPQ export API must be pinned to a specific version before model conversion code can be finalized.
3. Camera pin configuration depends on the exact development board selected.
4. Night performance likely requires an IR-capable sensor/illumination design.
5. Real-road drowsiness data collection requires careful ethics/safety planning.
6. The eye-state base model is IR-trained and does not transfer to DDD's visible-light
   ~45 px eye crops: AUC 0.62 vs its claimed 95.84% in-domain. Input-space fixes
   (grayscale, hist-eq, CLAHE, inversion, four patch scales) did not recover it.
   Open task: fine-tune it on visible-light eye-state labels, or pair it with the
   planned IR illumination, which matches its training domain. Not yet validated on a
   live camera with a real person - no human was available in this environment.
7. Behaviour event thresholds (yawn 1.2 s, microsleep 1.0 s, nod 1.5 s, sneeze 1.2 s +
   jaw delta) are literature-informed defaults. Their logic is unit-tested on synthetic
   traces but none are tuned or validated on labelled yawn/nod/sneeze video, which the
   project does not have. `yaw` is computed but unvalidated - needs a head-turn test.
8. No whole-face drowsiness checkpoint ships with the repo; all were removed on
   2026-08-10. Only fetched detectors live under `models/detectors/` (not tracked).
   Durable lesson worth keeping: `TinyDrowsyNet` trained from scratch on DDD reached
   ~0.81 validation but only ~0.57 on unseen drivers, and per-driver results showed it
   keyed on driver appearance rather than eyelid state. Judge any replacement by
   per-driver accuracy on held-out subjects, never by an average.
9. The DDD corpus was deleted after import at the user's request. `data/raw` and
   `data/processed` retain all 41,793 images; re-importing needs a fresh download.
10. On this Windows machine the installed `drowsyguard` console script throttles
   webcam capture to ~1 fps (both MSMF and DSHOW); `python -m drowsyguard.cli`
   runs the identical code at 30 fps. Root cause in the launcher is unresolved.

## Next best action
Model: get eye-state labels in the target (visible-light) domain and fine-tune the
11.3k-parameter base model on them, splitting by subject. MRL Eye ships subject IDs in
its filenames but is infrared and needs Kaggle credentials, which are not configured
here. Confirm any result per-driver on held-out subjects, and note for the write-up
that the highest-accuracy public drowsiness models (70-343 MB, 224x224) cannot run on
an ESP32-S3, which is why the eye-closure route was chosen.
Hardware: select one exact board (recommended: ESP32-S3-EYE-compatible board with PSRAM, or another supported ESP32-S3 camera board), then pin ESP-IDF/ESP-DL versions and complete hardware-in-the-loop validation.
