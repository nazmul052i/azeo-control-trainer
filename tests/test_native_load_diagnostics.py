"""A failed native import must explain what the destination machine lacks."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "AzeoPlantSimulator"


@pytest.mark.parametrize("failure", [
    "No module named 'azeoplant._azeocore'",
    "DLL load failed: The specified module could not be found",
    "DLL load failed: %1 is not a valid Win32 application",
])
def test_provider_keeps_the_loader_error_and_identifies_the_runtime(failure):
    program = """
import os, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from azeoplant.core import native
from azeoplant.embedding import create_embedded_plant
os.environ['AZEO_NATIVE'] = '1'
with patch('importlib.import_module', side_effect=ImportError(sys.argv[2])):
    try:
        create_embedded_plant({'source': 'loader-test', 'dt': .1})
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError('A missing native core must block startup')
    assert sys.argv[2] in message, message
    assert sys.version.split()[0] in message, message
    assert sys.executable in message, message
    assert '_azeocore' in message and 'azeocore.dll' in message, message
    assert 'Restart' in message, message
    details = native.diagnostics()
    assert not details['available']
    assert details['load_error'].endswith(sys.argv[2])
    assert details['expected_bridge'] in message
"""
    result = subprocess.run([sys.executable, "-c", program, str(COMPONENT), failure],
                            env=dict(os.environ, AZEO_NATIVE="0"),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
