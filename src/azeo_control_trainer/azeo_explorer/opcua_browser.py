"""Online OPC UA browsing — the EIOC's signal-configuration flow.

Connect to a live third-party OPC UA server, walk its address space in a
tree, and bind its variables to this area's field tags. Save writes the
binding into `_project.json` under `field_io` (`type: "opcua"`), which is
what `fieldio/opcua_driver.OpcUaLink` reads at launch — the same
readable-contract discipline `modbus_map.py` established for the Modbus
side.

Online browsing only, deliberately: the Azeo EIOC also imports offline
Nodeset files, but a trainer always has the live server to hand and a
second import path is a second thing to drift.

Direction semantics match the tag database's input/output split:

- **read**  — the external server feeds this store tag (a measurement;
  the tag should be one the database marks writable-from-outside).
- **write** — this controller's output is forwarded to the external
  server (a command; the tag is one the controller owns).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QFileDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.connectivity.fieldio.opcua_catalog import load_signal_catalog
from azeo_control_trainer.core.presentation.configuration_chrome import ConfigurationComboBox as QComboBox
from azeo_control_trainer.core.presentation.engineering_dialog import (
    Command, EngineeringMenus, button_command, polish_dialog,
)

log = logging.getLogger("strategy.opcua_browser")

_NODE_ROLE = 0x0100
_LOADED_ROLE = 0x0101

#: Network deadlines apply on the session loop; the GUI polls completion.
CALL_TIMEOUT_S = 6.0


class UaSession:
    """One OPC UA client with async UI requests and synchronous test helpers.

    Deliberately Qt-free so the smoke test can drive the whole browse and
    bind flow headless against the trainer's own PK server.
    """

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self.connected = False
        self._closing = False
        self._lifecycle_lock = threading.RLock()

    def _ensure_loop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            if self._closing:
                raise RuntimeError("OPC UA session is closing")
            return
        self._closing = False
        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._thread = threading.Thread(target=_run, daemon=True,
                                        name="opcua-browse")
        self._thread.start()
        ready.wait(5.0)

    def _submit(self, coroutine, timeout=CALL_TIMEOUT_S):
        with self._lifecycle_lock:
            try:
                self._ensure_loop()
            except Exception:
                coroutine.close()
                raise
            async def bounded():
                return await asyncio.wait_for(coroutine, timeout)
            return asyncio.run_coroutine_threadsafe(bounded(), self._loop)

    def _call(self, coroutine, timeout: float = CALL_TIMEOUT_S):
        future = self._submit(coroutine, timeout)
        return self._wait(future, timeout + 1)

    @staticmethod
    def _wait(future, timeout):
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    # ------------------------------------------------------------- session
    def connect(self, endpoint: str) -> None:
        self._wait(self.connect_async(endpoint), 11)

    def connect_async(self, endpoint: str):
        from asyncua import Client

        async def _connect():
            self.connected = False
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:                   # noqa: BLE001
                    pass
            self._client = Client(endpoint)
            await self._client.connect()
            self.connected = True

        return self._submit(_connect(), timeout=10.0)

    def disconnect(self, *, wait=True) -> bool:
        with self._lifecycle_lock:
            thread, loop = self._thread, self._loop
            if thread is None or not thread.is_alive():
                return True
            if not self._closing:
                self._closing = True
                self.connected = False

                async def shutdown():
                    current = asyncio.current_task()
                    pending = [task for task in asyncio.all_tasks() if task is not current]
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    try:
                        if self._client is not None:
                            await asyncio.wait_for(self._client.disconnect(), CALL_TIMEOUT_S)
                    except Exception:  # noqa: BLE001 - close the loop after a failed disconnect
                        log.debug("OPC UA disconnect failed", exc_info=True)
                    finally:
                        self._client = None
                        loop.call_soon(loop.stop)

                asyncio.run_coroutine_threadsafe(shutdown(), loop)
        if wait and thread is not threading.current_thread():
            thread.join(timeout=CALL_TIMEOUT_S + 2)
        # Retain the handle until the worker has actually exited.
        return not thread.is_alive()

    # ------------------------------------------------------------ browsing
    def children(self, node_id: str | None) -> list[dict]:
        return self._wait(self.children_async(node_id), CALL_TIMEOUT_S + 1)

    def children_async(self, node_id: str | None):
        """The node's children: name, id, and whether each is a variable."""
        from asyncua import ua

        async def _children():
            root = (self._client.nodes.objects if node_id is None
                    else self._client.get_node(node_id))
            out = []
            for child in await root.get_children(
                    refs=ua.ObjectIds.HierarchicalReferences):
                try:
                    browse_name = await child.read_browse_name()
                    node_class = await child.read_node_class()
                except Exception:                   # noqa: BLE001
                    continue
                out.append({
                    "name": browse_name.Name,
                    "id": child.nodeid.to_string(),
                    "variable": node_class == ua.NodeClass.Variable,
                })
            return out

        return self._submit(_children())


class OpcUaBrowserDialog(QDialog):
    """Browse a live server and configure an EIOC's OPC UA client map."""

    def __init__(self, store, project_path: Path | str | None = None,
                 parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._pending = None
        self._request_timer = QTimer(self)
        self._request_timer.setInterval(30)
        self._request_timer.timeout.connect(self._poll_request)
        self._store = store
        if project_path is None:
            from azeo_control_trainer.core.strategy.serialization import (
                strategy_io,
            )

            project_path = Path(strategy_io.STRATEGY_DIR) / "_project.json"
        self._project_path = Path(project_path)
        self.session = UaSession()
        self._signal_metadata: dict[str, dict] = {}
        self.setWindowTitle("EIOC OPC UA Client — online signal browsing")
        self.resize(860, 560)

        existing = self._existing_config()

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Endpoint:"))
        self._endpoint = QLineEdit(existing.get(
            "endpoint", "opc.tcp://127.0.0.1:4840/azeo/pk"))
        row.addWidget(self._endpoint, 1)
        self._connect_button = QPushButton("Connect")
        self._connect_button.clicked.connect(self._connect)
        row.addWidget(self._connect_button)
        layout.addLayout(row)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Browse name", "NodeId"])
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemSelectionChanged.connect(self._selected)
        split.addWidget(self.tree)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        self._picked = QLabel("Select a variable on the left.")
        self._picked.setWordWrap(True)
        right_lay.addWidget(self._picked)
        bind_row = QHBoxLayout()
        bind_row.addWidget(QLabel("Store tag:"))
        self._tag = QComboBox()
        self._tag.setEditable(True)
        tagdb = getattr(store, "tagdb", None)
        if tagdb is not None:
            self._tag.addItems(sorted(tagdb.field_tags()))
        bind_row.addWidget(self._tag, 1)
        self._direction = QComboBox()
        self._direction.addItems(["read", "write"])
        bind_row.addWidget(self._direction)
        self._add_button = QPushButton("Add signal")
        self._add_button.setEnabled(False)
        self._add_button.clicked.connect(self._add)
        bind_row.addWidget(self._add_button)
        right_lay.addLayout(bind_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Store tag", "Node",
                                              "Direction"])
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        right_lay.addWidget(self.table, 1)
        catalog_row = QHBoxLayout()
        import_catalog = QPushButton("Import signal catalog...")
        import_catalog.setToolTip(
            "Bulk configure AI/AO/DI/DO variables from a JSON tag catalogue")
        # QPushButton.clicked carries a checked bool. Do not let that bool be
        # mistaken for the optional testable file path.
        import_catalog.clicked.connect(lambda: self._import_catalog())
        catalog_row.addWidget(import_catalog)
        self._catalog_status = QLabel("")
        catalog_row.addWidget(self._catalog_status, 1)
        right_lay.addLayout(catalog_row)
        remove = QPushButton("Remove selected signal")
        remove.clicked.connect(self._remove)
        right_lay.addWidget(remove)
        split.addWidget(right)
        split.setSizes([380, 460])
        layout.addWidget(split, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Close)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for tag, spec in (existing.get("signals") or {}).items():
            self._append_row(tag, spec.get("node", ""),
                             spec.get("direction", "read"), spec)
        polish_dialog(self, title="EIOC Signal Configuration",
                      subtitle="Browse an OPC UA server and map variables to controller tags.", mark="assign_io")
        self.resize(1100, 650)
        self.menus = EngineeringMenus(self, search=self._endpoint,
            help_text="Connect to an OPC UA endpoint, select a variable and add its store tag mapping. "
            "Read receives a measurement; write sends a controller demand. Import signal catalog supports bulk mapping. "
            "Save writes the EIOC configuration; transport changes take effect at the next launch.")
        self.menus.add(self.menus.file, button_command(buttons.button(QDialogButtonBox.Save), shortcut="Ctrl+S"))
        self.menus.add(self.menus.file, button_command(import_catalog, mark="upload"))
        self.menus.menu("&Connection", [button_command(self._connect_button, mark="connect")])
        self.menus.menu("&Signal", [button_command(self._add_button, mark="new"),
            Command("Remove selected signal", self._remove, "delete", lambda: self.table.currentRow() >= 0)])
        self.menus.table(self.table, lambda row: [Command("Remove selected signal", self._remove, "delete")], title="Signal mapping")

    # ------------------------------------------------------------- config
    def _existing_config(self) -> dict:
        try:
            project = json.loads(self._project_path.read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        for entry in project.get("areas", ()):
            config = (entry.get("eioc") or {}).get("field_io") \
                or entry.get("field_io") or {}
            if config.get("type") == "opcua":
                return dict(config)
        return {}

    # ------------------------------------------------------------ browsing
    def _connect(self) -> None:
        endpoint = self._endpoint.text().strip()
        def connected(_):
            self.tree.clear()
            self._populate(None, self.tree.invisibleRootItem())
        self._request(lambda: self.session.connect_async(endpoint), connected)

    def _populate(self, node_id: str | None, parent_item) -> None:
        self._request(lambda: self.session.children_async(node_id),
                      lambda children: self._populate_rows(children, parent_item))

    def _request(self, submit, completed):
        try:
            future = submit()
        except Exception as error:  # noqa: BLE001 - optional client/closing session
            self._request_error(error)
            return
        self._pending = future, completed
        self.tree.setEnabled(False)
        self._connect_button.setEnabled(False)
        self._catalog_status.setText("Waiting for OPC UA server…")
        self._request_timer.start()

    def _poll_request(self):
        if self._pending is None or not self._pending[0].done():
            return
        future, completed = self._pending
        self._pending = None
        self._request_timer.stop()
        self.tree.setEnabled(True)
        self._connect_button.setEnabled(True)
        try:
            completed(future.result())
            if self._pending is None:
                self._catalog_status.setText("OPC UA browser ready")
        except Exception as error:                  # noqa: BLE001
            self._request_error(error)

    def _request_error(self, error):
        log.warning("Browse failed: %s", error)
        self._catalog_status.setText(f"OPC UA request failed: {error}")

    def _populate_rows(self, children, parent_item):
        for child in children:
            item = QTreeWidgetItem(parent_item,
                                   [child["name"], child["id"]])
            item.setData(0, _NODE_ROLE, child)
            if not child["variable"]:
                # A lazy placeholder so the expander shows; children are
                # fetched when the engineer actually opens the folder.
                QTreeWidgetItem(item, ["…"])

    def _expand(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _LOADED_ROLE):
            return
        item.setData(0, _LOADED_ROLE, True)
        item.takeChildren()
        node = item.data(0, _NODE_ROLE) or {}
        self._populate(node.get("id"), item)

    def _selected(self) -> None:
        items = self.tree.selectedItems()
        node = items[0].data(0, _NODE_ROLE) if items else None
        is_variable = bool(node and node.get("variable"))
        self._add_button.setEnabled(is_variable)
        if is_variable:
            self._picked.setText(f"{node['name']}\n{node['id']}")

    # ------------------------------------------------------------- binding
    def _append_row(self, tag: str, node: str, direction: str,
                    metadata: dict | None = None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate((tag, node, direction)):
            self.table.setItem(row, column, QTableWidgetItem(value))
        self._signal_metadata[tag] = {
            key: value for key, value in dict(metadata or {}).items()
            if key not in {"node", "direction"}
        }

    def _add(self) -> None:
        items = self.tree.selectedItems()
        node = items[0].data(0, _NODE_ROLE) if items else None
        tag = self._tag.currentText().strip()
        if not node or not node.get("variable") or not tag:
            return
        self._append_row(tag, node["id"], self._direction.currentText())

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            tag_item = self.table.item(row, 0)
            if tag_item is not None:
                self._signal_metadata.pop(tag_item.text().strip(), None)
            self.table.removeRow(row)

    def _import_catalog(self, path: str | Path | None = None) -> int:
        """Replace the table with a validated bulk signal catalogue."""
        if path is None:
            from azeo_control_trainer.core.presentation.headless import is_headless

            if is_headless():
                return 0
            selected, _filter = QFileDialog.getOpenFileName(
                self, "Import OPC UA signal catalog", str(self._project_path.parent),
                "JSON catalog (*.json)")
            if not selected:
                return 0
            path = selected
        try:
            signals = load_signal_catalog(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            from azeo_control_trainer.core.presentation.headless import is_headless

            log.warning("Could not import OPC UA catalogue %s: %s", path, error)
            if not is_headless():
                QMessageBox.warning(self, "OPC UA catalog", str(error))
            return 0
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            self._signal_metadata.clear()
            for tag, spec in signals.items():
                self._append_row(tag, spec["node"], spec["direction"], spec)
        finally:
            self.table.setUpdatesEnabled(True)
        self._catalog_status.setText(f"{len(signals)} signals ready to save")
        return len(signals)

    def signals_map(self) -> dict:
        out: dict = {}
        for row in range(self.table.rowCount()):
            tag = self.table.item(row, 0).text().strip()
            node = self.table.item(row, 1).text().strip()
            direction = self.table.item(row, 2).text().strip() or "read"
            if tag and node:
                out[tag] = {
                    **self._signal_metadata.get(tag, {}),
                    "node": node,
                    "direction": direction,
                }
        return out

    def _save(self) -> None:
        """Save the binding under the area's EIOC engineering node."""
        try:
            project = json.loads(self._project_path.read_text(
                encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("Could not read %s: %s", self._project_path, error)
            return
        if not project.get("areas"):
            return
        area = project["areas"][0]
        eioc = area.setdefault("eioc", {})
        eioc.setdefault("name", "EIOC-1")
        eioc.setdefault("description", "External OPC UA client")
        eioc["field_io"] = {
            "type": "opcua",
            "endpoint": self._endpoint.text().strip(),
            "signals": self.signals_map(),
        }
        legacy = area.get("field_io") or {}
        if legacy.get("type") == "opcua":
            del area["field_io"]
        try:
            self._project_path.write_text(
                json.dumps(project, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            log.info("EIOC OPC UA client saved: %d signal(s) — takes effect "
                     "at next launch", self.table.rowCount())
        except OSError as error:
            log.warning("Could not write %s: %s", self._project_path,
                        error)
        self.accept()

    def closeEvent(self, event):                    # noqa: N802
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self):
        self._request_timer.stop()
        if self._pending is not None:
            self._pending[0].cancel()
            self._pending = None
        self.session.disconnect(wait=False)

    def done(self, result):
        self._shutdown()
        super().done(result)
