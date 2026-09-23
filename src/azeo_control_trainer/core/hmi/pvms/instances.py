"""Linked and unlinked PVM instances, overrides, and templates.

The distinction the manual draws is the one that makes a library worth
having:

- A **linked** PVM tracks its class. Change the class, save it, and
  every linked instance follows — in the configuration environment.
  The displays holding them must then be published to deploy the
  change, which is why PVM classes are never published themselves.
- An **unlinked** PVM was made from a class and then cut loose. Class
  changes do not reach it. This is not a degraded linked PVM; it is
  the answer to "I need this one to be different for ever".

**Overrides** are the middle ground, and the subtle part. A property on
a linked PVM can stop tracking its class and hold its own value —
without unlinking the rest of the PVM. Removing the override makes the
property track again. The rule that makes it safe is that an override
records *that* it overrides, not merely a value that happens to differ:
otherwise a class edit could silently adopt an instance's value, or an
instance could silently lose a deliberate difference.

**Templates** are starting points for displays and layouts. The manual
is precise about what they are not: templates cannot be published,
cannot be tested online, and cannot be referenced from anywhere. They
are copied and then forgotten — which is exactly what distinguishes a
template from a PVM class.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

LINKED, UNLINKED = "linked", "unlinked"

#: What a template may be a template OF.
TEMPLATE_KINDS = ("display", "contextual_display", "layout")


class TemplateRefused(RuntimeError):
    """A template asked to do something templates do not do."""


@dataclass
class PvmInstance:
    """One placement of a PVM class, linked or not."""

    pvm_class: str
    link: str = LINKED
    #: property -> value, for properties that have stopped tracking.
    overrides: dict = field(default_factory=dict)
    #: The class's own property values, as of the last sync.
    tracked: dict = field(default_factory=dict)

    @property
    def is_linked(self) -> bool:
        return self.link == LINKED

    # ------------------------------------------------------- overrides
    def override(self, name: str, value=None) -> bool:
        """Stop tracking one property.

        The manual's detail worth keeping: creating an override does
        NOT change the value. It pins whatever the property already
        shows, so overriding is a decision about *tracking* and a later
        edit is a decision about the value. Collapsing the two makes
        "why did this move?" unanswerable.
        """
        if not self.is_linked:
            return False        # an unlinked PVM tracks nothing anyway
        if name in self.overrides:
            return False
        self.overrides[name] = self.tracked.get(name) \
            if value is None else value
        return True

    def remove_override(self, name: str) -> bool:
        """Track the class again."""
        if name not in self.overrides:
            return False
        del self.overrides[name]
        return True

    def is_overridden(self, name: str) -> bool:
        return name in self.overrides

    def value_of(self, name: str, default=None):
        """What this instance actually shows for one property."""
        if name in self.overrides:
            return self.overrides[name]
        return self.tracked.get(name, default)

    def sync_from_class(self, class_properties: dict) -> list:
        """Take the class's values, keeping overrides.

        Returns the property names that actually moved — what a
        "these displays are a Work in Progress" notification is built
        from. A sync that reported everything as changed would train
        people to ignore it.

        An **unlinked** instance takes nothing: being unlinked is the
        whole point, and a sync that still wrote through would make
        unlinking a lie that only shows up on the next class edit.
        """
        if not self.is_linked:
            return []
        moved = []
        for name, value in (class_properties or {}).items():
            if name in self.overrides:
                continue        # deliberately different; leave it
            if self.tracked.get(name) != value:
                moved.append(name)
            self.tracked[name] = value
        return moved

    def unlink(self) -> None:
        """Cut loose: fold the class's current values in and stop
        tracking. Irreversible by design — relinking is a different
        gesture with different consequences."""
        merged = dict(self.tracked)
        merged.update(self.overrides)
        self.tracked = merged
        self.overrides = {}
        self.link = UNLINKED

    def relink(self, pvm_class: str) -> None:
        """Point at another class. Overrides survive by NAME, so a
        property the new class does not have stays in the record,
        inert — relink back and it takes effect again. The same rule
        the configuration choices already follow."""
        self.pvm_class = pvm_class
        self.link = LINKED

    # ------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        out = {"class": self.pvm_class}
        if self.link != LINKED:
            out["link"] = self.link
        if self.overrides:
            out["overrides"] = dict(self.overrides)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "PvmInstance":
        return cls(pvm_class=str(data.get("class", "")),
                   link=str(data.get("link", LINKED)),
                   overrides=dict(data.get("overrides", {})))


def propagate(class_properties: dict, instances) -> list:
    """Push a class edit to every linked instance.

    A list of `(instance, [properties that moved])` — a list rather
    than a dict keyed by instance, because an instance is mutable and
    keying a report by the thing the report is about is how you get an
    unhashable-type error the first time somebody uses it.

    Unlinked instances are skipped entirely rather than reported as
    unchanged: they are not participating, and "no change" implies
    they were asked.
    """
    moved = []
    for instance in instances:
        if not instance.is_linked:
            continue
        changed = instance.sync_from_class(class_properties)
        if changed:
            moved.append((instance, changed))
    return moved


# ------------------------------------------------------------ templates
@dataclass
class Template:
    """A starting point, copied and then forgotten."""

    name: str
    kind: str = "display"
    document: dict = field(default_factory=dict)
    #: Built-ins are product starting points. They cannot be deleted, and a
    #: project edits one as an override kept in ``_templates.json``; Reset
    #: to built-in restores the product document.
    builtin: bool = False
    overridden: bool = False

    def instantiate(self, name: str) -> dict:
        """A fresh document from this template.

        A deep copy carrying the new name and nothing that points
        back — a display made from a template has no relationship to
        it afterwards, which is the whole difference from a PVM class.
        """
        import copy
        document = copy.deepcopy(self.document)
        document["display" if self.kind != "layout" else "layout"] = name
        document.pop("template", None)
        return document

    def publish(self, *_args, **_kwargs):
        raise TemplateRefused(
            f"templates are not published; make a display from "
            f"{self.name!r} and publish that")

    def to_dict(self) -> dict:
        return {"template": self.name, "kind": self.kind,
                "document": dict(self.document)}

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        return cls(name=str(data.get("template", "")),
                   kind=str(data.get("kind", "display")),
                   document=dict(data.get("document", {})))


class TemplateStore:
    """Library templates persisted beside functions and user PVM classes."""

    def __init__(self, root):
        self.path = Path(root) / "_templates.json"
        self.entries: dict[str, Template] = {}
        self.reload()

    def reload(self) -> None:
        from .hierarchy_templates import builtin_hierarchy_templates
        from .distillation_template import TEMPLATE_NAME, distillation_document

        self.entries = {
            name: Template(name, "display", document, builtin=True)
            for name, document in builtin_hierarchy_templates()
        }
        self.entries[TEMPLATE_NAME] = Template(
            TEMPLATE_NAME, "display", distillation_document(TEMPLATE_NAME).to_dict(), builtin=True)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            for name, record in data.items() \
                    if isinstance(data, dict) else ():
                template = Template.from_dict({**record, "template": name})
                if template.kind not in TEMPLATE_KINDS:
                    continue
                builtin = self.entries.get(name)
                if builtin is not None:
                    # A project record under a built-in name is that
                    # template edited in place: the built-in identity stays
                    # (it still cannot be deleted, and Reset restores the
                    # product document); only a matching kind may override.
                    if template.kind != builtin.kind:
                        continue
                    template.builtin = True
                    template.overridden = True
                self.entries[name] = template

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: {"kind": template.kind, "document": template.document,
                       **({"overrides_builtin": True} if template.builtin else {})}
                for name, template in self.entries.items()
                if not template.builtin or template.overridden}
        self.path.write_text(json.dumps(
            data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def names(self, kind: str = "") -> tuple[str, ...]:
        return tuple(sorted(name for name, template in self.entries.items()
                            if not kind or template.kind == kind))

    def add(self, name: str, kind: str, document: dict) -> Template:
        """Create or replace a template; on a built-in name this records the
        project's edit of that built-in (see :meth:`reset`)."""
        if not name or kind not in TEMPLATE_KINDS:
            raise ValueError("template needs a name and supported kind")
        existing = self.entries.get(name)
        if existing is not None and existing.builtin and existing.kind != kind:
            raise ValueError(f"built-in template {name!r} is a {existing.kind} template")
        template = Template(name, kind, dict(document),
                            builtin=bool(existing is not None and existing.builtin),
                            overridden=bool(existing is not None and existing.builtin))
        self.entries[name] = template
        self.save()
        return template

    def remove(self, name: str) -> bool:
        """Delete a project template; a built-in is never deleted, only reset."""
        if name not in self.entries or self.is_builtin(name):
            return False
        del self.entries[name]
        self.save()
        return True

    def is_overridden(self, name: str) -> bool:
        found = self.entries.get(name)
        return bool(found is not None and found.builtin and found.overridden)

    def reset(self, name: str) -> bool:
        """Drop the project's edit of a built-in and restore the product document."""
        if not self.is_overridden(name):
            return False
        del self.entries[name]
        self.save()
        self.reload()
        return True

    def instantiate(self, template: str, name: str) -> dict | None:
        found = self.entries.get(template)
        return found.instantiate(name) if found is not None else None

    def is_builtin(self, name: str) -> bool:
        found = self.entries.get(name)
        return bool(found is not None and found.builtin)


# -------------------------------------------------- contextual displays
FACEPLATE, DETAIL, MINI = "faceplate", "detail", "mini"
CONTEXTUAL_KINDS = (FACEPLATE, DETAIL, MINI)


@dataclass
class ContextualDisplay:
    """A faceplate, a detail display, or a mini faceplate.

    One document serves every module that calls it: opening substitutes
    the module tag into its data links, so `Loop_fp` drives every PID
    loop in the plant. That is the manual's "partial tag substitution",
    and it is why there is one faceplate document rather than one per
    module.
    """

    name: str
    kind: str = FACEPLATE
    #: The main faceplate this is the small version of, if any.
    expands_to: str = ""
    pinned: bool = False
    minimized: bool = False

    def substitute(self, template: str, tag: str) -> str:
        """Partial tag substitution: `{tag}` becomes the module."""
        return (template or "").replace("{tag}", tag)

    def toggle_pin(self) -> bool:
        """Pinned displays stay until closed by hand; unpinned ones are
        replaced when the next display of their type opens."""
        self.pinned = not self.pinned
        return self.pinned

    def replaced_by(self, other: "ContextualDisplay") -> bool:
        """Whether opening `other` should close this one."""
        return not self.pinned and other.kind == self.kind

    def to_dict(self) -> dict:
        out = {"contextual": self.name, "kind": self.kind}
        if self.expands_to:
            out["expands_to"] = self.expands_to
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "ContextualDisplay":
        return cls(name=str(data.get("contextual", "")),
                   kind=str(data.get("kind", FACEPLATE)),
                   expands_to=str(data.get("expands_to", "")))
