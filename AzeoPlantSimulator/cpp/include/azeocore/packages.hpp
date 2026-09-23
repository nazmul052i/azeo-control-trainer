// Reusable equipment packages: the C++ twins of azeoplant/models/packages.py.
//
// Each package creates its tags on the owning unit under the one naming
// convention, owns its devices and does the stepping. Its state dictionary
// is what the Python StatefulGroup's reflection would produce: every
// child that persists itself, plus the package's own plain scalars, minus
// the configuration names.
#pragma once

#include "azeocore/export.h"

#include <string>

#include "azeocore/devices.hpp"
#include "azeocore/state.hpp"
#include "azeocore/unit.hpp"

namespace azeocore {

struct AZEOCORE_API MovPackage : Stateful {
    ProcessUnit& unit;
    std::string tag;
    std::string service;
    MotorOperatedValve device;
    Tag* zso; Tag* zsc; Tag* trq; Tag* avl; Tag* cmd_open; Tag* cmd_close;
    bool local = false;
    bool simulate_enable = false;
    double simulate_value = 0.0;

    MovPackage(ProcessUnit& unit, std::string tag, std::string service, double travel_time = 25.0,
               double position = 0.0);
    double step(double dt);
    double fraction() const { return device.flow_fraction(); }
    bool is_open() const { return device.zso(); }
    Value state() const;
    void restore(const Value& s);
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

struct AZEOCORE_API SdvPackage : Stateful {
    ProcessUnit& unit;
    std::string tag;
    std::string service;
    double stroke;
    bool fail_open;
    double position;
    Tag* zso; Tag* zsc; Tag* cmd;

    SdvPackage(ProcessUnit& unit, std::string tag, std::string service, double stroke = 2.0, bool fail_open = false,
               bool initially_open = true);
    double step(double dt, bool trip = false, bool air_failure = false);
    bool is_open() const { return position >= 99.5; }
    double fraction() const { return clamp(position / 100.0, 0.0, 1.0); }
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

struct AZEOCORE_API MotorPackage : Stateful {
    ProcessUnit& unit;
    std::string tag;
    std::string service;
    bool vfd;
    Motor device;
    Tag* run; Tag* flt; Tag* avl; Tag* vfd_ok; Tag* cmd_start; Tag* cmd_stop;
    bool local = false;
    bool simulate_enable = false;
    double simulate_value = 0.0;

    MotorPackage(ProcessUnit& unit, std::string tag, std::string service, double rated_current = 100.0,
                 bool vfd = false, double start_delay = 1.0);
    void step(double dt, bool permissive = true, double speed_ref = 100.0, bool trip = false);
    bool running() const { return device.running; }
    double speed() const { return device.speed_pct; }
    double current() const { return device.current(); }
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

struct AZEOCORE_API PumpTrain : Stateful {
    std::string service;
    MovPackage mov_a, mov_b;
    MotorPackage motor_a, motor_b;
    CentrifugalPump pump_a, pump_b;
    double p_suction_a = 0.0, p_suction_b = 0.0;
    bool cav_a = false, cav_b = false;
    bool primed = true;

    PumpTrain(ProcessUnit& unit, std::string tag_a, std::string tag_b, std::string service, std::string mov_a,
              std::string mov_b, double head_shutoff, double flow_max, double flow_min, double rated_current = 100.0,
              bool vfd_a = false, double travel_time = 25.0, bool duty_running = false);
    void step(double dt, double suction_base, double flow, bool primed = true, double speed_ref = 100.0,
              double vapour_pressure = 0.3, double friction = 0.5, double design_flow = 100.0, bool trip = false);
    int running_count() const { return int(motor_a.running()) + int(motor_b.running()); }
    double discharge_pressure(double flow, double density = 780.0) const;
    bool any_running() const { return motor_a.running() || motor_b.running(); }
    Value state() const;
    void restore(const Value& s);
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

AZEOCORE_API ControlValve min_flow_valve(ProcessUnit& unit, const std::string& tag, const std::string& service, double cv = 60.0);

}  // namespace azeocore
