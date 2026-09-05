#pragma once
/*
MQTT alerting: the settings, the topics, the payload, and the three data structures
that make publishing safe to call from the capture loop.

Everything in this file is pure. No ESP-IDF, no FreeRTOS, no sockets - which is what
lets tests/test_mqtt_config.py compile it on the host and drive the topic builder,
the payload builder, the validator, the backoff schedule, the de-duplicator and the
outbox directly. mqtt_publisher.cpp is the half that talks to a broker, and it owns
no logic that could not be tested here.

WHY THIS SHAPE
--------------
The device already had three outputs: a speaker for the driver, a web page for
whoever is standing next to it, and a JPEG on an SD card for afterwards. All three
are local. MQTT is the first one that leaves the vehicle, and that changes what can
go wrong:

  * A broker is not there when you need it. Roaming, a dead hotspot, a certificate
    that expired last week - the network is down precisely on the long drive where
    the alerts matter. So publishing is queued, retried with a backoff, and buffered
    while offline within a fixed bound.
  * A broker is slow in a way a speaker never is. A TLS handshake is seconds; a
    blocked socket write is unbounded. Nothing on this path may ever be called from
    the capture loop in a way that can wait, which is why the producer's entire job
    is one bounded memcpy into MqttOutbox.
  * A retry duplicates. Every alert therefore carries an event_id that is stable
    across retries, and both ends can de-duplicate on it.
  * A topic is a namespace someone else also uses. The public broker below is
    shared with the entire internet, so the auto-generated topics are constrained to
    characters that cannot restructure the tree, and a manual topic is checked for
    the wildcards that would turn a publish into a broadcast.

The alert path itself is unchanged. voice_alert_trigger() still runs first and the
speaker still sounds whether or not there is a broker; a closure that behavior.cpp
declines to call a microsleep produces no drowsiness alert here for the same reason
it produces none on the speaker.
*/

#include <cstddef>
#include <cstdint>

#include "device_config.h"

// --- transports ------------------------------------------------------------
// Four, because the two audiences need different ones. A board sitting on a phone
// hotspot wants TLS on 8883; a browser - which is what the documentation's fleet
// page is - cannot open a raw TCP socket at all and must use WebSocket, and a page
// served over HTTPS may only use the secure variant. Supporting only one would mean
// either the device or the dashboard could not connect.
enum class MqttTransport : uint8_t {
    Tcp = 0,   // mqtt://host:1883    - plaintext, bench only
    Tls = 1,   // mqtts://host:8883   - the default
    Ws = 2,    // ws://host:8083/mqtt
    Wss = 3,   // wss://host:8084/mqtt
};

// 5 is worth offering rather than assuming: it carries reason codes and user
// properties a fleet backend can act on, and some managed brokers now default to
// it. 3.1.1 stays the default because every broker and every browser library
// speaks it.
enum class MqttProtocol : uint8_t { V311 = 0, V5 = 1 };

enum class MqttTopicMode : uint8_t { Auto = 0, Manual = 1 };

enum class MqttTopicKind : uint8_t {
    Alerts = 0,        // what this device publishes alerts to
    Status = 1,        // online/offline, retained, and the Last Will topic
    FleetAlerts = 2,   // one wildcard subscription covering every device in the fleet
    FleetStatus = 3,
};

#define MQTT_HOST_MAX 96
#define MQTT_PATH_MAX 64
#define MQTT_CLIENT_ID_MAX 64
#define MQTT_USER_MAX 64
#define MQTT_PASS_MAX 96
#define MQTT_TOPIC_MAX 160
// A single PEM certificate. 4 kB covers a 4096-bit RSA CA with room to spare; a
// chain does not fit and does not need to - a broker's server certificate is
// verified against one root, not against a bundle the device also has to store.
#define MQTT_CA_MAX 4096

// The auto-generated tree. Two fixed levels so a broker shared with other projects
// - which the public one below certainly is - cannot collide with us by accident,
// and so a subscription written as plxy/drowsyguard/# is unambiguous.
#define MQTT_TOPIC_ROOT "plxy/drowsyguard"

// --- the official EMQX public broker --------------------------------------
// https://www.emqx.com/en/mqtt/public-mqtt5-broker
//
// DEMONSTRATION AND TESTING ONLY. It is shared with the whole internet: there is no
// authentication, no isolation and no retention promise, and anyone who subscribes
// to the topic - which they can guess - reads every alert this device publishes,
// including the driver remark. It is preconfigured here because a thesis
// demonstration has to work in a room with no broker in it, and it is labelled on
// the settings modal, in the API response and in the documentation for the same
// reason. Point it at your own broker before any real vehicle.
#define MQTT_DEMO_HOST "broker.emqx.io"
#define MQTT_DEMO_PORT_TCP 1883
#define MQTT_DEMO_PORT_TLS 8883
#define MQTT_DEMO_PORT_WS 8083
#define MQTT_DEMO_PORT_WSS 8084
#define MQTT_DEMO_WS_PATH "/mqtt"

struct MqttConfig {
    bool enabled = false;
    MqttTransport transport = MqttTransport::Tls;
    MqttProtocol protocol = MqttProtocol::V311;
    char host[MQTT_HOST_MAX] = {0};
    uint16_t port = MQTT_DEMO_PORT_TLS;
    char ws_path[MQTT_PATH_MAX] = {0};
    // Empty means "derive it from device_id". A fixed client id that two boards
    // share is the classic MQTT failure: the broker disconnects whichever one
    // connected first, so two devices take turns knocking each other offline and
    // both look intermittently broken.
    char client_id[MQTT_CLIENT_ID_MAX] = {0};
    char username[MQTT_USER_MAX] = {0};
    char password[MQTT_PASS_MAX] = {0};
    // 1 by default and by intent: an alert that silently did not arrive is the one
    // outcome this feature exists to prevent. 0 is offered for a bench broker where
    // the retransmission is noise.
    uint8_t qos = 1;
    MqttTopicMode topic_mode = MqttTopicMode::Auto;
    char topic[MQTT_TOPIC_MAX] = {0};      // manual mode only
    uint16_t keepalive_s = 30;
    // Last Will. On by default: a fleet dashboard that cannot tell "no alerts
    // because the driver is fine" from "no alerts because the device fell off the
    // network" is worse than no dashboard, and the will is the only thing that can
    // distinguish them without polling.
    bool lwt = true;
    bool retain_status = true;
    // The PEM is not in this struct - it is 4 kB and this struct is copied between
    // tasks. It lives in its own NVS record and its own heap buffer; this only says
    // whether there is one.
    bool ca_present = false;
    // Skips server certificate verification. Valid, deliberately reachable, and
    // loudly labelled everywhere it appears: it is the difference between "TLS" and
    // "TLS that any hotspot can read", and someone debugging a broker with a
    // self-signed certificate will otherwise disable TLS entirely, which is worse.
    bool tls_insecure = false;
};

// Defaults: the public EMQX broker over TLS, MQTT 3.1.1, QoS 1, automatic topics -
// and `enabled = false`. Off by default is the only defensible choice: turning it
// on publishes a driver's state to a third party, and that must be an act, not an
// inherited setting.
MqttConfig mqtt_config_defaults();

uint16_t mqtt_default_port(MqttTransport t);
bool mqtt_transport_is_tls(MqttTransport t);
bool mqtt_transport_is_ws(MqttTransport t);
const char *mqtt_transport_name(MqttTransport t);          // "tcp" "tls" "ws" "wss"
bool mqtt_transport_from_name(const char *s, MqttTransport *out);
const char *mqtt_protocol_name(MqttProtocol p);            // "3.1.1" "5"
bool mqtt_protocol_from_name(const char *s, MqttProtocol *out);

// --- validation ------------------------------------------------------------
// Run whether or not `enabled` is set, so that switching MQTT on can never fail on
// a field the operator filled in three screens ago. On false, *err names the field
// and gives a sentence short enough to sit under the input.
bool mqtt_config_validate(const MqttConfig &cfg, const DeviceIdentity &id,
                          SettingsError *err);

// Shape check on a pasted CA certificate. Not a parse - mbedtls does that when the
// socket opens - but enough to reject the three things people actually paste: an
// empty box, a private key, and a DER file opened in a text editor. Catching them
// here turns a mid-drive TLS failure into a message next to the field.
bool mqtt_ca_pem_valid(const char *pem, size_t len, SettingsError *err);

// --- addressing ------------------------------------------------------------
// "mqtts://broker.emqx.io:8883", "wss://broker.emqx.io:8084/mqtt", and so on.
bool mqtt_uri(const MqttConfig &cfg, char *out, size_t out_cap);

// Auto mode:  plxy/drowsyguard/{fleet_id}/{device_id}/alerts
//             plxy/drowsyguard/{fleet_id}/{device_id}/status
//             plxy/drowsyguard/{fleet_id}/+/alerts        (fleet subscription)
// Manual mode: `cfg.topic` verbatim for Alerts; for Status a trailing "/alerts" is
//             replaced with "/status", or "/status" appended when there is none;
//             the fleet forms replace the second-to-last level with '+' so one
//             subscription still covers every device sharing the shape.
bool mqtt_topic(const MqttConfig &cfg, const DeviceIdentity &id, MqttTopicKind kind,
                char *out, size_t out_cap);

// The configured client id, or "drowsyguard-{device_id}" when it is empty.
bool mqtt_client_id(const MqttConfig &cfg, const DeviceIdentity &id, char *out,
                    size_t out_cap);

// --- the payload -----------------------------------------------------------
// One alert, as the capture loop hands it over. Fixed size and trivially copyable:
// this is what goes into MqttOutbox, and the rendering happens later on the
// publisher task, where a few hundred microseconds of snprintf costs nobody a frame.
struct MqttAlertEvent {
    char event_id[48] = {0};
    // The clip name from voice_alert_clip_name(): drowsy, microsleep, yawning,
    // head_nod, no_driver. Reused rather than re-encoded so the topic
    // payload, the SD card filename and the spoken warning cannot drift apart.
    char alert[16] = {0};
    float risk = 0.0f;
    float perclos = 0.0f;
    uint32_t alert_count = 0;
    uint32_t uptime_ms = 0;
    // Epoch milliseconds, or 0 when the wall clock has never been set - which is
    // the normal case on a board with no RTC that has not reached an SNTP server.
    // Reported as 0 rather than guessed: a fabricated timestamp in an incident
    // record is worse than an absent one.
    int64_t epoch_ms = 0;
    uint32_t seq = 0;
};

#define MQTT_PAYLOAD_MAX 768

// Coarse severity, derived rather than stored so it cannot disagree with the alert
// type. microsleep and no_driver are critical (eyes shut for over a second; nobody
// being monitored at all), drowsy and head_nod high, yawning medium. A sustained
// `drowsy` above 0.85 escalates to critical, because at that point the fusion is
// not hedging.
const char *mqtt_severity_for(const char *alert, float risk);

// The versioned alert document. Returns the length written, or 0 if it did not fit
// (which the caller treats as a dropped publish rather than sending half a JSON
// object). Schema: drowsyguard.alert.v1.
size_t mqtt_alert_payload(const DeviceIdentity &id, const MqttAlertEvent &ev,
                          char *out, size_t out_cap);

// The retained online/offline document, and the Last Will body. `reason` is a short
// machine token - "connected", "shutdown", "last-will" - so a dashboard can tell a
// clean goodbye from a device that vanished.
size_t mqtt_status_payload(const DeviceIdentity &id, bool online, const char *reason,
                           uint32_t uptime_ms, int64_t epoch_ms, uint32_t alert_count,
                           char *out, size_t out_cap);

// "{device_id}-{boot_id:08x}-{seq:06u}". Stable across retries and unique across
// reboots, which is what makes both ends able to de-duplicate: a device that
// restarts mid-drive must not reuse the sequence numbers it already published.
bool mqtt_event_id(const DeviceIdentity &id, uint32_t boot_id, uint32_t seq,
                   char *out, size_t out_cap);

// "2026-09-01T09:15:03Z", or an empty string when epoch_ms is 0. Implemented from
// the civil-calendar algorithm rather than gmtime_r, which is not available on
// every host this file is compiled for.
bool mqtt_iso8601(int64_t epoch_ms, char *out, size_t out_cap);

// --- the redacted settings document ---------------------------------------
// What GET /api/mqtt returns. The password is not in it - not masked, not
// truncated, absent - and neither is the station password. The username comes back
// masked so an operator can confirm *which* account is configured without the
// value being readable off a shoulder or out of a screenshot.
//
// This is the function that has to be right for "no secrets through the API" to be
// true, so it is the function the tests check character by character.
size_t mqtt_config_json(const MqttConfig &cfg, const DeviceIdentity &id,
                        const WifiStaConfig &sta, size_t ca_bytes,
                        char *out, size_t out_cap);

// "dr******m" - first two and last one kept, the middle starred, and nothing kept
// at all below four characters. Used for the username and for anything a log line
// wants to identify without disclosing.
void mqtt_mask(const char *in, char *out, size_t out_cap);

// --- persistence -----------------------------------------------------------
// A versioned byte stream, not a struct memcpy - see device_config.h for why. The
// deserialiser re-validates: a record written by an older build under looser rules
// must not be able to put an unusable host or an out-of-range QoS back into a live
// configuration, and rejecting it costs nothing because the fallback is the defaults.
//
// The CA certificate is NOT in here. It lives in its own NVS record because it is
// up to 4 kB and this struct is copied between tasks; `ca_present` is the only trace
// of it in the config.
#define MQTT_BLOB_VERSION 1
#define MQTT_BLOB_MAX 640

size_t mqtt_config_serialize(const MqttConfig &cfg, uint8_t *out, size_t cap);
bool mqtt_config_deserialize(const uint8_t *buf, size_t len, MqttConfig *out);

// --- reconnection ----------------------------------------------------------
// Doubling from 1 s to a 60 s ceiling. esp-mqtt has its own auto-reconnect and it
// is switched off in favour of this one, for two reasons: its interval is fixed, so
// a broker that is down for an hour is hit 360 times by a device that is also
// trying to run a drowsiness detector; and reconnecting is where the config has to
// be re-read, which the library cannot know about.
#define MQTT_BACKOFF_BASE_MS 1000u
#define MQTT_BACKOFF_MAX_MS 60000u
#define MQTT_BACKOFF_SHIFT_MAX 6u

uint32_t mqtt_backoff_ms(uint32_t attempt);   // attempt 1 -> 1000, 2 -> 2000, ...

// The same schedule with up to +25 % of jitter taken from `rnd`. Jitter matters at
// fleet scale: without it, every device that lost the same access point retries in
// the same millisecond forever. Deterministic in `rnd` so it can be tested.
uint32_t mqtt_backoff_jittered_ms(uint32_t attempt, uint32_t rnd);

// --- de-duplication --------------------------------------------------------
// QoS 1 is at-least-once by definition, and our own retry after a reconnect adds a
// second source of duplicates. This is the device-side half: an event_id that has
// already been published is not published again. Full strings rather than hashes -
// 24 slots is under a kilobyte, and a hash collision would silently drop a real
// alert, which is the one error this device must not make quietly.
#define MQTT_DEDUP_SLOTS 24

class MqttDedup {
  public:
    void reset();
    // True when this id has been seen before (and the ring is left unchanged).
    // False when it is new, in which case it is recorded.
    bool seen_or_add(const char *event_id);
    // The two halves of seen_or_add(), split because the publisher must not record
    // an id it has not delivered yet: recording before esp_mqtt_client_enqueue()
    // meant a failed enqueue's retry matched its own first attempt in this ring and
    // was dropped as a "duplicate" without ever being published. The publisher asks
    // first and marks only after the transport accepted the message; a "yes" from
    // already_published() is counted in suppressed(), same as seen_or_add().
    bool already_published(const char *event_id);
    void mark_published(const char *event_id);   // idempotent
    int size() const { return count_; }
    int capacity() const { return MQTT_DEDUP_SLOTS; }
    uint32_t suppressed() const { return suppressed_; }

  private:
    char slot_[MQTT_DEDUP_SLOTS][48] = {};
    int at_ = 0;
    int count_ = 0;
    uint32_t suppressed_ = 0;
};

// --- the outbox ------------------------------------------------------------
// Bounded offline buffering, and the reason the capture loop can call into this
// subsystem at all.
//
// Depth 16, which is about eight minutes of drowsiness alerts at the 30 s channel
// cooldown - long enough to cover a tunnel or a hotspot reconnect, short enough
// that the 1.6 kB it costs is not worth arguing about. When it is full the OLDEST
// event is discarded, not the newest: after twenty minutes offline, what a fleet
// operator needs is the last few minutes of a deteriorating driver, not the first.
// Every discard is counted and reported, because a buffer that quietly loses
// evidence is worse than one that admits it.
//
// push() is O(1), allocation-free and cannot block or fail. That property is the
// whole isolation argument: the worst thing a dead broker can do to the detection
// loop is cost it one memcpy of 96 bytes.
#define MQTT_OUTBOX_DEPTH 16

class MqttOutbox {
  public:
    void reset();
    // Always accepts the event. Returns false when it had to drop an older one to
    // make room - a signal for the log, not a failure the caller must handle.
    bool push(const MqttAlertEvent &ev);
    // Reads the head without removing it, so a publish that fails can be retried
    // with the same event_id rather than losing it. commit() removes it.
    bool peek(MqttAlertEvent *out) const;
    void commit();
    // commit(), but only when the head is still the event the caller just handled,
    // identified by its seq. Between a peek and a commit the producer can push into
    // a full ring and evict that same head; a plain commit would then remove the
    // event that replaced it - one that was never published - and lose it without
    // it ever being counted. False means the head had already been evicted and
    // nothing was removed.
    bool commit_if_seq(uint32_t seq);
    bool pop(MqttAlertEvent *out);

    int depth() const { return count_; }
    int capacity() const { return MQTT_OUTBOX_DEPTH; }
    uint32_t queued() const { return queued_; }
    uint32_t dropped() const { return dropped_; }

  private:
    MqttAlertEvent slot_[MQTT_OUTBOX_DEPTH] = {};
    int head_ = 0;
    int count_ = 0;
    uint32_t queued_ = 0;
    uint32_t dropped_ = 0;
};
