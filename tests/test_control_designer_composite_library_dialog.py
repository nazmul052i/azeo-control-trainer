from __future__ import annotations

from azeo_control_trainer.core.strategy.blocks.composite_blocks import CompositeBlock
from azeo_control_trainer.core.strategy.composites import CompositeLibrary
from azeo_control_trainer.azeo_control_designer.dialogs.composite_library_dialog import (
    CompositeLibraryDialog,
)


def test_dialog_publishes_links_and_unlinks_without_native_prompts(qapp, tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    block = CompositeBlock("Skid")
    dialog = CompositeLibraryDialog(block, library)
    dialog.name_edit.setText("Reusable Skid")
    definition = dialog.publish_current()

    assert block.is_linked
    assert block.definition_id == definition.id
    assert dialog.state_label.text().startswith("CURRENT")
    assert dialog.unlink_instance()
    assert not block.is_linked
    assert block.inner_graph.to_dict() == definition.graph


def test_dialog_links_an_existing_definition(qapp, tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    definition = library.create("Valve Train", CompositeBlock("x").inner_graph.to_dict())
    block = CompositeBlock("XV-101")
    dialog = CompositeLibraryDialog(block, library)
    dialog.definition_combo.setCurrentIndex(
        dialog.definition_combo.findData(definition.id))
    assert dialog.link_selected()
    assert block.definition_state(library) == "current"
