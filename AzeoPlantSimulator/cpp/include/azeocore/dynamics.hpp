// Dynamic primitives: the C++ twin of azeoplant/core/dynamics.py.
//
// Every element is unconditionally stable for any positive time step, and
// every element reproduces its Python twin to floating-point tolerance
// under the same inputs: that is the parity contract, tested from Python
// in tests/test_native_parity.py. The noise generator reproduces CPython's
// random.Random bit for bit (Mersenne Twister, res53 doubles, Box-Muller
// with the cached second value, integer seeding by init_by_array), so a
// seeded run is the same run on both sides and a snapshot's generator
// state moves between them unchanged. Each element captures and applies
// its state as the same dictionary its Python twin writes.
#pragma once

#include "azeocore/export.h"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "azeocore/state.hpp"

namespace azeocore {

AZEOCORE_API double clamp(double x, double lo, double hi);   // non-finite -> lo
AZEOCORE_API double lerp(double a, double b, double f);
AZEOCORE_API double safe_sqrt(double x);
AZEOCORE_API double safe_div(double num, double den, double dflt = 0.0, double eps = 1e-9);

// Python's ``x ** y`` is a call to the C runtime's pow(), which for y = 2
// can differ by one unit in the last place from x * x. Every translated
// power goes through this out-of-line call so the compiler cannot rewrite
// it into a multiply and both sides round identically.
AZEOCORE_API double py_pow(double base, double exponent);
AZEOCORE_API double py_exp(double x);   // math.exp, the runtime's, never an intrinsic
AZEOCORE_API double py_sin(double x);   // math.sin, likewise
AZEOCORE_API double py_cos(double x);   // math.cos
AZEOCORE_API double py_log(double x);   // math.log

// zlib-compatible CRC32 of a byte string: how transmitters seed their noise.
AZEOCORE_API std::uint32_t crc32(std::string_view data);

struct AZEOCORE_API Lag : Stateful {
    double tau;
    double y;
    explicit Lag(double tau, double y0 = 0.0);
    double step(double u, double dt);
    void reset(double y0) { y = y0; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

struct AZEOCORE_API LeadLag : Stateful {
    double lead;
    double lag;
    double y;
    double u_prev;
    LeadLag(double lead, double lag, double y0 = 0.0);
    double step(double u, double dt);
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

class AZEOCORE_API DeadTime : public Stateful {
public:
    DeadTime(double delay, double dt, double y0 = 0.0);
    double step(double u);
    void reset(double y0);
    std::size_t size() const { return buf_.size(); }
    double delay() const { return delay_; }
    double dt() const { return dt_; }
    // oldest first, the order the Python deque iterates in
    std::vector<double> buffer() const;
    // refill tolerating a buffer sized for another dt: a shorter one is
    // padded at the old end, a longer one truncated there
    void set_buffer(const std::vector<double>& values);
    Value capture_state() const override;
    void apply_state(const Value& s) override;

private:
    double delay_;
    double dt_;
    std::vector<double> buf_;
    std::size_t head_ = 0;
};

struct AZEOCORE_API RateLimiter : Stateful {
    double up;
    double down;
    double y;
    RateLimiter(double up, std::optional<double> down = std::nullopt, double y0 = 0.0);
    double step(double u, double dt);
    void reset(double y0) { y = y0; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

struct AZEOCORE_API Integrator : Stateful {
    double y;
    double lo;
    double hi;
    bool saturated = false;
    explicit Integrator(double y0 = 0.0, double lo = -1e9, double hi = 1e9);
    double step(double rate, double dt);
    void reset(double y0);
    Value capture_state() const override;
    void apply_state(const Value& s) override;
};

// CPython's random.Random, the parts the plant uses.
class AZEOCORE_API PyRandom {
public:
    static constexpr int N = 624;
    explicit PyRandom(std::uint32_t seed);
    explicit PyRandom(const std::vector<std::uint32_t>& key);
    std::uint32_t genrand_uint32();
    double random();                       // genrand_res53
    double gauss(double mu, double sigma); // Box-Muller with the cached half
    // 624 state words followed by the index, as Random.getstate() lays them out
    std::vector<std::uint32_t> state_words() const;
    std::optional<double> gauss_next() const { return gauss_next_; }
    void set_state(const std::vector<std::uint32_t>& words, std::optional<double> gauss_next);

private:
    void init_genrand(std::uint32_t s);
    void init_by_array(const std::vector<std::uint32_t>& key);
    std::uint32_t mt_[N];
    int index_ = N + 1;
    std::optional<double> gauss_next_;
};

class AZEOCORE_API Noise : public Stateful {
public:
    Noise(double sigma = 0.0, double bandwidth = 2.0, std::uint32_t seed = 0);
    double step(double dt);
    double sigma;
    Lag lag;
    PyRandom& rng() { return rng_; }
    const PyRandom& rng() const { return rng_; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;

private:
    PyRandom rng_;
};

// xorshift64* with a splitmix64 seed: the realism generator, the twin of
// dynamics.Xorshift. Integer operations only, so both cores produce the
// same sequence by construction; uniforms are the top 53 bits times 2^-53
// and the Gaussian is the Irwin-Hall sum of twelve uniforms less six.
class AZEOCORE_API Xorshift : public Stateful {
public:
    explicit Xorshift(std::uint64_t seed = 0);
    static std::uint64_t seed_state(std::uint64_t seed);
    std::uint64_t next_u64();
    double random();
    double gauss();
    std::uint64_t state() const { return state_; }
    void set_state(std::uint64_t s) { state_ = s ? s : 0x9E3779B97F4A7C15ULL; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;

private:
    std::uint64_t state_;
};

// The slow half of instrument noise: an Ornstein-Uhlenbeck walk in its
// exact discrete form, the twin of dynamics.RandomWalk.
class AZEOCORE_API RandomWalk : public Stateful {
public:
    RandomWalk(double sigma = 0.0, double tau = 600.0, std::uint64_t seed = 0);
    double step(double dt);
    double sigma;
    double tau;
    double y = 0.0;
    Xorshift& rng() { return rng_; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;

private:
    Xorshift rng_;
};

struct AZEOCORE_API Debounce : Stateful {
    double delay;
    bool state;
    Debounce(double delay, bool state = false);
    bool step(bool u, double dt);
    bool pending() const { return pending_; }
    double timer() const { return timer_; }
    void set_internal(bool pending, double timer) { pending_ = pending; timer_ = timer; }
    Value capture_state() const override;
    void apply_state(const Value& s) override;

private:
    bool pending_;
    double timer_ = 0.0;
};

// base64 of little-endian uint32 words, the snapshot's generator encoding
AZEOCORE_API std::string encode_words(const std::vector<std::uint32_t>& words);
AZEOCORE_API std::vector<std::uint32_t> decode_words(std::string_view text);

}  // namespace azeocore
