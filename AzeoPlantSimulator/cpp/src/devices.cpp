#include "azeocore/devices.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore {

// ------------------------------------------------------------ valve gain
double valve_gain(ValveChar chr, double x, double rangeability) {
    x = clamp(x, 0.0, 1.0);
    switch (chr) {
    case ValveChar::Linear: return x;
    case ValveChar::EqualPercent: return x > 0.0 ? std::pow(rangeability, x - 1.0) : 0.0;
    case ValveChar::QuickOpening: return std::sqrt(x);
    }
    return x;
}

// ---------------------------------------------------------- ControlValve
ControlValve::ControlValve(std::string tag_, double cv_rated_, ValveChar chr_, double stroke_time_,
                           bool fail_closed_, double leakage_pct_, double rangeability_, double stiction_,
                           double hysteresis_, bool stuck_, double zero_shift_, double position_,
                           double positioner_time_, double positioner_overshoot_, double positioner_deadband_)
    : tag(std::move(tag_)), cv_rated(cv_rated_), chr(chr_), stroke_time(stroke_time_), fail_closed(fail_closed_),
      leakage_pct(leakage_pct_), rangeability(rangeability_), stiction(stiction_), hysteresis(hysteresis_),
      stuck(stuck_), zero_shift(zero_shift_), position(position_), positioner_time(positioner_time_),
      positioner_overshoot(positioner_overshoot_), positioner_deadband(positioner_deadband_),
      rl_(100.0 / std::max(stroke_time_, 0.05), 100.0 / std::max(stroke_time_, 0.05), position_),
      pos_sp_(position_), servo_x_(position_) {}

double ControlValve::step(double dt, double command_pct, bool air_failure) {
    if (air_failure) command_pct = fail_closed ? 0.0 : 100.0;
    const double cmd = clamp(command_pct + zero_shift, 0.0, 100.0);
    if (stuck) return position;

    if (hysteresis > 0.0) {
        const double half = hysteresis * 0.5;
        if (cmd > target_ + half) target_ = cmd - half;
        else if (cmd < target_ - half) target_ = cmd + half;
    } else {
        target_ = cmd;
    }

    // the positioner: deadband on the demand, then the servo (devices.py)
    double goal = target_;
    if (positioner_deadband > 0.0) {
        if (std::fabs(goal - pos_sp_) >= positioner_deadband) pos_sp_ = goal;
        goal = pos_sp_;
    } else {
        pos_sp_ = goal;
    }
    if (positioner_time > 0.0) {
        goal = servo(goal, dt);
    } else {
        servo_x_ = goal;
        servo_v_ = 0.0;
    }

    if (stiction > 0.0) {
        const double err = goal - position;
        if (std::fabs(err) < stiction) return position;
        rl_.y = position + std::copysign(stiction * 0.8, err);
    }

    position = clamp(rl_.step(goal, dt), 0.0, 100.0);
    return position;
}

// The second-order servo in closed form over the step (ControlValve._servo):
// the same expressions in the same order, through the runtime's exp, log,
// sin and cos, so both cores round identically.
double ControlValve::servo(double u, double dt) {
    const double wn = 1.0 / std::max(positioner_time, 1e-3);
    const double os = clamp(positioner_overshoot, 0.0, 90.0) / 100.0;
    const double d0 = servo_x_ - u;
    const double v0 = servo_v_;
    dt = std::max(dt, 0.0);
    double d, v;
    if (os > 0.0) {
        const double ln = py_log(os);
        const double zeta = -ln / std::sqrt(py_pow(3.141592653589793, 2.0) + ln * ln);
        const double a = zeta * wn;
        const double wd = wn * std::sqrt(1.0 - zeta * zeta);
        const double e = py_exp(-a * dt);
        const double c = py_cos(wd * dt);
        const double s = py_sin(wd * dt);
        const double b = (v0 + a * d0) / wd;
        d = e * (d0 * c + b * s);
        v = e * ((-a * d0 + wd * b) * c + (-a * b - wd * d0) * s);
    } else {
        const double e = py_exp(-wn * dt);
        d = e * (d0 + (v0 + wn * d0) * dt);
        v = e * (v0 - wn * (v0 + wn * d0) * dt);
    }
    servo_x_ = u + d;
    servo_v_ = v;
    return servo_x_;
}

double ControlValve::flow(double dp_bar, double sg) const {
    double frac = valve_gain(chr, position / 100.0, rangeability);
    frac = std::max(frac, leakage_pct / 100.0);
    return 0.865 * cv_rated * frac * safe_sqrt(std::max(dp_bar, 0.0) / std::max(sg, 0.05));
}

double ControlValve::gas_flow(double p_up_bara, double p_dn_bara, double sg, double t_k) const {
    double frac = valve_gain(chr, position / 100.0, rangeability);
    frac = std::max(frac, leakage_pct / 100.0);
    if (frac <= 0.0) return 0.0;
    const double p_up = std::max(p_up_bara, 0.05);
    const double p_dn = clamp(p_dn_bara, 0.0, p_up);
    const double x = std::min((p_up - p_dn) / p_up, XT);
    const double y = 1.0 - x / (3.0 * XT);
    return 417.0 * cv_rated * frac * p_up * y * safe_sqrt(x / std::max(sg * t_k, 1.0));
}

void ControlValve::clear_faults() {
    stiction = hysteresis = zero_shift = 0.0;
    stuck = false;
}

void ControlValve::set_state(double pos, double tgt, double rl) {
    position = clamp(pos, 0.0, 100.0);
    target_ = tgt;
    rl_.reset(rl);
}

// -------------------------------------------------------------------- MOV
const char* mov_state_name(MovState s) {
    switch (s) {
    case MovState::Closed: return "Closed";
    case MovState::Opening: return "Opening";
    case MovState::Open: return "Open";
    case MovState::Closing: return "Closing";
    case MovState::Stopped: return "Stopped";
    case MovState::Tripped: return "Tripped";
    }
    return "Closed";
}

MotorOperatedValve::MotorOperatedValve(std::string tag_, double travel_time_, double position_, bool remote_)
    : tag(std::move(tag_)), travel_time(travel_time_), position(position_), remote(remote_) {}

void MotorOperatedValve::command(bool open_cmd, bool close_cmd) {
    if (open_cmd && close_cmd) { cmd_open_ = cmd_close_ = false; }
    else { cmd_open_ = open_cmd; cmd_close_ = close_cmd; }
}

void MotorOperatedValve::reset() {
    torque_tripped = false;
    state = MovState::Stopped;
}

double MotorOperatedValve::step(double dt) {
    const double rate = 100.0 / std::max(travel_time * std::max(slow_travel_factor, 0.05), 0.5);

    if (torque_tripped) {
        state = MovState::Tripped;
    } else if (!remote) {
        state = MovState::Stopped;
    } else if (cmd_open_ && !fail_to_open) {
        if (position < 100.0) {
            position = std::min(100.0, position + rate * dt);
            state = MovState::Opening;
            if (torque_trip_at && position >= *torque_trip_at) torque_tripped = true;
        }
    } else if (cmd_close_) {
        if (position > 0.0) {
            position = std::max(0.0, position - rate * dt);
            state = MovState::Closing;
        }
    }

    if (drifts_closed && position > 0.0) position = std::max(0.0, position - rate * 0.15 * dt);

    if (!torque_tripped) {
        if (position >= 99.5) state = MovState::Open;
        else if (position <= 0.5) state = MovState::Closed;
        else if (!cmd_open_ && !cmd_close_) state = MovState::Stopped;
        else if (state == MovState::Open || state == MovState::Closed) state = MovState::Stopped;
    }
    return position;
}

double MotorOperatedValve::flow_fraction() const { return std::sqrt(clamp(position / 100.0, 0.0, 1.0)); }

void MotorOperatedValve::clear_faults() {
    fail_to_open = open_limit_faulty = drifts_closed = false;
    torque_trip_at.reset();
    slow_travel_factor = 1.0;
    torque_tripped = false;
}

// ------------------------------------------------------------------ Motor
Motor::Motor(std::string tag_, double rated_current_, double start_delay_, double coast_time_, bool vfd_)
    : tag(std::move(tag_)), rated_current(rated_current_), start_delay(start_delay_), coast_time(coast_time_),
      vfd(vfd_), speed_lag_(vfd_ ? 2.5 : 1.0, 0.0) {}

void Motor::command(bool start, bool stop) {
    if (stop) cmd_start_ = false;
    else if (start) cmd_start_ = true;
}

void Motor::trip() {
    faulted = true;
    cmd_start_ = false;
}

void Motor::step(double dt, bool permissive, double speed_ref_pct) {
    if (trip_on_overload) faulted = true;
    // Python: self.vfd and not self.vfd_comms_fault or not self.vfd
    vfd_healthy = (vfd && !vfd_comms_fault) || !vfd;

    bool want = cmd_start_ && permissive && !faulted && available;
    if (vfd && vfd_comms_fault) want = false;

    if (want && !running) {
        timer_ += dt;
        if (timer_ >= start_delay) { running = true; timer_ = 0.0; }
    } else if (!want && running) {
        running = false;
        timer_ = 0.0;
    } else if (want) {
        timer_ = 0.0;
    }

    const double target = running ? (vfd ? clamp(speed_ref_pct, 0.0, 100.0) : 100.0) : 0.0;
    speed_pct = speed_lag_.step(target, dt);

    const double ratio = current() / std::max(rated_current, 1.0);
    const double excess = ratio * ratio - 1.0;
    if (excess > 0.0 && running) thermal_pct += excess * 100.0 / HEAT_TAU * dt;
    else thermal_pct -= thermal_pct * dt / COOL_TAU;
    thermal_pct = clamp(thermal_pct, 0.0, 130.0);
    if (thermal_pct >= 100.0 && running) trip();
}

double Motor::current() const {
    if (speed_pct <= 0.5 && !running) return 0.0;
    const double n = speed_pct / 100.0;
    // std::pow, not n*n*n: Python's ``n ** 3`` is the library power, and the
    // two differ in the last bit, which the parity test sees on IT-1001
    const double run_amps = 0.25 + 0.75 * py_pow(n, 3.0) * clamp(load_frac, 0.0, 1.5);
    if (vfd) return rated_current * run_amps;
    const double inrush = 6.0 * std::max(1.0 - n / 0.95, 0.0);
    return rated_current * std::max(run_amps, inrush);
}

void Motor::restore(bool run, bool cmd, double spd, double load, double thermal) {
    running = run;
    cmd_start_ = cmd;
    speed_pct = spd;
    speed_lag_.reset(spd);
    load_frac = load;
    thermal_pct = thermal;
    timer_ = 0.0;
}

// ------------------------------------------------------------------- Pump
CentrifugalPump::CentrifugalPump(std::string tag_, double head_shutoff_, double flow_max_, double flow_min_,
                                 double rated_speed_pct_)
    : tag(std::move(tag_)), head_shutoff(head_shutoff_), flow_max(flow_max_), flow_min(flow_min_),
      rated_speed_pct(rated_speed_pct_) {}

double CentrifugalPump::head(double flow_m3h, double speed_pct) const {
    const double n = clamp(speed_pct / std::max(rated_speed_pct, 1.0), 0.0, 1.5);
    if (n <= 0.01) return 0.0;
    const double h0 = head_shutoff * (1.0 - wear_pct / 100.0) * n * n;
    const double q_max = flow_max * n;
    const double k = safe_div(h0, q_max * q_max, 0.0);
    const double q = std::max(flow_m3h, 0.0);
    return std::max(h0 - k * py_pow(q, 2.0), 0.0);   // Python: k * max(flow, 0) ** 2
}

double CentrifugalPump::discharge_pressure(double suction_barg, double flow_m3h, double speed_pct,
                                           double density) const {
    return suction_barg + head(flow_m3h, speed_pct) * density * 9.81 / 1e5;
}

bool CentrifugalPump::check_cavitation(double suction_barg, double vapour_pressure_barg, bool running) {
    const double margin = suction_barg - vapour_pressure_barg;
    cavitating = running && margin < 0.3;
    return cavitating;
}

// ---------------------------------------------------------- HeatExchanger
HeatExchanger::HeatExchanger(std::string tag_, double ua_clean_, double duty_max_)
    : tag(std::move(tag_)), ua_clean(ua_clean_), duty_max(duty_max_) {}

double HeatExchanger::ua() const { return ua_clean * (1.0 - clamp(fouling_pct, 0.0, MAX_FOULING) / 100.0); }

double HeatExchanger::capacity(double t_hot, double t_cold, double driver) {
    if (!(std::isfinite(t_hot) && std::isfinite(t_cold))) return 0.0;
    const double dt = std::max(t_hot - t_cold, 0.0);
    duty = clamp(ua() * dt * clamp(driver, 0.0, 1.5), 0.0, duty_max);
    return duty;
}

double HeatExchanger::transfer(double demand, double t_hot, double t_cold, double driver) {
    const double cap = capacity(t_hot, t_cold, driver);
    demand = std::isfinite(demand) ? std::max(demand, 0.0) : 0.0;
    limited = cap < demand;
    duty = std::min(demand, cap);
    return duty;
}

// ------------------------------------------------------------ Transmitter
const char* tx_failure_name(TxFailure f) {
    switch (f) {
    case TxFailure::None: return "none";
    case TxFailure::Frozen: return "frozen";
    case TxFailure::FailLow: return "fail_low";
    case TxFailure::FailHigh: return "fail_high";
    case TxFailure::OutOfService: return "out_of_service";
    }
    return "none";
}

std::optional<TxFailure> tx_failure_from(std::string_view name) {
    if (name == "none") return TxFailure::None;
    if (name == "frozen") return TxFailure::Frozen;
    if (name == "fail_low") return TxFailure::FailLow;
    if (name == "fail_high") return TxFailure::FailHigh;
    if (name == "out_of_service") return TxFailure::OutOfService;
    return std::nullopt;
}

Transmitter::Transmitter(std::string tag_, double lo_, double hi_, double tau_, double noise_sigma_pct_,
                         double update_period_, double transport_, double drift_pct_per_hour_, TxFailure failure_,
                         double extra_lag_, double walk_sigma_pct_, double walk_tau_, double damping_,
                         double offset_pct_)
    : tag(std::move(tag_)), lo(lo_), hi(hi_), tau(tau_), noise_sigma_pct(noise_sigma_pct_),
      update_period(update_period_), transport(transport_), drift_pct_per_hour(drift_pct_per_hour_),
      failure(failure_), extra_lag(extra_lag_), walk_sigma_pct(walk_sigma_pct_), walk_tau(walk_tau_),
      damping(damping_), offset_pct(offset_pct_), measured(lo_), lag_(tau_, lo_),
      noise_(0.0, 2.0, crc32(tag)), walk_(0.0, walk_tau_, crc32(tag)), damp_(std::max(damping_, 1e-6), lo_),
      held_(lo_), clock_(update_period_) {}

double Transmitter::span() const {
    const double s = hi - lo;
    return std::fabs(s) > 1e-12 ? s : 1.0;
}

double Transmitter::step(double dt, double true_value) {
    if (failure == TxFailure::OutOfService) { quality = QUALITY_BAD; return measured; }
    if (failure == TxFailure::Frozen) { quality = QUALITY_UNCERTAIN; return measured; }
    if (failure == TxFailure::FailLow) { measured = lo; quality = QUALITY_BAD; return measured; }
    if (failure == TxFailure::FailHigh) { measured = hi; quality = QUALITY_BAD; return measured; }

    drift_ += drift_pct_per_hour / 100.0 * span() * dt / 3600.0;
    noise_.sigma = noise_sigma_pct / 100.0 * span();

    const double t = tau + extra_lag;
    if (std::fabs(t - lag_.tau) > 1e-9) lag_.tau = std::max(t, 1e-6);

    if (transport > 0.0) {
        // built lazily, rebuilt when the settings change the dead time
        if (!pipe_ || std::fabs(pipe_->delay() - transport) > 1e-9) pipe_.emplace(transport, dt, true_value);
        true_value = pipe_->step(true_value);
    }
    const double filtered = lag_.step(true_value, dt);
    double raw = filtered + drift_ + noise_.step(dt);

    // the slow walk, the calibration error, then the damping over the lot;
    // each skipped rather than applied with a zero (devices.py)
    if (walk_sigma_pct > 0.0) {
        walk_.sigma = walk_sigma_pct / 100.0 * span();
        walk_.tau = std::max(walk_tau, 1e-3);
        raw += walk_.step(dt);
    }
    if (offset_pct != 0.0) raw += offset_pct / 100.0 * span();
    if (damping > 0.0) {
        if (std::fabs(damping - damp_.tau) > 1e-9) damp_.tau = damping;
        raw = damp_.step(raw, dt);
    } else {
        damp_.y = raw;
    }

    if (update_period > 0.0) {
        clock_ += dt;
        if (clock_ >= update_period) {
            clock_ -= update_period;
            held_ = raw;
        }
        raw = held_;
    }

    const double tol = 3.0 * noise_.sigma;
    if (lo - tol <= raw && raw < lo) raw = lo;
    else if (hi < raw && raw <= hi + tol) raw = hi;
    measured = clamp(raw, lo - OVER_RANGE * span(), hi + OVER_RANGE * span());
    quality = (lo <= measured && measured <= hi) ? QUALITY_GOOD : QUALITY_UNCERTAIN;
    return measured;
}

void Transmitter::clear_faults() {
    failure = TxFailure::None;
    drift_pct_per_hour = 0.0;
    drift_ = 0.0;
    extra_lag = 0.0;
}

// ------------------------------------------------------------- state
Value ControlValve::capture_state() const {
    Dict d;
    d["pos"] = position; d["tgt"] = target_; d["rl"] = rl_.y;
    d["stiction"] = stiction; d["hysteresis"] = hysteresis; d["stuck"] = stuck; d["zero"] = zero_shift;
    d["psp"] = pos_sp_; d["sx"] = servo_x_; d["sv"] = servo_v_;
    return d;
}
void ControlValve::apply_state(const Value& s) {
    const double pos = s.get_number("pos", position);
    set_state(pos, s.get_number("tgt", pos), s.get_number("rl", pos));
    stiction = s.get_number("stiction", stiction);
    hysteresis = s.get_number("hysteresis", hysteresis);
    stuck = s.get_bool("stuck", stuck);
    zero_shift = s.get_number("zero", zero_shift);
    // a snapshot from before the positioner existed parks it at the target
    pos_sp_ = s.get_number("psp", target_);
    servo_x_ = s.get_number("sx", target_);
    servo_v_ = s.get_number("sv", 0.0);
}

Value MotorOperatedValve::capture_state() const {
    Dict d;
    d["pos"] = position; d["cmd"] = cmd_open_; d["tripped"] = torque_tripped; d["remote"] = remote;
    return d;
}
void MotorOperatedValve::apply_state(const Value& s) {
    position = s.get_number("pos", position);
    cmd_open_ = s.get_bool("cmd", cmd_open_);
    torque_tripped = s.get_bool("tripped", torque_tripped);
    remote = s.get_bool("remote", remote);
}

Value Motor::state() const {
    Dict d;
    d["run"] = running ? 1.0 : 0.0; d["cmd"] = cmd_start_ ? 1.0 : 0.0;
    d["spd"] = speed_pct; d["load"] = load_frac; d["thermal"] = thermal_pct;
    return d;
}
void Motor::apply_state(const Value& s) {
    const bool run = s.get_number("run", 0.0) != 0.0;
    const bool cmd = s.has("cmd") ? s.get_number("cmd", 0.0) != 0.0 : run;
    restore(run, cmd, s.get_number("spd", run ? 100.0 : 0.0), s.get_number("load", 1.0), s.get_number("thermal", 0.0));
}

Value CentrifugalPump::capture_state() const { Dict d; d["wear"] = wear_pct; d["cav"] = cavitating; return d; }
void CentrifugalPump::apply_state(const Value& s) {
    wear_pct = s.get_number("wear", wear_pct);
    cavitating = s.get_bool("cav", cavitating);
}

Value HeatExchanger::capture_state() const {
    Dict d; d["fouling"] = fouling_pct; d["duty"] = duty; d["limited"] = limited; return d;
}
void HeatExchanger::apply_state(const Value& s) {
    fouling_pct = s.get_number("fouling", fouling_pct);
    duty = s.get_number("duty", duty);
    limited = s.get_bool("limited", limited);
}

Value Transmitter::capture_state() const {
    Dict d;
    d["pipe"] = pipe_ ? pipe_->capture_state() : Value(nullptr);
    d["lag"] = lag_.capture_state();
    d["noise"] = noise_.capture_state();
    d["walk"] = walk_.capture_state();
    d["damp"] = damp_.capture_state();
    d["drift"] = drift_; d["held"] = held_; d["clock"] = clock_;
    d["measured"] = measured; d["quality"] = quality;
    d["failure"] = std::string(tx_failure_name(failure));
    d["extra_lag"] = extra_lag; d["drift_rate"] = drift_pct_per_hour;
    return d;
}
void Transmitter::apply_state(const Value& s) {
    const Value& pipe = s.get("pipe");
    if (pipe.is_dict() && !pipe.get("buf").list().empty()) {
        std::vector<double> buf;
        for (const Value& x : pipe.get("buf").list()) if (x.is_number() || x.is_bool()) buf.push_back(x.number());
        if (!buf.empty()) {
            if (!pipe_ && transport > 0.0) {
                const std::size_t n = std::max<std::size_t>(buf.size(), 1);
                pipe_.emplace(transport, transport / static_cast<double>(n), buf.front());
            }
            if (pipe_) pipe_->set_buffer(buf);
        }
    }
    if (s.get("lag").is_dict()) lag_.apply_state(s.get("lag"));
    if (s.get("noise").is_dict()) noise_.apply_state(s.get("noise"));
    if (s.get("walk").is_dict()) walk_.apply_state(s.get("walk"));
    if (s.get("damp").is_dict()) damp_.apply_state(s.get("damp"));
    drift_ = s.get_number("drift", drift_);
    held_ = s.get_number("held", held_);
    clock_ = s.get_number("clock", clock_);
    measured = s.get_number("measured", measured);
    quality = static_cast<int>(s.get_number("quality", quality));
    if (s.has("failure")) { auto f = tx_failure_from(s.get("failure").string()); if (f) failure = *f; }
    extra_lag = s.get_number("extra_lag", extra_lag);
    drift_pct_per_hour = s.get_number("drift_rate", drift_pct_per_hour);
}

}  // namespace azeocore
