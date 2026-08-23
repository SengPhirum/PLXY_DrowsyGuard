---
title: Getting started
---

# Getting started

There are three ways into this project, and they do not depend on each other.
Pick the one that matches what you have in front of you.

<div class="grid cards" markdown>

-   :material-laptop: **Only a laptop**

    Install the toolkit and run the live dashboard against your webcam. The
    detection pipeline, the risk logic and the tuning UI are all real; only the
    board is missing.

    [:octicons-arrow-right-24: Install the toolkit](installation.md)

-   :material-memory: **A board on the desk**

    Build, flash and monitor the firmware with `./plxy.sh dev`, then join the
    board's access point to see what it sees.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

-   :material-package-variant-closed: **A box of parts**

    Start at the hardware tutorial: four components, seven wires, and a test
    after every stage.

    [:octicons-arrow-right-24: Hardware setup tutorial](../tutorials/hardware-setup/README.md)

</div>

## What you need

| For | You need |
| --- | --- |
| Desktop toolkit, live dashboard | Python 3.10+ and a webcam |
| Training and export | the above, plus a dataset (see [Datasets](../guide/datasets.md)) |
| `.espdl` quantization | the above, plus `esp-ppq` |
| Firmware | ESP-IDF 5.x on Windows, macOS or Linux, and the board |
| Documentation | Python 3.10+ only — see the [documentation pipeline](../operations/documentation.md) |

## The 60-second version

```bash
git clone https://github.com/SengPhirum/PLXY_DrowsyGuard.git
cd PLXY_DrowsyGuard

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[live]"

drowsyguard doctor                 # what is installed, what is missing
drowsyguard fetch-models           # YuNet detector + eye-state model
python -m drowsyguard.cli live     # then open http://127.0.0.1:8000
```

No checkpoint is needed: eye mode is the default and uses the downloaded
eye-state model.
