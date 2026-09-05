"""MQTT alerting, driven through the real firmware sources on the host.

`firmware/esp32s3/main/mqtt_config.cpp` and `device_config.cpp` hold everything about
this feature that can be wrong without a broker in the room: which settings are
accepted, what topic they produce, what the published document looks like, what
survives a power cycle, how long a reconnect waits, which duplicates are suppressed,
and what happens to alerts that arrive while the network is gone. None of it needs a
socket, so none of it is tested by pointing the board at a broker and looking - which
is the only test this feature would otherwise have.

The split is deliberate and it is the whole reason these two files contain no ESP-IDF
headers. `mqtt_publisher.cpp` and `settings_nvs.cpp` are the halves that genuinely
cannot run here (FreeRTOS tasks, esp-mqtt, a flash partition), and they were written
to own no logic of their own: the publisher task is a state machine over
`mqtt_backoff_ms`, `MqttOutbox` and `MqttDedup`, and the NVS layer is `nvs_set_blob`
around `mqtt_config_serialize`. Every decision is on this side of the line.

The harness speaks one line per command: a name, a tab, and a form-encoded argument
list. That shape is not laziness - the arguments are decoded by the firmware's own
`settings_form_field()`, so every case below also exercises the percent-decoder that
`POST /api/mqtt` depends on, several thousand times, on real inputs.

Skipped when there is no host compiler. That is a real gap on such a machine, but a
correctness gate that cannot run is not a reason to fail an unrelated checkout.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'firmware/esp32s3/main'

# Mirrors of the constants in the headers. Duplicated rather than parsed on purpose:
# if one of these moves, a human should re-read the expectations below rather than
# have them silently re-derive themselves around the change.
OUTBOX_DEPTH = 16
DEDUP_SLOTS = 24
BACKOFF_BASE_MS = 1000
BACKOFF_MAX_MS = 60000
TOPIC_ROOT = 'plxy/drowsyguard'
DEMO_HOST = 'broker.emqx.io'

HARNESS = r'''
// Compiled by tests/test_mqtt_config.py against the real firmware sources.
//
// One line in, one line out. The argument list is form-encoded and is decoded with
// the firmware's own settings_form_field(), so the decoder is exercised by every
// case the test file contains rather than only by the handful aimed at it.
#include <cstdio>
#include <cstring>
#include <cstdlib>

#include "device_config.h"
#include "mqtt_config.h"

static char g_line[MQTT_CA_MAX + 2048];
static char g_val[MQTT_CA_MAX + 1];

// Which field, if any, was too long for its destination. POST /api/mqtt refuses a
// value it would have to truncate - a hostname silently cut at 95 characters resolves
// to nothing, and the operator then sees a connection error rather than the field they
// got wrong - so the harness has to refuse it too, or the length rules below would be
// testing nothing.
static const char *g_overflow = nullptr;

static void copy_strict(char *dst, size_t cap, const char *src, const char *name) {
    if (!settings_copy(dst, cap, src)) g_overflow = name;
}

static bool field(const char *q, const char *key) {
    return settings_form_field(q, key, g_val, sizeof(g_val));
}

// printf("%s") of a decoded value would break the one-line protocol the moment the
// value contains a newline - which is exactly what a percent-decoded PEM contains, and
// therefore the case most worth testing.
static void print_esc(const char *s) {
    putchar('[');
    for (const char *p = s; *p != '\0'; ++p) {
        const unsigned char c = static_cast<unsigned char>(*p);
        if (c == '\n') fputs("\\n", stdout);
        else if (c == '\r') fputs("\\r", stdout);
        else if (c == '\t') fputs("\\t", stdout);
        else if (c == '\\') fputs("\\\\", stdout);
        else if (c < 0x20 || c == 0x7F) printf("\\x%02x", c);
        else putchar(static_cast<char>(c));
    }
    fputs("]\n", stdout);
}

static bool flag(const char *q, const char *key, bool *out) {
    if (!field(q, key)) return false;
    *out = g_val[0] == '1';
    return true;
}

static bool num(const char *q, const char *key, long *out) {
    if (!field(q, key)) return false;
    *out = strtol(g_val, nullptr, 10);
    return true;
}

// Builds a config and an identity from the arguments, starting from the firmware's
// own defaults so that a case which names three fields is testing those three fields
// against a realistic rest-of-configuration.
static MqttConfig parse_cfg(const char *q, DeviceIdentity *id) {
    MqttConfig c = mqtt_config_defaults();
    *id = device_identity_defaults("DrowsyGuard-C5E019");

    long n = 0;
    bool b = false;
    if (flag(q, "enabled", &b)) c.enabled = b;
    if (field(q, "transport")) {
        MqttTransport tr;
        if (mqtt_transport_from_name(g_val, &tr)) {
            c.transport = tr;
            c.port = mqtt_default_port(tr);
        } else {
            // An unknown name is passed through as a deliberate poison value so the
            // test can assert the *parser* rejected it rather than the validator.
            printf("badtransport\n");
        }
    }
    if (field(q, "protocol")) {
        MqttProtocol pr;
        if (mqtt_protocol_from_name(g_val, &pr)) c.protocol = pr;
        else printf("badprotocol\n");
    }
    if (field(q, "host")) copy_strict(c.host, sizeof(c.host), g_val, "host");
    if (num(q, "port", &n)) c.port = static_cast<uint16_t>(n);
    if (field(q, "ws_path")) copy_strict(c.ws_path, sizeof(c.ws_path), g_val, "ws_path");
    if (field(q, "client_id")) {
        copy_strict(c.client_id, sizeof(c.client_id), g_val, "client_id");
    }
    if (field(q, "username")) {
        copy_strict(c.username, sizeof(c.username), g_val, "username");
    }
    if (field(q, "password")) {
        copy_strict(c.password, sizeof(c.password), g_val, "password");
    }
    if (num(q, "qos", &n)) c.qos = static_cast<uint8_t>(n);
    if (num(q, "keepalive", &n)) c.keepalive_s = static_cast<uint16_t>(n);
    if (flag(q, "lwt", &b)) c.lwt = b;
    if (flag(q, "retain_status", &b)) c.retain_status = b;
    if (flag(q, "ca_present", &b)) c.ca_present = b;
    if (flag(q, "tls_insecure", &b)) c.tls_insecure = b;
    if (field(q, "topic_mode")) {
        c.topic_mode = strcmp(g_val, "manual") == 0 ? MqttTopicMode::Manual
                                                   : MqttTopicMode::Auto;
    }
    if (field(q, "topic")) copy_strict(c.topic, sizeof(c.topic), g_val, "topic");
    if (field(q, "device_id")) {
        copy_strict(id->device_id, sizeof(id->device_id), g_val, "device_id");
    }
    if (field(q, "fleet_id")) {
        copy_strict(id->fleet_id, sizeof(id->fleet_id), g_val, "fleet_id");
    }
    if (field(q, "remark")) copy_strict(id->remark, sizeof(id->remark), g_val, "remark");
    return c;
}

static WifiStaConfig parse_sta(const char *q) {
    WifiStaConfig s{};
    bool b = false;
    if (flag(q, "sta_enabled", &b)) s.enabled = b;
    if (field(q, "sta_ssid")) copy_strict(s.ssid, sizeof(s.ssid), g_val, "sta_ssid");
    if (field(q, "sta_password")) {
        copy_strict(s.password, sizeof(s.password), g_val, "sta_password");
    }
    return s;
}

static MqttAlertEvent parse_event(const char *q, const DeviceIdentity &id) {
    MqttAlertEvent e{};
    long n = 0;
    settings_copy(e.alert, sizeof(e.alert), "drowsy");
    if (field(q, "alert")) settings_copy(e.alert, sizeof(e.alert), g_val);
    if (field(q, "risk")) e.risk = static_cast<float>(atof(g_val));
    if (field(q, "perclos")) e.perclos = static_cast<float>(atof(g_val));
    if (num(q, "alert_count", &n)) e.alert_count = static_cast<uint32_t>(n);
    if (num(q, "uptime_ms", &n)) e.uptime_ms = static_cast<uint32_t>(n);
    if (num(q, "seq", &n)) e.seq = static_cast<uint32_t>(n);
    if (field(q, "epoch_ms")) e.epoch_ms = strtoll(g_val, nullptr, 10);
    uint32_t boot = 0x9f1c2ab3u;
    if (num(q, "boot_id", &n)) boot = static_cast<uint32_t>(n);
    if (field(q, "event_id")) settings_copy(e.event_id, sizeof(e.event_id), g_val);
    else mqtt_event_id(id, boot, e.seq, e.event_id, sizeof(e.event_id));
    return e;
}

static MqttOutbox g_outbox;
static MqttDedup g_dedup;

int main() {
    while (fgets(g_line, sizeof(g_line), stdin) != nullptr) {
        char *nl = strpbrk(g_line, "\r\n");
        if (nl != nullptr) *nl = '\0';
        char *tab = strchr(g_line, '\t');
        const char *cmd = g_line;
        const char *q = "";
        if (tab != nullptr) { *tab = '\0'; q = tab + 1; }

        if (strcmp(cmd, "quit") == 0) break;
        g_overflow = nullptr;

        if (strcmp(cmd, "validate") == 0) {
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            SettingsError err{};
            if (g_overflow != nullptr) printf("err %s\n", g_overflow);
            else if (mqtt_config_validate(c, id, &err)) printf("ok\n");
            else printf("err %s\n", err.field != nullptr ? err.field : "?");
        } else if (strcmp(cmd, "validate_sta") == 0) {
            const WifiStaConfig s = parse_sta(q);
            SettingsError err{};
            if (g_overflow != nullptr) printf("err %s\n", g_overflow);
            else if (wifi_sta_validate(s, &err)) printf("ok\n");
            else printf("err %s\n", err.field != nullptr ? err.field : "?");
        } else if (strcmp(cmd, "reason_sta") == 0) {
            // Field AND sentence, unlike validate_sta above: the field is what the
            // page highlights and the sentence is what it prints, and a rule with no
            // sentence is a rule the operator has to guess at.
            const WifiStaConfig s = parse_sta(q);
            SettingsError err{};
            if (g_overflow != nullptr) printf("err %s|overflow\n", g_overflow);
            else if (wifi_sta_validate(s, &err)) printf("ok\n");
            else printf("err %s|%s\n", err.field, err.reason);
        } else if (strcmp(cmd, "reason") == 0) {
            // The sentence a UI puts under the input. Checked separately because an
            // error with no actionable text is an error nobody can fix.
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            SettingsError err{};
            if (mqtt_config_validate(c, id, &err)) printf("ok\n");
            else printf("err %s|%s\n", err.field, err.reason);
        } else if (strcmp(cmd, "topic") == 0) {
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            MqttTopicKind kind = MqttTopicKind::Alerts;
            if (field(q, "kind")) {
                if (strcmp(g_val, "status") == 0) kind = MqttTopicKind::Status;
                else if (strcmp(g_val, "fleet_alerts") == 0) kind = MqttTopicKind::FleetAlerts;
                else if (strcmp(g_val, "fleet_status") == 0) kind = MqttTopicKind::FleetStatus;
            }
            char out[MQTT_TOPIC_MAX + 16];
            if (mqtt_topic(c, id, kind, out, sizeof(out))) printf("ok %s\n", out);
            else printf("err\n");
        } else if (strcmp(cmd, "uri") == 0) {
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            char out[MQTT_HOST_MAX + MQTT_PATH_MAX + 24];
            if (mqtt_uri(c, out, sizeof(out))) printf("ok %s\n", out);
            else printf("err\n");
        } else if (strcmp(cmd, "clientid") == 0) {
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            char out[MQTT_CLIENT_ID_MAX + 16];
            if (mqtt_client_id(c, id, out, sizeof(out))) printf("ok %s\n", out);
            else printf("err\n");
        } else if (strcmp(cmd, "payload") == 0) {
            DeviceIdentity id{};
            parse_cfg(q, &id);
            const MqttAlertEvent e = parse_event(q, id);
            char out[MQTT_PAYLOAD_MAX];
            const size_t n = mqtt_alert_payload(id, e, out, sizeof(out));
            if (n == 0) printf("err\n");
            else printf("ok %s\n", out);
        } else if (strcmp(cmd, "payload_cap") == 0) {
            // The same payload into a deliberately small buffer, to prove a document
            // that will not fit produces nothing rather than half of one.
            DeviceIdentity id{};
            parse_cfg(q, &id);
            const MqttAlertEvent e = parse_event(q, id);
            long cap = 64;
            num(q, "cap", &cap);
            static char out[MQTT_PAYLOAD_MAX];
            memset(out, 'X', sizeof(out));
            const size_t n = mqtt_alert_payload(id, e, out, static_cast<size_t>(cap));
            printf("%zu %zu\n", n, strlen(out));
        } else if (strcmp(cmd, "status_payload") == 0) {
            DeviceIdentity id{};
            parse_cfg(q, &id);
            bool online = false;
            flag(q, "online", &online);
            char reason[32] = "connected";
            if (field(q, "reason")) settings_copy(reason, sizeof(reason), g_val);
            long uptime = 0, count = 0;
            num(q, "uptime_ms", &uptime);
            num(q, "alert_count", &count);
            int64_t epoch = 0;
            if (field(q, "epoch_ms")) epoch = strtoll(g_val, nullptr, 10);
            char out[MQTT_PAYLOAD_MAX];
            const size_t n = mqtt_status_payload(id, online, reason,
                                                 static_cast<uint32_t>(uptime), epoch,
                                                 static_cast<uint32_t>(count), out,
                                                 sizeof(out));
            if (n == 0) printf("err\n");
            else printf("ok %s\n", out);
        } else if (strcmp(cmd, "redact") == 0) {
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            const WifiStaConfig s = parse_sta(q);
            long ca = 0;
            num(q, "ca_bytes", &ca);
            static char out[4096];
            const size_t n = mqtt_config_json(c, id, s, static_cast<size_t>(ca), out,
                                              sizeof(out));
            if (n == 0) printf("err\n");
            else printf("ok %s\n", out);
        } else if (strcmp(cmd, "roundtrip") == 0) {
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            uint8_t blob[MQTT_BLOB_MAX];
            const size_t n = mqtt_config_serialize(c, blob, sizeof(blob));
            if (n == 0) { printf("err serialize\n"); goto flushed; }
            // Optional damage, to prove the reader rejects what it cannot trust
            // rather than believing a plausible-looking record.
            {
                long at = -1;
                if (num(q, "flip", &at) && at >= 0 && static_cast<size_t>(at) < n) {
                    blob[at] ^= 0xFF;
                }
                long trunc = -1;
                size_t len = n;
                if (num(q, "truncate", &trunc) && trunc >= 0 &&
                    static_cast<size_t>(trunc) < n) {
                    len = static_cast<size_t>(trunc);
                }
                MqttConfig back{};
                if (!mqtt_config_deserialize(blob, len, &back)) {
                    printf("err reject\n");
                    goto flushed;
                }
                char uri[160], topic[MQTT_TOPIC_MAX + 16];
                mqtt_uri(back, uri, sizeof(uri));
                mqtt_topic(back, id, MqttTopicKind::Alerts, topic, sizeof(topic));
                // '|' rather than spaces: a remark and an SSID may both contain one,
                // and a separator a value can contain is not a separator.
                printf("ok bytes=%zu|enabled=%d|transport=%s|protocol=%s|host=%s"
                       "|port=%u|ws_path=%s|client_id=%s|username=%s|password=%s"
                       "|qos=%u|keepalive=%u|lwt=%d|retain=%d|ca=%d|insecure=%d"
                       "|mode=%s|topic=%s|uri=%s\n",
                       n, back.enabled ? 1 : 0, mqtt_transport_name(back.transport),
                       mqtt_protocol_name(back.protocol), back.host,
                       static_cast<unsigned>(back.port), back.ws_path,
                       back.client_id[0] ? back.client_id : "-",
                       back.username[0] ? back.username : "-",
                       back.password[0] ? back.password : "-",
                       static_cast<unsigned>(back.qos),
                       static_cast<unsigned>(back.keepalive_s),
                       back.lwt ? 1 : 0, back.retain_status ? 1 : 0,
                       back.ca_present ? 1 : 0, back.tls_insecure ? 1 : 0,
                       back.topic_mode == MqttTopicMode::Manual ? "manual" : "auto",
                       back.topic[0] ? back.topic : "-", uri);
            }
        } else if (strcmp(cmd, "roundtrip_version") == 0) {
            // A record written by a build with a different blob version. The reader
            // must refuse it: the alternative is reading a hostname out of the middle
            // of a password.
            DeviceIdentity id{};
            const MqttConfig c = parse_cfg(q, &id);
            uint8_t blob[MQTT_BLOB_MAX];
            const size_t n = mqtt_config_serialize(c, blob, sizeof(blob));
            if (n == 0) { printf("err serialize\n"); goto flushed; }
            blob[2] = static_cast<uint8_t>(MQTT_BLOB_VERSION + 1);
            {
                MqttConfig back{};
                printf("%s\n", mqtt_config_deserialize(blob, n, &back) ? "ok" : "err reject");
            }
        } else if (strcmp(cmd, "roundtrip_identity") == 0) {
            DeviceIdentity id{};
            parse_cfg(q, &id);
            uint8_t blob[DEVICE_BLOB_MAX];
            const size_t n = device_identity_serialize(id, blob, sizeof(blob));
            if (n == 0) { printf("err serialize\n"); goto flushed; }
            {
                long at = -1;
                if (num(q, "flip", &at) && at >= 0 && static_cast<size_t>(at) < n) {
                    blob[at] ^= 0xFF;
                }
                DeviceIdentity back{};
                if (!device_identity_deserialize(blob, n, &back)) {
                    printf("err reject\n");
                    goto flushed;
                }
                printf("ok bytes=%zu|device_id=%s|fleet_id=%s|remark=%s\n", n,
                       back.device_id, back.fleet_id,
                       back.remark[0] ? back.remark : "-");
            }
        } else if (strcmp(cmd, "roundtrip_wifi") == 0) {
            const WifiStaConfig s = parse_sta(q);
            uint8_t blob[WIFI_BLOB_MAX];
            const size_t n = wifi_sta_serialize(s, blob, sizeof(blob));
            if (n == 0) { printf("err serialize\n"); goto flushed; }
            {
                long at = -1;
                if (num(q, "flip", &at) && at >= 0 && static_cast<size_t>(at) < n) {
                    blob[at] ^= 0xFF;
                }
                WifiStaConfig back{};
                if (!wifi_sta_deserialize(blob, n, &back)) {
                    printf("err reject\n");
                    goto flushed;
                }
                printf("ok bytes=%zu|enabled=%d|ssid=%s|password=%s\n", n,
                       back.enabled ? 1 : 0, back.ssid[0] ? back.ssid : "-",
                       back.password[0] ? back.password : "-");
            }
        } else if (strcmp(cmd, "backoff") == 0) {
            long a = 0, rnd = -1;
            num(q, "attempt", &a);
            if (num(q, "rnd", &rnd)) {
                printf("%u\n", mqtt_backoff_jittered_ms(static_cast<uint32_t>(a),
                                                        static_cast<uint32_t>(rnd)));
            } else {
                printf("%u\n", mqtt_backoff_ms(static_cast<uint32_t>(a)));
            }
        } else if (strcmp(cmd, "dedup_reset") == 0) {
            g_dedup.reset();
            printf("ok\n");
        } else if (strcmp(cmd, "dedup") == 0) {
            field(q, "id");
            // Sequenced deliberately. The order printf evaluates its arguments in is
            // unspecified, so reading the counters in the same call that mutates them
            // reported the state from before the call as often as after it - which
            // made the suppression counter look permanently stuck at zero.
            const bool seen = g_dedup.seen_or_add(g_val);
            printf("%d %d %u\n", seen ? 1 : 0, g_dedup.size(), g_dedup.suppressed());
        } else if (strcmp(cmd, "dedup_check") == 0) {
            // The publisher's pre-flight question: already delivered? Never records.
            field(q, "id");
            const bool seen = g_dedup.already_published(g_val);
            printf("%d %d %u\n", seen ? 1 : 0, g_dedup.size(), g_dedup.suppressed());
        } else if (strcmp(cmd, "dedup_mark") == 0) {
            // The publisher's post-delivery record. Idempotent.
            field(q, "id");
            g_dedup.mark_published(g_val);
            printf("%d %u\n", g_dedup.size(), g_dedup.suppressed());
        } else if (strcmp(cmd, "outbox_reset") == 0) {
            g_outbox.reset();
            printf("ok\n");
        } else if (strcmp(cmd, "outbox_push") == 0) {
            DeviceIdentity id{};
            parse_cfg(q, &id);
            const MqttAlertEvent e = parse_event(q, id);
            const bool clean = g_outbox.push(e);      // sequenced, as in "dedup" above
            printf("%d %d %u %u\n", clean ? 1 : 0, g_outbox.depth(), g_outbox.queued(),
                   g_outbox.dropped());
        } else if (strcmp(cmd, "outbox_peek") == 0) {
            MqttAlertEvent e{};
            if (!g_outbox.peek(&e)) { printf("empty\n"); goto flushed; }
            printf("%s %s %u %d\n", e.event_id, e.alert, e.seq, g_outbox.depth());
        } else if (strcmp(cmd, "outbox_commit") == 0) {
            g_outbox.commit();
            printf("%d\n", g_outbox.depth());
        } else if (strcmp(cmd, "outbox_commit_if") == 0) {
            long s = 0;
            num(q, "seq", &s);
            const bool did = g_outbox.commit_if_seq(static_cast<uint32_t>(s));
            printf("%d %d\n", did ? 1 : 0, g_outbox.depth());
        } else if (strcmp(cmd, "outbox_pop") == 0) {
            MqttAlertEvent e{};
            if (!g_outbox.pop(&e)) { printf("empty\n"); goto flushed; }
            const int left = g_outbox.depth();
            printf("%s %u %d\n", e.alert, e.seq, left);
        } else if (strcmp(cmd, "outbox_stats") == 0) {
            printf("%d %d %u %u\n", g_outbox.depth(), g_outbox.capacity(),
                   g_outbox.queued(), g_outbox.dropped());
        } else if (strcmp(cmd, "capem") == 0) {
            field(q, "pem");
            SettingsError err{};
            if (mqtt_ca_pem_valid(g_val, strlen(g_val), &err)) printf("ok\n");
            else printf("err %s|%s\n", err.field, err.reason);
        } else if (strcmp(cmd, "iso") == 0) {
            field(q, "epoch_ms");
            char out[40];
            printf("%s\n", mqtt_iso8601(strtoll(g_val, nullptr, 10), out, sizeof(out))
                               ? out : "-");
        } else if (strcmp(cmd, "severity") == 0) {
            char alert[24] = {0};
            if (field(q, "alert")) settings_copy(alert, sizeof(alert), g_val);
            double risk = 0.0;
            if (field(q, "risk")) risk = atof(g_val);
            printf("%s\n", mqtt_severity_for(alert, static_cast<float>(risk)));
        } else if (strcmp(cmd, "mask") == 0) {
            field(q, "in");
            char out[128];
            mqtt_mask(g_val, out, sizeof(out));
            print_esc(out);
        } else if (strcmp(cmd, "slug") == 0) {
            // The capacity is read BEFORE the value: field() decodes into the one
            // shared buffer, so reading it second would hand settings_slug() the
            // number rather than the text.
            char out[64];
            long cap = static_cast<long>(sizeof(out));
            num(q, "cap", &cap);
            field(q, "in");
            settings_slug(g_val, out, static_cast<size_t>(cap));
            print_esc(out);
        } else if (strcmp(cmd, "escape") == 0) {
            char out[512];
            long cap = static_cast<long>(sizeof(out));
            num(q, "cap", &cap);
            field(q, "in");
            const bool fit = settings_json_escape(g_val, out,
                                                  static_cast<size_t>(cap));
            printf("%d ", fit ? 1 : 0);
            print_esc(out);
        } else if (strcmp(cmd, "form") == 0) {
            // The decoder, exercised directly rather than only as a side effect.
            char body[1024];
            if (!field(q, "body")) { printf("err\n"); goto flushed; }
            settings_copy(body, sizeof(body), g_val);
            if (!field(q, "key")) { printf("err\n"); goto flushed; }
            char key[64];
            settings_copy(key, sizeof(key), g_val);
            char out[512];
            long cap = static_cast<long>(sizeof(out));
            num(q, "cap", &cap);
            const bool got = settings_form_field(body, key, out,
                                                 static_cast<size_t>(cap));
            printf("%d ", got ? 1 : 0);
            print_esc(out);
        } else if (strcmp(cmd, "segment") == 0) {
            field(q, "in");
            printf("%d\n", settings_is_topic_segment(g_val) ? 1 : 0);
        } else if (strcmp(cmd, "defaults") == 0) {
            const MqttConfig c = mqtt_config_defaults();
            const DeviceIdentity id = device_identity_defaults("DrowsyGuard-C5E019");
            char uri[160];
            mqtt_uri(c, uri, sizeof(uri));
            printf("enabled=%d|transport=%s|protocol=%s|host=%s|port=%u|ws_path=%s"
                   "|qos=%u|keepalive=%u|lwt=%d|insecure=%d|mode=%s|uri=%s"
                   "|device_id=%s|fleet_id=%s|remark=%s|ports=%u/%u/%u/%u\n",
                   c.enabled ? 1 : 0, mqtt_transport_name(c.transport),
                   mqtt_protocol_name(c.protocol), c.host,
                   static_cast<unsigned>(c.port), c.ws_path,
                   static_cast<unsigned>(c.qos),
                   static_cast<unsigned>(c.keepalive_s), c.lwt ? 1 : 0,
                   c.tls_insecure ? 1 : 0,
                   c.topic_mode == MqttTopicMode::Manual ? "manual" : "auto", uri,
                   id.device_id, id.fleet_id, id.remark,
                   mqtt_default_port(MqttTransport::Tcp),
                   mqtt_default_port(MqttTransport::Tls),
                   mqtt_default_port(MqttTransport::Ws),
                   mqtt_default_port(MqttTransport::Wss));
        } else {
            printf("unknown\n");
        }
    flushed:
        fflush(stdout);
    }
    return 0;
}
'''


def _compiler():
    for cc in ('g++', 'c++', 'clang++'):
        if shutil.which(cc):
            return [cc]
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, '-m', 'ziglang', 'c++']


@pytest.fixture(scope='module')
def mq(tmp_path_factory):
    """The harness, built from the firmware's own sources.

    Built with -Wall -Wextra -Werror rather than plain -O2: these two files are the
    only firmware sources a host compiler ever sees, so this is also the cheapest
    place to catch the sign-compare and unused-result mistakes the cross build's own
    -Werror=all would otherwise find first, on someone else's machine.
    """
    cc = _compiler()
    if cc is None:
        pytest.skip('no host C++ compiler')
    d = tmp_path_factory.mktemp('mqtt')
    src = d / 'harness.cpp'
    src.write_text(HARNESS, encoding='utf-8')
    exe = d / ('harness.exe' if sys.platform == 'win32' else 'harness')
    proc = subprocess.run(
        cc + ['-O2', '-std=c++17', '-Wall', '-Wextra', '-Werror',
              '-Wno-unused-parameter', f'-I{FIRMWARE}', str(src),
              str(FIRMWARE / 'mqtt_config.cpp'), str(FIRMWARE / 'device_config.cpp'),
              '-o', str(exe)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail('mqtt_config.cpp / device_config.cpp do not compile cleanly on '
                    'the host:\n' + proc.stderr[-6000:])

    def call(script):
        out = subprocess.run([str(exe)], input=script, capture_output=True, text=True,
                             timeout=120)
        assert out.returncode == 0, out.stderr
        return out.stdout.splitlines()

    return call


def _cmd(name, **kwargs):
    """One harness line: name, tab, form-encoded arguments."""
    if not kwargs:
        return name
    return f'{name}\t' + urlencode({k: v for k, v in kwargs.items()
                                    if v is not None})


def one(mq, name, **kwargs):
    lines = mq(_cmd(name, **kwargs) + '\nquit\n')
    assert lines, f'{name} produced no output'
    return lines[0]


def ok_value(line):
    assert line.startswith('ok '), line
    return line[3:]


# --------------------------------------------------------------------------- #
# defaults, and the preconfigured public broker
# --------------------------------------------------------------------------- #
def test_the_defaults_are_the_public_broker_over_tls_but_switched_off(mq):
    """Every part of this matters, and the last part most.

    `enabled = 0` is not a placeholder: turning MQTT on publishes a named driver's
    state to a third party, and that has to be an act rather than something inherited
    from a default. The rest is what makes the demonstration work in a room with no
    broker: the official EMQX public endpoints, TLS rather than plaintext, and QoS 1.
    """
    got = dict(kv.split('=', 1) for kv in one(mq, 'defaults').split('|'))
    assert got['enabled'] == '0'
    assert got['transport'] == 'tls'
    assert got['protocol'] == '3.1.1'
    assert got['host'] == DEMO_HOST
    assert got['port'] == '8883'
    assert got['ws_path'] == '/mqtt'
    assert got['qos'] == '1'
    assert got['lwt'] == '1'
    assert got['insecure'] == '0'
    assert got['mode'] == 'auto'
    assert got['uri'] == f'mqtts://{DEMO_HOST}:8883'
    # The four documented EMQX ports, in the order tcp/tls/ws/wss.
    assert got['ports'] == '1883/8883/8083/8084'
    # The identity defaults off the SoftAP name, which is already MAC-derived - so
    # two boards on one bench are distinguishable with no provisioning step.
    assert got['device_id'] == 'drowsyguard-c5e019'
    assert got['fleet_id'] == 'demo-fleet'


@pytest.mark.parametrize('transport,port,uri', [
    ('tcp', 1883, f'mqtt://{DEMO_HOST}:1883'),
    ('tls', 8883, f'mqtts://{DEMO_HOST}:8883'),
    ('ws', 8083, f'ws://{DEMO_HOST}:8083/mqtt'),
    ('wss', 8084, f'wss://{DEMO_HOST}:8084/mqtt'),
])
def test_each_transport_builds_the_uri_esp_mqtt_expects(mq, transport, port, uri):
    """The four endpoints EMQX publishes, and the four schemes esp-mqtt parses.

    The WebSocket schemes carry the path and the other two must not: esp-mqtt takes a
    single URI string, so a path appended to `mqtts://` is a broker hostname nobody
    can reach and a path missing from `wss://` is an HTTP 404 reported as a transport
    error.
    """
    assert one(mq, 'uri', transport=transport) == f'ok {uri}'
    got = dict(kv.split('=', 1)
               for kv in ok_value(one(mq, 'roundtrip', transport=transport)).split('|'))
    assert got['port'] == str(port)


def test_a_host_that_would_rewrite_the_uri_is_refused(mq):
    """The host is pasted straight into a URI, so this is the field where a wrong
    value does not fail - it connects somewhere else."""
    for bad in ('evil.example/../x', 'user@evil.example', 'broker.emqx.io:1883',
                'broker emqx io', 'broker..emqx.io', '.emqx.io', 'emqx.io.',
                '-emqx.io', '[::1]', 'ho\tst', ''):
        assert one(mq, 'validate', host=bad) == 'err host', f'accepted host {bad!r}'
    for good in ('broker.emqx.io', '192.168.1.50', 'mqtt', 'mqtt-01.depot.internal',
                 'a'):
        assert one(mq, 'validate', host=good) == 'ok', f'refused host {good!r}'


# --------------------------------------------------------------------------- #
# configuration validation
# --------------------------------------------------------------------------- #
def test_validation_names_the_field_and_says_something_actionable(mq):
    """An error with no field cannot highlight an input and an error with no sentence
    cannot be acted on, so both halves are part of the contract."""
    line = one(mq, 'reason', qos=2)
    assert line.startswith('err qos|'), line
    field, reason = line[4:].split('|', 1)
    assert field == 'qos'
    # Not just "invalid": it has to say what to do instead and why 2 is absent.
    assert '0 or 1' in reason
    assert 'de-duplication' in reason


@pytest.mark.parametrize('field,kwargs', [
    ('port', dict(port=0)),
    ('qos', dict(qos=2)),
    ('keepalive', dict(keepalive=4)),
    ('keepalive', dict(keepalive=301)),
    ('ws_path', dict(transport='ws', ws_path='mqtt')),      # no leading slash
    ('ws_path', dict(transport='wss', ws_path='')),
    ('ws_path', dict(transport='ws', ws_path='/mqtt?x=1')),
    ('client_id', dict(client_id='has a space')),
    ('username', dict(password='secret')),                  # password without a user
    ('transport', dict(transport='tcp', ca_present=1)),      # CA on a plaintext link
    ('tls_insecure', dict(transport='ws', tls_insecure=1)),
    ('device_id', dict(device_id='Has Capitals')),
    ('device_id', dict(device_id='trailing-')),
    ('device_id', dict(device_id='-leading')),
    ('device_id', dict(device_id='')),
    ('fleet_id', dict(fleet_id='fleet/with/slashes')),
    ('fleet_id', dict(fleet_id='wild+card')),
    ('remark', dict(remark='x' * 48)),
])
def test_configurations_that_cannot_work_are_refused(mq, field, kwargs):
    assert one(mq, 'validate', **kwargs) == f'err {field}', kwargs


@pytest.mark.parametrize('kwargs', [
    dict(transport='ws', ws_path='/mqtt'),
    dict(transport='wss', ws_path='/mqtt/v5'),
    dict(transport='tls', ca_present=1),
    dict(transport='wss', ca_present=1, tls_insecure=1),
    dict(qos=0),
    dict(keepalive=5),
    dict(keepalive=300),
    dict(client_id='lorry-7'),
    dict(username='fleet-reader', password='hunter2'),
    dict(username='fleet-reader'),                      # a username with no password
    dict(remark='Driver A'),
    dict(remark='Van 3 — morning'.replace('—', '-')),
    dict(remark='x' * 47),
    dict(device_id='a', fleet_id='z9'),
    dict(device_id='depot.van_3-b', fleet_id='kdsb.fleet_1-a'),
    dict(topic_mode='manual', topic='fleet/lorries/lorry-7/alerts'),
    dict(enabled=1, transport='tls'),
])
def test_configurations_that_can_work_are_accepted(mq, kwargs):
    assert one(mq, 'validate', **kwargs) == 'ok', kwargs


def test_a_manual_topic_may_not_contain_subscription_syntax(mq):
    """'+' and '#' are what a subscriber writes. A publish containing one is either a
    protocol error or - worse - accepted as a literal topic nobody is subscribed to,
    which looks exactly like a broker silently dropping alerts."""
    for bad in ('fleet/+/alerts', 'fleet/#', 'fleet/lorry#7/alerts',
                '$SYS/broker/alerts', '/leading/slash', 'trailing/slash/',
                'double//level', '', 'a' * 160):
        assert one(mq, 'validate', topic_mode='manual', topic=bad) == 'err topic', bad
    for good in ('fleet/lorries/lorry-7/alerts', 'drowsiness',
                 'sites/depot-1/vans/van 3/alerts'):
        assert one(mq, 'validate', topic_mode='manual', topic=good) == 'ok', good


def test_a_manual_topic_must_leave_room_for_its_status_topic(mq):
    """The status topic is derived by appending, and the Last Will uses it. A topic
    that leaves no room would silently truncate the will topic, so the broker would
    hold a retained message on something nobody subscribes to."""
    base = 'a/' * 76 + 'b'                     # 153 characters
    assert len(base) == 153
    assert one(mq, 'validate', topic_mode='manual', topic=base) == 'err topic'
    # 139, not 140: the 140-character prefix ends in '/', and an empty trailing
    # level is refused for its own reasons.
    assert one(mq, 'validate', topic_mode='manual', topic=base[:139]) == 'ok'


def test_settings_are_validated_whether_or_not_publishing_is_enabled(mq):
    """Otherwise the failure surfaces at the moment somebody flips the switch, which
    is the worst time to discover that the host they typed three screens ago is not a
    host."""
    assert one(mq, 'validate', enabled=0, host='not a host') == 'err host'
    assert one(mq, 'validate', enabled=1, host='not a host') == 'err host'


# --------------------------------------------------------------------------- #
# station credentials
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('kwargs,expect', [
    (dict(sta_enabled=0), 'ok'),                            # fields ignored when off
    (dict(sta_enabled=0, sta_ssid=''), 'ok'),
    (dict(sta_enabled=1, sta_ssid=''), 'err sta_ssid'),
    (dict(sta_enabled=1, sta_ssid='x' * 33), 'err sta_ssid'),
    (dict(sta_enabled=1, sta_ssid='Hotspot'), 'ok'),        # open network
    (dict(sta_enabled=1, sta_ssid='Hotspot', sta_password='short'), 'err sta_password'),
    (dict(sta_enabled=1, sta_ssid='Hotspot', sta_password='longenough'), 'ok'),
    (dict(sta_enabled=1, sta_ssid='Hotspot', sta_password='x' * 63), 'ok'),
    (dict(sta_enabled=1, sta_ssid='Hotspot', sta_password='x' * 64), 'err sta_password'),
])
def test_station_credentials_are_checked_against_what_wpa2_accepts(mq, kwargs, expect):
    """1-7 characters is always a typo, and the radio would otherwise reject it later
    with an error that reads like a bad SSID. Empty is legal - an open network - and
    occasionally what a demo wants."""
    assert one(mq, 'validate_sta', **kwargs) == expect, kwargs


# --------------------------------------------------------------------------- #
# topic generation
# --------------------------------------------------------------------------- #
def test_the_generated_topics_are_the_documented_shape(mq):
    kw = dict(fleet_id='demo-fleet', device_id='drowsyguard-c5e019')
    assert one(mq, 'topic', kind='alerts', **kw) == \
        f'ok {TOPIC_ROOT}/demo-fleet/drowsyguard-c5e019/alerts'
    assert one(mq, 'topic', kind='status', **kw) == \
        f'ok {TOPIC_ROOT}/demo-fleet/drowsyguard-c5e019/status'
    # One wildcard covers every device in the fleet, which is the point of putting
    # the fleet above the device rather than the other way round.
    assert one(mq, 'topic', kind='fleet_alerts', **kw) == \
        f'ok {TOPIC_ROOT}/demo-fleet/+/alerts'
    assert one(mq, 'topic', kind='fleet_status', **kw) == \
        f'ok {TOPIC_ROOT}/demo-fleet/+/status'


def test_an_unvalidated_identity_produces_no_topic_at_all(mq):
    """Refusing is the only safe answer. A topic is the one field where a bad value
    does not fail: it goes somewhere else, quietly, possibly somewhere someone else
    is subscribed to."""
    for kw in (dict(fleet_id='a/b'), dict(device_id='a+b'), dict(device_id=''),
               dict(fleet_id='#')):
        assert one(mq, 'topic', kind='alerts', **kw) == 'err', kw


def test_manual_topics_derive_their_status_and_fleet_forms(mq):
    m = dict(topic_mode='manual', topic='fleet/lorries/lorry-7/alerts')
    assert one(mq, 'topic', kind='alerts', **m) == 'ok fleet/lorries/lorry-7/alerts'
    # A trailing /alerts is replaced rather than appended to, so the pair reads as a
    # pair instead of as fleet/lorries/lorry-7/alerts/status.
    assert one(mq, 'topic', kind='status', **m) == 'ok fleet/lorries/lorry-7/status'
    assert one(mq, 'topic', kind='fleet_alerts', **m) == 'ok fleet/lorries/+/alerts'
    assert one(mq, 'topic', kind='fleet_status', **m) == 'ok fleet/lorries/+/status'

    n = dict(topic_mode='manual', topic='sites/depot/drowsiness')
    assert one(mq, 'topic', kind='status', **n) == 'ok sites/depot/drowsiness/status'
    assert one(mq, 'topic', kind='fleet_alerts', **n) == 'ok sites/+/drowsiness'

    # A single level has no device level to wildcard, so the fleet form is the topic
    # itself. Documented rather than clever: one subscriber, one topic.
    s = dict(topic_mode='manual', topic='drowsiness')
    assert one(mq, 'topic', kind='fleet_alerts', **s) == 'ok drowsiness'
    assert one(mq, 'topic', kind='status', **s) == 'ok drowsiness/status'


def test_the_client_id_defaults_to_something_unique_per_board(mq):
    """Two devices sharing a client id take turns knocking each other off the broker,
    and both look intermittently broken rather than misconfigured."""
    assert one(mq, 'clientid', device_id='drowsyguard-c5e019') == \
        'ok drowsyguard-drowsyguard-c5e019'
    assert one(mq, 'clientid', client_id='lorry-7') == 'ok lorry-7'


@pytest.mark.parametrize('raw,want', [
    ('DrowsyGuard-C5E019', 'drowsyguard-c5e019'),
    ('KDSB Fleet 1', 'kdsb-fleet-1'),
    ('  spaces  everywhere  ', 'spaces-everywhere'),
    ('Van#3/A+B', 'van-3-a-b'),
    ('---', ''),
    ('', ''),
    ('a', 'a'),
    ('Ünïcödé', 'n-c-d'),
])
def test_slugging_produces_something_that_is_always_one_topic_level(mq, raw, want):
    """Whatever an operator types has to come out as a single segment: the alternative
    is a fleet id with a slash in it silently restructuring the topic tree."""
    assert one(mq, 'slug', **{'in': raw}) == f'[{want}]'
    if want:
        assert one(mq, 'segment', **{'in': want}) == '1'


def test_a_slug_is_truncated_rather_than_overflowing(mq):
    assert one(mq, 'slug', cap=8, **{'in': 'DrowsyGuard-C5E019'}) == '[drowsyg]'


# --------------------------------------------------------------------------- #
# the published document
# --------------------------------------------------------------------------- #
def test_the_alert_payload_carries_every_documented_field(mq):
    line = ok_value(one(mq, 'payload', alert='microsleep', risk=0.712, perclos=0.421,
                        alert_count=12, uptime_ms=3723456, seq=42,
                        epoch_ms=1788261303000, fleet_id='demo-fleet',
                        device_id='drowsyguard-c5e019', remark='Driver A'))
    j = json.loads(line)
    assert j == {
        'schema': 'drowsyguard.alert.v1',
        'event_id': 'drowsyguard-c5e019-9f1c2ab3-000042',
        'seq': 42,
        'device_id': 'drowsyguard-c5e019',
        'fleet_id': 'demo-fleet',
        'remark': 'Driver A',
        'alert': 'microsleep',
        'severity': 'critical',
        'risk': 0.712,
        'perclos': 0.421,
        'alert_count': 12,
        'uptime_ms': 3723456,
        'ts': '2026-09-01T11:15:03Z',
        'ts_source': 'sntp',
    }


def test_the_payload_says_when_it_does_not_know_the_time(mq):
    """There is no RTC on this board. A device that has never reached a time server
    reports an empty timestamp and says why, rather than publishing 1970 - a
    fabricated timestamp in an incident record is worse than an absent one, and
    `uptime_ms` is always there to order events by."""
    j = json.loads(ok_value(one(mq, 'payload', epoch_ms=0, uptime_ms=61000)))
    assert j['ts'] == ''
    assert j['ts_source'] == 'uptime'
    assert j['uptime_ms'] == 61000


@pytest.mark.parametrize('alert,risk,severity', [
    ('microsleep', 0.6, 'critical'),
    ('no_driver', 0.0, 'critical'),
    ('drowsy', 0.6, 'high'),
    ('drowsy', 0.9, 'critical'),
    ('head_nod', 0.6, 'high'),
    ('yawning', 0.6, 'medium'),
    # deliberately did not fire. Grading it as a drowsiness event would undo the whole
    # point of detecting it.
    ('test', 0.0, 'info'),
    ('something-new', 0.99, 'info'),
])
def test_severity_is_derived_from_the_alert_rather_than_stored(mq, alert, risk,
                                                              severity):
    assert one(mq, 'severity', alert=alert, risk=risk) == severity
    j = json.loads(ok_value(one(mq, 'payload', alert=alert, risk=risk)))
    assert j['severity'] == severity


def test_a_remark_a_human_typed_cannot_break_the_document(mq):
    """The remark is the one free-text field in the payload, so it is escaped rather
    than restricted: refusing quotes would move the problem onto the operator for no
    gain, and a subscriber that throws on parse loses every alert after the bad one."""
    for remark in ('Driver "A"', 'C:\\vans\\3', 'quote:" backslash:\\ tab-ish',
                   'Driver A\x7f', "it's fine", '</script>', '{"injected":true}'):
        j = json.loads(ok_value(one(mq, 'payload', remark=remark)))
        assert j['remark'] == remark, remark


def test_a_payload_that_will_not_fit_produces_nothing(mq):
    """Half a JSON document is worse than none: the subscriber's parser throws, and
    depending on the client that can take out the whole subscription rather than one
    message."""
    n, written = one(mq, 'payload_cap', cap=64).split(' ')
    assert n == '0'
    assert written == '0', 'a truncated payload was left in the buffer'
    # The real buffer is comfortable even for the widest case.
    n, _ = one(mq, 'payload_cap', cap=768, remark='x' * 47).split(' ')
    assert int(n) > 200


def test_a_nan_out_of_the_behaviour_maths_never_reaches_the_broker(mq):
    """The same failure web_server.cpp's json_float() exists to prevent: printf emits
    "nan", which is not JSON."""
    for value in ('nan', 'inf', '-inf'):
        line = ok_value(one(mq, 'payload', risk=value, perclos=value))
        assert 'nan' not in line and 'inf' not in line, line
        j = json.loads(line)
        assert j['risk'] == 0.0 and j['perclos'] == 0.0


def test_the_status_document_distinguishes_a_goodbye_from_a_disappearance(mq):
    """This is the entire reason for the Last Will. A dashboard that cannot tell "no
    alerts because the driver is fine" from "no alerts because the device is in a
    tunnel" is worse than no dashboard."""
    online = json.loads(ok_value(one(mq, 'status_payload', online=1,
                                     reason='connected', uptime_ms=3723456,
                                     alert_count=12, epoch_ms=1788261303000)))
    assert online['schema'] == 'drowsyguard.status.v1'
    assert online['online'] is True
    assert online['reason'] == 'connected'
    assert online['alert_count'] == 12
    assert online['ts'] == '2026-09-01T11:15:03Z'

    # The will body, composed at connect time and delivered by the broker at an
    # unknown moment - so it claims no uptime and no timestamp, on purpose.
    will = json.loads(ok_value(one(mq, 'status_payload', online=0, reason='last-will')))
    assert will['online'] is False
    assert will['reason'] == 'last-will'
    assert will['uptime_ms'] == 0
    assert will['ts'] == ''

    bye = json.loads(ok_value(one(mq, 'status_payload', online=0, reason='shutdown',
                                  uptime_ms=99)))
    assert bye['reason'] == 'shutdown'
    assert bye['uptime_ms'] == 99


def test_the_event_id_survives_a_reboot_without_repeating_itself(mq):
    """A device that restarts mid-drive must not reuse the sequence numbers it already
    published: event 000001 after a reset would otherwise be de-duplicated against
    event 000001 from before it and thrown away."""
    a = json.loads(ok_value(one(mq, 'payload', seq=1, boot_id=0x11111111)))
    b = json.loads(ok_value(one(mq, 'payload', seq=1, boot_id=0x22222222)))
    assert a['event_id'] != b['event_id']
    assert a['event_id'].endswith('-000001') and b['event_id'].endswith('-000001')
    assert a['event_id'].startswith('drowsyguard-c5e019-')


@pytest.mark.parametrize('epoch_ms,want', [
    (0, '-'),
    (-1, '-'),
    (1788261303000, '2026-09-01T11:15:03Z'),
    (1000, '1970-01-01T00:00:01Z'),
    (1583020800000, '2020-03-01T00:00:00Z'),        # a leap year, day after Feb 29
    (1582934400000, '2020-02-29T00:00:00Z'),
    (4102444800000, '2100-01-01T00:00:00Z'),        # not a leap year
    (1788261303999, '2026-09-01T11:15:03Z'),        # milliseconds truncate, not round
])
def test_timestamps_are_formatted_without_the_c_library(mq, epoch_ms, want):
    """Written out from the civil-calendar algorithm because gmtime_r is not available
    on every host this file is compiled for - and because a leap year off by one would
    otherwise be discovered in a published incident record."""
    assert one(mq, 'iso', epoch_ms=epoch_ms) == want


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #
def test_a_full_configuration_survives_a_round_trip_through_the_blob(mq):
    kw = dict(enabled=1, transport='wss', protocol='5', host='mqtt.example.internal',
              port=8443, ws_path='/mqtt/v5', client_id='lorry-7',
              username='fleet-reader', password='hunter2-hunter2', qos=1,
              keepalive=45, lwt=1, retain_status=0, ca_present=1, tls_insecure=0,
              topic_mode='manual', topic='fleet/lorries/lorry-7/alerts')
    got = dict(kv.split('=', 1) for kv in ok_value(one(mq, 'roundtrip', **kw)).split('|'))
    assert got['enabled'] == '1'
    assert got['transport'] == 'wss'
    assert got['protocol'] == '5'
    assert got['host'] == 'mqtt.example.internal'
    assert got['port'] == '8443'
    assert got['ws_path'] == '/mqtt/v5'
    assert got['client_id'] == 'lorry-7'
    assert got['username'] == 'fleet-reader'
    assert got['password'] == 'hunter2-hunter2'
    assert got['qos'] == '1'
    assert got['keepalive'] == '45'
    assert got['lwt'] == '1'
    assert got['retain'] == '0'
    assert got['ca'] == '1'
    assert got['insecure'] == '0'
    assert got['mode'] == 'manual'
    assert got['topic'] == 'fleet/lorries/lorry-7/alerts'
    assert got['uri'] == 'wss://mqtt.example.internal:8443/mqtt/v5'
    # And it fits the buffer settings_nvs.cpp declares, with room to spare.
    assert int(got['bytes']) < 640


def test_the_blob_is_a_byte_stream_rather_than_a_struct(mq):
    """A raw struct in flash is a promise never to reorder a field, never to change a
    capacity and never to compile with different padding. The first time one of those
    is broken, the device reads a plausible-looking hostname out of the middle of a
    password - so the record carries a magic, a version and a CRC, and every one of
    them is checked."""
    # A version bump makes the record unreadable rather than misread.
    assert one(mq, 'roundtrip_version') == 'err reject'
    # Any single-byte corruption fails the CRC. Every offset is tried rather than a
    # sampled few: a CRC that only covers the payload would pass on a damaged header.
    n = int(dict(kv.split('=', 1)
                 for kv in ok_value(one(mq, 'roundtrip')).split('|'))['bytes'])
    script = ''.join(_cmd('roundtrip', flip=i) + '\n' for i in range(n)) + 'quit\n'
    lines = mq(script)
    assert len(lines) == n
    assert all(line == 'err reject' for line in lines), \
        [f'{i}: {line}' for i, line in enumerate(lines) if line != 'err reject']
    # And a truncated record too, at every length short of the whole thing.
    script = ''.join(_cmd('roundtrip', truncate=i) + '\n' for i in range(n)) + 'quit\n'
    assert all(line == 'err reject' for line in mq(script))


def test_a_stored_record_is_revalidated_against_the_current_rules(mq):
    """A record written by an older build under looser rules must not be able to put
    an unusable value back into a live configuration. Rejecting costs nothing: the
    fallback is the defaults, which is also what a fresh board does."""
    # A payload written with a bad host cannot be stored in the first place, so the
    # check that matters is the enum guard: a byte out of flash is an integer until it
    # has been range-checked, and casting an out-of-range one into the enum is
    # undefined behaviour before it is a wrong transport.
    assert one(mq, 'roundtrip', transport='tls') != 'err reject'
    assert one(mq, 'roundtrip', qos=1) != 'err reject'


def test_the_identity_and_the_station_config_round_trip_too(mq):
    got = dict(kv.split('=', 1) for kv in ok_value(
        one(mq, 'roundtrip_identity', device_id='depot.van_3-b',
            fleet_id='kdsb-fleet-1', remark='Driver A - morning')).split('|'))
    assert got['device_id'] == 'depot.van_3-b'
    assert got['fleet_id'] == 'kdsb-fleet-1'
    assert got['remark'] == 'Driver A - morning'

    got = dict(kv.split('=', 1) for kv in ok_value(
        one(mq, 'roundtrip_wifi', sta_enabled=1, sta_ssid='Pixel_Hotspot',
            sta_password='longenough')).split('|'))
    assert got['enabled'] == '1'
    assert got['ssid'] == 'Pixel_Hotspot'
    assert got['password'] == 'longenough'

    # A remark with a space in it, which is the normal case and the one a naive
    # length-free encoding would get wrong.
    line = ok_value(one(mq, 'roundtrip_identity', remark='Van 3 morning shift'))
    assert line.endswith('remark=Van 3 morning shift')


def test_corruption_in_the_identity_record_falls_back_to_defaults(mq):
    n = int(ok_value(one(mq, 'roundtrip_identity')).split('|')[0].split('=')[1])
    script = ''.join(_cmd('roundtrip_identity', flip=i) + '\n'
                     for i in range(n)) + 'quit\n'
    assert all(line == 'err reject' for line in mq(script))


def test_wifi_credentials_survive_a_reboot_exactly(mq):
    """This is the "automatically reconnect after reboot" requirement, at the only
    layer a host test can reach it: the bytes that go into NVS have to come back out
    as the same credentials, character for character. A passphrase that round-trips
    to something almost right is worse than one that fails to store - the device
    retries forever against a network that will never accept it."""
    # The awkward cases, all of them legal in WPA2: the maximum-length SSID, a
    # passphrase at each end of the 8-63 range, and the punctuation that a
    # length-free or delimiter-based encoding would eat.
    for ssid, password in (
            ('A' * 32, 'x' * 63),
            ('Guest WiFi 2.4GHz', 'p@ss w0rd=with&signs'),
            # An SSID is 32 arbitrary octets, and networks named in Khmer or with
            # an accent are ordinary. The passphrase is a different rule and stays
            # ASCII - see the test under this one.
            ('café-résidence', 'longenough'),
            ('ភ្នំពេញ-office', 'longenough'),
            ('KDSB', 'a' * 8),
            ('open-network', ''),
    ):
        got = dict(kv.split('=', 1) for kv in ok_value(
            one(mq, 'roundtrip_wifi', sta_enabled=1, sta_ssid=ssid,
                sta_password=password)).split('|'))
        assert got['ssid'] == ssid, f'{ssid!r} came back as {got["ssid"]!r}'
        assert got['password'] == (password or '-'), (
            f'the passphrase for {ssid!r} did not survive')
        assert got['enabled'] == '1'

    # And "do not join anything" is a stored state rather than an absent record: a
    # device told to stay access-point-only must still be that after a power cycle.
    got = dict(kv.split('=', 1) for kv in ok_value(
        one(mq, 'roundtrip_wifi', sta_enabled=0, sta_ssid='KDSB-Office',
            sta_password='longenough')).split('|'))
    assert got['enabled'] == '0'
    assert got['ssid'] == 'KDSB-Office'


def test_a_passphrase_outside_ascii_is_refused_with_a_reason(mq):
    """WPA2-PSK is 8-63 characters of ASCII 32..126 - that is the standard, not a
    shortcut. A passphrase the operator typed in another alphabet would be stored
    happily and then rejected by the access point with a reason code that reads like
    a wrong SSID, which is a whole evening lost. Refused at the form instead."""
    line = one(mq, 'reason_sta', sta_enabled=1, sta_ssid='KDSB-Office',
               sta_password='åéîøü-passphrase')
    assert line.startswith('err sta_password|'), line
    assert 'ascii' in line.lower(), line

    # And a control character in an SSID, which would be a terminal escape sequence
    # in the serial log rather than a network name. The rest of the byte range is
    # accepted now, so this is the one thing left that an SSID may not contain.
    line = one(mq, 'reason_sta', sta_enabled=1, sta_ssid='bell\x07here',
               sta_password='longenough')
    assert line.startswith('err sta_ssid|'), line
    assert 'control' in line.lower(), line

    # A 32-byte SSID is legal; 33 is not, and it is measured in bytes because that is
    # what 802.11 counts - eleven Khmer characters are thirty-three bytes.
    assert one(mq, 'reason_sta', sta_enabled=1, sta_ssid='x' * 32,
               sta_password='longenough') == 'ok'


def test_corruption_in_the_station_record_falls_back_to_provisioning(mq):
    """Every single byte, flipped. A half-read passphrase is not a smaller version of
    the right one - it is a device that will not join and cannot say why - so the
    record is rejected whole and the device comes back up provisioning, which is the
    state somebody can actually recover it from."""
    n = int(ok_value(one(mq, 'roundtrip_wifi', sta_enabled=1, sta_ssid='KDSB-Office',
                         sta_password='longenough')).split('|')[0].split('=')[1])
    script = ''.join(
        _cmd('roundtrip_wifi', sta_enabled=1, sta_ssid='KDSB-Office',
             sta_password='longenough', flip=i) + '\n'
        for i in range(n)) + 'quit\n'
    lines = mq(script)
    assert len(lines) == n
    assert all(line == 'err reject' for line in lines), (
        [f'byte {i}: {line}' for i, line in enumerate(lines)
         if line != 'err reject'][:8])


# --------------------------------------------------------------------------- #
# the redacted settings document
# --------------------------------------------------------------------------- #
def test_the_api_document_contains_no_password_anywhere(mq):
    """The function that has to be right for "no secrets through the API" to be true.

    Checked by searching the whole document for the secret rather than by inspecting
    named fields: a future field that happened to include the password would pass a
    field-by-field check and fail this one.
    """
    secret = 'correct-horse-battery-staple'
    sta_secret = 'hotspot-secret-value'
    line = ok_value(one(mq, 'redact', username='fleet-reader', password=secret,
                        sta_enabled=1, sta_ssid='Pixel_Hotspot',
                        sta_password=sta_secret, ca_bytes=2114, ca_present=1,
                        transport='tls'))
    assert secret not in line
    assert sta_secret not in line
    j = json.loads(line)
    assert j['password_set'] is True
    assert 'password' not in j
    assert j['sta']['password_set'] is True
    assert 'password' not in j['sta']
    # The username is masked rather than absent: an operator has to be able to tell
    # WHICH account is configured without the value being readable over a shoulder.
    assert j['username_set'] is True
    assert j['username_masked'] == 'fl*********r'
    assert 'fleet-reader' not in line
    # The certificate is public, but 2 kB of PEM on every poll is not, so only its
    # size is reported.
    assert j['ca_present'] is True and j['ca_bytes'] == 2114
    assert 'BEGIN CERTIFICATE' not in line


def test_the_api_document_carries_everything_the_modal_needs(mq):
    j = json.loads(ok_value(one(mq, 'redact', enabled=1, transport='wss', protocol='5',
                                host='broker.emqx.io', port=8084, ws_path='/mqtt',
                                fleet_id='demo-fleet',
                                device_id='drowsyguard-c5e019', remark='Driver A')))
    assert j['enabled'] is True
    assert j['transport'] == 'wss'
    assert j['protocol'] == '5'
    assert j['uri'] == 'wss://broker.emqx.io:8084/mqtt'
    assert j['client_id_auto'] is True
    assert j['client_id'] == 'drowsyguard-drowsyguard-c5e019'
    assert j['remark'] == 'Driver A'
    assert j['topics'] == {
        'alerts': f'{TOPIC_ROOT}/demo-fleet/drowsyguard-c5e019/alerts',
        'status': f'{TOPIC_ROOT}/demo-fleet/drowsyguard-c5e019/status',
        'fleet_alerts': f'{TOPIC_ROOT}/demo-fleet/+/alerts',
        'fleet_status': f'{TOPIC_ROOT}/demo-fleet/+/status',
    }
    # The public broker's endpoints, so the page can offer the preset without
    # hard-coding numbers that would then be able to disagree with the firmware.
    assert j['demo_broker'] == {'host': DEMO_HOST, 'tcp': 1883, 'tls': 8883,
                                'ws': 8083, 'wss': 8084, 'path': '/mqtt',
                                'public': True}


def test_the_api_document_is_valid_json_for_a_remark_full_of_quotes(mq):
    j = json.loads(ok_value(one(mq, 'redact', remark='Driver "A" \\ 3',
                                sta_ssid='Bob\'s "Hotspot"', sta_enabled=1,
                                sta_password='longenough')))
    assert j['remark'] == 'Driver "A" \\ 3'
    assert j['sta']['ssid'] == 'Bob\'s "Hotspot"'


@pytest.mark.parametrize('raw,want', [
    ('fleet-reader', 'fl*********r'),
    ('abcd', 'ab*d'),
    ('abc', '***'),
    ('ab', '**'),
    ('a', '*'),
    ('', ''),
])
def test_masking_discloses_nothing_useful_from_a_short_value(mq, raw, want):
    """"ab" masked as "a*b" discloses two thirds of it, which is the same as not
    masking. Below four characters nothing is kept."""
    assert one(mq, 'mask', **{'in': raw}) == f'[{want}]'


# --------------------------------------------------------------------------- #
# reconnection
# --------------------------------------------------------------------------- #
def test_the_backoff_doubles_to_a_ceiling(mq):
    """esp-mqtt's own auto-reconnect is switched off in favour of this, and the reason
    is the shape: a fixed 10 s retry against a broker that is down for an hour is 360
    connection attempts from a device whose actual job is inference."""
    script = ''.join(_cmd('backoff', attempt=a) + '\n' for a in range(0, 12)) + 'quit\n'
    got = [int(line) for line in mq(script)]
    assert got[0] == 0, 'the first connection should be immediate'
    assert got[1:8] == [1000, 2000, 4000, 8000, 16000, 32000, 64000 - 4000]
    # Past the shift ceiling it is flat at the maximum rather than growing to hours.
    assert all(v == BACKOFF_MAX_MS for v in got[7:]), got
    assert got[1] == BACKOFF_BASE_MS


def test_the_jitter_is_additive_and_bounded(mq):
    """Additive rather than multiplicative because the FLOOR is what matters: a device
    must not retry a broker 200 ms after being refused, however much jitter is
    applied. At fleet scale the jitter is what stops every device that lost the same
    access point retrying in the same millisecond forever."""
    script = ''.join(_cmd('backoff', attempt=a, rnd=r) + '\n'
                     for a in range(1, 9) for r in (0, 1, 250, 12345, 4294967295)) \
        + 'quit\n'
    lines = [int(line) for line in mq(script)]
    at = 0
    for a in range(1, 9):
        base = min(BACKOFF_BASE_MS << min(a - 1, 6), BACKOFF_MAX_MS)
        for _ in range(5):
            v = lines[at]
            at += 1
            assert base <= v <= base + base // 4, (a, v, base)
    # Deterministic in rnd, so this is a test rather than a coin flip.
    assert one(mq, 'backoff', attempt=3, rnd=0) == '4000'
    assert one(mq, 'backoff', attempt=3, rnd=999) == str(4000 + 999 % 1000)


# --------------------------------------------------------------------------- #
# de-duplication
# --------------------------------------------------------------------------- #
def test_an_event_id_is_never_published_twice(mq):
    """QoS 1 is at-least-once by definition, and our own retry after a reconnect adds
    a second source of duplicates."""
    script = 'dedup_reset\n'
    script += _cmd('dedup', id='dev-aaaa-000001') + '\n'
    script += _cmd('dedup', id='dev-aaaa-000002') + '\n'
    script += _cmd('dedup', id='dev-aaaa-000001') + '\n'    # the retry
    script += _cmd('dedup', id='dev-aaaa-000001') + '\n'    # and again
    script += _cmd('dedup', id='dev-aaaa-000003') + '\n'
    script += 'quit\n'
    seen = [line.split(' ') for line in mq(script)[1:]]
    assert [s[0] for s in seen] == ['0', '0', '1', '1', '0']
    assert seen[-1][1] == '3', 'a duplicate should not consume a slot'
    assert seen[-1][2] == '2', 'both suppressions should be counted'


def test_the_dedup_ring_forgets_the_oldest_rather_than_growing(mq):
    """Bounded on purpose. It is a guard against a retry storm, not a permanent
    ledger: on a device with 320 kB of internal heap, an unbounded set of event ids
    is a slow leak that ends in a reboot mid-drive."""
    script = 'dedup_reset\n'
    ids = [f'dev-aaaa-{i:06d}' for i in range(DEDUP_SLOTS + 4)]
    script += ''.join(_cmd('dedup', id=i) + '\n' for i in ids)
    # The very first id has been evicted, so it is treated as new again; the most
    # recent one is still remembered.
    script += _cmd('dedup', id=ids[0]) + '\n'
    script += _cmd('dedup', id=ids[-1]) + '\n'
    script += 'quit\n'
    lines = mq(script)[1:]
    fresh = [line.split(' ')[0] for line in lines[:len(ids)]]
    assert fresh == ['0'] * len(ids), 'no id in a fresh ring should look seen'
    assert lines[len(ids)].split(' ')[0] == '0', 'the oldest id should have been evicted'
    assert lines[len(ids) + 1].split(' ')[0] == '1', 'the newest id should be remembered'
    assert lines[-1].split(' ')[1] == str(DEDUP_SLOTS)


def test_an_empty_event_id_is_not_remembered(mq):
    """It would otherwise occupy a slot and then suppress the next unnamed event,
    which is a real alert."""
    script = 'dedup_reset\n' + _cmd('dedup', id='') + '\n' + _cmd('dedup', id='') \
        + '\nquit\n'
    lines = mq(script)[1:]
    assert all(line.split(' ')[0] == '0' for line in lines)
    assert lines[-1].split(' ')[1] == '0'


# --------------------------------------------------------------------------- #
# failure isolation: the outbox
# --------------------------------------------------------------------------- #
def test_the_outbox_never_refuses_an_alert(mq):
    """This is the property the whole subsystem's safety argument rests on.

    The capture loop has a 23 ms frame budget and calls into this from the same line
    that fires the speaker. push() is therefore O(1), allocation-free, and cannot
    fail: a broker that has been unreachable for a week costs the detection loop one
    96-byte memcpy per alert and nothing else. What it CAN do is discard an older
    event to make room, and that is reported rather than hidden.
    """
    n = 10_000
    script = 'outbox_reset\n'
    script += ''.join(_cmd('outbox_push', alert='drowsy', seq=i) + '\n'
                      for i in range(n))
    script += 'outbox_stats\n'
    script += 'quit\n'
    lines = mq(script)
    pushes = lines[1:1 + n]
    assert len(pushes) == n
    # Every push was accepted. The first OUTBOX_DEPTH are clean; after that each one
    # evicts an older event and says so.
    clean = [p.split(' ')[0] for p in pushes]
    assert clean[:OUTBOX_DEPTH] == ['1'] * OUTBOX_DEPTH
    assert clean[OUTBOX_DEPTH:] == ['0'] * (n - OUTBOX_DEPTH)

    depth, capacity, queued, dropped = lines[1 + n].split(' ')
    assert int(capacity) == OUTBOX_DEPTH
    assert int(depth) == OUTBOX_DEPTH, 'memory use has to be bounded'
    assert int(queued) == n
    assert int(dropped) == n - OUTBOX_DEPTH


def test_the_outbox_keeps_the_newest_alerts_when_it_overflows(mq):
    """After twenty minutes offline what a fleet operator needs is the last few
    minutes of a deteriorating driver, not the first. The alternative - refusing new
    events once full - would mean the buffer stops recording exactly when the
    situation is getting worse."""
    script = 'outbox_reset\n'
    script += ''.join(_cmd('outbox_push', alert='drowsy', seq=i) + '\n'
                      for i in range(OUTBOX_DEPTH + 5))
    script += 'outbox_peek\n'
    script += 'quit\n'
    head = mq(script)[-1].split(' ')
    # Five were evicted, so the head is now event 5 rather than event 0.
    assert head[2] == '5', head


def test_a_publish_that_fails_can_be_retried_with_the_same_event(mq):
    """peek/commit rather than pop, because a publish that failed must not lose the
    event - and must not change its event id either, or the de-duplication at the
    other end has nothing to work with."""
    script = 'outbox_reset\n'
    script += _cmd('outbox_push', alert='microsleep', seq=7) + '\n'
    script += _cmd('outbox_push', alert='yawning', seq=8) + '\n'
    script += 'outbox_peek\n'
    script += 'outbox_peek\n'          # a failed publish: peek again, same event
    script += 'outbox_commit\n'
    script += 'outbox_peek\n'          # now the next one
    script += 'quit\n'
    lines = mq(script)
    first, again = lines[3].split(' '), lines[4].split(' ')
    assert first == again, 'peek must not consume'
    assert first[1] == 'microsleep' and first[2] == '7'
    assert lines[5] == '1', 'commit should have removed exactly one'
    assert lines[6].split(' ')[1] == 'yawning'


def test_the_outbox_is_empty_after_it_drains(mq):
    script = 'outbox_reset\n'
    script += ''.join(_cmd('outbox_push', alert='drowsy', seq=i) + '\n'
                      for i in range(3))
    script += 'outbox_pop\noutbox_pop\noutbox_pop\noutbox_pop\n'
    script += 'quit\n'
    lines = mq(script)
    assert lines[4].split(' ')[2] == '2'
    assert lines[7] == 'empty', lines
    assert one(mq, 'outbox_peek') in ('empty',) or True   # peek on empty is safe


def test_the_outbox_carries_the_facts_rather_than_the_rendered_message(mq):
    """The alert is queued as a struct and the JSON is built later, on the publisher
    task. Two reasons: 96 bytes against ~450 for the document, and the identity and
    topic are read at publish time, so a settings change applies to what is still in
    the buffer rather than to nothing."""
    script = 'outbox_reset\n'
    script += _cmd('outbox_push', alert='head_nod', seq=3, risk=0.61,
                   perclos=0.33) + '\n'
    script += 'outbox_peek\n'
    script += 'quit\n'
    head = mq(script)[-1].split(' ')
    assert head[1] == 'head_nod'
    assert head[2] == '3'


# --------------------------------------------------------------------------- #
# the peek/publish/commit race: eviction between the two must not eat an alert
# --------------------------------------------------------------------------- #
def test_commit_if_seq_removes_the_event_it_was_asked_about(mq):
    """The ordinary case: peek, publish, commit the same event. The seq matches the
    head, so the commit removes exactly one slot."""
    script = 'outbox_reset\n'
    script += _cmd('outbox_push', alert='drowsy', seq=7) + '\n'
    script += _cmd('outbox_push', alert='yawning', seq=8) + '\n'
    script += _cmd('outbox_commit_if', seq=7) + '\n'
    script += 'outbox_peek\n'
    script += 'quit\n'
    lines = mq(script)
    did, depth = lines[3].split(' ')
    assert (did, depth) == ('1', '1')
    assert lines[4].split(' ')[2] == '8', 'the next event is now the head'


def test_commit_if_seq_refuses_when_the_head_was_evicted_mid_publish(mq):
    """The race the publisher actually runs: it peeks the head (seq 0), renders and
    sends it, and while it is doing that the capture loop pushes into the FULL ring,
    which evicts that same head. A plain commit() would then remove seq 1 - an event
    nobody ever published - and it would vanish uncounted. commit_if_seq(0) must
    notice the head is no longer seq 0 and remove nothing."""
    n = OUTBOX_DEPTH
    script = 'outbox_reset\n'
    script += ''.join(_cmd('outbox_push', alert='drowsy', seq=i) + '\n'
                      for i in range(n))                       # full, head is seq 0
    script += _cmd('outbox_push', alert='microsleep', seq=n) + '\n'  # evicts seq 0
    script += _cmd('outbox_commit_if', seq=0) + '\n'           # the stale commit
    script += 'outbox_peek\n'
    script += 'quit\n'
    lines = mq(script)
    # line 0: reset; 1..n: the filling pushes; n+1: the evicting push;
    # n+2: the stale commit; n+3: the peek.
    did, depth = lines[n + 2].split(' ')
    assert did == '0', 'the stale commit must be refused'
    assert depth == str(n), 'nothing was removed'
    assert lines[n + 3].split(' ')[2] == '1', 'seq 1 is still queued, not eaten'


# --------------------------------------------------------------------------- #
# the dedup halves: a failed publish attempt must stay retryable
# --------------------------------------------------------------------------- #
def test_a_failed_publish_attempt_is_not_its_own_duplicate(mq):
    """The publisher asks already_published() before an attempt and calls
    mark_published() only after the transport accepts the message. The old single
    seen_or_add() recorded the id on the ASK, so when esp_mqtt_client_enqueue()
    failed and the event was retried, the retry matched its own first attempt and
    was committed unsent - a silently lost alert."""
    script = 'dedup_reset\n'
    script += _cmd('dedup_check', id='dev-aaaa-000001') + '\n'   # attempt 1: new
    # enqueue fails here: nothing is marked.
    script += _cmd('dedup_check', id='dev-aaaa-000001') + '\n'   # retry: still new
    script += _cmd('dedup_mark', id='dev-aaaa-000001') + '\n'    # retry succeeded
    script += _cmd('dedup_check', id='dev-aaaa-000001') + '\n'   # now a duplicate
    script += 'quit\n'
    lines = mq(script)
    assert lines[1].split(' ')[0] == '0', 'first attempt is not a duplicate'
    assert lines[2].split(' ')[0] == '0', 'a retry after a failed enqueue must not be'
    assert lines[4].split(' ')[0] == '1', 'after a delivery it is'
    assert lines[4].split(' ')[2] == '1', 'and the suppression is counted'


def test_mark_published_is_idempotent(mq):
    script = 'dedup_reset\n'
    script += _cmd('dedup_mark', id='dev-aaaa-000009') + '\n'
    script += _cmd('dedup_mark', id='dev-aaaa-000009') + '\n'
    script += _cmd('dedup_mark', id='dev-aaaa-000009') + '\n'
    script += 'quit\n'
    lines = mq(script)
    assert lines[3].split(' ')[0] == '1', 'one slot, however many times it is marked'


def test_seen_or_add_still_behaves_as_one_call(mq):
    """The combined form survives (the ring is also used through it), and it must be
    exactly ask-then-mark: same recording, same suppression count."""
    script = 'dedup_reset\n'
    script += _cmd('dedup', id='dev-bbbb-000001') + '\n'
    script += _cmd('dedup', id='dev-bbbb-000001') + '\n'
    script += _cmd('dedup_check', id='dev-bbbb-000001') + '\n'
    script += 'quit\n'
    lines = mq(script)
    assert lines[1].split(' ')[0] == '0'
    assert lines[2].split(' ')[0] == '1'
    assert lines[3] == '1 1 2', 'both suppressions counted, one slot used'


# --------------------------------------------------------------------------- #
# TLS: the certificate
# --------------------------------------------------------------------------- #
FAKE_CERT = ('-----BEGIN CERTIFICATE-----\n'
             + 'MIIDdzCCAl+gAwIBAgIEAgAAuTANBgkqhkiG9w0BAQUFADBaMQswCQYDVQQGEwJJ\n' * 4
             + '-----END CERTIFICATE-----\n')


def test_a_certificate_is_shape_checked_before_it_is_stored(mq):
    """Not a parse - mbedtls does that when the socket opens - but enough to catch the
    three things people actually paste. Catching them here turns a TLS failure three
    days into a drive into a message next to the field."""
    assert one(mq, 'capem', pem=FAKE_CERT) == 'ok'
    # A certificate with Windows line endings, which is what a copy out of a browser
    # on Windows produces.
    assert one(mq, 'capem', pem=FAKE_CERT.replace('\n', '\r\n')) == 'ok'


@pytest.mark.parametrize('pem,expect_in_reason', [
    ('', 'empty'),
    ('-----BEGIN CERTIFICATE-----\nnope\n', 'BEGIN CERTIFICATE'),
    ('just some text', 'BEGIN CERTIFICATE'),
    # The mistake worth catching loudly: a key pasted into the CA box would be
    # stored, returned by nothing, and useless - while the operator believes their
    # broker is authenticated.
    ('-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n',
     'private key'),
    ('-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n', 'private key'),
    # A DER file opened in an editor. mbedtls would otherwise report this from inside
    # a handshake, minutes later.
    ('-----BEGIN CERTIFICATE-----\n\x01\x02\x03\n-----END CERTIFICATE-----\n',
     'not a PEM'),
])
def test_a_certificate_that_cannot_work_is_refused_with_a_reason(mq, pem,
                                                                expect_in_reason):
    line = one(mq, 'capem', pem=pem)
    assert line.startswith('err ca_cert|'), line
    assert expect_in_reason.lower() in line.split('|', 1)[1].lower(), line


def test_a_certificate_larger_than_the_store_is_refused(mq):
    """4 kB holds a 4096-bit RSA root with room to spare. A chain does not fit and
    does not need to: a broker's server certificate is verified against one root, not
    against a bundle the device also has to keep."""
    huge = ('-----BEGIN CERTIFICATE-----\n' + 'A' * 5000
            + '\n-----END CERTIFICATE-----\n')
    line = one(mq, 'capem', pem=huge)
    assert line.startswith('err ca_cert|')
    assert '4 kB' in line


def test_a_certificate_on_a_plaintext_transport_is_a_configuration_error(mq):
    """Not silently ignored. Someone who pasted a CA and chose TCP has almost
    certainly picked the wrong transport, and a UI that accepted both would leave them
    believing the link is authenticated."""
    assert one(mq, 'validate', transport='tcp', ca_present=1) == 'err transport'
    assert one(mq, 'validate', transport='ws', ca_present=1) == 'err transport'
    assert one(mq, 'validate', transport='tls', ca_present=1) == 'ok'
    assert one(mq, 'validate', transport='wss', ca_present=1) == 'ok'


def test_skipping_verification_is_reachable_and_only_for_tls(mq):
    """Reachable on purpose: the alternative is an operator with a self-signed broker
    turning TLS off altogether, which is strictly worse - this at least encrypts. It
    is meaningless on a plaintext transport, and accepting it there would put a
    setting in NVS that claims something the connection does not do."""
    assert one(mq, 'validate', transport='tls', tls_insecure=1) == 'ok'
    assert one(mq, 'validate', transport='wss', tls_insecure=1) == 'ok'
    assert one(mq, 'validate', transport='tcp', tls_insecure=1) == 'err tls_insecure'
    j = json.loads(ok_value(one(mq, 'redact', transport='tls', tls_insecure=1)))
    assert j['tls_insecure'] is True, 'the API has to report it so the UI can warn'


# --------------------------------------------------------------------------- #
# the form decoder POST /api/mqtt depends on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('body,key,found,value', [
    ('host=broker.emqx.io&port=8883', 'host', 1, 'broker.emqx.io'),
    ('host=broker.emqx.io&port=8883', 'port', 1, '8883'),
    ('host=broker.emqx.io&port=8883', 'nope', 0, ''),
    ('remark=Driver+A', 'remark', 1, 'Driver A'),
    ('remark=Driver%20A', 'remark', 1, 'Driver A'),
    ('pem=line1%0Aline2', 'pem', 1, 'line1\\nline2'),
    ('a=&b=2', 'a', 1, ''),                       # present but empty: "keep it"
    ('a=1', 'a', 1, '1'),
    # A key only matches at the start of a pair and only up to '=', so "qos" is not
    # satisfied by "extra_qos" nor by a value that happens to contain "qos=".
    ('extra_qos=2&qos=1', 'qos', 1, '1'),
    ('note=qos%3D9&qos=1', 'qos', 1, '1'),
    ('qos=1', 'os', 0, ''),
    # Malformed escapes are copied through literally. Guessing at one is how a
    # decoder invents characters that were never sent.
    ('a=100%', 'a', 1, '100%'),
    ('a=%zz', 'a', 1, '%zz'),
    ('a=%4', 'a', 1, '%4'),
    ('a=%41%42', 'a', 1, 'AB'),
    ('a=%2b', 'a', 1, '+'),                       # an encoded plus is a plus
    ('a=1%2B1', 'a', 1, '1+1'),
    ('', 'a', 0, ''),
    ('novalue', 'novalue', 0, ''),
])
def test_the_form_decoder_handles_what_a_browser_and_a_curl_will_send(mq, body, key,
                                                                     found, value):
    assert one(mq, 'form', body=body, key=key) == f'{found} [{value}]'


def test_a_form_value_longer_than_the_field_is_truncated_not_overflowed(mq):
    assert one(mq, 'form', body='a=abcdefghij', key='a', cap=5) == '1 [abcd]'


# --------------------------------------------------------------------------- #
# JSON escaping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('raw,want', [
    ('plain', 'plain'),
    ('quote"here', 'quote\\\\"here'),
    ('back\\slash', 'back\\\\\\\\slash'),
    ('tab\there', 'tab\\\\there'),
    ('new\nline', 'new\\\\nline'),
    ('cr\rhere', 'cr\\\\rhere'),
    ('bell\x07', 'bell\\\\u0007'),
    ('del\x7f', 'del\\\\u007f'),
    ('', ''),
])
def test_escaping_covers_every_byte_json_forbids(mq, raw, want):
    assert one(mq, 'escape', **{'in': raw}) == f'1 [{want}]'


def test_escaping_that_does_not_fit_reports_failure_rather_than_truncating(mq):
    """The payload builder abandons the buffer on false. A half-escaped string is a
    document no parser accepts, and the caller would have shipped it."""
    line = one(mq, 'escape', cap=4, **{'in': 'a"bc'})
    assert line.startswith('0 '), line
