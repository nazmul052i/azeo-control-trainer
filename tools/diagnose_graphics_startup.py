"""Launch normally while retaining Qt warning stacks and native exit codes.

Use the repository venv to run this script, then open Graphics Designer from
Explorer. Any arguments are forwarded to run.py (for example --graphics).
The supervisor stays outside Qt so even a native abort leaves an exit record.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _child(arguments: list[str]) -> None:
    import runpy
    import traceback

    import PySide6
    from PySide6.QtCore import qInstallMessageHandler, qVersion
    from PySide6.QtWidgets import QApplication

    from azeo_control_trainer.config import logging_config

    print(f"Python: {sys.executable} ({sys.version})", flush=True)
    print(f"PySide: {PySide6.__version__}; Qt: {qVersion()}", flush=True)
    install = logging_config.install_qt_message_logging

    def install_capture() -> bool:
        installed = install()
        app = QApplication.instance()
        print(f"Qt platform: {app.platformName()}; "
              f"style: {app.style().objectName()}", flush=True)

        def capture(kind, context, message):
            if previous is not None:
                previous(kind, context, message)
            if "QFont::setPointSize" in message:
                print("Qt font warning call stack:", flush=True)
                traceback.print_stack(file=sys.stdout)
                sys.stdout.flush()

        previous = qInstallMessageHandler(capture)
        return installed

    logging_config.install_qt_message_logging = install_capture
    sys.argv = [str(ROOT / "run.py"), *arguments]
    runpy.run_path(sys.argv[0], run_name="__main__")


def main(arguments: list[str]) -> int:
    from azeo_control_trainer.config.paths import logs_dir

    directory = logs_dir() / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"graphics-startup-{stamp}-{os.getpid()}.log"
    print(f"Diagnostic log: {path}", flush=True)
    command = [sys.executable, "-u", "-X", "faulthandler",
               str(Path(__file__).resolve()), "--child", *arguments]
    with path.open("w", encoding="utf-8", buffering=1) as journal:
        with subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        ) as process:
            print(f"Application PID: {process.pid}", file=journal)
            print(f"Application PID: {process.pid}", flush=True)
            for line in process.stdout:
                journal.write(line)
                print(line, end="", flush=True)
            code = process.wait()
        result = f"Application exit code: {code} (0x{code & 0xFFFFFFFF:08X})"
        print(result, file=journal)
        print(result, flush=True)
    return code


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        _child(sys.argv[2:])
    else:
        sys.exit(main(sys.argv[1:]))
