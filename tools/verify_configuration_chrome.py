"""Read-only native acceptance of the shared Configuration workspace."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows"


def main():
    from PySide6.QtCore import Qt, qInstallMessageHandler
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation.configuration_catalog import ConfigurationCatalogDialog, _shutdown

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_application_font()
    warnings = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    out = ROOT / "logs/configuration-chrome-ui"
    out.mkdir(parents=True, exist_ok=True)

    def wait(predicate, label):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.015)
        raise AssertionError(label + " timed out")

    dialog = ConfigurationCatalogDialog(ROOT / "projects/AzeoPlantVirtualController")
    dialog.show()
    host = dialog.workspace
    wait(lambda: dialog.browser._started and not host.busy(), "initial load")
    for i in reversed(range(host.projects.count())):
        if host.projects.itemText(i).startswith("Training Recovery ") and "source" not in host.projects.itemText(i):
            host.projects.setCurrentIndex(i)
            host.projects.activated.emit(i)
            break
    wait(lambda: not host.busy(), "project selection")
    assert host.browser.index, host.browser.status.text()
    for name, selector in (("project-menu", host.projects), ("category-menu", host.browser.kind)):
        selector.showPopup()
        app.processEvents()
        assert selector.view().window().grab().save(str(out / (name + ".png")))
        selector.hidePopup()
        app.processEvents()
    host.browser.search.setText("FIC-0101")
    wait(lambda: host.browser.result_model.rowCount() > 0, "catalog search")
    host.browser.results.selectRow(0)
    for name, menu in host.menu_bar.menus.items():
        QTest.mouseClick(host.menu_bar, Qt.LeftButton, pos=host.menu_bar.actionGeometry(menu.menuAction()).center())
        app.processEvents()
        assert menu.isVisible(), name
        assert menu.grab().save(str(out / ("menu-" + name.lower() + ".png")))
        menu.close()
        app.processEvents()

    def context(table, key):
        if table.model().rowCount() == 0:
            return
        table.setFocus()
        table.selectRow(0)
        point = table.visualRect(table.model().index(0, 0)).center()
        event = QContextMenuEvent(QContextMenuEvent.Mouse, point, table.viewport().mapToGlobal(point))
        QApplication.sendEvent(table.viewport(), event)
        controller = next(c for c in table.parent().findChildren(type(host.browser._configuration_table_menus[host.browser.results]))
                          if c.table is table)
        assert controller.menu.isVisible(), key
        assert controller.menu.grab().save(str(out / ("context-" + key + ".png")))
        action = next(a for a in controller.menu.actions() if a.text() == "Copy row")
        action.trigger()
        assert QApplication.clipboard().text()
        controller.close()
        app.processEvents()

    metrics = {}
    for key in ("catalog", "changes", "releases", "libraries", "training", "recovery", "capture"):
        page = host.open_page(key)
        assert page is not None, key
        wait(lambda: not host.busy(), key)
        host._update_busy()
        if key == "releases":
            assert page.target.currentData() or not page.target.count()
            page.target.showPopup()
            app.processEvents()
            assert page.target.view().window().grab().save(str(out / "runtime-menu.png"))
            page.target.hidePopup()
            app.processEvents()
            if page.objects.rowCount():
                assert not page.review_button.isEnabled()
                page.objects.item(0, 0).setCheckState(Qt.Checked)
                assert page.review_button.isEnabled()
            context(page.objects, "release-objects")
            assert dialog.grab().save(str(out / "build-release.png"))
            page.tabs.setCurrentIndex(2)
        elif key == "training" and page.model.rows:
            page.table.selectRow(0)
        elif key == "recovery" and page.backup_model.rows:
            page.backups.selectRow(0)
        tables = {"catalog": "results", "changes": "table", "releases": "comparison", "libraries": "instances",
                  "training": "table", "recovery": "backups", "capture": "table"}
        context(getattr(page, tables[key]), key)
        app.processEvents()
        assert dialog.grab().save(str(out / (key + ".png")))
        if key == "training":
            review = page.new_baseline()
            app.processEvents()
            assert review.grab().save(str(out / "baseline-review.png"))
            assert not review.create_button.isEnabled()
            review.close()
            page.run(lambda: time.sleep(.4), lambda _: None)
            wait(lambda: page._progress._phase > .1, "smooth loading line")
            assert dialog.grab().save(str(out / "loading.png"))
            wait(lambda: not host.busy(), "loading completed")
        elif key == "recovery" and page.restore_model.rows:
            page.tabs.setCurrentIndex(1)
            page.restores.selectRow(0)
            app.processEvents()
            assert dialog.grab().save(str(out / "verified-restore.png"))
        metrics[key] = {"minimum": [dialog.minimumSizeHint().width(), dialog.minimumSizeHint().height()],
                        "page": [page.width(), page.height()],
                        "buttons": len(page.findChildren(QPushButton)), "detached": page.isWindow()}
        assert not page.isWindow(), key
    normal_size = dialog.size()
    dialog.resize(1024, 720)
    for key in ("catalog", "releases", "libraries", "training", "recovery", "capture"):
        page = host.open_page(key)
        wait(lambda: not host.busy(), "compact " + key)
        app.processEvents()
        assert dialog.width() <= 1024 and dialog.height() <= 720, (key, dialog.size())
        assert dialog.grab().save(str(out / (key + "-compact.png")))
    dialog.resize(normal_size)
    host.open_page("catalog")
    for i in range(host.browser.tabs.count()):
        host.browser.tabs.setCurrentIndex(i)
        app.processEvents()
        assert host.browser.tabs.currentWidget().isVisible()
    host.browser.tabs.setCurrentIndex(0)
    category = host.browser.kind
    category.setFocus()
    QTest.keyClick(category, Qt.Key_Down)
    assert category.currentIndex() == 1
    QTest.keyClick(category, Qt.Key_Up)
    assert category.currentIndex() == 0
    assert host.browser.search.text() == "FIC-0101"
    host.browser.results.selectRow(0)
    assert host.browser.results.currentIndex().isValid()
    for widget in dialog.findChildren(QWidget):
        assert widget.font().pointSizeF() > 0, widget.metaObject().className()
    assert dialog.close()
    _shutdown()
    app.processEvents()
    (out / "report.json").write_text(json.dumps({"pages": metrics, "qt_messages": warnings}, indent=2), encoding="utf-8")
    assert not warnings, warnings
    print("PASS: seven retained pages, one project context, native chrome and clean Qt log", flush=True)


if __name__ == "__main__":
    main()
