---
title: Fleet alerting over MQTT
description: >-
  How the device publishes every confirmed alert to a broker - configuration, topics,
  the payload schema, offline buffering, and what happens when the broker is not there.
---

# Fleet alerting over MQTT

The device has three local outputs: the speaker the driver hears, the web dashboard
whoever is standing next to it reads, and a JPEG on the SD card for afterwards. MQTT
is the fourth, and the first one that leaves the vehicle: every confirmed alert is
also published to a broker as one JSON message, so a supervisor watching a fleet sees
a driver deteriorate without being in the cab.

Nothing about the first three changes. If the broker is unreachable, the wrong
password is stored, or there is no network at all, the speaker still sounds, the
dashboard is still on `192.168.4.1`, and the captures are still written.

## The rule this feature was built around

**The publish path can never stall the detection loop.** That loop has a ~23 ms frame
budget and it is the thing that wakes a drowsy driver up; a TLS handshake is seconds
and a blocked socket write is unbounded. So the capture loop's entire involvement is
one call that copies 96 bytes into a 16-deep buffer and returns:

```text
capture loop ──push──▶ MqttOutbox ──publisher task──▶ esp-mqtt ──▶ broker
  (bounded memcpy)      (16 deep)   (connect, backoff, retry, dedup)
```

`mqtt_publish_alert()` takes its mutex with a **zero** tick timeout, cannot allocate,
and does not care whether there is a broker, a network or a configuration. Everything
expensive happens on the publisher task, pinned to core 1 next to the web server
rather than core 0 where inference runs. This is the same rule the browser preview
follows, for the same reason.

The order at each alert site says which output matters:

```cpp
voice_alert_trigger(now_ms, last_reason);       // the driver hears this
web_server_capture_event(...);                  // the evidence
mqtt_publish_alert(...);                        // the telemetry
```

## Turning it on

Publishing is **off by default**, and that is a decision rather than an oversight:
switching it on sends a named driver's alertness state to a third party, which has to
be an act.

1. Join the device's access point and open `http://192.168.4.1/`.
2. Press **Configure MQTT** on the *Fleet alerting* card.
3. The public EMQX broker is already filled in. Set a **fleet ID**, a **device ID** and
   a **remark** - the remark is the only field that says who is being monitored, so
   "Driver A" or "Van 3, morning shift".
4. The device's own access point has no route to the internet, so a broker that is not
   on the same network needs **Wi-Fi station** credentials, at the bottom of the same
   modal. The access point stays up either way.
5. Tick **Publish alerts to a broker** and press **Save and connect**.
6. Press **Test publish**. Without it, the only way to test a broker is to make
   somebody fall asleep.

Then copy the **fleet subscription topic** - the middle of the three, with a `+` where
the device id goes - and paste it into the [fleet monitor](../fleet-monitoring.md).

!!! warning "`broker.emqx.io` is demonstration and testing only"
    It is the [official EMQX public broker][emqx], preconfigured so that a
    demonstration works in a room with no broker in it. There is no authentication and
    no isolation: anyone who subscribes to the topic reads every alert, including the
    driver remark. Point it at your own broker before any real vehicle.

[emqx]: https://www.emqx.com/en/mqtt/public-mqtt5-broker

## Transports

Four, because the two audiences need different ones - and a browser cannot open a raw
MQTT socket at all.

| Transport | URI | EMQX port | Use |
| --- | --- | --- | --- |
| TLS | `mqtts://host:8883` | 8883 | **the default** - what a device should use |
| TCP | `mqtt://host:1883` | 1883 | plaintext; a bench broker with no TLS |
| WSS | `wss://host:8084/mqtt` | 8084 | what the fleet monitor uses, and the only thing an HTTPS page may use |
| WS | `ws://host:8083/mqtt` | 8083 | plaintext WebSocket |

Changing the transport moves the port with it, but only when the port was still the
default for the old one - a port you typed survives.

**MQTT 3.1.1** is the default and every broker speaks it. **MQTT 5** is offered because
some managed brokers now default to it, and because its reason codes are more
actionable; both are compiled in.

### TLS, and what verifies what

| Configuration | What happens |
| --- | --- |
| TLS, no certificate pasted | verified against the Mozilla root bundle in flash (`CONFIG_MBEDTLS_CERTIFICATE_BUNDLE`). This is what makes the public broker work out of the box **with** verification |
| TLS, a CA certificate pasted | verified against that one root. For a private broker |
| **Skip verification** ticked | encrypted, but unauthenticated - anything on the path can present itself as your broker |

The last one is reachable on purpose, and it is labelled in red everywhere it appears.
The alternative is an operator with a self-signed broker turning TLS off altogether,
which is strictly worse. Use it to get the broker working, then paste that broker's CA
and turn verification back on.

A certificate is shape-checked before it is stored - the three things people actually
paste are an empty box, a private key, and a DER file opened in a text editor - so a
mistake is a message next to the field rather than a TLS failure three days into a
drive.

## Topics

Generated by default:

```text
plxy/drowsyguard/{fleet-id}/{device-id}/alerts     one device's alerts
plxy/drowsyguard/{fleet-id}/{device-id}/status     retained online/offline
plxy/drowsyguard/{fleet-id}/+/alerts               every device in the fleet
plxy/drowsyguard/{fleet-id}/+/status
```

Two fixed levels at the root so that a broker shared with other projects - which the
public one certainly is - cannot collide by accident, and the fleet **above** the
device so that one wildcard covers a whole fleet.

`fleet-id` and `device-id` are constrained to lowercase letters, digits, `-`, `_` and
`.`, and validated before either is put in a topic. That is not pedantry: a `/` or a
`+` in one of them silently restructures the tree, and a topic is the one field where a
bad value does not fail - it goes somewhere else. `device-id` defaults to a slug of the
SoftAP name, which is already MAC-derived and therefore unique per board.

The **remark is deliberately not in any topic.** A driver's name has no business in a
broker's subscription tree, where it would be visible to anyone holding a wildcard and
impossible to change without orphaning the topic. It travels in the payload instead.

**Manual topics** are accepted too, with the wildcards refused: `+` and `#` are
subscription syntax, and a publish containing one is either a protocol error or - worse
- accepted as a literal topic nobody is subscribed to, which looks exactly like a
broker silently dropping alerts. In manual mode the status topic is derived by
replacing a trailing `/alerts` with `/status`, and the fleet forms by replacing the
second-to-last level with `+`.

## The payload

One versioned JSON document per alert. Full field table in the
[Device HTTP API](../reference/device-api.md#the-alert-payload).

```json
{"schema":"drowsyguard.alert.v1","event_id":"drowsyguard-c5e019-9f1c2ab3-000042",
 "seq":42,"device_id":"drowsyguard-c5e019","fleet_id":"demo-fleet","remark":"Driver A",
 "alert":"microsleep","severity":"critical","risk":0.712,"perclos":0.421,
 "alert_count":12,"uptime_ms":3723456,"ts":"2026-09-01T11:15:03Z","ts_source":"sntp"}
```

Three things about it are worth knowing before you build anything on it:

* **`severity` is derived, not stored**, so it cannot disagree with `alert`.
  `microsleep` and `no_driver` are `critical`; `drowsy` and `head_nod` are `high`
  (`drowsy` escalates to `critical` above 0.85, where the fusion has stopped hedging);
  `yawning` is `medium`.
* **`ts` is empty when the device does not know the time.** There is no real-time clock
  on the board, so unless something has set it the timestamp is `""` and `ts_source` is
  `"uptime"`. A fabricated timestamp in an incident record is worse than an absent one,
  and `uptime_ms` is always there to order events by.
* **`event_id` is stable across retries and unique across reboots.** It is
  `{device-id}-{boot-id}-{sequence}`, and the boot id is what stops a device that
  restarted mid-drive from reusing sequence numbers it already published.

## When the broker is not there

| Situation | What happens |
| --- | --- |
| No configuration | nothing is queued and nothing is published. The card says "off" |
| Configured, no network | alerts are queued. 16 deep; past that the **oldest** is discarded and counted |
| Connection refused | the same, plus the reason code on the card and in the log |
| Reconnect | doubling backoff from 1 s to a 60 s ceiling, with up to 25 % of jitter |
| Back online | the retained `online` status is republished and the buffer is flushed, one event at a time |

The buffer discards the **oldest** rather than refusing the newest, and that is a
choice: after twenty minutes offline, what a fleet operator needs is the last few
minutes of a deteriorating driver, not the first. Every discard is counted and reported
- a buffer that quietly loses evidence is worse than one that admits it.

esp-mqtt's own auto-reconnect is switched **off** in favour of the backoff above. Its
interval is fixed, so a broker that is down for an hour would be dialled 360 times by a
device whose actual job is inference.

### Last Will

On by default. The broker holds a retained `online: false` document for the device and
publishes it the moment the connection drops without a `DISCONNECT`. That is the only
way a dashboard can tell **"no alerts because the driver is fine"** from **"no alerts
because the device is in a tunnel"** without polling.

A clean shutdown publishes `reason: "shutdown"` first, so the two cases stay
distinguishable:

| `reason` | Meaning |
| --- | --- |
| `connected` | the device published this itself, on connecting |
| `shutdown` | a graceful goodbye - a reconfigure, or the client being stopped |
| `last-will` | the broker published it. The device vanished |

The will body claims no uptime and no timestamp, deliberately: it is composed at
connect time and delivered at an unknown moment in the future, so any value it named
would be a lie.

### De-duplication

QoS 1 is at-least-once by definition, and a retry after a reconnect adds a second
source of duplicates. The device keeps the last 24 `event_id`s and does not republish
one it has already sent. The fleet monitor does the same on its side, over the last
500, because a reconnect redelivers whatever the broker still holds.

## What it deliberately does not do

**It does not subscribe.** This device publishes and takes no commands over MQTT. A
drowsiness alarm a broker can reconfigure - or mute - is a much larger safety argument
than any requirement here needs, and the attack surface is the whole internet on a
shared broker.

## From the command line

```bash
./plxy.sh mqtt                # state, topics and counters
./plxy.sh mqtt test           # one test publish through the real path
./plxy.sh mqtt topic          # just the fleet subscription, for piping
./plxy.sh mqtt on             # switch publishing on without opening a browser
```

`./plxy.sh mqtt` prints the whole settings document, and that is safe because the
document has no password in it — see
[Security](../security.md#mqtt-alerting-leaves-the-vehicle). The username comes back
masked.

`mqtt topic` exists to be piped:

```bash
mosquitto_sub -h broker.emqx.io -p 8883 --capath /etc/ssl/certs \
              -t "$(./plxy.sh mqtt topic)" -v
```

## Where the numbers live

| What | Where |
| --- | --- |
| Settings, validation, topics, payload, backoff, dedup, the outbox | `firmware/esp32s3/main/mqtt_config.{h,cpp}` - no ESP-IDF headers, so it is host-testable |
| Identity, station credentials, form decoding, the NVS blob format | `firmware/esp32s3/main/device_config.{h,cpp}` - likewise |
| The queue, the task, the client, reconnection | `firmware/esp32s3/main/mqtt_publisher.{h,cpp}` |
| NVS reads and writes | `firmware/esp32s3/main/settings_nvs.{h,cpp}` |
| `GET`/`POST /api/mqtt`, `POST /api/mqtt/test` | `firmware/esp32s3/main/web_server.cpp` |
| Every decision above, as a test | `tests/test_mqtt_config.py` |

The split is the point: the two pure files hold everything that can be wrong without a
broker in the room, and the two ESP-IDF files own no logic of their own. See
[Configuration](../configuration/index.md#mqtt-alerting) for the tunables and
[Security](../security.md#mqtt-alerting-leaves-the-vehicle) for the threat model.
