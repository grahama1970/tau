from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan

ROOT = Path(__file__).resolve().parents[4]
PROOF_DIR = ROOT / "docs/proofs/tickets/issue-293-attempt-result-admission"
ARTIFACT = PROOF_DIR / "live-readback.json"


def main() -> None:
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tau-issue-293-") as scratch:
        scratch_dir = Path(scratch)
        cases = [
            _run_scheduler_malformed_result(scratch_dir / "scheduler-malformed"),
            _run_store_rejects_raw_malformed(scratch_dir / "store-reject"),
        ]
    receipt = {
        "schema": "tau.issue_293_live_readback.v1",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "cases": cases,
        "status": "PASS" if all(case["ok"] for case in cases) else "FAIL",
    }
    ARTIFACT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if receipt["status"] != "PASS":
        raise SystemExit(json.dumps(receipt, indent=2, sort_keys=True))
    print(f"issue-293 live readback PASS: {ARTIFACT}")


def _run_scheduler_malformed_result(run_dir: Path) -> dict[str, Any]:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-293-scheduler-malformed",
            "run_dir": str(run_dir),
            "nodes": [
                _node(run_dir, "producer"),
                _node(run_dir, "consumer", depends_on=["producer"]),
            ],
        },
        source_path=run_dir / "dag.json",
    )
    calls: list[str] = []
    database = run_dir / "dag-run.sqlite3"

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        calls.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "FAIL",
            "accepted_output": {"source_node_id": node.node_id},
        }

    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="issue-293-scheduler-malformed",
        )

    staged_rows = _staged_rows(database)
    malformed_not_stored = all(row.get("verdict") != "FAIL" for row in staged_rows)
    blocked_replacement_stored = any(
        row.get("verdict") == "DAG_ATTEMPT_RESULT_INVALID"
        and row.get("errors") == ["dag_attempt_result_pass_verdict_mismatch"]
        for row in staged_rows
    )
    return {
        "name": "scheduler_malformed_result_blocks_successor",
        "ok": (
            result.status == "BLOCKED"
            and result.verdict == "DAG_ATTEMPT_RESULT_INVALID"
            and calls == ["producer"]
            and result.completed_node_ids == ()
            and malformed_not_stored
            and blocked_replacement_stored
        ),
        "status": result.status,
        "verdict": result.verdict,
        "calls": calls,
        "completed_node_ids": list(result.completed_node_ids),
        "staged_rows": staged_rows,
    }


def _run_store_rejects_raw_malformed(run_dir: Path) -> dict[str, Any]:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-293-store-reject",
            "run_dir": str(run_dir),
            "nodes": [_node(run_dir, "producer")],
        },
        source_path=run_dir / "dag.json",
    )
    database = run_dir / "dag-run.sqlite3"
    error_code = None
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="issue-293-store-reject", owner_id="proof")
        identity = store.reserve_attempt(
            lease,
            plan_sha256=plan.plan_sha256,
            node_id="producer",
            attempt=1,
        )
        store.mark_dispatched(lease, identity.attempt_id)
        try:
            store.stage_result(
                lease,
                identity.attempt_id,
                {"node_id": "producer", "status": True, "verdict": "PASS"},
            )
        except DagRunStoreError as exc:
            error_code = exc.code

    staged_rows = _staged_rows(database)
    return {
        "name": "store_rejects_raw_malformed_without_staging",
        "ok": error_code == "dag_attempt_result_status_invalid" and staged_rows == [],
        "error_code": error_code,
        "staged_rows": staged_rows,
    }


def _staged_rows(database: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT staged_json FROM dag_attempt_outputs ORDER BY attempt_id"
        ).fetchall()
    return [json.loads(str(row[0])) for row in rows]


def _node(
    run_dir: Path,
    node_id: str,
    *,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": depends_on or [],
        "receipt_path": str(run_dir / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


if __name__ == "__main__":
    main()
