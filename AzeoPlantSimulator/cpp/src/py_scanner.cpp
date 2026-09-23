// The strategy's scan in C++: azeoplant/control/strategy.py's step() and
// _selectors(), compiled from the strategy's own tables.
//
// The strategy stays in Python and stays data-driven: it builds the
// loops, owns the tables the operator's menus change (schemes, ratios,
// anchors, shaping) and captures the state. What moves here is the
// per-scan work: for every loop in scan order, the PV source, the
// shaping hooks, the cascade setpoint through the selectors and
// cross-limits, the external-reset image, the block execution, the
// output conditioning, the split ranges and the overrides, and the
// checked write through the I/O bus. The plant-specific reasoning in
// the selectors is carried across line by line with its comments,
// because every one of them records a failure that was measured.
//
// The few loops that carry a Python callable (a computed PV, a gain
// scheduler, a deadtime predictor) call back into Python for that one
// value; the rest of the scan never leaves C++. The Python
// ControlSystem compiles a Scanner from its tables and recompiles when
// a menu changes one (azeoplant/control/native_scan.py).
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "azeocore/control/pid.hpp"
#include "azeocore/tags.hpp"

namespace py = pybind11;
using namespace azeocore;
using namespace azeocore::control;

// the I/O bus's checked DCS write, defined beside the bus binding
bool iobus_write_from_dcs(py::handle bus, const std::string& tag, double value, const std::string& source);

namespace {

// Native callback fields must participate in GC: the default computed PV
// captures ControlSystem, which owns this scanner and otherwise never dies.
template <typename T>
py::custom_type_setup callback_gc() {
    return py::custom_type_setup([](PyHeapTypeObject* heap_type) {
        auto* type = &heap_type->ht_type;
        type->tp_flags |= Py_TPFLAGS_HAVE_GC;
        type->tp_traverse = [](PyObject* object, visitproc visit, void* arg) {
            Py_VISIT(Py_TYPE(object));
            auto value = reinterpret_cast<py::detail::instance*>(object)->get_value_and_holder(
                py::detail::get_type_info(typeid(T)), false);
            if (!value.inst || !value.holder_constructed()) return 0;
            return value.template value_ptr<T>()->traverse(visit, arg);
        };
        type->tp_clear = [](PyObject* object) {
            auto value = reinterpret_cast<py::detail::instance*>(object)->get_value_and_holder(
                py::detail::get_type_info(typeid(T)), false);
            if (value.inst && value.holder_constructed()) value.template value_ptr<T>()->clear();
            return 0;
        };
    });
}

constexpr double CROSS_LIMIT_LEAD = 1.02;   // strategy._CROSS_LIMIT_LEAD

struct SplitLeg {
    std::string tag;
    double lo, hi, v0, v1;
};

struct LoopSpec {
    std::string module, pv_tag, out_tag, master, ff_tag;
    bool enabled = true;
    double out_cond = 0.0;
    std::vector<SplitLeg> split;
    std::optional<double> ratio;                       // a ratio station's ratio
    std::optional<std::pair<std::string, std::string>> pv_select, pv_diff;
    py::object pv_fn, gain_fn, predictor;              // none when absent
    std::string adapt_tag;                             // predictor's adaptive deadtime
    double adapt_theta = 0.0, adapt_ref = 0.0;
    std::optional<std::tuple<std::string, double, double>> pct_comp;   // (ptag, pnom, base)
    std::optional<std::pair<std::string, double>> pct_cas;             // (ptag, anchor)
    std::string bk_source;                             // module whose BKCAL feeds this master
    double cas_offset = 0.0;                           // what the selectors added to the master's demand (strategy.py)
    double scan_period = 0.2;                          // the module's scan class, seconds (Loop.scan_period)
    double scan_accum = 0.0;                           // time towards its next execution when slower than the tick
    py::object pid_obj;
    PID* pid = nullptr;
    // resolved once
    Tag *pv = nullptr, *out = nullptr, *ff = nullptr;

    int traverse(visitproc visit, void* arg) const {
        Py_VISIT(pv_fn.ptr());
        Py_VISIT(gain_fn.ptr());
        Py_VISIT(predictor.ptr());
        Py_VISIT(pid_obj.ptr());
        return 0;
    }

    void clear() {
        pid = nullptr;
        pv = out = ff = nullptr;
        pv_fn = gain_fn = predictor = pid_obj = py::object();
    }
};

class Scanner {
public:
    Scanner(py::object db_obj, py::object bus_obj)
        : db_obj_(std::move(db_obj)), bus_obj_(std::move(bus_obj)), db_(py::cast<TagDatabase*>(db_obj_)) {}

    // the strategy's shared tables
    double ryskamp_rmax = 4.0;
    std::unordered_map<int, bool> ryskamp_col;
    std::unordered_map<int, std::string> col_scheme;
    std::unordered_map<int, double> cw_base;
    double lhv_anchor = 38.5;
    std::unordered_map<int, std::vector<double>> col_ff;                    // n -> [f0, r0, s0]
    double draw_ff = 0.6;                                                  // ControlSystem.DRAW_FF
    std::unordered_map<int, std::unordered_map<std::string, double>> ff_anchors;
    double base_period = 0.2;                                              // the system tick (ControlSystem._base_period)

    py::dict scan_accums() const {
        py::dict d;
        for (const LoopSpec& l : specs_) d[py::str(l.module)] = l.scan_accum;
        return d;
    }

    void add(LoopSpec spec) {
        if (!db_) throw std::runtime_error("Scanner has been cleared");
        spec.pid = py::cast<PID*>(spec.pid_obj);
        spec.pv = db_->get(spec.pv_tag);
        spec.out = spec.out_tag.empty() ? nullptr : db_->get(spec.out_tag);
        spec.ff = spec.ff_tag.empty() ? nullptr : db_->get(spec.ff_tag);
        pids_[spec.module] = spec.pid;
        specs_.push_back(std::move(spec));
    }

    py::dict cas_offsets() const {
        py::dict d;
        for (const LoopSpec& l : specs_) if (!l.master.empty()) d[py::str(l.module)] = l.cas_offset;
        return d;
    }

    void set_cas_offsets(const py::dict& d) {
        for (LoopSpec& l : specs_) if (d.contains(py::str(l.module))) l.cas_offset = py::cast<double>(d[py::str(l.module)]);
    }

    double cas_offset_of(const std::string& module) const {
        for (const LoopSpec& l : specs_) if (l.module == module) return l.cas_offset;
        return 0.0;
    }

    void scan(double dt, bool enabled) {
        if (!db_) throw std::runtime_error("Scanner has been cleared");
        if (!enabled) {
            track();
            return;
        }
        for (LoopSpec& loop : specs_) {
            if (!loop.enabled) continue;
            // a module on a slower class than the tick waits its turn and
            // then integrates over the time it waited (strategy.py)
            double ldt = dt;
            if (loop.scan_period > base_period + 1e-9) {
                loop.scan_accum += dt;
                if (loop.scan_accum < loop.scan_period - 1e-9) continue;
                ldt = loop.scan_accum;
                loop.scan_accum = 0.0;
            }
            PID& pid = *loop.pid;
            // ratio stations: OUT is simply ratio * PV, published as a
            // cascade SP for the flow slave. Kept as a degenerate block so
            // the faceplate machinery treats it like everything else.
            if (loop.ratio) {
                const double pv = loop.pv->value;
                pid.pv = pv;
                double r = *loop.ratio;
                if (loop.module == "RC-4001") {
                    // the plant-wide VPC trims the additive ratio +-50 %
                    r *= 0.5 + pids_.at("QIC-5001")->out / 100.0;
                }
                pid.out = r * pv;
                continue;
            }
            double pv = loop.pv->value;
            bool pv_good = loop.pv->quality == 0;
            if (loop.pv_select) {
                pv = std::max(tag(loop.pv_select->first), tag(loop.pv_select->second));
            }
            if (loop.pv_diff) {
                pv = tag(loop.pv_diff->first) - tag(loop.pv_diff->second);
                pv_good = pv_good && db_->at(loop.pv_diff->second).quality == 0;
            }
            if (loop.pv_fn) pv = py::cast<double>(loop.pv_fn(db_obj_));
            // loop shaping around the block: scheduled gain, then the
            // deadtime predictor's PV correction (percent domain)
            if (loop.gain_fn) pid.gain = py::cast<double>(loop.gain_fn(loop.pid_obj));
            if (loop.predictor) {
                if (!loop.adapt_tag.empty()) {
                    // adaptive model: transport deadtime follows flow
                    const double f = std::max(tag(loop.adapt_tag), 1.0);
                    loop.predictor.attr("set_theta")(loop.adapt_theta * loop.adapt_ref / f);
                }
                double pvp = (pv - pid.pv_eu0) / pid.pv_span() * 100.0;
                pvp = py::cast<double>(loop.predictor.attr("correct")(pvp, pid.out, ldt));
                pv = pid.pv_eu0 + pvp / 100.0 * pid.pv_span();
            }
            if (loop.pct_comp) {
                const auto& [ptag, pnom, base] = *loop.pct_comp;
                pid.sp = base + 7.5 * (tag(ptag) - pnom);
            }
            std::optional<double> cas;
            if (!loop.master.empty()) {
                const PID& m = *pids_.at(loop.master);
                // master OUT is percent; a cascade SP arrives in the slave's
                // engineering units, spanned over the slave's PV scale.
                double c;
                if (master_is_ratio(loop.master)) c = m.out;
                else c = pid.pv_eu0 + m.out / 100.0 * pid.pv_span();
                cas = selectors(loop, c);
                loop.cas_offset = *cas - c;
            }
            const double ff = loop.ff ? loop.ff->value : 0.0;
            // a master with a slave gets the slave's BKCAL for external
            // reset - unless it has been re-pointed at a direct actuator
            // (the compressor MV menu), where the slave image is stale
            std::optional<double> bk;
            std::string bk_lim;
            if (!loop.bk_source.empty() && loop.out_tag.empty() && loop.split.empty()) {
                const PID& s = *pids_.at(loop.bk_source);
                if (loop.module == "PIC-6001") {
                    // this cascade SP passes through the condenser-duty
                    // selector, so the BKCAL must come back through the
                    // selector's INVERSE - the linear span map hands the
                    // reset network the wrong image of the slave (86 m3/h
                    // reads as 12 %, which through the selector means a
                    // quartered CW setpoint) and external reset then drags
                    // the master into the very runaway it should prevent.
                    bk = cw_out_from_flow(s.bkcal_out());
                } else if (loop.module == "TIC-3001") {
                    // The fuel slave's cascade demand is corrected for the
                    // inferred heating value in the selectors. Return BKCAL
                    // through that map's inverse; otherwise a normal change
                    // in fuel density looks like a downstream limit and the
                    // temperature master retains a standing offset. Actual
                    // air/skin/supply constraints still return the lower
                    // achieved flow and therefore still stop reset.
                    const double lhv_scale = std::max(0.85, std::min(1.25, lhv_anchor / std::max(tag("AY-3001"), 20.0)));
                    bk = (s.bkcal_out() / lhv_scale - s.pv_eu0) / s.pv_span() * 100.0;
                } else {
                    // the slave's selector offset comes back out (strategy.py)
                    bk = (s.bkcal_out() - cas_offset_of(loop.bk_source) - s.pv_eu0) / s.pv_span() * 100.0;
                }
                bk_lim = s.bkcal_limit();
            }
            double out = pid.step(ldt, pv, pv_good, cas, bk, bk_lim, ff);
            if (loop.out_cond != 0.0) {
                // conditioning shapes the valve signal only; the block,
                // its BKCAL and the faceplate keep the raw OUT
                const double kc = loop.out_cond;
                out = 100.0 * (1.0 - kc) * out / std::max(100.0 - kc * out, 1e-6);
            }
            if (!loop.split.empty()) {
                for (const SplitLeg& leg : loop.split) {
                    double val;
                    if (out <= leg.lo) val = leg.v0;
                    else if (out >= leg.hi) val = leg.v1;
                    else {
                        const double f = (out - leg.lo) / std::max(leg.hi - leg.lo, 1e-9);
                        val = leg.v0 + f * (leg.v1 - leg.v0);
                    }
                    if (loop.module == "AIC-8001" && leg.tag != "FCV-8003") {
                        // the RC-8001 structure of the reference: caustic
                        // dosing rides the effluent flow, so a load change
                        // is answered by ratio before pH ever moves
                        val = std::min(100.0, val * std::max(0.3, std::min(1.6, tag("FT-8001") / 40.0)));
                    }
                    write(leg.tag, val);
                }
            } else if (!loop.out_tag.empty()) {
                // High-select overrides land at the valve, past the loop that
                // normally owns it: the runaway override can force the quench
                // open and the discharge constraint can force recycle open.
                if (loop.module == "UIC-2001") {
                    out = std::max({out, pids_.at("PIC-2002")->out, pids_.at("TIC-2001")->out});
                } else if (loop.module == "FIC-4001") {
                    out = std::max(out, pids_.at("TIC-4002")->out);
                }
                write(loop.out_tag, out);
            }
        }
    }

    size_t size() const { return specs_.size(); }

    int traverse(visitproc visit, void* arg) const {
        Py_VISIT(db_obj_.ptr());
        Py_VISIT(bus_obj_.ptr());
        for (const auto& spec : specs_) {
            const int result = spec.traverse(visit, arg);
            if (result) return result;
        }
        return 0;
    }

    void clear() {
        // Disable raw-pointer access before decrefs can run Python finalizers.
        db_ = nullptr;
        pids_.clear();
        auto old_specs = std::move(specs_);
        specs_.clear();
        old_specs.clear();
        bus_obj_ = py::object();
        db_obj_ = py::object();
    }

private:
    py::object db_obj_, bus_obj_;
    TagDatabase* db_;
    std::vector<LoopSpec> specs_;
    std::unordered_map<std::string, PID*> pids_;

    double tag(const std::string& name) const { return db_->at(name).value; }

    bool master_is_ratio(const std::string& module) const {
        for (const LoopSpec& s : specs_)
            if (s.module == module) return s.ratio.has_value();
        return false;
    }

    // open loop: every block tracks so closing the loop again starts from the truth
    void track() {
        for (LoopSpec& loop : specs_) {
            PID& pid = *loop.pid;
            if (loop.pv) pid.pv = loop.pv->value;
            if (loop.pv_diff) pid.pv = tag(loop.pv_diff->first) - tag(loop.pv_diff->second);
            if (loop.pv_fn) pid.pv = py::cast<double>(loop.pv_fn(db_obj_));
            if (loop.out) pid.out = loop.out->value;
            pid.actual = pid.target_mode;
        }
    }

    void write(const std::string& name, double value) {
        if (!bus_obj_.is_none()) iobus_write_from_dcs(bus_obj_, name, value, "internal");
        else db_->at(name).value = value;
    }

    // Inverse of the FIC-6003 condenser-duty selector: the PIC-6001
    // output percent whose scaled CW setpoint equals ``flow``.
    double cw_out_from_flow(double flow) const {
        auto it = cw_base.find(6);
        const double base = it == cw_base.end() ? 0.0 : it->second;
        if (base <= 1e-6) return 50.0;
        const double scale = flow / base;
        if (scale <= 1.0) return 50.0 * std::max(scale, 0.15);
        return 50.0 + 50.0 * std::min((scale - 1.0) / 0.6, 1.0);
    }

    // The O2 trim output mapped onto the excess-air target, 1.05-1.25.
    double excess(const std::string& module) const { return 1.05 + 0.002 * pids_.at(module)->out; }

    // The FY-3002 / FY-7002 cross-limits and the override selectors,
    // applied to a slave's incoming cascade setpoint each scan.
    double selectors(const LoopSpec& loop, double cas) {
        const std::string& name = loop.module;
        const PID& pid = *loop.pid;
        const char col = name.size() > 4 ? name[4] : '\0';
        if ((name == "FIC-5001" || name == "FIC-6001") && ryskamp_col[col - '0']) {
            // Ryskamp: the temperature master sets the reflux RATIO and the
            // distillate flow multiplies it
            const std::string n(1, col);
            const PID& m = *pids_.at("TIC-" + n + "001");
            cas = m.out / 100.0 * ryskamp_rmax * tag("FT-" + n + "003");
        }
        if (name == "FIC-5002" || name == "FIC-6002") {
            // the reboiler low-level override: a sump below 30 % takes the
            // steam demand down with it rather than boiling the column dry
            const double sump = tag(std::string("LT-") + col + "002");
            if (sump < 30.0)
                cas = std::min(cas, pid.pv_eu0 + std::max(sump, 0.0) / 30.0 * std::max(cas - pid.pv_eu0, 0.0));
        }
        if (name == "FIC-6007") cas = std::max(cas, 2.0);
        if (name == "FIC-1001") {
            // charge is cut back when the heater runs out of fuel valve, and
            // capped by the drum pressure constraint controller
            const double fuel_frac = pids_.at("TIC-3001")->out / 100.0;
            if (fuel_frac > 0.88) {
                const double charge_pv = tag("FT-1001");
                cas = std::min(cas, charge_pv + (0.92 - fuel_frac) * 0.5 * pid.pv_span());
            }
            const PID& lim = *pids_.at("PIC-1001");
            return std::min(cas, pid.pv_eu0 + lim.out / 100.0 * pid.pv_span());
        }
        if (name == "FIC-5006") {
            // D1 high level floors the recycle so the drum cannot overfill
            const double d1 = tag("LT-1001");
            if (d1 > 70.0) {
                const double floor = pid.pv_eu0 + (d1 - 70.0) / 25.0 * pid.pv_span();
                cas = std::max(cas, std::min(floor, pid.pv_eu100));
            }
        }
        if (name == "FIC-5006" || name == "FIC-6006") {
            const int ncol = col - '0';
            auto sc = col_scheme.find(ncol);
            if (sc != col_scheme.end() && sc->second == "material") {
                // In the material scheme the temperature master moves the
                // draw, and a pure top pins the tray temperature where the
                // master cannot see a feed change; the draw rides the feed
                // by the seeded ratio, as reflux and steam already do.
                auto ffit = col_ff.find(ncol);
                if (ffit != col_ff.end() && ffit->second.size() > 3) {
                    const double f0 = ffit->second[0], d0 = ffit->second[3];
                    const double feed = tag(std::string("FT-") + col + "001");
                    const double corr = draw_ff * (feed - f0) * d0 / std::max(f0, 1.0);
                    cas = cas + std::max(-0.3 * d0, std::min(0.3 * d0, corr));
                }
            }
            // the bottoms draw can never exceed what the feed leaves behind
            double frac;
            if (col == '5') {
                const double add = tag("FT-4005");
                const double chg = std::max(tag("FT-1001"), 1.0);
                const double z_inf = 0.46 + 0.5 * (add / chg - 25.0 / 90.0);
                const double impurity = std::max(0.001, std::min(0.10, pids_.at("AIC-5001")->sp / 100.0));
                frac = std::max(0.40, std::min(0.50, z_inf / (1.0 - impurity)));
            } else {
                // T2's feed is a fixed 0.64 light content; the same honest cap as T1
                const double impurity = std::max(0.001, std::min(0.10, pids_.at("AIC-6001")->sp / 100.0));
                frac = std::min(0.85, 0.64 / (1.0 - impurity));
            }
            const double cap = frac * tag(std::string("FT-") + col + "001");
            cas = std::min(cas, std::max(cap, 2.0));
            return cas;
        }
        if (name == "FIC-6003") {
            // T2 pressure through condenser duty: the low half of the
            // master's output cuts the cooling water, the high half opens
            // the vent (see the split range)
            auto it = cw_base.find(6);
            const double base = it == cw_base.end() ? cas : it->second;
            const PID& m = *pids_.at("PIC-6001");
            if (m.out <= 50.0) return base * std::max(m.out / 50.0, 0.15);
            return base * (1.0 + 0.6 * (m.out - 50.0) / 50.0);
        }
        if (name == "FIC-3001") {
            // energy firing: the demand is corrected for the inferred heating
            // value, then cross-limited by the air actually available, the
            // tube-skin constraint and the fuel supply pressure
            cas = cas * std::max(0.85, std::min(1.25, lhv_anchor / std::max(tag("AY-3001"), 20.0)));
            const double air = tag("FT-3003");                     // kNm3/h
            cas = std::min(cas, air * 1000.0 / (9.6 * excess("AIC-3001")));
            cas = std::min(cas, pids_.at("TIC-3004")->out / 100.0 * pid.pv_eu100);
            const double avail = std::max(0.0, std::min(1.0, tag("PT-0102") / 2.0));
            cas = std::min(cas, pid.pv_eu100 * avail);
        } else if (name == "FIC-3003") {
            // the air leads the fuel: demand is the greater of what the
            // master asks and what is actually burning
            const PID& f = *pids_.at("FIC-3001");
            const double demand = f.pv_eu0 + pids_.at("TIC-3001")->out / 100.0 * f.pv_span();
            const double need = std::max(demand, tag("FT-3001"));   // Nm3/h
            cas = need * 9.6 * excess("AIC-3001") * CROSS_LIMIT_LEAD / 1000.0;
        } else if (name == "FIC-7002") {
            // B1 fuel is limited by the air present, less what the oil takes
            const double air = tag("FT-7004");                      // kNm3/h
            const double oil_air = tag("FT-7005") * 11.4;
            const double ex = excess("AIC-7001");
            cas = std::min(cas, std::max(air * 1000.0 / ex - oil_air, 0.0) / 9.6);
            const double avail = std::max(0.0, std::min(1.0, tag("PT-0103") / 3.0));
            cas = std::min(cas, pid.pv_eu100 * avail);
        } else if (name == "FIC-7003") {
            // B1 air leads the fuel, the CO override floors it
            const PID& f = *pids_.at("FIC-7002");
            const double demand = f.pv_eu0 + pids_.at("PIC-7001")->out / 100.0 * f.pv_span();
            const double need = std::max(demand, tag("FT-7003"));
            cas = (need * 9.6 + tag("FT-7005") * 11.4) * excess("AIC-7001") * CROSS_LIMIT_LEAD / 1000.0;
            const PID& co = *pids_.at("AIC-7002");
            cas = std::max(cas, co.out / 100.0 * pid.pv_eu100);
        } else if (loop.pct_cas) {
            // pressure-compensated temperature target
            cas = cas + 7.5 * (tag(loop.pct_cas->first) - loop.pct_cas->second);
        } else if (name.compare(0, 4, "FIC-") == 0 && (col == '5' || col == '6')
                   && (name.compare(5, std::string::npos, "001") == 0 || name.compare(5, std::string::npos, "002") == 0)) {
            // column feedforward: reflux and steam ride the feed, the steam
            // demand also the reflux (energy scheme), the feed temperature
            // and, on T2, the feed composition; the flooding constraint caps
            // the steam
            const int n = col - '0';
            const std::string ns(1, col);
            const std::vector<double>& ff = col_ff.at(n);
            const double f0 = ff[0], r0 = ff[1], s0 = ff[2];
            const double feed = tag("FT-" + ns + "001");
            if (name.compare(name.size() - 3, 3, "001") == 0) {
                // Reflux feedforward on the ENERGY scheme only (strategy.py)
                auto sc0 = col_scheme.find(n);
                if (sc0 != col_scheme.end() && sc0->second == "energy") {
                    const double corr = 0.6 * (feed - f0) * r0 / std::max(f0, 1.0);
                    cas = cas + std::max(-0.25 * r0, std::min(0.25 * r0, corr));
                }
                const double drum = tag("LT-" + ns + "001");
                if (drum < 30.0) cas = std::min(cas, r0 * std::max(0.15, drum / 30.0));
            } else {
                const double corr = 0.6 * (feed - f0) * s0 / std::max(f0, 1.0);
                cas = cas + std::max(-0.25 * s0, std::min(0.25 * s0, corr));
                auto sc = col_scheme.find(n);
                if (sc != col_scheme.end() && sc->second == "energy") {
                    const double reflux = tag("FT-" + ns + "002");
                    cas = cas + 0.12 * (s0 / std::max(r0, 1.0)) * (reflux - r0 * feed / std::max(f0, 1.0));
                }
                auto anch = ff_anchors.find(n);
                if (anch != ff_anchors.end()) {
                    auto tf = anch->second.find("tf");
                    if (tf != anch->second.end()) {
                        const double corr_t = 0.010 * s0 * (tf->second - tag("TT-" + ns + "001"));
                        cas = cas + std::max(-0.15 * s0, std::min(0.15 * s0, corr_t));
                    }
                    auto z = anch->second.find("z");
                    if (n == 6 && z != anch->second.end()) {
                        const double corr_z = 0.05 * s0 * (tag("AT-5001") - z->second);
                        cas = cas + std::max(-0.15 * s0, std::min(0.15 * s0, corr_z));
                    }
                }
                const PID& pd = *pids_.at("PDIC-" + ns + "001");
                cas = std::min(cas, pd.out / 100.0 * pid.pv_eu100);
            }
        }
        return cas;
    }
};

}  // namespace

void bind_scanner(py::module_& m) {
    py::class_<LoopSpec>(m, "LoopSpec", callback_gc<LoopSpec>())
        .def(py::init<>())
        .def_readwrite("module", &LoopSpec::module)
        .def_readwrite("pv_tag", &LoopSpec::pv_tag)
        .def_readwrite("out_tag", &LoopSpec::out_tag)
        .def_readwrite("master", &LoopSpec::master)
        .def_readwrite("ff_tag", &LoopSpec::ff_tag)
        .def_readwrite("enabled", &LoopSpec::enabled)
        .def_readwrite("out_cond", &LoopSpec::out_cond)
        .def_property("split", [](const LoopSpec& s) {
            py::list out;
            for (const auto& l : s.split) out.append(py::make_tuple(l.tag, l.lo, l.hi, l.v0, l.v1));
            return out;
        }, [](LoopSpec& s, const py::sequence& legs) {
            s.split.clear();
            for (auto leg : legs) {
                auto t = py::cast<py::tuple>(leg);
                s.split.push_back({py::cast<std::string>(t[0]), py::cast<double>(t[1]), py::cast<double>(t[2]),
                                   py::cast<double>(t[3]), py::cast<double>(t[4])});
            }
        })
        .def_readwrite("ratio", &LoopSpec::ratio)
        .def_readwrite("pv_select", &LoopSpec::pv_select)
        .def_readwrite("pv_diff", &LoopSpec::pv_diff)
        .def_readwrite("pv_fn", &LoopSpec::pv_fn)
        .def_readwrite("gain_fn", &LoopSpec::gain_fn)
        .def_readwrite("predictor", &LoopSpec::predictor)
        .def_readwrite("adapt_tag", &LoopSpec::adapt_tag)
        .def_readwrite("adapt_theta", &LoopSpec::adapt_theta)
        .def_readwrite("adapt_ref", &LoopSpec::adapt_ref)
        .def_readwrite("pct_comp", &LoopSpec::pct_comp)
        .def_readwrite("pct_cas", &LoopSpec::pct_cas)
        .def_readwrite("bk_source", &LoopSpec::bk_source)
        .def_readwrite("scan_period", &LoopSpec::scan_period)
        .def_readwrite("scan_accum", &LoopSpec::scan_accum)
        .def_readwrite("pid", &LoopSpec::pid_obj);

    py::class_<Scanner>(m, "Scanner", callback_gc<Scanner>())
        .def(py::init<py::object, py::object>(), py::arg("db"), py::arg("bus") = py::none())
        .def_readwrite("ryskamp_rmax", &Scanner::ryskamp_rmax)
        .def_readwrite("ryskamp_col", &Scanner::ryskamp_col)
        .def_readwrite("col_scheme", &Scanner::col_scheme)
        .def_readwrite("cw_base", &Scanner::cw_base)
        .def_readwrite("lhv_anchor", &Scanner::lhv_anchor)
        .def_readwrite("col_ff", &Scanner::col_ff)
        .def_readwrite("draw_ff", &Scanner::draw_ff)
        .def("cas_offsets", &Scanner::cas_offsets)
        .def("set_cas_offsets", &Scanner::set_cas_offsets)
        .def_readwrite("base_period", &Scanner::base_period)
        .def("scan_accums", &Scanner::scan_accums)
        .def_readwrite("ff_anchors", &Scanner::ff_anchors)
        .def("add", &Scanner::add, py::arg("spec"))
        .def("scan", &Scanner::scan, py::arg("dt"), py::arg("enabled") = true)
        .def("__len__", &Scanner::size);
}
