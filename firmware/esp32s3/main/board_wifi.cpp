#include "board_wifi.h"

#include <cstdio>
#include <cstring>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "nvs_flash.h"

static const char *TAG = "wifi";

static esp_netif_t *s_ap_netif = nullptr;
static esp_netif_t *s_sta_netif = nullptr;
static bool s_ap_up = false;
static bool s_sta_enabled = false;
static bool s_sta_connected = false;
static char s_ap_ssid[33] = {0};
// The SSID actually joined, whichever source it came from. Held so the log line and
// the disconnect handler name the right network - they used to print WIFI_STA_SSID
// unconditionally, which would have been a lie for every runtime-configured device.
static char s_sta_ssid[33] = {0};
// Whether a station connection is currently *wanted*. Separate from s_sta_enabled,
// which says whether the interface exists: switching station mode off at runtime has
// to stop the reconnect timer, and without this flag the disconnect handler would
// dutifully redial the network the operator just removed, forever.
static bool s_sta_wanted = false;

static bool sta_configured() { return sizeof(WIFI_STA_SSID) > 1; }

// Station reconnects are deferred through a timer rather than slept for inside
// the event handler. Blocking there blocks the whole default event loop - which
// also carries the SoftAP's join and leave events - so a router that went away
// would stall the half of the system that still works.
static esp_timer_handle_t s_sta_retry = nullptr;

static void sta_retry_cb(void *) { esp_wifi_connect(); }

static void schedule_sta_retry(uint64_t delay_us) {
    if (s_sta_retry == nullptr) {
        esp_timer_create_args_t args = {};
        args.callback = sta_retry_cb;
        args.dispatch_method = ESP_TIMER_TASK;
        args.name = "sta_retry";
        if (esp_timer_create(&args, &s_sta_retry) != ESP_OK) {
            esp_wifi_connect();   // no timer: retry now rather than never
            return;
        }
    }
    esp_timer_stop(s_sta_retry);
    esp_timer_start_once(s_sta_retry, delay_us);
}

static void on_wifi_event(void *, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT) {
        switch (id) {
            case WIFI_EVENT_AP_STACONNECTED: {
                auto *e = static_cast<wifi_event_ap_staconnected_t *>(data);
                ESP_LOGI(TAG, "client joined: " MACSTR " (aid %d)", MAC2STR(e->mac), e->aid);
                break;
            }
            case WIFI_EVENT_AP_STADISCONNECTED: {
                auto *e = static_cast<wifi_event_ap_stadisconnected_t *>(data);
                ESP_LOGI(TAG, "client left: " MACSTR " (aid %d)", MAC2STR(e->mac), e->aid);
                break;
            }
            case WIFI_EVENT_STA_START:
                esp_wifi_connect();
                break;
            case WIFI_EVENT_STA_DISCONNECTED:
                // Retry forever rather than giving up: station mode is a
                // development convenience, and a router that reboots should not
                // require the board to be power-cycled. The AP side is unaffected
                // either way, so the retry loop can never take the preview down.
                s_sta_connected = false;
                if (s_sta_wanted) schedule_sta_retry(2 * 1000 * 1000);
                break;
            default:
                break;
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        auto *e = static_cast<ip_event_got_ip_t *>(data);
        s_sta_connected = true;
        ESP_LOGI(TAG, "joined \"%s\"; preview also at http://" IPSTR "/", s_sta_ssid,
                 IP2STR(&e->ip_info.ip));
    }
}

// NVS holds the radio's calibration data. Without it esp_wifi_init() fails, and
// the failure reads as a broken radio rather than an unformatted partition.
static bool nvs_ready() {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "nvs partition needs erasing; doing it now");
        if (nvs_flash_erase() != ESP_OK) return false;
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_flash_init: %s", esp_err_to_name(err));
        return false;
    }
    return true;
}

// A bounded copy into one of the radio's fixed-width credential fields, matching the
// idiom the AP path above already uses and for the same reason: the fields are 32 and
// 64 bytes and are *not* required to be NUL-terminated when full, so snprintf's
// truncation semantics are both wrong here and something GCC rejects outright
// (-Werror=format-truncation) once the source is a runtime string rather than a
// literal. The caller's structs are zero-initialised, so the tail is already NUL.
static void radio_field(uint8_t *field, size_t cap, const char *src) {
    if (src == nullptr) return;
    const size_t n = strnlen(src, cap);
    memcpy(field, src, n);
}

bool board_wifi_init(const WifiStaOverride *sta_override) {
    if (!nvs_ready()) return false;

    // The stored configuration wins over the compile-time one when it is enabled,
    // and is ignored entirely when it is not - so a board that has never been
    // configured behaves exactly as it did before this existed.
    const bool have_override = sta_override != nullptr && sta_override->enabled &&
                               sta_override->ssid != nullptr &&
                               sta_override->ssid[0] != '\0';

    if (esp_netif_init() != ESP_OK) return false;
    if (esp_event_loop_create_default() != ESP_OK) return false;

    s_ap_netif = esp_netif_create_default_wifi_ap();
    s_sta_enabled = have_override || sta_configured();
    s_sta_wanted = s_sta_enabled;
    if (have_override) {
        snprintf(s_sta_ssid, sizeof(s_sta_ssid), "%s", sta_override->ssid);
    } else if (sta_configured()) {
        snprintf(s_sta_ssid, sizeof(s_sta_ssid), "%s", WIFI_STA_SSID);
    }
    if (s_sta_enabled) s_sta_netif = esp_netif_create_default_wifi_sta();

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&init) != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_init failed");
        return false;
    }
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event,
                                       nullptr, nullptr);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi_event,
                                       nullptr, nullptr);

    if (esp_wifi_set_mode(s_sta_enabled ? WIFI_MODE_APSTA : WIFI_MODE_AP) != ESP_OK) {
        return false;
    }

    // Name the AP after the radio's own MAC. Anything else needs provisioning to
    // stay unique, and provisioning needs a UI the board no longer has.
    uint8_t mac[6] = {0};
    esp_wifi_get_mac(WIFI_IF_AP, mac);
    snprintf(s_ap_ssid, sizeof(s_ap_ssid), WIFI_AP_SSID_PREFIX "-%02X%02X%02X",
             mac[3], mac[4], mac[5]);

    wifi_config_t ap = {};
    // A bounded copy, not a string copy: the SSID field is exactly 32 bytes and is
    // length-delimited by ssid_len rather than NUL-terminated. Writing it this way
    // says so, and keeps -Wstringop-truncation from flagging a copy that cannot
    // actually truncate (the SSID is a fixed prefix plus six hex digits).
    const size_t ssid_len = strnlen(s_ap_ssid, sizeof(ap.ap.ssid));
    memcpy(ap.ap.ssid, s_ap_ssid, ssid_len);
    ap.ap.ssid_len = static_cast<uint8_t>(ssid_len);
    ap.ap.channel = WIFI_AP_CHANNEL;
    ap.ap.max_connection = WIFI_AP_MAX_CLIENTS;
    ap.ap.beacon_interval = 100;
    if (sizeof(WIFI_AP_PASSWORD) > 1) {
        snprintf(reinterpret_cast<char *>(ap.ap.password), sizeof(ap.ap.password),
                 "%s", WIFI_AP_PASSWORD);
        ap.ap.authmode = WIFI_AUTH_WPA2_PSK;
    } else {
        ap.ap.authmode = WIFI_AUTH_OPEN;
    }
    if (esp_wifi_set_config(WIFI_IF_AP, &ap) != ESP_OK) return false;

    if (s_sta_enabled) {
        wifi_config_t sta = {};
        const char *pass = have_override ? (sta_override->password != nullptr
                                                ? sta_override->password : "")
                                         : WIFI_STA_PASSWORD;
        radio_field(sta.sta.ssid, sizeof(sta.sta.ssid), s_sta_ssid);
        radio_field(sta.sta.password, sizeof(sta.sta.password), pass);
        if (esp_wifi_set_config(WIFI_IF_STA, &sta) != ESP_OK) return false;
    }

    if (esp_wifi_start() != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_start failed");
        return false;
    }
    // Power save off. With it on, the radio sleeps between beacons and the MJPEG
    // stream arrives in bursts a couple of hundred milliseconds apart, which looks
    // exactly like a camera that cannot keep up.
    esp_wifi_set_ps(WIFI_PS_NONE);

    s_ap_up = true;
    WifiStatus st{};
    board_wifi_status(&st);
    ESP_LOGI(TAG, "SoftAP \"%s\" up on channel %d, %s", s_ap_ssid, WIFI_AP_CHANNEL,
             sizeof(WIFI_AP_PASSWORD) > 1 ? "WPA2" : "OPEN");
    ESP_LOGI(TAG, "join it, then open http://%s/", st.ap_ip);
    // The SSID is logged; the password never is. That is not paranoia about this
    // one value - it is that `./plxy.sh monitor` puts this log on a screen in a room
    // with other people in it, and the serial log is the least protected surface on
    // the device.
    if (s_sta_enabled) {
        ESP_LOGI(TAG, "also joining \"%s\" (%s)", s_sta_ssid,
                 have_override ? "from the stored settings" : "compiled in");
    }
    return true;
}

bool board_wifi_apply_station(const WifiStaOverride &sta) {
    // No station interface, so there is nothing to configure. Reported rather than
    // faked: esp_netif_create_default_wifi_sta() after esp_wifi_start() means
    // reworking the mode of a running radio, and a half-applied radio change on the
    // device that is also serving the page is not a trade worth making.
    if (!s_sta_enabled || s_sta_netif == nullptr) return false;

    s_sta_wanted = sta.enabled && sta.ssid != nullptr && sta.ssid[0] != '\0';
    if (s_sta_retry != nullptr) esp_timer_stop(s_sta_retry);

    if (!s_sta_wanted) {
        esp_wifi_disconnect();
        s_sta_connected = false;
        s_sta_ssid[0] = '\0';
        ESP_LOGI(TAG, "station mode switched off; the access point is unaffected");
        return true;
    }

    snprintf(s_sta_ssid, sizeof(s_sta_ssid), "%s", sta.ssid);
    wifi_config_t cfg = {};
    radio_field(cfg.sta.ssid, sizeof(cfg.sta.ssid), s_sta_ssid);
    radio_field(cfg.sta.password, sizeof(cfg.sta.password), sta.password);
    if (esp_wifi_set_config(WIFI_IF_STA, &cfg) != ESP_OK) return false;
    // Disconnect first: esp_wifi_connect() on an already-associated radio returns
    // ESP_ERR_WIFI_CONN rather than switching networks, which would look like the new
    // credentials had been accepted and quietly kept the old association.
    esp_wifi_disconnect();
    s_sta_connected = false;
    // The SSID is logged; the password is not, here or anywhere else. `./plxy.sh
    // monitor` puts this log on a screen in a room with other people in it.
    ESP_LOGI(TAG, "joining \"%s\" (applied without a reboot)", s_sta_ssid);
    schedule_sta_retry(200 * 1000);
    return true;
}

static void netif_ip(esp_netif_t *netif, char *out, size_t n) {
    esp_netif_ip_info_t ip{};
    if (netif != nullptr && esp_netif_get_ip_info(netif, &ip) == ESP_OK) {
        snprintf(out, n, IPSTR, IP2STR(&ip.ip));
    } else {
        snprintf(out, n, "0.0.0.0");
    }
}

void board_wifi_status(WifiStatus *out) {
    if (out == nullptr) return;
    *out = WifiStatus{};
    out->ap_up = s_ap_up;
    snprintf(out->ap_ssid, sizeof(out->ap_ssid), "%s", s_ap_ssid);
    netif_ip(s_ap_netif, out->ap_ip, sizeof(out->ap_ip));

    if (s_ap_up) {
        wifi_sta_list_t list{};
        if (esp_wifi_ap_get_sta_list(&list) == ESP_OK) out->ap_clients = list.num;
    }

    out->sta_enabled = s_sta_enabled;
    out->sta_connected = s_sta_connected;
    if (s_sta_connected) {
        netif_ip(s_sta_netif, out->sta_ip, sizeof(out->sta_ip));
        wifi_ap_record_t ap{};
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) out->sta_rssi = ap.rssi;
    } else {
        snprintf(out->sta_ip, sizeof(out->sta_ip), "0.0.0.0");
    }
}
