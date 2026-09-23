// Native checks that need no Python: the generator against CPython's
// known first outputs, and the primitives' defining properties. Parity
// against the Python twins is tested from Python.
#include <cmath>
#include <cstdio>
#include <string>

#include "azeocore/dynamics.hpp"
#include "azeocore/iobus.hpp"
#include "azeocore/tags.hpp"

using namespace azeocore;

static int failures = 0;
static void check(bool ok, const char* what) {
    std::printf("  %s  %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) ++failures;
}
static bool near(double a, double b, double tol = 1e-12) { return std::fabs(a - b) <= tol * std::fmax(1.0, std::fabs(b)); }

int main() {
    // random.seed(42); random.random() -> 0.6394267984578837, 0.025010755222666936
    PyRandom r(42);
    check(near(r.random(), 0.6394267984578837), "CPython random() after seed 42, first");
    check(near(r.random(), 0.025010755222666936), "CPython random() after seed 42, second");

    // the realism generator against dynamics.Xorshift: Xorshift(42).random()
    // -> 0.1941059175341826, 0.5626318272656207, then gauss() -> 0.538412372260769
    Xorshift xs(42);
    check(xs.random() == 0.1941059175341826, "Xorshift(42) first uniform, exactly Python's");
    check(xs.random() == 0.5626318272656207, "Xorshift(42) second uniform");
    check(xs.gauss() == 0.538412372260769, "Xorshift(42) Irwin-Hall gaussian");
    check(Xorshift::seed_state(0) == 0xe220a8397b1dcdafULL, "splitmix64 seed of zero");
    RandomWalk walk(1.0, 10.0, 7);
    check(walk.step(0.1) == -0.12788114720192814, "RandomWalk first step, exactly Python's");
    check(walk.step(0.1) == -0.11651040345044923, "RandomWalk second step");

    // crc32 as zlib computes it
    check(crc32("") == 0u, "crc32 of the empty string is zero");
    check(crc32("123456789") == 0xCBF43926u, "crc32 check value 0xCBF43926");

    // Lag: exact ZOH, converges, never overshoots however large dt is
    Lag lag(10.0, 0.0);
    check(near(lag.step(1.0, 1e9), 1.0, 1e-9), "lag reaches its input in one enormous step");
    Lag lag2(10.0, 0.0);
    lag2.step(1.0, 10.0);
    check(near(lag2.y, 1.0 - std::exp(-1.0)), "lag one time constant");

    // DeadTime: n = round(delay/dt) samples of delay
    DeadTime d(1.0, 0.1, 0.0);
    double out = 0.0;
    for (int k = 0; k < 10; ++k) out = d.step(5.0);
    check(out == 0.0, "dead time still old after n-1 steps");
    check(d.step(5.0) == 5.0, "dead time delivers after n steps");
    auto b = d.buffer();
    check(b.size() == 10 && b.front() == 5.0, "dead time buffer oldest-first");

    // Integrator clamps and reports saturation
    Integrator i(50.0, 0.0, 100.0);
    i.step(1000.0, 1.0);
    check(i.y == 100.0 && i.saturated, "integrator saturates at its bound");

    // Noise: seeded identically, two generators produce the same series
    Noise a(1.0, 2.0, crc32("AT-5001")), c(1.0, 2.0, crc32("AT-5001"));
    bool same = true;
    for (int k = 0; k < 1000; ++k) same = same && (a.step(0.1) == c.step(0.1));
    check(same, "seeded noise is reproducible");

    // Generator state round trip through the snapshot encoding
    auto words = a.rng().state_words();
    auto back = decode_words(encode_words(words));
    check(back == words, "generator state survives base64");

    // Debounce holds for the delay
    Debounce db(2.0, false);
    db.step(true, 1.0);
    check(!db.state, "debounce not yet");
    db.step(true, 1.0);
    check(db.state, "debounce after the delay");

    // ---- the virtual I/O bus, no Python in the loop
    {
        TagDatabase db;
        db.analog("FCV-1", TagKind::AO, "U100", "a valve", "%", 0.0, 100.0, 10.0);
        db.analog("FT-1", TagKind::AI, "U100", "a flow", "m3/h", 0.0, 200.0, 50.0);
        db.discrete("XY-1", TagKind::DO, "U100", "a command", "Idle", "Start", false);
        IOBus bus(db, 2.0);
        int changed = 0;
        bus.on_change = [&](const std::string&) { ++changed; };
        PendingValue v; v.numeric = true; v.number = 55.0; v.truthy = true;
        check(bus.write_from_dcs("FCV-1", v, "internal") == WriteVerdict::Applied && db.at("FCV-1").value == 55.0,
              "bus applies a DCS write to an AO");
        check(bus.write_from_dcs("FT-1", v, "internal") == WriteVerdict::RejectedOwnership,
              "bus refuses a DCS write to an AI");
        check(bus.register_dcs("DCS-A") && !bus.register_dcs("DCS-B"), "one holder of the output side");
        check(bus.write_from_dcs("FCV-1", v, "DCS-B") == WriteVerdict::RejectedHolder, "a stranger's write is refused");
        check(bus.write_from_dcs("FCV-1", v, "sis") == WriteVerdict::Applied, "the SIS writes past the holder");
        v.number = 250.0;
        check(bus.write_from_dcs("FCV-1", v, "DCS-A") == WriteVerdict::Applied && db.at("FCV-1").value == 100.0
                  && bus.stats.adjusted_writes == 1, "an out-of-range write is clamped and counted");
        v.number = 20.0; bus.queue_write("FCV-1", v, "DCS-A");
        v.number = 30.0; bus.queue_write("FCV-1", v, "DCS-A");
        check(bus.stats.coalesced_writes == 1 && bus.queue_health().pending == 1, "queued writes coalesce per route");
        check(bus.drain_writes() == 1 && db.at("FCV-1").value == 30.0, "drain applies the newest queued write");
        PendingValue on; on.numeric = true; on.number = 1.0; on.truthy = true;
        check(bus.write_from_dcs("XY-1", on, "DCS-A") == WriteVerdict::Applied && db.at("XY-1").value == 1.0,
              "a discrete takes its truth");
        check(changed == 5, "on_change fired once per applied write");
        bus.force("FT-1", 12.5, "operator", "test");
        bus.tick();
        check(db.at("FT-1").value == 12.5 && bus.active_forces().size() == 1, "a force on an AI lands on the tick");
        Value st = bus.capture_state();
        bus.release_all();
        check(bus.active_forces().empty(), "release_all clears the forces");
        bus.apply_state(st);
        check(bus.active_forces().size() == 1 && bus.active_forces()[0].reason == "test", "state round-trips the forces");
        bool threw = false;
        try { bus.force("FT-1", 1.0, "operator", ""); } catch (const std::invalid_argument&) { threw = true; }
        check(threw, "a force needs a reason");
    }

    std::printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
