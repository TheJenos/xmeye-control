"""Test fixtures.

``dvrip.py`` is deliberately free of Home Assistant imports, so it is loaded
straight from the file rather than through ``custom_components.xmeye``. That
keeps the protocol tests runnable without a Home Assistant install.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_PATH = pathlib.Path(__file__).parents[1] / "custom_components" / "xmeye" / "dvrip.py"
_spec = importlib.util.spec_from_file_location("xmeye_dvrip", _PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules["xmeye_dvrip"] = _module
_spec.loader.exec_module(_module)
