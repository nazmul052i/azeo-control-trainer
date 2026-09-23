"""The one place a PvmDisplay becomes pictures.

The studio fills its editing canvas through this, and `viewer.py` fills
the operator's through the same calls. That shared path is what makes
the Assembler **WYSIWYG by construction** rather than by inspection: a
display cannot look one way to the engineer and another to the operator
because there is no second painter to disagree with the first.

This is the same rule D8 states for promoted shared assets — never grow a
parallel renderer — applied one level down.

What it does NOT do is anything about *editing*: no undo, no lock, no
selection. The studio adds those on top; the viewer adds nothing.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import configuration_document_path

import uuid
from dataclasses import replace
from pathlib import Path

from azeo_control_trainer.core.hmi.pvms.base import Pvm, registry
from azeo_control_trainer.core.hmi.pvms.elements import (
    ALARM_LIST, CHART, MULTI_POINT, RADAR_PLOT, TABLE, UserEntry,
    element_paths, validate_data_element, validate_datalink,
)
from .items import PVM_H, PVM_W, PvmItem, PipeItem, StaticItem


def pvm_from_dict(data: dict) -> Pvm:
    """A stored placement -> the frozen record the renderer wants.

    Shared by the studio's `_restore` and the operator view: two
    readings of one stored format is two chances to read it
    differently.
    """
    block_type, _, role = str(data.get("class", "/")).partition("/")
    return Pvm(id=data.get("id", f"pvm_{uuid.uuid4().hex[:4]}"),
               pvm_class="", block_type=block_type, role=role,
               params=data.get("params", {}),
               variant=data.get("variant", ""),
               label=data.get("label", ""),
               standard=data.get("standard", ""),
               fill=data.get("fill", ""),
               line=data.get("line", ""),
               x=data.get("x", 0), y=data.get("y", 0),
               w=data.get("w", PVM_W), h=data.get("h", PVM_H),
               rot=float(data.get("rot", 0) or 0),
               z=float(data.get("z", 0) or 0),
               group=str(data.get("group", "") or ""),
               layer=data.get("layer", "pvms"),
               visible=bool(data.get("visible", True)),
               locked=bool(data.get("locked", False)),
               choices=dict(data.get("choices", {})),
               class_revision=str(data.get("class_revision", "")),
               symbol=str(data.get("symbol", "") or ""))

#: The inline card's rows, bound from the class's own declarations
#: where it has them. Shared so a card carries the same rows in both
#: places — a row missing at runtime is a display that lied.
ROW_KEYS = (("PV", "pv.value"), ("SP", "sp.value"),
            # The working setpoint, for the HP combination bar. Absent
            # on classes whose block has no ramp — which is the honest
            # outcome, and draws no second tick.
            ("SP_WRK", "sp_wrk.value"),
            ("OUT", "out.value"), ("KIND", "kind"),
            ("RUNNING", "state.running"),
            ("STATE", "state.name"), ("FAIL", "state.fail"),
            ("TRIP", "state.tripped"),
            # Feeds the HP status icon; absent on classes whose block
            # has no simulation flag, which is the honest outcome.
            ("SIMULATE", "simulate"),
            # The DC/EDC abnormal-status conditions (PVMS+FB.pdf p14).
            # Absent on classes that do not bind them, which leaves the
            # matching status icon dark rather than guessed.
            ("COND_STATE", "cond.state"),
            ("COND_PERMISSIVE", "cond.permissive"),
            ("COND_INTERLOCK", "cond.interlock"),
            ("COND_TRACKING", "cond.tracking"),
            ("COND_BYPASSED", "cond.bypassed"),
            ("READBACK", "readback.value"),
            ("LO", "limits.lo"), ("HI", "limits.hi"),
            ("LL", "limits.lo_lo"), ("HH", "limits.hi_hi"))


class DisplayRenderer:
    """Builds canvas items for one display, against one theme."""

    def __init__(self, engine, palette, config_root=None,
                 action_handler=None, write_handler=None,
                 write_checker=None, interaction_handler=None,
                 alarm_provider=None, symbol_aliases=None):
        self.engine = engine
        self.palette = palette
        self._config_root = config_root
        self._action_handler = action_handler
        self._write_handler = write_handler or engine.write
        self._write_checker = write_checker or engine.can_write
        self._interaction_handler = interaction_handler
        self._alarm_provider = alarm_provider
        self._symbol_aliases = dict(symbol_aliases or {})
        self._config_cache: dict = {}
        #: Placements whose PVM class this build does not register.
        #: They draw unbound; `PvmStudio.validate()` names them.
        self.unregistered: set = set()

    # ------------------------------------------------- class lookup
    @staticmethod
    def class_bindings(pvm_cls) -> tuple:
        """A class's binding specs, or none when this build registers
        no class for the placement."""
        return tuple(getattr(pvm_cls, "bindings", ()) or ())

    def primary_template(self, pvm_cls) -> tuple[str, str]:
        specs = self.class_bindings(pvm_cls)
        value_spec = next((s for s in specs
                           if not s.prop and not s.expr), None)
        mode_spec = next((s for s in specs if s.key == "mode"), None)
        return (value_spec.path if value_spec else "",
                mode_spec.path if mode_spec else "")

    def pvm_config(self, pvm_cls, revision=""):
        if pvm_cls is None:
            return None
        name = getattr(pvm_cls, "__name__", "")
        config = self.pvm_config_named(name, revision)
        from azeo_control_trainer.core.hmi.pvms.typography import ensure_typography, typography_configuration
        return ensure_typography(config) if config is not None \
            else typography_configuration(name)

    def pvm_config_named(self, name: str, revision=""):
        """The class's configuration document, if an Author wrote one
        (`_pvmcfg/<ClassName>.pvmcfg.json` beside the displays).
        Cached; None means plain `{path}` binding."""
        key = (name, revision) if revision else name
        if key in self._config_cache:
            return self._config_cache[key]
        from ..class_revisions import revision_root
        root = revision_root(self._config_root, revision)
        config = None
        try:
            from ..configurator.model import PvmConfiguration
            path = configuration_document_path(Path(root), name)
            if path.exists():
                loaded = PvmConfiguration.load(path)
                if loaded.all_properties():
                    config = loaded
        except Exception:                           # noqa: BLE001
            config = None
        self._config_cache[key] = config
        return config

    def forget_config(self, name: str) -> None:
        """Drop one class's cached document.

        User PVM classes are authored live in the configurator, so
        their document can change while the studio is open — the cache
        that makes the poll path cheap has to be told.
        """
        self._config_cache.pop(name, None)

    # ------------------------------------------------------ building
    @staticmethod
    def ordered_content(display) -> list[tuple[bool, dict]]:
        """Yield PVMs and drawings in their shared back-to-front order.

        Construct in order instead of calling stackBefore: PySide transfers
        item ownership on that call and can destroy scene items on return.
        """
        content = [(True, pvm.to_dict() if isinstance(pvm, Pvm) else pvm)
                   for pvm in display.pvms]
        content.extend((False, item) for item in display.items)
        if display.stacking_order:
            ranks = {identity: index
                     for index, identity in enumerate(display.stacking_order)}
            content.sort(key=lambda pair: ranks.get(pair[1].get("id"), len(ranks)))
        return content

    def build_pvm(self, pvm: Pvm) -> PvmItem:
        """One placement, bound and ready to paint."""
        if pvm.symbol in self._symbol_aliases:
            pvm = replace(pvm, symbol=self._symbol_aliases[pvm.symbol])
        pvm_cls = registry.get(pvm.block_type, pvm.role, pvm.variant) \
            or registry.get(pvm.block_type, pvm.role) \
            or registry.get(pvm.block_type, "dynamo_compact")
        if pvm_cls is None:
            # A display naming a class this build does not register
            # must still OPEN. It draws unbound and validate() names
            # it; an AttributeError here told the engineer nothing and
            # lost every other placement on the display with it.
            self.unregistered.add(f"{pvm.block_type}/{pvm.role}")
        # Configuration-aware binding: templates may reference the
        # class's configuration properties ({Tag}/{Link.FB}/…), and a
        # spec gated off by Presence / Present Online is never bound.
        specs = self.class_bindings(pvm_cls)
        config = self.pvm_config(pvm_cls, pvm.class_revision)
        if config is not None:
            params = config.binding_params(pvm.choices,
                                           base=pvm.params)
            _active, gated = config.plan_bindings(
                specs, pvm.choices, pvm.params)
            gated_keys = {spec.key for spec in gated}
        else:
            params = pvm.params
            gated_keys = set()
        value_template, mode_template = self.primary_template(pvm_cls)
        primary_spec = next((s for s in specs
                             if not s.prop and not s.expr), None)
        binding = self.engine.bind(value_template, params) \
            if value_template and (primary_spec is None
                                   or primary_spec.key
                                   not in gated_keys) else None
        mode_binding = self.engine.bind(mode_template, params) \
            if mode_template and "mode" not in gated_keys else None
        rows: dict = {}
        for label, key in ROW_KEYS:
            spec = next((s for s in specs
                         if s.key == key and not s.prop
                         and not s.expr), None)
            if spec is not None and spec.key not in gated_keys:
                rows[label] = self.engine.bind(spec.path, params)
        points = tuple(getattr(config, "connection_points", ()) or
                       getattr(pvm_cls, "CONNECTION_POINTS", ()) or ())
        from azeo_control_trainer.core.hmi.pvms.typography import resolve_typography
        item = PvmItem(
            pvm, binding, mode_binding, self.palette, rows=rows,
            connection_points=points,
            status_conditions=getattr(
                pvm_cls, "STATUS_BOX_CONDITIONS", ()) if pvm_cls else (),
            typography=resolve_typography(config, pvm.choices))
        item.alarm_provider = self._alarm_provider
        return item

    def build_drawing(self, data: dict):
        """One drawing item — a pipe or anything else."""
        symbol = str(data.get("symbol", "") or "")
        if symbol in self._symbol_aliases:
            data = dict(data)
            data["symbol"] = self._symbol_aliases[symbol]
        if data.get("kind") == "pipe":
            return PipeItem(data, self.palette)
        item = StaticItem(data, self.palette)
        return self.configure_drawing(item)

    def configure_drawing(self, item: StaticItem) -> StaticItem:
        """Attach the live part of a data element to its shared item."""
        self.unbind(item)
        item.binding_error = ""
        item.action_handler = None
        item.interaction_handler = self._interaction_handler
        item.alarm_provider = self._alarm_provider
        item.write_handler = None
        item.write_check = None
        item.write_allowed = False
        item.write_error = ""
        kind = item.data.get("kind")
        if kind == "datalink":
            dtype = item.data.get("datalink_type", "numeric")
            path = str(item.data.get("path", "") or "").strip()
            item.binding_error = validate_datalink(dtype, path)
            if not item.binding_error:
                item.binding = self.engine.bind(path)
        elif kind == "display_link":
            item.action_handler = self._action_handler
        elif kind == "user_entry":
            entry = UserEntry.from_dict(item.data.get("entry", {}))
            item.binding_error = entry.validate()
            if not item.binding_error and entry.path:
                item.binding = self.engine.bind(entry.path)
                allowed = self._write_checker(entry.path)
                item.write_allowed = allowed.success
                item.write_error = allowed.error
            elif not item.binding_error:
                item.write_error = "no write target or action is configured"
            item.write_handler = self._write_handler
            item.write_check = self._write_checker
        elif kind in (CHART, MULTI_POINT, RADAR_PLOT, TABLE):
            item.binding_error = validate_data_element(item.data)
            if not item.binding_error:
                item.bindings = {
                    path: self.engine.bind(path)
                    for path in element_paths(item.data)}
        elif kind == ALARM_LIST:
            item.binding_error = validate_data_element(item.data)
        elif kind == "faceplate_section":
            # The placement carries {binding key: tag path}; the hosted
            # section is refreshed from these on paint.
            from ..faceplate_sections import LIVE_SECTIONS

            paths = item.data.get("paths") or {}
            if str(item.data.get("section", "")) not in LIVE_SECTIONS:
                item.binding_error = (
                    f"unknown faceplate section "
                    f"{item.data.get('section', '')!r}")
            elif not isinstance(paths, dict) or not paths:
                item.binding_error = "no binding paths are configured"
            else:
                item.bindings = {
                    str(path): self.engine.bind(str(path))
                    for path in paths.values() if str(path).strip()}
        else:
            item.binding_error = validate_data_element(item.data)
        context_path = item.data.get("command_context")
        if context_path:
            item.bindings[context_path] = self.engine.bind(context_path)
        for action in item.data.get("actions", ()):
            if action.get("kind") == "procedure_command" and action.get("source"):
                path = action["source"]
                if path not in item.bindings:
                    item.bindings[path] = self.engine.bind(path)
        return item

    @staticmethod
    def item_bindings(item):
        """Subscriptions owned by one placement, including compound rows."""
        bindings = [getattr(item, "binding", None),
                    *getattr(item, "bindings", {}).values()]
        if isinstance(item, PvmItem):
            bindings.extend((item.mode_binding, *item.rows.values()))
        return (binding for binding in bindings if binding is not None)

    @classmethod
    def binding_revision(cls, item):
        return tuple((id(binding), getattr(binding, "revision", 0))
                     for binding in cls.item_bindings(item))

    def unbind(self, item) -> None:
        """Release every subscription a rendered item holds."""
        for binding in self.item_bindings(item):
            self.engine.unbind(binding)
        if isinstance(item, StaticItem):
            item.binding = None
            item.bindings = {}
