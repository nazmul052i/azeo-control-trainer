"""Application identities stay distinct from the retired PID-only mark."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.presentation.app_icon import (
    APPLICATION_ICONS,
    apply_branding,
    get_app_icon,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _digest(icon, size):
    data = QBuffer()
    assert data.open(QIODevice.WriteOnly)
    assert icon.pixmap(size, size).save(data, "PNG")
    return hashlib.sha256(bytes(data.data())).hexdigest()


def test_every_application_has_a_distinct_multiresolution_mark(app):
    identities = tuple(APPLICATION_ICONS)
    icons = {identity: get_app_icon(identity) for identity in identities}
    assert all(not icon.isNull() for icon in icons.values())
    assert len({_digest(icon, 16) for icon in icons.values()}) == len(icons)
    assert len({_digest(icon, 256) for icon in icons.values()}) == len(icons)


def test_runtime_branding_sets_the_matching_icon_and_taskbar_identity(app, monkeypatch):
    calls = []
    shell = SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=lambda value: calls.append(value))
    import ctypes
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=shell), raising=False)
    assert apply_branding(app, "graphics_designer")
    assert calls == ["Azeo.ControlTrainer.graphics_designer"]
    assert _digest(app.windowIcon(), 32) == _digest(
        get_app_icon("graphics_designer"), 32)


def test_packaged_launchers_cover_the_icon_family(monkeypatch):
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tools"))
    spec = importlib.util.spec_from_file_location(
        "application_icon_builder", root / "tools/build_windows_app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert set(module.LAUNCHER_ICONS) == set(module.LAUNCHERS)
    assert {value[0] for value in module.LAUNCHER_ICONS.values()} <= APPLICATION_ICONS.keys()


def test_windows_resource_contains_all_optical_sizes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "graphics.ico"
    subprocess.run([
        sys.executable,
        str(root / "tools/windows/build_icon.py"),
        str(output),
        "graphics_designer",
    ], check=True)
    content = output.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", content)
    assert (reserved, image_type, count) == (0, 1, 9)
    dimensions = [struct.unpack_from("<BB", content, 6 + index * 16)
                  for index in range(count)]
    assert dimensions == [(16, 16), (20, 20), (24, 24), (32, 32),
                          (40, 40), (48, 48), (64, 64), (128, 128), (0, 0)]
