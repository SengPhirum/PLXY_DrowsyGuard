"""The browser installer: the manifest, the page, and the pipeline behind them.

The thing being guarded against is specific. A wrong offset, a wrong `chipFamily`
string, or a manifest that names a file which was not published all produce the same
symptom: the visitor clicks Install, the browser writes something, the board reboots
into nothing, and they have no way to tell that from a hardware fault. Nothing else
in this repository would notice, because the firmware builds and the documentation
renders either way.

Three layers, and they need different kinds of test:

* **`scripts/make_manifest.py`** is exercised against a synthetic build directory -
  a real `flasher_args.json` shape with stand-in binaries. That covers the offset
  handling, the chip-family mapping and the failure paths without needing ESP-IDF.
* **A real build**, when one is present, is used to check the numbers the
  documentation actually publishes. Skipped otherwise, so a checkout with no
  toolchain still runs everything else.
* **The page and the workflow** are checked as text, because the parts of them that
  can be wrong - a manifest path the deployment does not produce, a browser
  requirement the page fails to state, a release job that could run on a pull request
  - are not executable.

`DROWSYGUARD_MANIFEST_DIR` points the real-build tests at a directory the CI job has
already generated, so the same assertions run against the artifacts about to be
published rather than against a local rebuild.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'make_manifest.py'
PAGE = ROOT / 'docs' / 'getting-started' / 'install-esp32.md'
INSTALLER_JS = ROOT / 'docs' / 'assets' / 'js' / 'installer.js'
WORKFLOW = ROOT / '.github' / 'workflows' / 'firmware-release.yml'
DOCS_DEPLOY = ROOT / '.github' / 'workflows' / 'docs-deploy.yml'
MKDOCS = ROOT / 'mkdocs.yml'

# ESP Web Tools accepts exactly these chip families. Duplicated rather than imported
# so that a change to the mapping has to be made twice, deliberately: this list is
# defined by an external library, not by us.
VALID_FAMILIES = {'ESP32', 'ESP32-S2', 'ESP32-S3', 'ESP32-C3', 'ESP32-C6', 'ESP32-H2'}


# --------------------------------------------------------------------------- #
# a synthetic build directory
# --------------------------------------------------------------------------- #

def make_build(tmp_path, chip='esp32s3', app_size=4096):
    """A build directory with the shape ESP-IDF produces, and stand-in binaries.

    The binaries have distinct, recognisable contents so a merge can be checked for
    having put each one at the right offset rather than merely having produced a file
    of about the right size.
    """
    build = tmp_path / 'build'
    (build / 'bootloader').mkdir(parents=True)
    (build / 'partition_table').mkdir(parents=True)

    (build / 'bootloader' / 'bootloader.bin').write_bytes(b'\xE9BOOT' + b'\x01' * 1019)
    (build / 'partition_table' / 'partition-table.bin').write_bytes(b'\xAA\x50PART' + b'\x02' * 1018)
    (build / 'app.bin').write_bytes(b'\xE9APP' + b'\x03' * (app_size - 4))

    (build / 'flasher_args.json').write_text(json.dumps({
        'write_flash_args': ['--flash_mode', 'dio', '--flash_size', '16MB',
                             '--flash_freq', '80m'],
        'flash_settings': {'flash_mode': 'dio', 'flash_size': '16MB',
                           'flash_freq': '80m'},
        'flash_files': {
            '0x0': 'bootloader/bootloader.bin',
            '0x10000': 'app.bin',
            '0x8000': 'partition_table/partition-table.bin',
        },
        'bootloader': {'offset': '0x0', 'file': 'bootloader/bootloader.bin'},
        'app': {'offset': '0x10000', 'file': 'app.bin'},
        'partition-table': {'offset': '0x8000', 'file': 'partition_table/partition-table.bin'},
        'extra_esptool_args': {'chip': chip, 'stub': True},
    }), encoding='utf-8')
    (build / 'project_description.json').write_text(
        json.dumps({'project_name': 'drowsyguard_esp32s3'}), encoding='utf-8')
    return build


def run_script(*args, expect=0):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == expect, (
        f'expected exit {expect}, got {proc.returncode}\n'
        f'stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}')
    return proc


def esptool_available():
    try:
        subprocess.run([sys.executable, '-m', 'esptool', 'version'],
                       capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# --------------------------------------------------------------------------- #
# the manifest generator
# --------------------------------------------------------------------------- #

def test_a_parts_manifest_uses_the_offsets_the_build_reported(tmp_path):
    """The offsets are the whole point. Typed by hand they produce a board that
    flashes cleanly and boots into nothing."""
    build = make_build(tmp_path)
    out = tmp_path / 'out'
    run_script('--build', str(build), '--out', str(out), '--version', 'v1.2.3',
               '--no-merge')

    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['version'] == 'v1.2.3'
    assert len(manifest['builds']) == 1
    build_entry = manifest['builds'][0]
    assert build_entry['chipFamily'] == 'ESP32-S3'
    got = {part['path']: part['offset'] for part in build_entry['parts']}
    assert got == {'bootloader.bin': 0x0,
                   'partition-table.bin': 0x8000,
                   'app.bin': 0x10000}
    for name in got:
        assert (out / name).is_file(), f'{name} is in the manifest but was not copied'


def test_the_chip_family_comes_from_the_build_not_from_a_guess(tmp_path):
    """esptool says "esp32s3", the manifest schema wants "ESP32-S3". A wrong value
    makes the installer refuse a board it could flash perfectly well."""
    for chip, family in (('esp32', 'ESP32'), ('esp32s3', 'ESP32-S3'),
                         ('esp32c3', 'ESP32-C3')):
        build = make_build(tmp_path / chip, chip=chip)
        out = tmp_path / chip / 'out'
        run_script('--build', str(build), '--out', str(out), '--no-merge')
        manifest = json.loads((out / 'manifest.json').read_text())
        assert manifest['builds'][0]['chipFamily'] == family
        assert family in VALID_FAMILIES


def test_an_unknown_chip_is_refused_rather_than_guessed(tmp_path):
    build = make_build(tmp_path, chip='esp32p4')
    proc = run_script('--build', str(build), '--out', str(tmp_path / 'out'),
                      '--no-merge', expect=1)
    assert 'unsupported chip' in proc.stderr


def test_a_missing_build_directory_is_a_clear_error(tmp_path):
    proc = run_script('--build', str(tmp_path / 'nope'), '--out', str(tmp_path / 'out'),
                      expect=1)
    assert 'flasher_args.json is missing' in proc.stderr


def test_an_image_named_but_absent_is_refused(tmp_path):
    """Publishing a manifest that points at a file which was not built is the exact
    failure that produces an opaque browser error at flash time."""
    build = make_build(tmp_path)
    (build / 'app.bin').unlink()
    proc = run_script('--build', str(build), '--out', str(tmp_path / 'out'),
                      '--no-merge', expect=1)
    assert 'does not exist' in proc.stderr


def test_the_offset_record_lists_every_image_with_a_checksum(tmp_path):
    build = make_build(tmp_path)
    out = tmp_path / 'out'
    run_script('--build', str(build), '--out', str(out), '--no-merge')
    offsets = json.loads((out / 'flash-offsets.json').read_text())
    assert offsets['chip'] == 'esp32s3'
    assert offsets['chip_family'] == 'ESP32-S3'
    assert offsets['flash_settings']['flash_size'] == '16MB'
    assert [p['offset'] for p in offsets['parts']] == ['0x0', '0x8000', '0x10000']
    for part in offsets['parts']:
        assert len(part['sha256']) == 64
        assert part['bytes'] > 0


@pytest.mark.skipif(not esptool_available(), reason='esptool is not installed')
def test_the_merged_image_contains_each_part_at_its_offset(tmp_path):
    """esptool exits 0 for any merge that produced a file. This is what makes the
    merge trustworthy rather than merely successful."""
    build = make_build(tmp_path)
    out = tmp_path / 'out'
    run_script('--build', str(build), '--out', str(out), '--version', 'v9')

    manifest = json.loads((out / 'manifest.json').read_text())
    parts = manifest['builds'][0]['parts']
    assert len(parts) == 1
    assert parts[0]['offset'] == 0, 'a merged ESP32-S3 image is written at 0'

    merged = (out / parts[0]['path']).read_bytes()
    for offset, rel in (
            (0x0, build / 'bootloader' / 'bootloader.bin'),
            (0x8000, build / 'partition_table' / 'partition-table.bin'),
            (0x10000, build / 'app.bin')):
        want = rel.read_bytes()
        assert merged[offset:offset + len(want)] == want, (
            f'{rel.name} is not at {hex(offset)} in the merged image')


@pytest.mark.skipif(not esptool_available(), reason='esptool is not installed')
def test_the_merge_refuses_a_layout_whose_lowest_image_is_not_at_zero(tmp_path):
    """On the original ESP32 the bootloader is at 0x1000, and a merged image written
    at 0 would put every byte 4 kB early. The assertion is what stops this script
    from silently producing that for a target it was not designed for."""
    build = make_build(tmp_path)
    args = json.loads((build / 'flasher_args.json').read_text())
    args['flash_files'] = {'0x1000': 'bootloader/bootloader.bin',
                           '0x8000': 'partition_table/partition-table.bin',
                           '0x10000': 'app.bin'}
    (build / 'flasher_args.json').write_text(json.dumps(args), encoding='utf-8')
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), '--build', str(build),
         '--out', str(tmp_path / 'out')],
        capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode != 0
    assert 'not 0' in proc.stderr


# --------------------------------------------------------------------------- #
# a real build, when there is one
# --------------------------------------------------------------------------- #

def _real_manifest_dir():
    override = os.environ.get('DROWSYGUARD_MANIFEST_DIR')
    if override:
        return Path(override)
    return None


def _real_build():
    build = ROOT / 'firmware' / 'esp32s3' / 'build'
    return build if (build / 'flasher_args.json').is_file() else None


@pytest.fixture(scope='module')
def real_manifest(tmp_path_factory):
    """A manifest from an actual firmware build, generated or supplied by CI."""
    supplied = _real_manifest_dir()
    if supplied is not None and (supplied / 'manifest.json').is_file():
        return supplied
    build = _real_build()
    if build is None:
        pytest.skip('no firmware build present; run ./plxy.sh build')
    if not esptool_available():
        pytest.skip('esptool is not installed')
    out = tmp_path_factory.mktemp('realfw')
    run_script('--build', str(build), '--out', str(out), '--version', 'test')
    return out


def test_the_real_build_targets_an_esp32_s3_at_offset_zero(real_manifest):
    manifest = json.loads((real_manifest / 'manifest.json').read_text())
    build = manifest['builds'][0]
    assert build['chipFamily'] == 'ESP32-S3'
    assert [p['offset'] for p in build['parts']] == [0]
    for part in build['parts']:
        image = real_manifest / part['path']
        assert image.is_file(), f'{part["path"]} is in the manifest but not published'
        assert image.stat().st_size > 512 * 1024, 'that is too small to be this firmware'


def test_the_real_build_declares_the_flags_the_installer_needs(real_manifest):
    manifest = json.loads((real_manifest / 'manifest.json').read_text())
    # Erase on a first install, because NVS holds the alert language and a reflashed
    # board that keeps speaking Khmer is correct and baffling.
    assert manifest['new_install_prompt_erase'] is True
    # Improv is the post-flash Wi-Fi handshake. This firmware brings up its own
    # access point and implements no such handshake, so leaving this true would make
    # the installer wait for something that never arrives and report a timeout - which
    # reads as a failed flash.
    assert manifest['improv'] is False
    assert manifest['name'] and manifest['version']


def test_the_real_offsets_match_the_ones_the_documentation_publishes(real_manifest):
    """The page states 0x0 / 0x8000 / 0x10000 in prose. If the build ever disagrees,
    the prose is wrong and this is the only thing that would say so."""
    offsets = json.loads((real_manifest / 'flash-offsets.json').read_text())
    got = {p['offset'] for p in offsets['parts']}
    assert got == {'0x0', '0x8000', '0x10000'}
    page = PAGE.read_text(encoding='utf-8')
    for offset in sorted(got):
        assert f'`{offset}`' in page, f'{offset} is not documented on the install page'


def test_the_published_checksums_match_the_published_files(real_manifest):
    import hashlib

    offsets = json.loads((real_manifest / 'flash-offsets.json').read_text())
    for art in offsets['artifacts']:
        path = real_manifest / art['file']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == art['sha256'], f'{art["file"]} does not match its checksum'
        assert path.stat().st_size == art['bytes']


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #

def test_the_page_uses_esp_web_tools():
    page = PAGE.read_text(encoding='utf-8')
    assert '<esp-web-install-button' in page
    assert 'esp-web-tools' in page
    # Pinned to a major version. An unpinned CDN import is a third party's ability to
    # change what a page on this site does to someone's hardware.
    assert 'esp-web-tools@10' in page


def test_the_page_states_the_browser_requirement_up_front():
    """Web Serial is Chrome and Edge only. A visitor on Firefox who reads to the
    bottom before finding that out has been wasted, and one who does not read at all
    gets an unexplained failure."""
    page = PAGE.read_text(encoding='utf-8')
    head = page.split('## Install')[0]
    assert 'Chrome' in head and 'Edge' in head
    assert 'Web Serial' in head
    assert 'slot="unsupported"' in page, 'and the button itself has to say so too'


def test_the_page_offers_a_route_for_browsers_that_cannot_do_this():
    page = PAGE.read_text(encoding='utf-8')
    assert '## Flash it yourself instead' in page
    assert 'esptool' in page
    assert './plxy.sh flash' in page


def test_the_page_does_not_promise_the_device_is_safe():
    """A page whose whole job is to get firmware onto a board in a vehicle has to
    carry the limitation with it. Nobody reads the security page first."""
    page = PAGE.read_text(encoding='utf-8')
    assert 'not a certified' in page.lower()
    assert 'security.md' in page


def test_the_page_documents_what_gets_written():
    page = PAGE.read_text(encoding='utf-8')
    assert 'bootloader.bin' in page
    assert 'partition-table.bin' in page
    assert 'drowsyguard_esp32s3.bin' in page


def test_the_installer_script_is_loaded_site_wide_not_by_a_relative_src():
    """python-markdown stashes raw HTML blocks before MkDocs' relative-path
    treeprocessor runs, so a relative `src` inside the page body is emitted verbatim
    and 404s at every URL depth but one. extra_javascript is resolved against the site
    root for every page."""
    mkdocs = MKDOCS.read_text(encoding='utf-8')
    assert 'assets/js/installer.js' in mkdocs
    assert INSTALLER_JS.is_file()
    page = PAGE.read_text(encoding='utf-8')
    assert 'src="../assets/js/installer.js"' not in page


def test_the_installer_script_degrades_instead_of_offering_a_dead_button():
    """With no manifest published, the custom element would still render its activate
    slot; the visitor clicks, picks a port, and gets an error from deep inside the
    flashing library. The button stays hidden until a manifest has been parsed."""
    js = INSTALLER_JS.read_text(encoding='utf-8')
    assert 'button.hidden = true' in js
    assert "fetch(base + 'manifest.json'" in js
    assert "'serial' in navigator" in js, 'and it has to name the real requirement'


def test_the_installer_script_resolves_the_manifest_relative_to_the_site_root():
    """A fork publishes at a different path prefix, and MkDocs serves the same page
    at a different depth depending on directory-URL settings."""
    js = INSTALLER_JS.read_text(encoding='utf-8')
    assert 'firmwareBase' in js
    assert '/getting-started/' in js
    assert "'firmware/'" in js


def test_the_page_is_in_the_navigation():
    mkdocs = MKDOCS.read_text(encoding='utf-8')
    assert 'getting-started/install-esp32.md' in mkdocs


# --------------------------------------------------------------------------- #
# the pipeline
# --------------------------------------------------------------------------- #

def test_the_release_workflow_exists_and_is_separate_from_the_docs_ones():
    assert WORKFLOW.is_file()
    wf = WORKFLOW.read_text(encoding='utf-8')
    assert 'espressif/esp-idf-ci-action' in wf
    assert 'esp32s3' in wf


def test_the_release_workflow_pins_the_idf_version():
    """A drowsiness alarm whose binary changes because a base image moved is not
    reproducible, and the flash and RAM figures in the deployment notes are only
    meaningful against a known IDF."""
    wf = WORKFLOW.read_text(encoding='utf-8')
    assert 'IDF_VERSION: v5.' in wf
    assert 'esp_idf_version: ${{ env.IDF_VERSION }}' in wf


def test_the_release_workflow_verifies_before_it_publishes():
    wf = WORKFLOW.read_text(encoding='utf-8')
    for expected in ('fits its partition',
                     'Verify every image the build named actually exists',
                     'Verify the merged image byte-for-byte against its parts',
                     'tests/test_web_installer.py'):
        assert expected in wf, f'the workflow does not {expected!r}'


def test_a_pull_request_cannot_publish_a_release():
    """The publish job asks for `contents: write`; a run that could reach it from a
    pull request would let a fork's branch cut a release."""
    wf = WORKFLOW.read_text(encoding='utf-8')
    publish = wf.split('  publish:', 1)[1]
    assert 'contents: write' in publish
    assert "startsWith(github.ref, 'refs/tags/v')" in publish
    # And the workflow's default is read-only, so nothing else in it can write either.
    assert wf.split('jobs:', 1)[0].count('contents: read') == 1


def test_the_release_workflow_never_types_an_offset():
    """Every offset comes from flasher_args.json. A literal here is the beginning of
    the failure this whole file exists to prevent."""
    wf = WORKFLOW.read_text(encoding='utf-8')
    body = wf.split('jobs:', 1)[1]
    # 0x0 appears in the verification code as a parsed value, never as a flash target.
    assert 'write_flash 0x0' not in body.replace(
        'python -m esptool --chip esp32s3 write_flash 0x0 drowsyguard-esp32s3-merged.bin', '')
    assert 'flasher_args.json' in body


def test_the_docs_deployment_publishes_the_firmware_beside_the_page():
    """Same-origin is not a nicety here. Fetching the manifest cross-origin would put
    the installer at the mercy of whatever CORS headers a release-asset CDN happens to
    send, which is not something this repository controls or can test."""
    deploy = DOCS_DEPLOY.read_text(encoding='utf-8')
    assert 'site/firmware' in deploy
    assert 'releases/latest' in deploy or 'release download' in deploy.lower()
