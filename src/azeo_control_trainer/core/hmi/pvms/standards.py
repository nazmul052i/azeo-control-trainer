"""Persistent project standards shared by authoring and display runtime."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

STANDARD_DEFAULTS = {
    "Color": "#4472C4",
    "Font": "Segoe UI 12",
    "Boolean": "False",
    "Image": "",
    "String": "",
    "Private String": "",
    "Multi-language String": "en:",
    "Measurement": "0 px",
    "Number": "0",
}

BUILTIN_STANDARDS = (
    "hphmi.controller",
    "hphmi.indicator",
    "hphmi.equipment",
)


class StandardsStore:
    """Persist flat, foldered project standards beside display documents."""

    def __init__(self, root: Path | None):
        self.path = Path(root) / "_standards.json" if root is not None else None
        self.entries: list[dict] = []
        self.folders: list[str] = ["Common"]
        if self.path is not None and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.entries = list(data.get("standards", ()))
                self.folders = list(data.get("folders", ("Common",)))
            except Exception:  # A malformed optional library must not block startup.
                log.warning("Ignoring malformed standards library %s", self.path,
                            exc_info=True)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            {"folders": self.folders, "standards": self.entries},
            indent=2,
            ensure_ascii=False,
        ) + "\n"
        self.path.write_text(text, encoding="utf-8", newline="\n")

    def get(self, name: str) -> dict | None:
        return next((entry for entry in self.entries if entry["name"] == name), None)

    def add(self, std_type: str, folder: str = "Common", name: str = "") -> dict:
        if not name:
            index = 1
            while self.get(f"S_New{index}") is not None:
                index += 1
            name = f"S_New{index}"
        entry = {
            "name": name,
            "type": std_type,
            "value": STANDARD_DEFAULTS.get(std_type, ""),
            "folder": folder,
        }
        self.entries.append(entry)
        if folder not in self.folders:
            self.folders.append(folder)
        return entry

    def rename(self, old: str, new: str) -> bool:
        entry = self.get(old)
        if entry is None or self.get(new) is not None:
            return False
        entry["name"] = new
        return True

    def delete(self, name: str) -> bool:
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if entry["name"] != name]
        return len(self.entries) < before


__all__ = ["BUILTIN_STANDARDS", "STANDARD_DEFAULTS", "StandardsStore"]
