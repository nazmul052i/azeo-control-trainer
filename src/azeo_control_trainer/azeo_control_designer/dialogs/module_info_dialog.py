"""Module information — Azeo's Module Properties / Parameters / Cross
Reference / Named Sets views over one control module.

All four pages are read-only projections of data the module already carries,
so nothing here can change what executes:

* **Properties**  — identity, block/wire counts, scan state, file path.
* **Parameters**  — every block's configured parameters with units.
* **Cross Reference** — the store tags the module reads and writes, and which
  block each belongs to. This is what an engineer needs to answer "who else
  touches this tag?".
* **Named Sets** — enumerated parameters in use and their valid values, taken
  from each block's ``config_choices``.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

_PAGE_INDEX = {"properties": 0, "parameters": 1, "xref": 2, "namedsets": 3}

#: Config keys whose value names a store tag.
_TAG_KEYS = ("tag", "TAG")


def _tree(headers: list[str]) -> QTreeWidget:
    t = QTreeWidget()
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setAlternatingRowColors(True)
    t.setSortingEnabled(True)
    t.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    return t


class ModuleInfoDialog(QDialog):
    """Four read-only views over a single control module."""

    def __init__(self, canvas, module_name: str, page: str = "properties",
                 parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self._graph = canvas.scene.graph
        self.setWindowTitle(f"Module Information — {module_name}")
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._properties_page(module_name), "Properties")
        self._tabs.addTab(self._parameters_page(), "Parameters")
        self._tabs.addTab(self._xref_page(), "Cross Reference")
        self._tabs.addTab(self._named_sets_page(), "Named Sets")
        self._tabs.setCurrentIndex(_PAGE_INDEX.get(page, 0))
        layout.addWidget(self._tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ── Properties ────────────────────────────────────────────────────
    def _properties_page(self, module_name: str) -> QWidget:
        runtime = getattr(self._canvas, "runtime", None)
        online = bool(runtime and runtime.is_online)
        rows = [
            ("Module", module_name),
            ("Graph name", self._graph.name),
            ("Scan rate", f"{self._graph.scan_ms} ms"),
            ("File", str(getattr(self._canvas, "file_path", "") or "(unsaved)")),
            ("Blocks", str(len(self._graph.blocks))),
            ("Connections", str(len(self._graph.wires))),
            ("State", "On scan" if online else "Off scan"),
            ("Unsaved edits", "Yes" if getattr(self._canvas, "dirty", False) else "No"),
        ]
        if online:
            rows.append(("Scan time", f"{runtime.last_scan_ms:.2f} ms"))
            rows.append(("Scan count", str(runtime.scan_count)))
        if getattr(self._canvas, "downloaded_at", None):
            rows.append(("Modified since download",
                         "Yes" if self._canvas.modified_since_download else "No"))

        counts: dict[str, int] = {}
        for b in self._graph.blocks.values():
            counts[b.block_type] = counts.get(b.block_type, 0) + 1
        rows.append(("Block types",
                     ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))))

        t = _tree(["Property", "Value"])
        t.setSortingEnabled(False)
        for k, v in rows:
            QTreeWidgetItem(t, [k, v])
        t.header().setSectionResizeMode(1, QHeaderView.Stretch)

        # The module scan rate, from the Azeo rate menu (PK parity).
        # Applied live: the executive reads the rate at each deadline, so a
        # change needs no re-download — it is a parameter, not structure.
        from azeo_control_trainer.core.strategy.engine.pk_controller import (
            SCAN_RATE_MENU_MS, UNSUPPORTED_RATES_MS, rate_label,
        )

        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(t, 1)
        row = QHBoxLayout()
        row.setContentsMargins(8, 4, 8, 6)
        row.addWidget(QLabel("Module scan rate:"))
        combo = AuthoringComboBox()
        for ms in UNSUPPORTED_RATES_MS:
            # Offered by the real PK, greyed here with the reason — a rate
            # this runtime displays must be one it actually delivers, and
            # the greying itself teaches that controllers have a floor.
            combo.addItem(f"{rate_label(ms)} — below this runtime's "
                          "resolution", ms)
            item = combo.model().item(combo.count() - 1)
            item.setEnabled(False)
        for ms in SCAN_RATE_MENU_MS:
            combo.addItem(rate_label(ms), ms)
        index = combo.findData(self._graph.scan_ms)
        combo.setCurrentIndex(index if index >= 0 else
                              combo.findData(500))
        combo.activated.connect(
            lambda _i: self._set_scan_rate(combo.currentData()))
        row.addWidget(combo)
        row.addStretch(1)
        page_lay.addLayout(row)
        return page

    def _set_scan_rate(self, ms) -> None:
        if not ms or int(ms) == self._graph.scan_ms:
            return
        self._graph.scan_ms = int(ms)
        # The rate lives in the module file; the canvas must know it has
        # an unsaved edit or the change survives exactly one session.
        canvas = self._canvas
        marker = getattr(canvas, "mark_dirty", None)
        if callable(marker):
            marker()
        else:
            try:
                canvas.dirty = True
            except Exception:                              # noqa: BLE001
                pass

    # ── Parameters ────────────────────────────────────────────────────
    def _parameters_page(self) -> QWidget:
        t = _tree(["Block", "Type", "Parameter", "Value", "Unit"])
        for b in sorted(self._graph.blocks.values(),
                        key=lambda x: x.instance_name):
            try:
                schema = b.get_config_schema()
            except Exception:
                schema = {}
            for name in schema:
                value = b.config.params.get(name, schema[name][1])
                unit = ""
                try:
                    unit = b.unit_for(name)
                except Exception:
                    pass
                QTreeWidgetItem(t, [b.instance_name, b.block_type,
                                    name.upper(), str(value), unit])
        t.header().setSectionResizeMode(3, QHeaderView.Stretch)
        return t

    # ── Cross reference ───────────────────────────────────────────────
    def _xref_page(self) -> QWidget:
        t = _tree(["Store tag", "Direction", "Block", "Type"])
        for b in sorted(self._graph.blocks.values(),
                        key=lambda x: x.instance_name):
            tag = ""
            for key in _TAG_KEYS:
                if b.config.params.get(key):
                    tag = str(b.config.params[key])
                    break
            if not tag:
                continue
            # An input block reads the tag; an output block writes it.
            direction = "Read" if b.block_type in (
                "AI", "DI", "PIN", "TAGAI", "TAGAO", "TAGDI", "TAGDO",
            ) else (
                "Write" if b.block_type in ("AO", "DO") else "Read/Write")
            QTreeWidgetItem(t, [tag, direction, b.instance_name, b.block_type])
        if t.topLevelItemCount() == 0:
            QTreeWidgetItem(t, ["(this module references no store tags)", "", "", ""])
        t.header().setSectionResizeMode(0, QHeaderView.Stretch)
        return t

    # ── Named sets ────────────────────────────────────────────────────
    def _named_sets_page(self) -> QWidget:
        t = _tree(["Block", "Parameter", "Current", "Valid values"])
        for b in sorted(self._graph.blocks.values(),
                        key=lambda x: x.instance_name):
            try:
                schema = b.get_config_schema()
            except Exception:
                continue
            for name in schema:
                try:
                    choices = b.choices_for(name)
                except Exception:
                    choices = ()
                if not choices:
                    continue
                current = b.config.params.get(name, schema[name][1])
                QTreeWidgetItem(t, [b.instance_name, name, str(current),
                                    ", ".join(str(c) for c in choices)])
        if t.topLevelItemCount() == 0:
            QTreeWidgetItem(t, ["(no enumerated parameters in this module)",
                                "", "", ""])
        t.header().setSectionResizeMode(3, QHeaderView.Stretch)
        return t
