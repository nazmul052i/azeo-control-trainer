"""Keep public entry points self-contained in the curated source repository."""
from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def public_markdown():
    yield ROOT / "README.md"
    for directory in ("docs", "AzeoPlantSimulator", "projects", "src"):
        yield from (ROOT / directory).rglob("*.md")


def test_public_readme_and_docs_have_no_broken_local_links():
    missing = []
    for source in public_markdown():
        fenced = False
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            for match in LINK.finditer(line):
                target = match.group(1).split("#", 1)[0].strip("<>")
                if not target or target.startswith(("http:", "https:", "mailto:", "help:")):
                    continue
                path = (source.parent / unquote(target)).resolve()
                if not path.is_file():
                    missing.append(f"{source.relative_to(ROOT)}:{number}: {target}")
    assert not missing, "Broken local documentation links:\n" + "\n".join(missing)


def test_public_guides_do_not_name_a_maintainers_workstation():
    offenders = []
    for source in public_markdown():
        if "D:\\development\\GitHub" in source.read_text(encoding="utf-8"):
            offenders.append(str(source.relative_to(ROOT)))
    assert not offenders, offenders
