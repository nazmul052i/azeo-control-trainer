#!/usr/bin/env python3
"""Parity of the native primitives against their Python twins.

Every primitive in ``azeoplant.core.dynamics`` has a twin in the
``_azeocore`` extension. Under the same construction and the same input
sequence the two must agree: exactly for the generator and the noise
(same Mersenne Twister, same seeding, same Box-Muller), to round-off for
the algebra. State captured on one side must apply on the other and
continue identically, because that is what lets a snapshot move between
cores.

    python tests/test_native_parity.py

Skips, and says so, when the extension is not built. The Python core is
untouched by this test: it is the reference.
"""

from __future__ import annotations

import math
import random
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault("AZEO_NATIVE", "0")   # the Python side of this comparison is the reference
from azeoplant.core import dynamics as py  # noqa: E402

try:
    from azeoplant import _azeocore as cc  # noqa: E402
except ImportError:  # pragma: no cover - the build is optional
    print("native core not built: skipping parity (cmake -S cpp -B cpp/build)")
    sys.exit(0)

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures += 1


def close(a: float, b: float, rel: float = 1e-12) -> bool:
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def series(seed: int, n: int, lo: float = -50.0, hi: float = 150.0) -> list[float]:
    """A deterministic, spiky input: steps, ramps and the odd NaN."""
    r = random.Random(seed)
    out, level = [], 0.0
    for k in range(n):
        if r.random() < 0.05:
            level = r.uniform(lo, hi)
        elif r.random() < 0.3:
            level += r.uniform(-5.0, 5.0)
        out.append(float("nan") if r.random() < 0.01 else level)
    return out


def worst(a: list[float], b: list[float]) -> float:
    return max(abs(x - y) / max(1.0, abs(x), abs(y)) for x, y in zip(a, b))


# ------------------------------------------------------------- functions
check("clamp agrees incl. non-finite", all(
    py.clamp(x, 0.0, 10.0) == cc.clamp(x, 0.0, 10.0)
    for x in (-1.0, 5.0, 11.0, float("nan"), float("inf"), -float("inf"))))
check("safe_div agrees", py.safe_div(1.0, 1e-12, 7.0) == cc.safe_div(1.0, 1e-12, 7.0)
      and close(py.safe_div(3.0, 4.0), cc.safe_div(3.0, 4.0)))
check("crc32 matches zlib", all(cc.crc32(t) == zlib.crc32(t.encode()) for t in
                                 ("FT-1001", "AT-5001", "", "LT-7001", "TT-6002")))

# ---------------------------------------------------- realism generator
# xorshift64* + Irwin-Hall: integer arithmetic, so the two sides must agree
# exactly on every draw, and the random walk built on it likewise
for seed in (0, 1, 42, zlib.crc32(b"FT-1001"), 2**32 - 1):
    a, b = py.Xorshift(seed), cc.Xorshift(seed)
    check(f"Xorshift({seed}) uniforms and gaussians exact",
          [a.random() for _ in range(2000)] == [b.random() for _ in range(2000)]
          and [a.gauss() for _ in range(2000)] == [b.gauss() for _ in range(2000)]
          and a.state == b.state)
a, b = py.Xorshift(5), cc.Xorshift(5)
a.random()
check("Xorshift state dict identical", a.capture_state() == b.capture_state() or (b.random() is not None and a.capture_state() == b.capture_state()))
c = cc.Xorshift(1)
c.apply_state(a.capture_state())
check("Xorshift state Python -> native continues", [a.random() for _ in range(50)] == [c.random() for _ in range(50)])
for sigma, tau, dt in ((1.5, 60.0, 0.1), (0.3, 900.0, 0.2), (2.0, 5.0, 1.0)):
    a, b = py.RandomWalk(sigma, tau, seed=11), cc.RandomWalk(sigma, tau, seed=11)
    ya = [a.step(dt) for _ in range(5000)]
    yb = [b.step(dt) for _ in range(5000)]
    check(f"RandomWalk sigma {sigma} tau {tau} dt {dt} exact", ya == yb,
          f"first diff at {next((k for k, (p, q) in enumerate(zip(ya, yb)) if p != q), None)}")
check("RandomWalk state dict identical", a.capture_state() == b.capture_state())
c = cc.RandomWalk(2.0, 5.0, seed=0)
c.apply_state(a.capture_state())
check("RandomWalk state Python -> native continues", [a.step(0.3) for _ in range(300)] == [c.step(0.3) for _ in range(300)])

# ------------------------------------------------------------ primitives
u = series(1, 5000)
dts = [0.1, 0.05, 0.25, 1.0, 100.0]

for dt in dts:
    a, b = py.Lag(12.5, 3.0), cc.Lag(12.5, 3.0)
    ya, yb = [a.step(x, dt) for x in u], [b.step(x, dt) for x in u]
    check(f"Lag parity dt={dt:g}", worst(ya, yb) < 1e-12, f"worst {worst(ya, yb):.2e}")

a, b = py.LeadLag(4.0, 20.0, 1.0), cc.LeadLag(4.0, 20.0, 1.0)
uu = [x if math.isfinite(x) else 0.0 for x in u]        # LeadLag has no NaN guard
ya, yb = [a.step(x, 0.1) for x in uu], [b.step(x, 0.1) for x in uu]
check("LeadLag parity", worst(ya, yb) < 1e-12, f"worst {worst(ya, yb):.2e}")

for delay, dt in ((165.0, 0.1), (30.0, 0.25), (0.0, 0.1), (0.05, 0.1), (0.15, 0.1)):
    a, b = py.DeadTime(delay, dt, 2.0), cc.DeadTime(delay, dt, 2.0)
    check(f"DeadTime length delay={delay:g} dt={dt:g}", len(a.buf) == len(b),
          f"{len(a.buf)} vs {len(b)}")
    ya, yb = [a.step(x) for x in u], [b.step(x) for x in u]
    check(f"DeadTime parity delay={delay:g} dt={dt:g}",
          all((x == y) or (math.isnan(x) and math.isnan(y)) for x, y in zip(ya, yb)))

a, b = py.RateLimiter(2.0, 0.5, 10.0), cc.RateLimiter(2.0, 0.5, 10.0)
ya, yb = [a.step(x, 0.1) for x in u], [b.step(x, 0.1) for x in u]
check("RateLimiter parity", worst(ya, yb) < 1e-12)
a, b = py.RateLimiter(3.0), cc.RateLimiter(3.0)
check("RateLimiter default down", a.down == b.down == 3.0)

a, b = py.Integrator(50.0, 0.0, 100.0), cc.Integrator(50.0, 0.0, 100.0)
ya = [a.step(x / 10.0, 0.1) for x in u]
yb = [b.step(x / 10.0, 0.1) for x in u]
check("Integrator parity", worst(ya, yb) < 1e-12 and a.saturated == b.saturated)

# ------------------------------------------------------------ generator
for seed in (0, 1, 42, 2**31 - 1, 2**32 - 1, zlib.crc32(b"AT-5001")):
    pr, cr = random.Random(seed), cc.PyRandom(seed)
    same = all(pr.random() == cr.random() for _ in range(2000))
    check(f"random() bit-exact seed={seed}", same)
    pr, cr = random.Random(seed), cc.PyRandom(seed)
    same = all(pr.gauss(0.0, 1.5) == cr.gauss(0.0, 1.5) for _ in range(2001))
    check(f"gauss() bit-exact seed={seed}", same)
pr, cr = random.Random(7), cc.PyRandom(7)
for _ in range(3):
    pr.random(); cr.random()
check("getstate words agree", list(pr.getstate()[1]) == list(cr.getstate()[1]))

# ---------------------------------------------------------------- noise
for tag in ("FT-1001", "AT-5001", "LT-6001"):
    seed = zlib.crc32(tag.encode())
    a, b = py.Noise(1.2, 2.0, seed=seed), cc.Noise(1.2, 2.0, seed)
    ya, yb = [a.step(0.1) for _ in range(5000)], [b.step(0.1) for _ in range(5000)]
    check(f"Noise bit-exact {tag}", ya == yb)

# state round trips, both directions
a, b = py.Noise(0.8, 2.0, seed=99), cc.Noise(0.8, 2.0, 99)
for _ in range(777):
    a.step(0.1); b.step(0.1)
sa, sb = a.capture_state(), b.capture_state()
check("Noise capture_state identical", sa == sb, str({k: (sa.get(k) == sb.get(k)) for k in sa}))
c = cc.Noise(0.8, 2.0, 1)
c.apply_state(sa)                      # Python state into the native twin
check("Python noise state continues natively", [a.step(0.1) for _ in range(500)] == [c.step(0.1) for _ in range(500)])
d = py.Noise(0.8, 2.0, seed=1)
d.apply_state(b.capture_state())       # native state into the Python twin
check("native noise state continues in Python", [b.step(0.1) for _ in range(500)] == [d.step(0.1) for _ in range(500)])

# ------------------------------------------------ state dictionaries
for mk_py, mk_cc in ((lambda: py.Lag(5.0, 1.0), lambda: cc.Lag(5.0, 1.0)),
                     (lambda: py.Integrator(1.0, 0.0, 10.0), lambda: cc.Integrator(1.0, 0.0, 10.0)),
                     (lambda: py.RateLimiter(1.0), lambda: cc.RateLimiter(1.0)),
                     (lambda: py.LeadLag(1.0, 5.0), lambda: cc.LeadLag(1.0, 5.0)),
                     (lambda: py.Debounce(2.0), lambda: cc.Debounce(2.0))):
    a, b = mk_py(), mk_cc()
    check(f"{type(a).__name__} capture_state keys", set(a.capture_state()) == set(b.capture_state()),
          f"{sorted(a.capture_state())} vs {sorted(b.capture_state())}")

a, b = py.DeadTime(1.0, 0.1, 0.0), cc.DeadTime(1.0, 0.1, 0.0)
for k in range(7):
    a.step(k); b.step(k)
c = cc.DeadTime(1.0, 0.1, 0.0)
c.apply_state(a.capture_state())
check("DeadTime state moves Python -> native", [b.step(9) for _ in range(10)] == [c.step(9) for _ in range(10)])
short = py.DeadTime(0.5, 0.1, 0.0)
short.apply_state({"buf": [1, 2, 3, 4, 5, 6, 7, 8]})
cshort = cc.DeadTime(0.5, 0.1, 0.0)
cshort.apply_state({"buf": [1, 2, 3, 4, 5, 6, 7, 8]})
check("DeadTime truncates a longer saved buffer alike", list(short.buf) == list(cshort.buf))
long_ = py.DeadTime(1.0, 0.1, 0.0)
long_.apply_state({"buf": [4, 5]})
clong = cc.DeadTime(1.0, 0.1, 0.0)
clong.apply_state({"buf": [4, 5]})
check("DeadTime pads a shorter saved buffer alike", list(long_.buf) == list(clong.buf))

a, b = py.Debounce(0.5, False), cc.Debounce(0.5, False)
pattern = [bool(int(x) % 3 == 0) for x in u if math.isfinite(x)]
check("Debounce parity", [a.step(p, 0.1) for p in pattern] == [b.step(p, 0.1) for p in pattern])

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
