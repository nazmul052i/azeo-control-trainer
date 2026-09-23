"""Configuration-aware binding — the configurator meets the engine.

The binding rules are executable:

- **Property-composed templates** (gap 1): a Bind template may
  reference any configuration property — ``{Tag}/{Link.FB}/OUT_SCALE``
  — and it resolves through :meth:`PvmConfiguration.binding_params`
  without string concatenation living in the class.
- **Presence-gated subscription** (gap 2): a spec whose template
  touches a property that Presence or Present Online has turned off is
  never bound at all — ``engine.monitored_count`` stays honest, which
  is the whole point of Present Online. A Selection change is an
  engineering act, so it REBINDS (everything down, plan again, bind
  again) rather than re-evaluating per poll: the runtime hot path
  never composes a string.
"""
from __future__ import annotations

from .model import PvmConfiguration


def bind_pvm(engine, specs, config: PvmConfiguration,
             choices: dict | None = None,
             base: dict | None = None) -> tuple:
    """Bind a PVM's declared specs under a configuration.

    Returns ``(bound, gated)``: key → live Binding for the specs the
    choices keep alive, and the keys Presence/Present Online turned
    off. Expression specs are left to the render layer, as ever.
    """
    active, gated = config.plan_bindings(specs, choices, base)
    bound = {}
    for spec, params in active:
        if getattr(spec, "expr", "") or not getattr(spec, "path", ""):
            continue
        bound[spec.key] = engine.bind(spec.path, params)
    return bound, [spec.key for spec in gated]


def rebind_pvm(engine, bound: dict, specs, config: PvmConfiguration,
               choices: dict | None = None,
               base: dict | None = None) -> tuple:
    """A choice flipped: release everything, plan and bind afresh."""
    for binding in bound.values():
        engine.unbind(binding)
    return bind_pvm(engine, specs, config, choices, base)
