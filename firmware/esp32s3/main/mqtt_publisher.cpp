#include "mqtt_publisher.h"

#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mqtt_client.h"
#include "settings_nvs.h"

// The certificate bundle is what lets the preconfigured public broker work with
// nothing pasted in and verification still ON: broker.emqx.io presents a chain
// signed by a public root, and CONFIG_MBEDTLS_CERTIFICATE_BUNDLE puts those roots in
// flash. A device with its own private broker pastes that broker's CA instead, and
// this is then unused.
#include "esp_crt_bundle.h"

static const char *TAG = "mqtt";

/*
OWNERSHIP, because three tasks touch this file and getting it wrong here would be a
torn hostname rather than a compile error:

  s_cfg / s_id / s_ca      shared. Written by the HTTP task in mqtt_publisher_apply(),
                           read by the publisher task, both under s_cfg_lock.
  s_live / s_live_id       the publisher task's private copy, refreshed from the
                           shared pair whenever s_reconfigure is set. Every esp-mqtt
                           call reads only these, so a settings change mid-connection
                           cannot rewrite the topic a publish is halfway through.
  s_outbox                 shared, under s_lock. The capture loop takes it with a
                           ZERO tick timeout - see mqtt_publish_alert().
  the atomics              anything the event handler (esp-mqtt's own task) touches.
*/

static MqttConfig s_cfg{};
static DeviceIdentity s_id{};
static char *s_ca = nullptr;            // PEM, or nullptr; MQTT_CA_MAX + 1 bytes
static size_t s_ca_len = 0;

static MqttConfig s_live{};
static DeviceIdentity s_live_id{};

static esp_mqtt_client_handle_t s_client = nullptr;
static MqttOutbox s_outbox{};
static MqttDedup s_dedup{};
static SemaphoreHandle_t s_lock = nullptr;       // s_outbox
static SemaphoreHandle_t s_cfg_lock = nullptr;   // s_cfg / s_id / s_ca
static SemaphoreHandle_t s_error_lock = nullptr; // s_error
static TaskHandle_t s_task = nullptr;

static std::atomic<bool> s_connected{false};
static std::atomic<bool> s_reconfigure{false};
// Mirrors s_cfg.enabled so the capture loop can check it without a lock. A stale
// read is harmless in both directions: a spurious queue is drained and discarded,
// and a missed one is a single alert during the millisecond the setting changed.
static std::atomic<bool> s_enabled{false};
static std::atomic<uint32_t> s_connects{0};
static std::atomic<uint32_t> s_disconnects{0};
static std::atomic<uint32_t> s_published{0};
static std::atomic<uint32_t> s_acked{0};
static std::atomic<uint32_t> s_rejected{0};
static std::atomic<uint32_t> s_seq{1};
static std::atomic<uint32_t> s_attempt{0};
static std::atomic<uint32_t> s_next_retry_ms{0};
static std::atomic<uint32_t> s_last_publish_ms{0};
static std::atomic<int> s_state{static_cast<int>(MqttState::Disabled)};
static uint32_t s_boot_id = 0;
static char s_error[80] = {0};

const char *mqtt_state_name(MqttState s) {
    switch (s) {
        case MqttState::Disabled:   return "disabled";
        case MqttState::Idle:       return "idle";
        case MqttState::Connecting: return "connecting";
        case MqttState::Online:     return "online";
        case MqttState::Backoff:    return "backoff";
        case MqttState::Fault:      return "fault";
    }
    return "unknown";
}

// One place that writes the error string, so there is one place to audit for
// disclosure. Every caller passes a literal or an esp_err_to_name(); none passes a
// host, a topic or a credential, and none should - this string is rendered on a web
// page and printed to a serial log that ends up in bug reports.
static void set_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void set_error(const char *fmt, ...) {
    if (s_error_lock == nullptr) return;
    va_list ap;
    va_start(ap, fmt);
    xSemaphoreTake(s_error_lock, portMAX_DELAY);
    vsnprintf(s_error, sizeof(s_error), fmt, ap);
    xSemaphoreGive(s_error_lock);
    va_end(ap);
}

static void clear_error() { set_error("%s", ""); }

// Epoch milliseconds, or 0 when nothing has set the clock. There is no RTC on this
// board, so time() returns 1970 until an SNTP server is reached - or forever, on the
// access-point-only configuration. 0 is the honest answer: a fabricated timestamp in
// an incident record is worse than an absent one. The cut-off is 2020-01-01,
// comfortably past anything a bare newlib invents.
static int64_t epoch_ms_or_zero() {
    const time_t now = time(nullptr);
    return now > 1577836800 ? static_cast<int64_t>(now) * 1000 : 0;
}

// --- the producer ----------------------------------------------------------
bool mqtt_publish_alert(const char *alert, float risk, float perclos,
                        uint32_t alert_count, uint32_t uptime_ms) {
    if (s_lock == nullptr) return false;
    // "Is it configured", not "is it connected". An alert that arrives while the
    // broker is unreachable is precisely the alert offline buffering exists for, and
    // checking the connection here would throw away the only record of the tunnel
    // the driver got drowsy in.
    if (!s_enabled.load()) return false;

    MqttAlertEvent ev{};
    ev.seq = s_seq.fetch_add(1);
    // event_id is NOT built here. It needs the device_id, which lives behind
    // s_cfg_lock, and this function must never wait on a mutex the HTTP task can
    // hold. The publisher task fills it in from its own snapshot; (boot_id, seq) is
    // already unique, so nothing about the identity is lost by deferring it.
    settings_copy(ev.alert, sizeof(ev.alert), alert != nullptr ? alert : "drowsy");
    ev.risk = risk;
    ev.perclos = perclos;
    ev.alert_count = alert_count;
    ev.uptime_ms = uptime_ms;
    ev.epoch_ms = epoch_ms_or_zero();

    // ZERO ticks, and this is the line the whole isolation argument rests on. The
    // worst case is that the publisher task is mid-memcpy on the same mutex, in
    // which case this alert is counted as rejected and the capture loop carries on
    // without missing a frame. The critical section is one 96-byte struct copy, so
    // it has never been observed - but "never observed" is not "cannot block", and
    // the capture loop has a 23 ms frame budget.
    if (xSemaphoreTake(s_lock, 0) != pdTRUE) {
        s_rejected.fetch_add(1);
        return false;
    }
    const bool clean = s_outbox.push(ev);
    xSemaphoreGive(s_lock);
    // Wake the publisher; never wait for it.
    if (s_task != nullptr) xTaskNotifyGive(s_task);
    return clean;
}

bool mqtt_publisher_test() {
    if (!s_enabled.load()) return false;
    // Queued through exactly the same path as a real alert, on purpose: a test that
    // took a shortcut would only prove the shortcut works.
    return mqtt_publish_alert("test", 0.0f, 0.0f, 0,
                              static_cast<uint32_t>(esp_timer_get_time() / 1000));
}

// --- the client ------------------------------------------------------------
static void mqtt_event_handler(void *, esp_event_base_t, int32_t event_id, void *data) {
    auto *e = static_cast<esp_mqtt_event_handle_t>(data);
    switch (static_cast<esp_mqtt_event_id_t>(event_id)) {
        case MQTT_EVENT_CONNECTED:
            s_connected.store(true);
            s_connects.fetch_add(1);
            s_attempt.store(0);
            s_state.store(static_cast<int>(MqttState::Online));
            clear_error();
            ESP_LOGI(TAG, "broker connected (%s, qos %u)",
                     mqtt_transport_name(s_live.transport),
                     static_cast<unsigned>(s_live.qos));
            break;
        case MQTT_EVENT_DISCONNECTED:
            s_connected.store(false);
            s_disconnects.fetch_add(1);
            // Not an error in itself: a keepalive timeout in a moving vehicle is
            // expected. The publisher task decides what happens next.
            if (s_state.load() == static_cast<int>(MqttState::Online)) {
                s_state.store(static_cast<int>(MqttState::Backoff));
            }
            break;
        case MQTT_EVENT_PUBLISHED:
            // The QoS 1 PUBACK. Counted apart from `published` because the gap
            // between the two is the only on-device evidence that the broker is
            // accepting what it is being sent rather than dropping it.
            s_acked.fetch_add(1);
            break;
        case MQTT_EVENT_ERROR:
            if (e != nullptr && e->error_handle != nullptr) {
                const esp_mqtt_error_codes_t *er = e->error_handle;
                if (er->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
                    // Codes only. mbedtls error strings occasionally embed the peer
                    // name, and this string is rendered on a web page.
                    set_error("transport error (esp-tls 0x%x, socket errno %d)",
                              er->esp_tls_last_esp_err, er->esp_transport_sock_errno);
                } else if (er->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
                    set_error("broker refused the connection (return code %d)",
                              er->connect_return_code);
                } else {
                    set_error("mqtt error type %d", static_cast<int>(er->error_type));
                }
            } else {
                set_error("mqtt error");
            }
            ESP_LOGW(TAG, "%s", s_error);
            break;
        default:
            break;
    }
}

static void client_stop() {
    if (s_client == nullptr) return;
    // A graceful goodbye first, while there is still a connection: a DISCONNECT
    // tells the broker NOT to fire the will, which is the whole reason the status
    // document distinguishes "shutdown" from "last-will".
    if (s_connected.load() && s_live.lwt) {
        char topic[MQTT_TOPIC_MAX + 16];
        char body[MQTT_PAYLOAD_MAX];
        if (mqtt_topic(s_live, s_live_id, MqttTopicKind::Status, topic, sizeof(topic))) {
            const size_t n = mqtt_status_payload(
                s_live_id, false, "shutdown",
                static_cast<uint32_t>(esp_timer_get_time() / 1000), epoch_ms_or_zero(),
                s_published.load(), body, sizeof(body));
            if (n > 0) {
                esp_mqtt_client_publish(s_client, topic, body, static_cast<int>(n),
                                        s_live.qos, s_live.retain_status ? 1 : 0);
            }
        }
    }
    esp_mqtt_client_stop(s_client);
    esp_mqtt_client_destroy(s_client);
    s_client = nullptr;
    s_connected.store(false);
}

// Builds and starts the client for the snapshot currently in force. False means the
// client could not even be constructed, which the task retries on the same backoff:
// the cause is sometimes transient (no heap, no netif yet), and a device that needed
// a reboot to notice the network came up would be worse than one that keeps trying.
static bool client_start() {
    char uri[MQTT_HOST_MAX + MQTT_PATH_MAX + 24];
    if (!mqtt_uri(s_live, uri, sizeof(uri))) {
        set_error("invalid broker address");
        return false;
    }
    static char client_id[MQTT_CLIENT_ID_MAX + 16];
    if (!mqtt_client_id(s_live, s_live_id, client_id, sizeof(client_id))) {
        set_error("invalid client id");
        return false;
    }
    // static, and deliberately so: esp-mqtt copies the config strings at init on
    // current IDF, but the will body is the one field where a stack buffer would be
    // both large and easy to get wrong, and a will that points at freed memory is a
    // failure that only shows up when the device dies - which is exactly when the
    // will matters.
    static char lwt_topic[MQTT_TOPIC_MAX + 16];
    static char lwt_body[MQTT_PAYLOAD_MAX];
    lwt_topic[0] = '\0';
    lwt_body[0] = '\0';
    size_t lwt_len = 0;
    if (s_live.lwt && mqtt_topic(s_live, s_live_id, MqttTopicKind::Status, lwt_topic,
                                 sizeof(lwt_topic))) {
        // uptime 0 and no timestamp, which is correct rather than lazy: the will is
        // composed now and delivered by the broker at an unknown time in the future,
        // so any moment it claimed would be a lie. `reason` is what a dashboard
        // reads, and "last-will" is the interesting case - the device vanished
        // without saying goodbye.
        lwt_len = mqtt_status_payload(s_live_id, false, "last-will", 0, 0, 0, lwt_body,
                                      sizeof(lwt_body));
    }

    esp_mqtt_client_config_t mc = {};
    mc.broker.address.uri = uri;
    mc.credentials.client_id = client_id;
    if (s_live.username[0] != '\0') {
        mc.credentials.username = s_live.username;
        mc.credentials.authentication.password = s_live.password;
    }
    mc.session.keepalive = s_live.keepalive_s;
    // Ours, not the library's. See the header: a fixed 10 s retry against a broker
    // that is down for an hour is 360 connection attempts from a device whose actual
    // job is inference.
    mc.network.disable_auto_reconnect = true;
    mc.network.timeout_ms = 8000;
    mc.session.protocol_ver = s_live.protocol == MqttProtocol::V5
                                  ? MQTT_PROTOCOL_V_5 : MQTT_PROTOCOL_V_3_1_1;
    if (lwt_len > 0) {
        mc.session.last_will.topic = lwt_topic;
        mc.session.last_will.msg = lwt_body;
        mc.session.last_will.msg_len = static_cast<int>(lwt_len);
        mc.session.last_will.qos = s_live.qos;
        mc.session.last_will.retain = s_live.retain_status ? 1 : 0;
    }
    if (mqtt_transport_is_tls(s_live.transport)) {
        if (s_live.tls_insecure) {
            // Reachable on purpose, and shouted about everywhere it appears. The
            // alternative is an operator with a self-signed broker switching TLS off
            // altogether, which is strictly worse: this still encrypts, it just
            // cannot prove who it is talking to.
            mc.broker.verification.skip_cert_common_name_check = true;
            ESP_LOGW(TAG, "TLS server verification is DISABLED for this connection");
        } else if (s_ca != nullptr && s_ca_len > 0) {
            mc.broker.verification.certificate = s_ca;
            mc.broker.verification.certificate_len = s_ca_len + 1;  // mbedtls wants the NUL
        } else {
            mc.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;
        }
    }

    s_client = esp_mqtt_client_init(&mc);
    if (s_client == nullptr) {
        set_error("client init failed (out of memory?)");
        return false;
    }
    if (esp_mqtt_client_register_event(s_client, MQTT_EVENT_ANY, mqtt_event_handler,
                                       nullptr) != ESP_OK) {
        set_error("could not register the event handler");
        esp_mqtt_client_destroy(s_client);
        s_client = nullptr;
        return false;
    }
    const esp_err_t err = esp_mqtt_client_start(s_client);
    if (err != ESP_OK) {
        set_error("client start failed (%s)", esp_err_to_name(err));
        esp_mqtt_client_destroy(s_client);
        s_client = nullptr;
        return false;
    }
    // The URI is logged; the credentials are not. A masked username goes in instead -
    // enough to tell two accounts apart in a bug report, not enough to use.
    char user_mask[MQTT_USER_MAX + 1];
    mqtt_mask(s_live.username, user_mask, sizeof(user_mask));
    ESP_LOGI(TAG, "connecting to %s as \"%s\"%s%s", uri, client_id,
             user_mask[0] != '\0' ? " user " : "", user_mask);
    return true;
}

// The retained "I am here" document, published once per successful connection. Its
// counterpart is the will, which the broker publishes when this one stops arriving.
static void publish_online() {
    if (!s_live.lwt || s_client == nullptr) return;
    char topic[MQTT_TOPIC_MAX + 16];
    char body[MQTT_PAYLOAD_MAX];
    if (!mqtt_topic(s_live, s_live_id, MqttTopicKind::Status, topic, sizeof(topic))) return;
    const size_t n = mqtt_status_payload(
        s_live_id, true, "connected", static_cast<uint32_t>(esp_timer_get_time() / 1000),
        epoch_ms_or_zero(), s_published.load(), body, sizeof(body));
    if (n == 0) return;
    esp_mqtt_client_publish(s_client, topic, body, static_cast<int>(n), s_live.qos,
                            s_live.retain_status ? 1 : 0);
}

// Removes the head of the outbox. Split out because it happens on three paths and
// forgetting it on one of them would wedge the queue behind an event that can never
// be sent.
static void commit_head() {
    if (s_lock == nullptr) return;
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(50)) == pdTRUE) {
        s_outbox.commit();
        xSemaphoreGive(s_lock);
    }
}

// Publishes the head. True means it is dealt with - sent, or knowingly discarded -
// and the queue has moved on; false means keep it and try again.
static bool publish_head() {
    MqttAlertEvent ev{};
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(50)) != pdTRUE) return false;
    const bool have = s_outbox.peek(&ev);
    xSemaphoreGive(s_lock);
    if (!have) return false;

    // The identity half of the event id is filled in here rather than by the
    // producer - see mqtt_publish_alert().
    mqtt_event_id(s_live_id, s_boot_id, ev.seq, ev.event_id, sizeof(ev.event_id));

    // A duplicate is committed rather than published: it has already been sent, and
    // leaving it at the head would block everything behind it forever.
    if (s_dedup.seen_or_add(ev.event_id)) {
        ESP_LOGW(TAG, "event %s already published; dropping the duplicate", ev.event_id);
        commit_head();
        return true;
    }

    char topic[MQTT_TOPIC_MAX + 16];
    if (!mqtt_topic(s_live, s_live_id, MqttTopicKind::Alerts, topic, sizeof(topic))) {
        // Retried rather than dropped, unlike the payload failure below, and the
        // difference is whether a later configuration can fix it. A topic that cannot
        // be built means the identity is unusable, which a reconfigure repairs - so
        // the event waits. The queue stalls behind it at one attempt every 200 ms,
        // memory stays bounded, and the error is on the page; losing the alert would
        // be the worse trade. It should be unreachable: mqtt_publisher_apply()
        // validates the identity, and the boot default is derived from the MAC.
        set_error("could not build the alert topic - check the device and fleet ids");
        return false;
    }
    char body[MQTT_PAYLOAD_MAX];
    const size_t n = mqtt_alert_payload(s_live_id, ev, body, sizeof(body));
    if (n == 0) {
        // Not a network condition: the payload cannot be built at all, so retrying
        // forever would block the queue. Drop it loudly.
        ESP_LOGE(TAG, "alert payload would not fit; dropping event %s", ev.event_id);
        commit_head();
        return true;
    }

    // enqueue(), not publish(): it hands the message to esp-mqtt's own outbox and
    // returns, so the QoS 1 retransmission is the library's problem rather than a
    // blocking wait on this task. store = true keeps it there across a brief drop.
    const int msg = esp_mqtt_client_enqueue(s_client, topic, body, static_cast<int>(n),
                                            s_live.qos, 0, true);
    if (msg < 0) return false;      // client busy or full: keep the event, retry
    s_published.fetch_add(1);
    if (s_live.qos == 0) s_acked.fetch_add(1);   // there is no PUBACK to wait for
    s_last_publish_ms.store(static_cast<uint32_t>(esp_timer_get_time() / 1000));
    commit_head();
    ESP_LOGI(TAG, "published %s (%s) to %s", ev.event_id, ev.alert, topic);
    return true;
}

// Refreshes the task's private snapshot. The only place s_cfg / s_id / s_ca are read
// by this task, so every esp-mqtt call below works from a copy that cannot change
// underneath it.
static void take_snapshot() {
    if (s_cfg_lock == nullptr) {
        s_live = s_cfg;
        s_live_id = s_id;
        return;
    }
    xSemaphoreTake(s_cfg_lock, portMAX_DELAY);
    s_live = s_cfg;
    s_live_id = s_id;
    xSemaphoreGive(s_cfg_lock);
}

static int outbox_depth() {
    if (s_lock == nullptr) return 0;
    int d = 0;
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(50)) == pdTRUE) {
        d = s_outbox.depth();
        xSemaphoreGive(s_lock);
    }
    return d;
}

// --- the task --------------------------------------------------------------
static void publisher_task(void *) {
    int64_t retry_at_us = 0;
    bool was_online = false;
    take_snapshot();

    for (;;) {
        // A new configuration tears the client down and rebuilds it. Done here
        // rather than in the HTTP handler so that every esp-mqtt call stays on one
        // task, which is what the library documents and what stops a reconfigure
        // racing a reconnect.
        if (s_reconfigure.exchange(false)) {
            client_stop();
            take_snapshot();
            s_attempt.store(0);
            retry_at_us = 0;
            was_online = false;
            s_dedup.reset();     // a new broker has seen none of our event ids
            s_state.store(static_cast<int>(s_live.enabled ? MqttState::Idle
                                                         : MqttState::Disabled));
        }

        if (!s_live.enabled) {
            if (s_client != nullptr) client_stop();
            s_state.store(static_cast<int>(MqttState::Disabled));
            s_next_retry_ms.store(0);
            // Woken by a notify when the settings change; the timeout is only a
            // backstop so a lost notify cannot wedge the task forever.
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(2000));
            continue;
        }

        const int64_t now_us = esp_timer_get_time();

        if (s_client == nullptr) {
            if (now_us < retry_at_us) {
                s_state.store(static_cast<int>(MqttState::Backoff));
                s_next_retry_ms.store(static_cast<uint32_t>((retry_at_us - now_us) / 1000));
                ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(250));
                continue;
            }
            s_next_retry_ms.store(0);
            s_state.store(static_cast<int>(MqttState::Connecting));
            if (!client_start()) {
                const uint32_t a = s_attempt.fetch_add(1) + 1;
                const uint32_t wait = mqtt_backoff_jittered_ms(a, esp_random());
                retry_at_us = esp_timer_get_time() + static_cast<int64_t>(wait) * 1000;
                s_state.store(static_cast<int>(MqttState::Fault));
                ESP_LOGW(TAG, "client not started (attempt %u); retrying in %u ms",
                         static_cast<unsigned>(a), static_cast<unsigned>(wait));
                continue;
            }
            // Give the handshake a moment before calling it a failure. 8 s covers a
            // TLS handshake on this radio with room to spare.
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(8000));
            if (!s_connected.load()) {
                client_stop();
                const uint32_t a = s_attempt.fetch_add(1) + 1;
                const uint32_t wait = mqtt_backoff_jittered_ms(a, esp_random());
                retry_at_us = esp_timer_get_time() + static_cast<int64_t>(wait) * 1000;
                s_state.store(static_cast<int>(MqttState::Backoff));
                s_next_retry_ms.store(wait);
                ESP_LOGW(TAG, "connect attempt %u failed; next in %u ms",
                         static_cast<unsigned>(a), static_cast<unsigned>(wait));
            }
            continue;
        }

        if (!s_connected.load()) {
            // The client exists but the session dropped. Tear it down rather than
            // leaving it: esp-mqtt's own reconnect is disabled, so a client with no
            // session is a client that will never come back on its own.
            client_stop();
            was_online = false;
            const uint32_t a = s_attempt.fetch_add(1) + 1;
            const uint32_t wait = mqtt_backoff_jittered_ms(a, esp_random());
            retry_at_us = esp_timer_get_time() + static_cast<int64_t>(wait) * 1000;
            s_state.store(static_cast<int>(MqttState::Backoff));
            s_next_retry_ms.store(wait);
            continue;
        }

        if (!was_online) {
            publish_online();
            was_online = true;
            const int depth = outbox_depth();
            if (depth > 0) {
                ESP_LOGI(TAG, "flushing %d buffered alert%s", depth,
                         depth == 1 ? "" : "s");
            }
        }

        // Drain the outbox one event per iteration, with a yield between. A flush of
        // sixteen after a long tunnel must not monopolise core 1, which is also
        // serving the web page.
        if (outbox_depth() > 0) {
            vTaskDelay(pdMS_TO_TICKS(publish_head() ? 10 : 200));
            continue;
        }

        // Nothing to send. Wake on the next alert, or on the 1 s backstop.
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
    }
}

// --- lifecycle -------------------------------------------------------------
// Caller holds s_cfg_lock, or is running before the task exists.
static void load_ca_locked() {
    if (s_ca == nullptr) {
        s_ca = static_cast<char *>(malloc(MQTT_CA_MAX + 1));
        if (s_ca == nullptr) {
            s_ca_len = 0;
            return;
        }
    }
    s_ca_len = settings_load_ca(s_ca, MQTT_CA_MAX + 1);
    if (s_ca_len == 0) s_ca[0] = '\0';
}

bool mqtt_publisher_start(const DeviceIdentity &id) {
    s_lock = xSemaphoreCreateMutex();
    s_cfg_lock = xSemaphoreCreateMutex();
    s_error_lock = xSemaphoreCreateMutex();
    if (s_lock == nullptr || s_cfg_lock == nullptr || s_error_lock == nullptr) {
        ESP_LOGE(TAG, "mutex allocation failed; mqtt alerting is off");
        return false;
    }
    s_outbox.reset();
    s_dedup.reset();
    // Separates this boot's event ids from the previous boot's: without it, event
    // 000001 after a mid-drive reset would be de-duplicated against event 000001
    // from before it and silently thrown away.
    s_boot_id = esp_random();

    settings_store_init();
    s_id = id;
    s_cfg = mqtt_config_defaults();
    if (!settings_load_mqtt(&s_cfg)) {
        ESP_LOGI(TAG, "no stored broker settings; mqtt alerting stays off until configured");
    }
    load_ca_locked();
    // A CA that vanished from NVS while the config still claims one would silently
    // fall back to the certificate bundle, which may not verify a private broker at
    // all. Keep the flag honest instead of letting the UI show a certificate that
    // is not there.
    if (s_cfg.ca_present && s_ca_len == 0) s_cfg.ca_present = false;
    s_enabled.store(s_cfg.enabled);
    s_state.store(static_cast<int>(s_cfg.enabled ? MqttState::Idle : MqttState::Disabled));

    // Priority 4, core 1. Above the SD-card event writer (3), which is never urgent;
    // below the alert task, which is the only thing on this device that genuinely is;
    // and on the other core from app_main's capture loop and ESP-DL inference.
    if (xTaskCreatePinnedToCore(publisher_task, "mqtt_pub", 5120, nullptr, 4, &s_task,
                                1) != pdPASS) {
        ESP_LOGE(TAG, "publisher task failed to start; mqtt alerting is off");
        s_task = nullptr;
        return false;
    }
    ESP_LOGI(TAG, "publisher ready (%s), outbox %d deep, qos %u",
             s_cfg.enabled ? "enabled" : "disabled", MQTT_OUTBOX_DEPTH,
             static_cast<unsigned>(s_cfg.qos));
    return true;
}

void mqtt_publisher_status(MqttStatus *out) {
    if (out == nullptr) return;
    *out = MqttStatus{};
    out->state = static_cast<MqttState>(s_state.load());
    out->enabled = s_enabled.load();
    out->client_up = s_client != nullptr;
    out->connects = s_connects.load();
    out->disconnects = s_disconnects.load();
    out->attempt = s_attempt.load();
    out->next_retry_ms = s_next_retry_ms.load();
    out->published = s_published.load();
    out->acked = s_acked.load();
    out->rejected = s_rejected.load();
    out->suppressed = s_dedup.suppressed();
    out->boot_id = s_boot_id;
    out->seq = s_seq.load();
    out->last_publish_ms = s_last_publish_ms.load();
    if (s_lock != nullptr && xSemaphoreTake(s_lock, pdMS_TO_TICKS(20)) == pdTRUE) {
        out->depth = s_outbox.depth();
        out->dropped = s_outbox.dropped();
        xSemaphoreGive(s_lock);
    }
    if (s_error_lock != nullptr &&
        xSemaphoreTake(s_error_lock, pdMS_TO_TICKS(20)) == pdTRUE) {
        settings_copy(out->last_error, sizeof(out->last_error), s_error);
        xSemaphoreGive(s_error_lock);
    }
}

void mqtt_publisher_config(MqttConfig *cfg, DeviceIdentity *id) {
    if (s_cfg_lock == nullptr) {
        if (cfg != nullptr) *cfg = mqtt_config_defaults();
        if (id != nullptr) *id = DeviceIdentity{};
        return;
    }
    xSemaphoreTake(s_cfg_lock, portMAX_DELAY);
    if (cfg != nullptr) *cfg = s_cfg;
    if (id != nullptr) *id = s_id;
    xSemaphoreGive(s_cfg_lock);
}

bool mqtt_publisher_apply(const MqttConfig &cfg, const DeviceIdentity &id) {
    if (s_cfg_lock == nullptr) return false;
    // Validated by the caller, and validated again here: this is the only door into
    // the live configuration, and a caller that forgot would otherwise put an
    // unusable topic straight into the publish path.
    if (!mqtt_config_validate(cfg, id, nullptr)) return false;

    xSemaphoreTake(s_cfg_lock, portMAX_DELAY);
    s_cfg = cfg;
    s_id = id;
    load_ca_locked();
    if (s_cfg.ca_present && s_ca_len == 0) s_cfg.ca_present = false;
    s_enabled.store(s_cfg.enabled);
    xSemaphoreGive(s_cfg_lock);

    const bool stored = settings_save_mqtt(cfg) && settings_save_identity(id);
    s_reconfigure.store(true);
    if (s_task != nullptr) xTaskNotifyGive(s_task);
    if (!stored) ESP_LOGW(TAG, "settings applied but not persisted");
    return stored;
}
