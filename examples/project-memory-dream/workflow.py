"""Packaged project-memory-dream workflow command.

One stable command for the agent-skills scheduler/monitor integration:

    uv run python examples/project-memory-dream/workflow.py run \
        --config <run-config.json> --run-dir <new-dir>

The first invocation builds a ``tau.generic_dag_spec.v1``, persists per-node
work orders, and executes it on the canonical Tau scheduler. Re-invoking the
same command resumes the recorded run from durable receipts (``--fresh``
forces a new run directory instead).

No cron registration happens here; schedulers call this command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec_builder import build_spec_with_orders, write_work_orders  # noqa: E402

from tau_coding.generic_dag import (  # noqa: E402
    resume_generic_dag_from_run,
    run_generic_dag,
)


def _run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    run_dir = Path(args.run_dir).expanduser().resolve()
    spec_path = run_dir / "dag-spec.json"
    resume = spec_path.is_file() and not args.fresh
    if resume:
        receipt = resume_generic_dag_from_run(run_dir)
    else:
        if run_dir.exists() and any(run_dir.iterdir()) and not args.fresh:
            print(
                f"run directory {run_dir} is not empty; pass --fresh or resume with the same command",
                file=sys.stderr,
            )
            return 2
        run_dir.mkdir(parents=True, exist_ok=True)
        spec, orders = build_spec_with_orders(config_path=config_path, run_dir=run_dir)
        write_work_orders(orders)
        spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt = run_generic_dag(spec_path=spec_path, resume=True)
    summary_path = run_dir / "run-summary.json"
    payload = {
        "schema": "tau.workflow_run_receipt.v1",
        "workflow_id": "project-memory-dream",
        "run_id": receipt.get("run_id"),
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "mocked": False,
        "live": True,
        "provider_live": bool(receipt.get("provider_live")),
        "dag_status": receipt.get("status"),
        "run_dir": str(run_dir),
        "spec_path": str(spec_path),
        "run_summary_path": str(summary_path) if summary_path.is_file() else None,
        "final_receipt_path": str(_final_receipt(run_dir)),
        "resumed": resume,
        "node_count": receipt.get("node_count"),
        "completed_node_count": receipt.get("completed_node_count"),
        "errors": receipt.get("errors") or [],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if receipt.get("status") == "PASS" else 1


def _final_receipt(run_dir: Path) -> Path | None:
    candidate = run_dir / "receipts" / "aggregate" / "project-dream-receipt.json"
    return candidate if candidate.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="packaged project-memory-dream workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run (or resume) one consolidation run")
    run.add_argument("--config", required=True, help="run config JSON path")
    run.add_argument("--run-dir", required=True, help="run directory (new, or an existing run)")
    run.add_argument("--fresh", action="store_true", help="ignore any existing run in --run-dir")
    args = parser.parse_args()
    if args.command == "run":
        return _run(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
