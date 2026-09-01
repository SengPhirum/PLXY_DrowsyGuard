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
    assert out.count('  ok') >= 60, f'only {out.count("  ok")} checks ran:\n{out}'


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
                  'mqFleetId', 'mqRemark', 'mqTopicMode', 'mqTopic',
                  'mqStaEnabled', 'mqStaSsid', 'mqStaPass'):
        assert f'id="{field}"' in html, f'{field} is missing from the modal'

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
    for blanked in ("mqSet('mqPass', '')", "mqSet('mqStaPass', '')",
                    "mqSet('mqUser', '')", "mqSet('mqCa', '')"):
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
    # 96 kB against roughly 79 kB today. The MQTT modal accounts for about 16 kB of
    # that, which was most of the headroom this budget used to have - so the next
    # feature that wants a form should ask whether the page is still one document,
    # not whether it can afford another 16 kB.
    assert size < 96 * 1024, f'the device page is {size / 1024:.0f} kB'
