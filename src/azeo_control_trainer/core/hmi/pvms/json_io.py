"""Crash-safe JSON persistence shared by graphics engineering stores.

An engineering document must be either the previous complete revision or the
new complete revision after a process or machine failure.  Writing directly to
the destination cannot provide that guarantee: a terminated ``write_text``
leaves valid-looking paths containing truncated JSON.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def atomic_write_json(
        path: str | Path, value: Any, *, indent: int = 1,
        ensure_ascii: bool = False) -> Path:
    """Durably replace *path* with one complete UTF-8 JSON document.

    The temporary file lives beside the destination so ``os.replace`` stays
    on one filesystem.  Flushing it before replacement makes a power or
    process failure leave the prior destination intact rather than expose a
    partially-written engineering artifact.
    """
    destination = Path(path)
    from azeo_control_trainer.core.configuration.package_paths import assert_mutable
    assert_mutable(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        value, indent=indent, ensure_ascii=ensure_ascii, allow_nan=False,
    ) + "\n"
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                prefix=f".{destination.name}.", suffix=".tmp",
                dir=destination.parent, delete=False) as stream:
            temporary_name = stream.name
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
        temporary_name = ""
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                # Preserve the original persistence exception. A stranded
                # dot-temporary is harmless and can be removed at startup.
                pass
    return destination


__all__ = ["atomic_write_json"]
