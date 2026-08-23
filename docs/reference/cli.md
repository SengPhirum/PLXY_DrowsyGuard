---
title: Command line
---

# Command line reference

Two entry points: `drowsyguard` for the desktop toolkit, `./plxy.sh` for the
board and the project chores.

## `drowsyguard`

Installed by `pip install -e .`. On Windows prefer
`python -m drowsyguard.cli <cmd>` — the console-script launcher can throttle
webcam capture.

### `doctor`

```bash
drowsyguard doctor
```

Prints the Python version and platform, then `OK`/`MISSING` for `torch`, `onnx`,
`onnxruntime`, `yaml` and `PIL`, then `esp_ppq` (optional — only for `.espdl`
quantization) and the live-UI dependencies (`cv2`, `fastapi`, `uvicorn`).

### `prepare`

```bash
drowsyguard prepare --input data/raw --output data/processed [flags]
```

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `--input` | path | *required* | subject-based raw layout |
| `--output` | path | *required* | destination for the split |
| `--train` | float | `0.70` | fraction of subjects in train |
| `--val` | float | `0.15` | fraction of subjects in val (rest is test) |
| `--seed` | int | `42` | split seed |
| `--stride` | int | `1` | keep every Nth frame when a class dir holds videos |
| `--link` | flag | off | hardlink instead of copy where possible |
| `--overwrite` | flag | off | replace an existing split (required to re-split) |

### `import-ddd`

```bash
drowsyguard import-ddd --input "Driver Drowsiness Dataset (DDD)" [--output data/raw] [--copy]
```

Rebuilds the flat DDD class folders into the subject layout. Hardlinks by
default; `--copy` duplicates the bytes.

### `train`

```bash
drowsyguard train [--config configs/train.yaml]
```

Every hyperparameter comes from the config. See
[Configuration](../configuration/index.md#training-configuration).

### `evaluate`

```bash
drowsyguard evaluate [--config configs/train.yaml] [--checkpoint models/best.pt] [flags]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--per-subject` | off | break accuracy down by driver |
| `--split {train,val,test}` | all | evaluate one split |

### `export-onnx`

```bash
drowsyguard export-onnx [--config configs/train.yaml] [--checkpoint models/best.pt]
```

Writes the path named by `onnx_output` in the config.

### `quantize-espdl`

```bash
drowsyguard quantize-espdl --onnx models/drowsyguard.onnx \
                           --calib data/processed/train \
                           --output models/drowsyguard.espdl
```

All three flags are required. Needs `esp-ppq`.

### `fetch-models`

```bash
drowsyguard fetch-models [--output DIR]
```

Downloads the YuNet face detector and `open-closed-eye-0001`, and prints where
each landed.

### `camera-test`

```bash
drowsyguard camera-test [--index 0] [--frames 20]
```

Benchmarks each capture backend and prints `backend  thread  fps`.

### `live`

```bash
python -m drowsyguard.cli live [flags]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--source` | `0` | webcam index or path to a video file |
| `--mode {eye,face}` | `eye` | `eye` measures eyelid closure + PERCLOS (no checkpoint needed); `face` runs the whole-face CNN and needs `--checkpoint` |
| `--perclos-window` | `90` | frames in the PERCLOS window (~3 s at 30 fps) |
| `--eye-closed-threshold` | `0.5` | P(closed) above which an eye counts as shut |
| `--eye-model` | downloaded | path to an eye-state model |
| `--checkpoint` | none | face mode only: `.pt` or `.onnx` |
| `--config` | `configs/train.yaml` | preprocessing settings for face mode |
| `--host` | `127.0.0.1` | **see [Security](../security.md)** before changing |
| `--port` | `8000` | |
| `--trigger` | from `risk.py` | risk level that starts a streak |
| `--required` | from `risk.py` | consecutive frames required to alert |
| `--cooldown` | from `risk.py` | frames held off after an alert |
| `--zoom` | `1.0` | centre-crop fraction, used only when no face is detected |
| `--no-face-detect` | off | disable detection and tracking |
| `--face-margin` | `0.0` | expand the detected box; `0` matches DDD framing |
| `--face-model` | downloaded | path to a YuNet model |

## `./plxy.sh`

Run `./plxy.sh help` for the same list in the terminal.

### Firmware

| Command | Does |
| --- | --- |
| `dev` | build, flash and monitor |
| `build` | compile only |
| `flash` | flash without opening the monitor |
| `monitor` (`mon`) | serial console; <kbd>Ctrl</kbd>+<kbd>]</kbd> exits |
| `reconfigure` | re-resolve managed components |
| `menuconfig` | ESP-IDF config UI |
| `size` | binary size breakdown |
| `clean` | drop build artefacts |
| `fullclean` | also drop `sdkconfig` |
| `erase` | erase the whole flash — wipes NVS and Wi-Fi calibration |

### Device

| Command | Does |
| --- | --- |
| `port` (`ports`) | list serial ports, say which will be used |
| `wifi` | print SSID, password and URL |
| `open` | open the preview in a browser |
| `status` | pretty-print `GET /api/status` |
| `watch` | poll the risk/PERCLOS line once a second |
| `snapshot [file]` (`snap`) | save one JPEG (default `snapshot.jpg`) |
| `alert [reason]` | play `drowsy`, `microsleep`, `yawn` or `nod` |
| `mute` / `unmute` | silence or restore the speaker |

### Project

| Command | Does |
| --- | --- |
| `test` | `pytest tests/` |
| `diagrams` | regenerate tutorial figures and the wiring poster |
| `doctor` | toolchain, port and device check |
| `help` | the full list |

### Documentation

| Command | Does |
| --- | --- |
| `docs-check` | validate the docs — strict build, link/anchor check, secret scan |
| `docs-build` | build the static site into `site/` |
| `docs-preview [port]` | serve the docs with hot reload on `http://127.0.0.1:8001/` |
| `docs-deploy` | publish to GitHub Pages |
| `docs-clean` | remove `site/` |

None of these touch the firmware, ESP-IDF or the training environment — see
[Documentation pipeline](../operations/documentation.md).

### Environment

| Variable | Effect |
| --- | --- |
| `PLXY_PORT` | force a serial port |
| `PLXY_HOST` | talk to the board over station mode |
| `PLXY_DOCS_PYTHON`, `PLXY_DOCS_VENV`, `PLXY_DOCS_PORT`, `PLXY_DOCS_NO_VENV`, `PLXY_DOCS_SITE_URL` | see [Configuration](../configuration/index.md#environment-variables) |
