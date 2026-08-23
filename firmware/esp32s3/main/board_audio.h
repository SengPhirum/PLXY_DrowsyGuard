#pragma once
/*
I2S audio output for the MAX98357A class-D amplifier (khmeres.com item 2724,
"MAX98357 I2S audio amplifier filterless class D", board code YD024) driving the
4 ohm / 3 W 40 mm speaker (khmeres.com item 2554).

Pin choice, and why these three:
  Every GPIO the DVP camera uses (4,5,6,7,8,9,10,11,12,13,15,16,17,18) is off
  limits, as are 33..37 (SPI flash + octal PSRAM), 19/20 (native USB) and 43/44
  (UART0 console). GPIO 2 is the buzzer. That leaves 1, 3, 14, 21, 38, 39, 40, 41,
  42 and 47.

  The amplifier used to sit on 38/39/40 because the microSD slot they belong to
  was unused. It is not unused any more - a card went in on 2026-08-23 to hold the
  drowsiness-event history (see board_sdcard.h) - and those three pins are the
  slot's SDMMC bus, which cannot move. So the amplifier moved.

  It moved twice. Briefly to 14/21/47, and then to **41/42/2**, which is where it
  is now, for a reason that has nothing to do with electronics: 14 is on the top
  header row and 21/47 are on the bottom one, so wiring the amplifier meant
  reaching across the board. Every signal this project asks anyone to wire by hand
  now lands on the **bottom row**, and on three physically adjacent pins.

  The board's header order, read off a photograph of the actual part (2026-08-23):

      top     5V 14 13 12 11 10 9 46 3 8 18 17 16 15 7 6 5 4 EN 3V3
      bottom  GND 19 20 21 47 48 45 0 35 36 37 38 39 40 41 42 2 1 RX TX

  The top row is almost entirely the DVP camera bus - only 14 and 3 are free there,
  and there is no GND on it at all - so one-row wiring has to be the bottom row.
  On that row 41, 42, 2 and 1 are the only run of consecutive free pins, which is
  why the amplifier takes the first three and the buzzer moved to the fourth.

  5V is the one exception and it cannot be helped: the only 5V pin is top-left.
  It goes to the breadboard's + rail, which the build needs anyway, so in practice
  it is not an extra reach - see the tutorial's power-rails step.

  If you ever fit a microSD card, these three must move - there is no way to
  share them.

Amplifier notes that matter for bring-up (MAX98357A/MAX98357B datasheet):
  * VIN accepts 2.5 V - 5.5 V. Feed it 5 V: output power scales with supply, and
    3.2 W into 4 ohm is a 5 V number. It will run off 3V3 but much quieter.
  * BCLK/LRC/DIN are driven straight from the ESP32-S3 at 3.3 V. No level
    shifter is required in this direction, and none is wanted - see the tutorial.
  * No MCLK. The MAX98357A recovers its own clock, which is why only three
    signal wires are needed.
  * Filterless class D: no output LC filter, speaker wires straight to the
    screw terminal.
  * SD_MODE selects the channel as well as shutting the part down:
        < 0.16 V            shutdown
        0.16 V .. 0.77 V    (left + right) / 2
        0.77 V .. 1.4 V     right channel only
        > 1.4 V             left channel only
    Breakout boards differ in what they pull SD to, so this firmware writes the
    SAME sample into both the left and right slot. Every non-shutdown SD_MODE
    setting then produces identical audio and the board variant stops mattering.
*/

#include <cstddef>
#include <cstdint>

// --- I2S wiring, change here and nowhere else ---
// Three adjacent pins on the bottom header row. See the note above before changing
// these: the constraint is physical, not electrical.
#define AUDIO_PIN_BCLK  41   // amplifier BCLK  (bit clock)
#define AUDIO_PIN_LRCLK 42   // amplifier LRC   (word select / left-right clock)
#define AUDIO_PIN_DIN    2   // amplifier DIN   (serial data, ESP32-S3 -> amp)

// 16 kHz mono 16-bit is what firmware/esp32s3/assets/audio/README.md specifies for
// the recorded warnings, so the tone generator runs at the same rate and the two
// share one I2S channel configuration.
#define AUDIO_SAMPLE_RATE_HZ 16000

// Peak amplitude for generated tones, as a fraction of full scale. Kept below 1.0
// because the alert has to be clearly audible without being startling enough to
// make a drowsy driver flinch - see docs/VOICE_ALERT_HARDWARE.md.
#define AUDIO_TONE_AMPLITUDE 0.35f

// Brings up the I2S peripheral. Safe to call when no amplifier is connected: the
// pins simply clock into nothing. Returns false if the channel cannot be created,
// in which case voice_alert falls back to the buzzer.
bool board_audio_init();

bool board_audio_ready();

// Streams mono 16-bit PCM at AUDIO_SAMPLE_RATE_HZ, duplicating every sample into
// both I2S slots (see the SD_MODE note above). Blocks until the data is queued.
bool board_audio_play_pcm16(const int16_t *samples, size_t count);

// Generates and plays a sine tone. This is what makes the amplifier testable
// before any recorded speech exists, and it is the attention chime that precedes
// a spoken warning once the clips are embedded.
bool board_audio_play_tone(uint32_t freq_hz, uint32_t duration_ms);

// Pushes one buffer of silence so the class-D output stage settles instead of
// leaving the last sample on the speaker as a click.
void board_audio_silence();
