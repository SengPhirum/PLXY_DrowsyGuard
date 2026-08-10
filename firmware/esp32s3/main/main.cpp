#include "esp_log.h"
#include "esp_timer.h"

#include "behavior.h"
#include "board_camera.h"
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

static uint16_t s_framebuffer[240 * 240];

static void lcd_blit(const uint16_t *fb, int w, int h) {
    // TODO(HW): esp_lcd_panel_draw_bitmap(panel, 0, 0, w, h, fb) once the board's
    // LCD controller and pins are known. Left unimplemented rather than guessed.
    (void)fb; (void)w; (void)h;
}

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

    if (!display_ui_init(s_framebuffer, 240, 240, lcd_blit)) {
        ESP_LOGE(TAG, "Display init failed");
    }

    if (!model_init()) {
        ESP_LOGE(TAG, "Model adapter is not configured. See firmware/esp32s3/README.md");
        return;
    }

    // Risk is the fused behaviour score, so the trigger is a risk level rather than a
    // raw probability. Tune these on the desktop dashboard and paste them here.
    RiskFilter filter(0.55f, 8, static_cast<int>(TARGET_FPS * 4));
    Perclos perclos(PERCLOS_WINDOW, 0.5f);
    BehaviorAnalyzer behavior(0.5f, TARGET_FPS);

    Landmarks last_lm{};
    int face_x = 0, face_y = 0, face_side = 0;
    int frame_no = 0;
    int misses = 0;
    uint32_t last_us = static_cast<uint32_t>(esp_timer_get_time());

    // Camera capture + crop/resize is integrated only after selecting the exact
    // board, because ESP32-S3 camera pin maps differ by module/revision.
    //
    // for (;;) {
    //     camera_fb_t *fb = esp_camera_fb_get();
    //     const uint32_t now_us = static_cast<uint32_t>(esp_timer_get_time());
    //     const float dt = (now_us - last_us) / 1e6f;
    //     last_us = now_us;
    //
    //     // 1. Face + landmarks, only every DETECT_EVERY frames; hold the last box
    //     //    in between. Holding matters: detectors tend to drop the face exactly
    //     //    when the eyes close, which is the moment of interest.
    //     bool found = false;
    //     if (frame_no % DETECT_EVERY == 0) {
    //         auto &results = detect_faces(fb);          // ESP-DL msr + mnp
    //         if (!results.empty()) {
    //             const auto &r = results.front();
    //             last_lm = behavior_from_espdl_keypoints(r.keypoint.data());
    //             face_x = r.box[0];
    //             face_y = r.box[1];
    //             face_side = r.box[2] - r.box[0];
    //             found = true;
    //             misses = 0;
    //         } else if (++misses < DETECT_EVERY * 5) {
    //             found = last_lm.valid;                 // hold
    //         } else {
    //             last_lm.valid = false;
    //         }
    //     } else {
    //         found = last_lm.valid;
    //     }
    //
    //     // 2. Eye state on both eyes, every frame (cheap: 11.3k-parameter model).
    //     float p_closed = 0.0f;
    //     if (found) {
    //         p_closed = 0.5f * (eye_closed_prob(fb, last_lm, /*eye=*/0) +
    //                            eye_closed_prob(fb, last_lm, /*eye=*/1));
    //     }
    //
    //     // 3. Behaviour fusion: PERCLOS + long blinks + yawn + nod, sneeze-suppressed.
    //     const float pc = perclos.update(p_closed);
    //     const FaceGeometry geom = behavior_face_geometry(last_lm);
    //     const BehaviorState st = behavior.update(p_closed, geom, pc, dt);
    //
    //     // 4. Alert, naming the reason so the spoken message is actionable.
    //     const uint32_t now_ms = now_us / 1000u;
    //     if (filter.update(st.score)) {
    //         AlertReason reason = AlertReason::Drowsy;
    //         if (st.events & EVENT_MICROSLEEP)   reason = AlertReason::Microsleep;
    //         else if (st.events & EVENT_NOD)     reason = AlertReason::HeadNod;
    //         else if (st.events & EVENT_YAWN)    reason = AlertReason::Yawning;
    //         voice_alert_trigger(now_ms, reason);
    //     }
    //
    //     // 5. Show the driver what it saw.
    //     DisplayInput ui{};
    //     ui.preview = reinterpret_cast<const uint16_t *>(fb->buf);
    //     ui.preview_w = fb->width;
    //     ui.preview_h = fb->height;
    //     ui.face_found = found;
    //     ui.face_held = found && (frame_no % DETECT_EVERY != 0);
    //     ui.face_x = face_x; ui.face_y = face_y; ui.face_side = face_side;
    //     ui.state = st;
    //     ui.trigger = 0.55f;
    //     ui.alerting = voice_alert_is_active(now_ms);
    //     ui.alert_text = voice_alert_banner_text(AlertReason::Drowsy);
    //     ui.fps = (dt > 0.0f) ? (1.0f / dt) : 0.0f;
    //     display_ui_render(ui);
    //
    //     esp_camera_fb_return(fb);
    //     ++frame_no;
    // }
    (void)filter; (void)perclos; (void)behavior;
    (void)face_x; (void)face_y; (void)face_side; (void)frame_no; (void)misses; (void)last_us;
}
