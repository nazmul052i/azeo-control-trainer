"""Editing a library template on the ordinary canvas, and previewing one.

A template is edited with the same studio as a display: the studio is given a
:class:`TemplateDocumentStore` in place of its display store, so Save writes
the template (a built-in becomes a project override; Reset restores it) and
no display draft, lock, recovery file or revision ever appears under
Displays. Publish is refused the way the template model already refuses it.
The preview renders a template document through the shared renderer, the
same path Studio and the Operator Station use, so the Library shows what a
new display would start from.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from azeo_control_trainer.core.hmi.pvms.instances import TemplateRefused, TemplateStore
from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay


class TemplateDocumentStore:
    """The display-store surface a studio uses, backed by one template."""

    def __init__(self, templates: TemplateStore, template_name: str, display_root: str | Path):
        self.templates = templates
        self.template_name = template_name
        #: Libraries, functions, symbols and guides resolve from the project root.
        self.root = Path(display_root)

    # ------------------------------------------------------------ documents
    def document(self) -> dict | None:
        template = self.templates.entries.get(self.template_name)
        return None if template is None else dict(template.document)

    def load_draft(self, _name: str = "") -> PvmDisplay | None:
        document = self.document()
        if document is None:
            return None
        display = PvmDisplay.from_dict(document)
        display.name = self.template_name
        return display

    def draft_fingerprint(self, _name: str = "") -> str:
        document = self.document() or {}
        return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def save_draft(self, display: PvmDisplay) -> Path:
        document = display.to_dict()
        document["display"] = self.template_name
        self.templates.add(self.template_name, "display", document)
        return self.templates.path

    def create_draft(self, display: PvmDisplay) -> Path:
        return self.save_draft(display)

    # -------------------------------------------------------------- locking
    def acquire_lock(self, _name: str = "", who: str = "") -> str:
        return who

    def release_lock(self, _name: str = "", who: str = "") -> None:
        return None

    def owns_lock(self, _name: str = "") -> bool:
        return True

    # ------------------------------------------------------------- recovery
    def load_recovery(self, _name: str = "") -> PvmDisplay | None:
        return None

    def save_recovery(self, _display: PvmDisplay) -> None:
        return None

    def clear_recovery(self, _name: str = "") -> None:
        return None

    # ------------------------------------------------------------ revisions
    def history(self, _name: str = "") -> list[dict]:
        return []

    def revision_document(self, _name: str = "", _rev: int = 0) -> dict | None:
        return None

    def published_document(self, _name: str = "", *_args, **_kwargs) -> dict | None:
        return None

    def publish(self, *_args, **_kwargs):
        raise TemplateRefused(
            f"templates are not published; make a display from {self.template_name!r} and publish that")

    def revert(self, *_args, **_kwargs):
        raise TemplateRefused("a template has no revisions to revert to")

    def rename_display(self, *_args, **_kwargs):
        raise TemplateRefused("rename a template from the Library, not the canvas")

    def new_display_path(self, _name: str = "") -> Path:
        return self.templates.path


def template_preview(document: dict, width: int, height: int, *, dpr: float | None = None):
    """Render a template document to a pixmap through the shared renderer."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QGraphicsScene
    from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
    from azeo_control_trainer.core.hmi.pvms.rendering import viewer
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import route_scene_pipes
    from azeo_control_trainer.core.hmi.pvms.rendering.renderer import DisplayRenderer
    from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES

    display = PvmDisplay.from_dict(dict(document))
    renderer = DisplayRenderer(BindingEngine(LiveGraphSource(lambda: {})), THEMES[DEFAULT_THEME])
    scene = QGraphicsScene()
    scene.display = display                     # PVM furniture reads level and tags from it
    items = []
    for is_pvm, data in renderer.ordered_content(display):
        item = renderer.build_pvm(viewer.pvm_from_dict(data)) if is_pvm else renderer.build_drawing(data)
        if item is not None:
            items.append(item)
            scene.addItem(item)
    route_scene_pipes(scene)
    ratio = dpr if dpr is not None else 1.0
    result = QPixmap(int(width * ratio), int(height * ratio))
    result.setDevicePixelRatio(ratio)
    result.fill(Qt.white)
    painter = QPainter(result)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = scene.itemsBoundingRect()
        if display.width and display.height:
            bounds = bounds.united(QRectF(0, 0, display.width, display.height))
        if bounds.isEmpty():
            return result
        scale = min((width - 6) / max(1.0, bounds.width()), (height - 6) / max(1.0, bounds.height()))
        target = QRectF((width - bounds.width() * scale) / 2, (height - bounds.height() * scale) / 2,
                        bounds.width() * scale, bounds.height() * scale)
        scene.render(painter, target, bounds, Qt.KeepAspectRatio)
    finally:
        painter.end()
        scene.clear()
    return result


__all__ = ["TemplateDocumentStore", "template_preview"]
