#pragma once
/*
Browser-facing preview and telemetry. This is what replaced the SPI panel.

Why a browser instead of a screen:
  * The panel cost five GPIOs, a 150 KB PSRAM framebuffer and a per-frame blit,
    and it could only ever show 240x320 pixels of 8-pixel-tall text to one person
    sitting in front of it.
  * The detection numbers that actually matter for tuning - PERCLOS, the fused
    risk score, event rates, the face box, frame timing - are far more readable on
    a phone than on a 1.8" panel, and can be read while the driver is driving.
  * A browser is a debug console, a demo tool and a thesis screenshot source at
    once, with no extra hardware in the loop.

Two HTTP servers, not one, and that is deliberate. esp_http_server serves requests
from a single task per instance, so a handler that streams for minutes blocks every
other request on that instance. The MJPEG stream therefore lives on its own
instance on port 81 and the control API stays responsive on port 80. This is the
same split the esp32-camera reference web server uses, for the same reason.

Frame handoff never blocks the detection loop. main.cpp hands over a pointer to the
camera frame; this module copies it into one of two PSRAM snapshot buffers and
returns. JPEG encoding - tens of milliseconds of pure CPU - happens later, in the
stream task, on a buffer the producer is no longer writing to. A drowsiness
detector that stutters because someone opened a web page would be a bad trade.
*/

#include <cstddef>
#include <cstdint>

#include "behavior.h"

// Ports. 80 serves the page and the JSON API, 81 serves only /stream.
#define WEB_PORT_CONTROL 80
#define WEB_PORT_STREAM 81

// JPEG quality in libjpeg's 1..100 scale (NOT the sensor's inverted 0..63 scale).
// 80 keeps eyelid detail visible - the point of the preview is to see whether the
// eyes are shut - while staying around 12 KB a frame at 240x240.
#define WEB_JPEG_QUALITY_DEFAULT 80
#define WEB_STREAM_FPS_DEFAULT 12

// Ceiling for one encoded frame. 240x240 at quality 100 is comfortably under this;
// a frame that overflows is dropped with a log line rather than truncated into a
// corrupt JPEG that the browser renders as a grey block.
#define WEB_JPEG_BUFFER_BYTES (48 * 1024)

// Snapshot of everything the page displays. Plain POD: main.cpp fills one in on
// each frame and publishes it, and the API handlers copy it out under a mutex.
struct WebStatus {
    bool camera_ok = false;
    bool models_ok = false;
    bool eye_model_ok = false;

    bool face_found = false;
    bool face_held = false;      // box is the last good one, not a fresh detection
    int face_x = 0, face_y = 0, face_w = 0, face_h = 0;
    float face_score = 0.0f;
    int frame_w = 0, frame_h = 0;

    BehaviorState state{};
    FaceGeometry geom{};
    float trigger = 0.55f;       // RiskFilter threshold, drawn as a mark on the bar
    int streak = 0;
    int required = 0;

    float fps = 0.0f;
    bool alerting = false;
    const char *alert_text = nullptr;   // must point at a string literal
    const char *alert_reason = nullptr; // ditto
    uint32_t alert_count = 0;
    uint32_t frames = 0;
};

// Allocates the snapshot and JPEG buffers and starts both servers. Safe to call
// before the camera is up: the page then reports "no camera" instead of failing to
// load, which is the difference between diagnosing a ribbon cable and guessing.
bool web_server_start();

// True while at least one browser is pulling /stream. main.cpp uses this to skip
// the frame copy entirely when nobody is watching.
bool web_server_has_viewer();

// Copies one camera frame into the next free snapshot buffer. Returns false if it
// was skipped (no viewer, or both buffers in use); skipping is normal and not an
// error. `len` is the frame length in bytes, i.e. width * height * 2 for RGB565.
bool web_server_publish_frame(const uint8_t *rgb565, int width, int height, size_t len);

void web_server_publish_status(const WebStatus &status);
