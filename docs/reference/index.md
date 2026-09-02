---
title: API reference
---

# API reference

| Reference | Surface |
| --- | --- |
| [Command line](cli.md) | every `drowsyguard` subcommand and flag, and every `plxy.sh` command |
| [Device HTTP API](device-api.md) | what the board serves on ports 80 and 81, plus the [MQTT alert and status payloads](device-api.md#the-alert-payload) |
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

## Three surfaces, and only one of them is versioned

The two HTTP APIs are **not** versioned. This is a research prototype and both are
expected to change; the page and the CLI are updated with them in the same commit. If
you script against the device, pin to a commit.

The **MQTT payloads are versioned**, and that is a deliberate exception rather than an
inconsistency. An HTTP client polls a device it can see and can be updated alongside
it; a subscriber is somebody else's software, on somebody else's machine, reading
messages a device published while nobody was watching. So every document carries a
`schema` field — `drowsyguard.alert.v1`, `drowsyguard.status.v1` — and a subscriber
can refuse one it does not understand instead of rendering a device with no risk and
no remark. Fields will be added within a version; anything that changes the meaning of
an existing field gets a new one.
