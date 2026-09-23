"""AzeoPlant simulator.

The C++ core is used by default when built with ``tools/build_plant_core.py``.
``AZEO_NATIVE=0`` selects the Python reference. With the extension built,
the native core is swapped in here, before anything else in the package
is imported, so every module that names a core class gets the native one.
See azeoplant/core/native.py.
"""

from .core import native as _native

if _native.enabled():
    _native.activate()
