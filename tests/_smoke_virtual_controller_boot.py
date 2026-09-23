"""Bounded live-Station boot of the real APVC application.

This is the safe alternative to launching the GUI and sending Ctrl+C from a
test: Qt requests its own orderly shutdown, so provider leases and threads run
through the same cleanup path as a normal window close.  A process-local hard
watchdog exists only for failures before the Qt event loop becomes responsive.

Run::

    D:\\development\\GitHub\\vpy\\Scripts\\python.exe \
        tests\\_smoke_virtual_controller_boot.py
"""
from __future__ import annotations

import os
import logging
import sys
import threading
import time
import traceback
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PROJECT = REPO / "projects" / "AzeoPlantVirtualController"
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import azeo_control_trainer.app as trainer_app

    captured = {}
    attach = trainer_app._attach_field_io

    def _capturing_attach(store, area):
        driver = attach(store, area)
        captured["driver"] = driver
        # app.main() configures logging after this script's initial setting.
        # Reapply the smoke's quiet threshold before 160 compiles emit their
        # expected BKCAL-cycle engineering warnings.
        logging.disable(logging.WARNING)
        return driver

    trainer_app._attach_field_io = _capturing_attach

    # Arm this only after app.main() reaches QApplication.exec().  Normal Qt
    # close semantics then run app.py's provider/server/discovery teardown.
    qt_exec = QApplication.exec

    def _bounded_exec(_app):
        app = QApplication.instance()
        assert app is not None
        deadline = time.monotonic() + 60.0

        def _quit_after_scan() -> None:
            driver = captured.get("driver")
            runtimes = (driver.store.get_strategy_runtimes()
                        if driver is not None else ())
            station = next(
                (getattr(widget, "_live_station_window", None)
                 for widget in app.topLevelWidgets()
                 if getattr(widget, "_live_station_window", None) is not None),
                None,
            )
            if driver is not None and driver.running:
                captured["provider_seen_running"] = True
                captured["plant_health"] = driver.health().get("provider", {}).get("detail", {})
            if len(runtimes) == 160 and all(runtime.scan_count >= 1
                                            for runtime in runtimes) \
                    and station is not None:
                result = station.live_source.read(
                    "FIC-0101/FT-0102/PV")
                if getattr(result.quality, "name", "") == "GOOD" \
                        and result.module_running:
                    captured["display"] = station.history.current
                    captured["station"] = station
                    captured["binding_value"] = result.value
                    captured["binding_quality"] = result.quality.name
                    try:
                        if "procedure" not in captured:
                            assert "procedures" in {row[0] for row in station.tools_menu()}
                            dialog = station.open_procedures()
                            dialog.loop.setCurrentText("FIC-0101/FIC-0101")
                            captured["procedure"] = dialog
                        dialog = captured["procedure"]
                        dialog.poll()
                        if dialog.run.run_id and not dialog.run.active:
                            raise AssertionError(f"Procedure stopped during faceplate workflow: {dialog._last_finished}: {dialog.status.text()}")
                        if not dialog.run.run_id and dialog.start_button.isEnabled():
                            dialog.start_run()
                        if dialog._prompt is not None:
                            assert dialog._prompt["prompt_kind"] == "confirm"
                            assert dialog.run.active
                            assert all(sample.is_good for sample in dialog.run.connector.snapshot_values().values())
                            if "faceplate_opened" not in captured:
                                dialog.open_faceplate()
                                widget = station.faceplates[-1][1] if station.faceplates else None
                                assert widget is not None and widget.bound, {
                                    "widget": type(widget).__name__,
                                    "type": type(getattr(widget, "pvm", None)).__name__,
                                    "params": getattr(widget, "params", None),
                                    "declared": len(getattr(
                                        getattr(widget, "pvm", None), "bindings", ())),
                                    "faceplates": [
                                        (str(key), type(value).__name__, repr(value))
                                        for key, value in station.faceplates
                                    ],
                                    "view": type(station.view).__name__,
                                    "equipment": dialog.equipment.currentText(),
                                }
                                captured["faceplate_opened"] = time.monotonic()
                            elif time.monotonic() - captured["faceplate_opened"] > 1 and dialog._observation.ready:
                                station.open_procedures()
                                output = REPO / "logs/diagnostics"
                                output.mkdir(parents=True, exist_ok=True)
                                dialog.show()
                                station.grab().save(str(output / "procedures-live-console.png"))
                                dialog.grab().save(str(output / "procedures-live-panel.png"))
                                captured["procedure_run"] = dialog.run
                                app.quit()
                                return
                    except Exception:
                        captured["procedure_error"] = traceback.format_exc()
                        app.exit(1)
                        return
            if time.monotonic() >= deadline:
                app.exit(124)
                return
            QTimer.singleShot(250, _quit_after_scan)

        QTimer.singleShot(250, _quit_after_scan)
        return qt_exec()

    QApplication.exec = _bounded_exec

    def _watchdog_expired() -> None:
        print("[FAIL] APVC app boot exceeded 90 seconds", file=sys.stderr,
              flush=True)
        os._exit(124)

    watchdog = threading.Timer(90.0, _watchdog_expired)
    watchdog.daemon = True
    watchdog.start()
    started = time.perf_counter()
    prior_argv = sys.argv
    prior_disable = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        sys.argv = [
            "azeo-apvc-offscreen",
            str(PROJECT),
            "--station",
        ]
        code = trainer_app.main()
    finally:
        sys.argv = prior_argv
        QApplication.exec = qt_exec
        trainer_app._attach_field_io = attach
        watchdog.cancel()
        logging.disable(prior_disable)

    elapsed = time.perf_counter() - started
    driver = captured.get("driver")
    if code != 0:
        print(f"[FAIL] app returned {code}", captured.get("procedure_error", ""))
        return 1
    if driver is None:
        print("[FAIL] configured field-I/O driver was not attached")
        return 1
    if not captured.get("provider_seen_running"):
        print("[FAIL] dedicated station never started Virtual I/O")
        return 1
    health = captured.get("plant_health", {})
    expected = "python" if os.environ.get("AZEO_NATIVE", "1").lower() in {
        "0", "false", "off", "no"} else "cpp"
    if health.get("core") != expected or (expected == "cpp" and health.get("native_units") != 10):
        print("[FAIL] station did not use the requested plant core", health)
        return 1
    runtimes = driver.store.get_strategy_runtimes()
    if len(runtimes) != 160 or any(runtime.scan_count < 1
                                   for runtime in runtimes):
        counts = [runtime.scan_count for runtime in runtimes]
        print("[FAIL] full project did not reach scan",
              {"runtimes": len(runtimes),
               "min_scans": min(counts, default=0)})
        return 1
    if driver.running or driver._session is not None:
        print("[FAIL] application exit left the provider attached")
        return 1
    leaked = sorted(
        thread.name for thread in threading.enumerate()
        if thread.is_alive()
        and thread.name in {"local-virtual-io", "sim-engine", "azeo-advisory-procedure"}
    )
    if leaked:
        print("[FAIL] application exit leaked provider thread(s)", leaked)
        return 1
    if captured.get("display") != "Overview - L1 Plant":
        print("[FAIL] station did not open its published L1 home display")
        return 1
    procedure_run = captured.get("procedure_run")
    if not getattr(captured.get("station"), "_closing", False):
        print("[FAIL] event-loop exit did not explicitly close Operator Station")
        return 1
    if procedure_run is None or procedure_run.active or procedure_run.store.get_run(procedure_run.run_id)["status"] != "ABORTED":
        print("[FAIL] procedure did not reach operator confirmation and abort cleanly on station exit")
        return 1
    print(
        f"[ok]   APVC Station started {health['core']} Virtual I/O, scanned 160 modules, "
        f"opened {captured['display']!r}, resolved FT-0102="
        f"{captured['binding_value']:.6g} {captured['binding_quality']}, "
        f"started an advisory procedure, and shut down cleanly in {elapsed:.2f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
