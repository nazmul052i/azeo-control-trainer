// Bindings for the field devices, by the names and with the state
// dictionaries of azeoplant/core/devices.py.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "azeocore/devices.hpp"
#include "py_state.hpp"

namespace py = pybind11;
using namespace azeocore;
using azeocore::pyconv::add_state;

void bind_devices(py::module_& m) {
    py::enum_<ValveChar>(m, "ValveChar")
        .value("LINEAR", ValveChar::Linear)
        .value("EQUAL_PERCENT", ValveChar::EqualPercent)
        .value("QUICK_OPENING", ValveChar::QuickOpening);
    m.def("valve_gain", &valve_gain, py::arg("char"), py::arg("x"), py::arg("rangeability") = 50.0);

    py::class_<ControlValve> cv(m, "ControlValve");
    cv.def(py::init<std::string, double, ValveChar, double, bool, double, double, double, double, bool, double,
                    double, double, double, double>(),
           py::arg("tag"), py::arg("cv_rated") = 100.0, py::arg("char") = ValveChar::Linear,
           py::arg("stroke_time") = 5.0, py::arg("fail_closed") = true, py::arg("leakage_pct") = 0.0,
           py::arg("rangeability") = 50.0, py::arg("stiction") = 0.0, py::arg("hysteresis") = 0.0,
           py::arg("stuck") = false, py::arg("zero_shift") = 0.0, py::arg("position") = 0.0,
           py::arg("positioner_time") = 0.0, py::arg("positioner_overshoot") = 0.0,
           py::arg("positioner_deadband") = 0.0)
        .def_readwrite("positioner_time", &ControlValve::positioner_time)
        .def_readwrite("positioner_overshoot", &ControlValve::positioner_overshoot)
        .def_readwrite("positioner_deadband", &ControlValve::positioner_deadband)
        .def_readwrite("tag", &ControlValve::tag)
        .def_readwrite("cv_rated", &ControlValve::cv_rated)
        .def_readwrite("char", &ControlValve::chr)
        .def_readwrite("stroke_time", &ControlValve::stroke_time)
        .def_readwrite("fail_closed", &ControlValve::fail_closed)
        .def_readwrite("leakage_pct", &ControlValve::leakage_pct)
        .def_readwrite("rangeability", &ControlValve::rangeability)
        .def_readwrite("stiction", &ControlValve::stiction)
        .def_readwrite("hysteresis", &ControlValve::hysteresis)
        .def_readwrite("stuck", &ControlValve::stuck)
        .def_readwrite("zero_shift", &ControlValve::zero_shift)
        .def_readwrite("position", &ControlValve::position)
        .def_property_readonly_static("XT", [](py::object) { return ControlValve::XT; })
        .def("step", &ControlValve::step, py::arg("dt"), py::arg("command_pct"), py::arg("air_failure") = false)
        .def("flow", &ControlValve::flow, py::arg("dp_bar"), py::arg("sg") = 1.0)
        .def("gas_flow", &ControlValve::gas_flow, py::arg("p_up_bara"), py::arg("p_dn_bara"), py::arg("sg") = 0.65,
             py::arg("t_k") = 300.0)
        .def("clear_faults", &ControlValve::clear_faults);
    add_state(cv);

    py::enum_<MovState>(m, "MovState")
        .value("CLOSED", MovState::Closed).value("OPENING", MovState::Opening).value("OPEN", MovState::Open)
        .value("CLOSING", MovState::Closing).value("STOPPED", MovState::Stopped).value("TRIPPED", MovState::Tripped)
        .def_property_readonly("value", [](MovState s) { return std::string(mov_state_name(s)); });

    py::class_<MotorOperatedValve> mov(m, "MotorOperatedValve");
    mov.def(py::init<std::string, double, double, bool>(), py::arg("tag"), py::arg("travel_time") = 25.0,
            py::arg("position") = 0.0, py::arg("remote") = true)
        .def_readwrite("tag", &MotorOperatedValve::tag)
        .def_readwrite("travel_time", &MotorOperatedValve::travel_time)
        .def_readwrite("position", &MotorOperatedValve::position)
        .def_readwrite("remote", &MotorOperatedValve::remote)
        .def_readwrite("state", &MotorOperatedValve::state)
        .def_readwrite("fail_to_open", &MotorOperatedValve::fail_to_open)
        .def_property("torque_trip_at",
                      [](const MotorOperatedValve& v) -> py::object {
                          return v.torque_trip_at ? py::cast(*v.torque_trip_at) : py::none();
                      },
                      [](MotorOperatedValve& v, py::object o) {
                          if (o.is_none()) v.torque_trip_at.reset(); else v.torque_trip_at = py::cast<double>(o);
                      })
        .def_readwrite("open_limit_faulty", &MotorOperatedValve::open_limit_faulty)
        .def_readwrite("slow_travel_factor", &MotorOperatedValve::slow_travel_factor)
        .def_readwrite("drifts_closed", &MotorOperatedValve::drifts_closed)
        .def_readwrite("torque_tripped", &MotorOperatedValve::torque_tripped)
        .def("command", &MotorOperatedValve::command, py::arg("open_cmd"), py::arg("close_cmd"))
        .def("reset", &MotorOperatedValve::reset)
        .def("step", &MotorOperatedValve::step, py::arg("dt"))
        .def_property_readonly("zso", &MotorOperatedValve::zso)
        .def_property_readonly("zsc", &MotorOperatedValve::zsc)
        .def_property_readonly("flow_fraction", &MotorOperatedValve::flow_fraction)
        .def("clear_faults", &MotorOperatedValve::clear_faults);
    add_state(mov);

    py::class_<Motor> motor(m, "Motor");
    motor.def(py::init<std::string, double, double, double, bool>(), py::arg("tag"), py::arg("rated_current") = 100.0,
              py::arg("start_delay") = 1.0, py::arg("coast_time") = 3.0, py::arg("vfd") = false)
        .def_readwrite("tag", &Motor::tag)
        .def_readwrite("rated_current", &Motor::rated_current)
        .def_readwrite("start_delay", &Motor::start_delay)
        .def_readwrite("coast_time", &Motor::coast_time)
        .def_readwrite("vfd", &Motor::vfd)
        .def_readwrite("running", &Motor::running)
        .def_readwrite("faulted", &Motor::faulted)
        .def_readwrite("available", &Motor::available)
        .def_readwrite("speed_pct", &Motor::speed_pct)
        .def_readwrite("vfd_healthy", &Motor::vfd_healthy)
        .def_readwrite("trip_on_overload", &Motor::trip_on_overload)
        .def_readwrite("vfd_comms_fault", &Motor::vfd_comms_fault)
        .def_readwrite("load_frac", &Motor::load_frac)
        .def_readwrite("thermal_pct", &Motor::thermal_pct)
        .def_property_readonly("cmd_start", &Motor::cmd_start)
        .def_property_readonly("timer", &Motor::timer)
        .def("command", &Motor::command, py::arg("start"), py::arg("stop"))
        .def("trip", [](Motor& mo, const std::string&) { mo.trip(); }, py::arg("reason") = "")
        .def("reset", &Motor::reset)
        .def("step", &Motor::step, py::arg("dt"), py::arg("permissive") = true, py::arg("speed_ref_pct") = 100.0)
        .def_property_readonly("current", &Motor::current)
        .def("clear_faults", &Motor::clear_faults)
        .def("state", [](const Motor& mo) { return azeocore::pyconv::to_py(mo.state()); })
        .def("restore", [](Motor& mo, py::handle s) { mo.apply_state(azeocore::pyconv::from_py(s)); });
    add_state(motor);

    py::class_<CentrifugalPump> pump(m, "CentrifugalPump");
    pump.def(py::init<std::string, double, double, double, double>(), py::arg("tag"), py::arg("head_shutoff") = 280.0,
             py::arg("flow_max") = 230.0, py::arg("flow_min") = 25.0, py::arg("rated_speed_pct") = 100.0)
        .def_readwrite("tag", &CentrifugalPump::tag)
        .def_readwrite("head_shutoff", &CentrifugalPump::head_shutoff)
        .def_readwrite("flow_max", &CentrifugalPump::flow_max)
        .def_readwrite("flow_min", &CentrifugalPump::flow_min)
        .def_readwrite("rated_speed_pct", &CentrifugalPump::rated_speed_pct)
        .def_readwrite("wear_pct", &CentrifugalPump::wear_pct)
        .def_readwrite("cavitating", &CentrifugalPump::cavitating)
        .def("head", &CentrifugalPump::head, py::arg("flow_m3h"), py::arg("speed_pct"))
        .def("discharge_pressure", &CentrifugalPump::discharge_pressure, py::arg("suction_barg"), py::arg("flow_m3h"),
             py::arg("speed_pct"), py::arg("density") = 780.0)
        .def("check_cavitation", &CentrifugalPump::check_cavitation, py::arg("suction_barg"),
             py::arg("vapour_pressure_barg"), py::arg("running"))
        .def("clear_faults", &CentrifugalPump::clear_faults);
    add_state(pump);

    py::class_<HeatExchanger> hx(m, "HeatExchanger");
    hx.def(py::init<std::string, double, double>(), py::arg("tag"), py::arg("ua_clean"), py::arg("duty_max") = 1e9)
        .def_readwrite("tag", &HeatExchanger::tag)
        .def_readwrite("ua_clean", &HeatExchanger::ua_clean)
        .def_readwrite("duty_max", &HeatExchanger::duty_max)
        .def_readwrite("fouling_pct", &HeatExchanger::fouling_pct)
        .def_readwrite("duty", &HeatExchanger::duty)
        .def_readwrite("limited", &HeatExchanger::limited)
        .def_property_readonly_static("MAX_FOULING", [](py::object) { return HeatExchanger::MAX_FOULING; })
        .def_property_readonly("ua", &HeatExchanger::ua)
        .def("capacity", &HeatExchanger::capacity, py::arg("t_hot"), py::arg("t_cold"), py::arg("driver") = 1.0)
        .def("transfer", &HeatExchanger::transfer, py::arg("demand"), py::arg("t_hot"), py::arg("t_cold"),
             py::arg("driver") = 1.0)
        .def("clear_faults", &HeatExchanger::clear_faults);
    add_state(hx);

    py::enum_<TxFailure>(m, "TxFailure")
        .value("NONE", TxFailure::None).value("FROZEN", TxFailure::Frozen).value("FAIL_LOW", TxFailure::FailLow)
        .value("FAIL_HIGH", TxFailure::FailHigh).value("OUT_OF_SERVICE", TxFailure::OutOfService)
        .def_property_readonly("value", [](TxFailure f) { return std::string(tx_failure_name(f)); });

    py::class_<Transmitter> tx(m, "Transmitter");
    tx.def(py::init<std::string, double, double, double, double, double, double, double, TxFailure, double, double,
                    double, double, double>(),
           py::arg("tag"), py::arg("lo") = 0.0, py::arg("hi") = 100.0, py::arg("tau") = 0.5,
           py::arg("noise_sigma_pct") = 0.0, py::arg("update_period") = 0.0, py::arg("transport") = 0.0,
           py::arg("drift_pct_per_hour") = 0.0, py::arg("failure") = TxFailure::None, py::arg("extra_lag") = 0.0,
           py::arg("walk_sigma_pct") = 0.0, py::arg("walk_tau") = 600.0, py::arg("damping") = 0.0,
           py::arg("offset_pct") = 0.0)
        .def_readwrite("walk_sigma_pct", &Transmitter::walk_sigma_pct)
        .def_readwrite("walk_tau", &Transmitter::walk_tau)
        .def_readwrite("damping", &Transmitter::damping)
        .def_readwrite("offset_pct", &Transmitter::offset_pct)
        .def_readwrite("tag", &Transmitter::tag)
        .def_readwrite("lo", &Transmitter::lo)
        .def_readwrite("hi", &Transmitter::hi)
        .def_readwrite("tau", &Transmitter::tau)
        .def_readwrite("noise_sigma_pct", &Transmitter::noise_sigma_pct)
        .def_readwrite("update_period", &Transmitter::update_period)
        .def_readwrite("transport", &Transmitter::transport)
        .def_readwrite("drift_pct_per_hour", &Transmitter::drift_pct_per_hour)
        .def_readwrite("failure", &Transmitter::failure)
        .def_readwrite("extra_lag", &Transmitter::extra_lag)
        .def_readwrite("measured", &Transmitter::measured)
        .def_readwrite("quality", &Transmitter::quality)
        .def_property_readonly("span", &Transmitter::span)
        .def("step", &Transmitter::step, py::arg("dt"), py::arg("true_value"))
        .def("clear_faults", &Transmitter::clear_faults);
    add_state(tx);
}
