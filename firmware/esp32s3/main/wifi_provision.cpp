#include "wifi_provision.h"

#include <cstdio>
#include <cstring>

#include "device_config.h"   // settings_json_escape_utf8, settings_copy

// Pure, and it stays that way - see the header. Anything here that needs a radio or a
// GPIO belongs in board_wifi.cpp or board_button.cpp instead.

// --- the button ------------------------------------------------------------
const char *wifi_button_event_name(WifiButtonEvent e) {
    switch (e) {
        case WifiButtonEvent::None:      return "none";
        case WifiButtonEvent::Warning:   return "warning";
        case WifiButtonEvent::Fired:     return "fired";
        case WifiButtonEvent::Cancelled: return "cancelled";
        case WifiButtonEvent::Stuck:     return "stuck";
    }
    return "none";
}

void ButtonWatch::reset() { *this = ButtonWatch{}; }

WifiButtonEvent ButtonWatch::update(bool pressed_raw, uint32_t now_ms) {
    // Debounce first, and on the RAW level rather than on transitions: a bouncing
    // contact produces a burst of edges, and counting edges is how a single press
    // becomes three.
    if (pressed_raw != raw_) {
        raw_ = pressed_raw;
        raw_since_ms_ = now_ms;
        stable_ = false;
    } else if (!stable_ && now_ms - raw_since_ms_ >= WIFI_BUTTON_DEBOUNCE_MS) {
        stable_ = true;
        if (level_ != raw_) {
            level_ = raw_;
            if (level_) {
                press_start_ms_ = now_ms;
                warned_ = false;
                fired_ = false;
                stuck_ = false;
            }
        }
    }
    // No seeding of raw_since_ms_ is needed for the first call: the member defaults
    // are "released since t=0", which is what a board with nobody touching it looks
    // like, and a board with the pin already low takes the transition branch above.

    // Nothing at all during the boot window. GPIO0 is a strapping pin through reset
    // and the auto-reset lines can still be settling.
    if (now_ms < WIFI_BUTTON_ARM_AFTER_MS) {
        held_ms_ = 0;
        return WifiButtonEvent::None;
    }

    // Arming: a debounced RELEASE has to be seen before any press is believed. This
    // is the rule that makes the serial-adapter hazard harmless - a pin held low
    // from boot never gets here, so it can never fire.
    if (!armed_) {
        if (stable_ && !level_) {
            armed_ = true;
        } else if (stable_ && level_ && !stuck_ && now_ms >= WIFI_BUTTON_STUCK_MS) {
            // Low since boot and still low. Say so once: silence here looks like a
            // broken feature, and the cause is almost always a serial port being
            // held open rather than a fault.
            stuck_ = true;
            return WifiButtonEvent::Stuck;
        }
        held_ms_ = 0;
        return WifiButtonEvent::None;
    }

    if (!stable_ || !level_) {
        // Released. A press that got as far as a warning is worth a line - the
        // operator started something and stopped it - but a brush that never
        // reached two seconds is not an event at all.
        const bool was_warned = warned_ && !fired_;
        held_ms_ = 0;
        warned_ = false;
        fired_ = false;
        stuck_ = false;
        return was_warned ? WifiButtonEvent::Cancelled : WifiButtonEvent::None;
    }

    // Before the update, so a press that has been declared stuck freezes its
    // counter rather than showing a number that climbs forever on the page.
    if (stuck_) return WifiButtonEvent::None;
    held_ms_ = now_ms - press_start_ms_;

    if (!fired_ && held_ms_ >= WIFI_BUTTON_HOLD_MS) {
        fired_ = true;
        return WifiButtonEvent::Fired;
    }
    if (held_ms_ >= WIFI_BUTTON_STUCK_MS) {
        // Held long past the erase. Not an error - the operator is just slow to let
        // go, and the erase already happened - but stop counting.
        stuck_ = true;
        return WifiButtonEvent::None;
    }
    if (!warned_ && held_ms_ >= WIFI_BUTTON_WARN_MS) {
        warned_ = true;
        return WifiButtonEvent::Warning;
    }
    return WifiButtonEvent::None;
}

// --- scan results ----------------------------------------------------------
const char *wifi_auth_name(WifiAuth a) {
    switch (a) {
        case WifiAuth::Open:        return "open";
        case WifiAuth::Wep:         return "wep";
        case WifiAuth::WpaPsk:      return "wpa";
        case WifiAuth::Wpa2Psk:     return "wpa2";
        case WifiAuth::WpaWpa2Psk:  return "wpa/wpa2";
        case WifiAuth::Wpa3Psk:     return "wpa3";
        case WifiAuth::Wpa2Wpa3Psk: return "wpa2/wpa3";
        case WifiAuth::Enterprise:  return "enterprise";
        case WifiAuth::Unknown:     return "unknown";
    }
    return "unknown";
}

bool wifi_auth_is_open(WifiAuth a) { return a == WifiAuth::Open; }

int wifi_rssi_bars(int8_t rssi) {
    if (rssi >= -55) return 4;
    if (rssi >= -67) return 3;
    if (rssi >= -75) return 2;
    if (rssi >= -85) return 1;
    // Below -85 an association usually succeeds and then drops, which is worse than
    // failing outright, so it reads as no signal rather than as one bar.
    return 0;
}

int wifi_scan_prepare(WifiScanEntry *entries, int n) {
    if (entries == nullptr || n <= 0) return 0;
    if (n > WIFI_SCAN_MAX) n = WIFI_SCAN_MAX;

    // Drop hidden networks. An access point with no SSID in its beacon cannot be
    // joined from a list - there is nothing to show and nothing to select - and a row
    // of blank entries is worse than a shorter list.
    int keep = 0;
    for (int i = 0; i < n; ++i) {
        if (entries[i].ssid[0] != '\0') {
            if (keep != i) entries[keep] = entries[i];
            ++keep;
        }
    }

    // Insertion sort, strongest first. Two dozen entries at most, and it is stable,
    // which keeps the channel order deterministic for identical signal strengths -
    // a list that reshuffles between scans is a list nobody can click on.
    for (int i = 1; i < keep; ++i) {
        const WifiScanEntry key = entries[i];
        int j = i - 1;
        while (j >= 0 && entries[j].rssi < key.rssi) {
            entries[j + 1] = entries[j];
            --j;
        }
        entries[j + 1] = key;
    }

    // Collapse duplicates. A mesh or a dual-band router answers on several channels
    // and would otherwise fill the list with one name. The list is already sorted, so
    // the first occurrence is the strongest - which is the one the radio will
    // actually associate with.
    int out = 0;
    for (int i = 0; i < keep; ++i) {
        bool seen = false;
        for (int j = 0; j < out; ++j) {
            if (strncmp(entries[j].ssid, entries[i].ssid, WIFI_SSID_LEN) == 0) {
                seen = true;
                break;
            }
        }
        if (!seen) {
            if (out != i) entries[out] = entries[i];
            ++out;
        }
    }
    return out;
}

size_t wifi_scan_json(const WifiScanEntry *entries, int n, bool scanning,
                      uint32_t age_ms, char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return 0;
    out[0] = '\0';
    if (entries == nullptr) n = 0;
    if (n < 0) n = 0;
    if (n > WIFI_SCAN_MAX) n = WIFI_SCAN_MAX;

    int at = snprintf(out, out_cap, "{\"scanning\":%s,\"age_ms\":%lu,\"networks\":[",
                      scanning ? "true" : "false",
                      static_cast<unsigned long>(age_ms));
    if (at <= 0 || static_cast<size_t>(at) >= out_cap) {
        out[0] = '\0';
        return 0;
    }

    for (int i = 0; i < n; ++i) {
        // An SSID is 32 bytes chosen by whoever owns the access point, and anybody in
        // radio range of this device can broadcast one. It reaches the device's own
        // configuration page, so it is escaped rather than trusted.
        char ssid[WIFI_SSID_LEN * 6 + 1];
        if (!settings_json_escape_utf8(entries[i].ssid, ssid, sizeof(ssid))) {
            out[0] = '\0';
            return 0;
        }
        const int w = snprintf(out + at, out_cap - at,
                               "%s{\"ssid\":\"%s\",\"rssi\":%d,\"bars\":%d,"
                               "\"channel\":%u,\"auth\":\"%s\",\"open\":%s}",
                               i ? "," : "", ssid, static_cast<int>(entries[i].rssi),
                               wifi_rssi_bars(entries[i].rssi),
                               static_cast<unsigned>(entries[i].channel),
                               wifi_auth_name(entries[i].auth),
                               wifi_auth_is_open(entries[i].auth) ? "true" : "false");
        if (w <= 0 || static_cast<size_t>(at + w) >= out_cap) {
            // Half a list is worse than none: the page would render it as the whole
            // list and the network somebody wanted would simply not be there.
            out[0] = '\0';
            return 0;
        }
        at += w;
    }

    const int w = snprintf(out + at, out_cap - at, "]}");
    if (w <= 0 || static_cast<size_t>(at + w) >= out_cap) {
        out[0] = '\0';
        return 0;
    }
    return static_cast<size_t>(at + w);
}

// --- what went wrong -------------------------------------------------------
const char *wifi_sta_state_name(WifiStaState s) {
    switch (s) {
        case WifiStaState::Disabled:   return "disabled";
        case WifiStaState::Idle:       return "idle";
        case WifiStaState::Connecting: return "connecting";
        case WifiStaState::Connected:  return "connected";
        case WifiStaState::Failed:     return "failed";
    }
    return "disabled";
}

// The 802.11 / ESP-IDF reason codes worth naming. Deliberately not exhaustive:
// `wifi_err_reason_t` has around sixty values and most of them describe a protocol
// state nobody configuring a hotspot can act on. What is here is the set that maps to
// something an operator can actually do.
bool wifi_reason_is_auth_failure(uint8_t reason) {
    switch (reason) {
        case 2:    // AUTH_EXPIRE - the usual answer to a wrong PSK on WPA2
        case 15:   // 4WAY_HANDSHAKE_TIMEOUT - the other usual answer
        case 204:  // HANDSHAKE_TIMEOUT
        case 205:  // CONNECTION_FAIL
            return true;
        default:
            return false;
    }
}

const char *wifi_disconnect_reason_text(uint8_t reason, char *buf, size_t cap) {
    switch (reason) {
        case 1:   return "the access point ended the connection without saying why";
        case 2:   return "wrong password - the access point rejected the passphrase";
        case 4:   return "the access point timed out waiting for this device";
        case 8:   return "the access point disassociated this device";
        case 15:  return "wrong password - the four-way handshake was refused";
        case 39:  return "the access point closed the connection (timeout)";
        case 200: return "the access point requires a passphrase and none was given";
        case 201: return "no access point with that name is in range";
        case 202: return "the authentication method is not supported";
        case 203: return "the association was refused - the access point may be full";
        case 204: return "wrong password - the handshake timed out";
        case 205: return "wrong password, or the access point refused this device";
        default:  break;
    }
    // Everything else still carries the number, because a code is something to look
    // up and "unknown error" is not. Into the caller's buffer - see the header.
    if (buf == nullptr || cap == 0) return "the connection failed";
    snprintf(buf, cap, "the connection failed (802.11 reason %u)",
             static_cast<unsigned>(reason));
    return buf;
}
