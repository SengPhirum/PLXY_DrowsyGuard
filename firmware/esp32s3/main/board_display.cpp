#include "board_display.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "soc/gpio_reg.h"
#include "freertos/task.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_ili9341.h"
#include "esp_log.h"

static const char *TAG = "lcd";

static esp_lcd_panel_handle_t s_panel = nullptr;

// The framebuffer holds RGB565 in host (little-endian) order, but the ILI9341
// latches the high byte first. esp_lcd sends the buffer verbatim, so the bytes are
// swapped here on the way out. Swapping a chunk at a time rather than the whole
// frame keeps the DMA source buffers small and in internal RAM (the full 150 KB
// framebuffer itself lives in PSRAM).
static constexpr int CHUNK_ROWS = 16;

// Two chunks, filled alternately, because draw_bitmap does NOT copy and does NOT
// block: esp_lcd_panel_io_spi.c hands colour data to spi_device_queue_trans and
// returns while DMA is still reading it. Filling the next chunk takes tens of
// microseconds; shifting 4 KB at 40 MHz takes ~820 us, so a single shared buffer
// loses that race every time and each band on the panel gets the following band's
// pixels. Two buffers are sufficient (rather than one per chunk) because the next
// chunk's CASET goes out via tx_param, which drains every queued colour transfer
// before its own polling write - so by the time a buffer comes round again, the
// transfer that was reading it has already been waited on.
static uint16_t s_chunk[2][LCD_H_RES * CHUNK_ROWS];
static int s_chunk_idx = 0;

static inline uint16_t *next_chunk() {
    uint16_t *c = s_chunk[s_chunk_idx];
    s_chunk_idx ^= 1;
    return c;
}

static constexpr uint16_t swap16(uint16_t v) {
    return static_cast<uint16_t>((v >> 8) | (v << 8));
}

// --- automatic pin diagnosis --------------------------------------------------
// The panel is write-only, so nothing can be read back from it to prove a wire
// works. What the ESP32 can do is examine its own pins. Three of the four
// realistic breadboard faults leave a signature it can see directly, and the
// fourth shows up as a capacitance outlier.

struct DiagPin { const char *silk; const char *role; int gpio; };
static const DiagPin kDiagPins[] = {
    {"SCL", "SCK",  LCD_PIN_SCK},
    {"SDA", "MOSI", LCD_PIN_MOSI},
    {"CS",  "CS",   LCD_PIN_CS},
    {"DC",  "DC",   LCD_PIN_DC},
    {"RST", "RST",  LCD_PIN_RST},
};
static constexpr size_t kDiagN = sizeof(kDiagPins) / sizeof(kDiagPins[0]);

// GPIO 1 is documented spare in board_audio.h, so it is a fair "nothing attached"
// reference to compare the five LCD pins against.
static constexpr int kDiagRefGpio = 1;

// Discharge the pin, release it to the internal pull-up, and count iterations
// until it reads high. Charge time rises with the capacitance hanging off the pin,
// so a pin wired to a panel input is consistently slower than a bare pad. The
// absolute count is meaningless - internal pull-ups vary widely part to part - so
// only the comparison between pins carries information.
// Raw register access, not gpio_set_direction()/gpio_get_level(): those take longer
// to execute than the pin takes to charge (~0.5 us on a bare pad through the ~45k
// internal pull-up), so the first sample already reads high and every pin scores
// zero. Driver enable/disable is one register write and a sample is one read, which
// puts the poll interval far below the RC time being measured.
static inline void pin_drive_low(int gpio) {
    if (gpio < 32) {
        REG_WRITE(GPIO_OUT_W1TC_REG, 1U << gpio);
        REG_WRITE(GPIO_ENABLE_W1TS_REG, 1U << gpio);
    } else {
        REG_WRITE(GPIO_OUT1_W1TC_REG, 1U << (gpio - 32));
        REG_WRITE(GPIO_ENABLE1_W1TS_REG, 1U << (gpio - 32));
    }
}

static inline void pin_release(int gpio) {
    if (gpio < 32) REG_WRITE(GPIO_ENABLE_W1TC_REG, 1U << gpio);
    else           REG_WRITE(GPIO_ENABLE1_W1TC_REG, 1U << (gpio - 32));
}

static inline int pin_read(int gpio) {
    if (gpio < 32) return (REG_READ(GPIO_IN_REG) >> gpio) & 1;
    return (REG_READ(GPIO_IN1_REG) >> (gpio - 32)) & 1;
}

static int rc_charge_count(int gpio) {
    static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
    const gpio_num_t g = static_cast<gpio_num_t>(gpio);
    gpio_reset_pin(g);
    // INPUT_OUTPUT so the input buffer stays enabled while the output driver is
    // toggled underneath it via the enable registers.
    gpio_set_direction(g, GPIO_MODE_INPUT_OUTPUT);
    gpio_set_pull_mode(g, GPIO_PULLUP_ONLY);

    int best = 1 << 30;
    for (int trial = 0; trial < 128; ++trial) {
        pin_drive_low(gpio);
        esp_rom_delay_us(50);            // settle at 0 V
        int n = 0;
        portENTER_CRITICAL(&mux);
        pin_release(gpio);
        while (n < 20000 && pin_read(gpio) == 0) ++n;
        portEXIT_CRITICAL(&mux);
        if (n < best) best = n;          // minimum = the run least disturbed
    }
    gpio_set_pull_mode(g, GPIO_FLOATING);
    return best;
}

void board_display_pin_diagnose() {
    ESP_LOGW(TAG, "=== LCD pin diagnosis ===");

    // -- A: stuck levels. An unconnected pin, and a pin correctly wired to a
    // high-impedance panel input, both follow the internal pull. A pin that
    // refuses to follow it is tied to something it should not be - which is what
    // a jumper one row off in a power or ground rail looks like.
    for (size_t i = 0; i < kDiagN; ++i) {
        const DiagPin &p = kDiagPins[i];
        const gpio_num_t g = static_cast<gpio_num_t>(p.gpio);
        gpio_set_direction(g, GPIO_MODE_INPUT);
        gpio_set_pull_mode(g, GPIO_PULLUP_ONLY);
        esp_rom_delay_us(200);
        const int hi = gpio_get_level(g);
        gpio_set_pull_mode(g, GPIO_PULLDOWN_ONLY);
        esp_rom_delay_us(200);
        const int lo = gpio_get_level(g);
        gpio_set_pull_mode(g, GPIO_FLOATING);

        const char *verdict;
        if (hi == 1 && lo == 0)      verdict = "floating (normal)";
        else if (hi == 0 && lo == 0) verdict = "*** SHORTED TO GND - wire is in a ground row ***";
        else if (hi == 1 && lo == 1) verdict = "*** SHORTED TO 3V3 - wire is in a power row ***";
        else                         verdict = "*** driven by something else ***";
        ESP_LOGW(TAG, "A: %-3s (GPIO %2d) pull-up=%d pull-down=%d -> %s",
                 p.silk, p.gpio, hi, lo, verdict);
    }

    // -- B: cross-shorts. Two jumpers pushed into the same breadboard row is the
    // single easiest mistake to make and the hardest to see, and it is completely
    // invisible from the ESP32 unless you go looking for it like this.
    for (size_t i = 0; i < kDiagN; ++i) {
        const gpio_num_t drv = static_cast<gpio_num_t>(kDiagPins[i].gpio);
        for (size_t j = 0; j < kDiagN; ++j) {
            if (j == i) continue;
            const gpio_num_t other = static_cast<gpio_num_t>(kDiagPins[j].gpio);
            gpio_set_direction(other, GPIO_MODE_INPUT);
            gpio_set_pull_mode(other, GPIO_PULLDOWN_ONLY);
        }
        gpio_set_direction(drv, GPIO_MODE_OUTPUT);
        gpio_set_level(drv, 1);
        esp_rom_delay_us(300);
        for (size_t j = 0; j < kDiagN; ++j) {
            if (j == i) continue;
            const gpio_num_t other = static_cast<gpio_num_t>(kDiagPins[j].gpio);
            if (gpio_get_level(other) == 1) {
                ESP_LOGW(TAG, "B: *** %s (GPIO %d) SHORTED TO %s (GPIO %d) - same row ***",
                         kDiagPins[i].silk, kDiagPins[i].gpio,
                         kDiagPins[j].silk, kDiagPins[j].gpio);
            }
        }
        gpio_set_level(drv, 0);
    }
    for (size_t j = 0; j < kDiagN; ++j) {
        gpio_set_pull_mode(static_cast<gpio_num_t>(kDiagPins[j].gpio), GPIO_FLOATING);
    }
    ESP_LOGW(TAG, "B: cross-short scan complete (no '***' above means none found)");

    // -- C: capacitive load. This is the open-circuit test: a disconnected pin
    // charges noticeably faster than one dragging a jumper and a panel input
    // behind it. Indicative rather than proof - a long wire to a bad joint still
    // reads as loaded - so it is reported as a ranking, not a pass/fail.
    gpio_reset_pin(static_cast<gpio_num_t>(kDiagRefGpio));
    const int ref = rc_charge_count(kDiagRefGpio);
    int counts[kDiagN];
    int loaded_sum = 0, loaded_n = 0;
    for (size_t i = 0; i < kDiagN; ++i) {
        counts[i] = rc_charge_count(kDiagPins[i].gpio);
        if (counts[i] > ref) { loaded_sum += counts[i]; ++loaded_n; }
    }
    const int typical = loaded_n ? (loaded_sum / loaded_n) : ref;
    ESP_LOGW(TAG, "C: reference GPIO %d (nothing attached) = %d", kDiagRefGpio, ref);
    for (size_t i = 0; i < kDiagN; ++i) {
        // Close to the bare-pad reference, and well under what the other wired
        // pins read, means nothing is hanging off it.
        const bool suspect = (counts[i] <= ref + (ref / 4) + 1) ||
                             (typical > 0 && counts[i] * 2 < typical);
        ESP_LOGW(TAG, "C: %-3s (GPIO %2d) charge=%-5d %s", kDiagPins[i].silk,
                 kDiagPins[i].gpio, counts[i],
                 suspect ? "*** LOOKS UNCONNECTED (no wire load) ***" : "loaded (wire present)");
    }
    ESP_LOGW(TAG, "=== end LCD pin diagnosis ===");

    for (size_t i = 0; i < kDiagN; ++i) {
        gpio_reset_pin(static_cast<gpio_num_t>(kDiagPins[i].gpio));
    }
}

void board_display_pin_test(int per_pin_ms) {
    struct Pin { const char *name; const char *silk; int gpio; };
    static const Pin pins[] = {
        {"SCK",  "SCL", LCD_PIN_SCK},
        {"MOSI", "SDA", LCD_PIN_MOSI},
        {"CS",   "CS",  LCD_PIN_CS},
        {"DC",   "DC",  LCD_PIN_DC},
        {"RST",  "RST", LCD_PIN_RST},
    };
    const size_t n = sizeof(pins) / sizeof(pins[0]);

    uint64_t mask = 0;
    for (size_t i = 0; i < n; ++i) mask |= 1ULL << pins[i].gpio;
    gpio_config_t io = {};
    io.pin_bit_mask = mask;
    io.mode = GPIO_MODE_OUTPUT;
    gpio_config(&io);
    for (size_t i = 0; i < n; ++i) {
        gpio_set_level(static_cast<gpio_num_t>(pins[i].gpio), 0);
    }

    ESP_LOGW(TAG, "pin test: probe each named pin AT THE DISPLAY, not at the ESP32");
    ESP_LOGW(TAG, "  toggling  -> ~1.6 V on a DC multimeter, or a blinking LED");
    ESP_LOGW(TAG, "  flat 0 V  -> that wire is broken, in the wrong row, or on the wrong pin");

    for (size_t i = 0; i < n; ++i) {
        const Pin &p = pins[i];
        ESP_LOGW(TAG, "pin test %u/%u: display '%s' <- GPIO %d (%s) toggling now",
                 static_cast<unsigned>(i + 1), static_cast<unsigned>(n),
                 p.silk, p.gpio, p.name);
        const int cycles = per_pin_ms / 500;
        for (int c = 0; c < cycles; ++c) {
            gpio_set_level(static_cast<gpio_num_t>(p.gpio), 1);
            vTaskDelay(pdMS_TO_TICKS(250));
            gpio_set_level(static_cast<gpio_num_t>(p.gpio), 0);
            vTaskDelay(pdMS_TO_TICKS(250));
        }
    }
    ESP_LOGW(TAG, "pin test done; handing the pins to SPI");
}

bool board_display_init() {
    spi_bus_config_t bus = {};
    bus.sclk_io_num = LCD_PIN_SCK;
    bus.mosi_io_num = LCD_PIN_MOSI;
    bus.miso_io_num = -1;         // write-only panel
    bus.quadwp_io_num = -1;
    bus.quadhd_io_num = -1;
    bus.max_transfer_sz = LCD_H_RES * CHUNK_ROWS * static_cast<int>(sizeof(uint16_t));
    if (spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO) != ESP_OK) {
        ESP_LOGE(TAG, "SPI2 bus init failed");
        return false;
    }

    esp_lcd_panel_io_handle_t io = nullptr;
    esp_lcd_panel_io_spi_config_t io_cfg = {};
    io_cfg.dc_gpio_num = LCD_PIN_DC;
    io_cfg.cs_gpio_num = LCD_PIN_CS;
    io_cfg.pclk_hz = LCD_SPI_HZ;
    io_cfg.lcd_cmd_bits = 8;
    io_cfg.lcd_param_bits = 8;
    io_cfg.spi_mode = 0;
    io_cfg.trans_queue_depth = 10;
    if (esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(SPI2_HOST),
                                 &io_cfg, &io) != ESP_OK) {
        ESP_LOGE(TAG, "panel IO init failed");
        return false;
    }

    esp_lcd_panel_dev_config_t panel_cfg = {};
    panel_cfg.reset_gpio_num = LCD_PIN_RST;
    // These red 2.8" ILI9341 modules are wired BGR. If red and blue are swapped on
    // screen, change this to LCD_RGB_ELEMENT_ORDER_RGB.
    panel_cfg.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR;
    panel_cfg.bits_per_pixel = 16;
    if (esp_lcd_new_panel_ili9341(io, &panel_cfg, &s_panel) != ESP_OK) {
        ESP_LOGE(TAG, "ILI9341 panel init failed");
        return false;
    }

    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(s_panel, false));
    // Deliberately NOT esp_lcd_panel_mirror(): see LCD_ROTATE_180 in board_display.h.
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(s_panel, LCD_GAP_X, LCD_GAP_Y));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(s_panel, true));

    board_display_backlight(true);
    ESP_LOGI(TAG, "ILI9341 %dx%d up on SPI2 @ %d MHz", LCD_H_RES, LCD_V_RES,
             LCD_SPI_HZ / 1000000);
    return true;
}

void board_display_backlight(bool on) {
#if LCD_PIN_BL >= 0
    gpio_config_t io = {};
    io.pin_bit_mask = 1ULL << LCD_PIN_BL;
    io.mode = GPIO_MODE_OUTPUT;
    gpio_config(&io);
    gpio_set_level(static_cast<gpio_num_t>(LCD_PIN_BL), on ? 1 : 0);
#else
    (void)on;   // backlight hard-wired to 3V3
#endif
}

void board_display_blit(const uint16_t *fb, int w, int h) {
    if (s_panel == nullptr || fb == nullptr) return;
    if (w != LCD_H_RES) return;   // the UI is built at panel width; nothing to scale

    for (int y0 = 0; y0 < h; y0 += CHUNK_ROWS) {
        const int rows = (y0 + CHUNK_ROWS <= h) ? CHUNK_ROWS : (h - y0);
        uint16_t *chunk = next_chunk();
#if LCD_ROTATE_180
        // Panel rows [y0, y0+rows) come from framebuffer rows counted from the far
        // end, each read right to left. Same byte count, same one swap per pixel.
        for (int r = 0; r < rows; ++r) {
            const uint16_t *src = fb + static_cast<size_t>(h - 1 - (y0 + r)) * w;
            uint16_t *dst = chunk + r * w;
            for (int x = 0; x < w; ++x) {
                dst[x] = swap16(src[w - 1 - x]);
            }
        }
#else
        const uint16_t *src = fb + static_cast<size_t>(y0) * w;
        const int n = rows * w;
        for (int i = 0; i < n; ++i) {
            chunk[i] = swap16(src[i]);
        }
#endif
        const esp_err_t err =
            esp_lcd_panel_draw_bitmap(s_panel, 0, y0, w, y0 + rows, chunk);
        if (err != ESP_OK) {
            // Once, not once per chunk per frame: a failing panel fails every band.
            static bool logged = false;
            if (!logged) {
                logged = true;
                ESP_LOGE(TAG, "draw_bitmap failed at y=%d: %s", y0, esp_err_to_name(err));
            }
        }
    }
}

// --- bring-up self-test -------------------------------------------------------
// The panel is write-only (MISO is -1), so esp_lcd reporting "init success" proves
// only that bytes left the ESP32 - not that a panel received them. When the screen
// shows nothing, that ambiguity is the whole problem. These fills bypass the camera,
// the models and display_ui entirely, so whatever appears is attributable to the
// panel, its wiring and its backlight alone.

void board_display_fill(uint16_t host_order_colour) {
    const uint16_t c = swap16(host_order_colour);
    for (int y0 = 0; y0 < LCD_V_RES; y0 += CHUNK_ROWS) {
        const int rows = (y0 + CHUNK_ROWS <= LCD_V_RES) ? CHUNK_ROWS : (LCD_V_RES - y0);
        uint16_t *chunk = next_chunk();
        const int n = rows * LCD_H_RES;
        for (int i = 0; i < n; ++i) chunk[i] = c;
        esp_lcd_panel_draw_bitmap(s_panel, 0, y0, LCD_H_RES, y0 + rows, chunk);
    }
}

// Quadrants plus a one-pixel border. The border is the useful part: if a coloured
// band appears along an edge, or the border is clipped, LCD_GAP_X/LCD_GAP_Y are
// wrong for this module's tab colour. The asymmetric quadrants show orientation.
static void panel_fill_quadrants() {
    for (int y0 = 0; y0 < LCD_V_RES; y0 += CHUNK_ROWS) {
        const int rows = (y0 + CHUNK_ROWS <= LCD_V_RES) ? CHUNK_ROWS : (LCD_V_RES - y0);
        uint16_t *chunk = next_chunk();
        for (int r = 0; r < rows; ++r) {
            const int y = y0 + r;
            for (int x = 0; x < LCD_H_RES; ++x) {
                uint16_t v;
                if (x == 0 || y == 0 || x == LCD_H_RES - 1 || y == LCD_V_RES - 1) {
                    v = 0xFFFF;                                     // white border
                } else if (y < LCD_V_RES / 2) {
                    v = (x < LCD_H_RES / 2) ? 0xF800 : 0x07E0;      // red  | green
                } else {
                    v = (x < LCD_H_RES / 2) ? 0x001F : 0xFFE0;      // blue | yellow
                }
                chunk[r * LCD_H_RES + x] = swap16(v);
            }
        }
        esp_lcd_panel_draw_bitmap(s_panel, 0, y0, LCD_H_RES, y0 + rows, chunk);
    }
}

void board_display_selftest(int hold_ms) {
    if (s_panel == nullptr) {
        ESP_LOGE(TAG, "selftest skipped: panel not initialised");
        return;
    }
    struct Step { const char *name; uint16_t colour; };
    // Named by the RGB565 bit pattern, not by what should appear. panel_cfg sets
    // rgb_ele_order = BGR, so "RED" showing blue is a real and expected outcome -
    // and is exactly the signal that the element order needs flipping.
    static const Step steps[] = {
        {"BLACK",  0x0000},
        {"RED",    0xF800},
        {"GREEN",  0x07E0},
        {"BLUE",   0x001F},
        {"WHITE",  0xFFFF},
    };
    ESP_LOGW(TAG, "selftest start: watch the panel and note what you see");
    for (const Step &s : steps) {
        ESP_LOGW(TAG, "selftest fill %s (0x%04x)", s.name, s.colour);
        board_display_fill(s.colour);
        vTaskDelay(pdMS_TO_TICKS(hold_ms));
    }
    ESP_LOGW(TAG, "selftest quadrants: white border, R|G top, B|Y bottom");
    panel_fill_quadrants();
    vTaskDelay(pdMS_TO_TICKS(hold_ms));
    ESP_LOGW(TAG, "selftest done");
}
