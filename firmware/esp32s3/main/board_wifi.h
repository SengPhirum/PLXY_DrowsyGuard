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

#include "wifi_provision.h"

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

    // --- provisioning ------------------------------------------------------
    // The SSID the radio is configured for, which is not the same question as
    // whether it joined. A page that only showed "not connected" cannot tell an
    // operator whether the device is trying the network they meant.
    char sta_ssid[33] = {0};
    WifiStaState sta_state = WifiStaState::Disabled;
    // The 802.11 reason code from the last disconnection, and how many attempts have
    // failed since the last success. Together these are what turns "it did not work"
    // into "the passphrase is wrong" - see wifi_disconnect_reason_text().
    uint8_t sta_reason = 0;
    uint32_t sta_attempts = 0;
    // Remaining backoff before the next attempt, in milliseconds. Shown so that a
    // device waiting 60 s does not look like a device that has given up.
    uint32_t sta_retry_ms = 0;
    // Whether the BOOT-button watcher has seen a release and would act on a hold.
    // False on a board with a serial adapter holding GPIO0 low - see
    // wifi_provision.h, which is the only place that hazard is explained in full.
    bool button_armed = false;
    uint32_t button_held_ms = 0;
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
//
// The radio always comes up in AP+STA, whether or not there are credentials, and
// that changed with provisioning. It used to be AP-only until an SSID was stored,
// which made the first boot - the one boot where somebody definitely needs to
// configure Wi-Fi - the one boot where they could neither scan nor join without a
// reboot afterwards. A station interface with nothing configured costs a few
// kilobytes and does not associate; it is what makes scanning possible at all.
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

// --- scanning --------------------------------------------------------------
// Starts a scan and returns immediately. Results arrive on the Wi-Fi event task and
// are collected with board_wifi_scan_results(); nothing here blocks, which is the
// requirement rather than a preference - the capture loop has a 23 ms frame budget
// and a scan takes seconds.
//
// Returns false when a scan is already running or the radio is mid-association.
// esp_wifi_scan_start() refuses in both cases, and reporting that is better than
// letting the page think it asked for something.
//
// ONE COST WORTH KNOWING. Scanning hops the radio across every channel, and the
// SoftAP shares that radio - so for the two or three seconds a scan takes, the
// access point is off its own channel most of the time and the browser's connection
// stalls. It recovers on its own. The page says so before it starts one, because a
// dashboard that freezes with no explanation reads as a crash.
bool board_wifi_scan_start();
bool board_wifi_scan_busy();

// Copies the last completed scan into `out` (room for WIFI_SCAN_MAX), already
// sorted, de-duplicated and with hidden networks dropped by wifi_scan_prepare().
// Returns the count. `age_ms` receives how old the results are, so the page can
// offer a refresh rather than presenting a ten-minute-old list as current.
int board_wifi_scan_results(WifiScanEntry *out, uint32_t *age_ms);

// --- provisioning ----------------------------------------------------------
// Clears the station credentials from the RADIO and stops trying to associate. The
// SoftAP is untouched, which is the entire point: this is the operation most likely
// to be performed by somebody who has locked themselves out, so it must not be able
// to take away the interface they are performing it from.
//
// It does NOT touch NVS - settings_nvs.cpp owns that, and the caller does both so
// that the persisted record and the live radio cannot disagree.
void board_wifi_forget();

// Immediately retries the configured network, resetting the backoff. What the page's
// Reconnect button calls: a device sitting on a 60-second backoff after a router
// reboot should not need a power cycle to notice the router came back.
void board_wifi_reconnect();
