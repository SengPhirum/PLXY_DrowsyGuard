#pragma once
/*
The broker side of MQTT alerting: one queue, one task, one client.

  capture loop --push--> MqttOutbox --publisher task--> esp-mqtt --> broker
      (bounded memcpy)     (16 deep)    (connect, backoff, retry, dedup)

WHAT THE CAPTURE LOOP IS ALLOWED TO DO
--------------------------------------
Exactly one thing: mqtt_publish_alert(). It takes a mutex with a zero tick timeout,
copies 96 bytes, and returns. It cannot block, cannot allocate, cannot fail in a way
the caller has to handle, and does not care whether there is a broker, a network, or
a configuration. Everything else - DNS, the TLS handshake, the CONNECT, the retries,
the backoff, rendering the JSON - happens on the publisher task, pinned to core 1
alongside the web server rather than core 0 where inference runs.

That is the same rule the web preview follows (web_server.h), and for the same
reason: this device's job is to wake a driver up. A subsystem that can stall the
capture loop is a safety defect however useful it is, so the alert path publishes
into a buffer and walks away.

WHAT THIS OWNS
--------------
  * Reconnection, with the exponential backoff in mqtt_config.h. esp-mqtt's own
    auto-reconnect is switched OFF: its interval is fixed, and a broker that is down
    for an hour would otherwise be dialled 360 times by a device that is also trying
    to run a drowsiness detector.
  * Bounded offline buffering. 16 events, oldest discarded, every discard counted.
  * De-duplication on event_id, so a retry after a reconnect - or a QoS 1
    retransmission - cannot file the same alert twice.
  * Last Will. The broker holds a retained "online: false" document for this device
    and publishes it the moment the connection drops without a DISCONNECT, which is
    the only way a dashboard can tell "no alerts because the driver is fine" from
    "no alerts because the device is in a tunnel".
  * Applying a new configuration at runtime, from the web UI, without a reboot.

WHAT IT DELIBERATELY DOES NOT OWN
---------------------------------
Subscribing. This device publishes; it takes no commands over MQTT. A drowsiness
alarm that a broker can reconfigure - or mute - is a different and much larger
safety argument, and there is no requirement that needs it.
*/

#include <cstddef>
#include <cstdint>

#include "device_config.h"
#include "mqtt_config.h"

enum class MqttState : uint8_t {
    Disabled = 0,      // switched off in the settings; nothing is running
    Idle = 1,          // enabled, waiting for a network
    Connecting = 2,
    Online = 3,
    Backoff = 4,       // disconnected, counting down to the next attempt
    Fault = 5,         // a configuration or client error no retry can fix
};

const char *mqtt_state_name(MqttState s);

// Everything GET /api/mqtt reports about the live connection. No credentials in it,
// by construction: this struct has nowhere to put one.
struct MqttStatus {
    MqttState state = MqttState::Disabled;
    bool enabled = false;
    bool client_up = false;
    uint32_t connects = 0;         // successful CONNACKs since boot
    uint32_t disconnects = 0;
    uint32_t attempt = 0;          // consecutive failed attempts, drives the backoff
    uint32_t next_retry_ms = 0;    // remaining backoff, 0 when not waiting
    uint32_t published = 0;        // handed to the broker
    uint32_t acked = 0;            // PUBACKed (QoS 1) or written (QoS 0)
    uint32_t dropped = 0;          // evicted from a full outbox
    uint32_t suppressed = 0;       // duplicate event_ids not republished
    uint32_t rejected = 0;         // producer could not take the lock; see the header
    int depth = 0;                 // events waiting
    int capacity = MQTT_OUTBOX_DEPTH;
    uint32_t boot_id = 0;
    uint32_t seq = 0;              // next sequence number
    // Last error, as a short string safe to show and to log. Points at a static
    // literal or at an internal buffer that only ever holds an esp_err_t name and a
    // numeric code - never a host, never a credential.
    char last_error[80] = {0};
    uint32_t last_publish_ms = 0;  // uptime at the last successful publish
};

// Reads the stored settings, allocates the outbox and starts the task. Safe to call
// with MQTT disabled - the task still runs, so switching it on from the web UI does
// not need a reboot. Returns false only when the task or its mutex could not be
// created, which is a heap failure rather than a configuration problem.
//
// `id` is the identity already loaded (or defaulted) by main.cpp. Passed in rather
// than read here because the AP name it defaults from belongs to board_wifi, and a
// module that publishes should not be the module that decides what the device is
// called.
bool mqtt_publisher_start(const DeviceIdentity &id);

// One confirmed alert. Called from the capture loop, immediately after
// voice_alert_trigger() and never before it: the speaker is the safety-critical
// output and this is telemetry.
//
// `alert` is voice_alert_clip_name(reason) - the same token the SD card filename and
// the spoken clip use, so the three records of one event cannot disagree.
//
// Returns true when the event was queued. False means MQTT is off, or the outbox
// evicted an older event to make room, or the lock was busy: all three are counted
// and reported, and none of them is anything the caller should do differently.
bool mqtt_publish_alert(const char *alert, float risk, float perclos,
                        uint32_t alert_count, uint32_t uptime_ms);

// Snapshot for the status endpoint.
void mqtt_publisher_status(MqttStatus *out);

// Applies a validated configuration, persists it, and restarts the client. Called
// from the HTTP task. The identity is passed alongside because the topics and the
// payload are built from it and the two must be applied together - a device_id
// change with a stale topic would publish to the old one.
bool mqtt_publisher_apply(const MqttConfig &cfg, const DeviceIdentity &id);

// The configuration and identity currently in force, for GET /api/mqtt.
void mqtt_publisher_config(MqttConfig *cfg, DeviceIdentity *id);

// Publishes one synthetic alert marked as a test, so an operator can prove the whole
// path - credentials, TLS, topic, subscriber - without waiting for a real one.
// Queued exactly like a real alert, which is the point: a test that took a different
// route would not be evidence. Returns false when MQTT is disabled.
bool mqtt_publisher_test();
