#include "board_button.h"

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "wifi_provision.h"

static const char *TAG = "button";

static ButtonWatch s_watch;
static ButtonResetFn s_on_reset = nullptr;
static volatile bool s_armed = false;
static volatile uint32_t s_held_ms = 0;

// Polled at WIFI_BUTTON_POLL_MS rather than interrupt-driven, and that is a choice
// rather than laziness: the debounce and the long-press both need a clock anyway, an
// interrupt on a bouncing mechanical contact is a burst of interrupts rather than
// one, and 20 wake-ups a second on a task that does nothing else is unmeasurable
// next to a 39 ms face detection.
static void button_task(void *) {
    // Core 1 and priority 2: below the web servers (5) and the MQTT publisher (4),
    // far below the alert task, and on the other core from the capture loop and
    // ESP-DL. Nothing here is urgent - the worst case of a late poll is that a
    // five-second hold is noticed at 5.05 seconds.
    for (;;) {
        // Active low: the button pulls the pin to ground, the internal pull-up holds
        // it high the rest of the time.
        const bool pressed = gpio_get_level(static_cast<gpio_num_t>(BUTTON_GPIO)) == 0;
        const uint32_t now_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000);
        const WifiButtonEvent e = s_watch.update(pressed, now_ms);
        s_armed = s_watch.armed();
        s_held_ms = s_watch.held_ms();

        switch (e) {
            case WifiButtonEvent::Warning:
                // Two seconds in. Said before anything is lost, so a press that was
                // not meant can still be released.
                ESP_LOGW(TAG, "BOOT held - keep holding for %d more seconds to CLEAR "
                              "THE WI-FI CREDENTIALS, or release now to cancel",
                         (WIFI_BUTTON_HOLD_MS - WIFI_BUTTON_WARN_MS) / 1000);
                break;
            case WifiButtonEvent::Cancelled:
                ESP_LOGI(TAG, "BOOT released - nothing was changed");
                break;
            case WifiButtonEvent::Fired:
                ESP_LOGW(TAG, "BOOT held %d s - clearing the Wi-Fi credentials",
                         WIFI_BUTTON_HOLD_MS / 1000);
                if (s_on_reset != nullptr) s_on_reset();
                break;
            case WifiButtonEvent::Stuck:
                // Almost always a serial cable rather than a fault, so the message
                // names that first: this board's reset lines are inverted and
                // pyserial pulls GPIO0 low when it opens the port.
                ESP_LOGW(TAG, "GPIO%d has been low since boot, so the BOOT button is "
                              "disabled. A serial monitor holding DTR does this on "
                              "this board; unplug it, or press and release BOOT once.",
                         BUTTON_GPIO);
                break;
            case WifiButtonEvent::None:
                break;
        }
        vTaskDelay(pdMS_TO_TICKS(WIFI_BUTTON_POLL_MS));
    }
}

bool board_button_start(ButtonResetFn on_reset) {
    s_on_reset = on_reset;
    s_watch.reset();

    gpio_config_t cfg = {};
    cfg.pin_bit_mask = 1ULL << BUTTON_GPIO;
    cfg.mode = GPIO_MODE_INPUT;
    // The board has an external pull-up on BOOT, but enabling the internal one costs
    // nothing and means a bare module with no button fitted reads as "released"
    // rather than floating - and a floating input would arm and fire at random.
    cfg.pull_up_en = GPIO_PULLUP_ENABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    if (gpio_config(&cfg) != ESP_OK) {
        ESP_LOGE(TAG, "could not configure GPIO%d; the Wi-Fi reset button is off",
                 BUTTON_GPIO);
        return false;
    }

    if (xTaskCreatePinnedToCore(button_task, "button", 3072, nullptr, 2, nullptr, 1)
        != pdPASS) {
        ESP_LOGE(TAG, "button task failed to start; the Wi-Fi reset button is off");
        return false;
    }
    ESP_LOGI(TAG, "BOOT button watcher up: hold %d s to clear the Wi-Fi credentials "
                  "(nothing else is touched)", WIFI_BUTTON_HOLD_MS / 1000);
    return true;
}

bool board_button_armed() { return s_armed; }
uint32_t board_button_held_ms() { return s_held_ms; }
