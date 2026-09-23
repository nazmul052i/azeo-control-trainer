# -*- coding: utf-8 -*-
"""Force records: every force carries who did it and why.

A force on a simulator-owned signal makes the published value differ from
the physics (the plant keeps computing underneath); a force on a
DCS-owned signal rides the tag's existing override mechanism so the
models act on it through ``Tag.effective`` exactly as local field
operation always has.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ForceRecord:
    tag: str
    value: float | bool
    source: str                    # who: "instructor", "test", ...
    reason: str                    # why, free text, required
    t: float = field(default_factory=time.time)
    true_value: float | bool = 0.0  # last physics value underneath

    def as_dict(self) -> dict:
        return {"tag": self.tag, "value": self.value, "source": self.source,
                "reason": self.reason, "t": self.t}
