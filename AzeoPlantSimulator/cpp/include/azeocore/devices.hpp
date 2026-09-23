// Field devices: the C++ twins of azeoplant/core/devices.py.
//
// Same equations, same state dictionaries, same faults. The transmitter
// seeds its noise from the CRC32 of its tag exactly as the Python one
// does, which is what makes a measured trace identical on both sides.
#pragma once

#include "azeocore/export.h"

#include <cstdint>
#include <optional>
#include <string>

#include "azeocore/dynamics.hpp"
#include "azeocore/state.hpp"

namespace azeocore {

enum class ValveChar { Linear, EqualPercent, QuickOpening };

AZEOCORE_API double valve_gain(ValveChar chr, double x, double rangeability = 50.0);

struct AZEOCORE_API ControlValve : Stateful {
    std::string tag;
    double cv_rated = 100.0;
    ValveChar chr = ValveChar::Linear;
    double stroke_time = 5.0;
    bool fail_closed = true;
    double leakage_pct = 0.0;
    double rangeability = 50.0;
    double stiction = 0.0;
    double hysteresis = 0.0;
    bool stuck = false;
    double zero_shift = 0.0;
    double position = 0.0;
    // the positioner loop: servo time constant (0 = ideal), overshoot on
    // a step in percent, deadband in percent of span - all zero by default
    double positioner_time = 0.0;
    double positioner_overshoot = 0.0;
    double positioner_deadband = 0.0;
    static constexpr double XT = 0.7;

    ControlValve(std::string tag, double cv_rated = 100.0, ValveChar chr = ValveChar::Linear,
                 double stroke_time = 5.0, bool fail_closed = true, double leakage_pct = 0.0,
                 double rangeability = 50.0, double stiction = 0.0, double hysteresis = 0.0,
                 bool stuck = false, double zero_shift = 0.0, double position = 0.0,
                 double positioner_time = 0.0, double positioner_overshoot = 0.0,
                 double positioner_deadband = 0.0);
    double step(double dt, double command_pct, bool air_failure = false);
    double flow(double dp_bar, double sg = 1.0) const;
    double gas_flow(double p_up_bara, double p_dn_bara, double sg = 0.65, double t_k = 300.0) const;
    void clear_faults();
    double target() const { return target_; }
    double rl_y() const { return rl_.y; }
    void set_state(double pos, double tgt, double rl);
    Value capture_state() const override;
    void apply_state(const Value& s) override;

private:
    double servo(double u, double dt);
    double target_ = 0.0;
    RateLimiter rl_;
    double pos_sp_ = 0.0;      // the setpoint the positioner is holding
    double servo_x_ = 0.0;     // servo output and velocity
    double servo_v_ = 0.0;
};

enum class MovState { Closed, Opening, Open, Closing, Stopped, Tripped };
AZEOCORE_API const char* mov_state_name(MovState s);

struct AZEOCORE_API MotorOperatedValve : Stateful {
    std::string tag;
    double travel_time = 25.0;
    double position = 0.0;
    bool remote = true;
    MovState state = MovState::Closed;
    bool fail_to_open = false;
    std::optional<double> torque_trip_at;
    bool open_limit_faulty = false;
    double slow_travel_factor = 1.0;
    bool drifts_closed = false;
    bool torque_tripped = false;

    MotorOperatedValve(std::string tag, double travel_time = 25.0, double position = 0.0, bool remote = true);
    void command(bool open_cmd, bool close_cmd);
    void reset();
    double step(double dt);
    bool zso() const { return position >= 99.5 && !open_limit_faulty; }
    bool zsc() const { return position <= 0.5; }
    double flow_fraction() const;
    void clear_faults();
    bool cmd_open() const { return cmd_open_; }
    bool cmd_close() const { return cmd_close_; }
    void set_commands(bool open_cmd, bool close_cmd) { cmd_open_ = open_cmd; cmd_close_ = close_cmd; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;

private:
    bool cmd_open_ = false;
    bool cmd_close_ = false;
};

struct AZEOCORE_API Motor : Stateful {
    std::string tag;
    double rated_current = 100.0;
    double start_delay = 1.0;
    double coast_time = 3.0;
    bool vfd = false;
    bool running = false;
    bool faulted = false;
    bool available = true;
    double speed_pct = 0.0;
    bool vfd_healthy = true;
    bool trip_on_overload = false;
    bool vfd_comms_fault = false;
    double load_frac = 1.0;
    double thermal_pct = 0.0;
    static constexpr double HEAT_TAU = 240.0;
    static constexpr double COOL_TAU = 900.0;

    Motor(std::string tag, double rated_current = 100.0, double start_delay = 1.0, double coast_time = 3.0,
          bool vfd = false);
    void command(bool start, bool stop);
    void trip();
    void reset() { faulted = false; }
    void step(double dt, bool permissive = true, double speed_ref_pct = 100.0);
    double current() const;
    void clear_faults() { trip_on_overload = vfd_comms_fault = false; }
    bool cmd_start() const { return cmd_start_; }
    double timer() const { return timer_; }
    void set_cmd_start(bool c) { cmd_start_ = c; }
    void reset_speed(double spd) { speed_pct = spd; speed_lag_.reset(spd); }
    void restore(bool run, bool cmd, double spd, double load, double thermal);
    Value state() const;
    Value capture_state() const override { return state(); }
    void apply_state(const Value& s) override;

private:
    double timer_ = 0.0;
    bool cmd_start_ = false;
    Lag speed_lag_;
};

struct AZEOCORE_API CentrifugalPump : Stateful {
    std::string tag;
    double head_shutoff = 280.0;
    double flow_max = 230.0;
    double flow_min = 25.0;
    double rated_speed_pct = 100.0;
    double wear_pct = 0.0;
    bool cavitating = false;

    CentrifugalPump(std::string tag, double head_shutoff = 280.0, double flow_max = 230.0, double flow_min = 25.0,
                    double rated_speed_pct = 100.0);
    double head(double flow_m3h, double speed_pct) const;
    double discharge_pressure(double suction_barg, double flow_m3h, double speed_pct, double density = 780.0) const;
    bool check_cavitation(double suction_barg, double vapour_pressure_barg, bool running);
    void clear_faults() { wear_pct = 0.0; cavitating = false; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

struct AZEOCORE_API HeatExchanger : Stateful {
    std::string tag;
    double ua_clean;
    double duty_max = 1e9;
    double fouling_pct = 0.0;
    double duty = 0.0;
    bool limited = false;
    static constexpr double MAX_FOULING = 95.0;

    HeatExchanger(std::string tag, double ua_clean, double duty_max = 1e9);
    double ua() const;
    double capacity(double t_hot, double t_cold, double driver = 1.0);
    double transfer(double demand, double t_hot, double t_cold, double driver = 1.0);
    void clear_faults() { fouling_pct = 0.0; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

enum class TxFailure { None, Frozen, FailLow, FailHigh, OutOfService };
AZEOCORE_API const char* tx_failure_name(TxFailure f);
AZEOCORE_API std::optional<TxFailure> tx_failure_from(std::string_view name);

// mirrors azeoplant.core.tags: Quality codes and the 4-20 mA over-range band
constexpr int QUALITY_GOOD = 0;
constexpr int QUALITY_UNCERTAIN = 1;
constexpr int QUALITY_BAD = 2;
constexpr double OVER_RANGE = 0.02;

class AZEOCORE_API Transmitter : public Stateful {
public:
    std::string tag;
    double lo = 0.0;
    double hi = 100.0;
    double tau = 0.5;
    double noise_sigma_pct = 0.0;
    double update_period = 0.0;
    double transport = 0.0;
    double drift_pct_per_hour = 0.0;
    TxFailure failure = TxFailure::None;
    double extra_lag = 0.0;
    // the instrument model around the noise band, all zero by default:
    // the slow walk (% span, time constant), the transmitter's damping
    // after the noise, and a calibration offset (% span)
    double walk_sigma_pct = 0.0;
    double walk_tau = 600.0;
    double damping = 0.0;
    double offset_pct = 0.0;
    double measured = 0.0;
    int quality = QUALITY_GOOD;

    Transmitter(std::string tag, double lo = 0.0, double hi = 100.0, double tau = 0.5, double noise_sigma_pct = 0.0,
                double update_period = 0.0, double transport = 0.0, double drift_pct_per_hour = 0.0,
                TxFailure failure = TxFailure::None, double extra_lag = 0.0, double walk_sigma_pct = 0.0,
                double walk_tau = 600.0, double damping = 0.0, double offset_pct = 0.0);
    double span() const;
    double step(double dt, double true_value);
    void clear_faults();
    Value capture_state() const override;
    void apply_state(const Value& s) override;

    Lag& lag() { return lag_; }
    Noise& noise() { return noise_; }
    const Lag& lag() const { return lag_; }
    const Noise& noise() const { return noise_; }
    RandomWalk& walk() { return walk_; }
    Lag& damp() { return damp_; }
    std::optional<DeadTime>& pipe() { return pipe_; }
    double drift() const { return drift_; }
    double held() const { return held_; }
    double clock() const { return clock_; }
    void set_internal(double drift, double held, double clock) { drift_ = drift; held_ = held; clock_ = clock; }

private:
    Lag lag_;
    std::optional<DeadTime> pipe_;
    Noise noise_;
    RandomWalk walk_;
    Lag damp_;
    double drift_ = 0.0;
    double held_ = 0.0;
    double clock_ = 0.0;
};

}  // namespace azeocore
