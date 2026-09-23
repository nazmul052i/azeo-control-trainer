// U700 - steam boiler B1 and the MP steam header: the C++ twin of
// azeoplant/models/u700_boiler.py, shrink-and-swell included.
#pragma once

#include "azeocore/export.h"

#include <memory>

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

class AZEOCORE_API SteamBoiler : public ProcessUnit {
public:
    static constexpr double DRUM_VOLUME = 23.0;
    static constexpr double DRUM_SPAN_MM = 600.0;      // -300 to +300
    static constexpr double MP_HEADER_VOLUME = 340.0;
    static constexpr double DUTY_MAX = 80.0;           // t/h of steam
    static constexpr double AIR_MAX = 55.0;            // kNm3/h
    static constexpr double SWELL_GAIN = -14.0;        // mm per t/h step
    static constexpr double SWELL_TAU = 22.0;
    static constexpr double STEAM_LINE_K = 14.0;        // t/h per sqrt(bar) through the superheater

    SteamBoiler(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    std::vector<ParameterSpec> parameters() const override;

    Tag *LT7001, *LT7002, *PT7001, *PT7002, *PT7003, *PT7004, *FT7001, *FT7002, *FT7003, *FT7004, *FT7005, *FT7006;
    Tag *TT7001, *TT7002, *TT7003, *AT7001, *AT7002, *CT7001, *IT7003, *ZT7001, *IT7001, *IT7002;
    std::unique_ptr<SdvPackage> xv7001;
    std::unique_ptr<PumpTrain> bfw_pumps;
    std::unique_ptr<MotorPackage> fd_fan;
    ControlValve v_bfw, v_fuel, v_damper;
    Lag oil_lag, b2_lag;
    ControlValve v_blowdown, v_letdown, v_spray, v_da_makeup;
    Integrator inventory;
    Lag bfw_flow;
    Integrator drum_pressure, header_pressure, da_level;
    Lag steam;
    LeadLag swell;
    Lag superheat, flue_temp, o2, co;
    Integrator conductivity;
    bool lit = false;
    double tube_leak = 0.0;
    Transmitter tx_level, tx_steam, tx_o2;

private:
    Tag& t(const std::string& name) { return *tags.at(name); }
};

}  // namespace azeocore::units
