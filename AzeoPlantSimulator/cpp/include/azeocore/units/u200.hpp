// U200 - recycle gas compressor C1: the C++ twin of
// azeoplant/models/u200_compressor.py.
#pragma once

#include "azeocore/export.h"

#include <memory>

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

class AZEOCORE_API RecycleCompressor : public ProcessUnit {
public:
    static constexpr double SUCTION_VOLUME = 16.0;    // m3, V-201 plus suction line
    static constexpr double DISCHARGE_VOLUME = 26.0;  // m3
    static constexpr double RATED_SPEED = 12800.0;    // rpm
    static constexpr double RATED_FLOW = 80.0;        // kNm3/h, the surge line's scale: FT-2001's span
    static constexpr double HEAD_COEFF = 62.0;        // kJ/kg at rated speed, zero flow
    static constexpr double FLOW_COEFF = 0.0035;       // head loss per (kNm3/h)^2
    static constexpr double SURGE_SLOPE = 0.34;       // surge flow as a fraction of rated at full speed
    static constexpr double CHOKE_FLOW = 138.0;        // kNm3/h at full speed
    static constexpr double LINE_K = 8.7;             // kNm3/h per sqrt(bar) to H1

    RecycleCompressor(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    std::vector<ParameterSpec> parameters() const override;
    double surge_flow(double speed_frac, double mw, double gv_frac = 1.0) const;

    Tag *PT2001, *PT2002, *PT2003, *PDT2001, *TT2001, *TT2002, *TT2003, *TT2004, *TT2005, *FT2001, *FT2002;
    Tag *FT2003, *PT2004, *ST2001, *IT2001, *IT2002, *IT2003, *VT2001, *AT2001, *LT2001, *ZT2001, *UY2001;
    Tag *JT2001, *GT2001;
    std::unique_ptr<MotorPackage> motor, lube_main, lube_aux;
    std::unique_ptr<SdvPackage> xv2001;
    ControlValve antisurge, suction_throttle, cooler_cw, ko_drain, makeup_valve, discharge_throttle;
    Lag guide_vanes;
    Integrator p_suction, p_discharge, ko_level;
    Lag flow, t_discharge, bearing, lube_pressure, vibration;
    double surge_phase = 0.0;
    bool surging = false;
    double fouling_pct = 0.0;
    bool lube_decay = false;
    double h2_header = 25.0;   // barg
    Lag mw_lag;
    Transmitter tx_flow, tx_pdis, tx_vib;
    bool fault_flag = false;

private:
    Tag& t(const std::string& name) { return *tags.at(name); }
};

}  // namespace azeocore::units
