#pragma once
/*
Wi-Fi provisioning: the button, the scan list, and the words for what went wrong.

No ESP-IDF headers, on purpose - the same split as mqtt_config.h. Everything here is
a state machine, a sort, or a string, and tests/test_wifi_provision.py compiles it on
the host and drives it directly. board_wifi.cpp and board_button.cpp are the halves
that need a radio and a GPIO, and they own no logic of their own.

WHY A PROVISIONING FLOW AT ALL
------------------------------
Station credentials arrived with MQTT, as a pair of text boxes: type an SSID exactly,
type a passphrase, save, and find out from a connection error whether either was
right. That is serviceable for one developer with one hotspot and unusable for
anyone else, because the three things that go wrong are all invisible:

  * the SSID was typed wrong, or has a character that is easy to mistype;
  * the passphrase was wrong, which looks exactly like the SSID being wrong;
  * the network is simply not in range, which looks like both of the above.

So: scan and pick from a list, and when a join fails, say which of those it was.
`wifi_disconnect_reason_text()` is the whole point of the third one.

THE BUTTON, AND THE HAZARD IT HAS TO SURVIVE
--------------------------------------------
A device whose credentials are wrong cannot be fixed over the network it cannot
join - so there has to be a physical way back. BOOT (GPIO0) is the only button on
this board, it is not otherwise used by the application, and it is a strapping pin
only during reset.

That last part is where the danger is, and it is specific to this board. From the
firmware README: pyserial de-asserts DTR when it opens a port, and this board's
auto-reset lines are **inverted**, so opening a serial port pulls GPIO0 low - which
is electrically identical to somebody holding BOOT down. A naive "pin low for five
seconds means wipe the credentials" would therefore erase the Wi-Fi configuration
of any board somebody attached a terminal to.

ButtonWatch is built around that. Three rules, and the second is the one that
matters:

  1. it ignores the pin entirely for the first WIFI_BUTTON_ARM_AFTER_MS after boot,
     which covers the strapping window and any reset-line transient;
  2. it requires the pin to be observed RELEASED, debounced, before any press
     counts at all. A pin held low from boot - the serial-port case - never arms,
     no matter how long it stays there;
  3. it gives up on a pin that has been low for WIFI_BUTTON_STUCK_MS, on the
     grounds that no human holds a button for half a minute and a short circuit
     does.

On top of that the press is staged: nothing is logged until two seconds, and the
erase does not happen until five. A brush against the button produces no event at
all; a deliberate press produces a warning the operator can act on before anything
is lost.
*/

#include <cstddef>
#include <cstdint>

// --- the button ------------------------------------------------------------
// Polled, not interrupt-driven. The debounce and the long-press both need a clock
// anyway, an interrupt on a bouncing mechanical contact is a burst of interrupts,
// and 20 Hz on a task that does nothing else costs less than the ISR would.
#define WIFI_BUTTON_POLL_MS 50

// A level has to hold for this long before it is believed. 50 ms is past the bounce
// of every tactile switch worth buying and far below the reaction time of anyone
// pressing deliberately.
#define WIFI_BUTTON_DEBOUNCE_MS 50

// Nothing counts until this long after boot. GPIO0 is a strapping pin during reset,
// and the reset lines can still be settling.
#define WIFI_BUTTON_ARM_AFTER_MS 3000

// "Something is happening." Logged, and shown on the page, so a press that was not
// meant can be released before it costs anything.
#define WIFI_BUTTON_WARN_MS 2000

// The erase. Five seconds of continuous, deliberate holding.
#define WIFI_BUTTON_HOLD_MS 5000

// Past this the pin is assumed shorted or held by something that is not a person,
// and the watcher refuses to act until it goes high again. Nobody holds a button
// for half a minute; a solder bridge does it indefinitely.
#define WIFI_BUTTON_STUCK_MS 30000

enum class WifiButtonEvent : uint8_t {
    None = 0,
    // Held past WIFI_BUTTON_WARN_MS. Emitted once per press.
    Warning = 1,
    // Held past WIFI_BUTTON_HOLD_MS. Emitted once per press; this is the erase.
    Fired = 2,
    // Released after Warning but before Fired. Emitted so the log can say the
    // operator changed their mind rather than going silent.
    Cancelled = 3,
    // Low past WIFI_BUTTON_STUCK_MS without ever firing, or low from boot and
    // therefore never armed. Emitted once, then nothing until the pin goes high.
    Stuck = 4,
};

const char *wifi_button_event_name(WifiButtonEvent e);

class ButtonWatch {
  public:
    void reset();

    // `pressed_raw` is the debounced-in-hardware-by-nothing pin level, already
    // inverted so that true means "the button is down". `now_ms` is monotonic
    // milliseconds since boot. Call it every WIFI_BUTTON_POLL_MS; the maths does not
    // depend on the interval being exact, only on it being smaller than the
    // debounce.
    WifiButtonEvent update(bool pressed_raw, uint32_t now_ms);

    // True once a debounced release has been observed, i.e. the pin has proved it is
    // a button rather than a serial adapter holding the line down.
    bool armed() const { return armed_; }
    bool pressed() const { return stable_ && level_; }
    // How long the current press has lasted, or 0. Reported on the status page so a
    // hold in progress is visible rather than a mystery.
    uint32_t held_ms() const { return held_ms_; }
    bool fired() const { return fired_; }

  private:
    bool level_ = false;        // last debounced level
    bool raw_ = false;          // last raw level
    bool stable_ = false;       // has the raw level held for the debounce?
    uint32_t raw_since_ms_ = 0;
    uint32_t press_start_ms_ = 0;
    uint32_t held_ms_ = 0;
    bool armed_ = false;
    bool warned_ = false;
    bool fired_ = false;
    bool stuck_ = false;
};

// --- scan results ----------------------------------------------------------
// Our own authentication enum rather than wifi_auth_mode_t, so that this file stays
// free of ESP-IDF. board_wifi.cpp does the one-switch translation; the values here
// are ours and are ordered weakest to strongest, which is what lets the page warn
// about an open network.
enum class WifiAuth : uint8_t {
    Open = 0,
    Wep = 1,
    WpaPsk = 2,
    Wpa2Psk = 3,
    WpaWpa2Psk = 4,
    Wpa3Psk = 5,
    Wpa2Wpa3Psk = 6,
    Enterprise = 7,
    Unknown = 8,
};

const char *wifi_auth_name(WifiAuth a);
// True when joining needs no passphrase. The page uses it to stop asking for one,
// and the validator uses it to stop requiring one.
bool wifi_auth_is_open(WifiAuth a);

#define WIFI_SCAN_MAX 24
#define WIFI_SSID_LEN 33

struct WifiScanEntry {
    char ssid[WIFI_SSID_LEN] = {0};
    int8_t rssi = 0;
    uint8_t channel = 0;
    WifiAuth auth = WifiAuth::Unknown;
};

// Sorts strongest first, drops hidden networks, and collapses duplicates - a mesh or
// a dual-band router answers on several channels and would otherwise fill the list
// with one name. Returns the number of entries left, in place.
//
// Strongest-wins on the duplicate, not first-wins: the entry that survives is the one
// the radio will actually associate with.
int wifi_scan_prepare(WifiScanEntry *entries, int n);

// 0-4, for a signal-strength glyph. The thresholds are the usual ones: -55 excellent,
// -67 good enough for video, -75 workable, -85 marginal. Below that a join usually
// succeeds and then drops, which is worse than failing outright, so it reads 0.
int wifi_rssi_bars(int8_t rssi);

// The scan list as JSON. SSIDs are escaped, and that is a security control rather
// than tidiness: an SSID is 32 bytes chosen by whoever owns the access point, it can
// contain quotes, backslashes and control characters, and anybody within radio range
// of the device can broadcast one. It reaches this device's own configuration page.
//
// Returns the length written, or 0 if it did not fit - in which case `out` is left
// empty rather than holding a truncated document.
size_t wifi_scan_json(const WifiScanEntry *entries, int n, bool scanning,
                      uint32_t age_ms, char *out, size_t out_cap);

// --- what went wrong -------------------------------------------------------
// The station state, as the page shows it.
enum class WifiStaState : uint8_t {
    Disabled = 0,     // no credentials stored; the device is in provisioning mode
    Idle = 1,         // credentials stored, not yet attempted
    Connecting = 2,
    Connected = 3,
    Failed = 4,       // an attempt failed; `reason` says how
};

const char *wifi_sta_state_name(WifiStaState s);

// A sentence for an 802.11 reason code. This is the difference between "it did not
// work" and a fix: a wrong passphrase, a network out of range and a network that
// refused the association look identical from the outside, and the reason code is
// the only thing that separates them.
//
// The codes are 802.11 / ESP-IDF `wifi_err_reason_t` values; only the ones an
// operator can act on are named, and everything else falls through to a form that at
// least carries the number - which is built into `buf`, so it must be at least
// WIFI_REASON_TEXT_MAX bytes.
//
// The buffer belongs to the caller rather than to this function on purpose. Two tasks
// ask this: the Wi-Fi event task, logging a disconnection, and the HTTP task, building
// the status document for a page that polls while the radio is doing exactly that. A
// static buffer here would be a data race between them for a code neither of them
// named - rare, harmless-looking, and the kind of thing that is never found again.
#define WIFI_REASON_TEXT_MAX 64
const char *wifi_disconnect_reason_text(uint8_t reason, char *buf, size_t cap);

// True for the reason codes that mean "the passphrase is wrong", which is the one
// failure worth calling out by itself: it is the most common, and the page can then
// put the message next to the password box rather than in a status line.
bool wifi_reason_is_auth_failure(uint8_t reason);
