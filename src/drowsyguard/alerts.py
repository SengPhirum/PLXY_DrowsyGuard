"""What an alert is about, mirrored from ``firmware/esp32s3/main/voice_alert.h``.

The device speaks the reason rather than beeping, because a driver who hears "you
appear drowsy" knows what to do with it and a driver who hears three beeps has to
remember what three beeps meant. The dashboard does not speak, but it logs and
displays the same reasons under the same names, so a session recorded on a laptop and
a session recorded on the device produce comparable event logs.

The numbering is part of the device's HTTP API - ``/api/alert-test?reason=N`` - so
values are appended, never renumbered. ``tests/test_firmware_parity.py`` checks the
enum against the header and checks that every reason has an embedded clip in both
languages.

Channels exist because alerts do not all belong to the same conversation. Giving them
one shared cooldown was wrong: a no-driver warning suppressed by a drowsiness cooldown
is a safety message lost to an unrelated rate limit. Each channel keeps its own
episode.
"""
from __future__ import annotations

from enum import IntEnum


class AlertChannel(IntEnum):
    DROWSINESS = 0
    PRESENCE = 1


class AlertReason(IntEnum):
    DROWSY = 0
    MICROSLEEP = 1
    YAWNING = 2
    HEAD_NOD = 3
    #: Nobody in front of the camera for long enough that it is not a glance away.
    NO_DRIVER = 4

    @property
    def clip(self) -> str:
        """Asset basename, without language prefix or extension."""
        return _CLIP[self]

    @property
    def banner(self) -> str:
        """Short uppercase text, matching the spoken clip."""
        return _BANNER[self]

    @property
    def channel(self) -> AlertChannel:
        return _CHANNEL[self]


_CLIP = {
    AlertReason.DROWSY: 'drowsy',
    AlertReason.MICROSLEEP: 'microsleep',
    AlertReason.YAWNING: 'yawning',
    AlertReason.HEAD_NOD: 'head_nod',
    AlertReason.NO_DRIVER: 'no_driver',
}

_BANNER = {
    AlertReason.DROWSY: 'DROWSY',
    AlertReason.MICROSLEEP: 'WAKE UP',
    AlertReason.YAWNING: 'TAKE A BREAK',
    AlertReason.HEAD_NOD: 'STAY ALERT',
    AlertReason.NO_DRIVER: 'NO DRIVER DETECTED',
}

_CHANNEL = {
    AlertReason.DROWSY: AlertChannel.DROWSINESS,
    AlertReason.MICROSLEEP: AlertChannel.DROWSINESS,
    AlertReason.YAWNING: AlertChannel.DROWSINESS,
    AlertReason.HEAD_NOD: AlertChannel.DROWSINESS,
    AlertReason.NO_DRIVER: AlertChannel.PRESENCE,
}


def reason_for_events(events) -> AlertReason:
    """Which reason to announce for a drowsiness alert, given the frame's events.

    The order is the firmware's, and it is a severity order rather than an arbitrary
    one: a microsleep outranks a nod outranks a yawn, because that is the order in
    which they demand the driver do something immediately. Falls back to the generic
    reason when the alert came from accumulated risk with no single event behind it,
    which is the common case - sustained PERCLOS is a real reason to alert and is not
    an event.
    """
    if 'microsleep' in events:
        return AlertReason.MICROSLEEP
    if 'nod' in events:
        return AlertReason.HEAD_NOD
    if 'yawn' in events:
        return AlertReason.YAWNING
    return AlertReason.DROWSY
