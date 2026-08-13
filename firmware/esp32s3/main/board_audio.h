#pragma once
/*
I2S audio output for the MAX98357A class-D amplifier (khmeres.com item 2724,
"MAX98357 I2S audio amplifier filterless class D", board code YD024) driving the
4 ohm / 3 W 40 mm speaker (khmeres.com item 2554).

Pin choice, and why these three:
  Every GPIO the DVP camera uses (4,5,6,7,8,9,10,11,12,13,15,16,17,18) is off
  limits, as are 33..37 (SPI flash + octal PSRAM), 19/20 (native USB) and 43/44
  (UART0 console). board_display.h takes 14, 21, 41, 42, 47 and GPIO 2 is the
  buzzer. That leaves 1, 3, 38, 39, 40. The microSD slot owns 38/39/40 and this
  project does not use a card, so the amplifier gets them and 1/3 stay spare.

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
#define AUDIO_PIN_BCLK  39   // amplifier BCLK  (bit clock)
#define AUDIO_PIN_LRCLK 38   // amplifier LRC   (word select / left-right clock)
#define AUDIO_PIN_DIN   40   // amplifier DIN   (serial data, ESP32-S3 -> amp)

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
