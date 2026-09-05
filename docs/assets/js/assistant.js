/* The documentation assistant: instant grounded answers from a built-in index,
 * with an OPT-IN browser-local model on top. Ported from the KNetraHub docs
 * assistant and reshaped for a MkDocs Material site.
 *
 * The privacy stance mirrors the device's own: nothing typed here leaves the
 * browser. The instant tier is a keyword index over the paragraphs below and
 * needs no network at all; the Local AI tier - only after an explicit click -
 * downloads an open-weights model into the browser cache and runs it on
 * WebGPU (Llama 3.2 1B via WebLLM) or WebAssembly (SmolLM2 135M via
 * transformers.js). There is no server, no API key, and no telemetry.
 *
 * Two site rules this file follows on purpose:
 *   - No innerHTML anywhere, same as fleet.js: every node is built with
 *     createElement/textContent, so neither a question nor a model's output
 *     can inject markup into the page.
 *   - The fleet monitor page keeps its no-third-party-script guarantee: the
 *     widget never mounts there, so enabling Local AI can never cause a CDN
 *     import on the one page whose behaviour must stay pinned.
 *
 * Loaded on every page via extra_javascript (the one wiring that works with
 * python-markdown - see the note in mkdocs.yml), and the site is a SPA
 * (navigation.instant), so this runs once and follows page changes through
 * Material's document$ observable.
 */
(function () {
  'use strict';
  if (window.__dgAssistant) return;
  window.__dgAssistant = true;

  // ------------------------------------------------------------------ config
  var WEBLLM_URL = 'https://esm.run/@mlc-ai/web-llm@0.2.84';
  var WEBLLM_MODEL = 'Llama-3.2-1B-Instruct-q4f16_1-MLC';
  var WEBLLM_LABEL = 'Llama 3.2 1B · 4-bit';
  var TRANSFORMERS_URL = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1';
  var PORTABLE_MODEL = 'onnx-community/SmolLM2-135M-Instruct-ONNX';
  var PORTABLE_LABEL = 'SmolLM2 135M · 4-bit';
  var ENABLED_KEY = 'plxy.assistant.local-ai';

  // The site root, derived from this script's own URL so links work on GitHub
  // Pages (/PLXY_DrowsyGuard/), on a local preview, and anywhere in between.
  var BASE = (function () {
    var self = document.currentScript && document.currentScript.src;
    if (!self) {
      var scripts = document.getElementsByTagName('script');
      for (var i = 0; i < scripts.length; i++) {
        if (/assets\/js\/assistant\.js/.test(scripts[i].src || '')) { self = scripts[i].src; break; }
      }
    }
    return self ? self.replace(/assets\/js\/assistant\.js.*$/, '') : '/';
  })();

  // --------------------------------------------------------------- knowledge
  // One entry per topic a reader actually asks about, written from the pages
  // they link to. The instant tier quotes these; the Local AI tier gets the
  // best matches as grounding and is told not to answer beyond them.
  var KNOWLEDGE = [
    {
      id: 'overview',
      title: 'What DrowsyGuard is',
      page: '',
      keywords: 'overview what is drowsyguard project thesis driver drowsiness detector esp32 camera speaker alarm plxy',
      content: 'PLXY DrowsyGuard is a low-cost driver-drowsiness detector: an ESP32-S3 board with a camera watches the driver, fuses eye closure (PERCLOS), long blinks, yawns and head nods into a risk score, and wakes the driver with a spoken alert through a small speaker. It is headless - the preview and telemetry are served to a browser over the board’s own Wi-Fi access point - and it is a thesis project intended as a retrofit aid for older cars without built-in driver monitoring.'
    },
    {
      id: 'hardware',
      title: 'Hardware and parts',
      page: 'tutorials/hardware-setup/',
      keywords: 'hardware parts bom board buy camera speaker amplifier breadboard wiring solder ov3660 ov5640 max98357a n16r8 pins',
      content: 'The build is an ESP32-S3-WROOM-1 N16R8 CAM board (16 MB flash, 8 MB PSRAM) with a DVP camera, a MAX98357A I2S amplifier, a 4 ohm / 3 W speaker and an MB102 breadboard for the rails. The board is sold with an OV3660 but often ships an OV5640 - the boot log line "ov3660: Mismatch PID=0x5640" is expected and harmless. The hardware setup tutorial walks through wiring, soldering the amplifier header, and the first power-on checks.'
    },
    {
      id: 'install-browser',
      title: 'Install to the board from a browser',
      page: 'getting-started/install-esp32/',
      keywords: 'install flash firmware browser web serial chrome edge esp web tools no toolchain release download',
      content: 'The fastest install needs no toolchain: open the Install to ESP32 page in Chrome or Edge, plug the board in over USB, and the browser flasher (ESP Web Tools over Web Serial) writes the latest release. Firefox and Safari do not support Web Serial. If the board does not appear in the port picker, or the install fails part way, the same page and the Troubleshooting section cover the driver and boot-mode causes.'
    },
    {
      id: 'quickstart',
      title: 'Desktop quickstart',
      page: 'getting-started/quickstart/',
      keywords: 'quickstart desktop python pip install cli webcam live dashboard train dataset',
      content: 'The desktop toolkit runs the same detection logic against a webcam: install the package with pip, then "python -m drowsyguard.cli live" opens a local dashboard at 127.0.0.1:8000 for tuning thresholds before they are pasted into the firmware. Datasets, training and export have their own guide pages.'
    },
    {
      id: 'first-boot',
      title: 'First boot and the web preview',
      page: 'guide/device/',
      keywords: 'first boot chime access point ssid password join phone browser preview 192.168.4.1 web page face box',
      content: 'A healthy board plays three rising notes at power-on and starts an access point named DrowsyGuard-XXXXXX (the XXXXXX is from the board’s MAC; the password is drowsyguard). Join it from a phone or laptop and open http://192.168.4.1/ - the page shows the live camera preview with the face box, risk, PERCLOS, per-eye closure, event log, a mute switch and a speaker self-test. One live viewer streams at a time; extra viewers get still frames.'
    },
    {
      id: 'wifi',
      title: 'Wi-Fi provisioning',
      page: 'guide/device/',
      keywords: 'wifi provision join network scan forget station credentials password boot button hold clear reset access point',
      content: 'The board joins your own network from the device page: the Wi-Fi card scans, joins, shows state, IP and signal, and forgets. The access point never comes down - not during a scan, a join, or after a wrong password - so http://192.168.4.1/ is never the thing you lose. Holding the physical BOOT button for five seconds clears the stored Wi-Fi credentials and nothing else: MQTT settings, the device identity and the SD captures are untouched, and the board does not reboot.'
    },
    {
      id: 'mqtt',
      title: 'Fleet alerting over MQTT',
      page: 'guide/mqtt/',
      keywords: 'mqtt broker publish alert fleet telemetry configure transport tcp tls ws wss websocket qos lwt last will retain topic emqx offline buffer',
      content: 'Every confirmed alert can also be published to an MQTT broker as one versioned JSON document (drowsyguard.alert.v1). It is configured from the Configure MQTT modal on the device page: four transports (TCP, TLS, WebSocket, WSS), MQTT 3.1.1 or 5, QoS 0 or 1, an optional Last Will with a retained online/offline status, and a pasted CA certificate for a private broker. Publishing is off by default and buffered while offline (16 events, oldest dropped, every drop counted). The preconfigured broker.emqx.io is public and for demonstrations only - point a real vehicle at your own broker.'
    },
    {
      id: 'mqtt-trouble',
      title: 'MQTT troubleshooting',
      page: 'troubleshooting/#mqtt-and-the-fleet-monitor',
      keywords: 'mqtt not working connecting backoff error refused route save reboot crash state offline never leaves',
      content: 'If MQTT never leaves "connecting" or flips to "backoff", check in order: the board needs a route to the broker (its own access point has no internet - join it to your Wi-Fi first), the station side must actually hold an IP, the port must match the transport (EMQX: 1883 TCP, 8883 TLS, 8083 WS, 8084 WSS), and then read mqtt.error on the page. On firmware before 2026-09-05, saving the MQTT settings could reboot the board (a task-stack overflow) while still keeping the saved settings - update the firmware if you see that.'
    },
    {
      id: 'fleet',
      title: 'Fleet monitor dashboard',
      page: 'fleet-monitoring/',
      keywords: 'fleet monitor dashboard subscribe wss browser live status online offline devices map alerts watch',
      content: 'The documentation site carries a live fleet dashboard that subscribes to the alert and status topics over WebSocket directly from the browser - no server in between and no third-party script on the page. Point it at the same broker, fleet id and topic shape as the devices and it shows each device’s online state and its alerts as they arrive.'
    },
    {
      id: 'detection',
      title: 'How detection works',
      page: 'FIRMWARE_PIPELINE/',
      keywords: 'detection perclos blink yawn nod risk score fusion cue threshold baseline landmarks face track eye state model pipeline fps',
      content: 'Detection is multi-cue behaviour analysis, not whole-face classification: risk fuses PERCLOS (weight 0.55), long/slow blinks (0.20), yawning (0.15) and head nodding (0.10). Yawn, nod and roll are geometric, from five facial landmarks, and every cue is measured against a rolling per-driver baseline so anatomy and camera angle cannot become the signal. The face detector runs every third frame with tracking in between, the eye model runs one eye per frame, and the loop holds roughly 10-20 fps on the board.'
    },
    {
      id: 'eye-model',
      title: 'Eye model accuracy status',
      page: 'guide/training/',
      keywords: 'eye model accuracy ir infrared visible light auc fine tune daylight perclos wrong inaccurate open closed',
      content: 'The current eye-state model is IR-trained (open-closed-eye-0001) and does not transfer well to visible light - about 0.62 AUC on daylight eye crops against its claimed in-domain 95.84%. PERCLOS therefore moves but should not be read as accurate in daylight yet; the open task is fine-tuning on visible-light labels or pairing the camera with IR illumination. Detection logic, alerts and the rest of the pipeline are unaffected by this gap.'
    },
    {
      id: 'alerts',
      title: 'Spoken alerts and languages',
      page: 'VOICE_ALERT_HARDWARE/',
      keywords: 'alert voice speaker spoken clip language english khmer mute cooldown repeat buzzer chime audio i2s silent',
      content: 'Alerts name their cause - Drowsy, Microsleep, Yawning, Head nod, No driver - in English or Khmer, through the I2S speaker. Each channel has its own cooldown and a per-episode repeat cap that resets after five minutes of quiet, so a long drive cannot silence the alarm permanently. A buzzer fallback covers a missing speaker, and the page has a mute switch and a speaker self-test.'
    },
    {
      id: 'api',
      title: 'Device HTTP API',
      page: 'reference/device-api/',
      keywords: 'api http endpoint status snapshot events settings curl json scriptable rest',
      content: 'Everything the page shows is scriptable: GET /api/status returns the live numbers (fps, risk, PERCLOS, face box, presence, MQTT counters), /api/snapshot returns the exact frame the detector saw, /api/events lists the captures on the SD card, and /api/mqtt and /api/wifi read and write those settings with passwords never echoed back. The API reference documents every endpoint and field.'
    },
    {
      id: 'flash-trouble',
      title: 'Flashing and boot troubleshooting',
      page: 'troubleshooting/',
      keywords: 'wrong boot mode 0x28 waiting for download monitor silent dead serial port com bridge reset lines inverted mismatch pid camera fault psram',
      content: 'The board’s auto-reset lines are inverted relative to esptool’s convention, so "Wrong boot mode detected (0x28)" and a monitor showing only "waiting for download" are known quirks with documented fixes (./plxy.sh flash handles it; the manual route is hold BOOT, tap RESET). "ov3660: Mismatch PID=0x5640" at boot is expected - the board ships an OV5640. And note: a serial monitor attached while the app runs can read as a held BOOT button and, after five seconds, clear the saved Wi-Fi - reset-then-read is the safe way to capture logs.'
    },
    {
      id: 'security',
      title: 'Security and privacy',
      page: 'security/',
      keywords: 'security privacy image photo leave device publish password secret data driver name remark public broker safe',
      content: 'No image ever leaves the device over MQTT - captures stay on the SD card, and there is no code path that publishes a frame. Publishing is off by default because turning it on sends a named driver’s alertness state to a third party. Passwords are never echoed back by any API, the driver remark travels in the payload rather than in a topic, and the device publishes only - it takes no commands over MQTT, so a broker cannot mute the alarm.'
    },
    {
      id: 'deployment',
      title: 'Acceptance tests and measurements',
      page: 'DEPLOYMENT/',
      keywords: 'acceptance test deployment measure fps m1 m3 m9 w4 verification hardware gate checklist pass criteria',
      content: 'DEPLOYMENT.md carries the acceptance tests with pass criteria that do not depend on judgement: M1-M13 for the MQTT path (connection, isolation of fps from a dead broker, queue overflow, reconnect flush, soak) and W1-W11 for Wi-Fi provisioning (a wrong password must leave 192.168.4.1 serving; the BOOT hold clears Wi-Fi and nothing else). It also records the measured resource and frame-rate figures per release.'
    },
    {
      id: 'about',
      title: 'About the project',
      page: '',
      keywords: 'author who made founder seng phirum thesis contact license cambodia',
      content: 'DrowsyGuard is built by Seng Phirum as a thesis project - a Phnom Penh-based banking professional and hands-on technologist. The documentation, firmware, desktop toolkit and fleet dashboard live in one repository, linked from the GitHub icon in the header.'
    }
  ];

  var STOP_WORDS = { a: 1, an: 1, and: 1, are: 1, can: 1, do: 1, for: 1, from: 1, how: 1, i: 1, in: 1, is: 1, it: 1, me: 1, my: 1, of: 1, on: 1, or: 1, please: 1, the: 1, this: 1, to: 1, what: 1, when: 1, where: 1, with: 1, you: 1 };

  function terms(value) {
    return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').split(/\s+/)
      .filter(function (t) { return t.length > 1 && !STOP_WORDS[t]; });
  }

  function retrieve(query, limit) {
    var queryTerms = terms(query);
    var path = location.pathname;
    return KNOWLEDGE.map(function (entry) {
      var haystack = (entry.title + ' ' + entry.keywords + ' ' + entry.content).toLowerCase();
      var score = 0;
      for (var i = 0; i < queryTerms.length; i++) {
        if (haystack.indexOf(queryTerms[i]) !== -1) {
          score += entry.keywords.indexOf(queryTerms[i]) !== -1 ? 3 : 1;
        }
      }
      if (entry.page && path.indexOf(entry.page.split('#')[0]) !== -1) score += 2;
      return { entry: entry, score: score };
    }).sort(function (a, b) { return b.score - a.score; })
      .filter(function (item, index) { return item.score > 0 || index === 0; })
      .slice(0, limit || 4)
      .map(function (item) { return item.entry; });
  }

  function instantReply(query, entries) {
    if (/^(hi|hello|hey|help)[!. ]*$/i.test(query.trim())) {
      return { text: 'Hi — I answer questions about DrowsyGuard from the documentation: building the hardware, installing the firmware, using the device page, MQTT fleet alerts, and troubleshooting. Ask away, or enable Local AI below for generated answers that never leave this browser.', links: [] };
    }
    var primary = entries[0] || KNOWLEDGE[0];
    var links = [];
    entries.slice(0, 2).forEach(function (entry) {
      if (entry.page) links.push({ label: entry.title, href: BASE + entry.page });
    });
    return {
      text: primary.content + '\n\nThis instant answer is quoted from the built-in index. Enable Local AI for a generated explanation or a follow-up.',
      links: links
    };
  }

  function buildSystemPrompt(entries) {
    var knowledge = entries.map(function (e) { return '### ' + e.title + '\n' + e.content; }).join('\n\n');
    return 'You are the DrowsyGuard documentation assistant, running privately in the reader’s browser. DrowsyGuard is an ESP32-S3 driver-drowsiness detector; help readers build it, install the firmware, use the device page, configure Wi-Fi and MQTT fleet alerting, and troubleshoot. Base every product claim on the supplied knowledge; if the answer is not there, say so and point to the closest documentation page instead of guessing. Never invent device state, log lines, commands or measurements, and never ask for passwords or credentials. This is a safety device: do not advise disabling alerts, and note that the detector aids an attentive driver rather than replacing one. Keep answers short and concrete.\n\nDrowsyGuard knowledge:\n' + knowledge;
  }

  // ---------------------------------------------------------------- runtime
  var runtime = null;        // { label, generate(messages, onUpdate) }
  var runtimeLoading = null; // in-flight promise
  var ui = {};               // node handles, filled by mount()

  function detect() {
    var webgpu = Boolean(navigator.gpu && window.isSecureContext);
    var wasm = typeof WebAssembly === 'object' && typeof WebAssembly.instantiate === 'function';
    var iPadDesktop = navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1;
    var mobile = (navigator.userAgentData && navigator.userAgentData.mobile) ||
      iPadDesktop || /Android|iPhone|iPad|iPod|Mobile|Silk|Kindle/i.test(navigator.userAgent);
    if (webgpu && !mobile) return 'webllm';
    if (webgpu) return 'transformers-webgpu';
    if (wasm) return 'transformers-wasm';
    return 'instant';
  }

  function remember(value) {
    try { localStorage.setItem(ENABLED_KEY, value ? 'true' : 'false'); } catch (e) { /* private mode */ }
  }
  function wasEnabled() {
    try { return localStorage.getItem(ENABLED_KEY) === 'true'; } catch (e) { return false; }
  }

  function loadWebLLM(onProgress) {
    return import(WEBLLM_URL).then(function (webllm) {
      return webllm.CreateMLCEngine(WEBLLM_MODEL, {
        initProgressCallback: function (report) {
          onProgress(Number(report.progress || 0), report.text || 'Loading the accelerated model…');
        }
      }).then(function (engine) {
        return {
          label: WEBLLM_LABEL,
          generate: function (messages, onUpdate) {
            var answer = '';
            return engine.chat.completions.create({
              messages: messages, stream: true, temperature: 0.35, top_p: 0.9,
              max_tokens: 420, repetition_penalty: 1.05
            }).then(function (stream) {
              function pump() {
                return stream.next().then(function (step) {
                  if (step.done) return answer;
                  var chunk = step.value;
                  answer += (chunk && chunk.choices && chunk.choices[0] &&
                             chunk.choices[0].delta && chunk.choices[0].delta.content) || '';
                  if (onUpdate) onUpdate(answer);
                  return pump();
                });
              }
              return typeof stream.next === 'function' ? pump() : (function () {
                // for-await fallback for engines returning an async iterable
                var it = stream[Symbol.asyncIterator]();
                function walk() {
                  return it.next().then(function (step) {
                    if (step.done) return answer;
                    var chunk = step.value;
                    answer += (chunk && chunk.choices && chunk.choices[0] &&
                               chunk.choices[0].delta && chunk.choices[0].delta.content) || '';
                    if (onUpdate) onUpdate(answer);
                    return walk();
                  });
                }
                return walk();
              })();
            });
          }
        };
      });
    });
  }

  function loadPortable(device, onProgress) {
    return import(TRANSFORMERS_URL).then(function (mod) {
      var env = mod.env, pipeline = mod.pipeline, TextStreamer = mod.TextStreamer;
      env.allowRemoteModels = true;
      env.useBrowserCache = true;
      if (device === 'wasm' && env.backends && env.backends.onnx && env.backends.onnx.wasm) {
        env.backends.onnx.wasm.proxy = typeof Worker === 'function';
        env.backends.onnx.wasm.numThreads = globalThis.crossOriginIsolated
          ? Math.max(1, Math.min(4, navigator.hardwareConcurrency || 1)) : 1;
      }
      return pipeline('text-generation', PORTABLE_MODEL, {
        device: device,
        dtype: device === 'webgpu' ? 'q4f16' : 'q4',
        progress_callback: function (report) {
          if (report.status === 'progress' || report.status === 'progress_total') {
            var value = Math.max(0, Math.min(100, Number(report.progress || 0))) / 100;
            var file = report.file ? report.file.split('/').pop() : '';
            onProgress(value, file ? 'Downloading ' + file + '…' : 'Downloading the portable model…');
          } else if (report.status === 'ready') {
            onProgress(1, 'Starting the portable model…');
          }
        }
      }).then(function (generator) {
        return {
          label: PORTABLE_LABEL,
          generate: function (messages, onUpdate) {
            var answer = '';
            var streamer = new TextStreamer(generator.tokenizer, {
              skip_prompt: true, skip_special_tokens: true,
              callback_function: function (piece) { answer += piece; if (onUpdate) onUpdate(answer); }
            });
            return generator(messages, {
              max_new_tokens: 220, do_sample: true, temperature: 0.2, top_p: 0.9,
              repetition_penalty: 1.05, streamer: streamer
            }).then(function (output) {
              if (answer.trim()) return answer;
              var generated = output && output[0] && output[0].generated_text;
              if (Array.isArray(generated)) {
                var last = generated[generated.length - 1];
                return String((last && last.content) || '');
              }
              return String(generated || '');
            });
          }
        };
      });
    });
  }

  function loadRuntime(onProgress) {
    if (runtime) return Promise.resolve(runtime);
    if (runtimeLoading) return runtimeLoading;
    var preferred = detect();
    var wasm = typeof WebAssembly === 'object';
    var load;
    if (preferred === 'webllm') {
      load = loadWebLLM(onProgress).catch(function (gpuError) {
        if (!wasm) throw gpuError;
        onProgress(0, 'The GPU model did not start; switching to compatible CPU mode…');
        return loadPortable('wasm', onProgress);
      });
    } else if (preferred === 'transformers-webgpu') {
      load = loadPortable('webgpu', onProgress).catch(function (gpuError) {
        if (!wasm) throw gpuError;
        onProgress(0, 'GPU acceleration was unavailable; switching to compatible CPU mode…');
        return loadPortable('wasm', onProgress);
      });
    } else if (preferred === 'transformers-wasm') {
      load = loadPortable('wasm', onProgress);
    } else {
      load = Promise.reject(new Error('This browser has neither WebGPU nor WebAssembly, so a local model cannot start. Instant answers still work.'));
    }
    runtimeLoading = load.then(function (loaded) {
      runtime = loaded;
      runtimeLoading = null;
      return loaded;
    }, function (cause) {
      runtimeLoading = null;
      throw cause;
    });
    return runtimeLoading;
  }

  function friendlyError(cause) {
    var message = String((cause && cause.message) || cause || '');
    if (/memory|allocation|device lost|out of|array buffer/i.test(message)) {
      return 'This device could not reserve enough memory for the local model. Close other tabs and try again, or keep using instant answers.';
    }
    if (/network|fetch|cors|load failed|failed to fetch/i.test(message)) {
      return 'The model files could not be downloaded. Check the network and try again; anything already cached stays on this device.';
    }
    if (/wasm|webassembly/i.test(message)) {
      return 'This browser could not start its WebAssembly runtime. Update the browser, or keep using instant answers.';
    }
    return message || 'The local model could not start. Instant answers still work.';
  }

  // -------------------------------------------------------------------- UI
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  // Renders assistant text as plain paragraphs. Model output and user input
  // are DATA here - never markup - which is what makes a prompt-injected
  // <script> in a generated answer land as harmless text.
  function renderText(container, text) {
    while (container.firstChild) container.removeChild(container.firstChild);
    String(text).split(/\n{2,}/).forEach(function (para) {
      if (para.trim()) container.appendChild(el('p', '', para.trim()));
    });
  }

  function addMessage(role, text, links) {
    var row = el('div', 'dga-msg dga-msg-' + role);
    var body = el('div', 'dga-msg-body');
    renderText(body, text);
    row.appendChild(body);
    if (links && links.length) {
      var nav = el('div', 'dga-msg-links');
      links.forEach(function (link) {
        var a = el('a', 'dga-link', link.label);
        a.href = link.href;
        nav.appendChild(a);
      });
      row.appendChild(nav);
    }
    ui.log.appendChild(row);
    ui.log.scrollTop = ui.log.scrollHeight;
    return body;
  }

  function setStatus(text) { ui.status.textContent = text; }
  function setProgress(fraction, text) {
    ui.progressWrap.hidden = fraction >= 1 || fraction <= 0 ? !text : false;
    ui.progressBar.style.width = Math.round(Math.max(0, Math.min(1, fraction)) * 100) + '%';
    if (text) setStatus(text);
  }

  var history = [];
  var busy = false;

  function enableLocalAi() {
    remember(true);
    ui.enableBtn.disabled = true;
    ui.enableBtn.textContent = 'Loading…';
    setProgress(0.01, 'Preparing the private runtime…');
    loadRuntime(setProgress).then(function (loaded) {
      setProgress(1, loaded.label + ' ready — answers are generated on this device');
      ui.progressWrap.hidden = true;
      ui.enableRow.hidden = true;
      ui.badge.textContent = loaded.label;
      ui.badge.hidden = false;
    }, function (cause) {
      remember(false);
      ui.enableBtn.disabled = false;
      ui.enableBtn.textContent = 'Enable Local AI';
      ui.progressWrap.hidden = true;
      setStatus('');
      addMessage('assistant', friendlyError(cause), []);
    });
  }

  function ask(query) {
    if (busy || !query.trim()) return;
    busy = true;
    ui.input.value = '';
    addMessage('user', query, []);
    var entries = retrieve(query, 4);

    if (!runtime) {
      var reply = instantReply(query, entries);
      addMessage('assistant', reply.text, reply.links);
      busy = false;
      return;
    }

    var links = [];
    entries.slice(0, 2).forEach(function (entry) {
      if (entry.page) links.push({ label: entry.title, href: BASE + entry.page });
    });
    var body = addMessage('assistant', '…', links);
    setStatus('Generating on this device…');
    var messages = [{ role: 'system', content: buildSystemPrompt(entries) }]
      .concat(history.slice(-6))
      .concat([{ role: 'user', content: query }]);
    runtime.generate(messages, function (partial) {
      renderText(body, partial);
      ui.log.scrollTop = ui.log.scrollHeight;
    }).then(function (answer) {
      renderText(body, answer || 'The model returned nothing; the linked pages are the grounded answer.');
      history.push({ role: 'user', content: query }, { role: 'assistant', content: answer });
      setStatus('');
      busy = false;
    }, function (cause) {
      renderText(body, friendlyError(cause));
      setStatus('');
      busy = false;
    });
  }

  var SUGGESTIONS = [
    'How do I install the firmware from a browser?',
    'Set up MQTT fleet alerts',
    'The log says Mismatch PID=0x5640',
    'Why did my saved Wi-Fi disappear?'
  ];

  function mount() {
    var launcher = el('button', 'dga-launcher', '');
    launcher.type = 'button';
    launcher.setAttribute('aria-label', 'Ask the documentation assistant');
    launcher.appendChild(el('span', 'dga-launcher-icon', '✦'));
    launcher.appendChild(el('span', 'dga-launcher-text', 'Ask the docs'));

    var panel = el('section', 'dga-panel');
    panel.hidden = true;
    panel.setAttribute('aria-label', 'DrowsyGuard documentation assistant');

    var header = el('div', 'dga-header');
    var titleWrap = el('div', 'dga-title-wrap');
    titleWrap.appendChild(el('strong', 'dga-title', 'DrowsyGuard assistant'));
    ui.badge = el('span', 'dga-badge', '');
    ui.badge.hidden = true;
    titleWrap.appendChild(ui.badge);
    header.appendChild(titleWrap);
    var close = el('button', 'dga-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close the assistant');
    header.appendChild(close);
    panel.appendChild(header);

    panel.appendChild(el('p', 'dga-privacy',
      'Runs entirely in this browser — questions and answers never leave this device.'));

    ui.log = el('div', 'dga-log');
    panel.appendChild(ui.log);

    var chips = el('div', 'dga-chips');
    SUGGESTIONS.forEach(function (text) {
      var chip = el('button', 'dga-chip', text);
      chip.type = 'button';
      chip.addEventListener('click', function () { ask(text); });
      chips.appendChild(chip);
    });
    panel.appendChild(chips);

    ui.progressWrap = el('div', 'dga-progress');
    ui.progressWrap.hidden = true;
    ui.progressBar = el('div', 'dga-progress-bar');
    ui.progressWrap.appendChild(ui.progressBar);
    panel.appendChild(ui.progressWrap);
    ui.status = el('div', 'dga-status', '');
    panel.appendChild(ui.status);

    ui.enableRow = el('div', 'dga-enable');
    ui.enableBtn = el('button', 'dga-enable-btn', 'Enable Local AI');
    ui.enableBtn.type = 'button';
    ui.enableRow.appendChild(ui.enableBtn);
    ui.enableRow.appendChild(el('span', 'dga-enable-note',
      detect() === 'webllm'
        ? 'Downloads Llama 3.2 1B (~880 MB, cached) and runs it on this device’s GPU.'
        : detect() === 'instant'
          ? 'This browser cannot run a local model; instant answers still work.'
          : 'Downloads SmolLM2 135M (~184 MB, cached) and runs it on this device.'));
    if (detect() === 'instant') ui.enableBtn.disabled = true;
    panel.appendChild(ui.enableRow);

    var form = el('form', 'dga-form');
    ui.input = el('input', 'dga-input');
    ui.input.type = 'text';
    ui.input.placeholder = 'Ask about DrowsyGuard…';
    ui.input.setAttribute('aria-label', 'Question for the documentation assistant');
    var send = el('button', 'dga-send', 'Send');
    send.type = 'submit';
    form.appendChild(ui.input);
    form.appendChild(send);
    panel.appendChild(form);

    document.body.appendChild(launcher);
    document.body.appendChild(panel);

    var opened = false;
    launcher.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
      if (!panel.hidden && !opened) {
        opened = true;
        addMessage('assistant',
          'Ask me anything covered by this documentation — hardware, install, the device page, MQTT, troubleshooting. Instant answers are quoted from a built-in index; Local AI generates them on this device.',
          []);
        // A previously enabled model is only re-loaded once the reader opens
        // the panel: weights come from the browser cache, but decompressing
        // them still costs seconds nobody asked for on a page they are reading.
        if (wasEnabled() && !runtime && detect() !== 'instant') enableLocalAi();
      }
      if (!panel.hidden) ui.input.focus();
    });
    close.addEventListener('click', function () { panel.hidden = true; });
    ui.enableBtn.addEventListener('click', enableLocalAi);
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      ask(ui.input.value);
    });

    // The fleet monitor page keeps its no-third-party-script guarantee, so the
    // assistant absents itself there. Material's instant navigation swaps page
    // content without reloading scripts; document$ fires on every page change.
    function syncVisibility() {
      var onFleet = Boolean(document.getElementById('fleet-app'));
      launcher.hidden = onFleet;
      if (onFleet) panel.hidden = true;
    }
    syncVisibility();
    if (window.document$ && typeof window.document$.subscribe === 'function') {
      window.document$.subscribe(syncVisibility);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
