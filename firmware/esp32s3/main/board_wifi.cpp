#include "board_wifi.h"

#include <cstdio>
#include <cstring>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs_flash.h"

#include "wifi_provision.h"

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

// --- provisioning state ----------------------------------------------------
// Written from the Wi-Fi event task and read from the HTTP task, so everything that
// is not a single scalar is behind s_scan_lock. The scalars are plain: a torn read of
// a reason code shows one wrong number on a status page for 200 ms, and a mutex on
// the event task is a worse trade than that.
static WifiStaState s_sta_state = WifiStaState::Disabled;
static uint8_t s_sta_reason = 0;
static uint32_t s_sta_attempts = 0;
static int64_t s_retry_at_us = 0;

// The scan. Two buffers rather than one because ESP-IDF hands back its own record
// type and the page wants ours; the translation happens once, on the event task,
// rather than on every poll of the settings page.
static SemaphoreHandle_t s_scan_lock = nullptr;
static WifiScanEntry s_scan[WIFI_SCAN_MAX];
static int s_scan_count = 0;
static int64_t s_scan_done_us = 0;
static bool s_scanning = false;

// Backoff for the station side, same shape and the same reasoning as the MQTT
// publisher's: a network that is not there should not be dialled every two seconds
// forever by a device whose actual job is inference. Doubling from 2 s to a 60 s
// ceiling, and reset to zero by a success or by the page's Reconnect button.
#define STA_BACKOFF_BASE_MS 2000u
#define STA_BACKOFF_MAX_MS 60000u

static uint32_t sta_backoff_ms(uint32_t attempt) {
    if (attempt == 0) return 0;
    uint32_t d = STA_BACKOFF_BASE_MS;
    for (uint32_t i = 1; i < attempt && d < STA_BACKOFF_MAX_MS; ++i) d *= 2;
    return d > STA_BACKOFF_MAX_MS ? STA_BACKOFF_MAX_MS : d;
}

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

// ESP-IDF's authentication enum into ours. One switch, in the one file that is
// allowed to know about both - wifi_provision.h stays free of ESP-IDF so that the
// scan formatting can be tested on a host.
static WifiAuth auth_from_idf(wifi_auth_mode_t m) {
    switch (m) {
        case WIFI_AUTH_OPEN:            return WifiAuth::Open;
        case WIFI_AUTH_WEP:             return WifiAuth::Wep;
        case WIFI_AUTH_WPA_PSK:         return WifiAuth::WpaPsk;
        case WIFI_AUTH_WPA2_PSK:        return WifiAuth::Wpa2Psk;
        case WIFI_AUTH_WPA_WPA2_PSK:    return WifiAuth::WpaWpa2Psk;
        case WIFI_AUTH_WPA3_PSK:        return WifiAuth::Wpa3Psk;
        case WIFI_AUTH_WPA2_WPA3_PSK:   return WifiAuth::Wpa2Wpa3Psk;
        case WIFI_AUTH_WPA2_ENTERPRISE: return WifiAuth::Enterprise;
        default:                        return WifiAuth::Unknown;
    }
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
                // Guarded now that the station interface always exists: on a board
                // with no credentials there is nothing to connect to, and calling
                // esp_wifi_connect() anyway produces a disconnect event every time
                // it fails, which is a log full of a problem nobody has.
                if (s_sta_wanted) {
                    s_sta_state = WifiStaState::Connecting;
                    esp_wifi_connect();
                }
                break;
            case WIFI_EVENT_STA_DISCONNECTED: {
                // Retry forever rather than giving up - a router that reboots should
                // not require the board to be power-cycled - but on a backoff, and
                // remembering WHY. The reason code is the only thing that separates
                // a wrong passphrase from a network that is simply not switched on,
                // and an operator who cannot tell those apart re-types a correct
                // password until they give up.
                auto *e = static_cast<wifi_event_sta_disconnected_t *>(data);
                s_sta_connected = false;
                if (e != nullptr) s_sta_reason = static_cast<uint8_t>(e->reason);
                if (!s_sta_wanted) {
                    s_sta_state = WifiStaState::Disabled;
                    break;
                }
                ++s_sta_attempts;
                s_sta_state = WifiStaState::Failed;
                const uint32_t wait = sta_backoff_ms(s_sta_attempts);
                s_retry_at_us = esp_timer_get_time() + static_cast<int64_t>(wait) * 1000;
                // The SSID is logged and the passphrase is not, here as everywhere:
                // `./plxy.sh monitor` puts this on a screen in a room with other
                // people in it.
                char reason_text[WIFI_REASON_TEXT_MAX];
                ESP_LOGW(TAG, "could not join \"%s\" (attempt %u): %s; next try in %u ms",
                         s_sta_ssid, static_cast<unsigned>(s_sta_attempts),
                         wifi_disconnect_reason_text(s_sta_reason, reason_text,
                                                     sizeof(reason_text)),
                         static_cast<unsigned>(wait));
                schedule_sta_retry(static_cast<uint64_t>(wait) * 1000);
                break;
            }
            case WIFI_EVENT_SCAN_DONE: {
                // Collected here rather than by whoever asked for the scan, so that
                // nothing outside this file ever blocks on the radio.
                uint16_t found = 0;
                esp_wifi_scan_get_ap_num(&found);
                if (found > WIFI_SCAN_MAX) found = WIFI_SCAN_MAX;
                // ~80 bytes each; static rather than on the event task's stack, which
                // is not generous and is shared with everything else the radio emits.
                static wifi_ap_record_t records[WIFI_SCAN_MAX];
                uint16_t got = found;
                if (found > 0 && esp_wifi_scan_get_ap_records(&got, records) != ESP_OK) {
                    got = 0;
                }
                WifiScanEntry staged[WIFI_SCAN_MAX];
                int n = 0;
                for (uint16_t i = 0; i < got && n < WIFI_SCAN_MAX; ++i) {
                    WifiScanEntry &s = staged[n];
                    s = WifiScanEntry{};
                    snprintf(s.ssid, sizeof(s.ssid), "%s",
                             reinterpret_cast<const char *>(records[i].ssid));
                    s.rssi = records[i].rssi;
                    s.channel = records[i].primary;
                    s.auth = auth_from_idf(records[i].authmode);
                    ++n;
                }
                n = wifi_scan_prepare(staged, n);
                if (s_scan_lock != nullptr &&
                    xSemaphoreTake(s_scan_lock, pdMS_TO_TICKS(100)) == pdTRUE) {
                    for (int i = 0; i < n; ++i) s_scan[i] = staged[i];
                    s_scan_count = n;
                    s_scan_done_us = esp_timer_get_time();
                    xSemaphoreGive(s_scan_lock);
                }
                s_scanning = false;
                ESP_LOGI(TAG, "scan finished: %d network%s", n, n == 1 ? "" : "s");
                // A scan suspends the association attempt, so pick it back up.
                if (s_sta_wanted && !s_sta_connected) schedule_sta_retry(200 * 1000);
                break;
            }
            default:
                break;
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        auto *e = static_cast<ip_event_got_ip_t *>(data);
        s_sta_connected = true;
        s_sta_state = WifiStaState::Connected;
        // Cleared on success, so the page shows the failure that is current rather
        // than the last one that ever happened.
        s_sta_attempts = 0;
        s_sta_reason = 0;
        s_retry_at_us = 0;
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

    s_scan_lock = xSemaphoreCreateMutex();

    s_ap_netif = esp_netif_create_default_wifi_ap();
    // ALWAYS, whether or not there are credentials. See the header: a station
    // interface is what scanning needs, and without one the first boot - the one
    // boot where somebody definitely has Wi-Fi to configure - is the one boot where
    // they cannot scan or join without rebooting afterwards.
    s_sta_netif = esp_netif_create_default_wifi_sta();
    s_sta_enabled = true;

    // "Wanted" is the separate question of whether there is anything to join.
    s_sta_wanted = have_override || sta_configured();
    if (have_override) {
        snprintf(s_sta_ssid, sizeof(s_sta_ssid), "%s", sta_override->ssid);
    } else if (sta_configured()) {
        snprintf(s_sta_ssid, sizeof(s_sta_ssid), "%s", WIFI_STA_SSID);
    }
    s_sta_state = s_sta_wanted ? WifiStaState::Idle : WifiStaState::Disabled;

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&init) != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_init failed");
        return false;
    }
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event,
                                       nullptr, nullptr);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi_event,
                                       nullptr, nullptr);

    // APSTA unconditionally, for the reason above. The access point half of this is
    // what every other subsystem's fallback story depends on and it never comes down.
    if (esp_wifi_set_mode(WIFI_MODE_APSTA) != ESP_OK) return false;

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

    if (s_sta_wanted) {
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
    if (s_sta_wanted) {
        ESP_LOGI(TAG, "also joining \"%s\" (%s)", s_sta_ssid,
                 have_override ? "from the stored settings" : "compiled in");
    } else {
        ESP_LOGI(TAG, "no station credentials stored - provisioning mode. "
                      "Open http://%s/ and use the Wi-Fi card, or hold BOOT for "
                      "%d s to clear a bad one.", st.ap_ip, WIFI_BUTTON_HOLD_MS / 1000);
    }
    return true;
}

bool board_wifi_apply_station(const WifiStaOverride &sta) {
    // The station interface always exists now (see board_wifi_init), so this no
    // longer has a "you must reboot" path. It kept the boolean return because
    // callers and the API contract are written around it, and because a radio that
    // failed to come up at all still cannot be configured.
    if (!s_sta_enabled || s_sta_netif == nullptr) return false;

    s_sta_wanted = sta.enabled && sta.ssid != nullptr && sta.ssid[0] != '\0';
    if (s_sta_retry != nullptr) esp_timer_stop(s_sta_retry);
    s_sta_attempts = 0;
    s_sta_reason = 0;
    s_retry_at_us = 0;

    if (!s_sta_wanted) {
        esp_wifi_disconnect();
        s_sta_connected = false;
        s_sta_ssid[0] = '\0';
        s_sta_state = WifiStaState::Disabled;
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
    s_sta_state = WifiStaState::Connecting;
    // The SSID is logged; the password is not, here or anywhere else. `./plxy.sh
    // monitor` puts this log on a screen in a room with other people in it.
    ESP_LOGI(TAG, "joining \"%s\" (applied without a reboot)", s_sta_ssid);
    schedule_sta_retry(200 * 1000);
    return true;
}

void board_wifi_forget() {
    // Radio only. The stored record is settings_nvs.cpp's, and the caller clears
    // both so that flash and the live radio cannot end up disagreeing.
    s_sta_wanted = false;
    if (s_sta_retry != nullptr) esp_timer_stop(s_sta_retry);
    esp_wifi_disconnect();
    // The configuration is blanked as well as disconnected: leaving the SSID in the
    // radio means the next esp_wifi_connect() from anywhere - a driver-internal
    // retry, a mode change - silently rejoins the network somebody just asked to
    // forget.
    wifi_config_t blank = {};
    esp_wifi_set_config(WIFI_IF_STA, &blank);
    s_sta_connected = false;
    s_sta_ssid[0] = '\0';
    s_sta_state = WifiStaState::Disabled;
    s_sta_attempts = 0;
    s_sta_reason = 0;
    s_retry_at_us = 0;
    // The access point is deliberately untouched. This is the operation most likely
    // to be run by somebody who has locked themselves out, so it must not be able to
    // remove the interface they are running it from.
    ESP_LOGW(TAG, "station credentials cleared; the access point is still up and "
                  "the device is back in provisioning mode");
}

void board_wifi_reconnect() {
    if (!s_sta_wanted) return;
    s_sta_attempts = 0;
    s_sta_reason = 0;
    s_retry_at_us = 0;
    s_sta_state = WifiStaState::Connecting;
    esp_wifi_disconnect();
    schedule_sta_retry(100 * 1000);
    ESP_LOGI(TAG, "retrying \"%s\" now", s_sta_ssid);
}

bool board_wifi_scan_busy() { return s_scanning; }

bool board_wifi_scan_start() {
    if (!s_ap_up || s_sta_netif == nullptr) return false;
    if (s_scanning) return false;

    // Active scan, bounded per channel. 80-200 ms across thirteen channels is two to
    // three seconds - long enough to find a hotspot two rooms away, short enough that
    // the SoftAP being off-channel reads as a pause rather than a disconnection.
    wifi_scan_config_t cfg = {};
    cfg.show_hidden = false;
    cfg.scan_type = WIFI_SCAN_TYPE_ACTIVE;
    cfg.scan_time.active.min = 80;
    cfg.scan_time.active.max = 200;

    // A scan cannot run while the radio is mid-association: esp_wifi_scan_start()
    // returns ESP_ERR_WIFI_STATE and the page would show a failure it cannot act on.
    // Stopping the attempt first is the honest fix - the retry is rescheduled when
    // the scan completes.
    if (s_sta_wanted && !s_sta_connected) {
        if (s_sta_retry != nullptr) esp_timer_stop(s_sta_retry);
        esp_wifi_disconnect();
    }
    const esp_err_t err = esp_wifi_scan_start(&cfg, false);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "scan refused: %s", esp_err_to_name(err));
        if (s_sta_wanted && !s_sta_connected) schedule_sta_retry(500 * 1000);
        return false;
    }
    s_scanning = true;
    return true;
}

int board_wifi_scan_results(WifiScanEntry *out, uint32_t *age_ms) {
    if (out == nullptr) return 0;
    int n = 0;
    if (s_scan_lock != nullptr &&
        xSemaphoreTake(s_scan_lock, pdMS_TO_TICKS(100)) == pdTRUE) {
        n = s_scan_count;
        for (int i = 0; i < n; ++i) out[i] = s_scan[i];
        if (age_ms != nullptr) {
            *age_ms = s_scan_done_us == 0 ? 0
                : static_cast<uint32_t>((esp_timer_get_time() - s_scan_done_us) / 1000);
        }
        xSemaphoreGive(s_scan_lock);
    } else if (age_ms != nullptr) {
        *age_ms = 0;
    }
    return n;
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

    // "Enabled" is now "there are credentials to try", not "the interface exists" -
    // the interface always exists. Everything downstream of this field, including
    // the MQTT card and the status page, means the first thing.
    out->sta_enabled = s_sta_wanted;
    out->sta_connected = s_sta_connected;
    snprintf(out->sta_ssid, sizeof(out->sta_ssid), "%s", s_sta_ssid);
    out->sta_state = s_sta_state;
    out->sta_reason = s_sta_reason;
    out->sta_attempts = s_sta_attempts;
    const int64_t now_us = esp_timer_get_time();
    out->sta_retry_ms = (s_retry_at_us > now_us)
        ? static_cast<uint32_t>((s_retry_at_us - now_us) / 1000) : 0;
    if (s_sta_connected) {
        netif_ip(s_sta_netif, out->sta_ip, sizeof(out->sta_ip));
        wifi_ap_record_t ap{};
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) out->sta_rssi = ap.rssi;
    } else {
        snprintf(out->sta_ip, sizeof(out->sta_ip), "0.0.0.0");
    }
}
