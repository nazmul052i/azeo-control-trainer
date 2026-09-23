#include "azeocore/dynamics.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <string>

namespace azeocore {

double clamp(double x, double lo, double hi) {
    if (!std::isfinite(x)) return lo;
    return x < lo ? lo : (x > hi ? hi : x);
}

double lerp(double a, double b, double f) { return a + (b - a) * clamp(f, 0.0, 1.0); }

double safe_sqrt(double x) { return x > 0.0 ? std::sqrt(x) : 0.0; }

double safe_div(double num, double den, double dflt, double eps) {
    return std::fabs(den) > eps ? num / den : dflt;
}

double py_pow(double base, double exponent) { return std::pow(base, exponent); }
double py_exp(double x) { return std::exp(x); }
double py_sin(double x) { return std::sin(x); }
double py_cos(double x) { return std::cos(x); }
double py_log(double x) { return std::log(x); }

// --------------------------------------------------------------- Xorshift
Xorshift::Xorshift(std::uint64_t seed) : state_(seed_state(seed)) {}

std::uint64_t Xorshift::seed_state(std::uint64_t seed) {
    std::uint64_t z = seed + 0x9E3779B97F4A7C15ULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    z ^= z >> 31;
    return z ? z : 0x9E3779B97F4A7C15ULL;
}

std::uint64_t Xorshift::next_u64() {
    std::uint64_t x = state_;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    state_ = x;
    return x * 0x2545F4914F6CDD1DULL;
}

double Xorshift::random() {
    // the top 53 bits: exact in a double, exactly what Python computes
    return static_cast<double>(next_u64() >> 11) * (1.0 / 9007199254740992.0);
}

double Xorshift::gauss() {
    double s = 0.0;
    for (int i = 0; i < 12; ++i) s += random();
    return s - 6.0;
}

Value Xorshift::capture_state() const {
    char buf[17];
    std::snprintf(buf, sizeof buf, "%016llx", static_cast<unsigned long long>(state_));
    Dict d;
    d["s"] = std::string(buf);
    return d;
}

void Xorshift::apply_state(const Value& s) {
    if (!s.has("s") || !s.get("s").is_string()) return;
    try {
        set_state(static_cast<std::uint64_t>(std::stoull(s.get("s").string(), nullptr, 16)));
    } catch (const std::exception&) {
        // a foreign generator state: the walk restarts, the plant is exact
    }
}

// ------------------------------------------------------------- RandomWalk
RandomWalk::RandomWalk(double sigma_, double tau_, std::uint64_t seed)
    : sigma(sigma_), tau(std::max(tau_, 1e-3)), rng_(seed) {}

double RandomWalk::step(double dt) {
    if (sigma <= 0.0) return 0.0;
    const double a = py_exp(-std::max(dt, 0.0) / std::max(tau, 1e-3));
    y = y * a + sigma * std::sqrt(1.0 - a * a) * rng_.gauss();
    return y;
}

Value RandomWalk::capture_state() const {
    Dict d;
    d["y"] = y;
    d["rng"] = rng_.capture_state();
    return d;
}

void RandomWalk::apply_state(const Value& s) {
    y = s.get_number("y", y);
    if (s.get("rng").is_dict()) rng_.apply_state(s.get("rng"));
}

// ------------------------------------------------------------------ crc32
std::uint32_t crc32(std::string_view data) {
    static std::uint32_t table[256];
    static bool ready = false;
    if (!ready) {
        for (std::uint32_t i = 0; i < 256; ++i) {
            std::uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            table[i] = c;
        }
        ready = true;
    }
    std::uint32_t c = 0xFFFFFFFFu;
    for (unsigned char ch : data) c = table[(c ^ ch) & 0xFFu] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

// -------------------------------------------------------------------- Lag
Lag::Lag(double tau_, double y0) : tau(std::max(tau_, 1e-6)), y(y0) {}

double Lag::step(double u, double dt) {
    if (!std::isfinite(u)) return y;
    const double alpha = 1.0 - std::exp(-std::max(dt, 0.0) / tau);
    y += (u - y) * alpha;
    return y;
}

// ---------------------------------------------------------------- LeadLag
LeadLag::LeadLag(double lead_, double lag_, double y0)
    : lead(std::max(lead_, 0.0)), lag(std::max(lag_, 1e-6)), y(y0), u_prev(y0) {}

double LeadLag::step(double u, double dt) {
    const double alpha = 1.0 - std::exp(-std::max(dt, 0.0) / lag);
    const double du = (u - u_prev) / std::max(dt, 1e-6);
    y += (u + lead * du - y) * alpha;
    u_prev = u;
    return y;
}

// --------------------------------------------------------------- DeadTime
DeadTime::DeadTime(double delay, double dt, double y0)
    : delay_(std::max(delay, 0.0)), dt_(std::max(dt, 1e-6)) {
    // Python: n = max(1, int(round(delay / dt))); round half to even
    const double n = std::nearbyint(delay_ / dt_);
    buf_.assign(static_cast<std::size_t>(std::max(1.0, n)), y0);
}

double DeadTime::step(double u) {
    const double out = buf_[head_];
    buf_[head_] = std::isfinite(u) ? u : out;
    head_ = (head_ + 1) % buf_.size();
    return out;
}

void DeadTime::reset(double y0) {
    std::fill(buf_.begin(), buf_.end(), y0);
    head_ = 0;
}

std::vector<double> DeadTime::buffer() const {
    std::vector<double> out;
    out.reserve(buf_.size());
    for (std::size_t k = 0; k < buf_.size(); ++k) out.push_back(buf_[(head_ + k) % buf_.size()]);
    return out;
}

void DeadTime::set_buffer(const std::vector<double>& values) {
    if (values.empty()) return;
    const std::size_t n = buf_.size();
    std::vector<double> v = values;
    if (v.size() < n) v.insert(v.begin(), n - v.size(), v.front());
    if (v.size() > n) v.erase(v.begin(), v.begin() + static_cast<std::ptrdiff_t>(v.size() - n));
    buf_ = v;
    head_ = 0;
}

// ------------------------------------------------------------ RateLimiter
RateLimiter::RateLimiter(double up_, std::optional<double> down_, double y0)
    : up(std::fabs(up_)), down(down_ ? std::fabs(*down_) : std::fabs(up_)), y(y0) {}

double RateLimiter::step(double u, double dt) {
    if (!std::isfinite(u)) return y;
    const double delta = u - y;
    if (delta > 0) y += std::min(delta, up * dt);
    else y += std::max(delta, -down * dt);
    return y;
}

// ------------------------------------------------------------- Integrator
Integrator::Integrator(double y0, double lo_, double hi_) : lo(lo_), hi(hi_) {
    y = clamp(y0, lo, hi);
}

double Integrator::step(double rate, double dt) {
    if (!std::isfinite(rate)) return y;
    const double raw = y + rate * dt;
    y = clamp(raw, lo, hi);
    saturated = raw != y;
    return y;
}

void Integrator::reset(double y0) {
    y = clamp(y0, lo, hi);
    saturated = false;
}

// --------------------------------------------------------------- PyRandom
// The reference MT19937 as CPython carries it in Modules/_randommodule.c.
PyRandom::PyRandom(std::uint32_t seed) {
    // random.seed(int): abs(n) split into 32-bit words, little end first;
    // a single word for anything that fits, including zero
    init_by_array(std::vector<std::uint32_t>{seed});
}

PyRandom::PyRandom(const std::vector<std::uint32_t>& key) { init_by_array(key); }

void PyRandom::init_genrand(std::uint32_t s) {
    mt_[0] = s;
    for (int i = 1; i < N; ++i)
        mt_[i] = 1812433253u * (mt_[i - 1] ^ (mt_[i - 1] >> 30)) + static_cast<std::uint32_t>(i);
    index_ = N;
}

void PyRandom::init_by_array(const std::vector<std::uint32_t>& key_in) {
    std::vector<std::uint32_t> key = key_in.empty() ? std::vector<std::uint32_t>{0u} : key_in;
    init_genrand(19650218u);
    int i = 1;
    std::size_t j = 0;
    const std::size_t klen = key.size();
    for (std::size_t k = std::max<std::size_t>(N, klen); k > 0; --k) {
        mt_[i] = (mt_[i] ^ ((mt_[i - 1] ^ (mt_[i - 1] >> 30)) * 1664525u)) + key[j] + static_cast<std::uint32_t>(j);
        ++i; ++j;
        if (i >= N) { mt_[0] = mt_[N - 1]; i = 1; }
        if (j >= klen) j = 0;
    }
    for (int k = N - 1; k > 0; --k) {
        mt_[i] = (mt_[i] ^ ((mt_[i - 1] ^ (mt_[i - 1] >> 30)) * 1566083941u)) - static_cast<std::uint32_t>(i);
        ++i;
        if (i >= N) { mt_[0] = mt_[N - 1]; i = 1; }
    }
    mt_[0] = 0x80000000u;
    index_ = N;
    gauss_next_.reset();
}

std::uint32_t PyRandom::genrand_uint32() {
    static const std::uint32_t mag01[2] = {0x0u, 0x9908b0dfu};
    constexpr std::uint32_t UPPER = 0x80000000u, LOWER = 0x7fffffffu;
    constexpr int M = 397;
    if (index_ >= N) {
        int kk = 0;
        std::uint32_t y;
        for (; kk < N - M; ++kk) {
            y = (mt_[kk] & UPPER) | (mt_[kk + 1] & LOWER);
            mt_[kk] = mt_[kk + M] ^ (y >> 1) ^ mag01[y & 1u];
        }
        for (; kk < N - 1; ++kk) {
            y = (mt_[kk] & UPPER) | (mt_[kk + 1] & LOWER);
            mt_[kk] = mt_[kk + (M - N)] ^ (y >> 1) ^ mag01[y & 1u];
        }
        y = (mt_[N - 1] & UPPER) | (mt_[0] & LOWER);
        mt_[N - 1] = mt_[M - 1] ^ (y >> 1) ^ mag01[y & 1u];
        index_ = 0;
    }
    std::uint32_t y = mt_[index_++];
    y ^= (y >> 11);
    y ^= (y << 7) & 0x9d2c5680u;
    y ^= (y << 15) & 0xefc60000u;
    y ^= (y >> 18);
    return y;
}

double PyRandom::random() {
    const std::uint32_t a = genrand_uint32() >> 5, b = genrand_uint32() >> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
}

double PyRandom::gauss(double mu, double sigma) {
    constexpr double TWOPI = 6.283185307179586;
    double z;
    if (gauss_next_) {
        z = *gauss_next_;
        gauss_next_.reset();
    } else {
        const double x2pi = random() * TWOPI;
        const double g2rad = std::sqrt(-2.0 * std::log(1.0 - random()));
        z = std::cos(x2pi) * g2rad;
        gauss_next_ = std::sin(x2pi) * g2rad;
    }
    return mu + z * sigma;
}

std::vector<std::uint32_t> PyRandom::state_words() const {
    std::vector<std::uint32_t> out(mt_, mt_ + N);
    out.push_back(static_cast<std::uint32_t>(index_));
    return out;
}

void PyRandom::set_state(const std::vector<std::uint32_t>& words, std::optional<double> gauss_next) {
    if (words.size() != static_cast<std::size_t>(N) + 1) throw std::invalid_argument("state needs 625 words");
    std::copy(words.begin(), words.begin() + N, mt_);
    index_ = static_cast<int>(words[N]);
    if (index_ < 0 || index_ > N) throw std::invalid_argument("invalid state index");
    gauss_next_ = gauss_next;
}

// ------------------------------------------------------------------ Noise
Noise::Noise(double sigma_, double bandwidth, std::uint32_t seed)
    : sigma(sigma_), lag(1.0 / std::max(bandwidth, 1e-3)), rng_(seed) {}

double Noise::step(double dt) {
    if (sigma <= 0.0) return 0.0;
    const double alpha = 1.0 - std::exp(-std::max(dt, 1e-9) / lag.tau);
    const double scale = std::sqrt((2.0 - alpha) / std::max(alpha, 1e-12));
    const double raw = rng_.gauss(0.0, sigma) * scale;
    return lag.step(raw, dt);
}

// --------------------------------------------------------------- Debounce
Debounce::Debounce(double delay_, bool state_)
    : delay(std::max(delay_, 0.0)), state(state_), pending_(state_) {}

bool Debounce::step(bool u, double dt) {
    if (u == state) { pending_ = u; timer_ = 0.0; return state; }
    if (u != pending_) { pending_ = u; timer_ = 0.0; }
    timer_ += dt;
    if (timer_ >= delay) { state = u; timer_ = 0.0; }
    return state;
}

// ----------------------------------------------------------------- base64
static const char* B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

std::string encode_words(const std::vector<std::uint32_t>& words) {
    std::vector<unsigned char> bytes;
    bytes.reserve(words.size() * 4);
    for (std::uint32_t w : words)
        for (int k = 0; k < 4; ++k) bytes.push_back(static_cast<unsigned char>((w >> (8 * k)) & 0xFFu));
    std::string out;
    out.reserve((bytes.size() + 2) / 3 * 4);
    for (std::size_t i = 0; i < bytes.size(); i += 3) {
        std::uint32_t v = static_cast<std::uint32_t>(bytes[i]) << 16;
        if (i + 1 < bytes.size()) v |= static_cast<std::uint32_t>(bytes[i + 1]) << 8;
        if (i + 2 < bytes.size()) v |= bytes[i + 2];
        out.push_back(B64[(v >> 18) & 63]);
        out.push_back(B64[(v >> 12) & 63]);
        out.push_back(i + 1 < bytes.size() ? B64[(v >> 6) & 63] : '=');
        out.push_back(i + 2 < bytes.size() ? B64[v & 63] : '=');
    }
    return out;
}

std::vector<std::uint32_t> decode_words(std::string_view text) {
    auto val = [](char c) -> int {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    };
    std::vector<unsigned char> bytes;
    std::uint32_t acc = 0;
    int bits = 0;
    for (char c : text) {
        const int v = val(c);
        if (v < 0) continue;
        acc = (acc << 6) | static_cast<std::uint32_t>(v);
        bits += 6;
        if (bits >= 8) { bits -= 8; bytes.push_back(static_cast<unsigned char>((acc >> bits) & 0xFFu)); }
    }
    std::vector<std::uint32_t> words;
    for (std::size_t i = 0; i + 3 < bytes.size(); i += 4)
        words.push_back(static_cast<std::uint32_t>(bytes[i]) | (static_cast<std::uint32_t>(bytes[i + 1]) << 8)
                        | (static_cast<std::uint32_t>(bytes[i + 2]) << 16) | (static_cast<std::uint32_t>(bytes[i + 3]) << 24));
    return words;
}

// ------------------------------------------------------------ state
Value Lag::capture_state() const { Dict d; d["y"] = y; return d; }
void Lag::apply_state(const Value& s) { y = s.get_number("y", y); }

Value LeadLag::capture_state() const { Dict d; d["y"] = y; d["u"] = u_prev; return d; }
void LeadLag::apply_state(const Value& s) { y = s.get_number("y", y); u_prev = s.get_number("u", y); }

Value DeadTime::capture_state() const { Dict d; d["buf"] = Value(buffer()); return d; }
void DeadTime::apply_state(const Value& s) {
    std::vector<double> v;
    for (const Value& x : s.get("buf").list()) if (x.is_number() || x.is_bool()) v.push_back(x.number());
    set_buffer(v);
}

Value RateLimiter::capture_state() const { Dict d; d["y"] = y; return d; }
void RateLimiter::apply_state(const Value& s) { y = s.get_number("y", y); }

Value Integrator::capture_state() const { Dict d; d["y"] = y; d["sat"] = saturated; return d; }
void Integrator::apply_state(const Value& s) {
    y = clamp(s.get_number("y", y), lo, hi);
    saturated = s.get_bool("sat", false);
}

Value Noise::capture_state() const {
    Dict d;
    d["y"] = lag.y;
    d["rng"] = encode_words(rng_.state_words());
    d["rng_v"] = 3;
    if (rng_.gauss_next()) d["rng_g"] = *rng_.gauss_next();
    return d;
}
void Noise::apply_state(const Value& s) {
    lag.y = s.get_number("y", lag.y);
    if (!s.has("rng") || s.get("rng").is_null()) return;
    std::optional<double> g;
    if (s.has("rng_g") && !s.get("rng_g").is_null()) g = s.get("rng_g").number();
    try {
        rng_.set_state(decode_words(s.get("rng").string()), g);
    } catch (const std::invalid_argument&) {
        // a foreign generator state: the plant is exact, only the noise
        // realisation restarts, as the Python twin behaves
    }
}

Value Debounce::capture_state() const {
    Dict d; d["state"] = state; d["pending"] = pending_; d["timer"] = timer_; return d;
}
void Debounce::apply_state(const Value& s) {
    state = s.get_bool("state", state);
    pending_ = s.get_bool("pending", state);
    timer_ = s.get_number("timer", 0.0);
}

}  // namespace azeocore
