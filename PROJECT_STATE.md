# Project State

Last updated: 2026-09-04
Status: Scaffold and live dashboard complete. Detection reworked from whole-face
classification to multi-cue behaviour analysis (PERCLOS + long blinks + yawn + nod)
after the face CNN was found to key on driver identity. The
eye-state base model is integrated but not yet accurate in visible light, and the
behaviour thresholds are untuned. The SPI panel was removed on 2026-08-23: the
firmware is headless and serves its preview and telemetry to a browser over its own
Wi-Fi access point. **First hardware run the same day**: flashed to the board with
MAC `80:b5:4e:c5:e0:18` and it came up clean - 8 MB PSRAM, camera at 240x240 RGB565,
I2S chime, `SoftAP "DrowsyGuard-C5E019"` on 192.168.4.1, face detector loaded, and
**19.7 fps** with no viewer attached. The browser preview itself, the alert path and
everything downstream of the (still unbound) eye model remain unverified.

**MQTT alerting added 2026-09-01, working on hardware since 2026-09-05.** Every
confirmed alert is also published to a broker as one versioned JSON document,
configured from a modal on the device page and persisted in NVS; the documentation
site carries a live fleet dashboard that subscribes to it. Off by default. The first
hardware run against `broker.emqx.io` (TCP, QoS 1) published on the first try and
found the one bug host tests structurally cannot: saving the settings overflowed the
control server's task stack and rebooted the board - fixed 2026-09-05, verified with
2+ hours online, 50 alerts published and acked, and `GET /api/mqtt` answering in
0.17 s. What remains unexercised is in gap 13.

**Wi-Fi provisioning added 2026-09-02.** The board no longer needs a rebuild to join a
network: the device page scans, joins, shows the state, IP and signal, and forgets,
and a five-second hold on BOOT erases the stored credentials from the outside. The
radio runs AP+STA from boot whether or not anything is stored, so the access point is
up during a scan, during a join and after a failed one - which is what makes a wrong
password recoverable rather than terminal. 74 host-compiled tests in
`tests/test_wifi_provision.py`, plus the device-page harness. **Not yet exercised on
hardware** - see gap 14.

## Locked design decisions
- **MQTT publishing may never stall the capture loop, and the shape of the code is the
  guarantee rather than a comment.** `mqtt_publish_alert()` takes a mutex with a zero
  tick timeout, copies 96 bytes into a fixed 16-deep ring and returns; it cannot
  allocate, cannot block and cannot fail in a way the caller has to handle. Every
  socket, handshake, retry and JSON render happens on the publisher task on core 1.
  A broker that has been unreachable for a week costs the detector one memcpy per
  alert. `tests/test_mqtt_config.py` proves the bound with 10 000 pushes and nothing
  draining; `docs/DEPLOYMENT.md` test M3 measures `fps` with the broker dead.
- **The MQTT logic lives in two files with no ESP-IDF headers**, deliberately, so that
  validation, topic generation, the payload schema, the NVS blob format, the backoff
  schedule, de-duplication and the outbox are all host-testable. `mqtt_publisher.cpp`
  and `settings_nvs.cpp` are the halves that cannot be, and they were written to own
  no logic of their own. Anything new that could go in either pair belongs in the
  first.
- **Publishing is off by default and there is no way to make it otherwise.** Turning
  it on sends a named driver's alertness state to a third party; that has to be an act
  rather than an inherited setting.
- **The driver remark is never part of a topic.** A driver's name in a broker's
  subscription tree is visible to every wildcard subscriber, retained in broker state,
  and impossible to change without orphaning the topic. It travels in the payload.
  `device_id` and `fleet_id` are the public halves and are constrained to characters
  that cannot restructure the tree.
- **The MQTT payloads are versioned; the HTTP APIs are not.** An HTTP client polls a
  device it can see; a subscriber is somebody else's software reading messages
  published while nobody was watching. Hence `schema` on every document.
- **No image ever leaves the device over MQTT.** The captures stay on the card. There
  is no code path that would publish a frame, and there should not be one.
- **The device does not subscribe.** It publishes and takes no commands. A drowsiness
  alarm a broker can mute would put the existing unauthenticated mute on the whole
  internet.
- **The fleet page in the documentation has no third-party script.** It speaks MQTT
  over WebSocket itself. The page's only job is to display a driver's alertness state,
  and a CDN dependency on it means a third party can change what it does for every
  reader. It also has no `innerHTML`, and a test fails the build if one appears.
- **The access point never comes down.** Not for MQTT, not while the radio scans, not
  while it joins, and not when a join fails. Station mode is additive and the radio is
  APSTA from boot. A wrong hotspot password, a dead uplink or a refused broker all
  degrade to "no telemetry", never to "no dashboard" and never to "no alarm" - and the
  page that fixes the password is served over the interface that cannot be lost by
  getting the password wrong.
- **The physical reset clears Wi-Fi and only Wi-Fi.** `settings_clear_wifi()` erases
  one NVS key by name from a namespace that holds four, and the button hook does not
  reboot. A control that an accidental pocket press can trigger must not be able to
  cost somebody their broker configuration, their device identity or their captures.
  There is no factory-reset path on this button and there should not be one.
- **The button state machine is pure and tested, because the hazard is real.** This
  board's auto-reset lines are inverted, so opening a serial port pulls GPIO0 low -
  anyone with a monitor attached is electrically holding BOOT down. `ButtonWatch`
  therefore requires a debounced *release* before it believes any press, ignores the
  first three seconds after boot, and gives up on a pin that never rises. All three
  rules are in `wifi_provision.cpp` with no ESP-IDF headers, and
  `tests/test_wifi_provision.py` drives them by injected clock.
- **An SSID is hostile input.** It is 32 bytes chosen by whoever owns the access point,
  anybody in radio range can broadcast one, and it lands in the device's own recovery
  page. Escaped twice in two alphabets - `settings_json_escape_utf8()` for the parser
  (bytes that are not well-formed UTF-8 become `\u00XX`, because a raw one makes the
  whole scan document undecodable) and the page's `esc()` for the renderer - and the
  scan buffer is sized for the worst case rather than the likely one.
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
- A closure that begins with the mouth flung wide must outlast REFLEX_MAX_S (1.2 s)
  rather than MICROSLEEP_MIN_S before it is alarmed on: an involuntary reflex closes
  the eyes ~1 s and would otherwise read as a microsleep. Guard only - nothing is
  counted or reported. The sneeze feature this replaced was removed on 2026-09-02.
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
2. **ESP-PPQ is not installable from PyPI** - `pip index versions esp-ppq` returns
   nothing, so `scripts/quantize_espdl.py`'s advice to `pip install esp-ppq` cannot
   work. It lives in Espressif's git. This is now only a blocker for quantizing
   *future* models: the eye model went the float route instead (gap 6), and the
   face detector ships pre-quantized inside `espressif/human_face_detect`.
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
6. **The eye model is now bound** (2026-08-23) and PERCLOS moves for the first
   time - measured 0.00-0.22 at rest on hardware. Not via ESP-DL: that needs a
   quantized `.espdl`, and **esp-ppq is not on PyPI at all** while this repo has no
   calibration set either. Instead `firmware/esp32s3/main/eye_model.cpp` runs the
   four-convolution, 11,250-parameter graph directly in float32 from weights
   exported by `scripts/export_eye_model.py`, and
   `tests/test_eye_model_parity.py` holds it to the ONNX graph on the host to
   within 1e-5 (it host-compiles the firmware file itself, using Zig's bundled
   clang when no system compiler is present).
   The accuracy problem below is untouched by that and is still the real gap.
   The cost is also now measured rather than estimated: ~45 ms for both eyes,
   scalar float, which drops the loop from 19.7 to ~10 fps while a face is held.
   See `docs/FIRMWARE_PIPELINE.md` for the two optimisation routes.
7. The eye-state base model is IR-trained and does not transfer to DDD's visible-light
   ~45 px eye crops: AUC 0.62 vs its claimed 95.84% in-domain. Input-space fixes
   (grayscale, hist-eq, CLAHE, inversion, four patch scales) did not recover it.
   Open task: fine-tune it on visible-light eye-state labels, or pair it with the
   planned IR illumination, which matches its training domain. Not yet validated on a
   live camera with a real person - no human was available in this environment.
8. Behaviour event thresholds (yawn 1.2 s, microsleep 1.0 s, nod 1.5 s, reflex 1.2 s +
   jaw delta) are literature-informed defaults. Their logic is unit-tested on synthetic
   traces but none are tuned or validated on labelled yawn/nod video, which the
   project does not have. `yaw` is computed but unvalidated - needs a head-turn test.

   Reworked on 2026-08-23, and what changed is worth separating from what did not.
   Four defects in the *logic* were found and fixed, each demonstrated by running a
   synthetic trace through the previous version straight out of git: a microsleep was
   only announced once the driver reopened their eyes; one noisy frame split a closure,
   a yawn or a nod, and a split nod was then counted twice; any mouth movement shorter
   than `NOD_MAX_S` registered as a head nod, because the pitch proxy divides by the
   eye-to-mouth distance; and held landmarks were fed in as fresh evidence. The
   CHANGELOG entry has the before/after counts.

   Those were correctness bugs, not tuning. **The thresholds themselves are still
   untuned against real video** - the new ones (`YAWN_PEAK_DELTA`, `NOD_PEAK_DELTA`,
   `NOD_MIN_S`, `NOD_NORM_DELTA`, `MOUTH_NARROW_W`, `CLOSED_HYSTERESIS`, `CUE_GAP_S`)
   rest on the same literature-informed basis as the originals and are validated the
   same way, on synthetic traces with known ground truth. Fixing the logic does not
   make the numbers right; it makes them worth tuning.
9. No whole-face drowsiness checkpoint ships with the repo; all were removed on
   2026-08-10. Only fetched detectors live under `models/detectors/` (not tracked).
   Durable lesson worth keeping: `TinyDrowsyNet` trained from scratch on DDD reached
   ~0.81 validation but only ~0.57 on unseen drivers, and per-driver results showed it
   keyed on driver appearance rather than eyelid state. Judge any replacement by
   per-driver accuracy on held-out subjects, never by an average.
10. The DDD corpus was deleted after import at the user's request. `data/raw` and
   `data/processed` retain all 41,793 images; re-importing needs a fresh download.
11. On this Windows machine the installed `drowsyguard` console script throttles
   webcam capture to ~1 fps (both MSMF and DSHOW); `python -m drowsyguard.cli`
   runs the identical code at 30 fps. Root cause in the launcher is unresolved.
12. The 2026-08-23 firmware **has now been run on hardware**, and doing so found two
   bugs that host testing could not. The detection gate rejected 100% of real
   candidates, and the cause was not the gate: the frame is horizontally mirrored (the
   sensor is mounted upside down, so vflip is applied without hmirror), which reversed
   the eye pair and inverted the sign of every vertical cue - `jaw_drop` read -1.2
   instead of +1.2 and *fell* as the mouth opened, so **the yawn cue had never been
   able to fire**. Fixed by `behavior_orient_landmarks()`; the gate is enabled and
   reports `ok` on every detection. Measured: 15.7 ms per eye, 39 ms per detection,
   19.7 fps idle and 10.7-13.6 fps while tracking, 20/20 detections at score 1.00.

   Still unexercised on hardware: a real drowsiness alert firing end to end, the
   behaviour cues against an actual yawn or nod (the logic is now correct and the signs
   are now right, but no one has yawned in front of it), and heap over hours.

13. **MQTT alerting works on hardware over TCP; TLS/WSS and the isolation
   measurements are still open.** The logic is covered by `tests/test_mqtt_config.py`
   (162 cases) and `tests/test_fleet_page.py` + `fleet_page_harness.mjs` (83 checks),
   and since 2026-09-05 by a real run: connected to `broker.emqx.io:1883` (TCP,
   QoS 1, MQTT 3.1.1, auto topics), 50 alerts published and PUBACKed over 2+ hours
   with no reboot, `dropped` 0, real alerts arriving with distinct event ids, and the
   settings page saving and answering normally.

   Getting there took two rounds of fixes, both worth remembering because neither
   was reachable from a host test:

   * **Audited 2026-09-04, before the first hardware run.** Five defects on the
     reconnect/failure paths: the esp-mqtt client task was the only network task not
     pinned to core 1, so a TLS handshake could preempt the capture loop on core 0
     for its full duration on every backoff retry (the "freeze"); an alert queued
     mid-handshake woke the connect-wait early and tore the half-connected client
     down; the dedup ring recorded ids before delivery, so a failed enqueue's retry
     was dropped as its own duplicate; the outbox could commit an event
     evicted-and-replaced between peek and commit; and the CONNACK never woke the
     publisher, adding a silent 8 s before every flush. The two data-structure races
     are host-tested; the rest are DEPLOYMENT.md tests M9-M13.
   * **First broker contact 2026-09-05.** Publishing worked immediately, but every
     *save* of the settings rebooted the board: POST /api/mqtt overflowed the
     control httpd task's 6144-byte stack (the handler chain measures ~7 kB with
     `-fstack-usage`), just after the config reached NVS - so the save "took" and
     crashed at once. Stack is 8192 now; see the 2026-09-05 CHANGELOG entry.

   Still unexercised on hardware: **TLS** (the handshake against the broker, the
   certificate bundle verifying it, a pasted CA) and **WSS from the fleet page**;
   the Last Will actually firing (M6); the flush after a real outage (M4); and the
   measurements that carry the safety argument - M3 and M9, `fps` with the broker
   dead and *during* TLS reconnect attempts. One further thing is untested by
   construction rather than by omission: NVS behaviour on a partition that is
   genuinely full.

14. **Wi-Fi provisioning has not been exercised on hardware.** As of 2026-09-02 it
   compiles and links against ESP-IDF v5.5.5 with the real xtensa toolchain, and the
   logic is covered by `tests/test_wifi_provision.py` (74 cases, including the button
   state machine driven by an injected clock and hostile SSIDs through the real JSON
   builder) and by the device-page harness. What that does **not** establish is
   anything about a real radio: whether a scan really returns in two to three seconds
   and really costs the detector nothing, whether the access point genuinely stays
   associated through a scan and a failed join, whether the ESP-IDF disconnect reason
   codes arriving from a real router match the sentences written for them, and whether
   a five-second BOOT hold on this board behaves as the state machine assumes.
   `docs/DEPLOYMENT.md` has these as tests W1-W11. Run W4 and W8 first: W4 is the
   recovery guarantee and W8 is the scope guarantee.

## Next best action
Model: get eye-state labels in the target (visible-light) domain and fine-tune the
11.3k-parameter base model on them, splitting by subject. MRL Eye ships subject IDs in
its filenames but is infrared and needs Kaggle credentials, which are not configured
here. Confirm any result per-driver on held-out subjects, and note for the write-up
that the highest-accuracy public drowsiness models (70-343 MB, 224x224) cannot run on
an ESP32-S3, which is why the eye-closure route was chosen.
Wi-Fi: run `docs/DEPLOYMENT.md` tests W1-W11, and record W2 - `fps`, `ms_detect` and
`ms_eye` during a scan against the same three when idle. That, and W4 (a wrong
password must leave 192.168.4.1 serving), are the two claims a host test cannot make.
MQTT: M1/M2 have effectively passed on hardware over TCP (2026-09-05: online, 50
published and acked, test publishes arriving). What is left of `docs/DEPLOYMENT.md`
M1-M13 is: switch the transport to **TLS** and repeat (the certificate bundle has
never verified a real broker from this board), then record **M3 and M9** - `fps`,
`ms_detect` and `ms_eye` with the broker dead, and during TLS reconnect attempts.
Those two measurements are the entire safety argument for the feature and the one
thing a host test cannot produce. Then M4 (outage flush), M6 (the will), M10-M13.
`./plxy.sh mqtt` prints the state and the counters; `./plxy.sh mqtt test` publishes
one alert through the real path.
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
