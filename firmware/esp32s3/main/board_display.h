#pragma once
/*
Panel binding for the 2.8" 240x320 ILI9341 SPI module (red PCB, with resistive
touch and a microSD slot - both unused, leave T_* and SD_* pins unconnected).
SDO(MISO) is also left unconnected: the panel is driven write-only.

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
#define LCD_H_RES 240
#define LCD_V_RES 320

// The ILI9341's GRAM is exactly 240x320, so unlike the old ST7735S there is no
// tab-colour window offset. Leave at 0/0.
#define LCD_GAP_X 0
#define LCD_GAP_Y 0

// Panel orientation. Set to 1 if the module is mounted upside down: the row order
// reverses (the event line lands at the top instead of the bottom) and the text
// reads backwards.
//
// The rotation is applied in board_display_blit(), NOT via esp_lcd_panel_mirror().
// MADCTL's MX/MY bits do rotate the image, but on panels whose GRAM is larger than
// the glass they also move which corner the visible window starts from. Harmless on
// this exact-size ILI9341, but rotating during the copy costs nothing anyway: those
// bytes are already being touched for the endian swap.
#define LCD_ROTATE_180 0

// A full 240x320 RGB565 frame is 150 KB = 1.23 Mbit on the wire: ~31 ms at 40 MHz,
// ~123 ms at 10 MHz. The ILI9341 datasheet only promises ~10 MHz writes but these
// modules reliably run at 40 MHz over short wires.
//
// Currently at 10 MHz for bring-up. 40 MHz assumes short, well-grounded traces;
// over dupont jumpers to a panel on a separate breadboard the clock edges do not
// survive, the controller latches nothing, and the panel stays white with its
// backlight on - which looks identical to a dead panel. 10 MHz (~8 fps) is fine
// while bringing up but too slow for the 15 fps pipeline, so step this back up
// (10 -> 20 -> 40) once the panel is confirmed working and stop at the last
// speed that stays stable.
#define LCD_SPI_HZ (10 * 1000 * 1000)

// Bring-up aid. Set to 0 once the panel is confirmed working: it costs ~6 s of boot.
#define LCD_SELFTEST 1

// Wiring check. Set to 1 to toggle each LCD pin in turn before SPI claims them,
// so a wire can be traced instead of guessed at. See board_display_pin_test.
#define LCD_PIN_TEST 1

bool board_display_init();

// DisplayBlit-compatible: pushes a full RGB565 framebuffer to the panel.
void board_display_blit(const uint16_t *fb, int w, int h);

void board_display_backlight(bool on);

// Paints the whole panel one RGB565 colour (host byte order; the swap to panel
// order happens inside). Exposed so bring-up code can drive the panel without
// composing a framebuffer.
void board_display_fill(uint16_t rgb565_colour);

// Paints solid colours then a bordered quadrant pattern, holding each for hold_ms,
// bypassing the camera and the UI. The panel is write-only, so a successful
// esp_lcd init says nothing about whether a panel is actually attached; this is
// what separates "no pixels reaching the glass" from "wrong pixels composed".
void board_display_selftest(int hold_ms);

// Squarewaves each LCD control pin in turn at 2 Hz for per_pin_ms, holding the
// other four low, and logs which one it is driving. Probe the matching pin at the
// DISPLAY end: a good wire reads ~1.6 V on a multimeter in DC volts (a 50% duty
// squarewave between 0 and 3.3 V) or blinks an LED wired to ground through ~1k.
// A pin that stays at 0 V is a broken, misplaced or wrong-row wire.
//
// Must run BEFORE board_display_init(): once spi_bus_initialize() claims these
// pins the GPIO matrix is routed to the SPI peripheral and manual writes do
// nothing.
void board_display_pin_test(int per_pin_ms);

// Self-contained wiring diagnosis, no multimeter needed. Checks each LCD pin for
// a short to ground or 3V3, scans every pair for a shared breadboard row, and
// compares capacitive loading to flag a pin with no wire on it. Like
// board_display_pin_test, must run BEFORE board_display_init().
void board_display_pin_diagnose();
