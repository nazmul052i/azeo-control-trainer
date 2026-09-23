"""A bounded join must not hide a still-running transport worker."""
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("kind", ["client", "pk", "modbus"])
def test_timed_out_shutdown_retains_worker_until_actual_exit(kind, monkeypatch):
    from azeo_control_trainer.connectivity.fieldio.opcua_driver import OpcUaLink
    from azeo_control_trainer.connectivity.opcua.pk_server import PKOpcUaServer
    from azeo_control_trainer.connectivity.fieldio.modbus_server import StoreModbusServer
    cls = {"client": OpcUaLink, "pk": PKOpcUaServer, "modbus": StoreModbusServer}[kind]
    transport = cls.__new__(cls)
    worker = SimpleNamespace(alive=True)
    worker.join = lambda **_: None
    worker.is_alive = lambda: worker.alive
    transport._thread = worker
    transport._loop = object()
    transport._stopping = threading.Event()
    async def shutdown():
        pass
    transport._server = SimpleNamespace(shutdown=shutdown)
    def submit(coroutine, _loop):
        coroutine.close()
        return SimpleNamespace(result=lambda **_: None)
    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", submit)
    assert transport.stop() is False
    assert transport._thread is worker
    assert transport._loop is not None
    worker.alive = False
    assert transport.stop() is True
    assert transport._thread is None
    assert transport._loop is None
