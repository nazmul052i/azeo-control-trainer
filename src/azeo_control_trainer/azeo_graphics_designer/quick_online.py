"""Quick Online View: a separate, live, unpublished sandbox.

Documents are handed to this window explicitly.  They remain drafts,
do not alter workstation assignments, and use the shipping renderer.
The source is live but every write is refused at the boundary.
"""
from __future__ import annotations

import copy
import logging
import weakref
from contextlib import nullcontext
from functools import wraps

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QLabel, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QTabWidget, QToolBar,
)

from azeo_control_trainer.core.hmi.binding.source import WriteResult
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
from azeo_control_trainer.core.hmi.theme.service import ThemeService, THEME_LABELS
from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES
from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox


def _guard_preview(callback):
    @wraps(callback)
    def guarded(self, *args, **kwargs):
        try:
            return callback(self, *args, **kwargs)
        except Exception as error:  # noqa: BLE001 - native UI callback boundary
            logging.getLogger(__name__).exception("Quick Online preview failed")
            self.statusBar().showMessage(f"Preview failed: {error}")
            return None
    return guarded


class ReadOnlySource:
    """Live reads with a hard write sandbox."""

    def __init__(self, source):
        self.source = source

    def read(self, path):
        return self.source.read(path)

    def snapshot(self):
        return getattr(self.source, "snapshot", nullcontext)()

    @property
    def alarm_state(self):
        return self.source.alarm_state

    def can_write(self, _path):
        return WriteResult(False, "Quick Online View blocks operator writes")

    def write(self, _path, _value):
        return WriteResult(False, "Quick Online View blocks operator writes")


class QuickOnlineView(QMainWindow):
    """One scratch runtime to which drafts are added on demand."""

    def __init__(self, graphs_provider, *, source, config_root=None,
                 theme=DEFAULT_THEME, parent=None):
        super().__init__(parent)
        self.graphs_provider = graphs_provider
        self.source = ReadOnlySource(source)
        self.config_root = config_root
        self.theme = theme if theme in THEMES else DEFAULT_THEME
        self.faces = []
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.remove)
        self.tabs.currentChanged.connect(self._activate)
        self.setCentralWidget(self.tabs)
        self.setWindowTitle("Quick Online View — unpublished sandbox")
        self.resize(960, 680)
        bar = QToolBar("Operator preview", self)
        bar.setMovable(False)
        from azeo_control_trainer.core.hmi.theme.widgets import TOOL_METRICS
        bar.setStyleSheet(TOOL_METRICS)
        self.addToolBar(bar)
        bar.addWidget(QLabel(" Theme "))
        self.theme_selector = AuthoringComboBox()
        self.theme_selector.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.theme_selector.setSizeAdjustPolicy(AuthoringComboBox.AdjustToContents)
        self.theme_selector.setAccessibleName("Operator preview theme")
        for name in ThemeService(self.theme, initial_theme=self.theme).names():
            self.theme_selector.addItem(THEME_LABELS.get(name, name), name)
        self.theme_selector.setCurrentIndex(self.theme_selector.findData(self.theme))
        self.theme_selector.currentIndexChanged.connect(
            lambda _index: self.choose_theme(self.theme_selector.currentData()))
        bar.addWidget(self.theme_selector)
        bar.addWidget(QLabel(" Display viewport "))
        self.resolution_selector = AuthoringComboBox()
        self.resolution_selector.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.resolution_selector.setSizeAdjustPolicy(AuthoringComboBox.AdjustToContents)
        self.resolution_selector.setAccessibleName("Preview viewport resolution")
        self.resolution_selector.addItem("Fit window", None)
        for width, height in ((1366, 768), (1920, 1080), (2560, 1440)):
            self.resolution_selector.addItem(f"{width} × {height}", (width, height))
        self.resolution_selector.currentIndexChanged.connect(self._resize_views)
        bar.addWidget(self.resolution_selector)
        capture = QPushButton("Export image…")
        capture.clicked.connect(self.export_image)
        bar.addWidget(capture)
        check = QPushButton("Check readability")
        check.clicked.connect(self.check_readability)
        bar.addWidget(check)
        from PySide6.QtWidgets import QDockWidget
        from .studio.problems import ProblemsPane
        self.quality_dock = QDockWidget("Preview readability", self)
        self.quality_pane = ProblemsPane(self.quality_dock)
        self.quality_pane.problem_activated.connect(self.locate_readability)
        self.quality_dock.setWidget(self.quality_pane)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.quality_dock)
        self.quality_dock.hide()
        if callable(getattr(parent, "open_help", None)):
            help_button = QPushButton("Help")
            help_button.clicked.connect(self.open_help)
            bar.addWidget(help_button)
        self.statusBar().addPermanentWidget(QLabel("LIVE READS · WRITES BLOCKED"))
        self._apply_chrome()

    def _views(self):
        return [self.tabs.widget(i).widget() for i in range(self.tabs.count())]

    @_guard_preview
    def check_readability(self, *_args):
        from dataclasses import replace
        from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
        views = self._views()
        if not views:
            return ()
        view = views[self.tabs.currentIndex()]
        viewport = self.resolution_selector.currentData() or (view.viewport().width(), view.viewport().height())
        findings = visual_findings(view.scene(), view.display, self.theme, viewport)
        # Corrections belong to the authored draft, never this read-only copy.
        self.quality_pane.set_problems(view.display.name, [replace(f, fix="") for f in findings])
        self.quality_dock.show()
        self.statusBar().showMessage(f"{len(findings)} readability advisories · {self.theme} · L{view.display.level} · {viewport[0]} × {viewport[1]}")
        return findings

    @_guard_preview
    def locate_readability(self, identity):
        from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data
        views = self._views()
        if not views:
            return
        view = views[self.tabs.currentIndex()]
        for item in view.scene().items():
            if str(getattr(getattr(item, "pvm", None), "id", "") or item_document_data(item).get("id", "")) == identity:
                view.ensureVisible(item)
                self.statusBar().showMessage(f"{identity} · correct this object in Graphics Designer")
                break

    def _apply_chrome(self):
        from azeo_control_trainer.core.hmi.theme.widgets import apply_role_palette, widget_stylesheet
        apply_role_palette(self, THEMES[self.theme])
        self.setStyleSheet(widget_stylesheet(self.theme))

    @_guard_preview
    def choose_theme(self, name):
        if name not in THEMES:
            return False
        self.theme = name
        self.quality_pane.set_problems("", ())
        self._apply_chrome()
        for view in (*self._views(), *self.faces):
            view.apply_theme(name)
        index = self.theme_selector.findData(name)
        if index != self.theme_selector.currentIndex():
            with QSignalBlocker(self.theme_selector):
                self.theme_selector.setCurrentIndex(index)
        return True

    @_guard_preview
    def add_display(self, document: dict):
        name = str(document.get("display", "Untitled"))
        index = next((i for i, view in enumerate(self._views())
                      if view.display.name == name), -1)
        # Build first, so a failed replacement leaves the last preview usable.
        view = PvmDisplayView(
            copy.deepcopy(document), self.graphs_provider, theme=self.theme,
            config_root=self.config_root, source=self.source,
            action_handler=self.display_action,
            open_display=self.open_display,
            pvm_activation_handler=lambda pvm, _engine: self.open_faceplate(pvm),
            write_handler=self.source.write, write_checker=self.source.can_write)
        view.setFrameShape(QFrame.NoFrame)
        page = QScrollArea()
        page.setFrameShape(QFrame.NoFrame)
        page.setAlignment(Qt.AlignCenter)
        page.setWidget(view)
        if index >= 0:
            self.remove(index)
            self.tabs.insertTab(index, page, name)
        else:
            self.tabs.addTab(page, name)
        self._resize_views()
        self.tabs.setCurrentWidget(page)
        self._activate()
        return view

    @_guard_preview
    def _resize_views(self, *_args):
        self.quality_pane.set_problems("", ())
        size = self.resolution_selector.currentData()
        for index, view in enumerate(self._views()):
            page = self.tabs.widget(index)
            page.setWidgetResizable(size is None)
            if size:
                view.setFixedSize(*size)
            else:
                view.setMinimumSize(0, 0)
                view.setMaximumSize(16777215, 16777215)

    @_guard_preview
    def _activate(self, *_args):
        if hasattr(self, "quality_pane"):
            self.quality_pane.set_problems("", ())
        for index, view in enumerate(self._views()):
            view.set_active(index == self.tabs.currentIndex())
        views = self._views()
        if views:
            view = views[self.tabs.currentIndex()]
            self.statusBar().showMessage(
                "Authored fixed background: it will not follow the theme. Clear it in Display Properties."
                if view.display.background else "Click a PVM to preview its faceplate. Send Quick Online again after editing.")
        else:
            self.statusBar().showMessage("Add a draft from Graphics Designer → Quick Online.")

    @_guard_preview
    def remove(self, index: int) -> None:
        page = self.tabs.widget(index)
        if page is None:
            return
        self._close_faces()
        page.widget().close()
        self.tabs.removeTab(index)
        page.deleteLater()

    def documents(self) -> tuple[str, ...]:
        return tuple(self.tabs.tabText(index)
                     for index in range(self.tabs.count()))

    def _retain_face(self, face):
        self.faces.append(face)
        caption = "Preview · " + face.context_title.title.text()
        face.context_title.title.setText(caption)
        face.context_title.title.setToolTip(caption + " — operator writes are blocked")
        face.setAttribute(Qt.WA_DeleteOnClose)
        owner, target = weakref.ref(self), weakref.ref(face)

        def forget(*_args):
            host, widget = owner(), target()
            if host is not None and widget in host.faces:
                host.faces.remove(widget)
        face.destroyed.connect(forget)
        face.show()
        face.raise_()
        return face

    @_guard_preview
    def open_faceplate(self, pvm, role="faceplate"):
        from azeo_control_trainer.core.hmi.binding import BindingEngine
        from azeo_control_trainer.core.hmi.pvms.base import registry
        from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
        from azeo_control_trainer.core.hmi.pvms.rendering.renderer import DisplayRenderer
        cls = registry.get(pvm.block_type, role, pvm.variant) or registry.get(pvm.block_type, role)
        if cls is None:
            self.statusBar().showMessage(f"No {role} is configured for {pvm.block_type}.")
            return None
        key = (pvm.block_type, role, pvm.variant, repr(pvm.params), repr(pvm.choices), pvm.class_revision)
        for face in self.faces:
            if getattr(face, "preview_key", None) == key and face.bound:
                face.show()
                face.raise_()
                return face
        # Independent bindings let a preview refresh/close without invalidating a faceplate.
        engine = BindingEngine(self.source)
        engine.configuration_root = self.config_root
        config = DisplayRenderer(engine, {}, self.config_root).pvm_config(cls, pvm.class_revision)
        face = PvmFaceplateWidget(cls, pvm.params, engine, theme=self.theme,
                                  config=config, choices=pvm.choices, parent=self,
                                  window_flags=Qt.Tool | Qt.FramelessWindowHint)
        face.preview_key = key
        face.set_write_handler(self.source.write, self.source.can_write)
        actions = {target for target in ("faceplate", "detail") if target != role
                   and (registry.get(pvm.block_type, target, pvm.variant) or registry.get(pvm.block_type, target))}
        face.set_available_actions(actions)
        face.action_requested.connect(lambda action: self.open_faceplate(pvm, action)
                                      if action in actions else None)
        return self._retain_face(face)

    @_guard_preview
    def display_action(self, action, view=None, item=None):
        kind, target = str(action.get("kind", "")), str(action.get("target", ""))
        if kind == "open_display":
            return self.open_display(target)
        if kind in {"open_user_faceplate", "open_user_detail"}:
            from azeo_control_trainer.core.hmi.pvms.user_faceplate import UserFaceplateView
            from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data
            from azeo_control_trainer.core.hmi.pvms.class_revisions import item_revision, revision_root
            root = revision_root(getattr(view, "config_root", None) or self.config_root, item_revision(item))
            choices = dict(item_document_data(item).get("pvm_choices", {}))
            for face in self.faces:
                if isinstance(face, UserFaceplateView) and not face._disposed \
                        and face.class_name == target and face.instance_choices == choices and face.config_root == root:
                    face.show()
                    face.raise_()
                    return True
            face = UserFaceplateView(target, root, self.graphs_provider,
                                     source=self.source, choices=choices, theme=self.theme, parent=self,
                                     action_handler=self.display_action,
                                     write_handler=self.source.write, write_checker=self.source.can_write)
            self._retain_face(face)
            return True
        self.statusBar().showMessage("Quick Online previews displays and faceplates. Scripts and operating commands are not executed.")
        return False

    @_guard_preview
    def open_display(self, name):
        for index, view in enumerate(self._views()):
            if view.display.name == name:
                self.tabs.setCurrentIndex(index)
                return True
        self.statusBar().showMessage(f"Add '{name}' from Graphics Designer to preview this navigation link.")
        return False

    @_guard_preview
    def open_help(self, _checked=False):
        self.parent().open_help("preview_themes")

    @_guard_preview
    def export_image(self, _checked=False):
        from azeo_control_trainer.core.presentation.headless import is_headless
        views = self._views()
        if not views or is_headless():
            return
        path, _filter = QFileDialog.getSaveFileName(self, "Export preview image", "", "PNG image (*.png)")
        if path:
            if not views[self.tabs.currentIndex()].viewport().grab().save(path, "PNG"):
                raise OSError("The preview image could not be saved")
            self.statusBar().showMessage(f"Preview image saved: {path}")

    def _close_faces(self):
        for face in list(self.faces):
            face.close()
        self.faces.clear()

    def closeEvent(self, event):  # noqa: N802
        self._close_faces()
        for index in reversed(range(self.tabs.count())):
            self.remove(index)
        super().closeEvent(event)
