// pybind11 module `_azeocore`: the primitives by the names the Python
// core uses, with the same capture_state / apply_state dictionaries, so a
// C++ object can stand in for its Python twin and a snapshot moves
// between them unchanged.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "azeocore/dynamics.hpp"
#include "azeocore/log.hpp"
#include "py_state.hpp"

namespace py = pybind11;
using namespace azeocore;
using azeocore::pyconv::add_state;

void bind_devices(py::module_& m);
void bind_tags(py::module_& m);
void bind_bus(py::module_& m);
void bind_units(py::module_& m);
void bind_control(py::module_& m);
void bind_scanner(py::module_& m);

PYBIND11_MODULE(_azeocore, m) {
    m.doc() = "AzeoPlant native core: dynamic primitives, field devices, tags, buses, packages and units";

    m.def("clamp", &clamp);
    m.def("lerp", &lerp);
    m.def("safe_sqrt", &safe_sqrt);
    m.def("safe_div", &safe_div, py::arg("num"), py::arg("den"), py::arg("default") = 0.0,
          py::arg("eps") = 1e-9);
    m.def("crc32", [](const std::string& s) { return crc32(s); });

    // logging: the facade points the core's sink at Python's logging module
    m.def("set_log_sink", [](py::object sink) {
        if (sink.is_none()) { set_log_sink(nullptr); return; }
        set_log_sink([sink](LogLevel level, const std::string& logger, const std::string& msg) {
            py::gil_scoped_acquire gil;
            try {
                sink(static_cast<int>(level), logger, msg);
            } catch (py::error_already_set& e) {
                e.discard_as_unraisable("azeocore log sink");
            }
        });
    }, py::arg("sink"), "sink(level: int, logger: str, message: str); None restores stderr");
    m.def("set_log_threshold", [](int level) { set_log_threshold(static_cast<LogLevel>(level)); },
          py::arg("level"), "Messages below this level are not formatted at all (10 debug .. 50 critical)");

    py::class_<Lag> lag(m, "Lag");
    lag.def(py::init<double, double>(), py::arg("tau"), py::arg("y0") = 0.0)
        .def_readwrite("tau", &Lag::tau)
        .def_readwrite("y", &Lag::y)
        .def("step", &Lag::step)
        .def("reset", &Lag::reset);
    add_state(lag);

    py::class_<LeadLag> ll(m, "LeadLag");
    ll.def(py::init<double, double, double>(), py::arg("lead"), py::arg("lag"), py::arg("y0") = 0.0)
        .def_readwrite("lead", &LeadLag::lead)
        .def_readwrite("lag", &LeadLag::lag)
        .def_readwrite("y", &LeadLag::y)
        .def_readwrite("u_prev", &LeadLag::u_prev)
        .def("step", &LeadLag::step);
    add_state(ll);

    py::class_<DeadTime> dt(m, "DeadTime");
    dt.def(py::init<double, double, double>(), py::arg("delay"), py::arg("dt"), py::arg("y0") = 0.0)
        .def_property_readonly("delay", &DeadTime::delay)
        .def_property_readonly("dt", &DeadTime::dt)
        .def_property_readonly("buf", &DeadTime::buffer)
        .def("__len__", &DeadTime::size)
        .def("step", &DeadTime::step)
        .def("reset", &DeadTime::reset);
    add_state(dt);

    py::class_<RateLimiter> rl(m, "RateLimiter");
    rl.def(py::init([](double up, py::object down, double y0) {
               std::optional<double> d;
               if (!down.is_none()) d = py::cast<double>(down);
               return RateLimiter(up, d, y0);
           }),
           py::arg("up"), py::arg("down") = py::none(), py::arg("y0") = 0.0)
        .def_readwrite("up", &RateLimiter::up)
        .def_readwrite("down", &RateLimiter::down)
        .def_readwrite("y", &RateLimiter::y)
        .def("step", &RateLimiter::step)
        .def("reset", &RateLimiter::reset);
    add_state(rl);

    py::class_<Integrator> integ(m, "Integrator");
    integ.def(py::init<double, double, double>(), py::arg("y0") = 0.0, py::arg("lo") = -1e9, py::arg("hi") = 1e9)
        .def_readwrite("y", &Integrator::y)
        .def_readwrite("lo", &Integrator::lo)
        .def_readwrite("hi", &Integrator::hi)
        .def_readwrite("saturated", &Integrator::saturated)
        .def("step", &Integrator::step)
        .def("reset", &Integrator::reset);
    add_state(integ);

    py::class_<PyRandom>(m, "PyRandom")
        .def(py::init<std::uint32_t>(), py::arg("seed"))
        .def("random", &PyRandom::random)
        .def("gauss", &PyRandom::gauss, py::arg("mu") = 0.0, py::arg("sigma") = 1.0)
        .def("getrandbits32", &PyRandom::genrand_uint32)
        .def("getstate", [](const PyRandom& r) {
            py::object g = r.gauss_next() ? py::cast(*r.gauss_next()) : py::none();
            return py::make_tuple(3, py::cast(r.state_words()), g);
        })
        .def("setstate", [](PyRandom& r, const py::tuple& t) {
            auto words = py::cast<std::vector<std::uint32_t>>(t[1]);
            std::optional<double> g;
            if (!t[2].is_none()) g = py::cast<double>(t[2]);
            r.set_state(words, g);
        });

    py::class_<Noise> noise(m, "Noise");
    noise.def(py::init<double, double, std::uint32_t>(), py::arg("sigma") = 0.0, py::arg("bandwidth") = 2.0,
              py::arg("seed") = 0u)
        .def_readwrite("sigma", &Noise::sigma)
        .def_readonly("lag", &Noise::lag)
        .def_property_readonly("_rng", [](Noise& n) -> PyRandom& { return n.rng(); }, py::return_value_policy::reference_internal)
        .def("step", &Noise::step);
    add_state(noise);

    py::class_<Xorshift> xs(m, "Xorshift");
    xs.def(py::init<std::uint64_t>(), py::arg("seed") = 0)
        .def_static("seed_state", &Xorshift::seed_state, py::arg("seed"))
        .def("next_u64", &Xorshift::next_u64)
        .def("random", &Xorshift::random)
        .def("gauss", &Xorshift::gauss)
        .def_property("state", &Xorshift::state, &Xorshift::set_state);
    add_state(xs);

    py::class_<RandomWalk> walk(m, "RandomWalk");
    walk.def(py::init<double, double, std::uint64_t>(), py::arg("sigma") = 0.0, py::arg("tau") = 600.0,
             py::arg("seed") = 0)
        .def_readwrite("sigma", &RandomWalk::sigma)
        .def_readwrite("tau", &RandomWalk::tau)
        .def_readwrite("y", &RandomWalk::y)
        .def_property_readonly("_rng", [](RandomWalk& w) -> Xorshift& { return w.rng(); },
                               py::return_value_policy::reference_internal)
        .def("step", &RandomWalk::step);
    add_state(walk);

    py::class_<Debounce> db(m, "Debounce");
    db.def(py::init<double, bool>(), py::arg("delay"), py::arg("state") = false)
        .def_readwrite("delay", &Debounce::delay)
        .def_readwrite("state", &Debounce::state)
        .def("step", &Debounce::step);
    add_state(db);

    bind_devices(m);
    bind_tags(m);
    bind_bus(m);
    bind_units(m);
    bind_control(m);
    bind_scanner(m);
}
