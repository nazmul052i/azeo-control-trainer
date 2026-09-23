"""Browse cancellation must release the real loop without blocking Qt."""
import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from PySide6.QtTest import QTest
from azeo_control_trainer.azeo_explorer.opcua_browser import OpcUaBrowserDialog, UaSession


class Client:
    def __init__(self, *_):
        self.disconnected = False

    async def connect(self):
        await asyncio.sleep(30)

    async def disconnect(self):
        self.disconnected = True


@pytest.mark.parametrize("completion", ["reject", "accept", "close", "button"])
def test_all_browser_completion_paths_stop_client_and_loop(tmp_path, completion):
    app = QApplication.instance() or QApplication([])
    dialog = OpcUaBrowserDialog(None, tmp_path / "_project.json")
    session = dialog.session
    client = Client()
    session._client = client
    session._ensure_loop()
    worker, loop = session._thread, session._loop
    dialog.show()
    try:
        if completion == "button":
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close).click()
        else:
            getattr(dialog, completion)()
        worker.join(2)
        assert not worker.is_alive()
        assert loop.is_closed()
        assert client.disconnected
    finally:
        session.disconnect()
        app.processEvents()


def test_connect_is_async_and_closing_cancels_unfinished_connect(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setitem(sys.modules, "asyncua", SimpleNamespace(Client=Client))
    dialog = OpcUaBrowserDialog(None, tmp_path / "_project.json")
    session = dialog.session
    dialog.show()
    dialog._connect()
    assert dialog._pending is not None
    future = dialog._pending[0]
    assert not future.done(), "Connect should return while the network operation is pending"
    app.processEvents()
    worker = session._thread
    dialog.reject()
    worker.join(2)
    assert not worker.is_alive()
    assert future.done()
    app.processEvents()


def test_call_timeout_cancels_coroutine_and_empty_session_loop_closes():
    session = UaSession()
    cancelled = threading.Event()
    async def pending():
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()
    try:
        with pytest.raises(TimeoutError):
            session._call(pending(), timeout=.02)
        assert cancelled.wait(1)
        loop = session._loop
    finally:
        assert session.disconnect()
    assert loop.is_closed()


def test_async_connect_populates_and_expands_tree_on_gui_thread(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = OpcUaBrowserDialog(None, tmp_path / "_project.json")
    session = dialog.session

    async def connect():
        await asyncio.sleep(.01)
        session.connected = True

    async def children(node):
        await asyncio.sleep(.01)
        return [{"name": "Plant" if node is None else "PV",
                 "id": "plant" if node is None else "pv", "variable": node is not None}]

    monkeypatch.setattr(session, "connect_async", lambda _: session._submit(connect()))
    monkeypatch.setattr(session, "children_async", lambda node: session._submit(children(node)))

    def finish():
        deadline = time.monotonic() + 3
        while dialog._pending is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert dialog._pending is None
        assert dialog.tree.isEnabled()
        assert not dialog._request_timer.isActive()

    try:
        dialog.show()
        dialog._connect()
        assert not dialog.tree.isEnabled()
        finish()
        folder = dialog.tree.topLevelItem(0)
        assert folder.text(0) == "Plant"
        folder.setExpanded(True)
        finish()
        assert folder.child(0).text(0) == "PV"
        assert "ready" in dialog._catalog_status.text()
    finally:
        dialog.reject()
        session.disconnect()
        app.processEvents()


def test_connect_setup_failure_is_reported_without_escaping_qt(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = OpcUaBrowserDialog(None, tmp_path / "_project.json")

    def fail(_):
        raise ImportError("Client package unavailable")

    monkeypatch.setattr(dialog.session, "connect_async", fail)
    try:
        dialog._connect_button.click()
        assert "Client package unavailable" in dialog._catalog_status.text()
        assert dialog._pending is None
        assert dialog.tree.isEnabled()
    finally:
        dialog.reject()
        app.processEvents()
