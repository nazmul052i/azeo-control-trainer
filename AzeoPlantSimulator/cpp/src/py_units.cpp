// Bindings for malfunctions, the unit base, the packages and the units.
//
// A native unit is handed to the Python flowsheet in place of its Python
// twin, so it presents the same surface: code, name, tags, malfunctions,
// step, capture, apply, balance_readings, model_parameters and
// clear_malfunctions, plus whichever attributes the flowsheet reads back
// (U010.air_failed). model_parameters returns the Python ParameterSpec
// dataclass, registered by the facade, so the accountability report
// treats native and Python units alike.
#include <pybind11/functional.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "azeocore/packages.hpp"
#include "azeocore/unit.hpp"
#include "azeocore/units/u010.hpp"
#include "azeocore/units/u100.hpp"
#include "azeocore/units/u300.hpp"
#include "azeocore/units/u400.hpp"
#include "azeocore/units/u200.hpp"
#include "azeocore/units/u700.hpp"
#include "azeocore/units/u800.hpp"
#include "azeocore/units/u500.hpp"
#include "py_state.hpp"

namespace py = pybind11;
using namespace azeocore;
using azeocore::pyconv::add_state;
using azeocore::pyconv::from_py;
using azeocore::pyconv::to_py;

namespace {

py::object g_parameter_spec_cls;

py::object parameter_specs(const ProcessUnit& u) {
    py::list out;
    for (const ParameterSpec& p : u.parameters()) {
        if (g_parameter_spec_cls && !g_parameter_spec_cls.is_none()) {
            out.append(g_parameter_spec_cls(
                py::arg("unit") = u.code, py::arg("name") = p.name, py::arg("value") = p.value, py::arg("eu") = p.eu,
                py::arg("description") = p.description, py::arg("source") = u.name,
                py::arg("lo") = (p.lo ? py::object(py::cast(*p.lo)) : py::object(py::none())),
                py::arg("hi") = (p.hi ? py::object(py::cast(*p.hi)) : py::object(py::none())),
                py::arg("documented") = p.documented));
        } else {
            py::dict d;
            d["unit"] = u.code; d["name"] = p.name; d["value"] = p.value; d["eu"] = p.eu;
            d["description"] = p.description; d["documented"] = p.documented;
            out.append(d);
        }
    }
    return out;
}

py::dict tags_dict(ProcessUnit& u, py::object self) {
    py::dict d;
    for (const auto& n : u.tag_order) d[py::str(n)] = py::cast(u.tags.at(n), py::return_value_policy::reference_internal, self);
    return d;
}

template <class U>
void bind_unit_common(py::class_<U, ProcessUnit>& c) {
    (void)c;
}

}  // namespace

void bind_units(py::module_& m) {
    m.def("register_model_types", [](py::object parameter_spec) { g_parameter_spec_cls = std::move(parameter_spec); },
          py::arg("parameter_spec"));
    m.add_object("_release_model_types", py::capsule([]() { g_parameter_spec_cls = py::object(); }));

    py::class_<Malfunction>(m, "Malfunction")
        .def_readonly("mf_id", &Malfunction::mf_id)
        .def_readonly("target", &Malfunction::target)
        .def_readonly("description", &Malfunction::description)
        .def_readonly("category", &Malfunction::category)
        .def_readonly("param_label", &Malfunction::param_label)
        .def_readonly("param_min", &Malfunction::param_min)
        .def_readonly("param_max", &Malfunction::param_max)
        .def_readwrite("active", &Malfunction::active)
        .def_readwrite("value", &Malfunction::value)
        .def("set", [](Malfunction& mf, bool active, py::object value) {
            std::optional<double> v;
            if (!value.is_none()) v = py::cast<double>(value);
            mf.set(active, v);
        }, py::arg("active"), py::arg("value") = py::none());

    py::class_<ProcessUnit>(m, "ProcessUnit")
        .def_property_readonly("code", [](const ProcessUnit& u) { return u.code; })
        .def_property_readonly("name", [](const ProcessUnit& u) { return u.name; })
        .def_property_readonly("dt", [](const ProcessUnit& u) { return u.dt; })
        .def_property_readonly("db", [](ProcessUnit& u) -> TagDatabase& { return u.db; }, py::return_value_policy::reference)
        .def_property_readonly("bus", [](ProcessUnit& u) -> ProcessBus& { return u.bus; }, py::return_value_policy::reference)
        .def_property_readonly("tags", [](py::object self) { return tags_dict(py::cast<ProcessUnit&>(self), self); })
        .def_property_readonly("malfunctions", [](py::object self) {
            ProcessUnit& u = py::cast<ProcessUnit&>(self);
            py::list out;
            for (Malfunction& mf : u.malfunctions)
                out.append(py::cast(&mf, py::return_value_policy::reference_internal, self));
            return out;
        })
        .def("step", &ProcessUnit::run_step, py::arg("dt"))
        .def_readwrite("trace_enabled", &ProcessUnit::trace_enabled)
        .def_readwrite("trace_every", &ProcessUnit::trace_every)
        .def_readonly("step_count", &ProcessUnit::step_count)
        .def_property_readonly("logger", [](const ProcessUnit& u) { return u.logger; })
        .def_property_readonly("trace", [](const ProcessUnit& u) {
            py::dict d;
            for (const auto& kv : u.trace.values()) d[py::str(kv.first)] = kv.second;
            return d;
        }, "The last step's intermediates by name, filled while trace_enabled")
        .def("transmitters", [](py::object self) {
            ProcessUnit& u = py::cast<ProcessUnit&>(self);
            py::dict d;
            for (auto& kv : u.transmitters())
                d[py::str(kv.first)] = py::cast(kv.second, py::return_value_policy::reference_internal, self);
            return d;
        })
        .def("valves", [](py::object self) {
            ProcessUnit& u = py::cast<ProcessUnit&>(self);
            py::dict d;
            for (auto& kv : u.valves())
                d[py::str(kv.first)] = py::cast(kv.second, py::return_value_policy::reference_internal, self);
            return d;
        })
        .def("capture", [](const ProcessUnit& u) { return to_py(u.capture()); })
        .def("apply", [](ProcessUnit& u, py::handle s) { u.apply(from_py(s)); })
        .def("capture_state", [](const ProcessUnit& u) { return to_py(u.capture()); })
        .def("apply_state", [](ProcessUnit& u, py::handle s) { u.apply(from_py(s)); })
        .def("save_state", [](const ProcessUnit& u) { return to_py(u.save_state()); })
        .def("load_state", [](ProcessUnit& u, py::handle s) { u.load_state(from_py(s)); })
        .def("clear_malfunctions", &ProcessUnit::clear_malfunctions)
        .def("balance_readings", [](const ProcessUnit& u) { return u.balance_readings(); })
        .def("model_parameters", [](const ProcessUnit& u) { return parameter_specs(u); });

    // ---------------------------------------------------------- packages
    py::class_<MovPackage> mov(m, "MovPackage");
    mov.def_readwrite("local", &MovPackage::local)
        .def_readwrite("simulate_enable", &MovPackage::simulate_enable)
        .def_readwrite("simulate_value", &MovPackage::simulate_value)
        .def_readonly("tag", &MovPackage::tag)
        .def_readonly("service", &MovPackage::service)
        .def_readwrite("device", &MovPackage::device)
        .def("step", &MovPackage::step)
        .def_property_readonly("fraction", &MovPackage::fraction)
        .def_property_readonly("is_open", &MovPackage::is_open)
        .def("state", [](const MovPackage& p) { return to_py(p.state()); })
        .def("restore", [](MovPackage& p, py::handle s) { p.restore(from_py(s)); });
    add_state(mov);

    py::class_<SdvPackage> sdv(m, "SdvPackage");
    sdv.def_readonly("tag", &SdvPackage::tag)
        .def_readonly("service", &SdvPackage::service)
        .def_readonly("stroke", &SdvPackage::stroke)
        .def_readonly("fail_open", &SdvPackage::fail_open)
        .def_readwrite("position", &SdvPackage::position)
        .def("step", &SdvPackage::step, py::arg("dt"), py::arg("trip") = false, py::arg("air_failure") = false)
        .def_property_readonly("is_open", &SdvPackage::is_open)
        .def_property_readonly("fraction", &SdvPackage::fraction);
    add_state(sdv);

    py::class_<MotorPackage> mp(m, "MotorPackage");
    mp.def_readonly("tag", &MotorPackage::tag)
        .def_readonly("service", &MotorPackage::service)
        .def_readonly("vfd", &MotorPackage::vfd)
        .def_readwrite("device", &MotorPackage::device)
        .def_readwrite("local", &MotorPackage::local)
        .def_readwrite("simulate_enable", &MotorPackage::simulate_enable)
        .def_readwrite("simulate_value", &MotorPackage::simulate_value)
        .def("step", &MotorPackage::step, py::arg("dt"), py::arg("permissive") = true, py::arg("speed_ref") = 100.0,
             py::arg("trip") = false)
        .def_property_readonly("running", &MotorPackage::running)
        .def_property_readonly("speed", &MotorPackage::speed)
        .def_property_readonly("current", &MotorPackage::current);
    add_state(mp);

    py::class_<PumpTrain> pt(m, "PumpTrain");
    pt.def_readonly("service", &PumpTrain::service)
        .def_readonly("mov_a", &PumpTrain::mov_a)
        .def_readonly("mov_b", &PumpTrain::mov_b)
        .def_readonly("motor_a", &PumpTrain::motor_a)
        .def_readonly("motor_b", &PumpTrain::motor_b)
        .def_readwrite("pump_a", &PumpTrain::pump_a)
        .def_readwrite("pump_b", &PumpTrain::pump_b)
        .def_readwrite("p_suction_a", &PumpTrain::p_suction_a)
        .def_readwrite("p_suction_b", &PumpTrain::p_suction_b)
        .def_readwrite("cav_a", &PumpTrain::cav_a)
        .def_readwrite("cav_b", &PumpTrain::cav_b)
        .def_property_readonly("_primed", [](const PumpTrain& p) { return p.primed; })
        .def("step", &PumpTrain::step, py::arg("dt"), py::arg("suction_base"), py::arg("flow"), py::arg("primed") = true,
             py::arg("speed_ref") = 100.0, py::arg("vapour_pressure") = 0.3, py::arg("friction") = 0.5,
             py::arg("design_flow") = 100.0, py::arg("trip") = false)
        .def_property_readonly("running_count", &PumpTrain::running_count)
        .def("discharge_pressure", &PumpTrain::discharge_pressure, py::arg("flow"), py::arg("density") = 780.0)
        .def_property_readonly("any_running", &PumpTrain::any_running)
        .def("state", [](const PumpTrain& p) { return to_py(p.state()); })
        .def("restore", [](PumpTrain& p, py::handle s) { p.restore(from_py(s)); });
    add_state(pt);

    // --------------------------------------------------------------- units
    py::class_<units::FuelGasHeader, ProcessUnit>(m, "FuelGasHeader", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_readwrite("air_failed", &units::FuelGasHeader::air_failed)
        .def_readwrite("import_available", &units::FuelGasHeader::import_available)
        .def_readwrite("air_comp_lost", &units::FuelGasHeader::air_comp_lost)
        .def_readwrite("lhv_target", &units::FuelGasHeader::lhv_target)
        .def_readwrite("lhv_import", &units::FuelGasHeader::lhv_import)
        .def_readonly("pressure", &units::FuelGasHeader::pressure)
        .def_readonly("b1_header", &units::FuelGasHeader::b1_header)
        .def_readonly("lhv", &units::FuelGasHeader::lhv)
        .def_readonly("air_receiver", &units::FuelGasHeader::air_receiver)
        .def_readonly("mov0101", &units::FuelGasHeader::mov0101)
        .def_readonly("cw_pumps", &units::FuelGasHeader::cw_pumps)
        .def_readonly("ct_fan_a", &units::FuelGasHeader::ct_fan_a)
        .def_readonly("ct_fan_b", &units::FuelGasHeader::ct_fan_b)
        .def_readonly("cw_supply_temp", &units::FuelGasHeader::cw_supply_temp)
        .def_readonly("cw_return_temp", &units::FuelGasHeader::cw_return_temp)
        .def_readonly("cw_header_pressure", &units::FuelGasHeader::cw_header_pressure)
        .def_readonly("cw_basin_level", &units::FuelGasHeader::cw_basin_level)
        .def_readonly("cw_solids", &units::FuelGasHeader::cw_solids)
        .def_readwrite("tower_fouling_pct", &units::FuelGasHeader::tower_fouling_pct)
        .def_readwrite("wet_bulb_offset", &units::FuelGasHeader::wet_bulb_offset)
        .def_readwrite("fan_capacity_pct", &units::FuelGasHeader::fan_capacity_pct)
        .def_readonly("xv_import", &units::FuelGasHeader::xv_import)
        .def_property_readonly_static("code", [](py::object) { return std::string("U010"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Fuel gas header and utilities"); })
        .def_property_readonly_static("CW_BASIN_VOLUME", [](py::object) { return units::FuelGasHeader::CW_BASIN_VOLUME; })
        .def_property_readonly_static("CW_DESIGN_FLOW", [](py::object) { return units::FuelGasHeader::CW_DESIGN_FLOW; })
        .def_property_readonly_static("CW_OTHER_FLOW", [](py::object) { return units::FuelGasHeader::CW_OTHER_FLOW; })
        .def_property_readonly_static("CW_TOWER_KAV_L", [](py::object) { return units::FuelGasHeader::CW_TOWER_KAV_L; })
        .def_property_readonly_static("HEADER_VOLUME", [](py::object) { return units::FuelGasHeader::HEADER_VOLUME; })
        .def_property_readonly_static("LHV_NOMINAL", [](py::object) { return units::FuelGasHeader::LHV_NOMINAL; });

    py::class_<units::FeedSection, ProcessUnit>(m, "FeedSection", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_readwrite("hv1001_position", &units::FeedSection::hv1001_position)
        .def_readwrite("fresh_feed", &units::FeedSection::fresh_feed)
        .def_readwrite("strainer_dp", &units::FeedSection::strainer_dp)
        .def_readwrite("local_a", &units::FeedSection::local_a)
        .def_readwrite("local_b", &units::FeedSection::local_b)
        .def_readwrite("mov_a_local", &units::FeedSection::mov_a_local)
        .def_readwrite("mov_b_local", &units::FeedSection::mov_b_local)
        .def_readonly("xv1001_open", &units::FeedSection::xv1001_open)
        .def_readonly("mov_a", &units::FeedSection::mov_a)
        .def_readonly("mov_b", &units::FeedSection::mov_b)
        .def_property_readonly("mov_recycle", [](units::FeedSection& u) -> MovPackage& { return *u.mov_recycle; },
                               py::return_value_policy::reference_internal)
        .def_readonly("motor_a", &units::FeedSection::motor_a)
        .def_readonly("motor_b", &units::FeedSection::motor_b)
        .def_readonly("pump_a", &units::FeedSection::pump_a)
        .def_readonly("pump_b", &units::FeedSection::pump_b)
        .def_readonly("fcv1001", &units::FeedSection::fcv1001)
        .def_readonly("level", &units::FeedSection::level)
        .def_readonly("charge_flow", &units::FeedSection::charge_flow)
        .def_readonly("tx_flow", &units::FeedSection::tx_flow)
        .def_readonly("tx_level", &units::FeedSection::tx_level)
        .def_property_readonly_static("code", [](py::object) { return std::string("U100"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Feed surge drum D1 and charge pumps"); })
        .def_property_readonly_static("D1_AREA", [](py::object) { return units::FeedSection::D1_AREA; })
        .def_property_readonly_static("D1_HEIGHT", [](py::object) { return units::FeedSection::D1_HEIGHT; })
        .def_property_readonly_static("DENSITY", [](py::object) { return units::FeedSection::DENSITY; })
        .def_property_readonly_static("VAPOUR_PRESSURE", [](py::object) { return units::FeedSection::VAPOUR_PRESSURE; })
        .def_property_readonly_static("STATIC_HEAD", [](py::object) { return units::FeedSection::STATIC_HEAD; });

    py::class_<units::FiredHeater, ProcessUnit>(m, "FiredHeater", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_readwrite("lit", &units::FiredHeater::lit)
        .def_readwrite("fuel_oil_available", &units::FiredHeater::fuel_oil_available)
        .def_readwrite("efficiency_loss", &units::FiredHeater::efficiency_loss)
        .def_readwrite("id_fan_faulted", &units::FiredHeater::id_fan_faulted)
        .def_readwrite("purge_timer", &units::FiredHeater::purge_timer)
        .def_readwrite("purged", &units::FiredHeater::purged)
        .def_readonly("fcv3001", &units::FiredHeater::fcv3001)
        .def_readonly("damper", &units::FiredHeater::damper)
        .def_readonly("burner_pressure", &units::FiredHeater::burner_pressure)
        .def_readonly("duty", &units::FiredHeater::duty)
        .def_readonly("t_out", &units::FiredHeater::t_out)
        .def_readonly("coke", &units::FiredHeater::coke)
        .def_readonly("tx_tout", &units::FiredHeater::tx_tout)
        .def_readonly("tx_o2", &units::FiredHeater::tx_o2)
        .def_property_readonly("xv_oil", [](units::FiredHeater& u) -> SdvPackage& { return *u.xv_oil; },
                               py::return_value_policy::reference_internal)
        .def_static("_efficiency", &units::FiredHeater::efficiency)
        .def_property_readonly_static("code", [](py::object) { return std::string("U300"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Fired heater H1"); })
        .def_property_readonly_static("BURNER_HEADER_VOLUME", [](py::object) { return units::FiredHeater::BURNER_HEADER_VOLUME; })
        .def_property_readonly_static("DUTY_MAX", [](py::object) { return units::FiredHeater::DUTY_MAX; })
        .def_property_readonly_static("TAU_OUTLET", [](py::object) { return units::FiredHeater::TAU_OUTLET; })
        .def_property_readonly_static("DEADTIME_OUTLET", [](py::object) { return units::FiredHeater::DEADTIME_OUTLET; })
        .def_property_readonly_static("E5_EFF", [](py::object) { return units::FiredHeater::E5_EFF; })
        .def_property_readonly_static("AIR_MAX", [](py::object) { return units::FiredHeater::AIR_MAX; });

    py::class_<units::ReactorSection, ProcessUnit>(m, "ReactorSection", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_readwrite("activity", &units::ReactorSection::activity)
        .def_readwrite("quench_failed", &units::ReactorSection::quench_failed)
        .def_readwrite("fouled_pct", &units::ReactorSection::fouled_pct)
        .def_readonly("bed1", &units::ReactorSection::bed1)
        .def_readonly("bed2", &units::ReactorSection::bed2)
        .def_readonly("pressure", &units::ReactorSection::pressure)
        .def_readonly("d3_pressure", &units::ReactorSection::d3_pressure)
        .def_readonly("d3_level", &units::ReactorSection::d3_level)
        .def_readonly("d3_interface", &units::ReactorSection::d3_interface)
        .def_readonly("conversion", &units::ReactorSection::conversion)
        .def_readonly("tx_impurity", &units::ReactorSection::tx_impurity)
        .def_property_readonly("FT4004", [](py::object self) {
            return py::cast(py::cast<units::ReactorSection&>(self).FT4004, py::return_value_policy::reference_internal, self);
        })
        .def_property_readonly("xv4001", [](units::ReactorSection& u) -> SdvPackage& { return *u.xv4001; }, py::return_value_policy::reference_internal)
        .def_property_readonly("xv4002", [](units::ReactorSection& u) -> SdvPackage& { return *u.xv4002; }, py::return_value_policy::reference_internal)
        .def_property_readonly("bdv4001", [](units::ReactorSection& u) -> SdvPackage& { return *u.bdv4001; }, py::return_value_policy::reference_internal)
        .def("_conversion", &units::ReactorSection::conversion_of, py::arg("temp_c"), py::arg("volumetric_flow_m3s"))
        .def_property_readonly_static("code", [](py::object) { return std::string("U400"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Reactor R1 and separator D3"); })
        .def_property_readonly_static("D3_WATER_FRAC", [](py::object) { return units::ReactorSection::D3_WATER_FRAC; })
        .def_property_readonly_static("GAS_TO_OIL", [](py::object) { return units::ReactorSection::GAS_TO_OIL; })
        .def_property_readonly_static("H2_CONS_K", [](py::object) { return units::ReactorSection::H2_CONS_K; });

    py::class_<units::RecycleCompressor, ProcessUnit>(m, "RecycleCompressor", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_readwrite("fouling_pct", &units::RecycleCompressor::fouling_pct)
        .def_readwrite("lube_decay", &units::RecycleCompressor::lube_decay)
        .def_readwrite("h2_header", &units::RecycleCompressor::h2_header)
        .def_readonly("surging", &units::RecycleCompressor::surging)
        .def_readonly("surge_phase", &units::RecycleCompressor::surge_phase)
        .def_readonly("fault_flag", &units::RecycleCompressor::fault_flag)
        .def_readonly("antisurge", &units::RecycleCompressor::antisurge)
        .def_readonly("p_suction", &units::RecycleCompressor::p_suction)
        .def_readonly("p_discharge", &units::RecycleCompressor::p_discharge)
        .def_readonly("ko_level", &units::RecycleCompressor::ko_level)
        .def_readonly("flow", &units::RecycleCompressor::flow)
        .def_readonly("lube_pressure", &units::RecycleCompressor::lube_pressure)
        .def_property_readonly("motor", [](units::RecycleCompressor& u) -> MotorPackage& { return *u.motor; }, py::return_value_policy::reference_internal)
        .def_property_readonly("lube_main", [](units::RecycleCompressor& u) -> MotorPackage& { return *u.lube_main; }, py::return_value_policy::reference_internal)
        .def_property_readonly("lube_aux", [](units::RecycleCompressor& u) -> MotorPackage& { return *u.lube_aux; }, py::return_value_policy::reference_internal)
        .def_property_readonly("xv2001", [](units::RecycleCompressor& u) -> SdvPackage& { return *u.xv2001; }, py::return_value_policy::reference_internal)
        .def("_surge_flow", &units::RecycleCompressor::surge_flow, py::arg("speed_frac"), py::arg("mw"), py::arg("gv_frac") = 1.0)
        .def_property_readonly_static("code", [](py::object) { return std::string("U200"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Recycle gas compressor C1"); })
        .def_property_readonly_static("CHOKE_FLOW", [](py::object) { return units::RecycleCompressor::CHOKE_FLOW; })
        .def_property_readonly_static("RATED_SPEED", [](py::object) { return units::RecycleCompressor::RATED_SPEED; });

    py::class_<units::SteamBoiler, ProcessUnit>(m, "SteamBoiler", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_readwrite("tube_leak", &units::SteamBoiler::tube_leak)
        .def_readwrite("lit", &units::SteamBoiler::lit)
        .def_readonly("b2_lag", &units::SteamBoiler::b2_lag)
        .def_readonly("oil_lag", &units::SteamBoiler::oil_lag)
        .def_property_readonly("xv7001", [](units::SteamBoiler& u) -> SdvPackage& { return *u.xv7001; }, py::return_value_policy::reference_internal)
        .def_property_readonly("bfw_pumps", [](units::SteamBoiler& u) -> PumpTrain& { return *u.bfw_pumps; }, py::return_value_policy::reference_internal)
        .def_property_readonly("fd_fan", [](units::SteamBoiler& u) -> MotorPackage& { return *u.fd_fan; }, py::return_value_policy::reference_internal)
        .def_property_readonly_static("code", [](py::object) { return std::string("U700"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Steam boiler B1 and MP steam header"); })
        .def_property_readonly_static("DUTY_MAX", [](py::object) { return units::SteamBoiler::DUTY_MAX; })
        .def_property_readonly_static("DRUM_VOLUME", [](py::object) { return units::SteamBoiler::DRUM_VOLUME; })
        .def_property_readonly_static("SWELL_GAIN", [](py::object) { return units::SteamBoiler::SWELL_GAIN; });

    py::class_<units::EffluentTreatment, ProcessUnit>(m, "EffluentTreatment", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_readwrite("manual_caustic_open", &units::EffluentTreatment::manual_caustic_open)
        .def_readwrite("inlet_ph", &units::EffluentTreatment::inlet_ph)
        .def_property_readonly("agitator", [](units::EffluentTreatment& u) -> MotorPackage& { return *u.agitator; }, py::return_value_policy::reference_internal)
        .def_property_readonly("transfer", [](units::EffluentTreatment& u) -> PumpTrain& { return *u.transfer; }, py::return_value_policy::reference_internal)
        .def_static("_ph_from_excess", &units::EffluentTreatment::ph_from_excess, py::arg("excess"), py::arg("buffer_k") = 0.004)
        .def_property_readonly_static("code", [](py::object) { return std::string("U800"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Effluent treatment"); })
        .def_property_readonly_static("TANK_VOLUME", [](py::object) { return units::EffluentTreatment::TANK_VOLUME; });

    py::class_<units::SafetySystem, ProcessUnit>(m, "SafetySystem", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_property_readonly("initiators", [](const units::SafetySystem& u) {
            py::dict d;
            for (const auto& [tag, state] : u.initiators) d[py::str(tag)] = state;
            return d;
        })
        .def("set_initiator", &units::SafetySystem::set_initiator, py::arg("tag"), py::arg("state"))
        .def_property_readonly_static("EFFECTS", [](py::object) {
            py::dict d;
            for (const auto& e : units::SafetySystem::effects()) d[py::str(e.tag)] = py::make_tuple(e.key, e.desc);
            return d;
        })
        .def_property_readonly_static("code", [](py::object) { return std::string("U900"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Safety instrumented system"); });

    m.def("solve_split", [](double d, double z, double s) {
        auto [xd, xb] = units::solve_split(d, z, s);
        return py::make_tuple(xd, xb);
    }, py::arg("feed_frac_dist"), py::arg("z_feed"), py::arg("sep_factor"));

    py::class_<units::DistillationColumn, ProcessUnit> col(m, "DistillationColumn");
    col.def_readwrite("x_drum", &units::DistillationColumn::x_drum)
        .def_readwrite("x_col", &units::DistillationColumn::x_col)
        .def_readwrite("y_top", &units::DistillationColumn::y_top)
        .def_readwrite("x_bot_out", &units::DistillationColumn::x_bot_out)
        .def_readwrite("_vapour_light", &units::DistillationColumn::vapour_light)
        .def_readwrite("_q_cond", &units::DistillationColumn::q_cond)
        .def_readwrite("fouling_trays", &units::DistillationColumn::fouling_trays)
        .def_readwrite("flooding", &units::DistillationColumn::flooding)
        .def_readonly("cw_max", &units::DistillationColumn::cw_max)
        .def("feed_stream", [](units::DistillationColumn& u) {
            auto [f, t, z] = u.feed_stream();
            return py::make_tuple(f, t, z);
        })
        .def("publish_products", &units::DistillationColumn::publish_products, py::arg("distillate"), py::arg("bottoms"))
        .def_readwrite("hx_reboiler", &units::DistillationColumn::hx_reboiler)
        .def_readwrite("hx_condenser", &units::DistillationColumn::hx_condenser)
        .def_readwrite("v_reflux", &units::DistillationColumn::v_reflux)
        .def_readwrite("v_steam", &units::DistillationColumn::v_steam)
        .def_readwrite("tx_drum", &units::DistillationColumn::tx_drum)
        .def_readwrite("tx_dist", &units::DistillationColumn::tx_dist)
        .def_property_readonly("xv_feed", [](units::DistillationColumn& u) -> SdvPackage& { return *u.xv_feed; }, py::return_value_policy::reference_internal)
        .def_property_readonly("reflux_pumps", [](units::DistillationColumn& u) -> PumpTrain& { return *u.reflux_pumps; }, py::return_value_policy::reference_internal)
        .def_property_readonly("bottoms_pumps", [](units::DistillationColumn& u) -> PumpTrain& { return *u.bottoms_pumps; }, py::return_value_policy::reference_internal)
        .def_property_readonly("N", [](const units::DistillationColumn& u) { return u.spec.n; })
        .def_property_readonly("COLUMN", [](const units::DistillationColumn& u) { return std::string(u.spec.column); })
        .def_property_readonly("HAS_WATER_BOOT", [](const units::DistillationColumn& u) { return u.spec.has_water_boot; })
        .def_property_readonly("STEAM_MAX", [](const units::DistillationColumn& u) { return u.spec.steam_max; })
        .def_property_readonly("P_NOMINAL", [](const units::DistillationColumn& u) { return u.spec.p_nominal; })
        .def_property_readonly("FLOOD_DP", [](const units::DistillationColumn& u) { return u.spec.flood_dp; })
        .def_property_readonly("DRUM_VOLUME", [](const units::DistillationColumn& u) { return u.spec.drum_volume; })
        .def_property_readonly("SUMP_VOLUME", [](const units::DistillationColumn& u) { return u.spec.sump_volume; })
        .def_property_readonly("T_LIGHT", [](const units::DistillationColumn& u) { return u.spec.t_light; })
        .def_property_readonly("T_HEAVY", [](const units::DistillationColumn& u) { return u.spec.t_heavy; });

    py::class_<units::ColumnT1, units::DistillationColumn>(m, "ColumnT1", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_property_readonly_static("code", [](py::object) { return std::string("U500"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Distillation column T1"); })
        .def_property_readonly_static("SPLIT_TO_T2", [](py::object) { return units::ColumnT1::SPLIT_TO_T2; });

    py::class_<units::ColumnT2, units::DistillationColumn>(m, "ColumnT2", py::dynamic_attr())
        .def(py::init<TagDatabase&, ProcessBus&, double>(), py::arg("db"), py::arg("bus"), py::arg("dt") = 0.1,
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def_property_readonly_static("code", [](py::object) { return std::string("U600"); })
        .def_property_readonly_static("name", [](py::object) { return std::string("Distillation column T2"); });
}
