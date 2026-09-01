"""The documentation site's fleet monitor.

`docs/assets/js/fleet.js` is a hand-written MQTT-over-WebSocket client and a renderer
for strings a stranger chose, published on the project's own domain. Nothing else in
this repository would notice if it broke, and its two interesting failure modes are
both silent:

  * a codec mistake - a wrong reserved bit, a mis-skipped MQTT 5 property length -
    produces a page that connects and then receives nothing, which looks exactly like
    a device that is not publishing;
  * a sanitising mistake does not look like anything at all until it matters.

`fleet_page_harness.mjs` does the work: it stubs a DOM built from the ids the page
actually contains, evaluates `fleet.js` unmodified, drives the codec against byte
sequences from the MQTT 3.1.1 and MQTT 5 specifications, and feeds the parser the
payloads a shared broker can really deliver - including ones that are not DrowsyGuard
messages at all.

The checks in this file are the ones that are about the *files* rather than about the
behaviour: that the page and the script still refer to the same element ids, that the
script is wired into the site the one way that works, and that the properties which
have to hold for the whole page - no `innerHTML`, no persisted password - hold in the
source rather than only in a test run.

Skipped without Node. That is a real gap on a machine without it, and a page that
cannot be exercised is still better than one that is never exercised.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name('fleet_page_harness.mjs')
PAGE = ROOT / 'docs/fleet-monitoring.md'
SCRIPT = ROOT / 'docs/assets/js/fleet.js'
STYLE = ROOT / 'docs/stylesheets/fleet.css'
MKDOCS = ROOT / 'mkdocs.yml'


@pytest.fixture(scope='module')
def harness_output():
    if shutil.which('node') is None:
        pytest.skip('node is not installed')
    return subprocess.run(['node', str(HARNESS)], capture_output=True, text=True,
                          cwd=str(ROOT), timeout=180)


def test_every_path_is_clean(harness_output):
    assert harness_output.returncode == 0, (
        'the fleet monitor failed at least one check:\n'
        + harness_output.stdout[-6000:] + harness_output.stderr[-2000:])
    assert 'FAIL' not in harness_output.stdout, harness_output.stdout[-6000:]


def test_the_harness_reached_every_section(harness_output):
    """A harness that silently stops testing reports success, which is worse than no
    harness. Each section name is asserted rather than only the total, because losing
    one of them is exactly the failure a total would hide."""
    out = harness_output.stdout
    for section in ('remaining-length varint:', 'CONNECT:',
                    'SUBSCRIBE, PUBACK, PING, DISCONNECT:', 'incoming packets:',
                    'framing:', 'clean():', 'num():', 'parseAlert():',
                    'parseAlert() rejects:', 'hostile payloads:', 'parseStatus():',
                    'de-duplication:', 'device state:', 'filters:',
                    'rendering edges:', 'settings:', 'message routing:',
                    'clipboard:'):
        assert section in out, f'the harness never reached "{section}"'
    assert out.count('  ok') >= 70, f'only {out.count("  ok")} checks ran:\n{out}'


def test_the_page_and_the_script_agree_on_every_element():
    """The stub DOM answers every id the markdown contains, so it cannot catch a
    rename on its own: `$('fleet-hostname')` would get an element if the markup had
    been renamed to match. This is the check that does - every id the script asks for
    has to exist in the page."""
    page = PAGE.read_text(encoding='utf-8')
    script = SCRIPT.read_text(encoding='utf-8')

    page_ids = set(re.findall(r'id="([^"]+)"', page))
    # `$('...')` and the direct getElementById calls - and nothing looser than that.
    # A pattern like `'(fleet-[a-z-]+)'` would also collect the CSS class names the
    # renderer passes to el(), which are not ids and never will be.
    wanted = set(re.findall(r"\$\('([^']+)'\)", script))
    wanted |= set(re.findall(r"getElementById\('([^']+)'\)", script))
    # Plus the ids wired in a loop, which are written out as an array literal.
    for group in re.findall(r"for \(const id of \[([^\]]+)\]\)", script):
        wanted |= set(re.findall(r"'([^']+)'", group))

    missing = sorted(w for w in wanted if w.startswith('fleet-') and w not in page_ids)
    assert not missing, f'the script asks for element ids the page does not have: {missing}'


def test_the_script_is_wired_into_the_site_the_only_way_that_works():
    """A `<script src>` in the page body cannot work: python-markdown stashes raw HTML
    blocks before MkDocs' relative-path treeprocessor runs, so the src is emitted
    exactly as written and 404s at every URL depth but one. The same reasoning
    installer.js already documents."""
    conf = MKDOCS.read_text(encoding='utf-8')
    assert 'assets/js/fleet.js' in conf, 'fleet.js is not in extra_javascript'
    assert 'stylesheets/fleet.css' in conf, 'fleet.css is not in extra_css'
    assert 'fleet-monitoring.md' in conf, 'the page is not in the nav'

    page = PAGE.read_text(encoding='utf-8')
    assert '<script' not in page, 'the page carries an inline script'
    assert 'src=' not in page, 'the page loads something by src'

    # It is loaded on every page of the site, so it has to leave immediately on all
    # but one of them - and every selector in the stylesheet has to be namespaced.
    script = SCRIPT.read_text(encoding='utf-8')
    assert "getElementById('fleet-app')" in script
    assert 'if (!root) return;' in script, (
        'fleet.js does not bail out on pages without the app')


def test_the_stylesheet_cannot_restyle_the_rest_of_the_site():
    """It is loaded on every page. A bare `.card` or `.pill` in it would silently
    restyle the theme, on pages nobody thought to look at."""
    css = STYLE.read_text(encoding='utf-8')
    # Strip comments and at-rule/nesting braces, then look at what each rule targets.
    body = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    selectors = []
    for block in re.findall(r'([^{}]+)\{', body):
        for sel in block.split(','):
            sel = sel.strip()
            if sel and not sel.startswith('@') and not sel.startswith('%'):
                selectors.append(sel)
    allowed_bare = {':root', '[data-md-color-scheme="slate"]', '[hidden]'}
    for sel in selectors:
        if sel in allowed_bare:
            continue
        assert '.fleet-' in sel or '#fleet-' in sel, (
            f'selector {sel!r} is not namespaced to the fleet monitor')


def test_the_script_never_builds_dom_from_a_string():
    """The structural guarantee behind the whole page. Every value it renders was
    chosen by whoever is publishing to the topic, and on the default public broker
    that is anybody - so the only defence that does not depend on a blocklist being
    complete is never parsing markup at all."""
    js = SCRIPT.read_text(encoding='utf-8')
    for pattern, what in (
        (r'\.\s*innerHTML\s*=', 'assigns innerHTML'),
        (r'\.\s*outerHTML\s*=', 'assigns outerHTML'),
        (r'insertAdjacentHTML\s*\(', 'calls insertAdjacentHTML'),
        (r'document\s*\.\s*write', 'calls document.write'),
        # A string passed to either of these is evaluated. Neither is needed here.
        (r'\beval\s*\(', 'calls eval'),
        (r'new\s+Function\s*\(', 'builds a function from a string'),
    ):
        assert not re.search(pattern, js), f'fleet.js {what}'


def test_the_script_never_persists_a_password():
    """The host, port, topic and client id are remembered so a demonstration does not
    begin with retyping. The password is not, and the list of persisted keys is where
    that is decided - so that is what is checked, rather than trusting a comment."""
    js = SCRIPT.read_text(encoding='utf-8')
    persisted = re.search(r"const PERSISTED = \[(.*?)\];", js, flags=re.S)
    assert persisted, 'the persisted-key list has been renamed or removed'
    keys = re.findall(r"'([^']+)'", persisted.group(1))
    assert 'host' in keys and 'topic' in keys, keys
    for secret in ('password', 'pass', 'secret'):
        assert not any(secret in k for k in keys), (
            f'{secret!r} appears in the persisted keys: {keys}')
    # And the only writer must go through that list rather than storing the object.
    assert 'for (const k of PERSISTED) out[k] = s[k];' in js, (
        'saveSettings() no longer filters through PERSISTED')


def test_the_public_broker_is_labelled_wherever_it_appears():
    """It is preconfigured so a demonstration works in a room with no broker in it,
    and everything published to it is readable by anyone who guesses the topic. That
    trade has to be stated on the page, not only in the security document."""
    page = PAGE.read_text(encoding='utf-8').lower()
    assert 'broker.emqx.io' in page
    assert 'demonstration and testing only' in page
    assert 'no authentication' in page
    # And it has to point at the fuller explanation rather than being the only copy.
    assert 'security.md#mqtt-alerting-leaves-the-vehicle' in \
        PAGE.read_text(encoding='utf-8')


def test_the_page_documents_what_it_does_not_do():
    """Two promises a reader has to be able to find: it never publishes, and it never
    stores a password. Both are properties of the code above; this is the check that
    they are also written down where somebody deciding whether to use it will look."""
    page = PAGE.read_text(encoding='utf-8')
    assert 'It never publishes.' in page
    assert 'It never stores a password.' in page
