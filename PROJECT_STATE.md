# Project State

Last updated: 2026-08-23
Status: Scaffold and live dashboard complete. Detection reworked from whole-face
classification to multi-cue behaviour analysis (PERCLOS + long blinks + yawn + nod,
with sneeze suppression) after the face CNN was found to key on driver identity. The
eye-state base model is integrated but not yet accurate in visible light, and the
behaviour thresholds are untuned. The SPI panel was removed on 2026-08-23: the
firmware is headless and serves its preview and telemetry to a browser over its own
Wi-Fi access point. **First hardware run the same day**: flashed to the board with
MAC `80:b5:4e:c5:e0:18` and it came up clean - 8 MB PSRAM, camera at 240x240 RGB565,
I2S chime, `SoftAP "DrowsyGuard-C5E019"` on 192.168.4.1, face detector loaded, and
**19.7 fps** with no viewer attached. The browser preview itself, the alert path and
everything downstream of the (still unbound) eye model remain unverified.

## Locked design decisions
- Target platform: ESP32-S3 with PSRAM and camera. **ESP32-S2 is ruled out**: it has no
  AI vector instructions and ESP-DL's face detection models do not support it, so the
  detector + two eye inferences per frame is not achievable. See
  `docs/FIRMWARE_PIPELINE.md`.
- **Hardware bought 2026-08-11** ($11.75 of it still in the build):
  ESP32-S3-WROOM-1 **N16R8** CAM board with an **OV3660** (item 2991), a
  **MAX98357A** I2S class-D amplifier (2724), a **4 ohm / 3 W** speaker (2554) and
  an **MB102** breadboard (371). The board's DVP camera pin map is byte-for-byte
  the ESP32-S3-EYE map, so ESP-DL's vision examples and the published frame budget
  carry over unchanged. Toolchain and bring-up stages: `docs/HARDWARE_SETUP.md`;
  full beginner walkthrough with diagrams: `docs/tutorials/hardware-setup/`.
- **No display, by decision (2026-08-23).** The 1.8" ST7735S (item 1885), later a
  2.8" ILI9341, was dropped in favour of serving the preview over the board's own
  SoftAP. It cost five GPIOs, a 150 KB PSRAM framebuffer, a per-frame software
  blit and a managed component per panel variant, and showed 240x320 of 8-pixel
  text to one person sitting in front of it. The browser shows strictly more —
  risk, trigger, PERCLOS, per-eye closure, event rates, head geometry, an event
  log, frame timing — on a readable screen, and `GET /api/status` makes the same
  numbers scriptable for the acceptance tests. The panel hardware is not needed;
  anyone who bought one can keep it for another project.
- **The preview must never be load-bearing.** `web_server_publish_frame()` copies
  one frame and returns, and skips even the copy when no browser is connected;
  JPEG encoding happens in the stream task on core 1. The alert path does not
  touch the network, so a Wi-Fi failure degrades diagnostics, never safety.
- **GPIO budget, after the panel came out.** Camera 4-13/15-18, flash+PSRAM
  33-37, USB 19/20, console 43/44, I2S audio 38/39/40, buzzer 2. **GPIO 1, 3, 14,
  21, 41, 42 and 47 are free** — the last five were the panel's. Wi-Fi costs none:
  the radio is on-die.
- **Audio output is mono duplicated into both I2S slots**, deliberately. The MAX98357A's
  `SD` pin selects left / right / (L+R)/2 depending on what a given breakout pulls it
  to, and duplicating the sample makes every non-shutdown variant behave identically.
- **Alert playback runs on its own FreeRTOS task.** The capture loop has a ~23 ms frame
  budget, so inline playback would drop roughly twenty frames and stall the preview at
  the exact moment there is something worth looking at.
- **The alert repeat cap is per episode, not per power cycle.** Three announcements,
  then silence until five minutes have passed without one (`repeat_reset_ms`). With
  the panel gone the speaker is the only thing the driver perceives, so an alarm
  that goes permanently quiet after three events on a long drive would be the one
  failure mode this device cannot have.
- Device frame budget: detect the face every 3rd frame and track between, run eye state
  every frame, target 15 fps (~23 ms/frame). 15 fps is sufficient because PERCLOS needs
  temporal coverage, not frame rate.
- Landmark order differs between YuNet (desktop) and ESP-DL (device); the firmware must
  reorder via `behavior_from_espdl_keypoints()`. Guarded by `tests/test_firmware_parity.py`.
- The detection is visible in a browser, not on glass: MJPEG preview with a
  client-side face box, risk with the trigger marked, PERCLOS, per-eye closure,
  event rates, head geometry, an event log, and a reason-specific banner — plus a
  mute switch and a speaker self-test, because with no panel "no alert fired" and
  "the amplifier is dead" would otherwise be indistinguishable.
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
1. Runtime verification is partial. As of 2026-08-23 the firmware builds clean
   against ESP-IDF v5.5.5 (2.2 MB app, 65 % of the 6 MB partition free) **and boots
   on hardware**: PSRAM, camera, I2S, SoftAP, HTTP server and the face detector all
   initialise, at 19.7 fps. Not yet exercised: the preview from an actual browser
   (frame rate with a viewer attached, the single-stream fallback, heap over hours),
   an alert firing end to end, and the eye model, which is still unbound. Two board
   quirks worth knowing before touching hardware are in the firmware README - the
   sensor is an OV5640 rather than the advertised OV3660, and the UART bridge cannot
   reach download mode without the BOOT button.
2. ESP-PPQ export API must be pinned to a specific version before model conversion code can be finalized.
3. Camera pin map is settled (ESP32-S3-EYE-compatible, in `main/board_camera.h`) but
   unverified on the bench. The one remaining format unknown is the RGB565 byte
   order into the JPEG encoder: if the preview comes out with red and blue swapped,
   set `CAM_RGB565_BYTE_SWAP` to 0. The panel-specific unknowns (BGR element order,
   window offset, inversion) went away with the panel.
   Untested in the web path: whether the single-viewer MJPEG limit is acceptable in
   practice, and whether the still-image fallback for extra viewers behaves on a
   real phone browser.
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
Hardware: the board is flashed and running; `./plxy.sh` drives the loop. What is
left is to point it at a face - join `DrowsyGuard-C5E019`, open 192.168.4.1, and
check that the face box tracks and that `fps` holds up with the stream open. Then
bind the eye model, which is the only thing standing between this and a working
alarm. Original walkthrough, still accurate for a fresh board: follow
`docs/tutorials/hardware-setup/README.md` end to end - install ESP-IDF, solder the
amplifier's header strip, wire the seven connections, flash, and confirm four
things in one boot: 8 MB PSRAM in the log, three rising notes from the speaker,
`DrowsyGuard-XXXXXX` in the Wi-Fi list, and a live preview at 192.168.4.1 with
`fps` above 15. Record `fps` with and without a browser watching - that delta is
the evidence the preview costs the detector nothing it needs. Only then bind the
eye model and pin the resolved versions into `docs/DEPLOYMENT.md`.
