"""Hash the actual source, captures and PDF so releases cannot ship stale guides."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(path):
    return {"path": path.resolve().relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def record(source, output):
    from PIL import Image
    images = []
    for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", source.read_text(encoding="utf-8")):
        path = (source.parent / target).resolve()
        with Image.open(path) as image:
            if min(image.size) <= 0:
                raise ValueError(f"Empty capture: {path}")
            images.append({**fingerprint(path), "width": image.width, "height": image.height})
    manifest = {"source": fingerprint(source), "pdf": fingerprint(output), "captures": images}
    output.with_suffix(".pdf.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def verify(output):
    manifest = json.loads(output.with_suffix(".pdf.manifest.json").read_text(encoding="utf-8"))
    for item in [manifest["source"], manifest["pdf"], *manifest["captures"]]:
        actual = fingerprint(ROOT / item["path"])
        if actual["sha256"] != item["sha256"]:
            raise SystemExit(f"Stale document release: {item['path']}; rebuild and audit its PDF")
    print(f"Verified source, PDF and {len(manifest['captures'])} captures: {output.name}")


def build_release(source, output, build, audit=None):
    """Keep the prior PDF intact if rendering, validation or replacement fails."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".document-build-", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        report = build(staged)
        if audit:
            audit(staged, report)
        staged.replace(output)
    record(source, output)
    verify(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, nargs="+")
    for output in parser.parse_args().pdf:
        verify(output)
