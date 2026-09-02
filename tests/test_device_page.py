"""Run the device page against realistic API payloads.

`firmware/esp32s3/main/web/index.html` is the only user interface this build has -
there is no panel any more - and it is one file of hand-written JavaScript with no
build step and no framework. Nothing else in this repository would notice if a
rename broke it, and the failure mode is not subtle: an exception thrown in
`render()` stops every line after it, so one missing key blanks the whole dashboard
rather than one field.

The heavy lifting is in `device_page_harness.mjs`, which stubs a DOM, evaluates the
page's own `<script>` unmodified, and drives it with payloads shaped exactly like
`web_server.cpp` emits - including the degraded ones: no camera, no SD card, an
older firmware that does not send a field at all, a device that stops answering.

Skipped without Node. That is a real gap on a machine without it, but a device page
that cannot be exercised is still better than one that is never exercised, and the
firmware build does not depend on this.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name('device_page_harness.mjs')
PAGE = ROOT / 'firmware/esp32s3/main/web/index.html'
FAVICON = ROOT / 'firmware/esp32s3/main/web/favicon.ico'
CMAKE = ROOT / 'firmware/esp32s3/main/CMakeLists.txt'
WEB_SERVER = ROOT / 'firmware/esp32s3/main/web_server.cpp'


@pytest.fixture(scope='module')
def harness_output():
    if shutil.which('node') is None:
        pytest.skip('node is not installed')
    proc = subprocess.run(['node', str(HARNESS)], capture_output=True, text=True,
                          cwd=str(ROOT), timeout=120)
    return proc


def test_every_payload_renders(harness_output):
    """No payload the device can produce may throw."""
    assert harness_output.returncode == 0, (
        'the device page threw on at least one payload:\n'
        + harness_output.stdout[-4000:] + harness_output.stderr[-2000:])
    assert 'FAIL' not in harness_output.stdout, harness_output.stdout[-4000:]


def test_the_harness_actually_exercised_something(harness_output):
    """A harness that silently stops testing is worse than no harness: it reports
    success. Check it got far enough to reach the last section."""
    out = harness_output.stdout
    assert 'render() against the firmware payload:' in out
    assert 'loadHistory() against /api/events:' in out
    assert 'video path:' in out
    assert 'readout damping:' in out
    # The MQTT sections are named individually rather than folded into the count
    # below, because the count would still pass with the whole modal untested - and
    # the modal is the one part of this page where a silent failure means a
    # credential form that does not behave the way its labels claim.
    assert 'MQTT status pill:' in out
    assert 'MQTT modal against /api/mqtt:' in out
    assert 'topic preview:' in out
    assert 'submitted body:' in out
    assert 'copy to clipboard:' in out
    # Wi-Fi provisioning is the other credential form on this page, and the one an
    # operator reaches for when the device is joined to nothing - so a silent failure
    # here is a device that cannot be recovered from the page it is serving.
    assert 'Wi-Fi modal against /api/wifi:' in out
    assert 'scan list:' in out
    assert 'Wi-Fi actions:' in out
    assert out.count('  ok') >= 100, f'only {out.count("  ok")} checks ran:\n{out}'


def test_the_mqtt_modal_is_wired_to_the_firmware_api():
    """The ids the harness drives have to be the ids the page contains.

    The stub DOM cannot catch a rename on its own: it builds an element for every id
    in the file, so `$('mqHostname')` would happily return one if the markup had been
    renamed to match. What breaks in a browser and not in the harness is a form field
    the firmware reads that the page never sends - which is a setting nobody can
    change, discovered by an operator rather than by CI.
    """
    html = PAGE.read_text(encoding='utf-8')
    server = WEB_SERVER.read_text(encoding='utf-8')

    for endpoint in ("'/api/mqtt'", "'/api/mqtt/test'"):
        assert endpoint in html, f'the page never calls {endpoint}'
    for route in ('"/api/mqtt"', '"/api/mqtt/test"'):
        assert route in server, f'the firmware does not serve {route}'

    for field in ('mqEnabled', 'mqTransport', 'mqProtocol', 'mqHost', 'mqPort',
                  'mqWsPath', 'mqClientId', 'mqUser', 'mqPass', 'mqQos',
                  'mqKeepalive', 'mqLwt', 'mqTlsInsecure', 'mqCa', 'mqDeviceId',
                  'mqFleetId', 'mqRemark', 'mqTopicMode', 'mqTopic'):
        assert f'id="{field}"' in html, f'{field} is missing from the modal'

    # And the station fields are NOT here any more. Two forms writing one NVS record
    # meant that saving the broker from a page opened before a network change put the
    # old SSID back without saying so.
    for gone in ('mqStaEnabled', 'mqStaSsid', 'mqStaPass'):
        assert f'id="{gone}"' not in html, (
            f'{gone} is back in the mqtt modal; /api/wifi owns the station record')
    for gone in ('"sta_ssid"', '"sta_password"', '"sta_enabled"'):
        assert gone not in html

    # The fleet topic's copy button specifically: that is the string somebody pastes
    # into the documentation's fleet monitor, and without it they retype sixty
    # characters of slashes by hand.
    for target in ('topicDevice', 'topicFleet', 'topicStatus'):
        assert f'data-copy="{target}"' in html, f'no copy button for {target}'

    # The public broker is preconfigured, so the page has to say what that costs.
    assert 'broker.emqx.io' in html
    assert 'demonstration and testing only' in html.lower()


def test_the_page_never_renders_a_stored_password():
    """`value` is what a form submits and what a screenshot shows.

    The device returns `password_set`, never a password, so the only place a stored
    secret may be referred to is a placeholder - and the credential inputs have to be
    explicitly blanked on every fill rather than merely left alone, because a fill
    that skipped them would carry whatever the last operator typed into the next
    person's screen.
    """
    html = PAGE.read_text(encoding='utf-8')
    script = html.split('<script>')[1]
    for blanked in ("mqSet('mqPass', '')", "mqSet('mqUser', '')",
                    "mqSet('mqCa', '')"):
        assert blanked in script, f'{blanked} is missing from mqttFill()'

    # And the firmware must not be sending one in the first place.
    cfg = (ROOT / 'firmware/esp32s3/main/mqtt_config.cpp').read_text(encoding='utf-8')
    assert '\\"password_set\\":%s' in cfg, (
        'mqtt_config_json() no longer reports password_set')
    assert '\\"password\\":\\"' not in cfg, (
        'mqtt_config_json() formats a password value')


def test_the_page_stays_self_contained():
    """It is served from flash rodata by a board with no route to the internet, so
    an external stylesheet or script would simply never load."""
    html = PAGE.read_text(encoding='utf-8')
    for bad in ('src="http', "src='http", 'href="http://fonts',
                'cdn.', 'unpkg.com', 'jsdelivr'):
        assert bad not in html, f'the page references something external: {bad}'
    # One <script> and one <style>, both inline.
    assert html.count('<script') == 1 and 'src=' not in html.split('<script')[1][:120]
    assert html.count('<link') == 0


def test_favicon_is_branded_and_shipped_with_the_device():
    """The header and browser tab use the same icon embedded in flash."""
    html = PAGE.read_text(encoding='utf-8')
    cmake = CMAKE.read_text(encoding='utf-8')
    server = WEB_SERVER.read_text(encoding='utf-8')
    assert FAVICON.is_file() and 0 < FAVICON.stat().st_size < 48 * 1024
    assert 'src="/favicon.ico"' in html
    assert 'web/favicon.ico' in cmake
    assert '_binary_favicon_ico_start' in server
    assert 'image/vnd.microsoft.icon' in server


def test_the_page_still_fits_in_flash_comfortably():
    """It is linked into the binary with EMBED_TXTFILES, so it costs app-partition
    space directly. Not a tight budget - the partition has megabytes free - but a
    page that has quietly grown to hundreds of kilobytes is a mistake, not a
    feature."""
    size = PAGE.stat().st_size
    # 128 kB against roughly 97 kB today, raised from 96 kB when the Wi-Fi card and
    # modal were added (+19 kB, after the MQTT modal's +16 kB before it). The last
    # note here said the next feature wanting a form should ask whether this is still
    # one document rather than whether it can afford the bytes, so: it is, and it has
    # to be. The device is an access point with no route to the internet, so a second
    # file is a file that may not arrive. What the size actually costs is app-partition
    # flash - 97 kB of a partition with 2.5 MB free - and one uncompressed transfer
    # over the SoftAP, which is a fraction of a second on a 2.4 GHz link and happens
    # once per visit. Neither is close to mattering; a page at 128 kB would still not
    # be, but a page that reached it without anybody deciding to would be worth
    # stopping.
    assert size < 128 * 1024, f'the device page is {size / 1024:.0f} kB'


def test_the_wifi_card_is_wired_to_the_firmware_api():
    """Same reasoning as the MQTT modal: the stub DOM invents an element for every id
    in the file, so a rename that agrees with itself passes the harness and fails on
    the device. What is checked here is that the page and the firmware name the same
    endpoints and the same form fields."""
    html = PAGE.read_text(encoding='utf-8')
    server = WEB_SERVER.read_text(encoding='utf-8')

    for endpoint in ("'/api/wifi'", "'/api/wifi/scan'"):
        assert endpoint in html, f'the page never calls {endpoint}'
    for route in ('"/api/wifi"', '"/api/wifi/scan"'):
        assert route in server, f'the firmware does not serve {route}'

    for field in ('wifiPill', 'wifiAp', 'wifiSsid', 'wifiIp', 'wifiRssi', 'wifiNote',
                  'wifiBtn', 'wifiReconnectBtn', 'wfScan', 'wfNets', 'wfSsid',
                  'wfPass', 'wfOpen', 'wfConnect', 'wfReconnect', 'wfForget',
                  'wfState', 'wfNet', 'wfIp', 'wfRssi', 'wfTries', 'wfButton'):
        assert f'id="{field}"' in html, f'{field} is missing from the page'

    # The three form fields and the two actions the firmware reads, by name.
    for field in ('"ssid"', '"password"', '"open"', '"action"'):
        assert field in server, f'the firmware never reads {field}'
    for action in ('"forget"', '"reconnect"'):
        assert action in server, f'the firmware never handles action={action}'
    for sent in ('action: \'forget\'', 'action: \'reconnect\''):
        assert sent in html, f'the page never sends {sent}'

    # The physical reset has to be described where somebody looking for it would
    # look, and its one guarantee stated: it clears the station credentials only.
    assert 'BOOT button' in html
    assert 'broker settings' in html


def test_forgetting_a_network_is_confirmed_and_scoped():
    """A button three floors from the device that silently strands it is not a
    button. And the physical reset must erase one NVS key, not the namespace."""
    html = PAGE.read_text(encoding='utf-8')
    script = html.split('<script>')[1]

    forget = script.split("$('wfForget').onclick")[1][:900]
    assert 'confirm(' in forget, 'Forget network does not confirm'
    assert 'access point' in forget, 'the confirmation does not say what survives'

    # The firmware half: one key, by name, erased from a namespace that holds four.
    nvs = (ROOT / 'firmware/esp32s3/main/settings_nvs.cpp').read_text(encoding='utf-8')
    # Bounded at the function's own closing brace, so this reads settings_clear_wifi()
    # and not whatever happens to be defined under it.
    body = nvs.split('bool settings_clear_wifi')[1]
    clear = body[:body.index('\n}\n') + 3]
    assert 'KEY_WIFI' in clear
    for other in ('KEY_MQTT', 'KEY_DEVICE', 'KEY_CA'):
        assert other not in clear, (
            f'settings_clear_wifi() touches {other}; a wi-fi reset must not factory '
            f'reset the broker or the device identity')
    assert 'nvs_erase_all' not in clear, (
        'settings_clear_wifi() erases the whole namespace')

    # And the button calls exactly that, plus the radio-side forget - nothing wider.
    main = (ROOT / 'firmware/esp32s3/main/main.cpp').read_text(encoding='utf-8')
    assert 'board_button_start' in main
    hook = main.split('board_button_start')[1][:1200]
    assert 'settings_clear_wifi' in hook and 'board_wifi_forget' in hook
    assert 'esp_restart' not in hook, (
        'the button reboots the device; the access point and the detector are '
        'supposed to keep running')


def test_a_hostile_ssid_cannot_reach_the_page_as_markup():
    """An SSID is 32 bytes chosen by whoever owns the access point, and anybody in
    radio range of this device can broadcast one. It is escaped twice, in two
    different alphabets: by the firmware for JSON, and by the page for HTML."""
    html = PAGE.read_text(encoding='utf-8')
    script = html.split('<script>')[1]

    # The page's half. The scan list is the one place it assigns markup rather than
    # text, so the SSID in it has to go through esc().
    assert 'function esc(' in script
    rows = script.split('function wifiRenderNets')[1][:2000]
    assert 'esc(n.ssid)' in rows, 'the scan list interpolates an SSID unescaped'

    # The firmware's half.
    prov = (ROOT / 'firmware/esp32s3/main/wifi_provision.cpp').read_text(
        encoding='utf-8')
    scan = prov.split('size_t wifi_scan_json')[1][:2200]
    assert 'settings_json_escape' in scan, 'wifi_scan_json() emits a raw SSID'

    server = WEB_SERVER.read_text(encoding='utf-8')
    respond = server.split('static esp_err_t wifi_respond')[1][:2600]
    assert respond.count('settings_json_escape') >= 1, (
        'wifi_respond() emits a raw SSID')
