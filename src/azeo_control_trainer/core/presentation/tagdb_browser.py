"""Tag database browser — the view for ``strategy/tagdb.py``.

The model derives every addressable point by walking the loaded modules; until
now nothing showed it. This is that view: search, filter by kind, and a
cross-reference saying who writes a point and who reads it.

It is shared by Control Designer, PA Designer and the memory-tag dialog,
which is why it lives in ``core/presentation`` rather than inside a product.

It is deliberately the same widget an HMI binding picker needs. Choosing a
binding is choosing a point out of this namespace, and the thing that makes
that cheap — unit, range, choices, and whether the outside world may write it
— is already on every entry.

Two details are load-bearing:

* **Values are read live where they can be.** The tree is built from the
  database, but a terminal's value is re-read from the block on each refresh,
  because the database's value is a snapshot from build time and a watch
  window that lies is worse than none.
* **Writable is shown, not inferred.** An input block's field tag is writable
  from outside; an output block's is driven by the controller. That
  distinction is what stops an HMI command binding pointing at a valve the
  controller owns, so the browser states it per entry.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase
from azeo_control_trainer.core.presentation.headless import is_headless

log = logging.getLogger("ui.tagdb_browser")

_PATH_ROLE = Qt.UserRole + 1
_LAZY_ROLE = Qt.UserRole + 2

# QTreeWidget owns a native object for every visible row.  The plant project
# carries more than 31,000 points, and constructing that entire native tree in
# the ribbon click handler made the whole application appear to crash (and put
# enough pressure on Qt's Windows heap to destabilise shutdown).  Large result
# sets therefore expose their hierarchy lazily; a deliberate text search is
# bounded as well, because "a" is otherwise just another request for 31k rows.
_LAZY_POINT_THRESHOLD = 5_000
_SEARCH_RESULT_LIMIT = 2_000
_LAZY_MODULE = "module"
_LAZY_BLOCK = "block"
_LAZY_FIELD_IO = "field-io"

#: Kind filter entries, in the order the wireframe lists them.
_KINDS = (
    ("all", None),
    ("field", EntryKind.FIELD),
    ("terminal", EntryKind.TERMINAL),
    ("parameter", EntryKind.PARAMETER),
    ("module", EntryKind.MODULE),
    ("memory", EntryKind.MEMORY),
)

#: Rebuilding the tree on every keystroke is wasted work on 1,377 points.
_SEARCH_DEBOUNCE_MS = 200

#: Live value refresh. Matches the designer's own 1 Hz tick.
_REFRESH_MS = 1000

_STYLE = f"""
QWidget {{ background: {UI.chrome}; color: {UI.blue}; }}
QTreeWidget {{
    background: #FFFFFF; border: 1px solid {UI.disabled};
    font-size: 9pt; alternate-background-color: #F4F6FA;
}}
QTreeWidget::item {{ padding: 1px 2px; }}
QTreeWidget::item:selected {{ background: {UI.selection}; color: {UI.blue}; }}
QHeaderView::section {{
    background: {UI.chrome_alt}; color: {UI.blue}; border: none;
    border-right: 1px solid {UI.border}; padding: 3px; font-size: 9pt;
}}
QLineEdit, QComboBox {{
    background: #FFFFFF; border: 1px solid {UI.disabled}; border-radius: 2px;
    padding: 2px 4px; font-size: 9pt;
}}
QPushButton {{
    background: #D8DBE4; border: 1px solid {UI.disabled}; border-radius: 3px;
    padding: 3px 10px; font-size: 9pt;
}}
QPushButton:hover {{ background: {UI.selection}; }}
QLabel {{ font-size: 9pt; }}
"""


def _fmt(value) -> str:
    """Render a point's value the way a watch window would."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


class TagDatabaseBrowser(QWidget):
    """Browsable, searchable view of one area's addressable namespace."""

    #: Emitted when the operator double-clicks a point — the hook a binding
    #: picker connects to.
    pathActivated = Signal(str)
    selectedPathChanged = Signal(str)

    def __init__(self, graphs_provider=None, store=None, area_name: str = "",
                 parent=None):
        super().__init__(parent)
        self._graphs_provider = graphs_provider
        self._store = store
        self._area_name = area_name
        self._db: TagDatabase | None = None
        #: path -> live terminal, for the value refresh.
        self._live: dict[str, object] = {}
        #: path -> tree item, for the same.
        self._rows: dict[str, QTreeWidgetItem] = {}
        #: module -> block -> entries, retained only while the current view is
        #: lazy.  The database already owns the entries; these are references.
        self._lazy_groups: dict[str, dict[str, list]] = {}
        self._title_base = "Tag Database"

        self.setStyleSheet(_STYLE)
        self._build_ui()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._populate)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_values)

        self.reload()

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        self._title = QLabel("Tag Database")
        self._title.setStyleSheet(
            f"QLabel {{ font-size: 10pt; font-weight: bold; color: {UI.blue}; }}")
        root.addWidget(self._title)

        bar = QHBoxLayout()
        bar.setSpacing(5)
        bar.addWidget(QLabel("Search"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("path, description or I/O tag…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(
            lambda _: self._search_timer.start(_SEARCH_DEBOUNCE_MS))
        bar.addWidget(self._search, 1)

        bar.addWidget(QLabel("Kind"))
        self._kind = QComboBox()
        for label, _ in _KINDS:
            self._kind.addItem(label)
        self._kind.currentIndexChanged.connect(lambda _: self._populate())
        bar.addWidget(self._kind)

        btn_reload = QPushButton("Reload")
        btn_reload.setToolTip("Re-derive the database from the open modules")
        btn_reload.clicked.connect(self.reload)
        bar.addWidget(btn_reload)

        self.new_memory_button = QPushButton("New memory tag…")
        self.new_memory_button.clicked.connect(self.new_memory)
        bar.addWidget(self.new_memory_button)

        btn_csv = QPushButton("Export CSV")
        btn_csv.clicked.connect(self._export)
        bar.addWidget(btn_csv)
        root.addLayout(bar)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Path", "Kind", "Value", "Unit"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 80), (2, 120), (3, 60)):
            self._tree.header().setSectionResizeMode(col, QHeaderView.Fixed)
            self._tree.setColumnWidth(col, width)
        self._tree.currentItemChanged.connect(self._on_selected)
        self._tree.itemDoubleClicked.connect(self._on_activated)
        self._tree.itemExpanded.connect(self._on_expanded)
        root.addWidget(self._tree, 1)

        self._detail = QLabel("Select a point.")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail.setMinimumHeight(76)
        self._detail.setAlignment(Qt.AlignTop)
        self._detail.setStyleSheet(
            f"QLabel {{ background: #FFFFFF; border: 1px solid {UI.disabled};"
            " border-radius: 2px; padding: 5px; font-size: 9pt; }")
        root.addWidget(self._detail)

    # ---------------------------------------------------------- the model
    def _open_graphs(self) -> list:
        if self._graphs_provider is None:
            return []
        try:
            return list(self._graphs_provider())
        except Exception:                                   # noqa: BLE001
            log.exception("Tag browser: could not collect open modules")
            return []

    def reload(self) -> None:
        """Re-derive the database, preferring the modules that are open.

        An open module's blocks are the live ones — the compiler does not copy
        them — so building from those is what makes the values real. With
        nothing open it falls back to the database built at launch.
        """
        graphs = self._open_graphs()
        if graphs:
            self._db = TagDatabase.from_graphs(graphs, self._area_name)
            source = f"{len(graphs)} open module(s)"
        else:
            self._db = getattr(self._store, "tagdb", None)
            source = "the area, as loaded at launch"
        if self._db is None:
            self._db = TagDatabase(self._area_name)
            source = "nothing — no modules are open"

        shared = getattr(self._store, "tagdb", None)
        if shared is not None and shared.memory is not None:
            self._db.memory = shared.memory
        self._db.reload_memory()
        self.new_memory_button.setEnabled(self._db.memory is not None)

        self._index_live(graphs)
        self._title_base = (
            f"Tag Database — {self._db.area_name or 'unnamed area'}"
            f"          {len(self._db)} points")
        self._title.setText(self._title_base)
        self._title.setToolTip(f"Derived from {source}")
        self._populate()

    def new_memory(self):
        from azeo_control_trainer.core.presentation.memory_tag_dialog import MemoryDialog
        self.memory_dialog = MemoryDialog(self._db.memory, self)
        self.memory_dialog.accepted.connect(self.reload)
        self.memory_dialog.open()

    def _index_live(self, graphs) -> None:
        """Map each terminal's path to the terminal object driving its value."""
        self._live.clear()
        for graph in graphs:
            module = graph.name or "UNNAMED"
            for block in graph.blocks.values():
                bname = block.instance_name or block.id
                for terminals in (block.inputs, block.outputs):
                    for tname, term in terminals.items():
                        # An in/out pair shares one path; the output side
                        # carries the block's own computed value, and it is
                        # indexed second, so it wins.
                        self._live[f"{module}/{bname}/{tname}"] = term

    @property
    def database(self) -> TagDatabase | None:
        return self._db

    # -------------------------------------------------------------- view
    def _populate(self) -> None:
        self._tree.setUpdatesEnabled(False)
        # Release borrowed Python wrappers before Qt destroys their native
        # QTreeWidgetItems.  Keeping 30k stale wrappers alive across clear()
        # was another avoidable source of Windows heap pressure on Reload.
        self._rows.clear()
        self._tree.clear()
        self._lazy_groups.clear()

        db = self._db
        if db is None:
            self._tree.setUpdatesEnabled(True)
            return

        kind = _KINDS[self._kind.currentIndex()][1]
        needle = self._search.text().strip()
        entries = db.search(needle, kind) if needle else (
            db.of_kind(kind) if kind else list(db))
        match_count = len(entries)
        lazy = not needle and match_count > _LAZY_POINT_THRESHOLD
        clipped = bool(needle and match_count > _SEARCH_RESULT_LIMIT)
        if clipped:
            entries = entries[:_SEARCH_RESULT_LIMIT]

        # module -> block -> [entry]; module entries hang off the module node.
        tree: dict[str, dict[str, list]] = {}
        for e in entries:
            tree.setdefault(e.module, {}).setdefault(e.block, []).append(e)

        if lazy:
            self._lazy_groups = tree
        for module in sorted(tree):
            mod_item = self._module_item(module, tree[module], lazy=lazy)
            self._tree.addTopLevelItem(mod_item)

        # A search is a request to see the hits, not to go hunting for them.
        if needle:
            self._tree.expandAll()
        if lazy:
            suffix = "          expand a module to browse"
        elif clipped:
            suffix = (f"          showing {_SEARCH_RESULT_LIMIT} of "
                      f"{match_count} matches; refine Search")
        elif needle or kind:
            suffix = f"          {match_count} match(es)"
        else:
            suffix = ""
        self._title.setText(self._title_base + suffix)
        self._tree.setUpdatesEnabled(True)
        self.refresh_values()

    def _module_item(self, module: str, groups: dict[str, list], *,
                     lazy: bool) -> QTreeWidgetItem:
        """Create one module row, optionally deferring its native children."""
        label = module or "Configured Field I/O"
        mod_item = QTreeWidgetItem([label, "", "", ""])
        mod_item.setData(0, _PATH_ROLE, module)
        font = QFont(self._tree.font())
        font.setBold(True)
        mod_item.setFont(0, font)
        self._rows[module] = mod_item

        # EIOC channels not referenced by a module intentionally have no
        # module/block identity.  They are still first-class FIELD entries,
        # not a mysterious blank module row.
        if not module:
            entries = groups.get("", ())
            if lazy and entries:
                mod_item.setData(0, _LAZY_ROLE, _LAZY_FIELD_IO)
                QTreeWidgetItem(mod_item, ["…", "", "", ""])
            else:
                self._populate_entries(mod_item, entries)
            return mod_item

        # The module's own entry describes the parent row; it is not a child.
        for entry in groups.get("", ()):
            mod_item.setText(1, entry.kind)
        if lazy and any(groups.get(block) for block in groups if block):
            mod_item.setData(0, _LAZY_ROLE, _LAZY_MODULE)
            QTreeWidgetItem(mod_item, ["…", "", "", ""])
        else:
            self._populate_blocks(mod_item, module, groups, lazy=False)
        return mod_item

    def _populate_blocks(self, parent: QTreeWidgetItem, module: str,
                         groups: dict[str, list], *, lazy: bool) -> None:
        for block in sorted(groups):
            if not block:
                continue
            blk_item = QTreeWidgetItem([block, "", "", ""])
            path = f"{module}/{block}"
            blk_item.setData(0, _PATH_ROLE, path)
            parent.addChild(blk_item)
            self._rows[path] = blk_item
            if lazy:
                blk_item.setData(0, _LAZY_ROLE, _LAZY_BLOCK)
                QTreeWidgetItem(blk_item, ["…", "", "", ""])
            else:
                self._populate_entries(blk_item, groups[block])

    def _populate_entries(self, parent: QTreeWidgetItem, entries: list) -> None:
        for entry in sorted(entries, key=lambda value: (value.kind, value.name)):
            label = (f"tag  {entry.io_tag}" if entry.kind == EntryKind.FIELD
                     else f"/{entry.name}")
            row = QTreeWidgetItem(
                [label, entry.kind, _fmt(entry.value), entry.unit])
            row.setData(0, _PATH_ROLE, entry.path)
            if not entry.writable:
                row.setToolTip(
                    1, "Driven by the controller — not writable from outside")
            parent.addChild(row)
            self._rows[entry.path] = row

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        """Materialise only the branch the engineer chose to inspect."""
        lazy_kind = item.data(0, _LAZY_ROLE)
        if not lazy_kind:
            return
        item.setData(0, _LAZY_ROLE, None)
        item.takeChildren()
        path = item.data(0, _PATH_ROLE) or ""
        if lazy_kind == _LAZY_FIELD_IO:
            entries = self._lazy_groups.get("", {}).get("", ())
            self._populate_entries(item, entries)
            return
        if lazy_kind == _LAZY_MODULE:
            groups = self._lazy_groups.get(path, {})
            self._populate_blocks(item, path, groups, lazy=True)
            return
        if lazy_kind == _LAZY_BLOCK:
            module, _separator, block = path.partition("/")
            entries = self._lazy_groups.get(module, {}).get(block, ())
            self._populate_entries(item, entries)
            self.refresh_values()

    def refresh_values(self) -> None:
        """Re-read live terminal values into the visible rows."""
        if self._db and self._db.memory:
            try:
                for path, value in self._db.memory.read_many().items():
                    if path in self._rows:
                        self._rows[path].setText(2, _fmt(value))
            except Exception:
                log.exception("Tag browser: memory database unavailable")
                for path, item in self._rows.items():
                    if path.startswith("MEMORY/"):
                        item.setText(2, "Unavailable")
        if not self._live:
            return
        for path, item in self._rows.items():
            term = self._live.get(path)
            if term is None:
                continue
            try:
                item.setText(2, _fmt(term.value))
            except RuntimeError:
                # The item was taken out from under us by a repopulate.
                return

    # ---------------------------------------------------------- selection
    def _on_selected(self, item, _previous=None) -> None:
        self.selectedPathChanged.emit(item.data(0, _PATH_ROLE) or "" if item else "")
        if item is None or self._db is None:
            return
        path = item.data(0, _PATH_ROLE)
        entry = self._db.lookup(path) if path else None
        if entry is None:
            self._detail.setText(path or "")
            return

        bits = [f"<b>{entry.path}</b>"]
        if entry.description:
            bits.append(entry.description)
        facts = [entry.kind]
        if entry.block_type:
            facts.append(entry.block_type)
        if entry.data_type:
            facts.append(entry.data_type)
        if entry.direction:
            facts.append(entry.direction)
        facts.append("writable" if entry.writable else "read-only")
        if entry.io_tag:
            facts.append(f"field tag {entry.io_tag}")
        if entry.unit:
            facts.append(f"unit {entry.unit}")
        if entry.choices:
            facts.append("choices: " + ", ".join(str(c) for c in entry.choices))
        bits.append(" · ".join(facts))

        written, read = self._db.references(path)
        bits.append(
            f"Written by: {', '.join(written) if written else '—'}"
            f" &nbsp;&nbsp; Read by: {', '.join(read) if read else '—'}")
        self._detail.setText("<br>".join(bits))

    def _on_activated(self, item, _column=0) -> None:
        path = item.data(0, _PATH_ROLE)
        if path:
            self.pathActivated.emit(path)

    # ------------------------------------------------------------ export
    def _export(self) -> None:
        if self._db is None or not len(self._db):
            return
        default = f"{self._db.area_name or 'tagdb'}_tags.csv"
        if is_headless():
            log.info("Tag browser: headless, not prompting for an export path")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export tag database", default, "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        try:
            written = self._db.save(path)
        except Exception as exc:                            # noqa: BLE001
            log.exception("Tag database export failed")
            QMessageBox.warning(self, "Export", f"Could not write {path}:\n{exc}")
            return
        log.info("Tag database exported to %s (%d points)", written, len(self._db))
        QMessageBox.information(
            self, "Export", f"{len(self._db)} points written to\n{written}")

    # ---------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._refresh_timer.start(_REFRESH_MS)

    def stop(self) -> None:
        self._refresh_timer.stop()


class TagDatabaseDialog(QDialog):
    """Non-modal window hosting :class:`TagDatabaseBrowser`."""

    def __init__(self, graphs_provider=None, store=None, area_name: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tag Database")
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint |
                            Qt.WindowMinMaxButtonsHint)
        self.resize(760, 620)
        self.browser = TagDatabaseBrowser(
            graphs_provider, store, area_name, parent=self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.browser, "Live / open modules")
        self.catalog = None
        self._catalog_host = QWidget(self)
        self._catalog_layout = QVBoxLayout(self._catalog_host)
        self._catalog_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(self._catalog_host, "Shared configuration")
        self.tabs.currentChanged.connect(self._activate_catalog)
        lay.addWidget(self.tabs)

    def _activate_catalog(self, index):
        if index != 1 or self.catalog is not None:
            return
        # Do not build hidden model/view trees during the live browser's startup
        # polish. In long Qt sessions that deferred work collided with Studio's
        # next inspector layout pass and terminated the process in native Qt.
        from azeo_control_trainer.core.presentation.configuration_catalog import (
            context_root,
        )
        from azeo_control_trainer.core.presentation.configuration_workspace import ConfigurationWorkspace
        self.configuration_workspace = ConfigurationWorkspace(context_root(self.parentWidget()), self)
        self.catalog = self.configuration_workspace.browser
        self._catalog_layout.addWidget(self.configuration_workspace)

    def showEvent(self, event):                             # noqa: N802
        super().showEvent(event)
        self.browser.start()

    def closeEvent(self, event):                            # noqa: N802
        if self.catalog is not None and not self.configuration_workspace.close_pages():
            event.ignore()
            return
        self.browser.stop()
        super().closeEvent(event)
