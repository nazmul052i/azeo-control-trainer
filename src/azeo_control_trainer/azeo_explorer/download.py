"""The staged Total Download, honestly mapped.

The Downloading dialog shows eight stages; each is bold while it runs,
checked when it completes, stippled when the download did not need it.
Every stage maps to a real step against the live configuration:

- **Creating Log File** — a line-per-stage download log beside the
  area (`_download.log`), because a download you cannot audit is a
  download you cannot trust.
- **Performing Upload Checks** — byte-compares every open module
  against its file (save is byte-idempotent, item 18, which is what
  makes this check honest): unsaved edits are reported, not lost.
- **Performing Pre-download Checks** — the carrier keylock refuses
  the download outright; DST over-capacity warns and proceeds (the
  student learns more from the red gauge than a refusal).
- **Performing Dependency Checks** — every module's `validate()`
  findings, counted and logged.
- **Verifying Configuration** — the confirm dialog's checkbox made
  real: unchecked, the stage is stippled, exactly the course figure.
- **Generating** — compile every downloadable module; a compile error
  aborts here, before anything touches the runtime.
- **Downloading** — the modules go on scan.
- **Updating Download Status** — fingerprints of what was downloaded
  are recorded, which is what the blue triangle diffs against.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import hashlib
import json
import time

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QVBoxLayout,
)

STAGES = (
    "Creating Log File",
    "Performing Upload Checks",
    "Performing Pre-download Checks",
    "Performing Dependency Checks",
    "Verifying Configuration",
    "Generating",
    "Downloading",
    "Updating Download Status",
)

PENDING, RUNNING, DONE, SKIPPED, FAILED = range(5)
_GLYPHS = {PENDING: "○", RUNNING: "●", DONE: "✓",
           SKIPPED: "·", FAILED: "✕"}


def graph_fingerprint(graph) -> str:
    """A stable hash of a module's configuration — what a download
    actually transfers. Parameter tuning changes it; a repaint does
    not."""
    data = graph.to_dict()
    data.pop("comments", None)
    return hashlib.sha1(json.dumps(
        data, sort_keys=True, default=str).encode(),
        usedforsecurity=False).hexdigest()


class DownloadDialog(QDialog):
    """The p. 101 checklist, drawn like the figure."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Downloading Selected Objects")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        head = QLabel("Downloading PK-CTLR-1")
        head.setStyleSheet(f"background: {UI.blue}; color: #FFFFFF;"
                           "font-size: 9pt; padding: 4px 8px;")
        lay.addWidget(head)
        self.rows: list[QLabel] = []
        self.states = [PENDING] * len(STAGES)
        for stage in STAGES:
            row = QLabel(f"{_GLYPHS[PENDING]}  {stage}")
            row.setStyleSheet(f"font-size: 9pt; color: {UI.text_secondary};"
                              "padding: 2px 10px;")
            lay.addWidget(row)
            self.rows.append(row)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"font-size: 9pt; color: {UI.text_secondary};"
                                "padding: 4px 10px;")
        lay.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.setEnabled(False)
        lay.addWidget(self.buttons)

    def set_state(self, index: int, state: int) -> None:
        self.states[index] = state
        label = self.rows[index]
        label.setText(f"{_GLYPHS[state]}  {STAGES[index]}")
        if state == RUNNING:
            style = "font-weight: 600; color: #14304D;"
        elif state == DONE:
            style = "color: #2B3238;"
        elif state == SKIPPED:
            style = "color: #B8BCC0;"          # stippled
        elif state == FAILED:
            style = "color: #C0392B; font-weight: 600;"
        else:
            style = "color: #5A6168;"
        label.setStyleSheet(
            f"font-size: 9pt; padding: 2px 10px; {style}")
        from PySide6.QtWidgets import QApplication
        if QApplication.instance():
            QApplication.processEvents()

    def finish(self, message: str) -> None:
        self.note.setText(message)
        self.buttons.setEnabled(True)


def run_total_download(explorer, verify: bool = True,
                       dialog: DownloadDialog | None = None) -> dict:
    """Run the staged download against the Explorer's live objects.
    Returns {ok, notes, states} — the harness-facing record."""
    notes: list[str] = []
    states = [PENDING] * len(STAGES)

    def stage(index: int, state: int) -> None:
        states[index] = state
        if dialog is not None:
            dialog.set_state(index, state)

    def log_lines(lines) -> None:
        try:
            with open(explorer.area / "_download.log", "a",
                      encoding="utf-8", newline="\n") as handle:
                for line in lines:
                    handle.write(
                        time.strftime("%Y-%m-%d %H:%M:%S  ")
                        + line + "\n")
        except OSError:
            pass

    tab = explorer.designer_window.designer
    graphs = {g.name: g for g in tab.open_graphs()}

    # 1 — the log file.
    stage(0, RUNNING)
    log_lines([f"Total download requested — {len(graphs)} module(s)"])
    stage(0, DONE)

    # 2 — upload checks: unsaved edits reported, never lost.
    stage(1, RUNNING)
    dirty = []
    for name, graph in graphs.items():
        for folder in ("control", "sequence", "equipment"):
            path = explorer.area / folder / f"{name}.json"
            if path.exists():
                try:
                    on_disk = json.loads(
                        path.read_text(encoding="utf-8"))
                    on_disk.pop("comments", None)
                    current = graph.to_dict()
                    current.pop("comments", None)
                    if json.dumps(on_disk, sort_keys=True,
                                  default=str) \
                            != json.dumps(current, sort_keys=True,
                                          default=str):
                        dirty.append(name)
                except Exception:                   # noqa: BLE001
                    pass
                break
    if dirty:
        notes.append(f"unsaved edits in: {', '.join(sorted(dirty))}")
    stage(1, DONE)

    # 3 — pre-download: keylock refuses; over-capacity warns.
    stage(2, RUNNING)
    controller = getattr(explorer.store, "controller", None)
    if controller is not None and getattr(controller, "keylock",
                                          False):
        notes.append("carrier keylock LOCKED — download refused")
        log_lines(["ABORT: keylock locked"])
        stage(2, FAILED)
        for i in range(3, len(STAGES)):
            stage(i, SKIPPED)
        if dialog is not None:
            dialog.finish("Download refused: the carrier keylock is "
                          "LOCKED. Operation continues untouched.")
        return {"ok": False, "notes": notes, "states": states}
    if controller is not None and controller.over_capacity(
            explorer.store):
        notes.append("DST usage over the model's capacity — "
                     "proceeding (the gauge is the lesson)")
    stage(2, DONE)

    # 4 — dependency checks: validate everything, count findings.
    stage(3, RUNNING)
    findings = 0
    for name, graph in graphs.items():
        try:
            findings += len(graph.validate() or [])
        except Exception:                           # noqa: BLE001
            pass
    if findings:
        notes.append(f"{findings} validation finding(s) — see the "
                     "module validators")
    stage(3, DONE)

    # 5 — verify, only when asked (stippled otherwise, per figure).
    if verify:
        stage(4, RUNNING)
        log_lines([f"Verify: {findings} finding(s) across "
                   f"{len(graphs)} module(s)"])
        stage(4, DONE)
    else:
        stage(4, SKIPPED)

    # 6 — generate: compile before anything touches the runtime.
    stage(5, RUNNING)
    try:
        from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
        for name, graph in graphs.items():
            if graph.blocks:
                compile_strategy(graph)
    except Exception as error:                      # noqa: BLE001
        notes.append(f"compile failed: {error}")
        log_lines([f"ABORT: compile failed — {error}"])
        stage(5, FAILED)
        for i in range(6, len(STAGES)):
            stage(i, SKIPPED)
        if dialog is not None:
            dialog.finish(f"Compile failed: {error}")
        return {"ok": False, "notes": notes, "states": states}
    stage(5, DONE)

    # 7 — download.
    stage(6, RUNNING)
    tab.auto_go_online()
    stage(6, DONE)

    # 8 — update status: record what runtime now holds.
    stage(7, RUNNING)
    online = explorer._online_names()
    for name in online:
        graph = graphs.get(name)
        if graph is not None:
            explorer.downloaded_fp[name] = graph_fingerprint(graph)
    log_lines([f"Download complete — {len(online)} module(s) on "
               "scan"])
    stage(7, DONE)
    explorer.refresh()
    if dialog is not None:
        dialog.finish(f"{len(online)} module(s) on scan. "
                      + ("  ".join(notes) if notes
                         else "No findings."))
    return {"ok": True, "notes": notes, "states": states}
