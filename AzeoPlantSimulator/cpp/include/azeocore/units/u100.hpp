// U100 - feed surge drum D1, charge pumps and suction MOVs: the C++ twin
// of azeoplant/models/u100_feed.py.
#pragma once

#include "azeocore/export.h"

#include <memory>

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

class AZEOCORE_API FeedSection : public ProcessUnit {
public:
    static constexpr double D1_AREA = 37.0;          // m2
    static constexpr double D1_HEIGHT = 5.4;         // m, so 200 m3 total
    static constexpr double DENSITY = 780.0;         // kg/m3
    static constexpr double VAPOUR_PRESSURE = 0.35;  // barg at operating temperature
    static constexpr double STATIC_HEAD = 0.25;      // bar from drum liquid level to pump suction

    FeedSection(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    std::vector<ParameterSpec> parameters() const override;

    // tags the step writes by handle
    Tag *LT1001, *PT1001, *PT1002, *PT1003, *PT1004, *PDT1001, *TT1001, *TT1002, *FT1001, *FT1002, *FT1003;
    Tag *ST1001, *IT1001, *IT1002, *VT1001, *DT1001, *ZT1001;
    Tag *FT1004, *PT1005, *TT1003, *AT1001;      // the battery-limit metering station
    // equipment and states, by the Python attribute names
    MotorOperatedValve mov_a, mov_b;
    std::unique_ptr<MovPackage> mov_recycle;   // built after the tags that precede it
    Motor motor_a, motor_b;
    CentrifugalPump pump_a, pump_b;
    ControlValve fcv1001, fcv1002, lcv1001;
    bool xv1001_open = true;
    double hv1001_position = 0.0;   // field manual drain, simulator HMI only
    Integrator level;
    Lag charge_flow, minflow;
    double strainer_dp = 0.05;
    bool local_a = false, local_b = false, mov_a_local = false, mov_b_local = false;
    double fresh_feed = 66.2;       // m3/h, instructor adjustable
    double fresh_temperature = 38.0, fresh_pressure = 6.0, fresh_lightfrac = 0.12;   // u100_feed.py
    Lag bearing_temp;
    Transmitter tx_level, tx_flow, tx_pdis, tx_psuc_a;
    Transmitter tx_fresh, tx_fresh_p, tx_fresh_t, tx_fresh_z;

private:
    void add_pump_io(const std::string& base, const std::string& tag, bool vfd);
    void add_mov_io(const std::string& base, const std::string& tag);
    Tag& t(const std::string& name) { return *tags.at(name); }
};

}  // namespace azeocore::units
