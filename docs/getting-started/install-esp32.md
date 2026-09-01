# Install to ESP32 from your browser

Plug the board in, click **Install DrowsyGuard**, and the page writes the firmware
over USB. No toolchain, no Python, no ESP-IDF, no driver hunt.

This is here because the alternative — clone the repository, install a 2 GB
toolchain, resolve managed components, and read three pages of setup — is a
reasonable ask of somebody developing the firmware and an unreasonable one of
somebody who just wants to try the device or reflash a board that has gone quiet.
The build the browser writes is the same build the release pipeline verified; see
[what gets written](#what-gets-written).

!!! warning "Research prototype, not a certified safety device"

    DrowsyGuard is a thesis project. Flashing it to a board and putting that board
    in a vehicle does not make the vehicle safer, and nothing on this page should be
    read as saying it does. Read [Safety and limitations](../security.md#safety-and-limitations)
    before you use it anywhere near a road.

## Before you start

| You need | Why |
| --- | --- |
| **Chrome or Edge**, version 89 or newer | The installer uses Web Serial, which only these support. Firefox and Safari do not implement it, and no extension changes that. |
| An **ESP32-S3** board with 8 MB PSRAM and 16 MB flash | The camera framebuffers and the ESP-DL models live in PSRAM; the app partition alone is 6 MB. A board without both will fail at `esp_camera_init` or refuse to link. See [Hardware setup](../HARDWARE_SETUP.md). |
| A **data** USB cable | The most common reason this page appears to do nothing. Charge-only cables have no data pins and the board never enumerates. |
| The page open over **HTTPS** | Web Serial is a secure-context API. This site is served over HTTPS, so this is only a problem if you have copied the page somewhere else. |

Linux users: your account needs access to the serial device, which usually means
being in the `dialout` group (`sudo usermod -aG dialout $USER`, then log out and
back in). macOS and Windows need nothing extra — the ESP32-S3's native USB
enumerates as a standard CDC device.

## Install

<div id="dg-installer" markdown="0">
  <p id="dg-status" class="dg-status">Checking for a published build…</p>
  <esp-web-install-button id="dg-button" hidden>
    <button slot="activate" class="md-button md-button--primary">Install DrowsyGuard</button>
    <span slot="unsupported">
      Your browser cannot do this. Web Serial is only in Chrome and Edge (89+);
      open this page in one of those, or
      <a href="#flash-it-yourself-instead">flash it yourself</a>.
    </span>
    <span slot="not-allowed">
      This page has to be served over HTTPS for the browser to allow serial access.
    </span>
  </esp-web-install-button>
  <table id="dg-build" hidden>
    <thead><tr><th>Build</th><th>Value</th></tr></thead>
    <tbody></tbody>
  </table>
</div>

<script type="module" src="https://unpkg.com/esp-web-tools@10/dist/web/install-button.js"></script>

If the board is not offered in the port picker, hold **BOOT**, tap **RESET**, release
**BOOT**, and click Install again. That forces the ROM download loader, which always
enumerates. On this board a normal reset can leave the chip in the loader anyway —
see [Troubleshooting](../troubleshooting.md#the-board-does-not-appear-in-the-browsers-port-picker).

## What gets written

The installer writes one merged image at offset `0x0`. That is a deliberate choice
over writing the three images separately: with three parts there are three chances
for an interrupted write to leave the board with a new application and an old
bootloader, and neither the browser nor the board can tell you afterwards which
happened. One image either lands or it does not.

The merged image contains, at the offsets ESP-IDF's own `flasher_args.json` records:

| Offset | Image | What it is |
| --- | --- | --- |
| `0x0` | `bootloader.bin` | Second-stage bootloader. On the ESP32-S3 this sits at 0 — on the original ESP32 it is at `0x1000`, which is why the offsets are read from the build rather than typed. |
| `0x8000` | `partition-table.bin` | The layout in [`partitions.csv`](https://github.com/SengPhirum/PLXY_DrowsyGuard/blob/main/firmware/esp32s3/partitions.csv): 6 MB app, 4 MB SPIFFS for assets. |
| `0x10000` | `drowsyguard_esp32s3.bin` | The application, including the ESP-DL face detector, the eye model and all twelve voice clips in flash rodata. |

"Erase device" is offered on a first install and is worth taking: it clears NVS,
which is where the alert language is stored. Without it a board that was set to Khmer
stays on Khmer after a reflash, which is correct behaviour and confusing if you have
forgotten you set it.

`flash-offsets.json`, published beside the manifest, records the same table plus a
SHA-256 for every image, so a downloaded binary can be checked against what the
pipeline built.

## After it finishes

1. The board reboots and plays a three-note rising chime. If you hear it, the
   amplifier is wired and working — that chime is the only local confirmation this
   device has, because there is no screen.
2. It brings up a Wi-Fi access point called **DrowsyGuard-XXXXXX**. Join it.
3. Open <http://192.168.4.1>. That page is the whole user interface: live preview,
   the face box and landmarks, PERCLOS, the fused risk score, the event log, and the
   speaker test.

[Using the device](../guide/device.md) explains what everything on that page means.
If the preview is black or the page does not load, [Troubleshooting](../troubleshooting.md)
starts from the symptom.

## Flash it yourself instead

The browser installer is a convenience, not the only route. If you are working on the
firmware, or you are on Firefox, or you would rather see what is being written:

=== "From a release binary"

    Download `drowsyguard-esp32s3-merged.bin` from the
    [latest release](https://github.com/SengPhirum/PLXY_DrowsyGuard/releases/latest)
    and write it with esptool:

    ```bash
    pip install esptool
    python -m esptool --chip esp32s3 --port /dev/ttyACM0 write_flash 0x0 drowsyguard-esp32s3-merged.bin
    ```

    On Windows the port is a `COM` number; `python -m esptool --chip esp32s3 read_mac`
    with no `--port` will find it.

=== "From source"

    ```bash
    ./plxy.sh build
    ./plxy.sh flash
    ```

    This needs ESP-IDF 5.3 or newer. [Toolchain and firmware install](../HARDWARE_SETUP.md)
    covers the setup, and [Firmware dev loop](../guide/dev-loop.md) covers the day-to-day.

## Where the binaries come from

Nothing on this page is built by hand. The
[`firmware-release`](https://github.com/SengPhirum/PLXY_DrowsyGuard/actions/workflows/firmware-release.yml)
workflow builds the firmware in Espressif's own container image, checks that the
application fits its partition, merges the images using the offsets the build itself
reported, and publishes the merged binary, the manifest and the offset record as
release assets. The documentation deployment then copies those assets alongside this
page, so the installer fetches them from the same origin it was served from — no
cross-origin request, and nothing to configure.

If the button above says no build has been published, that is literal: the workflow
has not produced a release yet. [Flash it yourself](#flash-it-yourself-instead) in
the meantime.
