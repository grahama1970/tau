"""Headless entrypoint for Tau's attempt-scoped Python kernel worker."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tau_coding.runtime_backends.kernel import write_python_workspace_canary
from tau_coding.runtime_backends.kernel_contracts import build_python_package_manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tau-python-kernel-worker")
    parser.add_argument("--canary-output-dir", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    args = parser.parse_args(argv)
    if args.manifest_output is not None:
        manifest = build_python_package_manifest().to_payload()
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(manifest, sort_keys=True))
        return 0 if manifest["available"] else 2
    if args.canary_output_dir is not None:
        receipt = write_python_workspace_canary(args.canary_output_dir)
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt["status"] == "PASS" else 2
    parser.error("one of --canary-output-dir or --manifest-output is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
