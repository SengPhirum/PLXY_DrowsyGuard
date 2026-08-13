#pragma once
/*
Panel binding for the 1.8" 128x160 ST7735S SPI module (khmeres.com item 1885).

display_ui.h deliberately keeps panel init out of the UI code and takes a blit
callback instead. This file is that callback plus the SPI/panel bring-up, so the
UI stays portable if the panel is swapped later.

Pin choice: every GPIO the DVP camera uses (4,5,6,7,8,9,10,11,12,13,15,16,17,18)
is off limits, as are 33..37 (flash/PSRAM), 19/20 (native USB) and 43/44 (console).
The five pins below are non-strapping, non-reserved, and broken out on the header.
GPIO 2 is left free for the buzzer (see voice_alert.cpp) and 38/39/40 are left for
the I2S amplifier once it arrives - they belong to the microSD slot, which this
project does not use.
*/

#include <cstdint>

// --- LCD wiring, change here and nowhere else ---
#define LCD_PIN_SCK  14
#define LCD_PIN_MOSI 21
#define LCD_PIN_CS   47
#define LCD_PIN_DC   41
#define LCD_PIN_RST  42
#define LCD_PIN_BL   -1   // backlight tied to 3V3; set a GPIO here to switch it

// Native panel resolution, portrait. The UI stacks vertically, so portrait fits
// more rows than landscape does.
#define LCD_H_RES 128
#define LCD_V_RES 160

// Some 128x160 modules address a window offset from the panel origin ("red tab"
// vs "black tab"). If you see a coloured band on two edges and the image is
// shifted, set these to 2/1 or 2/3 and rebuild.
#define LCD_GAP_X 0
#define LCD_GAP_Y 0

// Panel orientation. This module comes up rotated 180 degrees: the row order is
// reversed (the event line lands at the top instead of the bottom) and the text
// reads backwards.
//
// The rotation is applied in board_display_blit(), NOT via esp_lcd_panel_mirror().
// MADCTL's MX/MY bits do rotate the image, but they also move which corner of the
// ST7735S's 132x162 GRAM the visible 128x160 window starts from, so LCD_GAP_X/Y
// would have to change with them - get that wrong and the panel goes white,
// because every write lands outside the visible area. Rotating during the copy is
// immune to that and costs nothing: those bytes are already being touched for the
// endian swap. Set to 0 if you remount the panel the other way up.
#define LCD_ROTATE_180 1

// 40 MHz keeps a full 128x160 RGB565 frame (40 KB) under ~8 ms on the wire. At
// 20 MHz it is ~16 ms, which eats most of the 23 ms frame budget in
// docs/FIRMWARE_PIPELINE.md. Drop to 20 MHz if long dupont wires make it flicker.
#define LCD_SPI_HZ (40 * 1000 * 1000)

bool board_display_init();

// DisplayBlit-compatible: pushes a full RGB565 framebuffer to the panel.
void board_display_blit(const uint16_t *fb, int w, int h);

void board_display_backlight(bool on);
