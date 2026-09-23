"""Phase 2 contract: one captured namespace, traceable references and outage behavior."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_configuration_database import capture, encoded
from test_configuration_database import database as database_fixture, files as files_fixture
from azeo_control_trainer.core.configuration.catalog import CatalogIndex
from azeo_control_trainer.core.configuration.catalog_client import CatalogSession
from azeo_control_trainer.core.configuration.client import ServiceUnavailable
from azeo_control_trainer.core.configuration.documents import Forbidden, prepare_import

database = database_fixture
files = files_fixture


@pytest.fixture
def catalog_files(files):
    source = copy.deepcopy(files)
    source[0] = encoded("_project.json", {"project_id": "test-project", "areas": [{
        "name": "Area", "controller": {"name": "CTRL"}, "strategies": ["control/LOOP.json"],
        "virtual_io": {"catalog": "io/catalog.json"}}]})
    source.extend([
        encoded("io/catalog.json", {"tags": [{"name": "SPARE_AI", "kind": "AI", "unit": "barg", "range": [0, 10]}]}),
        encoded("displays/pvm/Test/draft.json", {"display": "Test", "pvms": [
            {"id": "pid", "class": "PID/dynamo_inline", "params": {"path": "LOOP/PID1"}}], "items": [
                {"kind": "datalink", "path": "LOOP/PID1/PV"},
                {"kind": "rect", "props": {"text": {"kind": "expression", "expr": "pv - sp",
                 "refs": {"pv": "LOOP/PID1/PV", "sp": "MISSING/SP"}}}},
                {"kind": "rect", "anim": {"text": {"path": "LOOP/PID1/SP"}}},
                {"kind": "rect", "props": {"text": {"kind": "animation", "path": "{Tag}/PV", "indirect": True}}},
                {"kind": "user_entry", "entry": {"path": "LOOP/PID1/SP"}},
                {"kind": "rect", "user_pvm": "Custom", "actions": [
                    {"kind": "script", "source": "return 1;"},
                    {"kind": "open_user_faceplate", "target": "CustomFP"}]}]}),
        encoded("displays/pvm/_library/user_pvms.json", {
            "Custom": {"definition_kind": "pvm", "paired_faceplate": "CustomFP",
                       "items": [{"kind": "nested_pvm", "class": "MissingNested"}]},
            "CustomFP": {"definition_kind": "faceplate", "items": []}}),
    ])
    return source


@pytest.fixture
def bundle(catalog_files):
    prepared = prepare_import(catalog_files)
    return {"project": {"id": str(uuid4()), "name": "Pilot", "generation": 1,
                        "modified": "2026-09-08T00:00:00Z", "digest": prepared.digest},
            "catalog": prepared.catalog, "tags": prepared.tags, "objects": []}


def test_catalog_covers_spare_io_installed_and_authored_classes_and_exact_bindings(bundle):
    index = CatalogIndex(bundle)
    assert index.entries["FIELD/SPARE_AI"]["unit"] == "barg"
    assert index.entries["FIELD/SPARE_AI"]["source"] == "io/catalog.json"
    assert index.entries["io:SPARE_AI"]["route"]["eu_range"] == [0, 10]
    assert index.entries["class:PID/faceplate"]["origin"] == "installed"
    assert index.entries["user:CustomFP"]["kind"] == "faceplate"
    refs = index.references("LOOP")
    assert any(r["target"] == "class:PID/faceplate" for r in refs)
    assert any(r["target"] == "controller:CTRL" for r in refs)
    assert any(r["source"].startswith("display:") and r["target"] == "LOOP/PID1/PV" for r in refs)
    assert any(r["target"] == "LOOP/PID1/SP" and "anim" in r["location"] for r in refs)
    edges = index.catalog["edges"]
    assert any(r["target"] == "user:CustomFP" and r["status"] == "resolved" for r in edges)
    assert any(r["target"] == "user:MissingNested" and r["status"] == "unresolved" for r in edges)
    assert any(r["target"] == "MISSING/SP" and r["status"] == "unresolved" for r in edges)
    assert any(r["target"] == "{Tag}/PV" and r["status"] == "dynamic" for r in edges)
    assert any("Script" in i["message"] for i in index.catalog["issues"])
    assert any(r["path"] == "LOOP/PID1/CONFIG/lo_lim" for r in index.search("LOOP CONFIG lo_lim")[0])
    assert index.loop("LOOP/PID1/PV") == "LOOP"


def test_catalog_never_follows_server_filesystem_io_paths(catalog_files, tmp_path):
    marker = tmp_path / "do-not-read.json"
    marker.write_text('{"tags":[{"name":"LEAK","kind":"AI"}]}')
    catalog_files[0] = encoded("_project.json", {"areas": [{"virtual_io": {"catalog": str(marker)}}]})
    prepared = prepare_import(catalog_files)
    assert not any(t["io_tag"] == "LEAK" for t in prepared.tags)
    assert any("relative" in i["message"] for i in prepared.catalog["issues"])


def test_read_catalog_is_coherent_authorized_and_unchanged_capture_reindexes(database, catalog_files):
    repository, token = database
    first = capture(repository, token, catalog_files)
    snapshot = repository.catalog(token, first["project_id"])
    assert snapshot["project"]["generation"] == 1
    assert any(t["path"] == "FIELD/SPARE_AI" for t in snapshot["tags"])
    assert snapshot["catalog"]["source_project_id"] == "test-project"
    unchanged = repository.catalog(token, first["project_id"], known=snapshot["catalog_stamp"])
    assert unchanged["not_modified"] and "tags" not in unchanged
    original = repository.export(token, first["project_id"])
    with repository.connection() as c:
        c.execute("DELETE FROM catalogs WHERE project_id=%s", (first["project_id"],))
    repeat = capture(repository, token, catalog_files, expected=1)
    assert repeat["reindexed"] and repeat["changed"] == 0 and repeat["generation"] == 1
    assert original == repository.export(token, first["project_id"])
    assert "not_modified" not in repository.catalog(token, first["project_id"], known=snapshot["catalog_stamp"])
    reader = repository.provision_identity("reader")
    with pytest.raises(Forbidden):
        repository.catalog(reader, first["project_id"])
    repository.grant("reader", first["project_id"], "reader")
    assert repository.catalog(reader, first["project_id"])["project"]["generation"] == 1
    repository.revoke("reader")
    with pytest.raises(Forbidden):
        repository.catalog(reader, first["project_id"])


class FakeClient:
    def __init__(self, bundle):
        self.bundle, self.error, self.delay = bundle, None, 0
        self.threads = []

    def request(self, path):
        self.threads.append(threading.get_ident())
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        if path == "/v1/projects":
            return [{**self.bundle["project"], "source_project_id": "test-project"}]
        return copy.deepcopy(self.bundle)


def make_session(tmp_path, bundle, token="one"):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    (root / "_project.json").write_text(json.dumps({"project_id": "test-project", "areas": []}))
    return CatalogSession(root, profile={"url": "http://127.0.0.1:8766", "token": token},
                          cache_dir=tmp_path / "cache", client=FakeClient(bundle))


def test_cache_survives_outage_but_not_revocation_wrong_identity_or_mixed_project(tmp_path, bundle):
    session = make_session(tmp_path, bundle)
    online = session.load()
    assert online["selected"] == bundle["project"]["id"]
    assert "STALE" in online["local"]
    session.client.error = ServiceUnavailable("down")
    offline = session.load()
    assert "OFFLINE" in offline["state"]
    assert offline["index"].project == online["index"].project
    other = make_session(tmp_path, bundle, token="other")
    other.client.error = ServiceUnavailable("down")
    with pytest.raises(ServiceUnavailable):
        other.load()
    session.client.error = Forbidden("revoked")
    with pytest.raises(Forbidden):
        session.load()
    session.client.error = ServiceUnavailable("down")
    with pytest.raises(ServiceUnavailable):
        session.load()


def test_no_silent_selection_of_an_unrelated_project(tmp_path, bundle):
    session = make_session(tmp_path, bundle)
    (session.root / "_project.json").write_text('{"project_id":"different","areas":[]}')
    result = session.load()
    assert result["index"] is None and len(result["projects"]) == 1


def test_same_named_authored_classes_keep_library_scope(catalog_files):
    catalog_files.extend([
        encoded("libraries/Second/_library/user_pvms.json", {
            "Custom": {"definition_kind": "pvm", "paired_faceplate": "CustomFP", "items": []},
            "CustomFP": {"definition_kind": "faceplate", "items": []}}),
        encoded("displays/pvm/Second/draft.json", {"display": "Second", "pvms": [], "items": [
            {"kind": "rect", "user_pvm": "Custom"}]}),
    ])
    prepared = prepare_import(catalog_files)
    custom = [n for n in prepared.catalog["nodes"] if n.get("origin") == "authored" and n["name"] == "Custom"]
    assert len(custom) == 2 and len({n["path"] for n in custom}) == 2
    edge = next(e for e in prepared.catalog["edges"] if e["source"].endswith("Second/draft.json")
                and e["kind"] == "uses_class")
    assert edge["target"] == "user:displays/pvm/_library/user_pvms.json#Custom"
    assert edge["status"] == "resolved"


def test_colliding_controller_publications_are_not_resolved_to_the_first_module(catalog_files):
    import base64
    other = json.loads(base64.b64decode(catalog_files[1]["content"]))
    other["name"] = "OTHER"
    catalog_files.append(encoded("control/OTHER.json", other))
    prepared = prepare_import(catalog_files)
    publications = [n for n in prepared.catalog["nodes"] if n["path"] == "io:ctrl.PID1.OUT"]
    assert len(publications) == 1 and publications[0]["ambiguous"]
    assert any("Ambiguous controller" in i["message"] for i in prepared.catalog["issues"])


def test_full_catalog_transport_does_not_walk_every_scalar_through_generic_encoder(bundle):
    from fastapi.testclient import TestClient
    from azeo_control_trainer.services.configuration.api import create_app
    large = copy.deepcopy(bundle)
    sample = next(t for t in bundle["tags"] if t["kind"] == "parameter")
    large["tags"] = [{**sample, "path": f"LOOP/PID{n}/CONFIG/lo_lim"} for n in range(34000)]
    class Service:
        def authenticate(self, token):
            return {"name": "test"}
        def catalog(self, token, project, *, known=""):
            return large
    client = TestClient(create_app(Service()))
    times = []
    for _ in range(3):
        started = time.perf_counter()
        response = client.get("/v1/projects/" + bundle["project"]["id"] + "/catalog",
                              headers={"Authorization": "Bearer test"})
        times.append(time.perf_counter() - started)
        assert response.status_code == 200 and len(response.json()["tags"]) == 34000
    assert min(times) < 1.5, times


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    app = QApplication.instance() or QApplication([])
    apply_application_font()
    yield app
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    _shutdown()
    app.processEvents()


def wait_ready(app, browser):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        app.processEvents()
        if browser._started and browser._request is None:
            return
        time.sleep(.01)
    pytest.fail("Catalog worker did not finish")


def test_browser_worker_selection_loop_preview_and_close_are_reachable(app, tmp_path, bundle):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from azeo_control_trainer.core.presentation.configuration_catalog import ConfigurationCatalogDialog
    session = make_session(tmp_path, bundle)
    session.client.delay = .12
    dialog = ConfigurationCatalogDialog(session=session)
    ticks = []
    timer = QTimer(dialog)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(10)
    dialog.show()
    wait_ready(app, dialog.browser)
    browser = dialog.browser
    assert browser.index is not None, browser.status.text()
    assert ticks and all(t != threading.get_ident() for t in session.client.threads)
    QTest.keyClicks(browser.search, "LOOP/PID1/PV")
    browser.results.selectRow(0)
    assert browser._path == "LOOP/PID1/PV"
    QTest.mouseClick(browser.loop_button, Qt.LeftButton)
    assert browser._path == "LOOP"
    assert browser.reference_model.rows
    browser.inspect("LOOP/PID1")
    browser.inspect("class:PID/faceplate")
    preview = browser.preview()
    assert preview and not preview.viewer._timer.isActive()
    assert preview.faceplate.pvm.role == "faceplate"
    assert preview.faceplate.write_handler is None
    assert preview.faceplate.faceplate_surface.width() == preview.faceplate.faceplate_profile.width
    preview.close()
    session.client.error = ServiceUnavailable("down")
    browser.refresh()
    wait_ready(app, browser)
    assert "OFFLINE" in browser.status.text()
    session.client.error = Forbidden("revoked")
    browser.refresh()
    wait_ready(app, browser)
    assert browser.index is None and browser.result_model.rowCount() == 0
    browser.refresh()
    dialog.close()
    wait_ready(app, browser)


def test_binding_picker_preserves_captured_types_and_field_picker_returns_store_tag(app, tmp_path, bundle, monkeypatch):
    from azeo_control_trainer.azeo_graphics_designer.studio.binding_editor import (
        BindingCatalog, UnifiedBindingEditor, BindingTarget,
    )
    from azeo_control_trainer.azeo_control_designer.panels.tag_browser import TagBrowserDialog
    from azeo_control_trainer.core.presentation import configuration_catalog as ui
    editor = UnifiedBindingEditor(BindingTarget("value", "number"), BindingCatalog())
    tag = next(t for t in bundle["tags"] if t["path"] == "LOOP/PID1/PV")
    editor.use_configuration_tag(tag)
    result = editor.current_result()
    assert result.source == tag["path"] and result.valid
    assert result.descriptor()["path"] == tag["path"]
    real_dialog = ui.ConfigurationCatalogDialog
    def picker(*args, **kwargs):
        kwargs["session"] = make_session(tmp_path, bundle)
        return real_dialog(*args, **kwargs)
    monkeypatch.setattr(ui, "ConfigurationCatalogDialog", picker)
    field = TagBrowserDialog()
    field.shared_catalog_button.click()
    chosen = field._configuration_picker
    wait_ready(app, chosen.browser)
    chosen.browser.inspect("io:SPARE_AI")
    chosen.browser.choose()
    assert field.selected_tag == "SPARE_AI"
    editor.close()
    field.close()


def test_live_browser_defers_catalog_models_until_shared_tab_is_selected(app, tmp_path, bundle, monkeypatch):
    from azeo_control_trainer.core.presentation.tagdb_browser import TagDatabaseDialog
    from azeo_control_trainer.core.presentation import configuration_catalog as ui
    from PySide6.QtWidgets import QTableView
    monkeypatch.setattr(ui, "CatalogSession", lambda root: make_session(tmp_path, bundle))
    dialog = TagDatabaseDialog(lambda: [], area_name="Test")
    dialog.show()
    app.processEvents()
    assert dialog.catalog is None and not dialog.findChildren(QTableView)
    dialog.tabs.setCurrentIndex(1)
    wait_ready(app, dialog.catalog)
    assert dialog.catalog.index.entries["LOOP/PID1/CONFIG/lo_lim"]
    dialog.tabs.setCurrentIndex(0)
    dialog.close()
    app.processEvents()
