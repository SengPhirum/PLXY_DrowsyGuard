#pragma once
/*
microSD storage for the drowsiness-event history.

Why a card at all: the alert is the product, but a warning nobody can review is
not evidence. An event log with the actual frame attached turns "the alarm went
off twice" into something you can look at afterwards and argue about in a thesis -
was that a real microsleep, or did the driver just look down at the gearstick?

Wiring: none. The slot is on the board and the ESP32-S3 talks to it over its
SDMMC peripheral in 1-line mode on three fixed pins:

    GPIO 39  CLK
    GPIO 38  CMD
    GPIO 40  D0

Those pins are not negotiable - they are routed to the socket on the PCB. They
are also why the I2S amplifier moved to 14/21/47 (see board_audio.h): the
amplifier was squatting on the card's bus while the slot was empty.

1-line rather than 4-line mode because only D0 is brought out on this board. It
caps throughput at roughly 2-4 MB/s, which is an order of magnitude more than a
12 kB JPEG every few minutes needs.

No card is not an error. Everything here degrades to "history unavailable" and
says so on the status page; detection and alerting never touch the filesystem.
*/

#include <cstddef>
#include <cstdint>

// --- SDMMC bus, fixed by the board ---
#define SD_PIN_CLK 39
#define SD_PIN_CMD 38
#define SD_PIN_D0  40

// Mount point. Kept short: FATFS paths are length-limited and every event file
// path is built on top of this one.
#define SD_MOUNT_POINT "/sdcard"

// Where event captures live, relative to the mount point.
#define SD_EVENT_DIR SD_MOUNT_POINT "/events"

// Ring-buffer ceiling. At ~12 kB a JPEG this is roughly 12 MB, which even a 1 GB
// card swallows without noticing; the point of the cap is not space but keeping
// the index bounded and the history page fast to render. Oldest events are
// deleted first.
#define SD_MAX_EVENTS 1000

struct SdCardInfo {
    bool mounted = false;
    uint64_t total_bytes = 0;
    uint64_t free_bytes = 0;
    char name[16] = {0};     // the card's own product name
    int events = 0;          // files currently in SD_EVENT_DIR
    char error[48] = {0};    // why the mount failed, when it did
};

// Mounts the card as FAT. Returns false when no card is present or the mount
// fails - both are survivable, and the reason lands in SdCardInfo::error.
// Never formats an unrecognised card: someone's photos are not ours to erase.
bool board_sdcard_init();

bool board_sdcard_mounted();

// Refreshes and returns the card state. Cheap enough for the status endpoint;
// the free-space figure comes from FATFS and is cached for a second.
void board_sdcard_info(SdCardInfo *out);

// One stored drowsiness event. `name` is the on-card filename stem, which is also
// the id the web API uses.
struct SdEvent {
    char name[32];       // e.g. "0000042"
    uint32_t uptime_ms;  // when it fired, since boot
    uint32_t size;       // JPEG bytes
    float risk;
    float perclos;
    char reason[16];     // "microsleep", "yawning", ...
};

// Appends one event: writes <id>.jpg and a line in the index. Returns false if
// there is no card, the write failed, or `jpeg` is empty. Safe to call from any
// task; serialised internally.
bool board_sdcard_store_event(const uint8_t *jpeg, size_t len, float risk,
                              float perclos, const char *reason, uint32_t uptime_ms);

// Newest-first page of the index. Returns how many entries were written to `out`.
int board_sdcard_list_events(SdEvent *out, int max_out, int skip);

// Total events in the index, for paging.
int board_sdcard_event_count();

// Absolute path of one event's JPEG, for the file-serving handler. Returns false
// if `name` is not a plausible id - this is the only place a client-supplied
// string reaches the filesystem, so it is validated rather than trusted.
bool board_sdcard_event_path(const char *name, char *out, size_t out_len);

// Deletes every stored event and resets the index.
bool board_sdcard_clear_events();
