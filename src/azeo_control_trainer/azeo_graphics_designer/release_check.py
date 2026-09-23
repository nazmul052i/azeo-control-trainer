"""Release-time display verification, supplied to core's release validator.

``core/configuration/release_validation.py`` runs release checks in a child
process and finds this module by the name recorded in
``config.applications.RELEASE_DISPLAY_VERIFIER``, so shared code never imports a
product. The check itself is the Studio's own verifier over a real canvas: the
same renderer and the same findings an engineer sees from Verify and Publish.
"""
from __future__ import annotations

from pathlib import Path

from azeo_control_trainer.core.hmi.compatibility import is_display_document_path


def verify_displays(root, paths, graphs) -> list[dict]:
    """Findings for every ``displays/<folder>/<Name>/draft.json`` in ``paths``."""
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from .studio.assembler import PvmStudio
    from .studio.verification import findings_for_studio

    app = QApplication.instance() or QApplication([])
    apply_application_font()
    root = Path(root)
    findings = []
    for relative in paths:
        if not is_display_document_path(relative):
            continue
        name = Path(relative).parent.name
        studio = PvmStudio(lambda: graphs, root / Path(relative).parents[1], name, hosted=True)
        try:
            for finding in findings_for_studio(studio):
                findings.append({"path": relative, "severity": finding.severity,
                                 "message": finding.message, "item": finding.item})
            if studio.display.work_in_progress:
                findings.append({"path": relative, "severity": "ERROR",
                                 "message": "Finish Work In Progress before releasing"})
        finally:
            studio.close()
            studio.deleteLater()
            app.processEvents()
    return findings
