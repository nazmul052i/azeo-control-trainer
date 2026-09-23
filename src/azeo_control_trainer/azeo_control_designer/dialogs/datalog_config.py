"""Data-log configuration UI guarded by an explicit consumer capability.

Persisting a list of points is not data logging. The dialog is writable only
when its consumer implements ``apply_data_log_configuration`` and
``take_data_log_snapshot``; without that contract it remains visible as an
honest unavailable capability and cannot create a configuration nobody reads.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDoubleSpinBox,
    QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

log = logging.getLogger("strategy.datalog")


@dataclass(frozen=True)
class DataLoggingCapability:
    """Whether a concrete collector owns and consumes this configuration."""

    available: bool
    consumer_name: str = ""
    reason: str = ""


def data_logging_capability(consumer=None) -> DataLoggingCapability:
    """Describe the real data-log consumer available to Control Designer."""
    if consumer is None:
        return DataLoggingCapability(
            False,
            reason=("No data-log consumer is attached. Process History uses "
                    "its separately configured continuous historian."),
        )
    configure = getattr(consumer, "apply_data_log_configuration", None)
    snapshot = getattr(consumer, "take_data_log_snapshot", None)
    if not callable(configure) or not callable(snapshot):
        return DataLoggingCapability(
            False,
            consumer_name=type(consumer).__name__,
            reason=("The attached historian does not implement the Control "
                    "Studio data-log consumer contract."),
        )
    return DataLoggingCapability(
        True, consumer_name=type(consumer).__name__,
        reason="Configuration is owned by an attached data-log consumer.",
    )


def is_data_logging_available(consumer=None) -> bool:
    """Capability hook used by menus/ribbons to hide unavailable commands."""
    return data_logging_capability(consumer).available

_DLG_STYLE = f"""
QDialog {{
    background: {UI.chrome};
}}
QLabel#dlgTitle {{
    color: {UI.blue};
    font-size: 11pt;
    font-weight: bold;
    padding: 4px 0;
}}
QFrame#headerBar {{
    background: {UI.chrome};
    border: 1px solid {UI.border};
    border-radius: 2px;
    padding: 4px 8px;
}}
QGroupBox {{
    font-size: 9pt;
    font-weight: bold;
    color: {UI.blue};
    border: 1px solid {UI.border};
    border-radius: 3px;
    margin-top: 10px;
    padding-top: 16px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QTabWidget::pane {{
    border: 1px solid {UI.border};
    background: {UI.pane};
}}
QTabBar::tab {{
    background: {UI.chrome};
    color: {UI.blue};
    border: 1px solid {UI.border};
    border-bottom: none;
    padding: 6px 16px;
    font-size: 9pt;
    font-weight: bold;
}}
QTabBar::tab:selected {{
    background: {UI.pane};
    border-bottom: 2px solid {UI.blue};
}}
QTableWidget {{
    background: #FFFFFF;
    alternate-background-color: {UI.pane};
    color: {UI.text};
    font-size: 9pt;
    border: 1px solid {UI.border};
    gridline-color: {UI.border_light};
}}
QTableWidget::item {{ padding: 3px 6px; }}
QHeaderView::section {{
    background: {UI.chrome};
    color: {UI.blue};
    font-size: 9pt;
    font-weight: bold;
    border: 1px solid {UI.border};
    padding: 4px 6px;
}}
QListWidget {{
    background: #FFFFFF;
    border: 1px solid {UI.border};
    font-size: 9pt;
}}
QListWidget::item {{
    padding: 3px 6px;
}}
QListWidget::item:selected {{
    background: {UI.blue};
    color: white;
}}
"""

_BTN_STYLE = (
    f"QPushButton {{ background: {UI.chrome}; color: {UI.blue}; "
    f"border: 1px solid {UI.border}; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    f"QPushButton:hover {{ background: {UI.chrome_alt}; }}"
    f"QPushButton:disabled {{ background: {UI.border_light}; color: #999; }}"
)

_BTN_PRIMARY = (
    "QPushButton { background: #1565C0; color: #FFFFFF; "
    "border: 1px solid #0D47A1; border-radius: 3px; "
    "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
    "QPushButton:hover { background: #1976D2; }"
    "QPushButton:disabled { background: #78909C; color: #CCC; }"
)


class DataLogConfigDialog(QDialog):
    """Configure historian tag recording."""

    def __init__(self, store=None, historian=None, plugin=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._historian = historian
        self._plugin = plugin
        self._capability = data_logging_capability(historian)

        # Data log configuration: tag -> settings dict
        self._tag_config: dict[str, dict] = {}
        self._load_config()

        self.setWindowTitle("Data Log Configuration")
        self.setMinimumSize(900, 550)
        self.resize(1000, 650)
        self.setStyleSheet(_DLG_STYLE)

        self._build_ui()
        self._apply_capability_state()
        self._refresh_all()

    def _load_config(self):
        """Load data log config from settings."""
        import json
        config_path = self._config_path()
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self._tag_config = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._tag_config = {}

    def _save_config(self):
        """Save data log config to settings."""
        if not self._capability.available:
            raise RuntimeError(self._capability.reason)
        import json
        config_path = self._config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self._tag_config, f, indent=2)

    def _config_path(self) -> Path:
        from azeo_control_trainer.config.paths import data_dir
        return data_dir() / "datalog_config.json"

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Header
        hdr = QFrame()
        hdr.setObjectName("headerBar")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(8, 4, 8, 4)
        title = QLabel("Data Log Configuration")
        title.setObjectName("dlgTitle")
        hdr_lay.addWidget(title)
        hdr_lay.addStretch()
        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet(f"color: {UI.text_secondary}; font-size: 9pt;")
        hdr_lay.addWidget(self._lbl_status)
        layout.addWidget(hdr)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_tags_tab(), "Tag Selection")
        self._tabs.addTab(self._build_settings_tab(), "Recording Settings")
        self._tabs.addTab(self._build_stats_tab(), "Database Statistics")
        layout.addWidget(self._tabs, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()

        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setStyleSheet(_BTN_PRIMARY)
        self._btn_apply.clicked.connect(self._apply_config)
        btn_row.addWidget(self._btn_apply)

        self._btn_snapshot = QPushButton("Take Snapshot Now")
        self._btn_snapshot.setStyleSheet(_BTN_STYLE)
        self._btn_snapshot.clicked.connect(self._take_snapshot)
        btn_row.addWidget(self._btn_snapshot)

        btn_row.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(_BTN_STYLE)
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _apply_capability_state(self):
        """Prevent an unattached configuration from masquerading as active."""
        available = self._capability.available
        self._tabs.setEnabled(available)
        self._btn_apply.setEnabled(available)
        self._btn_snapshot.setEnabled(available)
        if not available:
            self._lbl_status.setText(f"Unavailable — {self._capability.reason}")
            self._lbl_status.setToolTip(self._capability.reason)

    def _build_tags_tab(self) -> QWidget:
        """Tag selection: available tags (left) -> recorded tags (right)."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(6)

        info = QLabel(
            "Select tags to record in the historian database. "
            "Use the arrow buttons to add or remove tags from recording.")
        info.setWordWrap(True)
        info.setStyleSheet(f"font-size: 9pt; color: {UI.text_secondary}; padding: 4px;")
        lay.addWidget(info)

        # Filter
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._txt_filter = QLineEdit()
        self._txt_filter.setPlaceholderText("Search tags...")
        self._txt_filter.setClearButtonEnabled(True)
        self._txt_filter.setStyleSheet(
            f"QLineEdit {{ background: #FFF; border: 1px solid {UI.border}; "
            "border-radius: 3px; padding: 4px; font-size: 9pt; }")
        self._txt_filter.textChanged.connect(self._filter_available)
        filter_row.addWidget(self._txt_filter, 1)
        lay.addLayout(filter_row)

        # Dual list with arrows
        dual = QHBoxLayout()

        # Available tags
        avail_lay = QVBoxLayout()
        avail_lay.addWidget(QLabel("Available Tags"))
        self._list_available = QListWidget()
        self._list_available.setSelectionMode(QListWidget.ExtendedSelection)
        self._list_available.setAlternatingRowColors(True)
        avail_lay.addWidget(self._list_available, 1)
        dual.addLayout(avail_lay, 1)

        # Arrow buttons
        arrow_lay = QVBoxLayout()
        arrow_lay.addStretch()
        btn_add = QPushButton(">>>")
        btn_add.setFixedSize(50, 30)
        btn_add.setStyleSheet(_BTN_STYLE)
        btn_add.setToolTip("Add selected tags to recording")
        btn_add.clicked.connect(self._add_tags)
        arrow_lay.addWidget(btn_add)

        btn_remove = QPushButton("<<<")
        btn_remove.setFixedSize(50, 30)
        btn_remove.setStyleSheet(_BTN_STYLE)
        btn_remove.setToolTip("Remove selected tags from recording")
        btn_remove.clicked.connect(self._remove_tags)
        arrow_lay.addWidget(btn_remove)

        btn_add_all = QPushButton("All >>>")
        btn_add_all.setFixedSize(50, 30)
        btn_add_all.setStyleSheet(_BTN_STYLE)
        btn_add_all.clicked.connect(self._add_all_tags)
        arrow_lay.addWidget(btn_add_all)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedSize(50, 30)
        btn_clear.setStyleSheet(_BTN_STYLE)
        btn_clear.clicked.connect(self._clear_tags)
        arrow_lay.addWidget(btn_clear)
        arrow_lay.addStretch()
        dual.addLayout(arrow_lay)

        # Recorded tags
        rec_lay = QVBoxLayout()
        rec_lay.addWidget(QLabel("Recorded Tags"))
        self._list_recorded = QListWidget()
        self._list_recorded.setSelectionMode(QListWidget.ExtendedSelection)
        self._list_recorded.setAlternatingRowColors(True)
        rec_lay.addWidget(self._list_recorded, 1)
        dual.addLayout(rec_lay, 1)

        lay.addLayout(dual, 1)

        # Count label
        self._lbl_tag_count = QLabel("")
        self._lbl_tag_count.setStyleSheet(
            f"font-size: 9pt; color: {UI.border}; padding: 2px 6px;")
        lay.addWidget(self._lbl_tag_count)

        return w

    def _build_settings_tab(self) -> QWidget:
        """Recording settings: rate, deadband, retention."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        # Recording rate
        rate_grp = QGroupBox("Recording Rate")
        rate_lay = QHBoxLayout(rate_grp)
        rate_lay.addWidget(QLabel("Record every:"))
        self._spn_rate = QSpinBox()
        self._spn_rate.setRange(1, 60)
        self._spn_rate.setValue(1)
        self._spn_rate.setSuffix(" second(s)")
        self._spn_rate.setFixedWidth(140)
        rate_lay.addWidget(self._spn_rate)
        rate_lay.addStretch()

        rate_lay.addWidget(QLabel("Recording mode:"))
        self._cmb_mode = AuthoringComboBox()
        self._cmb_mode.addItems([
            "Continuous (every interval)",
            "Exception-based (on change only)",
            "Periodic + Exception",
        ])
        self._cmb_mode.setFixedWidth(250)
        rate_lay.addWidget(self._cmb_mode)
        lay.addWidget(rate_grp)

        # Deadband
        db_grp = QGroupBox("Change Detection (Exception-based)")
        db_lay = QHBoxLayout(db_grp)
        db_lay.addWidget(QLabel("Default deadband:"))
        self._spn_deadband = QDoubleSpinBox()
        self._spn_deadband.setRange(0.0, 100.0)
        self._spn_deadband.setValue(0.1)
        self._spn_deadband.setDecimals(3)
        self._spn_deadband.setSuffix(" %")
        self._spn_deadband.setFixedWidth(120)
        db_lay.addWidget(self._spn_deadband)
        db_lay.addStretch()

        self._chk_compress = QCheckBox("Enable compression (skip unchanged values)")
        self._chk_compress.setChecked(True)
        self._chk_compress.setStyleSheet(f"font-size: 9pt; color: {UI.blue};")
        db_lay.addWidget(self._chk_compress)
        lay.addWidget(db_grp)

        # Retention
        ret_grp = QGroupBox("Data Retention")
        ret_lay = QHBoxLayout(ret_grp)
        ret_lay.addWidget(QLabel("Keep data for:"))
        self._spn_retention = QSpinBox()
        self._spn_retention.setRange(1, 365)
        self._spn_retention.setValue(2)
        self._spn_retention.setSuffix(" day(s)")
        self._spn_retention.setFixedWidth(120)
        ret_lay.addWidget(self._spn_retention)
        ret_lay.addStretch()

        self._chk_auto_purge = QCheckBox("Auto-purge old data")
        self._chk_auto_purge.setChecked(True)
        self._chk_auto_purge.setStyleSheet(f"font-size: 9pt; color: {UI.blue};")
        ret_lay.addWidget(self._chk_auto_purge)
        lay.addWidget(ret_grp)

        lay.addStretch()
        return w

    def _build_stats_tab(self) -> QWidget:
        """Database statistics."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        # Stats table
        self._stats_table = QTableWidget()
        self._stats_table.setColumnCount(2)
        self._stats_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self._stats_table.setAlternatingRowColors(True)
        self._stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._stats_table.verticalHeader().setVisible(False)
        hh = self._stats_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self._stats_table, 1)

        btn_refresh = QPushButton("Refresh Statistics")
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_stats)
        lay.addWidget(btn_refresh, alignment=Qt.AlignRight)

        return w

    # ── Refresh ──

    def _refresh_all(self):
        self._refresh_tag_lists()
        self._refresh_stats()
        self._update_status()

    def _refresh_tag_lists(self):
        """Populate available and recorded tag lists."""
        self._list_available.clear()
        self._list_recorded.clear()

        # Get all available tags from store
        all_tags = set()
        if self._store:
            try:
                data = self._store.get_all()
                all_tags = set(data.keys())
            except Exception:
                pass

        # Also include TAG_REGISTRY tags
        try:
            from azeo_control_trainer.core.presentation.tag_registry import TAG_REGISTRY
            all_tags.update(TAG_REGISTRY.keys())
        except ImportError:
            pass

        # Recorded tags from config
        recorded = set(self._tag_config.keys())

        # Filter out internal tags
        available = sorted(
            t for t in all_tags
            if not t.startswith("_")
            and not t.startswith("io.")
            and not t.startswith("valve.")
            and t not in recorded
        )
        recorded_sorted = sorted(recorded)

        for tag in available:
            self._list_available.addItem(tag)
        for tag in recorded_sorted:
            item = QListWidgetItem(tag)
            self._list_recorded.addItem(item)

        n_avail = len(available)
        n_rec = len(recorded_sorted)
        self._lbl_tag_count.setText(
            f"{n_avail} available | {n_rec} recording")

    def _filter_available(self, text: str):
        text = text.lower()
        for i in range(self._list_available.count()):
            item = self._list_available.item(i)
            item.setHidden(text not in item.text().lower() if text else False)

    def _refresh_stats(self):
        """Refresh historian database statistics."""
        stats = []

        # Historian info
        if self._historian:
            db_path = getattr(self._historian, '_db_path', None)
            if db_path:
                p = Path(db_path)
                stats.append(("Database Path", str(p)))
                if p.exists():
                    size_mb = p.stat().st_size / (1024 * 1024)
                    stats.append(("Database Size", f"{size_mb:.2f} MB"))

            retention = getattr(self._historian, '_retention_s', 172800)
            stats.append(("Retention Period",
                          f"{retention / 3600:.0f} hours ({retention / 86400:.1f} days)"))

            # Try to count records
            try:
                import sqlite3
                db_path_str = str(db_path) if db_path else ""
                if db_path_str and Path(db_path_str).exists():
                    conn = sqlite3.connect(db_path_str)
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM snapshots")
                    n_snapshots = cur.fetchone()[0]
                    stats.append(("Total Snapshots", f"{n_snapshots:,}"))

                    cur.execute("SELECT COUNT(*) FROM tag_values")
                    n_values = cur.fetchone()[0]
                    stats.append(("Total Tag Values", f"{n_values:,}"))

                    cur.execute("SELECT MIN(wall_time), MAX(wall_time) FROM snapshots")
                    row = cur.fetchone()
                    if row[0] and row[1]:
                        t_min = datetime.fromtimestamp(row[0])
                        t_max = datetime.fromtimestamp(row[1])
                        span = t_max - t_min
                        stats.append(("Earliest Record", t_min.strftime("%Y-%m-%d %H:%M:%S")))
                        stats.append(("Latest Record", t_max.strftime("%Y-%m-%d %H:%M:%S")))
                        stats.append(("Data Span", str(span)))

                    cur.execute("SELECT COUNT(DISTINCT tag) FROM tag_values")
                    n_tags = cur.fetchone()[0]
                    stats.append(("Distinct Tags Recorded", str(n_tags)))

                    conn.close()
            except Exception as e:
                stats.append(("Database Query", f"Error: {e}"))
        else:
            stats.append(("Historian", "Not available"))

        # Config info
        stats.append(("Configured Tags", str(len(self._tag_config))))
        stats.append(("Config File", str(self._config_path())))

        # Store info
        if self._store:
            try:
                all_data = self._store.get_all()
                stats.append(("Store Tags (live)", str(len(all_data))))
            except Exception:
                pass

        self._stats_table.setRowCount(len(stats))
        for r, (metric, value) in enumerate(stats):
            self._stats_table.setItem(r, 0, QTableWidgetItem(metric))
            self._stats_table.setItem(r, 1, QTableWidgetItem(str(value)))

    def _update_status(self):
        if not self._capability.available:
            self._lbl_status.setText(f"Unavailable — {self._capability.reason}")
            return
        n = len(self._tag_config)
        self._lbl_status.setText(
            f"{n} tag{'s' if n != 1 else ''} configured for recording")

    # ── Tag management ──

    def _add_tags(self):
        """Add selected tags to recording."""
        for item in self._list_available.selectedItems():
            tag = item.text()
            if tag not in self._tag_config:
                self._tag_config[tag] = {
                    "enabled": True,
                    "deadband": self._spn_deadband.value(),
                }
        self._refresh_tag_lists()

    def _remove_tags(self):
        """Remove selected tags from recording."""
        for item in self._list_recorded.selectedItems():
            tag = item.text()
            self._tag_config.pop(tag, None)
        self._refresh_tag_lists()

    def _add_all_tags(self):
        """Add all visible available tags."""
        for i in range(self._list_available.count()):
            item = self._list_available.item(i)
            if not item.isHidden():
                tag = item.text()
                if tag not in self._tag_config:
                    self._tag_config[tag] = {
                        "enabled": True,
                        "deadband": self._spn_deadband.value(),
                    }
        self._refresh_tag_lists()

    def _clear_tags(self):
        """Clear all recorded tags."""
        if not self._tag_config:
            return
        reply = QMessageBox.question(
            self, "Clear All",
            f"Remove all {len(self._tag_config)} tags from recording?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._tag_config.clear()
            self._refresh_tag_lists()

    def _apply_config(self):
        """Commit configuration to its concrete consumer, then persist it."""
        if not self._capability.available:
            QMessageBox.warning(
                self, "Data Log Unavailable", self._capability.reason)
            return
        settings = {
            "period_s": self._spn_rate.value(),
            "mode": self._cmb_mode.currentText(),
            "deadband_percent": self._spn_deadband.value(),
            "compression": self._chk_compress.isChecked(),
            "retention_days": self._spn_retention.value(),
            "auto_purge": self._chk_auto_purge.isChecked(),
        }
        try:
            self._historian.apply_data_log_configuration(
                dict(self._tag_config), settings, store=self._store)
            self._save_config()
        except Exception as exc:
            log.exception("Data-log consumer rejected configuration")
            QMessageBox.critical(
                self, "Data Log Configuration",
                f"Configuration was not applied:\n{exc}")
            return
        self._update_status()
        QMessageBox.information(
            self, "Data Log Configuration",
            f"Configuration saved.\n"
            f"{len(self._tag_config)} tags configured for recording.")

    def _take_snapshot(self):
        """Trigger an immediate historian snapshot."""
        if not self._capability.available:
            QMessageBox.warning(
                self, "Snapshot", self._capability.reason)
            return

        try:
            count = self._historian.take_data_log_snapshot(
                store=self._store,
                tags=tuple(self._tag_config),
                wall_time=time.time(),
            )
            QMessageBox.information(
                self, "Snapshot",
                f"Recorded snapshot with {int(count or 0)} tag values.")
        except Exception as e:
            QMessageBox.critical(
                self, "Snapshot Error", f"Failed: {e}")

    def get_configured_tags(self) -> set[str]:
        """Return the set of tags configured for recording."""
        return set(self._tag_config.keys())
