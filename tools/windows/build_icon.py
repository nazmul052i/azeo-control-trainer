"""Export one code-rendered application icon for Windows resources.

Usage: ``build_icon.py OUTPUT.ico [APPLICATION_ID]``.
"""
import os
from pathlib import Path
import struct
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory  # noqa: E402
from azeo_control_trainer.core.presentation.app_icon import get_app_icon  # noqa: E402

ensure_font_directory()
application = QApplication([])
application_id = sys.argv[2] if len(sys.argv) > 2 else "explorer"
icon = get_app_icon(application_id)
images = []
for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
    buffer = QBuffer()
    if not buffer.open(QIODevice.WriteOnly) \
            or not icon.pixmap(size, size).save(buffer, "PNG"):
        raise RuntimeError("Could not render the Azeo application icon")
    images.append((size, bytes(buffer.data())))

# ICO permits PNG-compressed entries. Keep every optical size so Explorer,
# title bars and the taskbar do not scale one large bitmap differently.
header = struct.pack("<HHH", 0, 1, len(images))
offset = len(header) + len(images) * 16
entries = []
for size, image in images:
    dimension = 0 if size == 256 else size
    entries.append(struct.pack(
        "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(image), offset))
    offset += len(image)
Path(sys.argv[1]).write_bytes(
    header + b"".join(entries) + b"".join(image for _, image in images))
