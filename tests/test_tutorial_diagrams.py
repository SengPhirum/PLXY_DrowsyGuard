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

def test_display_diagram_pins_match_board_display_header():
    gen = _diagram_module()
    d = _defines(FIRMWARE / 'board_display.h')
    expected = {
        'SCL': d['LCD_PIN_SCK'],
        'SDA': d['LCD_PIN_MOSI'],
        'CS': d['LCD_PIN_CS'],
        'DC': d['LCD_PIN_DC'],
        'RST': d['LCD_PIN_RST'],
    }
    drawn = {row[0]: row[2] for row in gen.DISPLAY_WIRING}
    for pin, gpio in expected.items():
        assert drawn[pin] == f'GPIO{gpio}', (
            f'display pin {pin} is drawn as {drawn[pin]} but board_display.h says GPIO{gpio}')

    # Power pins are not GPIOs and must stay on the rails they belong to.
    assert drawn['VDD'] == '3V3'
    assert drawn['BLK'] == '3V3'
    assert drawn['GND'] == 'GND'


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
    for row in gen.DISPLAY_WIRING + gen.AUDIO_WIRING:
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
    for row in gen.DISPLAY_WIRING:
        if row[2].startswith('GPIO'):
            assert gen.GPIO_ROLES[int(row[2][4:])][0] == 'lcd'
    for row in gen.AUDIO_WIRING:
        if row[2].startswith('GPIO'):
            assert gen.GPIO_ROLES[int(row[2][4:])][0] == 'audio'


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
    display = _defines(FIRMWARE / 'board_display.h')
    audio = _defines(FIRMWARE / 'board_audio.h')
    wired = {display['LCD_PIN_SCK'], display['LCD_PIN_MOSI'], display['LCD_PIN_CS'],
             display['LCD_PIN_DC'], display['LCD_PIN_RST'],
             audio['AUDIO_PIN_BCLK'], audio['AUDIO_PIN_LRCLK'], audio['AUDIO_PIN_DIN']}
    for gpio in wired:
        assert f'GPIO {gpio}' in readme or f'GPIO{gpio}' in readme, (
            f'GPIO {gpio} is wired by the firmware but never named in the tutorial')


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
    d = _defines(FIRMWARE / 'board_display.h')
    a = _defines(FIRMWARE / 'board_audio.h')
    by_module = {w.module: w.esp for w in pm.DISPLAY_WIRING}
    assert by_module['SCL'] == f"GPIO {d['LCD_PIN_SCK']}"
    assert by_module['SDA'] == f"GPIO {d['LCD_PIN_MOSI']}"
    assert by_module['CS'] == f"GPIO {d['LCD_PIN_CS']}"
    assert by_module['DC'] == f"GPIO {d['LCD_PIN_DC']}"
    assert by_module['RST'] == f"GPIO {d['LCD_PIN_RST']}"
    amp = {w.module: w.esp for w in pm.AMP_WIRING}
    assert amp['BCLK'] == f"GPIO {a['AUDIO_PIN_BCLK']}"
    assert amp['LRC'] == f"GPIO {a['AUDIO_PIN_LRCLK']}"
    assert amp['DIN'] == f"GPIO {a['AUDIO_PIN_DIN']}"


def test_backlight_goes_to_3v3_not_ground():
    """BLK is the backlight enable. Grounding it is a common tutorial error that
    leaves the panel dark and reads as a dead display."""
    pm = _pinmap()
    blk = next(w for w in pm.DISPLAY_WIRING if w.module == 'BLK')
    assert blk.esp == '3V3', 'BLK must go to 3V3; grounding it kills the backlight'
    vdd = next(w for w in pm.DISPLAY_WIRING if w.module == 'VDD')
    assert vdd.esp == '3V3', 'VDD is a 3.3 V input; 5 V can destroy the panel'


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
    assert pm.FREE_GPIOS == [1, 3], (
        f'docs say only GPIO 1 and 3 are spare; pinmap computes {pm.FREE_GPIOS}')
