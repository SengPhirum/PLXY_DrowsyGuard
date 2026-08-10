#pragma once

// On-device driver-facing UI.
//
// Purpose: the driver should be able to see that the system is watching and why it
// warned. So the screen shows the live preview with the tracked face, what the eyes
// are doing, the accumulating risk, and a full-width banner when an alert fires.
//
// Rendering is plain RGB565 into a caller-owned framebuffer, then one blit. No LVGL,
// to keep RAM and build complexity down on an ESP32-S3 that is already running two
// neural networks per frame.
//
// Panel init is deliberately NOT here: like board_camera.h, the exact LCD controller
// and pins depend on the board. Provide a blit callback instead.

#include <cstddef>
#include <cstdint>

#include "behavior.h"

struct DisplayTheme {
    uint16_t bg;
    uint16_t ok;
    uint16_t warn;
    uint16_t danger;
    uint16_t text;
    uint16_t dim;
};

// RGB565 helper.
constexpr uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
    return static_cast<uint16_t>(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

struct DisplayInput {
    // Optional camera preview, RGB565, preview_w x preview_h. May be null.
    const uint16_t *preview = nullptr;
    int preview_w = 0;
    int preview_h = 0;

    bool face_found = false;
    bool face_held = false;
    int face_x = 0, face_y = 0, face_side = 0;   // in preview coordinates

    BehaviorState state{};
    float trigger = 0.72f;      // RiskFilter trigger, drawn as a mark on the risk bar
    int streak = 0;
    int required = 8;
    bool alerting = false;      // an alert is currently being annunciated
    const char *alert_text = nullptr;
    float fps = 0.0f;
};

// Blit callback: copy a full RGB565 framebuffer to the panel.
typedef void (*DisplayBlit)(const uint16_t *fb, int w, int h);

bool display_ui_init(uint16_t *framebuffer, int width, int height, DisplayBlit blit);
void display_ui_render(const DisplayInput &in);

// Exposed for tests/tools.
void display_ui_draw_text(const char *s, int x, int y, uint16_t colour, int scale);
