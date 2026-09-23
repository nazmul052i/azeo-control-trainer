from __future__ import annotations

from typing import Any

from .tag_provider import TagProvider


class AliasedTagProvider(TagProvider):
    """Maps a subprocedure's generic tag names onto concrete parent tags.

    A reusable child procedure declares equipment-neutral tags
    (e.g. PUMP.CMD) and each subprocedure step
    binds them to real tags (e.g. P101.CMD) without editing the child.
    Unmapped tags pass through unchanged.
    """

    def __init__(self, inner: TagProvider, aliases: dict[str, str]):
        self.inner = inner
        self.aliases = dict(aliases)

    def resolve(self, tag: str) -> str:
        return self.aliases.get(tag, tag)

    def read(self, tag: str) -> Any:
        return self.inner.read(self.resolve(tag))

    def write(self, tag: str, value: Any) -> None:
        self.inner.write(self.resolve(tag), value)

    def snapshot(self) -> dict[str, Any]:
        return self.inner.snapshot()

    def __getattr__(self, name: str) -> Any:
        # Delegate feature probes (read_value, proposed_writes, ...) so the
        # engine's hasattr checks see exactly what the inner provider offers.
        attr = getattr(self.inner, name)
        if name == "read_value":
            return lambda tag: attr(self.resolve(tag))
        return attr
