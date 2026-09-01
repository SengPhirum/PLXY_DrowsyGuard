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

// The stream port serves two things:
//
//   GET /frame   one JPEG, one request. This is what the page uses.
//   GET /stream  the same frames as multipart/x-mixed-replace (MJPEG).
//
// /frame exists because MJPEG in an <img> flashes white between frames on mobile
// browsers - a rendering artifact of multipart/x-mixed-replace, not a fault in the
// stream. Measured on this board: the camera's mean frame luminance never left
// 115-135 while the preview was visibly flashing white, so nothing was wrong with
// the frames themselves. Fetching discrete JPEGs and drawing them into a canvas
// gives the page control of when the picture changes, and it never blanks.
//
// It is also better under load. A request for /frame is short, so two phones share
// the port and each gets half the rate; a request for /stream never ends, so the
// second viewer waits for the first to leave. /stream is kept because it is the
// one thing that works with `curl` and with any generic MJPEG client.

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
    // The five landmarks, in frame pixels and canonical order (image-left eye,
    // image-right eye, nose, mouth corner, mouth corner). Sent so the page can draw
    // the eyes and the mouth rather than just a box round the head: the box says a
    // face was found, these say the thing the cues are actually measured from was
    // found in the right place, which is a different and more useful claim.
    Landmarks lm{};
    // Whether there is a driver at all. Not the same as face_found, which goes false
    // for a frame or two whenever detection misses: this stays true across a held box
    // and only drops when the face has really been given up on.
    bool driver_present = false;

    // Presence as PresenceMonitor sees it, and - separately - whether the device is
    // in a position to judge. Two fields rather than one because "nobody is in the
    // seat" and "the camera stopped" are the same observation and opposite
    // conclusions, and a page that shows only the first would report a hardware
    // failure as an empty cabin. Both point at string literals from
    // presence_state_name() and presence_health_name(); see presence.h.
    const char *presence_state = nullptr;
    const char *health = nullptr;
    float presence_absent_s = 0.0f;      // continuous absence so far, seconds
    float presence_alert_after_s = 0.0f; // the configured threshold, for the page
    uint16_t presence_alerts = 0;        // no-driver announcements since boot

    BehaviorState state{};
    FaceGeometry geom{};
    float trigger = 0.55f;       // RiskFilter threshold, drawn as a mark on the bar
    int streak = 0;
    int required = 0;

    float fps = 0.0f;
    // Where the frame time actually goes. Here because the frame budget in main.cpp
    // was an estimate off a model card for a long time, and when it was finally
    // measured it was wrong by a factor of six. A budget nobody can see is a comment.
    float ms_detect = 0.0f;   // last face detection
    float ms_eye = 0.0f;      // last eye inference, one eye
    // What the detector was handed, and what the plausibility gate threw away. "No
    // face in the frame" and "a face the gate rejected" look identical without this
    // and have completely different fixes.
    bool detect_roi = false;  // searched a crop rather than the whole frame
    int detect_roi_w = 0;     // that crop's side, in pixels
    int detect_rejected = 0;  // candidates that failed face_gate_check()
    // Which check the first failing candidate failed. Points at a string literal.
    const char *detect_reject = nullptr;
    // Mean luminance of the frame, 0-255, plus the extremes. Here because a
    // preview that flashes white and a preview that flashes for some other reason
    // are indistinguishable by eye, and this is the number that separates them:
    // an exposure problem shows as luma spiking toward 255.
    float luma = 0.0f;
    int luma_min = 0, luma_max = 0;
    // Highest mean luminance seen since this field was last reported. A frame that
    // blows out for two frames out of twenty is invisible in a 1 Hz sample of the
    // mean, and two frames is exactly how long a white flash lasts - so the peak is
    // held rather than sampled.
    int luma_peak = 0;
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

// Files one drowsiness event: the frame that triggered it, plus the numbers that
// explain why. Browsable afterwards at /api/events.
//
// Called from the capture loop, so it must not block it. It stages a copy of the
// frame - about 3 ms - and hands the expensive half, the JPEG encode and the SD
// write, to a background task. Returns false when there is no card, when a
// previous capture is still being written (events are 30 s apart in practice, so
// this does not happen), or when the encode failed.
//
// Note it cannot go through the normal snapshot path: that path asks the capture
// loop for a frame, and the capture loop is the caller here - it would be waiting
// on itself.
bool web_server_capture_event(const uint8_t *rgb565, int width, int height, size_t len,
                              float risk, float perclos, const char *reason,
                              uint32_t uptime_ms);
