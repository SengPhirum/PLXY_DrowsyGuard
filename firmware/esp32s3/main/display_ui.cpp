#include "display_ui.h"

#include <cstdio>
#include <cstring>

static uint16_t *g_fb = nullptr;
static int g_w = 0, g_h = 0;
static DisplayBlit g_blit = nullptr;

static const DisplayTheme kTheme{
    /*bg*/ rgb565(12, 14, 18),   /*ok*/ rgb565(63, 185, 80),
    /*warn*/ rgb565(210, 153, 34), /*danger*/ rgb565(248, 81, 73),
    /*text*/ rgb565(230, 237, 243), /*dim*/ rgb565(120, 132, 145),
};

// Compact 5x7 font: digits, uppercase, and the few symbols the UI needs. Keeping the
// glyph set small matters more than completeness on a 240x240 panel.
static const char kGlyphOrder[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ .:%/-!";
static const uint8_t kGlyphs[][5] = {
    {0x3E,0x51,0x49,0x45,0x3E},{0x00,0x42,0x7F,0x40,0x00},{0x42,0x61,0x51,0x49,0x46},
    {0x21,0x41,0x45,0x4B,0x31},{0x18,0x14,0x12,0x7F,0x10},{0x27,0x45,0x45,0x45,0x39},
    {0x3C,0x4A,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},{0x36,0x49,0x49,0x49,0x36},
    {0x06,0x49,0x49,0x29,0x1E},
    {0x7E,0x11,0x11,0x11,0x7E},{0x7F,0x49,0x49,0x49,0x36},{0x3E,0x41,0x41,0x41,0x22},
    {0x7F,0x41,0x41,0x22,0x1C},{0x7F,0x49,0x49,0x49,0x41},{0x7F,0x09,0x09,0x09,0x01},
    {0x3E,0x41,0x49,0x49,0x7A},{0x7F,0x08,0x08,0x08,0x7F},{0x00,0x41,0x7F,0x41,0x00},
    {0x20,0x40,0x41,0x3F,0x01},{0x7F,0x08,0x14,0x22,0x41},{0x7F,0x40,0x40,0x40,0x40},
    {0x7F,0x02,0x0C,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},{0x3E,0x41,0x41,0x41,0x3E},
    {0x7F,0x09,0x09,0x09,0x06},{0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},
    {0x46,0x49,0x49,0x49,0x31},{0x01,0x01,0x7F,0x01,0x01},{0x3F,0x40,0x40,0x40,0x3F},
    {0x1F,0x20,0x40,0x20,0x1F},{0x3F,0x40,0x38,0x40,0x3F},{0x63,0x14,0x08,0x14,0x63},
    {0x07,0x08,0x70,0x08,0x07},{0x61,0x51,0x49,0x45,0x43},
    {0x00,0x00,0x00,0x00,0x00},{0x00,0x60,0x60,0x00,0x00},{0x00,0x36,0x36,0x00,0x00},
    {0x62,0x64,0x08,0x13,0x23},{0x20,0x10,0x08,0x04,0x02},{0x08,0x08,0x08,0x08,0x08},
    {0x00,0x00,0x5F,0x00,0x00},
};

static inline void px(int x, int y, uint16_t c) {
    if (x < 0 || y < 0 || x >= g_w || y >= g_h) return;
    g_fb[y * g_w + x] = c;
}

static void fill_rect(int x, int y, int w, int h, uint16_t c) {
    for (int j = y; j < y + h; ++j)
        for (int i = x; i < x + w; ++i) px(i, j, c);
}

static void frame_rect(int x, int y, int w, int h, uint16_t c, int t = 1) {
    fill_rect(x, y, w, t, c);
    fill_rect(x, y + h - t, w, t, c);
    fill_rect(x, y, t, h, c);
    fill_rect(x + w - t, y, t, h, c);
}

static int glyph_index(char ch) {
    if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 'a' + 'A');
    for (int i = 0; kGlyphOrder[i]; ++i)
        if (kGlyphOrder[i] == ch) return i;
    return -1;
}

void display_ui_draw_text(const char *s, int x, int y, uint16_t colour, int scale) {
    if (s == nullptr || scale < 1) return;
    int cx = x;
    for (const char *p = s; *p; ++p) {
        const int gi = glyph_index(*p);
        if (gi >= 0) {
            for (int col = 0; col < 5; ++col) {
                const uint8_t bits = kGlyphs[gi][col];
                for (int row = 0; row < 7; ++row) {
                    if (bits & (1 << row))
                        fill_rect(cx + col * scale, y + row * scale, scale, scale, colour);
                }
            }
        }
        cx += 6 * scale;
    }
}

// Text width in pixels, so labels can be right-aligned instead of pinned to a
// hardcoded offset. On a 128 px panel a hardcoded offset overlaps the left label.
static inline int text_w(const char *s, int scale) {
    int n = 0;
    for (const char *p = s; *p; ++p) ++n;
    return n * 6 * scale;
}

static void draw_text_right(const char *s, int right_x, int y, uint16_t colour, int scale) {
    display_ui_draw_text(s, right_x - text_w(s, scale), y, colour, scale);
}

static void draw_bar(int x, int y, int w, int h, float frac, uint16_t colour) {
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;
    fill_rect(x, y, w, h, rgb565(24, 28, 34));
    fill_rect(x, y, static_cast<int>(w * frac), h, colour);
}

bool display_ui_init(uint16_t *framebuffer, int width, int height, DisplayBlit blit) {
    if (framebuffer == nullptr || width <= 0 || height <= 0) return false;
    g_fb = framebuffer;
    g_w = width;
    g_h = height;
    g_blit = blit;
    fill_rect(0, 0, g_w, g_h, kTheme.bg);
    return true;
}

void display_ui_render(const DisplayInput &in) {
    if (g_fb == nullptr) return;
    fill_rect(0, 0, g_w, g_h, kTheme.bg);

    // A 128x160 panel has to fit the same seven rows of status into 88 fewer pixels
    // than a 240x240 one, so the preview gives up ten percentage points of height
    // and the banner drops to single-size text. Everything else is width-relative.
    const bool narrow = g_w < 200;

    // --- camera preview, nearest-neighbour scaled into the top area ---
    const int preview_h = (g_h * (narrow ? 45 : 55)) / 100;
    if (in.preview != nullptr && in.preview_w > 0 && in.preview_h > 0) {
        for (int y = 0; y < preview_h; ++y) {
            const int sy = y * in.preview_h / preview_h;
            for (int x = 0; x < g_w; ++x) {
                const int sx = x * in.preview_w / g_w;
                uint16_t v = in.preview[sy * in.preview_w + sx];
                if (in.preview_swap_bytes) v = static_cast<uint16_t>((v >> 8) | (v << 8));
                px(x, y, v);
            }
        }
    } else {
        fill_rect(0, 0, g_w, preview_h, rgb565(20, 24, 30));
        display_ui_draw_text("NO CAMERA", g_w / 2 - 27, preview_h / 2 - 4, kTheme.dim, 1);
    }

    // --- tracked face box, so the driver can see it is locked on ---
    if (in.face_side > 0 && in.preview_w > 0) {
        const float sx = static_cast<float>(g_w) / in.preview_w;
        const float sy = static_cast<float>(preview_h) / in.preview_h;
        const int bx = static_cast<int>(in.face_x * sx);
        const int by = static_cast<int>(in.face_y * sy);
        const int bw = static_cast<int>(in.face_side * sx);
        const int bh = static_cast<int>(in.face_side * sy);
        const uint16_t c = in.face_held ? kTheme.warn : (in.state.eye_closed >= 0.5f ? kTheme.danger : kTheme.ok);
        frame_rect(bx, by, bw, bh, c, 2);
    } else {
        display_ui_draw_text("NO FACE", 4, 4, kTheme.danger, 1);
    }

    int y = preview_h + 4;

    // --- eye state + risk, the two things worth showing continuously ---
    const bool closed = in.state.eye_closed >= 0.5f;
    display_ui_draw_text(closed ? "EYES CLOSED" : "EYES OPEN", 4, y,
                         closed ? kTheme.danger : kTheme.ok, 1);
    char buf[24];
    // PERCLOS as a percentage. The label shortens on a narrow panel so it cannot
    // collide with "EYES CLOSED" on the same row.
    const int pc = static_cast<int>(in.state.perclos * 100.0f + 0.5f);
    std::snprintf(buf, sizeof(buf), narrow ? "PC %d%%" : "PERCLOS %d%%", pc);
    draw_text_right(buf, g_w - 4, y, kTheme.text, 1);
    y += 10;
    draw_bar(4, y, g_w - 8, 6, in.state.perclos,
             in.state.perclos > 0.5f ? kTheme.danger : kTheme.warn);
    y += 12;

    display_ui_draw_text("RISK", 4, y, kTheme.dim, 1);
    std::snprintf(buf, sizeof(buf), "%d%%", static_cast<int>(in.state.score * 100.0f + 0.5f));
    draw_text_right(buf, g_w - 4, y, kTheme.text, 1);
    y += 10;
    draw_bar(4, y, g_w - 8, 8, in.state.score,
             in.state.score >= in.trigger ? kTheme.danger : kTheme.ok);
    // Trigger mark, so the driver can see how close they are to a warning.
    const int tx = 4 + static_cast<int>((g_w - 8) * in.trigger);
    fill_rect(tx, y - 2, 2, 12, kTheme.text);
    y += 14;

    // --- behaviour cues ---
    std::snprintf(buf, sizeof(buf), "YAWN %d  NOD %d",
                  static_cast<int>(in.state.yawn_rate + 0.5f),
                  static_cast<int>(in.state.nod_rate + 0.5f));
    display_ui_draw_text(buf, 4, y, kTheme.dim, 1);
    if (in.state.mouth_open) draw_text_right("MOUTH", g_w - 4, y, kTheme.warn, 1);
    y += 10;
    if (in.state.head_down) display_ui_draw_text("HEAD DOWN", 4, y, kTheme.warn, 1);
    else if (!in.state.baselines_ready) display_ui_draw_text("CALIBRATING", 4, y, kTheme.dim, 1);
    else {
        std::snprintf(buf, sizeof(buf), "FPS %d", static_cast<int>(in.fps + 0.5f));
        display_ui_draw_text(buf, 4, y, kTheme.dim, 1);
    }
    y += 12;

    // --- most recent event, named, so a warning is explainable ---
    const char *ev = nullptr;
    if (in.no_model) ev = "NO MODEL - PREVIEW";
    else if (in.state.events & EVENT_SNEEZE) ev = "SNEEZE IGNORED";
    else if (in.state.events & EVENT_MICROSLEEP) ev = "MICROSLEEP";
    else if (in.state.events & EVENT_YAWN) ev = "YAWN";
    else if (in.state.events & EVENT_NOD) ev = "NOD";
    else if (in.state.events & EVENT_LONG_BLINK) ev = "SLOW BLINK";
    if (ev != nullptr) display_ui_draw_text(ev, 4, y, kTheme.warn, 1);

    // --- alert banner ---
    if (in.alerting) {
        const int scale = narrow ? 1 : 2;
        const int bh = narrow ? 22 : 34;
        fill_rect(0, g_h - bh, g_w, bh, kTheme.danger);
        const char *msg = (in.alert_text != nullptr) ? in.alert_text : "WAKE UP";
        display_ui_draw_text(msg, (g_w - text_w(msg, scale)) / 2,
                             g_h - bh + (bh - 7 * scale) / 2, rgb565(255, 255, 255), scale);
    }

    if (g_blit != nullptr) g_blit(g_fb, g_w, g_h);
}
