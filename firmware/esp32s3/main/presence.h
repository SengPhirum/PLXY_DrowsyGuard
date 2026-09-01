#pragma once
/*
"Is anyone actually driving this?" - and, separately, "can this device still tell?"

A drowsiness detector that sees nothing has two completely different reasons for it,
and conflating them is a safety defect rather than a UX wrinkle:

  * **Nobody is there.** The seat is empty, or the driver has slumped out of frame,
    or the camera is pointed at a door pillar. The device is working; there is just
    no driver in front of it. That deserves one alert, because a monitoring system
    that has silently stopped monitoring is worse than no monitoring system - the
    driver believes they are covered.

  * **The device is broken.** The camera stopped returning frames, or the models
    never loaded. It cannot see a driver whether or not one is present, so "no
    driver detected" would be a lie: it is not a statement about the cabin, it is a
    statement about the firmware, and the fix is completely different.

This module keeps those apart, and it is the only place that decides which of them
is happening. Everything it does is arithmetic on a boolean and a time step, so it
compiles on the host and tests/test_presence.py drives it through occlusion, camera
faults, model faults and the ordinary come-and-go of a driver leaning out of frame.
src/drowsyguard/presence.py is the desktop mirror; tests/test_presence_parity.py
runs both against the same sequences.

Two debounces, in opposite directions, and both are needed:

  * absence has to persist for PRESENCE_ALERT_S before it is announced, so a driver
    who checks a mirror, is briefly occluded by a hand, or is lost for a moment by
    the detector does not set off an alarm about their own absence;
  * presence has to persist for PRESENCE_CLEAR_S before the alert re-arms, so a
    single flickering detection on an empty seat cannot cancel a real absence and
    then restart the countdown from zero, over and over, and thereby never announce
    anything at all. That failure mode is quiet, which is what makes it dangerous.

Note that FaceTrack's hold has already run before anything here: `driver_present`
only goes false once the tracking hold has expired. So the wall-clock delay from
the driver leaving to the announcement is the hold plus PRESENCE_ALERT_S.
*/

#include <cstdint>

// Seconds of continuous absence, after the tracking hold has already expired,
// before the no-driver alert fires. Three seconds is long enough that no ordinary
// glance away reaches it - the hold covers roughly a second before this even starts
// counting - and short enough that a driver who has slumped sideways out of frame is
// told within about four seconds that they are no longer being monitored.
constexpr float PRESENCE_ALERT_S = 3.0f;

// Seconds of continuous presence before the alert re-arms and the absence timer is
// allowed to restart from zero. Deliberately much shorter than PRESENCE_ALERT_S: it
// only has to outlast a single flickering detection, and making it long would delay
// the point at which a returning driver is considered monitored again.
constexpr float PRESENCE_CLEAR_S = 0.5f;

// Seconds between repeat announcements while the seat stays empty. Zero means the
// alert fires exactly once per absence episode, which is the default and the
// behaviour the safety documentation describes: a parked vehicle with the device
// still powered should say its piece and then be quiet. Set it non-zero for a
// deployment where a continuously unattended camera is itself the fault condition.
constexpr float PRESENCE_REPEAT_S = 0.0f;

// Seconds the pipeline has to be healthy before any absence is believed. This covers
// boot - the camera, the models and the auto-exposure all settle over the first
// couple of seconds and a face found during that window is luck - and it covers
// recovery from a fault, where the same argument applies for the same reason.
constexpr float PRESENCE_WARMUP_S = 5.0f;

// Why the pipeline might not be able to see anyone. Ordered by severity so a caller
// with more than one problem can report the worst.
enum class PipelineHealth : uint8_t {
    Ok = 0,
    ModelFault = 1,    // the face detector did not load; nothing can be detected
    CameraFault = 2,   // frames have stopped arriving
};

enum class PresenceState : uint8_t {
    Warmup = 0,     // healthy, but not yet trusted to judge absence
    Present = 1,    // a confirmed driver
    Absent = 2,     // nobody, and counting down to the alert
    NoDriver = 3,   // nobody, and the alert has been announced
    Fault = 4,      // the device cannot tell; this is NOT an absence
};

struct PresenceConfig {
    float alert_after_s = PRESENCE_ALERT_S;
    float clear_s = PRESENCE_CLEAR_S;
    float repeat_s = PRESENCE_REPEAT_S;
    float warmup_s = PRESENCE_WARMUP_S;
    bool enabled = true;
};

struct PresenceResult {
    PresenceState state = PresenceState::Warmup;
    // True on exactly the update that announces a no-driver condition, and never
    // again for that episode unless repeat_s is non-zero. This is an edge, not a
    // level: the caller triggers an alert on it and does not need its own
    // de-duplication.
    bool alert = false;
    float absent_s = 0.0f;    // continuous absence so far
    float present_s = 0.0f;   // continuous presence so far
    uint16_t alerts = 0;      // announcements since the last reset
    PipelineHealth health = PipelineHealth::Ok;
};

const char *presence_state_name(PresenceState s);
const char *presence_health_name(PipelineHealth h);

class PresenceMonitor {
  public:
    PresenceMonitor() = default;
    explicit PresenceMonitor(const PresenceConfig &cfg) : cfg_(cfg) {}

    // One frame. `driver_present` is FaceTrack's answer, which has already absorbed
    // the tracking hold. `dt_s` is the frame interval; a non-positive value is
    // ignored so a caller that has not measured one yet cannot advance the clock by
    // a garbage amount.
    PresenceResult update(bool driver_present, PipelineHealth health, float dt_s);

    void configure(const PresenceConfig &cfg);
    const PresenceConfig &config() const { return cfg_; }
    void reset();

  private:
    PresenceConfig cfg_{};
    PresenceState state_ = PresenceState::Warmup;
    float absent_s_ = 0.0f;
    float present_s_ = 0.0f;
    float healthy_s_ = 0.0f;
    float since_alert_s_ = 0.0f;
    uint16_t alerts_ = 0;
    bool announced_ = false;   // this absence episode has already been announced
};
