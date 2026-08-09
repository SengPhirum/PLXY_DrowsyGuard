# Voice alert assets

DrowsyGuard uses short pre-recorded warnings rather than on-device text-to-speech. This keeps CPU/RAM use predictable and makes multilingual output practical on ESP32-S3.

## Required assets

Create these files before hardware audio validation:

- `en_warning.wav` — recommended spoken text: `Warning. You appear drowsy. Please take a rest.`
- `km_warning.wav` — natural Khmer translation recorded by a fluent speaker and reviewed before thesis testing.

Do not commit copyrighted/commercial voice recordings without permission.

## Recommended encoding

Start with mono PCM, 16-bit, 16 kHz. If flash usage becomes important, measure intelligibility and memory use before choosing a more compressed representation.

The final firmware build should convert/embed approved recordings as binary assets and stream them through ESP-IDF I2S to the audio amplifier.
