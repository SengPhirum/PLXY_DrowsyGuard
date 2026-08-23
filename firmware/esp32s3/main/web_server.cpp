#include "web_server.h"

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "board_camera.h"
#include "board_wifi.h"
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "img_converters.h"
#include "voice_alert.h"

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
static bool s_busy[2] = {false, false};
static uint32_t s_seq = 0;           // bumped on every publish, so the streamer
                                     // can tell a new frame from the same one twice
static SemaphoreHandle_t s_frame_lock = nullptr;

// One encode buffer per server: the stream task and a /api/snapshot request run on
// different httpd instances, i.e. different tasks, and would otherwise scribble
// over each other.
static uint8_t *s_jpeg_stream = nullptr;
static uint8_t *s_jpeg_shot = nullptr;

static SemaphoreHandle_t s_status_lock = nullptr;
static WebStatus s_status{};

// Touched from three tasks (the capture loop, the control server and the stream
// server), so these are atomics rather than volatile ints.
static std::atomic<int> s_viewers{0};

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
        s_busy[idx] = true;
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
    s_busy[idx] = false;
    xSemaphoreGive(s_frame_lock);
}

static uint32_t frame_seq() {
    if (s_frame_lock == nullptr) return 0;
    xSemaphoreTake(s_frame_lock, portMAX_DELAY);
    const uint32_t seq = s_seq;
    xSemaphoreGive(s_frame_lock);
    return seq;
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
    // Nobody is watching: skip the copy entirely. This is the common case in a
    // vehicle, where the alert path is the product and the preview is a diagnostic.
    if (s_viewers.load() <= 0) return false;
    if (len > s_snap_bytes) return false;

    int idx = -1;
    xSemaphoreTake(s_frame_lock, portMAX_DELAY);
    for (int i = 0; i < 2; ++i) {
        if (i != s_ready && !s_busy[i]) { idx = i; break; }
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
    return true;
}

void web_server_publish_status(const WebStatus &status) {
    if (s_status_lock == nullptr) return;
    xSemaphoreTake(s_status_lock, portMAX_DELAY);
    s_status = status;
    xSemaphoreGive(s_status_lock);
}

bool web_server_has_viewer() { return s_viewers.load() > 0; }

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

    // Hand-rolled rather than cJSON: one allocation-free snprintf is easier to
    // reason about in a 5 Hz polling path than a tree of nodes, and the shape of
    // this object is fixed by the page that consumes it.
    // static, not on the stack: the control server has a 6 KB task stack and this
    // object measures ~1.1 kB in practice and 1754 B with every field at its widest.
    // Only one task ever serves port 80, so there is nothing to race with.
    static char buf[2048];
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
                   "\"muted\":%s},"
        "\"stream\":{\"viewers\":%d,\"quality\":%d,\"fps\":%d,\"port\":%d},"
        "\"net\":{\"ssid\":\"%s\",\"ip\":\"%s\",\"clients\":%d,\"sta\":%s,"
                 "\"sta_ip\":\"%s\",\"rssi\":%d},"
        "\"mem\":{\"heap\":%u,\"psram\":%u}"
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
        s_viewers.load(), s_quality.load(), s_stream_fps.load(), WEB_PORT_STREAM,
        net.ap_ssid, net.ap_ip, net.ap_clients,
        net.sta_connected ? "true" : "false", net.sta_ip,
        static_cast<int>(net.sta_rssi),
        static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
        static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));

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
    // A viewer that only ever asks for stills would otherwise never see a frame:
    // publish_frame() skips the copy when the viewer count is zero. Register as a
    // viewer for the duration of the request and wait one frame period.
    s_viewers.fetch_add(1);
    size_t len = encode_latest(s_jpeg_shot, WEB_JPEG_BUFFER_BYTES);
    if (len == 0) {
        const uint32_t before = frame_seq();
        for (int i = 0; i < 20 && frame_seq() == before; ++i) vTaskDelay(pdMS_TO_TICKS(20));
        len = encode_latest(s_jpeg_shot, WEB_JPEG_BUFFER_BYTES);
    }
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

    char buf[128];
    const int n = snprintf(buf, sizeof(buf),
                           "{\"quality\":%d,\"fps\":%d,\"muted\":%s}",
                           s_quality.load(), s_stream_fps.load(),
                           voice_alert_muted() ? "true" : "false");
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
    char buf[96];
    const int n = snprintf(buf, sizeof(buf), "{\"played\":%s,\"text\":\"%s\"}",
                           played ? "true" : "false", voice_alert_banner_text(r));
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, buf, n);
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
    uint32_t last_seq = 0;
    int64_t next_us = esp_timer_get_time();
    int sent = 0, skipped = 0;

    for (;;) {
        // Pace to the requested rate rather than encoding flat out. The encoder is
        // the most expensive thing on the board after inference, and a phone screen
        // gains nothing from frames the radio cannot carry.
        const int fps = s_stream_fps.load() > 0 ? s_stream_fps.load() : 1;
        const int64_t period_us = 1000000 / fps;
        const int64_t now_us = esp_timer_get_time();
        if (now_us < next_us) {
            vTaskDelay(pdMS_TO_TICKS((next_us - now_us) / 1000 + 1));
            continue;
        }
        next_us = now_us + period_us;

        const uint32_t seq = frame_seq();
        if (seq == last_seq) {
            // The detection loop has not produced anything new. Re-sending the same
            // JPEG would waste both CPU and airtime.
            ++skipped;
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        last_seq = seq;

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
    return true;
}

bool web_server_start() {
    s_frame_lock = xSemaphoreCreateMutex();
    s_status_lock = xSemaphoreCreateMutex();
    if (s_frame_lock == nullptr || s_status_lock == nullptr) return false;
    if (!alloc_buffers()) return false;

    // esp32-camera emits RGB565 most-significant byte first, which is what the JPEG
    // converter defaults to. Flip this if the preview comes out with red and blue
    // swapped - and note that CAM_RGB565_BYTE_SWAP is about the same disagreement.
    jpgSetRgb565BE(CAM_RGB565_BYTE_SWAP != 0);

    httpd_config_t control = HTTPD_DEFAULT_CONFIG();
    control.server_port = WEB_PORT_CONTROL;
    control.ctrl_port = 32768;
    control.max_uri_handlers = 8;
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
    };
    for (const httpd_uri_t &r : routes) httpd_register_uri_handler(s_control, &r);

    httpd_config_t stream = HTTPD_DEFAULT_CONFIG();
    stream.server_port = WEB_PORT_STREAM;
    stream.ctrl_port = 32769;
    stream.max_uri_handlers = 1;
    stream.lru_purge_enable = true;
    // Two sockets, not one: the browser's next connection can be accepted (and the
    // stale one purged) instead of hanging when a tab is closed mid-stream.
    stream.max_open_sockets = 2;
    // jpge::jpeg_encoder is a ~1.1 kB stack object and convert_image() adds its
    // own frame on top of httpd's, so this is not left at the default.
    stream.stack_size = 6144;
    stream.send_wait_timeout = 3;
    stream.core_id = 1;

    if (httpd_start(&s_stream, &stream) != ESP_OK) {
        ESP_LOGE(TAG, "stream server failed to start on port %d", WEB_PORT_STREAM);
        return false;
    }
    const httpd_uri_t stream_uri = {
        .uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = nullptr};
    httpd_register_uri_handler(s_stream, &stream_uri);

    WifiStatus net{};
    board_wifi_status(&net);
    ESP_LOGI(TAG, "preview at http://%s/  (stream on port %d)", net.ap_ip, WEB_PORT_STREAM);
    return true;
}
