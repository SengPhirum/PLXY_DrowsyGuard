# ESP32-S3 Deployment Notes

Espressif currently recommends ESP32-S3/ESP32-P4-class boards for ESP-DL and supports camera sensors through `esp32-camera`. ESP-DL deploys quantized `.espdl` models produced with ESP-PPQ.

## Version policy
Intended versions, chosen 2026-08-11 when the hardware was bought. Replace each with
the version actually resolved by `idf.py reconfigure` (see
`firmware/esp32s3/dependencies.lock`) once the first build succeeds - the resolved
numbers are what the thesis reports.

- ESP-IDF: v5.4.x intended (>=5.3 required by esp-dl 3.x) — **confirm after first build**
- esp32-camera: ^2.1.7 — **confirm** (also supplies the JPEG encoder the web
  preview uses; no separate dependency)
- ESP-DL: ^3.1.2 — **confirm at stage 3**
- ESP-PPQ: TBD (pin before running `scripts/quantize_espdl.py`)
- Board: ESP32-S3-WROOM-1 **N16R8** CAM dev board (16 MB flash, 8 MB octal PSRAM)
- Camera sensor: **OV3660**, DVP, pin map identical to ESP32-S3-EYE
- Display: **none.** The preview is served over the board's own Wi-Fi access
  point (SoftAP, `esp_http_server`, MJPEG on port 81). Both ship with ESP-IDF,
  so there is no version to pin here — record the ESP-IDF version instead.

Do not leave these floating for thesis experiments. Setup and flashing procedure:
`docs/HARDWARE_SETUP.md`.

## Firmware pipeline
Camera (240x240 RGB565) -> ESP-DL face detect + 5 landmarks, every 3rd frame ->
**plausibility gate and track confirmation** -> one 32x32 eye crop per frame,
alternating -> eye-closed probability -> PERCLOS + long blinks + yawn + nod fused
into a risk score, sneeze-suppressed -> temporal risk filter -> reason-specific voice
alert (buzzer fallback), with the frame and every intermediate number served to a
browser over Wi-Fi.

Two decisions run alongside that chain rather than inside it, because neither is a
drowsiness measurement:

- **Sneeze**: an edge, announced on its own channel, which also suppresses the
  drowsiness score for `SNEEZE_MAX_S`.
- **Presence**: `PresenceMonitor` takes the track's verdict *and* a `PipelineHealth`,
  and announces "no driver detected" once per absence episode - never when the camera
  or the models are down, because that would be a claim about the cabin drawn from a
  fact about the firmware.

This replaced the earlier whole-face 64x64 grayscale classifier, which learned driver
identity rather than eyelid state; see `PROJECT_STATE.md`.

## Hardware acceptance tests
- Camera initializes 100 consecutive boots.
- No heap exhaustion after 1 hour.
- Inference latency recorded over >=1000 frames.
- Peak RAM and flash usage recorded.
- Alert output verified physically (`POST /api/alert-test` makes this a one-liner
  rather than a firmware edit).
- Quantized predictions compared with Python on a shared test-image set.
- **Frame rate recorded twice: with a browser watching the preview and with
  none.** The delta is the JPEG encode, and it is the evidence that the
  preview costs the detector nothing it needs. `GET /api/status` reports `fps`
  and `stream.viewers`, so this test scripts cleanly.
- Preview opened and closed 20 times with heap sampled after each, to rule out
  a leak in the stream path.
- **The gate rejects nothing on a real face.** `gate would drop 0 ok` in the
  once-a-second log line, over a run with someone in front of the camera. This is a
  regression test, not a feature test: an earlier version of the gate rejected 100% of
  real candidates on hardware and reported it as a bare count.
- **The no-driver alert fires once per absence, and re-arms.** Leave the frame empty
  and confirm exactly one `ALERT (NO DRIVER DETECTED)`; then sit down, leave again, and
  confirm a second. `/api/status` reports `presence.alerts`.
- **A camera fault is not reported as an absence.** Unseat the ribbon while running:
  `presence.health` must read `camera-fault`, `presence.state` must read `fault`, and
  no announcement may follow.
- **Every alert clip is audible in both languages.** `./plxy.sh alert <reason>` for all
  six reasons, in `en` and `km`; the response's `source` field must read `embedded` or
  `card`, never `tone`.

### Measured, 2026-09-01 (IDF v5.5.5, N16R8 + OV5640)

| | value |
| --- | --- |
| App binary | 3,404,992 B, 46% of the 6 MB partition free |
| Flash `.text` / `.rodata` | 1,802,632 B / 1,466,884 B |
| DIRAM used | 218,403 B (63.91%), 123,357 B free |
| Internal heap free, running | 17,475-21,807 B (lower with a browser attached) |
| PSRAM free, running | 7.01-7.03 MB |
| Frame rate, nobody in frame | 19.7 fps |
| Frame rate, tracking + one viewer | 11.5-17.2 fps |
| Face detection incl. gate, 0-1 candidates | 39.2-39.6 ms every 3rd frame |
| Face detection incl. gate, busy frame | up to 70.3 ms |
| Eye model | 17.8-18.5 ms per eye, one eye per frame |
| Detection hit rate with someone in frame | 20/20 at score 1.00 |
| Gate rejections on real candidates | 0 over every logged interval |

Against the same tree before the gate, the presence monitor and the sneeze
announcement: **+276,128 B of flash** (259 kB of that is the four new voice clips;
the logic is 5.4 kB of code) and **+1,120 B of internal RAM** (1,024 B of that is the
status JSON buffer, grown to fit the new `presence` and `alert.counts` objects).
Nothing new is allocated from PSRAM, and there is no measurable change in detection
latency at equal candidate counts.
