from __future__ import annotations

from pathlib import Path


def resolve_within(root: str | Path, candidate: str | Path, *, label: str) -> Path:
    """Resolve a relative application path and reject escapes from ``root``."""

    root_path = Path(root).resolve()
    raw = Path(candidate)
    if raw.is_absolute():
        raise ValueError(f"{label} must be relative to {root_path}")
    resolved = (root_path / raw).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the allowed root {root_path}: {candidate}") from exc
    return resolved


def safe_path_segment(value: str, *, label: str) -> str:
    """Validate a value that becomes exactly one filesystem path segment."""

    text = value.strip()
    if not text or text in {".", ".."}:
        raise ValueError(f"{label} is not a valid path segment")
    if Path(text).name != text or "/" in text or "\\" in text:
        raise ValueError(f"{label} must not contain path separators")
    return text
