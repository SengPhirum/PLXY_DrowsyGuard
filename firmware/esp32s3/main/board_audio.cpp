#include "board_audio.h"

#include <cmath>

#include "driver/i2s_std.h"
#include "esp_log.h"

static const char *TAG = "audio";

static i2s_chan_handle_t s_tx = nullptr;

// Stereo frames pushed per i2s_channel_write. 256 frames is 16 ms at 16 kHz, so a
// tone is generated in small pieces rather than allocating the whole thing, and the
// capture loop is never blocked for long.
static constexpr size_t FRAMES_PER_CHUNK = 256;
static int16_t s_chunk[FRAMES_PER_CHUNK * 2];   // interleaved L,R

bool board_audio_init() {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;     // write silence on underrun instead of repeating
    if (i2s_new_channel(&chan_cfg, &s_tx, nullptr) != ESP_OK) {
        ESP_LOGE(TAG, "i2s_new_channel failed");
        s_tx = nullptr;
        return false;
    }

    // Stereo, not mono: the same sample goes to both slots so that any SD_MODE
    // wiring on the amplifier board (left / right / average) sounds identical.
    // See the SD_MODE table in board_audio.h.
    i2s_std_config_t std_cfg = {};
    std_cfg.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE_HZ);
    std_cfg.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                           I2S_SLOT_MODE_STEREO);
    std_cfg.gpio_cfg.mclk = I2S_GPIO_UNUSED;    // MAX98357A recovers its own clock
    std_cfg.gpio_cfg.bclk = static_cast<gpio_num_t>(AUDIO_PIN_BCLK);
    std_cfg.gpio_cfg.ws   = static_cast<gpio_num_t>(AUDIO_PIN_LRCLK);
    std_cfg.gpio_cfg.dout = static_cast<gpio_num_t>(AUDIO_PIN_DIN);
    std_cfg.gpio_cfg.din  = I2S_GPIO_UNUSED;    // playback only
    std_cfg.gpio_cfg.invert_flags.mclk_inv = false;
    std_cfg.gpio_cfg.invert_flags.bclk_inv = false;
    std_cfg.gpio_cfg.invert_flags.ws_inv = false;

    if (i2s_channel_init_std_mode(s_tx, &std_cfg) != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_init_std_mode failed");
        i2s_del_channel(s_tx);
        s_tx = nullptr;
        return false;
    }
    if (i2s_channel_enable(s_tx) != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_enable failed");
        i2s_del_channel(s_tx);
        s_tx = nullptr;
        return false;
    }

    ESP_LOGI(TAG, "I2S up: BCLK=%d LRCLK=%d DIN=%d @ %d Hz 16-bit stereo",
             AUDIO_PIN_BCLK, AUDIO_PIN_LRCLK, AUDIO_PIN_DIN, AUDIO_SAMPLE_RATE_HZ);
    return true;
}

bool board_audio_ready() { return s_tx != nullptr; }

// Queues one interleaved chunk. `frames` counts stereo frames, not samples.
static bool write_chunk(size_t frames) {
    size_t written = 0;
    const size_t bytes = frames * 2 * sizeof(int16_t);
    return i2s_channel_write(s_tx, s_chunk, bytes, &written, 200) == ESP_OK &&
           written == bytes;
}

bool board_audio_play_pcm16(const int16_t *samples, size_t count) {
    if (s_tx == nullptr || samples == nullptr) return false;

    size_t done = 0;
    while (done < count) {
        const size_t frames = (count - done < FRAMES_PER_CHUNK) ? (count - done)
                                                                : FRAMES_PER_CHUNK;
        for (size_t i = 0; i < frames; ++i) {
            const int16_t v = samples[done + i];
            s_chunk[2 * i] = v;         // left
            s_chunk[2 * i + 1] = v;     // right
        }
        if (!write_chunk(frames)) return false;
        done += frames;
    }
    return true;
}

bool board_audio_play_tone(uint32_t freq_hz, uint32_t duration_ms) {
    if (s_tx == nullptr || freq_hz == 0 || duration_ms == 0) return false;

    const size_t total = static_cast<size_t>(AUDIO_SAMPLE_RATE_HZ) * duration_ms / 1000;
    // Ramp the first and last 5 ms. Starting a class-D amplifier on a full-amplitude
    // sample produces an audible click, which reads as a fault during bring-up.
    const size_t ramp = static_cast<size_t>(AUDIO_SAMPLE_RATE_HZ) * 5 / 1000;
    const float step = 2.0f * static_cast<float>(M_PI) * static_cast<float>(freq_hz) /
                       static_cast<float>(AUDIO_SAMPLE_RATE_HZ);

    size_t done = 0;
    while (done < total) {
        const size_t frames = (total - done < FRAMES_PER_CHUNK) ? (total - done)
                                                                : FRAMES_PER_CHUNK;
        for (size_t i = 0; i < frames; ++i) {
            const size_t n = done + i;
            float gain = AUDIO_TONE_AMPLITUDE;
            if (ramp > 0) {
                if (n < ramp) {
                    gain *= static_cast<float>(n) / static_cast<float>(ramp);
                } else if (n + ramp > total) {
                    gain *= static_cast<float>(total - n) / static_cast<float>(ramp);
                }
            }
            const float v = sinf(step * static_cast<float>(n)) * gain * 32767.0f;
            const int16_t s = static_cast<int16_t>(v);
            s_chunk[2 * i] = s;
            s_chunk[2 * i + 1] = s;
        }
        if (!write_chunk(frames)) return false;
        done += frames;
    }
    return true;
}

void board_audio_silence() {
    if (s_tx == nullptr) return;
    for (size_t i = 0; i < FRAMES_PER_CHUNK * 2; ++i) s_chunk[i] = 0;
    write_chunk(FRAMES_PER_CHUNK);
}
