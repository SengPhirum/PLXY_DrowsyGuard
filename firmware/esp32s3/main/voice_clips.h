#pragma once
/*
Spoken alert clips: where they come from and in what order.

The alert used to be a tone pattern per reason - audible, testable, and not
actually a warning. It now speaks, and it speaks the reason: a driver who hears
"you appear drowsy" knows what to do with it, where a driver who hears three beeps
has to remember what three beeps meant.

Three sources, tried in this order for a given (language, reason):

  1. **The microSD card**, at /sdcard/audio/<lang>_<reason>.wav. First because it
     is the only one that can be changed without a toolchain: copy a file from a
     laptop and the next alert says something different. That is what makes Khmer
     practical - the recording has to come from a fluent speaker, and asking them
     to rebuild firmware is not reasonable.
  2. **Flash**, from the clips embedded at build time (EMBED_FILES in
     main/CMakeLists.txt). English only, and the reason a board with no card still
     speaks rather than beeps.
  3. **A tone pattern**, in voice_alert.cpp. Never silence: a drowsiness alarm
     that says nothing because a file is missing has failed at its only job.

Format is fixed at mono 16-bit PCM, 16 kHz - what board_audio.cpp streams and what
assets/audio/README.md has specified from the start. A clip in any other format is
rejected with a log line naming what was wrong, rather than played as noise;
`python scripts/make_voice_clips.py --check` reports the same verdicts offline.
*/

#include <cstddef>
#include <cstdint>

// Where a clip came from. Reported per reason on the status page, because "the
// alert spoke Khmer" and "the alert fell back to English" are indistinguishable
// by ear if you do not speak one of them.
enum class ClipSource : uint8_t {
    None = 0,      // nothing usable; the caller should fall back to tones
    Card = 1,      // /sdcard/audio/<lang>_<reason>.wav
    Embedded = 2,  // linked into the firmware
};

// Streams <lang>_<reason>.wav to the amplifier and returns what it played from.
// Blocks for the length of the clip, so call it from the alert task and not from
// the capture loop. `lang` is "en" or "km"; `reason` is a voice_alert clip name.
ClipSource voice_clip_play(const char *lang, const char *reason);

// What voice_clip_play() would use, without playing anything. Cheap enough for the
// status endpoint: it stats one path and checks one table.
ClipSource voice_clip_probe(const char *lang, const char *reason);

// Human-readable, for logs and JSON.
const char *voice_clip_source_name(ClipSource s);
