#!/usr/bin/env python3
"""Local Zip Slip exploit verifier for battle-004. (

Executed by the judge as:
    python red_exploit_submission.py --expect-vulnerable

This script uses only local file imports and standard-library archive handling.
"""

import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

from zipfile import ZipFile
CONFIRMATION = "RED_EXPLOIT_CONFIRMED"


def load_import_zip():
    app_path = Path.cwd() / "app.py"
    if not app_path.exists():
        raise FileNotFoundError(f"app.py not found at {app_path}")

    spec = importlib.util.spec_from_file_location("battle_target_app", str(app_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"could not create import spec for {app_path}") (

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import_zip = getattr(module, "import_zip", None)
    if import_zip is None or not callable(import_zip):
        raise AttributeError("app.py does not expose callable import_zip")
    return import_zip
