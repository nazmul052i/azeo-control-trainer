"""Render the complete application-icon family for visual release review."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.presentation.app_icon import (  # noqa: E402
    APPLICATION_ICONS,
    get_app_icon,
)
from azeo_control_trainer.core.hmi.theme.fonts import (  # noqa: E402
    FontRole,
    ensure_font_directory,
    font_for,
)


def render(output: Path) -> Path:
    ensure_font_directory()
    app = QApplication.instance() or QApplication([])
    _ = app
    columns, cell_width, cell_height = 3, 290, 175
    rows = (len(APPLICATION_ICONS) + columns - 1) // columns
    image = QImage(
        columns * cell_width,
        rows * cell_height,
        QImage.Format_ARGB32_Premultiplied,
    )
    image.fill(QColor("#ECEEEF"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    title_font = QFont(font_for(FontRole.CHROME_BOLD))
    title_font.setPixelSize(15)
    detail_font = QFont(font_for(FontRole.CHROME))
    detail_font.setPixelSize(12)
    for index, (key, (_, description)) in enumerate(APPLICATION_ICONS.items()):
        column, row = index % columns, index // columns
        left, top = column * cell_width, row * cell_height
        painter.setPen(QColor("#D8DADC"))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(QRect(left + 10, top + 10, 270, 155), 8, 8)
        get_app_icon(key).paint(painter, QRect(left + 25, top + 28, 96, 96))
        painter.setPen(QColor("#2B323B"))
        painter.setFont(title_font)
        painter.drawText(QRect(left + 137, top + 38, 130, 45),
                         Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                         key.replace("_", " ").title())
        painter.setPen(QColor("#5A6168"))
        painter.setFont(detail_font)
        painter.drawText(QRect(left + 137, top + 88, 130, 42),
                         Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                         description)
        for size_index, size in enumerate((16, 24, 32)):
            get_app_icon(key).paint(
                painter,
                QRect(left + 25 + size_index * 38, top + 133, size, size),
            )
    painter.end()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output)):
        raise RuntimeError(f"Could not save {output}")
    return output


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "logs/diagnostics/application-icon-family.png")
    print(render(target))
