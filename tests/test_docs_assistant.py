"""The documentation assistant, held to the same rules as the fleet monitor.

`docs/assets/js/assistant.js` is loaded on every page of the site, renders text a
language model generated, and can import third-party modules after an opt-in click.
Each of those is safe only because of a property this file pins:

  * no `innerHTML`, anywhere - model output and user questions are data;
  * every stylesheet selector is namespaced, because the CSS also loads site-wide;
  * it never mounts on the fleet monitor page, whose documented guarantee is that
    no third-party script can change what it does;
  * every documentation link inside the knowledge index points at a page that
    exists - mkdocs --strict validates markdown links, but a path inside a JS
    string is invisible to it, and the assistant offering a 404 would be worse
    than offering nothing.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'docs/assets/js/assistant.js'
STYLE = ROOT / 'docs/stylesheets/assistant.css'
MKDOCS = ROOT / 'mkdocs.yml'
DOCS = ROOT / 'docs'


def _code_only(script: str) -> str:
    """The script's own comments are allowed to *say* innerHTML; the code is not
    allowed to use it. Good enough here: the file contains no string literal or
    regex whose text looks like a comment marker."""
    script = re.sub(r'/\*.*?\*/', '', script, flags=re.S)
    return re.sub(r'^\s*//.*$', '', script, flags=re.M)


def test_the_script_never_uses_innerhtml():
    code = _code_only(SCRIPT.read_text(encoding='utf-8'))
    assert 'innerHTML' not in code, (
        'assistant.js renders generated text; it must build DOM via textContent only')


def test_the_script_is_wired_into_the_site_the_only_way_that_works():
    conf = MKDOCS.read_text(encoding='utf-8')
    assert 'assets/js/assistant.js' in conf, 'assistant.js is not in extra_javascript'
    assert 'stylesheets/assistant.css' in conf, 'assistant.css is not in extra_css'


def test_the_assistant_absents_itself_from_the_fleet_page():
    """The fleet page's guarantee is that no third-party script can change what it
    does. An assistant that can import a CDN module on click breaks that guarantee
    by existing there, so it must check for the fleet app and leave - including on
    navigation.instant page changes, which swap content without reloading scripts."""
    script = SCRIPT.read_text(encoding='utf-8')
    assert "getElementById('fleet-app')" in script
    assert 'document$' in script, (
        'the site is a SPA; the fleet check must re-run on instant page changes')


def test_nothing_external_loads_before_the_opt_in():
    """The only import() calls are inside the two runtime loaders, which run only
    after Enable Local AI. A top-level import or script injection would download
    third-party code on every page view."""
    script = SCRIPT.read_text(encoding='utf-8')
    assert 'createElement(\'script\')' not in script.replace('"', "'")
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith('import '):
            raise AssertionError(f'top-level import found: {stripped}')


def test_the_stylesheet_cannot_restyle_the_rest_of_the_site():
    css = STYLE.read_text(encoding='utf-8')
    body = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    for block in re.findall(r'([^{}]+)\{', body):
        for sel in block.split(','):
            sel = sel.strip()
            if not sel or sel.startswith('@'):
                continue
            assert '.dga-' in sel or '#dga-' in sel, (
                f'selector {sel!r} is not namespaced to the assistant')


def test_every_knowledge_link_points_at_a_real_page():
    script = SCRIPT.read_text(encoding='utf-8')
    pages = re.findall(r"page:\s*'([^']*)'", script)
    assert pages, 'the knowledge index has no page fields; the regex has rotted'
    for page in pages:
        if page == '':
            continue                       # the site root
        path, _, anchor = page.partition('#')
        path = path.strip('/')
        candidates = [DOCS / f'{path}.md', DOCS / path / 'index.md',
                      DOCS / path / 'README.md']
        target = next((c for c in candidates if c.is_file()), None)
        assert target is not None, f'knowledge entry links to a missing page: {page!r}'
        if anchor:
            text = target.read_text(encoding='utf-8')
            assert (f'#{anchor}' in text or f'id="{anchor}"' in text), (
                f'anchor #{anchor} is not declared in {target.name}')


def test_the_privacy_claims_are_load_bearing():
    """The page promises "questions and answers never leave this device". That is
    only true while the script has no fetch/XHR/beacon of its own - the model CDN
    imports are the single sanctioned network path, and they carry no user text."""
    script = SCRIPT.read_text(encoding='utf-8')
    for banned in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'WebSocket('):
        assert banned not in script, f'assistant.js performs its own network I/O: {banned}'
