"""Exercise the four reported loops in the verifier's disposable live station."""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from azeo_control_trainer.core.hmi.pvms.base import registry


def check_faceplate_feedback(station, output):
    assert station.show_display("U300 - L2 Charge Heating")
    names = ("TIC-3001", "FIC-3001", "AIC-3001", "FIC-3003")
    windows = []
    blocks = []
    graphs = station.graphs_provider()
    report = {"module_count": len(graphs), "observations": []}
    for name in names:
        station.open_faceplate(registry.get("PID", "faceplate")().place(name, path=f"{name}/{name}"))
        window = station.faceplates[-1][1]
        window.toggle_pin()
        window.show()
        windows.append(window)
        blocks.append(next(block for block in graphs[name].blocks.values() if block.instance_name == name))
    assert len(station.faceplates) == 4

    def wait_for(predicate, reason):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(50)
        assert predicate(), reason

    def capture(stage):
        for name, block, window in zip(names, blocks, windows):
            surface = window.visual
            report["observations"].append({
                "stage": stage, "loop": name, "actual": block.mode_actual,
                "target": block.mode_target, "painted_actual": surface.state.actual_mode,
                "painted_target": surface.state.target_mode,
                "sp": surface.state.sp, "out": surface.state.output,
                "out_range": [surface.state.output_min, surface.state.output_max],
                "native_window": window.isWindow(), "pinned": window.pinned,
            })
            window.grab().save(str(output / f"{stage}-{name}.png"))

    wait_for(lambda: all(w.visual.state.actual_mode == b.mode_actual for b, w in zip(blocks, windows)),
             "Initial mode feedback did not arrive")
    capture("initial")
    for mode, window in zip(("AUTO", "CAS", "AUTO", "MAN"), windows):
        point = window.visual.actual_mode_arrow_geometry().center()
        QTest.mouseClick(window.visual, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        action = next(action for action in window._mode_menu.actions() if action.text() == mode)
        assert action.isEnabled()
        action.trigger()
    modes = ("AUTO", "CAS", "AUTO", "MAN")
    wait_for(lambda: all(w.visual.state.target_mode == mode and w.visual.state.actual_mode == b.mode_actual
                         and b.mode_target == mode for mode, b, w in zip(modes, blocks, windows)),
             "Mode commands did not reach the live faceplate feedback")
    # Actual may legitimately differ from target under cascade initialization
    # or tracking; compare with the controller, never invent requested feedback.
    assert windows[0].visual.state.actual_mode != "MAN"
    capture("modes")
    initial_sp = round(windows[0].visual.state.sp, 1)
    # TIC-3001 deliberately slews SP at 0.5 EU/s. Small moves exercise that
    # behavior without mistaking a correct configured ramp for a frozen UI.
    for index, value in enumerate((initial_sp - .5, initial_sp + .5)):
        assert windows[0].write_bound("sp.value", value)
        wait_for(lambda: abs(windows[0].visual.state.sp - value) < .01,
                 f"SP did not settle at {value}")
        QTest.qWait(800)
        assert abs(windows[0].visual.state.sp - value) < .01, "SP snapped back on a later scan"
        capture(f"sp-{index}")
    for index, value in enumerate((30.0, 55.1, 80.0)):
        assert windows[3].write_bound("out.value", value)
        wait_for(lambda: abs(windows[3].visual.state.output - value) < .01,
                 f"OUT did not settle at {value}")
        capture(f"out-{index}")
    report["passed"] = True
    return report
