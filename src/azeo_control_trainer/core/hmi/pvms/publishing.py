"""Display lifecycle — sheet 05. Saving is not publishing.

A save that reaches operators is the most damaging default this tool
could ship with, so the store keeps a draft lane and a publish step
with:

- a **diff in display terms** — PVMs added, removed, moved, restyled —
  because reviewers should not read the layout format;
- a **publish gate**: unresolved binding paths block the publish, and
  removed PVMs report how many monitored items they release (leaks
  surface as slow drift);
- **workstation targeting** — publish to some stations, validate, then
  go wide;
- **revision history with one-action revert** and no export/import
  round trip;
- **single-writer locking** per display — several engineers share one
  project from week one, and last-write-wins loses work silently.

Layout on disk, one directory per display under the area's
`displays/pvm/`:

    <name>/draft.json          the working copy
    <name>/revisions/<n>.json  retained published revisions
    <name>/history.json        who published what, when, where
    <name>/.lock               single-writer lock
"""
from __future__ import annotations

import getpass
import copy
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..compatibility import (
    LEGACY_PVM_COLLECTION_KEY,
    normalize_display_document,
    pvm_records,
)
from .base import Pvm, registry
from .json_io import atomic_write_json

RETAINED_REVISIONS = 10
_LOCK_STALE_S = 8 * 3600
_LOCK_RECOVERY_GUARD_STALE_S = 30
CURRENT_DISPLAY_SCHEMA_VERSION = 3


#: Verification severities, from `azeolive.chm` "Verification tests
#: performed on graphics configuration". Only ERROR blocks a publish:
#:
#:   Informational  a suggestion; still eligible
#:   Warning        some functionality may not work; still eligible
#:   Error          severe enough that it should not be deployed online
#:
#: The distinction is the point. A gate that refuses on every finding
#: gets switched off, and then nothing is checked at all — so the
#: severities exist to keep the ERROR refusal credible.
INFORMATIONAL, WARNING, ERROR = "informational", "warning", "error"
SEVERITIES = (INFORMATIONAL, WARNING, ERROR)


@dataclass(frozen=True)
class Finding:
    """One verification result."""

    severity: str
    message: str
    item: str = ""

    @property
    def blocks_publish(self) -> bool:
        return self.severity == ERROR


def blocking(findings) -> tuple:
    """Only the findings that actually refuse a publish."""
    return tuple(f for f in findings if f.blocks_publish)


class PublishRefused(RuntimeError):
    """The gate said no; the message says why."""


class DisplayLocked(RuntimeError):
    pass


@dataclass
class PvmDisplay:
    """One display: PVM placements plus drawing and data elements.

    Most items are static shapes. Data Links carry a read path and
    Display Links carry a navigation target; their runtime bindings
    still belong to the shared renderer, never to this document.
    """

    name: str
    pvms: list = field(default_factory=list)
    items: list = field(default_factory=list)
    description: str = ""
    background: str = ""
    #: Optional authored display frame. Zero keeps the historical unbounded
    #: canvas and derives its size from content.
    width: int = 0
    height: int = 0
    #: Edit-only inset guide used by page alignment and containment tools.
    #: It is document metadata, not operator ink.
    safe_margin: int = 24
    fit: str = "fit_to_frame"
    view_type: str = "scale_to_frame"
    #: ISA-101 display level: 1 overview, 2 unit, 3 detail,
    #: 4 diagnostics/support. The display SET's navigation derives
    #: from level + parent — configuration, not scripting.
    # New independent displays start at the hierarchy root.  Older drafts
    # omitted level 2 because it used to be the implicit default; from_dict
    # deliberately preserves that legacy interpretation below.
    level: int = 1
    parent: str = ""
    #: Azeo's `ShowTag` layout variable — what every PVM's display
    #: tag shows unless the PVM overrides it. A LAYOUT concern, not a
    #: per-PVM one: an operator reading a display wants one kind of
    #: name on all of it, not module names beside friendly names.
    show_tag: str = "module"
    #: Typed graphics variables and built-in open/close action lists.
    variables: list = field(default_factory=list)
    events: dict = field(default_factory=dict)
    #: Work In Progress — "this display is not ready to be published".
    #:
    #: It ADVISES, it does not refuse: Azeo auto-deselects the
    #: display's Publish check box and shows Yes in a Work in Progress
    #: column, and a user may still tick it. Modelling it as a refusal
    #: would be stricter than Azeo and would make an engineer's
    #: judgement unreachable.
    work_in_progress: bool = False
    #: Why it was set or cleared. Azeo keeps these as events on the
    #: display's File -> Info and drops the comment once published.
    wip_reason: str = ""
    #: Absent means a legacy document that predates explicit schemas. Loading
    #: must not rewrite project files; an explicit Save or Publish upgrades it
    #: to ``CURRENT_DISPLAY_SCHEMA_VERSION``.
    schema_version: int | None = None
    #: Back-to-front IDs disambiguate equal-z objects across item families.
    #: Legacy documents retain their original PVM-then-drawing insertion order.
    stacking_order: list[str] = field(default_factory=list)
    commissioning: list[dict] = field(default_factory=list)
    test_sequences: list[dict] = field(default_factory=list)
    #: Prepared custom SVGs pinned into a published revision. Drafts normally
    #: resolve the project library; revisions remain self-contained forever.
    symbol_assets: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"display": self.name,
             "pvms": [g.to_dict() if isinstance(g, Pvm) else dict(g)
                       for g in self.pvms]}
        if self.schema_version is not None:
            d["schema_version"] = int(self.schema_version)
        if self.items:                  # absent when empty (I1 habit)
            d["items"] = [dict(i) for i in self.items]
        if self.stacking_order:
            d["stacking_order"] = list(self.stacking_order)
        if self.commissioning:
            d["commissioning"] = [dict(row) for row in self.commissioning]
        if self.test_sequences:
            d["test_sequences"] = copy.deepcopy(self.test_sequences)
        if self.symbol_assets:
            d["symbol_assets"] = dict(self.symbol_assets)
        if self.description:
            d["description"] = self.description
        if self.background:
            d["background"] = self.background
        if self.width > 0:
            d["width"] = int(self.width)
        if self.height > 0:
            d["height"] = int(self.height)
        if self.safe_margin != 24:
            d["safe_margin"] = max(0, int(self.safe_margin))
        if self.fit != "fit_to_frame":
            d["fit"] = self.fit
        if self.view_type != "scale_to_frame":
            d["view_type"] = self.view_type
        # Always persist this now.  The historical format omitted L2, so an
        # absent value must continue to mean L2 when old projects are read;
        # explicit output keeps a new L1 display stable across a round-trip.
        d["level"] = self.level
        if self.parent:
            d["parent"] = self.parent
        if self.show_tag != "module":
            d["show_tag"] = self.show_tag
        if self.variables:
            d["variables"] = [dict(value) for value in self.variables]
        if self.events:
            d["events"] = {name: [dict(action) for action in actions]
                           for name, actions in self.events.items()}
        if self.work_in_progress:
            d["work_in_progress"] = True
            if self.wip_reason:
                d["wip_reason"] = self.wip_reason
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "PvmDisplay":
        data = normalize_display_document(data)
        symbol_assets = data.get("symbol_assets", {})
        if not isinstance(symbol_assets, dict):
            symbol_assets = {}
        return cls(name=data.get("display", ""),
                   pvms=list(data.get("pvms", [])),
                   items=list(data.get("items", [])),
                   stacking_order=list(data.get("stacking_order", [])),
                   commissioning=list(data.get("commissioning", [])),
                   test_sequences=copy.deepcopy(data.get("test_sequences", [])),
                   symbol_assets={
                       str(name): str(svg)
                       for name, svg in symbol_assets.items()},
                   schema_version=(
                       int(data["schema_version"])
                       if data.get("schema_version") is not None else None),
                   description=data.get("description", ""),
                   background=data.get("background", ""),
                   width=max(0, int(data.get("width", 0) or 0)),
                   height=max(0, int(data.get("height", 0) or 0)),
                   safe_margin=max(
                       0, int(data.get("safe_margin", 24) or 0)),
                   fit=data.get("fit", "fit_to_frame"),
                   view_type=data.get("view_type", "scale_to_frame"),
                   # Backward compatibility: files created before levels
                   # became explicit omitted the then-default L2 value.
                   level=int(data.get("level", 2)),
                   parent=data.get("parent", ""),
                   show_tag=data.get("show_tag", "module"),
                   variables=list(data.get("variables", [])),
                   events=dict(data.get("events", {})),
                   work_in_progress=bool(data.get("work_in_progress")),
                   wip_reason=data.get("wip_reason", ""))


_pvm_display_init = PvmDisplay.__init__


def _pvm_display_init_with_legacy_collection(self, *args, **kwargs):
    """Accept the previous constructor keyword while emitting only ``pvms``."""
    if LEGACY_PVM_COLLECTION_KEY in kwargs:
        if "pvms" in kwargs:
            raise TypeError("Specify only the current PVM collection keyword")
        kwargs["pvms"] = kwargs.pop(LEGACY_PVM_COLLECTION_KEY)
    _pvm_display_init(self, *args, **kwargs)


PvmDisplay.__init__ = _pvm_display_init_with_legacy_collection
setattr(
    PvmDisplay,
    LEGACY_PVM_COLLECTION_KEY,
    property(
        lambda self: self.pvms,
        lambda self, value: setattr(self, "pvms", value),
        doc="Compatibility alias for the PVM collection.",
    ),
)


def _pvm_key(pvm: dict) -> str:
    params = pvm.get("params", {})
    return f"{pvm.get('class', '')}@" + "/".join(
        str(v) for v in params.values())


def display_diff(old: dict, new: dict) -> list[str]:
    """Changes in display terms, including native drawing elements.

    Reviewers should never have to infer a pipe, table, action, page-size or
    hierarchy change from raw JSON.  Keep the historic PVM wording and add
    the rest of the one canonical ``PvmDisplay`` document.
    """
    def number(value) -> str:
        try:
            return f"{float(value):g}"
        except (TypeError, ValueError):
            return str(value or 0)

    def label(item: dict) -> str:
        return str(item.get("title") or item.get("name")
                   or item.get("id") or item.get("kind") or "item")

    old_pvms = {g["id"]: g for g in pvm_records(old)}
    new_pvms = {g["id"]: g for g in pvm_records(new)}
    lines = []
    for pvm_id, pvm in new_pvms.items():
        if pvm_id not in old_pvms:
            params = "/".join(str(v)
                              for v in pvm.get("params", {}).values())
            lines.append(f"+ PVM {params} {pvm.get('class', '')}")
    for pvm_id, pvm in old_pvms.items():
        if pvm_id not in new_pvms:
            params = "/".join(str(v)
                              for v in pvm.get("params", {}).values())
            lines.append(f"- PVM {params} {pvm.get('class', '')}")
    for pvm_id in old_pvms.keys() & new_pvms.keys():
        before, after = old_pvms[pvm_id], new_pvms[pvm_id]
        params = "/".join(str(v)
                          for v in after.get("params", {}).values())
        if (before.get("x"), before.get("y")) \
                != (after.get("x"), after.get("y")):
            lines.append(
                f"~ moved {params} {number(before.get('x'))},"
                f"{number(before.get('y'))} -> {number(after.get('x'))},"
                f"{number(after.get('y'))}")
        if (before.get("w"), before.get("h")) \
                != (after.get("w"), after.get("h")):
            lines.append(
                f"~ resized {params} {number(before.get('w'))}×"
                f"{number(before.get('h'))} -> {number(after.get('w'))}×"
                f"{number(after.get('h'))}")
        if before.get("standard") != after.get("standard"):
            lines.append(f"~ std {params} "
                         f"{after.get('standard', '')}")
        if (before.get("fill", ""), before.get("line", "")) != (
                after.get("fill", ""), after.get("line", "")):
            fill = after.get("fill") or "theme"
            line = after.get("line") or "theme"
            lines.append(
                f"~ appearance {params} fill={fill} line={line}")
        if before.get("params") != after.get("params"):
            lines.append(f"~ rebound {pvm_id} -> {params}")

    old_items = {str(item.get("id") or f"legacy-{index}"): item
                 for index, item in enumerate(old.get("items", []))}
    new_items = {str(item.get("id") or f"legacy-{index}"): item
                 for index, item in enumerate(new.get("items", []))}
    for item_id in new_items.keys() - old_items.keys():
        item = new_items[item_id]
        lines.append(f"+ {str(item.get('kind', 'item')).replace('_', ' ')} "
                     f"{label(item)}")
    for item_id in old_items.keys() - new_items.keys():
        item = old_items[item_id]
        lines.append(f"- {str(item.get('kind', 'item')).replace('_', ' ')} "
                     f"{label(item)}")
    geometry = ("x", "y", "w", "h", "rot")
    appearance = (
        "fill", "line", "width", "style", "opacity", "visible",
        "text", "text_color", "font_family", "font_size", "font_bold",
        "font_italic", "font_underline", "icon", "fill_pct",
        "fill_direction",
    )
    binding = (
        "path", "target", "path_prefix", "entry", "pens", "parameters",
        "rows", "columns", "props", "ports", "stream_direction",
        "anim", "lo", "hi",
    )
    routing = (
        "a", "b", "a_side", "b_side", "auto", "route_mode",
        "route_points", "clearance", "crossover",
    )
    for item_id in old_items.keys() & new_items.keys():
        before, after = old_items[item_id], new_items[item_id]
        name = label(after)
        if any(before.get(key) != after.get(key) for key in geometry):
            if (before.get("x"), before.get("y")) \
                    != (after.get("x"), after.get("y")):
                lines.append(f"~ moved {name}")
            if (before.get("w"), before.get("h")) \
                    != (after.get("w"), after.get("h")):
                lines.append(f"~ resized {name}")
            if before.get("rot") != after.get("rot"):
                lines.append(f"~ rotated {name}")
        if any(before.get(key) != after.get(key) for key in appearance):
            lines.append(f"~ restyled {name}")
        if any(before.get(key) != after.get(key) for key in binding):
            lines.append(f"~ rebound/configured {name}")
        if after.get("kind") == "pipe" and any(
                before.get(key) != after.get(key) for key in routing):
            lines.append(f"~ rerouted {name}")
        if before.get("actions") != after.get("actions"):
            lines.append(f"~ interaction changed {name}")
        if before.get("layer") != after.get("layer"):
            lines.append(f"~ layer changed {name}")

    metadata = {
        "description": "description", "background": "background",
        "width": "page width", "height": "page height",
        "safe_margin": "safe margin", "fit": "fit mode",
        "view_type": "view type", "level": "display level",
        "parent": "parent display", "show_tag": "tag presentation",
        "variables": "display variables", "events": "display events",
        "stacking_order": "object stacking",
        "commissioning": "commissioning checklist",
        "test_sequences": "visual TEST sequences",
    }
    for key, title in metadata.items():
        if old.get(key) != new.get(key):
            lines.append(f"~ {title} changed")
    old_assets = old.get("symbol_assets", {})
    new_assets = new.get("symbol_assets", {})
    old_assets = old_assets if isinstance(old_assets, dict) else {}
    new_assets = new_assets if isinstance(new_assets, dict) else {}
    for name in sorted(new_assets.keys() - old_assets.keys()):
        lines.append(f"+ custom symbol {name}")
    for name in sorted(old_assets.keys() - new_assets.keys()):
        lines.append(f"- custom symbol {name}")
    for name in sorted(old_assets.keys() & new_assets.keys()):
        if old_assets[name] != new_assets[name]:
            lines.append(f"~ custom symbol artwork changed {name}")
    return lines



def displays_using_class(pvm_class: str, displays) -> tuple:
    """Displays that must be published because a PVM class changed.

    `azeolive.chm`: "PVM classes do not require publishing. Instead,
    when a PVM class is modified, the displays containing the PVMs
    linked to that PVM class must be published."

    So a class edit produces a list of *displays*, and there is
    deliberately no `publish(pvm_class)` anywhere to call. A plain
    function rather than a method, because it asks nothing of a store —
    it is a question about what a set of documents contains.
    """
    touched = []
    for display in displays:
        pvms = getattr(display, "pvms", None)
        items = getattr(display, "items", None)
        if pvms is None and isinstance(display, dict):
            pvms = pvm_records(display)
            items = display.get("items", [])
        matched = False
        for pvm in pvms or ():
            data = pvm if isinstance(pvm, dict) else pvm.to_dict()
            block, _, role = str(data.get("class", "")).partition("/")
            installed = registry.get(block, role, str(data.get("variant", "")))
            # Sidecars are named after the registered Python class, while a
            # display stores block/role/variant. Without resolving that key,
            # saving PIDCompact reported no consumers even on a PID display.
            if block == pvm_class or data.get("class") == pvm_class \
                    or installed is not None and installed.__name__ == pvm_class:
                matched = True
                break
        if not matched:
            for item in items or ():
                if not isinstance(item, dict):
                    continue
                # Current authored instances carry an authoritative root
                # identity.  It must win over the legacy per-member fields:
                # otherwise a root explicitly unlinked from its class can be
                # reported as affected merely because an old ``pvm_link``
                # value survived on one flattened member.
                root = str(item.get("instance_definition") or "")
                if root:
                    root_link = str(
                        item.get("instance_link", "linked") or "linked")
                    if root_link != "linked":
                        continue
                    if root == pvm_class:
                        matched = True
                        break

                    # A linked root may contain linked nested classes.  The
                    # chain records intermediate definitions even when an
                    # intermediate class contributes no visible member of its
                    # own.  ``pvm_link`` remains the member/nested link state;
                    # an explicitly unlinked nested definition must stay
                    # frozen when that nested class changes.
                    member_link = str(
                        item.get("pvm_link", "linked") or "linked")
                    chain = item.get("pvm_chain", ())
                    if not isinstance(chain, (list, tuple, set)):
                        chain = ()
                    nested_classes = {
                        str(value) for value in chain if str(value)
                    }
                    nested_classes.update(filter(None, (
                        str(item.get("user_pvm") or ""),
                        str(item.get("nested_pvm") or ""),
                    )))
                    if member_link == "linked" \
                            and pvm_class in nested_classes:
                        matched = True
                        break
                    continue

                # Legacy documents predate stable instance identity and only
                # have the class/link pair on each flattened member.
                if item.get("user_pvm") == pvm_class \
                        and item.get("pvm_link", "linked") == "linked":
                    matched = True
                    break
        if matched:
            name = getattr(display, "name", "")
            if not name and isinstance(display, dict):
                name = display.get("display", "")
            if name:
                touched.append(name)
    return tuple(sorted(touched))


class DisplayStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        # User names are presentation, not ownership.  Two application
        # sessions commonly run as the same Windows account, so every store
        # session gets an unguessable identity written into its lock files.
        self._session_token = uuid.uuid4().hex
        self._held_locks: dict[str, str] = {}

    # ------------------------------------------------------------ locking
    def _dir(self, name: str) -> Path:
        return self.root / name

    @staticmethod
    def _exclusive_json(path: Path, document: dict) -> None:
        """Create *path* once; a concurrent creator gets FileExistsError."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write((json.dumps(document) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            # This process created the name exclusively, so removing a failed
            # partial reservation cannot discard another session's lock.
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _lock_document(path: Path) -> dict:
        """Read lock metadata; a just-created partial file stays fresh."""
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                document = {}
        except (OSError, ValueError, TypeError):
            document = {}
        try:
            document["at"] = float(document.get("at", path.stat().st_mtime))
        except (OSError, TypeError, ValueError):
            document["at"] = time.time()
        return document

    @staticmethod
    def _fresh(document: dict, timeout: float = _LOCK_STALE_S) -> bool:
        return time.time() - float(document.get("at", time.time())) < timeout

    def _lock_payload(self, who: str) -> dict:
        return {"who": who, "at": time.time(),
                "token": self._session_token}

    def _acquire_recovery_guard(self, lock: Path) -> Path | None:
        """Serialize stale takeover and token-checked release.

        Ordinary acquisition remains one atomic exclusive create.  The guard
        only protects the exceptional operations that remove an existing lock
        name, preventing a stale takeover from racing an owner's release.
        """
        guard = lock.with_name(f"{lock.name}.recovery")
        for _attempt in range(8):
            try:
                self._exclusive_json(
                    guard, {"at": time.time(), "token": self._session_token})
                return guard
            except FileExistsError:
                held = self._lock_document(guard)
                if not self._fresh(
                        held, _LOCK_RECOVERY_GUARD_STALE_S):
                    try:
                        guard.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                time.sleep(0.005)
        return None

    def _recover_stale_lock(self, lock: Path, payload: dict) -> bool:
        guard = self._acquire_recovery_guard(lock)
        if guard is None:
            return False
        try:
            if lock.exists():
                held = self._lock_document(lock)
                if self._fresh(held):
                    return False
                lock.unlink()
            try:
                self._exclusive_json(lock, payload)
            except FileExistsError:
                # A normal acquirer may win the intentional unlink/create
                # window.  Its fresh lock is authoritative.
                return False
            return True
        finally:
            try:
                guard.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _locked_error(held: dict) -> DisplayLocked:
        stamp = time.strftime(
            "%H:%M", time.localtime(float(held.get("at", 0))))
        return DisplayLocked(
            f"Locked for editing — {held.get('who', '?')} opened "
            f"this display at {stamp}. Editing is single-writer "
            "per display.")

    def acquire_lock(self, name: str, who: str = "") -> str:
        """Atomically acquire one display for this application session."""
        who = who or getpass.getuser()
        lock = self._dir(name) / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        payload = self._lock_payload(who)
        try:
            self._exclusive_json(lock, payload)
        except FileExistsError:
            held = self._lock_document(lock)
            if held.get("token") == self._session_token \
                    and self._held_locks.get(name) == who:
                return self._session_token
            if self._fresh(held) or not self._recover_stale_lock(lock, payload):
                # Re-read after a failed recovery: another contender may have
                # replaced the stale owner while this session waited.
                raise self._locked_error(self._lock_document(lock))
        self._held_locks[name] = who
        return self._session_token

    def release_lock(self, name: str, who: str = "") -> None:
        lock = self._dir(name) / ".lock"
        if not lock.exists():
            self._held_locks.pop(name, None)
            return
        guard = self._acquire_recovery_guard(lock)
        if guard is None:
            return
        try:
            if not lock.exists():
                self._held_locks.pop(name, None)
                return
            held = self._lock_document(lock)
            expected_who = self._held_locks.get(name)
            owns = held.get("token") == self._session_token \
                and expected_who is not None
            if who and who != expected_who:
                owns = False
            if owns:
                lock.unlink()
                self._held_locks.pop(name, None)
        finally:
            try:
                guard.unlink()
            except FileNotFoundError:
                pass

    def owns_lock(self, name: str) -> bool:
        """Check the actual token; a stale editor flag cannot authorize Save."""
        return name in self._held_locks and self._lock_document(
            self._dir(name) / ".lock").get("token") == self._session_token

    def new_display_path(self, name: str) -> Path:
        """Resolve a single display name before reserving or moving a folder."""
        if not name or name in (".", "..") or any(
                char in name for char in '/\\:*?"<>|') or name[-1] in ' .':
            raise ValueError("Enter a display name without path separators.")
        path = self.root / name
        if path.resolve().parent != self.root.resolve():
            raise ValueError("Display must stay inside the graphics library.")
        return path

    def create_draft(self, display: PvmDisplay) -> Path:
        """Reserve a new name atomically; never replace an existing display."""
        directory = self.new_display_path(display.name)
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()
        try:
            return self.save_draft(display)
        except Exception:
            # Only remove our own empty reservation, never existing content.
            if not any(directory.iterdir()):
                directory.rmdir()
            raise

    def rename_display(self, name: str, new_name: str) -> None:
        """Move an owned document and its history, retaining edit ownership."""
        from azeo_control_trainer.core.configuration.workspace import draft_root
        if draft_root(self.root):
            raise ValueError("Shared display rename is not available in this pilot. "
                             "Its consumers and immutable history need a reviewed repository rename.")
        from .configuration import InstalledItems
        InstalledItems(self.root).assert_editable("display", name)
        if not self.owns_lock(name):
            raise DisplayLocked("Open this display for editing before renaming.")
        source = self.new_display_path(name)
        target = self.new_display_path(new_name)
        if target.exists():
            raise FileExistsError(f"{new_name} already exists.")
        documents = {
            filename: json.loads((source / filename).read_text(encoding="utf-8"))
            for filename in ("draft.json", ".recovery.json")
            if (source / filename).exists()
        }
        old_fingerprint = self.draft_fingerprint(name)
        source.rename(target)
        self._held_locks[new_name] = self._held_locks.pop(name)
        try:
            for filename, original in documents.items():
                changed = dict(original, display=new_name)
                if filename == ".recovery.json":
                    metadata = dict(changed.get("_recovery", {}))
                    if metadata.get("base_draft_sha256") == old_fingerprint:
                        metadata["base_draft_sha256"] = self.draft_fingerprint(new_name)
                        changed["_recovery"] = metadata
                atomic_write_json(target / filename, changed)
        except Exception:
            # A failed metadata write must not strand an open tab at the old
            # name. Roll back the move and any already-replaced document.
            target.rename(source)
            self._held_locks[name] = self._held_locks.pop(new_name)
            for filename, original in documents.items():
                atomic_write_json(source / filename, original)
            raise
        # Retained revisions remain immutable; readers resolve their
        # historical name through the owning directory.

    # ------------------------------------------------------------- drafts
    def draft_fingerprint(self, name: str) -> str:
        return self._digest(self._dir(name) / "draft.json")

    def save_draft(self, display: PvmDisplay) -> Path:
        from .configuration import InstalledItems
        InstalledItems(self.root).assert_editable("display", display.name)
        path = self._dir(display.name) / "draft.json"
        document = display.to_dict()
        document["schema_version"] = CURRENT_DISPLAY_SCHEMA_VERSION
        atomic_write_json(path, document)
        # Upgrade only after replacement succeeds. A failed explicit Save must
        # not make the in-memory object claim a schema that never reached disk.
        display.schema_version = CURRENT_DISPLAY_SCHEMA_VERSION
        return path

    def load_draft(self, name: str) -> PvmDisplay | None:
        path = self._dir(name) / "draft.json"
        if not path.exists():
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
        document["display"] = name
        return PvmDisplay.from_dict(document)

    # ----------------------------------------------------------- recovery
    def save_recovery(self, display: PvmDisplay) -> Path:
        """Atomically checkpoint an unsaved authoring document.

        Recovery is deliberately separate from ``draft.json``: a timer must
        never turn an unreviewed edit into an engineering save.  ``replace``
        prevents a process termination during JSON output from leaving a
        half-document that blocks the next launch.
        """
        path = self._dir(display.name) / ".recovery.json"
        draft = self._dir(display.name) / "draft.json"
        document = display.to_dict()
        document["schema_version"] = (
            display.schema_version or CURRENT_DISPLAY_SCHEMA_VERSION)
        document["_recovery"] = {
            "written_at_ns": time.time_ns(),
            # A base fingerprint resolves equal/coarse file timestamps and,
            # more importantly, proves when an explicit later Save superseded
            # this checkpoint even if both files report the same mtime.
            "base_draft_sha256": self._digest(draft),
        }
        atomic_write_json(path, document)
        return path

    def load_recovery(self, name: str) -> PvmDisplay | None:
        """Return a newer valid recovery checkpoint, never stale autosave."""
        path = self._dir(name) / ".recovery.json"
        draft = self._dir(name) / "draft.json"
        if not path.exists():
            return None
        try:
            recovery_document = json.loads(path.read_text(encoding="utf-8"))
            recovery_meta = recovery_document.pop("_recovery", {})
            if draft.exists():
                base_digest = recovery_meta.get("base_draft_sha256", "") \
                    if isinstance(recovery_meta, dict) else ""
                if base_digest:
                    if self._digest(draft) != base_digest:
                        return None
                elif path.stat().st_mtime_ns < draft.stat().st_mtime_ns:
                    # Legacy recovery files have no fingerprint. Equality is
                    # intentionally eligible: coarse timestamps used to drop
                    # genuine unsaved work created in the same clock tick.
                    return None
                draft_document = json.loads(
                    draft.read_text(encoding="utf-8"))
                if recovery_document == draft_document:
                    return None
            recovery_document["display"] = name
            return PvmDisplay.from_dict(recovery_document)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _digest(path: Path) -> str:
        """Content identity used to order draft and recovery safely."""
        if not path.exists():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def clear_recovery(self, name: str) -> None:
        path = self._dir(name) / ".recovery.json"
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------ history
    def history(self, name: str) -> list[dict]:
        path = self._dir(name) / "history.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_history(self, name: str, entries: list[dict]) -> None:
        atomic_write_json(self._dir(name) / "history.json", entries)

    def revision_document(self, name: str, rev: int) -> dict | None:
        path = self._dir(name) / "revisions" / f"{rev}.json"
        if not path.exists():
            return None
        document = normalize_display_document(
            json.loads(path.read_text(encoding="utf-8")))
        document["display"] = name
        return document

    def published_document(self, name: str,
                           workstation: str = "") -> dict | None:
        """What a station shows: its targeted revision, else the widest
        current one."""
        for entry in reversed(self.history(name)):
            stations = entry.get("workstations")
            if not stations or not workstation \
                    or workstation in stations:
                return self.revision_document(name, entry["rev"])
        return None

    # ------------------------------------------------------------ publish
    def publish(self, display: PvmDisplay, env: str = "TEST",
                workstations: list | None = None, by: str = "",
                resolve=None, released_items: int = 0,
                allow_wip: bool = False, findings=(), repository_release=None,
                _prefer_symbol_assets: bool = False) -> dict:
        """The gated step. `resolve(path) -> bool` is the binding-health
        check; any unresolved path refuses the publish by name."""
        from azeo_control_trainer.core.configuration.workspace import reject_draft_deployment
        try:
            reject_draft_deployment(self.root)
        except ValueError as error:
            raise PublishRefused(str(error)) from error
        from .configuration import InstalledItems
        InstalledItems(self.root).assert_editable("display", display.name)
        blockers = blocking(tuple(findings or ()))
        if blockers:
            details = "; ".join(
                f"{finding.item}: {finding.message}"
                if finding.item and finding.item not in finding.message
                else finding.message
                for finding in blockers)
            raise PublishRefused(
                "verification error(s) block this publish: " + details)
        if resolve is not None:
            unresolved = []
            for pvm in display.pvms:
                pvm_dict = pvm.to_dict() if isinstance(pvm, Pvm) \
                    else dict(pvm)
                for value in pvm_dict.get("params", {}).values():
                    if not resolve(str(value)):
                        unresolved.append(str(value))
            if unresolved:
                raise PublishRefused(
                    "unresolved binding path(s) block this publish: "
                    + ", ".join(sorted(set(unresolved))))

        # A Work In Progress publishes only when the caller says so.
        # Azeo auto-deselects the check box and lets the user tick it
        # again; `allow_wip` IS that tick, made explicit so it cannot
        # happen by accident in a script.
        if display.work_in_progress and not allow_wip:
            raise PublishRefused(
                "%s is marked Work In Progress%s — publish it explicitly "
                "with allow_wip=True if that is intended"
                % (display.name,
                   " (%s)" % display.wip_reason if display.wip_reason
                   else ""))

        history = self.history(display.name)
        if repository_release is not None:
            required = {"release_id", "package_hash", "command_id", "object_id", "revision", "digest"}
            if set(repository_release) != required:
                raise PublishRefused("Incomplete repository release identity")
            for entry in history:
                previous_release = entry.get("repository_release", {})
                if previous_release.get("command_id") == repository_release["command_id"]:
                    if previous_release != repository_release:
                        raise PublishRefused("Publication command already identifies another release")
                    return entry
        rev = (history[-1]["rev"] + 1) if history else 1
        previous = self.revision_document(display.name,
                                          history[-1]["rev"]) \
            if history else {"pvms": []}
        document = display.to_dict()
        document["schema_version"] = CURRENT_DISPLAY_SCHEMA_VERSION
        pinned = self._pin_symbol_assets(
            display, document, prefer_embedded=_prefer_symbol_assets)
        if pinned:
            document["symbol_assets"] = pinned
        else:
            document.pop("symbol_assets", None)
        published_as_wip = bool(display.work_in_progress)
        # WIP is an engineering-draft state, never operator runtime state.
        # Remove both fields unconditionally so a stale reason cannot leak
        # into a released revision. Mutation of the live draft waits until
        # revision + history both commit.
        document.pop("work_in_progress", None)
        document.pop("wip_reason", None)
        rev_dir = self._dir(display.name) / "revisions"
        atomic_write_json(rev_dir / f"{rev}.json", document)
        entry = {
            "rev": rev, "env": env,
            "workstations": list(workstations or []),
            "by": by or getpass.getuser(),
            "at": time.strftime("%Y-%m-%d %H:%M"),
            "diff": display_diff(previous, document),
            "released_items": released_items,
        }
        if published_as_wip:
            entry["published_as_wip"] = True
        if repository_release is not None:
            # This identity commits with history, before any acknowledgment.
            # Replaying a lost response therefore cannot publish a second revision.
            entry["repository_release"] = dict(repository_release)
        history.append(entry)
        self._write_history(display.name, history)
        display.schema_version = CURRENT_DISPLAY_SCHEMA_VERSION
        if published_as_wip or display.wip_reason:
            # "This comment is removed once the display is published." Do it
            # only after history commits; a failed publish leaves the user's
            # draft state truthful and retryable.
            display.work_in_progress = False
            display.wip_reason = ""
        # Retain the last N revision documents.
        keep = {e["rev"] for e in history[-RETAINED_REVISIONS:]}
        accepted_path = self.root / "_accepted.json"
        if accepted_path.exists():
            try:
                accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
                keep.update(station[display.name] for station in accepted.values()
                            if isinstance(station, dict) and display.name in station)
            except (OSError, ValueError, TypeError):
                # Missing acceptance evidence must never prune an operator's
                # possibly held revision. A later healthy publish can collect it.
                keep.update(e["rev"] for e in history)
        for path in rev_dir.glob("*.json"):
            if int(path.stem) not in keep:
                path.unlink()
        return entry

    def _pin_symbol_assets(self, display: PvmDisplay,
                           document: dict, *,
                           prefer_embedded: bool = False) -> dict[str, str]:
        """Embed every custom symbol used by this exact revision."""
        from .svg_import import MAX_SVG_BYTES, SvgImportError, prepare
        from .symbols import (
            MAX_DOCUMENT_SYMBOL_BYTES, MAX_DOCUMENT_SYMBOLS, VENDORED_NAMES,
        )

        names: set[str] = set()
        stack = [document.get("pvms", ()), document.get("items", ())]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                symbol = value.get("symbol")
                if isinstance(symbol, str) and symbol:
                    names.add(symbol)
                stack.extend(value.values())
            elif isinstance(value, (list, tuple)):
                stack.extend(value)

        pinned = {}
        total = 0
        library = self.root / "_library" / "svg"
        previous = dict(display.symbol_assets or {})
        custom_names = sorted(names - set(VENDORED_NAMES))
        if len(custom_names) > MAX_DOCUMENT_SYMBOLS:
            raise PublishRefused(
                f"a display may use at most {MAX_DOCUMENT_SYMBOLS} custom symbols")
        for name in custom_names:
            # Imported names are legal stems by construction. Rechecking here
            # keeps a hand-edited document from escaping the library folder.
            candidate = library / f"{name}.svg"
            try:
                if prefer_embedded and name in previous:
                    text = previous[name]
                elif candidate.parent != library or not candidate.is_file():
                    text = previous[name]
                else:
                    if candidate.stat().st_size > MAX_SVG_BYTES:
                        raise SvgImportError(
                            f"the file exceeds {MAX_SVG_BYTES} bytes")
                    text = candidate.read_text(encoding="utf-8", errors="replace")
                prepared = prepare(text).svg
                total += len(prepared.encode("utf-8"))
                if total > MAX_DOCUMENT_SYMBOL_BYTES:
                    raise SvgImportError(
                        "the display's custom symbols exceed the "
                        f"{MAX_DOCUMENT_SYMBOL_BYTES}-byte revision limit")
                pinned[name] = prepared
            except KeyError as error:
                raise PublishRefused(
                    f"custom symbol {name!r} is missing from the project library") from error
            except (OSError, SvgImportError) as error:
                raise PublishRefused(
                    f"custom symbol {name!r} cannot be published: {error}") from error
        return pinned

    def revert(self, name: str, rev: int, by: str = "") -> dict:
        """One action from any retained revision: the old document is
        republished as a NEW revision, so history stays append-only."""
        document = self.revision_document(name, rev)
        if document is None:
            raise PublishRefused(f"revision {rev} is not retained")
        display = PvmDisplay.from_dict(document)
        original = next((e for e in self.history(name)
                         if e["rev"] == rev), {})
        entry = self.publish(display, env=original.get("env", "PROD"),
                             workstations=original.get("workstations"),
                             by=by, _prefer_symbol_assets=True)
        entry["reverted_from"] = rev
        history = self.history(name)
        history[-1] = entry
        self._write_history(name, history)
        self.save_draft(display)
        return entry
