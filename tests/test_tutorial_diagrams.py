"""Keep the hardware tutorial, its diagrams and the firmware pin maps in agreement.

The wiring appears in three places on purpose - a written step, a wiring table and
a labelled diagram - so a beginner has three chances to catch a mistake. That
redundancy is only useful while the three agree, which is what this checks:
`scripts/generate_tutorial_diagrams.py` is the single source the images are drawn
from, and its tables must match the firmware headers that the build actually uses.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'firmware/esp32s3/main'
TUTORIAL = ROOT / 'docs/tutorials/hardware-setup'
IMAGES = TUTORIAL / 'images'


def _defines(path: Path) -> dict:
    """Collect `#define NAME value` pairs from a firmware header."""
    text = path.read_text(encoding='utf-8')
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r'^#define\s+(\w+)\s+(-?\w+)', text, re.M)}


def _diagram_module():
    pytest.importorskip('PIL', reason='Pillow is only needed to regenerate diagrams')
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        'gen_diagrams', ROOT / 'scripts/generate_tutorial_diagrams.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Pin maps: diagram tables vs firmware headers
# --------------------------------------------------------------------------- #

def test_the_panel_is_gone_from_the_firmware():
    """The SPI panel was removed when the preview moved into a browser.

    Asserted rather than assumed: a stray board_display.h would be picked up by
    pinmap.load_pins() and silently reintroduce five GPIOs to the wiring tables,
    and the tutorial would start telling readers to wire a panel the firmware no
    longer drives.
    """
    for gone in ('board_display.h', 'board_display.cpp',
                 'display_ui.h', 'display_ui.cpp'):
        assert not (FIRMWARE / gone).exists(), f'{gone} is back; the tables will drift'
    for present in ('board_wifi.h', 'board_wifi.cpp',
                    'web_server.h', 'web_server.cpp', 'web/index.html'):
        assert (FIRMWARE / present).exists(), f'{present} is missing'


def test_no_artefact_still_configures_the_removed_panel():
    """Prose explaining the removal is fine; a live pin reference is not."""
    stale = ('ST7735', 'ILI9341', 'board_display.h', 'LCD_PIN_', 'DISPLAY_WIRING')
    excuses = ('remove', 'gone', 'no longer', 'used to', 'was the', 'came back',
               'gets soldered', 'gets wired', 'gets a rail')
    targets = [ROOT / 'scripts/pinmap.py',
               ROOT / 'scripts/generate_tutorial_diagrams.py',
               ROOT / 'scripts/generate_wiring_poster.py',
               ROOT / 'scripts/generate_step_diagrams.py',
               TUTORIAL / 'README.md',
               FIRMWARE / 'CMakeLists.txt',
               FIRMWARE / 'idf_component.yml']
    for path in targets:
        for line in path.read_text(encoding='utf-8').splitlines():
            if any(e in line.lower() for e in excuses):
                continue
            for token in stale:
                assert token not in line, (
                    f'{path.name} still references {token}: {line.strip()!r}')


def test_audio_diagram_pins_match_board_audio_header():
    gen = _diagram_module()
    d = _defines(FIRMWARE / 'board_audio.h')
    drawn = {row[0]: row[2] for row in gen.AUDIO_WIRING}
    assert drawn['BCLK'] == f"GPIO{d['AUDIO_PIN_BCLK']}"
    assert drawn['LRC'] == f"GPIO{d['AUDIO_PIN_LRCLK']}"
    assert drawn['DIN'] == f"GPIO{d['AUDIO_PIN_DIN']}"
    assert drawn['VIN'] == '5V'
    assert drawn['GND'] == 'GND'


def test_camera_diagram_pins_match_board_camera_header():
    gen = _diagram_module()
    d = _defines(FIRMWARE / 'board_camera.h')
    by_name = dict(gen.CAMERA_PINS)
    assert by_name['XCLK'] == int(d['CAM_PIN_XCLK'])
    assert by_name['SIOD'] == int(d['CAM_PIN_SIOD'])
    assert by_name['SIOC'] == int(d['CAM_PIN_SIOC'])
    assert by_name['PCLK'] == int(d['CAM_PIN_PCLK'])
    assert by_name['D7/Y9'] == int(d['CAM_PIN_D7'])
    assert by_name['D0/Y2'] == int(d['CAM_PIN_D0'])


def test_no_pin_is_claimed_by_two_subsystems():
    """The whole point of the GPIO allocation figure: no double-booking."""
    gen = _diagram_module()
    used = {}
    for row in gen.AUDIO_WIRING:
        if row[2].startswith('GPIO'):
            n = int(row[2][4:])
            assert n not in used, f'GPIO {n} is used by both {used[n]} and {row[0]}'
            used[n] = row[0]
    camera = {n for _, n in gen.CAMERA_PINS}
    assert not (set(used) & camera), 'a wired pin collides with the DVP camera bus'
    # 33-37 carry the octal PSRAM; driving one hangs the board.
    assert not (set(used) & set(range(33, 38))), 'a wired pin lands on flash/PSRAM'


def test_gpio_roles_agree_with_the_wiring_tables():
    gen = _diagram_module()
    for row in gen.AUDIO_WIRING:
        if row[2].startswith('GPIO'):
            assert gen.GPIO_ROLES[int(row[2][4:])][0] == 'audio'
    # The microSD slot owns its three pins in the figure too, or the reader is
    # left wondering why the amplifier is not on them.
    for n in (38, 39, 40):
        assert gen.GPIO_ROLES[n][0] == 'sdcard', f'GPIO {n} is not drawn as the SD slot'
    # What the panel gave back and nothing has since taken.
    for n in (14, 21, 47):
        assert gen.GPIO_ROLES[n][0] == 'free', f'GPIO {n} is still claimed'
    # GPIO 3 is the ESP32-S3's JTAG-source strapping pin, not a spare. Drawing it
    # as free is how a beginner ends up reaching for it first.
    assert gen.GPIO_ROLES[3][0] == 'strap', 'GPIO 3 is drawn as free; it is strapping'


# --------------------------------------------------------------------------- #
# The tutorial itself
# --------------------------------------------------------------------------- #

def test_every_generated_image_exists_and_is_a_real_png():
    gen = _diagram_module()
    names = [fn.__name__ for fn in gen.FIGURES]
    assert len(names) == 11, 'the tutorial refers to eleven reference figures'
    # 11 reference figures plus the one-page poster. glob is non-recursive, so the
    # step-by-step sequence under images/steps/ is counted separately below.
    pngs = sorted(IMAGES.glob('*.png'))
    assert len(pngs) == 12, f'expected 11 figures + 1 poster, found {len(pngs)}'
    for p in pngs:
        assert p.stat().st_size > 20_000, f'{p.name} looks like a placeholder'
        with p.open('rb') as fh:
            assert fh.read(8) == b'\x89PNG\r\n\x1a\n', f'{p.name} is not a PNG'


def test_step_diagrams_are_real_pngs():
    steps = sorted((IMAGES / 'steps').glob('*.png'))
    assert steps, 'the step-by-step sequence is missing'
    for p in steps:
        assert p.stat().st_size > 20_000, f'{p.name} looks like a placeholder'
        with p.open('rb') as fh:
            assert fh.read(8) == b'\x89PNG\r\n\x1a\n', f'{p.name} is not a PNG'


def test_tutorial_embeds_every_image():
    readme = TUTORIAL / 'README.md'
    assert readme.exists(), 'the hardware tutorial is missing'
    text = readme.read_text(encoding='utf-8')
    for png in sorted(IMAGES.glob('*.png')):
        assert f'images/{png.name}' in text, f'{png.name} is never embedded in the tutorial'


def test_tutorial_wiring_table_matches_the_firmware():
    """Every GPIO in the tutorial's wiring table must exist in the headers."""
    readme = (TUTORIAL / 'README.md').read_text(encoding='utf-8')
    audio = _defines(FIRMWARE / 'board_audio.h')
    wired = {audio['AUDIO_PIN_BCLK'], audio['AUDIO_PIN_LRCLK'], audio['AUDIO_PIN_DIN']}
    for gpio in wired:
        assert f'GPIO {gpio}' in readme or f'GPIO{gpio}' in readme, (
            f'GPIO {gpio} is wired by the firmware but never named in the tutorial')


def test_tutorial_tells_the_reader_how_to_reach_the_preview():
    """With no panel, the tutorial is useless unless it names the SSID and the URL."""
    readme = (TUTORIAL / 'README.md').read_text(encoding='utf-8')
    wifi = (FIRMWARE / 'board_wifi.h').read_text(encoding='utf-8')
    prefix = re.search(r'#define WIFI_AP_SSID_PREFIX\s+"([^"]*)"', wifi).group(1)
    password = re.search(r'#define WIFI_AP_PASSWORD\s+"([^"]*)"', wifi).group(1)
    assert prefix in readme, 'the tutorial never names the SSID the board broadcasts'
    assert password in readme, 'the tutorial never gives the Wi-Fi password'
    assert '192.168.4.1' in readme, 'the tutorial never gives the address to open'


def test_root_readme_links_to_the_tutorial():
    text = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert 'docs/tutorials/hardware-setup' in text


# --------------------------------------------------------------------------- #
# scripts/pinmap.py - the single source every artefact reads
# --------------------------------------------------------------------------- #

def _pinmap():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        'pinmap', ROOT / 'scripts/pinmap.py')
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules, so the module has to be
    # registered before exec_module or the Wire definition raises.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_pinmap_reads_the_firmware_headers():
    pm = _pinmap()
    a = _defines(FIRMWARE / 'board_audio.h')
    amp = {w.module: w.esp for w in pm.AMP_WIRING}
    assert amp['BCLK'] == f"GPIO {a['AUDIO_PIN_BCLK']}"
    assert amp['LRC'] == f"GPIO {a['AUDIO_PIN_LRCLK']}"
    assert amp['DIN'] == f"GPIO {a['AUDIO_PIN_DIN']}"
    assert not hasattr(pm, 'DISPLAY_WIRING'), (
        'the panel table is back; this build has no panel to wire')


def test_amplifier_sd_and_gain_are_never_wired():
    """SD below 0.16 V puts the MAX98357A in shutdown, so tying it to GND silences
    the amplifier rather than enabling it. Neither pin belongs in the wiring table."""
    pm = _pinmap()
    wired = {w.module for w in pm.AMP_WIRING}
    assert 'SD' not in wired, 'SD must be left floating, not wired'
    assert 'GAIN' not in wired, 'GAIN must be left floating for the default 9 dB'
    assert {name for name, _ in pm.AMP_LEAVE_ALONE} == {'SD', 'GAIN'}


def test_no_gpio_is_double_booked_and_none_land_on_reserved_pins():
    pm = _pinmap()
    wired = pm.wired_gpios()
    for group, pins in pm.RESERVED.items():
        clash = set(wired) & set(pins)
        assert not clash, f'GPIO {clash} is wired but reserved for {group}'
    assert len(wired) == len(set(wired))


def test_free_gpio_list_is_what_the_docs_claim():
    pm = _pinmap()
    assert pm.FREE_GPIOS == [14, 21, 47], (
        f'docs say GPIO 14, 21 and 47 are spare; pinmap computes {pm.FREE_GPIOS}')


def test_every_wired_pin_is_actually_brought_out():
    """A pin the board does not expose fails silently in every other check.

    The build would succeed, the diagram would draw a label, and the only symptom
    would be a module that never responds - so this compares the firmware's choices
    against the physical header read off the board itself.
    """
    pm = _pinmap()
    available = pm.header_gpios()
    for gpio, owner in pm.wired_gpios().items():
        assert gpio in available, (
            f'{owner} is wired to GPIO {gpio}, which is not on either header')


def test_hand_wired_signals_all_land_on_one_row():
    """The reason the amplifier sits on 41/42/2.

    Signals split across both header rows mean reaching over the board to wire it,
    which on a mini breadboard is the difference between tidy and unusable. The top
    row is nearly all DVP camera bus and has no GND, so the bottom row is the only
    candidate - and this keeps a future pin change from quietly undoing it.
    """
    pm = _pinmap()
    rows = {gpio: pm.header_row(gpio) for gpio in pm.wired_gpios()}
    assert all(r == 'bottom' for r in rows.values()), (
        f'these are not all on the bottom row: '
        f'{ {g: r for g, r in rows.items() if r != "bottom"} }')


def test_the_amplifier_signals_are_physically_adjacent():
    """Three wires into three neighbouring holes, which is the whole point."""
    pm = _pinmap()
    row = list(pm.HEADER_BOTTOM)
    at = sorted(row.index(str(gpio))
                for gpio, owner in pm.wired_gpios().items() if owner != 'buzzer')
    assert at == list(range(at[0], at[0] + len(at))), (
        f'the I2S pins are at header positions {at}, which are not consecutive')


def test_the_amplifier_is_off_the_sd_cards_bus():
    """The reason the I2S pins moved, asserted so it cannot silently regress.

    The microSD slot's SDMMC bus is fixed by the PCB. If the amplifier is ever put
    back on 38/39/40 the card stops mounting - and the symptom is a history page
    that is simply always empty, which is a long way from the cause.
    """
    pm = _pinmap()
    sd = set(pm.SDCARD_SDMMC.values())
    amp = set(pm.wired_gpios())
    clash = sd & amp
    assert not clash, f'GPIO {clash} is both the SD bus and wired to a module'
    assert sd == {38, 39, 40}, f'unexpected SD bus {sd}'


# --------------------------------------------------------------------------- #
# The web preview, which is the only user interface the board now has
# --------------------------------------------------------------------------- #

def test_the_page_and_the_firmware_agree_on_the_stream_port():
    """index.html hard-codes a port to use before the first /api/status reply.

    If web_server.h moves the stream and the page does not follow, the preview is
    dark for the first fraction of a second of every page load - and stays dark
    for good if the status poll ever fails.
    """
    header = (FIRMWARE / 'web_server.h').read_text(encoding='utf-8')
    page = (FIRMWARE / 'web/index.html').read_text(encoding='utf-8')
    port = int(re.search(r'#define WEB_PORT_STREAM\s+(\d+)', header).group(1))
    fallback = int(re.search(r'let streamPort = (\d+)', page).group(1))
    assert fallback == port, (
        f'web_server.h streams on {port} but index.html falls back to {fallback}')


def test_the_access_point_password_is_a_legal_wpa2_key():
    """WPA2 needs eight characters. Anything shorter makes esp_wifi_set_config
    fail, which surfaces as a board that never appears in the Wi-Fi list at
    all - a symptom that looks like dead hardware."""
    wifi = (FIRMWARE / 'board_wifi.h').read_text(encoding='utf-8')
    password = re.search(r'#define WIFI_AP_PASSWORD\s+"([^"]*)"', wifi).group(1)
    assert password == '' or len(password) >= 8, (
        f'WIFI_AP_PASSWORD is {len(password)} characters; WPA2 needs 8 or more '
        '(use "" for a deliberately open network)')


def test_the_jpeg_quality_default_is_on_the_right_scale():
    """esp32-camera has two quality scales that run in opposite directions: the
    sensor's jpeg_quality is 0-63 where lower is better, while fmt2jpg takes
    1-100 where higher is better. The web server uses the second, so a value
    copied from the sensor config would quietly produce a mush preview."""
    header = (FIRMWARE / 'web_server.h').read_text(encoding='utf-8')
    quality = int(re.search(r'#define WEB_JPEG_QUALITY_DEFAULT\s+(\d+)',
                            header).group(1))
    assert 50 <= quality <= 95, (
        f'WEB_JPEG_QUALITY_DEFAULT is {quality}; on the 1-100 fmt2jpg scale that '
        'is either unreadable or wasteful, and looks like a 0-63 sensor value')
