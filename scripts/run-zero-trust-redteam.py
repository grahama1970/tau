#!/usr/bin/env python3
"""Run Tau's deterministic zero-trust adversarial containment checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tau_coding.zero_trust_redteam import run_zero_trust_redteam


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    receipt = run_zero_trust_redteam(output_dir=args.out_dir)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
