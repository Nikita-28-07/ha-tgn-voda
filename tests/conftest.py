from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

API_PATH = Path(__file__).parents[1] / "custom_components" / "tgn_voda" / "api.py"
SPEC = importlib.util.spec_from_file_location("tgn_voda_api", API_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load integration API from {API_PATH}")
API_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = API_MODULE
SPEC.loader.exec_module(API_MODULE)
