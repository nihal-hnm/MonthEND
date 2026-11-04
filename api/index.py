"""Vercel Serverless Function entrypoint bridge.

Allows native Vercel file-based detection under /api/index.py while delegating
to the unified application handler in root index.py without module name collisions.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

_root_index_path = _root / "index.py"
_spec = importlib.util.spec_from_file_location("root_index", str(_root_index_path))
if not _spec or not _spec.loader:
    raise ImportError(f"Cannot load root index.py from {_root_index_path}")

_mod = importlib.util.module_from_spec(_spec)
sys.modules["root_index"] = _mod
_spec.loader.exec_module(_mod)

app = getattr(_mod, "app")
application = getattr(_mod, "application")
handler = getattr(_mod, "handler")
