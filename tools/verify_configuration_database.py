"""Verify the real configuration service, lossless pilot export and native Explorer UI."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"
OUT = ROOT / "logs" / "configuration-ui"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    from PySide6.QtCore import Qt, qInstallMessageHandler
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from azeo_control_trainer.azeo_explorer.project_admin import ProjectAdministrator
    from azeo_control_trainer.azeo_explorer.project_administrator import ProjectAdministratorDialog
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    from azeo_control_trainer.core.configuration.documents import export_project, prepare_import, read_project
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font

    client = ConfigurationClient(**read_profile())
    project = next(r for r in client.request("/v1/projects") if r["name"] == "APVC Database Pilot")
    bundle = client.request(f"/v1/projects/{project['id']}/export")
    source = read_project(ROOT / "projects/AzeoPlantVirtualController")
    prepared = prepare_import(source["files"])
    assert prepared.digest == bundle["digest"], "Source changed after the pilot capture"
    assert {f["path"]: f["content"] for f in source["files"]} == {
        f["path"]: f["content"] for f in bundle["files"]}
    with tempfile.TemporaryDirectory(prefix="azeo-configuration-export-") as temporary:
        restored = export_project(bundle, Path(temporary) / "restored")
        for item in bundle["files"]:
            assert (restored / item["path"]).read_bytes() == base64.b64decode(item["content"])
    samples = []
    path = f"/v1/projects/{project['id']}/tags?q=PIC-2001&limit=200"
    client.request(path)
    for _ in range(40):
        started = time.perf_counter()
        assert client.request(path)
        samples.append((time.perf_counter() - started) * 1000)
    results = {"files": len(bundle["files"]), "tags": len(prepared.tags),
               "kinds": prepared.summary()["kinds"], "digest": prepared.digest,
               "lossless_export": True, "search_samples": len(samples),
               "search_median_ms": round(statistics.median(samples), 2),
               "search_p95_ms": round(sorted(samples)[37], 2),
               "platform": platform.platform(), "processor": platform.processor()}
    (OUT / "search-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    app = QApplication([])
    apply_application_font()
    messages = []
    qInstallMessageHandler(lambda _kind, _context, message: messages.append(message))
    administrator = ProjectAdministratorDialog(ProjectAdministrator(),
                                               ROOT / "projects/AzeoPlantVirtualController")
    administrator.show()
    browser = administrator.open_configuration_database()
    available = app.primaryScreen().availableGeometry()
    browser.resize(min(1240, available.width() - 40), min(820, available.height() - 60))

    def wait_ready():
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
            if browser._request is None:
                QTest.qWait(50)
                app.processEvents()
                if browser._request is None:
                    return
        raise RuntimeError("Configuration browser did not finish its background request")

    try:
        QTest.mouseClick(browser.connect_button, Qt.LeftButton)
        wait_ready()
        assert browser.table.rowCount() == 200, browser.status.text()
        browser.table.selectRow(0)
        wait_ready()
        assert "Stable repository ID" in browser.details.toPlainText()
        browser.grab().save(str(OUT / "objects.png"))
        browser.tabs.setCurrentIndex(1)
        wait_ready()
        browser.search.setText("PIC-2001")
        QTest.qWait(350)
        wait_ready()
        assert browser.tags.rowCount() > 0
        assert browser.tags.columnWidth(0) >= 280
        browser.tags.selectRow(0)
        assert "PIC-2001" in browser.details.toPlainText()
        browser.grab().save(str(OUT / "tags.png"))
        browser.import_name.setText("APVC Database Pilot")
        QTest.mouseClick(browser.preview_button, Qt.LeftButton)
        wait_ready()
        assert browser.import_button.isEnabled(), browser.status.text()
        assert "Changed (0)" in browser.details.toPlainText()
        browser.grab().save(str(OUT / "preview.png"))
        # Drive a real, idempotent reimport through the reachable UI handler.
        QTest.mouseClick(browser.import_button, Qt.LeftButton)
        wait_ready()
        browser.tabs.setCurrentIndex(2)
        wait_ready()
        assert browser.audit.rowCount() >= 2
        browser.grab().save(str(OUT / "audit.png"))
        assert all(w.font().pointSizeF() > 0 for w in browser.findChildren(QWidget))
        assert not messages, messages
        assert browser.width() <= available.width() and browser.height() <= available.height()
        results.update(native_ui="--native" in sys.argv, ui_import=True, qt_warnings=messages,
                       success=True)
    finally:
        wait_ready()
        browser.close()
        administrator.close()
        app.processEvents()
        (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
