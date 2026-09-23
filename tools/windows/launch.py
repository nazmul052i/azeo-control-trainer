"""Entry script copied beside the portable Windows launchers."""
import ctypes
import json
import os
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parent


def main():
    from azeo_control_trainer.config.distribution import (
        default_workspace, prepare_workspace, read_installation,
    )
    installed = read_installation(ROOT)
    if installed.installed:
        workspace = prepare_workspace(ROOT, default_workspace())
        os.environ["AZEO_WORKSPACE_DIR"] = str(workspace)
        os.chdir(workspace)
    else:
        workspace = ROOT
    sys.dont_write_bytecode = True
    log_directory = workspace / "logs"
    log_directory.mkdir(exist_ok=True)
    os.environ.setdefault("AZEO_LOG_DIR", str(log_directory))
    qt = ROOT / "runtime/Lib/site-packages/PySide6"
    os.environ["QT_PLUGIN_PATH"] = str(qt / "plugins")
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(qt / "plugins/platforms")
    # pythonw has no console. Early tracebacks still need a real destination,
    # including failures before Qt or the application's logging can import.
    stream = (log_directory / "startup.log").open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    if "--help-center" in sys.argv:
        from azeo_control_trainer.core.presentation.product_help import main as help_main
        return help_main()
    if "--verify-package" in sys.argv:
        # A packaged check must fail, never wait on a dialog such as a licence refusal.
        os.environ["AZEO_UNATTENDED"] = "1"
        index = sys.argv.index("--verify-package")
        mode, output = sys.argv[index + 1:index + 3]
        from _verify_runtime import verify
        report = {"mode": mode, "python": sys.version, "executable": sys.executable}
        try:
            report.update(verify(ROOT, mode, Path(output).parent))
            report["ok"] = True
        except BaseException:
            report.update(ok=False, error=traceback.format_exc())
        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0 if report["ok"] else 1
    try:
        runpy.run_path(str(ROOT / "run.py"), run_name="__main__")
    except SystemExit as result:
        return result.code or 0
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        details = traceback.format_exc()
        try:
            if (ROOT / "installation.ini").exists():
                # Even a malformed install record or unusable workspace must
                # report outside the immutable version directory.
                output = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Azeo/Logs/startup-error.txt"
            else:
                output = ROOT / "logs/startup-error.txt"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(details, encoding="utf-8")
            message = f"Azeo could not start. Details were saved to:\n{output}\n\n{details[-1500:]}"
        except OSError:
            message = "Extract Azeo to a writable folder such as Documents.\n\n" + details[-1500:]
        ctypes.windll.user32.MessageBoxW(None, message, "Azeo startup error", 0x10)
        result = 1
    raise SystemExit(result)
