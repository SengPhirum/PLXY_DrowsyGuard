"""Drawing primitives for the DrowsyGuard hardware tutorial diagrams.

Shared by the step-by-step assembly figures and the reference figures. Everything
is drawn at SS times the final size and downsampled on save, which is what keeps
small pin labels legible.

The Breadboard class is the important part: it draws a real MB102 and exposes
hole() / rail() so a step diagram can point a leader line at an actual hole
coordinate ("E24") instead of an approximate position on the picture.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagram_fonts  # noqa: E402  (needs the sys.path line above)

from PIL import Image, ImageDraw, ImageFont

SS = 2

# --- palette ---------------------------------------------------------------- #
BG = (247, 248, 250)
INK = (23, 28, 38)
MUTED = (108, 118, 134)
HAIRLINE = (203, 210, 220)
PANEL = (255, 255, 255)

NAVY = (16, 42, 94)
GOLD = (255, 208, 46)
RED = (214, 48, 49)
BLUE = (41, 98, 185)
GREEN = (33, 145, 80)
PURPLE = (118, 58, 160)
BLACK_W = (35, 39, 47)

# PCB colours taken from the owner's photograph of the actual parts.
PCB_BLACK = (28, 30, 34)
PCB_BLUE = (28, 62, 122)
PCB_PURPLE = (126, 61, 155)
TERM_GREEN = (86, 176, 86)
SCREEN = (12, 14, 18)

BOARD_CREAM = (237, 233, 222)
BOARD_EDGE = (208, 202, 188)
HOLE_DARK = (72, 69, 63)

# Resolved at import time rather than hard-coded to a Linux path, so the artwork
# can be regenerated on the same machine the firmware is flashed from. See
# scripts/diagram_fonts.py for where it looks and why it insists on DejaVu.
_F_REG, _F_BOLD, _F_MONO, _F_MONO_BOLD = diagram_fonts.faces()


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), max(1, int(size * SS)))


class Canvas:
    """Drawing surface. All coordinates are final-scale pixels."""

    def __init__(self, w: int, h: int, bg=BG):
        self.w, self.h = int(w), int(h)
        self.img = Image.new('RGB', (self.w * SS, self.h * SS), bg)
        self.d = ImageDraw.Draw(self.img)

    def _s(self, *v):
        return [x * SS for x in v]

    # -- primitives ---------------------------------------------------------- #
    def rect(self, x, y, w, h, fill=None, outline=None, width=1, radius=0):
        box = self._s(x, y, x + w, y + h)
        if radius:
            self.d.rounded_rectangle(box, radius=radius * SS, fill=fill,
                                     outline=outline, width=max(1, int(width * SS)))
        else:
            self.d.rectangle(box, fill=fill, outline=outline,
                             width=max(1, int(width * SS)))

    def line(self, pts, fill=INK, width=2):
        self.d.line([c * SS for p in pts for c in p], fill=fill,
                    width=max(1, int(width * SS)), joint='curve')

    def circle(self, x, y, r, fill=None, outline=None, width=1):
        self.d.ellipse(self._s(x - r, y - r, x + r, y + r), fill=fill,
                       outline=outline, width=max(1, int(width * SS)))

    def text(self, x, y, s, size=16, fill=INK, bold=False, mono=False, anchor='la'):
        path = (_F_MONO_BOLD if bold else _F_MONO) if mono else (_F_BOLD if bold else _F_REG)
        self.d.text(self._s(x, y), s, font=_font(path, size), fill=fill, anchor=anchor)

    def text_w(self, s, size=16, bold=False, mono=False) -> float:
        path = (_F_MONO_BOLD if bold else _F_MONO) if mono else (_F_BOLD if bold else _F_REG)
        return self.d.textlength(s, font=_font(path, size)) / SS

    def arrow(self, a, b, fill=INK, width=3, head=11):
        """Straight leader line from a to b with a solid arrowhead at b."""
        import math
        (x1, y1), (x2, y2) = a, b
        ang = math.atan2(y2 - y1, x2 - x1)
        bx, by = x2 - head * 0.85 * math.cos(ang), y2 - head * 0.85 * math.sin(ang)
        self.line([(x1, y1), (bx, by)], fill=fill, width=width)
        self.d.polygon([
            self._s(x2, y2)[0:2],
            self._s(x2 - head * math.cos(ang - 0.42), y2 - head * math.sin(ang - 0.42))[0:2],
            self._s(x2 - head * math.cos(ang + 0.42), y2 - head * math.sin(ang + 0.42))[0:2],
        ], fill=fill)

    def elbow_arrow(self, a, b, fill=INK, width=3, head=11, first='h'):
        """Right-angled leader: travels on one axis, then the other, then arrows in."""
        import math
        (x1, y1), (x2, y2) = a, b
        mid = (x2, y1) if first == 'h' else (x1, y2)
        ang = math.atan2(y2 - mid[1], x2 - mid[0])
        if mid == (x2, y2):
            ang = math.atan2(y2 - y1, x2 - x1)
        bx = x2 - head * 0.85 * math.cos(ang)
        by = y2 - head * 0.85 * math.sin(ang)
        self.line([(x1, y1), mid, (bx, by)], fill=fill, width=width)
        self.d.polygon([
            self._s(x2, y2)[0:2],
            self._s(x2 - head * math.cos(ang - 0.42), y2 - head * math.sin(ang - 0.42))[0:2],
            self._s(x2 - head * math.cos(ang + 0.42), y2 - head * math.sin(ang + 0.42))[0:2],
        ], fill=fill)

    # -- composites ---------------------------------------------------------- #
    def banner(self, step: str, title: str, h=64):
        """The bold navy title bar: 'STEP 1 - PLACE THE BOARD ...'."""
        self.rect(0, 0, self.w, h, fill=NAVY)
        self.rect(0, h, self.w, 4, fill=GOLD)
        x = 26
        self.text(x, h / 2, step, size=30, bold=True, fill=(255, 255, 255), anchor='lm')
        x += self.text_w(step, size=30, bold=True) + 16
        self.text(x, h / 2 + 1, '—', size=28, bold=True, fill=GOLD, anchor='lm')
        x += 34
        self.text(x, h / 2, title, size=30, bold=True, fill=GOLD, anchor='lm')

    def wrap(self, s: str, width: float, size=14, bold=False) -> list:
        out, line = [], ''
        for word in s.split():
            trial = f'{line} {word}'.strip()
            if self.text_w(trial, size=size, bold=bold) <= width:
                line = trial
            else:
                if line:
                    out.append(line)
                line = word
        if line:
            out.append(line)
        return out

    def callout(self, x, y, w, title, body, accent=INK, target=None, size=14,
                via_x=None, anchor_side='auto'):
        """Titled box with a leader line to `target`. Returns its height.

        `body` is a list of paragraphs, each wrapped to the box width - do not
        pre-split them into display lines or they get wrapped twice.

        `via_x` routes the leader through a vertical channel at that x before
        turning in horizontally, which keeps the line beside the breadboard
        instead of dragging it across the holes.
        """
        tlines = self.wrap(title, w - 32, size=size + 1, bold=True)
        blines = []
        for para in body:
            blines.extend(self.wrap(para, w - 32, size=size) or [''])
        h = 18 + 21 * len(tlines) + 20 * len(blines) + 14
        self.rect(x + 3, y + 4, w, h, fill=(231, 234, 239), radius=8)
        self.rect(x, y, w, h, fill=PANEL, outline=accent, width=2, radius=8)
        ty = y + 14
        for ln in tlines:
            self.text(x + 16, ty, ln, size=size + 1, bold=True, fill=accent)
            ty += 21
        ty += 2
        for ln in blines:
            self.text(x + 16, ty, ln, size=size, fill=(58, 64, 76))
            ty += 20
        if target:
            side = anchor_side
            if side == 'auto':
                side = 'right' if target[0] > x + w else 'left'
            ax = x + w if side == 'right' else x
            ay = y + h / 2
            if via_x is None:
                self.elbow_arrow((ax, ay), target, fill=accent, width=3, first='v')
            else:
                self.line([(ax, ay), (via_x, ay), (via_x, target[1])],
                          fill=accent, width=3)
                self.arrow((via_x, target[1]), target, fill=accent, width=3)
        return h

    def panel_box(self, x, y, w, header, rows, accent=NAVY, size=14, row_h=30,
                  check=False):
        """Dark-header box: the LEGEND / CHECK / NOTE blocks."""
        hh = 34
        h = hh + 14 + row_h * len(rows) + 10
        self.rect(x, y, w, h, fill=PANEL, outline=accent, width=2, radius=8)
        self.rect(x, y, w, hh, fill=accent, radius=8)
        self.rect(x, y + hh - 10, w, 10, fill=accent)
        self.text(x + w / 2, y + hh / 2, header, size=size + 2, bold=True,
                  fill=(255, 255, 255), anchor='mm')
        ry = y + hh + 12
        for row in rows:
            if check:
                self.rect(x + 16, ry + 3, 18, 18, fill=(233, 248, 239),
                          outline=GREEN, width=2, radius=4)
                self.line([(x + 20, ry + 12), (x + 24, ry + 17)], fill=GREEN, width=3)
                self.line([(x + 24, ry + 17), (x + 31, ry + 7)], fill=GREEN, width=3)
                self.text(x + 44, ry + 4, row, size=size, fill=(48, 54, 66))
            else:
                self.text(x + 16, ry + 4, row, size=size, fill=(48, 54, 66))
            ry += row_h
        return h

    def swatch_row(self, x, y, colour, label, size=14, w=30):
        self.rect(x, y + 6, w, 6, fill=colour, radius=3)
        self.text(x + w + 12, y, label, size=size, fill=(48, 54, 66))

    def note(self, x, y, w, lines, kind='info'):
        colours = {'info': ((234, 242, 252), BLUE), 'warn': ((255, 244, 229), (211, 84, 0)),
                   'danger': ((253, 236, 234), (192, 57, 43)), 'ok': ((233, 248, 239), GREEN)}
        bg, edge = colours[kind]
        wrapped = []
        for i, ln in enumerate(lines):
            wrapped.extend([(w2, i == 0) for w2 in self.wrap(ln, w - 34, size=14,
                                                             bold=(i == 0))] or [('', i == 0)])
        h = 16 + 21 * len(wrapped)
        self.rect(x, y, w, h, fill=bg, radius=8)
        self.rect(x, y, 6, h, fill=edge, radius=2)
        for i, (ln, bold) in enumerate(wrapped):
            self.text(x + 20, y + 10 + 21 * i, ln, size=14, bold=bold, fill=(60, 66, 78))
        return h

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.img.resize((self.w, self.h), Image.LANCZOS).save(path, 'PNG', optimize=True)
        return path


# --------------------------------------------------------------------------- #
# Breadboard
# --------------------------------------------------------------------------- #

COLS = 'ABCDEFGHIJ'


class Breadboard:
    """An MB102 830-point solderless breadboard, drawn to scale.

    830 tie points = 63 rows x 10 holes in the main field, plus four power rails
    of 50 holes each (10 groups of 5). Verified against the owner's photograph:
    row numbers run past 60, column letters A-E and F-J, rails on both long edges.

    Geometry follows the real thing: 0.1 in hole pitch, and a 0.3 in centre trench,
    so a 0.9 in wide module lands in columns B and I.
    """

    ROWS = 63
    RAIL_HOLES = 50

    def __init__(self, canvas: Canvas, x: float, y: float, pitch: float = 16,
                 orient: str = 'portrait', rows: int | None = None):
        """orient 'portrait' runs rows top-to-bottom; 'landscape' runs them
        left-to-right with the power rails along the top and bottom edges, which
        is how a breadboard is normally photographed for a wiring poster."""
        self.c = canvas
        self.x, self.y, self.p = x, y, pitch
        self.orient = orient
        if rows:
            self.ROWS = rows
        self.rail_w = 3 * pitch          # +/- pair plus margin
        self.head = pitch * 1.6          # column-letter strip
        self.trench = 3 * pitch
        self.across = 10 * pitch + self.trench      # A..J plus the trench
        self.along = self.ROWS * pitch              # rows 1..63
        if orient == 'portrait':
            self.field_x = x + self.rail_w + pitch
            self.field_y = y + self.head
            self.field_w, self.field_h = self.across, self.along
            self.w = self.across + 2 * (self.rail_w + pitch)
            self.h = self.along + 2 * self.head
        else:
            self.field_x = x + self.head
            self.field_y = y + self.rail_w + self.head
            self.field_w, self.field_h = self.along, self.across
            self.w = self.along + 2 * self.head
            self.h = self.across + 2 * (self.rail_w + self.head)

    # -- coordinates --------------------------------------------------------- #
    def hole(self, col: str, row: int):
        """Centre of hole `col``row`, e.g. hole('E', 24). Rows are 1-based."""
        i = COLS.index(col.upper())
        across = (i + 0.5) * self.p + (self.trench if i >= 5 else 0)
        along = (row - 0.5) * self.p
        if self.orient == 'portrait':
            return (self.field_x + across, self.field_y + along)
        return (self.field_x + along, self.field_y + across)

    def rail(self, side: str, polarity: str, row: int):
        """A power-rail hole.

        side is 'L'/'R' in portrait and 'T'/'B' in landscape ('L' and 'T' are
        accepted as synonyms for the first edge). On both edges the outer line is
        '+' (red) and the inner one is '-' (blue), which is how the MB102 in the
        owner's photograph is printed.
        """
        first = side.upper() in ('L', 'T')
        along = (row - 0.5) * self.p
        if first:
            base = (self.x if self.orient == 'portrait' else self.y) + self.p * 0.5
            off = 0 if polarity == '+' else self.p * 1.5
        else:
            far = (self.x + self.w) if self.orient == 'portrait' else (self.y + self.h)
            base = far - self.rail_w + self.p * 0.5
            off = self.p * 1.5 if polarity == '+' else 0
        if self.orient == 'portrait':
            return (base + off, self.field_y + along)
        return (self.field_x + along, base + off)

    def row_y(self, row: int):
        return self.hole('A', row)[1]

    # -- drawing ------------------------------------------------------------- #
    def draw(self, label_every: int = 5, letters: bool = True, numbers: bool = True):
        c, p = self.c, self.p
        port = self.orient == 'portrait'
        c.rect(self.x, self.y, self.w, self.h, fill=BOARD_CREAM,
               outline=BOARD_EDGE, width=2, radius=6)

        # centre trench, between the E and F hole lines
        e_end = self.hole('E', 1)[0 if port else 1] + p * 0.5
        if port:
            c.rect(e_end, self.field_y, self.trench, self.field_h, fill=(200, 194, 180))
            c.rect(e_end + 2, self.field_y, self.trench - 4, self.field_h,
                   fill=(186, 180, 166))
        else:
            c.rect(self.field_x, e_end, self.field_w, self.trench, fill=(200, 194, 180))
            c.rect(self.field_x, e_end + 2, self.field_w, self.trench - 4,
                   fill=(186, 180, 166))

        # main field holes
        r = max(1.1, p * 0.17)
        for row in range(1, self.ROWS + 1):
            for col in COLS:
                hx, hy = self.hole(col, row)
                c.rect(hx - r * 1.5, hy - r * 1.5, r * 3, r * 3, fill=HOLE_DARK,
                       radius=1)

        # column letters A..J, on both sides of the across-axis
        if letters:
            for col in COLS:
                hx, hy = self.hole(col, 1)
                fs = max(6, int(p * 0.72))
                if port:
                    for yy in (self.y + self.head * 0.5,
                               self.y + self.h - self.head * 0.5):
                        c.text(hx, yy, col, size=fs, bold=True, fill=(90, 86, 78),
                               anchor='mm')
                else:
                    for xx in (self.x + self.head * 0.5,
                               self.x + self.w - self.head * 0.5):
                        c.text(xx, hy, col, size=fs, bold=True, fill=(90, 86, 78),
                               anchor='mm')

        # row numbers 1..63 along the along-axis
        if numbers:
            fs = max(6, int(p * 0.68))
            for row in range(1, self.ROWS + 1):
                if row % label_every and row != 1:
                    continue
                hx, hy = self.hole('A', row)
                if port:
                    c.text(self.field_x - p * 0.35, hy, str(row), size=fs,
                           fill=(120, 114, 104), anchor='rm')
                    c.text(self.field_x + self.field_w + p * 0.35, hy, str(row),
                           size=fs, fill=(120, 114, 104), anchor='lm')
                else:
                    c.text(hx, self.field_y - p * 0.45, str(row), size=fs,
                           fill=(120, 114, 104), anchor='mb')
                    c.text(hx, self.field_y + self.field_h + p * 0.45, str(row),
                           size=fs, fill=(120, 114, 104), anchor='mt')

        # power rails
        for side in (('L', 'R') if port else ('T', 'B')):
            for pol, colour in (('+', RED), ('-', BLUE)):
                a = self.rail(side, pol, 1)
                b = self.rail(side, pol, self.ROWS)
                if port:
                    c.line([(a[0], a[1] - p * 0.6), (b[0], b[1] + p * 0.6)],
                           fill=colour, width=2)
                else:
                    c.line([(a[0] - p * 0.6, a[1]), (b[0] + p * 0.6, b[1])],
                           fill=colour, width=2)
                for row in range(1, self.ROWS + 1):
                    # 50 holes in 10 groups of 5: one row per group of six is blank
                    if row % 6 == 0:
                        continue
                    hx, hy = self.rail(side, pol, row)
                    c.rect(hx - r * 1.5, hy - r * 1.5, r * 3, r * 3,
                           fill=HOLE_DARK, radius=1)
                sym = '+' if pol == '+' else '−'
                fs = max(7, int(p * 0.9))
                if port:
                    for yy in (self.y + self.head * 0.5,
                               self.y + self.h - self.head * 0.5):
                        c.text(a[0], yy, sym, size=fs, bold=True, fill=colour,
                               anchor='mm')
                else:
                    for xx in (self.x + self.head * 0.5,
                               self.x + self.w - self.head * 0.5):
                        c.text(xx, a[1], sym, size=fs, bold=True, fill=colour,
                               anchor='mm')

        # mounting clips along the long edges, as on the real board
        for f in (0.16, 0.5, 0.84):
            if port:
                yy = self.y + self.h * f
                for xx in (self.x - p * 0.45, self.x + self.w - p * 0.05):
                    c.rect(xx, yy - p * 0.9, p * 0.5, p * 1.8, fill=(232, 228, 216),
                           outline=BOARD_EDGE, width=1, radius=2)
            else:
                xx = self.x + self.w * f
                for yy in (self.y - p * 0.45, self.y + self.h - p * 0.05):
                    c.rect(xx - p * 0.9, yy, p * 1.8, p * 0.5, fill=(232, 228, 216),
                           outline=BOARD_EDGE, width=1, radius=2)
        return self

    def wire(self, a, b, colour, width=5, bow=0.0):
        """A jumper wire between two holes, drawn with a slight sag."""
        (x1, y1), (x2, y2) = a, b
        if bow:
            mx, my = (x1 + x2) / 2 + bow, (y1 + y2) / 2
            pts = [(x1, y1), (mx, my), (x2, y2)]
        else:
            pts = [(x1, y1), (x2, y2)]
        self.c.line(pts, fill=(0, 0, 0), width=width + 2)
        self.c.line(pts, fill=colour, width=width)
        for (px, py) in ((x1, y1), (x2, y2)):
            self.c.circle(px, py, width * 0.62, fill=colour,
                          outline=(30, 30, 30), width=1)
