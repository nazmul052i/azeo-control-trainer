"""Private, non-published state for one Graphics Designer document.

Alignment guides are an engineering aid, not operator graphics.  Keeping
them beside (rather than inside) ``draft.json`` gives an author persistent
guides without letting a Publish copy editor chrome into a station package.
Each document owns an atomic sidecar, so two open display tabs never rewrite
one shared settings file.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def _state_path(root: str | Path, document_name: str) -> Path:
    identity = hashlib.sha256(
        str(document_name).encode("utf-8")).hexdigest()[:24]
    return Path(root) / ".studio" / "guides" / f"{identity}.json"


def _normalise(guides) -> list[tuple[str, float]]:
    clean: list[tuple[str, float]] = []
    for record in guides or ():
        if isinstance(record, dict):
            axis, value = record.get("axis"), record.get("value")
        else:
            try:
                axis, value = record
            except (TypeError, ValueError):
                continue
        try:
            coordinate = float(value)
        except (TypeError, ValueError):
            continue
        if axis not in ("x", "y") or not math.isfinite(coordinate):
            continue
        candidate = (str(axis), coordinate)
        if not any(existing[0] == candidate[0]
                   and abs(existing[1] - candidate[1]) < 0.01
                   for existing in clean):
            clean.append(candidate)
    return clean


def load_guides(root: str | Path,
                document_name: str) -> list[tuple[str, float]]:
    """Load valid editor guides; a corrupt sidecar behaves like no guides."""
    path = _state_path(root, document_name)
    if not path.exists():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []
    if document.get("document") != str(document_name):
        return []
    return _normalise(document.get("guides", ()))


def save_guides(root: str | Path, document_name: str, guides) -> Path:
    """Atomically persist guides outside the publishable display document."""
    path = _state_path(root, document_name)
    clean = _normalise(guides)
    if not clean:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "schema": 1,
        "document": str(document_name),
        "guides": [{"axis": axis, "value": value}
                   for axis, value in clean],
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


__all__ = ["load_guides", "save_guides"]
