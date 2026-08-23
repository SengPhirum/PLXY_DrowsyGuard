---
title: API reference
---

# API reference

| Reference | Surface |
| --- | --- |
| [Command line](cli.md) | every `drowsyguard` subcommand and flag, and every `plxy.sh` command |
| [Device HTTP API](device-api.md) | what the board serves on ports 80 and 81 |
| [Dashboard HTTP API](dashboard-api.md) | what the desktop live dashboard serves on port 8000 |
| [Python modules](python-api.md) | the modules behind the CLI, and which one owns what |

## Two HTTP APIs, deliberately different

The board and the desktop dashboard both serve a live page with a stream and a
JSON state object, and they are **not** the same API. The device speaks a
hand-rolled, fixed-shape JSON built with one `snprintf` — allocation-free in a
5 Hz polling path on a 6 kB task stack — while the dashboard is FastAPI and
returns whatever the engine snapshot holds.

What *is* shared is the decision logic underneath: `drowsyguard.risk.RiskFilter`
mirrors `risk_filter.cpp`, checked by `tests/test_firmware_parity.py`.

## Stability

Neither API is versioned. This is a research prototype and both are expected to
change; the page and the CLI are updated with them in the same commit. If you
script against the device, pin to a commit.
