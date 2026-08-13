#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "behavior.h"
#include "board_audio.h"
#include "board_camera.h"
#include "board_display.h"
#include "display_ui.h"
#include "model_adapter.h"
#include "risk_filter.h"
#include "voice_alert.h"

static const char *TAG = "drowsyguard";

// Frame budget on ESP32-S3, from ESP-DL's published latencies:
//   msr_s8_v1 (face, 120x160)   33.1 ms  -- run every DETECT_EVERY frames only
//   mnp_s8_v1 (refine, 48x48)    5.8 ms  -- same cadence
//   open_closed_eye (32x32) x2  ~4-8 ms  -- every frame, both eyes
//   behaviour + PERCLOS + UI     ~2-5 ms -- integer/float arithmetic and one blit
// Detecting every 3rd frame and tracking in between amortises the detector to ~13 ms,
// giving roughly 25 ms/frame => a comfortable 15-20 fps. PERCLOS needs temporal
// resolution, not high frame rate: 15 fps still resolves a 1 s closure into 15 samples.
static constexpr int DETECT_EVERY = 3;
static constexpr float TARGET_FPS = 15.0f;

// PERCLOS window in frames. At 15 fps, 45 frames is a 3 s window.
static constexpr int PERCLOS_WINDOW = 45;

// Sized to the panel, not to the camera: display_ui composes at panel resolution and
// scales the preview down on the way in. 128x160x2 = 40 KB, internal RAM.
static uint16_t s_framebuffer[LCD_H_RES * LCD_V_RES];

extern "C" void app_main(void) {
    ESP_LOGI(TAG, "DrowsyGuard ESP32-S3 research firmware starting");

    VoiceAlertConfig alert_config{};
    alert_config.language = AlertLanguage::English; // switch to Khmer once the clip is approved
    alert_config.cooldown_ms = 30000;
    alert_config.max_repeat_count = 3;
    alert_config.buzzer_fallback = true;
    if (!voice_alert_init(alert_config)) {
        ESP_LOGE(TAG, "Alert subsystem initialization failed");
    }
    // One short chirp at boot. It costs 120 ms and it is the only way to tell an
    // amplifier that is wired but silent from one that was never initialized -
    // the difference between a wiring fault and a firmware fault during bring-up.
    if (board_audio_ready()) {
        board_audio_play_tone(880, 120);
        board_audio_silence();
    }

    if (!board_display_init()) {
        ESP_LOGE(TAG, "LCD init failed; check the wiring in board_display.h");
    }
    if (!display_ui_init(s_framebuffer, LCD_H_RES, LCD_V_RES, board_display_blit)) {
        ESP_LOGE(TAG, "Display UI init failed");
    }

    const camera_config_t cam = board_camera_config();
    const esp_err_t cam_err = esp_camera_init(&cam);
    if (cam_err != ESP_OK) {
        // Almost always one of: PSRAM not enabled, ribbon not seated, or a pin map
        // from a different board. See the troubleshooting table in
        // docs/HARDWARE_SETUP.md before touching board_camera.h.
        ESP_LOGE(TAG, "esp_camera_init failed: 0x%x (%s)", cam_err, esp_err_to_name(cam_err));
        DisplayInput ui{};
        ui.alerting = true;
        ui.alert_text = "NO CAMERA";
        display_ui_render(ui);
        return;
    }
    board_camera_tune();
    ESP_LOGI(TAG, "camera up: %dx%d RGB565, PSRAM free %u B", CAM_FRAME_W, CAM_FRAME_H,
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));

    // Not fatal: without the models this still proves the camera, the panel and the
    // power supply, which is the whole point of stage 1.
    const bool models = model_init();

    // Risk is the fused behaviour score, so the trigger is a risk level rather than a
    // raw probability. Tune these on the desktop dashboard and paste them here.
    static constexpr float RISK_TRIGGER = 0.55f;
    RiskFilter filter(RISK_TRIGGER, 8, static_cast<int>(TARGET_FPS * 4));
    Perclos perclos(PERCLOS_WINDOW, 0.5f);
    BehaviorAnalyzer behavior(0.5f, TARGET_FPS);

    Landmarks last_lm{};
    FaceDetection det{};
    int frame_no = 0;
    int misses = 0;
    AlertReason last_reason = AlertReason::Drowsy;
    int64_t last_us = esp_timer_get_time();

    for (;;) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb == nullptr) {
            ESP_LOGW(TAG, "frame grab failed");
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        const int64_t now_us = esp_timer_get_time();
        const float dt = static_cast<float>(now_us - last_us) / 1e6f;
        last_us = now_us;

        bool found = false;
        float p_closed = 0.0f;
        BehaviorState st{};

        if (models) {
            // 1. Face + landmarks, only every DETECT_EVERY frames; hold the last box
            //    in between. Holding matters: detectors tend to drop the face exactly
            //    when the eyes close, which is the moment of interest.
            if (frame_no % DETECT_EVERY == 0) {
                FaceDetection d{};
                if (model_detect_face(fb->buf, fb->width, fb->height, &d)) {
                    det = d;
                    last_lm = behavior_from_espdl_keypoints(d.keypoint);
                    found = true;
                    misses = 0;
                } else if (++misses < DETECT_EVERY * 5) {
                    found = last_lm.valid;                 // hold
                } else {
                    last_lm.valid = false;
                    det.valid = false;
                }
            } else {
                found = last_lm.valid;
            }

            // 2. Eye state on both eyes, every frame (cheap: 11.3k-parameter model).
            if (found) {
                p_closed = 0.5f * (model_eye_closed_prob(fb->buf, fb->width, fb->height, last_lm, 0) +
                                   model_eye_closed_prob(fb->buf, fb->width, fb->height, last_lm, 1));
            }

            // 3. Behaviour fusion: PERCLOS + long blinks + yawn + nod, sneeze-suppressed.
            const float pc = perclos.update(p_closed);
            const FaceGeometry geom = behavior_face_geometry(last_lm);
            st = behavior.update(p_closed, geom, pc, dt);

            // 4. Alert, naming the reason so the spoken message is actionable.
            const uint32_t now_ms = static_cast<uint32_t>(now_us / 1000);
            if (filter.update(st.score)) {
                last_reason = AlertReason::Drowsy;
                if (st.events & EVENT_MICROSLEEP)   last_reason = AlertReason::Microsleep;
                else if (st.events & EVENT_NOD)     last_reason = AlertReason::HeadNod;
                else if (st.events & EVENT_YAWN)    last_reason = AlertReason::Yawning;
                voice_alert_trigger(now_ms, last_reason);
            }
        }

        // 5. Show the driver what it saw. In preview-only mode this still renders the
        //    camera feed and an empty risk bar, which is what validates the panel.
        DisplayInput ui{};
        ui.preview = reinterpret_cast<const uint16_t *>(fb->buf);
        ui.preview_w = fb->width;
        ui.preview_h = fb->height;
        ui.preview_swap_bytes = CAM_RGB565_BYTE_SWAP;
        ui.face_found = found;
        ui.face_held = found && (frame_no % DETECT_EVERY != 0);
        ui.face_x = det.x; ui.face_y = det.y; ui.face_side = found ? det.w : 0;
        ui.state = st;
        ui.trigger = RISK_TRIGGER;
        ui.alerting = voice_alert_is_active(static_cast<uint32_t>(now_us / 1000));
        ui.alert_text = voice_alert_banner_text(last_reason);
        ui.fps = (dt > 0.0f) ? (1.0f / dt) : 0.0f;
        ui.no_model = !models;
        display_ui_render(ui);

        esp_camera_fb_return(fb);

        // One line a second is enough to record the numbers the acceptance tests in
        // docs/DEPLOYMENT.md ask for, without flooding the monitor.
        if (++frame_no % 60 == 0) {
            ESP_LOGI(TAG, "fps %.1f  risk %.2f  perclos %.2f  heap %u  psram %u",
                     ui.fps, st.score, st.perclos,
                     static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                     static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
        }
    }
}
