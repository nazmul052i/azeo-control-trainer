// U300 - fired heater H1: the C++ twin of azeoplant/models/u300_heater.py.
#pragma once

#include "azeocore/export.h"

#include <memory>

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

class AZEOCORE_API FiredHeater : public ProcessUnit {
public:
    static constexpr double BURNER_HEADER_VOLUME = 5.0;   // m3
    static constexpr double DUTY_MAX = 30.0;              // MW
    static constexpr double TAU_OUTLET = 105.0;           // s
    static constexpr double DEADTIME_OUTLET = 28.0;       // s
    static constexpr double E5_EFF = 0.60;                // feed/product exchanger effectiveness
    static constexpr double AIR_MAX = 45.0;               // kNm3/h at full damper and full fan speed

    FiredHeater(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    std::vector<ParameterSpec> parameters() const override;
    static double efficiency(double excess_air);

    Tag *TT3001, *TT3002, *TT3003, *TT3004, *TT3005, *TT3006, *FT3001, *FT3002, *FT3003, *FT3004, *FT3005;
    Tag *PT3001, *PT3002, *PT3003, *AT3001, *AT3002, *IT3001, *TT3007, *AY3001, *ZT3001, *ZT3002, *ZT3003;
    ControlValve fcv3001, fcv3002, fcv3003, damper, pass1, pass2;
    Integrator coke;
    Lag e5_byp, ay_lag;
    Integrator burner_pressure;
    Lag duty, t_out;
    DeadTime t_dead;
    Lag t_pass1, t_pass2, skin1, skin2, o2, co, stack;
    bool lit = false;
    double efficiency_loss = 0.0;
    bool fuel_oil_available = false;
    std::unique_ptr<SdvPackage> xv_oil;
    double purge_timer = 0.0;
    bool purged = false;
    Lag oil_pressure;
    bool id_fan_faulted = false;
    Transmitter tx_tout, tx_o2, tx_fuel, tx_skin;

private:
    Tag& t(const std::string& name) { return *tags.at(name); }
};

}  // namespace azeocore::units
