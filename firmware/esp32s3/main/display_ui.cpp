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

static void draw_bar(int x, int y, int w, int h, float frac, uint16_t colour) {
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;
    fill_rect(x, y, w, h, rgb565(24, 28, 34));
    fill_rect(x, y, static_cast<int>(w * frac), h, colour);
}

// --- overlay helpers -------------------------------------------------------
// The readouts sit on top of the live frame now, so they have to stay legible
// over whatever the camera happens to be pointing at - a white shirt or a
// sunlit windscreen included.

// 50% blend toward black, the standard RGB565 trick: shift each field down one
// and mask the bit that fell in from the field above.
static inline uint16_t half(uint16_t v) {
    return static_cast<uint16_t>((v >> 1) & 0x7BEF);
}

// Darkens the video behind a band of text instead of hiding it. Two passes
// would be cheaper to read but would black the frame out; one keeps the driver
// visible underneath.
static void scrim_rect(int x, int y, int w, int h) {
    for (int j = y; j < y + h; ++j) {
        if (j < 0 || j >= g_h) continue;
        uint16_t *row = g_fb + j * g_w;
        for (int i = x; i < x + w; ++i) {
            if (i < 0 || i >= g_w) continue;
            row[i] = half(row[i]);
        }
    }
}

// A one-pixel black drop shadow. Cheap, and it is what keeps thin 5x7 glyphs
// off a busy background from dissolving into it.
static void draw_text_shadow(const char *s, int x, int y, uint16_t colour, int scale) {
    display_ui_draw_text(s, x + 1, y + 1, rgb565(0, 0, 0), scale);
    display_ui_draw_text(s, x, y, colour, scale);
}

static void draw_text_right_shadow(const char *s, int right_x, int y, uint16_t colour,
                                   int scale) {
    draw_text_shadow(s, right_x - text_w(s, scale), y, colour, scale);
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

    // Full-bleed layout: the camera frame covers the whole panel and every
    // readout is drawn on top of it inside a darkened band. The driver gets the
    // largest view of themselves the glass can give; the numbers ride along
    // instead of taking half the screen. No full-screen background fill either -
    // the preview writes every pixel anyway.
    const bool narrow = g_w < 200;

    // --- camera preview, scaled to cover the whole panel ---
    // "Cover", not "stretch". The sensor frame is square (240x240) and the panel
    // is portrait, so the source is centre-cropped to the panel's aspect ratio
    // before scaling. Stretching would make every face look long, and face
    // geometry is exactly what the yawn and nod thresholds are tuned on.
    int cw = in.preview_w, ch = in.preview_h, cx0 = 0, cy0 = 0;
    if (in.preview != nullptr && in.preview_w > 0 && in.preview_h > 0) {
        if (cw * g_h > ch * g_w) cw = ch * g_w / g_h;   // too wide: trim the sides
        else                     ch = cw * g_h / g_w;   // too tall: trim top and bottom
        cx0 = (in.preview_w - cw) / 2;
        cy0 = (in.preview_h - ch) / 2;

        for (int y = 0; y < g_h; ++y) {
            const uint16_t *src = in.preview + (cy0 + y * ch / g_h) * in.preview_w;
            uint16_t *dst = g_fb + y * g_w;
            for (int x = 0; x < g_w; ++x) {
                const uint16_t v = src[cx0 + x * cw / g_w];
                dst[x] = in.preview_swap_bytes
                             ? static_cast<uint16_t>((v >> 8) | (v << 8))
                             : v;
            }
        }
    } else {
        fill_rect(0, 0, g_w, g_h, kTheme.bg);
        const char *nc = "NO CAMERA";
        display_ui_draw_text(nc, (g_w - text_w(nc, 1)) / 2, g_h / 2 - 4, kTheme.dim, 1);
    }

    char buf[24];

    // --- top band: eye state and PERCLOS ---
    // Band heights are absolute, not proportional: the text inside them is a
    // fixed 7 px tall, so a percentage would only ever add slack or clip rows.
    const int top_h = 21;
    scrim_rect(0, 0, g_w, top_h);
    const bool closed = in.state.eye_closed >= 0.5f;
    draw_text_shadow(closed ? "EYES CLOSED" : "EYES OPEN", 3, 2,
                     closed ? kTheme.danger : kTheme.ok, 1);
    // The label shortens on a narrow panel so it cannot collide with
    // "EYES CLOSED" on the same row.
    const int pc = static_cast<int>(in.state.perclos * 100.0f + 0.5f);
    std::snprintf(buf, sizeof(buf), narrow ? "PC %d%%" : "PERCLOS %d%%", pc);
    draw_text_right_shadow(buf, g_w - 3, 2, kTheme.text, 1);
    draw_bar(3, 12, g_w - 6, 5, in.state.perclos,
             in.state.perclos > 0.5f ? kTheme.danger : kTheme.warn);

    // --- bottom band: risk, behaviour cues, most recent event ---
    const int bot_h = 50;
    const int by0 = g_h - bot_h;
    scrim_rect(0, by0, g_w, bot_h);

    int y = by0 + 3;
    draw_text_shadow("RISK", 3, y, kTheme.dim, 1);
    std::snprintf(buf, sizeof(buf), "%d%%", static_cast<int>(in.state.score * 100.0f + 0.5f));
    draw_text_right_shadow(buf, g_w - 3, y, kTheme.text, 1);
    y += 9;
    draw_bar(3, y, g_w - 6, 8, in.state.score,
             in.state.score >= in.trigger ? kTheme.danger : kTheme.ok);
    // Trigger mark, so the driver can see how close they are to a warning.
    const int tx = 3 + static_cast<int>((g_w - 6) * in.trigger);
    fill_rect(tx, y - 2, 2, 12, kTheme.text);
    y += 12;

    // --- behaviour cues ---
    std::snprintf(buf, sizeof(buf), "YAWN %d  NOD %d",
                  static_cast<int>(in.state.yawn_rate + 0.5f),
                  static_cast<int>(in.state.nod_rate + 0.5f));
    draw_text_shadow(buf, 3, y, kTheme.dim, 1);
    if (in.state.mouth_open) draw_text_right_shadow("MOUTH", g_w - 3, y, kTheme.warn, 1);
    y += 9;
    if (in.state.head_down) draw_text_shadow("HEAD DOWN", 3, y, kTheme.warn, 1);
    else if (!in.state.baselines_ready) draw_text_shadow("CALIBRATING", 3, y, kTheme.dim, 1);
    else {
        std::snprintf(buf, sizeof(buf), "FPS %d", static_cast<int>(in.fps + 0.5f));
        draw_text_shadow(buf, 3, y, kTheme.dim, 1);
    }
    y += 9;

    // --- most recent event, named, so a warning is explainable ---
    const char *ev = nullptr;
    if (in.no_model) ev = "NO MODEL - PREVIEW";
    else if (in.state.events & EVENT_SNEEZE) ev = "SNEEZE IGNORED";
    else if (in.state.events & EVENT_MICROSLEEP) ev = "MICROSLEEP";
    else if (in.state.events & EVENT_YAWN) ev = "YAWN";
    else if (in.state.events & EVENT_NOD) ev = "NOD";
    else if (in.state.events & EVENT_LONG_BLINK) ev = "SLOW BLINK";
    // Last in the chain, not first: this is a standing condition rather than an
    // event, so a real yawn or nod still gets the line when it happens.
    else if (in.no_eye_model) ev = "NO EYE MODEL";
    if (ev != nullptr) draw_text_shadow(ev, 3, y, kTheme.warn, 1);

    // --- tracked face box, so the driver can see it is locked on ---
    // Mapped through the same crop as the preview, otherwise the box drifts off
    // the face by exactly the cropped margin.
    if (in.face_side > 0 && cw > 0 && ch > 0) {
        const float fx = static_cast<float>(g_w) / cw;
        const float fy = static_cast<float>(g_h) / ch;
        const uint16_t c = in.face_held ? kTheme.warn : (closed ? kTheme.danger : kTheme.ok);
        frame_rect(static_cast<int>((in.face_x - cx0) * fx),
                   static_cast<int>((in.face_y - cy0) * fy),
                   static_cast<int>(in.face_side * fx),
                   static_cast<int>(in.face_side * fy), c, 2);
    } else if (!in.no_model) {
        // Suppressed in preview-only mode: with no detector bound "NO FACE"
        // would be permanent and would sit in the middle of the video. The
        // event line already says NO MODEL.
        const char *nf = "NO FACE";
        draw_text_shadow(nf, (g_w - text_w(nf, 1)) / 2, (top_h + by0) / 2 - 4,
                         kTheme.danger, 1);
    }

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
