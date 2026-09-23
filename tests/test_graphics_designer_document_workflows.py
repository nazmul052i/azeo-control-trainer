"""Document commands preserve authored content, ownership and live truth."""
import os
import json
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QWidget,
)

import azeo_control_trainer.core.strategy.blocks  # noqa: F401 - register blocks
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import L1_TEMPLATE
from azeo_control_trainer.core.hmi.pvms.instances import TemplateStore
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import MODE_EDIT, MODE_VIEW
from azeo_control_trainer.azeo_graphics_designer.new_display import NewDisplayDialog
from azeo_control_trainer.azeo_graphics_designer.studio.display_properties import (
    DisplayPropertiesDialog,
)
from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    apply_application_font()
    return application


@pytest.fixture
def make_window(tmp_path, app):
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                      str(tmp_path / "settings"))
    windows = []

    def create(root=None, graphs=None):
        root = Path(root or tmp_path / "graphics")
        window = HmiStudioWindow(lambda: graphs or {}, root, area_name="Review")
        windows.append(window)
        return window

    yield create
    for window in reversed(windows):
        window.close()
        window.deleteLater()
    app.processEvents()


def seed(root, name="Overview", **kwargs):
    store = DisplayStore(root)
    store.save_draft(PvmDisplay(name, **kwargs))
    return store


def test_display_properties_preserves_qwidget_geometry_contract(make_window, app):
    from azeo_control_trainer.core.presentation.dialog_layout import (
        fit_dialog_to_screen,
    )

    window = make_window()
    dialog = DisplayPropertiesDialog(window.current(), window)
    try:
        assert callable(dialog.width) and callable(dialog.height)
        assert dialog.property("authoringDialog") is True
        assert dialog.findChild(QWidget, "authoring_dialog_header") is not None
        assert dialog.buttons.button(
            QDialogButtonBox.Ok).property("primaryAction") is True
        assert dialog.width_spin.value() == window.current().display.width
        assert dialog.height_spin.value() == window.current().display.height
        dialog.show()
        app.processEvents()
        fit_dialog_to_screen(dialog)
        assert dialog.width() > 0 and dialog.height() > 0
    finally:
        dialog.close()
        dialog.deleteLater()


def test_save_template_from_current_canvas(make_window):
    window = make_window()
    window.current().add_static("rect", x=20, y=30, w=80, h=40)
    template = window.save_current_as_template("Review template")
    assert template is not None and template.document["items"]


def test_new_from_template_preserves_existing_display(make_window, tmp_path):
    root = tmp_path / "graphics"
    store = seed(root, description="Existing authored display")
    window = make_window(root)
    before = store.load_draft("Overview").to_dict()
    window.new_from_template(L1_TEMPLATE, "Overview")
    assert store.load_draft("Overview").to_dict() == before


def test_create_display_dialog_offers_blank_and_display_templates_only(
        tmp_path, app):
    templates = TemplateStore(tmp_path)
    templates.add("Project display", "display", {
        "display": "Project display", "description": "Unit starting point",
        "width": 1200, "height": 800, "level": 2,
    })
    templates.add("Project layout", "layout", {
        "layout": "Project layout", "screens": [],
    })
    dialog = NewDisplayDialog(templates)
    try:
        assert dialog.property("authoringDialog") is True
        assert dialog.findChild(QWidget, "authoring_dialog_header") is not None
        assert dialog.create_button.property("primaryAction") is True
        choices = {
            dialog.template_combo.itemText(index)
            for index in range(dialog.template_combo.count())
        }
        assert dialog.blank_option.isChecked()
        assert not dialog.template_combo.isEnabled()
        assert "Project display" in choices
        assert "Project layout" not in choices
        assert not dialog.create_button.isEnabled()

        dialog.name_edit.setText("Boiler Feedwater")
        assert dialog.create_button.isEnabled()
        dialog.template_option.setChecked(True)
        dialog.template_combo.setCurrentText("Project display")
        assert dialog.template_name == "Project display"
        assert "Project · L2 · 1200 × 800" in dialog.template_summary.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_blank_and_from_template_commands_share_the_creation_dialog(
        make_window, monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer import new_display as dialog_module
    from azeo_control_trainer.core.presentation import headless

    window = make_window()
    selections = iter((
        ("Blank Process", ""),
        ("Template Process", L1_TEMPLATE),
    ))
    preferences = []

    class AcceptedCreation:
        def __init__(self, _templates, _parent, *, prefer_template=False, **_destination):
            preferences.append(prefer_template)
            self.display_name, self.template_name = next(selections)
            self.display_level, self.parent_name = 1, ""

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(headless, "is_headless", lambda: False)
    monkeypatch.setattr(dialog_module, "NewDisplayDialog", AcceptedCreation)

    blank = window._new_display()
    templated = window._new_display_from_template()
    store = DisplayStore(window._root)
    blank_document = store.load_draft("Blank Process")
    template_document = store.load_draft("Template Process")

    assert preferences == [False, True]
    assert blank is not None and blank_document is not None
    assert (blank_document.width, blank_document.height) == (1600, 900)
    assert templated is not None and template_document is not None
    assert template_document.level == 1
    assert template_document.items


def test_every_new_display_entry_point_routes_to_the_shared_dialog(
        make_window, monkeypatch):
    window = make_window()
    preferences = []
    monkeypatch.setattr(
        window,
        "_new_display",
        lambda *, prefer_template=False: preferences.append(prefer_template),
    )

    window.dispatch("display.new")
    window.dispatch("display.new_from_template")

    assert preferences == [False, True]
    assert window.FOLDER_NEW["displays"] == ("New Display…", "_new_display")


def test_view_save_cannot_overwrite_another_editor(make_window, tmp_path):
    root = tmp_path / "graphics"
    store = seed(root)
    writer = make_window(root)
    observer = make_window(root)
    assert writer.current().mode == MODE_EDIT
    assert observer.current().mode == MODE_VIEW
    writer.current().add_static("rect", x=20, y=30, w=80, h=40)
    writer.dispatch("display.save")
    before = store.load_draft("Overview").to_dict()
    observer.dispatch("display.save")
    assert store.load_draft("Overview").to_dict() == before


def test_find_replace_preserves_unsaved_items_even_with_no_matches(
        make_window, tmp_path):
    root = tmp_path / "graphics"
    seed(root)
    window = make_window(root)
    studio = window.current()
    studio.add_static("rect", x=20, y=30, w=80, h=40)
    assert studio.unsaved
    before = studio._document()
    assert window.find_replace_configuration(
        "THIS_STRING_IS_ABSENT", "replacement", apply=True) == ()
    assert studio._document() == before


def test_rename_does_not_recreate_old_display_on_save(
        make_window, tmp_path, monkeypatch):
    root = tmp_path / "graphics"
    seed(root, "Original")
    window = make_window(root)
    with monkeypatch.context() as patched:
        patched.setattr(
            "azeo_control_trainer.core.presentation.headless.is_headless",
            lambda: False)
        patched.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed", True))
        assert window._rename_display("Original")
    window.dispatch("display.save")
    assert not (root / "Original" / "draft.json").exists()
    assert window.current().display.name == "Renamed"


def test_copy_does_not_copy_the_source_edit_lock(make_window, tmp_path):
    root = tmp_path / "graphics"
    seed(root)
    window = make_window(root)
    window._clip_display("Overview")
    new_name = window._paste_display()
    assert new_name != "Overview"
    copied = window.open_display(new_name)
    assert copied.mode == MODE_EDIT


def test_quick_online_uses_live_values_even_when_studio_is_testing(
        make_window):
    block = BlockRegistry().create("PID", "PID1")
    block._apply_config()
    block.outputs["OUT"].value = 12.5
    graph = StrategyGraph(name="REVIEW")
    graph.add_block(block)
    window = make_window(graphs={"REVIEW": graph})
    studio = window.current()
    path = "REVIEW/PID1/OUT"
    live_value = studio.preview_source.source.read(path).value
    assert live_value == 12.5
    studio.preview_source.set_override(path, value=99.0)
    studio.enter_test()
    view = window.open_quick_online()
    assert view.engine._source.read(path).value == live_value
    assert not view.engine._source.write(path, 42).success
    studio.preview_source.set_override(path, value=123.0)
    block.outputs["OUT"].value = 14.0
    assert view.engine._source.read(path).value == 14.0


def test_revert_restores_display_variable_values(make_window, tmp_path):
    root = tmp_path / "graphics"
    store = DisplayStore(root)
    old = PvmDisplay("Overview", variables=[{
        "name": "Limit", "type": "Number", "value": 10,
    }])
    first = store.publish(old)
    current = PvmDisplay("Overview", variables=[{
        "name": "Limit", "type": "Number", "value": 20,
    }])
    store.publish(current)
    store.save_draft(current)
    window = make_window(root)
    studio = window.current()
    assert studio.variables.get("Limit").value == 20
    studio._revert(first["rev"])
    assert studio.variables.get("Limit").value == 10


def test_published_view_preserves_overlapping_object_order(make_window):
    from PySide6.QtCore import QPointF
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView

    window = make_window()
    studio = window.current()
    background = studio.add_static("rect", x=10, y=10, w=100, h=100)
    background.data["fill"] = "#ffffff"
    foreground = studio.add_static(
        "rect", x=20, y=20, w=40, h=40)
    foreground.data["fill"] = "#0055ff"
    location = QPointF(30, 30)

    def top_static(scene):
        return next(item.data["id"] for item in scene.items(location)
                    if isinstance(item, StaticItem))

    assert top_static(studio.canvas.scene()) == foreground.data["id"]
    viewer = PvmDisplayView(studio._document(), lambda: {}, live=False)
    try:
        assert top_static(viewer.scene()) == foreground.data["id"]
    finally:
        viewer.close()


def test_find_replace_uses_live_unsaved_text_and_is_undoable(make_window, tmp_path):
    store = seed(tmp_path / "graphics", description="Old disk text")
    window = make_window()
    studio = window.current()
    studio.add_static("text", text="Old unsaved label")
    before = json.loads(json.dumps(studio._document()))
    results = window.find_replace_configuration("Old", "New", apply=True)
    assert results[0].occurrences == 2
    assert studio.display.description == "New disk text"
    assert studio._document()["items"][0]["text"] == "New unsaved label"
    assert not store.load_draft("Overview").items
    assert studio.unsaved
    assert studio.undo()
    assert studio._document() == before
    assert studio.redo()
    assert studio.save_draft()
    assert store.load_draft("Overview").items[0]["text"] == "New unsaved label"


def test_find_replace_preflights_all_locks_before_writing(tmp_path):
    from azeo_control_trainer.core.hmi.pvms.configuration import find_replace
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayLocked

    store = seed(tmp_path, "First", description="old")
    seed(tmp_path, "Locked", description="old")
    store.acquire_lock("Locked")
    try:
        with pytest.raises(DisplayLocked):
            find_replace(tmp_path, "old", "new", apply=True)
        assert store.load_draft("First").description == "old"
        assert not (tmp_path / "First" / ".lock").exists()
        assert store.owns_lock("Locked")
    finally:
        store.release_lock("Locked")


def test_failed_rename_restores_draft_identity_and_ownership(tmp_path, monkeypatch):
    from azeo_control_trainer.core.hmi.pvms import publishing

    store = seed(tmp_path, description="Original content")
    store.acquire_lock("Overview")
    before = store.load_draft("Overview").to_dict()
    write = publishing.atomic_write_json

    def fail_new_name(path, document):
        if path.parent.name == "Renamed":
            raise OSError("simulated write failure")
        return write(path, document)

    monkeypatch.setattr(publishing, "atomic_write_json", fail_new_name)
    with pytest.raises(OSError, match="write failure"):
        store.rename_display("Overview", "Renamed")
    assert store.load_draft("Overview").to_dict() == before
    assert not (tmp_path / "Renamed").exists()
    assert store.owns_lock("Overview")
    store.release_lock("Overview")


@pytest.mark.parametrize("name", ["Overview", "../escaped", "folder/child", ""])
def test_template_destination_cannot_replace_or_escape_library(tmp_path, name):
    store = seed(tmp_path)
    before = (tmp_path / "Overview" / "draft.json").read_bytes()
    with pytest.raises((FileExistsError, ValueError)):
        store.create_draft(PvmDisplay(name, description="Replacement"))
    assert (tmp_path / "Overview" / "draft.json").read_bytes() == before


def test_rename_preserves_unsaved_undo_recovery_guides_and_lock(make_window, tmp_path):
    from azeo_control_trainer.azeo_graphics_designer.studio.authoring_state import (
        load_guides, save_guides,
    )
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayLocked

    root = tmp_path / "graphics"
    store = seed(root)
    published = store.publish(store.load_draft("Overview"))
    window = make_window()
    studio = window.current()
    studio.add_static("text", text="Unsaved")
    studio.authoring_guides = [("x", 100.0)]
    save_guides(root, "Overview", studio.authoring_guides)
    studio._write_recovery()
    assert window._move_display("Overview", "Renamed")
    assert studio.unsaved
    assert studio.store.owns_lock("Renamed")
    assert json.loads((root / "Renamed" / "draft.json").read_text())[
        "display"] == "Renamed"
    assert not studio.store.owns_lock("Overview")
    with pytest.raises(DisplayLocked):
        store.acquire_lock("Renamed")
    assert load_guides(root, "Renamed") == [("x", 100.0)]
    assert not load_guides(root, "Overview")
    assert store.load_recovery("Renamed").name == "Renamed"
    assert studio.undo() and studio.display.name == "Renamed"
    assert studio.redo() and studio.display.name == "Renamed"
    assert studio.save_draft()
    assert not (root / "Overview").exists()
    assert store.revision_document("Renamed", published["rev"])["display"] == "Renamed"
    studio._revert(published["rev"])
    assert studio.display.name == "Renamed"
    assert not (root / "Overview").exists()


def test_cut_moves_open_draft_and_copy_keeps_latest_canvas(make_window, tmp_path):
    root = tmp_path / "graphics"
    seed(root)
    window = make_window()
    studio = window.current()
    studio.add_static("text", text="Unsaved source")
    studio._write_recovery()
    window._clip_display("Overview")
    copied_name = window._paste_display()
    assert DisplayStore(root).load_draft(copied_name).items[0]["text"] == "Unsaved source"
    assert not (root / copied_name / ".recovery.json").exists()
    window._clip_display("Overview", cut=True)
    moved_name = window._paste_display()
    assert studio.display.name == moved_name
    assert studio.save_draft()
    assert not (root / "Overview").exists()


def test_save_and_publish_require_the_current_lock_token(make_window, tmp_path):
    from azeo_control_trainer.core.hmi.pvms.publishing import PublishRefused

    root = tmp_path / "graphics"
    store = seed(root)
    window = make_window()
    studio = window.current()
    studio.add_static("text", text="Unsaved")
    studio.store.release_lock("Overview")
    store.acquire_lock("Overview")
    try:
        assert studio.mode == MODE_EDIT  # stale UI state is not ownership
        assert not studio.save_draft()
        with pytest.raises(PublishRefused):
            studio.publish()
        studio._write_recovery()
        assert not (root / "Overview" / ".recovery.json").exists()
        assert not store.load_draft("Overview").items
    finally:
        store.release_lock("Overview")


@pytest.mark.parametrize("rename_first", [False, True])
def test_observer_gets_current_draft_when_it_enters_edit(
        make_window, tmp_path, rename_first):
    root = tmp_path / "graphics"
    store = seed(root)
    writer = make_window()
    observer = make_window()
    writer.current().add_static("text", text="Saved by first engineer")
    assert writer.current().save_draft()
    writer.current().leave_edit()
    name = "Renamed" if rename_first else "Overview"
    if rename_first:
        assert observer._move_display("Overview", name)
    observer.current().enter_edit()
    assert observer.current()._document()["items"][0]["text"] == "Saved by first engineer"
    assert observer.current().save_draft()
    assert store.load_draft(name).items[0]["text"] == "Saved by first engineer"


def test_mixed_equal_z_stacking_survives_repeated_load_and_undo(make_window):
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem, StaticItem, PipeItem
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView

    window = make_window()
    studio = window.current()
    studio.add_static("rect", 0, 0, 100, 100)
    pvm = studio.place_block("UNIT/PID1", "PID", 10, 10,
                             role=("dynamo_compact", ""))
    studio._restore_item({"kind": "pipe", "id": "pipe", "z": 0,
                          "points": [[0, 0], [80, 80]]})
    studio.add_static("text", text="Top")

    def order(scene):
        return [item.pvm.id if isinstance(item, PvmItem) else item.data["id"]
                for item in scene.items()
                if isinstance(item, (PvmItem, StaticItem, PipeItem))]

    expected = order(studio.canvas.scene())
    assert expected.index("pipe") < expected.index(pvm.id)
    for _ in range(2):
        document = json.loads(json.dumps(studio._document()))
        viewer = PvmDisplayView(document, lambda: {}, live=False)
        try:
            assert order(viewer.scene()) == expected
        finally:
            viewer.close()
        studio._load_document(document)
        assert order(studio.canvas.scene()) == expected
    studio.add_static("text", text="Temporary")
    assert studio.undo()
    assert order(studio.canvas.scene()) == expected
