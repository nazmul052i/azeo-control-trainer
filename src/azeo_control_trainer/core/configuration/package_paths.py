"""Filesystem-only release boundary shared by existing engineering serializers."""
from pathlib import Path

MARKER = ".repository-release.json"


def release_root(path):
    path = Path(path).resolve()
    return next((candidate for candidate in (path, *path.parents) if (candidate / MARKER).is_file()), None)


def assert_mutable(path):
    if release_root(path):
        raise ValueError("Released configuration is immutable. Use Shared editing or reviewed online tuning upload.")
    path = Path(path).resolve()
    if any((candidate / ".class-revision.json").is_file() for candidate in (path, *path.parents)):
        raise ValueError("Pinned class revisions are immutable. Edit the class and adopt a reviewed revision.")
