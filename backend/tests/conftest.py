"""Shared test setup.

Runs before any test module is imported, so the SQLite database and provider
cache live in a temporary directory instead of the checkout. Set GFI_DATA_DIR to
override (the app reads it once, at import time).
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("GFI_ALLOW_NETWORK", "0")

if not os.environ.get("GFI_DATA_DIR"):
    _RUNTIME = tempfile.mkdtemp(prefix="gfi-test-")
    atexit.register(shutil.rmtree, _RUNTIME, True)
    os.environ["GFI_DATA_DIR"] = _RUNTIME
