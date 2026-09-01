/*
Wiring for the browser installer on getting-started/install-esp32.md.

Three jobs, and the third is the one worth explaining.

1. Point <esp-web-install-button> at the manifest. The path is computed rather than
   written into the page because MkDocs serves the same document at a different depth
   depending on whether directory URLs are on, and a relative path that is right in
   one build is wrong in the other. Resolving against the site root - which is
   whatever precedes /getting-started/ - is correct in both.

2. Show what the build actually is: version, size, checksum, build date. A page that
   offers to overwrite the firmware on someone's hardware should say what it is about
   to write, and the checksum is what makes the download verifiable against the
   release.

3. Say clearly when there is nothing to install. The failure this avoids: with no
   manifest published, esp-web-install-button renders its activate slot anyway, the
   visitor clicks it, picks a port, and gets an opaque error from deep inside the
   flashing library. So the button stays hidden until a manifest has actually been
   fetched and parsed, and the status line says which of the three things went wrong -
   no build published, a build whose manifest is malformed, or a browser that cannot
   do this at all.

Deliberately no build step and no framework: this ships as one static file next to a
Markdown page, and anything else would put a bundler between a documentation edit and
the published site.
*/
(function () {
  'use strict';

  var status = document.getElementById('dg-status');
  var button = document.getElementById('dg-button');
  var table = document.getElementById('dg-build');
  if (!status || !button || !table) return;   // not this page

  function say(text, kind) {
    status.textContent = text;
    status.className = 'dg-status' + (kind ? ' dg-' + kind : '');
  }

  // The site root, whatever depth this page is served at.
  function firmwareBase() {
    var path = window.location.pathname;
    var cut = path.indexOf('/getting-started/');
    var root = cut >= 0 ? path.slice(0, cut + 1) : '/';
    return root + 'firmware/';
  }

  function kb(bytes) {
    return (bytes / 1024).toFixed(0) + ' kB';
  }

  function row(label, value, mono) {
    var tr = document.createElement('tr');
    var th = document.createElement('td');
    th.textContent = label;
    var td = document.createElement('td');
    if (mono) {
      var code = document.createElement('code');
      code.textContent = value;
      td.appendChild(code);
    } else {
      td.textContent = value;
    }
    tr.appendChild(th);
    tr.appendChild(td);
    return tr;
  }

  var base = firmwareBase();

  // Web Serial is the hard requirement, and it is worth checking before the fetch:
  // telling someone on Firefox that no build is published would send them looking
  // for the wrong problem.
  var serial = 'serial' in navigator;

  fetch(base + 'manifest.json', { cache: 'no-store' })
    .then(function (r) {
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    })
    .then(function (manifest) {
      var build = (manifest.builds || [])[0];
      if (!build || !build.parts || !build.parts.length) {
        throw new Error('the manifest names no images');
      }
      button.setAttribute('manifest', base + 'manifest.json');
      button.hidden = false;

      if (serial) {
        say('Ready to install ' + (manifest.name || 'firmware') + ' ' +
            (manifest.version || '') + ' to an ' + build.chipFamily + '.', 'ok');
      } else {
        // The custom element renders its own "unsupported" slot, so this only has
        // to explain rather than block.
        say('This browser does not support Web Serial, so the button below will '
            + 'explain rather than flash. Chrome or Edge 89+ can install directly.',
            'warn');
      }

      return fetch(base + 'flash-offsets.json', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (offsets) { describe(manifest, build, offsets); })
        .catch(function () { describe(manifest, build, null); });
    })
    .catch(function (err) {
      button.hidden = true;
      if (!serial) {
        say('This browser cannot flash over USB - Web Serial is Chrome and Edge '
            + 'only. See "Flash it yourself instead" below.', 'warn');
        return;
      }
      say('No published build was found (' + err.message + '). The release workflow '
          + 'may not have run yet - see "Flash it yourself instead" below.', 'warn');
    });

  function describe(manifest, build, offsets) {
    var body = table.querySelector('tbody');
    body.textContent = '';
    body.appendChild(row('Version', manifest.version || 'unknown', true));
    body.appendChild(row('Target', build.chipFamily));

    var art = offsets && offsets.artifacts && offsets.artifacts[0];
    if (art) {
      body.appendChild(row('Image', art.file + ' at ' + art.offset, true));
      body.appendChild(row('Size', kb(art.bytes)));
      body.appendChild(row('SHA-256', art.sha256, true));
    } else {
      var part = build.parts[0];
      body.appendChild(row('Image', part.path + ' at 0x' + part.offset.toString(16),
                           true));
    }
    if (offsets && offsets.built_utc) {
      body.appendChild(row('Built', offsets.built_utc));
    }
    if (offsets && offsets.flash_settings) {
      var f = offsets.flash_settings;
      body.appendChild(row('Flash', [f.flash_size, f.flash_mode, f.flash_freq]
                                      .filter(Boolean).join(' · '), true));
    }
    table.hidden = false;
  }
})();
