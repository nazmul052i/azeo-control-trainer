"""Named palettes mix installed blocks and reusable templates."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.azeo_control_designer.panels import (  # noqa: E402
    block_palette,
)


def _app():
    return QApplication.instance() or QApplication([])


def test_named_palette_persists_blocks_and_templates(tmp_path, monkeypatch):
    _app()
    settings = tmp_path / "palette_settings.json"
    monkeypatch.setattr(block_palette, "_PALETTE_SETTINGS", settings)
    template = tmp_path / "cascade.json"
    template.write_text(json.dumps({"name": "Cascade"}), encoding="utf-8")

    palette = block_palette.BlockPalette()
    assert palette.create_custom_palette("Unit Operations")
    assert palette.add_block_to_palette("Unit Operations", "PID")
    assert palette.add_template_to_palette(
        "Unit Operations", str(template), "Cascade Loop")
    assert not palette.add_block_to_palette("Unit Operations", "PID")

    restored = block_palette.BlockPalette()
    entries = restored.custom_palettes()["Unit Operations"]
    assert entries[0] == {"kind": "block", "value": "PID"}
    assert entries[1]["kind"] == "template"
    assert entries[1]["label"] == "Cascade Loop"

    custom = next(
        restored._tree.topLevelItem(i)
        for i in range(restored._tree.topLevelItemCount())
        if str(restored._tree.topLevelItem(i).data(
            0, block_palette._ROLE_GROUP)).startswith(
                block_palette._CUSTOM_PREFIX))
    template_item = custom.child(1)
    mime = restored._tree.mimeData([template_item])
    assert mime.hasFormat(block_palette.TEMPLATE_MIME_TYPE)
    assert bytes(mime.data(block_palette.TEMPLATE_MIME_TYPE)).decode() \
        == str(template)

    placed = []
    restored.templatePlacementRequested.connect(placed.append)
    restored._on_item_double_clicked(template_item)
    assert placed == [str(template)]

    assert restored.remove_palette_entry("Unit Operations", 0)
    assert restored.rename_custom_palette("Unit Operations", "Loops")
    assert restored.remove_custom_palette("Loops")


def test_standard_palette_introspection_excludes_custom_duplicates(
        tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(
        block_palette, "_PALETTE_SETTINGS", tmp_path / "palette_settings.json")
    palette = block_palette.BlockPalette()
    palette.create_custom_palette("My Blocks")
    palette.add_block_to_palette("My Blocks", "AI")

    assert palette.visible_block_types().count("AI") == 1
    assert all(not name.startswith(block_palette._CUSTOM_PREFIX)
               for name in palette.group_contents())
