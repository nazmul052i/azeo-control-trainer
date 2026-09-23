"""The strategy's scan on the native core.

``azeoplant.control.strategy.ControlSystem`` is untouched: it builds the
loops, owns the tables the operator's menus change and captures the
state exactly as before. When the native core is active this module
installs a replacement ``step`` on the class that compiles those tables
into a native ``Scanner`` (cpp/src/py_scanner.cpp) and lets it do the
per-scan work: the PV sources, the shaping hooks, the cascade setpoints
through the selectors and cross-limits, the external-reset images, the
block executions, the output conditioning, the split ranges, the
overrides and the checked writes.

The scanner is compiled from the tables and recompiled whenever a
method that changes one has run; the methods are wrapped here, the
strategy does not know. The few loops that carry a Python callable (a
computed PV, a gain scheduler, a deadtime predictor) keep calling it.

Installed by ``azeoplant.core.native.activate()``.
"""

from __future__ import annotations

import functools
import logging

log = logging.getLogger(__name__)

#: methods after which the tables may have changed
_INVALIDATING = (
    "seed_from_plant", "set_algorithm", "set_compensator", "set_output_conditioning",
    "set_compressor_mv", "set_boiler_mode", "set_level_compensation", "define_inferential",
    "use_inferential", "set_column_scheme", "set_asc_formulation", "apply_state",
    "open_loop", "close_loop", "all_auto", "all_manual", "set_scan_class",
)


def _pull_scan_accums(cs) -> None:
    """The scan-class accumulators live in the native scanner while it
    runs; the Python loops own them between scanners. Before any method
    that may recompile, and before a capture, the scanner's running values
    come back to the loops, so a recompile continues every slow module
    where it was and a snapshot carries the truth."""
    sc = cs.__dict__.get("_native_scanner")
    if sc is None:
        return
    loops = cs.loops
    for m, a in sc.scan_accums().items():
        loop = loops.get(m)
        if loop is not None:
            loop._scan_accum = a


def _pv_fn_signature(cs) -> tuple:
    """The identities of the computed-PV callables, so a table edited from
    outside the strategy (the tests do) still recompiles."""
    return tuple(sorted((m, id(f)) for m, f in cs._pv_fn.items()))


def compile_scanner(cs, cc):
    """Build a native Scanner from the strategy's tables."""
    sc = cc.Scanner(cs.db, cs.bus)
    sc.ryskamp_rmax = float(cs.RYSKAMP_RMAX)
    sc.ryskamp_col = {int(k): bool(v) for k, v in cs._ryskamp_col.items()}
    sc.col_scheme = {int(k): str(v) for k, v in cs._col_scheme.items()}
    sc.cw_base = {int(k): float(v) for k, v in cs._cw_base.items()}
    sc.lhv_anchor = float(cs._lhv_anchor)
    sc.col_ff = {int(k): [float(x) for x in v] for k, v in cs._col_ff.items()}
    sc.draw_ff = float(cs.DRAW_FF)
    sc.ff_anchors = {int(k): {str(a): float(b) for a, b in v.items()} for k, v in cs._ff_anchors.items()}
    sc.base_period = float(cs._base_period)
    for loop in cs._order:
        m = loop.module
        s = cc.LoopSpec()
        s.module, s.pv_tag, s.out_tag = m, loop.pv_tag, loop.out_tag or ""
        s.master = loop.master or ""
        s.ff_tag = loop.ff_tag or ""
        s.enabled = bool(loop.enabled)
        s.out_cond = float(loop.out_cond)
        s.scan_period = float(loop.scan_period)
        s.scan_accum = float(loop._scan_accum)
        s.split = [(str(t), float(lo), float(hi), float(v0), float(v1)) for t, lo, hi, v0, v1 in loop.split]
        if m in cs._ratio:
            s.ratio = float(cs._ratio[m])
        if m in cs._pv_select:
            s.pv_select = tuple(cs._pv_select[m])
        if m in cs._pv_diff:
            s.pv_diff = tuple(cs._pv_diff[m])
        if m in cs._pv_fn:
            s.pv_fn = cs._pv_fn[m]
        if loop.gain_fn is not None:
            s.gain_fn = loop.gain_fn
        if loop.predictor is not None:
            s.predictor = loop.predictor
            comp = cs._shaping.get(m, {}).get("comp", {})
            if comp.get("adapt_tag"):
                s.adapt_tag = str(comp["adapt_tag"])
                s.adapt_theta = float(comp["theta"])
                s.adapt_ref = float(comp["adapt_ref"])
        if m in cs._pct_comp:
            ptag, pnom, base = cs._pct_comp[m]
            s.pct_comp = (str(ptag), float(pnom), float(base))
        if m in cs._pct_cas:
            ptag, anchor = cs._pct_cas[m]
            s.pct_cas = (str(ptag), float(anchor))
        src = None if (loop.out_tag or loop.split) else cs._bk_source.get(m)
        s.bk_source = src.module if src is not None else ""
        s.pid = loop.pid
        sc.add(s)
    return sc


def _native_step(self, dt: float) -> None:
    db = self.db
    self._accum += dt
    if self._accum < self._base_period - 1e-9:
        return
    dt, self._accum = self._accum, 0.0
    # The SIS is independent of the regulatory layer, exactly as on a
    # real plant: open loop makes the CONTROL passive, never the trips.
    self.esd.step(dt)
    sig = _pv_fn_signature(self)
    sc = self.__dict__.get("_native_scanner")
    if sc is None or self.__dict__.get("_native_dirty", True) or self.__dict__.get("_native_sig") != sig:
        from ..core import native
        prev = self.__dict__.get("_native_scanner")
        sc = compile_scanner(self, native.module())
        if prev is not None:
            # the selector offsets are running state, like the blocks:
            # a recompile must not hand the masters a zero for a scan
            sc.set_cas_offsets(prev.cas_offsets())
        self._native_scanner, self._native_dirty, self._native_sig = sc, False, sig
    sc.scan(dt, bool(self.enabled))


def _invalidating(method):
    @functools.wraps(method)
    def wrapper(self, *a, **kw):
        _pull_scan_accums(self)
        try:
            return method(self, *a, **kw)
        finally:
            self._native_dirty = True
    return wrapper


def _capturing(method):
    @functools.wraps(method)
    def wrapper(self, *a, **kw):
        _pull_scan_accums(self)
        return method(self, *a, **kw)
    return wrapper


def install(control_system_cls) -> None:
    """Route the class's scan through the native scanner. Idempotent."""
    if getattr(control_system_cls, "_native_scan_installed", False):
        return
    control_system_cls._python_step = control_system_cls.step
    control_system_cls.step = _native_step
    for name in _INVALIDATING:
        method = getattr(control_system_cls, name, None)
        if method is not None:
            setattr(control_system_cls, name, _invalidating(method))
    for name in ("capture_state", "scan_classes"):
        method = getattr(control_system_cls, name, None)
        if method is not None:
            setattr(control_system_cls, name, _capturing(method))
    control_system_cls._native_scan_installed = True
    log.info("Strategy scan routed through the native scanner")
