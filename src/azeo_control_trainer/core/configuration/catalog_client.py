"""Per-seat catalog access and outage cache, independent of any application UI."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import urlencode

from .catalog import VERSION, CatalogIndex
from .client import ConfigurationClient, ServiceUnavailable, default_profile, read_profile
from .documents import ConfigurationError, Forbidden, Missing, read_project


def project_root(path=None):
    if path is None:
        from ..strategy.serialization import strategy_io
        path = strategy_io.STRATEGY_DIR
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if (candidate / "_project.json").is_file():
            return candidate
    return None


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".catalog-", suffix=".tmp", delete=False) as f:
        temporary = Path(f.name)
        json.dump(value, f, ensure_ascii=True, allow_nan=False, default=str)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path):
    try:
        if path.stat().st_size > 190 * 1024 * 1024:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class CatalogSession:
    def __init__(self, root=None, *, profile=None, cache_dir=None, client=None):
        self.root = project_root(root)
        profile = profile or read_profile()
        self.profile = dict(profile)
        self.client = client or ConfigurationClient(profile["url"], profile["token"], timeout=15)
        # A different credential must not inherit another engineer's offline access.
        self.scope = hashlib.sha256((profile["url"] + "\0" + profile["token"]).encode()).hexdigest()
        self.cache_dir = (cache_dir or default_profile().parent / "catalog_cache") / self.scope
        root_key = hashlib.sha256(str(self.root).casefold().encode()).hexdigest()
        self.link_path = self.cache_dir / ("seat-" + root_key + ".json")

    def _cache_path(self, project):
        from .repository import identifier
        return self.cache_dir / (identifier(project) + ".json")

    def cached(self, project):
        value = _read(self._cache_path(project))
        if not isinstance(value, dict) or value.get("project", {}).get("id") != project \
                or value.get("catalog", {}).get("version") != VERSION:
            return None
        return value

    def local_state(self, bundle):
        if self.root is None:
            return "No local project selected"
        try:
            files = read_project(self.root)["files"]
            manifest = sorted((f["path"], hashlib.sha256(base64.b64decode(f["content"])).hexdigest())
                              for f in files)
            digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=True).encode()).hexdigest()
            return "Local files match snapshot" if digest == bundle["project"]["digest"] else \
                "STALE — local files differ from this snapshot"
        except (ConfigurationError, OSError):
            return "Local file comparison unavailable"

    def load(self, selected="", publish_cached=None):
        link = _read(self.link_path) or {}
        selected = selected or link.get("project", "")
        cached = self.cached(selected) if selected else None
        if cached is not None and publish_cached:
            publish_cached({"index": CatalogIndex(cached), "state": "Cached — checking service",
                            "local": "Checking local files", "projects": [], "selected": selected})
        try:
            projects = self.client.request("/v1/projects")
            if selected and selected not in {p["id"] for p in projects}:
                raise Forbidden("The linked project is no longer accessible to this identity")
            if not selected:
                local = _read(self.root / "_project.json") if self.root else {}
                local = local or {}
                matches = [p for p in projects if (
                    local.get("project_id") and p.get("source_project_id") == local["project_id"])
                    or (not local.get("project_id") and local.get("name")
                        and p.get("source_name") == local["name"])]
                if len(matches) == 1:
                    selected = matches[0]["id"]
                else:
                    return {"index": None, "projects": projects, "selected": "",
                            "state": "Choose the captured project for this workspace", "local": ""}
            cached = cached or self.cached(selected)
            known = cached.get("catalog_stamp", "") if cached else ""
            bundle = self.client.request(f"/v1/projects/{selected}/catalog?" + urlencode({"known": known}))
            unchanged = bundle.get("not_modified", False)
            if unchanged:
                if (cached is None or bundle.get("catalog_stamp") != known
                        or bundle.get("project", {}).get("id") != selected
                        or bundle["project"]["generation"] != cached["project"]["generation"]
                        or bundle["project"]["digest"] != cached["project"]["digest"]):
                    raise ConfigurationError("Service returned an inconsistent catalog revision")
                bundle = cached
            if bundle["project"]["id"] != selected or bundle["catalog"]["version"] != VERSION:
                raise ConfigurationError("Catalog response belongs to a different project or version")
            bundle["cached_at"] = datetime.now(timezone.utc).isoformat()
            cache_warning = ""
            try:
                if not unchanged:
                    _write(self._cache_path(selected), bundle)
                _write(self.link_path, {"project": selected})
            except OSError:
                cache_warning = " — offline cache could not be saved"
            return {"index": CatalogIndex(bundle), "projects": projects, "selected": selected,
                    "state": ("Connected — checked-in engineering" if bundle["project"].get("mode") == "repository"
                              else "Connected — captured configuration") + cache_warning,
                    "local": self.local_state(bundle)}
        except (Forbidden, Missing):
            if selected:
                self._cache_path(selected).unlink(missing_ok=True)
            self.link_path.unlink(missing_ok=True)
            raise
        except ServiceUnavailable:
            if cached is None:
                raise
            return {"index": CatalogIndex(cached), "projects": [], "selected": selected,
                    "state": "OFFLINE — cached snapshot; service currency unknown",
                    "local": self.local_state(cached)}
