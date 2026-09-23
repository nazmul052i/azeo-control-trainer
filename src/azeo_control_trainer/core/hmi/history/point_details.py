"""Sample provenance is read from the archive asynchronously, never inferred from the current tag."""
from datetime import datetime, timezone
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHeaderView, QLabel, QVBoxLayout

from ...presentation.configuration_editing import BackgroundDialog
from ...presentation.configuration_catalog import _table


class PointDetails(BackgroundDialog):
    def __init__(self, historian, path, start, end, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Historian point configuration")
        self.resize(1100, 650)
        layout = QVBoxLayout(self)
        point = historian.TAGS[path]
        self.status = QLabel(point.path + (" · Retired identity" if not point.active else ""))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        details = QFormLayout()
        for name, value in [("Point identity", point.point_id or "Legacy address history"),
                            ("Module", point.module), ("Current units / range", f"{point.unit} · {point.lo:g} to {point.hi:g}"),
                            ("Loaded configuration", "Revision " + str(point.configuration.get("revision", "unknown"))),
                            ("Release", point.configuration.get("release_id", "Not recorded"))]:
            label = QLabel(str(value))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details.addRow(name, label)
        layout.addLayout(details)
        layout.addWidget(QLabel("Recorded configuration in the selected interval (first and last observed sample, UTC)"))
        self.table, self.model = _table([("path", "Recorded tag"), ("first", "First sample"), ("last", "Last sample"),
                                        ("unit", "Units"), ("span", "Configured range"), ("revision", "Revision"),
                                        ("release", "Release")], "Historian sample provenance")
        for column, width in [(0, 240), (1, 170), (2, 170), (3, 65), (4, 130), (5, 65)]:
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.table)
        if historian.archive:
            future = historian.query_async([path], start, end)
            def loaded(result):
                rows = []
                for epoch in result["provenance"].get(path, []):
                    cfg = epoch["configuration"]
                    rows.append({"path": epoch["path"], "first": self._time(historian.origin + epoch["start"]),
                                 "last": self._time(historian.origin + epoch["end"]), "unit": cfg.get("unit", ""),
                                 "span": f"{cfg.get('lo', '')} – {cfg.get('hi', '')}",
                                 "revision": cfg.get("revision", ""), "release": cfg.get("release_id", "")})
                self.model.set_rows(rows)
                if not rows:
                    self.status.setText(point.path + " · No repository provenance recorded in this interval")
            self.run(lambda: future.result(timeout=60), loaded)
        else:
            self.status.setText(point.path + " · Memory history; current configuration shown above")

        from ..theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    @staticmethod
    def _time(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
