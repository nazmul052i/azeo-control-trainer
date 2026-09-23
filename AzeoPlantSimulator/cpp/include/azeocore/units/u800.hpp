// U800 effluent treatment and U900 safety instrumented system: the C++
// twins of azeoplant/models/u800_u900.py.
#pragma once

#include "azeocore/export.h"

#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

class AZEOCORE_API EffluentTreatment : public ProcessUnit {
public:
    static constexpr double TANK_VOLUME = 49.0;
    static constexpr double MIX_TAU_RUNNING = 45.0;
    static constexpr double MIX_TAU_STOPPED = 600.0;

    EffluentTreatment(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    std::vector<ParameterSpec> parameters() const override;
    static double ph_from_excess(double excess, double buffer_k = 0.004);

    Tag *AT8001, *AT8002, *AT8003, *FT8001, *TT8001, *LT8001, *IT8001, *QT8001, *IT8002, *IT8003;
    bool manual_caustic_open = true;
    std::unique_ptr<MotorPackage> agitator;
    std::unique_ptr<PumpTrain> transfer;
    ControlValve v_coarse, v_fine, v_acid, v_discharge, v_cw;
    Integrator excess_acid;
    Lag eff_flow;
    Integrator level;
    DeadTime ph_dead;
    Lag e4_lag, ph_lag;
    double inlet_ph = 4.2;
    Transmitter tx_ph;

private:
    Tag& t(const std::string& name) { return *tags.at(name); }
};

class AZEOCORE_API SafetySystem : public ProcessUnit {
public:
    struct Effect { const char* tag; const char* key; const char* desc; };
    static const std::vector<Effect>& effects();

    SafetySystem(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;

    std::vector<std::pair<std::string, bool>> initiators;   // in the Python dict's order
    // The pushbuttons, detectors and the field reset are set from outside
    // (the malfunction panel, the SIS reset action); the Python dict is
    // written directly, so this is the one write path both cores share.
    void set_initiator(const std::string& tag, bool state) {
        for (auto& kv : initiators) if (kv.first == tag) { kv.second = state; return; }
        initiators.emplace_back(tag, state);
    }

private:
    Tag& t(const std::string& name) { return *tags.at(name); }
};

}  // namespace azeocore::units
