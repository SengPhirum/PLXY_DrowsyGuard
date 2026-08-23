"""Locate the four DejaVu faces every diagram in this repo is drawn with.

The diagrams were first generated on Linux, where DejaVu lives in
/usr/share/fonts/truetype/dejavu. Hard-coding that path meant the artwork could
only be regenerated on a Linux box, and a tutorial whose figures cannot be
rebuilt on the machine the firmware is flashed from goes stale the first time a
pin moves.

So the same four faces are looked up in the places they actually turn up:

  1. the Linux system font directory (the original path, still first);
  2. matplotlib's bundled copy - matplotlib ships exactly DejaVuSans,
     DejaVuSans-Bold, DejaVuSansMono and DejaVuSansMono-Bold, which is the whole
     set, so `pip install matplotlib` is all a Windows or macOS checkout needs;
  3. anything the caller points DROWSYGUARD_FONT_DIR at.

Sticking to DejaVu rather than falling back to a platform font is deliberate:
every text width in these diagrams is measured to lay the drawing out, so
swapping the metrics would move labels, and a regenerated figure would differ
from its neighbours in a way that reads as a rendering bug.
"""
from __future__ import annotations

import os
from pathlib import Path

FACES = ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf',
         'DejaVuSansMono.ttf', 'DejaVuSansMono-Bold.ttf')


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get('DROWSYGUARD_FONT_DIR')
    if env:
        out.append(Path(env))
    out.append(Path('/usr/share/fonts/truetype/dejavu'))
    out.append(Path('/usr/share/fonts/TTF'))                    # Arch and friends
    out.append(Path('/Library/Fonts'))                          # macOS, if installed
    try:
        import matplotlib
        out.append(Path(matplotlib.__file__).parent / 'mpl-data' / 'fonts' / 'ttf')
    except ImportError:
        pass
    return out


def font_dir() -> Path:
    """The first directory that holds all four faces."""
    tried = []
    for d in _candidates():
        tried.append(str(d))
        if all((d / f).is_file() for f in FACES):
            return d
    raise FileNotFoundError(
        'the DejaVu faces used by every diagram in this repo were not found.\n'
        'Install them with `pip install matplotlib` (it bundles all four), or\n'
        'point DROWSYGUARD_FONT_DIR at a directory containing:\n  '
        + '\n  '.join(FACES) + '\nLooked in:\n  ' + '\n  '.join(tried))


def faces() -> tuple[Path, Path, Path, Path]:
    """(regular, bold, mono, mono-bold), in the order the drawing code wants."""
    d = font_dir()
    return tuple(d / f for f in FACES)   # type: ignore[return-value]
