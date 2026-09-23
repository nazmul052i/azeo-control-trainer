"""Shared visual tokens for engineering function-block canvases."""
from __future__ import annotations

from azeo_control_trainer.core.strategy.model.block_base import BlockCategory


FUNCTION_BLOCK_BODY_TOP = "#F4F6F9"
FUNCTION_BLOCK_BODY_BOTTOM = "#E8EBF0"
FUNCTION_BLOCK_NORMAL_BORDER = "#9AA5B4"
FUNCTION_BLOCK_SELECTED_BORDER = "#3574C4"
FUNCTION_BLOCK_DEFAULT_HEADER = "#5A6577"

# Canvas blocks use this restrained ISA-101 palette in every engineering app.
# Keeping the hex values Qt-free prevents one editor from depending on another.
FUNCTION_BLOCK_CATEGORY_COLORS = {
    BlockCategory.IO: "#3A8A4A",
    BlockCategory.CONTROL: "#3574C4",
    BlockCategory.MATH: "#5A6577",
    BlockCategory.SIGNAL: "#4A7AA4",
    BlockCategory.LOGIC: "#B8962A",
    BlockCategory.SAFETY: "#C82A3A",
    BlockCategory.SFC: "#2A9A8A",
    BlockCategory.COMPOSITE: "#7B1FA2",
}


def function_block_category_color(category: BlockCategory) -> str:
    """Return the shared header color for a block category."""
    return FUNCTION_BLOCK_CATEGORY_COLORS.get(category, FUNCTION_BLOCK_DEFAULT_HEADER)


__all__ = [
    "FUNCTION_BLOCK_BODY_BOTTOM",
    "FUNCTION_BLOCK_BODY_TOP",
    "FUNCTION_BLOCK_CATEGORY_COLORS",
    "FUNCTION_BLOCK_DEFAULT_HEADER",
    "FUNCTION_BLOCK_NORMAL_BORDER",
    "FUNCTION_BLOCK_SELECTED_BORDER",
    "function_block_category_color",
]
