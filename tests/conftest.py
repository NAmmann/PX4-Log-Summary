"""Pytest configuration: make `src/` importable as the `px4_flight_analyzer` package.

The project deliberately has no `pyproject.toml`/packaging step (see
README for why), so this is the one place that wires up `sys.path` for
every test module, mirroring what `px4_flight_analyzer.py` does for a
normal run.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

# Insert PROJECT_ROOT first, SRC_DIR second: each insert(0, ...) pushes
# the previous entry back, so SRC_DIR ends up searched *before*
# PROJECT_ROOT. That guarantees `import px4_flight_analyzer` resolves to
# the real package under src/, not to the top-level px4_flight_analyzer.py
# entry-point script of the same name.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
