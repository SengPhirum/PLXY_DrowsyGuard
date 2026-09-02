#pragma once
/*
The BOOT button, as a way back into a device whose Wi-Fi credentials are wrong.

That is the whole justification. Every other setting on this board can be changed
from the web page, and the web page is always reachable because the SoftAP never
comes down - but a device configured to join a network that does not exist has
nothing wrong with its access point either, so the page is not actually the problem
this solves. What it solves is the case where somebody hands the board to a colleague
with a hotspot that is gone, or where a saved SSID is wrong in a way that is easier
to clear than to correct on a phone keyboard.

WHY GPIO0 AND WHAT THAT COSTS
-----------------------------
It is the only button on this board, and the application uses no other GPIO for
anything (see docs/HARDWARE_SETUP.md: the camera has 4-13 and 15-18, the amplifier
38/39/40, the buzzer 2). It is a strapping pin, but only while RESET is asserted;
after boot it is an ordinary input with a pull-up, which is what this reads.

The cost is a real hazard rather than a theoretical one, and it is why the state
machine lives in wifi_provision.h with its own tests: this board's auto-reset lines
are inverted, so pyserial pulls GPIO0 LOW when it opens a serial port. Anyone
attaching a terminal is, electrically, holding BOOT down. ButtonWatch requires a
debounced RELEASE before it believes any press, which makes that case inert - it
never arms, and says so once rather than going quiet.

NO SOUND
--------
The obvious thing to do at the two-second warning is beep, and this deliberately does
not. board_audio_play_tone() blocks until the samples are queued and neither
board_audio.cpp nor voice_alert.cpp holds a lock, so calling it from here would race
the alert task for the I2S channel. On a device whose speaker is the only output a
drowsy driver perceives, a convenience beep is not worth putting a second writer on
that path. Feedback goes to the serial log - which is what the requirement asks for -
and to the status page, which shows the hold in progress.
*/

#include <cstdint>

// GPIO0. Not configurable, because there is exactly one button and picking a
// different pin would mean picking one the camera or the amplifier is using.
#define BUTTON_GPIO 0

// Fired once, from the button task, when the hold completes. Runs on that task -
// which does nothing else and has no deadline - so it may take its time, but it must
// not assume it is on the main task.
typedef void (*ButtonResetFn)(void);

// Configures the pin and starts the watcher task. Returns false only if the task or
// the GPIO could not be set up; the rest of the firmware is unaffected either way,
// which is the same rule every other optional subsystem here follows.
bool board_button_start(ButtonResetFn on_reset);

// For the status page. `armed` is false on a board with a serial adapter holding
// GPIO0 low, which is worth showing: it is the difference between "the button does
// nothing because it is broken" and "the button does nothing because a cable is
// plugged in".
bool board_button_armed();
uint32_t board_button_held_ms();
