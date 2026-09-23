// Value <-> Python conversion for state dictionaries, and the two
// binding helpers every Stateful class uses.
#pragma once

#include <pybind11/pybind11.h>

#include "azeocore/state.hpp"

namespace azeocore::pyconv {

namespace py = pybind11;

inline py::object to_py(const Value& v) {
    return std::visit([](auto&& x) -> py::object {
        using T = std::decay_t<decltype(x)>;
        if constexpr (std::is_same_v<T, std::nullptr_t>) return py::none();
        else if constexpr (std::is_same_v<T, bool>) return py::bool_(x);
        else if constexpr (std::is_same_v<T, double>) {
            // whole numbers that were ints in Python come back as ints when
            // they are small counters (quality, version); floats otherwise
            return py::float_(x);
        }
        else if constexpr (std::is_same_v<T, std::string>) return py::str(x);
        else if constexpr (std::is_same_v<T, List>) {
            py::list out;
            for (const Value& e : x) out.append(to_py(e));
            return out;
        }
        else {
            py::dict out;
            for (const auto& kv : x) out[py::str(kv.first)] = to_py(kv.second);
            return out;
        }
    }, v.storage());
}

inline Value from_py(py::handle h) {
    if (h.is_none()) return Value(nullptr);
    if (py::isinstance<py::bool_>(h)) return Value(py::cast<bool>(h));
    if (py::isinstance<py::int_>(h) || py::isinstance<py::float_>(h)) return Value(py::cast<double>(h));
    if (py::isinstance<py::str>(h)) return Value(py::cast<std::string>(h));
    if (py::isinstance<py::dict>(h)) {
        Dict d;
        for (auto kv : py::cast<py::dict>(h)) d[py::cast<std::string>(py::str(kv.first))] = from_py(kv.second);
        return Value(std::move(d));
    }
    if (py::isinstance<py::list>(h) || py::isinstance<py::tuple>(h)) {
        List l;
        for (auto e : h) l.push_back(from_py(e));
        return Value(std::move(l));
    }
    // anything else (numpy scalars and the like): try a float, else a string
    try { return Value(py::cast<double>(h)); } catch (const py::cast_error&) {}
    return Value(py::cast<std::string>(py::str(h)));
}

// Integer-valued fields that Python writes as ints: quality, rng_v
inline py::object to_py_int_aware(const Value& v, std::initializer_list<const char*> int_keys) {
    py::object o = to_py(v);
    if (v.is_dict()) {
        py::dict d = py::cast<py::dict>(o);
        for (const char* k : int_keys)
            if (d.contains(k) && py::isinstance<py::float_>(d[k])) d[k] = py::int_(static_cast<long long>(py::cast<double>(d[k])));
    }
    return o;
}

template <class Cls, class... Extra>
void add_state(py::class_<Cls, Extra...>& c) {
    c.def("capture_state", [](const Cls& self) { return to_py(self.capture_state()); });
    c.def("apply_state", [](Cls& self, py::handle s) { self.apply_state(from_py(s)); });
}

}  // namespace azeocore::pyconv
