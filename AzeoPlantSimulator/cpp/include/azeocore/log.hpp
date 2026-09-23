// Logging and the calculation trace.
//
// The native core logs through one sink, by logger name and level, and
// the Python binding points that sink at the standard ``logging`` module
// under the same names the Python core uses ("azeoplant.io.bus",
// "azeoplant.models.u100_feed", ...), so a native unit's messages land
// where the Python unit's did. Without a Python sink, messages go to
// stderr.
//
// The calculation trace is the troubleshooting aid: a unit records its
// intermediates each step by name (suction and discharge pressures,
// valve flows, inventory in, out and net, balance residuals), the last
// frame is readable from Python at any time, and when tracing is enabled
// for that unit the frame is logged at debug level every N steps. When
// tracing is off the cost is one boolean test per value.
#pragma once

#include "azeocore/export.h"

#include <cstdio>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace azeocore {

enum class LogLevel : int { Debug = 10, Info = 20, Warning = 30, Error = 40, Critical = 50 };

using LogSink = std::function<void(LogLevel, const std::string& logger, const std::string& message)>;

AZEOCORE_API void set_log_sink(LogSink sink);          // nullptr restores the stderr sink
AZEOCORE_API void set_log_threshold(LogLevel level);   // below this nothing is formatted
AZEOCORE_API bool log_enabled(LogLevel level);
AZEOCORE_API void log(LogLevel level, const std::string& logger, const std::string& message);

inline void log_debug(const std::string& logger, const std::string& msg) { log(LogLevel::Debug, logger, msg); }
inline void log_info(const std::string& logger, const std::string& msg) { log(LogLevel::Info, logger, msg); }
inline void log_warning(const std::string& logger, const std::string& msg) { log(LogLevel::Warning, logger, msg); }
inline void log_error(const std::string& logger, const std::string& msg) { log(LogLevel::Error, logger, msg); }
inline void log_critical(const std::string& logger, const std::string& msg) { log(LogLevel::Critical, logger, msg); }

AZEOCORE_API std::string fmt_g(double v);   // %g, as Python prints in its messages

class AZEOCORE_API TraceFrame {
public:
    void clear() { values_.clear(); }
    void put(const char* name, double value) { values_.emplace_back(name, value); }
    void put(const std::string& name, double value) { values_.emplace_back(name, value); }
    const std::vector<std::pair<std::string, double>>& values() const { return values_; }
    bool empty() const { return values_.empty(); }
    std::string render() const;   // "name=value name=value ..."

private:
    std::vector<std::pair<std::string, double>> values_;
};

}  // namespace azeocore
