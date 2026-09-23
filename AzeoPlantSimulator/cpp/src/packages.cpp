#include "azeocore/packages.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore {

// ------------------------------------------------------------ MovPackage
MovPackage::MovPackage(ProcessUnit& u, std::string tag_, std::string service_, double travel_time, double position)
    : unit(u), tag(std::move(tag_)), service(std::move(service_)), device(tag, travel_time, position) {
    const std::string b = base_of(tag);
    zso = &u.di("ZSO-" + b, tag + " open limit switch", "Not open", "Open", position >= 99.5);
    zsc = &u.di("ZSC-" + b, tag + " closed limit switch", "Not closed", "Closed", position <= 0.5);
    trq = &u.di("XS-" + b + "-TRQ", tag + " torque or thermal overload trip", "Healthy", "Tripped");
    avl = &u.di("XS-" + b + "-AVL", tag + " actuator in remote and available", "Local", "Remote", true);
    cmd_open = &u.do_("XY-" + b + "-OPN", tag + " open command from control room", "Idle", "Open", position >= 99.5);
    cmd_close = &u.do_("XY-" + b + "-CLS", tag + " close command from control room", "Idle", "Close");
}

double MovPackage::step(double dt) {
    device.remote = !local;
    device.command(cmd_open->effective() != 0.0, cmd_close->effective() != 0.0);
    const double pos = device.step(dt);
    if (simulate_enable) {
        zso->set(simulate_value >= 99.5 ? 1.0 : 0.0);
        zsc->set(simulate_value <= 0.5 ? 1.0 : 0.0);
    } else {
        zso->set(device.zso() ? 1.0 : 0.0);
        zsc->set(device.zsc() ? 1.0 : 0.0);
    }
    trq->set(device.torque_tripped ? 1.0 : 0.0);
    avl->set((!local && !device.torque_tripped) ? 1.0 : 0.0);
    return pos;
}

Value MovPackage::state() const {
    Dict d; d["pos"] = device.position; d["trip"] = device.torque_tripped ? 1.0 : 0.0; return d;
}

void MovPackage::restore(const Value& s) {
    device.position = s.get_number("pos", 0.0);
    device.torque_tripped = s.get_number("trip", 0.0) != 0.0;
}

Value MovPackage::capture_state() const {
    Dict d;
    d["simulate_enable"] = simulate_enable;
    d["simulate_value"] = simulate_value;
    d["device"] = device.capture_state();
    d["local"] = local;
    return d;
}

void MovPackage::apply_state(const Value& s) {
    if (s.get("device").is_dict()) device.apply_state(s.get("device"));
    if (s.has("local")) local = s.get("local").boolean(local);
    if (s.has("simulate_enable")) simulate_enable = s.get("simulate_enable").boolean(simulate_enable);
    if (s.has("simulate_value")) simulate_value = s.get("simulate_value").number(simulate_value);
}

// ------------------------------------------------------------ SdvPackage
SdvPackage::SdvPackage(ProcessUnit& u, std::string tag_, std::string service_, double stroke_, bool fail_open_,
                       bool initially_open)
    : unit(u), tag(std::move(tag_)), service(std::move(service_)), stroke(std::max(stroke_, 0.2)),
      fail_open(fail_open_), position(initially_open ? 100.0 : 0.0) {
    const std::string b = base_of(tag);
    zso = &u.di("ZSO-" + b, tag + " open limit switch", "Not open", "Open", initially_open);
    zsc = &u.di("ZSC-" + b, tag + " closed limit switch", "Not closed", "Closed", !initially_open);
    cmd = &u.do_("XY-" + b + "-OPN", tag + " open command", "Close", "Open", initially_open);
}

double SdvPackage::step(double dt, bool trip, bool air_failure) {
    const bool want = (trip || air_failure) ? fail_open : (cmd->effective() != 0.0);
    const double target = want ? 100.0 : 0.0;
    const double rate = 100.0 / stroke;
    if (position < target) position = std::min(target, position + rate * dt);
    else if (position > target) position = std::max(target, position - rate * dt);
    zso->set(position >= 99.5 ? 1.0 : 0.0);
    zsc->set(position <= 0.5 ? 1.0 : 0.0);
    return position;
}

Value SdvPackage::capture_state() const { Dict d; d["position"] = position; return d; }
void SdvPackage::apply_state(const Value& s) { if (s.has("position")) position = s.get("position").number(position); }

// ---------------------------------------------------------- MotorPackage
MotorPackage::MotorPackage(ProcessUnit& u, std::string tag_, std::string service_, double rated_current, bool vfd_,
                           double start_delay)
    : unit(u), tag(std::move(tag_)), service(std::move(service_)), vfd(vfd_),
      device(tag, rated_current, start_delay, 3.0, vfd_) {
    const std::string b = base_of(tag);
    run = &u.di("XS-" + b + "-RUN", tag + " running feedback", "Stopped", "Running");
    flt = &u.di("XS-" + b + "-FLT", tag + " fault or trip", "Healthy", "Faulted");
    avl = &u.di("XS-" + b + "-AVL", tag + " available and in remote", "Not avail", "Available", true);
    vfd_ok = vfd ? &u.di("XS-" + b + "-VFD", tag + " VFD healthy", "Fault", "Healthy", true) : nullptr;
    cmd_start = &u.do_("XY-" + b + "-STR", tag + " start command", "Idle", "Start");
    cmd_stop = &u.do_("XY-" + b + "-STP", tag + " stop command", "Idle", "Stop");
}

void MotorPackage::step(double dt, bool permissive, double speed_ref, bool trip) {
    if (trip) device.trip();
    device.available = !local;
    device.command(cmd_start->effective() != 0.0, cmd_stop->effective() != 0.0);
    device.step(dt, permissive && !trip, speed_ref);
    if (simulate_enable) run->set(simulate_value != 0.0 ? 1.0 : 0.0);
    else run->set(device.running ? 1.0 : 0.0);
    flt->set(device.faulted ? 1.0 : 0.0);
    avl->set((!local && !device.faulted) ? 1.0 : 0.0);
    if (vfd_ok) vfd_ok->set(device.vfd_healthy ? 1.0 : 0.0);
}

Value MotorPackage::capture_state() const {
    Dict d;
    d["device"] = device.capture_state();
    d["local"] = local;
    d["simulate_enable"] = simulate_enable;
    d["simulate_value"] = simulate_value;
    return d;
}

void MotorPackage::apply_state(const Value& s) {
    if (s.get("device").is_dict()) device.apply_state(s.get("device"));
    if (s.has("local")) local = s.get("local").boolean(local);
    if (s.has("simulate_enable")) simulate_enable = s.get("simulate_enable").boolean(simulate_enable);
    if (s.has("simulate_value")) simulate_value = s.get("simulate_value").number(simulate_value);
}

// -------------------------------------------------------------- PumpTrain
PumpTrain::PumpTrain(ProcessUnit& u, std::string tag_a, std::string tag_b, std::string service_, std::string mov_a_,
                     std::string mov_b_, double head_shutoff, double flow_max, double flow_min, double rated_current,
                     bool vfd_a, double travel_time, bool duty_running)
    : service(std::move(service_)),
      mov_a(u, std::move(mov_a_), tag_a + " suction isolation", travel_time, duty_running ? 100.0 : 0.0),
      mov_b(u, std::move(mov_b_), tag_b + " suction isolation", travel_time),
      motor_a(u, tag_a, service, rated_current, vfd_a),
      motor_b(u, tag_b, service + " (standby)", rated_current),
      pump_a(tag_a, head_shutoff, flow_max, flow_min), pump_b(tag_b, head_shutoff, flow_max, flow_min) {
    if (duty_running) {
        motor_a.device.running = true;
        motor_a.device.set_cmd_start(true);
        motor_a.cmd_start->value = 1.0;
        motor_a.device.reset_speed(100.0);
    }
}

void PumpTrain::step(double dt, double suction_base, double flow, bool primed_, double speed_ref, double vapour_pressure,
                     double friction, double design_flow, bool trip) {
    mov_a.step(dt);
    mov_b.step(dt);
    primed = primed_;
    auto suction = [&](const MovPackage& mov) {
        const double frac = std::max(mov.fraction(), 1e-3);
        const double q = std::max(flow, 0.0) / std::max(design_flow, 1.0);
        const double loss = friction * py_pow(q, 2.0) / py_pow(frac, 2.0);   // Python: friction * q ** 2 / frac ** 2
        return clamp(suction_base - loss, -0.9, 60.0);
    };
    p_suction_a = suction(mov_a);
    p_suction_b = suction(mov_b);
    motor_a.step(dt, true, speed_ref, trip);
    motor_b.step(dt, true, 100.0, trip);
    cav_a = pump_a.check_cavitation(p_suction_a, vapour_pressure, motor_a.running());
    cav_b = pump_b.check_cavitation(p_suction_b, vapour_pressure, motor_b.running());
    if (!primed) {
        if (motor_a.running()) { pump_a.cavitating = true; cav_a = true; }
        if (motor_b.running()) { pump_b.cavitating = true; cav_b = true; }
    }
    const double share = std::max(flow, 0.0) / std::max(running_count(), 1);
    auto load_motor = [&](CentrifugalPump& pump, MotorPackage& motor, bool cav) {
        const double n = std::max(motor.speed() / 100.0, 0.05);
        double load = clamp(share / std::max(pump.flow_max * n, 1.0), 0.05, 1.5);
        if (cav || !primed) load = 0.15;
        motor.device.load_frac = motor.running() ? load : 1.0;
    };
    load_motor(pump_a, motor_a, cav_a);
    load_motor(pump_b, motor_b, cav_b);
}

double PumpTrain::discharge_pressure(double flow, double density) const {
    double best_head = -1.0, best_suction = 0.0;
    auto consider = [&](const CentrifugalPump& pump, const MotorPackage& motor, double suction, bool cav) {
        double head = pump.head(flow, motor.speed());
        if (!primed) head = 0.0;
        else if (cav) head *= 0.45;
        if (head > best_head) { best_head = head; best_suction = suction; }
    };
    consider(pump_a, motor_a, p_suction_a, cav_a);
    consider(pump_b, motor_b, p_suction_b, cav_b);
    return best_suction + best_head * density * 9.81 / 1e5;
}

Value PumpTrain::state() const {
    Dict d;
    d["mov_a"] = mov_a.state(); d["mov_b"] = mov_b.state();
    d["motor_a"] = motor_a.device.state(); d["motor_b"] = motor_b.device.state();
    return d;
}

void PumpTrain::restore(const Value& s) {
    mov_a.restore(s.get("mov_a"));
    mov_b.restore(s.get("mov_b"));
    if (s.get("motor_a").is_dict()) motor_a.device.apply_state(s.get("motor_a"));
    else { Dict d; d["run"] = s.get_number("run_a", 0.0); motor_a.device.apply_state(d); }
    if (s.get("motor_b").is_dict()) motor_b.device.apply_state(s.get("motor_b"));
    else { Dict d; d["run"] = s.get_number("run_b", 0.0); motor_b.device.apply_state(d); }
}

Value PumpTrain::capture_state() const {
    Dict d;
    d["mov_a"] = mov_a.capture_state(); d["mov_b"] = mov_b.capture_state();
    d["motor_a"] = motor_a.capture_state(); d["motor_b"] = motor_b.capture_state();
    d["pump_a"] = pump_a.capture_state(); d["pump_b"] = pump_b.capture_state();
    d["p_suction_a"] = p_suction_a; d["p_suction_b"] = p_suction_b;
    d["cav_a"] = cav_a; d["cav_b"] = cav_b;
    d["_primed"] = primed;
    return d;
}

void PumpTrain::apply_state(const Value& s) {
    if (s.get("mov_a").is_dict()) mov_a.apply_state(s.get("mov_a"));
    if (s.get("mov_b").is_dict()) mov_b.apply_state(s.get("mov_b"));
    if (s.get("motor_a").is_dict()) motor_a.apply_state(s.get("motor_a"));
    if (s.get("motor_b").is_dict()) motor_b.apply_state(s.get("motor_b"));
    if (s.get("pump_a").is_dict()) pump_a.apply_state(s.get("pump_a"));
    if (s.get("pump_b").is_dict()) pump_b.apply_state(s.get("pump_b"));
    if (s.has("p_suction_a")) p_suction_a = s.get("p_suction_a").number(p_suction_a);
    if (s.has("p_suction_b")) p_suction_b = s.get("p_suction_b").number(p_suction_b);
    if (s.has("cav_a")) cav_a = s.get("cav_a").boolean(cav_a);
    if (s.has("cav_b")) cav_b = s.get("cav_b").boolean(cav_b);
    if (s.has("_primed")) primed = s.get("_primed").boolean(primed);
}

// ---------------------------------------------------------- min_flow_valve
ControlValve min_flow_valve(ProcessUnit& u, const std::string& tag, const std::string& service, double cv) {
    u.ao(tag, service, "%", 0.0, 100.0, 0.0);
    return ControlValve(tag, cv, ValveChar::Linear, 4.0, false);
}

}  // namespace azeocore
