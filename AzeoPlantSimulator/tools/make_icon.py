#!/usr/bin/env python3
"""Build the application icon from its vector source.

Renders ``azeoplant/ui/assets/icon.svg`` at every size Windows asks for and
packs them into one ``.ico``. Windows picks a different size for the title bar,
the taskbar, alt-tab and the large view in Explorer, and if the file holds only
one image it scales that badly. Rendering each size from the vector keeps them
all crisp.

The container uses PNG payloads, which Windows has accepted since Vista and
which keeps a 256 px image from bloating the file.

    python tools/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "azeoplant" / "ui" / "assets" / "icon.svg"
ICO = ROOT / "azeoplant" / "ui" / "assets" / "azeoplant.ico"
PNG = ROOT / "azeoplant" / "ui" / "assets" / "icon_256.png"

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def render(size: int) -> bytes:
    """One PNG of the icon at ``size`` square, straight from the SVG."""
    from PySide6.QtCore import QBuffer, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(SRC))
    if not renderer.isValid():
        raise SystemExit(f"cannot read {SRC}")
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(0)                                   # transparent, rounded corners
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def pack(images: dict[int, bytes]) -> bytes:
    """Assemble an ICO directory around already-encoded PNGs."""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)       # reserved, type 1 = icon
    offset = len(header) + count * 16
    entries, blobs = b"", b""
    for size in sorted(images):
        data = images[size]
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,              # 0 means 256
            size if size < 256 else 0,
            0,                                      # palette, 0 for true colour
            0,                                      # reserved
            1,                                      # colour planes
            32,                                     # bits per pixel
            len(data),
            offset,
        )
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> int:
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    images = {s: render(s) for s in SIZES}
    ICO.write_bytes(pack(images))
    PNG.write_bytes(images[256])
    print(f"{ICO.name}: {len(SIZES)} sizes {SIZES}, {ICO.stat().st_size / 1024:.1f} kB")
    print(f"{PNG.name}: {PNG.stat().st_size / 1024:.1f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
