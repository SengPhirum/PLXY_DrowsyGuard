"""Wi-Fi provisioning: the button, the scan list, and the words for a failed join.

`firmware/esp32s3/main/wifi_provision.cpp` holds the parts of provisioning that can
be wrong without a radio in the room, and the button is the reason this file is long.
A physical reset that fires when it should not costs somebody their Wi-Fi
configuration and their afternoon; one that never fires leaves a device with wrong
credentials unrecoverable, because the network it cannot join is the one you would
fix it over. Both failures are silent, and neither shows up in a build.

The hazard driving most of the button cases is specific to this board and is
documented in `firmware/esp32s3/README.md`: its auto-reset lines are **inverted**, so
pyserial de-asserting DTR when it opens a port pulls GPIO0 low - electrically
identical to somebody holding BOOT down. A naive long-press watcher would erase the
Wi-Fi credentials of every board anyone attached a terminal to. `test_a_pin_held_low_
from_boot_never_fires` is that scenario.

The scan half is smaller but has its own edge: an SSID is 32 bytes chosen by whoever
owns the access point, anybody within radio range can broadcast one, and it lands in
this device's own configuration page. So it is escaped, and the escaping is checked
against the strings somebody would choose on purpose.

Skipped when there is no host compiler; a correctness gate that cannot run is not a
reason to fail an unrelated checkout.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'firmware/esp32s3/main'

# Mirrors of the constants in wifi_provision.h. Duplicated rather than parsed on
# purpose: if one of these moves, a human should re-read the expectations below rather
# than have them silently re-derive themselves around the change.
DEBOUNCE_MS = 50
ARM_AFTER_MS = 3000
WARN_MS = 2000
HOLD_MS = 5000
STUCK_MS = 30000
POLL_MS = 50
SCAN_MAX = 24
# WIFI_JSON_MAX in web_server.cpp. Mirrored rather than guessed, and checked below,
# so a buffer that shrinks fails here rather than on somebody's bench.
WIFI_JSON_MAX = 8192

HARNESS = r'''
// Compiled by tests/test_wifi_provision.py against the real firmware sources.
//
// One line in, one line out. Arguments are form-encoded and decoded with the
// firmware's own settings_form_field(), same as the MQTT harness.
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "device_config.h"
#include "wifi_provision.h"

static char g_line[8192];
static char g_val[4096];

static bool field(const char *q, const char *key) {
    return settings_form_field(q, key, g_val, sizeof(g_val));
}

static long num(const char *q, const char *key, long dflt) {
    if (!field(q, key)) return dflt;
    return strtol(g_val, nullptr, 10);
}

static ButtonWatch g_btn;
static WifiScanEntry g_scan[WIFI_SCAN_MAX];
static int g_scan_n = 0;

// Runs the watcher over a span of time at the real poll interval, so the tests
// exercise the same call pattern board_button.cpp uses rather than a convenient one.
// Returns the events seen, in order, as a space-separated list.
static void run_span(uint32_t from_ms, uint32_t to_ms, bool pressed, char *out,
                     size_t cap, uint32_t step) {
    size_t at = strlen(out);
    for (uint32_t t = from_ms; t <= to_ms; t += step) {
        const WifiButtonEvent e = g_btn.update(pressed, t);
        if (e == WifiButtonEvent::None) continue;
        const int w = snprintf(out + at, cap - at, "%s%s@%lu",
                               at ? " " : "", wifi_button_event_name(e),
                               static_cast<unsigned long>(t));
        if (w <= 0 || at + w >= cap) return;
        at += w;
    }
}

int main() {
    while (fgets(g_line, sizeof(g_line), stdin) != nullptr) {
        char *nl = strpbrk(g_line, "\r\n");
        if (nl != nullptr) *nl = '\0';
        char *tab = strchr(g_line, '\t');
        const char *cmd = g_line;
        const char *q = "";
        if (tab != nullptr) { *tab = '\0'; q = tab + 1; }

        if (strcmp(cmd, "quit") == 0) break;

        if (strcmp(cmd, "btn_reset") == 0) {
            g_btn.reset();
            printf("ok\n");
        } else if (strcmp(cmd, "btn_span") == 0) {
            // from, to, pressed, step
            const uint32_t from = static_cast<uint32_t>(num(q, "from", 0));
            const uint32_t to = static_cast<uint32_t>(num(q, "to", 0));
            const bool pressed = num(q, "pressed", 0) != 0;
            const uint32_t step = static_cast<uint32_t>(num(q, "step", WIFI_BUTTON_POLL_MS));
            static char events[1024];
            events[0] = '\0';
            run_span(from, to, pressed, events, sizeof(events), step);
            printf("[%s]\n", events);
        } else if (strcmp(cmd, "btn_state") == 0) {
            printf("armed=%d pressed=%d held=%lu fired=%d\n", g_btn.armed() ? 1 : 0,
                   g_btn.pressed() ? 1 : 0,
                   static_cast<unsigned long>(g_btn.held_ms()),
                   g_btn.fired() ? 1 : 0);
        } else if (strcmp(cmd, "btn_step") == 0) {
            // One call, for the cases where the exact instant matters.
            const bool pressed = num(q, "pressed", 0) != 0;
            const uint32_t at = static_cast<uint32_t>(num(q, "at", 0));
            printf("%s\n", wifi_button_event_name(g_btn.update(pressed, at)));
        } else if (strcmp(cmd, "scan_clear") == 0) {
            g_scan_n = 0;
            printf("ok\n");
        } else if (strcmp(cmd, "scan_add") == 0) {
            if (g_scan_n >= WIFI_SCAN_MAX) { printf("full\n"); goto flushed; }
            {
                WifiScanEntry e{};
                const long rssi = num(q, "rssi", -60);
                const long ch = num(q, "channel", 6);
                const long auth = num(q, "auth", 3);
                if (field(q, "ssid")) settings_copy(e.ssid, sizeof(e.ssid), g_val);
                e.rssi = static_cast<int8_t>(rssi);
                e.channel = static_cast<uint8_t>(ch);
                e.auth = static_cast<WifiAuth>(auth);
                g_scan[g_scan_n++] = e;
                printf("%d\n", g_scan_n);
            }
        } else if (strcmp(cmd, "scan_prepare") == 0) {
            g_scan_n = wifi_scan_prepare(g_scan, g_scan_n);
            printf("%d\n", g_scan_n);
        } else if (strcmp(cmd, "scan_list") == 0) {
            // ssid|rssi per surviving entry, so order and de-duplication are visible
            // without going through JSON.
            for (int i = 0; i < g_scan_n; ++i) {
                printf("%s%s|%d", i ? " " : "", g_scan[i].ssid, g_scan[i].rssi);
            }
            printf("\n");
        } else if (strcmp(cmd, "scan_json") == 0) {
            const bool scanning = num(q, "scanning", 0) != 0;
            const uint32_t age = static_cast<uint32_t>(num(q, "age_ms", 0));
            const long cap = num(q, "cap", 4096);
            static char out[8192];
            memset(out, 'X', sizeof(out));
            const size_t n = wifi_scan_json(g_scan, g_scan_n, scanning, age, out,
                                            static_cast<size_t>(cap));
            if (n == 0) printf("err %zu\n", strlen(out));
            else printf("ok %s\n", out);
        } else if (strcmp(cmd, "bars") == 0) {
            printf("%d\n", wifi_rssi_bars(static_cast<int8_t>(num(q, "rssi", 0))));
        } else if (strcmp(cmd, "auth") == 0) {
            const WifiAuth a = static_cast<WifiAuth>(num(q, "auth", 0));
            printf("%s %d\n", wifi_auth_name(a), wifi_auth_is_open(a) ? 1 : 0);
        } else if (strcmp(cmd, "state") == 0) {
            printf("%s\n", wifi_sta_state_name(
                static_cast<WifiStaState>(num(q, "state", 0))));
        } else if (strcmp(cmd, "reason") == 0) {
            const uint8_t r = static_cast<uint8_t>(num(q, "reason", 0));
            char rbuf[WIFI_REASON_TEXT_MAX];
            printf("%d|%s\n", wifi_reason_is_auth_failure(r) ? 1 : 0,
                   wifi_disconnect_reason_text(r, rbuf, sizeof(rbuf)));
        } else {
            printf("unknown\n");
        }
    flushed:
        fflush(stdout);
    }
    return 0;
}
'''


def _compiler():
    for cc in ('g++', 'c++', 'clang++'):
        if shutil.which(cc):
            return [cc]
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, '-m', 'ziglang', 'c++']


@pytest.fixture(scope='module')
def wp(tmp_path_factory):
    cc = _compiler()
    if cc is None:
        pytest.skip('no host C++ compiler')
    d = tmp_path_factory.mktemp('wifi')
    src = d / 'harness.cpp'
    src.write_text(HARNESS, encoding='utf-8')
    exe = d / ('harness.exe' if sys.platform == 'win32' else 'harness')
    proc = subprocess.run(
        cc + ['-O2', '-std=c++17', '-Wall', '-Wextra', '-Werror',
              '-Wno-unused-parameter', f'-I{FIRMWARE}', str(src),
              str(FIRMWARE / 'wifi_provision.cpp'), str(FIRMWARE / 'device_config.cpp'),
              '-o', str(exe)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail('wifi_provision.cpp does not compile cleanly on the host:\n'
                    + proc.stderr[-6000:])

    def call(script):
        out = subprocess.run([str(exe)], input=script, capture_output=True, text=True,
                             timeout=120)
        assert out.returncode == 0, out.stderr
        return out.stdout.splitlines()

    return call


def _cmd(name, **kwargs):
    if not kwargs:
        return name
    return f'{name}\t' + urlencode({k: v for k, v in kwargs.items() if v is not None})


def one(wp, name, **kwargs):
    lines = wp(_cmd(name, **kwargs) + '\nquit\n')
    assert lines, f'{name} produced no output'
    return lines[0]


def press(wp, script):
    """Run a button script and return the list of `event@ms` strings it produced."""
    lines = wp(script + 'quit\n')
    out = []
    for line in lines:
        if line.startswith('[') and line.endswith(']'):
            body = line[1:-1]
            out.extend(body.split(' ') if body else [])
    return out


def held(events):
    return [e.split('@')[0] for e in events]


def at(events, name):
    """The millisecond at which `name` fired, or None."""
    for e in events:
        if e.startswith(name + '@'):
            return int(e.split('@')[1])
    return None


# =========================================================================== #
# the button
# =========================================================================== #
def test_a_deliberate_five_second_hold_fires_once(wp):
    """The happy path, at the real poll interval.

    Warning first, then Fired, and each exactly once - a watcher that re-fired every
    poll after the threshold would erase the credentials, then erase them again, and
    log a screen of it.
    """
    ev = press(wp, 'btn_reset\n'
                   # released through the boot window, so the watcher arms
                   + _cmd('btn_span', **{'from': 0, 'to': 3500, 'pressed': 0}) + '\n'
                   # then held for eight seconds
                   + _cmd('btn_span', **{'from': 3550, 'to': 11500, 'pressed': 1}) + '\n')
    assert held(ev) == ['warning', 'fired'], ev
    # Two seconds and five seconds after the press began, within one poll interval.
    assert WARN_MS <= at(ev, 'warning') - 3550 < WARN_MS + POLL_MS + DEBOUNCE_MS
    assert HOLD_MS <= at(ev, 'fired') - 3550 < HOLD_MS + POLL_MS + DEBOUNCE_MS


def test_a_pin_held_low_from_boot_never_fires(wp):
    """The hazard this whole design is shaped around.

    This board's auto-reset lines are inverted, so pyserial de-asserting DTR when it
    opens a port pulls GPIO0 low - indistinguishable, electrically, from somebody
    holding BOOT. Without the "must be seen released first" rule, opening a serial
    monitor would erase the Wi-Fi credentials of every board it was pointed at, five
    seconds later, silently.
    """
    ev = press(wp, 'btn_reset\n'
                   + _cmd('btn_span', **{'from': 0, 'to': 60000, 'pressed': 1}) + '\n')
    assert 'fired' not in held(ev), ev
    assert 'warning' not in held(ev), ev
    # It says so once, rather than going quiet: a feature that appears not to work is
    # worse than one that explains itself.
    assert held(ev) == ['stuck'], ev
    assert at(ev, 'stuck') == STUCK_MS

    state = dict(kv.split('=') for kv in one(wp, 'btn_state').split(' '))
    assert state['armed'] == '0', 'a pin low since boot must never arm'
    assert state['fired'] == '0'


def test_releasing_a_stuck_pin_arms_the_watcher_normally(wp):
    """Recovery from the case above: unplug the serial adapter and the button works.
    A latch that stayed stuck forever would need a power cycle to undo, which on a
    device you are trying to recover is the wrong direction."""
    ev = press(wp, 'btn_reset\n'
                   + _cmd('btn_span', **{'from': 0, 'to': 40000, 'pressed': 1}) + '\n'
                   + _cmd('btn_span', **{'from': 40050, 'to': 41000, 'pressed': 0}) + '\n'
                   + _cmd('btn_span', **{'from': 41050, 'to': 48000, 'pressed': 1}) + '\n')
    assert held(ev) == ['stuck', 'warning', 'fired'], ev


def test_a_brush_against_the_button_is_not_an_event(wp):
    """Nothing is logged and nothing happens below two seconds. A device in a vehicle
    gets knocked; a log line for every knock trains people to ignore the log."""
    for duration in (0, 50, 100, 500, 1000, 1900):
        ev = press(wp, 'btn_reset\n'
                       + _cmd('btn_span', **{'from': 0, 'to': 3500, 'pressed': 0}) + '\n'
                       + _cmd('btn_span', **{'from': 3550, 'to': 3550 + duration,
                                             'pressed': 1}) + '\n'
                       + _cmd('btn_span', **{'from': 3600 + duration,
                                             'to': 5000 + duration, 'pressed': 0}) + '\n')
        assert ev == [], f'a {duration} ms press produced {ev}'


def test_letting_go_after_the_warning_cancels_and_says_so(wp):
    """The warning exists so that a press which was not meant can be undone. If
    releasing were silent, the operator would not know whether they got out in time."""
    ev = press(wp, 'btn_reset\n'
                   + _cmd('btn_span', **{'from': 0, 'to': 3500, 'pressed': 0}) + '\n'
                   + _cmd('btn_span', **{'from': 3550, 'to': 7000, 'pressed': 1}) + '\n'
                   + _cmd('btn_span', **{'from': 7050, 'to': 8000, 'pressed': 0}) + '\n')
    assert held(ev) == ['warning', 'cancelled'], ev
    assert at(ev, 'cancelled') >= 7050


def test_nothing_happens_during_the_boot_window(wp):
    """GPIO0 is a strapping pin through reset and the auto-reset lines can still be
    settling. A press that begins before the device is up is not a press."""
    ev = press(wp, 'btn_reset\n'
                   + _cmd('btn_span', **{'from': 0, 'to': 2900, 'pressed': 0}) + '\n'
                   + _cmd('btn_span', **{'from': 2950, 'to': 2999, 'pressed': 1}) + '\n')
    assert ev == [], ev
    assert dict(kv.split('=') for kv in one(wp, 'btn_state').split(' '))['armed'] == '0'


def test_bounce_on_the_contact_does_not_restart_the_timer(wp):
    """A mechanical switch bounces for a few milliseconds. Counting edges is how one
    press becomes three; the debounce is on the level, not on transitions."""
    script = 'btn_reset\n' + _cmd('btn_span', **{'from': 0, 'to': 3500,
                                                 'pressed': 0}) + '\n'
    # 30 ms of alternating levels at 10 ms - all below the 50 ms debounce - then a
    # clean hold. The press must be timed from the end of the bounce, not restarted
    # by it, so the fire still lands ~5 s later rather than never.
    t = 3550
    for i in range(3):
        script += _cmd('btn_step', pressed=1, at=t) + '\n'
        script += _cmd('btn_step', pressed=0, at=t + 10) + '\n'
        t += 20
    script += _cmd('btn_span', **{'from': t, 'to': t + 7000, 'pressed': 1}) + '\n'
    ev = press(wp, script)
    assert held(ev) == ['warning', 'fired'], ev


def test_a_press_that_is_never_released_stops_counting(wp):
    """It already fired. Continuing to count would show a number climbing forever on
    the status page, which reads as a device stuck in a loop."""
    ev = press(wp, 'btn_reset\n'
                   + _cmd('btn_span', **{'from': 0, 'to': 3500, 'pressed': 0}) + '\n'
                   + _cmd('btn_span', **{'from': 3550, 'to': 60000, 'pressed': 1}) + '\n')
    assert held(ev) == ['warning', 'fired'], ev
    state = dict(kv.split('=') for kv in one(wp, 'btn_state').split(' '))
    assert int(state['held']) <= STUCK_MS + POLL_MS, state


def test_two_presses_in_a_row_both_fire(wp):
    """The watcher is not one-shot. Somebody who resets the Wi-Fi, gets it wrong and
    resets it again should not need a power cycle in between."""
    ev = press(wp, 'btn_reset\n'
                   + _cmd('btn_span', **{'from': 0, 'to': 3500, 'pressed': 0}) + '\n'
                   + _cmd('btn_span', **{'from': 3550, 'to': 9000, 'pressed': 1}) + '\n'
                   + _cmd('btn_span', **{'from': 9050, 'to': 10000, 'pressed': 0}) + '\n'
                   + _cmd('btn_span', **{'from': 10050, 'to': 16000, 'pressed': 1}) + '\n')
    assert held(ev) == ['warning', 'fired', 'warning', 'fired'], ev


def test_a_slow_poll_still_fires(wp):
    """The maths is on timestamps, not on call counts, so a task that was starved for
    a second does not miss the threshold - it just notices late."""
    ev = press(wp, 'btn_reset\n'
                   + _cmd('btn_span', **{'from': 0, 'to': 3500, 'pressed': 0,
                                         'step': 500}) + '\n'
                   + _cmd('btn_span', **{'from': 4000, 'to': 11000, 'pressed': 1,
                                         'step': 900}) + '\n')
    assert held(ev) == ['warning', 'fired'], ev


# =========================================================================== #
# the scan list
# =========================================================================== #
def _scan(wp, entries, **json_kwargs):
    script = 'scan_clear\n'
    for e in entries:
        script += _cmd('scan_add', **e) + '\n'
    script += 'scan_prepare\nscan_list\n'
    script += _cmd('scan_json', **json_kwargs) + '\n'
    lines = wp(script + 'quit\n')
    # scan_clear, one per add, prepare, list, json
    listing = lines[len(entries) + 2]
    js = lines[len(entries) + 3]
    return listing.split(' ') if listing else [], js


def test_the_strongest_network_comes_first(wp):
    """A list ordered by whatever the radio happened to report is a list where the
    network you want is in an unpredictable place."""
    listing, _ = _scan(wp, [
        {'ssid': 'weak', 'rssi': -80},
        {'ssid': 'strong', 'rssi': -40},
        {'ssid': 'middling', 'rssi': -60},
    ])
    assert listing == ['strong|-40', 'middling|-60', 'weak|-80']


def test_one_network_on_several_channels_appears_once(wp):
    """A mesh or a dual-band router answers on several channels. Three rows saying
    "Home" is not a choice, it is a puzzle - and the survivor has to be the strongest,
    because that is the one the radio will actually associate with."""
    listing, _ = _scan(wp, [
        {'ssid': 'Home', 'rssi': -70, 'channel': 1},
        {'ssid': 'Guest', 'rssi': -75, 'channel': 6},
        {'ssid': 'Home', 'rssi': -45, 'channel': 36},
        {'ssid': 'Home', 'rssi': -82, 'channel': 11},
    ])
    assert listing == ['Home|-45', 'Guest|-75']


def test_hidden_networks_are_dropped(wp):
    """An access point with no SSID in its beacon cannot be joined from a list -
    there is nothing to show and nothing to click."""
    listing, _ = _scan(wp, [
        {'ssid': '', 'rssi': -40},
        {'ssid': 'Visible', 'rssi': -60},
        {'ssid': '', 'rssi': -50},
    ])
    assert listing == ['Visible|-60']


def test_an_empty_scan_is_valid_json_rather_than_an_error(wp):
    """"No networks in range" is an answer. A page that got an error instead would
    show a failure where the truth is an empty room."""
    _, js = _scan(wp, [])
    assert js.startswith('ok '), js
    doc = json.loads(js[3:])
    assert doc['networks'] == []
    assert doc['scanning'] is False


@pytest.mark.parametrize('ssid', [
    'Bob"s Wi-Fi',
    'back\\slash',
    'quote:" backslash:\\',
    '</script><img src=x>',
    '{"injected":true}',
    'tab\there',
    'Café Wi-Fi'.encode('ascii', 'replace').decode(),
])
def test_an_ssid_chosen_by_a_stranger_cannot_break_the_document(wp, ssid):
    """An SSID is 32 bytes chosen by whoever owns the access point, anybody within
    radio range of the device can broadcast one, and it lands in the device's own
    configuration page. Escaped rather than trusted - and the page renders it with
    textContent, so the two defences are independent."""
    _, js = _scan(wp, [{'ssid': ssid, 'rssi': -50}])
    assert js.startswith('ok '), js
    doc = json.loads(js[3:])
    assert len(doc['networks']) == 1
    # Round-trips exactly: escaping, not stripping. An operator has to be able to
    # recognise their own network by name.
    assert doc['networks'][0]['ssid'] == ssid


def test_a_scan_document_that_will_not_fit_produces_nothing(wp):
    """Half a list is worse than none: the page renders it as the whole list, and the
    network somebody was looking for is simply absent with no indication why."""
    entries = [{'ssid': f'net-{i:02d}', 'rssi': -40 - i} for i in range(12)]
    _, js = _scan(wp, entries, cap=200)
    assert js.startswith('err '), js
    assert js.split(' ')[1] == '0', 'a truncated document was left in the buffer'


def test_the_json_carries_what_the_page_needs(wp):
    _, js = _scan(wp, [{'ssid': 'Home', 'rssi': -45, 'channel': 6, 'auth': 3}],
                  scanning=1, age_ms=1500)
    doc = json.loads(js[3:])
    assert doc['scanning'] is True
    assert doc['age_ms'] == 1500
    net = doc['networks'][0]
    assert net == {'ssid': 'Home', 'rssi': -45, 'bars': 4, 'channel': 6,
                   'auth': 'wpa2', 'open': False}


def test_a_full_scan_fits_the_buffer_the_firmware_gives_it(wp):
    """24 networks with the longest SSID each is the worst case a busy office
    produces, and it has to fit in one response rather than being cut short."""
    entries = [{'ssid': 'x' * 32, 'rssi': -40 - i, 'channel': i % 13 + 1}
               for i in range(SCAN_MAX)]
    # Distinct names, or de-duplication would collapse them.
    for i, e in enumerate(entries):
        e['ssid'] = f'{i:02d}' + 'x' * 30
    _, js = _scan(wp, entries, cap=4096)
    assert js.startswith('ok '), js
    assert len(json.loads(js[3:])['networks']) == SCAN_MAX


# --------------------------------------------------------------------------- #
# SSIDs in alphabets that are not ASCII, and bytes that are not any alphabet
# --------------------------------------------------------------------------- #
def _scan_raw(wp, raw_ssids, cap=WIFI_JSON_MAX):
    """Like _scan, but each SSID is a percent-encoded byte string rather than text.

    The point is to put bytes on the wire that Python could not hold as a str: an
    access point broadcasts 32 octets, not 32 characters, and `urlencode` would have
    encoded them as UTF-8 and hidden the case under test.
    """
    script = 'scan_clear\n'
    for i, pct in enumerate(raw_ssids):
        script += f'scan_add\tssid={pct}&rssi={-40 - i}\n'
    # No scan_list here, unlike _scan: it prints the SSIDs raw, and a pipe being read
    # as text cannot carry bytes that are not valid UTF-8 - which is the whole point
    # of these cases. The JSON document is pure ASCII by construction, so it comes
    # back through the same pipe intact, and that is itself part of what is checked.
    script += 'scan_prepare\n'
    script += f'scan_json\tcap={cap}\n'
    lines = wp(script + 'quit\n')
    return lines[len(raw_ssids) + 2]


@pytest.mark.parametrize('ssid', [
    'Café Wi-Fi',                       # two bytes
    'ភ្នំពេញ',                              # Khmer, three bytes each
    'Wi-Fi 🛻 Van 3',                   # four bytes
    'Ωμέγα',
])
def test_a_network_named_in_someone_elses_alphabet_reads_as_itself(wp, ssid):
    """802.11 says an SSID is 32 arbitrary octets, and the routers in the building
    this is being written in are not all named in ASCII. A scan list that mangled
    them would be a list an operator cannot pick their own network out of."""
    _, js = _scan(wp, [{'ssid': ssid, 'rssi': -50}])
    assert js.startswith('ok '), js
    assert json.loads(js[3:])['networks'][0]['ssid'] == ssid


@pytest.mark.parametrize('label,pct', [
    ('a lone continuation byte', '%80'),
    ('a truncated two-byte sequence', 'caf%c3'),
    ('a truncated three-byte sequence', '%e1%88'),
    ('an overlong encoding of "/"', '%c0%af'),
    ('a surrogate half', '%ed%a0%80'),
    ('beyond U+10FFFF', '%f5%80%80%80'),
    ('every high byte there is', '%80%81%fe%ff'),
    ('latin-1 text from a router that never heard of unicode', 'Caf%e9'),
])
def test_an_ssid_that_is_not_utf8_still_parses(wp, label, pct):
    r"""The cheapest denial of service anybody in radio range can mount.

    One access point broadcasting 32 bytes that are not valid UTF-8 used to make the
    whole scan document undecodable - JSON.parse throws, the page shows no networks
    at all, and the operator is looking at an empty list on the one page they are
    using to recover the device. The bytes are escaped as \u00XX instead: the wrong
    text, but a document that parses and a row that can still be picked.
    """
    js = _scan_raw(wp, [pct])
    assert js.startswith('ok '), f'{label}: {js}'
    # The document itself carries no byte a parser could choke on: the escaping is
    # what makes it pure ASCII, and the parse below is what proves it was enough.
    assert all(ord(c) < 0x80 for c in js), f'{label}: a raw byte reached the document'
    doc = json.loads(js[3:])                     # the assertion that matters
    assert len(doc['networks']) == 1
    # The row is still there and still has a name, so it can still be picked. What
    # that name reads as is not the point - these bytes are not text in any encoding
    # the device was told about.
    assert doc['networks'][0]['ssid'], f'{label}: the row lost its name entirely'


def test_the_worst_case_scan_still_fits_the_buffer(wp):
    """24 networks, each 32 bytes of the most expensive thing to escape. That is the
    document a hostile neighbour produces on purpose, and it has to fit - because a
    document that does not fit is not a truncated list, it is no list at all."""
    # A distinct first byte each, or de-duplication collapses them and the test
    # measures one network instead of twenty-four.
    raw = ['%%%02x' % (0x80 + i) + '%ff' * 31 for i in range(SCAN_MAX)]
    js = _scan_raw(wp, raw)
    assert js.startswith('ok '), js
    doc = json.loads(js[3:])
    assert len(doc['networks']) == SCAN_MAX
    assert len(js) - 3 <= WIFI_JSON_MAX, 'the document overran the buffer it claims'


@pytest.mark.parametrize('rssi,bars', [
    (-30, 4), (-55, 4), (-56, 3), (-67, 3), (-68, 2), (-75, 2),
    (-76, 1), (-85, 1), (-86, 0), (-100, 0),
])
def test_signal_bars(wp, rssi, bars):
    """Below -85 an association usually succeeds and then drops, which is worse than
    failing outright - so it reads as no signal rather than as one bar."""
    assert one(wp, 'bars', rssi=rssi) == str(bars)


@pytest.mark.parametrize('auth,name,is_open', [
    (0, 'open', '1'), (1, 'wep', '0'), (2, 'wpa', '0'), (3, 'wpa2', '0'),
    (4, 'wpa/wpa2', '0'), (5, 'wpa3', '0'), (6, 'wpa2/wpa3', '0'),
    (7, 'enterprise', '0'), (8, 'unknown', '0'),
])
def test_authentication_modes(wp, auth, name, is_open):
    assert one(wp, 'auth', auth=auth) == f'{name} {is_open}'


# =========================================================================== #
# why a join failed
# =========================================================================== #
@pytest.mark.parametrize('reason', [2, 15, 204, 205])
def test_the_wrong_password_is_named_as_the_wrong_password(wp, reason):
    """The most common failure, and the one worth calling out by itself: an access
    point that rejects a passphrase, one that is out of range and one that refuses
    the association look identical from outside, and the reason code is the only
    thing that separates them."""
    flag, text = one(wp, 'reason', reason=reason).split('|', 1)
    assert flag == '1', f'reason {reason} is not reported as an auth failure'
    assert 'password' in text.lower(), text


def test_a_network_out_of_range_is_not_reported_as_a_bad_password(wp):
    """Sending somebody to re-type a correct passphrase because their hotspot is off
    is the specific failure this mapping exists to prevent."""
    flag, text = one(wp, 'reason', reason=201).split('|', 1)
    assert flag == '0'
    assert 'in range' in text


@pytest.mark.parametrize('reason', [1, 4, 8, 39, 200, 202, 203])
def test_the_other_named_reasons_say_something_actionable(wp, reason):
    flag, text = one(wp, 'reason', reason=reason).split('|', 1)
    assert len(text) > 20, text
    assert not text.startswith('the connection failed (802.11'), (
        f'reason {reason} fell through to the generic form')


def test_an_unnamed_reason_still_carries_its_number(wp):
    """A code is something to look up. "Unknown error" is not."""
    flag, text = one(wp, 'reason', reason=77).split('|', 1)
    assert flag == '0'
    assert '77' in text


@pytest.mark.parametrize('state,name', [
    (0, 'disabled'), (1, 'idle'), (2, 'connecting'), (3, 'connected'), (4, 'failed'),
])
def test_station_state_names(wp, state, name):
    assert one(wp, 'state', state=state) == name
