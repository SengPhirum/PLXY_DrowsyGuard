#pragma once
/*
The only file that touches NVS for the MQTT feature.

Three records plus a certificate, all in one namespace, all versioned blobs written
by device_config.cpp and mqtt_config.cpp rather than as raw structs. The split
matters: the encoding is testable on a host and this file is not, so everything that
could be wrong about the *format* is checked by tests/test_mqtt_config.py, and what
is left here is a handful of nvs_get_blob / nvs_set_blob calls.

Namespace "dgsettings", kept separate from voice_alert.cpp's "drowsyguard" and from
the Wi-Fi driver's own namespace. That is deliberate: a partition erase performed to
recover one subsystem should not silently reset the others, and a key collision
between two modules that both wanted "cfg" is the kind of bug that only shows up
after a firmware update.

Nothing here logs a credential, and that is a rule rather than a coincidence: a
serial log is the least protected place on this device, it is what gets pasted into
a bug report, and `./plxy.sh monitor` puts it on screen in a room with other people
in it. Load failures report the key and the error code, never the value.
*/

#include <cstddef>
#include <cstdint>

#include "device_config.h"
#include "mqtt_config.h"

// Opens the namespace. Safe to call before anything else has touched NVS -
// board_wifi_init() already runs nvs_flash_init(), and this is idempotent. Returns
// false when the partition is unusable, in which case every load below returns the
// defaults and every save fails: the device still detects drowsiness and still
// alerts, it just forgets its broker across a reboot.
bool settings_store_init();

bool settings_store_ready();

// Each loader returns true when a stored record was found AND it validated. On
// false the output is left at its defaults, which is the same behaviour as a fresh
// board - a corrupt record is not a reason to refuse to boot.
bool settings_load_identity(DeviceIdentity *out);
bool settings_load_wifi(WifiStaConfig *out);
bool settings_load_mqtt(MqttConfig *out);

// Validated by the caller before it gets here; these only serialise and write.
bool settings_save_identity(const DeviceIdentity &id);
bool settings_save_wifi(const WifiStaConfig &sta);
bool settings_save_mqtt(const MqttConfig &cfg);

// The CA certificate, kept out of MqttConfig because it is up to 4 kB and that
// struct is copied between tasks. `out` must have room for MQTT_CA_MAX; returns the
// length excluding the NUL, or 0 when there is none stored.
size_t settings_load_ca(char *out, size_t out_cap);
bool settings_save_ca(const char *pem);
bool settings_clear_ca();

// How many bytes the stored certificate has, without reading it. Reported by
// GET /api/mqtt so the page can say "2114 B stored" without shipping the PEM on
// every poll.
size_t settings_ca_bytes();
