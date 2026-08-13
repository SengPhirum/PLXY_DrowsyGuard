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
    assert len(names) == 11, 'the tutorial refers to eleven figures'
    pngs = sorted(IMAGES.glob('*.png'))
    assert len(pngs) == 11, f'expected 11 diagrams, found {len(pngs)}'
    for p in pngs:
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
