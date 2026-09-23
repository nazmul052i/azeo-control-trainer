"""Allowlisted application adapters for governed procedure integration blocks.

Adapters are registered by reviewed product code. Procedure documents select
an identity and exact version; they cannot import a module, name an executable,
or acquire filesystem/network/controller objects from this API.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import queue
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping


_MAX_PAYLOAD_BYTES = 65536
_MAX_ITEMS = 1024


@dataclass(frozen=True)
class AdapterContext:
    run_id: str
    step_id: str
    actor: str
    variables: Mapping[str, Any]


@dataclass(frozen=True)
class ProcedureAdapter:
    adapter_id: str
    version: str
    handler: Callable[[Mapping[str, Any], AdapterContext], Mapping[str, Any]]
    description: str = ""


_REGISTRY: dict[tuple[str, str], ProcedureAdapter] = {}
_SLOTS = threading.BoundedSemaphore(4)


def _bounded_json(value: Any, label: str) -> Any:
    def walk(item, count):
        count[0] += 1
        if count[0] > _MAX_ITEMS:
            raise ValueError(f"{label} exceeds {_MAX_ITEMS} values")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{label} contains a non-finite number")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError(f"{label} object keys must be strings")
            for key, child in item.items():
                walk(key, count)
                walk(child, count)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child, count)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{label} contains unsupported value {type(item).__name__}")

    walk(value, [0])
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be bounded JSON data") from error
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise ValueError(f"{label} exceeds {_MAX_PAYLOAD_BYTES} bytes")
    return json.loads(encoded.decode("utf-8"))


def register_adapter(adapter: ProcedureAdapter, *, replace: bool = False) -> None:
    if not adapter.adapter_id or not adapter.version:
        raise ValueError("Adapter identity and version are required")
    key = (adapter.adapter_id, adapter.version)
    existing = _REGISTRY.get(key)
    if existing is not None and not replace:
        raise ValueError(
            f"Procedure adapter already registered: {adapter.adapter_id} {adapter.version}"
        )
    _REGISTRY[key] = adapter


def adapter_catalog() -> tuple[ProcedureAdapter, ...]:
    return tuple(sorted(_REGISTRY.values(), key=lambda row: (row.adapter_id, row.version)))


def require_adapter(adapter_id: str, version: str) -> ProcedureAdapter:
    adapter = _REGISTRY.get((adapter_id, version))
    if adapter is not None:
        return adapter
    versions = sorted(key_version for key_id, key_version in _REGISTRY if key_id == adapter_id)
    if versions:
        raise ValueError(
            f"Procedure adapter {adapter_id} requires version in {versions}, not {version}"
        )
    raise ValueError(f"Procedure adapter is not registered: {adapter_id} {version}")


def execute_adapter(
    adapter_id: str,
    version: str,
    inputs: Mapping[str, Any],
    *,
    run_id: str,
    step_id: str,
    actor: str,
    variables: Mapping[str, Any],
    timeout_sec: float = 10.0,
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    adapter = require_adapter(adapter_id, version)
    bounded_inputs = _bounded_json(dict(inputs), "Adapter inputs")
    context = AdapterContext(
        run_id=run_id,
        step_id=step_id,
        actor=actor,
        variables=MappingProxyType(_bounded_json(dict(variables), "Adapter variables")),
    )
    if not math.isfinite(timeout_sec) or not 0.1 <= timeout_sec <= 60:
        raise ValueError("Adapter timeout must be between 0.1 and 60 seconds")
    if not _SLOTS.acquire(blocking=False):
        raise RuntimeError("Procedure adapter concurrency limit reached")
    completed = queue.Queue(maxsize=1)

    def invoke():
        try:
            completed.put((True, adapter.handler(MappingProxyType(bounded_inputs), context)))
        except BaseException as error:  # noqa: BLE001 - return the reviewed handler failure
            completed.put((False, error))
        finally:
            _SLOTS.release()

    threading.Thread(
        target=invoke,
        name=f"procedure-adapter-{adapter_id}",
        daemon=True,
    ).start()
    deadline = time.monotonic() + timeout_sec
    while True:
        if cancel_check is not None:
            cancel_check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Procedure adapter {adapter_id} timed out")
        try:
            succeeded, result = completed.get(timeout=min(0.05, remaining))
            break
        except queue.Empty:
            continue
    if not succeeded:
        raise RuntimeError(f"Procedure adapter {adapter_id} failed: {result}") from result
    if not isinstance(result, Mapping):
        raise ValueError(f"Procedure adapter {adapter_id} must return a mapping")
    return _bounded_json(dict(result), "Adapter results")


register_adapter(
    ProcedureAdapter(
        "azeo.identity",
        "1.0.0",
        lambda inputs, _context: dict(inputs),
        "Return reviewed bounded inputs for integration testing and simple data handoff.",
    )
)


__all__ = [
    "AdapterContext",
    "ProcedureAdapter",
    "adapter_catalog",
    "execute_adapter",
    "require_adapter",
    "register_adapter",
]
