// U500 and U600 - distillation columns T1 and T2: the C++ twins of
// azeoplant/models/u500_u600_columns.py. One class, two sizings, like the
// Python; the split solved by bisection on a separation factor, the
// component holdup integrated in the drum and the column.
#pragma once

#include "azeocore/export.h"

#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "azeocore/devices.hpp"
#include "azeocore/dynamics.hpp"
#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"

namespace azeocore::units {

// (x_dist, x_bot) for a distillate fraction, feed composition and separation factor
AZEOCORE_API std::pair<double, double> solve_split(double feed_frac_dist, double z_feed, double sep_factor);

struct ColumnSpec {
    int n;                       // tag number prefix, 5 for T1 and 6 for T2
    const char* column;          // "T1" or "T2"
    double stages, alpha, drum_volume, boot_water_frac, sump_volume, column_volume;
    double reboiler_ua, condenser_ua, latent_kj_per_m3, steam_latent_kj_kg, dh_alpha_k;
    double weep_boilup, cp_liq_kj_kg, p_nominal, t_light, t_heavy, flood_dp, steam_max;
    bool has_water_boot;
    const char* dist_label;
    const char* bot_label;
};

class AZEOCORE_API DistillationColumn : public ProcessUnit {
public:
    const ColumnSpec spec;

    DistillationColumn(TagDatabase& db, ProcessBus& bus, double dt, const std::string& code, const std::string& name,
                       const ColumnSpec& spec);
    void step(double dt) override;
    Value save_state() const override;
    void load_state(const Value& s) override;
    std::vector<ParameterSpec> parameters() const override;

    // (flow_m3h, temperature_c, light_key_fraction) and the products on the bus
    virtual std::tuple<double, double, double> feed_stream() = 0;
    virtual void publish_products(double distillate, double bottoms) = 0;

    Tag *FT_feed, *FT_reflux, *FT_dist, *FT_bot, *FT_steam, *FT_cw, *FT_mf1, *FT_mf2;
    Tag *TT_feed, *TT_t1, *TT_t2, *TT_t3, *TT_ovhd, *TT_bot, *TT_cond;
    Tag *PT_ovhd, *PT_bot, *PT_p1, *PT_p2, *PDT, *LT_drum, *LT_sump, *LT_boot = nullptr, *AT_dist, *AT_bot, *ZT;
    Tag *IT_ra, *IT_rb, *IT_ba, *IT_bb;
    std::unique_ptr<SdvPackage> xv_feed;
    std::unique_ptr<PumpTrain> reflux_pumps, bottoms_pumps;
    std::unique_ptr<ControlValve> mf1, mf2;
    ControlValve v_reflux, v_dist, v_bot, v_steam, v_cw, v_press;
    std::optional<ControlValve> v_boot;
    Integrator drum, sump, boot, pressure;
    Lag boilup, reflux_flow, bot_flow;
    double x_drum = 0.985;       // light fraction of the drum liquid
    double x_col = 0.5;          // light fraction of the column liquid
    double y_top = 0.985;        // top vapour, what the condenser gets
    double x_bot_out = 0.02;     // bottom liquid, what the sump sends out
    double vapour_light = 0.0;   // light key made but not yet condensed
    Lag t_cond;
    double fouling_trays = 0.0;
    HeatExchanger hx_reboiler, hx_condenser;
    double cw_max = 1.0;
    double q_cond = 0.0;         // vapour condensed by the subcooled feed
    bool flooding = false;
    Transmitter tx_dist, tx_bot, tx_drum, tx_pdt;

protected:
    Tag& t(const std::string& name) { return *tags.at(name); }
    std::string tg(const char* prefix, const char* suffix) const;   // "FT-" + n + "001"
    std::vector<ParameterSpec> base_parameters() const;
};

class AZEOCORE_API ColumnT1 : public DistillationColumn {
public:
    static constexpr double SPLIT_TO_T2 = 0.85;
    static const ColumnSpec SPEC;
    ColumnT1(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    std::tuple<double, double, double> feed_stream() override;
    void publish_products(double distillate, double bottoms) override;
    std::vector<ParameterSpec> parameters() const override;
};

class AZEOCORE_API ColumnT2 : public DistillationColumn {
public:
    static const ColumnSpec SPEC;
    ColumnT2(TagDatabase& db, ProcessBus& bus, double dt = 0.1);
    std::tuple<double, double, double> feed_stream() override;
    void publish_products(double distillate, double bottoms) override;
};

}  // namespace azeocore::units
