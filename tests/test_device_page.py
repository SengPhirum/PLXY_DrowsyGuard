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
    assert out.count('  ok') >= 20, f'only {out.count("  ok")} checks ran:\n{out}'


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
    assert size < 96 * 1024, f'the device page is {size / 1024:.0f} kB'
