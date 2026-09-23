"""Procedure commands composed from the suite's shared ribbon components."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.ribbon import (
    RIBBON_TAB_STYLE, RibbonGroup, RibbonPage, SmallRibbonButton,
)
from azeo_control_trainer.core.presentation.studio_icons import studio_icon


class ActionButton(SmallRibbonButton):
    def __init__(self, action, label, mark):
        super().__init__(label.replace("\n", " "), mark, action.toolTip() or action.text())
        self.action = action
        self.clicked.connect(action.trigger)
        action.changed.connect(self.sync_action)
        self.sync_action()

    def sync_action(self):
        self.setEnabled(self.action.isEnabled())


class ProcedureRibbon(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.buttons = {}
        self.pages = {}
        self.groups = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tabs = QTabBar(objectName="RibbonTabBar")
        self.tabs.setStyleSheet(RIBBON_TAB_STYLE)
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.stack = QStackedWidget()
        self.stack.setFixedHeight(74)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.addWidget(self.tabs, 1)
        self.collapse_action = QAction("Collapse ribbon", self)
        self.collapse_action.setIcon(studio_icon("collapse_ribbon", 16))
        self.collapse_action.setToolTip("Collapse or expand the ribbon (Ctrl+F1)")
        self.collapse_action.setCheckable(True)
        self.collapse_action.setShortcut(QKeySequence("Ctrl+F1"))
        window.addAction(self.collapse_action)
        self.collapse_button = SmallRibbonButton(
            "Collapse ribbon", tip="Collapse or expand the ribbon (Ctrl+F1)"
        )
        self.collapse_button.setDefaultAction(self.collapse_action)
        self.collapse_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.collapse_button.setFixedWidth(34)
        tab_row.addWidget(self.collapse_button)
        self.collapse_action.toggled.connect(self.set_collapsed)
        layout.addLayout(tab_row)
        layout.addWidget(self.stack)
        self.tabs.currentChanged.connect(self.select_page)
        self.tabs.tabBarDoubleClicked.connect(self.toggle_collapsed)

        def group(page_name, title):
            if page_name not in self.pages:
                page = RibbonPage()
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.NoFrame)
                scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                scroll.setFixedHeight(74)
                scroll.setWidget(page)
                scroll.horizontalScrollBar().rangeChanged.connect(
                    lambda _low, high, s=scroll: self.size_page(s, high))
                self.pages[page_name] = page
                self.stack.addWidget(scroll)
                self.tabs.addTab(page_name)
            result = RibbonGroup(title)
            self.pages[page_name].addGroup(result)
            self.groups[(page_name, title)] = result
            return result

        pending = {}

        def place(target, button):
            column = pending.setdefault(target, [])
            column.append(button)
            if len(column) == 2:
                target.addSmallButtonColumn(*column)
                pending.pop(target)

        def action_button(target, key, action, label, mark):
            button = ActionButton(action, label, mark)
            place(target, button)
            self.buttons[key] = button
            return button

        def command(target, key, label, mark, callback):
            button = SmallRibbonButton(label.replace("\n", " "), mark)
            button.clicked.connect(callback)
            place(target, button)
            self.buttons[key] = button
            return button

        document = group("Home", "Procedure")
        for label, caption, mark in (("New procedure", "New", "new"), ("Open selected procedure", "Open", "open"),
                                     ("Save revision", "Save\nrevision", "save"), ("Validate procedure", "Validate", "compile")):
            action_button(document, label, window.menu_actions[label], caption, mark)
        edit = group("Home", "Edit")
        for key, label, mark in (("Undo", "Undo", "undo"), ("Redo", "Redo", "redo")):
            action_button(edit, key, window.menu_actions[key], label, mark)
        for key, label, mark in (("cut", "Cut", "cut"), ("copy", "Copy", "copy"), ("paste", "Paste", "paste"),
                                 ("duplicate", "Duplicate", "copy"), ("delete", "Delete\nBlock", "delete")):
            button = action_button(edit, key, window.block_actions[key], label, mark)
            if key in {"duplicate", "delete"}:
                window.step_buttons["Duplicate" if key == "duplicate" else "Delete Block"] = button

        insert = group("Procedure", "Insert")
        column = QHBoxLayout()
        column.addWidget(window.add_type)
        add = SmallRibbonButton("Add step", "new")
        add.clicked.connect(window.add_step)
        column.addWidget(add)
        insert.addLayout(column)
        window.step_buttons["Add step"] = add
        command(insert, "library", "Block\nlibrary", "templates", window.focus_block_library)
        definition = group("Procedure", "Definition")
        for index, label, mark in ((1, "Properties", "properties"), (2, "Tag\nmappings", "assign_io"), (3, "Variables", "values")):
            command(definition, label, label, mark, lambda _checked=False, i=index: window.tabs.setCurrentIndex(i))
        order = group("Procedure", "Execution order")
        for label, delta, mark in (("Move up", -1, "undo"), ("Move down", 1, "redo")):
            window.step_buttons[label] = command(order, label, label, mark, lambda _checked=False, d=delta: window.move_step(d))
        engineering = group("Procedure", "Engineering")
        for key, label, mark in (
            ("Rename symbol / Find usages", "Find /\nRename", "properties"),
            ("Extract reusable procedure", "Extract\nReusable", "templates"),
            ("Compare revisions", "Compare", "compare"),
            ("Add engineering annotation", "Annotate", "comment"),
            ("Review and release", "Review /\nRelease", "compile"),
        ):
            action_button(engineering, key, window.menu_actions[key], label, mark)

        view = group("View", "Workflow")
        command(view, "workflow", "Workflow", "procedure", lambda: window.show_workflow_view(0))
        command(view, "list", "Step list", "values", lambda: window.show_workflow_view(1))
        command(view, "fit", "Zoom\nto Fit", "zoom_fit", window.canvas.zoom_fit)
        command(view, "reset", "100%", "restore", window.canvas.zoom_reset)
        command(view, "arrange", "Auto\nArrange", "auto", window.canvas.arrange)
        action_button(view, "align", window.align_action, "Align\nleft", "align")
        help_group = group("Help", "Reference")
        for key, label in (("getting_started", "Getting\nstarted"), ("workflow", "Visual\nworkflow"),
                           ("engineering", "Engineering\nworkflows"),
                           ("blocks", "Block\nreference"), ("troubleshooting", "Trouble-\nshooting"), ("shortcuts", "Keyboard\nshortcuts")):
            command(help_group, "help:" + key, label, "comment", lambda _checked=False, k=key: window.open_help_topic(k))
        for target, column in pending.items():
            target.addSmallButtonColumn(*column)
        for page in self.pages.values():
            page.addStretch()
        self.tabs.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)

    def select_page(self, index):
        self.stack.setCurrentIndex(index)
        self.collapse_action.setChecked(False)
        self.stack.show()
        self.stack.setFixedHeight(self.stack.currentWidget().height())

    def toggle_collapsed(self, _index):
        self.collapse_action.trigger()

    def set_collapsed(self, collapsed):
        self.stack.setVisible(not collapsed)
        self.collapse_action.setText("Expand ribbon" if collapsed else "Collapse ribbon")
        self.collapse_action.setIcon(studio_icon(
            "expand_ribbon" if collapsed else "collapse_ribbon", 16
        ))

    def size_page(self, scroll, maximum):
        # A narrow desktop needs room for the horizontal scrollbar as well as
        # both command rows; hiding the lower row would leave enabled actions invisible.
        scroll.setFixedHeight(74 + (scroll.horizontalScrollBar().sizeHint().height() if maximum else 0))
        if self.stack.currentWidget() is scroll:
            self.stack.setFixedHeight(scroll.height())
