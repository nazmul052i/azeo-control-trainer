"""Release preflight uses the same compiler and Graphics Designer verifier as editors.

The checks run in a child process so Qt never enters the configuration service.
Strategy checks are core's own; the display checks are Graphics Designer's, which
core locates by the module name recorded in the product catalog
(``config.applications.RELEASE_DISPLAY_VERIFIER``) rather than by importing a
product. When that component is not installed, display verification is reported
as unavailable instead of failing the release silently.
"""
from __future__ import annotations

from ..hmi.compatibility import is_display_document_path

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from functools import lru_cache

from .documents import ConfigurationError, export_project
from azeo_control_trainer.config.applications import RELEASE_DISPLAY_VERIFIER


@lru_cache(maxsize=1)
def implementation_digest():
    """Installed Python classes are pinned by implementation, never shipped as executable assets."""
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and
                       p.suffix.lower() in {".py", ".svg", ".png", ".ttf", ".otf", ".qss", ".js"}):
        digest.update(path.relative_to(root).as_posix().encode())
        raw = path.read_bytes()
        digest.update(raw.replace(b"\r\n", b"\n") if path.suffix in {".py", ".js", ".qss", ".svg"} else raw)
    from importlib.metadata import version
    digest.update((sys.version + version("PySide6")).encode())
    return digest.hexdigest()


def validate_bundle(bundle, paths):
    # Qt ownership and plugins stay out of the HTTP thread pool. No database
    # transaction stays open while the real canvas performs its verification.
    with tempfile.TemporaryDirectory(prefix="azeo-release-") as temporary:
        directory = Path(temporary)
        export_project(bundle, directory / "project")
        request = directory / "request.json"
        request.write_text(json.dumps({"paths": paths}), encoding="utf-8")
        env = os.environ.copy()
        env.update(QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        try:
            result = subprocess.run(
                [sys.executable, "-m", __name__, str(directory)], env=env,
                capture_output=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise ConfigurationError("Release verification timed out; no release was created") from None
        output = directory / "result.json"
        if result.returncode or not output.exists():
            import logging
            logging.getLogger(__name__).error("Release verifier: %s", result.stderr.decode("utf-8", errors="replace")[-8000:])
            raise ConfigurationError("Release verifier failed; no release was created")
        return json.loads(output.read_text(encoding="utf-8"))


def display_verifier():
    """Graphics Designer's release-time display check, located through the product catalog.

    Returns ``None`` when the Graphics Designer component is not installed.
    """
    try:
        module = importlib.import_module(RELEASE_DISPLAY_VERIFIER)
    except ImportError:
        return None
    return module.verify_displays


def verify_project(root, paths):
    from azeo_control_trainer.core.strategy import blocks  # noqa: F401
    from azeo_control_trainer.core.strategy.serialization.strategy_io import graph_from_document
    from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
    from azeo_control_trainer.core.strategy.engine.validator import validate_strategy
    root = Path(root)
    graphs, findings = {}, []
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        if not relative.startswith(("control/", "sequence/")) or "/versions/" in relative:
            continue
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if "blocks" not in raw:
            continue
        graph, _ = graph_from_document(raw, strict=True)
        graphs[graph.name] = graph
        if relative in paths:
            for finding in validate_strategy(graph):
                findings.append({"path": relative, "severity": str(finding.severity).split(".")[-1].upper(),
                                 "message": finding.message})
            try:
                compile_strategy(graph)
            except Exception as error:
                findings.append({"path": relative, "severity": "ERROR", "message": str(error)})
    display_paths = [relative for relative in paths if is_display_document_path(relative)]
    if display_paths:
        verify_displays = display_verifier()
        if verify_displays is None:
            findings.extend({"path": relative, "severity": "ERROR",
                             "message": "Display verification is unavailable: "
                                        "Azeo Graphics Designer is not installed"}
                            for relative in display_paths)
        else:
            findings.extend(verify_displays(root, display_paths, graphs))
    return findings

if __name__ == "__main__":
    directory = Path(sys.argv[1])
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    findings = verify_project(directory / "project", request["paths"])
    (directory / "result.json").write_text(json.dumps(findings), encoding="utf-8")
