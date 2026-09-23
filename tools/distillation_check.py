"""Visual and binding checks in verify_operator_workspace's disposable plant."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from create_distillation_display import DISPLAY_NAME, plant_document
from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem, PipeItem
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
from azeo_control_trainer.core.presentation import headless


def check_distillation(station, output, report):
    app = QApplication.instance()
    station.refresh_configuration()
    assert station.show_display(DISPLAY_NAME, target_frame=station._all_frame_names()[0])
    available = station.screen().availableGeometry()
    station.resize(min(1900, available.width() - 16), min(1040, available.height() - 32))
    station.show()
    details = report["distillation"] = {}
    studio = None
    authoring = TemporaryDirectory(prefix="azeo-column-authoring-")
    previous_headless = headless.is_headless
    headless.is_headless = lambda: False
    try:
        # Compare identical process values in the disposable simulator. The
        # normal PAUSED status remains visible in the evidence.
        station.simulation_service.pause()
        station.tick()
        for theme in ("silver", "dark", "hpgray"):
            station.apply_theme(theme)
            QTest.qWait(1000)
            station.tick()
            station.view.refresh()
            scene = station.view.scene()
            bad_routes = {i.data["id"]: i.route_message for i in scene.items()
                          if isinstance(i, PipeItem) and i.route_status != "ok"}
            bad_bindings = {i.pvm.id: i.binding.result.quality.name for i in scene.items()
                            if isinstance(i, PvmItem) and i.binding.result.quality.name != "GOOD"}
            assert station.grab().save(str(output / f"distillation-{theme}.png"))
            details[theme] = dict(routes=bad_routes, bindings=bad_bindings,
                                  source_state=station.status.status.liveness())
        assert not any(v["routes"] or v["bindings"] for v in details.values()), details
        station.apply_theme("silver")
        for ident in ("pressure_fan", "column", "reflux_pump"):
            pvm = next(g for g in plant_document().pvms if g.id == ident)
            station.open_faceplate(pvm)
            QTest.qWait(500)
            face = station.faceplates[-1][1]
            assert face.isVisible() and face.bound
            face.grab().save(str(output / f"faceplate-{ident}.png"))
            face.close()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        DisplayStore(Path(authoring.name)).save_draft(plant_document())
        studio = PvmStudio(station.graphs_provider, Path(authoring.name), display_name=DISPLAY_NAME)
        studio.resize(1500, 900)
        studio.show()
        studio.enter_edit()
        studio.fit_drawing()
        app.processEvents()
        assert studio._document()["pvms"], "The graphic must remain editable PVMs"
        assert studio.display.name == DISPLAY_NAME and studio.display.level == 2
        assert studio.save_draft()
        studio.grab().save(str(output / "distillation-studio.png"))
        details["faceplates_and_authoring"] = True
    finally:
        headless.is_headless = previous_headless
        if studio is not None:
            studio.unsaved = False
            studio.close()
            studio.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        authoring.cleanup()
