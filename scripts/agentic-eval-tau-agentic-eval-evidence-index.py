"""Agentic-eval wrapper for Tau retained evidence-index verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tau_coding.run_ledger import write_agentic_eval_ledger_evidence_selftest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=[
            "positive",
            "mutated-report",
            "substituted-report",
            "deleted-artifact",
            "dirty-tree",
        ],
        required=True,
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = write_agentic_eval_ledger_evidence_selftest(
        args.repo,
        mode=args.mode,
        out=args.out,
    )
    print(
        json.dumps(
            {
                "expected_failure_code": payload.get("expected_failure_code"),
                "proof": str(args.out),
                "status": payload["status"],
            },
            sort_keys=True,
        )
    )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
