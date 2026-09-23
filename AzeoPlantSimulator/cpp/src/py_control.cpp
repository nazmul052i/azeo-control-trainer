// Python bindings for the control layer: the PID block.
//
// The block hands back the Python Mode and Structure enum members
// (registered by azeoplant.core.native), so identity checks in the
// strategy and the faceplates keep working; its alarm enable and shelve
// maps are dict-like views onto the block's own storage, so the
// faceplate's ``pid.alarm_enab[key] = v`` lands where the scan reads it.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <functional>
#include <map>

#include "azeocore/control/pid.hpp"
#include "py_state.hpp"

namespace py = pybind11;
using namespace azeocore;
using namespace azeocore::control;
using azeocore::pyconv::from_py;
using azeocore::pyconv::to_py;

namespace {

py::object g_mode_cls;        // azeoplant.control.pid.Mode
py::object g_structure_cls;   // azeoplant.control.pid.Structure
py::object g_mode_members[8];       // the members themselves, looked up once:
py::object g_structure_members[8];  // the strategy reads a mode 90 times a scan

py::object to_py_mode(Mode m) {
    const auto& cached = g_mode_members[static_cast<int>(m)];
    if (cached) return cached;
    if (g_mode_cls) return g_mode_cls(py::str(mode_value(m)));
    return py::str(mode_value(m));
}

Mode mode_from_py(const py::handle& o) {
    std::string v;
    if (py::hasattr(o, "value")) v = py::cast<std::string>(o.attr("value"));
    else v = py::cast<std::string>(o);
    auto m = mode_from(v);
    if (!m) throw py::value_error("unknown PID mode " + v);
    return *m;
}

py::object to_py_structure(Structure s) {
    const auto& cached = g_structure_members[static_cast<int>(s)];
    if (cached) return cached;
    if (g_structure_cls) return g_structure_cls(py::str(structure_value(s)));
    return py::str(structure_value(s));
}

Structure structure_from_py(const py::handle& o) {
    std::string v;
    if (py::hasattr(o, "value")) v = py::cast<std::string>(o.attr("value"));
    else v = py::cast<std::string>(o);
    auto s = structure_from(v);
    if (!s) throw py::value_error("unknown PID structure " + v);
    return *s;
}

// a dict-like view on one of the block's BoolMaps
struct BoolMapView {
    py::object owner;   // the block's Python object, kept alive by the view
    PID* pid;
    bool shelved;
    BoolMap& map() const { return shelved ? pid->alarm_shelved : pid->alarm_enab; }
};

void fill_from_dict(BoolMap& m, const py::handle& d) {
    m.clear();
    for (auto item : py::cast<py::dict>(d)) m.set(py::cast<std::string>(item.first), py::cast<bool>(item.second));
}

void fill_from_dict(NumMap& m, const py::handle& d) {
    m.clear();
    for (auto item : py::cast<py::dict>(d)) m.set(py::cast<std::string>(item.first), py::cast<double>(item.second));
}

// a copy, not a view: the numeric maps are assigned whole, as the strategy does
py::dict to_py_dict(const NumMap& m) {
    py::dict d;
    for (const auto& [k, v] : m.items) d[py::str(k)] = v;
    return d;
}

using Setter = std::function<void(PID&, const py::handle&)>;

const std::map<std::string, Setter>& setters() {
    static const std::map<std::string, Setter> table = {
#define NUM(f) {#f, [](PID& p, const py::handle& v) { p.f = py::cast<double>(v); }}
#define BOOL(f) {#f, [](PID& p, const py::handle& v) { p.f = py::cast<bool>(v); }}
#define STR(f) {#f, [](PID& p, const py::handle& v) { p.f = py::cast<std::string>(v); }}
        STR(name), STR(description),
        NUM(pv_eu0), NUM(pv_eu100), NUM(out_eu0), NUM(out_eu100),
        NUM(gain), NUM(reset), NUM(rate), NUM(beta), NUM(gamma), NUM(pv_ftime), NUM(sp_ftime),
        NUM(sp_hi_lim), NUM(sp_lo_lim), NUM(sp_rate_up), NUM(sp_rate_dn), NUM(out_hi_lim), NUM(out_lo_lim),
        BOOL(direct_acting), BOOL(sp_pv_track_in_man), BOOL(use_pv_for_bkcal), BOOL(ff_enable), NUM(ff_gain),
        NUM(hi_hi_lim), NUM(hi_lim), NUM(lo_lim), NUM(lo_lo_lim), NUM(dv_hi_lim), NUM(dv_lo_lim),
        NUM(alarm_hys), NUM(arw_hi_lim), NUM(arw_lo_lim), BOOL(simulate_enable), NUM(simulate_value),
        NUM(sp), NUM(sp_wrk), NUM(out), NUM(pv), BOOL(pv_good), NUM(field_value), BOOL(field_good),
#undef NUM
#undef BOOL
#undef STR
        {"structure", [](PID& p, const py::handle& v) { p.structure = structure_from_py(v); }},
        {"target_mode", [](PID& p, const py::handle& v) { p.target_mode = mode_from_py(v); }},
        {"alarm_enab", [](PID& p, const py::handle& v) { fill_from_dict(p.alarm_enab, v); }},
        {"alarm_shelved", [](PID& p, const py::handle& v) { fill_from_dict(p.alarm_shelved, v); }},
        {"alarm_delay", [](PID& p, const py::handle& v) { fill_from_dict(p.alarm_delay, v); }},
    };
    return table;
}

}  // namespace

void bind_control(py::module_& m) {
    m.def("register_control_enums", [](py::object mode, py::object structure) {
        g_mode_cls = std::move(mode);
        g_structure_cls = std::move(structure);
        for (int i = 0; i < 8; ++i) {
            g_mode_members[i] = g_mode_cls(py::str(mode_value(static_cast<Mode>(i))));
            g_structure_members[i] = g_structure_cls(py::str(structure_value(static_cast<Structure>(i))));
        }
    }, py::arg("mode"), py::arg("structure"),
    "Register azeoplant.control.pid.Mode and Structure so the native block hands back the Python enums.");
    m.add_object("_release_control_enums", py::capsule([]() {
        for (int i = 0; i < 8; ++i) { g_mode_members[i] = py::object(); g_structure_members[i] = py::object(); }
        g_mode_cls = py::object(); g_structure_cls = py::object();
    }));

    py::class_<PidAlarms>(m, "PidAlarms")
        .def(py::init<>())
        .def_readwrite("hi_hi", &PidAlarms::hi_hi)
        .def_readwrite("hi", &PidAlarms::hi)
        .def_readwrite("lo", &PidAlarms::lo)
        .def_readwrite("lo_lo", &PidAlarms::lo_lo)
        .def_readwrite("dv_hi", &PidAlarms::dv_hi)
        .def_readwrite("dv_lo", &PidAlarms::dv_lo)
        .def("any_active", &PidAlarms::any_active);

    py::class_<BoolMapView>(m, "PidBoolMap")
        .def("__getitem__", [](const BoolMapView& v, const std::string& k) {
            if (!v.map().has(k)) throw py::key_error(k);
            return v.map().get(k, false);
        })
        .def("__setitem__", [](BoolMapView& v, const std::string& k, bool b) { v.map().set(k, b); })
        .def("__delitem__", [](BoolMapView& v, const std::string& k) {
            if (!v.map().has(k)) throw py::key_error(k);
            v.map().erase(k);
        })
        .def("__contains__", [](const BoolMapView& v, const std::string& k) { return v.map().has(k); })
        .def("__len__", [](const BoolMapView& v) { return v.map().items.size(); })
        .def("__iter__", [](const BoolMapView& v) {
            py::list keys;
            for (const auto& [k, b] : v.map().items) keys.append(k);
            return py::iter(keys);
        })
        .def("__eq__", [](const BoolMapView& v, const py::handle& other) {
            return to_py(v.map().to_value()).equal(other);
        })
        .def("__repr__", [](const BoolMapView& v) { return py::repr(to_py(v.map().to_value())); })
        .def("get", [](const BoolMapView& v, const std::string& k, py::object dflt) -> py::object {
            if (!v.map().has(k)) return dflt;
            return py::bool_(v.map().get(k, false));
        }, py::arg("key"), py::arg("default") = py::none())
        .def("keys", [](const BoolMapView& v) {
            py::list keys;
            for (const auto& [k, b] : v.map().items) keys.append(k);
            return keys;
        })
        .def("values", [](const BoolMapView& v) {
            py::list vals;
            for (const auto& [k, b] : v.map().items) vals.append(b);
            return vals;
        })
        .def("items", [](const BoolMapView& v) {
            py::list out;
            for (const auto& [k, b] : v.map().items) out.append(py::make_tuple(k, b));
            return out;
        })
        .def("update", [](BoolMapView& v, const py::dict& d) {
            for (auto item : d) v.map().set(py::cast<std::string>(item.first), py::cast<bool>(item.second));
        })
        .def("pop", [](BoolMapView& v, const std::string& k, py::object dflt) -> py::object {
            if (!v.map().has(k)) {
                if (dflt.is_none()) throw py::key_error(k);
                return dflt;
            }
            const bool b = v.map().get(k, false);
            v.map().erase(k);
            return py::bool_(b);
        }, py::arg("key"), py::arg("default") = py::none())
        .def("clear", [](BoolMapView& v) { v.map().clear(); })
        .def("copy", [](const BoolMapView& v) { return to_py(v.map().to_value()); });

    py::class_<PID> pid(m, "PID", py::dynamic_attr());
    pid.def(py::init([](const py::kwargs& kw) {
            auto p = std::make_unique<PID>();
            const auto& table = setters();
            for (auto item : kw) {
                const std::string key = py::cast<std::string>(item.first);
                auto it = table.find(key);
                if (it == table.end()) throw py::type_error("PID() got an unexpected keyword argument '" + key + "'");
                it->second(*p, item.second);
            }
            p->post_init();
            return p;
        }))
#define RW(f) .def_readwrite(#f, &PID::f)
        RW(name) RW(description) RW(pv_eu0) RW(pv_eu100) RW(out_eu0) RW(out_eu100)
        RW(gain) RW(reset) RW(rate) RW(beta) RW(gamma) RW(pv_ftime) RW(sp_ftime)
        RW(sp_hi_lim) RW(sp_lo_lim) RW(sp_rate_up) RW(sp_rate_dn) RW(out_hi_lim) RW(out_lo_lim)
        RW(direct_acting) RW(sp_pv_track_in_man) RW(use_pv_for_bkcal) RW(ff_enable) RW(ff_gain)
        RW(hi_hi_lim) RW(hi_lim) RW(lo_lim) RW(lo_lo_lim) RW(dv_hi_lim) RW(dv_lo_lim)
        RW(alarm_hys) RW(arw_hi_lim) RW(arw_lo_lim) RW(simulate_enable) RW(simulate_value)
        RW(sp) RW(sp_wrk) RW(out) RW(pv) RW(pv_good) RW(field_value) RW(field_good)
        RW(alarms)
#undef RW
        .def_property("structure", [](const PID& p) { return to_py_structure(p.structure); },
                      [](PID& p, const py::handle& v) { p.structure = structure_from_py(v); })
        .def_property("target_mode", [](const PID& p) { return to_py_mode(p.target_mode); },
                      [](PID& p, const py::handle& v) { p.target_mode = mode_from_py(v); })
        .def_property_readonly("actual_mode", [](const PID& p) { return to_py_mode(p.actual); })
        .def_property("_actual", [](const PID& p) { return to_py_mode(p.actual); },
                      [](PID& p, const py::handle& v) { p.actual = mode_from_py(v); })
        .def_property("alarm_enab", [](py::object self) { return BoolMapView{self, py::cast<PID*>(self), false}; },
                      [](PID& p, const py::handle& d) { fill_from_dict(p.alarm_enab, d); })
        .def_property("alarm_shelved", [](py::object self) { return BoolMapView{self, py::cast<PID*>(self), true}; },
                      [](PID& p, const py::handle& d) { fill_from_dict(p.alarm_shelved, d); })
        .def_property("alarm_delay", [](const PID& p) { return to_py_dict(p.alarm_delay); },
                      [](PID& p, const py::handle& d) { fill_from_dict(p.alarm_delay, d); })
        .def_property("_alarm_timer", [](const PID& p) { return to_py_dict(p.alarm_timer); },
                      [](PID& p, const py::handle& d) { fill_from_dict(p.alarm_timer, d); })
        .def_property_readonly("pv_span", &PID::pv_span)
        .def_property_readonly("out_eu", &PID::out_eu)
        .def_property_readonly("bkcal_out", &PID::bkcal_out)
        .def_property_readonly("bkcal_limit", &PID::bkcal_limit)
        .def_readwrite("_pv_filter_primed", &PID::pv_filter_primed)
        .def_readwrite("_was_closed", &PID::was_closed)
        .def_readwrite("_sp_suppress_dev", &PID::sp_suppress_dev)
        .def_readwrite("_out_limited", &PID::out_limited)
        .def_property("_d_prev", [](const PID& p) -> py::object {
                          return p.d_prev ? py::object(py::float_(*p.d_prev)) : py::object(py::none());
                      },
                      [](PID& p, const py::handle& v) {
                          if (v.is_none()) p.d_prev.reset();
                          else p.d_prev = py::cast<double>(v);
                      })
        .def_property_readonly("_reset_fb", [](PID& p) -> Lag& { return p.reset_fb; },
                               py::return_value_policy::reference_internal)
        .def_property_readonly("_pv_filter", [](PID& p) -> py::object {
            if (!p.pv_filter) return py::none();
            return py::cast(&*p.pv_filter, py::return_value_policy::reference_internal, py::cast(&p));
        })
        .def("_pct", &PID::pct, py::arg("eu"))
        .def("set_mode", [](PID& p, const py::handle& m) { p.set_mode(mode_from_py(m)); }, py::arg("mode"))
        .def("step", &PID::step, py::arg("dt"), py::arg("pv"), py::arg("pv_good") = true,
             py::arg("cas_in") = py::none(), py::arg("bkcal_in") = py::none(), py::arg("bkcal_in_limit") = "",
             py::arg("ff_val") = 0.0)
        .def("note_sp_change", &PID::note_sp_change, py::arg("seconds") = 60.0)
        .def("capture_state", [](const PID& p) { return to_py(p.capture_state()); })
        .def("apply_state", [](PID& p, const py::handle& s) { p.apply_state(from_py(s)); });
}
