#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "behavior.h"
#include "board_audio.h"
#include "board_camera.h"
#include "board_sdcard.h"
#include "board_wifi.h"
#include "model_adapter.h"
#include "risk_filter.h"
#include "voice_alert.h"
#include "web_server.h"

static const char *TAG = "drowsyguard";

/*
Headless build. There is no SPI panel: the device is a camera, a speaker and an
access point.

  camera -> face + landmarks -> eye state -> behaviour fusion -> risk -> speaker
                             \-> JPEG snapshot -> browser preview + telemetry

Two consequences of dropping the panel that are worth stating explicitly, because
they shaped everything below:

  1. The speaker is now the only output the driver perceives. Anything that can
     silence it permanently is a safety defect, not an annoyance - see
     repeat_reset_ms in voice_alert.h.
  2. Diagnostics moved from an 8-pixel font on 240x320 glass to a browser. That is
     a strict upgrade in every dimension except one: the preview only exists while
     someone has the page open, so the pipeline must never depend on it. It does
     not - web_server_publish_frame() copies and returns, and skips entirely when
     nobody is watching.
*/

// Frame budget on ESP32-S3, from ESP-DL's published latencies:
//   msr_s8_v1 (face, 120x160)   33.1 ms  -- run every DETECT_EVERY frames only
//   mnp_s8_v1 (refine, 48x48)    5.8 ms  -- same cadence
//   open_closed_eye (32x32) x2  ~4-8 ms  -- every frame, both eyes
//   behaviour + PERCLOS          ~1-2 ms -- integer/float arithmetic
//   frame copy for the preview   ~1-2 ms -- only while a browser is watching
// Detecting every 3rd frame and tracking in between amortises the detector to ~13 ms,
// giving roughly 25 ms/frame => a comfortable 15-20 fps. PERCLOS needs temporal
// resolution, not high frame rate: 15 fps still resolves a 1 s closure into 15 samples.
static constexpr int DETECT_EVERY = 3;
static constexpr float TARGET_FPS = 15.0f;

// PERCLOS window in frames. At 15 fps, 45 frames is a 3 s window.
static constexpr int PERCLOS_WINDOW = 45;

// Risk is the fused behaviour score, so the trigger is a risk level rather than a
// raw probability. Tune these on the desktop dashboard and paste them here.
static constexpr float RISK_TRIGGER = 0.55f;
static constexpr int RISK_REQUIRED = 8;

// Bring-up aid, off by default. Runs the face detector over the captured frames in
// test_frames.h - the same bytes every boot, at three face scales - which separates
// "the ESP-DL binding is wrong" from "nobody was in front of the camera". Leave it
// at 0: for live debugging, http://192.168.4.1/api/snapshot now returns the actual
// frame the detector was handed, which is strictly better than a fixed asset.
#define MODEL_SELFTEST 0

#if MODEL_SELFTEST
#include "test_frames.h"
static void model_selftest() {
    for (int i = 0; i < kTestFrameCount; ++i) {
        FaceDetection d{};
        const bool hit = model_detect_face(kTestFrames[i].data, CAM_FRAME_W, CAM_FRAME_H, &d);
        ESP_LOGW(TAG, "selftest scale %.2f -> %s score %.2f box %d,%d %dx%d",
                 kTestFrames[i].scale, hit ? "FACE" : "none", d.score, d.x, d.y, d.w, d.h);
    }
}
#endif

// Keeps the status endpoint answering when the pipeline cannot run, so the page
// says which subsystem failed instead of timing out. Never returns.
[[noreturn]] static void degraded_loop(const WebStatus &base) {
    ESP_LOGE(TAG, "running degraded; open the web page for the reason");
    for (;;) {
        web_server_publish_status(base);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

extern "C" void app_main(void) {
    ESP_LOGI(TAG, "DrowsyGuard ESP32-S3 firmware starting (headless, web preview)");

    // Radio first. If everything after this fails, the page still loads and says so,
    // which is the whole point of having a network on a board with no screen.
    const bool net_up = board_wifi_init();
    if (!net_up) ESP_LOGE(TAG, "Wi-Fi bring-up failed; alerts still work, preview does not");

    // Before voice_alert_init() and before web_server_start(), and both matter:
    // the alert controller reports at boot which clip each reason resolves to, and
    // a card that is not mounted yet reads as "no card clips" - so a Khmer
    // recording sitting on the card would be reported as English. The web server
    // separately only allocates its event-capture buffers when there is somewhere
    // to write them. No card is not an error: detection and alerting never touch
    // the filesystem.
    if (board_sdcard_init()) {
        SdCardInfo card{};
        board_sdcard_info(&card);
        ESP_LOGI(TAG, "sd card \"%s\": %llu MB free of %llu MB, %d events on it",
                 card.name, card.free_bytes >> 20, card.total_bytes >> 20, card.events);
    } else {
        SdCardInfo card{};
        board_sdcard_info(&card);
        ESP_LOGW(TAG, "no event history: %s", card.error);
    }

    VoiceAlertConfig alert_config{};
    alert_config.language = AlertLanguage::English; // switch to Khmer once the clip is approved
    alert_config.cooldown_ms = 30000;
    alert_config.max_repeat_count = 3;
    alert_config.repeat_reset_ms = 300000;
    alert_config.buzzer_fallback = true;
    if (!voice_alert_init(alert_config)) {
        ESP_LOGE(TAG, "Alert subsystem initialization failed");
    }
    // A three-note rising chime at boot. With no panel this is the only local
    // confirmation that the board is alive, and it is the only way to tell an
    // amplifier that is wired but silent from one that was never initialized. A
    // single short beep is easy to miss and easy to mistake for the click a class-D
    // stage makes when it powers up, which is exactly the ambiguity to remove.
    if (board_audio_ready()) {
        board_audio_play_tone(660, 180);
        board_audio_play_tone(880, 180);
        board_audio_play_tone(1175, 260);
        board_audio_silence();
        ESP_LOGI(TAG, "boot chime played on I2S");
    } else {
        ESP_LOGW(TAG, "no audio output available; boot chime skipped");
    }

    const bool web_up = web_server_start();
    if (!web_up) ESP_LOGE(TAG, "web server failed to start; check PSRAM and free heap");

    WebStatus ui{};
    ui.trigger = RISK_TRIGGER;
    ui.required = RISK_REQUIRED;
    ui.frame_w = CAM_FRAME_W;
    ui.frame_h = CAM_FRAME_H;

    const camera_config_t cam = board_camera_config();
    const esp_err_t cam_err = esp_camera_init(&cam);
    if (cam_err != ESP_OK) {
        // Almost always one of: PSRAM not enabled, ribbon not seated, or a pin map
        // from a different board. See the troubleshooting table in
        // docs/HARDWARE_SETUP.md before touching board_camera.h.
        ESP_LOGE(TAG, "esp_camera_init failed: 0x%x (%s)", cam_err, esp_err_to_name(cam_err));
        degraded_loop(ui);
    }
    board_camera_tune();
    ui.camera_ok = true;
    ESP_LOGI(TAG, "camera up: %dx%d RGB565, PSRAM free %u B", CAM_FRAME_W, CAM_FRAME_H,
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));

    // Not fatal: without the models this still proves the camera, the radio, the
    // speaker and the power supply. The two models bind independently - the face
    // detector is in, the eye model is not yet - so they are tracked separately
    // rather than as one "models are up" flag.
    const bool models = model_init();
    const bool eye_model = model_eye_ready();
    ui.models_ok = models;
    ui.eye_model_ok = eye_model;
#if MODEL_SELFTEST
    if (models) model_selftest();
#endif

    RiskFilter filter(RISK_TRIGGER, RISK_REQUIRED, static_cast<int>(TARGET_FPS * 4));
    Perclos perclos(PERCLOS_WINDOW, 0.5f);
    BehaviorAnalyzer behavior(0.5f, TARGET_FPS);

    Landmarks last_lm{};
    FaceDetection det{};
    uint32_t frame_no = 0;
    int misses = 0;
    // The cumulative pair matters: it decouples "does the detector ever work" from
    // "was anyone in front of the camera when the log was sampled", which a
    // per-interval count cannot tell apart.
    int det_tries = 0, det_hits = 0, det_hits_total = 0;
    float det_best_score = 0.0f;
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
        FaceGeometry geom{};

        if (models) {
            // 1. Face + landmarks, only every DETECT_EVERY frames; hold the last box
            //    in between. Holding matters: detectors tend to drop the face exactly
            //    when the eyes close, which is the moment of interest.
            if (frame_no % DETECT_EVERY == 0) {
                FaceDetection d{};
                ++det_tries;
                if (model_detect_face(fb->buf, fb->width, fb->height, &d)) {
                    ++det_hits;
                    ++det_hits_total;
                    if (d.score > det_best_score) det_best_score = d.score;
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
            if (found && eye_model) {
                // Both eyes, averaged. det.w is the face box side the crop is
                // sized from, so it has to be the box the landmarks came from -
                // which is why the held box is used on tracked frames too.
                const int face_side = det.w > 0 ? det.w : fb->width / 3;
                p_closed = 0.5f * (model_eye_closed_prob(fb->buf, fb->width, fb->height,
                                                         last_lm, 0, face_side) +
                                   model_eye_closed_prob(fb->buf, fb->width, fb->height,
                                                         last_lm, 1, face_side));
            }

            // 3. Behaviour fusion: PERCLOS + long blinks + yawn + nod, sneeze-suppressed.
            //    Still runs without the eye model: the geometry half (jaw drop, head
            //    pitch, roll) is real and worth showing. Only the eye-derived half is
            //    missing, and PERCLOS reads a flat zero because of it.
            const float pc = perclos.update(p_closed);
            geom = behavior_face_geometry(last_lm);
            st = behavior.update(p_closed, geom, pc, dt);

            // 4. Alert, naming the reason so the spoken message is actionable.
            //    Gated on the eye model: with PERCLOS pinned at zero the fused score
            //    can only under-report, and for a drowsiness alarm silence is the
            //    dangerous way to be wrong. The web page says EYE MODEL MISSING
            //    rather than shipping an alarm that quietly never fires.
            if (eye_model) {
                const uint32_t now_ms = static_cast<uint32_t>(now_us / 1000);
                if (filter.update(st.score)) {
                    last_reason = AlertReason::Drowsy;
                    if (st.events & EVENT_MICROSLEEP)   last_reason = AlertReason::Microsleep;
                    else if (st.events & EVENT_NOD)     last_reason = AlertReason::HeadNod;
                    else if (st.events & EVENT_YAWN)    last_reason = AlertReason::Yawning;
                    voice_alert_trigger(now_ms, last_reason);
                    // File the frame that caused it. The speaker is what wakes the
                    // driver; this is what lets anyone afterwards check whether the
                    // alarm was right - which is the difference between a warning
                    // and evidence. Costs one memcpy here; the encode and the card
                    // write happen on another task.
                    web_server_capture_event(fb->buf, fb->width, fb->height, fb->len,
                                             st.score, st.perclos,
                                             voice_alert_clip_name(last_reason), now_ms);
                }
            }
        }

        // 5. Hand the frame to the browser. Returns immediately - and does nothing
        //    at all - when no page is open, so the detection path costs the same in
        //    a vehicle as it does on the bench.
        web_server_publish_frame(fb->buf, fb->width, fb->height, fb->len);

        ui.face_found = found;
        ui.face_held = found && (frame_no % DETECT_EVERY != 0);
        ui.face_x = det.x;
        ui.face_y = det.y;
        ui.face_w = found ? det.w : 0;
        ui.face_h = found ? det.h : 0;
        ui.face_score = det.score;
        ui.frame_w = fb->width;
        ui.frame_h = fb->height;
        ui.state = st;
        ui.geom = geom;
        ui.streak = filter.streak();
        ui.fps = (dt > 0.0f) ? (1.0f / dt) : 0.0f;

        // Frame brightness, on a 1024-pixel stride sample so it costs nothing.
        // Decoded properly rather than averaging the raw uint16: RGB565 packs the
        // channels unevenly, so the mean of the raw words is not a luminance and
        // would move even on a frame that did not change brightness.
        {
            const int px = static_cast<int>(fb->len / 2);
            const int stride = px > 1024 ? px / 1024 : 1;
            uint32_t sum = 0;
            int n = 0, lo = 255, hi = 0;
            for (int i = 0; i < px; i += stride) {
                const uint8_t *p = fb->buf + static_cast<size_t>(i) * 2;
                const uint16_t v = static_cast<uint16_t>((p[0] << 8) | p[1]);
                const int r = ((v >> 11) & 0x1F) << 3;
                const int g = ((v >> 5) & 0x3F) << 2;
                const int b = (v & 0x1F) << 3;
                const int y = (r * 77 + g * 151 + b * 28) >> 8;
                sum += static_cast<uint32_t>(y);
                ++n;
                if (y < lo) lo = y;
                if (y > hi) hi = y;
            }
            ui.luma = n ? static_cast<float>(sum) / n : 0.0f;
            ui.luma_min = lo;
            ui.luma_max = hi;
            // Peak-hold, decayed once a second along with the log line below, so
            // the page shows "the brightest frame in the last second" rather than
            // "the frame that happened to be current when you polled".
            if (static_cast<int>(ui.luma) > ui.luma_peak) {
                ui.luma_peak = static_cast<int>(ui.luma);
            }
        }
        ui.alerting = voice_alert_is_active(static_cast<uint32_t>(now_us / 1000));
        ui.alert_text = voice_alert_banner_text(last_reason);
        ui.alert_reason = voice_alert_clip_name(last_reason);
        ui.alert_count = voice_alert_count();
        ui.frames = frame_no;
        web_server_publish_status(ui);

        esp_camera_fb_return(fb);

        // One line a second is enough to record the numbers the acceptance tests in
        // docs/DEPLOYMENT.md ask for, without flooding the monitor.
        if (++frame_no % 60 == 0) {
            ESP_LOGI(TAG, "fps %.1f  risk %.2f  perclos %.2f  luma %.0f (%d-%d) peak %d"
                          "  face %d/%d (total %d, best %.2f)"
                          "  viewers %d  heap %u  psram %u",
                     ui.fps, st.score, st.perclos, ui.luma, ui.luma_min, ui.luma_max,
                     ui.luma_peak,
                     det_hits, det_tries, det_hits_total,
                     det_best_score, web_server_has_viewer() ? 1 : 0,
                     static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                     static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
            det_tries = 0;
            det_hits = 0;
            ui.luma_peak = 0;
        }
    }
}
