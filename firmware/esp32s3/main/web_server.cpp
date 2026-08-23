#include "web_server.h"

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "board_camera.h"
#include "board_sdcard.h"
#include "board_wifi.h"
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "img_converters.h"
#include "voice_alert.h"
#include "voice_clips.h"

static const char *TAG = "web";

// The page is linked into the binary rather than served from SPIFFS. It is a few
// kilobytes, it has to be available before any filesystem is mounted, and keeping
// it in flash rodata means the page can never be out of step with the firmware
// that produces the JSON it parses.
extern const uint8_t index_html_start[] asm("_binary_index_html_start");
extern const uint8_t index_html_end[] asm("_binary_index_html_end");

// --- MJPEG framing ---------------------------------------------------------
#define PART_BOUNDARY "drowsyguardframe"
static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static httpd_handle_t s_control = nullptr;
static httpd_handle_t s_stream = nullptr;

// --- frame handoff ---------------------------------------------------------
// Two snapshot buffers. The producer (detection loop) writes into whichever one is
// neither the newest nor being encoded; the consumer (stream task) encodes the
// newest. With a single consumer, two buffers are provably enough: at most one can
// be held for encoding, which always leaves one free to write into.
static uint8_t *s_snap[2] = {nullptr, nullptr};
static size_t s_snap_bytes = 0;      // capacity of each buffer
static size_t s_snap_len = 0;        // bytes actually in the newest snapshot
static int s_snap_w = 0, s_snap_h = 0;
static int s_ready = -1;             // index of the newest complete snapshot
// A hold COUNT per buffer, not a flag. Two consumers can be encoding at once -
// the stream task on port 81 and a /api/snapshot on port 80 - and with a bool the
// second one to finish would clear the flag while the first was still reading,
// letting the capture loop overwrite the buffer mid-encode. The result is a
// half-old, half-new frame: a corrupt JPEG, which a browser renders as a torn or
// blank image. Rare, because the page only fetches stills when the stream slot is
// taken, but a counter costs nothing and removes the failure mode.
static int s_hold[2] = {0, 0};
static uint32_t s_seq = 0;           // bumped on every publish, so the streamer
                                     // can tell a new frame from the same one twice
static SemaphoreHandle_t s_frame_lock = nullptr;

// Frame delivery is demand-driven, and that is the whole point. The capture loop
// used to copy 115 kB into a snapshot buffer on every single frame the moment a
// viewer existed - 19 fps of copying to feed a 12 fps stream - and the wasted
// PSRAM bandwidth cost the detector real frames: measured 19.7 fps with no viewer
// against 10.0 fps with one. Now the streamer asks for a frame when it is ready
// for one, and the capture loop copies exactly then. Everything else falls
// through at the cost of one atomic read.
static std::atomic<bool> s_want_frame{false};
static SemaphoreHandle_t s_frame_signal = nullptr;   // given when a frame lands

// One encode buffer per server: the stream task and a /api/snapshot request run on
// different httpd instances, i.e. different tasks, and would otherwise scribble
// over each other.
static uint8_t *s_jpeg_stream = nullptr;
static uint8_t *s_jpeg_shot = nullptr;

static SemaphoreHandle_t s_status_lock = nullptr;
static WebStatus s_status{};

// --- event capture --------------------------------------------------------
// Staged raw frame, its encoded form, and the job description. The capture loop
// only ever does the memcpy into s_event_raw; the encode and the SD write both
// happen on the writer task, because an SD write can block for tens of
// milliseconds and this is the one moment - a drowsiness alert firing - when the
// detection loop must not stall.
static uint8_t *s_event_raw = nullptr;
static uint8_t *s_jpeg_event = nullptr;

struct EventJob {
    int w, h;
    size_t raw_len;
    float risk, perclos;
    uint32_t uptime_ms;
    char reason[16];
};
static QueueHandle_t s_event_q = nullptr;
// Guards s_event_raw between the capture loop staging a frame and the writer task
// finishing with it. One in flight at a time; alerts are 30 s apart.
static std::atomic<bool> s_event_busy{false};
static std::atomic<uint32_t> s_events_stored{0};
static std::atomic<uint32_t> s_events_dropped{0};

// Touched from three tasks (the capture loop, the control server and the stream
// server), so these are atomics rather than volatile ints.
static std::atomic<int> s_viewers{0};
// A /frame client holds no socket between frames, so it cannot be counted the way
// an MJPEG stream is. Treat a recent request as a viewer instead: it is what makes
// the page's "is the device actually streaming to me" check work for both paths.
static std::atomic<int64_t> s_last_frame_req_us{0};
#define FRAME_VIEWER_WINDOW_US (2 * 1000 * 1000)

// Runtime-tunable, from the page. Quality trades bandwidth for eyelid detail; fps
// trades bandwidth for smoothness. Both matter on a phone in a car.
static std::atomic<int> s_quality{WEB_JPEG_QUALITY_DEFAULT};
static std::atomic<int> s_stream_fps{WEB_STREAM_FPS_DEFAULT};

// ---------------------------------------------------------------------------
// JPEG encoding into a fixed buffer
// ---------------------------------------------------------------------------
struct JpegSink {
    uint8_t *buf;
    size_t cap;
    size_t len;
    bool overflow;
};

static size_t jpeg_sink_write(void *arg, size_t index, const void *data, size_t len) {
    auto *sink = static_cast<JpegSink *>(arg);
    if (data == nullptr) return 0;   // encoder signalling end of image
    if (sink->len + len > sink->cap) {
        sink->overflow = true;
        // Still report the bytes as consumed: the encoder uses the running index
        // for its own bookkeeping, and lying about it corrupts the rest of the pass.
        return len;
    }
    memcpy(sink->buf + sink->len, data, len);
    sink->len += len;
    return len;
}

// Claims the newest snapshot for encoding. Returns the buffer index, or -1 when no
// frame has been published yet.
static int frame_acquire(const uint8_t **data, size_t *len, int *w, int *h) {
    if (s_frame_lock == nullptr) return -1;
    int idx = -1;
    xSemaphoreTake(s_frame_lock, portMAX_DELAY);
    if (s_ready >= 0) {
        idx = s_ready;
        ++s_hold[idx];
        *data = s_snap[idx];
        *len = s_snap_len;
        *w = s_snap_w;
        *h = s_snap_h;
    }
    xSemaphoreGive(s_frame_lock);
    return idx;
}

static void frame_release(int idx) {
    if (idx < 0 || s_frame_lock == nullptr) return;
    xSemaphoreTake(s_frame_lock, portMAX_DELAY);
    if (s_hold[idx] > 0) --s_hold[idx];
    xSemaphoreGive(s_frame_lock);
}

static uint32_t frame_seq() {
    if (s_frame_lock == nullptr) return 0;
    xSemaphoreTake(s_frame_lock, portMAX_DELAY);
    const uint32_t seq = s_seq;
    xSemaphoreGive(s_frame_lock);
    return seq;
}

// Asks the capture loop for one fresh frame and waits for it. Returns false if
// none arrived - which means the capture loop is stalled, not that the client is
// slow, so callers should surface it rather than retry forever.
static bool request_frame(uint32_t timeout_ms) {
    const uint32_t before = frame_seq();
    s_want_frame.store(true);
    const int64_t deadline = esp_timer_get_time() + static_cast<int64_t>(timeout_ms) * 1000;
    while (frame_seq() == before) {
        if (esp_timer_get_time() > deadline) {
            s_want_frame.store(false);
            return false;
        }
        // The semaphore is the fast path; the short timeout is the backstop for a
        // give that landed before this wait began.
        xSemaphoreTake(s_frame_signal, pdMS_TO_TICKS(20));
    }
    return true;
}

// Encodes the newest snapshot into `out`. Returns the encoded length, or 0.
static size_t encode_latest(uint8_t *out, size_t out_cap) {
    const uint8_t *src = nullptr;
    size_t src_len = 0;
    int w = 0, h = 0;
    const int idx = frame_acquire(&src, &src_len, &w, &h);
    if (idx < 0) return 0;

    JpegSink sink{out, out_cap, 0, false};
    const bool ok = fmt2jpg_cb(const_cast<uint8_t *>(src), src_len,
                               static_cast<uint16_t>(w), static_cast<uint16_t>(h),
                               PIXFORMAT_RGB565, static_cast<uint8_t>(s_quality.load()),
                               jpeg_sink_write, &sink);
    frame_release(idx);

    if (!ok || sink.overflow || sink.len == 0) {
        if (sink.overflow) {
            ESP_LOGW(TAG, "jpeg overflowed %u B at quality %d; lower it",
                     static_cast<unsigned>(out_cap), s_quality.load());
        }
        return 0;
    }
    return sink.len;
}

bool web_server_publish_frame(const uint8_t *rgb565, int width, int height, size_t len) {
    if (s_snap[0] == nullptr || rgb565 == nullptr) return false;
    // Nobody asked for a frame, so do not make one. exchange() rather than a plain
    // load: the request is consumed here, so one request yields exactly one copy
    // and the capture loop returns to full speed until the next one.
    if (!s_want_frame.exchange(false)) return false;
    if (len > s_snap_bytes) return false;

    int idx = -1;
    xSemaphoreTake(s_frame_lock, portMAX_DELAY);
    for (int i = 0; i < 2; ++i) {
        if (i != s_ready && s_hold[i] == 0) { idx = i; break; }
    }
    xSemaphoreGive(s_frame_lock);
    if (idx < 0) return false;   // encoder is behind; drop this frame, not the pipeline

    memcpy(s_snap[idx], rgb565, len);

    xSemaphoreTake(s_frame_lock, portMAX_DELAY);
    s_ready = idx;
    s_snap_len = len;
    s_snap_w = width;
    s_snap_h = height;
    ++s_seq;
    xSemaphoreGive(s_frame_lock);
    // Wake whoever asked. A binary semaphore is enough: there is one consumer at a
    // time, and a give that nobody is waiting on is harmless.
    if (s_frame_signal != nullptr) xSemaphoreGive(s_frame_signal);
    return true;
}

void web_server_publish_status(const WebStatus &status) {
    if (s_status_lock == nullptr) return;
    xSemaphoreTake(s_status_lock, portMAX_DELAY);
    s_status = status;
    xSemaphoreGive(s_status_lock);
}

bool web_server_has_viewer() {
    if (s_viewers.load() > 0) return true;
    const int64_t last = s_last_frame_req_us.load();
    return last != 0 && (esp_timer_get_time() - last) < FRAME_VIEWER_WINDOW_US;
}

bool web_server_capture_event(const uint8_t *rgb565, int width, int height, size_t len,
                              float risk, float perclos, const char *reason,
                              uint32_t uptime_ms) {
    if (!board_sdcard_mounted() || s_event_raw == nullptr || rgb565 == nullptr) return false;
    if (len == 0 || len > s_snap_bytes) return false;
    if (s_event_busy.exchange(true)) {
        // The previous capture is still being written. Dropping is the right
        // answer: the alert has already sounded, and blocking here to save a
        // second JPEG would cost frames at the worst possible moment.
        s_events_dropped.fetch_add(1);
        return false;
    }

    memcpy(s_event_raw, rgb565, len);

    EventJob job{};
    job.w = width;
    job.h = height;
    job.raw_len = len;
    job.risk = risk;
    job.perclos = perclos;
    job.uptime_ms = uptime_ms;
    snprintf(job.reason, sizeof(job.reason), "%s", reason != nullptr ? reason : "drowsy");

    if (xQueueSend(s_event_q, &job, 0) != pdTRUE) {
        s_event_busy.store(false);
        s_events_dropped.fetch_add(1);
        return false;
    }
    return true;
}

static void event_writer_task(void *) {
    EventJob job{};
    for (;;) {
        if (xQueueReceive(s_event_q, &job, portMAX_DELAY) != pdTRUE) continue;

        JpegSink sink{s_jpeg_event, WEB_JPEG_BUFFER_BYTES, 0, false};
        const bool encoded = fmt2jpg_cb(s_event_raw, job.raw_len,
                                        static_cast<uint16_t>(job.w),
                                        static_cast<uint16_t>(job.h),
                                        PIXFORMAT_RGB565,
                                        static_cast<uint8_t>(s_quality.load()),
                                        jpeg_sink_write, &sink);
        bool stored = false;
        if (encoded && !sink.overflow && sink.len > 0) {
            stored = board_sdcard_store_event(s_jpeg_event, sink.len, job.risk,
                                              job.perclos, job.reason, job.uptime_ms);
        }
        if (stored) {
            s_events_stored.fetch_add(1);
        } else {
            s_events_dropped.fetch_add(1);
            ESP_LOGW(TAG, "event capture failed (encoded %d, %u B)",
                     encoded ? 1 : 0, static_cast<unsigned>(sink.len));
        }
        s_event_busy.store(false);
    }
}

// printf("%f") on a NaN emits "nan", which is not valid JSON - and JSON.parse()
// throwing takes the entire page down, not just the one field. A stray NaN out of
// the behaviour maths is unlikely but not impossible, and a blank dashboard is a
// far worse failure than a zero. Every float in the status object goes through here.
static float json_float(float v) { return std::isfinite(v) ? v : 0.0f; }

// ---------------------------------------------------------------------------
// handlers
// ---------------------------------------------------------------------------
static esp_err_t index_handler(httpd_req_t *req) {
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Content-Encoding", "identity");
    const size_t len = index_html_end - index_html_start - 1;   // drop the NUL
    return httpd_resp_send(req, reinterpret_cast<const char *>(index_html_start), len);
}

// Browsers request this unprompted; answering with 204 is cheaper than letting it
// 404 and cheaper still than shipping an icon.
static esp_err_t favicon_handler(httpd_req_t *req) {
    httpd_resp_set_status(req, "204 No Content");
    return httpd_resp_send(req, nullptr, 0);
}

static esp_err_t status_handler(httpd_req_t *req) {
    WebStatus st{};
    xSemaphoreTake(s_status_lock, portMAX_DELAY);
    st = s_status;
    xSemaphoreGive(s_status_lock);

    WifiStatus net{};
    board_wifi_status(&net);
    SdCardInfo card{};
    board_sdcard_info(&card);

    // Hand-rolled rather than cJSON: one allocation-free snprintf is easier to
    // reason about in a 5 Hz polling path than a tree of nodes, and the shape of
    // this object is fixed by the page that consumes it.
    // static, not on the stack: the control server has a 6 KB task stack and this
    // object measures ~1.1 kB in practice and 1754 B with every field at its widest.
    // Only one task ever serves port 80, so there is nothing to race with.
    static char buf[2816];
    const int n = snprintf(buf, sizeof(buf),
        "{"
        "\"uptime_ms\":%llu,"
        "\"frames\":%lu,"
        "\"fps\":%.1f,"
        "\"camera\":%s,"
        "\"models\":%s,"
        "\"eye_model\":%s,"
        "\"frame\":{\"w\":%d,\"h\":%d},"
        "\"face\":{\"found\":%s,\"held\":%s,\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"score\":%.2f},"
        "\"risk\":{\"score\":%.3f,\"trigger\":%.3f,\"streak\":%d,\"required\":%d},"
        "\"eyes\":{\"closed\":%.3f,\"perclos\":%.3f,\"closure_s\":%.2f},"
        "\"cues\":{\"mouth_open\":%s,\"head_down\":%s,\"suppressed\":%s,"
                  "\"baselines_ready\":%s,\"events\":%u},"
        "\"rates\":{\"blink\":%.1f,\"long_blink\":%.1f,\"yawn\":%.1f,\"nod\":%.1f,"
                   "\"sneeze\":%u},"
        "\"geom\":{\"valid\":%s,\"roll\":%.1f,\"jaw_drop\":%.3f,\"nose_frac\":%.3f,"
                  "\"eye_dist\":%.1f},"
        "\"alert\":{\"active\":%s,\"text\":\"%s\",\"reason\":\"%s\",\"count\":%lu,"
                   "\"muted\":%s,\"lang\":\"%s\",\"lang_stored\":%s,"
                   "\"clips\":{\"drowsy\":\"%s\",\"microsleep\":\"%s\","
                              "\"yawning\":\"%s\",\"head_nod\":\"%s\"}},"
        "\"stream\":{\"viewers\":%d,\"quality\":%d,\"fps\":%d,\"port\":%d},"
        "\"net\":{\"ssid\":\"%s\",\"ip\":\"%s\",\"clients\":%d,\"sta\":%s,"
                 "\"sta_ip\":\"%s\",\"rssi\":%d},"
        "\"image\":{\"luma\":%.0f,\"min\":%d,\"max\":%d,\"peak\":%d},"
        "\"mem\":{\"heap\":%u,\"psram\":%u},"
        "\"card\":{\"mounted\":%s,\"events\":%d,\"free_mb\":%llu,\"stored\":%lu}"
        "}",
        static_cast<unsigned long long>(esp_timer_get_time() / 1000),
        static_cast<unsigned long>(st.frames), json_float(st.fps),
        st.camera_ok ? "true" : "false",
        st.models_ok ? "true" : "false",
        st.eye_model_ok ? "true" : "false",
        st.frame_w, st.frame_h,
        st.face_found ? "true" : "false", st.face_held ? "true" : "false",
        st.face_x, st.face_y, st.face_w, st.face_h, json_float(st.face_score),
        json_float(st.state.score), json_float(st.trigger), st.streak, st.required,
        json_float(st.state.eye_closed), json_float(st.state.perclos), json_float(st.state.closure_s),
        st.state.mouth_open ? "true" : "false",
        st.state.head_down ? "true" : "false",
        st.state.suppressed ? "true" : "false",
        st.state.baselines_ready ? "true" : "false",
        static_cast<unsigned>(st.state.events),
        json_float(st.state.blink_rate), json_float(st.state.long_blink_rate), json_float(st.state.yawn_rate),
        json_float(st.state.nod_rate), static_cast<unsigned>(st.state.sneeze_count),
        st.geom.valid ? "true" : "false", json_float(st.geom.roll), json_float(st.geom.jaw_drop),
        json_float(st.geom.nose_frac), json_float(st.geom.eye_dist),
        st.alerting ? "true" : "false",
        st.alert_text != nullptr ? st.alert_text : "",
        st.alert_reason != nullptr ? st.alert_reason : "",
        static_cast<unsigned long>(st.alert_count),
        voice_alert_muted() ? "true" : "false",
        voice_alert_language_code(),
        voice_alert_language_persisted() ? "true" : "false",
        voice_clip_source_name(voice_clip_probe(voice_alert_language_code(), "drowsy")),
        voice_clip_source_name(voice_clip_probe(voice_alert_language_code(), "microsleep")),
        voice_clip_source_name(voice_clip_probe(voice_alert_language_code(), "yawning")),
        voice_clip_source_name(voice_clip_probe(voice_alert_language_code(), "head_nod")),
        web_server_has_viewer() ? (s_viewers.load() > 0 ? s_viewers.load() : 1) : 0,
        s_quality.load(), s_stream_fps.load(), WEB_PORT_STREAM,
        net.ap_ssid, net.ap_ip, net.ap_clients,
        net.sta_connected ? "true" : "false", net.sta_ip,
        static_cast<int>(net.sta_rssi),
        json_float(st.luma), st.luma_min, st.luma_max, st.luma_peak,
        static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
        static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
        card.mounted ? "true" : "false", card.events,
        static_cast<unsigned long long>(card.free_bytes >> 20),
        static_cast<unsigned long>(s_events_stored.load()));

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    if (n < 0 || static_cast<size_t>(n) >= sizeof(buf)) {
        // Truncated JSON is worse than none: the page would parse half an object.
        ESP_LOGE(TAG, "status json truncated (%d B)", n);
        httpd_resp_set_status(req, "500 Internal Server Error");
        return httpd_resp_sendstr(req, "{\"error\":\"status truncated\"}");
    }
    return httpd_resp_send(req, buf, n);
}

// One still frame. This is also the fallback the page uses when the single MJPEG
// slot is already taken by another viewer.
static esp_err_t snapshot_handler(httpd_req_t *req) {
    // Ask for a frame rather than hoping one is lying around: with demand-driven
    // publishing there is no standing supply, so a still request has to place its
    // own order. 1 s is generous - the capture loop runs at 15-20 fps.
    s_viewers.fetch_add(1);
    size_t len = 0;
    if (request_frame(1000)) len = encode_latest(s_jpeg_shot, WEB_JPEG_BUFFER_BYTES);
    s_viewers.fetch_sub(1);

    if (len == 0) {
        httpd_resp_set_status(req, "503 Service Unavailable");
        httpd_resp_set_type(req, "text/plain");
        return httpd_resp_sendstr(req, "no frame available");
    }
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=drowsyguard.jpg");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, reinterpret_cast<const char *>(s_jpeg_shot), len);
}

static bool query_int(httpd_req_t *req, const char *key, int *out) {
    char query[128];
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK) return false;
    char value[16];
    if (httpd_query_key_value(query, key, value, sizeof(value)) != ESP_OK) return false;
    *out = atoi(value);
    return true;
}

static bool query_str(httpd_req_t *req, const char *key, char *out, size_t out_len) {
    char query[128];
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK) return false;
    return httpd_query_key_value(query, key, out, out_len) == ESP_OK;
}

static int clamp_int(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

static esp_err_t settings_handler(httpd_req_t *req) {
    int v = 0;
    if (query_int(req, "quality", &v)) {
        // Below ~10 the eyelids are indistinguishable from JPEG ringing, which
        // defeats the purpose of looking at the preview at all.
        s_quality = clamp_int(v, 10, 95);
    }
    if (query_int(req, "fps", &v)) {
        // Capped at the detection loop's own rate: asking for more only burns CPU
        // re-encoding frames the camera has not replaced yet.
        s_stream_fps = clamp_int(v, 1, 20);
    }
    if (query_int(req, "muted", &v)) voice_alert_set_muted(v != 0);

    char lang[8];
    if (query_str(req, "lang", lang, sizeof(lang))) {
        if (!voice_alert_set_language_code(lang)) {
            httpd_resp_set_status(req, "400 Bad Request");
            httpd_resp_set_type(req, "application/json");
            return httpd_resp_sendstr(req, "{\"error\":\"lang must be en or km\"}");
        }
    }

    char buf[192];
    const int n = snprintf(buf, sizeof(buf),
                           "{\"quality\":%d,\"fps\":%d,\"muted\":%s,\"lang\":\"%s\"}",
                           s_quality.load(), s_stream_fps.load(),
                           voice_alert_muted() ? "true" : "false",
                           voice_alert_language_code());
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, buf, n);
}

// Speaker check. Without a screen, this is the only way to tell "no alert fired"
// from "the amplifier is dead", and it is the first thing to try after wiring.
static esp_err_t alert_test_handler(httpd_req_t *req) {
    int reason = 0;
    query_int(req, "reason", &reason);
    const AlertReason r = static_cast<AlertReason>(clamp_int(reason, 0, 3));
    const bool played = voice_alert_test(r);
    // Report the source as well as the fact: with no display, "it spoke Khmer off
    // the card" and "it fell back to the embedded English" sound identical to
    // anyone who does not speak one of the two.
    const char *src = voice_clip_source_name(
        voice_clip_probe(voice_alert_language_code(), voice_alert_clip_name(r)));
    char buf[160];
    const int n = snprintf(buf, sizeof(buf),
                           "{\"played\":%s,\"text\":\"%s\",\"reason\":\"%s\","
                           "\"lang\":\"%s\",\"source\":\"%s\"}",
                           played ? "true" : "false", voice_alert_banner_text(r),
                           voice_alert_clip_name(r), voice_alert_language_code(), src);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, buf, n);
}

// Newest-first page of the history index. `?skip=` pages backwards through it.
static esp_err_t events_handler(httpd_req_t *req) {
    int skip = 0;
    query_int(req, "skip", &skip);
    if (skip < 0) skip = 0;
    int limit = 24;
    query_int(req, "limit", &limit);
    limit = clamp_int(limit, 1, 48);

    SdCardInfo card{};
    board_sdcard_info(&card);

    // One page at a time, on the stack of the control server task: 48 entries is
    // about 3 kB, and the whole index could be a thousand.
    static SdEvent page[48];
    const int n = board_sdcard_list_events(page, limit, skip);

    // Sized for the header plus 48 entries at ~130 B each.
    static char buf[7168];
    int at = snprintf(buf, sizeof(buf),
        "{\"card\":{\"mounted\":%s,\"name\":\"%s\",\"total\":%llu,\"free\":%llu,"
        "\"error\":\"%s\"},\"total\":%d,\"skip\":%d,\"stored\":%lu,\"dropped\":%lu,"
        "\"events\":[",
        card.mounted ? "true" : "false", card.name,
        static_cast<unsigned long long>(card.total_bytes),
        static_cast<unsigned long long>(card.free_bytes), card.error,
        board_sdcard_event_count(), skip,
        static_cast<unsigned long>(s_events_stored.load()),
        static_cast<unsigned long>(s_events_dropped.load()));

    for (int i = 0; i < n && at > 0 && at < static_cast<int>(sizeof(buf)) - 160; ++i) {
        at += snprintf(buf + at, sizeof(buf) - at,
                       "%s{\"id\":\"%s\",\"uptime_ms\":%lu,\"size\":%lu,"
                       "\"risk\":%.3f,\"perclos\":%.3f,\"reason\":\"%s\"}",
                       i ? "," : "", page[i].name,
                       static_cast<unsigned long>(page[i].uptime_ms),
                       static_cast<unsigned long>(page[i].size),
                       json_float(page[i].risk), json_float(page[i].perclos),
                       page[i].reason);
    }
    at += snprintf(buf + at, sizeof(buf) - at, "]}");

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, buf, at);
}

// One stored frame. The id goes through board_sdcard_event_path(), which accepts
// only digits - so a traversal attempt cannot be expressed, never mind escaped.
static esp_err_t event_image_handler(httpd_req_t *req) {
    char query[64];
    char id[16] = {0};
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "id", id, sizeof(id)) != ESP_OK) {
        httpd_resp_set_status(req, "400 Bad Request");
        return httpd_resp_sendstr(req, "id required");
    }

    char path[96];
    if (!board_sdcard_event_path(id, path, sizeof(path))) {
        httpd_resp_set_status(req, "404 Not Found");
        return httpd_resp_sendstr(req, "no such event");
    }
    FILE *f = fopen(path, "rb");
    if (f == nullptr) {
        httpd_resp_set_status(req, "404 Not Found");
        return httpd_resp_sendstr(req, "no such event");
    }

    httpd_resp_set_type(req, "image/jpeg");
    // Stored frames never change, so let the browser keep them: the history page
    // shows the same thumbnails on every poll.
    httpd_resp_set_hdr(req, "Cache-Control", "max-age=86400, immutable");

    // Streamed in chunks off the card rather than read whole: the control task has
    // a 6 kB stack and no business holding a whole JPEG.
    char chunk[1024];
    esp_err_t err = ESP_OK;
    for (;;) {
        const size_t got = fread(chunk, 1, sizeof(chunk), f);
        if (got == 0) break;
        err = httpd_resp_send_chunk(req, chunk, got);
        if (err != ESP_OK) break;
    }
    fclose(f);
    if (err == ESP_OK) httpd_resp_send_chunk(req, nullptr, 0);
    return err;
}

static esp_err_t events_clear_handler(httpd_req_t *req) {
    const bool ok = board_sdcard_clear_events();
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, ok ? "{\"cleared\":true}" : "{\"cleared\":false}");
}

// One JPEG per request. Lives on the stream port rather than the control port so
// that a burst of these cannot delay /api/status - encoding is the most expensive
// thing on the board after inference.
static esp_err_t frame_handler(httpd_req_t *req) {
    s_last_frame_req_us = esp_timer_get_time();

    size_t len = 0;
    if (request_frame(1000)) len = encode_latest(s_jpeg_stream, WEB_JPEG_BUFFER_BYTES);
    if (len == 0) {
        httpd_resp_set_status(req, "503 Service Unavailable");
        httpd_resp_set_type(req, "text/plain");
        return httpd_resp_sendstr(req, "no frame");
    }

    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, reinterpret_cast<const char *>(s_jpeg_stream), len);
}

static esp_err_t stream_handler(httpd_req_t *req) {
    if (esp_err_t err = httpd_resp_set_type(req, STREAM_CONTENT_TYPE); err != ESP_OK) {
        return err;
    }
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");

    s_viewers.fetch_add(1);
    const int viewers = s_viewers.load();
    ESP_LOGI(TAG, "stream opened (%d viewer%s)", viewers, viewers == 1 ? "" : "s");

    char part[64];
    esp_err_t err = ESP_OK;
    int64_t next_us = esp_timer_get_time();
    int sent = 0, skipped = 0;

    for (;;) {
        // Pace to the requested rate rather than encoding flat out: the encoder is
        // the most expensive thing on the board after inference, and a phone screen
        // gains nothing from frames the radio cannot carry.
        //
        // The rate floor is measured from the last frame actually SENT, which is
        // the fix for a stutter that was very visible on a phone. The old loop
        // advanced the deadline before checking whether a new frame existed, so
        // whenever the capture loop was fractionally slower than the stream rate -
        // 10 fps of production against a 12 fps request, which is exactly what a
        // viewer's own encode load causes - it burned a whole extra period waiting.
        // Frames then left in irregular bursts and the preview read as blinking.
        const int fps = s_stream_fps.load() > 0 ? s_stream_fps.load() : 1;
        const int64_t period_us = 1000000 / fps;
        const int64_t now_us = esp_timer_get_time();
        if (now_us < next_us) {
            vTaskDelay(pdMS_TO_TICKS((next_us - now_us) / 1000 + 1));
            continue;
        }

        // Order a frame and wait for it, rather than polling for one that may
        // never have been made.
        if (!request_frame(1000)) {
            ++skipped;
            continue;
        }

        const size_t len = encode_latest(s_jpeg_stream, WEB_JPEG_BUFFER_BYTES);
        if (len == 0) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        const int plen = snprintf(part, sizeof(part), STREAM_PART, static_cast<unsigned>(len));
        err = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
        if (err == ESP_OK) err = httpd_resp_send_chunk(req, part, plen);
        if (err == ESP_OK) {
            err = httpd_resp_send_chunk(req, reinterpret_cast<const char *>(s_jpeg_stream),
                                        len);
        }
        if (err != ESP_OK) break;   // the browser closed the tab; this is the normal exit
        ++sent;
        next_us = esp_timer_get_time() + period_us;
    }

    s_viewers.fetch_sub(1);
    const int left = s_viewers.load();
    ESP_LOGI(TAG, "stream closed after %d frames (%d idle waits), %d viewer%s left",
             sent, skipped, left, left == 1 ? "" : "s");
    // Return the failure rather than ESP_OK: the chunked response is already
    // broken, and only an error return makes httpd close the socket instead of
    // keeping a half-written response alive.
    return err;
}

// ---------------------------------------------------------------------------
// start-up
// ---------------------------------------------------------------------------
static bool alloc_buffers() {
    s_snap_bytes = static_cast<size_t>(CAM_FRAME_W) * CAM_FRAME_H * 2;
    for (int i = 0; i < 2; ++i) {
        s_snap[i] = static_cast<uint8_t *>(heap_caps_malloc(s_snap_bytes, MALLOC_CAP_SPIRAM));
        if (s_snap[i] == nullptr) {
            ESP_LOGE(TAG, "snapshot buffer %d alloc failed (%u B); is PSRAM enabled?",
                     i, static_cast<unsigned>(s_snap_bytes));
            return false;
        }
    }
    s_jpeg_stream = static_cast<uint8_t *>(
        heap_caps_malloc(WEB_JPEG_BUFFER_BYTES, MALLOC_CAP_SPIRAM));
    s_jpeg_shot = static_cast<uint8_t *>(
        heap_caps_malloc(WEB_JPEG_BUFFER_BYTES, MALLOC_CAP_SPIRAM));
    if (s_jpeg_stream == nullptr || s_jpeg_shot == nullptr) {
        ESP_LOGE(TAG, "jpeg buffer alloc failed");
        return false;
    }

    // Event capture buffers are only worth their PSRAM if there is somewhere to
    // put the result, so they follow the card.
    if (board_sdcard_mounted()) {
        s_event_raw = static_cast<uint8_t *>(heap_caps_malloc(s_snap_bytes, MALLOC_CAP_SPIRAM));
        s_jpeg_event = static_cast<uint8_t *>(
            heap_caps_malloc(WEB_JPEG_BUFFER_BYTES, MALLOC_CAP_SPIRAM));
        if (s_event_raw == nullptr || s_jpeg_event == nullptr) {
            ESP_LOGW(TAG, "event capture buffers unavailable; history disabled");
            s_event_raw = nullptr;
        }
    }
    return true;
}

bool web_server_start() {
    s_frame_lock = xSemaphoreCreateMutex();
    s_status_lock = xSemaphoreCreateMutex();
    s_frame_signal = xSemaphoreCreateBinary();
    if (s_frame_lock == nullptr || s_status_lock == nullptr || s_frame_signal == nullptr) {
        return false;
    }
    if (!alloc_buffers()) return false;

    // esp32-camera emits RGB565 most-significant byte first, which is what the JPEG
    // converter defaults to. Flip this if the preview comes out with red and blue
    // swapped - and note that CAM_RGB565_BYTE_SWAP is about the same disagreement.
    jpgSetRgb565BE(CAM_RGB565_BYTE_SWAP != 0);

    httpd_config_t control = HTTPD_DEFAULT_CONFIG();
    control.server_port = WEB_PORT_CONTROL;
    control.ctrl_port = 32768;
    control.max_uri_handlers = 12;
    control.lru_purge_enable = true;
    control.max_open_sockets = 5;
    // Formatting the status object costs a few hundred bytes of stack on its own
    // (soft-float %f is not cheap), on top of httpd's own frames.
    control.stack_size = 6144;
    // Pinned away from core 0, where app_main runs the capture loop and ESP-DL runs
    // inference. Serving a page must not cost the detector a frame.
    control.core_id = 1;

    if (httpd_start(&s_control, &control) != ESP_OK) {
        ESP_LOGE(TAG, "control server failed to start on port %d", WEB_PORT_CONTROL);
        return false;
    }

    const httpd_uri_t routes[] = {
        {.uri = "/",              .method = HTTP_GET,  .handler = index_handler,      .user_ctx = nullptr},
        {.uri = "/favicon.ico",   .method = HTTP_GET,  .handler = favicon_handler,    .user_ctx = nullptr},
        {.uri = "/api/status",    .method = HTTP_GET,  .handler = status_handler,     .user_ctx = nullptr},
        {.uri = "/api/snapshot",  .method = HTTP_GET,  .handler = snapshot_handler,   .user_ctx = nullptr},
        {.uri = "/api/settings",  .method = HTTP_GET,  .handler = settings_handler,   .user_ctx = nullptr},
        {.uri = "/api/settings",  .method = HTTP_POST, .handler = settings_handler,   .user_ctx = nullptr},
        {.uri = "/api/alert-test",.method = HTTP_POST, .handler = alert_test_handler, .user_ctx = nullptr},
        {.uri = "/api/events",    .method = HTTP_GET,  .handler = events_handler,     .user_ctx = nullptr},
        {.uri = "/api/event",     .method = HTTP_GET,  .handler = event_image_handler,.user_ctx = nullptr},
        {.uri = "/api/events/clear", .method = HTTP_POST, .handler = events_clear_handler, .user_ctx = nullptr},
    };
    for (const httpd_uri_t &r : routes) httpd_register_uri_handler(s_control, &r);

    httpd_config_t stream = HTTPD_DEFAULT_CONFIG();
    stream.server_port = WEB_PORT_STREAM;
    stream.ctrl_port = 32769;
    stream.max_uri_handlers = 2;
    stream.lru_purge_enable = true;
    // Four, not one: /frame is request-per-frame, so a couple of viewers plus the
    // odd stale socket all want a slot at once. lru_purge_enable reclaims the
    // stale ones rather than refusing the new connection.
    stream.max_open_sockets = 4;
    // jpge::jpeg_encoder is a ~1.1 kB stack object and convert_image() adds its
    // own frame on top of httpd's, so this is not left at the default.
    stream.stack_size = 6144;
    stream.send_wait_timeout = 3;
    stream.core_id = 1;

    if (httpd_start(&s_stream, &stream) != ESP_OK) {
        ESP_LOGE(TAG, "stream server failed to start on port %d", WEB_PORT_STREAM);
        return false;
    }
    const httpd_uri_t stream_routes[] = {
        {.uri = "/frame",  .method = HTTP_GET, .handler = frame_handler,  .user_ctx = nullptr},
        {.uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = nullptr},
    };
    for (const httpd_uri_t &r : stream_routes) httpd_register_uri_handler(s_stream, &r);

    if (s_event_raw != nullptr) {
        s_event_q = xQueueCreate(2, sizeof(EventJob));
        // Priority 3: below the alert task, which is the thing that actually has to
        // be prompt, and below the servers. Writing history is never urgent.
        if (s_event_q == nullptr ||
            xTaskCreatePinnedToCore(event_writer_task, "event_writer", 4096, nullptr, 3,
                                    nullptr, 1) != pdPASS) {
            ESP_LOGW(TAG, "event writer task failed to start; history disabled");
            s_event_raw = nullptr;
        } else {
            ESP_LOGI(TAG, "event capture ready (%d already on the card)",
                     board_sdcard_event_count());
        }
    }

    WifiStatus net{};
    board_wifi_status(&net);
    ESP_LOGI(TAG, "preview at http://%s/  (stream on port %d)", net.ap_ip, WEB_PORT_STREAM);
    return true;
}
