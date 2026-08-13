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
#include "test_frames.h"   // TEMP bring-up asset
#include "voice_alert.h"

static const char *TAG = "drowsyguard";

// TEMP bring-up: dump raw frames as base64 so the desktop pipeline can run on the
// exact bytes ESP-DL is handed. That settles two things at once - whether a failed
// detection is a bad image or a bad binding, and where real OV3660 eye crops for
// quantisation calibration come from. 240x240x2 is divisible by 3, so the encoding
// needs no padding.
static void dump_frame_b64(const uint8_t *d, size_t n, int idx) {
    static const char kB64[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    // A frame takes seconds to clock out, and other tasks keep logging while it
    // does - their lines land in the middle of the base64 and corrupt it. Mute the
    // log for the duration rather than making the parser guess what is data.
    esp_log_level_set("*", ESP_LOG_NONE);
    printf("\n#FRAME %d %u\n", idx, static_cast<unsigned>(n));
    // 1 KB per write, not 64 B: the console costs far more per call than per byte,
    // and at 64 B a single 240x240 frame took ~20 s to clock out.
    static char line[1025];
    int col = 0;
    uint32_t acc = 0;
    int bits = 0;
    for (size_t i = 0; i < n; ++i) {
        acc = (acc << 8) | d[i];
        bits += 8;
        while (bits >= 6) {
            bits -= 6;
            line[col++] = kB64[(acc >> bits) & 0x3F];
            if (col == 1024) {
                line[1024] = '\0';
                printf("%s\n", line);
                col = 0;
            }
        }
    }
    if (col > 0) {
        line[col] = '\0';
        printf("%s\n", line);
    }
    printf("#ENDFRAME %d\n", idx);
    fflush(stdout);
    esp_log_level_set("*", ESP_LOG_INFO);
}

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
        // A three-note rising chime rather than one 120 ms chirp. At boot the job is
        // to be unmistakable: a single short beep is easy to miss entirely and easy
        // to mistake for the click a class-D stage makes when it powers up, which is
        // exactly the ambiguity this is here to remove. Recorded speech would be
        // better still, but assets/audio/ holds only a README - no clips exist yet.
        board_audio_play_tone(660, 180);
        board_audio_play_tone(880, 180);
        board_audio_play_tone(1175, 260);
        board_audio_silence();
        ESP_LOGI(TAG, "boot chime played on I2S");
    } else {
        ESP_LOGW(TAG, "no audio output available; boot chime skipped");
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
    // power supply, which is the whole point of stage 1. The two models bind
    // independently - the face detector is in, the eye model is not yet - so they
    // are tracked separately rather than as one "models are up" flag.
    const bool models = model_init();
    const bool eye_model = model_eye_ready();

    // TEMP bring-up: run the detector on a real captured frame at three face scales.
    // Same bytes every boot, so it separates "the binding is wrong" from "nobody was
    // in front of the camera" and from "the face was too close for the anchors".
    if (models) {
        for (int i = 0; i < kTestFrameCount; ++i) {
            FaceDetection d{};
            const bool hit = model_detect_face(kTestFrames[i].data, CAM_FRAME_W, CAM_FRAME_H, &d);
            ESP_LOGW(TAG, "selftest scale %.2f -> %s score %.2f box %d,%d %dx%d",
                     kTestFrames[i].scale, hit ? "FACE" : "none", d.score, d.x, d.y, d.w, d.h);
        }
        // The image is known good (desktop YuNet scores it 0.861), so if every scale
        // misses, the input format is the suspect rather than the content. Try the
        // other byte order, and RGB888, on the same bytes.
        const size_t px = static_cast<size_t>(CAM_FRAME_W) * CAM_FRAME_H;
        uint8_t *swapped = static_cast<uint8_t *>(heap_caps_malloc(px * 2, MALLOC_CAP_SPIRAM));
        if (swapped != nullptr) {
            for (size_t i = 0; i < px; ++i) {
                swapped[2 * i + 0] = kTestFrames[0].data[2 * i + 1];
                swapped[2 * i + 1] = kTestFrames[0].data[2 * i + 0];
            }
            FaceDetection d{};
            const bool hit = model_detect_face(swapped, CAM_FRAME_W, CAM_FRAME_H, &d);
            ESP_LOGW(TAG, "selftest rgb565 byte-swapped -> %s score %.2f", hit ? "FACE" : "none", d.score);
            heap_caps_free(swapped);
        }
        uint8_t *rgb888 = static_cast<uint8_t *>(heap_caps_malloc(px * 3, MALLOC_CAP_SPIRAM));
        if (rgb888 != nullptr) {
            for (size_t i = 0; i < px; ++i) {
                const uint16_t v = static_cast<uint16_t>((kTestFrames[0].data[2 * i] << 8) |
                                                         kTestFrames[0].data[2 * i + 1]);
                rgb888[3 * i + 0] = static_cast<uint8_t>(((v >> 11) & 0x1F) << 3);
                rgb888[3 * i + 1] = static_cast<uint8_t>(((v >> 5) & 0x3F) << 2);
                rgb888[3 * i + 2] = static_cast<uint8_t>((v & 0x1F) << 3);
            }
            FaceDetection d{};
            const bool hit = model_detect_face_rgb888(rgb888, CAM_FRAME_W, CAM_FRAME_H, &d);
            ESP_LOGW(TAG, "selftest rgb888 -> %s score %.2f box %d,%d %dx%d",
                     hit ? "FACE" : "none", d.score, d.x, d.y, d.w, d.h);
            heap_caps_free(rgb888);
        }
    }

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
    // TEMP bring-up counters. The cumulative pair matters: it decouples "does the
    // detector ever work" from "was anyone in front of the camera when the log was
    // sampled", which a per-interval count cannot tell apart.
    int det_tries = 0, det_hits = 0;
    int det_hits_total = 0;
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
                p_closed = 0.5f * (model_eye_closed_prob(fb->buf, fb->width, fb->height, last_lm, 0) +
                                   model_eye_closed_prob(fb->buf, fb->width, fb->height, last_lm, 1));
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
            //    dangerous way to be wrong. Better to say NO EYE MODEL on the panel
            //    than to ship an alarm that quietly never fires.
            if (eye_model) {
                const uint32_t now_ms = static_cast<uint32_t>(now_us / 1000);
                if (filter.update(st.score)) {
                    last_reason = AlertReason::Drowsy;
                    if (st.events & EVENT_MICROSLEEP)   last_reason = AlertReason::Microsleep;
                    else if (st.events & EVENT_NOD)     last_reason = AlertReason::HeadNod;
                    else if (st.events & EVENT_YAWN)    last_reason = AlertReason::Yawning;
                    voice_alert_trigger(now_ms, last_reason);
                }
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
        ui.no_eye_model = models && !eye_model;
        display_ui_render(ui);

        // TEMP bring-up: six frames at roughly 4 s intervals, starting ~8 s in, so
        // there is time to get in front of the camera before the first one lands.
        {
            static constexpr int TEMP_DUMP_FRAMES = 0;   // raise to capture more
            static int dumped = 0;
            const int fps_guess = 15;
            if (dumped < TEMP_DUMP_FRAMES && frame_no >= (8 + 4 * dumped) * fps_guess) {
                dump_frame_b64(fb->buf, fb->len, dumped);
                ++dumped;
            }
        }

        // TEMP bring-up diagnostic: is the white screen upstream of the blit (camera
        // frame or composed framebuffer) or downstream of it (panel addressing)?
        if ((frame_no + 1) % 60 == 0) {
            // Mean luminance, decoded properly rather than averaging the raw uint16:
            // a face detector needs a exposed image, and "too dark to detect" and
            // "detector broken" are indistinguishable from a hit count alone.
            const uint16_t *cam = reinterpret_cast<const uint16_t *>(fb->buf);
            const int cam_n = (fb->len / 2 > 4096) ? 4096 : static_cast<int>(fb->len / 2);
            uint32_t cs = 0, luma = 0; uint16_t cmin = 0xFFFF, cmax = 0;
            for (int i = 0; i < cam_n; ++i) {
                const uint16_t raw = cam[i];
                cs += raw; if (raw < cmin) cmin = raw; if (raw > cmax) cmax = raw;
                const uint16_t v = static_cast<uint16_t>((raw >> 8) | (raw << 8));
                const int r = ((v >> 11) & 0x1F) << 3;
                const int g = ((v >> 5) & 0x3F) << 2;
                const int b = (v & 0x1F) << 3;
                luma += static_cast<uint32_t>((r * 77 + g * 151 + b * 28) >> 8);
            }
            const int fb_n = LCD_H_RES * LCD_V_RES;
            uint32_t fs = 0; uint16_t fmin = 0xFFFF, fmax = 0;
            for (int i = 0; i < fb_n; ++i) {
                const uint16_t v = s_framebuffer[i];
                fs += v; if (v < fmin) fmin = v; if (v > fmax) fmax = v;
            }
            ESP_LOGI(TAG, "diag cam min=%04x max=%04x luma=%u/255 | fb min=%04x max=%04x avg=%04x",
                     cmin, cmax, static_cast<unsigned>(cam_n ? luma / cam_n : 0),
                     fmin, fmax, static_cast<unsigned>(fs / fb_n));
            ESP_LOGI(TAG, "diag face %d/%d (total %d, best %.2f) box %d,%d %dx%d | geom valid %d "
                          "jaw %.3f nose %.3f roll %.1f eyed %.1f | base %d mouth %d head %d",
                     det_hits, det_tries, det_hits_total, det_best_score,
                     det.x, det.y, det.w, det.h,
                     geom.valid ? 1 : 0, geom.jaw_drop, geom.nose_frac, geom.roll, geom.eye_dist,
                     st.baselines_ready ? 1 : 0, st.mouth_open ? 1 : 0, st.head_down ? 1 : 0);
            (void)cs;
            det_tries = 0;
            det_hits = 0;
        }

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
