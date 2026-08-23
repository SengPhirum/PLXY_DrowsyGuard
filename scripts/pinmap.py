"""The wiring tables, derived from the firmware headers rather than retyped.

Everything that draws or documents a pin - the poster, the step diagrams, the
tutorial tables - reads from here, and here reads `#define` values straight out of
firmware/esp32s3/main/board_*.h. That is the whole point: a GPIO can only be
changed in one place, and every artefact follows.

There is no display table any more. The SPI panel was removed from the build: the
preview is served to a browser over the ESP32-S3's own Wi-Fi access point (see
firmware/esp32s3/main/web_server.h), which needs no GPIOs at all. That handed
GPIO 14, 21, 41, 42 and 47 back to the free list and cut the wire count from 15
to 7.

tests/test_tutorial_diagrams.py asserts the parse still works and that nothing
double-books a pin.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[1] / 'firmware/esp32s3/main'


def load_pins() -> dict:
    """All `#define NAME <int>` pairs from the board headers."""
    out = {}
    for name in ('board_audio.h', 'board_camera.h'):
        text = (FIRMWARE / name).read_text(encoding='utf-8')
        for m in re.finditer(r'^#define\s+(\w+)\s+(-?\d+)', text, re.M):
            out[m.group(1)] = int(m.group(2))
    if not out:
        raise RuntimeError('no #define pins found; did the headers move?')
    return out


@dataclass(frozen=True)
class Wire:
    module: str      # the label printed on the module
    esp: str         # the label printed on the ESP32-S3 board
    role: str        # 'gnd' | '3v3' | '5v' | 'sig'
    note: str = ''
    alt: str = ''    # other silkscreen spellings seen on these modules


_P = load_pins()


# MAX98357A breakout. GAIN and SD are deliberately absent: both are left floating.
AMP_WIRING = (
    Wire('GND', 'GND', 'gnd', 'connect this one first'),
    Wire('VIN', '5V', '5v', '2.5–5.5 V; use 5 V'),
    Wire('BCLK', f"GPIO {_P['AUDIO_PIN_BCLK']}", 'sig', 'I2S bit clock', alt='BLCK'),
    Wire('LRC', f"GPIO {_P['AUDIO_PIN_LRCLK']}", 'sig', 'I2S word select',
         alt='LRCLK / WS'),
    Wire('DIN', f"GPIO {_P['AUDIO_PIN_DIN']}", 'sig', 'I2S data to amp'),
)

# Pins the amplifier exposes that must NOT be wired, and why.
AMP_LEAVE_ALONE = (
    ('SD', 'Leave floating. Below 0.16 V the part is SHUT DOWN, so tying SD to GND '
           'silences the amplifier rather than enabling it.'),
    ('GAIN', 'Leave floating for the default 9 dB, which is already loud into a '
             '4 ohm 3 W speaker.'),
)

CAMERA_DVP = {
    'XCLK': _P['CAM_PIN_XCLK'], 'SIOD': _P['CAM_PIN_SIOD'], 'SIOC': _P['CAM_PIN_SIOC'],
    'VSYNC': _P['CAM_PIN_VSYNC'], 'HREF': _P['CAM_PIN_HREF'], 'PCLK': _P['CAM_PIN_PCLK'],
    'D7': _P['CAM_PIN_D7'], 'D6': _P['CAM_PIN_D6'], 'D5': _P['CAM_PIN_D5'],
    'D4': _P['CAM_PIN_D4'], 'D3': _P['CAM_PIN_D3'], 'D2': _P['CAM_PIN_D2'],
    'D1': _P['CAM_PIN_D1'], 'D0': _P['CAM_PIN_D0'],
}

RESERVED = {
    'DVP camera bus': sorted(CAMERA_DVP.values()),
    'SPI flash + octal PSRAM': list(range(33, 38)),
    'native USB D-/D+': [19, 20],
    'UART0 console': [43, 44],
    'strapping / BOOT': [0, 45, 46],
    'on-board RGB LED': [48],
}

BUZZER_GPIO = 2

# The five GPIOs the SPI panel used to hold. Nothing in this build claims them, and
# they are listed by name so the tutorial can say what became free and why.
FREED_BY_WEB_PREVIEW = (14, 21, 41, 42, 47)


def wired_gpios() -> dict:
    """GPIO number -> what claims it, for every wire in this build."""
    out = {}
    for w in AMP_WIRING:
        m = re.fullmatch(r'GPIO (\d+)', w.esp)
        if m:
            out[int(m.group(1))] = w.module
    out[BUZZER_GPIO] = 'buzzer'
    return out


def free_gpios() -> list:
    """GPIOs on the module that nothing in this build or the board itself claims."""
    taken = set(wired_gpios())
    for group in RESERVED.values():
        taken.update(group)
    # 22..32 are not bonded out on the WROOM-1 module at all.
    return [g for g in range(0, 49) if g not in taken and not (22 <= g <= 32)]


FREE_GPIOS = free_gpios()
