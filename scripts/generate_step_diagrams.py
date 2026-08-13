#!/usr/bin/env python3
"""Generate the step-by-step assembly diagrams for the hardware tutorial.

One image per physical action, in the order the reader performs them. Each image
carries a title banner, the actual MB102 breadboard drawn to scale with real hole
coordinates, callout boxes with leader lines to the exact hole or pin they discuss,
a legend, and a check list of what to verify before moving on.

Run:  python scripts/generate_step_diagrams.py

Pin positions
-------------
Steps that place the ESP32-S3 board on the breadboard need that board's physical
header order (which silkscreen label sits at which position down each strip).
That order varies between production batches of this board and is NOT guessed
here - see ESP32_HEADER below. Steps that do not depend on it are generated
regardless, so the sequence is usable while that is being confirmed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagram_kit import (BLUE, Breadboard, Canvas, GREEN, INK, MUTED, NAVY, PANEL,
                         PCB_BLUE, PCB_PURPLE, PURPLE, RED, SCREEN, TERM_GREEN,
                         BLACK_W, HAIRLINE)

OUT = Path(__file__).resolve().parents[1] / 'docs/tutorials/hardware-setup/images/steps'

# Where the breadboard sits, and the two vertical channels the leader lines run in
# so they pass beside the board rather than across the holes.
BB_X = 660
CH_L = BB_X - 34
CH_R = BB_X + 336 + 34

# --------------------------------------------------------------------------- #
# Hardware facts
# --------------------------------------------------------------------------- #

# Physical top-to-bottom order of the ESP32-S3 board's two header strips.
# UNKNOWN until read off the actual board - see the module docstring. Fill this
# in as ['3V3', '5V', 'GND', 'GPIO4', ...] per side and the placement steps
# become exact hole coordinates instead of label-keyed callouts.
ESP32_HEADER = {'left': None, 'right': None}

# Silkscreen order on the 1.8" ST7735S module, read off the owner's photograph
# of the actual part: "GND VDD SCL SDA RST DC CS BLK", left to right.
DISPLAY_PINS = ['GND', 'VDD', 'SCL', 'SDA', 'RST', 'DC', 'CS', 'BLK']

# MAX98357A breakout, seven pins plus the two-way screw terminal.
AMP_PINS = ['LRC', 'BCLK', 'DIN', 'GAIN', 'SD', 'GND', 'VIN']

WIRE_LEGEND = [
    (RED, 'Red = 5 V'),
    (BLUE, 'Blue = 3.3 V'),
    (BLACK_W, 'Black = GND'),
]


def _legend(c: Canvas, x, y, w, rows, extra=None):
    h = c.panel_box(x, y, w, 'LEGEND', [''] * len(rows), accent=NAVY, row_h=30)
    ry = y + 46
    for colour, label in rows:
        c.swatch_row(x + 16, ry + 4, colour, label)
        ry += 30
    return h


# --------------------------------------------------------------------------- #
# Step 01 - identify the breadboard
# --------------------------------------------------------------------------- #

def step_breadboard_anatomy():
    c = Canvas(1680, 1320)
    c.banner('STEP 1', 'KNOW YOUR BREADBOARD BEFORE YOU PLUG ANYTHING IN')

    bb = Breadboard(c, BB_X, 120, pitch=16).draw()

    c.callout(60, 150, 400,
              'Two power rails per edge',
              ['The outer line marked + is one continuous node running the whole '
               'length of the board. The inner line marked - is a separate one. '
               'Four rails in total, two per edge.'],
              accent=RED, target=bb.rail('L', '+', 8), via_x=CH_L)

    c.callout(60, 360, 400,
              'Each column of 5 holes is ONE node',
              ['Holes A20 B20 C20 D20 E20 are electrically the same point. Anything '
               'plugged into one of them is connected to everything in the other four.'],
              accent=BLUE, target=bb.hole('A', 20), via_x=CH_L)

    c.callout(60, 570, 400,
              'The centre trench splits every row',
              ['E32 and F32 are NOT connected to each other. This is what lets a '
               'module straddle the middle with its two sides independent.'],
              accent=PURPLE, target=bb.hole('E', 32), via_x=CH_L)

    c.callout(1150, 150, 440,
              '63 rows, 830 tie points',
              ['63 rows x 10 holes = 630 in the main field, plus four power rails of '
               '50 holes each = 200. That is the 830 in the product name.'],
              accent=NAVY, target=bb.hole('J', 6), via_x=CH_R, anchor_side='left')

    c.callout(1150, 360, 440,
              'Some units split the rails in the middle',
              ['Look for a break in the red and blue lines near row 30. If your board '
               'has one, each rail is two separate nodes and needs bridging in Step 3.'],
              accent=(211, 84, 0), target=bb.rail('R', '-', 32), via_x=CH_R,
              anchor_side='left')

    c.note(1150, 540, 440, [
        'Test before you trust it.',
        'A cheap breadboard can have a dead row. If something',
        'refuses to work later, move it two rows and retry',
        'before you suspect the component.',
    ], kind='warn')

    _legend(c, 60, 720, 400, [
        (RED, '+ rail  (will carry 5 V)'),
        (BLUE, '- rail  (will carry GND)'),
        ((120, 126, 138), 'A-J  column letters'),
    ])

    c.panel_box(1150, 720, 440, 'CHECK', [
        'Board is the right way up: row 1 at the top',
        'Column letters A-E and F-J both readable',
        'You can see the trench between E and F',
        'Rails run the full length, or you found the break',
    ], accent=GREEN, check=True)

    c.note(60, 900, 400, [
        'Nothing is powered yet.',
        'This step is orientation only. Keep the USB',
        'cable unplugged until Step 8.',
    ], kind='info')

    return c.save(OUT / '01-breadboard-anatomy.png')


# --------------------------------------------------------------------------- #
# Step 02 - solder the headers
# --------------------------------------------------------------------------- #

def _draw_display(c, x, y, w, h, pins=True):
    c.rect(x, y, w, h, fill=PCB_BLUE, radius=5)
    c.rect(x + w * 0.06, y + h * 0.16, w * 0.88, h * 0.66, fill=SCREEN, radius=2)
    step = w * 0.104
    for i, name in enumerate(DISPLAY_PINS):
        px = x + w * 0.105 + i * step
        c.rect(px - 4, y + 5, 8, 8, fill=(212, 176, 90), radius=1)
        if pins:
            c.text(px, y - 8, name, size=11, bold=True, mono=True,
                   fill=(40, 46, 58), anchor='mm')
    c.text(x + w / 2, y + h - 12, "1.8'128X160 RGB_TFT", size=10,
           fill=(200, 214, 240), anchor='mm')


def _draw_amp(c, x, y, w, h, pins=True):
    c.rect(x, y, w, h, fill=PCB_PURPLE, radius=5)
    c.rect(x + w * 0.40, y + h * 0.34, w * 0.26, h * 0.28, fill=(32, 34, 40), radius=2)
    c.rect(x + w * 0.05, y + h * 0.62, w * 0.32, h * 0.30, fill=TERM_GREEN, radius=3)
    step = w * 0.125
    for i, name in enumerate(AMP_PINS):
        px = x + w * 0.11 + i * step
        c.rect(px - 4, y + 5, 8, 8, fill=(212, 176, 90), radius=1)
        if pins:
            c.text(px, y - 8, name, size=11, bold=True, mono=True,
                   fill=(40, 46, 58), anchor='mm')


def step_solder_headers():
    c = Canvas(1680, 1180)
    c.banner('STEP 2', 'SOLDER THE HEADER STRIPS TO THE TWO SMALL MODULES')

    c.note(60, 110, 1560, [
        'Both modules ship with their header strips loose in the bag. Until they are soldered nothing can make reliable contact,',
        'so this is the one step that needs a tool that was not in the order: a soldering iron and solder.',
    ], kind='warn')

    # display
    c.rect(90, 250, 700, 420, fill=PANEL, outline=HAIRLINE, width=2, radius=12)
    c.text(440, 278, '1.8" ST7735S display  -  8-pin strip', size=18, bold=True,
           anchor='mm')
    _draw_display(c, 250, 360, 380, 230)
    c.text(440, 620, 'Silkscreen order, left to right', size=13, fill=MUTED, anchor='mm')
    c.text(440, 642, ' '.join(DISPLAY_PINS), size=14, mono=True, bold=True,
           fill=INK, anchor='mm')

    # amplifier
    c.rect(870, 250, 720, 420, fill=PANEL, outline=HAIRLINE, width=2, radius=12)
    c.text(1230, 278, 'MAX98357A amplifier  -  7-pin strip', size=18, bold=True,
           anchor='mm')
    _draw_amp(c, 1040, 360, 380, 230)
    c.text(1230, 620, 'Silkscreen order, left to right', size=13, fill=MUTED, anchor='mm')
    c.text(1230, 642, ' '.join(AMP_PINS), size=14, mono=True, bold=True,
           fill=INK, anchor='mm')

    c.callout(90, 710, 480,
              'Long pins DOWN, short pins into the board',
              ['Insert the strip from the component side so the long ends stick out',
               'underneath. Those are what go into the breadboard.'],
              accent=BLUE)

    c.callout(600, 710, 480,
              'Solder on the side opposite the parts',
              ['Push the strip flush, then solder from the back. A joint should be a',
               'small shiny cone, not a dull ball sitting on top of the pad.'],
              accent=BLUE)

    c.callout(1110, 710, 480,
              'Use the breadboard as a jig',
              ['Push the strip into the breadboard, sit the module on top, and it',
               'holds everything square while you solder. Nothing is powered.'],
              accent=GREEN)

    c.panel_box(90, 900, 700, 'CHECK', [
        'All 8 display pins and all 7 amplifier pins soldered',
        'No solder bridging two neighbouring pins',
        'Strips sit square, not tilted',
        'Silkscreen labels still readable after soldering',
    ], accent=GREEN, check=True)

    c.note(870, 900, 720, [
        'Do not solder the speaker.',
        'The speaker wires go into the amplifier\'s green screw terminal.',
        'Strip about 5 mm, twist the strands, insert, tighten the screw.',
        'You have two speakers but the MAX98357A is MONO - it drives one.',
    ], kind='info')

    return c.save(OUT / '02-solder-headers.png')


# --------------------------------------------------------------------------- #
# Step 03 - power rails
# --------------------------------------------------------------------------- #

def step_power_rails():
    c = Canvas(1680, 1320)
    c.banner('STEP 3', 'BRIDGE THE POWER RAILS AND AGREE ON A COLOUR CODE')

    bb = Breadboard(c, BB_X, 120, pitch=16).draw()

    # Rail-to-rail bridges, for boards whose rails are split at the middle.
    for pol, colour, row in (('+', RED, 30), ('-', BLACK_W, 33)):
        bb.wire(bb.rail('L', pol, row), bb.rail('R', pol, row), colour, width=5)

    c.callout(60, 170, 420,
              'Only if your rails are split',
              ['Many MB102 boards break each rail near the middle. If yours does, '
               'these jumpers rejoin the halves so one rail is one node again.',
               'If your rails run unbroken end to end, skip this and move on.'],
              accent=(211, 84, 0), target=bb.rail('L', '+', 30), via_x=CH_L)

    c.callout(60, 430, 420,
              'Left + rail becomes 5 V',
              ['Everything needing 5 V taps this rail. In this build that is exactly '
               'one thing: the amplifier VIN.'],
              accent=RED, target=bb.rail('L', '+', 12), via_x=CH_L)

    c.callout(60, 630, 420,
              'Left - rail becomes GND',
              ['Every module ground lands here. This is the most important net on the '
               'board: get it wrong and the I2S amplifier outputs noise or silence.'],
              accent=BLACK_W, target=bb.rail('L', '-', 20), via_x=CH_L)

    c.callout(1150, 170, 440,
              'Keep the right-hand rails for 3.3 V',
              ['The display needs 3.3 V, not 5 V. Giving it its own rail on the '
               'opposite edge makes the two physically hard to confuse.'],
              accent=BLUE, target=bb.rail('R', '+', 12), via_x=CH_R,
              anchor_side='left')

    c.note(1150, 380, 440, [
        'This is a convention, not a rule.',
        'The breadboard does not know what a rail is for.',
        'Pick one layout, draw it on paper, and keep to it -',
        'most wiring mistakes are a rail used for two things.',
    ], kind='info')

    _legend(c, 1150, 560, 440, WIRE_LEGEND + [
        ((120, 126, 138), 'Any other colour = signal'),
    ])

    c.panel_box(1150, 780, 440, 'CHECK', [
        'Rails bridged, or confirmed unbroken',
        'You know which rail is 5 V and which is GND',
        'Right-hand rails still empty, reserved for 3.3 V',
        'Still nothing plugged into USB',
    ], accent=GREEN, check=True)

    c.note(60, 860, 420, [
        'Colour discipline pays for itself.',
        'Red only ever carries 5 V, blue only 3.3 V,',
        'black only ground. When a wire is the wrong',
        'colour you can see the mistake without tracing it.',
    ], kind='ok')

    return c.save(OUT / '03-power-rails.png')


STEPS = [step_breadboard_anatomy, step_solder_headers, step_power_rails]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    for fn in STEPS:
        p = fn()
        print(f'wrote {p.relative_to(root)} ({p.stat().st_size // 1024} KB)')
    if not ESP32_HEADER['left']:
        print('\nNOTE: ESP32_HEADER is unset, so the board-placement steps '
              '(4 onward) are not generated yet.\n      Fill it in from the '
              'physical board to unlock exact hole coordinates.')


if __name__ == '__main__':
    main()
