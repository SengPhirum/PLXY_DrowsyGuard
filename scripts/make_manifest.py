#!/usr/bin/env python3
"""Turn an ESP-IDF build directory into something ESP Web Tools can flash.

Two outputs, and they are for two different audiences:

  * ``manifest.json`` plus the binaries, for the browser installer on the
    documentation site. A visitor with Chrome or Edge clicks one button and the page
    writes the firmware over Web Serial - no ESP-IDF, no Python, no toolchain, no
    driver hunt. That is the difference between "clone the repo, install a 2 GB
    toolchain and read three pages of setup" and "plug the board in".
  * ``flash-offsets.json``, a small machine-readable record of what went where, so
    the documentation and the tests can state the offsets without anyone
    transcribing them by hand.

**Everything comes from the build, nothing is hard-coded.** ESP-IDF writes
``flasher_args.json`` next to the binaries and it is authoritative: it names the
chip, the flash mode, the size, the frequency, and the offset of every image. A
manifest with a hand-copied offset is the kind of mistake that produces a board which
flashes successfully and then boots into nothing, and the person holding it has no
way to tell that from a hardware fault. So the offsets are read, not written.

**Why a merged image rather than a parts list.** ESP Web Tools accepts either. The
merged form is used because it removes an entire class of failure: with three parts
there are three chances for a partial write to leave the board with a new app and an
old bootloader, and the browser cannot verify afterwards which happened. One image at
offset 0 either lands or does not. It also makes the download a single file, which
matters when the page is served from GitHub Pages and the person is on a phone
hotspot in a workshop.

    python scripts/make_manifest.py --build firmware/esp32s3/build --out site/firmware
    python scripts/make_manifest.py --build ... --out ... --version v0.2.0

Requires esptool, which ESP-IDF already provides; ``--no-merge`` skips it and emits a
parts-list manifest instead, which is the fallback if esptool is unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ESP Web Tools' `chipFamily` vocabulary. Not the same strings as esptool's `--chip`,
# which is why this mapping exists rather than an uppercase() call: esptool says
# "esp32s3", the manifest schema wants "ESP32-S3", and a wrong value here makes the
# installer refuse a board it could flash perfectly well.
CHIP_FAMILY = {
    'esp32': 'ESP32',
    'esp32s2': 'ESP32-S2',
    'esp32s3': 'ESP32-S3',
    'esp32c3': 'ESP32-C3',
    'esp32c6': 'ESP32-C6',
    'esp32h2': 'ESP32-H2',
}

MERGED_NAME = 'drowsyguard-esp32s3-merged.bin'
MANIFEST_NAME = 'manifest.json'
OFFSETS_NAME = 'flash-offsets.json'


def read_flasher_args(build: Path) -> dict:
    path = build / 'flasher_args.json'
    if not path.is_file():
        raise SystemExit(f'{path} is missing - run `idf.py build` first')
    return json.loads(path.read_text(encoding='utf-8'))


def project_name(build: Path, fallback='drowsyguard') -> str:
    path = build / 'project_description.json'
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding='utf-8')).get('project_name',
                                                                    fallback)
        except json.JSONDecodeError:
            pass
    return fallback


def parts_from(args: dict, build: Path):
    """[(offset_int, Path)], in ascending offset order.

    Sorted because the merge is positional: esptool writes the images into one blob
    in the order it is given them, and an out-of-order pair silently produces a file
    whose later section overwrites the earlier one's padding.
    """
    out = []
    for offset, rel in args['flash_files'].items():
        path = build / rel
        if not path.is_file():
            raise SystemExit(f'{path} is named in flasher_args.json but does not exist')
        out.append((int(offset, 16), path))
    return sorted(out)


def esptool_cmd():
    """How to invoke esptool here.

    Three ways, in order of how likely they are to be the *right* esptool: the
    module inside the interpreter running this script, a standalone binary on PATH,
    or nothing. Preferring the module matters inside ESP-IDF's export environment,
    where the version pinned to the IDF release is the one that produced the build.
    """
    try:
        subprocess.run([sys.executable, '-m', 'esptool', 'version'],
                       capture_output=True, check=True)
        return [sys.executable, '-m', 'esptool']
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    found = shutil.which('esptool.py') or shutil.which('esptool')
    return [found] if found else None


def merge(build: Path, args: dict, dest: Path) -> Path:
    cmd = esptool_cmd()
    if cmd is None:
        raise SystemExit('esptool is not available; pass --no-merge for a parts manifest')
    chip = args.get('extra_esptool_args', {}).get('chip', 'esp32s3')
    flash = args.get('flash_settings', {})
    call = cmd + ['--chip', chip, 'merge_bin', '-o', str(dest)]
    for key, flag in (('flash_mode', '--flash_mode'), ('flash_size', '--flash_size'),
                      ('flash_freq', '--flash_freq')):
        if key in flash:
            call += [flag, flash[key]]
    for offset, path in parts_from(args, build):
        call += [hex(offset), str(path)]
    proc = subprocess.run(call, capture_output=True, text=True)
    if proc.returncode != 0:
        # esptool renamed merge_bin to merge-bin in v5. Try the other spelling before
        # giving up, so this script works either side of that change.
        call[call.index('merge_bin')] = 'merge-bin'
        proc = subprocess.run(call, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit('esptool merge failed:\n' + proc.stdout[-2000:] + proc.stderr[-2000:])
    if not dest.is_file():
        raise SystemExit(f'esptool reported success but {dest} was not written')
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(name, version, chip_family, parts):
    """The ESP Web Tools manifest.

    `improv: false` is deliberate and worth a line. Improv is the serial handshake
    that lets the browser hand Wi-Fi credentials to the board after flashing, and
    this firmware does not implement it - it brings up its own access point instead.
    Leaving the flag true would make the installer wait for a handshake that never
    comes and then report a timeout, which reads as a failed flash.
    """
    return {
        'name': name,
        'version': version,
        'new_install_prompt_erase': True,
        'improv': False,
        'builds': [{'chipFamily': chip_family, 'parts': parts}],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', default='firmware/esp32s3/build',
                    help='ESP-IDF build directory (default: %(default)s)')
    ap.add_argument('--out', default='site/firmware',
                    help='where to write the manifest and binaries')
    ap.add_argument('--version', default=None,
                    help='version string for the manifest (default: from git, else "dev")')
    ap.add_argument('--name', default='DrowsyGuard',
                    help='product name shown by the installer')
    ap.add_argument('--no-merge', action='store_true',
                    help='emit a three-part manifest instead of one merged image')
    args_ns = ap.parse_args()

    build = Path(args_ns.build)
    out = Path(args_ns.out)
    out.mkdir(parents=True, exist_ok=True)

    fa = read_flasher_args(build)
    chip = fa.get('extra_esptool_args', {}).get('chip', 'esp32s3')
    family = CHIP_FAMILY.get(chip)
    if family is None:
        raise SystemExit(f'unsupported chip {chip!r}; add it to CHIP_FAMILY')

    version = args_ns.version or describe_version()
    parts_in = parts_from(fa, build)

    if args_ns.no_merge:
        parts = []
        for offset, path in parts_in:
            shutil.copy2(path, out / path.name)
            parts.append({'path': path.name, 'offset': offset})
    else:
        merged = merge(build, fa, out / MERGED_NAME)
        # Offset 0, always, and that is not an assumption: merge_bin pads from the
        # lowest offset it was given, and the lowest here is the bootloader's, which
        # is 0x0 on the S3. The assert makes that explicit rather than lucky - on an
        # original ESP32 the bootloader sits at 0x1000 and a merged image would have
        # to be written there instead.
        assert parts_in[0][0] == 0, (
            f'the lowest image is at {hex(parts_in[0][0])}, not 0; the merged image '
            f'must be written at that offset, not 0')
        parts = [{'path': MERGED_NAME, 'offset': 0}]

    manifest = build_manifest(args_ns.name, version, family, parts)
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + '\n',
                                     encoding='utf-8')

    # The record the documentation and the tests read, so nobody has to transcribe an
    # offset into prose and get it wrong.
    offsets = {
        'chip': chip,
        'chip_family': family,
        'project': project_name(build),
        'version': version,
        'built_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'flash_settings': fa.get('flash_settings', {}),
        'merged': not args_ns.no_merge,
        'parts': [{'offset': hex(o), 'file': p.name, 'bytes': p.stat().st_size,
                   'sha256': sha256(p)} for o, p in parts_in],
        'artifacts': [{'file': part['path'], 'offset': hex(part['offset']),
                       'bytes': (out / part['path']).stat().st_size,
                       'sha256': sha256(out / part['path'])} for part in parts],
    }
    (out / OFFSETS_NAME).write_text(json.dumps(offsets, indent=2) + '\n',
                                    encoding='utf-8')

    print(f'{args_ns.name} {version} for {family}')
    for part in offsets['artifacts']:
        print(f"  {part['offset']:>8}  {part['file']}  "
              f"{part['bytes'] / 1024:.0f} kB  {part['sha256'][:16]}")
    print(f'  wrote {out / MANIFEST_NAME} and {out / OFFSETS_NAME}')
    return 0


def describe_version() -> str:
    """A version string from git, so a published binary can be traced to a commit.

    Falls back to "dev" rather than failing: this script has to work in a tarball
    with no .git, and a build that refuses to produce a manifest because it cannot
    find a tag is worse than one labelled honestly.
    """
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'describe', '--tags', '--always',
                              '--dirty'], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except FileNotFoundError:
        pass
    return 'dev'


if __name__ == '__main__':
    raise SystemExit(main())
