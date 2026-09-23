"""Display templates are editable in Graphics Designer, and a new display names where it lands.

Built-ins are edited in place as a project override (Reset to built-in restores the
product document; a built-in is never deleted); a project template is edited
directly; any display can become a template; the New Display dialog states level,
parent and destination and the tree reveals the new display.
"""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
import shiboken6  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import builtin_hierarchy_templates  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.instances import TemplateStore  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay  # noqa: E402

BUILTIN = builtin_hierarchy_templates()[1][0]          # the L2 starting point


def test_builtin_is_edited_as_a_project_override_and_reset_restores_it(tmp_path):
    store = TemplateStore(tmp_path)
    original = store.entries[BUILTIN].document
    assert store.is_builtin(BUILTIN) and not store.is_overridden(BUILTIN)
    edited = {**original, "description": "Site-specific L2 starting point"}
    store.add(BUILTIN, "display", edited)
    assert store.is_builtin(BUILTIN) and store.is_overridden(BUILTIN)
    assert store.instantiate(BUILTIN, "Unit 3")["description"] == "Site-specific L2 starting point"
    saved = json.loads((tmp_path / "_templates.json").read_text(encoding="utf-8"))
    assert saved[BUILTIN]["overrides_builtin"] is True
    reloaded = TemplateStore(tmp_path)                   # the override survives a reload
    assert reloaded.is_overridden(BUILTIN)
    assert reloaded.entries[BUILTIN].document["description"] == "Site-specific L2 starting point"
    assert not reloaded.remove(BUILTIN), "a built-in is never deleted"
    assert reloaded.reset(BUILTIN)
    assert reloaded.is_builtin(BUILTIN) and not reloaded.is_overridden(BUILTIN)
    assert reloaded.entries[BUILTIN].document == original
    assert BUILTIN not in json.loads((tmp_path / "_templates.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="display template"):
        reloaded.add(BUILTIN, "layout", {"layout": BUILTIN, "screens": []})
    assert not reloaded.reset("Not a template")


def test_project_templates_are_created_replaced_and_deleted(tmp_path):
    store = TemplateStore(tmp_path)
    store.add("Unit", "display", {"display": "Unit", "level": 2, "pvms": [], "items": []})
    store.add("Unit", "display", {"display": "Unit", "level": 3, "pvms": [], "items": []})
    assert store.entries["Unit"].document["level"] == 3 and not store.is_builtin("Unit")
    assert store.remove("Unit") and "Unit" not in TemplateStore(tmp_path).names("display")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_new_display_dialog_names_level_parent_and_destination(app, tmp_path):
    from azeo_control_trainer.azeo_graphics_designer.new_display import NO_PARENT, NewDisplayDialog
    templates = TemplateStore(tmp_path)
    hierarchy = {"Overview": (1, ""), "U300": (2, "Overview"), "Heater": (3, "U300")}
    dialog = NewDisplayDialog(templates, hierarchy=hierarchy, display_root=tmp_path / "displays" / "pvm")
    dialog.name_edit.setText("U400")
    dialog.template_option.setChecked(True)
    dialog.template_combo.setCurrentText(BUILTIN)
    assert dialog.display_level == 2 and not dialog.level_combo.isEnabled()
    assert dialog.parent_candidates(2) == ["Overview"]
    assert dialog.parent_combo.itemText(0) == NO_PARENT
    assert dialog.parent_warning.isVisibleTo(dialog) and "L1 parent" in dialog.parent_warning.text()
    dialog.parent_combo.setCurrentIndex(dialog.parent_combo.findData("Overview"))
    assert dialog.parent_name == "Overview"
    assert not dialog.parent_warning.isVisibleTo(dialog)
    text = dialog.destination_text()
    assert "Displays › Overview › U400" in text and "L2" in text
    assert str(tmp_path / "displays" / "pvm" / "U400" / "draft.json") in text
    dialog.blank_option.setChecked(True)
    assert dialog.level_combo.isEnabled()
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(4))
    assert dialog.parent_candidates(4) == ["Heater"]
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(1))
    assert not dialog.parent_combo.isEnabled() and not dialog.parent_warning.isVisibleTo(dialog)
    assert "Displays › U400" in dialog.destination_text()


def flush(app):
    for _ in range(3):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def editor(app, tmp_path, monkeypatch):
    from azeo_control_trainer.azeo_control_designer.designer_window import StrategyDesignerWindow
    from azeo_control_trainer.azeo_graphics_designer import window as graphics_window
    from azeo_control_trainer.core.strategy.serialization import strategy_io

    class WorkspaceSettings(QSettings):
        def __init__(self, *_args):
            super().__init__(str(tmp_path / "workspace.ini"), QSettings.IniFormat)

    monkeypatch.setattr(graphics_window, "QSettings", WorkspaceSettings)
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", tmp_path)
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    root = tmp_path / "displays" / "pvm"
    store = DisplayStore(root)
    store.save_draft(PvmDisplay(name="Overview", level=1, width=1600, height=900))
    designer = StrategyDesignerWindow()
    shell = QWidget()
    designer.explorer = shell
    shell.show()
    window = designer._pvm_studio()
    flush(app)
    yield window, root
    if shiboken6.isValid(window):
        window._confirm_studios_close = lambda _studios: True
        window.close()
    shell.close()
    if shiboken6.isValid(designer):
        designer.close()
        designer.deleteLater()
    shell.deleteLater()
    flush(app)


def tree_names(tree):
    found = {}

    def walk(item):
        for index in range(item.childCount()):
            child = item.child(index)
            name = child.data(0, Qt.UserRole)
            if name:
                found[name] = child
            walk(child)

    walk(tree.invisibleRootItem())
    return found


def test_display_from_template_lands_under_its_parent_and_is_revealed(app, editor):
    window, root = editor
    studio = window.new_from_template(BUILTIN, "U300", parent="Overview")
    flush(app)
    assert studio is not None and studio.display.name == "U300"
    draft = DisplayStore(root).load_draft("U300")
    assert draft.level == 2 and draft.parent == "Overview"
    nodes = tree_names(window.graphics_tree)
    assert nodes["U300"].parent() is nodes["Overview"], "the tree nests the new display under its parent"
    assert window.graphics_tree.currentItem() is nodes["U300"], "the new display is selected"
    assert "U300" in window.statusBar().currentMessage() and "Overview" in window.statusBar().currentMessage()


def test_template_session_saves_back_to_the_template_and_never_publishes(app, editor):
    window, root = editor
    studio = window.edit_template(BUILTIN)
    flush(app)
    assert studio is not None and studio.template_session == BUILTIN
    assert "[Template]" in window.tabs.tabText(window.tabs.indexOf(studio))
    assert BUILTIN not in window._display_hierarchy(), "a template session is not a display"
    assert window.edit_template(BUILTIN) is studio, "editing again raises the open session"
    studio.enter_edit()
    studio.display.description = "Edited in the studio"
    studio.unsaved = True
    assert studio.save_draft()
    templates = TemplateStore(root)
    assert templates.is_overridden(BUILTIN)
    assert templates.entries[BUILTIN].document["description"] == "Edited in the studio"
    assert not (root / BUILTIN).exists(), "a template session writes no display draft"
    from azeo_control_trainer.core.hmi.pvms.instances import TemplateRefused
    with pytest.raises(TemplateRefused):
        studio.store.publish(studio.display)
    window.tabs.setCurrentWidget(studio)
    window._publish()                                   # refused without a dialog, no exception
    assert window.reset_template(BUILTIN)
    assert not TemplateStore(root).is_overridden(BUILTIN)


def test_any_display_can_become_a_template(app, editor):
    window, root = editor
    template = window._save_display_as_template("Overview", "Site overview")
    assert template is not None and template.name == "Site overview"
    templates = TemplateStore(root)
    assert "Site overview" in templates.names("display") and not templates.is_builtin("Site overview")
    assert templates.instantiate("Site overview", "Copy")["display"] == "Copy"
