#!/usr/bin/env python3
"""Generate the wiring/identification diagrams for docs/tutorials/hardware-setup/.

The PNGs in that folder are build products of this script, so the wiring shown in
them cannot drift away from the wiring tables: every pin string drawn here is
checked against the firmware headers by tests/test_tutorial_diagrams.py.

Run:  python scripts/generate_tutorial_diagrams.py

Deliberate scope limit
----------------------
These are connection diagrams, not photographs of a specific PCB revision. The
physical top-to-bottom order of the pins on the ESP32-S3 board's two headers is
NOT drawn, because it varies between batches of this board and could not be
verified against a datasheet while writing this. Every connection is therefore
keyed to the *printed silkscreen label* (``GPIO14``, ``3V3``, ``GND`` ...), which
is stable, rather than to a position in a drawing, which is not.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagram_fonts  # noqa: E402  (needs the sys.path line above)

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# Canvas / style
# --------------------------------------------------------------------------- #

SS = 2  # supersampling factor; everything is drawn at SSx then resized down

BG = (247, 248, 250)
INK = (23, 28, 38)
MUTED = (108, 118, 134)
HAIRLINE = (203, 210, 220)
PANEL = (255, 255, 255)

# Wire colours follow common dupont practice: red = 5 V, orange = 3.3 V,
# black = ground, and one distinct colour per signal.
C_5V = (214, 48, 49)
C_3V3 = (230, 126, 34)
C_GND = (35, 39, 47)
# Signal colours deliberately exclude every red and orange hue: those two are
# reserved for the 5 V and 3.3 V rails, and a red signal wire in a diagram whose
# legend says "red = 5 V" is exactly the sort of ambiguity that gets a part wired
# to the wrong supply.
C_SIG = [
    (41, 128, 185),   # blue
    (39, 174, 96),    # green
    (142, 68, 173),   # purple
    (241, 196, 15),   # yellow
    (26, 188, 156),   # teal
    (52, 73, 94),     # slate
    (120, 80, 60),    # brown
    (200, 80, 160),   # magenta
]

# PCB colours taken from the product photographs of the actual purchased items.
PCB_BLACK = (28, 30, 34)
PCB_BLUE = (28, 62, 122)
PCB_PURPLE = (126, 61, 155)
PCB_GREEN_TERM = (86, 176, 86)
BREADBOARD = (240, 236, 226)
SCREEN = (12, 14, 18)

# Resolved at import time rather than hard-coded to a Linux path, so the artwork
# can be regenerated on the same machine the firmware is flashed from. See
# scripts/diagram_fonts.py for where it looks and why it insists on DejaVu.
_F_REG, _F_BOLD, _F_MONO, _F_MONO_BOLD = diagram_fonts.faces()


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size * SS)


class Canvas:
    """Thin drawing helper: everything takes final-scale coordinates."""

    def __init__(self, w: int, h: int, bg=BG):
        self.w, self.h = w, h
        self.img = Image.new('RGB', (w * SS, h * SS), bg)
        self.d = ImageDraw.Draw(self.img)
        self._sig = 0

    # -- primitives -------------------------------------------------------- #
    def _s(self, *v):
        return [x * SS for x in v]

    def rect(self, x, y, w, h, fill=None, outline=None, width=1, radius=0):
        box = self._s(x, y, x + w, y + h)
        if radius:
            self.d.rounded_rectangle(box, radius=radius * SS, fill=fill,
                                     outline=outline, width=width * SS)
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=width * SS)

    def line(self, pts, fill=INK, width=2):
        self.d.line([c * SS for p in pts for c in p], fill=fill,
                    width=max(1, width * SS), joint='curve')

    def circle(self, x, y, r, fill=None, outline=None, width=1):
        self.d.ellipse(self._s(x - r, y - r, x + r, y + r), fill=fill,
                       outline=outline, width=width * SS)

    def text(self, x, y, s, size=16, fill=INK, bold=False, mono=False,
             anchor='la', angle=0):
        path = (_F_MONO_BOLD if bold else _F_MONO) if mono else (_F_BOLD if bold else _F_REG)
        f = _font(path, size)
        if angle:
            tmp = Image.new('RGBA', (int(len(s) * size * 1.4 * SS) + 8 * SS,
                                     int(size * 2.0 * SS)), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((0, 0), s, font=f, fill=fill)
            tmp = tmp.rotate(angle, expand=True, resample=Image.BICUBIC)
            self.img.paste(tmp, (int(x * SS), int(y * SS)), tmp)
        else:
            self.d.text(self._s(x, y), s, font=f, fill=fill, anchor=anchor)

    def text_w(self, s, size=16, bold=False, mono=False) -> float:
        path = (_F_MONO_BOLD if bold else _F_MONO) if mono else (_F_BOLD if bold else _F_REG)
        return self.d.textlength(s, font=_font(path, size)) / SS

    # -- composites -------------------------------------------------------- #
    def title(self, s, sub=None):
        self.text(40, 32, s, size=30, bold=True)
        if sub:
            self.text(40, 74, sub, size=16, fill=MUTED)
        self.line([(40, 104), (self.w - 40, 104)], fill=HAIRLINE, width=1)

    def panel(self, x, y, w, h, label=None, fill=PANEL, radius=10):
        self.rect(x, y, w, h, fill=fill, outline=HAIRLINE, width=1, radius=radius)
        if label:
            self.text(x + 16, y + 12, label, size=15, bold=True, fill=MUTED)

    def chip(self, x, y, w, h, label, fill=PCB_BLACK, tc=(235, 238, 243), sub=None):
        self.rect(x, y, w, h, fill=fill, radius=6)
        self.text(x + w / 2, y + h / 2 - (9 if sub else 0), label, size=15,
                  bold=True, fill=tc, anchor='mm')
        if sub:
            self.text(x + w / 2, y + h / 2 + 11, sub, size=12, fill=tc, anchor='mm')

    def pin(self, x, y, label, colour=INK, side='right', size=13, pad=9,
            box=True, boxfill=(255, 255, 255)):
        """A pin pad with its printed label. Returns the wire attachment point."""
        r = 6
        self.circle(x, y, r, fill=(250, 214, 120), outline=(120, 100, 40), width=1)
        tw = self.text_w(label, size=size, mono=True, bold=True)
        if side == 'right':
            tx = x + pad + r
            if box:
                self.rect(tx - 5, y - 11, tw + 10, 22, fill=boxfill,
                          outline=HAIRLINE, width=1, radius=4)
            self.text(tx, y, label, size=size, mono=True, bold=True, fill=colour,
                      anchor='lm')
            return (x, y)
        tx = x - pad - r - tw
        if box:
            self.rect(tx - 5, y - 11, tw + 10, 22, fill=boxfill,
                      outline=HAIRLINE, width=1, radius=4)
        self.text(tx, y, label, size=size, mono=True, bold=True, fill=colour,
                  anchor='lm')
        return (x, y)

    def wire(self, a, b, colour, label=None, midx=None, width=4, label_side='top',
             label_size=13):
        """Orthogonal wire from a to b with a rounded elbow and an optional tag."""
        (x1, y1), (x2, y2) = a, b
        mx = midx if midx is not None else (x1 + x2) / 2
        pts = [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
        self.line(pts, fill=colour, width=width)
        self.circle(x1, y1, 3, fill=colour)
        self.circle(x2, y2, 3, fill=colour)
        if label:
            tw = self.text_w(label, size=label_size, mono=True, bold=True)
            ly = (y1 + y2) / 2
            self.rect(mx - tw / 2 - 7, ly - 12, tw + 14, 24, fill=(255, 255, 255),
                      outline=colour, width=1, radius=6)
            self.text(mx, ly, label, size=label_size, mono=True, bold=True,
                      fill=colour, anchor='mm')

    def callout(self, x, y, tx, ty, text, size=13, colour=MUTED, anchor='la'):
        self.line([(x, y), (tx, ty)], fill=colour, width=1)
        self.circle(x, y, 3, fill=colour)
        self.text(tx, ty, text, size=size, fill=colour, anchor=anchor)

    def note(self, x, y, w, lines, kind='info'):
        colours = {'info': ((234, 242, 252), (41, 128, 185)),
                   'warn': ((255, 244, 229), (211, 84, 0)),
                   'danger': ((253, 236, 234), (192, 57, 43)),
                   'ok': ((233, 248, 239), (39, 174, 96))}
        bg, edge = colours[kind]
        h = 18 + 21 * len(lines)
        self.rect(x, y, w, h, fill=bg, radius=8)
        self.rect(x, y, 5, h, fill=edge, radius=2)
        for i, ln in enumerate(lines):
            self.text(x + 18, y + 12 + 21 * i, ln, size=13,
                      fill=(60, 66, 78), bold=(i == 0))
        return h

    def legend(self, x, y, items, cols=1, gap=200):
        for i, (colour, text) in enumerate(items):
            cx = x + (i % cols) * gap
            cy = y + (i // cols) * 24
            self.rect(cx, cy + 5, 22, 5, fill=colour, radius=2)
            self.text(cx + 30, cy, text, size=13, fill=(70, 76, 88))

    def next_sig(self):
        c = C_SIG[self._sig % len(C_SIG)]
        self._sig += 1
        return c

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.img.resize((self.w, self.h), Image.LANCZOS).save(path, 'PNG', optimize=True)
        return path


# --------------------------------------------------------------------------- #
# Verified hardware facts. These strings are what the tests compare against.
# --------------------------------------------------------------------------- #

# There is no display table. The SPI panel was removed from the build: the live
# preview is served to a phone or laptop over the board's own Wi-Fi access point
# (firmware/esp32s3/main/web_server.h), which costs no GPIOs and no wires. What
# used to be figure 5 - eight wires to a 1.8" panel - is now a page in a browser.

# From firmware/esp32s3/main/board_audio.h
# Three adjacent pins on the bottom header row. Off 38/39/40 because those are the
# microSD slot's SDMMC bus, and off 14/21/47 because 14 is on the *other* header row
# - wiring across the board is what makes a mini breadboard unusable.
AUDIO_WIRING = [
    ('GND', '', 'GND', 'gnd', 'Common ground - connect this first'),
    ('VIN', '', '5V', '5v', 'Amplifier supply, 5 V for full output'),
    ('BCLK', 'BLCK', 'GPIO41', 'sig', 'I2S bit clock'),
    ('LRC', 'LRCLK / WS', 'GPIO42', 'sig', 'I2S word select'),
    ('DIN', '', 'GPIO2', 'sig', 'I2S serial data, ESP32-S3 to amplifier'),
]

# The physical header order, read off a photograph of the board (2026-08-23). Every
# earlier revision of this file said it could not be verified and keyed everything
# to the printed label instead.
HEADER_TOP = ('5V', '14', '13', '12', '11', '10', '9', '46', '3', '8',
              '18', '17', '16', '15', '7', '6', '5', '4', 'EN', '3V3')
HEADER_BOTTOM = ('GND', '19', '20', '21', '47', '48', '45', '0', '35', '36',
                 '37', '38', '39', '40', '41', '42', '2', '1', 'RX', 'TX')

# The microSD slot. Nothing to wire - it is on the board - but it owns three GPIOs
# and that is the whole reason the amplifier is where it is.
SDCARD_PINS = [('CLK', 39), ('CMD', 38), ('D0', 40)]

# From firmware/esp32s3/main/board_camera.h - the DVP bus is on the board's FPC
# connector, not on the header, so this is reference material rather than wiring.
CAMERA_PINS = [
    ('XCLK', 15), ('SIOD', 4), ('SIOC', 5), ('VSYNC', 6), ('HREF', 7),
    ('PCLK', 13), ('D7/Y9', 16), ('D6/Y8', 17), ('D5/Y7', 18), ('D4/Y6', 12),
    ('D3/Y5', 10), ('D2/Y4', 8), ('D1/Y3', 9), ('D0/Y2', 11),
]

GPIO_ROLES = {}
for _n in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18):
    GPIO_ROLES[_n] = ('camera', 'DVP camera bus')
for _n in range(33, 38):
    GPIO_ROLES[_n] = ('flash', 'SPI flash + octal PSRAM')
for _n in (19, 20):
    GPIO_ROLES[_n] = ('usb', 'native USB D-/D+')
for _n in (43, 44):
    GPIO_ROLES[_n] = ('uart', 'UART0 console')
for _n in (0, 45, 46):
    GPIO_ROLES[_n] = ('strap', 'strapping / BOOT')
GPIO_ROLES[48] = ('led', 'on-board RGB LED')
for _n in (38, 39, 40):
    GPIO_ROLES[_n] = ('sdcard', 'microSD slot')
for _n in (41, 42, 2):
    GPIO_ROLES[_n] = ('audio', 'MAX98357A I2S')
GPIO_ROLES[1] = ('buzzer', 'buzzer fallback')
# GPIO 3 is the JTAG-source strapping pin on the ESP32-S3, not a plain GPIO. It was
# listed as free until 2026-08-23, which was wrong.
GPIO_ROLES[3] = ('strap', 'strapping / BOOT')
# What the panel gave back and nothing has taken: all on the bottom row bar 14.
for _n in (14, 21, 47):
    GPIO_ROLES[_n] = ('free', 'free - was the panel')

ROLE_COLOUR = {
    'camera': (52, 73, 94), 'flash': (192, 57, 43), 'usb': (127, 140, 141),
    'uart': (155, 89, 182), 'strap': (211, 84, 0), 'led': (22, 160, 133),
    'audio': (39, 174, 96), 'buzzer': (241, 196, 15), 'sdcard': (142, 68, 173),
    'free': (189, 195, 199), 'na': (222, 226, 230),
}

# Four items, not five. The 1.8" SPI panel (item 1885) is no longer part of the
# build - the preview moved to a browser - so it is not listed and does not need
# to be bought.
PRODUCTS = [
    ('ESP32-S3 N16R8 development board with OV3660', '2991', '$7.50',
     'Controller + camera + Wi-Fi', PCB_BLACK),
    ('MAX98357 I2S audio amplifier filterless class D', '2724', '$2.00',
     'Spoken-alert amplifier', PCB_PURPLE),
    ('High quality speaker 3 watt 4 ohm 40mmX22mm', '2554', '$0.75',
     'Alert loudspeaker', (60, 60, 64)),
    ('Breadboard 830 point solderless MB102 test board', '371', '$1.50',
     'Solderless prototyping + power rails', (196, 190, 176)),
]

OUT = Path(__file__).resolve().parents[1] / 'docs/tutorials/hardware-setup/images'


def _wire_colour(role, canvas):
    return {'gnd': C_GND, '3v3': C_3V3, '5v': C_5V}.get(role) or canvas.next_sig()


# --------------------------------------------------------------------------- #
# 01 - components overview
# --------------------------------------------------------------------------- #

def draw_esp32_board(c, x, y, w, h, labels=True):
    c.rect(x, y, w, h, fill=PCB_BLACK, radius=6)
    # WROOM-1 module with its shielded can and antenna at the top edge
    mw, mh = w * 0.46, h * 0.30
    mx, my = x + (w - mw) / 2, y + h * 0.20
    c.rect(mx, my, mw, mh, fill=(196, 200, 206), radius=3)
    if mw > 80:   # below this the label does not fit inside the can
        c.text(mx + mw / 2, my + mh / 2, 'ESP32-S3', size=10, bold=True,
               fill=(40, 44, 50), anchor='mm')
    for i in range(6):
        c.rect(x + w * 0.32 + i * (w * 0.07), y + h * 0.06, w * 0.04, h * 0.09,
               fill=(228, 230, 234), radius=1)
    # two header strips
    for hx in (x + 7, x + w - 13):
        for i in range(11):
            c.rect(hx, y + h * 0.14 + i * (h * 0.062), 6, 6, fill=(212, 176, 90), radius=1)
    # FPC camera connector
    c.rect(x + w * 0.24, y + h * 0.58, w * 0.52, h * 0.07, fill=(40, 44, 52), radius=2)
    # two USB-C ports on the bottom edge
    for i, cx in enumerate((x + w * 0.27, x + w * 0.57)):
        c.rect(cx, y + h - 9, w * 0.16, 12, fill=(180, 186, 194), radius=4)
    if labels:
        c.text(x + w / 2, y + h * 0.72, 'N16R8', size=10, fill=(150, 156, 166), anchor='mm')


def draw_preview_page(c, x, y, w, h, face=True):
    """The page firmware/esp32s3/main/web/index.html serves, in miniature.

    Drawn rather than screenshotted on purpose: a screenshot would pin the figure
    to one firmware build and one phone, and would have to be retaken every time
    a pill moves. The point of the figure is the shape of the page - preview on
    top, risk underneath, status pills at the bottom - not its exact pixels.
    """
    c.rect(x, y, w, h, fill=(14, 17, 22))
    # header strip with the live-link dot
    c.rect(x, y, w, h * 0.09, fill=(22, 27, 34))
    c.circle(x + w * 0.07, y + h * 0.045, max(2, w * 0.014), fill=(63, 185, 80))
    c.text(x + w * 0.13, y + h * 0.045, 'DrowsyGuard', size=max(8, int(w * 0.045)),
           bold=True, fill=(230, 237, 243), anchor='lm')

    # live view
    vx, vy, vw, vh = x + w * 0.05, y + h * 0.13, w * 0.90, h * 0.46
    c.rect(vx, vy, vw, vh, fill=(6, 8, 11), radius=3)
    if face:
        # a head, so the frame reads as a camera feed and not as an empty box
        hcx, hcy, hr = vx + vw / 2, vy + vh * 0.52, min(vw, vh) * 0.26
        c.circle(hcx, hcy, hr, fill=(62, 70, 82))
        c.circle(hcx - hr * 0.38, hcy - hr * 0.18, hr * 0.11, fill=(226, 232, 240))
        c.circle(hcx + hr * 0.38, hcy - hr * 0.18, hr * 0.11, fill=(226, 232, 240))
        c.rect(hcx - hr * 0.28, hcy + hr * 0.42, hr * 0.56, hr * 0.12,
               fill=(206, 212, 220), radius=1)
        c.rect(hcx - hr * 1.15, hcy - hr * 1.25, hr * 2.3, hr * 2.4,
               outline=(63, 185, 80), width=max(1, int(w * 0.006)))
        c.text(hcx - hr * 1.15, hcy - hr * 1.25 - h * 0.035, 'face 0.87',
               size=max(7, int(w * 0.032)), fill=(63, 185, 80))

    # risk score and bar
    ty = vy + vh + h * 0.06
    c.text(vx, ty, 'RISK', size=max(7, int(w * 0.032)), fill=(139, 148, 158))
    c.text(vx + vw, ty, '0.18', size=max(7, int(w * 0.032)), fill=(230, 237, 243),
           anchor='ra')
    br = ty + h * 0.055
    c.rect(vx, br, vw, h * 0.028, fill=(11, 15, 20), radius=3)
    c.rect(vx, br, vw * 0.18, h * 0.028, fill=(63, 185, 80), radius=3)

    # status pills
    py = br + h * 0.075
    for i, (label, col) in enumerate((('camera', (63, 185, 80)),
                                      ('models', (63, 185, 80)),
                                      ('eye model', (210, 153, 34)))):
        pw = vw * 0.30
        px = vx + i * (vw * 0.35)
        c.rect(px, py, pw, h * 0.075, fill=(33, 38, 45), outline=col, width=1, radius=6)
        c.text(px + pw / 2, py + h * 0.037, label, size=max(6, int(w * 0.026)),
               fill=col, anchor='mm')


def draw_phone(c, x, y, w, h, face=True):
    """A phone with the preview page on it."""
    c.rect(x, y, w, h, fill=(38, 41, 48), radius=int(min(w, h) * 0.09))
    bez = max(4, w * 0.045)
    draw_preview_page(c, x + bez, y + bez * 1.6, w - 2 * bez, h - bez * 3.2, face=face)
    c.rect(x + w * 0.36, y + h - bez * 1.2, w * 0.28, max(2, bez * 0.28),
           fill=(120, 126, 136), radius=2)


def draw_amp_module(c, x, y, w, h):
    c.rect(x, y, w, h, fill=PCB_PURPLE, radius=4)
    c.rect(x + w * 0.34, y + h * 0.30, w * 0.30, h * 0.30, fill=(32, 34, 40), radius=2)
    c.rect(x + w * 0.04, y + h * 0.58, w * 0.34, h * 0.34, fill=PCB_GREEN_TERM, radius=3)
    for i in range(7):
        c.rect(x + w * 0.10 + i * (w * 0.115), y + 4, 6, 6, fill=(212, 176, 90), radius=1)


def draw_speaker(c, x, y, w, h):
    cx, cy = x + w / 2, y + h / 2
    r = min(w, h) / 2
    c.circle(cx, cy, r, fill=(52, 54, 58), outline=(150, 154, 160), width=2)
    c.circle(cx, cy, r * 0.62, fill=(30, 32, 36))
    c.circle(cx, cy, r * 0.30, fill=(120, 124, 130))
    c.line([(cx - r, cy + r * 0.86), (cx - r - 14, cy + r * 0.86)], fill=C_5V, width=3)
    c.line([(cx - r, cy + r * 0.60), (cx - r - 14, cy + r * 0.60)], fill=C_GND, width=3)


def draw_breadboard(c, x, y, w, h, detail=True):
    c.rect(x, y, w, h, fill=BREADBOARD, outline=(200, 194, 180), width=1, radius=4)
    c.line([(x + 6, y + h * 0.09), (x + w - 6, y + h * 0.09)], fill=C_5V, width=2)
    c.line([(x + 6, y + h * 0.15), (x + w - 6, y + h * 0.15)], fill=C_GND, width=2)
    c.line([(x + 6, y + h * 0.85), (x + w - 6, y + h * 0.85)], fill=C_5V, width=2)
    c.line([(x + 6, y + h * 0.91), (x + w - 6, y + h * 0.91)], fill=C_GND, width=2)
    if detail:
        c.rect(x + 6, y + h * 0.47, w - 12, h * 0.06, fill=(224, 219, 206))
        for col in range(int((w - 20) / 9)):
            for row in range(5):
                for base in (0.22, 0.56):
                    c.rect(x + 12 + col * 9, y + h * base + row * (h * 0.048), 3, 3,
                           fill=(176, 170, 158))


def fig_components():
    c = Canvas(1680, 1080)
    c.title('Figure 1 - The four purchased components',
            'Every item below is one khmeres.com order line. Identify each one before wiring anything. No display: the preview is a web page.')
    drawers = [draw_esp32_board, draw_amp_module, draw_speaker, draw_breadboard]
    positions = [(60, 150, 780, 400), (880, 150, 740, 400),
                 (60, 610, 780, 400), (880, 610, 740, 400)]
    for (name, item, price, role, _), drawer, (x, y, w, h) in zip(PRODUCTS, drawers, positions):
        c.panel(x, y, w, h)
        pad = 30
        drawer(c, x + pad + 30, y + 60, w - 2 * pad - 60, h - 190)
        c.text(x + pad, y + h - 108, name, size=15, bold=True)
        c.text(x + pad, y + h - 82, role, size=14, fill=(41, 128, 185))
        c.text(x + pad, y + h - 56, f'khmeres.com item {item}   -   {price}', size=13,
               fill=MUTED, mono=True)
    return c.save(OUT / '01-components-overview.png')


# --------------------------------------------------------------------------- #
# 02 - GPIO allocation map
# --------------------------------------------------------------------------- #

def fig_pin_map():
    c = Canvas(1680, 1000)
    c.title('Figure 2 - ESP32-S3-WROOM-1 N16R8 GPIO allocation',
            'Which of the 49 GPIOs are taken, and the three that are free. Everything wired by hand is on the bottom header row, on adjacent pins.')
    cols, cw, ch = 7, 200, 74
    x0, y0 = 60, 150
    for n in range(49):
        cx = x0 + (n % cols) * cw
        cy = y0 + (n // cols) * ch
        role, desc = GPIO_ROLES.get(n, ('na', 'not broken out / unused'))
        col = ROLE_COLOUR[role]
        light = tuple(int(v + (255 - v) * 0.86) for v in col)
        c.rect(cx, cy, cw - 12, ch - 12, fill=light, outline=col, width=2, radius=7)
        c.rect(cx, cy, 6, ch - 12, fill=col, radius=2)
        c.text(cx + 16, cy + 10, f'GPIO {n}', size=14, bold=True, mono=True)
        c.text(cx + 16, cy + 33, desc, size=11, fill=(80, 86, 98))
    ly = y0 + 7 * ch + 16
    c.legend(60, ly, [
        (ROLE_COLOUR['camera'], 'DVP camera bus (fixed by the board)'),
        (ROLE_COLOUR['flash'], 'SPI flash + octal PSRAM - never drive'),
        (ROLE_COLOUR['audio'], 'MAX98357A I2S (this build)'),
        (ROLE_COLOUR['sdcard'], 'microSD slot - event history'),
        (ROLE_COLOUR['buzzer'], 'Buzzer fallback (this build)'),
        (ROLE_COLOUR['uart'], 'UART0 console - idf.py monitor'),
        (ROLE_COLOUR['usb'], 'Native USB D-/D+'),
        (ROLE_COLOUR['strap'], 'Strapping / BOOT'),
        (ROLE_COLOUR['led'], 'On-board RGB LED'),
        (ROLE_COLOUR['free'], 'Free - GPIO 14, 21, 47'),
    ], cols=2, gap=560)
    c.note(1180, ly - 6, 440, [
        'GPIO 33-37 are fatal.',
        'They carry the octal PSRAM. Driving one',
        'hangs the board, and the symptom looks',
        'like a broken camera, not a wiring fault.',
    ], kind='danger')
    return c.save(OUT / '02-controller-pin-map.png')


# --------------------------------------------------------------------------- #
# 03 - USB ports and buttons
# --------------------------------------------------------------------------- #

def fig_usb_ports():
    c = Canvas(1680, 860)
    c.title('Figure 3 - Which USB-C port to plug into',
            'The board has two USB-C sockets. They are not interchangeable: use the UART one for flashing.')
    bx, by, bw, bh = 420, 160, 500, 560
    draw_esp32_board(c, bx, by, bw, bh, labels=False)
    left_x = bx + bw * 0.35
    right_x = bx + bw * 0.65
    c.callout(left_x, by + bh - 4, 120, by + bh + 40,
              'UART port - CH340/CP2102 bridge', size=15, colour=(39, 174, 96))
    c.text(120, by + bh + 64, 'USE THIS ONE. Gives a COM port that survives', size=13, fill=MUTED)
    c.text(120, by + bh + 86, 'resets and firmware crashes.', size=13, fill=MUTED)
    c.callout(right_x, by + bh - 4, 1000, by + bh + 40,
              'USB / OTG port - native USB-serial-JTAG', size=15, colour=(211, 84, 0))
    c.text(1000, by + bh + 64, 'Works, but re-enumerates on every reset and', size=13, fill=MUTED)
    c.text(1000, by + bh + 86, 'disappears if firmware reconfigures GPIO 19/20.', size=13, fill=MUTED)
    c.callout(bx + bw * 0.5, by + bh * 0.61, 1000, by + 150,
              'Camera FPC connector (flip the black latch up)', size=14)
    c.callout(bx + bw * 0.5, by + bh * 0.28, 1000, by + 260,
              'ESP32-S3-WROOM-1 N16R8 module', size=14)
    c.callout(bx + bw * 0.5, by + bh * 0.09, 1000, by + 320,
              'Antenna edge - keep clear of metal', size=14)
    c.callout(bx + 10, by + bh * 0.35, 120, by + 200,
              'Pin header - match printed labels', size=14)
    c.note(120, by + 260, 260, [
        'If flashing will not start',
        'Hold BOOT, tap RESET,',
        'release BOOT, then flash.',
    ], kind='warn')
    c.note(60, 780, 1560, [
        'A charge-only USB-C cable is the single most common cause of "the board does not appear". The cable must carry data.',
    ], kind='danger')
    return c.save(OUT / '03-usb-ports.png')


# --------------------------------------------------------------------------- #
# 04 - camera ribbon
# --------------------------------------------------------------------------- #

def fig_camera_ribbon():
    c = Canvas(1680, 800)
    c.title('Figure 4 - Seating the OV3660 camera ribbon',
            'Nothing to wire: the camera is a flat flexible cable (FPC) into the board connector. It is the only step that can be done wrong silently.')
    steps = [
        ('1. Latch UP', 'Flip the black latch upwards. It hinges; it does not pull out.'),
        ('2. Slide IN', 'Contacts face DOWN, towards the board. Push in until the blue\nstiffener reaches the connector body.'),
        ('3. Latch DOWN', 'Press the latch flat. The ribbon should not move when tugged\ngently.'),
    ]
    for i, (head, body) in enumerate(steps):
        x = 60 + i * 530
        c.panel(x, 150, 500, 420, )
        c.text(x + 26, 172, head, size=19, bold=True, fill=(41, 128, 185))
        # connector
        cx, cy, cw2 = x + 60, 300, 380
        c.rect(cx, cy, cw2, 46, fill=(40, 44, 52), radius=3)
        if i == 0:
            c.rect(cx, cy - 34, cw2, 30, fill=(70, 76, 88), radius=3)
            c.text(cx + cw2 / 2, cy - 19, 'latch open', size=12, fill=(230, 233, 238), anchor='mm')
        else:
            c.rect(cx, cy + 44, cw2, 22, fill=(70, 76, 88), radius=3)
            c.text(cx + cw2 / 2, cy + 55, 'latch closed', size=12, fill=(230, 233, 238), anchor='mm')
        if i >= 1:
            c.rect(cx + 40, cy + 10, cw2 - 80, 26, fill=(214, 190, 132), radius=2)
            c.rect(cx + 40, cy + 10, cw2 - 80, 8, fill=(60, 90, 150))
            c.text(cx + cw2 / 2, cy + 26, 'contacts DOWN', size=11, bold=True,
                   fill=(60, 50, 20), anchor='mm')
        for j, ln in enumerate(body.split('\n')):
            c.text(x + 26, 400 + j * 22, ln, size=13, fill=(80, 86, 98))
    c.note(60, 610, 1560, [
        'A half-seated ribbon reports esp_camera_init failed: 0x105 (ESP_ERR_NOT_FOUND) - the same code as a wrong pin map.',
        'The OV3660 PWDN and RESET lines are not routed on this board, so the sensor is always powered and is reset over SCCB.',
    ], kind='warn')
    return c.save(OUT / '04-camera-ribbon.png')


# --------------------------------------------------------------------------- #
# Shared: a module-to-controller wiring figure
# --------------------------------------------------------------------------- #

def _wiring_figure(fname, fignum, title, subtitle, rows, module_name, module_drawer,
                   module_pin_labels, notes, extra=None, height=1080):
    c = Canvas(1680, height)
    c.title(f'Figure {fignum} - {title}', subtitle)

    # Rows start 160 px below the panel top and step by 52; the panel has to
    # enclose the last one with room to spare, hence the +40 tail.
    row_h, row_top = 52, 160
    mod_x, mod_y, mod_w = 150, 210, 300
    mod_h = row_top + row_h * (len(rows) - 1) + 40
    esp_x, esp_y, esp_w = 1180, 210, 300
    esp_h = mod_h

    c.rect(mod_x, mod_y, mod_w, mod_h, fill=PANEL, outline=HAIRLINE, width=1, radius=10)
    c.text(mod_x + mod_w / 2, mod_y + 24, module_name, size=15, bold=True, anchor='mm')
    module_drawer(c, mod_x + 40, mod_y + 46, mod_w - 80, 90)

    c.rect(esp_x, esp_y, esp_w, esp_h, fill=PANEL, outline=HAIRLINE, width=1, radius=10)
    c.text(esp_x + esp_w / 2, esp_y + 24, 'ESP32-S3-WROOM-1 N16R8', size=15, bold=True,
           anchor='mm')
    draw_esp32_board(c, esp_x + 105, esp_y + 46, 90, 90, labels=False)

    y = mod_y + row_top
    for (mpin, alt, epin, role, purpose) in rows:
        colour = _wire_colour(role, c)
        a = c.pin(mod_x + mod_w - 22, y, mpin, side='left', colour=colour)
        b = c.pin(esp_x + 22, y, epin, side='right', colour=colour)
        c.wire(a, b, colour, label=f'{mpin} -> {epin}')
        if alt:
            c.text(mod_x + 20, y - 4, f'also printed {alt}', size=11, fill=MUTED)
        c.text(esp_x + 130, y + 14, purpose, size=11, fill=MUTED)
        y += row_h

    ny = mod_y + mod_h + 40
    if extra:
        ny = extra(c, ny)
    for kind, lines in notes:
        ny += c.note(150, ny, 1330, lines, kind=kind) + 16
    return c.save(OUT / fname)


def fig_web_preview():
    """What used to be eight wires to a panel is now three taps on a phone."""
    c = Canvas(1680, 1080)
    c.title('Figure 5 - Watching the camera: join the board\'s own Wi-Fi  (0 wires)',
            'The SPI panel was removed. The ESP32-S3 serves the live preview itself, so there is nothing to wire for this step.')

    # -- the phone, with the page on it ------------------------------------ #
    c.panel(60, 150, 460, 700, 'What you end up looking at')
    draw_phone(c, 150, 220, 280, 560)
    c.text(290, 800, 'Any phone, tablet or laptop browser', size=13, fill=MUTED,
           anchor='ma')

    # -- the three steps --------------------------------------------------- #
    steps = [
        ('1', 'Power the board', [
            'Plug the UART USB-C port into a phone charger or',
            'a laptop. You should hear the three-note boot chime.',
        ]),
        ('2', 'Join the Wi-Fi network', [
            'SSID      DrowsyGuard-XXXXXX   (XXXXXX is the last three',
            '          bytes of the board\'s MAC, so two boards differ)',
            'Password  drowsyguard',
            'Your phone will warn that this network has no internet.',
            'That is correct - the board is the network.',
        ]),
        ('3', 'Open the page', [
            'http://192.168.4.1/',
            'The preview starts on its own. Everything else on the',
            'page - risk, PERCLOS, event log, speaker test - is live.',
        ]),
    ]
    y = 150
    for num, head, lines in steps:
        hh = 90 + 26 * len(lines)
        c.panel(560, y, 1060, hh)
        c.circle(600, y + 44, 22, fill=(41, 128, 185))
        c.text(600, y + 44, num, size=20, bold=True, fill=(255, 255, 255), anchor='mm')
        c.text(640, y + 32, head, size=19, bold=True)
        for i, ln in enumerate(lines):
            c.text(640, y + 74 + i * 26, ln, size=14, fill=(80, 86, 98),
                   mono=ln.startswith(('SSID', 'Password', 'http', '   ')))
        y += hh + 24

    c.note(560, y, 1060, [
        'One live stream at a time, and that is a design decision rather than a bug.',
        'ESP-IDF\'s HTTP server answers one request per instance at a time, and an MJPEG stream never finishes, so the board runs a second',
        'server on port 81 just for it. A second phone still gets the full page and a still image every 0.7 s from port 80.',
    ], kind='info')

    c.note(60, 880, 460, [
        'Change the SSID or password in',
        'firmware/esp32s3/main/board_wifi.h.',
        'Set WIFI_AP_PASSWORD to "" for an open',
        'network - convenient on the bench, wrong',
        'in a car: the stream is the driver\'s face.',
    ], kind='warn')

    # The HTTP surface, which is the nearest thing a headless build has to a
    # connector pinout: everything an examiner or a script can reach is here.
    ey = 900
    c.panel(560, ey, 1060, 150, 'Everything the board serves')
    routes = [
        ('GET  /', ':80', 'the page itself, linked into the firmware binary'),
        ('GET  /stream', ':81', 'MJPEG live preview - one viewer at a time'),
        ('GET  /api/snapshot', ':80', 'one JPEG; the fallback for extra viewers'),
        ('GET  /api/status', ':80', 'risk, PERCLOS, face box, rates, heap, uptime'),
        ('POST /api/settings', ':80', 'quality, stream fps, mute'),
        ('POST /api/alert-test', ':80', 'play one warning - the speaker self-test'),
    ]
    for i, (route, port, what) in enumerate(routes):
        ry = ey + 40 + (i % 3) * 34
        rx = 580 + (i // 3) * 520
        c.text(rx, ry, route, size=13, mono=True, bold=True)
        c.text(rx + 190, ry, port, size=12, mono=True, fill=(41, 128, 185))
        c.text(rx + 236, ry + 1, what, size=11, fill=MUTED)
    return c.save(OUT / '05-web-preview.png')


def fig_amp_wiring():
    def extra(c, y):
        c.text(150, y, 'Speaker output - screw terminal, no polarity risk to the amplifier',
               size=15, bold=True)
        y += 32
        c.rect(150, y, 620, 150, fill=PANEL, outline=HAIRLINE, width=1, radius=10)
        c.rect(190, y + 40, 120, 70, fill=PCB_GREEN_TERM, radius=4)
        c.text(250, y + 20, 'MAX98357A terminal', size=12, fill=MUTED, anchor='mm')
        c.text(250, y + 62, '+', size=18, bold=True, fill=(255, 255, 255), anchor='mm')
        c.text(250, y + 90, '-', size=18, bold=True, fill=(255, 255, 255), anchor='mm')
        draw_speaker(c, 590, y + 20, 100, 100)
        c.line([(310, y + 62), (450, y + 62), (450, y + 56), (588, y + 56)], fill=C_5V, width=4)
        c.line([(310, y + 90), (420, y + 90), (420, y + 84), (588, y + 84)], fill=C_GND, width=4)
        c.text(330, y + 122, 'Speaker 4 ohm 3 W (item 2554)', size=12, fill=MUTED)
        c.note(800, y, 680, [
            'The speaker is not polarity-critical.',
            'Swapping + and - only inverts the waveform. Never',
            'connect either speaker wire to GND: the output is',
            'a bridged (BTL) pair and both pins are driven.',
        ], kind='warn')
        return y + 180
    return _wiring_figure(
        '06-amplifier-wiring.png', 6,
        'Wiring the MAX98357A I2S amplifier  (5 wires + speaker)',
        'Three signal wires, power and ground. There is no MCLK: the MAX98357A recovers its own clock.',
        AUDIO_WIRING, 'MAX98357 I2S amp  (item 2724)', draw_amp_module,
        [r[0] for r in AUDIO_WIRING],
        [('info', [
            'VIN takes 2.5 V - 5.5 V. Use 5V for full loudness; 3V3 works but is much quieter.',
            'BCLK, LRC and DIN are driven at 3.3 V straight from the ESP32-S3. No level shifter is needed or wanted.',
        ]),
         ('info', [
             'Change these pins in one place only: AUDIO_PIN_* in firmware/esp32s3/main/board_audio.h.',
         ])],
        extra=extra, height=1180)


# --------------------------------------------------------------------------- #
# 07 - amplifier configuration pins
# --------------------------------------------------------------------------- #

def fig_amp_config():
    c = Canvas(1680, 900)
    c.title('Figure 7 - MAX98357A configuration pins: SD and GAIN',
            'Two pins that are normally left alone, and exactly what to do if the amplifier stays silent.')

    c.panel(60, 150, 780, 470, 'SD  -  shutdown AND channel select')
    rows = [('below 0.16 V', 'Shut down - no output at all', (192, 57, 43)),
            ('0.16 V to 0.77 V', '(Left + Right) / 2', (39, 174, 96)),
            ('0.77 V to 1.4 V', 'Right channel only', (39, 174, 96)),
            ('above 1.4 V', 'Left channel only', (39, 174, 96))]
    y = 200
    for volts, meaning, col in rows:
        c.rect(84, y, 730, 56, fill=(250, 251, 252), outline=HAIRLINE, width=1, radius=7)
        c.rect(84, y, 6, 56, fill=col, radius=2)
        c.text(106, y + 18, volts, size=15, bold=True, mono=True)
        c.text(360, y + 18, meaning, size=14, fill=(70, 76, 88))
        y += 66
    c.note(84, y + 6, 730, [
        'The firmware makes this setting irrelevant.',
        'board_audio.cpp writes the same sample into the left and right I2S',
        'slot, so any of the three non-shutdown modes sounds identical.',
        'If there is no sound at all, measure SD: below 0.16 V means the',
        'part is shut down. Fit 100 kilo-ohm from SD to VIN to force Left.',
    ], kind='ok')

    c.panel(880, 150, 740, 470, 'GAIN  -  output level')
    grows = [('100 k to GND', '15 dB'), ('direct to GND', '12 dB'),
             ('left floating', '9 dB  (default - leave it alone)'),
             ('direct to VDD', '6 dB'), ('100 k to VDD', '3 dB')]
    y = 200
    for conn, gain in grows:
        default = 'default' in gain
        c.rect(904, y, 690, 56, fill=(233, 248, 239) if default else (250, 251, 252),
               outline=(39, 174, 96) if default else HAIRLINE,
               width=2 if default else 1, radius=7)
        c.text(926, y + 18, conn, size=15, mono=True, bold=default)
        c.text(1240, y + 18, gain, size=14, bold=default,
               fill=(39, 174, 96) if default else (70, 76, 88))
        y += 66
    c.note(904, y + 6, 690, [
        'Leave GAIN unconnected.',
        '9 dB into a 4 ohm 3 W speaker is already loud in a car cabin.',
        'docs/VOICE_ALERT_HARDWARE.md requires the alert to be audible',
        'but not startling - a startled drowsy driver is a hazard.',
    ], kind='info')

    c.note(60, 660, 1560, [
        'Breakout boards differ in what they pull SD to, so treat the silkscreen as the truth and measure if in doubt.',
        'Adafruit-style boards fit 1 M ohm from SD to VIN, which lands at roughly 0.45 V on a 5 V supply: the (L+R)/2 mode.',
        'Source: MAX98357A/MAX98357B datasheet (Analog Devices) and the Adafruit MAX98357 breakout pinout page.',
    ], kind='info')
    return c.save(OUT / '07-amplifier-config.png')


# --------------------------------------------------------------------------- #
# 08 - power architecture
# --------------------------------------------------------------------------- #

def fig_power():
    c = Canvas(1680, 940)
    c.title('Figure 8 - Power architecture and common ground',
            'What feeds what, at which voltage, and the two mistakes that destroy parts.')

    c.chip(80, 190, 240, 96, 'USB-C host', sub='5 V, 500 mA - 3 A', fill=(52, 73, 94))
    c.chip(470, 190, 260, 96, 'Board 5V rail', sub='straight from USB VBUS', fill=C_5V)
    c.chip(470, 360, 260, 96, 'On-board LDO', sub='5 V -> 3.3 V', fill=C_3V3)
    c.chip(900, 120, 300, 84, 'ESP32-S3 module', sub='3.3 V logic', fill=PCB_BLACK)
    c.chip(900, 240, 300, 84, 'OV3660 camera', sub='3.3 V, via FPC', fill=PCB_BLACK)
    c.chip(900, 480, 300, 84, 'MAX98357A amp', sub='VIN = 5V preferred', fill=PCB_PURPLE)
    c.chip(1310, 480, 260, 84, 'Speaker 4 ohm', sub='3 W, BTL output', fill=(60, 60, 64))

    c.wire((320, 238), (470, 238), C_5V, width=5)
    c.wire((600, 286), (600, 360), C_5V, width=5, midx=600)
    c.wire((730, 402), (900, 162), C_3V3, label='3V3', width=5, midx=820)
    c.wire((730, 402), (900, 282), C_3V3, label='3V3', width=5, midx=850)
    c.wire((730, 238), (900, 522), C_5V, label='5V', width=5, midx=790)
    c.callout(1050, 204, 1310, 250, '+ the 2.4 GHz radio, on-die', size=14)
    c.text(1310, 274, 'Serving the preview is the largest', size=13, fill=MUTED)
    c.text(1310, 296, 'current draw this build has.', size=13, fill=MUTED)
    c.wire((1200, 522), (1310, 522), C_SIG[0], width=5)

    c.text(80, 640, 'Common ground', size=17, bold=True)
    c.line([(100, 690), (1560, 690)], fill=C_GND, width=6)
    for x in (200, 600, 1050, 1450):
        c.line([(x, 690), (x, 672)], fill=C_GND, width=4)
        c.circle(x, 690, 6, fill=C_GND)
    c.text(100, 706, 'Every GND on every module ties to this one net. Without it the I2S clock has no reference and the amplifier outputs noise or nothing.',
           size=13, fill=MUTED)

    c.note(80, 760, 740, [
        'Feed this from a real supply, not a laptop hub.',
        'The camera, the class-D amplifier and Wi-Fi transmit',
        'bursts peak together. A thin cable or a weak hub browns',
        'the board out mid-boot, which reads as a camera fault.',
    ], kind='danger')
    c.note(860, 760, 740, [
        'Do not power the amplifier from a second supply',
        'unless its ground is tied to the board ground. Two',
        'supplies without a shared ground is the classic way',
        'to blow an I2S input.',
    ], kind='danger')
    return c.save(OUT / '08-power-architecture.png')


# --------------------------------------------------------------------------- #
# 09 - breadboard layout
# --------------------------------------------------------------------------- #

def fig_breadboard():
    c = Canvas(1680, 900)
    c.title('Figure 9 - MB102 breadboard: rails, rows, and the one gotcha',
            '830 tie points. Understanding which holes are joined saves most beginner wiring faults.')
    bx, by, bw, bh = 60, 170, 1000, 420
    draw_breadboard(c, bx, by, bw, bh)
    c.callout(bx + bw - 40, by + bh * 0.09, 1110, by + 20,
              'Top + rail  - runs the full length', size=14, colour=C_5V)
    c.callout(bx + bw - 40, by + bh * 0.15, 1110, by + 60,
              'Top - rail  - runs the full length', size=14, colour=C_GND)
    c.callout(bx + bw * 0.5, by + bh * 0.50, 1110, by + 110,
              'Centre channel - the two halves are NOT joined', size=14)
    c.callout(bx + 120, by + bh * 0.30, 1110, by + 160,
              'Each column of 5 holes is one node', size=14)
    c.callout(bx + bw - 40, by + bh * 0.91, 1110, by + 210,
              'Bottom rails are separate from the top rails', size=14, colour=(192, 57, 43))

    c.note(1110, by + 250, 510, [
        'Bridge the rails yourself.',
        'On most MB102 boards the top and bottom',
        'power rails are not connected to each other.',
        'Run two jumpers if you use both.',
    ], kind='warn')

    c.note(60, 630, 1560, [
        'The ESP32-S3 board is wider than one breadboard.',
        'This dev board straddles the centre channel and typically leaves no free holes on one or both sides. Three options, in order of preference:',
        '1. Leave the ESP32-S3 board off the breadboard and run female-to-male dupont wires from its header to the breadboard rows.',
        '2. Use the breadboard only for the power rails and the two small modules.',
        '3. Split the breadboard into its two halves (they unclip) and bridge the gap - only if your unit has the interlocking sides.',
    ], kind='info')
    return c.save(OUT / '09-breadboard-layout.png')


# --------------------------------------------------------------------------- #
# 10 - complete wiring
# --------------------------------------------------------------------------- #

def fig_complete():
    """Seven connections. It was fifteen while the build had a panel."""
    ROW_H, ROW_TOP = 52, 172

    ax, aw = 120, 300
    ay = 200
    ah = ROW_TOP + ROW_H * (len(AUDIO_WIRING) - 1) + 44
    sx, sy, sw, sh = 120, ay + ah + 60, 300, 200
    esp_x, esp_y, esp_w = 760, 200, 380
    esp_h = ah

    c = Canvas(1780, int(sy + sh + 200))
    c.title('Figure 10 - Complete wiring, all 7 connections',
            'The definitive diagram. Five wires to the amplifier, two from the amplifier to the speaker. Nothing else is wired at all.')

    c.rect(esp_x, esp_y, esp_w, esp_h, fill=PANEL, outline=HAIRLINE, width=2, radius=12)
    c.text(esp_x + esp_w / 2, esp_y + 26, 'ESP32-S3-WROOM-1 N16R8', size=16, bold=True,
           anchor='mm')
    c.text(esp_x + esp_w / 2, esp_y + 50, 'item 2991  -  OV3660 on the FPC connector',
           size=12, fill=MUTED, anchor='mm')
    draw_esp32_board(c, esp_x + 120, esp_y + 74, 140, 150, labels=False)

    c.rect(ax, ay, aw, ah, fill=PANEL, outline=HAIRLINE, width=1, radius=10)
    c.text(ax + aw / 2, ay + 22, 'MAX98357A  (2724)', size=14, bold=True, anchor='mm')
    draw_amp_module(c, ax + 90, ay + 46, 120, 76)

    c.rect(sx, sy, sw, sh, fill=PANEL, outline=HAIRLINE, width=1, radius=10)
    c.text(sx + sw / 2, sy + 22, 'Speaker 4 ohm 3 W  (2554)', size=14, bold=True, anchor='mm')
    draw_speaker(c, sx + 90, sy + 56, 120, 120)

    y = ay + ROW_TOP
    for (mpin, _alt, epin, role, _p) in AUDIO_WIRING:
        colour = _wire_colour(role, c)
        a = c.pin(ax + aw - 22, y, mpin, side='left', colour=colour, size=12)
        b = c.pin(esp_x + 22, y, epin, side='right', colour=colour, size=12)
        c.wire(a, b, colour, label=f'{mpin}->{epin}', label_size=11)
        y += ROW_H

    # Amplifier screw terminal down to the speaker.
    term_y = ay + ah - 18
    c.line([(ax + 130, term_y), (ax + 130, sy - 2)], fill=C_5V, width=4)
    c.line([(ax + 170, term_y), (ax + 170, sy - 2)], fill=C_GND, width=4)
    c.text(ax + 200, term_y + 14, 'screw terminal -> speaker', size=12, fill=MUTED)
    c.text(ax + 200, term_y + 34, 'BTL pair, not polarity critical', size=11,
           fill=(41, 128, 185))

    # The preview replaces the eight wires that used to be in this figure, so it
    # gets the space they occupied rather than being a footnote.
    px, py, pw, ph = 1240, esp_y, 420, 300
    c.rect(px, py, pw, ph, fill=PANEL, outline=HAIRLINE, width=1, radius=10)
    c.text(px + pw / 2, py + 22, 'Live preview  -  0 wires', size=14, bold=True,
           anchor='mm')
    draw_phone(c, px + pw / 2 - 70, py + 50, 140, 230)

    lx = 1240
    c.legend(lx, py + ph + 30, [
        (C_GND, 'GND  - common ground, connect first'),
        (C_5V, '5V   - amplifier supply'),
        ((120, 126, 138), 'signal wires - one colour each'),
    ])
    c.note(lx, py + ph + 130, 420, [
        'Count before powering up.',
        '5 wires to the amplifier and 2 from the',
        'amplifier to the speaker: 7 total. The camera',
        'adds none - it is the FPC ribbon - and the',
        'preview adds none: it goes over Wi-Fi.',
    ], kind='ok')
    c.note(120, sy + sh + 40, 1040, [
        'Connect GND first, disconnect it last.',
        'That order means no module is ever powered through a signal pin,',
        'which is what quietly damages I2S inputs.',
    ], kind='warn')
    c.note(1240, sy + sh + 40, 420, [
        'No level shifter anywhere.',
        'The ESP32-S3 drives 3.3 V logic and the',
        'MAX98357A accepts it while its VIN runs at 5 V.',
    ], kind='info')
    c.note(120, sy + sh + 140, 1540, [
        'Every pin string in this diagram is generated from firmware/esp32s3/main/board_audio.h by scripts/generate_tutorial_diagrams.py,',
        'and tests/test_tutorial_diagrams.py fails if the drawing and the firmware header ever disagree.',
    ], kind='info')
    return c.save(OUT / '10-complete-wiring.png')


# --------------------------------------------------------------------------- #
# 11 - first power-on
# --------------------------------------------------------------------------- #

def fig_first_boot():
    c = Canvas(1680, 940)
    c.title('Figure 11 - What a correct first power-on looks like',
            'Two things to check, and they fail in different ways: the serial monitor, and the page on your phone. Neither needs the models to be bound.')

    c.panel(80, 160, 400, 620, 'On your phone')
    draw_phone(c, 160, 220, 240, 480)
    c.text(280, 716, 'http://192.168.4.1/', size=14, mono=True, bold=True, anchor='ma')
    c.text(280, 744, 'after joining DrowsyGuard-XXXXXX', size=12, fill=MUTED, anchor='ma')

    tx, ty, tw, th = 520, 160, 1100, 440
    c.rect(tx, ty, tw, th, fill=(24, 26, 32), radius=10)
    c.text(tx + 20, ty + 16, 'idf.py -p COM5 flash monitor', size=13, mono=True,
           fill=(140, 200, 255))
    log = [
        ('I (312) esp_psram: Found 8MB PSRAM device', (120, 230, 150)),
        ('I (318) esp_psram: Adding pool of 8192K of PSRAM memory to heap', (120, 230, 150)),
        ('I (402) wifi: SoftAP "DrowsyGuard-A1B2C3" up on channel 6, WPA2', (120, 230, 150)),
        ('I (404) wifi: join it, then open http://192.168.4.1/', (120, 230, 150)),
        ('I (452) audio: I2S up: BCLK=39 LRCLK=38 DIN=40 @ 16000 Hz 16-bit stereo', (120, 230, 150)),
        ('I (460) voice_alert: Alert controller initialized; language=en '
         'output=I2S/MAX98357A', (120, 230, 150)),
        ('I (486) web: preview at http://192.168.4.1/  (stream on port 81)', (120, 230, 150)),
        ('I (690) drowsyguard: camera up: 240x240 RGB565, PSRAM free 8123456 B', (120, 230, 150)),
        ('W (700) model_adapter: eye model not bound - alerting disabled', (250, 210, 100)),
        ('I (1700) drowsyguard: fps 15.2  risk 0.00  perclos 0.00  viewers 1 ...',
         (220, 226, 235)),
    ]
    for i, (line, col) in enumerate(log):
        c.text(tx + 20, ty + 52 + i * 38, line[:96], size=12, mono=True, fill=col)

    c.note(520, 630, 540, [
        'You should HEAR three rising notes at boot.',
        'That is the chime in main.cpp. It proves the amplifier,',
        'the I2S pins and the speaker in one step. With no panel',
        'it is also the only local sign the board came up at all.',
    ], kind='ok')
    c.note(1080, 630, 540, [
        'If PSRAM reports 2 MB or nothing, stop.',
        'Fix CONFIG_SPIRAM_MODE_OCT before anything else. An R8',
        'board in quad mode reports 0 B, and the failure surfaces',
        'later as an unexplained camera error.',
    ], kind='danger')
    c.note(80, 800, 1540, [
        'No SSID in the phone\'s Wi-Fi list? The radio never started - look for the wifi: lines above, and check that PSRAM came up first.',
        'SSID visible but the page will not load? You are on the right network but the wrong address: it is 192.168.4.1, not a .local name.',
        'Page loads but the preview stays dark? That is the camera, not the network - the pills under the preview say which subsystem failed.',
    ], kind='info')
    return c.save(OUT / '11-first-power-on.png')


FIGURES = [fig_components, fig_pin_map, fig_usb_ports, fig_camera_ribbon,
           fig_web_preview, fig_amp_wiring, fig_amp_config, fig_power,
           fig_breadboard, fig_complete, fig_first_boot]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for fn in FIGURES:
        path = fn()
        print(f'wrote {path.relative_to(Path(__file__).resolve().parents[1])} '
              f'({path.stat().st_size // 1024} KB)')


if __name__ == '__main__':
    main()
