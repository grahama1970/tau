#!/usr/bin/env python3
"""Live crash-injection reconciliation harness (#200).

A real child process performs S1-S5 (durable receipt + sidecar intents) and is
SIGKILLed before S7 admission. A second, bare orphan file is planted with no
intent. The store is then reopened and reconcile_startup runs. Exit 0 only if
the crashed receipt is re-admitted (row read back) and the bare orphan is
quarantined with a named reason."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.reconciliation import reconcile_startup  # noqa: E402
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore  # noqa: E402

CHILD = """
import os, signal, sys
sys.path.insert(0, {src!r})
from pathlib import Path
from tau_coding.dag_runtime.admission import write_durable_json
from tau_coding.dag_runtime.write_intent import append_intent
receipts = Path({receipts!r}); sidecar = Path({sidecar!r})
target = receipts / "node-a" / "attempt-1.json"
append_intent(sidecar, run_id={run_id!r}, node_id="node-a", attempt_id={attempt_id!r},
              receipt_kind="node_receipt", stage="S1", target_path=str(target))
result = write_durable_json(target, {{"node_id": "node-a", "verdict": "PASS"}})
append_intent(sidecar, run_id={run_id!r}, node_id="node-a", attempt_id={attempt_id!r},
              receipt_kind="node_receipt", stage="S5", target_path=str(target),
              extra={{"sha256": result.sha256}})
os.kill(os.getpid(), signal.SIGKILL)  # dies before S7 admission
"""


def main() -> int:
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "crash-reconcile-run").resolve()
    receipts = run_dir / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    sidecar = run_dir / "intents.twi"
    db = run_dir / "dag-run.sqlite3"
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-crash",
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
    store = SqliteDagRunStore(db)
    lease = store.acquire_run(plan=plan, run_id="run-crash", owner_id="crash", ttl_seconds=300)
    identity = store.reserve_attempt(
        lease, plan_sha256=plan.plan_sha256, node_id="node-a", attempt=1
    )
    store.close()

    code = CHILD.format(
        src=str(SRC), receipts=str(receipts), sidecar=str(sidecar),
        run_id="run-crash", attempt_id=identity.attempt_id,
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
    killed = proc.returncode == -9

    bare = receipts / "node-a" / "injected-orphan.json"
    bare.write_text('{"claim": "I was never intended"}')

    store = SqliteDagRunStore(db)
    lease2 = DagRunLease(lease.run_id, lease.owner_id, lease.epoch, lease.expires_at_ms)
    outcome = reconcile_startup(
        store, lease2, receipts_root=receipts, sidecar_path=sidecar
    )
    rows = store.list_admissions("run-crash")
    store.close()

    readmitted_ok = (
        len(outcome.readmitted) == 1
        and len(rows) == 1
        and rows[0]["node_id"] == "node-a"
    )
    quarantined_ok = (
        len(outcome.quarantined) == 1
        and outcome.quarantined[0]["reason"] == "no_matching_durable_intent"
        and not bare.exists()
        and Path(outcome.quarantined[0]["quarantined_to"]).exists()
    )
    ok = killed and readmitted_ok and quarantined_ok and not outcome.clean
    receipt = {
        "schema": "tau.crash_reconcile_harness_receipt.v1",
        "mocked": False,
        "live": True,
        "ok": ok,
        "child_killed": killed,
        "readmitted": outcome.readmitted,
        "quarantined": outcome.quarantined,
        "admission_rows": [
            {k: r[k] for k in ("node_id", "receipt_kind", "sha256")} for r in rows
        ],
        "reconciliation_receipt": str(outcome.receipt_path),
    }
    write_durable_json(run_dir / "crash-reconcile-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
