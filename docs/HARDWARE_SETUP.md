# Hardware setup and firmware install guide

> **New here?** This page is the toolchain and bring-up reference. If you are
> assembling the hardware for the first time, start with the step-by-step
> [Hardware Setup Tutorial](./tutorials/hardware-setup/README.md), which covers
> every part with wiring diagrams, then come back here for the ESP-DL stages.

For the parts bought on 2026-08-11:

| Part | Listing | Price |
| --- | --- | --- |
| ESP32-S3-WROOM-1 **N16R8** dev board with **OV3660** camera | [khmeres.com item 2991](https://khmeres.com/product_detail/2991) | $7.50 |
| ~~SPI TFT panel~~ **removed from the build 2026-08-23**, see section 1 | [khmeres.com item 1885](https://khmeres.com/product_detail/1885) | $3.50 |
| **MAX98357A** I2S filterless class-D amplifier | [khmeres.com item 2724](https://khmeres.com/product_detail/2724) | $2.00 |
| 4 ohm / 3 W 40x22 mm speaker | [khmeres.com item 2554](https://khmeres.com/product_detail/2554) | $0.75 |
| MB102 830-point solderless breadboard | [khmeres.com item 371](https://khmeres.com/product_detail/371) | $1.50 |

Written for Windows 11 / PowerShell. Nothing in this guide has been run against real
hardware - no board existed in the development environment - so treat every "you
should see" as an expectation to verify, not a result.

---

## 1. Do these parts actually fit the project?

Yes, and better than expected.

**The board.** `docs/FIRMWARE_PIPELINE.md` rules out the ESP32-S2 (no AI vector
instructions, unsupported by ESP-DL's face detector) and recommends the ESP32-S3-EYE.
What arrived is an ESP32-S3-WROOM-1 N16R8 CAM board: the same silicon class, dual-core
240 MHz S3, 16 MB flash, **8 MB octal PSRAM**. More importantly its DVP camera pin map
is **byte-for-byte identical to the ESP32-S3-EYE map** in arduino-esp32's
`camera_pins.h`, verified against
[keyestudio's MB0184 pin table](https://docs.keyestudio.com/projects/MB0184/en/latest/docs/MB0184%20ESP32-S3%20CAM%20Development%20Board.html)
for the same board, which also ships an OV3660. So the ESP-DL and ESP-WHO vision
examples run on it unmodified, and the frame budget in `FIRMWARE_PIPELINE.md` stands.

The OV3660 is a 3 MP sensor rather than the EYE's 2 MP OV2640. That is fine and
slightly better: `esp32-camera` supports it, the firmware asks for 240x240 RGB565
anyway, and a larger native array gives a cleaner downscale.

**The display - there isn't one any more.** The build carried a 1.8" ST7735S, then
a 2.8" ILI9341, and as of 2026-08-23 neither: the preview is served to a browser
over the ESP32-S3's own Wi-Fi access point instead. What that traded away:

| Removed | Gained |
| --- | --- |
| 5 GPIOs (14, 21, 41, 42, 47) | 7 free GPIOs instead of 2 |
| 8 jumper wires | 7 wires in the whole build instead of 15 |
| a 150 KB PSRAM framebuffer + per-frame software blit | ~20 ms of JPEG encode, on core 1, only while someone is watching |
| two managed panel-driver components | nothing - `esp_http_server` and Wi-Fi are in-tree |
| 240x320 of 8-pixel text, readable by one person in front of it | the frame plus risk, PERCLOS, per-eye closure, blink/yawn/nod rates, head geometry, an event log and frame timing, on a phone, from the passenger seat |
| - | `curl`-able JSON for scripted acceptance tests |

The preview is a diagnostic, never a dependency: the capture loop copies one frame
and returns, skips the copy entirely when no browser is connected, and the alert
path never touches the network. See section 2.2 for how to reach it and
[firmware/esp32s3/main/web_server.h](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/main/web_server.h) for why
there are two HTTP servers.

**The audio path.** The MAX98357A and the 4 ohm / 3 W speaker are now in hand, and
`main/board_audio.h/.cpp` drives them over I2S on GPIO 39 (BCLK), 38 (LRCLK) and
40 (DIN). The buzzer on GPIO 2 remains the automatic fallback when I2S fails to
initialize. Wiring and the `SD`/`GAIN` configuration pins: see the
[tutorial, section 6](./tutorials/hardware-setup/README.md#6-wiring).

**Still missing** for the full build:

- A **USB-C data cable**. Charge-only cables are the single most common cause of
  "the board doesn't appear".
- **~8 dupont jumper wires** - 5 for the amplifier, 2 for the speaker, 1 spare.
- A **soldering iron**. The amplifier ships with a loose header strip; it is the
  only thing in the build that needs soldering.
- A **phone, tablet or laptop** with a browser. That is the display now.
- Approved English/Khmer voice recordings. Until they exist each alert reason
  plays its own tone pattern, which is audible and testable but is not speech.
- Optionally a **microSD card** - but the SD slot's GPIOs are now the I2S amplifier's,
  so you cannot have both without re-planning pins.

---

## 2. Wiring

### 2.1 Camera

Nothing to wire. The OV3660 plugs into the board's FPC connector: lift the black latch,
slide the ribbon in with the contacts facing the connector's contact side, press the
latch down. A half-seated ribbon reads as `esp_camera_init failed: 0x105`.

The pin map is now recorded in [board_camera.h](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/main/board_camera.h):

| Signal | GPIO | | Signal | GPIO |
| --- | --- | --- | --- | --- |
| XCLK | 15 | | Y9 (D7) | 16 |
| SIOD (SDA) | 4 | | Y8 (D6) | 17 |
| SIOC (SCL) | 5 | | Y7 (D5) | 18 |
| VSYNC | 6 | | Y6 (D4) | 12 |
| HREF | 7 | | Y5 (D3) | 10 |
| PCLK | 13 | | Y4 (D2) | 8 |
| PWDN | not routed | | Y3 (D1) | 9 |
| RESET | not routed | | Y2 (D0) | 11 |

### 2.2 The preview (nothing to wire)

The board is headless. It comes up as a SoftAP and serves the live preview and
all the detection telemetry over HTTP:

| | |
| --- | --- |
| **SSID** | `DrowsyGuard-XXXXXX` - the last three bytes of the AP MAC, so two boards on one bench stay distinguishable |
| **Password** | `drowsyguard` (WPA2; set `WIFI_AP_PASSWORD` to `""` for an open network) |
| **Address** | `http://192.168.4.1/` |

| Endpoint | Port | What it serves |
| --- | --- | --- |
| `/` | 80 | the page, linked into the binary from `main/web/index.html` |
| `/stream` | 81 | MJPEG live preview - **one viewer at a time** |
| `/api/snapshot` | 80 | one JPEG; the page's fallback for extra viewers |
| `/api/status` | 80 | risk, PERCLOS, face box, event rates, heap, uptime |
| `/api/settings` | 80 | `?quality=`, `?fps=`, `?muted=` |
| `/api/alert-test` | 80 | `?reason=0..5`, plays one warning |

Two servers because `esp_http_server` serves one request at a time per instance
and an MJPEG stream never ends; a stream on port 80 would block the page and the
API for as long as anyone watched. Hence the single-viewer limit on the stream,
and the still-image fallback for everyone else.

Wi-Fi costs no GPIOs: the radio is on-die and shares no pins with the DVP camera
bus or the I2S amplifier. Change the SSID, password, channel or optional station
credentials in [board_wifi.h](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/main/board_wifi.h); change the
ports, JPEG quality and stream-rate defaults in
[web_server.h](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/main/web_server.h).

> **The radio is the biggest current consumer in the build.** Transmit bursts of a
> couple of hundred milliamps land on top of whatever the camera and the
> amplifier are doing. A board that is stable until you open the preview has a
> supply problem, not a firmware one.

### 2.3 Pins you must not use

The camera eats fourteen GPIOs, and an N16R8 module reserves more than most people
expect:

| GPIO | Why it is unavailable |
| --- | --- |
| 4,5,6,7,8,9,10,11,12,13,15,16,17,18 | DVP camera |
| **33-37** | SPI flash + **octal PSRAM**; driving these hangs the board |
| 19, 20 | native USB D-/D+ (free only if you never use the USB-OTG port) |
| 43, 44 | UART0 console, which `idf.py monitor` needs |
| 0 | BOOT button / strapping — **read by the firmware after boot** as the Wi-Fi reset (hold 5 s). Free to use as a button, not as an output |
| 45, 46 | strapping pins; avoid driving them at boot |
| 48 | on-board RGB LED on most units |

That leaves **1, 2, 3, 14, 21, 38, 39, 40, 41, 42, 47**. GPIO 2 is the buzzer in
`voice_alert.cpp`; **38/39/40** (the microSD slot, unused by this project) are the
I2S amplifier's, assigned in `board_audio.h`. **GPIO 1, 3, 14, 21, 41, 42 and 47
are free** - the last five came back when the SPI panel was dropped.

> If one of these is not broken out on your particular board's header, pick another
> from the free list and update `board_audio.h`. Do not reach for 33-37.

### 2.4 I2S amplifier

Five wires, plus two from the amplifier's screw terminal to the speaker.

| Amplifier pin | Also labelled | Wire to | Why this pin |
| --- | --- | --- | --- |
| GND | - | GND | common ground; connect first |
| VIN | - | **5V** | 2.5-5.5 V accepted; 3.2 W into 4 ohm is a 5 V figure |
| BCLK | BLCK | **GPIO 39** | I2S bit clock |
| LRC | LRCLK, WS | **GPIO 38** | I2S word select |
| DIN | - | **GPIO 40** | I2S data, ESP32-S3 to amplifier |
| GAIN | - | *leave floating* | floating = 9 dB, which is already loud here |
| SD | SD_MODE | *leave alone* | see below |

There is no MCLK: the MAX98357A recovers its own clock, which is why three signal
wires suffice. BCLK/LRC/DIN are driven at 3.3 V directly from the ESP32-S3 - no
level shifter is needed or wanted.

`SD` selects the channel as well as shutting the part down (`<0.16 V` shutdown,
`0.16-0.77 V` (L+R)/2, `0.77-1.4 V` right, `>1.4 V` left). `board_audio.cpp` writes
the same sample into **both** I2S slots, so every non-shutdown setting sounds
identical and the breakout variant stops mattering. If there is no sound at all,
measure `SD`: near 0 V means the part is shut down, and a 100 kohm resistor from
`SD` to `VIN` forces left-channel mode.

Change these in one place only: `AUDIO_PIN_*` in
[board_audio.h](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/main/board_audio.h).

---

## 3. Toolchain (Windows)

### 3.1 Install ESP-IDF

Use the offline installer rather than a manual git clone: it brings the matching
Python, CMake, Ninja and Xtensa toolchain, and registers the USB drivers.

1. Go to <https://dl.espressif.com/dl/esp-idf/> and download the **offline installer
   for ESP-IDF v5.4.4** (~1.56 GB). Not the 4.6 MB "Universal Online Installer" - the
   offline one bundles its own Python 3.11, which matters if the machine's system
   Python is 3.13 (ESP-IDF v5.4 supports 3.9-3.12).
   v5.3 is the minimum for the ESP-DL 3.x components used in stage 3.
2. Install to a path with **no spaces and no non-ASCII characters**. `C:\Espressif`
   is the default and is correct; a path like `C:\Users\Phirum Seng\...` breaks CMake.
3. Tick the driver components (CP210x / FTDI / WCH-CH34x) when offered.
4. Let it finish completely. It unpacks the Xtensa toolchain, CMake, Ninja and a
   Python virtualenv, which takes a while after the progress bar looks done.

Alternative, if you prefer to stay in the editor: the **VS Code ESP-IDF extension**
(`espressif.esp-idf-extension`), *Configure > Express*, v5.4. Same result; the commands
below then run in its "ESP-IDF Terminal".

### 3.2 Enable long paths

ESP-IDF build trees exceed the Windows 260-character path limit. Once, in an
**Administrator** PowerShell:

```powershell
New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
  -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

Then reboot.

### 3.3 Open an ESP-IDF shell

`idf.py` is **not** a global command and never appears on the system PATH. It lives
inside the ESP-IDF install and only works in a shell where `export.ps1` has run. An
ordinary PowerShell or VS Code terminal will always say `idf.py not found`.

Start menu > **ESP-IDF 5.4 PowerShell** (the installer creates this shortcut; it runs
`export.ps1` for you). Everything below assumes that shell:

```powershell
idf.py --version    # expect: ESP-IDF v5.4.4
```

To get the same environment in a terminal you already have open, dot-source the export
script once per session:

```powershell
. C:\Espressif\frameworks\esp-idf-v5.4.4\export.ps1
```

Adjust the version in that path to whatever you installed.

---

## 4. Connect the board

The board has **two USB-C ports**:

- **UART** - behind a CH340/CP2102 bridge. Use this one. It gives a COM port that
  survives resets and firmware crashes.
- **USB / OTG** - the S3's native USB-serial-JTAG. Works too, but the port vanishes and
  re-enumerates on every reset, and disappears entirely if firmware ever reconfigures
  GPIO 19/20.

Plug into the **UART** port and find it:

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
```

Nothing listed? In order: try another cable (must carry data), check Device Manager for
a yellow-triangle "USB2.0-Serial" and install the
[WCH CH34x driver](https://www.wch-ic.com/downloads/CH341SER_EXE.html), then try the
other USB port.

If flashing refuses to start, force download mode: **hold BOOT, tap RESET, release
BOOT**, then flash.

---

## 5. Smoke test before touching this project

Prove the toolchain and the board independently. Five minutes here saves an hour of
misattributed errors later.

```powershell
cd $env:USERPROFILE
Copy-Item -Recurse "$env:IDF_PATH\examples\get-started\hello_world" .\hw_test
cd .\hw_test
idf.py set-target esp32s3
idf.py -p COM5 flash monitor      # substitute your port
```

Expect a chip banner and a countdown. `Ctrl+]` exits the monitor.

---

## 6. Build and flash DrowsyGuard

Everything the board needs is committed. From the project root:

```powershell
cd <path-to-this-repo>\firmware\esp32s3
idf.py set-target esp32s3          # seeds sdkconfig from sdkconfig.defaults
idf.py reconfigure                 # fetches managed components, writes dependencies.lock
idf.py build
idf.py -p COM5 flash monitor
```

`set-target` reads [sdkconfig.defaults](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/sdkconfig.defaults), which
already sets what is easy to get wrong:

| Setting | Value | Consequence if wrong |
| --- | --- | --- |
| `CONFIG_SPIRAM` | y | camera cannot allocate framebuffers |
| `CONFIG_SPIRAM_MODE_OCT` | y | **R8 boards report 0 B PSRAM in quad mode** |
| `CONFIG_ESPTOOLPY_FLASHSIZE_16MB` | y | truncated image / boot loop |
| `CONFIG_OV3660_SUPPORT` | y | sensor not detected (0x105) |
| `CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240` | y | roughly half the frame rate |
| custom partition table | `partitions.csv` | 6 MB app, room for the ESP-DL models |

Confirm PSRAM was actually found. These lines must appear in the boot log:

```
I (xxx) esp_psram: Found 8MB PSRAM device
I (xxx) esp_psram: Adding pool of 8192K of PSRAM memory to heap allocator
```

If it reports 2 MB, or nothing, stop and fix `SPIRAM_MODE_OCT` before anything else.

### What you should see after this flash

This is **stage 1: preview-only**. The ESP-DL models are not bound yet, so
`model_init()` returns false and the firmware says so rather than pretending:

- **three rising notes at boot** - the audio self-test in `main.cpp`,
- `DrowsyGuard-XXXXXX` in your phone's Wi-Fi list,
- a live camera feed at `http://192.168.4.1/`, with the face box drawn over it,
- an amber `eye model missing` pill: the detector is bound, the eye model is not,
- one log line a second:
  `fps 15.2  risk 0.00  perclos 0.00  face 18/20 ... viewers 1  heap ...  psram ...`

Between them those two signals validate everything: the chime covers the I2S pins,
the amplifier and the speaker; the SSID covers PSRAM and the radio (the Wi-Fi
stack allocates from PSRAM, so a PSRAM failure takes the network with it); the
page covers the HTTP path; and the image covers the camera ribbon, the pin map,
the RGB565 byte order and the power supply. Do not move on until all four are
clean.

They also fail distinguishably, which is the whole point of having two:

| What you get | Where the fault is |
| --- | --- |
| no chime | the audio path - not the camera, not the network |
| chime, no SSID | the radio, or PSRAM upstream of it |
| SSID, no page | wrong address - it is `192.168.4.1`, not a `.local` name |
| page, dark preview | the camera. The status pills say which subsystem failed |

The boot banner also states which output path is live:

```
I (xxx) wifi: SoftAP "DrowsyGuard-A1B2C3" up on channel 6, WPA2
I (xxx) wifi: join it, then open http://192.168.4.1/
I (xxx) audio: I2S up: BCLK=39 LRCLK=38 DIN=40 @ 16000 Hz 16-bit stereo
I (xxx) voice_alert: Alert controller initialized; ... output=I2S/MAX98357A
I (xxx) web: preview at http://192.168.4.1/  (stream on port 81)
```

`output=buzzer` there means I2S did not come up and the fallback is in use.

---

## 7. Stage 2: the pipeline without models

Nothing to write. `main.cpp` already runs `Perclos`, `BehaviorAnalyzer`, `RiskFilter`,
`voice_alert` and the UI every frame; they are simply fed zeros until the models land.
Use this stage to:

- record the **preview-only frame rate**, twice: with a browser watching and with
  none. The difference is the JPEG encode, and it is the number to quote when
  arguing that the preview does not cost the detector anything it needs,
- confirm the alert path fires, by pressing **Test speaker** on the page or
  `curl -X POST "http://192.168.4.1/api/alert-test?reason=1"`. No code edit and no
  reflash - which is the point of the endpoint existing,
- log peak heap for the acceptance tests in [DEPLOYMENT.md](DEPLOYMENT.md), opening
  and closing the stream a few times while you watch it.

## 8. Stage 3: bind ESP-DL

Two functions in [model_adapter.cpp](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/main/model_adapter.cpp); a
sketch of both is already in that file.

1. The ESP-DL dependencies are already declared in
   [main/idf_component.yml](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/main/idf_component.yml) (`espressif/esp-dl`
   and `espressif/human_face_detect`), and the face detector is bound.

2. `idf.py reconfigure`, then `idf.py menuconfig` and leave the model location at
   `CONFIG_HUMAN_FACE_DETECT_MODEL_IN_FLASH_RODATA` - simplest, and `partitions.csv`
   already gives the app 6 MB for it.

3. Implement `model_detect_face()` against `HumanFaceDetect::run()`.
   **The keypoint order is a trap**: ESP-DL emits *(left eye, left mouth, nose, right
   eye, right mouth)* while everything in this project uses YuNet's *(right eye, left
   eye, nose, right mouth, left mouth)*. Pass the raw array to
   `behavior_from_espdl_keypoints()` and never index it directly;
   `tests/test_firmware_parity.py` guards the mapping.

4. Export and implement the eye model. `scripts/quantize_espdl.py` produces the
   `.espdl` from `open-closed-eye-0001`. Its preprocessing must match
   `src/drowsyguard/eyestate.py` exactly: `(pixel - 127) / 255`, output already
   softmaxed, and **index 0 is *closed*** despite the model card saying otherwise.

5. Re-tune `RISK_TRIGGER` in `main.cpp` (currently 0.55) from the desktop dashboard.

Be aware of gap 6 in [PROJECT_STATE.md](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/PROJECT_STATE.md): the eye model is
IR-trained and scored AUC 0.62 on visible-light data. Expect stage 3 to work
mechanically and still classify badly in daylight until that model is fine-tuned. The
OV3660 has no IR capability; an IR-cut-removed sensor plus 850 nm illumination is the
eventual fix, and it also matches the base model's training domain.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `idf.py not found` / `not recognized` | ESP-IDF not installed, or a plain terminal | install per section 3; use the **ESP-IDF PowerShell** shortcut, or dot-source `export.ps1` |
| `idf.py` works in one terminal, not another | `export.ps1` is per-session, not permanent | re-run it in each new terminal; do not add it to your profile |
| No COM port | charge-only cable, or missing CH34x driver | data cable; install driver; try the other USB-C port |
| `Failed to connect ... Wrong boot mode` | esptool's reset sequence cannot reach the loader: this board's auto-reset lines are inverted | `./plxy.sh flash` drives them directly now (`scripts/board_reset.py`); if that fails, hold BOOT, tap RESET, release BOOT, reflash |
| `esp_camera_init failed: 0x105` (`ESP_ERR_NOT_FOUND`) | ribbon half-seated, PSRAM off, or wrong pin map | reseat the ribbon; check the 8 MB PSRAM boot line; check `CONFIG_OV3660_SUPPORT` |
| `esp_camera_init failed: 0x101` (`ESP_ERR_NO_MEM`) | PSRAM not enabled | `CONFIG_SPIRAM=y` **and** `CONFIG_SPIRAM_MODE_OCT=y` |
| Boot loop, `Brownout detector was triggered` | USB port cannot supply camera + radio + amp | powered hub, shorter/thicker cable, or a 5 V bench supply |
| Stable until the preview is opened, then resets | transmit bursts on a marginal supply | as above, or lower the stream rate on the page |
| No `DrowsyGuard-` SSID anywhere | radio never started | look for the `wifi: SoftAP ... up` line; if PSRAM failed first, fix that - the Wi-Fi stack allocates from it |
| SSID visible, phone refuses to join | `WIFI_AP_PASSWORD` shorter than 8 characters | WPA2 needs 8+; see `board_wifi.h` |
| Joined, page times out | wrong address | it is `http://192.168.4.1/` - not `https`, not a `.local` name |
| Page loads, preview stuck on "opening live stream" | another viewer holds the single stream slot | close the other tab, or use **Use still photos** |
| Preview arrives in bursts seconds apart | Wi-Fi power save on, or TCP window too small | confirm `esp_wifi_set_ps(WIFI_PS_NONE)` ran and that `sdkconfig` kept `CONFIG_LWIP_TCP_SND_BUF_DEFAULT` and `CONFIG_ESP_WIFI_STATIC_TX_BUFFER_NUM` |
| `jpeg overflowed ... lower it` in the log | quality too high for the encode buffer | lower the quality slider, or raise `WEB_JPEG_BUFFER_BYTES` |
| Preview red and blue swapped | RGB565 byte order into the JPEG encoder | set `CAM_RGB565_BYTE_SWAP` to 0 in `board_camera.h` |
| Preview mirrored the wrong way | mounting orientation | `set_hmirror` / `set_vflip` in `board_camera_tune()` |
| CMake path errors or `filename too long` | IDF or project in a path with spaces, or long paths disabled | reinstall IDF to `C:\Espressif`; enable `LongPathsEnabled` |
| `fps` far below 15 | CPU at 160 MHz, or the stream rate too high | check `CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240`; lower the stream rate on the page |
| `_binary_index_html_start` undefined at link time | `EMBED_TXTFILES` missing from `main/CMakeLists.txt` | restore it - the page is linked in from `main/web/index.html` |
| No boot beep, log says `output=buzzer` | I2S channel failed to initialize | check `AUDIO_PIN_*` against the reserved list in section 2.3 |
| No boot beep, log says `output=I2S/MAX98357A` | wiring, or the amplifier is shut down | check GND and VIN, then measure `SD` - below 0.16 V is shutdown |
| Loud hiss or buzz instead of a tone | no common ground between board and amplifier | tie every module GND to one net |
| Audio very quiet | `VIN` on 3V3 rather than 5V | move `VIN` to `5V` |
| Tone plays but the preview freezes | playback running inline instead of on its task | confirm `voice_alert_init()` returned true |
| `driver/i2s_std.h: No such file` | ESP-IDF older than v5.0 | install v5.4.x; the legacy `i2s.h` API is not used |

---

## 10. Record these before the thesis experiments

The version policy block in [DEPLOYMENT.md](DEPLOYMENT.md) is filled in with the
intended versions, but the resolved ones are authoritative. `idf.py reconfigure` writes
them to `firmware/esp32s3/dependencies.lock`:

```powershell
idf.py --version
Get-Content .\dependencies.lock | Select-String -Pattern 'version|esp32-camera|esp-dl|human_face'
```

Then run the six hardware acceptance tests already listed in `DEPLOYMENT.md`:
100-boot camera init, one-hour heap stability, latency over 1000 frames, peak RAM and
flash, physical alert output, and quantized-vs-Python agreement on a shared image set.

## Sources

- [khmeres.com item 2991 - ESP32-S3 N16R8 board with OV3660](https://khmeres.com/product_detail/2991)
- [keyestudio MB0184 ESP32-S3 CAM pin table](https://docs.keyestudio.com/projects/MB0184/en/latest/docs/MB0184%20ESP32-S3%20CAM%20Development%20Board.html)
- [arduino-esp32 camera_pins.h (ESP32S3_EYE map)](https://github.com/espressif/arduino-esp32/blob/master/libraries/ESP32/examples/Camera/CameraWebServer/camera_pins.h)
- [espressif/esp32-camera component](https://components.espressif.com/components/espressif/esp32-camera)
  - also the source of `fmt2jpg_cb`, the JPEG encoder the web preview uses
- [ESP-IDF HTTP Server API reference](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/protocols/esp_http_server.html)
  - one request per instance at a time, which is why the stream has its own port
- [ESP-IDF Wi-Fi API guide (SoftAP)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/wifi.html)
- [espressif/esp-dl component](https://components.espressif.com/components/espressif/esp-dl)
- [espressif/human_face_detect component](https://components.espressif.com/components/espressif/human_face_detect/versions/0.3.0/readme)
