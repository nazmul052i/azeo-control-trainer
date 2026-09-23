"""A guided front door to the existing checklist, comparison and publish gate."""
from dataclasses import asdict

from PySide6.QtWidgets import QHBoxLayout, QTreeWidget, QTreeWidgetItem

from azeo_control_trainer.core.hmi.pvms.engineering import commissioning_progress, document_digest
from azeo_control_trainer.core.hmi.pvms.json_io import atomic_write_json
from .engineering_tools import EngineeringDialog


class ReleaseDialog(EngineeringDialog):
    def __init__(self, studio):
        super().__init__(studio, "Release readiness")
        self.report = None
        self.table = QTreeWidget()
        self.table.setHeaderLabels(("Check", "Current draft"))
        self.table.setRootIsDecorated(False)
        self.table.setColumnWidth(0, 210)
        self.root.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.button(row, "Refresh readiness", self.refresh)
        self.button(row, "Commissioning checklist", lambda: self.open_tool("commissioning"))
        self.button(row, "Operator preview", self.preview)
        self.button(row, "Compare revisions", lambda: self.open_tool("revisions"))
        self.root.addLayout(row)
        row = QHBoxLayout()
        self.button(row, "Save readiness report", self.save_report)
        self.publish_button = self.button(
            row,
            "Save draft and review publication…",
            self.review_publication,
            primary=True,
        )
        self.root.addLayout(row)
        self.studio.documentChanged.connect(self.invalidate)
        self.refresh()

    def invalidate(self):
        self.report = None
        self.publish_button.setEnabled(False)
        self.status.setText("Draft changed. Refresh readiness to check this revision.")

    def refresh(self):
        findings = self.studio.verification_findings()
        document = self.studio._document()
        progress = commissioning_progress(document)
        errors = sum(f.blocks_publish for f in findings)
        self.report = {"display": self.studio.display.name, "digest": document_digest(document),
                       "theme": getattr(self.studio, "quality_theme", "silver"),
                       "viewport": getattr(self.studio, "quality_viewport", None), "level": self.studio.display.level,
                       "commissioning": progress, "findings": [asdict(f) for f in findings]}
        self.table.clear()
        for label, value in (
                ("Verification", f"{errors} errors · {len(findings) - errors} advisories"),
                ("Commissioning checks", f"{progress['checked']} / {progress['total']} current"),
                ("Visual evidence", f"{progress['reviewed']} / {progress['total']} current"),
                ("Checklist", f"{progress['remaining']} cases remain" if progress['total'] else "No cases saved; commissioning coverage has not been established"),
                ("Operator context", f"{self.report['theme']} · L{self.report['level']} · {self.report['viewport'] or 'design size'}"),
                ("Publication", "Uses the existing TEST / PROD review and station retrieval workflow")):
            self.table.addTopLevelItem(QTreeWidgetItem([label, value]))
        for finding in findings:
            self.table.addTopLevelItem(QTreeWidgetItem([finding.severity.upper(), finding.message]))
        self.publish_button.setEnabled(errors == 0 and progress["remaining"] == 0)
        self.status.setText("Resolve errors and complete saved cases, then review the revision for publication. "
                            "Readability advisories and coverage still require an engineer's review.")
        return self.report

    def open_tool(self, kind):
        host = self.studio.window()
        host.tabs.setCurrentWidget(self.studio)
        return host.open_engineering_tool(kind)

    def preview(self):
        host = self.studio.window()
        host.tabs.setCurrentWidget(self.studio)
        return host.open_quick_online()

    def save_report(self):
        report = self.refresh()
        path = self.studio.store.root / "_commissioning" / (report["digest"] + "-readiness.json")
        atomic_write_json(path, report)
        self.status.setText(f"Saved readiness report: {path}")
        return path

    def review_publication(self):
        self.refresh()
        if not self.publish_button.isEnabled():
            raise ValueError("Resolve verification errors and complete the saved commissioning cases first")
        self.editable()
        if not self.studio.save_draft():
            raise ValueError("The draft could not be saved; resolve the save error before publishing")
        # The existing dialog and publish() both run fresh verification. This
        # report never acts as an authorization token or bypasses that gate.
        self.studio.open_publish()
        self.refresh()

    def closeEvent(self, event):  # noqa: N802
        self.studio.documentChanged.disconnect(self.invalidate)
        super().closeEvent(event)
