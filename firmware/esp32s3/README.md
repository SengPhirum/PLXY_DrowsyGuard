# ESP32-S3 Firmware

This directory is an ESP-IDF scaffold for the deployment phase.

Because ESP32-S3 camera pin mappings vary by board and ESP-DL's model wrapper is version-specific, the code isolates both behind explicit TODO adapters instead of pretending one untested pin map/API works everywhere.

## Intended commands
```bash
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py flash monitor -p /dev/ttyUSB0
```

## Before first build
1. Select exact board and camera sensor.
2. Add its verified pin map in `main/board_camera.h`.
3. Pin ESP-DL dependency in `main/idf_component.yml`.
4. Implement `model_adapter.cpp` against that pinned ESP-DL release.
5. Place/export the quantized `.espdl` model as configured.
