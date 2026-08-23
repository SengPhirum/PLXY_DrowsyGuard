#!/usr/bin/env python3
"""Reset this board into the ROM download loader, or into the application.

The firmware README and plxy.sh both said this board cannot be put into download mode
by any reset-line sequence, and that the BOOT button was unavoidable. That is true of
esptool's sequences but not of the lines themselves: **this board's auto-reset lines are
inverted** relative to esptool's convention. Determined by trying all four combinations
of line assignment and polarity and reading back the ROM banner:

    dtr = False  ->  GPIO0 LOW   (what holding BOOT does)
    dtr = True   ->  GPIO0 high  (run the application)
    rts = False  ->  EN LOW      (held in reset)
    rts = True   ->  EN high     (running)

So the sequence is: hold EN low, set GPIO0 to pick the boot mode, release EN, release
GPIO0. That also explains a confusing symptom - pyserial de-asserts both lines when a
port is opened with dtr/rts set False, which on this board means "hold in reset with
BOOT pressed", so merely opening the port to read the log dropped the board into
`boot:0x20 (DOWNLOAD)` and made it look dead.

    python scripts/board_reset.py COM9 --download   # ready for esptool
    python scripts/board_reset.py COM9              # run the firmware

**This script does not certify the result, and deliberately so.** An early version
tried to, by matching the ROM banner it read back, and got it wrong in both directions:
it reported the application when the board was in the loader, because a single read
with no flush returned the *previous* reset's bytes, and then reported failure on a
successful run because a reset caught mid-byte prints garbage a cp1252 console cannot
even display. The authority on whether the chip is in the loader is esptool, which
plxy.sh already asks immediately afterwards. So this drives the lines, prints whatever
came back for a human to look at, and leaves the verdict to the tool that can actually
establish it.

Needs pyserial. ESP-IDF's own Python environment has it; the project venv does not.
"""
from __future__ import annotations

import argparse
import sys
import time

# Both lines are inverted on this board, so True is the released state for each.
IDLE = True


def reset(port: str, baud: int, download: bool, listen: float = 0.6) -> str:
    """Drive one reset and return whatever the board said afterwards."""
    import serial

    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0.1
    # Set before open(): opening with these de-asserted would itself reset the chip
    # into the loader, which is the trap described above.
    s.dtr = IDLE
    s.rts = IDLE
    s.open()
    try:
        s.dtr = not download      # GPIO0: low selects the loader, high the app
        s.rts = False             # EN low - hold in reset
        time.sleep(0.15)
        s.rts = True              # EN high - boots, sampling GPIO0
        time.sleep(0.05)
        s.dtr = IDLE              # release GPIO0

        # Read in a loop rather than one sized read: the loader emits about 120 bytes
        # and then stops, so a single large read would just sit on its timeout, while
        # the application emits far more than any one read would take.
        s.reset_input_buffer()
        end = time.time() + listen
        buf = bytearray()
        while time.time() < end:
            chunk = s.read(512)
            if chunk:
                buf += chunk
        return bytes(buf).decode('utf-8', errors='replace')
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('port')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--download', action='store_true',
                    help='boot into the ROM loader instead of the application')
    ap.add_argument('--attempts', type=int, default=3,
                    help='repeat the sequence this many times (it is a timing race)')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    # A Windows console is cp1252 and raises on the garbage a mid-byte reset produces,
    # so the banner would take the whole script down on a run that actually worked.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

    banner = ''
    for i in range(max(1, args.attempts)):
        try:
            banner = reset(args.port, args.baud, args.download)
        except ImportError:
            print("needs pyserial; run this with ESP-IDF's Python", file=sys.stderr)
            return 2
        except Exception as exc:                  # noqa: BLE001 - any serial failure
            print(f'could not drive {args.port}: {exc}', file=sys.stderr)
            return 2
        # Stop early when the banner happens to confirm it, but do not *require* that -
        # see the note in the module docstring about why this is not the authority.
        if not args.download or 'DOWNLOAD' in banner:
            break

    if not args.quiet and banner.strip():
        print(banner.replace('\r\n', '\n').strip())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
