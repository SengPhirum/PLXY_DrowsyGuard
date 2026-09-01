#include "mqtt_config.h"

#include <cmath>
#include <cstdio>
#include <cstring>

// Pure, and it stays that way. See the header for why: the whole point of splitting
// mqtt_config from mqtt_publisher is that the topic tree, the payload schema, the
// validator, the backoff schedule and the two data structures can be driven from a
// host test rather than from a broker.

// --- transports ------------------------------------------------------------
uint16_t mqtt_default_port(MqttTransport t) {
    switch (t) {
        case MqttTransport::Tcp: return MQTT_DEMO_PORT_TCP;
        case MqttTransport::Tls: return MQTT_DEMO_PORT_TLS;
        case MqttTransport::Ws:  return MQTT_DEMO_PORT_WS;
        case MqttTransport::Wss: return MQTT_DEMO_PORT_WSS;
    }
    return MQTT_DEMO_PORT_TLS;
}

bool mqtt_transport_is_tls(MqttTransport t) {
    return t == MqttTransport::Tls || t == MqttTransport::Wss;
}

bool mqtt_transport_is_ws(MqttTransport t) {
    return t == MqttTransport::Ws || t == MqttTransport::Wss;
}

const char *mqtt_transport_name(MqttTransport t) {
    switch (t) {
        case MqttTransport::Tcp: return "tcp";
        case MqttTransport::Tls: return "tls";
        case MqttTransport::Ws:  return "ws";
        case MqttTransport::Wss: return "wss";
    }
    return "tls";
}

bool mqtt_transport_from_name(const char *s, MqttTransport *out) {
    if (s == nullptr || out == nullptr) return false;
    if (strcmp(s, "tcp") == 0) { *out = MqttTransport::Tcp; return true; }
    if (strcmp(s, "tls") == 0) { *out = MqttTransport::Tls; return true; }
    if (strcmp(s, "ws") == 0)  { *out = MqttTransport::Ws;  return true; }
    if (strcmp(s, "wss") == 0) { *out = MqttTransport::Wss; return true; }
    return false;
}

const char *mqtt_protocol_name(MqttProtocol p) {
    return p == MqttProtocol::V5 ? "5" : "3.1.1";
}

bool mqtt_protocol_from_name(const char *s, MqttProtocol *out) {
    if (s == nullptr || out == nullptr) return false;
    // Both spellings of each, because a form can plausibly send either and
    // rejecting "3.1.1" in favour of "311" would be a gratuitous trap.
    if (strcmp(s, "3.1.1") == 0 || strcmp(s, "311") == 0 || strcmp(s, "4") == 0) {
        *out = MqttProtocol::V311;
        return true;
    }
    if (strcmp(s, "5") == 0 || strcmp(s, "5.0") == 0) {
        *out = MqttProtocol::V5;
        return true;
    }
    return false;
}

MqttConfig mqtt_config_defaults() {
    MqttConfig c{};
    c.enabled = false;
    c.transport = MqttTransport::Tls;
    c.protocol = MqttProtocol::V311;
    settings_copy(c.host, sizeof(c.host), MQTT_DEMO_HOST);
    c.port = MQTT_DEMO_PORT_TLS;
    settings_copy(c.ws_path, sizeof(c.ws_path), MQTT_DEMO_WS_PATH);
    c.client_id[0] = '\0';          // derived from device_id
    c.username[0] = '\0';
    c.password[0] = '\0';
    c.qos = 1;
    c.topic_mode = MqttTopicMode::Auto;
    c.topic[0] = '\0';
    c.keepalive_s = 30;
    c.lwt = true;
    c.retain_status = true;
    c.ca_present = false;
    c.tls_insecure = false;
    return c;
}

// --- validation ------------------------------------------------------------
static bool fail(SettingsError *err, const char *field, const char *reason) {
    if (err != nullptr) { err->field = field; err->reason = reason; }
    return false;
}

// A hostname or an IPv4 literal, and nothing else. The restriction is not
// pedantry: this string is pasted straight into a URI, so a '/' would move the
// path, an '@' would move the host, and a space would truncate the whole thing at
// the transport layer with an error that names neither the field nor the cause.
// IPv6 is not supported and saying so is better than accepting "[::1]" and failing
// at connect time.
static bool host_ok(const char *h) {
    const size_t n = strlen(h);
    if (n == 0 || n >= MQTT_HOST_MAX) return false;
    if (h[0] == '.' || h[0] == '-' || h[n - 1] == '.' || h[n - 1] == '-') return false;
    bool prev_dot = false;
    for (size_t i = 0; i < n; ++i) {
        const char c = h[i];
        const bool alnum = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                           (c >= '0' && c <= '9');
        if (alnum || c == '-') { prev_dot = false; continue; }
        if (c == '.') {
            if (prev_dot) return false;    // ".." is not a label boundary
            prev_dot = true;
            continue;
        }
        return false;
    }
    return true;
}

// A topic this device may PUBLISH to. The wildcard check is the one that matters:
// '+' and '#' are subscription syntax, and a broker's response to a publish
// containing them ranges from a protocol error to accepting a literal topic nobody
// is subscribed to - which looks exactly like a broker that is silently dropping
// alerts.
static bool publish_topic_ok(const char *t) {
    const size_t n = strlen(t);
    if (n == 0 || n >= MQTT_TOPIC_MAX) return false;
    if (t[0] == '$') return false;          // broker-reserved ($SYS and friends)
    if (t[0] == '/' || t[n - 1] == '/') return false;
    for (size_t i = 0; i < n; ++i) {
        const unsigned char c = static_cast<unsigned char>(t[i]);
        if (c == '+' || c == '#') return false;
        if (c < 0x20 || c > 0x7E) return false;
        if (c == '/' && i > 0 && t[i - 1] == '/') return false;   // no empty level
    }
    return true;
}

bool mqtt_config_validate(const MqttConfig &cfg, const DeviceIdentity &id,
                          SettingsError *err) {
    // Identity first: it is what the auto topics are built from, so an invalid
    // device_id is an invalid MQTT configuration even though it lives elsewhere.
    if (!device_identity_validate(id, err)) return false;

    if (!host_ok(cfg.host)) {
        return fail(err, "host",
                    "a hostname or IPv4 address - letters, digits, '.' and '-' only");
    }
    if (cfg.port == 0) return fail(err, "port", "1-65535");

    if (mqtt_transport_is_ws(cfg.transport)) {
        const size_t n = strlen(cfg.ws_path);
        if (n == 0 || cfg.ws_path[0] != '/') {
            return fail(err, "ws_path", "must start with '/' - the EMQX broker uses /mqtt");
        }
        if (n >= MQTT_PATH_MAX) return fail(err, "ws_path", "too long");
        for (size_t i = 0; i < n; ++i) {
            const unsigned char c = static_cast<unsigned char>(cfg.ws_path[i]);
            if (c <= 0x20 || c > 0x7E || c == '?' || c == '#') {
                return fail(err, "ws_path", "no spaces, '?' or '#'");
            }
        }
    }

    if (cfg.client_id[0] != '\0') {
        const size_t n = strlen(cfg.client_id);
        if (n >= MQTT_CLIENT_ID_MAX) return fail(err, "client_id", "at most 63 characters");
        for (size_t i = 0; i < n; ++i) {
            const unsigned char c = static_cast<unsigned char>(cfg.client_id[i]);
            if (c <= 0x20 || c > 0x7E) {
                return fail(err, "client_id", "printable ASCII, no spaces");
            }
        }
    }

    if (!settings_is_printable(cfg.username, MQTT_USER_MAX - 1)) {
        return fail(err, "username", "printable ASCII, at most 63 characters");
    }
    if (!settings_is_printable(cfg.password, MQTT_PASS_MAX - 1)) {
        return fail(err, "password", "printable ASCII, at most 95 characters");
    }
    // A password with no username is not a configuration any broker accepts, and it
    // fails at CONNECT with a bare "not authorized".
    if (cfg.password[0] != '\0' && cfg.username[0] == '\0') {
        return fail(err, "username", "required when a password is set");
    }

    if (cfg.qos > 1) {
        return fail(err, "qos",
                    "0 or 1 - QoS 2 is not offered: the alert path is at-least-once "
                    "with event-id de-duplication at both ends");
    }

    if (cfg.topic_mode == MqttTopicMode::Manual) {
        if (!publish_topic_ok(cfg.topic)) {
            return fail(err, "topic",
                        "no '+' or '#' wildcards, no leading '$' or '/', no empty levels");
        }
        // The status topic is derived by appending, so the manual topic has to leave
        // room for it - otherwise the will topic silently truncates and the broker
        // holds a retained message on a topic nobody subscribes to.
        if (strlen(cfg.topic) + 8 >= MQTT_TOPIC_MAX) {
            return fail(err, "topic", "leave room for the derived '/status' topic");
        }
    }

    if (cfg.keepalive_s < 5 || cfg.keepalive_s > 300) {
        return fail(err, "keepalive", "5-300 seconds");
    }

    // Not an error, but worth refusing the combination that cannot work: plaintext
    // transports have nowhere to put a CA, and someone who pasted one has almost
    // certainly picked the wrong transport.
    if (cfg.ca_present && !mqtt_transport_is_tls(cfg.transport)) {
        return fail(err, "transport",
                    "a CA certificate is only used by TLS or WSS - remove it or "
                    "switch transport");
    }
    if (cfg.tls_insecure && !mqtt_transport_is_tls(cfg.transport)) {
        return fail(err, "tls_insecure", "only meaningful for TLS or WSS");
    }
    return true;
}

bool mqtt_ca_pem_valid(const char *pem, size_t len, SettingsError *err) {
    if (pem == nullptr || len == 0) return fail(err, "ca_cert", "empty");
    if (len >= MQTT_CA_MAX) {
        return fail(err, "ca_cert", "at most 4 kB - paste one root certificate, not a chain");
    }
    // A private key in this box is the mistake worth catching loudly: it would be
    // stored, returned by nothing, and useless - while the operator believes their
    // broker is authenticated.
    if (strstr(pem, "PRIVATE KEY") != nullptr) {
        return fail(err, "ca_cert",
                    "that is a private key, not a certificate - paste the broker's "
                    "CA certificate");
    }
    if (strstr(pem, "-----BEGIN CERTIFICATE-----") == nullptr ||
        strstr(pem, "-----END CERTIFICATE-----") == nullptr) {
        return fail(err, "ca_cert", "expected a PEM block starting -----BEGIN CERTIFICATE-----");
    }
    // Base64, newlines and the delimiter dashes. A DER file opened in an editor
    // trips this on its first byte, which is the point: mbedtls would otherwise
    // report a parse failure minutes later, from inside a TLS handshake.
    for (size_t i = 0; i < len && pem[i] != '\0'; ++i) {
        const unsigned char c = static_cast<unsigned char>(pem[i]);
        if (c == '\n' || c == '\r' || c == ' ' || c == '\t') continue;
        const bool b64 = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                         (c >= '0' && c <= '9') || c == '+' || c == '/' || c == '=';
        if (b64 || c == '-') continue;
        return fail(err, "ca_cert", "not a PEM file - unexpected byte in the body");
    }
    return true;
}

// --- addressing ------------------------------------------------------------
bool mqtt_uri(const MqttConfig &cfg, char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return false;
    out[0] = '\0';
    if (!host_ok(cfg.host) || cfg.port == 0) return false;

    const char *scheme = "mqtt";
    switch (cfg.transport) {
        case MqttTransport::Tcp: scheme = "mqtt"; break;
        case MqttTransport::Tls: scheme = "mqtts"; break;
        case MqttTransport::Ws:  scheme = "ws"; break;
        case MqttTransport::Wss: scheme = "wss"; break;
    }
    int n;
    if (mqtt_transport_is_ws(cfg.transport)) {
        const char *path = cfg.ws_path[0] != '\0' ? cfg.ws_path : MQTT_DEMO_WS_PATH;
        if (path[0] != '/') return false;
        n = snprintf(out, out_cap, "%s://%s:%u%s", scheme, cfg.host,
                     static_cast<unsigned>(cfg.port), path);
    } else {
        n = snprintf(out, out_cap, "%s://%s:%u", scheme, cfg.host,
                     static_cast<unsigned>(cfg.port));
    }
    return n > 0 && static_cast<size_t>(n) < out_cap;
}

// Replaces the second-to-last level of `in` with '+', so one subscription covers
// every device that shares the topic shape. Used only for manual topics: in auto
// mode the device level is known and substituted directly.
static bool wildcard_device_level(const char *in, char *out, size_t out_cap) {
    const char *last = strrchr(in, '/');
    if (last == nullptr) return settings_copy(out, out_cap, in);
    // Find the slash before that one.
    const char *prev = nullptr;
    for (const char *p = in; p < last; ++p) {
        if (*p == '/') prev = p;
    }
    const size_t head = prev != nullptr ? static_cast<size_t>(prev - in) + 1 : 0;
    const int n = snprintf(out, out_cap, "%.*s+%s", static_cast<int>(head), in, last);
    return n > 0 && static_cast<size_t>(n) < out_cap;
}

bool mqtt_topic(const MqttConfig &cfg, const DeviceIdentity &id, MqttTopicKind kind,
                char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return false;
    out[0] = '\0';

    if (cfg.topic_mode == MqttTopicMode::Auto) {
        // Refuse rather than build a topic out of an unvalidated segment: a topic is
        // the one field where a bad value does not fail, it just goes somewhere else.
        if (!settings_is_topic_segment(id.fleet_id) ||
            !settings_is_topic_segment(id.device_id)) {
            return false;
        }
        const char *leaf = (kind == MqttTopicKind::Status ||
                            kind == MqttTopicKind::FleetStatus) ? "status" : "alerts";
        const bool fleet = (kind == MqttTopicKind::FleetAlerts ||
                            kind == MqttTopicKind::FleetStatus);
        const int n = snprintf(out, out_cap, MQTT_TOPIC_ROOT "/%s/%s/%s",
                               id.fleet_id, fleet ? "+" : id.device_id, leaf);
        return n > 0 && static_cast<size_t>(n) < out_cap;
    }

    // Manual mode.
    if (!publish_topic_ok(cfg.topic)) return false;
    char base[MQTT_TOPIC_MAX + 8];
    switch (kind) {
        case MqttTopicKind::Alerts:
            return settings_copy(out, out_cap, cfg.topic);
        case MqttTopicKind::Status: {
            const size_t n = strlen(cfg.topic);
            const char *tail = "/alerts";
            const size_t tl = strlen(tail);
            if (n > tl && strcmp(cfg.topic + n - tl, tail) == 0) {
                const int w = snprintf(out, out_cap, "%.*s/status",
                                       static_cast<int>(n - tl), cfg.topic);
                return w > 0 && static_cast<size_t>(w) < out_cap;
            }
            const int w = snprintf(out, out_cap, "%s/status", cfg.topic);
            return w > 0 && static_cast<size_t>(w) < out_cap;
        }
        case MqttTopicKind::FleetAlerts:
            return wildcard_device_level(cfg.topic, out, out_cap);
        case MqttTopicKind::FleetStatus:
            if (!mqtt_topic(cfg, id, MqttTopicKind::Status, base, sizeof(base))) return false;
            return wildcard_device_level(base, out, out_cap);
    }
    return false;
}

bool mqtt_client_id(const MqttConfig &cfg, const DeviceIdentity &id, char *out,
                    size_t out_cap) {
    if (out == nullptr || out_cap == 0) return false;
    if (cfg.client_id[0] != '\0') return settings_copy(out, out_cap, cfg.client_id);
    const int n = snprintf(out, out_cap, "drowsyguard-%s",
                           id.device_id[0] != '\0' ? id.device_id : "unnamed");
    return n > 0 && static_cast<size_t>(n) < out_cap;
}

// --- timestamps ------------------------------------------------------------
// Howard Hinnant's civil-from-days, which is exact for the whole range of a 64-bit
// epoch and has no dependency on a C library that may or may not have gmtime_r.
// Written out rather than pulled in because this file is compiled by the host test
// as well as by the cross toolchain, and the two do not agree about <time.h>.
static void civil_from_days(int64_t z, int *y, unsigned *m, unsigned *d) {
    z += 719468;
    const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const uint64_t doe = static_cast<uint64_t>(z - era * 146097);
    const uint64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const int64_t yr = static_cast<int64_t>(yoe) + era * 400;
    const uint64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const uint64_t mp = (5 * doy + 2) / 153;
    const unsigned dd = static_cast<unsigned>(doy - (153 * mp + 2) / 5 + 1);
    const unsigned mm = static_cast<unsigned>(mp < 10 ? mp + 3 : mp - 9);
    *y = static_cast<int>(yr + (mm <= 2 ? 1 : 0));
    *m = mm;
    *d = dd;
}

bool mqtt_iso8601(int64_t epoch_ms, char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return false;
    out[0] = '\0';
    // 0 is "the clock has never been set", which is the normal state of a board with
    // no RTC that has not reached an SNTP server. An empty string is the honest
    // answer; 1970 would be a fabricated timestamp in an incident record.
    if (epoch_ms <= 0) return false;

    const int64_t secs = epoch_ms / 1000;
    int64_t days = secs / 86400;
    int64_t rem = secs % 86400;
    if (rem < 0) { rem += 86400; --days; }
    int y = 0;
    unsigned mo = 0, d = 0;
    civil_from_days(days, &y, &mo, &d);
    const int n = snprintf(out, out_cap, "%04d-%02u-%02uT%02u:%02u:%02uZ", y, mo, d,
                           static_cast<unsigned>(rem / 3600),
                           static_cast<unsigned>((rem / 60) % 60),
                           static_cast<unsigned>(rem % 60));
    return n > 0 && static_cast<size_t>(n) < out_cap;
}

// --- payloads --------------------------------------------------------------
const char *mqtt_severity_for(const char *alert, float risk) {
    if (alert == nullptr) return "info";
    if (strcmp(alert, "microsleep") == 0) return "critical";
    if (strcmp(alert, "no_driver") == 0) return "critical";
    if (strcmp(alert, "drowsy") == 0) return risk >= 0.85f ? "critical" : "high";
    if (strcmp(alert, "head_nod") == 0) return "high";
    if (strcmp(alert, "yawning") == 0) return "medium";
    if (strcmp(alert, "sneeze") == 0) return "info";
    return "info";
}

// %.3f rather than %g, and non-finite values become 0 - the same rule and the same
// reason as web_server.cpp's json_float(): printf emits "nan" and "inf", neither of
// which is JSON, and a subscriber whose parser throws loses every alert after the bad
// one rather than just that one. Zero is also the right substitute for a risk score:
// clamping an infinity to a large number would publish a confident nonsense reading
// where the honest answer is "the maths produced nothing".
//
// Finite values are still bounded, for the buffer rather than for the semantics: a
// stray 1e30 would print thirty digits and push the document past MQTT_PAYLOAD_MAX.
static float json_num(float v) {
    if (!std::isfinite(v)) return 0.0f;
    if (v > 1e6f) return 1e6f;
    if (v < -1e6f) return -1e6f;
    return v;
}

size_t mqtt_alert_payload(const DeviceIdentity &id, const MqttAlertEvent &ev,
                          char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return 0;
    out[0] = '\0';

    // The remark is operator free text, so it is escaped rather than restricted.
    // Everything else in the document is either a number or a validated segment.
    char remark[DEVICE_REMARK_MAX * 6 + 1];
    if (!settings_json_escape(id.remark, remark, sizeof(remark))) return 0;
    char ts[32];
    const bool have_ts = mqtt_iso8601(ev.epoch_ms, ts, sizeof(ts));

    const int n = snprintf(out, out_cap,
        "{\"schema\":\"drowsyguard.alert.v1\""
        ",\"event_id\":\"%s\""
        ",\"seq\":%lu"
        ",\"device_id\":\"%s\""
        ",\"fleet_id\":\"%s\""
        ",\"remark\":\"%s\""
        ",\"alert\":\"%s\""
        ",\"severity\":\"%s\""
        ",\"risk\":%.3f"
        ",\"perclos\":%.3f"
        ",\"alert_count\":%lu"
        ",\"uptime_ms\":%lu"
        ",\"ts\":\"%s\""
        ",\"ts_source\":\"%s\""
        "}",
        ev.event_id, static_cast<unsigned long>(ev.seq), id.device_id, id.fleet_id,
        remark, ev.alert, mqtt_severity_for(ev.alert, ev.risk),
        static_cast<double>(json_num(ev.risk)),
        static_cast<double>(json_num(ev.perclos)),
        static_cast<unsigned long>(ev.alert_count),
        static_cast<unsigned long>(ev.uptime_ms),
        have_ts ? ts : "",
        have_ts ? "sntp" : "uptime");
    if (n <= 0 || static_cast<size_t>(n) >= out_cap) {
        out[0] = '\0';
        return 0;
    }
    return static_cast<size_t>(n);
}

size_t mqtt_status_payload(const DeviceIdentity &id, bool online, const char *reason,
                           uint32_t uptime_ms, int64_t epoch_ms, uint32_t alert_count,
                           char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return 0;
    out[0] = '\0';
    char remark[DEVICE_REMARK_MAX * 6 + 1];
    if (!settings_json_escape(id.remark, remark, sizeof(remark))) return 0;
    char ts[32];
    const bool have_ts = mqtt_iso8601(epoch_ms, ts, sizeof(ts));
    const int n = snprintf(out, out_cap,
        "{\"schema\":\"drowsyguard.status.v1\""
        ",\"device_id\":\"%s\""
        ",\"fleet_id\":\"%s\""
        ",\"remark\":\"%s\""
        ",\"online\":%s"
        ",\"reason\":\"%s\""
        ",\"uptime_ms\":%lu"
        ",\"alert_count\":%lu"
        ",\"ts\":\"%s\""
        "}",
        id.device_id, id.fleet_id, remark, online ? "true" : "false",
        reason != nullptr ? reason : "",
        static_cast<unsigned long>(uptime_ms),
        static_cast<unsigned long>(alert_count),
        have_ts ? ts : "");
    if (n <= 0 || static_cast<size_t>(n) >= out_cap) {
        out[0] = '\0';
        return 0;
    }
    return static_cast<size_t>(n);
}

bool mqtt_event_id(const DeviceIdentity &id, uint32_t boot_id, uint32_t seq, char *out,
                   size_t out_cap) {
    if (out == nullptr || out_cap == 0) return false;
    out[0] = '\0';
    // The boot id is what stops a device that rebooted mid-drive from reusing the
    // sequence numbers it already published: without it, event 000001 after a reset
    // would be de-duplicated against event 000001 from before it and thrown away.
    const int n = snprintf(out, out_cap, "%s-%08lx-%06lu",
                           id.device_id[0] != '\0' ? id.device_id : "unnamed",
                           static_cast<unsigned long>(boot_id),
                           static_cast<unsigned long>(seq));
    return n > 0 && static_cast<size_t>(n) < out_cap;
}

// --- redaction -------------------------------------------------------------
void mqtt_mask(const char *in, char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return;
    out[0] = '\0';
    if (in == nullptr || in[0] == '\0') return;
    const size_t n = strlen(in);
    size_t at = 0;
    // Below four characters nothing is kept: "ab" masked as "a*b" discloses two
    // thirds of it, which is the same as not masking.
    const size_t keep_head = n >= 4 ? 2 : 0;
    const size_t keep_tail = n >= 4 ? 1 : 0;
    for (size_t i = 0; i < n && at + 1 < out_cap; ++i) {
        const bool clear = i < keep_head || i + keep_tail >= n;
        out[at++] = clear ? in[i] : '*';
    }
    out[at] = '\0';
}

size_t mqtt_config_json(const MqttConfig &cfg, const DeviceIdentity &id,
                        const WifiStaConfig &sta, size_t ca_bytes,
                        char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return 0;
    out[0] = '\0';

    char topic_alerts[MQTT_TOPIC_MAX + 16] = {0};
    char topic_status[MQTT_TOPIC_MAX + 16] = {0};
    char topic_fleet[MQTT_TOPIC_MAX + 16] = {0};
    char topic_fleet_status[MQTT_TOPIC_MAX + 16] = {0};
    mqtt_topic(cfg, id, MqttTopicKind::Alerts, topic_alerts, sizeof(topic_alerts));
    mqtt_topic(cfg, id, MqttTopicKind::Status, topic_status, sizeof(topic_status));
    mqtt_topic(cfg, id, MqttTopicKind::FleetAlerts, topic_fleet, sizeof(topic_fleet));
    mqtt_topic(cfg, id, MqttTopicKind::FleetStatus, topic_fleet_status,
               sizeof(topic_fleet_status));

    char client[MQTT_CLIENT_ID_MAX + 16] = {0};
    mqtt_client_id(cfg, id, client, sizeof(client));
    char uri[MQTT_HOST_MAX + MQTT_PATH_MAX + 24] = {0};
    mqtt_uri(cfg, uri, sizeof(uri));

    // The two fields that must never carry a value: the broker password and the
    // station password. They are absent, not starred - a masked field still has to
    // be built from the secret, and the value that is never formatted is the value
    // that cannot be logged by accident.
    char user_mask[MQTT_USER_MAX + 1] = {0};
    mqtt_mask(cfg.username, user_mask, sizeof(user_mask));
    char remark[DEVICE_REMARK_MAX * 6 + 1];
    if (!settings_json_escape(id.remark, remark, sizeof(remark))) return 0;
    char ssid[WIFI_SSID_MAX * 6 + 1];
    if (!settings_json_escape(sta.ssid, ssid, sizeof(ssid))) return 0;
    char topic_manual[MQTT_TOPIC_MAX * 6 + 1];
    if (!settings_json_escape(cfg.topic, topic_manual, sizeof(topic_manual))) return 0;

    const int n = snprintf(out, out_cap,
        "{\"enabled\":%s"
        ",\"transport\":\"%s\""
        ",\"protocol\":\"%s\""
        ",\"host\":\"%s\""
        ",\"port\":%u"
        ",\"ws_path\":\"%s\""
        ",\"client_id\":\"%s\""
        ",\"client_id_auto\":%s"
        ",\"username_masked\":\"%s\""
        ",\"username_set\":%s"
        ",\"password_set\":%s"
        ",\"qos\":%u"
        ",\"keepalive\":%u"
        ",\"lwt\":%s"
        ",\"retain_status\":%s"
        ",\"tls_insecure\":%s"
        ",\"ca_present\":%s"
        ",\"ca_bytes\":%u"
        ",\"topic_mode\":\"%s\""
        ",\"topic\":\"%s\""
        ",\"uri\":\"%s\""
        ",\"device_id\":\"%s\""
        ",\"fleet_id\":\"%s\""
        ",\"remark\":\"%s\""
        ",\"topics\":{\"alerts\":\"%s\",\"status\":\"%s\""
                    ",\"fleet_alerts\":\"%s\",\"fleet_status\":\"%s\"}"
        ",\"sta\":{\"enabled\":%s,\"ssid\":\"%s\",\"password_set\":%s}"
        ",\"demo_broker\":{\"host\":\"%s\",\"tcp\":%u,\"tls\":%u,\"ws\":%u,\"wss\":%u"
                        ",\"path\":\"%s\",\"public\":true}"
        "}",
        cfg.enabled ? "true" : "false",
        mqtt_transport_name(cfg.transport), mqtt_protocol_name(cfg.protocol),
        cfg.host, static_cast<unsigned>(cfg.port), cfg.ws_path,
        client, cfg.client_id[0] == '\0' ? "true" : "false",
        user_mask,
        cfg.username[0] != '\0' ? "true" : "false",
        cfg.password[0] != '\0' ? "true" : "false",
        static_cast<unsigned>(cfg.qos), static_cast<unsigned>(cfg.keepalive_s),
        cfg.lwt ? "true" : "false",
        cfg.retain_status ? "true" : "false",
        cfg.tls_insecure ? "true" : "false",
        cfg.ca_present ? "true" : "false", static_cast<unsigned>(ca_bytes),
        cfg.topic_mode == MqttTopicMode::Manual ? "manual" : "auto",
        topic_manual, uri,
        id.device_id, id.fleet_id, remark,
        topic_alerts, topic_status, topic_fleet, topic_fleet_status,
        sta.enabled ? "true" : "false", ssid,
        sta.password[0] != '\0' ? "true" : "false",
        MQTT_DEMO_HOST, MQTT_DEMO_PORT_TCP, MQTT_DEMO_PORT_TLS, MQTT_DEMO_PORT_WS,
        MQTT_DEMO_PORT_WSS, MQTT_DEMO_WS_PATH);
    if (n <= 0 || static_cast<size_t>(n) >= out_cap) {
        out[0] = '\0';
        return 0;
    }
    return static_cast<size_t>(n);
}

// --- persistence -----------------------------------------------------------
size_t mqtt_config_serialize(const MqttConfig &cfg, uint8_t *out, size_t cap) {
    if (out == nullptr || cap < SETTINGS_BLOB_HEADER) return 0;
    BlobOut o{out + SETTINGS_BLOB_HEADER, cap - SETTINGS_BLOB_HEADER, 0, true};
    blob_put_u8(&o, cfg.enabled ? 1 : 0);
    blob_put_u8(&o, static_cast<uint8_t>(cfg.transport));
    blob_put_u8(&o, static_cast<uint8_t>(cfg.protocol));
    blob_put_str(&o, cfg.host);
    blob_put_u16(&o, cfg.port);
    blob_put_str(&o, cfg.ws_path);
    blob_put_str(&o, cfg.client_id);
    blob_put_str(&o, cfg.username);
    blob_put_str(&o, cfg.password);
    blob_put_u8(&o, cfg.qos);
    blob_put_u8(&o, static_cast<uint8_t>(cfg.topic_mode));
    blob_put_str(&o, cfg.topic);
    blob_put_u16(&o, cfg.keepalive_s);
    blob_put_u8(&o, cfg.lwt ? 1 : 0);
    blob_put_u8(&o, cfg.retain_status ? 1 : 0);
    blob_put_u8(&o, cfg.ca_present ? 1 : 0);
    blob_put_u8(&o, cfg.tls_insecure ? 1 : 0);
    if (!o.ok) return 0;
    return blob_seal(out, cap, MQTT_BLOB_VERSION, o.at);
}

bool mqtt_config_deserialize(const uint8_t *buf, size_t len, MqttConfig *out) {
    if (out == nullptr) return false;
    BlobIn i{};
    if (!blob_open(buf, len, MQTT_BLOB_VERSION, &i)) return false;

    MqttConfig c = mqtt_config_defaults();
    c.enabled = blob_get_u8(&i) != 0;
    const uint8_t tr = blob_get_u8(&i);
    const uint8_t pr = blob_get_u8(&i);
    // An enum read out of flash is an integer until it has been checked. Casting an
    // out-of-range byte straight into the enum is undefined behaviour and, more
    // practically, would give the URI builder a transport it has no scheme for.
    if (tr > static_cast<uint8_t>(MqttTransport::Wss)) return false;
    if (pr > static_cast<uint8_t>(MqttProtocol::V5)) return false;
    c.transport = static_cast<MqttTransport>(tr);
    c.protocol = static_cast<MqttProtocol>(pr);
    blob_get_str(&i, c.host, sizeof(c.host));
    c.port = blob_get_u16(&i);
    blob_get_str(&i, c.ws_path, sizeof(c.ws_path));
    blob_get_str(&i, c.client_id, sizeof(c.client_id));
    blob_get_str(&i, c.username, sizeof(c.username));
    blob_get_str(&i, c.password, sizeof(c.password));
    c.qos = blob_get_u8(&i);
    const uint8_t tm = blob_get_u8(&i);
    if (tm > static_cast<uint8_t>(MqttTopicMode::Manual)) return false;
    c.topic_mode = static_cast<MqttTopicMode>(tm);
    blob_get_str(&i, c.topic, sizeof(c.topic));
    c.keepalive_s = blob_get_u16(&i);
    c.lwt = blob_get_u8(&i) != 0;
    c.retain_status = blob_get_u8(&i) != 0;
    c.ca_present = blob_get_u8(&i) != 0;
    c.tls_insecure = blob_get_u8(&i) != 0;
    if (!i.ok) return false;

    // Validated against the *current* rules, with a placeholder identity: the
    // identity has its own record and its own validation, and coupling the two here
    // would mean a bad remark discarded a perfectly good broker configuration.
    DeviceIdentity probe{};
    settings_copy(probe.device_id, sizeof(probe.device_id), "probe");
    settings_copy(probe.fleet_id, sizeof(probe.fleet_id), "probe");
    if (!mqtt_config_validate(c, probe, nullptr)) return false;
    *out = c;
    return true;
}

// --- backoff ---------------------------------------------------------------
uint32_t mqtt_backoff_ms(uint32_t attempt) {
    if (attempt == 0) return 0;             // the first connection is immediate
    const uint32_t shift = (attempt - 1) < MQTT_BACKOFF_SHIFT_MAX
                               ? (attempt - 1) : MQTT_BACKOFF_SHIFT_MAX;
    const uint32_t d = MQTT_BACKOFF_BASE_MS << shift;
    return d > MQTT_BACKOFF_MAX_MS ? MQTT_BACKOFF_MAX_MS : d;
}

uint32_t mqtt_backoff_jittered_ms(uint32_t attempt, uint32_t rnd) {
    const uint32_t base = mqtt_backoff_ms(attempt);
    if (base == 0) return 0;
    // Additive, up to a quarter of the interval. Additive rather than
    // multiplicative because the floor matters: a device must not retry a broker
    // 200 ms after being refused, however much jitter is applied.
    const uint32_t span = base / 4;
    return span == 0 ? base : base + (rnd % span);
}

// --- de-duplication --------------------------------------------------------
void MqttDedup::reset() {
    at_ = 0;
    count_ = 0;
    suppressed_ = 0;
    for (int i = 0; i < MQTT_DEDUP_SLOTS; ++i) slot_[i][0] = '\0';
}

bool MqttDedup::seen_or_add(const char *event_id) {
    if (event_id == nullptr || event_id[0] == '\0') return false;
    for (int i = 0; i < count_; ++i) {
        if (strncmp(slot_[i], event_id, sizeof(slot_[0])) == 0) {
            ++suppressed_;
            return true;
        }
    }
    settings_copy(slot_[at_], sizeof(slot_[0]), event_id);
    at_ = (at_ + 1) % MQTT_DEDUP_SLOTS;
    if (count_ < MQTT_DEDUP_SLOTS) ++count_;
    return false;
}

// --- outbox ----------------------------------------------------------------
void MqttOutbox::reset() {
    head_ = 0;
    count_ = 0;
    queued_ = 0;
    dropped_ = 0;
}

bool MqttOutbox::push(const MqttAlertEvent &ev) {
    bool evicted = false;
    if (count_ == MQTT_OUTBOX_DEPTH) {
        // Drop the oldest. After a long outage the useful record is the last few
        // minutes of a deteriorating driver, not the first few - and the alternative,
        // refusing the new event, would mean the buffer stops recording exactly when
        // the situation is getting worse.
        head_ = (head_ + 1) % MQTT_OUTBOX_DEPTH;
        --count_;
        ++dropped_;
        evicted = true;
    }
    slot_[(head_ + count_) % MQTT_OUTBOX_DEPTH] = ev;
    ++count_;
    ++queued_;
    return !evicted;
}

bool MqttOutbox::peek(MqttAlertEvent *out) const {
    if (count_ == 0) return false;
    if (out != nullptr) *out = slot_[head_];
    return true;
}

void MqttOutbox::commit() {
    if (count_ == 0) return;
    head_ = (head_ + 1) % MQTT_OUTBOX_DEPTH;
    --count_;
}

bool MqttOutbox::pop(MqttAlertEvent *out) {
    if (!peek(out)) return false;
    commit();
    return true;
}
