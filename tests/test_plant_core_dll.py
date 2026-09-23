"""The UI's Python bridge must execute the independently reusable core DLL."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "AzeoPlantSimulator"


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL deployment contract")
def test_python_bridge_loads_the_shared_core():
    # A fresh process cannot accidentally find a DLL loaded by another test.
    program = """
import ctypes
import sys
sys.path.insert(0, sys.argv[1])
import azeoplant
from azeoplant.core import native
assert native.enabled()
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel.GetModuleHandleW.restype = ctypes.c_void_p
assert kernel.GetModuleHandleW('azeocore.dll'), 'Python bridge still embeds a private static core'
from azeoplant.core.tags import TagDatabase
from azeoplant.models.flowsheet import Flowsheet
db = TagDatabase()
plant = Flowsheet(db)
assert len(plant.units) == 10
assert len(db.all()) == 612
for unit in plant.units:
    plant.step_unit(unit, .1)
plant.after_step(.1)
print('Shared DLL: ten units, 612 tags, process step complete')
"""
    env = dict(os.environ, AZEO_NATIVE="1")
    result = subprocess.run([sys.executable, "-c", program, str(COMPONENT)],
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL deployment contract")
def test_dll_exports_its_sdk_abi_version():
    path = COMPONENT / "azeoplant/azeocore.dll"
    assert path.is_file(), "Build the shared core with tools/build_plant_core.py"
    library = ctypes.CDLL(str(path))
    library.azeocore_abi_version.argtypes = []
    library.azeocore_abi_version.restype = ctypes.c_uint32
    assert library.azeocore_abi_version() == 1
    library.azeocore_version.argtypes = []
    library.azeocore_version.restype = ctypes.c_char_p
    assert library.azeocore_version() == b"1.0.0"
