"""Open the trainer's focused plant UI using its configured Local Virtual I/O."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    sys.argv.insert(1, "--simulator")
    runpy.run_path(str(Path(__file__).resolve().with_name("run.py")), run_name="__main__")
