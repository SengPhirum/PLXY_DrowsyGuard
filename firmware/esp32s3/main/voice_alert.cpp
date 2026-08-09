#include "voice_alert.h"

#include "driver/gpio.h"
#include "esp_log.h"

// Lightweight alert controller. Audio transport is intentionally isolated here so
// the selected ESP32-S3 board can later use ESP-IDF I2S with a MAX98357A-class
// amplifier without coupling audio hardware to drowsiness inference.

static const char *TAG = "voice_alert";
static VoiceAlertConfig g_config{};
static uint32_t g_last_alert_ms = 0;
static uint32_t g_repeat_count = 0;
static bool g_initialized = false;

// Replace after the physical board GPIO allocation is finalized.
static constexpr gpio_num_t BUZZER_GPIO = GPIO_NUM_2;

static void buzzer_pulse() {
    if (!g_config.buzzer_fallback) return;
    gpio_set_level(BUZZER_GPIO, 1);
    // Non-blocking buzzer timing should replace this simple fallback in the
    // hardware-validation phase.
    gpio_set_level(BUZZER_GPIO, 0);
}

bool voice_alert_init(const VoiceAlertConfig& config) {
    g_config = config;
    gpio_config_t io{};
    io.pin_bit_mask = 1ULL << BUZZER_GPIO;
    io.mode = GPIO_MODE_OUTPUT;
    if (gpio_config(&io) != ESP_OK) return false;
    gpio_set_level(BUZZER_GPIO, 0);
    g_initialized = true;
    ESP_LOGI(TAG, "Alert controller initialized; language=%s cooldown=%lu ms",
             g_config.language == AlertLanguage::Khmer ? "km" : "en",
             static_cast<unsigned long>(g_config.cooldown_ms));
    return true;
}

void voice_alert_set_language(AlertLanguage language) {
    g_config.language = language;
}

bool voice_alert_trigger(uint32_t now_ms) {
    if (!g_initialized) return false;
    if (g_repeat_count > 0 && (now_ms - g_last_alert_ms) < g_config.cooldown_ms) {
        return false;
    }
    if (g_config.max_repeat_count > 0 && g_repeat_count >= g_config.max_repeat_count) {
        return false;
    }

    g_last_alert_ms = now_ms;
    ++g_repeat_count;

    // TODO(HW): stream the matching embedded PCM/WAV asset over I2S after the
    // exact board pins and amplifier are validated. Keeping recorded speech in
    // flash is intentionally preferred over MCU text-to-speech for predictable
    // latency, memory use and multilingual output.
    ESP_LOGW(TAG, "DROWSINESS ALERT: play assets/audio/%s_warning.wav",
             g_config.language == AlertLanguage::Khmer ? "km" : "en");
    buzzer_pulse();
    return true;
}
