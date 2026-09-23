"""RuntimeContext — per-scan environment passed to function blocks.

Most function blocks (PID, AI, AO, math, selectors) don't need anything
beyond their terminal values. ACT scripts, however, often want to read
or write tags that aren't physically wired to ``IN1..IN8``. The
``RuntimeContext`` makes the surrounding world available to a block
**without** widening the public block interface — the runtime simply
attaches a context object to each block right before ``execute(dt)``.

A block that wants to participate reads ``self.runtime_context`` and
either uses what it finds or ignores the field entirely (the default
``FunctionBlock.runtime_context`` is ``None``).

The context carries:

* **store** — the :class:`SharedDataStore` shared across the azeo_control_trainer.
* **plugin_id** — the active plugin's id, for audit logging.
* **write_allowlist** — exact-match tags that ACT scripts may
  ``set_tag()`` (in addition to anything matched by
  ``write_patterns``).
* **write_patterns** — fnmatch patterns (e.g. ``"ctrl.*.SP"``) widening
  the allowlist.

Plugins opt in by exposing these attributes on the plugin object:

    plugin.script_writable_tags     : frozenset[str]
    plugin.script_writable_patterns : tuple[str, ...]

The strategy runtime reads them once, builds the context, and reuses it
across every scan. If neither attribute is present the allowlists stay
empty and ``set_tag`` rejects all writes — a deliberate fail-safe
default that mirrors the OPC UA server's writable-tags policy.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeContext:
    """Per-scan environment handed to blocks before ``execute(dt)``."""

    store: Any = None
    plugin_id: str = ""
    write_allowlist: frozenset[str] = field(default_factory=frozenset)
    write_patterns: tuple[str, ...] = field(default_factory=tuple)

    # ─────────────────────────────────────────────────────────────────
    def can_write(self, tag: str) -> bool:
        """Return True if ``tag`` may be written from an ACT script."""
        if not tag:
            return False
        if tag in self.write_allowlist:
            return True
        for pat in self.write_patterns:
            if fnmatch.fnmatchcase(tag, pat):
                return True
        return False

    # ----- Convenience helpers (used by ACT) -----
    def read(self, tag: str, default: Any = None) -> Any:
        """Read a tag from the store; returns ``default`` if missing."""
        if self.store is None:
            return default
        # A failed read must reach ACT's read-error policy, not become a
        # plausible process value. Single reads also avoid copying all tags.
        getter = getattr(self.store, "get", None)
        if callable(getter):
            return getter(tag, default)
        return self.store.get_all().get(tag, default)

    def write(self, tag: str, value: Any) -> None:
        """Queue a write to the store (uses queue_write for external-write
        semantics so engine handlers process it like any other client).

        Raises:
            PermissionError: tag is not in the allowlist.
            RuntimeError:    no store attached (context not initialised).
        """
        if not self.can_write(tag):
            raise PermissionError(
                f"set_tag({tag!r}): not in plugin's script_writable_tags / "
                f"script_writable_patterns allow-list")
        if self.store is None:
            raise RuntimeError(f"set_tag({tag!r}): no store in RuntimeContext")
        if hasattr(self.store, "queue_write"):
            self.store.queue_write(tag, value)
        elif hasattr(self.store, "set"):
            self.store.set(tag, value)
        else:
            raise RuntimeError(
                "set_tag: store has neither queue_write() nor set()")


# ────────────────────────────────────────────────────────────────────────
def build_context_from_plugin(store: Any, plugin: Any) -> RuntimeContext:
    """Build a RuntimeContext from a plugin's opt-in attributes.

    Looks for:
        plugin.script_writable_tags      (frozenset[str] | set[str])
        plugin.script_writable_patterns  (tuple[str,...] | list[str])

    Missing attributes default to empty — i.e. *no writes allowed* and
    ``set_tag()`` will refuse every call from ACT.
    """
    allow = getattr(plugin, "script_writable_tags", None) or frozenset()
    patterns = tuple(getattr(plugin, "script_writable_patterns", ()) or ())
    return RuntimeContext(
        store=store,
        plugin_id=getattr(plugin, "id", "") if plugin is not None else "",
        write_allowlist=frozenset(allow),
        write_patterns=patterns,
    )
