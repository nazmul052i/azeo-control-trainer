// Bindings for the tag database. Kind and quality cross the boundary as
// the Python enums the rest of the code compares by identity: the facade
// registers azeoplant.core.tags.Quality and TagKind once, and every
// getter converts through them.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <stdexcept>

#include "azeocore/tags.hpp"

namespace py = pybind11;
using namespace azeocore;

namespace {

py::object g_quality_cls;   // azeoplant.core.tags.Quality
py::object g_kind_cls;      // azeoplant.core.tags.TagKind

py::object to_py_quality(int q) {
    if (g_quality_cls) return g_quality_cls(q);
    return py::int_(q);
}

py::object to_py_kind(TagKind k) {
    if (g_kind_cls) return g_kind_cls(py::str(tag_kind_name(k)));
    return py::str(tag_kind_name(k));
}

int quality_from_py(const py::handle& o) { return py::cast<int>(o); }   // Quality is an IntEnum

TagKind kind_from_py(const py::handle& o) {
    std::string s;
    if (py::isinstance<py::str>(o)) s = py::cast<std::string>(o);
    else if (py::hasattr(o, "value")) s = py::cast<std::string>(o.attr("value"));
    else s = py::cast<std::string>(py::str(o));
    auto k = tag_kind_from(s);
    if (!k) throw py::value_error("'" + s + "' is not a valid TagKind");
    return *k;
}

py::object tag_value(const Tag& t) {
    if (t.analogue()) return py::float_(t.value);
    return py::bool_(t.value != 0.0);
}

double value_from_py(const Tag& t, const py::handle& o) {
    if (t.analogue()) return py::cast<double>(o);
    return py::cast<bool>(o) ? 1.0 : 0.0;
}

// Take the database lock with the GIL released, so a thread that already
// holds the lock from Python can finish what it is doing and let go.
static void acquire_without_gil(TagDatabase& db) {
    py::gil_scoped_release release;
    db.lock.lock();
}

struct LockGuard {
    RecursiveLock* lock;
    py::object owner;   // keeps the database alive while a guard exists
};

}  // namespace

py::object native_quality(int q) { return to_py_quality(q); }
py::object native_kind(TagKind k) { return to_py_kind(k); }

void bind_tags(py::module_& m) {
    m.def("register_enums", [](py::object quality, py::object kind) {
        g_quality_cls = std::move(quality);
        g_kind_cls = std::move(kind);
    }, py::arg("quality"), py::arg("kind"),
    "Register azeoplant.core.tags.Quality and TagKind so native tags hand back the Python enums.");
    m.add_object("_release_enums", py::capsule([]() { g_quality_cls = py::object(); g_kind_cls = py::object(); }));
    m.attr("OVER_RANGE") = TAG_OVER_RANGE;

    py::class_<LockGuard>(m, "TagLock")
        .def("acquire", [](LockGuard& g, bool blocking) {
            if (!blocking) return g.lock->try_lock();
            py::gil_scoped_release release;
            g.lock->lock();
            return true;
        }, py::arg("blocking") = true)
        .def("release", [](LockGuard& g) { g.lock->unlock(); })
        .def("__enter__", [](LockGuard& g) -> LockGuard& {
            py::gil_scoped_release release;
            g.lock->lock();
            return g;
        }, py::return_value_policy::reference)
        .def("__exit__", [](LockGuard& g, py::object, py::object, py::object) { g.lock->unlock(); return false; });

    py::class_<Tag>(m, "Tag")
        .def(py::init([](std::string name, py::object kind, std::string unit, std::string desc, std::string eu,
                         double lo, double hi, py::object value, py::object quality, py::object ts, std::string state0,
                         std::string state1, bool override_on, py::object override_value, int excursions, double worst) {
                 Tag t;
                 t.name = std::move(name); t.kind = kind_from_py(kind); t.unit = std::move(unit); t.desc = std::move(desc);
                 t.eu = std::move(eu); t.lo = lo; t.hi = hi;
                 t.value = value.is_none() ? 0.0 : value_from_py(t, value);
                 t.quality = quality.is_none() ? Q_GOOD : quality_from_py(quality);
                 t.ts = ts.is_none() ? wall_time() : py::cast<double>(ts);
                 t.state0 = std::move(state0); t.state1 = std::move(state1);
                 t.override_on = override_on;
                 t.override_value = override_value.is_none() ? 0.0 : value_from_py(t, override_value);
                 t.excursions = excursions; t.worst = worst;
                 return t;
             }),
             py::arg("name"), py::arg("kind"), py::arg("unit"), py::arg("desc"), py::arg("eu") = "",
             py::arg("lo") = 0.0, py::arg("hi") = 100.0, py::arg("value") = py::none(), py::arg("quality") = py::none(),
             py::arg("ts") = py::none(), py::arg("state0") = "Off", py::arg("state1") = "On",
             py::arg("override") = false, py::arg("override_value") = py::none(), py::arg("excursions") = 0,
             py::arg("worst") = 0.0)
        .def_readwrite("name", &Tag::name)
        .def_property("kind", [](const Tag& t) { return to_py_kind(t.kind); },
                      [](Tag& t, py::object o) { t.kind = kind_from_py(o); })
        .def_readwrite("unit", &Tag::unit)
        .def_readwrite("desc", &Tag::desc)
        .def_readwrite("eu", &Tag::eu)
        .def_readwrite("lo", &Tag::lo)
        .def_readwrite("hi", &Tag::hi)
        .def_property("value", &tag_value, [](Tag& t, py::object o) { t.value = value_from_py(t, o); })
        .def_property("quality", [](const Tag& t) { return to_py_quality(t.quality); },
                      [](Tag& t, py::object o) { t.quality = quality_from_py(o); })
        .def_readwrite("ts", &Tag::ts)
        .def_readwrite("state0", &Tag::state0)
        .def_readwrite("state1", &Tag::state1)
        .def_readwrite("override", &Tag::override_on)
        .def_property("override_value", [](const Tag& t) {
                          return t.analogue() ? py::object(py::float_(t.override_value)) : py::object(py::bool_(t.override_value != 0.0));
                      },
                      [](Tag& t, py::object o) { t.override_value = value_from_py(t, o); })
        .def_readwrite("excursions", &Tag::excursions)
        .def_readwrite("worst", &Tag::worst)
        .def_property_readonly("span", &Tag::span)
        .def_property_readonly("pct", &Tag::pct)
        .def_property_readonly("effective", [](const Tag& t) {
            const double v = t.effective();
            return t.analogue() ? py::object(py::float_(v)) : py::object(py::bool_(v != 0.0));
        })
        .def_property_readonly("node_id", &Tag::node_id)
        .def("set", [](Tag& t, py::object value, py::object quality) {
            double v;
            if (t.analogue()) v = py::cast<double>(value);
            else v = py::cast<bool>(value) ? 1.0 : 0.0;
            t.set(v, quality.is_none() ? Q_GOOD : quality_from_py(quality));
        }, py::arg("value"), py::arg("quality") = py::none())
        .def("set_from_dcs", [](Tag& t, py::object value) {
            double v;
            try {
                v = t.analogue() ? py::cast<double>(value) : (py::cast<bool>(value) ? 1.0 : 0.0);
            } catch (const py::cast_error&) {
                throw py::type_error(t.name + ": value is not a number");
            }
            std::pair<double, bool> r;
            try {
                r = t.set_from_dcs(v);
            } catch (const std::domain_error& e) {
                PyErr_SetString(PyExc_PermissionError, e.what());
                throw py::error_already_set();
            } catch (const std::invalid_argument& e) {
                throw py::value_error(e.what());
            }
            py::object applied = t.analogue() ? py::object(py::float_(r.first)) : py::object(py::bool_(r.first != 0.0));
            return py::make_tuple(applied, r.second);
        })
        .def("format", &Tag::format)
        .def("__repr__", [](const Tag& t) { return "Tag(" + t.name + ", " + tag_kind_name(t.kind) + ")"; });

    py::class_<TagDatabase>(m, "TagDatabase")
        .def(py::init<>())
        .def_property_readonly("lock", [](py::object self) {
            return LockGuard{&py::cast<TagDatabase&>(self).lock, self};
        })
        .def("add", [](TagDatabase& db, const Tag& t) -> Tag& {
            try { return db.add(t); } catch (const std::invalid_argument& e) { throw py::key_error(e.what()); }
        }, py::return_value_policy::reference_internal)
        .def("analog", [](TagDatabase& db, const std::string& name, py::object kind, const std::string& unit,
                          const std::string& desc, const std::string& eu, double lo, double hi, py::object value) -> Tag& {
            std::optional<double> v;
            if (!value.is_none()) v = py::cast<double>(value);
            try { return db.analog(name, kind_from_py(kind), unit, desc, eu, lo, hi, v); }
            catch (const std::invalid_argument& e) { throw py::key_error(e.what()); }
        }, py::arg("name"), py::arg("kind"), py::arg("unit"), py::arg("desc"), py::arg("eu"), py::arg("lo"), py::arg("hi"),
           py::arg("value") = py::none(), py::return_value_policy::reference_internal)
        .def("discrete", [](TagDatabase& db, const std::string& name, py::object kind, const std::string& unit,
                            const std::string& desc, const std::string& state0, const std::string& state1, bool value) -> Tag& {
            try { return db.discrete(name, kind_from_py(kind), unit, desc, state0, state1, value); }
            catch (const std::invalid_argument& e) { throw py::key_error(e.what()); }
        }, py::arg("name"), py::arg("kind"), py::arg("unit"), py::arg("desc"), py::arg("state0") = "Off",
           py::arg("state1") = "On", py::arg("value") = false, py::return_value_policy::reference_internal)
        .def("__contains__", &TagDatabase::contains)
        .def("__getitem__", [](TagDatabase& db, const std::string& name) -> Tag& {
            try { return db.at(name); } catch (const std::out_of_range&) { throw py::key_error(name); }
        }, py::return_value_policy::reference_internal)
        .def("__len__", &TagDatabase::size)
        .def("get", [](TagDatabase& db, const std::string& name) -> py::object {
            Tag* t = db.get(name);
            return t ? py::cast(t, py::return_value_policy::reference_internal, py::cast(db)) : py::none();
        })
        .def("all", &TagDatabase::all, py::return_value_policy::reference_internal)
        .def("by_kind", [](TagDatabase& db, py::args kinds) {
            std::vector<TagKind> ks;
            for (auto k : kinds) ks.push_back(kind_from_py(k));
            return db.by_kind(ks);
        }, py::return_value_policy::reference_internal)
        .def("by_unit", &TagDatabase::by_unit, py::return_value_policy::reference_internal)
        .def("units", &TagDatabase::units)
        .def("counts", &TagDatabase::counts)
        .def("excursions", &TagDatabase::excursions)
        .def("snapshot", [](TagDatabase& db, py::object names) {
            py::dict out;
            auto put = [&](Tag& t) { out[py::str(t.name)] = py::make_tuple(tag_value(t), t.quality, t.ts); };
            // Never wait for the database lock while holding the GIL: a
            // Python thread inside `with db.lock:` gives the GIL up at its
            // switch interval, and a caller that then blocks here with the
            // GIL held has deadlocked the process (the operator window
            // froze on exactly this, its refresh against the OPC publisher).
            acquire_without_gil(db);
            try {
                if (names.is_none()) for (Tag* t : db.all()) put(*t);
                else for (auto n : names) put(db.at(py::cast<std::string>(n)));
            } catch (...) { db.lock.unlock(); throw; }
            db.lock.unlock();
            return out;
        }, py::arg("names") = py::none())
        .def("save_state", [](TagDatabase& db) {
            py::dict out;
            acquire_without_gil(db);
            for (Tag* t : db.all()) out[py::str(t->name)] = tag_value(*t);
            db.lock.unlock();
            return out;
        })
        .def("load_state", [](TagDatabase& db, py::dict state) {
            int applied = 0;
            acquire_without_gil(db);
            try {
                for (auto item : state) {
                    Tag* t = db.get(py::cast<std::string>(item.first));
                    if (!t) continue;
                    t->value = t->analogue() ? py::cast<double>(item.second) : (py::cast<bool>(item.second) ? 1.0 : 0.0);
                    t->quality = Q_GOOD;
                    t->ts = wall_time();
                    ++applied;
                }
            } catch (...) { db.lock.unlock(); throw; }
            db.lock.unlock();
            return applied;
        });
}
