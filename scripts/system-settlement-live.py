#!/usr/bin/env python3
"""Live fault-injection harness for system settlement (#201). Scenario A: a
real attempt whose worker never produced a receipt is settled BLOCKED through
the trusted path; the system_settlement admission row and its durable payload
are read back through a fresh store connection. Scenario B: the store is made
genuinely unusable (closed connection) and the trusted path must write the
run-store-failure marker and refuse dispatch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402
from tau_coding.dag_runtime.system_settlement import (  # noqa: E402
    RUN_STORE_FAILURE_MARKER,
    RunStoreFailure,
    assert_dispatch_allowed,
    settle_with_system_receipt,
)


def _mk(run_dir: Path, run_id: str):
    receipts = run_dir / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": run_id,
            "run_dir": str(run_dir / "run"),
            "nodes": [{
                "node_id": "node-a", "role": "node-a", "command": ["true"],
                "depends_on": [], "accepted_context_from": [],
                "receipt_path": str(receipts / "node-a" / "attempt-1.json"),
                "timeout_seconds": 1, "max_attempts": 1,
            }],
        },
        source_path=run_dir / "dag.json",
    )
    store = SqliteDagRunStore(run_dir / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id=run_id, owner_id="live", ttl_seconds=120)
    identity = store.reserve_attempt(
        lease, plan_sha256=plan.plan_sha256, node_id="node-a", attempt=1
    )
    return store, lease, identity, receipts


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "system-settlement-run").resolve()

    a_dir = base / "scenario-a"
    store, lease, identity, receipts = _mk(a_dir, "run-sys-a")
    settle_with_system_receipt(
        store, lease, identity.attempt_id,
        receipts_root=receipts, node_id="node-a",
        reason_code="expected_receipt_not_admitted",
        expected_receipt_kind="node_receipt",
        classification="attempted_and_swallowed",
        run_dir=a_dir,
    )
    store.close()
    fresh = SqliteDagRunStore(a_dir / "dag-run.sqlite3")
    rows = fresh.list_admissions("run-sys-a")
    fresh.close()
    payload = json.loads(Path(rows[0]["path"]).read_text()) if rows else {}
    a_ok = (
        len(rows) == 1
        and rows[0]["receipt_kind"] == "system_settlement"
        and payload.get("verdict") == "BLOCKED"
        and not (a_dir / RUN_STORE_FAILURE_MARKER).exists()
    )

    b_dir = base / "scenario-b"
    store, lease, identity, receipts = _mk(b_dir, "run-sys-b")
    store.close()  # storage genuinely unusable
    b_raised = False
    try:
        settle_with_system_receipt(
            store, lease, identity.attempt_id,
            receipts_root=receipts, node_id="node-a",
            reason_code="expected_receipt_not_admitted",
            expected_receipt_kind="node_receipt",
            classification="attempted_and_swallowed",
            run_dir=b_dir,
        )
    except RunStoreFailure:
        b_raised = True
    marker_path = b_dir / RUN_STORE_FAILURE_MARKER
    dispatch_refused = False
    try:
        assert_dispatch_allowed(b_dir)
    except RunStoreFailure:
        dispatch_refused = True
    b_ok = b_raised and marker_path.exists() and dispatch_refused

    ok = a_ok and b_ok
    receipt = {
        "schema": "tau.system_settlement_harness_receipt.v1",
        "mocked": False,
        "live": True,
        "ok": ok,
        "scenario_a": {
            "admission_rows": len(rows),
            "receipt_kind": rows[0]["receipt_kind"] if rows else None,
            "payload_verdict": payload.get("verdict"),
        },
        "scenario_b": {
            "run_store_failure_raised": b_raised,
            "marker_present": marker_path.exists(),
            "dispatch_refused": dispatch_refused,
            "marker_reason": json.loads(marker_path.read_text()).get("reason", "")[:80]
            if marker_path.exists()
            else None,
        },
    }
    write_durable_json(base / "system-settlement-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
