"""Graphics variables — one value, shared by many properties.

The manual's example is the whole reason they exist: you want every
element in a PVM to take the same animated colour. Without variables
you animate each element separately, five copies of one decision that
drift apart; or you write a script, which is control logic living in a
picture. A variable is the third answer, and it is the one Azeo Operator Station
built its no-scripting claim on.

Variables live on a Display, a Contextual display, a Layout, a Group,
a PVM class or an unlinked PVM — never on a *linked* PVM, because a
linked PVM tracks its class and a variable added to the instance would
be a silent fork of it. `VariableSet.SCOPES` names them and
`may_hold_variables()` enforces it.

A variable is typed, and its value is itself a property descriptor: a
literal, a standard, or an animation. That is what makes them
composable — one variable animates from a parameter and thirty
properties reference the variable.
"""
from __future__ import annotations

from ..compatibility import LEGACY_CLASS_SCOPE_LABELS

from dataclasses import dataclass, field

from .properties import PropertyResolver, Resolved, descriptor_for

#: The manual's variable types, exactly.
VARIABLE_TYPES = ("Color", "Font", "Boolean", "Image", "String",
                  "Localizable String", "Measurement", "Number")

#: Where a variable may be declared. A linked PVM is deliberately
#: absent: to add one you edit its PVM class.
SCOPES = ("Display", "Contextual display", "Layout", "Group",
          "PVM class", "Unlinked PVM")

#: Scope -> the expression prefix that reaches it (manual Table 1).
SCOPE_PREFIX = {"Display": "Dsp.", "Contextual display": "Dsp.",
                "Layout": "Lyt.", "Group": "Grp.",
                "PVM class": "Pvm.", "Unlinked PVM": "Pvm."}
# Existing callers and saved scope labels still resolve to the same variables.
SCOPE_PREFIX.update(LEGACY_CLASS_SCOPE_LABELS)

_DEFAULTS = {"Color": "#808080", "Font": "Segoe UI 12",
             "Boolean": False, "Image": "", "String": "",
             "Localizable String": "", "Measurement": 0.0,
             "Number": 0.0}


@dataclass
class Variable:
    """One declared value."""

    name: str
    vtype: str = "Number"
    #: The static value, used when no descriptor resolves.
    value: object = None
    #: Optional descriptor — a variable may itself be animated or
    #: reference a standard. This is what makes one variable able to
    #: drive thirty properties from one live parameter.
    source: dict | None = None

    def __post_init__(self) -> None:
        if self.vtype not in VARIABLE_TYPES:
            self.vtype = "Number"
        if self.value is None:
            self.value = _DEFAULTS.get(self.vtype, "")

    def to_dict(self) -> dict:
        out = {"name": self.name, "type": self.vtype,
               "value": self.value}
        if self.source:
            out["source"] = dict(self.source)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Variable":
        return cls(name=str(data.get("name", "")),
                   vtype=str(data.get("type", "Number")),
                   value=data.get("value"),
                   source=dict(data["source"])
                   if isinstance(data.get("source"), dict) else None)


@dataclass
class VariableSet:
    """The variables declared on one piece of graphics configuration."""

    scope: str = "Display"
    items: list = field(default_factory=list)

    SCOPES = SCOPES

    # ------------------------------------------------------- authoring
    def add(self, name: str, vtype: str = "Number",
            value: object = None, source: dict | None = None):
        """Declare one. Names are unique within a scope, as the manual
        requires of every library and configuration item."""
        if not name or self.get(name) is not None:
            return None
        variable = Variable(name, vtype, value, source)
        self.items.append(variable)
        return variable

    def get(self, name: str):
        return next((v for v in self.items if v.name == name), None)

    def remove(self, name: str) -> bool:
        variable = self.get(name)
        if variable is None:
            return False
        self.items.remove(variable)
        return True

    def rename(self, old: str, new: str) -> bool:
        if not new or self.get(new) is not None:
            return False
        variable = self.get(old)
        if variable is None:
            return False
        variable.name = new
        return True

    def names(self) -> list:
        return [v.name for v in self.items]

    @property
    def prefix(self) -> str:
        """The expression prefix that reaches this scope."""
        return SCOPE_PREFIX.get(self.scope, "Dsp.")

    def reference(self, name: str) -> str:
        """The expression an author would write for one variable."""
        return f"{self.prefix}{name}"

    # ---------------------------------------------------------- runtime
    def resolve(self, resolver: PropertyResolver) -> dict:
        """{name: value} for every variable that answers.

        A variable whose source goes Bad falls back to its static
        value rather than disappearing: a property referencing it must
        keep rendering something, and the static value is the author's
        own stated default.
        """
        out = {}
        for variable in self.items:
            answer = resolver.resolve(variable.source, variable.value)
            if isinstance(answer, Resolved) and answer.value is not None:
                out[variable.name] = answer.value
            elif variable.value is not None:
                out[variable.name] = variable.value
        return out

    # ------------------------------------------------------ persistence
    def to_list(self) -> list:
        return [v.to_dict() for v in self.items]

    @classmethod
    def from_list(cls, data, scope: str = "Display") -> "VariableSet":
        found = cls(scope=scope)
        for entry in data or ():
            if isinstance(entry, dict) and entry.get("name"):
                found.items.append(Variable.from_dict(entry))
        return found


def may_hold_variables(scope: str) -> bool:
    """Whether variables may be declared on this kind of thing.

    False for a linked PVM — the manual is explicit that you add the
    variable to its PVM class instead, and a variable on the instance
    would be a fork of the class that nothing propagates.
    """
    return scope in SCOPE_PREFIX


def references_in(data: dict) -> set:
    """Every variable name a graphics item's properties reference.

    Used by the complexity count and by deletion: removing a variable
    that thirty properties reference should be able to say so.
    """
    from .properties import VARIABLE, described_properties
    names = set()
    for spec in described_properties(data).values():
        if spec and spec.get("kind") == VARIABLE:
            ref = str(spec.get("ref", ""))
            names.add(ref.split(".", 1)[-1] if "." in ref else ref)
    return names


def descriptor(name: str, scope: str = "Display") -> dict:
    """The descriptor an author's 'reference this variable' produces."""
    from .properties import VARIABLE
    return {"kind": VARIABLE,
            "ref": f"{SCOPE_PREFIX.get(scope, 'Dsp.')}{name}"}


__all__ = ["SCOPES", "SCOPE_PREFIX", "VARIABLE_TYPES", "Variable",
           "VariableSet", "descriptor", "descriptor_for",
           "may_hold_variables", "references_in"]
