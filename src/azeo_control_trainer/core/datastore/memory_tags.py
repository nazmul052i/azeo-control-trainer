"""Project memory points shared by engineering, procedures and HMI bindings.

The TagDatabase remains the namespace. SQLite backs its authored memory
entries and last values so a PA cannot accidentally reset another app's tag.
"""
from contextlib import contextmanager
import json
import keyword
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryTag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)
    data_type: Literal["any", "float", "int", "bool", "str"] = "any"
    value: Any = 0
    description: str = ""
    min_value: float | None = Field(default=None, allow_inf_nan=False)
    max_value: float | None = Field(default=None, allow_inf_nan=False)

    @property
    def path(self):
        return f"MEMORY/{self.name}/VALUE"

    def checked(self, value):
        valid = {"any": True, "float": type(value) in {int, float}, "int": type(value) is int,
                 "bool": type(value) is bool, "str": isinstance(value, str)}[self.data_type]
        if not valid:
            raise ValueError(f"Memory {self.name} requires {self.data_type}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Memory values must be finite")
        if isinstance(value, str) and len(value) > 8192:
            raise ValueError("Memory text exceeds 8192 characters")
        if type(value) is int and value.bit_length() > 256:
            raise ValueError("Memory integer exceeds 256 bits")
        if self.min_value is not None or self.max_value is not None:
            if type(value) not in {int, float}:
                raise ValueError(f"Memory {self.name}: limits require a numeric value")
            if (self.min_value is not None and value < self.min_value
                    or self.max_value is not None and value > self.max_value):
                raise ValueError(f"Memory {self.name} is outside its configured limits")
        return float(value) if self.data_type == "float" else value

    @model_validator(mode="after")
    def validate_memory(self):
        if (keyword.iskeyword(self.name) or self.name.startswith("tag_")
                or self.name in {"abs", "avg", "len", "max", "min", "round", "sum"}):
            raise ValueError("Memory name must be an unreserved expression identifier")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("Memory minimum must not exceed maximum")
        self.checked(self.value)
        return self


def memory_name(path):
    match = re.fullmatch(r"MEMORY/([A-Za-z_][A-Za-z0-9_]*)/VALUE", path)
    if not match:
        raise ValueError(f"Invalid memory tag path: {path}")
    return match[1]


class MemoryTagStore:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.path = self.project / "tagdb" / "memory.sqlite3"

    @contextmanager
    def connection(self, *, write=False, timeout=5):
        if write:
            from azeo_control_trainer.core.configuration.package_paths import release_root
            if release_root(self.project):
                raise ValueError("Released configuration is immutable; use an editable project for memory tags")
            if not self.path.resolve().is_relative_to(self.project):
                raise ValueError("Memory database must remain inside the project")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path if write else self.path.as_uri() + "?mode=ro", uri=not write, timeout=timeout)
        try:
            if write:
                conn.execute("CREATE TABLE IF NOT EXISTS memory (name TEXT PRIMARY KEY COLLATE NOCASE, definition TEXT NOT NULL, value TEXT NOT NULL)")
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        finally:
            conn.close()

    def definitions(self):
        if not self.path.exists():
            return []
        with self.connection(timeout=0.02) as conn:
            return [MemoryTag.model_validate_json(row[0]) for row in conn.execute("SELECT definition FROM memory ORDER BY name")]

    def create(self, definition):
        spec = MemoryTag.model_validate(definition)
        if spec.data_type == "any":
            raise ValueError("Choose float, int, bool or str for a shared memory tag")
        with self.connection(write=True) as conn:
            try:
                conn.execute("INSERT INTO memory VALUES (?, ?, ?)",
                             (spec.name, spec.model_dump_json(), json.dumps(spec.checked(spec.value), allow_nan=False)))
            except sqlite3.IntegrityError as error:
                raise ValueError(f"Memory tag {spec.name} already exists in this project") from error
        return spec

    def read_many(self, *, timeout=0.02):
        if not self.path.exists():
            return {}
        with self.connection(timeout=timeout) as conn:
            return {f"MEMORY/{name}/VALUE": json.loads(value) for name, value in conn.execute("SELECT name, value FROM memory")}

    def read(self, path, *, timeout=0.02):
        name = memory_name(path)
        if not self.path.exists():
            raise ValueError(f"Memory tag does not exist: {path}")
        with self.connection(timeout=timeout) as conn:
            row = conn.execute("SELECT value FROM memory WHERE name = ? COLLATE BINARY", (name,)).fetchone()
        if row is None:
            raise ValueError(f"Memory tag does not exist: {path}")
        return json.loads(row[0])

    def write_many(self, values, *, types=None, timeout=5):
        if not values:
            return
        if not self.path.exists():
            raise ValueError("Project memory database is unavailable")
        with self.connection(write=True, timeout=timeout) as conn:
            updates = []
            for path, value in values.items():
                name = memory_name(path)
                row = conn.execute("SELECT definition, value FROM memory WHERE name = ? COLLATE BINARY", (name,)).fetchone()
                if row is None:
                    raise ValueError(f"Memory tag does not exist: {path}")
                spec = MemoryTag.model_validate_json(row[0])
                if types and types[path] != spec.data_type:
                    raise ValueError(f"Memory tag type changed: {path}")
                encoded = json.dumps(spec.checked(value), allow_nan=False)
                if encoded != row[1]:
                    updates.append((encoded, name))
            # Validate the whole calculation batch before making any value visible.
            conn.executemany("UPDATE memory SET value = ? WHERE name = ?", updates)

    def write(self, path, value):
        self.write_many({path: value})
