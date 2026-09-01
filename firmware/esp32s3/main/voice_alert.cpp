#include "voice_alert.h"

#include "board_audio.h"
#include "driver/gpio.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "voice_clips.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include <cstring>

// Alert controller. Audio transport lives in board_audio.cpp so the amplifier can
// be swapped without touching drowsiness inference.
//
// Playback runs on its own task. voice_alert_trigger() is called from the capture
// loop, which has a ~23 ms frame budget (docs/FIRMWARE_PIPELINE.md); playing even a
// half-second alert inline would drop roughly twenty frames and stall the browser
// preview at exactly the moment there is something worth looking at.
//
// Since the panel was removed this is the only output the driver perceives, so the
// failure modes here matter more than they did: see repeat_reset_ms in the header.
//
// Rate limiting is per channel, not global. That is the one structural change worth
// calling out: with a single shared cooldown, a sneeze announcement or a "no driver"
// announcement could be swallowed by a drowsiness cooldown that had nothing to do
// with it, and the symptom is silence - the hardest failure of all to notice.

static const char *TAG = "voice_alert";
static VoiceAlertConfig g_config{};

// One episode's worth of state per channel.
struct ChannelState {
    uint32_t last_ms = 0;
    uint32_t repeats = 0;
};
static ChannelState g_channel[static_cast<int>(AlertChannel::Count)];

static uint32_t g_total_count = 0;
static uint32_t g_reason_count[ALERT_REASON_COUNT] = {0};
static uint32_t g_last_any_ms = 0;
static bool g_initialized = false;
static bool g_audio = false;
static bool g_muted = false;
static bool g_lang_persisted = false;

// NVS, so the choice survives a power cycle. Namespace kept separate from the
// Wi-Fi driver's so a `nvs_flash_erase` for one reason cannot silently reset the
// other.
#define LANG_NVS_NAMESPACE "drowsyguard"
#define LANG_NVS_KEY "alert_lang"

static const char *lang_code(AlertLanguage l) {
    return l == AlertLanguage::Khmer ? "km" : "en";
}

static void lang_save(AlertLanguage l) {
    nvs_handle_t h;
    if (nvs_open(LANG_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return;
    if (nvs_set_str(h, LANG_NVS_KEY, lang_code(l)) == ESP_OK) nvs_commit(h);
    nvs_close(h);
}

static void lang_load() {
    nvs_handle_t h;
    if (nvs_open(LANG_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return;
    char buf[8] = {0};
    size_t n = sizeof(buf);
    if (nvs_get_str(h, LANG_NVS_KEY, buf, &n) == ESP_OK) {
        g_config.language = (strcmp(buf, "km") == 0) ? AlertLanguage::Khmer
                                                     : AlertLanguage::English;
        g_lang_persisted = true;
    }
    nvs_close(h);
}

static QueueHandle_t g_queue = nullptr;

// Buzzer fallback, used when the I2S amplifier is absent or fails to initialize.
// GPIO 1, not 2: 2 became the amplifier's DIN when every hand-wired signal was
// moved onto the bottom header row, and 1 is the pin next door - so an optional
// buzzer still sits beside the three wires it is a fallback for.
static constexpr gpio_num_t BUZZER_GPIO = GPIO_NUM_1;
static constexpr uint32_t BUZZER_PULSE_MS = 120;

// Attention pattern per reason. Recorded speech replaces these once approved
// clips are embedded (firmware/esp32s3/assets/audio/README.md); until then this is
// a real, audible alert rather than a log line, and it is what makes the amplifier
// testable on the bench.
struct TonePattern {
    uint32_t freq_hz;
    uint32_t beep_ms;
    uint32_t gap_ms;
    uint32_t beeps;
};

static TonePattern pattern_for(AlertReason reason) {
    switch (reason) {
        // Most urgent: eyes were shut for over a second. Highest and fastest.
        case AlertReason::Microsleep: return {1200, 150, 90, 3};
        case AlertReason::HeadNod:    return {780, 200, 120, 2};
        case AlertReason::Yawning:    return {660, 260, 0, 1};
        // A sneeze is an acknowledgement, not a warning, so it is the one pattern
        // that rises rather than repeats - deliberately unlike the alarms, because
        // its whole message is "that was not drowsiness".
        case AlertReason::Sneeze:     return {520, 120, 60, 2};
        // Two long low tones. Slow and unhurried on purpose: nothing is wrong with
        // the vehicle, the device has simply stopped being able to watch anyone, and
        // a pattern that sounds like the microsleep alarm would be actively harmful
        // to someone who is not there to hear it and returns mid-way through.
        case AlertReason::NoDriver:   return {440, 400, 200, 2};
        case AlertReason::Drowsy:
        default:                      return {880, 220, 130, 2};
    }
}

static void buzzer_pulse() {
    if (!g_config.buzzer_fallback) return;
    gpio_set_level(BUZZER_GPIO, 1);
    vTaskDelay(pdMS_TO_TICKS(BUZZER_PULSE_MS));
    gpio_set_level(BUZZER_GPIO, 0);
}

static void alert_audio_task(void *) {
    AlertReason reason = AlertReason::Drowsy;
    for (;;) {
        if (xQueueReceive(g_queue, &reason, portMAX_DELAY) != pdTRUE) continue;

        ESP_LOGW(TAG, "ALERT (%s)", voice_alert_banner_text(reason));

        if (g_muted) {
            ESP_LOGW(TAG, "muted; not annunciating %s", voice_alert_clip_name(reason));
        } else if (g_audio) {
            // Speech first, tones only if there is nothing to say. A tone pattern
            // is a fallback, not the product: the whole argument for naming the
            // reason is that "you appear drowsy" is actionable where three beeps
            // have to be remembered.
            const ClipSource used = voice_clip_play(lang_code(g_config.language),
                                                    voice_alert_clip_name(reason));
            if (used == ClipSource::None) {
                const TonePattern p = pattern_for(reason);
                for (uint32_t i = 0; i < p.beeps; ++i) {
                    board_audio_play_tone(p.freq_hz, p.beep_ms);
                    if (p.gap_ms && i + 1 < p.beeps) vTaskDelay(pdMS_TO_TICKS(p.gap_ms));
                }
                board_audio_silence();
                ESP_LOGW(TAG, "no %s clip for %s; used the tone pattern",
                         lang_code(g_config.language), voice_alert_clip_name(reason));
            } else {
                ESP_LOGI(TAG, "spoke %s_%s from %s", lang_code(g_config.language),
                         voice_alert_clip_name(reason), voice_clip_source_name(used));
            }
        } else {
            buzzer_pulse();
        }
    }
}

AlertChannel voice_alert_channel(AlertReason reason) {
    switch (reason) {
        case AlertReason::Sneeze:   return AlertChannel::Sneeze;
        case AlertReason::NoDriver: return AlertChannel::Presence;
        default:                    return AlertChannel::Drowsiness;
    }
}

static const VoiceAlertChannelConfig &channel_config(AlertChannel c) {
    switch (c) {
        case AlertChannel::Sneeze:   return g_config.sneeze;
        case AlertChannel::Presence: return g_config.presence;
        default:                     return g_config.drowsiness;
    }
}

static const char *channel_name(AlertChannel c) {
    switch (c) {
        case AlertChannel::Sneeze:   return "sneeze";
        case AlertChannel::Presence: return "presence";
        default:                     return "drowsiness";
    }
}

bool voice_alert_init(const VoiceAlertConfig& config) {
    g_config = config;

    gpio_config_t io{};
    io.pin_bit_mask = 1ULL << BUZZER_GPIO;
    io.mode = GPIO_MODE_OUTPUT;
    if (gpio_config(&io) != ESP_OK) return false;
    gpio_set_level(BUZZER_GPIO, 0);

    g_audio = board_audio_init();
    if (!g_audio) {
        ESP_LOGW(TAG, "I2S amplifier unavailable; falling back to the buzzer on GPIO %d",
                 static_cast<int>(BUZZER_GPIO));
    }

    // Three deep, one per channel: the point of separating the channels is that a
    // sneeze and a no-driver announcement can both be pending behind a drowsiness
    // one without either being dropped.
    g_queue = xQueueCreate(3, sizeof(AlertReason));
    if (g_queue == nullptr) return false;
    // Priority 4: above the idle/UI work, below the camera driver, so an alert is
    // prompt without preempting frame capture.
    if (xTaskCreate(alert_audio_task, "alert_audio", 3072, nullptr, 4, nullptr) != pdPASS) {
        vQueueDelete(g_queue);
        g_queue = nullptr;
        return false;
    }

    // After the queue and task exist, so a stored language takes effect on the
    // very first alert rather than the second.
    lang_load();

    g_initialized = true;
    ESP_LOGI(TAG, "Alert controller initialized; language=%s%s output=%s",
             lang_code(g_config.language), g_lang_persisted ? " (stored)" : " (default)",
             g_audio ? "I2S/MAX98357A" : "buzzer");
    for (int c = 0; c < static_cast<int>(AlertChannel::Count); ++c) {
        const AlertChannel ch = static_cast<AlertChannel>(c);
        const VoiceAlertChannelConfig &cc = channel_config(ch);
        ESP_LOGI(TAG, "  channel %-11s cooldown %lu ms, cap %lu, reset %lu ms",
                 channel_name(ch), static_cast<unsigned long>(cc.cooldown_ms),
                 static_cast<unsigned long>(cc.max_repeat_count),
                 static_cast<unsigned long>(cc.repeat_reset_ms));
    }

    // Which source each reason will actually use. Worth a line at boot: a missing
    // or malformed clip is otherwise only discoverable by triggering the alert and
    // listening to what comes out.
    for (int r = 0; r < ALERT_REASON_COUNT; ++r) {
        const char *reason = voice_alert_clip_name(static_cast<AlertReason>(r));
        ESP_LOGI(TAG, "  %s_%s -> %s", lang_code(g_config.language), reason,
                 voice_clip_source_name(
                     voice_clip_probe(lang_code(g_config.language), reason)));
    }
    return true;
}

void voice_alert_set_language(AlertLanguage language) {
    if (g_config.language == language) return;
    g_config.language = language;
    lang_save(language);
    g_lang_persisted = true;
    ESP_LOGI(TAG, "alert language set to %s", lang_code(language));
}

bool voice_alert_set_language_code(const char *code) {
    if (code == nullptr) return false;
    if (strcmp(code, "en") == 0) { voice_alert_set_language(AlertLanguage::English); return true; }
    if (strcmp(code, "km") == 0) { voice_alert_set_language(AlertLanguage::Khmer); return true; }
    return false;
}

const char *voice_alert_language_code() { return lang_code(g_config.language); }

bool voice_alert_language_persisted() { return g_lang_persisted; }

uint32_t voice_alert_count() { return g_total_count; }

uint32_t voice_alert_count_for(AlertReason reason) {
    const uint8_t i = static_cast<uint8_t>(reason);
    return i < ALERT_REASON_COUNT ? g_reason_count[i] : 0;
}

void voice_alert_set_muted(bool muted) {
    if (g_muted != muted) ESP_LOGW(TAG, "alerts %s", muted ? "MUTED" : "unmuted");
    g_muted = muted;
}

bool voice_alert_muted() { return g_muted; }

bool voice_alert_test(AlertReason reason) {
    if (!g_initialized || g_muted) return false;
    return xQueueSend(g_queue, &reason, 0) == pdTRUE;
}

const char* voice_alert_banner_text(AlertReason reason) {
    switch (reason) {
        case AlertReason::Microsleep: return "WAKE UP";
        case AlertReason::Yawning:    return "TAKE A BREAK";
        case AlertReason::HeadNod:    return "STAY ALERT";
        case AlertReason::Sneeze:     return "SNEEZE DETECTED";
        case AlertReason::NoDriver:   return "NO DRIVER DETECTED";
        case AlertReason::Drowsy:
        default:                      return "DROWSY";
    }
}

const char* voice_alert_clip_name(AlertReason reason) {
    switch (reason) {
        case AlertReason::Microsleep: return "microsleep";
        case AlertReason::Yawning:    return "yawning";
        case AlertReason::HeadNod:    return "head_nod";
        case AlertReason::Sneeze:     return "sneeze";
        case AlertReason::NoDriver:   return "no_driver";
        case AlertReason::Drowsy:
        default:                      return "drowsy";
    }
}

// Roughly how long an announcement occupies the speaker; used only for the UI banner.
static constexpr uint32_t ANNOUNCE_MS = 2500;

bool voice_alert_is_active(uint32_t now_ms) {
    return g_total_count > 0 && (now_ms - g_last_any_ms) < ANNOUNCE_MS;
}

bool voice_alert_trigger(uint32_t now_ms, AlertReason reason) {
    if (!g_initialized) return false;

    const AlertChannel ch = voice_alert_channel(reason);
    ChannelState &st = g_channel[static_cast<int>(ch)];
    const VoiceAlertChannelConfig &cfg = channel_config(ch);

    // A long quiet spell ends the episode, so the repeat cap applies per episode
    // rather than per power cycle. Per channel, so a quiet drowsiness spell does not
    // reset the sneeze channel's episode or vice versa.
    if (st.repeats > 0 && cfg.repeat_reset_ms > 0 &&
        (now_ms - st.last_ms) >= cfg.repeat_reset_ms) {
        ESP_LOGI(TAG, "%lu ms quiet on the %s channel; repeat counter reset",
                 static_cast<unsigned long>(now_ms - st.last_ms), channel_name(ch));
        st.repeats = 0;
    }
    if (st.repeats > 0 && (now_ms - st.last_ms) < cfg.cooldown_ms) {
        return false;
    }
    if (cfg.max_repeat_count > 0 && st.repeats >= cfg.max_repeat_count) {
        return false;
    }

    st.last_ms = now_ms;
    ++st.repeats;
    g_last_any_ms = now_ms;
    ++g_total_count;
    if (static_cast<uint8_t>(reason) < ALERT_REASON_COUNT) {
        ++g_reason_count[static_cast<uint8_t>(reason)];
    }

    // Hand off and return: never block the capture loop on playback.
    if (xQueueSend(g_queue, &reason, 0) != pdTRUE) {
        ESP_LOGW(TAG, "alert queue full; dropped %s", voice_alert_clip_name(reason));
    }
    return true;
}
