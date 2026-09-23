"""The Qt-free PVM configuration property model.

Three mechanisms govern configuration and binding:

- **Selection properties.** One named choice drives many subproperty
  values — the grid columns. Orientation has four options and one
  BodyRot column holding 0°/180°/270°/90°; the class's rotation
  references ``Pvm.Orientation.BodyRot`` and the engineer never sees
  an angle. A subproperty value may itself reference another property
  (``Pvm.CustomAngle``), which is how the extended Orientation's
  Custom option defers to a number the engineer types.
- **Presence.** A property exists only while a referenced Boolean
  subproperty says so — Port is present only when
  ``ValveType.threeWay`` is true. Absent means absent: not greyed,
  not defaulted, simply not part of the instance.
- **Present Online.** A Boolean on property groups: when false the
  group is never loaded into runtime, so its parameters are never
  subscribed. That is how one class covers many cases without paying
  subscriptions for the unused ones — the binding engine asks
  :meth:`PvmConfiguration.online_references` and gets only what a
  given set of choices actually needs.

Values are strings throughout, as the designer edits them. A value of
the exact form ``Pvm.<Property>`` or ``Pvm.<Property>.<Column>`` is a
reference and resolves recursively; anything else (including the DLSYS
scale expressions, which merely *contain* ``Pvm.`` text) passes
through literally — an expression is the runtime's business.
"""
from __future__ import annotations

from ...compatibility import (
    PVM_SCOPE_NAMES, PVM_SCOPE_PREFIXES, normalize_configuration_document,
)

import json
import math
import re
import string
from dataclasses import dataclass, field
from pathlib import Path

_formatter = string.Formatter()


def placeholders(template: str) -> set:
    """The `{field}` names a binding template references."""
    return {field for _lit, field, _s, _c in _formatter.parse(template)
            if field}

#: The Add Property menu, exactly as the figure groups it.
VALUE_TYPES = ("String", "Boolean", "Number", "Color", "Font", "Image",
               "Measurement", "Degree Angle", "Multi-language String",
               "Procedure Reference")
REFERENCE_TYPES = ("Control Tag", "Function Block Reference",
                   "Parameter Reference")
SELECTION_TYPE = "Selection"

#: A reusable class has a deliberately small public surface.  Internal
#: properties still participate in class bindings, but an engineer placing an
#: instance must never be asked to configure presentation helpers such as a
#: normalized PV or combined alarm state.
PUBLIC, INTERNAL = "public", "internal"
READ_ONLY, OPERATOR_WRITE = "read", "operator_write"

#: Presence conditions, the combo's three entries.
ALWAYS = "Always Present"
WHEN_TRUE = "Present when true"
WHEN_FALSE = "Present when false"

#: A property value of the form ``Standard.<Name>`` references a
#: library standard instead of carrying a literal. The reference is
#: what gets stored in the class; the VALUE resolves at render time
#: through the live standards store — which is the asymmetry the
#: paper documents: changing a class means republishing every
#: affected display, changing a standard publishes only itself.
STANDARD_PREFIX = "Standard."


def is_standard_ref(value) -> bool:
    return isinstance(value, str) and value.startswith(STANDARD_PREFIX)


def standard_name(value: str) -> str:
    return value[len(STANDARD_PREFIX):]


def resolve_standard_refs(values: dict, lookup) -> dict:
    """Late-resolve every ``Standard.<Name>`` in a resolved-values
    dict through `lookup` (a callable or mapping of name → value).
    An unknown standard keeps its reference text — honest, visible,
    and greppable — rather than becoming an invented literal."""
    out = {}
    for key, value in values.items():
        if is_standard_ref(value):
            name = standard_name(value)
            try:
                resolved = lookup(name) if callable(lookup) \
                    else lookup.get(name)
            except Exception:                       # noqa: BLE001
                resolved = None
            out[key] = value if resolved is None else resolved
        else:
            out[key] = value
    return out

_TRUE_WORDS = {"true", "1", "yes", "show", "on"}


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in _TRUE_WORDS


@dataclass
class Presence:
    """Which Boolean subproperty gates this property, and how."""

    property: str = ""              # "ValveType.threeWay", "" = none
    condition: str = ALWAYS

    def to_dict(self) -> dict:
        if self.condition == ALWAYS and not self.property:
            return {}
        return {"property": self.property, "condition": self.condition}


@dataclass
class Option:
    """One row of a Selection grid: a name plus one value per column."""

    name: str
    values: list = field(default_factory=list)


@dataclass
class PvmProperty:
    name: str
    ptype: str = "String"
    title: str = ""
    description: str = ""
    tooltip: str = ""
    default: str = ""
    presentation: str = "Combo box"          # Boolean only
    columns: list = field(default_factory=list)   # Selection only
    options: list = field(default_factory=list)   # list[Option]
    presence: Presence = field(default_factory=Presence)
    scope: str = PUBLIC
    required: bool = False
    drop_target: bool = False
    accepted_block_types: list = field(default_factory=list)
    direction: str = READ_ONLY

    def option(self, name: str) -> Option | None:
        return next((o for o in self.options if o.name == name), None)

    def to_dict(self) -> dict:
        out = {"name": self.name, "type": self.ptype}
        for key in ("title", "description", "tooltip", "default"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.ptype == "Boolean" and self.presentation != "Combo box":
            out["presentation"] = self.presentation
        if self.columns:
            out["columns"] = list(self.columns)
        if self.options:
            out["options"] = [{"name": o.name, "values": list(o.values)}
                              for o in self.options]
        if self.scope != PUBLIC:
            out["scope"] = self.scope
        if self.required:
            out["required"] = True
        if self.drop_target:
            out["drop_target"] = True
        if self.accepted_block_types:
            out["accepted_block_types"] = list(self.accepted_block_types)
        if self.direction != READ_ONLY:
            out["direction"] = self.direction
        presence = self.presence.to_dict()
        if presence:
            out["presence"] = presence
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "PvmProperty":
        presence = data.get("presence") or {}
        return cls(
            name=data["name"], ptype=data.get("type", "String"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            tooltip=data.get("tooltip", ""),
            default=data.get("default", ""),
            presentation=data.get("presentation", "Combo box"),
            columns=list(data.get("columns", ())),
            options=[Option(o["name"], list(o.get("values", ())))
                     for o in data.get("options", ())],
            presence=Presence(presence.get("property", ""),
                              presence.get("condition", ALWAYS)),
            scope=data.get("scope", PUBLIC),
            required=bool(data.get("required", False)),
            drop_target=bool(data.get("drop_target", False)),
            accepted_block_types=[
                str(value).strip().upper()
                for value in data.get("accepted_block_types", ())
                if str(value).strip()],
            direction=data.get("direction", READ_ONLY))


@dataclass
class PropertyGroup:
    name: str
    properties: list = field(default_factory=list)
    present_online: bool = True

    def to_dict(self) -> dict:
        out = {"name": self.name,
               "properties": [p.to_dict() for p in self.properties]}
        if not self.present_online:
            out["present_online"] = False
        return out


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ConfigurationIssue:
    """One actionable problem in a PVM configuration document."""

    severity: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}" if self.location \
            else self.message


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_ALL_TYPES = frozenset((*VALUE_TYPES, *REFERENCE_TYPES, SELECTION_TYPE))


@dataclass
class PvmConfiguration:
    """One class's configuration pane: groups of typed properties."""

    pvm_class: str
    groups: list = field(default_factory=list)
    #: Normalized class-owned connection points. Linked instances read
    #: these fresh with the rest of the class configuration.
    connection_points: list = field(default_factory=list)

    def add_connection_point(self, x: float, y: float,
                             name: str = "") -> str:
        x = max(0.0, min(1.0, float(x)))
        y = max(0.0, min(1.0, float(y)))
        used = {str(point.get("name", ""))
                for point in self.connection_points}
        if not name:
            index = 1
            while f"cp{index}" in used:
                index += 1
            name = f"cp{index}"
        if name in used:
            raise ConfigurationError(f"connection point {name!r} exists")
        self.connection_points.append({"name": name, "x": x, "y": y})
        return name

    def remove_connection_point(self, name: str) -> bool:
        before = len(self.connection_points)
        self.connection_points = [point for point in self.connection_points
                                  if point.get("name") != name]
        return len(self.connection_points) != before

    # -------------------------------------------------------- lookup
    def property(self, name: str) -> PvmProperty | None:
        for group in self.groups:
            for prop in group.properties:
                if prop.name == name:
                    return prop
        return None

    def group_of(self, name: str) -> PropertyGroup | None:
        for group in self.groups:
            if any(p.name == name for p in group.properties):
                return group
        return None

    def all_properties(self) -> list:
        return [p for g in self.groups for p in g.properties]

    def public_properties(self) -> list:
        """Properties an ordinary placement is allowed to configure."""
        return [prop for prop in self.all_properties()
                if prop.scope == PUBLIC]

    def internal_properties(self) -> list:
        """Class implementation values hidden from ordinary instances."""
        return [prop for prop in self.all_properties()
                if prop.scope == INTERNAL]

    def primary_drop_target(self) -> PvmProperty | None:
        """The one typed input that accepts a dragged control object.

        Invalid documents can temporarily contain more than one while an
        Author is editing.  Returning ``None`` keeps placement honest until
        validation repairs that ambiguity.
        """
        targets = [prop for prop in self.public_properties()
                   if prop.drop_target]
        return targets[0] if len(targets) == 1 else None

    def drop_target_for(self, block_type: str) -> PvmProperty | None:
        """Return the compatible primary target for *block_type*, if any."""
        target = self.primary_drop_target()
        if target is None:
            return None
        accepted = {str(value).strip().upper()
                    for value in target.accepted_block_types}
        requested = str(block_type).strip().upper()
        return target if requested and ("*" in accepted
                                        or requested in accepted) else None

    def split_property_path(self, path: str) -> tuple[str, str]:
        """Split a property/subproperty path without breaking dotted names.

        Configuration property names may contain dots.  Longest-match keeps
        ``pv.value.Units`` attached to property ``pv.value`` instead of
        accidentally resolving a non-existent property named ``pv``.
        """
        path = str(path)
        exact = self.property(path)
        if exact is not None:
            return path, ""
        names = sorted((prop.name for prop in self.all_properties()),
                       key=len, reverse=True)
        for name in names:
            prefix = name + "."
            if path.startswith(prefix):
                return name, path[len(prefix):]
        return path.partition(".")[0], path.partition(".")[2]

    def rename_group(self, old: str, new: str) -> bool:
        """Rename one group without permitting ambiguous tree entries."""
        new = str(new).strip()
        group = next((g for g in self.groups if g.name == old), None)
        if group is None or not new or any(
                g is not group and g.name == new for g in self.groups):
            return False
        group.name = new
        return True

    def rename_property(self, old: str, new: str) -> bool:
        """Rename a property and every exact ``Pvm.`` reference to it.

        A configurator that renames the tree row but leaves Presence and
        Selection cells pointing at the old name produces a class that looks
        valid until it is placed online. References therefore move with the
        property as one model operation.
        """
        new = str(new).strip()
        prop = self.property(old)
        if prop is None or not new or (new != old and self.property(new)):
            return False

        def rewrite(value):
            if not isinstance(value, str):
                return value
            for prefix in PVM_SCOPE_NAMES:
                exact = f"{prefix}.{old}"
                if value == exact or value.startswith(exact + "."):
                    return f"{prefix}.{new}{value[len(exact):]}"
            return value

        prop.name = new
        for candidate in self.all_properties():
            candidate.default = rewrite(candidate.default)
            gate = candidate.presence.property
            if gate == old or gate.startswith(old + "."):
                candidate.presence.property = new + gate[len(old):]
            for option in candidate.options:
                option.values = [rewrite(value) for value in option.values]
        return True

    def issues(self) -> tuple[ConfigurationIssue, ...]:
        """Validate structure, references and defaults before persistence.

        Runtime resolution is intentionally total, but authoring must be
        strict: silently saving duplicate names or a Selection with ragged
        rows creates an instance pane whose meaning depends on traversal
        order. All errors are returned together so an engineer can repair a
        class in one pass.
        """
        found: list[ConfigurationIssue] = []

        def error(location: str, message: str) -> None:
            found.append(ConfigurationIssue("error", location, message))

        if not self.pvm_class.strip():
            error("PVM class", "name is required")
        elif not _NAME.match(self.pvm_class):
            error("PVM class", "name is not a valid identifier")
        group_names: set[str] = set()
        property_names: set[str] = set()
        for group in self.groups:
            location = f"Group {group.name or '<unnamed>'}"
            if not group.name.strip():
                error(location, "name is required")
            elif not _NAME.match(group.name):
                error(location, "name is not a valid identifier")
            elif group.name in group_names:
                error(location, "group name is duplicated")
            group_names.add(group.name)
            for prop in group.properties:
                ploc = f"{location} / {prop.name or '<unnamed>'}"
                if not prop.name.strip():
                    error(ploc, "property name is required")
                elif not _NAME.match(prop.name):
                    error(ploc, "use letters, digits, '_', '-' or '.'; "
                          "the first character must be a letter or '_'")
                elif prop.name in property_names:
                    error(ploc, "property name is duplicated")
                property_names.add(prop.name)
                if prop.ptype not in _ALL_TYPES:
                    error(ploc, f"unknown property type {prop.ptype!r}")
                if prop.scope not in (PUBLIC, INTERNAL):
                    error(ploc, f"unknown interface scope {prop.scope!r}")
                if prop.direction not in (READ_ONLY, OPERATOR_WRITE):
                    error(ploc, f"unknown binding direction "
                          f"{prop.direction!r}")
                if prop.scope == INTERNAL and prop.required:
                    error(ploc, "an internal property cannot be required "
                          "from an instance")
                if prop.scope == INTERNAL and prop.direction != READ_ONLY:
                    error(ploc, "an internal property cannot expose an "
                          "operator write")
                if prop.drop_target:
                    if prop.scope != PUBLIC:
                        error(ploc, "the primary drop target must be public")
                    if prop.ptype not in REFERENCE_TYPES:
                        error(ploc, "the primary drop target must be a "
                              "Control Tag, Function Block Reference, or "
                              "Parameter Reference")
                    if prop.direction != READ_ONLY:
                        error(ploc, "the primary drop target must be "
                              "read-only")
                    if not prop.accepted_block_types:
                        error(ploc, "the primary drop target must declare "
                              "at least one accepted block type")
                    if not prop.required:
                        error(ploc, "the primary drop target must be "
                              "required")
                if prop.accepted_block_types:
                    normalized = [str(value).strip().upper()
                                  for value in prop.accepted_block_types]
                    if prop.ptype not in REFERENCE_TYPES:
                        error(ploc, "accepted block types apply only to "
                              "reference properties")
                    if not prop.drop_target:
                        error(ploc, "accepted block types apply only to the "
                              "primary drop target")
                    if any(not value for value in normalized) \
                            or len(set(normalized)) != len(normalized):
                        error(ploc, "accepted block types must be unique "
                              "and non-empty")
                if prop.presence.condition not in (
                        ALWAYS, WHEN_TRUE, WHEN_FALSE):
                    error(ploc, "presence condition is not supported")

                if prop.ptype == SELECTION_TYPE:
                    if not prop.columns:
                        error(ploc, "Selection needs at least one column")
                    if len(set(prop.columns)) != len(prop.columns) \
                            or any(not str(c).strip() for c in prop.columns):
                        error(ploc, "Selection column names must be unique "
                              "and non-empty")
                    option_names = [option.name for option in prop.options]
                    if not option_names:
                        error(ploc, "Selection needs at least one option")
                    if len(set(option_names)) != len(option_names) \
                            or any(not str(n).strip() for n in option_names):
                        error(ploc, "Selection option names must be unique "
                              "and non-empty")
                    if prop.default not in option_names:
                        error(ploc, "default must name an existing option")
                    for option in prop.options:
                        if len(option.values) != len(prop.columns):
                            error(f"{ploc} / {option.name}",
                                  "cell count does not match the columns")

        drop_targets = [prop for prop in self.all_properties()
                        if prop.drop_target]
        if len(drop_targets) > 1:
            error("PVM interface", "only one primary drop target is allowed")

        # References are checked after collecting all names, so forward
        # references are valid and the issue list is independent of order.
        for prop in self.all_properties():
            ploc = f"Property {prop.name or '<unnamed>'}"
            gate = prop.presence.property
            if gate:
                root, column = self.split_property_path(gate)
                target = self.property(root)
                if target is None:
                    error(ploc, f"Presence references missing {root!r}")
                elif column and column not in target.columns:
                    error(ploc, f"Presence references missing column "
                          f"{gate!r}")
            references = [prop.default]
            references.extend(value for option in prop.options
                              for value in option.values)
            for value in references:
                if not (isinstance(value, str)
                        and value.startswith(PVM_SCOPE_PREFIXES)):
                    continue
                path = value[4:]
                root, column = self.split_property_path(path)
                target = self.property(root)
                if target is None:
                    error(ploc, f"references missing property {root!r}")
                elif column and column not in target.columns:
                    error(ploc, f"references missing subproperty {path!r}")

        point_names: set[str] = set()
        for index, point in enumerate(self.connection_points, 1):
            location = f"Connection point {index}"
            name = str(point.get("name", "")).strip()
            if not name:
                error(location, "name is required")
            elif name in point_names:
                error(location, f"name {name!r} is duplicated")
            point_names.add(name)
            try:
                x, y = float(point["x"]), float(point["y"])
            except (KeyError, TypeError, ValueError):
                error(location, "x and y must be numbers")
                continue
            if not math.isfinite(x) or not math.isfinite(y) \
                    or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                error(location, "x and y must be normalized from 0 to 1")

        # Resolving every default catches reference cycles using the exact
        # resolver runtime uses, rather than maintaining a second graph.
        for prop in self.all_properties():
            try:
                self.value_of(prop.name, {})
                for column in prop.columns:
                    self.subvalue(f"{prop.name}.{column}", {})
            except ConfigurationError as exc:
                error(f"Property {prop.name}", str(exc))
        return tuple(found)

    def validate(self) -> None:
        issues = self.issues()
        if issues:
            raise ConfigurationError("\n".join(str(issue)
                                               for issue in issues))

    # ----------------------------------------------------- the rules
    def value_of(self, name: str, choices: dict,
                 _depth: int = 0) -> str:
        """A property's resolved value under `choices` — for a
        Selection that is its chosen option NAME; references chase."""
        if _depth > 8:
            raise ConfigurationError(
                f"reference cycle resolving {name}")
        prop = self.property(name)
        if prop is None:
            return ""
        raw = str(choices.get(name, prop.default))
        if prop.ptype == SELECTION_TYPE:
            return raw
        return self._deref(raw, choices, _depth)

    def subvalue(self, path: str, choices: dict,
                 _depth: int = 0) -> str:
        """``Prop.Column`` — the chosen option's cell, dereferenced."""
        name, column = self.split_property_path(path)
        if not column:
            return self.value_of(name, choices, _depth)
        prop = self.property(name)
        if prop is None or column not in prop.columns:
            return ""
        option = prop.option(self.value_of(name, choices, _depth))
        if option is None:
            return ""
        raw = str(option.values[prop.columns.index(column)])
        return self._deref(raw, choices, _depth)

    def _deref(self, raw: str, choices: dict, depth: int) -> str:
        """``Pvm.X`` / ``Pvm.X.Y`` exactly is a reference; an
        expression merely containing Pvm. is the runtime's."""
        if raw.startswith(PVM_SCOPE_PREFIXES) and "'" not in raw \
                and "[" not in raw and "+" not in raw:
            return self.subvalue(raw[4:], choices, depth + 1)
        return raw

    def is_present(self, name: str, choices: dict) -> bool:
        prop = self.property(name)
        if prop is None:
            return False
        presence = prop.presence
        if presence.condition == ALWAYS or not presence.property:
            return True
        gate = _truthy(self.subvalue(presence.property, choices))
        return gate if presence.condition == WHEN_TRUE else not gate

    def resolved(self, choices: dict) -> dict:
        """Every present property's value plus every Selection cell —
        what an instance actually configures to under `choices`."""
        out = {}
        for prop in self.all_properties():
            if not self.is_present(prop.name, choices):
                continue
            out[prop.name] = self.value_of(prop.name, choices)
            for column in prop.columns:
                out[f"{prop.name}.{column}"] = self.subvalue(
                    f"{prop.name}.{column}", choices)
        return out

    def subscribable(self, name: str, choices: dict) -> bool:
        """Would runtime load this property at all? Present Online
        gates the group, Presence gates the property — a false on
        either means never loaded, so never subscribed."""
        group = self.group_of(name)
        return group is not None and group.present_online \
            and self.is_present(name, choices)

    def binding_params(self, choices: dict | None = None,
                       base: dict | None = None) -> dict:
        """The format dict for property-composed binding templates:
        every subscribable property and Selection cell, over `base`
        (the placement's own params — `{path}` stays `{path}`)."""
        choices = choices or {}
        out = dict(base or {})
        for prop in self.all_properties():
            if not self.subscribable(prop.name, choices):
                continue
            if prop.name in out and prop.name not in choices:
                # The placement's own value (the base params — e.g.
                # `path`) IS the instance's value for a property of
                # the same name; a document default must never
                # overwrite it. An explicit choice still wins.
                continue
            out[prop.name] = self.value_of(prop.name, choices)
            for column in prop.columns:
                out[f"{prop.name}.{column}"] = self.subvalue(
                    f"{prop.name}.{column}", choices)
        return out

    def plan_bindings(self, specs, choices: dict | None = None,
                      base: dict | None = None) -> tuple:
        """Split Bind specs into (active, gated) under `choices`.

        A spec whose template references a property that Presence or
        Present Online has turned off is GATED — never bound, so never
        subscribed; that is the paper's performance lever. A
        placeholder that names nothing known stays active so the
        engine refuses it loudly at bind time: a typo is an error,
        a gate is not. Active entries are (spec, params) pairs ready
        for ``engine.bind(spec.path, params)``.
        """
        choices = choices or {}
        params = self.binding_params(choices, base)
        active, gated = [], []
        for spec in specs:
            template = getattr(spec, "path", "") or ""
            gate = False
            for name in placeholders(template):
                if name in params:
                    continue
                root = name.split(".", 1)[0]
                if self.property(root) is not None:
                    gate = True
                    break
            (gated if gate else active).append(
                spec if gate else (spec, params))
        return active, gated

    def online_references(self, choices: dict) -> list:
        """The reference-typed properties runtime would subscribe:
        Present Online gates the group, Presence gates the property.
        This is the performance lever — a hidden group costs nothing."""
        out = []
        for group in self.groups:
            if not group.present_online:
                continue
            for prop in group.properties:
                if prop.ptype in REFERENCE_TYPES \
                        and self.is_present(prop.name, choices):
                    out.append(prop.name)
        return out

    # -------------------------------------------------- persistence
    def to_dict(self) -> dict:
        out = {"pvm_class": self.pvm_class,
               "groups": [g.to_dict() for g in self.groups]}
        if self.connection_points:
            out["connection_points"] = [dict(point)
                                        for point in self.connection_points]
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "PvmConfiguration":
        data = normalize_configuration_document(data)
        return cls(
            pvm_class=data.get("pvm_class", ""),
            groups=[PropertyGroup(
                name=g["name"],
                properties=[PvmProperty.from_dict(p)
                            for p in g.get("properties", ())],
                present_online=g.get("present_online", True))
                for g in data.get("groups", ())],
            connection_points=[dict(point) for point in
                               data.get("connection_points", ())])

    def save(self, root: Path) -> Path:
        from azeo_control_trainer.core.configuration.package_paths import assert_mutable
        assert_mutable(root)
        self.validate()
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.pvm_class}.pvmcfg.json"
        text = json.dumps(self.to_dict(), indent=2,
                          ensure_ascii=False) + "\n"
        pending = path.with_suffix(path.suffix + ".tmp")
        pending.write_text(text, encoding="utf-8", newline="\n")
        pending.replace(path)
        return path

    @classmethod
    def load(cls, path: Path) -> "PvmConfiguration":
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8")))
