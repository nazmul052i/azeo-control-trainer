"""Audit the packages a Windows payload bundles against the PyPI/OSV advisory database.

    python tools/audit_dependencies.py --payload build/windows-app-YYYYMMDD-HHMMSS/Azeo-Windows-x64
    python tools/audit_dependencies.py --freeze requirements.txt
    python tools/audit_dependencies.py --payload ... --report docs/dependency_audit.md

The check runs pip-audit over the exact versions found in the payload's private
runtime (``runtime/Lib/site-packages``), without dependency resolution, so the
result describes what ships rather than what the build machine has installed.
pip-audit is a build-machine tool, not a product dependency: install it into a
separate directory and point ``--tool`` at it (the default is
``build/pip-audit``), for example

    python -m pip install --target build/pip-audit pip-audit

The exit status is 1 when any advisory remains, so the release pipeline can gate
on it; an accepted advisory is listed by id with ``--accept`` and a reason is
expected in the release record.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def freeze_payload(payload: Path) -> str:
    site = payload / "runtime" / "Lib" / "site-packages"
    if not site.is_dir():
        raise SystemExit(f"No private runtime under {payload}: expected {site}")
    listing = subprocess.run([sys.executable, "-m", "pip", "list", "--path", str(site), "--format=freeze"],
                             capture_output=True, text=True, check=True)
    lines = [line for line in listing.stdout.splitlines() if "==" in line]
    if not lines:
        raise SystemExit(f"pip found no distributions under {site}")
    return "\n".join(lines) + "\n"


def run_audit(freeze_text: str, tool: Path | None) -> list[dict]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if tool is not None:
        env["PYTHONPATH"] = str(tool) + os.pathsep + env.get("PYTHONPATH", "")
    with tempfile.TemporaryDirectory(prefix="azeo-audit-") as temporary:
        requirements = Path(temporary) / "requirements.txt"
        requirements.write_text(freeze_text, encoding="utf-8")
        output = Path(temporary) / "audit.json"
        completed = subprocess.run(
            [sys.executable, "-m", "pip_audit", "-r", str(requirements), "--no-deps", "--disable-pip",
             "--progress-spinner", "off", "-f", "json", "-o", str(output)],
            env=env, capture_output=True, text=True)
        if completed.returncode not in (0, 1) or not output.exists():
            sys.stderr.write(completed.stdout + completed.stderr)
            raise SystemExit("pip-audit did not run; install it with "
                             "'python -m pip install --target build/pip-audit pip-audit'")
        report = json.loads(output.read_text(encoding="utf-8"))
    findings = []
    for dependency in report.get("dependencies", []):
        for vuln in dependency.get("vulns", []):
            findings.append({
                "name": dependency["name"], "version": dependency["version"], "id": vuln["id"],
                "aliases": vuln.get("aliases", []), "fix_versions": vuln.get("fix_versions", []),
                "description": (vuln.get("description") or "").strip().splitlines()[0][:160]
                if vuln.get("description") else "",
            })
    return findings


def render(findings: list[dict], accepted: set[str], source: str) -> str:
    lines = [f"Dependency audit of {source}", ""]
    if not findings:
        lines.append("No known advisories for the bundled versions.")
        return "\n".join(lines) + "\n"
    lines.append("| Package | Version | Advisory | Fixed in | Status | Summary |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in findings:
        ids = ", ".join([item["id"], *item["aliases"]])
        status = "accepted" if {item["id"], *item["aliases"]} & accepted else "open"
        lines.append(f"| {item['name']} | {item['version']} | {ids} | "
                     f"{', '.join(item['fix_versions']) or 'none'} | {status} | {item['description']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload", type=Path, help="portable payload directory with runtime/Lib/site-packages")
    source.add_argument("--freeze", type=Path, help="a pip freeze file to audit instead of a payload")
    parser.add_argument("--tool", type=Path, default=ROOT / "build" / "pip-audit",
                        help="directory pip-audit was installed into with --target (ignored if importable)")
    parser.add_argument("--accept", action="append", default=[], help="advisory id accepted for this release")
    parser.add_argument("--report", type=Path, help="write the Markdown table here as well")
    options = parser.parse_args()

    freeze_text = (options.freeze.read_text(encoding="utf-8") if options.freeze
                   else freeze_payload(options.payload))
    tool = options.tool if options.tool.is_dir() else None
    findings = run_audit(freeze_text, tool)
    accepted = set(options.accept)
    text = render(findings, accepted, str(options.payload or options.freeze))
    print(text)
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(text, encoding="utf-8")
    open_findings = [f for f in findings if not ({f["id"], *f["aliases"]} & accepted)]
    print(f"{len(findings)} advisories, {len(open_findings)} open, "
          f"{len({f['name'] for f in findings})} packages affected")
    return 1 if open_findings else 0


if __name__ == "__main__":
    sys.exit(main())
