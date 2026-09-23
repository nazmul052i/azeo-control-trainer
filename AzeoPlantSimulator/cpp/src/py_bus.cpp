// Bindings for the virtual I/O bus and the process bus.
//
// The I/O bus lives in the library (azeocore::IOBus); this file wraps it:
// the facade registers the Python classes the bus must hand back or raise
// (SignalSample, OwnershipViolation, BusContractError, ForceRecord), the
// wrapper parses client values at the doorway and dispatches the Python
// subscribers from the bus's on_change hook.
#include <pybind11/functional.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <map>
#include <mutex>
#include <vector>

#include "azeocore/bus.hpp"
#include "azeocore/iobus.hpp"
#include "azeocore/log.hpp"
#include "azeocore/tags.hpp"
#include "py_state.hpp"

namespace py = pybind11;
using namespace azeocore;

py::object native_quality(int q);
py::object native_kind(TagKind k);

namespace {

py::object g_sample_cls, g_ownership_exc, g_contract_exc, g_force_cls;

// the bus logs under the name the Python bus uses, through the core sink
void logmsg(const char* level, const std::string& msg) {
    const std::string lv = level;
    LogLevel l = LogLevel::Info;
    if (lv == "warning") l = LogLevel::Warning;
    else if (lv == "debug") l = LogLevel::Debug;
    else if (lv == "error" || lv == "exception") l = LogLevel::Error;
    log(l, "azeoplant.io.bus", msg);
}

[[noreturn]] void raise_ownership(const std::string& tag, const std::string& by) {
    if (g_ownership_exc && !g_ownership_exc.is_none()) {
        py::object exc = g_ownership_exc(tag, by);
        PyErr_SetObject(g_ownership_exc.ptr(), exc.ptr());
        throw py::error_already_set();
    }
    throw std::runtime_error(by + " may not write " + tag + ": wrong side of the virtual I/O boundary");
}

[[noreturn]] void raise_contract(const std::string& msg) {
    if (g_contract_exc && !g_contract_exc.is_none()) {
        PyErr_SetString(g_contract_exc.ptr(), msg.c_str());
        throw py::error_already_set();
    }
    throw std::runtime_error(msg);
}

py::object tag_value_obj(const Tag& t) {
    if (t.analogue()) return py::float_(t.value);
    return py::bool_(t.value != 0.0);
}

// ------------------------------------------------------------ I/O bus
// The bus itself is azeocore::IOBus (cpp/src/iobus.cpp). This wrapper owns
// one, keeps the Python database object alive, parses client values once
// at the doorway, dispatches the Python subscribers from the bus's
// on_change hook, and turns the bus's verdicts into the Python types and
// exceptions the callers expect.
struct PyIOBus {
    static constexpr int MAX_PENDING_WRITES = IOBus::MAX_PENDING_WRITES;
    py::object db_obj;                 // keeps the database alive
    IOBus bus;
    std::map<std::string, std::vector<py::object>> subs;

    PyIOBus(py::object dbo, double stale_timeout_s)
        : db_obj(std::move(dbo)), bus(*py::cast<TagDatabase*>(db_obj), stale_timeout_s) {
        bus.on_change = [this](const std::string& tag) { notify(tag); };
    }
    PyIOBus(const PyIOBus&) = delete;
    PyIOBus& operator=(const PyIOBus&) = delete;
    // Members die in reverse order: subs first, then the bus. Anything the
    // bus does on its way out that changes a tag (a force released, a
    // pending write dropped) would dispatch into the destroyed subscriber
    // map: the access violation at interpreter exit. Unhook first.
    ~PyIOBus() { bus.on_change = nullptr; subs.clear(); }

    void notify(const std::string& tag) {
        auto it = subs.find(tag);
        if (it == subs.end()) return;
        for (auto& cb : it->second) {
            try { cb(tag); } catch (py::error_already_set& e) { logmsg("exception", "Bus subscriber failed for " + tag + ": " + e.what()); }
        }
    }
};

// a client's value, parsed once: a number when it can be read as one, and
// its truth for discretes, as the Python bus's float() and bool() would
PendingValue pending_of(const py::handle& v) {
    PendingValue p;
    try { p.number = py::cast<double>(v); p.numeric = true; } catch (const py::cast_error&) { p.numeric = false; }
    p.truthy = PyObject_IsTrue(v.ptr()) == 1;
    return p;
}

py::object sample_obj(const IOSample& s) {
    return g_sample_cls(py::arg("name") = s.name,
                        py::arg("value") = (s.analogue ? py::object(py::float_(s.value)) : py::object(py::bool_(s.value != 0.0))),
                        py::arg("quality") = native_quality(s.quality), py::arg("timestamp") = s.ts,
                        py::arg("kind") = native_kind(s.kind), py::arg("unit") = s.unit, py::arg("eu") = s.eu,
                        py::arg("lo") = s.lo, py::arg("hi") = s.hi);
}

// ---------------------------------------------------------- process bus
struct SourceScope {
    ProcessBus* bus;
    std::string name;
    std::optional<std::string> previous;
};

}  // namespace


// The strategy scanner's output path: write_from_dcs without a Python
// call per loop. Same ownership and holder checks, same apply.
bool iobus_write_from_dcs(py::handle bus, const std::string& tag, double value, const std::string& source) {
    PyIOBus& b = py::cast<PyIOBus&>(bus);
    PendingValue v; v.numeric = true; v.number = value; v.truthy = value != 0.0;
    const WriteVerdict r = b.bus.write_from_dcs(tag, v, source);
    if (r == WriteVerdict::RejectedOwnership) raise_ownership(tag, source);
    return r == WriteVerdict::Applied;
}

void bind_bus(py::module_& m) {
    m.def("register_io_types", [](py::object sample, py::object ownership, py::object contract, py::object force,
                                  py::object) {
        g_sample_cls = std::move(sample); g_ownership_exc = std::move(ownership); g_contract_exc = std::move(contract);
        g_force_cls = std::move(force);
    }, py::arg("sample"), py::arg("ownership"), py::arg("contract"), py::arg("force"), py::arg("logger") = py::none(),
       "Register the Python types the I/O bus hands back or raises; logging goes through set_log_sink.");
    // Released while the interpreter is still alive: a static py::object
    // destroyed after finalisation is a use after free at exit.
    m.add_object("_release_io_types", py::capsule([]() {
        g_sample_cls = py::object(); g_ownership_exc = py::object();
        g_contract_exc = py::object(); g_force_cls = py::object();
    }));

    py::class_<BusStats>(m, "BusStats")
        .def(py::init<>())
        .def_readwrite("sim_writes", &BusStats::sim_writes).def_readwrite("dcs_writes", &BusStats::dcs_writes)
        .def_readwrite("queued", &BusStats::queued).def_readwrite("drained", &BusStats::drained)
        .def_readwrite("rejected_ownership", &BusStats::rejected_ownership)
        .def_readwrite("rejected_holder", &BusStats::rejected_holder)
        .def_readwrite("rejected_value", &BusStats::rejected_value)
        .def_readwrite("adjusted_writes", &BusStats::adjusted_writes)
        .def_readwrite("coalesced_writes", &BusStats::coalesced_writes)
        .def_readwrite("rejected_capacity", &BusStats::rejected_capacity)
        .def_readwrite("pending_peak", &BusStats::pending_peak).def_readwrite("forces_applied", &BusStats::forces_applied);

    py::class_<StaleTracker>(m, "StaleTracker")
        .def(py::init<double>(), py::arg("timeout_s") = 2.0)
        .def_readwrite("timeout_s", &StaleTracker::timeout_s)
        .def("touch", &StaleTracker::touch).def("disarm", &StaleTracker::disarm).def("disarm_all", &StaleTracker::disarm_all)
        .def("scan", &StaleTracker::scan).def("is_stale", &StaleTracker::is_stale).def("armed", &StaleTracker::armed);

    py::class_<PyIOBus>(m, "VirtualIOBus",
        // The bus holds Python callables (its subscribers). A subscriber that
        // is a module-level function refers to the module dictionary, which
        // refers to the bus: a cycle the collector can only break if this
        // type takes part in garbage collection. Without it the bus outlives
        // the interpreter and its destructor decrefs dead objects at exit.
        py::custom_type_setup([](PyHeapTypeObject* heap_type) {
            auto* type = &heap_type->ht_type;
            type->tp_flags |= Py_TPFLAGS_HAVE_GC;
            type->tp_traverse = [](PyObject* self_base, visitproc visit, void* arg) {
                auto& self = py::cast<PyIOBus&>(py::handle(self_base));
                Py_VISIT(self.db_obj.ptr());
                for (auto& kv : self.subs) for (auto& cb : kv.second) Py_VISIT(cb.ptr());
                return 0;
            };
            type->tp_clear = [](PyObject* self_base) {
                auto& self = py::cast<PyIOBus&>(py::handle(self_base));
                self.bus.on_change = nullptr;
                self.subs.clear();
                return 0;
            };
        }))
        .def(py::init<py::object, double>(), py::arg("db"), py::arg("stale_timeout_s") = 2.0)
        .def_property_readonly("db", [](PyIOBus& b) { return b.db_obj; })
        .def_property("stats", [](PyIOBus& b) -> BusStats& { return b.bus.stats; },
                      [](PyIOBus& b, const BusStats& st) { b.bus.stats = st; }, py::return_value_policy::reference_internal)
        .def_property("stale", [](PyIOBus& b) -> StaleTracker& { return b.bus.stale; },
                      [](PyIOBus& b, const StaleTracker& st) { b.bus.stale = st; }, py::return_value_policy::reference_internal)
        .def_property_readonly_static("MAX_PENDING_WRITES", [](py::object) { return PyIOBus::MAX_PENDING_WRITES; })
        .def("read", [](PyIOBus& b, const std::string& tag) { return b.db_obj.attr("__getitem__")(tag); })
        .def("samples", [](PyIOBus& b, py::object tags) {
            py::dict out;
            std::vector<std::string> names;
            std::vector<IOSample> rows;
            if (tags.is_none()) rows = b.bus.samples(nullptr);
            else {
                for (auto n : tags) names.push_back(py::cast<std::string>(n));
                rows = b.bus.samples(&names);
            }
            for (const IOSample& s : rows) out[py::str(s.name)] = sample_obj(s);
            return out;
        }, py::arg("tags") = py::none())
        .def("sample", [](PyIOBus& b, const std::string& tag) { return sample_obj(b.bus.sample(tag)); })
        .def("owner", [](PyIOBus& b, const std::string& tag) { return std::string(b.bus.owner(tag)); })
        .def("register_dcs", [](PyIOBus& b, const std::string& name) { return b.bus.register_dcs(name); })
        .def("release_dcs", [](PyIOBus& b, const std::string& name) { b.bus.release_dcs(name); })
        .def_property_readonly("holder", [](PyIOBus& b) -> py::object { return b.bus.holder ? py::cast(*b.bus.holder) : py::none(); })
        .def("write_from_dcs", [](PyIOBus& b, const std::string& tag, py::object value, const std::string& source) {
            const WriteVerdict r = b.bus.write_from_dcs(tag, pending_of(value), source);
            if (r == WriteVerdict::RejectedOwnership) raise_ownership(tag, source);
            return r == WriteVerdict::Applied;
        }, py::arg("tag"), py::arg("value"), py::arg("source") = "internal")
        .def("authorize_dcs_write", [](PyIOBus& b, const std::string& tag, const std::string& source) {
            return b.bus.authorize_dcs_write(tag, source);
        }, py::arg("tag"), py::arg("source") = "external")
        .def("queue_write", [](PyIOBus& b, const std::string& tag, py::object value, const std::string& source) {
            try {
                b.bus.queue_write(tag, pending_of(value), source);
            } catch (const std::length_error& e) {
                PyErr_SetString(PyExc_BufferError, e.what());
                throw py::error_already_set();
            }
        }, py::arg("tag"), py::arg("value"), py::arg("source") = "external")
        .def("drain_writes", [](PyIOBus& b) { return b.bus.drain_writes(); })
        .def("queue_health", [](PyIOBus& b) {
            const QueueHealth h = b.bus.queue_health();
            py::dict d;
            d["pending"] = h.pending; d["capacity"] = h.capacity; d["peak"] = h.peak; d["coalesced"] = h.coalesced;
            d["rejected_capacity"] = h.rejected_capacity;
            return d;
        })
        .def("force", [](PyIOBus& b, const std::string& tag, py::object value, const std::string& source, const std::string& reason) {
            if (reason.empty()) throw py::value_error("a force needs a reason");
            const Tag& t = b.bus.db.at(tag);
            const double v = t.analogue() ? py::cast<double>(value) : (PyObject_IsTrue(value.ptr()) == 1 ? 1.0 : 0.0);
            b.bus.force(tag, v, source, reason);
        }, py::arg("tag"), py::arg("value"), py::arg("source"), py::arg("reason"))
        .def("release", [](PyIOBus& b, const std::string& tag) { b.bus.release(tag); })
        .def("simulate_input", [](PyIOBus& b, const std::string& tag, py::object value,
                                   py::object quality, const std::string& source) {
            int q;
            if (py::isinstance<py::str>(quality)) {
                const std::string name = py::cast<std::string>(quality.attr("strip")().attr("upper")());
                if (name == "GOOD") q = Q_GOOD;
                else if (name == "UNCERTAIN") q = Q_UNCERTAIN;
                else if (name == "BAD") q = Q_BAD;
                else throw py::value_error("unsupported quality: " + name);
            } else q = py::cast<int>(quality);
            const double v = py::cast<double>(value);
            // Never wait for the engine's database lock with the GIL held.
            { py::gil_scoped_release release; b.bus.db.lock.lock(); }
            std::lock_guard<RecursiveLock> guard(b.bus.db.lock, std::adopt_lock);
            if (b.bus.owner(tag) == std::string("DCS")) {
                PyErr_SetString(PyExc_PermissionError, "only AI and DI can be simulated");
                throw py::error_already_set();
            }
            b.bus.simulate_input(tag, v, q, source);
        }, py::arg("tag"), py::arg("value"), py::arg("quality"),
           py::arg("source") = "virtual-io-simulator")
        .def("clear_simulated_input", [](PyIOBus& b, const std::string& tag) { b.bus.release(tag); })
        .def("release_all", [](PyIOBus& b) { b.bus.release_all(); })
        .def("active_forces", [](PyIOBus& b) {
            py::list out;
            for (const ForceRecord& r : b.bus.active_forces()) {
                const Tag* t = b.bus.db.get(r.tag);
                const bool disc = t && !t->analogue();
                py::object v = disc ? py::object(py::bool_(r.value != 0.0)) : py::object(py::float_(r.value));
                py::object tv = disc ? py::object(py::bool_(r.true_value != 0.0)) : py::object(py::float_(r.true_value));
                out.append(g_force_cls(py::arg("tag") = r.tag, py::arg("value") = v, py::arg("source") = r.source,
                                       py::arg("reason") = r.reason, py::arg("t") = r.t, py::arg("true_value") = tv));
            }
            return out;
        })
        .def("tick", [](PyIOBus& b, double) { b.bus.tick(); }, py::arg("dt") = 0.0)
        .def("subscribe", [](PyIOBus& b, const std::string& tag, py::object cb) { b.subs[tag].push_back(std::move(cb)); })
        .def("unsubscribe", [](PyIOBus& b, const std::string& tag, py::object cb) {
            auto it = b.subs.find(tag);
            if (it == b.subs.end()) return;
            auto& v = it->second;
            auto pos = std::find_if(v.begin(), v.end(), [&](const py::object& o) { return o.is(cb); });
            if (pos != v.end()) v.erase(pos);
        })
        .def("capture_state", [](PyIOBus& b) { return azeocore::pyconv::to_py(b.bus.capture_state()); })
        .def("apply_state", [](PyIOBus& b, py::object s) {
            if (s.is_none()) { b.bus.release_all(); return; }
            b.bus.apply_state(azeocore::pyconv::from_py(s));
        });

    // ---------------------------------------------------------- process bus
    py::class_<BusSignalSpec>(m, "BusSignalSpec")
        .def(py::init([](std::string name, double dflt, std::string eu, std::string producer, std::vector<std::string> consumers,
                         py::object lo, py::object hi, bool tear, std::string description) {
                 BusSignalSpec s;
                 s.name = std::move(name); s.dflt = dflt; s.eu = std::move(eu); s.producer = std::move(producer);
                 s.consumers = std::move(consumers);
                 if (!lo.is_none()) s.lo = py::cast<double>(lo);
                 if (!hi.is_none()) s.hi = py::cast<double>(hi);
                 s.tear = tear; s.description = std::move(description);
                 try { s.validate(); } catch (const std::invalid_argument& e) { throw py::value_error(e.what()); }
                 return s;
             }),
             py::arg("name"), py::arg("default"), py::arg("eu"), py::arg("producer"),
             py::arg("consumers") = std::vector<std::string>{}, py::arg("lo") = py::none(), py::arg("hi") = py::none(),
             py::arg("tear") = false, py::arg("description") = "")
        .def_readonly("name", &BusSignalSpec::name)
        .def_readonly("default", &BusSignalSpec::dflt)
        .def_readonly("eu", &BusSignalSpec::eu)
        .def_readonly("producer", &BusSignalSpec::producer)
        .def_property_readonly("consumers", [](const BusSignalSpec& s) { return py::tuple(py::cast(s.consumers)); })
        .def_property_readonly("lo", [](const BusSignalSpec& s) -> py::object { return s.lo ? py::cast(*s.lo) : py::none(); })
        .def_property_readonly("hi", [](const BusSignalSpec& s) -> py::object { return s.hi ? py::cast(*s.hi) : py::none(); })
        .def_readonly("tear", &BusSignalSpec::tear)
        .def_readonly("description", &BusSignalSpec::description);

    py::class_<SourceScope>(m, "SourceScope")
        .def("__enter__", [](SourceScope& s) { s.previous = s.bus->active_source(); s.bus->set_source(s.name); })
        .def("__exit__", [](SourceScope& s, py::object, py::object, py::object) { s.bus->set_source(s.previous); return false; });

    py::class_<ProcessBus>(m, "ProcessBus")
        .def(py::init([](py::iterable specs) {
            std::vector<BusSignalSpec> v;
            for (auto s : specs) v.push_back(py::cast<BusSignalSpec>(s));
            try { return new ProcessBus(v); } catch (const std::invalid_argument& e) { throw py::value_error(e.what()); }
        }))
        .def_property_readonly("specs", [](ProcessBus& b) {
            py::dict d;
            for (const auto& n : b.order()) d[py::str(n)] = b.specs().at(n);
            return d;
        })
        .def_property_readonly("active_source", [](ProcessBus& b) -> py::object {
            auto s = b.active_source(); return s ? py::cast(*s) : py::none();
        })
        .def("source", [](ProcessBus& b, const std::string& name) { return SourceScope{&b, name, std::nullopt}; }, py::keep_alive<0, 1>())
        .def("__getitem__", [](ProcessBus& b, const std::string& name) {
            try { return b.read(name); } catch (const BusContractError& e) { raise_contract(e.message); }
        })
        .def("get", [](ProcessBus& b, const std::string& name, py::object) {
            try { return b.read(name); } catch (const BusContractError& e) { raise_contract(e.message); }
        }, py::arg("name"), py::arg("default") = py::none())
        .def("__setitem__", [](ProcessBus& b, const std::string& name, py::object value) {
            double v;
            try { v = py::cast<double>(value); }
            catch (const py::cast_error&) {
                b.stats.last_violation = name + ": non-numeric bus value " + py::cast<std::string>(py::repr(value));
                raise_contract(b.stats.last_violation);
            }
            try { b.write(name, v); } catch (const BusContractError& e) { raise_contract(e.message); }
        })
        .def("__contains__", &ProcessBus::has)
        .def("__len__", [](ProcessBus& b) { return b.order().size(); })
        .def("__iter__", [](ProcessBus& b) { return py::iter(py::cast(b.order())); })
        .def("keys", [](ProcessBus& b) { return b.order(); })
        .def("items", [](ProcessBus& b) {
            py::list out;
            for (const auto& n : b.order()) out.append(py::make_tuple(n, b.raw(n)));
            return out;
        })
        .def("values", [](ProcessBus& b) { std::vector<double> out; for (const auto& n : b.order()) out.push_back(b.raw(n)); return out; })
        .def("restore", [](ProcessBus& b, const std::string& name, double value) {
            try { b.restore(name, value); } catch (const BusContractError& e) { raise_contract(e.message); }
        })
        .def("current_range_issues", &ProcessBus::current_range_issues)
        .def("validate_topology", &ProcessBus::validate_topology)
        .def("contract_rows", [](ProcessBus& b) {
            py::list out;
            for (const auto& n : b.order()) {
                const BusSignalSpec& s = b.specs().at(n);
                py::dict d;
                d["name"] = s.name; d["default"] = s.dflt; d["eu"] = s.eu; d["producer"] = s.producer;
                d["consumers"] = py::tuple(py::cast(s.consumers));
                d["lo"] = s.lo ? py::object(py::cast(*s.lo)) : py::object(py::none());
                d["hi"] = s.hi ? py::object(py::cast(*s.hi)) : py::object(py::none());
                d["tear"] = s.tear; d["description"] = s.description;
                out.append(d);
            }
            return out;
        })
        .def_property_readonly("stats", [](ProcessBus& b) -> ProcessBusStats& { return b.stats; },
                               py::return_value_policy::reference_internal)
        .def("stats_snapshot", [](ProcessBus& b) { return ProcessBusStats(b.stats); });

    py::class_<ProcessBusStats>(m, "ProcessBusStats")
        .def(py::init<>())
        .def_readwrite("reads", &ProcessBusStats::reads)
        .def_readwrite("writes", &ProcessBusStats::writes)
        .def_readwrite("range_excursions", &ProcessBusStats::range_excursions)
        .def_readwrite("producer_violations", &ProcessBusStats::producer_violations)
        .def_readwrite("consumer_violations", &ProcessBusStats::consumer_violations)
        .def_readwrite("unknown_signals", &ProcessBusStats::unknown_signals)
        .def_readwrite("rejected_nonfinite", &ProcessBusStats::rejected_nonfinite)
        .def_readwrite("last_violation", &ProcessBusStats::last_violation)
        .def_property_readonly("excursions_by_signal", [](const ProcessBusStats& s) {
            py::dict d;
            for (const auto& kv : s.excursions_by_signal) d[py::str(kv.first)] = kv.second;
            return d;
        })
        .def("as_dict", [](const ProcessBusStats& s) {
            py::dict d;
            d["reads"] = s.reads; d["writes"] = s.writes; d["range_excursions"] = s.range_excursions;
            d["producer_violations"] = s.producer_violations; d["consumer_violations"] = s.consumer_violations;
            d["unknown_signals"] = s.unknown_signals; d["rejected_nonfinite"] = s.rejected_nonfinite;
            d["last_violation"] = s.last_violation;
            py::dict ex;
            for (const auto& kv : s.excursions_by_signal) ex[py::str(kv.first)] = kv.second;
            d["excursions_by_signal"] = ex;
            return d;
        });

    py::class_<BalanceReading>(m, "BalanceReading")
        .def(py::init([](std::string unit, std::string name, double inflow, double outflow, double accumulation,
                         double residual, double tolerance, std::string eu, std::string kind) {
                 BalanceReading r;
                 r.unit = std::move(unit); r.name = std::move(name); r.inflow = inflow; r.outflow = outflow;
                 r.accumulation = accumulation; r.residual = residual; r.tolerance = tolerance; r.eu = std::move(eu);
                 r.kind = std::move(kind);
                 return r;
             }),
             py::arg("unit"), py::arg("name"), py::arg("inflow"), py::arg("outflow"), py::arg("accumulation"),
             py::arg("residual"), py::arg("tolerance"), py::arg("eu"), py::arg("kind") = "material")
        .def_readonly("unit", &BalanceReading::unit).def_readonly("name", &BalanceReading::name)
        .def_readonly("inflow", &BalanceReading::inflow).def_readonly("outflow", &BalanceReading::outflow)
        .def_readonly("accumulation", &BalanceReading::accumulation).def_readonly("residual", &BalanceReading::residual)
        .def_readonly("tolerance", &BalanceReading::tolerance).def_readonly("eu", &BalanceReading::eu)
        .def_readonly("kind", &BalanceReading::kind)
        .def_property_readonly("healthy", &BalanceReading::healthy);
}
