# ESP32-S3 N16R8 + OV5640 + 2.8-inch SPI TFT setup

Photo-matched setup notes for the hardware received on 2026-08-15:

- ESP32-S3-WROOM-1 **N16R8** camera board
- built-in **OV5640** camera module
- red 2.8-inch **240×320 SPI TFT touch** module
- existing MAX98357A amplifier + 4 Ω / 3 W speaker

> **STOP before applying power:** the rear label of the TFT is not visible in the supplied photo. The front PCB layout matches the common ILI9341 + XPT2046 / MSP2807 family, but the controller and header order must be confirmed from the back of the actual module. Do not trust physical pin position alone.

> The separate four pads on the left edge of the TFT are normally the microSD breakout, not the LCD's four-wire connector. Leave them unused.

## 1. Solder first

The ESP32 and TFT through-holes in the photo appear unsoldered. Solder two straight male header strips to the ESP32 and one 1×14 male header to the TFT. Do not rely on loose pins pressed into a breadboard; intermittent SCK/CS/RESET commonly produces a white screen.

## 2. Existing safe GPIO allocation

The camera already occupies GPIO 4–13 and 15–18. GPIO 33–37 are reserved by octal flash/PSRAM on N16R8. GPIO 19/20 are native USB, 43/44 are the serial console, and the audio amplifier already uses 38/39/40.

For the LCD, keep the project's existing conflict-free SPI assignment:

| LCD signal | ESP32-S3 |
| --- | --- |
| SCK / CLK | GPIO14 |
| MOSI / SDI | GPIO21 |
| CS | GPIO47 |
| DC / RS / A0 | GPIO41 |
| RESET / RST | GPIO42 |
| GND | GND |
| VCC | 3V3 for the first test |
| LED / BL | 3V3 for the first test |
| LCD MISO / SDO | not connected |

This is the same five-GPIO allocation already used by `firmware/esp32s3/main/board_display.h`, so it does not collide with the camera or MAX98357A.

## 3. Common ILI9341 14-pin order — VERIFY ON YOUR MODULE

If, and only if, the TFT rear silkscreen confirms the common ILI9341/XPT2046 order, wire it as follows:

| TFT pin | Typical label | ESP32-S3 | Status |
| ---: | --- | --- | --- |
| 1 | VCC | 3V3 | LCD power |
| 2 | GND | GND | common ground |
| 3 | CS | GPIO47 | LCD CS |
| 4 | RESET / RST | GPIO42 | LCD reset |
| 5 | DC/RS / DC | GPIO41 | data/command |
| 6 | SDI / MOSI | GPIO21 | SPI MOSI |
| 7 | SCK / CLK | GPIO14 | SPI clock |
| 8 | LED | 3V3 | backlight |
| 9 | SDO / MISO | leave open | LCD reads unused |
| 10 | T_CLK | GPIO14 | optional touch, shared clock |
| 11 | T_CS | GPIO1 | optional touch CS |
| 12 | T_DIN | GPIO21 | optional touch, shared MOSI |
| 13 | T_DO | GPIO3 | optional touch data into ESP32 |
| 14 | T_IRQ | leave open | optional touch IRQ |

**Do the LCD-only test first: pins 1–8, leaving 9–14 disconnected.** DrowsyGuard does not need touch to detect drowsiness or show the live UI.

GPIO3 is a boot-strapping pin. If touch is later added, use GPIO3 only as the high-impedance `T_DO` input. If the board stops booting, disconnect TFT pin 13 immediately.

## 4. Breadboard procedure

1. Disconnect USB.
2. Insert the ESP32 so its two header rows straddle the breadboard centre trench and every pin has an accessible hole in the same numbered node.
3. Keep the TFT beside the breadboard or mount its single header on an outside row so the screen body does not cover the jumper holes.
4. Connect GND first.
5. Connect TFT VCC and LED to 3V3.
6. Connect GPIO14→SCK, GPIO21→MOSI, GPIO47→CS, GPIO41→DC, GPIO42→RST.
7. Trace every wire from printed label to printed label before connecting USB.
8. Power on and test the LCD before adding touch or audio.

Breadboard rule: the five holes A–E of one numbered row are one node; F–J are another node. The centre trench separates them. Long power rails may be split in the middle depending on the breadboard; verify continuity before relying on the rail.

## 5. Camera

The OV5640 is already connected by FPC ribbon; no jumper wire is required. Keep the existing board DVP pin map unless the new board is proven to be a different PCB revision. The current project map is:

| Camera signal | GPIO | Camera signal | GPIO |
| --- | ---: | --- | ---: |
| XCLK | 15 | D7 | 16 |
| SIOD / SDA | 4 | D6 | 17 |
| SIOC / SCL | 5 | D5 | 18 |
| VSYNC | 6 | D4 | 12 |
| HREF | 7 | D3 | 10 |
| PCLK | 13 | D2 | 8 |
| D1 | 9 | D0 | 11 |

The firmware must enable `CONFIG_OV5640_SUPPORT=y`. The N16R8 board must also boot with 8 MB octal PSRAM detected.

## 6. Existing audio wiring remains unchanged

| MAX98357A | ESP32-S3 |
| --- | --- |
| VIN | 5V |
| GND | GND |
| BCLK | GPIO39 |
| LRC / WS | GPIO38 |
| DIN | GPIO40 |
| GAIN | leave floating |
| SD / SD_MODE | leave floating unless measured in shutdown |

Connect the 4 Ω / 3 W speaker only across the amplifier output terminals. Neither speaker lead goes to ESP32 ground because the MAX98357A output is bridged.

## 7. Firmware work required after the rear TFT label is confirmed

The current repository is written for the old 1.8-inch ST7735S panel. The new display requires a panel-driver change before it can render correctly. If the rear label confirms **ILI9341 240×320**, update:

- `board_display.h`: resolution to 240×320; keep GPIO14/21/47/41/42.
- `board_display.cpp`: replace ST7735 construction with ILI9341 construction and set panel orientation.
- `idf_component.yml`: replace the ST7735 component with Espressif's ILI9341 component.
- `sdkconfig.defaults`: add `CONFIG_OV5640_SUPPORT=y`.
- tutorial/tests: update the expected display type and dimensions.

Do not merge that driver change merely from the front photograph; ILI9341 and other 2.8-inch SPI modules can look almost identical.

## 8. First power-on diagnosis

**Backlight dark:** verify VCC→3V3, LED→3V3 and GND→GND, then inspect solder joints.

**Bright white screen:** backlight works but the controller is not initialized. Verify CS GPIO47, RESET GPIO42, DC GPIO41, SCK GPIO14 and MOSI GPIO21, then confirm the rear controller label.

**`esp_camera_init failed`:** reseat the camera ribbon, enable OV5640 support, confirm 8 MB octal PSRAM, and do not substitute an AI-Thinker ESP32-CAM pin map.

**Board resets under load:** use a short known-good USB data cable / adequate 5 V supply. Keep all grounds common.

## Safety

DrowsyGuard is a research prototype, not a certified automotive safety device. Bench-test the complete system while stationary before any vehicle trial. Never adjust wiring or read the serial console while driving.
