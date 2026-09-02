#include "voice_clips.h"

#include <cstdio>
#include <cstring>
#include <sys/stat.h>

#include "board_audio.h"
#include "board_sdcard.h"
#include "esp_log.h"

static const char *TAG = "voice_clips";

// ---------------------------------------------------------------------------
// clips embedded at build time
// ---------------------------------------------------------------------------
// EMBED_FILES turns assets/audio/en_drowsy.wav into _binary_en_drowsy_wav_start.
//
// Both languages, so the board speaks whether or not a card is fitted. The Khmer
// set is synthesised (scripts/make_voice_clips.py --engine google) and should be
// replaced by a recording from a fluent speaker before this is anything but a
// prototype - which needs no rebuild, because a clip on the card wins over these.
#define EMBEDDED(name)                                                        \
    extern const uint8_t name##_start[] asm("_binary_" #name "_start");       \
    extern const uint8_t name##_end[] asm("_binary_" #name "_end")

EMBEDDED(en_drowsy_wav);
EMBEDDED(en_microsleep_wav);
EMBEDDED(en_yawning_wav);
EMBEDDED(en_head_nod_wav);
EMBEDDED(en_no_driver_wav);
EMBEDDED(km_drowsy_wav);
EMBEDDED(km_microsleep_wav);
EMBEDDED(km_yawning_wav);
EMBEDDED(km_head_nod_wav);
EMBEDDED(km_no_driver_wav);

struct Embedded {
    const char *lang;
    const char *reason;
    const uint8_t *start;
    const uint8_t *end;
};

static const Embedded kEmbedded[] = {
    {"en", "drowsy", en_drowsy_wav_start, en_drowsy_wav_end},
    {"en", "microsleep", en_microsleep_wav_start, en_microsleep_wav_end},
    {"en", "yawning", en_yawning_wav_start, en_yawning_wav_end},
    {"en", "head_nod", en_head_nod_wav_start, en_head_nod_wav_end},
    {"en", "no_driver", en_no_driver_wav_start, en_no_driver_wav_end},
    {"km", "drowsy", km_drowsy_wav_start, km_drowsy_wav_end},
    {"km", "microsleep", km_microsleep_wav_start, km_microsleep_wav_end},
    {"km", "yawning", km_yawning_wav_start, km_yawning_wav_end},
    {"km", "head_nod", km_head_nod_wav_start, km_head_nod_wav_end},
    {"km", "no_driver", km_no_driver_wav_start, km_no_driver_wav_end},
};

static const Embedded *find_embedded(const char *lang, const char *reason) {
    for (const Embedded &e : kEmbedded) {
        if (strcmp(e.lang, lang) == 0 && strcmp(e.reason, reason) == 0) return &e;
    }
    return nullptr;
}

// ---------------------------------------------------------------------------
// WAV parsing
// ---------------------------------------------------------------------------
// Only what is needed to find the samples and refuse anything that is not the one
// format the I2S path streams. Deliberately a chunk walk rather than "assume a
// 44-byte header": plenty of tools emit a LIST or fact chunk before `data`, and
// assuming the offset would play metadata as audio - which is loud, alarming, and
// exactly the wrong thing for this device to do.
struct WavInfo {
    uint32_t data_off = 0;
    uint32_t data_len = 0;
    const char *problem = nullptr;   // nullptr when usable
};

static uint32_t rd32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}
static uint16_t rd16(const uint8_t *p) {
    return static_cast<uint16_t>(p[0] | (p[1] << 8));
}

static WavInfo wav_parse(const uint8_t *buf, size_t n) {
    WavInfo out;
    if (n < 44 || memcmp(buf, "RIFF", 4) != 0 || memcmp(buf + 8, "WAVE", 4) != 0) {
        out.problem = "not a RIFF/WAVE file";
        return out;
    }

    bool fmt_ok = false;
    size_t at = 12;
    while (at + 8 <= n) {
        const char *id = reinterpret_cast<const char *>(buf + at);
        const uint32_t len = rd32(buf + at + 4);
        const size_t body = at + 8;

        if (memcmp(id, "fmt ", 4) == 0 && body + 16 <= n) {
            const uint16_t format = rd16(buf + body);
            const uint16_t channels = rd16(buf + body + 2);
            const uint32_t rate = rd32(buf + body + 4);
            const uint16_t bits = rd16(buf + body + 14);
            if (format != 1) out.problem = "not uncompressed PCM";
            else if (channels != 1) out.problem = "not mono";
            else if (rate != AUDIO_SAMPLE_RATE_HZ) out.problem = "wrong sample rate";
            else if (bits != 16) out.problem = "not 16-bit";
            else fmt_ok = true;
            if (out.problem != nullptr) return out;
        } else if (memcmp(id, "data", 4) == 0) {
            out.data_off = static_cast<uint32_t>(body);
            out.data_len = len;
        }

        // Chunks are word-aligned, and an odd length carries a pad byte.
        at = body + len + (len & 1u);
    }

    if (!fmt_ok) out.problem = "no fmt chunk";
    else if (out.data_len == 0) out.problem = "no samples";
    return out;
}

// ---------------------------------------------------------------------------
// paths
// ---------------------------------------------------------------------------
static void card_path(const char *lang, const char *reason, char *out, size_t n) {
    snprintf(out, n, SD_MOUNT_POINT "/audio/%s_%s.wav", lang, reason);
}

static bool card_has(const char *lang, const char *reason) {
    if (!board_sdcard_mounted()) return false;
    char path[96];
    card_path(lang, reason, path, sizeof(path));
    struct stat st{};
    return stat(path, &st) == 0 && st.st_size > 44;
}

// ---------------------------------------------------------------------------
// playback
// ---------------------------------------------------------------------------
static bool play_from_card(const char *lang, const char *reason) {
    char path[96];
    card_path(lang, reason, path, sizeof(path));
    FILE *f = fopen(path, "rb");
    if (f == nullptr) return false;

    // 256 bytes is plenty of header for any chunk layout that matters, and small
    // enough to sit on the alert task's stack.
    uint8_t head[256];
    const size_t got = fread(head, 1, sizeof(head), f);
    const WavInfo info = wav_parse(head, got);
    if (info.problem != nullptr) {
        ESP_LOGW(TAG, "%s rejected: %s", path, info.problem);
        fclose(f);
        return false;
    }

    if (fseek(f, static_cast<long>(info.data_off), SEEK_SET) != 0) {
        fclose(f);
        return false;
    }

    // Streamed in blocks rather than read whole: a clip is ~90 kB and there is no
    // reason to hold that when board_audio_play_pcm16 consumes it a chunk at a
    // time anyway.
    static int16_t block[512];
    uint32_t left = info.data_len;
    bool ok = true;
    while (left > 0) {
        const size_t want = left < sizeof(block) ? left : sizeof(block);
        const size_t n = fread(block, 1, want, f);
        if (n < 2) break;
        if (!board_audio_play_pcm16(block, n / 2)) { ok = false; break; }
        left -= static_cast<uint32_t>(n);
    }
    fclose(f);
    return ok;
}

static bool play_embedded(const Embedded *e) {
    const size_t n = static_cast<size_t>(e->end - e->start);
    const WavInfo info = wav_parse(e->start, n);
    if (info.problem != nullptr) {
        // A build-time asset that does not parse is a build mistake, not a runtime
        // condition - say so loudly rather than falling through quietly.
        ESP_LOGE(TAG, "embedded %s_%s rejected: %s", e->lang, e->reason, info.problem);
        return false;
    }
    uint32_t len = info.data_len;
    if (info.data_off + len > n) len = static_cast<uint32_t>(n - info.data_off);
    return board_audio_play_pcm16(
        reinterpret_cast<const int16_t *>(e->start + info.data_off), len / 2);
}

ClipSource voice_clip_probe(const char *lang, const char *reason) {
    if (lang == nullptr || reason == nullptr) return ClipSource::None;
    if (card_has(lang, reason)) return ClipSource::Card;
    if (find_embedded(lang, reason) != nullptr) return ClipSource::Embedded;
    return ClipSource::None;
}

ClipSource voice_clip_play(const char *lang, const char *reason) {
    if (lang == nullptr || reason == nullptr) return ClipSource::None;
    if (!board_audio_ready()) return ClipSource::None;

    if (card_has(lang, reason) && play_from_card(lang, reason)) {
        board_audio_silence();
        return ClipSource::Card;
    }

    const Embedded *e = find_embedded(lang, reason);
    if (e != nullptr && play_embedded(e)) {
        board_audio_silence();
        return ClipSource::Embedded;
    }

    return ClipSource::None;
}

const char *voice_clip_source_name(ClipSource s) {
    switch (s) {
        case ClipSource::Card: return "card";
        case ClipSource::Embedded: return "embedded";
        case ClipSource::None:
        default: return "tone";
    }
}
