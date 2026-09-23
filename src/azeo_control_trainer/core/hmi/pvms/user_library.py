"""User PVM classes — Convert-to-PVM, the Azeo creation flow.

In Azeo, a PVM's graphics are authored in Graphics Designer: draw the
shapes (or import an image), select them, and convert the selection
into a PVM class in the library; instances then place as one object.
This module is that library for drawing-item PVMs: each entry stores
the selected items' data with geometry relative to the selection's
own origin, so a placement is a translate — nothing is re-authored.

These are *drawing* PVMs (composites of symbols, shapes and pipes),
distinct from the coded, binding-carrying PVM classes an Author writes
in Python. Placement recreates the items as one named group, so the
existing group mechanics (click grabs the group, Alt-click reaches
inside) apply unchanged.
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from pathlib import Path

from ..compatibility import (
    LEGACY_DEFAULT_FOLDER, LEGACY_PVM_LIBRARY_FILENAME, PVM_DEFAULT_FOLDER,
    PVM_LIBRARY_FILENAME, PVM_SCOPE_NAMES, USER_DEFINITION_ID_SEED,
    normalize_library_entries,
)

log = logging.getLogger(__name__)


class UserPvmLibrary:
    """`_library/user_pvms.json` beside the displays."""

    SCHEMA_VERSION = 2
    _INSTANCE_FIELDS = frozenset({
        "group", "user_pvm", "pvm_index", "pvm_link", "pvm_choices",
        "pvm_overrides", "pvm_chain", "nested_pvm", "instance_id",
        "instance_definition", "instance_link", "instance_choices",
        "instance_overrides", "instance_origin_x", "instance_origin_y",
        "definition_id", "definition_revision", "source_x", "source_y",
        "instance_internal", "instance_detached_nested",
        "class_revision",
    })

    def __init__(self, root):
        self.path = Path(root) / "_library" / PVM_LIBRARY_FILENAME
        self._legacy_path = self.path.with_name(LEGACY_PVM_LIBRARY_FILENAME)
        self.entries: dict = {}
        self.reload()

    @classmethod
    def from_snapshot(cls, root, entries, configurations):
        """Instantiate captured classes using the same expansion code, without disk reads."""
        library = cls.__new__(cls)
        library.path = Path(root) / "_library" / PVM_LIBRARY_FILENAME
        library._legacy_path = library.path.with_name(LEGACY_PVM_LIBRARY_FILENAME)
        library._snapshot = (copy.deepcopy(entries), copy.deepcopy(configurations))
        library.reload()
        return library

    def reload(self) -> None:
        """Fresh disk state. The studio caches one library while the
        configurator and its dialogs build their own, so a mutator
        working from a stale snapshot would silently drop whatever
        another instance wrote — shape bindings, most painfully."""
        if getattr(self, "_snapshot", None) is not None:
            self.entries = normalize_library_entries(copy.deepcopy(self._snapshot[0]))
            return
        self.entries = {}
        source = self.path if self.path.exists() else self._legacy_path
        if source.exists():
            try:
                self.entries = normalize_library_entries(json.loads(
                    source.read_text(encoding="utf-8")))
            except Exception:                       # noqa: BLE001
                self.entries = {}

    def save(self) -> None:
        if getattr(self, "_snapshot", None) is not None:
            raise ValueError("Captured library definitions are read-only")
        from azeo_control_trainer.core.configuration.package_paths import assert_mutable
        assert_mutable(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(self.path.suffix + ".tmp")
        pending.write_text(
            json.dumps(self.entries, indent=2, ensure_ascii=False)
            + "\n", encoding="utf-8", newline="\n")
        pending.replace(self.path)

    @staticmethod
    def _valid_uuid(value) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            return ""

    def _legacy_definition_id(self, name: str) -> str:
        """Stable read-only identity for a pre-schema class.

        Loading a legacy project must not rewrite it.  UUID5 lets an open
        legacy instance participate in connection remapping immediately;
        the first explicit class save persists this same identity.
        """
        return str(uuid.uuid5(
            uuid.NAMESPACE_URL, USER_DEFINITION_ID_SEED + str(name)))

    @staticmethod
    def _source_key(item: dict, index: int) -> str:
        return str(item.get("id") or f"element-{index}")

    def _legacy_source_id(self, name: str, item: dict, index: int) -> str:
        definition_id = uuid.UUID(self._legacy_definition_id(name))
        return str(uuid.uuid5(
            definition_id, f"element:{self._source_key(item, index)}"))

    def definition_metadata(self, name: str) -> tuple[str, int]:
        """Return class identity without upgrading or writing legacy data."""
        self.reload()
        entry = self.entries.get(name) or {}
        definition_id = self._valid_uuid(entry.get("definition_id")) \
            or self._legacy_definition_id(name)
        try:
            revision = max(0, int(entry.get("definition_revision", 0)))
        except (TypeError, ValueError):
            revision = 0
        return definition_id, revision

    @staticmethod
    def override_key(source_element_id: str, prop: str) -> str:
        """Canonical instance override key: member identity, then property."""
        return f"{source_element_id}.{prop}"

    def normalize_overrides(self, name: str, overrides: dict | None, *,
                            source_by_index: dict[int, str] | None = None,
                            entry: dict | None = None) -> dict:
        """Translate positional legacy overrides to stable member identities.

        ``source_by_index`` should come from the placed snapshot when one is
        available. That distinction matters after a class member has been
        deleted or reordered: the current class index no longer describes
        what the engineer originally overrode.
        """
        if not overrides:
            return {}
        if entry is None:
            self.reload()
            entry = self.entries.get(name) or {}
        fallback = {}
        shape_index = 0
        for stored_index, item in enumerate(entry.get("items", ())):
            if item.get("kind") in ("pipe", "nested_pvm"):
                continue
            source_id = self._valid_uuid(item.get("source_element_id")) \
                or self._legacy_source_id(name, item, stored_index)
            fallback[shape_index] = source_id
            shape_index += 1
        sources = dict(fallback)
        sources.update(source_by_index or {})
        normalized = {}
        for key, value in dict(overrides).items():
            owner, separator, prop = str(key).rpartition(".")
            if not separator or not prop:
                normalized[str(key)] = value
                continue
            source_id = self._valid_uuid(owner)
            if not source_id:
                try:
                    source_id = self._valid_uuid(sources[int(owner)])
                except (KeyError, TypeError, ValueError):
                    source_id = ""
            normalized[self.override_key(source_id, prop)
                       if source_id else str(key)] = value
        return normalized

    def _upgrade_entry_metadata(self, name: str, entry: dict, *,
                                increment: bool) -> None:
        """Attach stable class/member identities during an explicit edit."""
        definition_id = self._valid_uuid(entry.get("definition_id")) \
            or self._legacy_definition_id(name)
        entry["schema_version"] = self.SCHEMA_VERSION
        entry["definition_id"] = definition_id
        try:
            revision = max(0, int(entry.get("definition_revision", 0)))
        except (TypeError, ValueError):
            revision = 0
        entry["definition_revision"] = revision + (1 if increment else 0)
        for index, item in enumerate(entry.get("items", ())):
            source_id = self._valid_uuid(item.get("source_element_id"))
            if not source_id:
                source_id = self._legacy_source_id(name, item, index)
            item["source_element_id"] = source_id

    def _assert_editable(self, name: str) -> None:
        from .configuration import InstalledItems
        InstalledItems(self.path.parent.parent).assert_editable(
            "pvm_class", name)

    def touch_definition(self, name: str) -> bool:
        """Advance an authored class after its configuration contract saves.

        The configuration sidecar changes what linked instances resolve even
        when the drawing body did not change.  Treat that explicit Save as a
        class revision while keeping reads and instantiation write-free.
        """
        self.reload()
        entry = self.entries.get(name)
        if entry is None:
            return False
        self._assert_editable(name)
        self._upgrade_entry_metadata(name, entry, increment=True)
        self.save()
        return True

    def _configuration_for(self, name: str):
        """Load a visual class's own typed contract without creating one."""
        from .configurator.model import PvmConfiguration
        if getattr(self, "_snapshot", None) is not None:
            document = self._snapshot[1].get(name)
            return PvmConfiguration.from_dict(copy.deepcopy(document)) if document else None
        path = self.path.parent.parent / "_pvmcfg" / f"{name}.pvmcfg.json"
        return PvmConfiguration.load(path) if path.exists() else None

    def names(self, folder: str = "", definition_kind: str = "") -> list:
        return sorted(name for name, entry in self.entries.items()
                      if (not folder
                          or entry.get("folder", "My PVMs") == folder)
                      and (not definition_kind
                           or entry.get("definition_kind", "pvm")
                           == definition_kind))

    def folders(self) -> tuple[str, ...]:
        return tuple(sorted({entry.get("folder", "My PVMs")
                             for entry in self.entries.values()}))

    @staticmethod
    def folder_title(folder: str) -> str:
        # Older libraries retain their folder key; only its built-in label changes.
        return PVM_DEFAULT_FOLDER if folder == LEGACY_DEFAULT_FOLDER else folder

    def add(self, name: str, item_dicts: list,
            folder: str = "My PVMs", *,
            definition_kind: str | None = None,
            paired_faceplate: str | None = None,
            master_size: tuple[float, float] | None = None) -> dict | None:
        """Store a selection as a class: geometry made relative to
        the selection's bounding origin. Pipes come along only when
        both their endpoints do.

        ``master_size`` marks a Graphics Designer class-master save. Its page
        is the class coordinate system, so deliberate whitespace is retained
        and the entry does not shrink to whichever object happens to be
        outermost. Selection conversion omits it and keeps the compact
        selection-relative behaviour.
        """
        self.reload()
        previous = self.entries.get(name, {})
        if previous:
            self._assert_editable(name)
        try:
            previous_revision = max(
                0, int(previous.get("definition_revision", 0) or 0))
        except (TypeError, ValueError):
            previous_revision = 0
        shapes = [copy.deepcopy(d) for d in item_dicts
                  if d.get("kind") != "pipe"]
        if not name or not shapes:
            return None
        ids = {d.get("id") for d in shapes}
        pipes = [copy.deepcopy(d) for d in item_dicts
                 if d.get("kind") == "pipe"
                 and d.get("a") in ids and d.get("b") in ids]
        incoming_sources = [self._valid_uuid(
            item.get("source_element_id")) for item in (*shapes, *pipes)]
        # A class document never owns placement metadata.  In particular,
        # converting a placed linked instance into another class must not
        # make two definitions share member identities.
        for item in (*shapes, *pipes):
            for field in self._INSTANCE_FIELDS:
                item.pop(field, None)
        previous_items = list(previous.get("items", ()))
        previous_by_id = {
            str(item.get("id")): (index, item)
            for index, item in enumerate(previous_items)
            if item.get("id") is not None
        }
        previous_sources = {
            self._valid_uuid(item.get("source_element_id"))
            for item in previous_items
            if self._valid_uuid(item.get("source_element_id"))
        }
        for index, item in enumerate((*shapes, *pipes)):
            source_id = incoming_sources[index]
            if source_id not in previous_sources:
                source_id = ""
            matched = previous_by_id.get(str(item.get("id"))) \
                if previous else None
            if matched is not None:
                old_index, old_item = matched
                source_id = self._valid_uuid(
                    old_item.get("source_element_id")) \
                    or self._legacy_source_id(name, old_item, old_index)
            if not source_id:
                source_id = str(uuid.uuid4())
            item["source_element_id"] = source_id
        if master_size is None:
            origin_x = min(d.get("x", 0) for d in shapes)
            origin_y = min(d.get("y", 0) for d in shapes)
        else:
            origin_x = origin_y = 0.0
        for d in shapes:
            d["x"] = d.get("x", 0) - origin_x
            d["y"] = d.get("y", 0) - origin_y
            d.pop("group", None)
        content_w = max(d["x"] + d.get("w", 0) for d in shapes)
        content_h = max(d["y"] + d.get("h", 0) for d in shapes)
        if master_size is None:
            width, height = content_w, content_h
        else:
            width = max(1.0, float(master_size[0]))
            height = max(1.0, float(master_size[1]))
        entry = {
            "items": shapes + pipes,
            "w": width,
            "h": height,
            "folder": (previous.get("folder", "My PVMs")
                       if previous and folder == "My PVMs"
                       else folder or "My PVMs"),
            "definition_kind": definition_kind
            or previous.get("definition_kind", "pvm"),
            "definition_id": (
                self._valid_uuid(previous.get("definition_id"))
                or (self._legacy_definition_id(name)
                    if previous else str(uuid.uuid4()))),
            "definition_revision": previous_revision + 1,
            "schema_version": self.SCHEMA_VERSION,
        }
        pair = paired_faceplate if paired_faceplate is not None \
            else previous.get("paired_faceplate", "")
        if pair:
            entry["paired_faceplate"] = pair
        self.entries[name] = entry
        self.save()
        return entry

    def move(self, name: str, folder: str) -> bool:
        self.reload()
        entry = self.entries.get(name)
        if entry is None or not folder:
            return False
        self._assert_editable(name)
        entry["folder"] = folder
        self.save()
        return True

    def add_nested(self, parent: str, child: str, *, x=0.0, y=0.0,
                   choices=None, link="linked") -> bool:
        """Place a class reference inside another class at arbitrary depth."""
        self.reload()
        entry = self.entries.get(parent)
        if entry is None or child not in self.entries or parent == child:
            return False
        self._assert_editable(parent)
        entry["items"].append({
            "kind": "nested_pvm", "class": child,
            "x": float(x), "y": float(y), "choices": dict(choices or {}),
            "link": link,
            "source_element_id": str(uuid.uuid4()),
        })
        self._upgrade_entry_metadata(parent, entry, increment=True)
        self.save()
        return True

    def create(self, name: str) -> dict | None:
        """A fresh composite class, authored on the canvas rather
        than converted from a selection. Seeded with one starter
        rectangle so the class exists to be laid out — the author
        deletes it once real shapes are in."""
        if not name or name in self.entries:
            return None
        return self.add(name, [{"kind": "rect", "id": "shp_seed",
                                "x": 0.0, "y": 0.0,
                                "w": 80.0, "h": 80.0}],
                        definition_kind="pvm")

    def create_faceplate_blueprint(self, name: str) -> dict | None:
        """Create a measured, live-data faceplate body for Studio.

        It is deliberately a user PVM, not a second faceplate runtime.  The
        Configurator supplies its typed ``Pvm.*`` inputs and Studio remains
        the sole drawing surface.  Every visible control in the scaffold has
        a real data or navigation contract; no ornamental icon hotspots are
        seeded.
        """
        self.reload()
        if not name or name in self.entries:
            return None
        # Composed from the section catalogue so there is one
        # definition of what a PV bar or a trend is, shared with
        # Insert Section. The output is item-for-item what this
        # method emitted when the scaffold was written out here.
        from .faceplate_sections import blueprint_items
        items = blueprint_items()
        return self.add(name, items, folder="My Faceplates",
                        definition_kind="faceplate")

    def create_pvm_blueprint(self, name: str,
                             faceplate_name: str) -> dict | None:
        """Create the compact half of a user-authored PVM/FP pair.

        The double-click contract is stored on the class metadata and a
        visible mini-faceplate button provides the explicit single-click
        route.  Both carry the instance's typed choices into the faceplate.
        """
        self.reload()
        if not name or name in self.entries \
                or faceplate_name not in self.entries:
            return None
        items = [
            {"kind": "rect", "id": "pvm_surface", "x": 0, "y": 0,
             "w": 132, "h": 64, "fill": "#E2E6EF", "line": "#B5BBC6",
             "width": 1, "locked": True},
            {"kind": "text", "id": "pvm_tag", "x": 6, "y": 3,
             "w": 104, "h": 14, "text": "Pvm.ModuleName",
             "font_size": 8, "font_bold": True},
            {"kind": "text", "id": "pvm_pv_label", "x": 6, "y": 20,
             "w": 20, "h": 18, "text": "PV", "font_size": 8},
            {"kind": "datalink", "id": "pvm_pv", "x": 27, "y": 18,
             "w": 58, "h": 20, "path": "Pvm.PVPath",
             "datalink_type": "numeric", "decimals": 2, "units": True},
            {"kind": "text", "id": "pvm_out_label", "x": 88, "y": 20,
             "w": 26, "h": 18, "text": "OUT", "font_size": 8},
            {"kind": "datalink", "id": "pvm_out", "x": 88, "y": 34,
             "w": 40, "h": 16, "path": "Pvm.OUTPath",
             "datalink_type": "numeric", "decimals": 1},
            {"kind": "rect", "id": "pvm_out_track", "x": 6, "y": 50,
             "w": 122, "h": 7, "fill": "#D0D2D4", "line": "#A7AAAD",
             "width": 1},
            {"kind": "rect", "id": "pvm_out_bar", "x": 7, "y": 51,
             "w": 120, "h": 5, "fill": "#178A91", "line": "#178A91",
             "fill_pct": 50, "fill_direction": "left",
             "props": {"fill_pct": {
                 "kind": "animation", "path": "Pvm.OUTPath",
                 "type": "number", "input_start": 0, "input_end": 100,
                 "output_start": 0, "output_end": 100}}},
            {"kind": "icon_button", "id": "pvm_open_fp",
             "x": 111, "y": 2, "w": 17, "h": 17,
             "icon": "mini_faceplate", "tooltip": "Open faceplate",
             "actions": [{"event": "click",
                          "kind": "open_user_faceplate",
                          "target": faceplate_name}]},
        ]
        return self.add(name, items, folder="My PVMs",
                        definition_kind="pvm",
                        paired_faceplate=faceplate_name)

    def pair_faceplate(self, pvm_name: str, faceplate_name: str) -> bool:
        """Associate a compact user PVM with a reusable faceplate class."""
        self.reload()
        pvm = self.entries.get(pvm_name)
        faceplate = self.entries.get(faceplate_name)
        if pvm is None or faceplate is None:
            return False
        if pvm.get("definition_kind", "pvm") != "pvm" \
                or faceplate.get("definition_kind", "pvm") != "faceplate":
            return False
        self._assert_editable(pvm_name)
        pvm["paired_faceplate"] = faceplate_name
        self._upgrade_entry_metadata(pvm_name, pvm, increment=True)
        self.save()
        return True

    def remove(self, name: str) -> bool:
        self.reload()
        if name in self.entries:
            self._assert_editable(name)
            del self.entries[name]
            self.save()
            return True
        return False

    #: Shape properties a class may bind to ``Pvm.<Prop>[.<Col>]``.
    #: `present` is the shapes' Present Online: resolved falsy, the
    #: shape is never instantiated — not hidden, absent.
    SHAPE_BINDABLE = (
        "fill", "line", "width", "fill_pct", "fill_direction", "rot",
        "opacity",
        "visible", "text", "text_color", "font_family", "font_size",
        "font_bold", "font_italic", "font_underline", "present",
    )
    BINDABLE = (*SHAPE_BINDABLE, "path", "target", "path_prefix",
                "icon", "tooltip", "enabled", "rows_path", "series_path", "command_context")

    def set_shape_binding(self, name: str, index: int, key: str,
                          reference: str) -> bool:
        """Author a shape binding on the CLASS: item `index`'s `key`
        becomes a ``Pvm.…`` reference (empty clears it)."""
        self.reload()
        entry = self.entries.get(name)
        if entry is None or key not in self.BINDABLE:
            return False
        self._assert_editable(name)
        shapes = [d for d in entry["items"]
                  if d.get("kind") != "pipe"]
        if not (0 <= index < len(shapes)):
            return False
        if reference:
            shapes[index][key] = reference
        else:
            shapes[index].pop(key, None)
        self._upgrade_entry_metadata(name, entry, increment=True)
        self.save()
        return True

    @staticmethod
    def _resolve(value, config, choices, standards):
        """One stored value: ``Pvm.X[.Y]`` resolves through the
        class's configuration document (then through a Standard
        reference); anything else passes through."""
        if not isinstance(value, str) or config is None:
            return value
        resolved = value
        # Indirect bindings are authored as readable templates such as
        # ``{ControlTag}/PV``.  Resolve them when a class instance is
        # expanded, where the typed instance choices are finally known.
        # Existing documents without braces follow the exact old path.
        if "{" in resolved and "}" in resolved:
            from ..binding.engine import format_template
            try:
                resolved = format_template(
                    resolved, config.binding_params(choices or {}))
            except (KeyError, TypeError, ValueError):
                # Preserve the unresolved template so verification/runtime
                # report the bad source instead of silently inventing one.
                pass
        if value.startswith(("Pvm.", "Pvm.")):
            root, column = config.split_property_path(value[4:])
            target = config.property(root)
            if target is not None and (not column
                                       or column in target.columns):
                resolved = config.subvalue(value[4:], choices or {})
        # Labels may embed a class property, for example
        # ``Unit: Pvm.UnitName``. Longest-first preserves dotted names.
        paths = []
        for prop in config.all_properties():
            paths.append(prop.name)
            paths.extend(f"{prop.name}.{column}" for column in prop.columns)
        for path in sorted(paths, key=len, reverse=True):
            for prefix in PVM_SCOPE_NAMES:
                token = f"{prefix}.{path}"
                if token in str(resolved):
                    resolved = str(resolved).replace(
                        token, str(config.subvalue(path, choices or {})))
        try:
            from .configurator.model import (
                is_standard_ref, standard_name,
            )
            if is_standard_ref(resolved) and standards is not None:
                looked = standards(standard_name(resolved))
                if looked is not None:
                    resolved = looked
        except Exception:                           # noqa: BLE001
            log.warning("Unable to resolve standard reference %r", resolved,
                        exc_info=True)
        return resolved

    @staticmethod
    def _detached_snapshot(items: list[dict]) -> list[dict]:
        """Persist expanded child ink in root-relative coordinates."""
        snapshot = []
        for item in items:
            row = copy.deepcopy(item)
            row.pop("instance_detached_nested", None)
            if row.get("kind") != "pipe":
                row["x"] = float(row.get("source_x", row.get("x", 0)))
                row["y"] = float(row.get("source_y", row.get("y", 0)))
            snapshot.append(row)
        return snapshot

    def _clone_detached_snapshot(self, snapshot: list[dict],
                                 root: dict) -> list[dict]:
        """Materialize frozen child ink with fresh transient scene ids."""
        id_map = {}
        cloned = []
        deferred_pipes = []
        for stored in snapshot:
            row = copy.deepcopy(stored)
            if row.get("kind") == "pipe":
                deferred_pipes.append(row)
                continue
            old_id = row.get("id")
            row["id"] = f"itm_{uuid.uuid4()}"
            id_map[old_id] = row["id"]
            row["x"] = float(row.get("source_x", row.get("x", 0))) \
                + float(root["current_x"])
            row["y"] = float(row.get("source_y", row.get("y", 0))) \
                + float(root["current_y"])
            row.update({
                "group": root["group"],
                "instance_id": root["instance_id"],
                "instance_definition": root["name"],
                "instance_link": root["link"],
                "instance_choices": dict(root["choices"]),
                "instance_overrides": dict(root["overrides"]),
                "instance_origin_x": float(root["original_x"]),
                "instance_origin_y": float(root["original_y"]),
                "definition_id": root["definition_id"],
                "definition_revision": int(root["definition_revision"]),
            })
            source_id = self._valid_uuid(row.get("source_element_id"))
            for prop in ("fill", "line", "rot", "visible", "text"):
                key = self.override_key(source_id, prop)
                if source_id and key in root["overrides"]:
                    row[prop] = root["overrides"][key]
            cloned.append(row)
        for row in deferred_pipes:
            row["id"] = f"itm_{uuid.uuid4()}"
            row["a"] = id_map.get(row.get("a"), row.get("a"))
            row["b"] = id_map.get(row.get("b"), row.get("b"))
            row.update({
                "group": root["group"],
                "instance_id": root["instance_id"],
                "instance_definition": root["name"],
                "instance_link": root["link"],
                "instance_choices": dict(root["choices"]),
                "instance_overrides": dict(root["overrides"]),
                "instance_origin_x": float(root["original_x"]),
                "instance_origin_y": float(root["original_y"]),
                "definition_id": root["definition_id"],
                "definition_revision": int(root["definition_revision"]),
                "instance_internal": True,
            })
            cloned.append(row)
        return cloned

    def instantiate(self, name: str, x: float, y: float,
                    config=None, choices: dict | None = None,
                    standards=None, link: str = "linked",
                    overrides: dict | None = None,
                    unlink_nested: bool = False, *, instance_id: str = "",
                    instance_origin: tuple[float, float] | None = None,
                    group_id: str = "", _chain=(), _root=None,
                    _source_prefix: str = "",
                    detached_nested: dict | None = None) -> list:
        """Fresh item dicts for one placement: new ids, translated,
        grouped, pipes re-pointed — and every ``Pvm.…`` shape binding
        resolved through the class's configuration document. A shape
        whose `present` resolves falsy is NOT instantiated (the
        shapes' Present Online), and pipes touching it come out too.

        Reads fresh: a class authored in another tab (or in the
        configurator) must be placeable here without reopening the
        display that caches this library.
        """
        self.reload()
        entry = self.entries.get(name)
        if entry is None or name in _chain or len(_chain) >= 16:
            return []
        definition_id = self._valid_uuid(entry.get("definition_id")) \
            or self._legacy_definition_id(name)
        try:
            definition_revision = max(
                0, int(entry.get("definition_revision", 0)))
        except (TypeError, ValueError):
            definition_revision = 0
        stable_instance_id = self._valid_uuid(instance_id) \
            or str(uuid.uuid4())
        group = group_id or f"ug_{stable_instance_id}"
        original_origin = instance_origin or (float(x), float(y))
        normalized_overrides = self.normalize_overrides(
            name, overrides, entry=entry)
        if _root is None:
            _root = {
                "name": name,
                "definition_id": definition_id,
                "definition_revision": definition_revision,
                "instance_id": stable_instance_id,
                "group": group,
                "link": link,
                "choices": dict(choices or {}),
                "overrides": normalized_overrides,
                "original_x": float(original_origin[0]),
                "original_y": float(original_origin[1]),
                "current_x": float(x),
                "current_y": float(y),
                "detached_nested": copy.deepcopy(detached_nested or {}),
            }
        else:
            stable_instance_id = _root["instance_id"]
            group = _root["group"]
        paired_faceplate = str(entry.get("paired_faceplate", ""))
        id_map: dict = {}
        dropped: set = set()
        out = []
        shape_index = 0
        for stored_index, stored in enumerate(entry["items"]):
            data = copy.deepcopy(stored)
            stored_source_id = self._valid_uuid(
                stored.get("source_element_id")) \
                or self._legacy_source_id(name, stored, stored_index)
            if _source_prefix:
                stored_source_id = str(uuid.uuid5(
                    uuid.UUID(_root["definition_id"]),
                    f"{_source_prefix}:{stored_source_id}"))
            if data.get("kind") == "pipe":
                data["source_element_id"] = stored_source_id
                out.append(data)
                continue
            if data.get("kind") == "nested_pvm":
                nested_link = "unlinked" if unlink_nested \
                    else data.get("link", link)
                child_name = str(data.get("class", ""))
                snapshot = _root["detached_nested"].get(stored_source_id) \
                    if unlink_nested else None
                if snapshot is not None:
                    nested = self._clone_detached_snapshot(snapshot, _root)
                else:
                    nested = self.instantiate(
                        child_name,
                        x + float(data.get("x", 0)),
                        y + float(data.get("y", 0)),
                        config=self._configuration_for(child_name),
                        choices=dict(data.get("choices", {})),
                        standards=standards, link=nested_link,
                        overrides=normalized_overrides,
                        unlink_nested=unlink_nested,
                        instance_id=stable_instance_id,
                        instance_origin=original_origin, group_id=group,
                        _chain=(*_chain, name), _root=_root,
                        _source_prefix=(
                            f"{_source_prefix}/{stored_source_id}"
                            if _source_prefix else stored_source_id))
                    if unlink_nested:
                        _root["detached_nested"][stored_source_id] = \
                            self._detached_snapshot(nested)
                for child in nested:
                    if child.get("kind") != "pipe":
                        child["group"] = group
                        child["pvm_chain"] = [name, *child.get(
                            "pvm_chain", [data.get("class", "")])]
                    out.append(child)
                continue
            index = shape_index
            shape_index += 1
            present = self._resolve(data.pop("present", None),
                                    config, choices, standards)
            if present is not None and str(present).strip().lower() \
                    in ("false", "0", "", "no", "off"):
                dropped.add(data.get("id"))
                continue
            for key in self.BINDABLE:
                if key in data:
                    data[key] = self._resolve(data[key], config,
                                              choices, standards)
            # Descriptor fields may themselves be class inputs.  A PVM
            # property descriptor becomes a static class value here; a live
            # animation keeps its descriptor after its path/range resolves.
            props = {}
            for prop_name, original in dict(data.get("props") or {}).items():
                spec = copy.deepcopy(original)
                for field, value in tuple(spec.items()):
                    if field == "refs" and isinstance(value, dict):
                        spec[field] = {
                            alias: self._resolve(
                                source, config, choices, standards)
                            for alias, source in value.items()
                        }
                    else:
                        spec[field] = self._resolve(
                            value, config, choices, standards)
                if spec.get("kind") == "pvm":
                    data[prop_name] = spec.get("ref")
                else:
                    props[prop_name] = spec
            if props:
                data["props"] = props
            else:
                data.pop("props", None)
            entry_data = data.get("entry")
            if isinstance(entry_data, dict):
                for field in ("path", "lo", "hi", "value"):
                    if field in entry_data:
                        entry_data[field] = self._resolve(
                            entry_data[field], config, choices, standards)
            for pen in data.get("pens", ()):
                if isinstance(pen, dict) and "path" in pen:
                    pen["path"] = self._resolve(
                        pen["path"], config, choices, standards)
            # A hosted faceplate section binds by {key: path}. Without
            # this the section would resolve in a plain display and be
            # permanently unbound inside an authored class, which is the
            # case it exists for.
            section_paths = data.get("paths")
            if isinstance(section_paths, dict):
                data["paths"] = {
                    key: self._resolve(value, config, choices, standards)
                    for key, value in section_paths.items()}
            for row in data.get("rows", ()):
                if not isinstance(row, dict):
                    continue
                for cell in row.values():
                    if isinstance(cell, dict) and "path" in cell:
                        cell["path"] = self._resolve(
                            cell["path"], config, choices, standards)
            for action in data.get("actions", ()):
                if not isinstance(action, dict):
                    continue
                fields = ("target", "value", "source") if action.get("kind") == "procedure_command" else ("target", "value")
                for field in fields:
                    if field in action:
                        action[field] = self._resolve(
                            action[field], config, choices, standards)
            for key in ("lo", "hi"):
                if key in data:
                    data[key] = self._resolve(
                        data[key], config, choices, standards)
            for key in ("width", "fill_pct", "rot", "opacity",
                        "font_size"):
                if key not in data:
                    continue
                try:
                    data[key] = float(data[key])
                except (TypeError, ValueError):
                    data.pop(key)
            for key in ("visible", "font_bold", "font_italic",
                        "font_underline", "enabled"):
                if key in data and isinstance(data[key], str):
                    data[key] = data[key].strip().lower() not in (
                        "false", "0", "", "no", "off")
            new_id = f"itm_{uuid.uuid4()}"
            id_map[data.get("id")] = new_id
            data["id"] = new_id
            local_x = float(data.get("x", 0)) + float(x) \
                - float(_root["current_x"])
            local_y = float(data.get("y", 0)) + float(y) \
                - float(_root["current_y"])
            data["x"] = local_x + float(_root["current_x"])
            data["y"] = local_y + float(_root["current_y"])
            data["group"] = group
            nested_name = data.get("user_pvm")
            data["user_pvm"] = name
            data["pvm_index"] = index
            data["pvm_link"] = link
            data.update({
                "instance_id": stable_instance_id,
                "instance_definition": _root["name"],
                "instance_link": _root["link"],
                "instance_choices": dict(_root["choices"]),
                "instance_overrides": dict(_root["overrides"]),
                "instance_origin_x": float(_root["original_x"]),
                "instance_origin_y": float(_root["original_y"]),
                "definition_id": _root["definition_id"],
                "definition_revision": int(
                    _root["definition_revision"]),
                "source_element_id": stored_source_id,
                "source_x": local_x,
                "source_y": local_y,
            })
            if normalized_overrides:
                data["pvm_overrides"] = dict(normalized_overrides)
                for key in ("fill", "line", "rot", "visible", "text"):
                    override = normalized_overrides.get(
                        self.override_key(stored_source_id, key))
                    if override is not None:
                        data[key] = override
            if nested_name and nested_name != name:
                data["nested_pvm"] = nested_name
            if unlink_nested and nested_name and nested_name != name:
                data["pvm_link"] = "unlinked"
            data["pvm_choices"] = dict(choices or {})
            data["pvm_overrides"] = dict(normalized_overrides)
            if paired_faceplate:
                actions = list(data.get("actions", ()))
                if not any(action.get("event") == "double_click"
                           and action.get("kind") == "open_user_faceplate"
                           for action in actions if isinstance(action, dict)):
                    actions.append({
                        "event": "double_click",
                        "kind": "open_user_faceplate",
                        "target": paired_faceplate,
                    })
                data["actions"] = actions
            out.append(data)
        kept = []
        for data in out:
            if data.get("kind") == "pipe":
                if data.get("a") in dropped \
                        or data.get("b") in dropped:
                    continue
                data = dict(data)
                data["id"] = f"itm_{uuid.uuid4()}"
                data["a"] = id_map.get(data.get("a"), data.get("a"))
                data["b"] = id_map.get(data.get("b"), data.get("b"))
                data.update({
                    "group": group,
                    "user_pvm": name,
                    "pvm_link": link,
                    "pvm_choices": dict(choices or {}),
                    "pvm_overrides": dict(normalized_overrides),
                    "instance_id": stable_instance_id,
                    "instance_definition": _root["name"],
                    "instance_link": _root["link"],
                    "instance_choices": dict(_root["choices"]),
                    "instance_overrides": dict(_root["overrides"]),
                    "instance_origin_x": float(_root["original_x"]),
                    "instance_origin_y": float(_root["original_y"]),
                    "definition_id": _root["definition_id"],
                    "definition_revision": int(
                        _root["definition_revision"]),
                    "instance_internal": True,
                })
            kept.append(data)
        detached = copy.deepcopy(_root["detached_nested"])
        for data in kept:
            data["instance_detached_nested"] = detached
        return kept
