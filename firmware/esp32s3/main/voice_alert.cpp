#include "voice_alert.h"

#include "board_audio.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

// Alert controller. Audio transport lives in board_audio.cpp so the amplifier can
// be swapped without touching drowsiness inference.
//
// Playback runs on its own task. voice_alert_trigger() is called from the capture
// loop, which has a ~23 ms frame budget (docs/FIRMWARE_PIPELINE.md); playing even a
// half-second alert inline would drop roughly twenty frames and freeze the preview
// at exactly the moment the driver needs to see it.

static const char *TAG = "voice_alert";
static VoiceAlertConfig g_config{};
static uint32_t g_last_alert_ms = 0;
static uint32_t g_repeat_count = 0;
static bool g_initialized = false;
static bool g_audio = false;

static QueueHandle_t g_queue = nullptr;

// Buzzer fallback, used when the I2S amplifier is absent or fails to initialize.
static constexpr gpio_num_t BUZZER_GPIO = GPIO_NUM_2;
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

        ESP_LOGW(TAG, "ALERT (%s): clip assets/audio/%s_%s.wav",
                 voice_alert_banner_text(reason),
                 g_config.language == AlertLanguage::Khmer ? "km" : "en",
                 voice_alert_clip_name(reason));

        if (g_audio) {
            const TonePattern p = pattern_for(reason);
            for (uint32_t i = 0; i < p.beeps; ++i) {
                board_audio_play_tone(p.freq_hz, p.beep_ms);
                if (p.gap_ms && i + 1 < p.beeps) vTaskDelay(pdMS_TO_TICKS(p.gap_ms));
            }
            board_audio_silence();
        } else {
            buzzer_pulse();
        }
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

    g_queue = xQueueCreate(2, sizeof(AlertReason));
    if (g_queue == nullptr) return false;
    // Priority 4: above the idle/UI work, below the camera driver, so an alert is
    // prompt without preempting frame capture.
    if (xTaskCreate(alert_audio_task, "alert_audio", 3072, nullptr, 4, nullptr) != pdPASS) {
        vQueueDelete(g_queue);
        g_queue = nullptr;
        return false;
    }

    g_initialized = true;
    ESP_LOGI(TAG, "Alert controller initialized; language=%s cooldown=%lu ms output=%s",
             g_config.language == AlertLanguage::Khmer ? "km" : "en",
             static_cast<unsigned long>(g_config.cooldown_ms),
             g_audio ? "I2S/MAX98357A" : "buzzer");
    return true;
}

void voice_alert_set_language(AlertLanguage language) {
    g_config.language = language;
}

const char* voice_alert_banner_text(AlertReason reason) {
    switch (reason) {
        case AlertReason::Microsleep: return "WAKE UP";
        case AlertReason::Yawning:    return "TAKE A BREAK";
        case AlertReason::HeadNod:    return "STAY ALERT";
        case AlertReason::Drowsy:
        default:                      return "DROWSY";
    }
}

const char* voice_alert_clip_name(AlertReason reason) {
    switch (reason) {
        case AlertReason::Microsleep: return "microsleep";
        case AlertReason::Yawning:    return "yawning";
        case AlertReason::HeadNod:    return "head_nod";
        case AlertReason::Drowsy:
        default:                      return "drowsy";
    }
}

// Roughly how long an announcement occupies the speaker; used only for the UI banner.
static constexpr uint32_t ANNOUNCE_MS = 2500;

bool voice_alert_is_active(uint32_t now_ms) {
    return g_repeat_count > 0 && (now_ms - g_last_alert_ms) < ANNOUNCE_MS;
}

bool voice_alert_trigger(uint32_t now_ms, AlertReason reason) {
    if (!g_initialized) return false;
    if (g_repeat_count > 0 && (now_ms - g_last_alert_ms) < g_config.cooldown_ms) {
        return false;
    }
    if (g_config.max_repeat_count > 0 && g_repeat_count >= g_config.max_repeat_count) {
        return false;
    }

    g_last_alert_ms = now_ms;
    ++g_repeat_count;

    // Hand off and return: never block the capture loop on playback.
    if (xQueueSend(g_queue, &reason, 0) != pdTRUE) {
        ESP_LOGW(TAG, "alert queue full; dropped %s", voice_alert_clip_name(reason));
    }
    return true;
}
