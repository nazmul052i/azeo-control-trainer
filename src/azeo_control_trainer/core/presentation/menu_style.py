"""One professional context-menu style for every Azeo engineering application.

The designer grew five context menus with three different inline styles —
two selection blues and a third for the tree. This module is the single
style, modelled on Azeo Control Designer's menus (user reference
screenshot): a **titled header band** naming what was clicked, grouped
sections, an icon column on the left, shortcut hints on the right, and a
quiet light-blue hover instead of a saturated fill.

Usage at a call site is one line each way:

    menu = studio_menu(f"{block_type}  {name}", "PID Control", parent)
    menu.exec_transient(global_position)

Transient menus use ``exec_transient`` for cleanup; persistent ribbon menus
use Qt's inherited ``popup``/``exec`` and remain reusable.

Icons attach themselves: on ``aboutToShow`` every action's label is matched
against `_ICON_FOR` and given the ribbon's own vector glyph — the same
`_draw_icon` the ribbon buttons use, so the vocabulary cannot drift. A
label with no good glyph simply gets none; a fallback icon on everything
would be decoration, not information.

Shortcut hints are display text (``"Cut\\tCtrl+X"``), never live
``QAction`` shortcuts — the designer already owns its key bindings, and a
menu that *registers* shortcuts would fire them twice.
"""
from __future__ import annotations

import weakref
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMenu, QWidgetAction

from .brand import AUTHORING_BLUE, AUTHORING_HOVER, AUTHORING_SELECTION

MENU_QSS = f"""
QMenu {{
    background: #ffffff;
    color: #2b323b;
    border: 1px solid #b7bec8;
    padding: 4px 0 4px 0;
    font-size: 9pt;
}}
QMenu::item {{
    padding: 5px 26px 5px 10px;
    background: transparent;
}}
QMenu::item:selected {{
    background: {AUTHORING_HOVER};
    color: {AUTHORING_BLUE};
}}
QMenu::item:disabled {{ color: #9aa4b0; }}
QMenu::separator {{
    height: 1px;
    background: #e2e6ec;
    margin: 4px 10px 4px 10px;
}}
QMenu::icon {{ padding-left: 6px; }}
"""

_HEADER_QSS = f"""
QLabel {{
    background: {AUTHORING_SELECTION};
    color: {AUTHORING_BLUE};
    border-bottom: 1px solid {AUTHORING_BLUE};
    padding: 6px 12px 6px 10px;
    font-size: 9pt;
}}
"""

#: Action-label prefix -> ribbon icon name. Prefix match, so
#: "Force value...  (2 active)" still finds its glyph.
_ICON_FOR = (
    # Control Designer's complete menu vocabulary. Put specific labels before
    # general verbs ("Save Checkpoint" before "Save") so prefix matching
    # cannot assign a plausible-looking but incorrect glyph.
    ("Save Checkpoint", "checkpoint"),
    ("Restore Checkpoint", "restore"),
    ("Print Preview", "print"),
    ("Module Report", "print"),
    ("Close Modules to the Right", "deactivate"),
    ("Close Other Modules", "deactivate"),
    ("Close All Modules", "deactivate"),
    ("Close Module", "deactivate"),
    ("Close Project", "disconnect"),
    ("Close Window", "deactivate"),
    ("Show Execution Order", "exec_order"),
    ("Show Wire Values", "values"),
    ("Hide Unused Pins", "hide"),
    ("Show All Pins", "show"),
    ("Compare Parameters", "compare"),
    ("Compare Strategies", "compare"),
    ("Controller Simulator", "simulator"),
    ("Controller Diagnostics", "diagnostics"),
    ("Module Properties", "module_props"),
    ("Module Parameters", "params"),
    ("Cross Reference", "xref"),
    ("Data Log Configuration", "datalog"),
    ("Version History", "history"),
    ("Template Manager", "templates"),
    ("Control Module Classes", "templates"),
    ("Monitoring Tab", "datalog"),
    ("Keyboard Shortcuts", "params"),
    ("Block Reference", "xref"),
    ("Control Designer Help", "comment"),
    ("SFC Programming Guide", "templates"),
    ("Equipment Modules", "templates"),
    ("PID & BKCAL Wiring", "connect"),
    ("Design Patterns", "templates"),
    ("Knowledge Builder", "compile"),
    ("About Control Designer", "properties"),
    ("Function Block", "new"),
    ("Create Composite from Selection", "templates"),
    ("Remove Block and Heal Connection", "connect"),
    ("Insert Block into Wire", "connect"),
    ("Reconnect Source", "connect"),
    ("Reconnect Destination", "connect"),
    ("Start Branch from Source", "connect"),
    ("Find Block", "search"),
    ("Zoom to Fit", "zoom_fit"),
    ("Zoom In", "zoom_in"),
    ("Zoom Out", "zoom_out"),
    ("Auto Arrange", "auto"),
    ("Named Sets", "named_sets"),
    ("Upload", "upload"),
    ("Go On Line", "connect"),
    ("Go Off Line", "disconnect"),
    ("Undo", "undo"),
    ("Redo", "redo"),
    ("Print", "print"),
    # Explorer verbs (longest prefixes first — first match wins).
    ("Plant Simulator Status", "status"),
    ("Plant Simulator Starting", "connect"),
    ("Plant Simulator Stopping", "disconnect"),
    ("Retry Plant Simulator", "undo"),
    ("Start Plant Simulator", "connect"),
    ("Restart Simulator", "undo"),
    ("Start Simulator", "connect"),
    ("Stop Simulator", "disconnect"),
    ("Provider Status", "status"),
    ("Virtual I/O", "assign_io"),
    ("Total Download", "download"),
    ("Download Control Network", "download"),
    ("Control Designer", "exec_edit"),
    ("Open Control Designer", "exec_edit"),
    ("Graphics Designer", "faceplate"),
    ("Open Graphics Designer", "faceplate"),
    ("PA Designer", "procedure"),
    ("Open in PA Designer", "procedure"),
    ("Open PA Designer", "procedure"),
    ("Operator Station", "simulator"),
    ("Tag Database", "named_sets"),
    ("Controller Status", "status"),
    ("PVM Configuration Designer", "properties"),
    ("Configuration", "properties"),
    ("Identify", "show"),
    ("Decommission", "disconnect"),
    ("Commission", "connect"),
    ("Auto-sense", "assign_io"),
    ("Insert into open module", "new"),
    ("Insert into current procedure", "new"),
    ("Enable all", "connect"),
    ("Disable all", "disconnect"),
    ("Enable", "connect"),
    ("Disable", "disconnect"),
    ("Expand all", "show"),
    ("Collapse all", "hide"),
    ("New Area", "new"),
    ("Open trend", "datalog"),
    ("Edit value", "params"),
    ("Publish", "upload"),
    ("Verify", "compile"),
    ("Exit", "deactivate"),
    ("About", "properties"),
    ("Diagnose", "diagnostics"),
    ("Properties", "properties"),
    ("Open Faceplate", "faceplate"),
    ("Diagnostics", "diagnostics"),
    ("Find References", "xref"),
    ("References", "xref"),
    ("Add to Watch", "watch"),
    ("Watch", "watch"),
    ("Force value", "params"),
    ("Release all forces", "params"),
    ("Documentation", "search"),
    ("Block Help", "search"),
    ("Cut", "cut"),
    ("Copy", "copy"),
    ("Paste", "paste"),
    ("Duplicate", "copy"),
    ("Delete", "delete"),
    ("Rename", "module_props"),
    ("New", "new"),
    ("Open", "open"),
    ("Save", "save"),
    ("Download", "download"),
    ("Go Online", "connect"),
    ("Go Offline", "disconnect"),
    ("Compile", "compile"),
    ("Refresh", "undo"),
    ("Expand All", "show"),
    ("Collapse All", "hide"),
    ("Select All", "select_all"),
    ("Align", "align"),
    ("Auto", "auto"),
    ("Comment", "comment"),
    ("Text", "comment"),
    ("Mode", "exec_order"),
    ("Block Scan Rate", "exec_order"),
    ("Execution Order", "exec_order"),
    ("Mark as BKCAL", "connect"),
    ("Unmark BKCAL", "disconnect"),
    ("Delete Wire", "delete"),
    ("Export", "save_as"),
    ("Assign I/O", "assign_io"),
    ("Trend", "datalog"),
)


def _attach_icons(menu: QMenu) -> None:
    from .studio_icons import studio_icon
    from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme, operator_service
    from azeo_control_trainer.core.hmi.theme.roles import Role
    service = operator_service(menu)
    color = service.palette[Role.ACTION] if service else None

    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            if bind_operator_theme(submenu) is None:
                submenu.setStyleSheet(MENU_QSS)
            _attach_icons(submenu)
        if action.isSeparator() or not action.icon().isNull():
            continue
        # Qt uses a single ampersand for a mnemonic and a doubled ampersand
        # for a visible one.  Preserve the latter so labels such as
        # ``PID & BKCAL Wiring`` still receive their intended icon.
        label = action.text().split("\t", 1)[0]
        label = label.replace("&&", "\0").replace("&", "").replace("\0", "&")
        for prefix, icon_name in _ICON_FOR:
            if label.casefold().startswith(prefix.casefold()):
                action.setIcon(studio_icon(icon_name, 16, color))
                break


class StudioMenu(QMenu):
    def __init__(self, parent=None):
        super().__init__(parent)
        # A lambda capturing this menu forms a cycle through Qt's signal slot.
        self.aboutToShow.connect(self._prepare)

    def _prepare(self):
        _attach_icons(self)

    def exec_transient(self, *args):
        """Run a one-shot context menu and release its actions afterwards."""
        try:
            # QMenu.exec has both static and instance overloads. Calling it
            # through super() from an exec override loses the native receiver
            # in PySide and crashes before aboutToShow. Keep exec inherited.
            return self.exec(*args)
        finally:
            # exec is used for transient context menus, not ribbon submenus.
            if shiboken6.isValid(self):
                self.deleteLater()


def retain_menu(owner, menu: QMenu, attribute: str = "_context_menu") -> QMenu:
    """Keep one inspectable context menu, disposing the previous invocation."""
    previous = getattr(owner, attribute, None)
    if isinstance(previous, QMenu) and previous is not menu and shiboken6.isValid(previous):
        previous.hide()
        previous.deleteLater()
    if menu.parent() is None:
        # QWidget.setParent otherwise drops Qt.Popup: the menu stays open
        # after selecting an action and its nested event loop never returns.
        menu.setParent(owner, menu.windowFlags())
    setattr(owner, attribute, menu)
    owner_ref, menu_ref = weakref.ref(owner), weakref.ref(menu)

    def forgotten(*_):
        parent, old = owner_ref(), menu_ref()
        if parent is not None and getattr(parent, attribute, None) is old:
            setattr(parent, attribute, None)

    menu.destroyed.connect(forgotten)
    return menu


def studio_menu(title: str = "", subtitle: str = "",
                parent=None) -> StudioMenu:
    """A styled QMenu with the Azeo-style header band.

    `title` is the identity ("PID  FIC-101"), `subtitle` the human name
    ("PID Control"); they render as one band, em-dash joined, exactly the
    reference layout. Icons and submenu styling attach on first show, so
    call sites build their actions as they always did.
    """
    menu = StudioMenu(parent)
    menu.setStyleSheet(MENU_QSS)
    if title:
        text = f"<b>{title}</b>"
        if subtitle:
            text += f" &nbsp;—&nbsp; {subtitle}"
        label = QLabel(text)
        label.setStyleSheet(_HEADER_QSS)
        label.setTextFormat(Qt.RichText)
        header = QWidgetAction(menu)
        header.setDefaultWidget(label)
        header.setEnabled(False)
        menu.addAction(header)
    return menu
