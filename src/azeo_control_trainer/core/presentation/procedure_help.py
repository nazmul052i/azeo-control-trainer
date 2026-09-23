"""Modeless searchable procedure help, available offline in source and packages."""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from azeo_control_trainer.core.presentation.dialog_layout import fit_dialog_to_screen
from azeo_control_trainer.core.presentation.engineering_dialog import ENGINEERING_QSS, polish_dialog
from azeo_control_trainer.core.presentation.help_style import styled_help_html
from azeo_control_trainer.core.procedures.help_content import help_topics


class ProcedureHelpCenter(QDialog):
    def __init__(self, parent=None, *, topics=None, title="PA Designer Help",
                 initial="getting_started"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(1000, 690)
        self.setStyleSheet(ENGINEERING_QSS)
        self.topics = help_topics() if topics is None else topics
        self._search_text = {key: topic.search_text for key, topic in self.topics.items()}
        self._history, self._history_index = [], -1
        self.current_topic_key = ""
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search workflows, block properties, examples or troubleshooting…")
        self.search.setAccessibleName("Search " + title)
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("Help topics")
        self._items = {}
        categories = {}
        for key, topic in self.topics.items():
            if topic.category not in categories:
                category = QTreeWidgetItem(self.tree, [topic.category])
                category.setFlags(category.flags() & ~Qt.ItemIsSelectable)
                categories[topic.category] = category
            item = QTreeWidgetItem(categories[topic.category], [topic.title])
            item.setData(0, Qt.UserRole, key)
            self._items[key] = item
        self.tree.expandAll()
        split.addWidget(self.tree)
        self.browser = QTextBrowser()
        self.browser.setAccessibleName("Help content")
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.open_link)
        split.addWidget(self.browser)
        split.setSizes([280, 700])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.back = QPushButton("Back")
        self.forward = QPushButton("Forward")
        self.back.clicked.connect(lambda: self.navigate(-1))
        self.forward.clicked.connect(lambda: self.navigate(1))
        row.addWidget(self.back)
        row.addWidget(self.forward)
        self.results = QLabel()
        row.addWidget(self.results)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.tree.currentItemChanged.connect(self.topic_changed)
        self.search.textChanged.connect(self.filter_topics)
        self.filter_topics("")
        self.show_topic(initial)
        polish_dialog(
            self,
            title=title,
            subtitle="Search the offline procedure-authoring and block reference.",
            mark="comment",
        )
        from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
        bind_operator_theme(self, tool_controls=True)

    def _render_help(self, body):
        self._help_body = body
        self.browser.setHtml(styled_help_html(body, theme=getattr(self, "_hmi_theme_name", None)))

    def apply_operator_theme(self, theme):
        position = self.browser.verticalScrollBar().value()
        cursor = self.browser.textCursor()
        anchor, end = cursor.anchor(), cursor.position()
        self._render_help(getattr(self, "_help_body", ""))
        from PySide6.QtGui import QTextCursor
        cursor = self.browser.textCursor()
        cursor.setPosition(min(anchor, self.browser.document().characterCount() - 1))
        cursor.setPosition(min(end, self.browser.document().characterCount() - 1), QTextCursor.KeepAnchor)
        self.browser.setTextCursor(cursor)
        self.browser.verticalScrollBar().setValue(position)

    def show_topic(self, key, *, remember=True):
        if key not in self.topics:
            return False
        item = self._items[key]
        if item.isHidden():
            self.search.clear()
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self.tree.blockSignals(False)
        self.current_topic_key = key
        if remember and (self._history_index < 0 or self._history[self._history_index] != key):
            self._history = (self._history[:self._history_index + 1] + [key])[-128:]
            self._history_index = len(self._history) - 1
        topic = self.topics[key]
        self._render_help(f"<h2>{escape(topic.title)}</h2>" + topic.html)
        self.browser.verticalScrollBar().setValue(0)
        self.back.setEnabled(self._history_index > 0)
        self.forward.setEnabled(self._history_index + 1 < len(self._history))
        return True

    def topic_changed(self, item, _previous):
        if item and item.data(0, Qt.UserRole):
            self.show_topic(item.data(0, Qt.UserRole))

    def filter_topics(self, query):
        words = query.casefold().split()
        visible = []
        for key, item in self._items.items():
            match = all(word in self._search_text[key] for word in words)
            item.setHidden(not match)
            if match:
                visible.append(key)
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            group.setHidden(all(group.child(i).isHidden() for i in range(group.childCount())))
            if words and not group.isHidden():
                group.setExpanded(True)
        self.results.setText(f"{len(visible)} topics")
        if not visible:
            self._render_help("<h2>No matching topic</h2><p>Try a block name, property or command.</p>")
        elif self.current_topic_key not in visible:
            self.show_topic(visible[0])
        else:
            self.show_topic(self.current_topic_key, remember=False)

    def open_link(self, url):
        if url.scheme() == "help":
            self.show_topic(url.path() or url.host())

    def navigate(self, delta):
        target = self._history_index + delta
        if 0 <= target < len(self._history):
            self._history_index = target
            self.show_topic(self._history[target], remember=False)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        fit_dialog_to_screen(self)
