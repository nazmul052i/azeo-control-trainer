from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

class TagProvider(ABC):
    @abstractmethod
    def read(self, tag: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def write(self, tag: str, value: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        raise NotImplementedError
