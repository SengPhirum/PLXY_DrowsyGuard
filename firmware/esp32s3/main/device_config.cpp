#include "device_config.h"

#include <cstdio>
#include <cstring>

// Nothing from ESP-IDF is included here and nothing should be: the whole file is
// string handling and arithmetic, and tests/test_mqtt_config.py compiles it with the
// host compiler. The moment this needs esp_log.h, the thing that needs logging
// belongs in settings_nvs.cpp instead.

// --- small helpers ---------------------------------------------------------
static bool is_lower_alnum(char c) {
    return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
}

bool settings_is_printable(const char *s, size_t max_len) {
    if (s == nullptr) return false;
    size_t n = 0;
    for (; s[n] != '\0'; ++n) {
        if (n >= max_len) return false;
        const unsigned char c = static_cast<unsigned char>(s[n]);
        if (c < 0x20 || c > 0x7E) return false;
    }
    return true;
}

bool settings_is_topic_segment(const char *s) {
    if (s == nullptr || s[0] == '\0') return false;
    if (!is_lower_alnum(s[0])) return false;          // no leading '-', '_' or '.'
    for (size_t i = 0; s[i] != '\0'; ++i) {
        const char c = s[i];
        if (is_lower_alnum(c) || c == '-' || c == '_' || c == '.') continue;
        return false;
    }
    // A trailing separator would produce "fleet-/device" style topics, which are
    // legal MQTT and unreadable in a subscription list.
    const size_t n = strlen(s);
    const char last = s[n - 1];
    return is_lower_alnum(last);
}

bool settings_copy(char *out, size_t out_cap, const char *in) {
    if (out == nullptr || out_cap == 0) return false;
    if (in == nullptr) { out[0] = '\0'; return true; }
    const size_t n = strlen(in);
    if (n >= out_cap) {
        memcpy(out, in, out_cap - 1);
        out[out_cap - 1] = '\0';
        return false;
    }
    memcpy(out, in, n + 1);
    return true;
}

size_t settings_slug(const char *in, char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return 0;
    out[0] = '\0';
    if (in == nullptr) return 0;

    size_t at = 0;
    bool pending_dash = false;
    for (size_t i = 0; in[i] != '\0'; ++i) {
        char c = in[i];
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
        if (is_lower_alnum(c)) {
            // A separator only becomes a character once something follows it, which
            // is what collapses runs and trims both ends in one pass.
            if (pending_dash && at > 0 && at + 1 < out_cap) out[at++] = '-';
            pending_dash = false;
            if (at + 1 >= out_cap) break;
            out[at++] = c;
        } else if (at > 0) {
            pending_dash = true;
        }
    }
    out[at] = '\0';
    return at;
}

bool settings_json_escape(const char *s, char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return false;
    out[0] = '\0';
    if (s == nullptr) return true;

    size_t at = 0;
    const char *hex = "0123456789abcdef";
    for (size_t i = 0; s[i] != '\0'; ++i) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        char esc[7];
        size_t n = 0;
        switch (c) {
            case '"':  esc[0] = '\\'; esc[1] = '"';  n = 2; break;
            case '\\': esc[0] = '\\'; esc[1] = '\\'; n = 2; break;
            case '\n': esc[0] = '\\'; esc[1] = 'n';  n = 2; break;
            case '\r': esc[0] = '\\'; esc[1] = 'r';  n = 2; break;
            case '\t': esc[0] = '\\'; esc[1] = 't';  n = 2; break;
            default:
                if (c < 0x20 || c == 0x7F) {
                    esc[0] = '\\'; esc[1] = 'u'; esc[2] = '0'; esc[3] = '0';
                    esc[4] = hex[(c >> 4) & 0xF];
                    esc[5] = hex[c & 0xF];
                    n = 6;
                } else {
                    esc[0] = static_cast<char>(c);
                    n = 1;
                }
                break;
        }
        if (at + n + 1 > out_cap) { out[at] = '\0'; return false; }
        memcpy(out + at, esc, n);
        at += n;
    }
    out[at] = '\0';
    return true;
}

// --- form fields -----------------------------------------------------------
static int hex_nibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool settings_form_field(const char *body, const char *key, char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return false;
    out[0] = '\0';
    if (body == nullptr || key == nullptr || key[0] == '\0') return false;

    const size_t klen = strlen(key);
    const char *p = body;
    while (*p != '\0') {
        // A key matches only at the start of a pair and only up to '=', so "qos"
        // cannot be satisfied by "extra_qos" or by a value that happens to contain
        // "qos=".
        const bool match = strncmp(p, key, klen) == 0 && p[klen] == '=';
        const char *amp = strchr(p, '&');
        if (match) {
            const char *v = p + klen + 1;
            const char *end = amp != nullptr ? amp : v + strlen(v);
            size_t at = 0;
            for (const char *q = v; q < end; ++q) {
                char c = *q;
                if (c == '+') {
                    c = ' ';
                } else if (c == '%') {
                    const int hi = (q + 2 < end) ? hex_nibble(q[1]) : -1;
                    const int lo = (q + 2 < end) ? hex_nibble(q[2]) : -1;
                    if (hi >= 0 && lo >= 0) {
                        c = static_cast<char>((hi << 4) | lo);
                        q += 2;
                    }
                    // else: a malformed escape is copied through as a literal '%'.
                    // Silently dropping it would let "%41" and "%4" mean different
                    // things by accident; inventing a byte is worse.
                }
                if (at + 1 >= out_cap) break;
                out[at++] = c;
            }
            out[at] = '\0';
            return true;
        }
        if (amp == nullptr) break;
        p = amp + 1;
    }
    return false;
}

// --- blobs -----------------------------------------------------------------
void blob_put_u8(BlobOut *o, uint8_t v) {
    if (o == nullptr || !o->ok) return;
    if (o->at + 1 > o->cap) { o->ok = false; return; }
    o->buf[o->at++] = v;
}

void blob_put_u16(BlobOut *o, uint16_t v) {
    blob_put_u8(o, static_cast<uint8_t>(v & 0xFF));
    blob_put_u8(o, static_cast<uint8_t>((v >> 8) & 0xFF));
}

void blob_put_u32(BlobOut *o, uint32_t v) {
    blob_put_u16(o, static_cast<uint16_t>(v & 0xFFFF));
    blob_put_u16(o, static_cast<uint16_t>((v >> 16) & 0xFFFF));
}

void blob_put_str(BlobOut *o, const char *s) {
    if (o == nullptr || !o->ok) return;
    const size_t n = s != nullptr ? strlen(s) : 0;
    if (n > 0xFFFF) { o->ok = false; return; }
    blob_put_u16(o, static_cast<uint16_t>(n));
    if (!o->ok) return;
    if (o->at + n > o->cap) { o->ok = false; return; }
    if (n > 0) memcpy(o->buf + o->at, s, n);
    o->at += n;
}

uint8_t blob_get_u8(BlobIn *i) {
    if (i == nullptr || !i->ok) return 0;
    if (i->at + 1 > i->len) { i->ok = false; return 0; }
    return i->buf[i->at++];
}

uint16_t blob_get_u16(BlobIn *i) {
    const uint16_t lo = blob_get_u8(i);
    const uint16_t hi = blob_get_u8(i);
    return static_cast<uint16_t>(lo | (hi << 8));
}

uint32_t blob_get_u32(BlobIn *i) {
    const uint32_t lo = blob_get_u16(i);
    const uint32_t hi = blob_get_u16(i);
    return lo | (hi << 16);
}

void blob_get_str(BlobIn *i, char *out, size_t out_cap) {
    if (out != nullptr && out_cap > 0) out[0] = '\0';
    if (i == nullptr || !i->ok) return;
    const uint16_t n = blob_get_u16(i);
    if (!i->ok) return;
    if (i->at + n > i->len) { i->ok = false; return; }
    if (out != nullptr && out_cap > 0) {
        // A field whose capacity shrank between builds is a truncation, not a
        // corruption: keep what fits and carry on rather than discarding the whole
        // record, which would also discard the fields that are still valid.
        const size_t take = (n < out_cap - 1) ? n : out_cap - 1;
        memcpy(out, i->buf + i->at, take);
        out[take] = '\0';
    }
    i->at += n;
}

// CRC-16/CCITT-FALSE. Table-free: this runs a handful of times per boot over a
// couple of hundred bytes, and 512 bytes of lookup table would cost more than the
// loop saves.
uint16_t settings_crc16(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= static_cast<uint16_t>(data[i]) << 8;
        for (int b = 0; b < 8; ++b) {
            crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                                 : static_cast<uint16_t>(crc << 1);
        }
    }
    return crc;
}

size_t blob_seal(uint8_t *buf, size_t cap, uint16_t version, size_t body) {
    if (buf == nullptr) return 0;
    if (body > 0xFFFF) return 0;
    const size_t total = SETTINGS_BLOB_HEADER + body;
    if (total > cap) return 0;
    buf[0] = static_cast<uint8_t>(SETTINGS_BLOB_MAGIC & 0xFF);
    buf[1] = static_cast<uint8_t>((SETTINGS_BLOB_MAGIC >> 8) & 0xFF);
    buf[2] = static_cast<uint8_t>(version & 0xFF);
    buf[3] = static_cast<uint8_t>((version >> 8) & 0xFF);
    buf[4] = static_cast<uint8_t>(body & 0xFF);
    buf[5] = static_cast<uint8_t>((body >> 8) & 0xFF);
    const uint16_t crc = settings_crc16(buf + SETTINGS_BLOB_HEADER, body);
    buf[6] = static_cast<uint8_t>(crc & 0xFF);
    buf[7] = static_cast<uint8_t>((crc >> 8) & 0xFF);
    return total;
}

bool blob_open(const uint8_t *buf, size_t len, uint16_t expect_version, BlobIn *out) {
    if (out == nullptr) return false;
    *out = BlobIn{};
    if (buf == nullptr || len < SETTINGS_BLOB_HEADER) return false;
    const uint16_t magic = static_cast<uint16_t>(buf[0] | (buf[1] << 8));
    const uint16_t version = static_cast<uint16_t>(buf[2] | (buf[3] << 8));
    const uint16_t body = static_cast<uint16_t>(buf[4] | (buf[5] << 8));
    const uint16_t crc = static_cast<uint16_t>(buf[6] | (buf[7] << 8));
    if (magic != SETTINGS_BLOB_MAGIC) return false;
    if (version != expect_version) return false;
    if (static_cast<size_t>(SETTINGS_BLOB_HEADER) + body > len) return false;
    if (settings_crc16(buf + SETTINGS_BLOB_HEADER, body) != crc) return false;
    out->buf = buf + SETTINGS_BLOB_HEADER;
    out->len = body;
    out->at = 0;
    out->ok = true;
    return true;
}

// --- identity --------------------------------------------------------------
DeviceIdentity device_identity_defaults(const char *ap_ssid) {
    DeviceIdentity id{};
    // The SoftAP name already ends in three MAC bytes, so slugging it produces
    // "drowsyguard-c5e019" - unique per board, with no provisioning step and no
    // second identifier for an operator to keep in step with the first.
    if (ap_ssid != nullptr && ap_ssid[0] != '\0') {
        settings_slug(ap_ssid, id.device_id, sizeof(id.device_id));
    }
    if (id.device_id[0] == '\0') settings_copy(id.device_id, sizeof(id.device_id), "drowsyguard");
    settings_copy(id.fleet_id, sizeof(id.fleet_id), "demo-fleet");
    settings_copy(id.remark, sizeof(id.remark), "Driver A");
    return id;
}

static bool fail(SettingsError *err, const char *field, const char *reason) {
    if (err != nullptr) { err->field = field; err->reason = reason; }
    return false;
}

bool device_identity_validate(const DeviceIdentity &id, SettingsError *err) {
    if (strlen(id.device_id) >= DEVICE_ID_MAX) {
        return fail(err, "device_id", "too long");
    }
    if (!settings_is_topic_segment(id.device_id)) {
        return fail(err, "device_id",
                    "use lowercase letters, digits, '-', '_' or '.', starting and "
                    "ending with a letter or digit");
    }
    if (strlen(id.fleet_id) >= FLEET_ID_MAX) {
        return fail(err, "fleet_id", "too long");
    }
    if (!settings_is_topic_segment(id.fleet_id)) {
        return fail(err, "fleet_id",
                    "use lowercase letters, digits, '-', '_' or '.', starting and "
                    "ending with a letter or digit");
    }
    // The remark is the one free-text field, so it is escaped rather than
    // restricted - but a control byte would still corrupt a log line and a
    // non-ASCII byte cannot survive the fixed-width field.
    if (!settings_is_printable(id.remark, DEVICE_REMARK_MAX - 1)) {
        return fail(err, "remark",
                    "printable ASCII only, up to 47 characters");
    }
    return true;
}

bool wifi_sta_validate(const WifiStaConfig &sta, SettingsError *err) {
    if (!sta.enabled) return true;      // the fields are ignored when it is off
    const size_t ssid_len = strlen(sta.ssid);
    if (ssid_len == 0) return fail(err, "sta_ssid", "required when station mode is on");
    if (ssid_len >= WIFI_SSID_MAX) return fail(err, "sta_ssid", "at most 32 characters");
    if (!settings_is_printable(sta.ssid, WIFI_SSID_MAX - 1)) {
        return fail(err, "sta_ssid", "printable ASCII only");
    }
    const size_t pass_len = strlen(sta.password);
    if (pass_len >= WIFI_PASS_MAX) return fail(err, "sta_password", "at most 63 characters");
    // WPA2-PSK is 8..63 characters. An empty password means an open network, which
    // is legal and occasionally what a demo wants; 1..7 is always a typo, and the
    // radio would reject it later with an error that reads like a bad SSID.
    if (pass_len > 0 && pass_len < 8) {
        return fail(err, "sta_password", "WPA2 needs at least 8 characters (or none for an open network)");
    }
    if (!settings_is_printable(sta.password, WIFI_PASS_MAX - 1)) {
        return fail(err, "sta_password", "printable ASCII only");
    }
    return true;
}

size_t device_identity_serialize(const DeviceIdentity &id, uint8_t *out, size_t cap) {
    if (out == nullptr || cap < SETTINGS_BLOB_HEADER) return 0;
    BlobOut o{out + SETTINGS_BLOB_HEADER, cap - SETTINGS_BLOB_HEADER, 0, true};
    blob_put_str(&o, id.device_id);
    blob_put_str(&o, id.fleet_id);
    blob_put_str(&o, id.remark);
    if (!o.ok) return 0;
    return blob_seal(out, cap, DEVICE_BLOB_VERSION, o.at);
}

bool device_identity_deserialize(const uint8_t *buf, size_t len, DeviceIdentity *out) {
    if (out == nullptr) return false;
    BlobIn i{};
    if (!blob_open(buf, len, DEVICE_BLOB_VERSION, &i)) return false;
    DeviceIdentity id{};
    blob_get_str(&i, id.device_id, sizeof(id.device_id));
    blob_get_str(&i, id.fleet_id, sizeof(id.fleet_id));
    blob_get_str(&i, id.remark, sizeof(id.remark));
    if (!i.ok) return false;
    // A record that was valid when written can still be invalid now - a tightened
    // rule, a shrunk capacity - and honouring it would put an unusable value into a
    // topic. Reject it here so the caller falls back to the defaults.
    if (!device_identity_validate(id, nullptr)) return false;
    *out = id;
    return true;
}

size_t wifi_sta_serialize(const WifiStaConfig &sta, uint8_t *out, size_t cap) {
    if (out == nullptr || cap < SETTINGS_BLOB_HEADER) return 0;
    BlobOut o{out + SETTINGS_BLOB_HEADER, cap - SETTINGS_BLOB_HEADER, 0, true};
    blob_put_u8(&o, sta.enabled ? 1 : 0);
    blob_put_str(&o, sta.ssid);
    blob_put_str(&o, sta.password);
    if (!o.ok) return 0;
    return blob_seal(out, cap, WIFI_BLOB_VERSION, o.at);
}

bool wifi_sta_deserialize(const uint8_t *buf, size_t len, WifiStaConfig *out) {
    if (out == nullptr) return false;
    BlobIn i{};
    if (!blob_open(buf, len, WIFI_BLOB_VERSION, &i)) return false;
    WifiStaConfig sta{};
    sta.enabled = blob_get_u8(&i) != 0;
    blob_get_str(&i, sta.ssid, sizeof(sta.ssid));
    blob_get_str(&i, sta.password, sizeof(sta.password));
    if (!i.ok) return false;
    if (!wifi_sta_validate(sta, nullptr)) return false;
    *out = sta;
    return true;
}
