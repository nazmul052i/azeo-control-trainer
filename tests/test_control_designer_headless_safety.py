"""Offscreen Control Designer commands must never enter a native modal loop."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidgetItem  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.engine import compiler  # noqa: E402
from azeo_control_trainer.core.strategy.engine.compiler import CompileError  # noqa: E402
from azeo_control_trainer.core.strategy.model.block_registry import registry  # noqa: E402
from azeo_control_trainer.azeo_control_designer import designer_tab  # noqa: E402
from azeo_control_trainer.azeo_control_designer.designer_tab import (  # noqa: E402
    StrategyDesignerTab,
)
from azeo_control_trainer.azeo_control_designer.dialogs import (  # noqa: E402
    checkpoint_dialog,
    module_info_dialog,
    upload_dialog,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _NativeModalTrap:
    """Stand-in for Qt's native static methods; any call is a regression."""

    Ok = designer_tab._QtMessageBox.Ok
    No = designer_tab._QtMessageBox.No

    @staticmethod
    def information(*_args, **_kwargs):
        raise AssertionError("native information dialog called headlessly")

    @staticmethod
    def warning(*_args, **_kwargs):
        raise AssertionError("native warning dialog called headlessly")

    @staticmethod
    def critical(*_args, **_kwargs):
        raise AssertionError("native critical dialog called headlessly")

    @staticmethod
    def question(*_args, **_kwargs):
        raise AssertionError("native question dialog called headlessly")


def test_compile_success_and_error_notices_never_call_native_modals(
    monkeypatch,
) -> None:
    _app()
    tab = StrategyDesignerTab()
    tab._create_canvas("HEADLESS")
    monkeypatch.setattr(designer_tab, "is_headless", lambda: True)
    monkeypatch.setattr(designer_tab, "_QtMessageBox", _NativeModalTrap)

    compiled = SimpleNamespace(exec_order=[], forward_wires=[], bkcal_wires=[])
    monkeypatch.setattr(compiler, "compile_strategy", lambda _graph: compiled)
    assert tab._compile() is compiled

    def fail_compile(_graph):
        raise CompileError("invalid strategy")

    monkeypatch.setattr(compiler, "compile_strategy", fail_compile)
    assert tab._compile() is None
    tab.cleanup()
    tab.deleteLater()


def test_file_input_and_exec_prompts_exit_before_native_ui(monkeypatch) -> None:
    _app()
    tab = StrategyDesignerTab()
    canvas = tab._create_canvas("HEADLESS")
    block = registry.create("ABS", "ABS1")
    assert block is not None
    item = canvas.scene.add_block(block, QPointF())
    item.setSelected(True)
    monkeypatch.setattr(designer_tab, "is_headless", lambda: True)

    assert tab._save_as() is False

    monkeypatch.setattr(
        module_info_dialog,
        "ModuleInfoDialog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("modal module-info dialog created headlessly")),
    )
    assert tab._show_module_info("properties") is None

    assert tab._save_selection_as_template() is False
    tab.cleanup()
    tab.deleteLater()


def test_headless_recovery_preserves_autosaves(monkeypatch, tmp_path) -> None:
    _app()
    recovery = tmp_path / "module.json"
    recovery.write_text("{}", encoding="utf-8")
    tab = StrategyDesignerTab()
    monkeypatch.setattr(designer_tab, "is_headless", lambda: True)
    monkeypatch.setattr(designer_tab, "_QtMessageBox", _NativeModalTrap)

    tab.check_auto_save_recovery(tmp_path)

    assert recovery.exists()
    tab.cleanup()
    tab.deleteLater()


def test_upload_information_and_error_paths_are_nonmodal(monkeypatch) -> None:
    _app()
    tab = StrategyDesignerTab()
    dialog = upload_dialog.UploadDialog(tab, None, parent=tab)
    monkeypatch.setattr(upload_dialog, "is_headless", lambda: True)
    monkeypatch.setattr(upload_dialog, "_QtMessageBox", _NativeModalTrap)

    # Empty selection reaches the ordinary informational outcome.
    dialog._do_upload()

    change = upload_dialog.ParameterChange(
        module="M1",
        file_path=Path("missing.json"),
        block_id="block",
        instance_name="ABS1",
        block_type="ABS",
        parameter="X",
        file_value=0,
        runtime_value=1,
        category="configuration",
    )
    dialog._changes = [change]
    dialog._table.setRowCount(1)
    selected = QTableWidgetItem()
    selected.setCheckState(Qt.Checked)
    selected.setData(Qt.UserRole, "configuration")
    dialog._table.setItem(0, 5, selected)
    monkeypatch.setattr(
        upload_dialog,
        "save_and_sync_parameter_changes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("write failed")),
    )

    dialog._do_upload()
    dialog.close()
    tab.cleanup()
    tab.deleteLater()


def test_checkpoint_confirmations_refuse_destructive_work_headlessly(
    monkeypatch,
) -> None:
    _app()
    tab = StrategyDesignerTab()
    dialog = checkpoint_dialog.CheckpointDialog(tab, None, parent=tab)
    monkeypatch.setattr(checkpoint_dialog, "is_headless", lambda: True)
    monkeypatch.setattr(checkpoint_dialog, "_QtMessageBox", _NativeModalTrap)

    dialog._table.setRowCount(1)
    dialog._table.setItem(0, 0, QTableWidgetItem("CP1"))
    dialog._table.setItem(0, 5, QTableWidgetItem("checkpoint.json"))
    dialog._table.setCurrentCell(0, 0)
    deleted: list[str] = []
    restored: list[str] = []
    monkeypatch.setattr(
        checkpoint_dialog,
        "delete_checkpoint",
        lambda path: deleted.append(path),
    )
    monkeypatch.setattr(
        checkpoint_dialog,
        "load_checkpoint",
        lambda path: restored.append(path) or {},
    )

    dialog._delete_checkpoint()
    dialog._restore_checkpoint()

    assert deleted == []
    assert restored == []
    dialog.close()
    tab.cleanup()
    tab.deleteLater()
