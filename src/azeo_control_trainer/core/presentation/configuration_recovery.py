"""Database-aware backup and isolated restore review; all I/O uses workers."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QSplitter, QTabWidget, QTextBrowser, QVBoxLayout)

from ..configuration.client import ConfigurationClient, read_profile
from ..configuration.recovery import sqlite_capture
from .configuration_catalog import _table
from .configuration_editing import BackgroundDialog
from .configuration_releases import send_command, retry_pending
from .configuration_chrome import command_bar, ConfigurationComboBox as QComboBox
from .configuration_chrome import ConfigurationButton as QPushButton, detail_document


class RecoveryManager(BackgroundDialog):
    def __init__(self, parent=None, *, profile=None):
        super().__init__(parent)
        self.profile = profile or read_profile()
        self.client = ConfigurationClient(**self.profile, timeout=600)
        self.archives = []
        self._backup_payload = None
        self.backup_command, self.restore_command = str(uuid4()), str(uuid4())
        self.setWindowTitle("Configuration backup and recovery")
        self.resize(1100, 700)
        layout = QVBoxLayout(self)
        self.status = QLabel("Loading server backup history…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel("Preserve configuration and selected training evidence. Verify recovery in a separate database.")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.name = QLineEdit("Training recovery checkpoint")
        row.addWidget(self.name, 1)
        self.kind = QComboBox()
        for kind in ("history", "training", "journal"):
            self.kind.addItem(kind.title(), kind)
        row.addWidget(self.kind)
        add = QPushButton("Add local archive…")
        add.clicked.connect(self.add_archive)
        row.addWidget(add)
        clear = QPushButton("Clear archive selection")
        clear.clicked.connect(self.clear_archives)
        row.addWidget(clear)
        layout.addLayout(row)
        self.archive_label = QLabel("No additional local archives selected")
        self.archive_label.setWordWrap(True)
        layout.addWidget(self.archive_label)
        self.commands = command_bar(self, [
            ("Create consistent backup", self.backup, "checkpoint", True),
            ("Rehearse selected restore", self.rehearse, "restore", False),
            ("Refresh", self.refresh, "restore", False),
        ], more=[("Export selected backup…", self.export, "download"),
                 ("Import exported backup…", self.import_backup, "upload"),
                 ("Retry pending command", self.retry, "restore")])
        layout.addWidget(self.commands)
        self.commands.buttons["Rehearse selected restore"].setEnabled(False)
        tabs = QTabWidget()
        self.backups, self.backup_model = _table([("name", "Backup"), ("created", "Captured"), ("actor", "Administrator"),
                                                  ("id", "Identity")], "Configuration backups")
        self.backups.setColumnHidden(3, True)
        self.restores, self.restore_model = _table([("state", "Result"), ("completed", "Verified"),
                                                    ("backup_name", "Backup")], "Restore rehearsals")
        self.restores.setColumnWidth(0, 100)
        self.restores.setColumnWidth(1, 160)
        tabs.addTab(self.backups, "Backups")
        tabs.addTab(self.restores, "Restore rehearsals")
        self.tabs = tabs
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(tabs)
        self.details = QTextBrowser()
        self.details.setPlainText("Select a backup or restore rehearsal to inspect its evidence.")
        split.addWidget(self.details)
        split.setSizes([700, 340])
        layout.addWidget(split, 1)
        self.backups.selectionModel().currentRowChanged.connect(self.describe)
        self.restores.selectionModel().currentRowChanged.connect(self.describe_restore)
        tabs.currentChanged.connect(lambda index: self.describe() if index == 0 else self.describe_restore())
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        def ready(state):
            self.backup_model.set_rows(state["backups"])
            names = {row["id"]: row["name"] for row in state["backups"]}
            self.restore_model.set_rows([{**row, "backup_name": names.get(row["backup"], row["backup"])}
                                         for row in state["restores"]])
            self.status.setText(f"{len(state['backups'])} completed backups · {len(state['restores'])} verified rehearsals · "
                                f"{len(state['incomplete'])} incomplete rehearsals retained for diagnosis")
        self.run(lambda: self.client.request("/v1/recovery"), ready)

    def add_archive(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select existing evidence archive", "", "SQLite archives (*.sqlite *.sqlite3 *.db)")
        if path:
            self.archives.append((path, self.kind.currentData()))
            self.archive_label.setText("; ".join(f"{kind}: {Path(path).name}" for path, kind in self.archives))

    def clear_archives(self):
        self.archives.clear()
        self.archive_label.setText("No additional local archives selected")

    def backup(self):
        name, selected = self.name.text().strip(), list(self.archives)
        if not name:
            self.status.setText("Enter a backup name")
            return
        if self._backup_payload is not None:
            self.status.setText("A backup receipt is unconfirmed. Use Retry pending command before creating another backup.")
            return
        self.status.setText("Capturing committed archive evidence, then the configuration database…")
        def capture():
            evidence = []
            with TemporaryDirectory(prefix="azeo-recovery-evidence-") as temporary:
                for path, kind in selected:
                    identity = str(uuid4())
                    target = sqlite_capture(path, Path(temporary) / (identity + ".sqlite3"))
                    evidence.append(self.client.upload_evidence(target, kind, identity)["id"])
            self._backup_payload = {"name": name, "evidence": evidence, "command_id": self.backup_command}
            return send_command(self.profile, "recovery", "/v1/recovery/backups", self._backup_payload, timeout=600)
        def ready(result):
            self.backup_command = str(uuid4())
            self._backup_payload = None
            self.backup_model.set_rows([result, *self.backup_model.rows])
            self.backups.selectRow(0)
            self.status.setText("Consistent backup completed. Rehearse restore to verify its recoverability.")
        self.run(capture, ready)

    def import_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import an Azeo configuration backup", "", "Backup (*.zip)")
        if path:
            def ready(result):
                self.backup_model.set_rows([result, *[row for row in self.backup_model.rows if row["id"] != result["id"]]])
                self.backups.selectRow(0)
                self.status.setText("Backup imported and checksums verified. Rehearse restore to verify recovery.")
            self.run(lambda: self.client.upload_evidence(path, "backup", str(uuid4())), ready)

    def retry(self):
        def ready(message):
            self._backup_payload = None
            self.backup_command, self.restore_command = str(uuid4()), str(uuid4())
            self.status.setText(message + " Refresh to inspect the receipts.")
        self.run(lambda: retry_pending(self.profile, "recovery"), ready)

    def selected(self):
        index = self.backups.currentIndex()
        return self.backup_model.rows[index.row()] if index.isValid() else None

    def describe(self, *_):
        row = self.selected()
        self.commands.buttons["Rehearse selected restore"].setEnabled(row is not None)
        if row:
            self.details.setHtml(detail_document(
                row["name"], f"{len(row['database']['projects'])} projects · {len(row['evidence'])} evidence archives",
                row["consistency"], f"Manifest checksum\n{row['hash']}",
            ))
        else:
            self.details.setPlainText("Select a backup to inspect its contents and recovery evidence.")

    def describe_restore(self, *_):
        index = self.restores.currentIndex()
        if index.isValid():
            row = self.restore_model.rows[index.row()]
            self.details.setHtml(detail_document(
                "Restore verified", "Database and evidence checksums match · runtime not started",
                "\n\n".join(e['kind'].title() + " archive\n" + ", ".join(
                    f"{value:,} {key.replace('_', ' ')}" for key, value in e['rows'].items())
                    for e in row["evidence"]), f"Isolated database\n{row['database']}",
            ))
        else:
            self.details.setPlainText("Select a restore rehearsal to inspect its verification result.")

    def export(self):
        row = self.selected()
        if not row:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Export configuration backup", "azeo-backup.zip", "Backup (*.zip)")
        if destination:
            self.run(lambda: self.client.download_backup(row["id"], destination),
                     lambda path: self.status.setText("Backup exported: " + str(path)))

    def rehearse(self):
        row = self.selected()
        if not row:
            self.status.setText("Select a completed backup")
            return
        self.status.setText("Restoring into a new isolated database and verifying all configuration and evidence…")
        def ready(result):
            self.restore_command = str(uuid4())
            result = {**result, "backup_name": row["name"]}
            self.restore_model.set_rows([result, *self.restore_model.rows])
            self.tabs.setCurrentIndex(1)
            self.restores.selectRow(0)
            self.status.setText("Restore verified: " + result["database"] + " · original database remains in service")
        self.run(lambda: send_command(self.profile, "recovery", f"/v1/recovery/backups/{row['id']}/rehearse",
                                      {"command_id": self.restore_command}, timeout=600), ready)
