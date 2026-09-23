// U010 - fuel gas header and utilities: the C++ twin of
// azeoplant/models/u010_fuel_gas.py. Same equations in the same order,
// same tags, same malfunctions, same snapshot.
#pragma once

#include "azeocore/export.h"

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

constexpr double P_STD = 1.01325;          // bara
constexpr double SUPPLY_PRESSURE = 32.0;   // bara at the battery limit

class AZEOCORE_API FuelGasHeader : public ProcessUnit {
public:
    static constexpr double HEADER_VOLUME = 60.0;   // m3
    static constexpr double LHV_NOMINAL = 38.5;     // MJ/Nm3
    static constexpr double CW_BASIN_VOLUME = 600.0;
    static constexpr double CW_DESIGN_FLOW = 2600.0;
    static constexpr double CW_OTHER_FLOW = 620.0;
    static constexpr double CW_TOWER_KAV_L = 1.20;

    FuelGasHeader(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    std::vector<ParameterSpec> parameters() const override;

    // read by the flowsheet after each step
    bool air_failed = false;

    // Members are declared in the order the Python build() creates them,
    // because members initialise in declaration order and the tag
    // database keeps creation order.
    Tag *PT0101, *PT0102, *PT0103, *PT0104, *FT0101, *FT0102, *TT0101, *AT0101, *AT0102, *TT0102;
    Tag *PSLL, *PSHH, *PSL_AIR, *PSL_CW, *LSLL_CW, *LSHH_CW;
    Tag *PCV0101, *PCV0102, *PCV0103, *PCV0104, *FCV0101, *SC0101, *SC0102, *LCV0101, *FCV0102;
    ControlValve v_import, v_flare, v_h1, v_b1, v_offgas, v_cw_makeup, v_cw_blowdown;
    MovPackage mov0101;
    Integrator pressure, b1_header;
    Lag lhv;
    double lhv_target;
    double lhv_import;
    bool import_available = true;
    bool air_comp_lost = false;
    Integrator air_receiver;
    Transmitter tx_pt0101, tx_ft0101, tx_ft0102;
    Tag* FT0103;
    Transmitter tx_ft0103;
    Tag *TT0103, *PT0105, *LT0101, *AT0103, *TT0104, *TT0105, *FT0104, *FT0105;
    Tag *IT0101, *IT0102, *IT0103, *IT0104;
    PumpTrain cw_pumps;
    MotorPackage ct_fan_a, ct_fan_b;
    Lag cw_supply_temp, cw_return_temp, cw_header_pressure;
    Integrator cw_basin_level, cw_solids;
    double tower_fouling_pct = 0.0;
    double wet_bulb_offset = 0.0;
    double fan_capacity_pct = 100.0;
    SdvPackage xv_import;

private:
    void step_cooling_water(double dt, bool air_failure);
};

}  // namespace azeocore::units
