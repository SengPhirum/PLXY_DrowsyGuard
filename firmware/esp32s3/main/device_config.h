#pragma once
/*
Device identity, station credentials, and the byte-level plumbing that puts both in
NVS. No ESP-IDF headers here, on purpose: everything in this file is arithmetic and
string handling, and tests/test_mqtt_config.py compiles it on the host and drives it
directly. The NVS calls themselves live in settings_nvs.cpp, which is the only file
that needs a flash partition to run.

Why the device needs an identity at all. Until MQTT arrived, nothing on this board
had a name: the preview is served to whoever joined the access point, and "the
device" was whichever one you were standing next to. A fleet is different. Two
boards publishing to one broker are only distinguishable by what they put in the
topic and the payload, so the identity is now a persisted, validated setting rather
than something derived at boot and forgotten.

Three fields, and the third is the one that matters to a human:

  device_id  a topic segment. Defaults to a slug of the SoftAP name, which is
             already MAC-derived and therefore already unique per board.
  fleet_id   the group a dashboard subscribes to with one wildcard.
  remark     free text the operator writes - "Driver A", "Van 3, morning shift".
             It is the only field in the system that says who is being monitored,
             and it is deliberately NOT part of any topic: a driver's name has no
             business in a broker's subscription tree, where it would be visible to
             anyone holding a wildcard and impossible to change without orphaning
             the topic.

The validation is not decoration. `device_id` and `fleet_id` become topic segments,
so a `/`, a `+` or a `#` in either one silently rewrites the topic tree - which is
how a device ends up publishing into a topic a dashboard is not watching, or worse,
into one it is. See mqtt_config.h for the rest of the topic rules.
*/

#include <cstddef>
#include <cstdint>

// Field capacities. Fixed arrays rather than std::string: these structs are
// serialised into an NVS blob and passed by value between tasks, and neither is a
// place to be allocating.
#define DEVICE_ID_MAX 32       // topic segment, so short and constrained
#define FLEET_ID_MAX 32
#define DEVICE_REMARK_MAX 48   // "Driver A" and rather more than that
#define WIFI_SSID_MAX 33       // 32 + NUL, as 802.11 defines it
#define WIFI_PASS_MAX 64

struct DeviceIdentity {
    char device_id[DEVICE_ID_MAX] = {0};
    char fleet_id[FLEET_ID_MAX] = {0};
    char remark[DEVICE_REMARK_MAX] = {0};
};

// Station mode, persisted. board_wifi.h still carries compile-time defaults, and
// they still work; this is what lets a demo join a phone hotspot without a rebuild,
// which is the only way the board reaches a broker at all. The SoftAP is never
// configurable from here and never comes down: it is the fallback that keeps the
// dashboard reachable when the station side, the internet or the broker fails.
struct WifiStaConfig {
    bool enabled = false;
    char ssid[WIFI_SSID_MAX] = {0};
    char password[WIFI_PASS_MAX] = {0};
};

// Which field failed, and why, in a form a UI can put next to the input. Two
// separate strings rather than one sentence because the page highlights the field.
struct SettingsError {
    const char *field = nullptr;    // API field name, e.g. "host"
    const char *reason = nullptr;   // short human sentence
};

// --- validation ------------------------------------------------------------
// Both return true when the value is safe to persist. On false, *err names the
// offending field. Passing nullptr for err is allowed.
bool device_identity_validate(const DeviceIdentity &id, SettingsError *err);
bool wifi_sta_validate(const WifiStaConfig &sta, SettingsError *err);

// True when `s` is usable as a single MQTT topic segment: 1..max-1 bytes of
// lowercase alphanumerics, '-', '_' or '.', starting with an alphanumeric. That
// excludes every character with meaning to a broker ('/', '+', '#', '$') and every
// non-ASCII byte, so a segment that passes can only ever be one level deep.
bool settings_is_topic_segment(const char *s);

// True when every byte is printable ASCII (0x20..0x7E) and the string fits. Used
// for the remark and the credentials: those may contain anything a keyboard
// produces, but a control character in them would corrupt a log line, an HTTP
// header or the JSON payload, and a non-ASCII byte cannot survive the fixed-width
// fields either way.
bool settings_is_printable(const char *s, size_t max_len);

// --- helpers the rest of the firmware shares -------------------------------
// Squeezes arbitrary text into a topic segment: lowercased, anything outside
// [a-z0-9] collapsed to a single '-', ends trimmed, truncated to fit. Returns the
// length written. Used to derive a default device_id from the SoftAP name and to
// make a fleet_id out of whatever an operator typed.
size_t settings_slug(const char *in, char *out, size_t out_cap);

// Appends `s` to a JSON string literal already in progress, escaping the five
// characters JSON requires plus every control byte (as \u00XX). Returns false if
// the escaped form did not fit, in which case *out is left NUL-terminated at
// whatever did - callers treat that as a failure and abandon the buffer.
//
// This exists because the payload builder is one snprintf, and a remark like
//   Driver "A" \ night
// would otherwise produce a document no parser accepts. The alternative - refusing
// quotes in the remark - moves the problem onto the operator for no gain.
bool settings_json_escape(const char *s, char *out, size_t out_cap);

// The same thing for text that did NOT come from a validator.
//
// An SSID is 0-32 arbitrary octets chosen by whoever owns the access point, and
// anybody in radio range of this device can broadcast one. settings_json_escape()
// passes bytes >= 0x80 through untouched, which is correct for the fields it was
// written for - they are all validated to printable ASCII first - and wrong here: a
// high byte that is not part of a well-formed UTF-8 sequence makes the whole document
// undecodable, so the page's JSON.parse throws and the operator sees an empty scan
// list on the one page they are using to recover the device.
//
// So: well-formed UTF-8 passes through, and a network really called "cafe" with an
// accent reads as itself. Anything else - a lone continuation byte, a truncated
// sequence, an overlong encoding, a surrogate half - is escaped byte by byte as
// \u00XX, which is always valid JSON and always renders something.
//
// Same contract as settings_json_escape otherwise: 6 bytes of output per input byte
// in the worst case, and false when the escaped form did not fit.
bool settings_json_escape_utf8(const char *s, char *out, size_t out_cap);

// Copies at most out_cap-1 bytes and always NUL-terminates. Returns false when the
// input had to be truncated, which every caller treats as a validation failure
// rather than silently storing half a hostname.
bool settings_copy(char *out, size_t out_cap, const char *in);

// --- URL-encoded form fields ----------------------------------------------
// The MQTT settings arrive as an application/x-www-form-urlencoded body rather than
// a query string, because a CA certificate is 1-2 kB and a query string is not.
// ESP-IDF's httpd_query_key_value() finds a key in that shape but does NOT
// percent-decode the value, so the decoding is here - where it can be tested,
// including on the malformed inputs a hand-written client will send.
//
// Returns true when the key was present. `out` is always NUL-terminated. A '+'
// becomes a space and %XX becomes its byte; a stray '%' or a truncated escape is
// copied through literally rather than dropped, because guessing at a malformed
// escape is how a decoder invents characters that were never sent.
bool settings_form_field(const char *body, const char *key, char *out, size_t out_cap);

// --- versioned NVS blobs ---------------------------------------------------
// Everything persisted goes through this rather than memcpy of the struct. A raw
// struct in flash is a promise never to reorder a field, never to change a
// capacity and never to compile with different padding - and the first time one of
// those is broken the device reads a plausible-looking hostname out of the middle
// of a password. The blob is an explicit byte stream with a magic, a version and a
// CRC, so an old or corrupt record is rejected and the defaults are used instead.
#define SETTINGS_BLOB_MAGIC 0x4447u   /* 'DG' */
#define SETTINGS_BLOB_HEADER 8        /* magic(2) version(2) len(2) crc(2) */

struct BlobOut {
    uint8_t *buf = nullptr;
    size_t cap = 0;
    size_t at = 0;
    bool ok = true;
};

struct BlobIn {
    const uint8_t *buf = nullptr;
    size_t len = 0;
    size_t at = 0;
    bool ok = true;
};

void blob_put_u8(BlobOut *o, uint8_t v);
void blob_put_u16(BlobOut *o, uint16_t v);
void blob_put_u32(BlobOut *o, uint32_t v);
// Length-prefixed, so a field that grows a capacity later still reads back.
void blob_put_str(BlobOut *o, const char *s);

uint8_t blob_get_u8(BlobIn *i);
uint16_t blob_get_u16(BlobIn *i);
uint32_t blob_get_u32(BlobIn *i);
void blob_get_str(BlobIn *i, char *out, size_t out_cap);

uint16_t settings_crc16(const uint8_t *data, size_t len);

// Wraps a payload written by the callers above with the header and CRC. `body` is
// the number of bytes the caller placed at buf + SETTINGS_BLOB_HEADER. Returns the
// total length, or 0 when it did not fit.
size_t blob_seal(uint8_t *buf, size_t cap, uint16_t version, size_t body);

// Checks magic, version and CRC and positions a reader on the payload. Returns
// false for a record this build cannot read - which is not an error worth
// reporting to a user, it is "use the defaults".
bool blob_open(const uint8_t *buf, size_t len, uint16_t expect_version, BlobIn *out);

// --- identity and station config as blobs ---------------------------------
#define DEVICE_BLOB_VERSION 1
#define WIFI_BLOB_VERSION 1
#define DEVICE_BLOB_MAX 192
#define WIFI_BLOB_MAX 160

size_t device_identity_serialize(const DeviceIdentity &id, uint8_t *out, size_t cap);
bool device_identity_deserialize(const uint8_t *buf, size_t len, DeviceIdentity *out);
size_t wifi_sta_serialize(const WifiStaConfig &sta, uint8_t *out, size_t cap);
bool wifi_sta_deserialize(const uint8_t *buf, size_t len, WifiStaConfig *out);

// Defaults, given the board's own SoftAP name (which is MAC-derived, so this is
// unique per board without provisioning). `ap_ssid` may be empty.
DeviceIdentity device_identity_defaults(const char *ap_ssid);
