"""Tracked text must not cite the third-party training course by number, module or page."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CITATION = re.compile(
    r"course\s+7009|7009[\s-]+(?:module|course|example|workshop|p&id|pid0|training|pump|tank)|"
    r"\(course\s+p\.|\bcourse\s+p\.\s?\d|dv7009",
    re.IGNORECASE)
SKIP_SUFFIXES = {".png", ".jpg", ".gif", ".pdf", ".zip", ".exe", ".dll", ".pyd", ".ico", ".woff", ".woff2",
                 ".ttf", ".otf", ".sha256", ".db", ".sqlite"}


def tracked_text_files():
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    for name in listing.stdout.decode("utf-8").split("\0"):
        if not name or Path(name).suffix.lower() in SKIP_SUFFIXES:
            continue
        if name.startswith(("dist/", "docs/uiux/performance_", "output/")):
            continue
        path = ROOT / name
        if path.is_file():
            yield name, path


def test_no_course_citations_in_tracked_text():
    offenders = []
    for name, path in tracked_text_files():
        if name == "tests/test_no_course_citations.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if CITATION.search(line):
                offenders.append(f"{name}:{number}: {line.strip()[:100]}")
    assert not offenders, "\n".join(offenders)
