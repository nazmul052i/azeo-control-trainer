"""The startup licence gate every product entry point passes through.

``app.main`` calls :func:`enforce_license` after the QApplication exists and
before any product window opens. A build without a licence file is
unrestricted; a build that ships one must hold a valid, unexpired licence.
The refusal is a plain dialog naming the licence and the reason, and the
process exits with :data:`EXIT_LICENSE_REFUSED` so launchers and scripts can
tell a licence stop from a crash. The Help Center never passes through here.
"""
from __future__ import annotations

import logging

from azeo_control_trainer.config import licensing
from azeo_control_trainer.config.licensing import EXIT_LICENSE_REFUSED, LicenseStatus

from .headless import is_headless


def _unattended() -> bool:
    """No dialog may block a packaged check or a scripted run; the log carries the reason."""
    import os
    return is_headless() or os.environ.get("AZEO_UNATTENDED", "").strip() not in ("", "0")

log = logging.getLogger("azeo.license")
#: Warn at startup when this many days (or fewer) remain.
WARN_DAYS = 2
CONTACT = ("To continue after the evaluation, obtain a licence file from your Azeo "
           "contact and place it as license\\azeo.lic in the application folder, or "
           "install the build that carries it.")


def describe(status: LicenseStatus) -> str:
    """Human-readable licence detail for dialogs and System information."""
    lines = []
    licence = status.license
    if licence is not None:
        lines.append(f"Licence {licence.id} ({licence.edition}) issued {licence.issued.isoformat()} "
                     f"to {licence.licensee or 'unnamed licensee'}.")
    if status.valid and status.expires is not None:
        lines.append(f"Valid until {status.expires.astimezone():%Y-%m-%d %H:%M} "
                     f"({status.days_left} day(s) remaining).")
    elif not status.valid:
        lines.append(f"This build cannot start: {status.reason}.")
    return "\n".join(lines)


def enforce_license(product_title: str) -> int | None:
    """Return an exit code when the product may not start, otherwise ``None``."""
    try:
        status = licensing.check()
    except Exception:                                   # noqa: BLE001
        # A gate that fails must refuse, never fall through to an unlicensed run.
        log.exception("Licence check failed")
        status = LicenseStatus(True, False, "the licence check failed; see the application log")
    log.info("Licence: %s", status.summary)
    if not status.required:
        return None
    if status.valid:
        if status.days_left is not None and status.days_left <= WARN_DAYS and not _unattended():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(None, f"{product_title}: evaluation ending",
                                describe(status) + "\n\n" + CONTACT)
        return None
    if not _unattended():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, f"{product_title}: licence required",
                             describe(status) + "\n\n" + CONTACT)
    return EXIT_LICENSE_REFUSED
