#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "behavior.h"
#include "board_audio.h"
#include "eye_model.h"
#include "board_camera.h"
#include "board_sdcard.h"
#include "board_wifi.h"
#include "face_gate.h"
#include "model_adapter.h"
#include "presence.h"
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

// Frame budget. The loop now measures itself - ui.ms_detect and ui.ms_eye are on the
// status page and in the log line below - because the last set of estimates here was
// wrong by a factor of six, and an estimate that is never checked is just a comment.
//
// What is known, measured on this board:
//   face detect (msr_s8_v1 + mnp_s8_v1)  ~39 ms  -- every DETECT_EVERY frames
//   open_closed_eye, per eye             ~22 ms  -- ONE eye per frame, alternating
//   behaviour + PERCLOS                   <1 ms  -- plain arithmetic
//   frame copy for the preview           ~1-2 ms -- only while a browser is watching
//
// The eye model dominates, and it used to run on BOTH eyes every frame. Alternating
// them halves that for almost nothing: the two eyes of one face close together, so
// sampling one per frame still yields one closure measurement per frame - it is the
// per-eye refresh that drops to 2 frames, not the closure's time resolution. What
// feeds PERCLOS is the mean of the two most recent readings, which is the same
// quantity as before, one frame staler on one side.
static constexpr int DETECT_EVERY = 3;

// Even while tracking, sweep the whole frame this often (counted in detections, so
// every 30 frames). Two failures need it: a track that has drifted onto something
// else would otherwise keep confirming itself inside its own crop, and a driver who
// moved outside the crop would never be re-found.
static constexpr int FULL_SWEEP_EVERY = 10;

// Starting frame rate, used only until the loop has measured itself.
static constexpr float TARGET_FPS = 15.0f;

// Windows and confirmation delays, in SECONDS. They used to be frame counts, which
// silently coupled the alarm's sensitivity to the frame rate: 8 frames is half a
// second at 15 fps and a third of a second at 25, so making the loop faster made the
// alarm twitchier without anyone editing a threshold. They are converted to frames
// from the measured rate once a second - see retune_for_fps() below.
static constexpr float PERCLOS_WINDOW_S = 3.0f;
static constexpr float RISK_REQUIRED_S = 0.55f;
static constexpr float RISK_COOLDOWN_S = 4.0f;

// Risk is the fused behaviour score, so the trigger is a risk level rather than a
// raw probability. Tune this on the desktop dashboard and paste it here.
static constexpr float RISK_TRIGGER = 0.55f;

// Consecutive failed frame grabs before the camera is declared faulty.
//
// The distinction this number draws is the whole reason PresenceMonitor takes a
// health argument. A camera that has stopped delivering frames and a cabin with
// nobody in it both produce "no face", and announcing the wrong one is worse than
// saying nothing: "no driver detected" aimed at a driver who is sitting right there
// teaches them the device is broken, and it happens to be right - just not about
// them. Ten grabs is roughly 700 ms at the loop's own pace and comfortably past any
// single dropped frame, which esp32-camera produces routinely under Wi-Fi load.
static constexpr int CAM_FAIL_FAULT = 10;

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
        const bool hit = model_detect_face(kTestFrames[i].data, CAM_FRAME_W, CAM_FRAME_H,
                                           true, &d);
        ESP_LOGW(TAG, "selftest scale %.2f -> %s score %.2f box %d,%d %dx%d",
                 kTestFrames[i].scale, hit ? "FACE" : "none", d.score, d.x, d.y, d.w, d.h);
    }
}
#endif

// Frames per second -> frame counts, so the windows above stay the durations they
// were tuned as. Called once a second with a smoothed rate; both objects keep their
// history across the change.
//
// The bounds and the deadband are not defensive padding, they are both from
// hardware. The first retune after boot reported **109 fps** and duly set the PERCLOS
// window to its 300-frame ceiling - 20 seconds - and made the alarm demand 60
// consecutive frames. The camera's DMA queue starts full, so the first frames arrive
// far faster than the loop can sustain, and an average seeded at TARGET_FPS is
// dragged along with them. Anything outside what this pipeline can physically do is
// therefore not a reading, it is an artefact, and acting on it is worse than ignoring
// it: these numbers decide how sensitive the alarm is.
static constexpr float FPS_PLAUSIBLE_MIN = 5.0f;
static constexpr float FPS_PLAUSIBLE_MAX = 40.0f;

static void retune_for_fps(float fps, Perclos *perclos, RiskFilter *filter) {
    if (fps < FPS_PLAUSIBLE_MIN || fps > FPS_PLAUSIBLE_MAX) return;

    const int win = static_cast<int>(PERCLOS_WINDOW_S * fps + 0.5f);
    const int req = static_cast<int>(RISK_REQUIRED_S * fps + 0.5f);
    const int cool = static_cast<int>(RISK_COOLDOWN_S * fps + 0.5f);

    // Deadband. The estimate wanders by a frame or two between intervals and
    // resizing the window every second is churn for no gain, so only a change worth
    // acting on is acted on.
    const int have = perclos->window();
    if (win > 0 && (win - have > have / 10 || have - win > have / 10)) {
        perclos->set_window(win);
    }
    filter->retune(req, cool);
}

// One number, at boot, for the thing that dominates the frame budget.
//
// Worth its 30 ms because every previous figure for this was an estimate off a model
// card, and the one measurement that got taken came in six times higher. It runs on
// a fixed synthetic tensor, so it is a property of the board and the build rather
// than of whatever happened to be in front of the camera.
static void bench_eye_model(void) {
    static float tensor[EYE_INPUT_FLOATS];
    uint32_t seed = 12345u;
    for (int i = 0; i < EYE_INPUT_FLOATS; ++i) {
        seed = seed * 1103515245u + 12345u;
        tensor[i] = static_cast<float>((seed >> 16) & 0xFFFF) / 65535.0f - 0.5f;
    }
    eye_model_infer_closed(tensor);              // warm the caches
    const int reps = 20;
    const int64_t t0 = esp_timer_get_time();
    float sink = 0.0f;
    for (int i = 0; i < reps; ++i) sink += eye_model_infer_closed(tensor);
    const int64_t us = (esp_timer_get_time() - t0) / reps;
    ESP_LOGI(TAG, "eye model: %lld us per eye (%.0f eyes/s), checksum %.3f",
             us, us > 0 ? 1e6 / static_cast<double>(us) : 0.0, static_cast<double>(sink));
}

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
    // Per channel, not global. A sneeze acknowledgement or a "no driver" warning
    // held back by a drowsiness cooldown is a message that silently never arrives,
    // and silence is the one failure this device must not have - see voice_alert.h.
    alert_config.drowsiness = {30000, 3, 300000};
    alert_config.sneeze = {2000, 0, 0};
    alert_config.presence = {5000, 0, 0};
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

    if (eye_model) bench_eye_model();

    RiskFilter filter(RISK_TRIGGER,
                      static_cast<int>(RISK_REQUIRED_S * TARGET_FPS + 0.5f),
                      static_cast<int>(RISK_COOLDOWN_S * TARGET_FPS + 0.5f));
    Perclos perclos(static_cast<int>(PERCLOS_WINDOW_S * TARGET_FPS + 0.5f), 0.5f);
    BehaviorAnalyzer behavior(0.5f, TARGET_FPS);
    ui.required = filter.required();

    // Who the driver is, across time. Confirmation, the hold, the continuity check
    // and safe reacquisition all live in here rather than in this loop, because they
    // are arithmetic on boxes and the host tests drive them through whole sequences -
    // hands, empty frames, occlusion, a passenger appearing mid-track.
    FaceTrack track;
    // Whether there is a driver at all, and whether the answer can be trusted. The
    // second half is the point: an empty seat and a dead camera are the same
    // observation and completely different conclusions.
    PresenceMonitor presence;
    ui.presence_alert_after_s = presence.config().alert_after_s;

    Landmarks last_lm{};
    FaceDetection det{};
    uint32_t frame_no = 0;
    int detections = 0;
    int cam_fails = 0;

    // Latest reading for each eye, and which one is due. Held across frames because
    // only one is refreshed per frame; both are dropped when the face is lost, so a
    // stale closure from before the driver moved cannot leak into the new one.
    float p_eye[2] = {0.0f, 0.0f};
    int next_eye = 0;

    // Last state computed while a driver was actually present, so the page can keep
    // showing the numbers instead of flashing to zero the moment detection blinks.
    BehaviorState last_state{};

    // Smoothed frame rate, for retuning the windows. Smoothed because a single
    // frame that took twice as long - a full-frame sweep, an SD write - must not
    // resize the PERCLOS window.
    float fps_avg = TARGET_FPS;
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
            const int64_t fail_us = esp_timer_get_time();
            const float fail_dt = static_cast<float>(fail_us - last_us) / 1e6f;
            last_us = fail_us;
            if (++cam_fails == CAM_FAIL_FAULT) {
                ESP_LOGE(TAG, "camera has returned no frames for %d grabs; "
                              "reporting a camera fault, NOT an empty seat", cam_fails);
            }
            if (cam_fails >= CAM_FAIL_FAULT) {
                // Keep the status endpoint truthful while the pipeline is down. The
                // page then says "camera fault" instead of freezing on the last good
                // frame, and PresenceMonitor is told the reason so it suppresses the
                // no-driver alert rather than announcing a conclusion it cannot draw.
                const PresenceResult pr =
                    presence.update(false, PipelineHealth::CameraFault, fail_dt);
                ui.camera_ok = false;
                ui.face_found = false;
                ui.face_held = false;
                ui.driver_present = false;
                ui.presence_state = presence_state_name(pr.state);
                ui.health = presence_health_name(pr.health);
                ui.presence_absent_s = pr.absent_s;
                ui.presence_alerts = pr.alerts;
                web_server_publish_status(ui);
            }
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        if (cam_fails >= CAM_FAIL_FAULT) {
            ESP_LOGW(TAG, "camera recovered after %d failed grabs", cam_fails);
            ui.camera_ok = true;
        }
        cam_fails = 0;
        const int64_t now_us = esp_timer_get_time();
        const float dt = static_cast<float>(now_us - last_us) / 1e6f;
        last_us = now_us;

        bool found = false;
        bool fresh = false;                 // landmarks from THIS frame, not held
        FaceReject track_reject = FaceReject::None;
        float p_closed = 0.0f;
        BehaviorState st{};
        FaceGeometry geom{};
        int64_t detect_us = 0, eye_us = 0;

        if (models) {
            // 1. Face + landmarks, only every DETECT_EVERY frames; hold the last box
            //    in between. Holding matters: detectors tend to drop the face exactly
            //    when the eyes close, which is the moment of interest.
            if (frame_no % DETECT_EVERY == 0) {
                FaceDetection d{};
                ++det_tries;
                // Periodically ignore the track and sweep the whole frame, so a crop
                // that has drifted cannot keep confirming itself.
                const bool sweep = (detections % FULL_SWEEP_EVERY) == 0;
                const int64_t t_det = esp_timer_get_time();
                const bool hit = model_detect_face(fb->buf, fb->width, fb->height, sweep, &d);
                detect_us = esp_timer_get_time() - t_det;
                ++detections;
                if (hit) {
                    ++det_hits;
                    ++det_hits_total;
                    if (d.score > det_best_score) det_best_score = d.score;
                }

                // Everything about whether this detection is believed, whether it
                // continues the same face, and whether the hold has run out is
                // FaceTrack's. This loop used to do it inline with one integer, and
                // that integer could not express the two things that matter most:
                // that a single frame is not evidence, and that a candidate which
                // teleports is a different object rather than the same one moving.
                FaceBox cand;
                cand.x = d.x;
                cand.y = d.y;
                cand.w = d.w;
                cand.h = d.h;
                cand.valid = d.valid;
                const FaceTrackResult tr = track.update(hit, cand, d.lm, d.score,
                                                        fb->width, fb->height);
                track_reject = tr.reject;
                if (tr.fresh) {
                    det = d;                // box, score and landmarks all from this frame
                } else if (!tr.present) {
                    det.valid = false;
                }
                if (tr.lost) {
                    // The face has really been given up on. Stop searching where it
                    // used to be, and drop both eye readings: a closure measured
                    // before the driver moved must not leak into whatever comes next.
                    model_detect_forget();
                    p_eye[0] = p_eye[1] = 0.0f;
                }
                found = tr.present;
                // Landmarks count as fresh only when this frame produced them AND the
                // track is confirmed. A pending track has landmarks but no standing to
                // push them into a baseline.
                fresh = tr.fresh && tr.present;
                last_lm = tr.present ? tr.lm : Landmarks{};
            } else {
                // Between detections nothing new is known, so the track is read
                // rather than advanced - the hold is counted in detection attempts,
                // not in frames, so that a change to DETECT_EVERY cannot silently
                // change how long a face is held for.
                const FaceTrackResult tr = track.peek();
                found = tr.present;
                last_lm = tr.present ? tr.lm : Landmarks{};
            }

            // 2. Eye state - ONE eye per frame, alternating. See the frame budget
            //    note above: this is the single most expensive thing in the loop and
            //    the two eyes of a face close together, so refreshing one per frame
            //    costs nothing that matters and halves the bill.
            if (found && eye_model) {
                // det.w is the face box side the crop is sized from, so it has to be
                // the box the landmarks came from - which is why the held box is used
                // on tracked frames too.
                const int face_side = det.w > 0 ? det.w : fb->width / 3;
                const int64_t t_eye = esp_timer_get_time();
                p_eye[next_eye] = model_eye_closed_prob(fb->buf, fb->width, fb->height,
                                                        last_lm, next_eye, face_side);
                eye_us = esp_timer_get_time() - t_eye;
                next_eye ^= 1;
                // Mean of both eyes, as before, so the 0.5 threshold and the fusion
                // weights still mean what they were tuned to mean.
                p_closed = 0.5f * (p_eye[0] + p_eye[1]);
            }

            // 3. Behaviour fusion: PERCLOS + long blinks + yawn + nod, sneeze-suppressed.
            //    Still runs without the eye model: the geometry half (jaw drop, mouth
            //    width, both pitch channels, roll) is real and worth showing. Only the
            //    eye-derived half is missing, and PERCLOS reads a flat zero because
            //    of it.
            //
            //    `fresh` is what stops held landmarks being counted as evidence. They
            //    are identical to the last real ones, so feeding them in pushed up to
            //    a second of duplicate samples into a 10 s baseline and kept the mouth
            //    and nod timers running on a pose that no longer existed.
            //
            //    Nothing runs at all when there is no driver. Feeding p_closed = 0 in
            //    was worse than skipping: a zero there does not mean "nobody is
            //    there", it means "eyes wide open", so an empty cabin was being
            //    recorded as the most alert possible driver and the PERCLOS window
            //    filled with evidence about no one. The first seconds after a driver
            //    sat down were then averaged against that.
            if (found) {
                const float pc = perclos.update(p_closed);
                geom = behavior_face_geometry(last_lm);
                st = behavior.update(p_closed, geom, pc, dt, fresh);
                last_state = st;
            } else {
                // Hold the last reading for the page, marked absent, and let the
                // streak decay so a confirmation in progress cannot survive the
                // driver leaving.
                st = last_state;
                st.events = EVENT_NONE;
                filter.update(0.0f);
            }

            // 4. Alert, naming the reason so the spoken message is actionable.
            //    Gated on the eye model: with PERCLOS pinned at zero the fused score
            //    can only under-report, and for a drowsiness alarm silence is the
            //    dangerous way to be wrong. The web page says EYE MODEL MISSING
            //    rather than shipping an alarm that quietly never fires.
            const uint32_t now_ms = static_cast<uint32_t>(now_us / 1000);
            if (eye_model && found) {
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

                // 4b. The sneeze announcement, which deliberately does NOT go through
                //     the risk filter.
                //
                //     A sneeze is not drowsiness - the entire reason it is detected is
                //     to suppress the microsleep alarm it would otherwise trigger - so
                //     routing it through an accumulator that measures drowsiness would
                //     be wrong twice over: it would never reach the trigger on its own,
                //     and if it did it would be announcing the wrong thing. It is an
                //     edge, already de-duplicated and rate-limited inside
                //     BehaviorAnalyzer by SNEEZE_ALERT_COOLDOWN_S, and it lands on its
                //     own alert channel so a drowsiness cooldown cannot swallow it.
                //
                //     Announcing it at all is a decision worth defending. The driver
                //     has just closed their eyes for a second and heard nothing; the
                //     alternative is a system that appears to have missed it. One short
                //     acknowledgement says the device saw the event and classified it.
                if (st.sneeze_alert) {
                    last_reason = AlertReason::Sneeze;
                    voice_alert_trigger(now_ms, last_reason);
                    web_server_capture_event(fb->buf, fb->width, fb->height, fb->len,
                                             st.score, st.perclos,
                                             voice_alert_clip_name(last_reason), now_ms);
                }
            }

            // 4c. Nobody there.
            //
            //     Gated on `models` only, not on the eye model: finding a driver needs
            //     the face detector and nothing else, so this alert still works on a
            //     build where the eye model is missing - which is the build most
            //     likely to be silent about everything else.
            //
            //     PresenceMonitor decides, because the decision needs the one thing
            //     this loop cannot express in a boolean: whether "no face" means the
            //     seat is empty or the device has stopped working. It is fed the
            //     health, it debounces in both directions, and it returns an edge.
            const PresenceResult pres = presence.update(found, PipelineHealth::Ok, dt);
            if (pres.alert) {
                last_reason = AlertReason::NoDriver;
                voice_alert_trigger(now_ms, last_reason);
                web_server_capture_event(fb->buf, fb->width, fb->height, fb->len,
                                         st.score, st.perclos,
                                         voice_alert_clip_name(last_reason), now_ms);
                ESP_LOGW(TAG, "no driver for %.1f s (announcement %u)",
                         static_cast<double>(pres.absent_s),
                         static_cast<unsigned>(pres.alerts));
            }
            ui.presence_state = presence_state_name(pres.state);
            ui.health = presence_health_name(pres.health);
            ui.presence_absent_s = pres.absent_s;
            ui.presence_alerts = pres.alerts;
        } else {
            // No face detector at all. Nothing can be seen, so this is a device fault
            // and never an empty seat; PresenceMonitor is told so explicitly rather
            // than being left to infer it from a permanent absence.
            const PresenceResult pres =
                presence.update(false, PipelineHealth::ModelFault, dt);
            ui.presence_state = presence_state_name(pres.state);
            ui.health = presence_health_name(pres.health);
            ui.presence_absent_s = pres.absent_s;
            ui.presence_alerts = pres.alerts;
        }

        // 5. Hand the frame to the browser. Returns immediately - and does nothing
        //    at all - when no page is open, so the detection path costs the same in
        //    a vehicle as it does on the bench.
        web_server_publish_frame(fb->buf, fb->width, fb->height, fb->len);

        ui.face_found = found;
        ui.face_held = found && !fresh;
        ui.face_x = det.x;
        ui.face_y = det.y;
        ui.face_w = found ? det.w : 0;
        ui.face_h = found ? det.h : 0;
        ui.face_score = det.score;
        ui.frame_w = fb->width;
        ui.frame_h = fb->height;
        ui.lm = found ? last_lm : Landmarks{};
        ui.driver_present = found;
        ui.state = st;
        ui.geom = geom;
        ui.streak = filter.streak();
        ui.required = filter.required();
        ui.fps = (dt > 0.0f) ? (1.0f / dt) : 0.0f;
        if (ui.fps > 0.0f) fps_avg += 0.05f * (ui.fps - fps_avg);
        if (detect_us > 0) ui.ms_detect = static_cast<float>(detect_us) / 1000.0f;
        if (eye_us > 0) ui.ms_eye = static_cast<float>(eye_us) / 1000.0f;
        {
            ModelDetectStats ds{};
            model_detect_stats(&ds);
            ui.detect_roi = ds.used_roi;
            ui.detect_roi_w = ds.roi_w;
            ui.detect_rejected = ds.rejected;
            // The track's verdict when it has one, because that is the decision that
            // was actually acted on: model_adapter's stats describe the candidates
            // ESP-DL produced, and FaceTrack can refuse a candidate those stats call
            // perfectly good - a plausible face in the wrong place.
            ui.detect_reject = face_gate_reject_name(
                track_reject != FaceReject::None ? track_reject : ds.reject);
        }

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
        // Not gated on `found` any more. The no-driver announcement happens exactly
        // when no face is present, so requiring one would have hidden the banner for
        // the one alert whose entire subject is that there is nobody there.
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
            // Windows are durations, frames are what the objects count, and the frame
            // rate is not a constant - so re-derive one from the other before the
            // mismatch quietly changes how sensitive the alarm is.
            //
            // Not for the first two intervals: the estimate has not settled yet, and
            // retune_for_fps() explains what happened when it was trusted early.
            const int was = perclos.window();
            if (frame_no >= 120) retune_for_fps(fps_avg, &perclos, &filter);
            if (perclos.window() != was) {
                ESP_LOGI(TAG, "retuned for %.1f fps: perclos window %d -> %d frames, "
                              "risk needs %d frames",
                         fps_avg, was, perclos.window(), filter.required());
            }

            ESP_LOGI(TAG, "fps %.1f  detect %.1f ms  eye %.1f ms  risk %.2f  perclos %.2f"
                          "  luma %.0f (%d-%d) peak %d"
                          "  face %d/%d (total %d, best %.2f, roi %d, gate would drop %d %s)"
                          "  viewers %d  heap %u  psram %u",
                     ui.fps, ui.ms_detect, ui.ms_eye, st.score, st.perclos,
                     ui.luma, ui.luma_min, ui.luma_max, ui.luma_peak,
                     det_hits, det_tries, det_hits_total, det_best_score,
                     ui.detect_roi ? ui.detect_roi_w : 0, ui.detect_rejected,
                     ui.detect_reject != nullptr ? ui.detect_reject : "ok",
                     web_server_has_viewer() ? 1 : 0,
                     static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                     static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
            det_tries = 0;
            det_hits = 0;
            ui.luma_peak = 0;
        }
    }
}
