"""Registry of all available function block types."""
from __future__ import annotations

from typing import Type
import logging

from .block_base import FunctionBlock, BlockCategory

log = logging.getLogger("strategy.registry")


class BlockRegistry:
    """Singleton registry mapping block_type strings to classes."""

    _instance = None
    _types: dict[str, Type[FunctionBlock]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._types = {}
        return cls._instance

    def register(self, block_cls: Type[FunctionBlock]):
        self._types[block_cls.block_type] = block_cls
        return block_cls

    def get(self, block_type: str) -> Type[FunctionBlock] | None:
        return self._types.get(block_type)

    def create(self, block_type: str, instance_name: str = "") -> FunctionBlock | None:
        cls = self._types.get(block_type)
        if cls is None:
            log.warning("Unknown block type: %s", block_type)
            return None
        return cls(instance_name=instance_name)

    def all_types(self) -> dict[str, Type[FunctionBlock]]:
        return dict(self._types)

    def by_category(self) -> dict[BlockCategory, list[Type[FunctionBlock]]]:
        result: dict[BlockCategory, list[Type[FunctionBlock]]] = {}
        for cls in self._types.values():
            result.setdefault(cls.category, []).append(cls)
        return result


# Module-level singleton
registry = BlockRegistry()


def register_block(cls: Type[FunctionBlock]) -> Type[FunctionBlock]:
    """Decorator to register a block type."""
    registry.register(cls)
    return cls
