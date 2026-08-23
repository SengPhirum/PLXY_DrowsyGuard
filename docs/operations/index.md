---
title: Operations
---

# Operations

| Page | Covers |
| --- | --- |
| [Deployment](../DEPLOYMENT.md) | version policy, the firmware pipeline, and the hardware acceptance tests |
| [On-device pipeline](../FIRMWARE_PIPELINE.md) | the frame budget, the detection gate, why not an ESP32-S2, and what the web interface replaced |
| [Documentation pipeline](documentation.md) | the docs-only build, local preview, validation and automated GitHub Pages deployment |
| [Handoff protocol](../AI_HANDOFF.md) | the rules any contributor — human or model — works under |

## Two build modes, independent by design

```text
Normal build                        Docs-only build
└── ./plxy.sh build                 └── ./plxy.sh docs-build
    ESP-IDF, PowerShell, 16 MB          mkdocs-material and nothing else
    firmware, a board attached          seconds, no toolchain, no hardware
```

Neither triggers the other. A documentation change never queues behind a
firmware build, and a firmware change never republishes the site unless it
touched `docs/`. The GitHub Actions paths filters enforce the same split.

## The record

Three files at the repository root are the running history, and they are
maintained by hand rather than generated:

| File | Is |
| --- | --- |
| `PROJECT_STATE.md` | what is true now, including what has been tried and failed |
| `ROADMAP.md` | what is planned |
| `CHANGELOG.md` | what changed, in order |

Read them before major changes. The [handoff protocol](../AI_HANDOFF.md) says
the same thing in rule form.

## Before a thesis experiment

`docs/HARDWARE_SETUP.md` §10 lists the measurements to record **before** the
experiments start — board MAC, sensor part, measured fps, PSRAM free, the
component versions in `dependencies.lock`. Numbers taken afterwards cannot be
attributed to a configuration.
