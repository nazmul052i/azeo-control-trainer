"""Author typed PVM configuration with a property tree, form and live preview.

Editing is live against the model; Save writes the class's
``<name>.pvmcfg.json`` beside the displays. Preview opens the
instance-side view: pick options the way a graphics engineer would and
watch subproperties resolve, absent properties vanish, and the online
subscription list shrink as Present Online and Presence take effect.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import configuration_document_paths

from azeo_control_trainer.core.presentation.menu_style import retain_menu

import json
import logging
from pathlib import Path

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    style_dialog_buttons,
)
from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
    QSizePolicy, QTableWidgetItem, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CONTROLS_QSS
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import WF
from .icons import command_icon, property_icon
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    ALWAYS, INTERNAL, OPERATOR_WRITE, PUBLIC, READ_ONLY, REFERENCE_TYPES,
    SELECTION_TYPE, VALUE_TYPES, WHEN_FALSE, WHEN_TRUE, ConfigurationError,
    PvmConfiguration, PvmProperty, Option, PropertyGroup,
)
from azeo_control_trainer.core.hmi.pvms.configurator.samples import (
    sample_configurations,
)

log = logging.getLogger("azeo.pvm_configurator")

CW = {"blue": WF["lapis"], "blue_d": WF["lapis"], "sel": WF["sel"],
      "sel_br": WF["sel_br"], "hover": WF["hover"], "grey": WF["sel"],
      "chrome": WF["chrome"], "chrome2": WF["chrome"], "pane": WF["pane"],
      "bd": WF["bd"], "bd_lt": WF["bd_lt"], "tx": WF["tx"],
      "tx2": WF["tx2"], "tx3": WF["tx3"], "red": "#D0342C",
      "green": "#237148"}

_QSS = f"""
QMainWindow, #cfg_root {{ background: {CW['pane']}; }}
QWidget {{ color: {CW['tx']}; font-family: "Segoe UI"; font-size: 9.5pt; }}
QWidget#cfg_form, QWidget#cfg_tree, QWidget#cfg_toolbar {{ background: {CW['pane']}; }}
QWidget#cfg_header {{ background: {CW['chrome']}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {CW['pane']}; border: 1px solid {CW['bd']};
    border-radius: 4px; padding: 4px 7px; min-height: 22px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {CW['blue']}; }}
QPushButton, QToolButton {{ background: {CW['pane']}; border: 1px solid
    {CW['bd_lt']}; border-radius: 4px; padding: 5px 9px; }}
QPushButton:hover, QToolButton:hover {{ background: {CW['hover']};
    border-color: {CW['sel_br']}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {CW['blue']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {CW['tx3']}; }}
QToolButton {{ background: transparent; border-color: transparent; }}
QToolButton:checked {{ background: {CW['sel']}; border-color: {CW['sel_br']}; }}
QToolButton::menu-button {{ width: 18px; border: none; background: transparent; }}
QToolButton::menu-arrow {{ width: 8px; height: 8px; }}
QTreeWidget {{ background: {CW['pane']}; border: none;
    outline: none; }}
QTreeWidget::item {{ padding: 5px 2px; }}
QTreeWidget::item:selected {{ background: {CW['grey']};
    color: {CW['blue_d']}; }}
QTableWidget {{ background: {CW['chrome']}; border: 1px solid
    {CW['bd']}; }}
QHeaderView::section {{ background: {CW['chrome']}; border: none;
    border-bottom: 1px solid {CW['bd']}; border-right: 1px solid
    {CW['bd_lt']}; padding: 5px 8px; }}
QSplitter::handle {{ background: {CW['bd_lt']}; }}
QSplitter::handle:hover {{ background: {CW['sel_br']}; }}
""" + AUTHORING_CONTROLS_QSS

# Parentless PySide objects need a Python owner until Qt consumes their
# DeferredDelete event. Without this keeper the wrapper can leave scope and
# destroy the native object first; the queued event then deletes it again.
_DEFERRED_QT_OBJECTS: list = []


def _detach_and_delete_later(obj) -> None:
    """Remove *obj* from QObject searches now and delete it safely later."""
    obj.setParent(None)
    _DEFERRED_QT_OBJECTS.append(obj)
    token = id(obj)

    def release(*_args) -> None:
        _DEFERRED_QT_OBJECTS[:] = [
            current for current in _DEFERRED_QT_OBJECTS
            if id(current) != token]

    obj.destroyed.connect(release)
    obj.deleteLater()


def _band(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"background: {CW['chrome']}; color: {CW['tx']};"
        f"border-bottom: 1px solid {CW['bd_lt']};"
        "font-size: 9.5pt; font-weight: 600; padding: 10px 12px;")
    return label


def _section_head(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color: {CW['blue']}; font-size: 9.75pt;"
                        "font-weight: 600; padding: 10px 0 4px; border: none;")
    return label


def _dispose_layout(layout) -> None:
    """Remove a dynamic form now, including rows stored as layouts.

    Each row is detached immediately so QObject searches and signals see the
    new form only. A module-level keeper retains the Python wrapper until Qt
    consumes DeferredDelete, avoiding the parentless-wrapper double deletion.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            _detach_and_delete_later(widget)
        elif child_layout is not None:
            _dispose_layout(child_layout)
            _detach_and_delete_later(child_layout)


def _coded_class_preview(cls, width: int = 200, height: int = 160,
                         typography=None, *, thumbnail=False, dpr=1.0,
                         fit=False, strict=False):
    """Paint the PVM itself, the way the canvas paints it.

    This pane used to draw a rectangle with the words "coded class —
    painted live" in it, which is a caption about a preview rather
    than a preview. An Author configuring `limits.lo_lo` cannot see
    what their change does to a box that says what kind of box it is.

    So build the real `PvmItem` and let it paint. It draws UNBOUND —
    no engine, no live block — which is the honest state for a
    configuration view and is what Azeo's own configuration figures
    show (`#######`, `DATADATADATA`): the shape and the anatomy,
    without pretending to carry a value. The class's own painter runs,
    so a bar previews as a bar and a pump as a pump.
    """
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QStyleOptionGraphicsItem

    from azeo_control_trainer.core.hmi.pvms.base import Pvm
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
    from azeo_control_trainer.core.hmi.theme.roles import Role
    from azeo_control_trainer.core.hmi.theme.tokens import DEFAULT_THEME, THEMES

    palette = THEMES[DEFAULT_THEME]
    size = getattr(cls, "DEFAULT_SIZE", None) or (150.0, 54.0)
    # The tag row carries the block type as its placeholder. Without
    # it every generic card previews identically — a PID and a RATIO
    # are the same empty card until something fills the tag — and
    # Azeo's own configuration figures place `DATADATADATA` in this
    # exact row for the same reason.
    pvm = Pvm(id="preview", pvm_class=cls.__name__,
              block_type=cls.block_type, role=cls.role,
              params={"path": ""}, variant=getattr(cls, "variant", ""),
              label=cls.block_type,
              w=float(size[0]), h=float(size[1]))
    pixmap = QPixmap(round(width * dpr), round(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent if thumbnail else QColor(palette[Role.SURFACE_BG]))
    painter = QPainter(pixmap)
    try:
        item = PvmItem(pvm, None, None, palette, rows={},
                       typography=typography)
        item.alarm_provider = None
        rect = item.boundingRect() if thumbnail or fit or strict else item.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return pixmap
        # Palette thumbnails retain their actual-size cap. The Designer's
        # explicitly labelled Fit view can enlarge small PVMs for inspection.
        margin = 8 if thumbnail else 24
        scale = min((width - margin) / rect.width(),
                    (height - margin) / rect.height(), 4.0 if fit else 1.0)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.translate((width - rect.width() * scale) / 2,
                          (height - rect.height() * scale) / 2)
        painter.scale(scale, scale)
        painter.translate(-rect.left(), -rect.top())
        item.paint(painter, QStyleOptionGraphicsItem(), None)
    except Exception:                               # noqa: BLE001
        if thumbnail or strict:
            # A broken installed painter must fail the icon audit, rather
            # than quietly shipping an empty tile in the palette.
            raise
        # A class whose painter needs more than an unbound item still
        # gets a pane, just an empty one — better than taking the
        # designer down while someone is editing a property.
        log.exception("Unable to preview PVM class %s", cls.__name__)
    finally:
        # end() must run even if a paint call raises — a leaked active
        # painter aborts Qt at teardown.
        painter.end()
    return pixmap


def _seed_from_class(cls) -> "PvmConfiguration":
    """A coded class's starter document, derived from what the class
    itself declares — the tree opens POPULATED, not blank:

    - one Control Tag property per PARAMS entry (the instance values
      a placement carries; a placement's own value always wins over
      the document default);
    - a 'DeclaredBindings' group listing every Bind spec with its
      template, kept OFFLINE (Present Online false) so it documents
      the type without entering the binding params — the same
      read-only from-type story the studio's config pane tells.
    """
    from azeo_control_trainer.core.hmi.pvms.configurator.model import (
        PvmProperty,
        PropertyGroup,
    )

    basic = PropertyGroup("BasicConfiguration", [])
    for param in getattr(cls, "PARAMS", ("path",)):
        primary = param == "path"
        basic.properties.append(PvmProperty(
            name=param, ptype="Control Tag",
            title="Control Tag" if param == "path"
            else param.title(),
            description="The instance's binding — set at placement; "
                        "the placement's own value wins.",
            required=primary, drop_target=primary,
            accepted_block_types=(
                [str(getattr(cls, "block_type", "")).upper()]
                if primary else [])))
    declared = PropertyGroup("DeclaredBindings", [],
                             present_online=False)
    for spec in getattr(cls, "bindings", ()):
        if spec.expr:
            description = f"expr: {spec.expr}"
        elif spec.prop:
            description = f"{spec.path} → {spec.prop}"
        else:
            description = spec.path
        declared.properties.append(PvmProperty(
            name=spec.key, ptype="Parameter Reference",
            title=spec.key, default=spec.path or spec.expr,
            description=description))
    groups = [basic]
    if declared.properties:
        groups.append(declared)
    # Typography is a class contract, not an arbitrary placement override.
    # The named profile selected by an instance resolves through this grid.
    from azeo_control_trainer.core.hmi.pvms.typography import typography_property
    groups.append(PropertyGroup(
        "Typography", [typography_property()], present_online=False))
    return PvmConfiguration(cls.__name__, groups)


CONFIGURATOR_HELP = """
<h2>Working the PVM Configuration Designer</h2>
<p>This designer defines the <b>configuration pane</b> a PVM's
instances show — what a graphics engineer picks and types when they
place one. The PVM's <i>graphics</i> are authored in Graphics Designer
(draw or Import&nbsp;SVG, select, <b>Convert to PVM class</b>); the
two halves meet when instances of the class resolve their bindings
through this document.</p>

<h3>The five-minute workflow</h3>
<ol>
<li><b>Pick the class</b> in the PVM class combo (top right). Study
<code>HP_C_Valve</code> first — it is the white paper's own worked
example, shipped as a sample.</li>
<li><b>Add group</b> — the new group is selected for the next property. Groups organise the pane, and each
carries <b>Present Online</b>: set false and the group is never
loaded into runtime, so its parameters are never subscribed. Put
optional extras (chart parameters, diagnostics) in an offline group
and one class covers many cases without paying for the unused
ones.</li>
<li><b>Add property</b> — pick a type:
<table border="0" cellspacing="0" cellpadding="3">
<tr><td><b>Control Tag</b></td><td>the instance's binding — the one
thing a placement carries. Name it <code>ControlTag</code>.</td></tr>
<tr><td><b>Selection</b></td><td>the core mechanism: named options
whose grid columns drive many values at once. The engineer picks
"Left", never 270°.</td></tr>
<tr><td><b>Color / Font / …</b></td><td>appearance values — press
<b>Standard&nbsp;▾</b> to reference a library standard instead of a
literal: change the standard later and every instance follows with
no republish.</td></tr>
</table></li>
<li><b>Define the Instance Interface</b> for each property. Use
<b>Public instance parameter</b> only for values an engineer supplies per
placement. Use <b>Internal class property</b> for derived/helper state; it is
available to member bindings but hidden from Configure instance. Mark a
public property Required only when no valid placement can omit it. Mark a
writable Parameter Reference as <b>Operator write (checked service)</b> only
when it is consumed by a User Entry.</li>
<li><b>Declare one typed Primary Drop Target</b>. Select a public Control Tag
or Function Block Reference, enable <b>Primary drop target</b>, and list compatible block types such as
<code>PID, PID_AT, PID_DEADTIME, FLC</code>. The target becomes Required and
Read only. Exactly one target is allowed; without it the authored class is
not offered when a block is dragged from Control Data.</li>
<li><b>Build the Selection grid</b>: set the option count, name the
Default Selection, <b>Add column</b> for each subproperty
(right-click a column header to rename or ✕ remove it). A cell may
reference another property — <code>Pvm.CustomAngle</code> — which is
how a Custom option defers to a number the engineer types.
<b>Use capture mode</b> seeds new options from the default's values
instead of blanks.</li>
<li><b>Presence</b> (bottom of every property): pick a Boolean
subproperty and a condition, and the property exists only while it
says so — Port only on a three-way valve. The section outlines red
while conditional, the paper's own emphasis.</li>
<li><b>Test configuration</b> is the instance side: flip choices and
watch subproperties resolve, absent properties vanish, and the
online-subscription list shrink. An empty preview means the class
has no properties yet — add some first.</li>
<li><b>Save</b> writes <code>&lt;Class&gt;.pvmcfg.json</code> beside
the displays. From then on, placements of the class bind through it:
templates like <code>{Tag}/{Link.FB}/OUT</code> compose from the
properties, Presence-gated specs are never subscribed, and the
studio's Graphics Configuration pane grows a CONFIGURATION section
of your Selections. Save updates the engineering class; it does not publish
an operator display. Use Library Explorer &gt; Find Usages, then verify and
publish each affected display.</li>
</ol>
<p>The property tree follows mouse and keyboard selection. Use its search field
to find a property or group. Drag the pane dividers to make room for long
property names or the preview. The appearance preview renders at your screen's
resolution: choose <b>Fit</b> to inspect the complete PVM or <b>Actual size</b>
to keep its canvas dimensions (shrunk only when needed to fit). These are
offline previews; they do not connect to a controller.</p>

<h3>Professional typography workflow</h3>
<p>Coded process PVMs declare <b>Standard</b>, <b>Compact</b> and
<b>Large</b> typography profiles. Select <b>Typography</b> in the property
tree and edit the profile grid. Family columns accept an installed font
family; size columns accept exact points (<code>10</code> or
<code>10pt</code>) or a measured scale (<code>125%</code>). The semantic
columns independently govern tags, process values, secondary labels, mode
chips, scales and alarms.</p>
<p>A display placement stores only the profile name. Resizing continues to
scale the complete PVM geometry, while the class profile gives Authors exact,
project-wide control of readability. Save refreshes all linked instances in
open Graphics Designer displays; published operator displays pick up the class
contract through their normal publish/deployment workflow.</p>

<h3>How this works in Azeo</h3>
<p>The designer opens from Graphics Designer when editing a PVM class and
defines Selection properties, Presence, and Present Online. Reorder
properties from the tree's right-click menu —
the tree order is the pane order.</p>
"""


class _PreviewLabel(QLabel):
    resized = Signal()

    # QLabel derives both size hints and heightForWidth from its last pixmap.
    # Ignoring that policy alone makes Qt allocate less than minimumHeight at
    # 200% scaling, so the caption overlaps the image. Supply stable hints.
    def sizeHint(self):  # noqa: N802
        return QSize(220, 220)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(180, 200)

    def heightForWidth(self, _width):  # noqa: N802
        return 200

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.resized.emit()


class PvmConfigDesigner(QMainWindow):
    """Typed properties, class authoring tools and an offline appearance preview."""

    configuration_saved = Signal(str)

    def __init__(self, root: Path, pvm_class: str = "",
                 parent=None):
        super().__init__(parent)
        self.root = Path(root)
        log.info("PVM Configuration Designer opened: root=%s class=%s",
                 self.root.resolve(), pvm_class or "<default>")
        self.unsaved = False
        #: The library standards live beside the displays (the
        #: Library Explorer's store); read FRESH on every lookup so
        #: an edited standard re-resolves without touching the class
        #: — the no-republish asymmetry, demonstrable.
        self.standards_root = self.root.parent
        #: Capture mode: new options seed from the default option's
        #: values instead of blanks (the figure's checkbox, honest).
        self.capture_mode = False
        self.configs: dict[str, PvmConfiguration] = \
            sample_configurations()
        for path in configuration_document_paths(self.root):
            try:
                cfg = PvmConfiguration.load(path)
                self.configs[cfg.pvm_class] = cfg
            except Exception:               # noqa: BLE001
                log.warning("Unable to load PVM configuration %s", path,
                            exc_info=True)
        try:
            from azeo_control_trainer.core.hmi.pvms.base import registry
            from azeo_control_trainer.core.hmi.pvms.typography import ensure_typography
            for (block_type, role, variant), cls in sorted(
                    registry.all_classes().items()):
                config = self.configs.setdefault(
                    cls.__name__, _seed_from_class(cls))
                # Older coded-class sidecars acquire the profile contract in
                # memory. It reaches disk only when an Author saves.
                ensure_typography(config)
        except Exception:                       # noqa: BLE001
            log.warning("Unable to seed coded PVM configurations",
                        exc_info=True)
        try:
            # User PVM classes (Convert to PVM / Import SVG) are
            # configurable too — that is how a drawn valve gets its
            # ValveType Selection and Port Presence.
            from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
            for name in UserPvmLibrary(self.standards_root).names():
                self.configs.setdefault(
                    name, PvmConfiguration(
                        name,
                        [PropertyGroup("BasicConfiguration", [])]))
        except Exception:                       # noqa: BLE001
            log.warning("Unable to load user PVM configurations",
                        exc_info=True)
        self.current_class = pvm_class if pvm_class in self.configs \
            else next(iter(self.configs))
        self._saved_states = {
            name: self._snapshot(config)
            for name, config in self.configs.items()
        }
        self._history_states = dict(self._saved_states)
        self._undo_stacks = {name: [] for name in self.configs}
        self._redo_stacks = {name: [] for name in self.configs}
        self.selected: str | None = None        # property name
        self.selected_group: str | None = None

        self.setObjectName("cfg_root")
        self.setStyleSheet(_QSS)
        self.resize(1280, 800)

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_titlebar())
        column.addWidget(self._build_ribbon())

        body = self.body_splitter = QSplitter()
        body.setChildrenCollapsible(False)
        body.setHandleWidth(5)
        body.addWidget(self._build_tree_pane())
        body.addWidget(self._build_form_pane())
        body.addWidget(self._build_preview_pane())
        body.setSizes([270, 650, 330])
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 2)
        body.setStretchFactor(2, 1)
        column.addWidget(body, 1)
        self.setCentralWidget(central)

        # Keep Python wrappers for native shortcuts alive with the window.
        # Dropping/reconstructing them during modeless window churn has caused
        # native heap failures even though Qt still reports QObject children.
        self._shortcuts = [
            QShortcut(QKeySequence.Save, self, activated=self.save),
            QShortcut(QKeySequence.Undo, self, activated=self.undo),
            QShortcut(QKeySequence.Redo, self, activated=self.redo),
            QShortcut(QKeySequence(Qt.Key_F1), self,
                      activated=self.show_help),
        ]
        self._select_class(self.current_class)
        available = self.screen().availableGeometry()
        self.resize(min(1280, available.width() - 40),
                    min(800, available.height() - 80))

    def show_help(self):
        """The working guide — F1 or the Help ribbon button."""
        from PySide6.QtWidgets import QTextBrowser

        from azeo_control_trainer.core.presentation.help_style import styled_help_html
        dialog = getattr(self, "_help_dialog", None)
        if dialog is not None:
            try:
                if not is_headless():
                    dialog.show()
                    dialog.raise_()
                    dialog.activateWindow()
                return dialog
            except RuntimeError:
                self._help_dialog = None
        dialog = QDialog(self)
        dialog.setWindowTitle("PVM Configuration Designer — Help")
        dialog.resize(700, 560)
        lay = QVBoxLayout(dialog)
        add_authoring_dialog_header(
            dialog,
            lay,
            "PVM Configuration Designer help",
            "Property groups, choices, presence rules and online subscriptions.",
        )
        browser = QTextBrowser()
        browser.setHtml(styled_help_html(CONFIGURATOR_HELP))
        lay.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        style_dialog_buttons(buttons)
        lay.addWidget(buttons)
        self._help_dialog = dialog
        if not is_headless():
            dialog.show()
        return dialog

    def _build_preview_pane(self) -> QWidget:
        pane = QWidget()
        pane.setMinimumWidth(200)
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(12, 0, 12, 12)
        lay.addWidget(_band("PVM preview"))
        preview_tools = QHBoxLayout()
        preview_tools.addWidget(QLabel("View"))
        self.preview_scale = AuthoringComboBox()
        self.preview_scale.addItems(["Fit", "Actual size"])
        self.preview_scale.setToolTip("Fit enlarges the PVM for inspection; Actual size uses its canvas dimensions.")
        self.preview_scale.currentTextChanged.connect(lambda _text: self._update_preview())
        preview_tools.addWidget(self.preview_scale, 1)
        lay.addLayout(preview_tools)
        self.preview_label = _PreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumSize(180, 200)
        # Pixmap size must never become the window's minimum size on a resize.
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet(
            f"background: {CW['chrome']}; color: {CW['tx2']};"
            f"border: 1px solid {CW['bd_lt']}; border-radius: 6px; padding: 8px;")
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._update_preview)
        self.preview_label.resized.connect(self._preview_timer.start)
        lay.addWidget(self.preview_label, 1)
        note = QLabel("Appearance preview · not connected")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {CW['tx2']}; border: none;")
        lay.addWidget(note)
        self.preview_profile_label = QLabel("Typography preview")
        self.preview_profile = AuthoringComboBox()
        self.preview_profile.currentTextChanged.connect(lambda _name: self._update_preview())
        lay.addWidget(self.preview_profile_label)
        lay.addWidget(self.preview_profile)
        self.preview_facts = QLabel("")
        self.preview_facts.setWordWrap(True)
        self.preview_facts.setStyleSheet(f"color: {CW['tx2']}; border: none;")
        lay.addWidget(self.preview_facts)
        lay.addWidget(_section_head("Validation"))
        self.validation_label = QLabel("")
        self.validation_label.setWordWrap(True)
        self.validation_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.validation_label)
        # At 150–200% Windows scaling, preview + facts + validation can be
        # taller than the entire logical desktop. Keep that content reachable
        # by scrolling instead of imposing its minimum height on the window.
        scroll = QScrollArea()
        scroll.setObjectName("cfg_preview_scroll")
        scroll.setMinimumWidth(230)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(pane)
        return scroll

    def validate_configuration(self, show_dialog: bool = False):
        """Refresh the always-visible preflight and optionally explain it."""
        issues = self.config.issues()
        if issues:
            preview = "\n".join(f"• {issue}" for issue in issues[:6])
            if len(issues) > 6:
                preview += f"\n• … {len(issues) - 6} more"
            self.validation_label.setText(
                f"{len(issues)} issue(s) block Save\n{preview}")
            self.validation_label.setStyleSheet(
                f"color: {CW['red']}; font-size: 9.5pt; border: none;")
        else:
            self.validation_label.setText("Ready to save · no structural issues")
            self.validation_label.setStyleSheet(
                f"color: {CW['green']}; font-size: 9.5pt; border: none;")
        if show_dialog and not is_headless():
            QMessageBox.information(
                self, "PVM configuration validation",
                "\n".join(str(issue) for issue in issues)
                if issues else "No structural issues were found.")
        return issues

    def _update_preview(self) -> None:
        # A class switch or explicit preview supersedes a queued edit/resize.
        self._preview_timer.stop()
        # A broken authored member must not let an exception cross a Qt signal
        # callback and close the application while an engineer edits a field.
        try:
            self._render_preview()
            self.preview_label.setProperty("previewError", "")
        except Exception as error:  # noqa: BLE001
            log.exception("Unable to preview PVM configuration %s", self.current_class)
            self.preview_label.setText("Preview unavailable\nReview the class layout and bindings.")
            self.preview_label.setProperty("previewError", str(error))
            self.preview_facts.setText("The configuration can still be edited and validated.")
        self.validate_configuration()

    def _render_preview(self) -> None:
        from azeo_control_trainer.azeo_graphics_designer.component_icons import authored_preview
        from azeo_control_trainer.core.hmi.pvms.base import registry
        from azeo_control_trainer.core.hmi.pvms.typography import TYPOGRAPHY_PROPERTY, resolve_typography

        name = self.current_class
        library = self._user_library()
        entry = library.entries.get(name)
        width = max(80, self.preview_label.contentsRect().width())
        height = max(80, self.preview_label.contentsRect().height())
        dpr = self.preview_label.devicePixelRatioF()
        fit = self.preview_scale.currentText() == "Fit"
        if entry:
            self.preview_profile_label.hide()
            self.preview_profile.hide()
            pixmap = authored_preview(library, name, self.config, width, height,
                                      dpr=dpr, fit=fit)
            shapes = sum(d.get("kind") != "pipe" for d in entry["items"])
            facts = (f"User drawing PVM · {shapes} shape(s), "
                     f"{len(entry['items']) - shapes} pipe(s)")
        else:
            cls = next((c for c in registry.all_classes().values() if c.__name__ == name), None)
            if cls is None:
                self.preview_profile_label.hide()
                self.preview_profile.hide()
                self.preview_label.setText("Configuration example\n\nThis sample defines properties and has no drawing attached.")
                self.preview_facts.setText("Use Test configuration to try its options and presence rules.")
                return
            prop = self.config.property(TYPOGRAPHY_PROPERTY)
            names = [option.name for option in prop.options] if prop is not None else []
            selected = self.preview_profile.currentText()
            with QSignalBlocker(self.preview_profile):
                self.preview_profile.clear()
                self.preview_profile.addItems(names)
                if selected in names:
                    self.preview_profile.setCurrentText(selected)
                elif prop is not None and prop.default in names:
                    self.preview_profile.setCurrentText(prop.default)
            self.preview_profile_label.setVisible(bool(names))
            self.preview_profile.setVisible(bool(names))
            profile_name = self.preview_profile.currentText() or "Standard"
            typography = resolve_typography(self.config, {TYPOGRAPHY_PROPERTY: profile_name})
            pixmap = _coded_class_preview(cls, width, height, typography=typography,
                                          dpr=dpr, fit=fit, strict=True)
            facts = (f"{cls.block_type} / {cls.role}"
                     + (f" · {cls.variant}" if cls.variant else "")
                     + f" — {len(cls.bindings)} binding(s)"
                     + f" · typography: {typography.name}")
        self.preview_label.setPixmap(pixmap)
        self.preview_facts.setText(facts)

    # ------------------------------------------------------- chrome

    def _build_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("cfg_header")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 10, 14, 10)
        icon = QLabel()
        icon.setPixmap(command_icon("designer", 28).pixmap(28, 28))
        lay.addWidget(icon)
        self.title_label = QLabel("PVM Configuration Designer")
        self.title_label.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.title_label, 1)
        label = QLabel("PVM class")
        self.class_combo = AuthoringComboBox()
        self.class_combo.setAccessibleName("PVM class")
        self.class_combo.setMinimumWidth(220)
        self.class_combo.setMaximumWidth(360)
        self.class_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.class_combo.setMinimumContentsLength(22)
        label.setBuddy(self.class_combo)
        from azeo_control_trainer.azeo_graphics_designer.component_icons import pvm_icon
        from azeo_control_trainer.core.hmi.pvms.base import registry
        classes = {cls.__name__: cls for cls in registry.all_classes().values()}
        for name in sorted(self.configs):
            glyph = pvm_icon(classes[name]) if name in classes else command_icon("templates", 20)
            self.class_combo.addItem(glyph, name)
        self.class_combo.currentTextChanged.connect(self._select_class)
        lay.addWidget(label)
        lay.addWidget(self.class_combo)
        self.save_button = QPushButton(command_icon("save", 20), "Save")
        self.save_button.setToolTip("Save this class configuration (Ctrl+S)")
        self.save_button.setStyleSheet(
            f"QPushButton {{ background: {CW['sel']}; color: {CW['blue_d']};"
            f"border: 1px solid {CW['sel_br']}; padding: 7px 16px; font-weight: 600; }}")
        self.save_button.clicked.connect(self.save)
        lay.addWidget(self.save_button)
        return bar

    def _build_ribbon(self) -> QWidget:
        ribbon = QWidget()
        ribbon.setObjectName("cfg_toolbar")
        lay = QHBoxLayout(ribbon)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(3)
        self.command_buttons = {}
        commands = [
            ("group", "add_group", "Add group", "Organize related properties in a new group", self.add_group),
            ("property", "add_property", "Add property", "Choose a typed property to add to the selected group", self._add_property_menu),
            ("delete", "delete", "Delete", "Delete the selected property or group", self.delete_selected),
            ("composite", "templates", "New PVM", "Create a composite PVM and open its drawing in Graphics Designer", self.new_composite),
            ("layout", "exec_edit", "Edit layout", "Edit this authored class on the Graphics Designer canvas", self.edit_layout),
            ("faceplate", "faceplate", "New faceplate", "Create a faceplate blueprint and its companion PVM", self.new_faceplate_blueprint),
            ("port", "connect", "Add port", "Add a normalized connection point to this class", self.add_connection_point),
            ("preview", "show", "Test configuration", "Try instance options, presence rules and online subscriptions", self.preview),
            ("impact", "validate", "Change impact", "Compare saved and proposed class values across affected displays", self.review_impact),
            ("validate", "validate", "Validate", "Check the configuration before saving", lambda: self.validate_configuration(True)),
            ("undo", "undo", "Undo", "Undo the last change to this class (Ctrl+Z)", self.undo),
            ("redo", "redo", "Redo", "Redo the last undone change (Ctrl+Y)", self.redo),
            ("help", "help", "Help", "PVM Configuration Designer guide (F1)", self.show_help),
        ]
        for key, glyph, label, tooltip, handler in commands:
            if key in ("composite", "preview", "undo", "help"):
                divider = QFrame()
                divider.setFrameShape(QFrame.VLine)
                divider.setStyleSheet(f"color: {CW['bd_lt']};")
                lay.addWidget(divider)
            button = QToolButton()
            button.setObjectName(f"cfg_command_{key}")
            button.setIcon(command_icon(glyph))
            button.setIconSize(QSize(22, 22))
            button.setText(label)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setToolTip(tooltip)
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, action=handler: action())
            self.command_buttons[key] = button
            lay.addWidget(button)
        blueprint_menu = QMenu(self.command_buttons["faceplate"])
        self._blueprint_menu = blueprint_menu
        self._blueprint_actions = [
            blueprint_menu.addAction("Analog faceplate + PVM", self.new_faceplate_blueprint),
            blueprint_menu.addAction("Procedure HMI: PVM, faceplate and detail", self.new_procedure_blueprint),
        ]
        self.command_buttons["faceplate"].setMenu(blueprint_menu)
        self.command_buttons["faceplate"].setPopupMode(QToolButton.MenuButtonPopup)
        self.add_property_button = self.command_buttons["property"]
        self.undo_button = self.command_buttons["undo"]
        self.redo_button = self.command_buttons["redo"]
        lay.addStretch(1)
        # Long labels may scroll, but must never force the native window past
        # the desktop width (especially with Windows display scaling).
        scroll = QScrollArea()
        scroll.setObjectName("cfg_commands")
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(ribbon)
        scroll.setFixedHeight(ribbon.sizeHint().height() + 16)
        return scroll

    # ------------------------------------------------- composite PVMs

    def _user_library(self):
        from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
        return UserPvmLibrary(self.standards_root)

    def is_user_class(self, name: str | None = None) -> bool:
        return (name or self.current_class) \
            in self._user_library().names()

    def add_connection_point(self, x: float | None = None,
                             y: float | None = None,
                             name: str = "") -> str | None:
        """Add one normalized point to the class, inherited by instances."""
        if x is None or y is None:
            if is_headless():
                x, y = 0.5, 0.5
            else:
                x, ok = QInputDialog.getDouble(
                    self, "Connection point", "Horizontal position (0-1):",
                    0.5, 0.0, 1.0, 3)
                if not ok:
                    return None
                y, ok = QInputDialog.getDouble(
                    self, "Connection point", "Vertical position (0-1):",
                    0.5, 0.0, 1.0, 3)
                if not ok:
                    return None
        created = self.config.add_connection_point(float(x), float(y), name)
        self._mark_unsaved()
        self._update_preview()
        return created

    def new_composite(self, name: str = ""):
        """Create a composite PVM class from scratch: the class is
        seeded with a starter shape, opens here for properties, and
        opens on the studio canvas for layout — add any symbols,
        shapes and pipes there; Save captures them back."""
        if not name:
            if is_headless():
                return None
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self, "New Composite PVM", "Class name:")
            if not ok or not name:
                return None
        name = name.strip().replace(" ", "")
        entry = self._user_library().create(name)
        if entry is None:
            return None
        self.configs.setdefault(
            name, PvmConfiguration(
                name, [PropertyGroup("BasicConfiguration", [])]))
        snapshot = self._snapshot(self.configs[name])
        self._saved_states[name] = snapshot
        self._history_states[name] = snapshot
        self._undo_stacks[name] = []
        self._redo_stacks[name] = []
        if self.class_combo.findText(name) < 0:
            self.class_combo.addItem(command_icon("templates", 20), name)
        self.class_combo.setCurrentText(name)
        self.edit_layout(name)
        return entry

    @staticmethod
    def _faceplate_configuration(name: str) -> PvmConfiguration:
        """Typed inputs consumed by the Studio-authored blueprint."""
        props = [
            PvmProperty(
                "ControlTag", "Function Block Reference",
                title="Control tag",
                description="Primary PID-family block placed on the canvas",
                required=True, drop_target=True,
                accepted_block_types=[
                    "PID", "PID_AT", "PID_DEADTIME", "FLC"]),
            PvmProperty("ModuleName", "String", title="Module name",
                        default="MODULE"),
            PvmProperty("Title", "String", title="Faceplate title",
                        default="PID LOOP"),
            PvmProperty("Description", "String", title="Description",
                        default="Process controller"),
            PvmProperty("PVPath", "Parameter Reference", title="PV path"),
            PvmProperty(
                "SPPath", "Parameter Reference", title="SP path",
                description="Checked operator setpoint write target",
                direction=OPERATOR_WRITE),
            PvmProperty("OUTPath", "Parameter Reference", title="OUT path"),
            PvmProperty("ModulePath", "Control Tag",
                        title="Alarm module path"),
            PvmProperty("EU0", "Number", title="PV scale EU0",
                        default="0"),
            PvmProperty("EU100", "Number", title="PV scale EU100",
                        default="100"),
            PvmProperty("UnitName", "String", title="Unit name",
                        default="UNIT"),
        ]
        return PvmConfiguration(name, [
            PropertyGroup("Faceplate", props, present_online=True),
        ], connection_points=[
            {"name": "north", "x": 0.5, "y": 0.0},
            {"name": "south", "x": 0.5, "y": 1.0},
        ])

    def new_faceplate_blueprint(self, name: str = ""):
        """Create a professional faceplate plus its compact calling PVM."""
        if not name:
            if is_headless():
                return None
            name, ok = QInputDialog.getText(
                self, "New Faceplate Blueprint", "Class name:")
            if not ok or not name:
                return None
        name = name.strip().replace(" ", "")
        library = self._user_library()
        entry = library.create_faceplate_blueprint(name)
        if entry is None:
            return None
        pvm_name = f"{name}_PVM"
        companion = library.create_pvm_blueprint(pvm_name, name)
        if companion is None:
            # The faceplate has only just been created in this operation, so
            # rolling it back is safer than leaving an uncallable half-pair.
            library.remove(name)
            return None
        config = self._faceplate_configuration(name)
        config.save(self.root)
        pvm_config = self._faceplate_configuration(pvm_name)
        pvm_config.save(self.root)
        self.configs[name] = config
        self.configs[pvm_name] = pvm_config
        snapshot = self._snapshot(config)
        self._saved_states[name] = snapshot
        self._history_states[name] = snapshot
        self._undo_stacks[name] = []
        self._redo_stacks[name] = []
        companion_snapshot = self._snapshot(pvm_config)
        self._saved_states[pvm_name] = companion_snapshot
        self._history_states[pvm_name] = companion_snapshot
        self._undo_stacks[pvm_name] = []
        self._redo_stacks[pvm_name] = []
        for class_name in (name, pvm_name):
            if self.class_combo.findText(class_name) < 0:
                self.class_combo.addItem(command_icon("templates", 20), class_name)
        self.class_combo.setCurrentText(name)
        self.edit_layout(name)
        return entry

    def new_procedure_blueprint(self, name: str = ""):
        from azeo_control_trainer.core.hmi.pvms.procedure_blueprint import create_blueprint, configuration
        if not name:
            if is_headless():
                return None
            name, accepted = QInputDialog.getText(self, "Procedure HMI Blueprint", "Class name:")
            if not accepted or not name:
                return None
        name = name.strip().replace(" ", "")
        library = self._user_library()
        try:
            names = create_blueprint(library, name)
        except ValueError as error:
            self.statusBar().showMessage(str(error))
            return None
        for class_name in names:
            config = configuration(class_name)
            config.save(self.root)
            self.configs[class_name] = config
            snapshot = self._snapshot(config)
            self._saved_states[class_name] = snapshot
            self._history_states[class_name] = snapshot
            self._undo_stacks[class_name] = []
            self._redo_stacks[class_name] = []
            if self.class_combo.findText(class_name) < 0:
                self.class_combo.addItem(command_icon("templates", 20), class_name)
        self.class_combo.setCurrentText(name)
        self.edit_layout(name)
        return library.entries[name]

    def edit_layout(self, name: str = ""):
        """Open the class's shapes on the studio canvas (the
        class_edit pattern). Only user classes have an editable
        layout — a coded class's drawing is its painter."""
        name = name or self.current_class
        if not self.is_user_class(name):
            return None
        host = self.parent()
        opener = getattr(host, "edit_user_pvm_layout", None)
        return opener(name) if callable(opener) else None

    # --------------------------------------------------------- panes
    def _build_tree_pane(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("cfg_tree")
        pane.setMinimumWidth(180)
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_band("PVM Properties"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a property or group…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Find a property or group")
        self.search.textChanged.connect(self._filter_tree)
        search_box = QWidget()
        srow = QHBoxLayout(search_box)
        srow.setContentsMargins(5, 5, 5, 2)
        srow.addWidget(self.search)
        lay.addWidget(search_box)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._tree_clicked)
        self.tree.setIconSize(QSize(20, 20))
        self.tree.setAccessibleName("PVM properties")
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            self._prop_tree_menu)
        lay.addWidget(self.tree, 1)
        return pane

    def _build_form_pane(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("cfg_form")
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_band("Configuration"))
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.form_scroll, 1)
        return pane

    # ------------------------------------------------------- the tree
    @property
    def config(self) -> PvmConfiguration:
        return self.configs[self.current_class]

    def _select_class(self, name: str) -> None:
        if name not in self.configs:
            return
        self.current_class = name
        dirty = self._snapshot(self.config) != self._saved_states.get(name)
        self.title_label.setText(
            f"{name} - PVM Configuration Designer"
            + (" •" if dirty else ""))
        self.save_button.setText("Save •" if dirty else "Save")
        with QSignalBlocker(self.class_combo):
            self.class_combo.setCurrentText(name)
        self.setWindowTitle(f"{name} — PVM Configuration Designer")
        can_edit = self.is_user_class(name) and callable(
            getattr(self.parent(), "edit_user_pvm_layout", None))
        self.command_buttons["layout"].setVisible(can_edit)
        first = next((p.name for g in self.config.groups
                      for p in g.properties), None)
        if first is not None:
            self.selected, self.selected_group = first, None
        elif self.config.groups:
            # A class with no properties yet: select its group so the
            # pane shows SOMETHING to work on — a blank window reads
            # as broken, not as empty.
            self.selected = None
            self.selected_group = self.config.groups[0].name
        else:
            self.selected = self.selected_group = None
        self.reload_tree()
        self._show_form()
        try:
            self._update_preview()
        except Exception:                           # noqa: BLE001
            log.exception("Unable to refresh the PVM configuration preview")
        self._sync_history_buttons()

    def reload_tree(self) -> None:
        # Rebuilding emits currentItemChanged(None). Block it until both the
        # visual current item and the editor's selection refer to the new tree.
        with QSignalBlocker(self.tree):
            self.tree.clear()
            root = QTreeWidgetItem(self.tree, ["PVM configuration"])
            root.setIcon(0, command_icon("designer", 20))
            current = root
            for group in self.config.groups:
                group_item = QTreeWidgetItem(root, [group.name])
                group_item.setIcon(0, command_icon("open", 20))
                group_item.setData(0, Qt.UserRole, ("group", group.name))
                if group.name == self.selected_group:
                    current = group_item
                for prop in group.properties:
                    scope = "  [INTERNAL]" if prop.scope == INTERNAL else ""
                    target = "  DROP TARGET" if prop.drop_target else ""
                    item = QTreeWidgetItem(group_item, [f"{prop.name}{scope}{target}"])
                    item.setIcon(0, property_icon(prop.ptype))
                    item.setToolTip(0, f"{prop.name} · {prop.ptype}{scope}{target}")
                    item.setData(0, Qt.UserRole, ("prop", prop.name))
                    if prop.name == self.selected:
                        current = item
                group_item.setExpanded(True)
            root.setExpanded(True)
            self.tree.setCurrentItem(current)
        self._filter_tree(self.search.text())
        self.tree.scrollToItem(current)

    def _filter_tree(self, text: str) -> None:
        needle = text.strip().lower()

        def walk(item) -> bool:
            keep = needle in item.text(0).lower() if needle else True
            child_kept = False
            for i in range(item.childCount()):
                child_kept |= walk(item.child(i))
            item.setHidden(bool(needle) and not (keep or child_kept))
            return keep or child_kept

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _tree_clicked(self, item, _col) -> None:
        payload = item.data(0, Qt.UserRole) if item is not None else None
        if not payload:
            self.selected = self.selected_group = None
            self._show_form()
            return
        kind, name = payload
        if kind == "prop":
            self.selected, self.selected_group = name, None
        else:
            self.selected, self.selected_group = None, name
        self._show_form()

    # ------------------------------------------------------- the form
    @staticmethod
    def _snapshot(config: PvmConfiguration) -> str:
        return json.dumps(config.to_dict(), sort_keys=True,
                          ensure_ascii=False)

    def _dirty_classes(self) -> list[str]:
        return [name for name, config in self.configs.items()
                if self._snapshot(config) != self._saved_states.get(name)]

    def _mark_unsaved(self) -> None:
        name = self.current_class
        current = self._snapshot(self.config)
        previous = self._history_states.get(name)
        if previous is not None and previous != current:
            stack = self._undo_stacks.setdefault(name, [])
            stack.append(previous)
            del stack[:-100]
            self._redo_stacks.setdefault(name, []).clear()
            self._history_states[name] = current
        self.unsaved = bool(self._dirty_classes())
        self.save_button.setText("Save •")
        self.title_label.setText(
            f"{self.current_class} - PVM Configuration Designer •")
        self.validate_configuration()
        # Keep editing/validation immediate; render only the latest appearance
        # after a burst of edits using the same bounded resize debounce.
        self._preview_timer.start()
        self._sync_history_buttons()

    def _sync_history_buttons(self) -> None:
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(bool(
                self._undo_stacks.get(self.current_class)))
            self.redo_button.setEnabled(bool(
                self._redo_stacks.get(self.current_class)))

    def _restore_history(self, snapshot: str) -> None:
        self.configs[self.current_class] = PvmConfiguration.from_dict(
            json.loads(snapshot))
        self._history_states[self.current_class] = snapshot
        if self.selected and self.config.property(self.selected) is None:
            self.selected = None
        if self.selected_group and not any(
                group.name == self.selected_group
                for group in self.config.groups):
            self.selected_group = None
        self.unsaved = bool(self._dirty_classes())
        dirty = self.current_class in self._dirty_classes()
        self.save_button.setText("Save •" if dirty else "Save")
        self.title_label.setText(
            f"{self.current_class} - PVM Configuration Designer"
            + (" •" if dirty else ""))
        self.reload_tree()
        self._show_form()
        self._update_preview()
        self._sync_history_buttons()

    def undo(self) -> bool:
        stack = self._undo_stacks.setdefault(self.current_class, [])
        if not stack:
            return False
        current = self._snapshot(self.config)
        self._redo_stacks.setdefault(self.current_class, []).append(current)
        self._restore_history(stack.pop())
        return True

    def redo(self) -> bool:
        stack = self._redo_stacks.setdefault(self.current_class, [])
        if not stack:
            return False
        current = self._snapshot(self.config)
        self._undo_stacks.setdefault(self.current_class, []).append(current)
        self._restore_history(stack.pop())
        return True

    def _show_form(self) -> None:
        form = QWidget()
        form.setObjectName("cfg_form")
        lay = QVBoxLayout(form)
        lay.setContentsMargins(14, 10, 14, 20)
        lay.setSpacing(8)
        if self.selected_group is not None:
            self._group_form(lay)
        elif self.selected is not None:
            self._property_form(lay)
        else:
            self._empty_form(lay)
        lay.addStretch(1)
        self.form_scroll.setWidget(form)

    def _empty_form(self, lay) -> None:
        """The guided empty state — the designer explains itself
        instead of presenting a blank pane."""
        title = QLabel("Select a property or group" if self.config.groups else "No configuration yet")
        title.setFont(QFont("Segoe UI", 15))
        title.setStyleSheet("font-size: 15pt; font-weight: 600; border: none;")
        lay.addWidget(title)
        text = QLabel(
            "Select an item in the tree to edit its configuration. Use "
            "<b>Add group</b> and <b>Add property</b> to extend the class:<br><br>"
            "• a <b>Selection</b> gives the engineer named options "
            "(“Left”, never 270°) whose columns drive many "
            "values at once;<br>"
            "• <b>Presence</b> shows a property only while a Boolean "
            "subproperty says so;<br>"
            "• <b>Present Online</b> on a group keeps its parameters "
            "unsubscribed while unused.<br><br>"
            "The PVM's <i>graphics</i> are authored in Graphics "
            "Studio — draw or import, select, and Convert to PVM. "
            "This designer defines the configuration pane those "
            "instances show.")
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        text.setStyleSheet(f"color: {CW['tx2']}; font-size: 9.5pt;"
                           "border: none;")
        lay.addWidget(text)
        add_group = QPushButton(command_icon("add_group"), "Add property group")
        add_group.clicked.connect(self.add_group)
        lay.addWidget(add_group)
        add_prop = QPushButton(command_icon("add_property"), "Add property")
        add_prop.clicked.connect(self._add_property_menu)
        lay.addWidget(add_prop)

    def _row(self, lay, label: str, widget) -> None:
        row = QFormLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setRowWrapPolicy(QFormLayout.WrapLongRows)
        row.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        row.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        text = QLabel(label)
        text.setMinimumWidth(135)
        text.setBuddy(widget)
        text.setStyleSheet("border: none;")
        row.addRow(text, widget)
        lay.addLayout(row)

    def _group_form(self, lay) -> None:
        group = next(g for g in self.config.groups
                     if g.name == self.selected_group)
        title = QLabel(group.name)
        title.setFont(QFont("Segoe UI", 15))
        title.setStyleSheet("font-size: 15pt; font-weight: 600; border: none;")
        lay.addWidget(title)
        name = QLineEdit(group.name)
        name.editingFinished.connect(
            lambda g=group, field=name:
            self._rename_group(g.name, field.text()))
        self._row(lay, "Name", name)
        lay.addWidget(_section_head("Runtime"))
        self.present_online_box = QCheckBox("Present Online")
        self.present_online_box.setChecked(group.present_online)

        def toggled(on):
            group.present_online = bool(on)
            self._mark_unsaved()
        self.present_online_box.toggled.connect(toggled)
        lay.addWidget(self.present_online_box)
        note = QLabel("Load this group at runtime and subscribe to its parameters. "
                      "Clear this option to leave the group and its subscriptions "
                      "out of the running display.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {CW['tx2']}; font-size: 9.5pt;"
                           "border: none;")
        lay.addWidget(note)
        if not group.properties:
            lay.addWidget(_section_head("Getting started"))
            hint = QLabel(
                "This group is empty. <b>Add Property</b> puts a "
                "typed property here — a Selection for named "
                "options, a Control Tag for the instance's binding, "
                "a Color that can reference a library Standard. The "
                "PVM's graphics are drawn in Graphics Designer; this "
                "pane defines what its instances configure.")
            hint.setWordWrap(True)
            hint.setTextFormat(Qt.RichText)
            hint.setStyleSheet(f"color: {CW['tx2']};"
                               "font-size: 9.5pt; border: none;")
            lay.addWidget(hint)
            add_prop = QPushButton(command_icon("new"), "Add property")
            add_prop.clicked.connect(self._add_property_menu)
            lay.addWidget(add_prop)

    def _property_form(self, lay) -> None:
        prop = self.config.property(self.selected)
        if prop is None:
            return
        head = QHBoxLayout()
        name_label = QLabel(prop.title or prop.name)
        name_label.setFont(QFont("Segoe UI", 15))
        name_label.setStyleSheet("font-size: 15pt; font-weight: 600; border: none;")
        head.addWidget(name_label, 1)
        type_icon = QLabel()
        type_icon.setPixmap(property_icon(prop.ptype).pixmap(20, 20))
        head.addWidget(type_icon)
        type_label = QLabel(prop.ptype)
        type_label.setStyleSheet(f"color: {CW['blue']};"
                                 "font-size: 9pt; border: none;")
        head.addWidget(type_label)
        lay.addLayout(head)
        name = QLineEdit(prop.name)
        name.editingFinished.connect(
            lambda p=prop, field=name:
            self._rename_property(p.name, field.text()))
        self._row(lay, "Name", name)
        lay.addWidget(_section_head("Information"))
        for label, key in (("Title", "title"),
                           ("Description", "description"),
                           ("Tooltip", "tooltip")):
            field = QLineEdit(getattr(prop, key))
            field.textChanged.connect(
                lambda text, p=prop, k=key:
                (setattr(p, k, text), self._mark_unsaved()))
            self._row(lay, label, field)

        lay.addWidget(_section_head("Instance Interface"))
        scope = AuthoringComboBox()
        scope.addItem("Public instance parameter", PUBLIC)
        scope.addItem("Internal class property", INTERNAL)
        scope.setCurrentIndex(max(0, scope.findData(prop.scope)))

        def scope_changed(_index, p=prop, editor=scope):
            p.scope = editor.currentData()
            if p.scope == INTERNAL:
                p.required = False
                p.drop_target = False
                p.accepted_block_types = []
                p.direction = READ_ONLY
            self._mark_unsaved()
            self.reload_tree()
            self._show_form()

        scope.currentIndexChanged.connect(scope_changed)
        self._row(lay, "Visibility", scope)

        required = QCheckBox("Required for each instance")
        required.setChecked(prop.required)
        required.setEnabled(prop.scope == PUBLIC and not prop.drop_target)
        required.toggled.connect(
            lambda on, p=prop:
            (setattr(p, "required", bool(on)), self._mark_unsaved()))
        self._row(lay, "Required", required)

        drop_target = QCheckBox("Primary drop target")
        drop_target.setToolTip("Primary target for a dragged control block or tag")
        drop_target.setChecked(prop.drop_target)
        drop_target.setEnabled(
            prop.scope == PUBLIC and prop.ptype in REFERENCE_TYPES)

        def target_changed(on, p=prop):
            if on:
                for other in self.config.all_properties():
                    if other is not p:
                        other.drop_target = False
                p.required = True
                p.direction = READ_ONLY
                required.setChecked(True)
            else:
                p.accepted_block_types = []
            p.drop_target = bool(on)
            required.setEnabled(p.scope == PUBLIC and not on)
            accepted.setEnabled(bool(on))
            direction.setEnabled(p.scope == PUBLIC and not on)
            direction.setCurrentIndex(
                max(0, direction.findData(p.direction)))
            self._mark_unsaved()
            self.reload_tree()

        drop_target.toggled.connect(target_changed)
        self._row(lay, "Drop Target", drop_target)

        accepted = QLineEdit(", ".join(prop.accepted_block_types))
        accepted.setPlaceholderText("PID, AI, AO  ·  * accepts any type")
        accepted.setEnabled(
            prop.ptype in REFERENCE_TYPES and prop.drop_target)

        def accepted_changed(p=prop, editor=accepted):
            values = []
            for token in editor.text().replace(";", ",").split(","):
                value = token.strip().upper()
                if value and value not in values:
                    values.append(value)
            p.accepted_block_types = values
            self._mark_unsaved()

        accepted.editingFinished.connect(accepted_changed)
        self._row(lay, "Accepted block types", accepted)

        direction = AuthoringComboBox()
        direction.addItem("Read only", READ_ONLY)
        direction.addItem("Operator write (checked service)",
                          OPERATOR_WRITE)
        direction.setCurrentIndex(max(0, direction.findData(prop.direction)))
        direction.setEnabled(prop.scope == PUBLIC and not prop.drop_target)
        direction.currentIndexChanged.connect(
            lambda _index, p=prop, editor=direction:
            (setattr(p, "direction", editor.currentData()),
             self._mark_unsaved()))
        self._row(lay, "Direction", direction)

        if prop.ptype == SELECTION_TYPE:
            self._selection_form(lay, prop)
        elif prop.ptype == "Boolean":
            self._boolean_form(lay, prop)
        elif prop.ptype in ("String", "Number", "Degree Angle",
                            "Measurement", "Color", "Font",
                            "Image", "Multi-language String") \
                + REFERENCE_TYPES:
            lay.addWidget(_section_head("Value"))
            default = QLineEdit(prop.default)
            default.textChanged.connect(
                lambda text, p=prop:
                (setattr(p, "default", text), self._mark_unsaved()))
            self._row(lay, "Default Value", default)
            options = self.standards_options(prop.ptype)
            if options:
                pick = QPushButton("Standard ▾")
                pick.setStyleSheet(
                    f"QPushButton {{ background: {CW['chrome']};"
                    f"border: 1px solid {CW['bd']};"
                    "padding: 2px 8px; }}")
                pick.clicked.connect(
                    lambda _=False, p=prop, f=default:
                    self._pick_standard(p, f))
                self._row(lay, "", pick)
            from azeo_control_trainer.core.hmi.pvms.configurator.model import (
                is_standard_ref,
                standard_name,
            )
            if is_standard_ref(prop.default):
                resolved = self.standards_lookup(
                    standard_name(prop.default))
                note = QLabel(
                    f"→ resolves to  {resolved}" if resolved
                    is not None else "→ standard not found — the "
                    "reference stays visible, never invented")
                note.setStyleSheet(f"color: {CW['tx2']};"
                                   "font-size: 9.5pt; border: none;")
                self._row(lay, "", note)
        self._presence_form(lay, prop)

    def _selection_form(self, lay, prop: PvmProperty) -> None:
        lay.addWidget(_section_head("Value"))
        count = QSpinBox()
        count.setRange(1, 32)
        count.setValue(len(prop.options))
        count.valueChanged.connect(
            lambda n, p=prop: self._set_option_count(p, n))
        self._row(lay, "Number of options", count)
        default = AuthoringComboBox()
        default.addItems([o.name for o in prop.options])
        default.setCurrentText(prop.default)
        default.currentTextChanged.connect(
            lambda text, p=prop:
            (setattr(p, "default", text), self._mark_unsaved()))
        self._row(lay, "Default Selection", default)

        buttons = QHBoxLayout()
        add_col = QPushButton("Add column ▾")
        add_col.setStyleSheet(
            f"QPushButton {{ background: {CW['chrome']}; border: 1px "
            f"solid {CW['bd']}; padding: 2px 8px; }}")
        add_col.clicked.connect(lambda: self._add_column(prop))
        buttons.addWidget(add_col)
        buttons.addStretch(1)
        self.capture_box = QCheckBox("Use capture mode")
        self.capture_box.setChecked(self.capture_mode)
        self.capture_box.setToolTip(
            "New options seed from the default option's values "
            "instead of blanks — capture what works, then vary it.")
        self.capture_box.toggled.connect(
            lambda on: setattr(self, "capture_mode", bool(on)))
        buttons.addWidget(self.capture_box)
        lay.addLayout(buttons)

        self.option_grid = QTableWidget(len(prop.options),
                                        len(prop.columns) + 1)
        self.option_grid.setHorizontalHeaderLabels(
            ["NAME"] + list(prop.columns))
        self.option_grid.verticalHeader().setVisible(False)
        for r, option in enumerate(prop.options):
            self.option_grid.setItem(
                r, 0, QTableWidgetItem(option.name))
            for c, value in enumerate(option.values):
                item = QTableWidgetItem(str(value))
                item.setFont(QFont("Consolas", 10))
                self.option_grid.setItem(r, c + 1, item)
        self.option_grid.itemChanged.connect(
            lambda item, p=prop: self._grid_edited(p, item))
        self.option_grid.setMinimumHeight(
            26 * (len(prop.options) + 1) + 8)
        # The figure's per-column ✕ lives on the header: right-click
        # a column for Rename / Remove; anywhere for Add.
        header = self.option_grid.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos, p=prop: self._grid_header_menu(p, pos))
        lay.addWidget(self.option_grid)

    def _grid_edited(self, prop: PvmProperty, item) -> None:
        r, c = item.row(), item.column()
        if r >= len(prop.options):
            return
        if c == 0:
            prop.options[r].name = item.text()
        else:
            values = prop.options[r].values
            while len(values) < len(prop.columns):
                values.append("")
            values[c - 1] = item.text()
        self._mark_unsaved()

    def _set_option_count(self, prop: PvmProperty, n: int) -> None:
        seed = prop.option(prop.default) if self.capture_mode \
            else None
        while len(prop.options) < n:
            values = list(seed.values) if seed is not None \
                else [""] * len(prop.columns)
            prop.options.append(
                Option(f"Option {len(prop.options) + 1}", values))
        del prop.options[n:]
        self._mark_unsaved()
        self._show_form()

    def _grid_header_menu(self, prop: PvmProperty, pos) -> None:
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        header = self.option_grid.horizontalHeader()
        column = header.logicalIndexAt(pos) - 1     # 0 is NAME
        menu = studio_menu("OPTION GRID", "Subproperty columns")
        menu.addAction("Add column…").triggered.connect(
            lambda: self._add_column(prop))
        if 0 <= column < len(prop.columns):
            name = prop.columns[column]
            menu.addSeparator()
            menu.addAction(f"Rename '{name}'…").triggered.connect(
                lambda _=False, p=prop, c=column:
                self.rename_column(p, c))
            remove = menu.addAction(f"✕  Remove '{name}'")
            remove.triggered.connect(
                lambda _=False, p=prop, c=column:
                self.remove_column_at(p, c))
        self._grid_menu = menu
        if not is_headless():
            menu.exec_transient(header.mapToGlobal(pos))

    def rename_column(self, prop: PvmProperty, index: int,
                      new_name: str = "") -> bool:
        """Rename a subproperty column. Presence and binding
        templates reference columns by name, so the note in the
        dialog is not decoration."""
        if not (0 <= index < len(prop.columns)):
            return False
        old = prop.columns[index]
        if not new_name:
            if is_headless():
                return False
            new_name, ok = QInputDialog.getText(
                self, "Rename column",
                f"New name for '{old}':\n(Presence conditions and "
                "binding templates referencing "
                f"'{prop.name}.{old}' must be updated too.)",
                text=old)
            if not ok or not new_name:
                return False
        prop.columns[index] = new_name
        self._mark_unsaved()
        self._show_form()
        return True

    def remove_column_at(self, prop: PvmProperty,
                         index: int) -> bool:
        if not (0 <= index < len(prop.columns)):
            return False
        prop.columns.pop(index)
        for option in prop.options:
            if index < len(option.values):
                option.values.pop(index)
        self._mark_unsaved()
        self._show_form()
        return True

    def move_property(self, name: str, delta: int) -> bool:
        """Reorder a property within its group — the tree order IS
        the configuration form's order."""
        group = self.config.group_of(name)
        if group is None:
            return False
        names = [p.name for p in group.properties]
        index = names.index(name)
        target = index + delta
        if not (0 <= target < len(group.properties)):
            return False
        properties = group.properties
        properties[index], properties[target] = \
            properties[target], properties[index]
        self._mark_unsaved()
        self.reload_tree()
        return True

    def _add_column(self, prop: PvmProperty) -> None:
        name = f"Column{len(prop.columns) + 1}"
        if not is_headless():
            name, ok = QInputDialog.getText(
                self, "Add column", "Subproperty name:", text=name)
            if not ok or not name:
                return
        prop.columns.append(name)
        for option in prop.options:
            option.values.append("")
        self._mark_unsaved()
        self._show_form()

    def _remove_column(self, prop: PvmProperty) -> None:
        if not prop.columns:
            return
        index = len(prop.columns) - 1
        if not is_headless():
            name, ok = QInputDialog.getItem(
                self, "Remove column", "Column:", prop.columns,
                index, False)
            if not ok:
                return
            index = prop.columns.index(name)
        prop.columns.pop(index)
        for option in prop.options:
            if index < len(option.values):
                option.values.pop(index)
        self._mark_unsaved()
        self._show_form()

    def _boolean_form(self, lay, prop: PvmProperty) -> None:
        lay.addWidget(_section_head("Value"))
        default = AuthoringComboBox()
        default.addItems(["True", "False"])
        default.setCurrentText(prop.default or "True")
        default.currentTextChanged.connect(
            lambda text, p=prop:
            (setattr(p, "default", text), self._mark_unsaved()))
        self._row(lay, "Default Value", default)
        presentation = AuthoringComboBox()
        presentation.addItems(["Combo box", "Check box", "Toggle"])
        presentation.setCurrentText(prop.presentation)
        presentation.currentTextChanged.connect(
            lambda text, p=prop:
            (setattr(p, "presentation", text), self._mark_unsaved()))
        self._row(lay, "Presentation", presentation)

    def _gate_paths(self) -> list:
        """Every Boolean-ish thing Presence could reference."""
        out = []
        for other in self.config.all_properties():
            if other.name == self.selected:
                continue
            if other.ptype == "Boolean":
                out.append(other.name)
            for column in other.columns:
                out.append(f"{other.name}.{column}")
        return out

    def _presence_form(self, lay, prop: PvmProperty) -> None:
        conditional = prop.presence.condition != ALWAYS \
            and bool(prop.presence.property)
        frame = QFrame()
        self.presence_frame = frame
        if conditional:
            frame.setStyleSheet(
                f"QFrame {{ border: 2px solid {CW['red']}; }}")
        else:
            frame.setStyleSheet("QFrame { border: none; }")
        flay = QVBoxLayout(frame)
        flay.setContentsMargins(4, 2, 4, 6)
        flay.addWidget(_section_head("Presence"))
        gate = AuthoringComboBox()
        gate.addItem("")
        gate.addItems(self._gate_paths())
        gate.setCurrentText(prop.presence.property)
        self._row(flay, "Property", gate)
        condition = AuthoringComboBox()
        condition.addItems([ALWAYS, WHEN_TRUE, WHEN_FALSE])
        condition.setCurrentText(prop.presence.condition)
        self._row(flay, "Condition", condition)

        def changed(*_a, p=prop, g=gate, c=condition):
            p.presence.property = g.currentText()
            p.presence.condition = c.currentText()
            self._mark_unsaved()
            self._show_form()        # re-evaluate the red outline
        gate.currentTextChanged.connect(changed)
        condition.currentTextChanged.connect(changed)
        lay.addWidget(frame)

    def _prop_tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        payload = item.data(0, Qt.UserRole) if item else None
        if not payload:
            return
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        kind, name = payload
        if kind == "prop":
            menu = studio_menu(f"PROPERTY  {name}", "Configuration")
            up = menu.addAction("Move up")
            up.triggered.connect(
                lambda _=False, n=name: self.move_property(n, -1))
            down = menu.addAction("Move down")
            down.triggered.connect(
                lambda _=False, n=name: self.move_property(n, +1))
            menu.addSeparator()
            menu.addAction("Delete").triggered.connect(
                lambda _=False, n=name:
                (setattr(self, "selected", n),
                 setattr(self, "selected_group", None),
                 self.delete_selected()))
        else:
            menu = studio_menu(f"GROUP  {name}", "Property group")
            menu.addAction("Add Property ▾").triggered.connect(
                lambda _=False, n=name:
                (setattr(self, "selected_group", n),
                 setattr(self, "selected", None),
                 self._add_property_menu()))
            menu.addSeparator()
            menu.addAction("Delete group").triggered.connect(
                lambda _=False, n=name:
                (setattr(self, "selected_group", n),
                 setattr(self, "selected", None),
                 self.delete_selected()))
        retain_menu(self, menu, "_tree_context_menu")
        if not is_headless():
            menu.exec_transient(self.tree.mapToGlobal(pos))

    # ----------------------------------------------------- standards
    def standards_options(self, ptype: str) -> list:
        """(name, value) of every library standard matching the
        property's type — read fresh from the store each call."""
        try:
            from azeo_control_trainer.core.hmi.pvms.standards import StandardsStore
            store = StandardsStore(self.standards_root)
            return [(e["name"], e["value"]) for e in store.entries
                    if e["type"] == ptype]
        except Exception:                           # noqa: BLE001
            return []

    def standards_lookup(self, name: str):
        """One standard's CURRENT value — late, live, per call."""
        try:
            from azeo_control_trainer.core.hmi.pvms.standards import StandardsStore
            entry = StandardsStore(self.standards_root).get(name)
            return entry["value"] if entry else None
        except Exception:                           # noqa: BLE001
            return None

    def set_standard_default(self, prop_name: str,
                             std_name: str) -> bool:
        """Point a property's default at a standard reference — the
        class stores the NAME; the value stays the library's."""
        from azeo_control_trainer.core.hmi.pvms.configurator.model import (
            STANDARD_PREFIX,
        )
        prop = self.config.property(prop_name)
        if prop is None:
            return False
        prop.default = f"{STANDARD_PREFIX}{std_name}"
        self._mark_unsaved()
        self._show_form()
        return True

    def _pick_standard(self, prop, field) -> None:
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        menu = studio_menu(f"STANDARDS  {prop.ptype}",
                           "Reference, not literal")
        for name, value in self.standards_options(prop.ptype):
            menu.addAction(f"{name}   ·   {value}") \
                .triggered.connect(
                    lambda _=False, n=name, p=prop, f=field:
                    (self.set_standard_default(p.name, n),
                     f.setText(p.default)))
        self._std_menu = menu
        if not is_headless():
            from PySide6.QtGui import QCursor
            menu.exec_transient(QCursor.pos())

    # ----------------------------------------------------- ribbon ops
    def _rename_group(self, old: str, new: str) -> bool:
        new = str(new).strip()
        if new == old:
            return True
        if not self.config.rename_group(old, new):
            self.validate_configuration()
            return False
        self.selected_group = new
        self._mark_unsaved()
        self.reload_tree()
        self._show_form()
        return True

    def _rename_property(self, old: str, new: str) -> bool:
        new = str(new).strip()
        if new == old:
            return True
        if not self.config.rename_property(old, new):
            self.validate_configuration()
            return False
        self.selected = new
        self._mark_unsaved()
        self.reload_tree()
        self._show_form()
        return True

    def add_group(self) -> None:
        index = len(self.config.groups) + 1
        names = {group.name for group in self.config.groups}
        while f"Group{index}" in names:
            index += 1
        name = f"Group{index}"
        if not is_headless():
            name, ok = QInputDialog.getText(
                self, "Add Property Group", "Group name:", text=name)
            if not ok or not name:
                return
        name = name.strip()
        if not name or name in names:
            return
        self.config.groups.append(PropertyGroup(name, []))
        self.selected, self.selected_group = None, name
        self._mark_unsaved()
        self.reload_tree()
        self._show_form()

    def add_property(self, ptype: str) -> None:
        group = next((g for g in self.config.groups
                      if g.name == self.selected_group),
                     self.config.group_of(self.selected or "")
                     or (self.config.groups[0]
                         if self.config.groups else None))
        if group is None:
            self.config.groups.append(
                PropertyGroup("BasicConfiguration", []))
            group = self.config.groups[0]
        base = "NewProperty"
        index = 1
        while self.config.property(f"{base}{index}") is not None:
            index += 1
        prop = PvmProperty(name=f"{base}{index}", ptype=ptype)
        if ptype == SELECTION_TYPE:
            prop.columns = ["Value"]
            prop.options = [Option("Option 1", [""]),
                            Option("Option 2", [""])]
            prop.default = "Option 1"
        group.properties.append(prop)
        self.selected, self.selected_group = prop.name, None
        self._mark_unsaved()
        self.reload_tree()
        self._show_form()

    def _add_property_menu(self) -> None:
        from azeo_control_trainer.core.presentation.menu_style \
            import studio_menu
        menu = studio_menu("ADD PROPERTY", "Pick a value type")
        menu.addSection("VALUE TYPES")
        for ptype in VALUE_TYPES:
            menu.addAction(
                property_icon(ptype), ptype
            ).triggered.connect(
                lambda _=False, t=ptype: self.add_property(t))
        menu.addSection("REFERENCES")
        for ptype in REFERENCE_TYPES:
            menu.addAction(
                property_icon(ptype), ptype
            ).triggered.connect(
                lambda _=False, t=ptype: self.add_property(t))
        menu.addSeparator()
        menu.addAction(property_icon(SELECTION_TYPE), "Selection").triggered.connect(
            lambda: self.add_property(SELECTION_TYPE))
        self._add_menu = menu
        if not is_headless():
            from PySide6.QtGui import QCursor
            menu.exec_transient(QCursor.pos())

    def delete_selected(self) -> None:
        if self.selected_group is not None:
            self.config.groups = [
                g for g in self.config.groups
                if g.name != self.selected_group]
            self.selected_group = None
        elif self.selected is not None:
            group = self.config.group_of(self.selected)
            if group is not None:
                group.properties = [p for p in group.properties
                                    if p.name != self.selected]
            self.selected = None
        self._mark_unsaved()
        self.reload_tree()
        self._show_form()

    def review_impact(self, name=None, *, saving=False):
        from .impact import ImpactDialog
        previous = getattr(self, "_impact_dialog", None)
        if previous is not None:
            previous.close()
            previous.deleteLater()
        dialog = ImpactDialog(self, name, saving=saving)
        self._impact_dialog = dialog
        if not is_headless() and not saving:
            dialog.show()
        return dialog

    def _confirm_impact(self, name):
        if self._saved_states.get(name) == self._snapshot(self.configs[name]):
            return True
        dialog = self.review_impact(name, saving=True)
        if dialog.blockers:
            if not is_headless():
                dialog.exec()
            return False
        if is_headless() or not dialog.instances:
            return True
        return dialog.exec() == QDialog.Accepted

    def save(self) -> Path | None:
        issues = self.validate_configuration()
        if issues:
            if not is_headless():
                QMessageBox.warning(
                    self, "Save refused",
                    "Repair the configuration issues before saving.\n\n"
                    + "\n".join(str(issue) for issue in issues))
            return None
        if not self._confirm_impact(self.current_class):
            return None
        try:
            path = self.config.save(self.root)
        except ConfigurationError as error:
            if not is_headless():
                QMessageBox.warning(self, "Save refused", str(error))
            return None
        snapshot = self._snapshot(self.config)
        self._saved_states[self.current_class] = snapshot
        self._history_states[self.current_class] = snapshot
        self.unsaved = bool(self._dirty_classes())
        self.save_button.setText("Save")
        self.title_label.setText(
            f"{self.current_class} - PVM Configuration Designer")
        self.configuration_saved.emit(self.current_class)
        return path

    def save_all(self) -> bool:
        """Persist every edited class or leave the window open."""
        for name in self._dirty_classes():
            config = self.configs[name]
            if not self._confirm_impact(name):
                return False
            try:
                config.save(self.root)
            except ConfigurationError as error:
                if not is_headless():
                    QMessageBox.warning(
                        self, f"Save refused — {name}", str(error))
                return False
            snapshot = self._snapshot(config)
            self._saved_states[name] = snapshot
            self._history_states[name] = snapshot
            self.configuration_saved.emit(name)
        self.unsaved = False
        return True

    def closeEvent(self, event) -> None:            # noqa: N802
        if not self.unsaved or is_headless():
            self._preview_timer.stop()
            event.accept()
            return
        answer = QMessageBox.question(
            self, "Unsaved PVM configuration",
            "Save the PVM configuration before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if answer == QMessageBox.Cancel:
            event.ignore()
        elif answer == QMessageBox.Save and not self.save_all():
            event.ignore()
        else:
            self._preview_timer.stop()
            event.accept()

    # -------------------------------------------------------- preview
    def preview(self) -> "PreviewDialog":
        previous = getattr(self, "_preview", None)
        if previous is not None:
            try:
                previous.close()
                previous.deleteLater()
            except RuntimeError:
                pass
        dialog = PreviewDialog(self.config, self)
        self._preview = dialog
        if not is_headless():
            dialog.show()
        return dialog


class PreviewDialog(QDialog):
    """The instance side: pick options, watch the class resolve.

    This is the whole argument made visible — the engineer picks
    "Left" and BodyRot says 270; flip ValveType to three-way and Port
    appears; a group with Present Online off never reaches the
    subscription list."""

    def __init__(self, config: PvmConfiguration, parent=None):
        super().__init__(parent)
        self.config = config
        self.choices: dict = {}
        self.setWindowTitle(f"{config.pvm_class} — Test configuration")
        self.setStyleSheet(_QSS)
        self.resize(760, 680)
        lay = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            lay,
            "Test configuration",
            f"Resolve instance choices and online subscriptions for {config.pvm_class}.",
        )
        lay.addWidget(_band("Choices — what the graphics engineer "
                            "sees"))
        self.pickers = QWidget()
        self.pick_lay = QVBoxLayout(self.pickers)
        self.pick_lay.setContentsMargins(8, 6, 8, 6)
        pick_scroll = QScrollArea()
        pick_scroll.setWidgetResizable(True)
        pick_scroll.setMaximumHeight(260)
        pick_scroll.setFrameShape(QFrame.NoFrame)
        pick_scroll.setWidget(self.pickers)
        lay.addWidget(pick_scroll)
        lay.addWidget(_band("Resolved — what the class does with it"))
        self.resolved_table = QTableWidget(0, 2)
        self.resolved_table.setHorizontalHeaderLabels(
            ["Property", "Value"])
        self.resolved_table.verticalHeader().setVisible(False)
        self.resolved_table.setColumnWidth(0, 260)
        self.resolved_table.horizontalHeader().setStretchLastSection(True)
        self.resolved_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.resolved_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.resolved_table, 1)
        self.absent_label = QLabel("")
        self.absent_label.setWordWrap(True)
        self.absent_label.setStyleSheet(
            f"color: {CW['tx3']}; font-size: 9.5pt; border: none;")
        lay.addWidget(self.absent_label)
        self.online_label = QLabel("")
        self.online_label.setWordWrap(True)
        self.online_label.setStyleSheet(
            f"color: {CW['blue']}; font-size: 9.5pt; border: none;")
        lay.addWidget(self.online_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(buttons)
        lay.addWidget(buttons)
        self._build_pickers()
        self.refresh()
        available = self.screen().availableGeometry()
        self.resize(min(760, available.width() - 40),
                    min(680, available.height() - 80))

    def _build_pickers(self) -> None:
        _dispose_layout(self.pick_lay)
        for prop in self.config.all_properties():
            if not self.config.is_present(prop.name, self.choices):
                continue
            if prop.ptype == SELECTION_TYPE:
                combo = AuthoringComboBox()
                combo.addItems([o.name for o in prop.options])
                combo.setCurrentText(
                    self.choices.get(prop.name, prop.default))
            elif prop.ptype == "Boolean":
                combo = AuthoringComboBox()
                combo.addItems(["True", "False"])
                combo.setCurrentText(
                    self.choices.get(prop.name,
                                     prop.default or "True"))
            elif prop.ptype in ("Number", "Degree Angle"):
                combo = QLineEdit(
                    self.choices.get(prop.name, prop.default))
            else:
                continue
            row = QHBoxLayout()
            label = QLabel(prop.title or prop.name)
            label.setMinimumWidth(180)
            label.setStyleSheet("border: none;")
            row.addWidget(label)
            row.addWidget(combo, 1)
            self.pick_lay.addLayout(row)
            if isinstance(combo, QComboBox):
                combo.currentTextChanged.connect(
                    lambda text, n=prop.name: self.set_choice(n, text))
            else:
                # Rebuilding on every keystroke destroys the focused editor
                # after the first digit. Commit the complete value on blur.
                combo.editingFinished.connect(
                    lambda editor=combo, n=prop.name:
                    self.set_choice(n, editor.text()))

    def set_choice(self, name: str, value: str) -> None:
        self.choices[name] = value
        self._build_pickers()       # presence may have changed
        self.refresh()

    def refresh(self) -> None:
        from azeo_control_trainer.core.hmi.pvms.configurator.model import (
            is_standard_ref, resolve_standard_refs, standard_name,
        )
        resolved = self.config.resolved(self.choices)
        lookup = getattr(self.parent(), "standards_lookup", None)
        display = resolve_standard_refs(resolved, lookup) \
            if callable(lookup) else dict(resolved)
        self.resolved_table.setRowCount(len(display))
        for i, (key, value) in enumerate(sorted(display.items())):
            self.resolved_table.setItem(i, 0, QTableWidgetItem(key))
            text = str(value)
            raw = resolved.get(key)
            if is_standard_ref(raw) and text != raw:
                # Resolved LIVE through the library — edit the
                # standard and this changes with no republish.
                text += f"   · via {standard_name(raw)}"
            item = QTableWidgetItem(text)
            item.setFont(QFont("Consolas", 10))
            self.resolved_table.setItem(i, 1, item)
        absent = [p.name for p in self.config.all_properties()
                  if not self.config.is_present(p.name, self.choices)]
        self.absent_label.setText(
            "Absent (Presence): " + (", ".join(absent) or "none"))
        online = self.config.online_references(self.choices)
        offline = [g.name for g in self.config.groups
                   if not g.present_online]
        self.online_label.setText(
            "Online subscriptions: " + (", ".join(online) or "none")
            + ("   ·   never loaded (Present Online off): "
               + ", ".join(offline) if offline else ""))
