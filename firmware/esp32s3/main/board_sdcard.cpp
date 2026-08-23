#include "board_sdcard.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/unistd.h>

#include "driver/sdmmc_host.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "sdmmc_cmd.h"

static const char *TAG = "sdcard";

static sdmmc_card_t *s_card = nullptr;
static bool s_mounted = false;
static char s_error[48] = {0};
static SemaphoreHandle_t s_lock = nullptr;

// The index is a plain append-only text file, one event per line:
//
//     <id> <uptime_ms> <size> <risk> <perclos> <reason>
//
// Deliberately not a database and not JSON. It has to survive a power cut in a
// car mid-write, and a truncated last line of a text file costs one event, while
// a truncated JSON array costs the whole history. Recovery is "ignore lines that
// do not parse", which needs no code.
#define INDEX_PATH SD_EVENT_DIR "/index.txt"

static uint32_t s_next_id = 0;
static int s_count = 0;

// ---------------------------------------------------------------------------
// mount
// ---------------------------------------------------------------------------
static void fail(const char *why) {
    snprintf(s_error, sizeof(s_error), "%s", why);
    ESP_LOGW(TAG, "%s", why);
}

// Counts index lines and finds the highest id, so ids keep rising across reboots.
static void scan_index() {
    s_count = 0;
    s_next_id = 0;
    FILE *f = fopen(INDEX_PATH, "r");
    if (f == nullptr) return;
    char line[160];
    while (fgets(line, sizeof(line), f) != nullptr) {
        unsigned id = 0;
        if (sscanf(line, "%u", &id) != 1) continue;   // truncated tail: ignore it
        ++s_count;
        if (id + 1 > s_next_id) s_next_id = id + 1;
    }
    fclose(f);
    ESP_LOGI(TAG, "index: %d events, next id %u", s_count, static_cast<unsigned>(s_next_id));
}

bool board_sdcard_init() {
    if (s_mounted) return true;
    if (s_lock == nullptr) s_lock = xSemaphoreCreateMutex();
    if (s_lock == nullptr) { fail("no memory for the sd lock"); return false; }

    esp_vfs_fat_sdmmc_mount_config_t mount = {};
    // format_if_mount_failed stays false on purpose: a card that does not mount
    // might be someone's, formatted for something else, or simply dirty. Erasing
    // it to make an error message go away is not a trade this project makes.
    mount.format_if_mount_failed = false;
    mount.max_files = 4;
    mount.allocation_unit_size = 16 * 1024;

    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    // 1-line mode: only D0 is routed on this board. Also drop the clock to the
    // conservative default - the card sits behind long PCB traces next to a DVP
    // bus clocking at 10 MHz, and a JPEG every few minutes does not need speed.
    host.flags = SDMMC_HOST_FLAG_1BIT;
    host.max_freq_khz = SDMMC_FREQ_DEFAULT;

    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 1;
    slot.clk = static_cast<gpio_num_t>(SD_PIN_CLK);
    slot.cmd = static_cast<gpio_num_t>(SD_PIN_CMD);
    slot.d0 = static_cast<gpio_num_t>(SD_PIN_D0);
    slot.flags = SDMMC_SLOT_FLAG_INTERNAL_PULLUP;   // no external pull-ups fitted

    const esp_err_t err = esp_vfs_fat_sdmmc_mount(SD_MOUNT_POINT, &host, &slot,
                                                  &mount, &s_card);
    if (err != ESP_OK) {
        if (err == ESP_FAIL) {
            fail("card present but not FAT - format it FAT32");
        } else {
            char msg[48];
            snprintf(msg, sizeof(msg), "no card (%s)", esp_err_to_name(err));
            fail(msg);
        }
        s_card = nullptr;
        return false;
    }

    s_mounted = true;
    s_error[0] = '\0';
    ESP_LOGI(TAG, "mounted %s: %s, %llu MB", SD_MOUNT_POINT, s_card->cid.name,
             (static_cast<uint64_t>(s_card->csd.capacity) * s_card->csd.sector_size) >> 20);

    if (mkdir(SD_EVENT_DIR, 0777) != 0 && errno != EEXIST) {
        ESP_LOGW(TAG, "could not create %s (errno %d); history disabled",
                 SD_EVENT_DIR, errno);
    }
    scan_index();
    return true;
}

bool board_sdcard_mounted() { return s_mounted; }

void board_sdcard_info(SdCardInfo *out) {
    if (out == nullptr) return;
    *out = SdCardInfo{};
    out->mounted = s_mounted;
    snprintf(out->error, sizeof(out->error), "%s", s_error);
    if (!s_mounted || s_card == nullptr) return;

    snprintf(out->name, sizeof(out->name), "%s", s_card->cid.name);
    out->events = s_count;

    // Cached: f_getfree walks the allocation table, which is far too expensive to
    // do on every status poll at 3 Hz.
    static int64_t last_us = 0;
    static uint64_t total = 0, freeb = 0;
    const int64_t now = esp_timer_get_time();
    if (total == 0 || now - last_us > 5 * 1000 * 1000) {
        uint64_t t = 0, f = 0;
        if (esp_vfs_fat_info(SD_MOUNT_POINT, &t, &f) == ESP_OK) {
            total = t;
            freeb = f;
            last_us = now;
        }
    }
    out->total_bytes = total;
    out->free_bytes = freeb;
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------
static void event_path_for(uint32_t id, char *out, size_t n) {
    snprintf(out, n, SD_EVENT_DIR "/%07u.jpg", static_cast<unsigned>(id));
}

// Drops the oldest entries until the index is back under the cap. Rewrites the
// index rather than editing in place: at a thousand short lines that is a few
// tens of kilobytes, and an atomic replace cannot leave a half-edited index.
static void trim_to_cap() {
    if (s_count <= SD_MAX_EVENTS) return;
    const int drop = s_count - SD_MAX_EVENTS;

    FILE *in = fopen(INDEX_PATH, "r");
    if (in == nullptr) return;
    FILE *out = fopen(INDEX_PATH ".tmp", "w");
    if (out == nullptr) { fclose(in); return; }

    char line[160];
    int seen = 0, kept = 0;
    while (fgets(line, sizeof(line), in) != nullptr) {
        unsigned id = 0;
        if (sscanf(line, "%u", &id) != 1) continue;
        if (seen++ < drop) {
            char path[80];
            event_path_for(id, path, sizeof(path));
            unlink(path);
            continue;
        }
        fputs(line, out);
        ++kept;
    }
    fclose(in);
    fclose(out);
    if (rename(INDEX_PATH ".tmp", INDEX_PATH) == 0) {
        s_count = kept;
        ESP_LOGI(TAG, "trimmed %d old events, %d kept", drop, kept);
    } else {
        unlink(INDEX_PATH ".tmp");
    }
}

bool board_sdcard_store_event(const uint8_t *jpeg, size_t len, float risk,
                              float perclos, const char *reason, uint32_t uptime_ms) {
    if (!s_mounted || jpeg == nullptr || len == 0) return false;

    xSemaphoreTake(s_lock, portMAX_DELAY);
    const uint32_t id = s_next_id;
    bool ok = false;

    char path[80];
    event_path_for(id, path, sizeof(path));
    FILE *f = fopen(path, "wb");
    if (f != nullptr) {
        ok = fwrite(jpeg, 1, len, f) == len;
        fclose(f);
        if (!ok) unlink(path);
    } else {
        ESP_LOGW(TAG, "could not open %s (errno %d)", path, errno);
    }

    if (ok) {
        // Index last, so a power cut leaves an orphan JPEG rather than an index
        // entry pointing at a file that is not there. An orphan is invisible; a
        // dangling entry is a broken thumbnail in the history page.
        FILE *ix = fopen(INDEX_PATH, "a");
        if (ix != nullptr) {
            fprintf(ix, "%u %u %u %.3f %.3f %s\n", static_cast<unsigned>(id),
                    static_cast<unsigned>(uptime_ms), static_cast<unsigned>(len),
                    risk, perclos, reason != nullptr ? reason : "drowsy");
            fclose(ix);
            ++s_next_id;
            ++s_count;
            trim_to_cap();
        } else {
            unlink(path);
            ok = false;
        }
    }
    xSemaphoreGive(s_lock);

    if (ok) {
        ESP_LOGI(TAG, "stored event %07u (%u B, risk %.2f, %s)",
                 static_cast<unsigned>(id), static_cast<unsigned>(len), risk,
                 reason != nullptr ? reason : "drowsy");
    }
    return ok;
}

int board_sdcard_event_count() { return s_count; }

int board_sdcard_list_events(SdEvent *out, int max_out, int skip) {
    if (!s_mounted || out == nullptr || max_out <= 0) return 0;

    // Newest first, which means reading forwards and keeping a tail window. The
    // index is at most SD_MAX_EVENTS short lines, so one pass is cheap and needs
    // no seeking; the alternative (reverse-seeking a text file) is far more code
    // for no gain at this size.
    const int want_from = s_count - skip - max_out;
    const int want_to = s_count - skip;          // exclusive
    if (want_to <= 0) return 0;

    FILE *f = fopen(INDEX_PATH, "r");
    if (f == nullptr) return 0;

    char line[160];
    int row = 0, n = 0;
    while (fgets(line, sizeof(line), f) != nullptr && n < max_out) {
        unsigned id = 0, up = 0, size = 0;
        float risk = 0.0f, pc = 0.0f;
        char reason[16] = {0};
        if (sscanf(line, "%u %u %u %f %f %15s", &id, &up, &size, &risk, &pc, reason) < 5) {
            continue;
        }
        const int here = row++;
        if (here < want_from || here >= want_to) continue;
        SdEvent &e = out[n++];
        snprintf(e.name, sizeof(e.name), "%07u", id);
        e.uptime_ms = up;
        e.size = size;
        e.risk = risk;
        e.perclos = pc;
        snprintf(e.reason, sizeof(e.reason), "%s", reason);
    }
    fclose(f);

    // Reverse in place: the caller wants newest first, the file is oldest first.
    for (int i = 0, j = n - 1; i < j; ++i, --j) {
        SdEvent t = out[i];
        out[i] = out[j];
        out[j] = t;
    }
    return n;
}

bool board_sdcard_event_path(const char *name, char *out, size_t out_len) {
    if (!s_mounted || name == nullptr || out == nullptr) return false;

    // This is the only place a client-supplied string becomes a filesystem path,
    // so it is validated rather than escaped: exactly 1-7 digits, nothing else.
    // That makes "../../secret" unrepresentable instead of merely filtered.
    const size_t n = strlen(name);
    if (n == 0 || n > 7) return false;
    for (size_t i = 0; i < n; ++i) {
        if (name[i] < '0' || name[i] > '9') return false;
    }
    unsigned id = 0;
    if (sscanf(name, "%u", &id) != 1) return false;
    event_path_for(id, out, out_len);

    struct stat st{};
    return stat(out, &st) == 0 && st.st_size > 0;
}

bool board_sdcard_clear_events() {
    if (!s_mounted) return false;
    xSemaphoreTake(s_lock, portMAX_DELAY);

    DIR *d = opendir(SD_EVENT_DIR);
    if (d != nullptr) {
        struct dirent *ent;
        while ((ent = readdir(d)) != nullptr) {
            // d_name is up to 255 bytes by POSIX, so the buffer is sized for the
            // directory plus a full-length name rather than for the names we
            // happen to write.
            char path[sizeof(SD_EVENT_DIR) + 260];
            snprintf(path, sizeof(path), SD_EVENT_DIR "/%s", ent->d_name);
            unlink(path);
        }
        closedir(d);
    }
    s_count = 0;
    // Ids keep climbing rather than restarting at zero: a stale bookmark or an
    // open history page then 404s instead of quietly showing a different event.
    xSemaphoreGive(s_lock);
    ESP_LOGI(TAG, "history cleared");
    return true;
}
