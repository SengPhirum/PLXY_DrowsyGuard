#include "board_display.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_st7735.h"
#include "esp_log.h"

static const char *TAG = "lcd";

static esp_lcd_panel_handle_t s_panel = nullptr;

// The framebuffer holds RGB565 in host (little-endian) order, but the ST7735S
// latches the high byte first. esp_lcd sends the buffer verbatim, so the bytes are
// swapped here on the way out. Swapping a chunk at a time rather than the whole
// frame keeps this off the 40 KB critical path and out of PSRAM.
static constexpr int CHUNK_ROWS = 16;
static uint16_t s_chunk[LCD_H_RES * CHUNK_ROWS];

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
    // These 1.8" modules are wired BGR. If red and blue are swapped on screen,
    // change this to LCD_RGB_ELEMENT_ORDER_RGB.
    panel_cfg.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR;
    panel_cfg.bits_per_pixel = 16;
    if (esp_lcd_new_panel_st7735(io, &panel_cfg, &s_panel) != ESP_OK) {
        ESP_LOGE(TAG, "ST7735S panel init failed");
        return false;
    }

    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(s_panel, false));
    // Deliberately NOT esp_lcd_panel_mirror(): see LCD_ROTATE_180 in board_display.h.
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(s_panel, LCD_GAP_X, LCD_GAP_Y));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(s_panel, true));

    board_display_backlight(true);
    ESP_LOGI(TAG, "ST7735S %dx%d up on SPI2 @ %d MHz", LCD_H_RES, LCD_V_RES,
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
#if LCD_ROTATE_180
        // Panel rows [y0, y0+rows) come from framebuffer rows counted from the far
        // end, each read right to left. Same byte count, same one swap per pixel.
        for (int r = 0; r < rows; ++r) {
            const uint16_t *src = fb + static_cast<size_t>(h - 1 - (y0 + r)) * w;
            uint16_t *dst = s_chunk + r * w;
            for (int x = 0; x < w; ++x) {
                const uint16_t v = src[w - 1 - x];
                dst[x] = static_cast<uint16_t>((v >> 8) | (v << 8));
            }
        }
#else
        const uint16_t *src = fb + static_cast<size_t>(y0) * w;
        const int n = rows * w;
        for (int i = 0; i < n; ++i) {
            const uint16_t v = src[i];
            s_chunk[i] = static_cast<uint16_t>((v >> 8) | (v << 8));
        }
#endif
        esp_lcd_panel_draw_bitmap(s_panel, 0, y0, w, y0 + rows, s_chunk);
    }
}
