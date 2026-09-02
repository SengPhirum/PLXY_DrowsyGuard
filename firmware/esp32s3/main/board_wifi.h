#pragma once
/*
Network bring-up for the headless build.

There is no panel on this board any more, so Wi-Fi is not a convenience feature -
it is the only way to see what the camera sees. The device therefore comes up as
its own access point (SoftAP) rather than waiting to join someone else's network:
a car has no infrastructure Wi-Fi, and a thesis demo has to work in a room where
nobody knows the password. The phone or laptop joins DrowsyGuard-XXXX and browses
to http://192.168.4.1/.

Station mode is optional and additive. Fill in WIFI_STA_SSID and the device runs
AP+STA: it keeps serving its own SSID while also joining the named network, which
is what makes it reachable from a development machine without unplugging from the
lab Wi-Fi. Leave it empty for the in-vehicle configuration.

No GPIOs here. The ESP32-S3 radio is on-die and shares no pins with the DVP
camera bus or the I2S amplifier, which is exactly why dropping the SPI panel frees
the design rather than constraining it.
*/

#include <cstdint>

// --- SoftAP identity -------------------------------------------------------
// The last three bytes of the AP MAC are appended to the SSID, so two boards on
// the same bench are distinguishable without reflashing either of them.
#define WIFI_AP_SSID_PREFIX "DrowsyGuard"

// WPA2 requires at least 8 characters. Set to "" for an open network, which is
// convenient on the bench and wrong in a vehicle: the stream is a live camera
// feed of the driver's face.
#define WIFI_AP_PASSWORD "drowsyguard"

// 6 is a sane default in the middle of the 2.4 GHz band. Channel choice matters
// more than it looks: the camera and the radio share the same power budget, and a
// congested channel means retransmits, which means brownouts on a marginal USB
// supply rather than a slow stream.
#define WIFI_AP_CHANNEL 6

// Only one client can hold the MJPEG stream at a time (see web_server.h), but
// several may sit on the status page, so the AP allows a small group.
#define WIFI_AP_MAX_CLIENTS 4

// --- optional station mode -------------------------------------------------
// Leave WIFI_STA_SSID empty to stay AP-only.
#define WIFI_STA_SSID ""
#define WIFI_STA_PASSWORD ""

struct WifiStatus {
    bool ap_up = false;
    char ap_ssid[33] = {0};
    char ap_ip[16] = {0};
    int ap_clients = 0;
    bool sta_enabled = false;
    bool sta_connected = false;
    char sta_ip[16] = {0};
    int8_t sta_rssi = 0;
};

// Runtime station credentials, which take precedence over WIFI_STA_SSID above when
// `enabled` is set. Pass nullptr to use the compile-time defaults.
//
// This exists because of MQTT. The SoftAP is an island: it has no route to a broker,
// so a device that can only be its own access point can only ever publish to
// something on the same island. Station mode is how it reaches one - and a demo has
// to be able to point it at a phone hotspot in the room without a rebuild, which a
// compile-time #define cannot do.
//
// The access point comes up either way and never comes down. That is the whole
// design: if the hotspot is wrong, the internet is out or the broker refuses the
// credentials, the dashboard is still on 192.168.4.1 and the alerts still sound.
struct WifiStaOverride {
    bool enabled = false;
    const char *ssid = nullptr;
    const char *password = nullptr;
};

// Initialises NVS, the network stack and the radio. Returns false only if the AP
// itself could not be started - the firmware still runs in that case, because the
// alert path does not depend on the network.
bool board_wifi_init(const WifiStaOverride *sta = nullptr);

// Applies station credentials to a running radio, so a broker can be reached without
// a power cycle. Returns true when they took effect immediately.
//
// False means the radio came up access-point-only: the station netif does not exist,
// and creating one after esp_wifi_start() is not something ESP-IDF supports cleanly.
// The caller persists the settings anyway and tells the operator to reboot, which is
// the honest answer - the alternative is a UI that claims to have joined a network
// the radio has no interface for.
bool board_wifi_apply_station(const WifiStaOverride &sta);

// Snapshot of the current network state, for the status endpoint and the log.
void board_wifi_status(WifiStatus *out);
