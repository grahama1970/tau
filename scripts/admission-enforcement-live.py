#!/usr/bin/env python3
"""Live #208 harness for the receipt-admission enforcement invariant."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402
from tau_coding.dag_runtime.scheduler import run_dag_plan  # noqa: E402


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "208-admission-enforcement").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = out_dir / "run"
    receipts_dir = out_dir / "receipts"
    torn_receipt = receipts_dir / "liar" / "attempt.json"
    torn_receipt.parent.mkdir(parents=True, exist_ok=True)
    torn_receipt.write_text('{"schema": "torn-mid-write"', encoding="utf-8")

    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "run-enforce-live",
        "run_dir": str(run_dir),
        "nodes": [
            {
                "node_id": "liar",
                "role": "liar",
                "command": ["true"],
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(torn_receipt),
                "timeout_seconds": 5,
                "max_attempts": 1,
            }
        ],
    }
    spec_path = out_dir / "bypass-spec.json"
    write_durable_json(spec_path, spec)
    plan = compile_generic_dag_plan(spec, source_path=spec_path)
    store_path = out_dir / "dag-run.sqlite3"

    def lying_executor(node, accepted_inputs, attempt):  # noqa: ANN001, ARG001
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(torn_receipt),
            "command_results": [],
        }

    with SqliteDagRunStore(store_path) as store:
        outcome = run_dag_plan(
            plan,
            execute_node=lying_executor,
            run_store=store,
            run_id="run-enforce-live",
            lease_owner="admission-enforcement-live",
        )
        admission_rows = store.list_admissions("run-enforce-live")
        events = store.load_events("run-enforce-live")

    node_result = next(
        row for row in outcome.node_results if row.get("node_id") == "liar"
    )
    system_rows = [
        row for row in admission_rows if row.get("receipt_kind") == "system_settlement"
    ]
    accepted_pass = (
        node_result.get("status") == "PASS" and node_result.get("verdict") == "PASS"
    )
    ok = (
        outcome.status == "BLOCKED"
        and node_result.get("status") == "BLOCKED"
        and node_result.get("verdict") == "RECEIPT_NOT_ADMITTED"
        and len(system_rows) == 1
        and not accepted_pass
    )
    receipt = {
        "schema": "tau.admission_enforcement_harness_receipt.v1",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "ok": ok,
        "run_status": outcome.status,
        "run_verdict": outcome.verdict,
        "node_status": node_result.get("status"),
        "node_verdict": node_result.get("verdict"),
        "accepted_pass_state_present": accepted_pass,
        "system_settlement_admission_rows": len(system_rows),
        "admission_rows": admission_rows,
        "event_count": len(events),
        "spec_path": str(spec_path),
        "store_path": str(store_path),
        "torn_receipt_path": str(torn_receipt),
    }
    write_durable_json(out_dir / "admission-enforcement-receipt.json", receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
