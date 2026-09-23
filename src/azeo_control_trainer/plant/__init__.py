"""Simulated processes that sit *behind* the tag store.

`DECISIONS.md` D2 keeps the plant out of the control engineering half of this
repo, and that still holds: nothing in ``core/strategy/``,
``azeo_control_designer/`` or ``core/pid/`` imports this package, and Control
Studio runs with it absent.
What lives here is the other side of the ``SharedDataStore`` boundary — the
thing a module's ``AI`` reads from and its ``AO`` writes to, which in a real
installation would be an OPC UA server or physical I/O.

Keeping it a package with a declared tag contract is what makes that boundary
testable instead of decorative: a plant states what it reads and writes, and
:meth:`Plant.verify_against` checks that against the tag database walked out of
the actual modules.

    from azeo_control_trainer.plant import get_plant
    plant = get_plant("training_process")

``driver.py`` imports Qt; nothing else here does, so a headless test can step a
process without a GUI stack.
"""
from __future__ import annotations

from .process import Plant, PlantRegistry, registry

# Importing the shipped processes registers them.
from . import training_process  # noqa: F401,E402

#: The plant that matches the shipped strategy area.
DEFAULT_PLANT = "training_process"


def get_plant(key: str = DEFAULT_PLANT, **kwargs) -> Plant:
    """Build a registered plant by key."""
    cls = registry.get(key)
    if cls is None:
        raise KeyError(
            f"Unknown plant '{key}'. Available: {', '.join(registry.keys())}")
    return cls(**kwargs)


def available() -> list[str]:
    return registry.keys()


__all__ = [
    "DEFAULT_PLANT", "Plant", "PlantRegistry",
    "available", "get_plant", "registry",
]
