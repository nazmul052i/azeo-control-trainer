// U400 - reactor R1 and separator D3: the C++ twin of
// azeoplant/models/u400_reactor.py.
#pragma once

#include "azeocore/export.h"

#include <memory>

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

class AZEOCORE_API ReactorSection : public ProcessUnit {
public:
    static constexpr double BED_VOLUME = 35.0;          // m3 per bed
    static constexpr double BED_MASS_CP = 7.8e4;        // kJ/K, catalyst plus metal per bed
    static constexpr double DH_RXN = 1850.0;            // kJ per kg converted, exothermic
    static constexpr double EA = 78000.0;               // J/mol
    static constexpr double K0 = 3625.0;                // 1/s
    static constexpr double REACTIVE_FRACTION = 0.05;   // mass fraction of the feed that reacts
    static constexpr double GAS_TO_OIL = 0.17;          // kNm3/h per m3/h feed
    static constexpr double H2_CONS_K = 0.075;          // kNm3/h per m3/h feed at full conversion
    static constexpr double D3_VOLUME = 60.0;           // m3
    static constexpr double D3_AREA = 13.0;              // m2
    static constexpr double D3_WATER_FRAC = 0.012;      // share of the charge that is water

    ReactorSection(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    void apply(const Value& state) override;
    std::vector<ParameterSpec> parameters() const override;
    double conversion_of(double temp_c, double volumetric_flow_m3s) const;

    Tag *TT4001, *TT4002, *TT4003, *TT4004, *TT4005, *PT4001, *PT4002, *PDT4001, *FT4001, *FT4002, *FT4003;
    Tag *FT4004, *FT4005, *LT4001, *LT4002, *AT4001, *AT4002, *XY4010;
    std::unique_ptr<SdvPackage> xv4001, xv4002, bdv4001;
    ControlValve quench, cooler, v_add;
    Lag add_lag;
    Lag fresh_lag;   // the fresh feed's light key, arriving through the beds (u400_reactor.py)
    DeadTime add_dead;
    ControlValve lcv4001, lcv4002, pcv4001;
    Integrator bed1, bed2, pressure, d3_pressure, d3_level, d3_interface;
    Lag d3_temp;
    DeadTime impurity_dead;
    Lag conversion;
    double activity = 100.0;
    bool quench_failed = false;
    double fouled_pct = 0.0;
    Transmitter tx_bed1, tx_impurity, tx_interface, tx_level;

private:
    Tag& t(const std::string& name) { return *tags.at(name); }
};

}  // namespace azeocore::units
