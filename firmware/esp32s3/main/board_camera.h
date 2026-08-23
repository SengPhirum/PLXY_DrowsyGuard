#pragma once
/*
Verified pin map for the board selected for this thesis:

  ESP32-S3-WROOM-1 N16R8 "ESP32-S3-CAM" development board + OV3660/OV5640 sensor
  (khmeres.com item 2991; the same board ships as keyestudio MB0184 and as the
  Freenove ESP32-S3-WROOM CAM board).

The sensor is not guaranteed to be the one on the listing. The unit verified on
2026-08-23 shipped an **OV5640**, and esp32-camera says so on the way past:

    I (1776) ov3660: Mismatch PID=0x5640
    I (1777) camera: Detected OV5640 camera

That is a probe miss, not an error - the driver then binds the right sensor and
240x240 RGB565 comes out the same. Nothing below changes: the DVP pin map is the
board's, not the sensor's. Just keep CONFIG_OV5640_SUPPORT enabled (it is pinned
in sdkconfig.defaults) or the same board will look like a dead camera.

The DVP map below is byte-for-byte the ESP32-S3-EYE map in arduino-esp32's
camera_pins.h and in ESP-WHO, which is why the ESP-DL vision examples run on this
board unmodified. ESP32-S3-EYE is the board docs/FIRMWARE_PIPELINE.md recommended,
so nothing downstream of here changes.

Do NOT paste an ESP32-CAM (classic ESP32, AI-Thinker) pin map in here: it drives
GPIOs that this module reserves for its octal PSRAM, and the failure looks like a
flaky camera rather than a wiring error.

Reserved on N16R8 and unavailable no matter what the silkscreen says:
  GPIO 33..37  SPI flash + octal PSRAM
  GPIO 19, 20  native USB D-/D+ (free only if you never use the USB-OTG port)
  GPIO 43, 44  UART0 console
  GPIO 38..40  microSD slot, SDMMC 1-line bus (see board_sdcard.h)
*/

#include "esp_camera.h"

// --- DVP camera, fixed by the board ---
#define CAM_PIN_PWDN   -1   // not routed; the sensor is always powered
#define CAM_PIN_RESET  -1   // not routed; reset via SCCB
#define CAM_PIN_XCLK   15
#define CAM_PIN_SIOD    4   // SCCB SDA
#define CAM_PIN_SIOC    5   // SCCB SCL
#define CAM_PIN_D7     16   // Y9
#define CAM_PIN_D6     17   // Y8
#define CAM_PIN_D5     18   // Y7
#define CAM_PIN_D4     12   // Y6
#define CAM_PIN_D3     10   // Y5
#define CAM_PIN_D2      8   // Y4
#define CAM_PIN_D1      9   // Y3
#define CAM_PIN_D0     11   // Y2
#define CAM_PIN_VSYNC   6
#define CAM_PIN_HREF    7
#define CAM_PIN_PCLK   13

// 240x240 RGB565 is deliberate: it is what the ESP-DL face detector expects to be
// fed (it letterboxes to 120x160 internally), it is square so the preview crop is
// not biased, and two frames fit trivially in 8 MB PSRAM.
#define CAM_FRAME_W 240
#define CAM_FRAME_H 240

// esp32-camera emits RGB565 with the two bytes of each pixel in the opposite order
// to the host framebuffer, so the preview copy swaps them. If the preview comes out
// in psychedelic colours - red and blue traded - set this to 0.
#define CAM_RGB565_BYTE_SWAP 1

// --- orientation ---
// Set to 1 when the camera module is physically mounted upside down, which is easy
// to do: the ribbon will seat either way up.
//
// This rotates in the SENSOR, and that is now correct. It used to be wrong - an
// earlier revision of this file said "rotate the panel, never the sensor", because
// a 180-degree panel rotation fixed the view for the driver while leaving ESP-DL an
// upright frame. There is no panel any more. The browser could rotate the preview
// in CSS, but the models read these same bytes, and **a face detector does not
// detect upside-down faces** - it would simply stop finding anyone, which reads as
// a broken camera. So the frame is corrected before anything sees it.
#define CAM_ROTATE_180 1

// Mirror left-right, so the driver sees themselves the way a mirror shows them
// rather than the way a photograph does. Purely cosmetic - detection is unaffected,
// since a mirrored face is still a face.
#define CAM_SELFIE_MIRROR 1

inline camera_config_t board_camera_config() {
    camera_config_t c = {};
    c.pin_pwdn = CAM_PIN_PWDN;
    c.pin_reset = CAM_PIN_RESET;
    c.pin_xclk = CAM_PIN_XCLK;
    c.pin_sccb_sda = CAM_PIN_SIOD;
    c.pin_sccb_scl = CAM_PIN_SIOC;
    c.pin_d7 = CAM_PIN_D7;
    c.pin_d6 = CAM_PIN_D6;
    c.pin_d5 = CAM_PIN_D5;
    c.pin_d4 = CAM_PIN_D4;
    c.pin_d3 = CAM_PIN_D3;
    c.pin_d2 = CAM_PIN_D2;
    c.pin_d1 = CAM_PIN_D1;
    c.pin_d0 = CAM_PIN_D0;
    c.pin_vsync = CAM_PIN_VSYNC;
    c.pin_href = CAM_PIN_HREF;
    c.pin_pclk = CAM_PIN_PCLK;

    c.xclk_freq_hz = 20000000;   // 20 MHz; drop to 10 MHz if the sensor misbehaves
    c.ledc_timer = LEDC_TIMER_0;
    c.ledc_channel = LEDC_CHANNEL_0;

    c.pixel_format = PIXFORMAT_RGB565;   // no JPEG decode step in the frame budget
    c.frame_size = FRAMESIZE_240X240;
    c.jpeg_quality = 12;                 // unused for RGB565
    c.fb_count = 2;                      // one in flight, one being processed
    c.fb_location = CAMERA_FB_IN_PSRAM;  // requires CONFIG_SPIRAM=y
    c.grab_mode = CAMERA_GRAB_LATEST;    // drop stale frames: latency beats coverage
    return c;
}

// Sensor tuning for a driver-facing camera: the face is close, backlit through a
// windscreen, and mirrored relative to how a driver expects to see themselves.
//
// Written for the OV3660 the board was sold with, then corrected for the OV5640 it
// actually carries (2026-08-23) after the preview showed periodic white flashes.
// The suspect, and the reason aec2 is now explicitly off: on the OV5640 `set_aec2`
// toggles bit 2 of register 0x3A00, which is the sensor's long-exposure "night
// mode". With it on, the AEC is allowed to stretch exposure far past the frame
// period in dim scenes and then snap back, and the overshoot arrives as a handful
// of blown-out frames - which is exactly what a white flash in the preview is.
//
// Left alone deliberately: `set_gainceiling`. On the OV5640 that setter writes a
// raw 10-bit gain ceiling to 0x3A18/0x3A19, but its argument is the OV2640-era
// `gainceiling_t` enum, where GAINCEILING_4X is the value 1. Passing the enum
// would therefore cap the gain at 1x and make the image nearly black. Better to
// leave the sensor default than to write a number that means something else here.
inline void board_camera_tune() {
    sensor_t *s = esp_camera_sensor_get();
    if (s == nullptr) return;
    // A 180-degree rotation is a flip on both axes, so it composes with the selfie
    // mirror rather than replacing it: mirroring left-right and then rotating puts
    // the horizontal flip back where it started. Hence the XOR - writing
    // hmirror=1,vflip=1 by hand would give a rotated image that is no longer
    // mirrored, which is a different picture from the one intended.
    s->set_hmirror(s, (CAM_SELFIE_MIRROR ^ CAM_ROTATE_180) ? 1 : 0);
    s->set_vflip(s, CAM_ROTATE_180 ? 1 : 0);
    s->set_gain_ctrl(s, 1);      // AGC on: cabin light swings hard
    s->set_exposure_ctrl(s, 1);  // AEC on
    s->set_whitebal(s, 1);
    s->set_saturation(s, -1);    // the models only care about structure

    // --- exposure stability, added for the OV5640 ---
    if (s->set_aec2 != nullptr) s->set_aec2(s, 0);        // no long-exposure night mode
    if (s->set_ae_level != nullptr) s->set_ae_level(s, 0);  // no exposure bias

    // brightness 0, not the +1 the OV3660 had. The +1 was there to lift a backlit
    // face, but on this sensor it pushes highlights into clipping and gives the AEC
    // something to hunt. Bias exposure with set_ae_level(+1) instead if the face
    // comes out too dark - that moves the target, rather than the whole curve.
    s->set_brightness(s, 0);
}
