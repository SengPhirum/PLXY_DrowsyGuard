#!/usr/bin/env python3
"""Generate the single-sheet DrowsyGuard wiring poster.

One page, numbered panels, in build order - the format that is easiest to prop up
next to the bench. Every GPIO number on it is read from the firmware headers by
scripts/pinmap.py, so the poster cannot drift away from what the board is actually
told to drive, and tests/test_tutorial_diagrams.py fails if it does.

Run:  python scripts/generate_wiring_poster.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagram_kit import (BLACK_W, BLUE, Breadboard, Canvas, GOLD, GREEN, HAIRLINE,
                         INK, MUTED, NAVY, PANEL, PCB_BLACK, PCB_PURPLE, PURPLE,
                         RED, SCREEN, TERM_GREEN)
from pinmap import AMP_WIRING, FREE_GPIOS, load_pins

OUT = Path(__file__).resolve().parents[1] / 'docs/tutorials/hardware-setup/images'


# --------------------------------------------------------------------------- #
# Module footprints, drawn as they sit on the breadboard (top-down)
# --------------------------------------------------------------------------- #

def esp32_on_board(c, bb, row_a, row_b, label=True):
    """The ESP32-S3 CAM board straddling the trench from row_a to row_b."""
    x0 = bb.hole('B', row_a)[0] - bb.p * 0.5
    x1 = bb.hole('B', row_b)[0] + bb.p * 0.5
    y0 = bb.hole('B', row_a)[1] - bb.p * 0.5
    y1 = bb.hole('I', row_a)[1] + bb.p * 0.5
    c.rect(x0, y0, x1 - x0, y1 - y0, fill=PCB_BLACK, radius=3)
    w, h = x1 - x0, y1 - y0
    # WROOM module can, camera, FPC socket, two USB-C on the short edge
    c.rect(x0 + w * 0.30, y0 + h * 0.16, w * 0.30, h * 0.66, fill=(196, 200, 206),
           radius=2)
    c.circle(x0 + w * 0.70, y0 + h * 0.5, min(w, h) * 0.13, fill=(24, 24, 28),
             outline=(90, 90, 96), width=1)
    c.rect(x0 + w * 0.63, y0 + h * 0.80, w * 0.16, h * 0.12, fill=(48, 44, 40), radius=1)
    for f in (0.28, 0.62):
        c.rect(x0 - bb.p * 0.5, y0 + h * f, bb.p * 0.6, h * 0.16,
               fill=(180, 186, 194), radius=1)
    if label and w > 90:
        c.text(x0 + w * 0.45, y1 - 9, 'ESP32-S3', size=max(7, int(bb.p * 0.8)),
               bold=True, fill=(170, 176, 186), anchor='mm')
    return (x0, y0, w, h)


def single_row_module(c, bb, row_a, row_b, fill, body_rows=4.6, col='J'):
    """A module whose header is a SINGLE row of pins.

    The amplifier has one 7-pin strip, so unlike the controller it does not
    straddle the trench: the pins go into one column line and the body overhangs
    the edge of the board. Mounting it on the outer column (J here) is what keeps
    the other four holes of each row reachable for a jumper - put it anywhere
    inland and the body covers its own wiring holes.
    """
    x0 = bb.hole(col, row_a)[0] - bb.p * 0.6
    x1 = bb.hole(col, row_b)[0] + bb.p * 0.6
    hy = bb.hole(col, row_a)[1]
    bh = bb.p * body_rows
    y0 = hy + bb.p * 0.6 if col == 'J' else hy - bb.p * 0.6 - bh
    c.rect(x0, y0, x1 - x0, bh, fill=fill, radius=3)
    for row in range(row_a, row_b + 1):
        hx, hyy = bb.hole(col, row)
        c.rect(hx - bb.p * 0.22, hyy - bb.p * 0.22, bb.p * 0.44, bb.p * 0.44,
               fill=(212, 176, 90), radius=1)
    return (x0, y0, x1 - x0, bh)


def amp_on_board(c, bb, row_a, row_b, col='J'):
    x0, y0, w, h = single_row_module(c, bb, row_a, row_b, PCB_PURPLE, 4.2, col)
    c.rect(x0 + w * 0.34, y0 + h * 0.26, w * 0.34, h * 0.34, fill=(32, 34, 40), radius=2)
    c.rect(x0 + w * 0.05, y0 + h * 0.62, w * 0.34, h * 0.30, fill=TERM_GREEN, radius=2)
    return (x0, y0, w, h)


def speaker(c, cx, cy, r):
    c.circle(cx, cy, r, fill=(46, 48, 52), outline=(140, 144, 150), width=2)
    c.circle(cx, cy, r * 0.66, fill=(206, 208, 212))
    c.circle(cx, cy, r * 0.30, fill=(150, 154, 160))


# --------------------------------------------------------------------------- #
# Panel helpers
# --------------------------------------------------------------------------- #

def panel(c, x, y, w, h, num, title, accent=NAVY):
    c.rect(x, y, w, h, fill=PANEL, outline=HAIRLINE, width=2, radius=10)
    bh = 34
    tw = c.text_w(title, size=17, bold=True) + 76
    c.rect(x + 12, y - bh / 2, min(tw, w - 24), bh, fill=accent, radius=7)
    c.text(x + 30, y + 1, num, size=17, bold=True, fill=GOLD, anchor='lm')
    c.text(x + 30 + c.text_w(num, size=17, bold=True) + 12, y + 1, title,
           size=17, bold=True, fill=(255, 255, 255), anchor='lm')


def pin_table(c, x, y, rows, w=330, title_l='ESP32-S3 pin', title_r='Module pin',
              size=13, row_h=25, notes=True):
    c.text(x, y, title_l, size=size, bold=True, fill=MUTED)
    c.text(x + w * 0.44, y, title_r, size=size, bold=True, fill=MUTED)
    if notes:
        c.text(x + w * 0.78, y, 'why', size=size, bold=True, fill=MUTED)
    yy = y + 24
    for left, right, colour, note in rows:
        c.text(x, yy, left, size=size + 1, mono=True, bold=True, fill=colour)
        c.text(x + w * 0.33, yy + 1, '→', size=size + 1, bold=True, fill=(140, 146, 158))
        c.text(x + w * 0.44, yy, right, size=size + 1, mono=True, bold=True, fill=colour)
        if notes and note:
            c.text(x + w * 0.78, yy, note, size=size - 2, fill=MUTED)
        yy += row_h
    return yy - y


def _rows(wiring):
    """Turn a pinmap wiring table into pin_table rows with rail colours."""
    out = []
    for w in wiring:
        colour = {'gnd': BLACK_W, '3v3': BLUE, '5v': RED}.get(w.role, PURPLE)
        out.append((w.esp, w.module, colour, w.note))
    return out


# --------------------------------------------------------------------------- #
# The poster
# --------------------------------------------------------------------------- #

def build():
    pins = load_pins()
    W, H = 1700, 2520
    c = Canvas(W, H)

    # ---- header ---------------------------------------------------------- #
    c.rect(0, 0, W, 74, fill=NAVY)
    c.text(W / 2, 26, 'DROWSYGUARD', size=34, bold=True, fill=(255, 255, 255),
           anchor='mm')
    c.text(W / 2, 56, 'STEP-BY-STEP WIRING POSTER', size=20, bold=True, fill=GOLD,
           anchor='mm')
    c.rect(0, 74, W, 40, fill=(232, 238, 250))
    sub = ('ESP32-S3-WROOM-1 N16R8 + camera   •   MAX98357A I2S amp   •   '
           '4 Ω 3 W speaker   •   microSD history   •   preview over Wi-Fi')
    c.text(W / 2, 94, sub, size=15, bold=True, fill=NAVY, anchor='mm')
    c.rect(0, 114, W, 38, fill=(255, 244, 224))
    c.text(W / 2, 133, 'IMPORTANT — unplug USB before changing any wire. '
                       'Connect GND first, remove it last.',
           size=15, bold=True, fill=(168, 84, 8), anchor='mm')

    # ---- 0: components --------------------------------------------------- #
    panel(c, 40, 190, 800, 250, '0', 'COMPONENTS')
    items = [
        ('ESP32-S3 CAM', 'item 2991', PCB_BLACK),
        ('MAX98357A', 'item 2724', PCB_PURPLE),
        ('4 Ω 3 W spk', 'item 2554', (52, 54, 58)),
        ('MB102 board', 'item 371', (214, 208, 192)),
    ]
    for i, (name, code, col) in enumerate(items):
        cx = 130 + i * 180
        c.rect(cx, 235, 120, 80, fill=col, radius=6)
        c.text(cx + 60, 330, name, size=13, bold=True, anchor='mm')
        c.text(cx + 60, 352, code, size=11, mono=True, fill=MUTED, anchor='mm')
    c.text(60, 390, 'You have TWO speakers. The MAX98357A is MONO — it drives ONE.',
           size=13, bold=True, fill=(192, 57, 43))
    c.text(60, 412, 'If you must use both, wire them in SERIES (8 Ω). Never in '
                    'parallel (2 Ω over-currents the amp).', size=12, fill=MUTED)

    # ---- 1: prepare breadboard ------------------------------------------- #
    panel(c, 870, 190, 790, 250, '1', 'PREPARE THE BREADBOARD')
    bb1 = Breadboard(c, 895, 232, pitch=6.6, orient='landscape').draw(label_every=10)
    ty = 232 + bb1.h + 16
    for i, t in enumerate([
            'Outer red line = + rail.   Inner blue line = − rail.',
            'A–E and F–J are separate: the trench splits every row.',
            'Each column of 5 holes is one node. 63 rows, 830 tie points.']):
        c.text(900, ty + i * 22, t, size=13, fill=(58, 64, 76))

    # ---- 2: power rails -------------------------------------------------- #
    panel(c, 40, 490, 800, 250, '2', 'SET UP THE POWER RAILS')
    bb2 = Breadboard(c, 70, 532, pitch=6.6, orient='landscape').draw(label_every=10)
    bb2.wire(bb2.rail('T', '+', 4), bb2.rail('B', '+', 4), RED, width=3)
    bb2.wire(bb2.rail('T', '-', 60), bb2.rail('B', '-', 60), BLACK_W, width=3)
    ty = 532 + bb2.h + 16
    c.text(70, ty, 'Bridge top↔bottom rails only if yours are split at the middle.',
           size=13, fill=(58, 64, 76))
    c.text(70, ty + 22, 'Top + rail → 5 V.   Top − rail → GND.   '
                        'Bottom + rail → 3.3 V.', size=13, bold=True, fill=NAVY)
    c.text(70, ty + 44, '3.3 V and 5 V get different rails on purpose: it makes '
                        'them hard to confuse.', size=12, fill=MUTED)

    # ---- 3: insert the ESP32 --------------------------------------------- #
    panel(c, 870, 490, 790, 250, '3', 'INSERT THE ESP32-S3 CAM')
    bb3 = Breadboard(c, 895, 532, pitch=6.6, orient='landscape').draw(label_every=10)
    esp32_on_board(c, bb3, 6, 27)
    ty = 532 + bb3.h + 16
    c.text(900, ty, 'Straddle the trench: one header in row B, the other in row I.',
           size=13, fill=(58, 64, 76))
    c.text(900, ty + 22, 'USB-C sockets face OUT past the end of the board so a '
                         'cable can reach.', size=13, fill=(58, 64, 76))
    c.text(900, ty + 44, 'Press evenly. Leave the camera ribbon for the next step.',
           size=12, fill=MUTED)

    # ---- 4: camera + power to the board ---------------------------------- #
    panel(c, 40, 790, 800, 250, '4', 'CAMERA RIBBON, THEN BOARD POWER')
    c.text(70, 832, 'Camera', size=15, bold=True, fill=NAVY)
    for i, t in enumerate([
            '1. Flip the black latch on the FPC socket UP.',
            '2. Slide the ribbon in with contacts facing DOWN.',
            '3. Press the latch flat. It must not pull out.']):
        c.text(70, 858 + i * 22, t, size=13, fill=(58, 64, 76))
    c.text(70, 934, 'A half-seated ribbon reports esp_camera_init failed: 0x105.',
           size=12, fill=(192, 57, 43))
    c.text(470, 832, 'Board → rails', size=15, bold=True, fill=NAVY)
    pin_table(c, 470, 858, [
        ('5V', '+ rail (5 V)', RED, ''),
        ('GND', '− rail (GND)', BLACK_W, ''),
        ('3V3', '+ rail (3.3 V)', BLUE, ''),
    ], w=330, title_l='Board pin', title_r='Breadboard rail', notes=False)
    c.text(470, 970, 'Find these three by their printed label on the header.',
           size=12, fill=MUTED)

    # ---- 5: add the display ---------------------------------------------- #
    panel(c, 870, 790, 790, 250, '5', 'PLACE THE AMPLIFIER')
    bb5 = Breadboard(c, 895, 832, pitch=6.6, orient='landscape').draw(label_every=10)
    esp32_on_board(c, bb5, 6, 27, label=False)
    amp_on_board(c, bb5, 40, 46)          # 7 pins
    ty = 832 + bb5.h + 16
    c.text(900, ty, 'The amplifier has a SINGLE row of pins, so it does not straddle '
                    'the trench.', size=13, bold=True, fill=NAVY)
    c.text(900, ty + 22, 'Mount it on the outer column J with the body overhanging '
                         'the edge — that keeps', size=13, fill=(58, 64, 76))
    c.text(900, ty + 42, 'F–I of each row free to plug a jumper into. Inland, the '
                         'body covers its own holes.', size=13, fill=(58, 64, 76))
    c.text(900, ty + 64, 'Solder its header strip first — it ships loose.',
           size=12, fill=(168, 84, 8))

    # ---- 6: the preview, which used to be eight wires to a panel --------- #
    panel(c, 40, 1090, 1620, 330, '6', 'SEE THE CAMERA  —  JOIN THE BOARD\'S WI-FI, '
                                       '0 WIRES')
    c.text(80, 1140, 'There is no screen to wire', size=14, bold=True, fill=NAVY)
    for i, t in enumerate([
            'The ESP32-S3 serves the live preview itself, over its',
            'own access point. Any phone or laptop browser is the',
            'display, and it shows far more than a 1.8" panel could:',
            'risk, PERCLOS, event rates, the face box, frame timing.',
            '',
            'This is what freed five GPIOs and eight wires.']):
        c.text(80, 1168 + i * 21, t, size=13, fill=(58, 64, 76),
               bold=t.startswith('This is'))
    c.rect(520, 1130, 2, 250, fill=HAIRLINE)

    c.text(570, 1140, 'Three steps', size=14, bold=True, fill=NAVY)
    for i, t in enumerate([
            '1.  Power the board. Listen for the boot chime.',
            '2.  Join Wi-Fi   DrowsyGuard-XXXXXX',
            '        password  drowsyguard',
            '3.  Open        http://192.168.4.1/',
            '',
            'Your phone will say the network has no internet.',
            'It has not. The board IS the network.',
            '',
            'SSID and password: main/board_wifi.h.']):
        c.text(570, 1168 + i * 21, t, size=13, mono=t.startswith(('1.', '2.', '3.', '   ')),
               fill=(58, 64, 76))

    c.text(1150, 1140, 'Never use these GPIOs', size=14, bold=True, fill=(192, 57, 43))
    for i, t in enumerate([
            'GPIO 33–37   SPI flash + octal PSRAM — hangs the board',
            'GPIO 43, 44  UART0 console — idf.py monitor needs them',
            'GPIO 19, 20  native USB D−/D+',
            'GPIO 0, 45, 46  strapping pins',
            'GPIO 48      on-board RGB LED',
            'GPIO 2       buzzer fallback output',
            'GPIO 38-40   microSD slot - SDMMC bus, fixed by the PCB',
            '',
            f"Spare after this build: GPIO "
            f"{', '.join(str(g) for g in FREE_GPIOS)}.",
            'The amp sits on 14/21/47 because the card owns 38-40.']):
        c.text(1150, 1168 + i * 21, t, size=12, mono=('GPIO' in t),
               fill=(58, 64, 76) if 'Spare' not in t else NAVY,
               bold='Spare' in t)

    # ---- 7: amplifier ---------------------------------------------------- #
    panel(c, 40, 1470, 800, 340, '7', 'WIRE THE AMPLIFIER  —  I2S, 5 WIRES')
    pin_table(c, 80, 1520, _rows(AMP_WIRING), w=400)
    c.note(80, 1670, 720, [
        'Leave SD and GAIN unconnected.',
        'SD below 0.16 V SHUTS THE AMPLIFIER DOWN — grounding it is the OFF switch,',
        'not an "always on" setting. GAIN floating gives 9 dB, which is already loud.',
    ], kind='danger')

    # ---- 8: speaker ------------------------------------------------------ #
    panel(c, 870, 1470, 790, 340, '8', 'CONNECT ONE SPEAKER')
    c.rect(930, 1540, 130, 90, fill=TERM_GREEN, radius=6)
    c.text(995, 1522, 'screw terminal', size=12, fill=MUTED, anchor='mm')
    c.text(995, 1568, '+', size=22, bold=True, fill=(255, 255, 255), anchor='mm')
    c.text(995, 1604, '−', size=22, bold=True, fill=(255, 255, 255), anchor='mm')
    speaker(c, 1290, 1585, 62)
    c.line([(1060, 1568), (1180, 1568), (1180, 1560), (1232, 1560)], fill=RED, width=4)
    c.line([(1060, 1604), (1150, 1604), (1150, 1610), (1232, 1610)],
           fill=BLACK_W, width=4)
    c.text(1090, 1660, 'Strip 5 mm, twist, insert, tighten.', size=13, fill=(58, 64, 76))
    c.note(930, 1690, 700, [
        'Polarity does not matter; grounding a speaker lead does.',
        'Swapping + and − only inverts the waveform. But the output is a bridged (BTL)',
        'pair — both terminals are driven, so neither may go to GND.',
    ], kind='warn')

    # ---- 9: final overview ----------------------------------------------- #
    panel(c, 40, 1860, 1620, 420, '9', 'FINAL CONNECTION OVERVIEW  —  7 WIRES')
    bb9 = Breadboard(c, 80, 1910, pitch=10.2, orient='landscape').draw(label_every=5)
    esp32_on_board(c, bb9, 6, 27)
    amp_on_board(c, bb9, 40, 46)

    # The controller's right-hand header sits in column I, so its taps are column
    # J; the amplifier sits in J, so its taps are column F.
    for i, wire in enumerate(AMP_WIRING):
        colour = {'gnd': BLACK_W, '3v3': BLUE, '5v': RED}.get(wire.role, PURPLE)
        bb9.wire(bb9.hole('J', 20 + i), bb9.hole('F', 40 + i), colour, width=2)
    spk_x, spk_y = 1500, 2010
    speaker(c, spk_x, spk_y, 60)
    term = bb9.hole('J', 44)
    c.line([(term[0], term[1] + 30), (spk_x - 90, term[1] + 30),
            (spk_x - 90, spk_y - 18), (spk_x - 52, spk_y - 18)], fill=RED, width=3)
    c.line([(term[0] + 20, term[1] + 30), (spk_x - 70, term[1] + 30),
            (spk_x - 70, spk_y + 18), (spk_x - 52, spk_y + 18)], fill=BLACK_W, width=3)
    ty = 1910 + bb9.h + 20
    c.text(80, ty, '5 wires to the amplifier   +   2 to the speaker   =   7.   '
                   'The camera adds none — it is the FPC ribbon — and the preview '
                   'adds none: it goes over Wi-Fi.', size=14, bold=True, fill=NAVY)
    c.swatch_row(80, ty + 28, RED, '5 V — amplifier VIN only')
    c.swatch_row(400, ty + 28, BLACK_W, 'GND — every module, one net')
    c.swatch_row(740, ty + 28, PURPLE, 'signals — I2S BCLK, LRC, DIN')

    # ---- 10: power and test ---------------------------------------------- #
    panel(c, 40, 2330, 1620, 150, '10', 'POWER UP AND TEST')
    for i, t in enumerate([
            '1.  Re-check VIN→5V and every GND. Those are what destroy parts.',
            '2.  Plug USB-C into the UART port. Watch and smell for five seconds.',
            '3.  idf.py -p COM5 flash monitor']):
        c.text(80, 2372 + i * 24, t, size=14, fill=(58, 64, 76),
               mono=t.strip().startswith('3.  idf'))
    for i, t in enumerate([
            '4.  Expect: "Found 8MB PSRAM device" in the log.',
            '5.  Expect: THREE rising notes — the boot chime.',
            '6.  Expect: SSID DrowsyGuard-XXXXXX, then a live preview at '
            '192.168.4.1.']):
        c.text(880, 2372 + i * 24, t, size=14, fill=(58, 64, 76))

    c.rect(0, H - 34, W, 34, fill=NAVY)
    c.text(W / 2, H - 17, 'Power from a 5 V 2 A USB-C supply. Every GPIO on this '
                          'sheet is generated from the firmware headers.',
           size=13, bold=True, fill=(220, 228, 245), anchor='mm')
    return c.save(OUT / 'wiring-poster.png')


if __name__ == '__main__':
    p = build()
    root = Path(__file__).resolve().parents[1]
    print(f'wrote {p.relative_to(root)} ({p.stat().st_size // 1024} KB)')
