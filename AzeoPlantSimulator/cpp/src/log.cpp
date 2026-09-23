#include "azeocore/log.hpp"

#include <cstdio>
#include <mutex>

namespace azeocore {

namespace {
std::mutex g_mutex;
LogSink g_sink;
LogLevel g_threshold = LogLevel::Debug;

const char* level_name(LogLevel l) {
    switch (l) {
    case LogLevel::Debug: return "DEBUG";
    case LogLevel::Info: return "INFO";
    case LogLevel::Warning: return "WARNING";
    case LogLevel::Error: return "ERROR";
    case LogLevel::Critical: return "CRITICAL";
    }
    return "INFO";
}
}  // namespace

void set_log_sink(LogSink sink) {
    std::lock_guard<std::mutex> g(g_mutex);
    g_sink = std::move(sink);
}

void set_log_threshold(LogLevel level) {
    std::lock_guard<std::mutex> g(g_mutex);
    g_threshold = level;
}

bool log_enabled(LogLevel level) { return static_cast<int>(level) >= static_cast<int>(g_threshold); }

void log(LogLevel level, const std::string& logger, const std::string& message) {
    if (!log_enabled(level)) return;
    LogSink sink;
    {
        std::lock_guard<std::mutex> g(g_mutex);
        sink = g_sink;
    }
    if (sink) {
        sink(level, logger, message);
    } else {
        std::fprintf(stderr, "%-8s %s  %s\n", level_name(level), logger.c_str(), message.c_str());
    }
}

std::string fmt_g(double v) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%g", v);
    return buf;
}

std::string TraceFrame::render() const {
    std::string out;
    for (const auto& kv : values_) {
        if (!out.empty()) out.push_back(' ');
        char buf[48];
        std::snprintf(buf, sizeof buf, "%.6g", kv.second);
        out += kv.first;
        out.push_back('=');
        out += buf;
    }
    return out;
}

}  // namespace azeocore
