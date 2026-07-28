#!/usr/bin/env python3
"""Live concurrent-admit harness (#199): two real processes race to admit the
same (attempt, receipt_kind, sha256) against one real run store. Exit 0 only
if exactly one admission row exists, both processes succeeded (one as
duplicate-suppressed), and the events table carries both the admission and the
suppression."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402

CHILD = """
import sys
sys.path.insert(0, {src!r})
from pathlib import Path
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore
store = SqliteDagRunStore(Path({db!r}))
lease = DagRunLease({run_id!r}, {owner!r}, {epoch}, {expires})
record = store.admit_receipt(
    lease, {attempt_id!r}, receipt_kind="node_receipt",
    sha256="sha256:race", path="/run/receipts/node-a/attempt-1.json", size_bytes=7,
)
print("duplicate" if record["duplicate"] else "admitted")
"""


def main() -> int:
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "concurrent-admit-run").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    db = run_dir / "dag-run.sqlite3"
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-race",
            "run_dir": str(run_dir / "run"),
            "nodes": [{
                "node_id": "node-a", "role": "node-a", "command": ["true"],
                "depends_on": [], "accepted_context_from": [],
                "receipt_path": str(run_dir / "node-a.json"),
                "timeout_seconds": 1, "max_attempts": 1,
            }],
        },
        source_path=run_dir / "dag.json",
    )
    store = SqliteDagRunStore(db)
    lease = store.acquire_run(plan=plan, run_id="run-race", owner_id="racer", ttl_seconds=120)
    identity = store.reserve_attempt(
        lease, plan_sha256=plan.plan_sha256, node_id="node-a", attempt=1
    )
    store.close()

    code = CHILD.format(
        src=str(SRC), db=str(db), run_id=lease.run_id, owner=lease.owner_id,
        epoch=lease.epoch, expires=lease.expires_at_ms, attempt_id=identity.attempt_id,
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    outcomes = sorted(p.communicate()[0].strip() for p in procs)
    rcs = [p.returncode for p in procs]

    verify = SqliteDagRunStore(db)
    rows = verify.list_admissions("run-race")
    events = [
        dict(r)
        for r in verify._connection.execute(
            "SELECT event_type FROM dag_run_events WHERE event_type LIKE 'receipt_admi%'"
        ).fetchall()
    ]
    verify.close()
    event_types = sorted(e["event_type"] for e in events)
    ok = (
        rcs == [0, 0]
        and outcomes in (["admitted", "duplicate"],)
        and len(rows) == 1
        and rows[0]["sha256"] == "sha256:race"
        and "receipt_admitted" in event_types
        and "receipt_admission_duplicate_suppressed" in event_types
    )
    receipt = {
        "schema": "tau.concurrent_admit_receipt.v1",
        "mocked": False,
        "live": True,
        "ok": ok,
        "child_returncodes": rcs,
        "child_outcomes": outcomes,
        "admission_rows": len(rows),
        "admission_sha256": rows[0]["sha256"] if rows else None,
        "admission_event_types": event_types,
    }
    write_durable_json(run_dir / "concurrent-admit-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
