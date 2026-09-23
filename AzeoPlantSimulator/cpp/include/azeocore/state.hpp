// A small JSON-like value for snapshot state.
//
// Every primitive, device, package and unit captures its state into one of
// these and applies from one, in C++, so the snapshot a native unit writes
// is byte-for-byte the dictionary its Python twin writes and the binding
// only converts. Snapshots are JSON files; this is that subset of JSON.
#pragma once

#include "azeocore/export.h"

#include <map>
#include <memory>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace azeocore {

class Value;
using Dict = std::map<std::string, Value>;
using List = std::vector<Value>;

class Value {
public:
    using Storage = std::variant<std::nullptr_t, bool, double, std::string, List, Dict>;

    Value() : v_(nullptr) {}
    Value(std::nullptr_t) : v_(nullptr) {}
    Value(bool b) : v_(b) {}
    Value(int i) : v_(static_cast<double>(i)) {}
    Value(double d) : v_(d) {}
    Value(const char* s) : v_(std::string(s)) {}
    Value(std::string s) : v_(std::move(s)) {}
    Value(List l) : v_(std::move(l)) {}
    Value(Dict d) : v_(std::move(d)) {}
    Value(const std::vector<double>& xs) {
        List l;
        l.reserve(xs.size());
        for (double x : xs) l.emplace_back(x);
        v_ = std::move(l);
    }

    bool is_null() const { return std::holds_alternative<std::nullptr_t>(v_); }
    bool is_bool() const { return std::holds_alternative<bool>(v_); }
    bool is_number() const { return std::holds_alternative<double>(v_); }
    bool is_string() const { return std::holds_alternative<std::string>(v_); }
    bool is_list() const { return std::holds_alternative<List>(v_); }
    bool is_dict() const { return std::holds_alternative<Dict>(v_); }

    // Python semantics: a bool is a number too, a number is truthy when non-zero
    double number(double dflt = 0.0) const {
        if (auto p = std::get_if<double>(&v_)) return *p;
        if (auto b = std::get_if<bool>(&v_)) return *b ? 1.0 : 0.0;
        return dflt;
    }
    bool boolean(bool dflt = false) const {
        if (auto b = std::get_if<bool>(&v_)) return *b;
        if (auto p = std::get_if<double>(&v_)) return *p != 0.0;
        return dflt;
    }
    const std::string& string(const std::string& dflt = empty_string()) const {
        if (auto s = std::get_if<std::string>(&v_)) return *s;
        return dflt;
    }
    const List& list() const { static const List none; auto p = std::get_if<List>(&v_); return p ? *p : none; }
    const Dict& dict() const { static const Dict none; auto p = std::get_if<Dict>(&v_); return p ? *p : none; }
    Dict& dict() { if (!is_dict()) v_ = Dict{}; return std::get<Dict>(v_); }
    List& list() { if (!is_list()) v_ = List{}; return std::get<List>(v_); }

    // dictionary access with Python's .get() semantics
    bool has(const std::string& key) const { return is_dict() && dict().count(key) != 0; }
    const Value& get(const std::string& key) const {
        static const Value none;
        if (!is_dict()) return none;
        auto it = dict().find(key);
        return it == dict().end() ? none : it->second;
    }
    double get_number(const std::string& key, double dflt) const { return has(key) ? get(key).number(dflt) : dflt; }
    bool get_bool(const std::string& key, bool dflt) const { return has(key) ? get(key).boolean(dflt) : dflt; }
    std::string get_string(const std::string& key, const std::string& dflt) const { return has(key) ? get(key).string(dflt) : dflt; }
    Value& operator[](const std::string& key) { return dict()[key]; }

    const Storage& storage() const { return v_; }

private:
    static const std::string& empty_string() { static const std::string s; return s; }
    Storage v_;
};

// Something that persists itself: every primitive, device, package and unit.
struct AZEOCORE_API Stateful {
    virtual ~Stateful() = default;
    virtual Value capture_state() const = 0;
    virtual void apply_state(const Value& s) = 0;
};

}  // namespace azeocore
