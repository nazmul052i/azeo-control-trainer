"""Record real Qt screen moves separately from simulated scale-factor checks."""
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget


def check_monitor_moves(windows, output):
    screens = QApplication.screens()
    report = {"method": "Native Qt window moves and bounds/font checks; no human usability acceptance",
              "screens": [{"name": screen.name(), "geometry": screen.geometry().getRect(),
                           "available": screen.availableGeometry().getRect(),
                           "dpr": screen.devicePixelRatio()} for screen in screens], "moves": []}
    for name, window in windows:
        original_screen, position, size = window.screen(), window.pos(), window.size()
        try:
            for index, screen in enumerate(screens):
                area = screen.availableGeometry()
                window.windowHandle().setScreen(screen)
                window.resize(min(size.width(), area.width() - 80), min(size.height(), area.height() - 80))
                window.move(area.topLeft() + QPoint(32, 32))
                QTest.qWait(250)
                frame = window.frameGeometry()
                assert window.isVisible(), (name, "Window disappeared during screen change")
                assert window.screen() == screen, (name, index, window.screen().name())
                assert area.contains(frame), (name, area, frame)
                invalid = [type(child).__name__ for child in window.findChildren(QWidget)
                           if child.font().pointSizeF() <= 0]
                assert not invalid, invalid
                path = output / f"monitor-{index}-{name}.png"
                assert window.grab().save(str(path))
                report["moves"].append({"window": name, "screen": screen.name(),
                                        "frame": frame.getRect(), "dpr": window.devicePixelRatioF(),
                                        "capture": path.name, "bounds_and_fonts_passed": True})
        finally:
            window.windowHandle().setScreen(original_screen)
            window.resize(size)
            window.move(position)
    report["multiple_screens_exercised"] = len(screens) > 1
    report["mixed_dpi_exercised"] = len({screen.devicePixelRatio() for screen in screens}) > 1
    return report
