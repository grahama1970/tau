#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode  # noqa: E402
from tau_coding.dag_runtime.replay import replay_dag_run  # noqa: E402
from tau_coding.dag_runtime.run_store import (  # noqa: E402
    SqliteDagRunReader,
    SqliteDagRunStore,
)
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan  # noqa: E402
from tau_coding.dag_runtime.transition import (  # noqa: E402
    AllSuccessTransitionPolicy,
    DagNodeCancellation,
    DagNodeCompletion,
    DagTransitionBatch,
    DagTransitionView,
)

PROOF_DIR = Path(__file__).resolve().parent
RUN_ROOT = PROOF_DIR / "live-readback-run"
OUT = PROOF_DIR / "live-readback.json"


class ReceiptPolicy(AllSuccessTransitionPolicy):
    def __init__(self, receipt: Path) -> None:
        self.receipt = receipt

    def after_node_terminal(
        self, view: DagTransitionView, completion: DagNodeCompletion
    ) -> DagTransitionBatch:
        self.receipt.write_text(
            json.dumps(
                {
                    "schema": "tau.issue_294.transition_receipt.v1",
                    "node_id": completion.node_id,
                    "attempt": completion.attempt,
                    "created_at_ns": time.time_ns(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        base = super().after_node_terminal(view, completion)
        return replace(base, receipt_paths=(str(self.receipt),))


class InvalidPolicy(AllSuccessTransitionPolicy):
    def after_node_terminal(
        self, view: DagTransitionView, completion: DagNodeCompletion
    ) -> DagTransitionBatch:
        del view, completion
        return DagTransitionBatch(
            node_cancellations=(
                DagNodeCancellation(node_id="missing-node", reason_code="bad_cancel"),
            )
        )


def main() -> int:
    if RUN_ROOT.exists():
        shutil.rmtree(RUN_ROOT)
    RUN_ROOT.mkdir(parents=True)
    proof: dict[str, Any] = {
        "schema": "tau.issue_294.live_readback.v1",
        "mocked": False,
        "live": True,
        "checks": {},
    }

    valid_plan = _plan(RUN_ROOT / "valid", ["source"])
    valid_db = RUN_ROOT / "valid" / "dag-run.sqlite3"
    transition_receipt = RUN_ROOT / "valid" / "transition-receipt.json"
    with SqliteDagRunStore(valid_db) as store:
        result = run_dag_plan(
            valid_plan,
            run_store=store,
            run_id="issue-294-valid",
            transition_policy=ReceiptPolicy(transition_receipt),
            execute_node=_pass_node,
        )
        proof["checks"]["valid_run"] = {
            "status": result.status,
            "verdict": result.verdict,
            "receipt_paths": list(result.transition_receipt_paths),
            "store_integrity": store.integrity_check(),
        }
    with SqliteDagRunReader(valid_db) as reader:
        replay = replay_dag_run(
            plan=reader.load_plan("issue-294-valid"),
            run_record=reader.load_run_record("issue-294-valid"),
            events=tuple(
                item.to_mapping()
                for item in reader.load_events("issue-294-valid", limit=5000)
            ),
            attempts=reader.load_attempts("issue-294-valid"),
            runtime_projections=reader.runtime_projections("issue-294-valid"),
        )
    expected_hash = f"sha256:{hashlib.sha256(transition_receipt.read_bytes()).hexdigest()}"
    proof["checks"]["valid_replay"] = {
        "run_status": replay.run_status,
        "journal_sequence": replay.journal_sequence,
        "transition_receipt_count": len(replay.transition_receipts),
        "receipt_hash_matches_file": replay.transition_receipts[0].file_sha256 == expected_hash,
    }

    transition_receipt.write_text(
        json.dumps({"schema": "tau.issue_294.transition_receipt.v1", "tampered": True})
        + "\n",
        encoding="utf-8",
    )
    with SqliteDagRunReader(valid_db) as reader:
        try:
            replay_dag_run(
                plan=reader.load_plan("issue-294-valid"),
                run_record=reader.load_run_record("issue-294-valid"),
                events=tuple(
                    item.to_mapping()
                    for item in reader.load_events("issue-294-valid", limit=5000)
                ),
                attempts=reader.load_attempts("issue-294-valid"),
                runtime_projections=reader.runtime_projections("issue-294-valid"),
            )
        except RuntimeError as exc:
            proof["checks"]["receipt_mutation_rejected"] = {
                "error": str(exc).split(":", 1)[0],
            }
        else:
            raise AssertionError("mutated receipt replay unexpectedly succeeded")

    invalid_plan = _plan(RUN_ROOT / "invalid", ["source"])
    invalid_db = RUN_ROOT / "invalid" / "dag-run.sqlite3"
    with SqliteDagRunStore(invalid_db) as store:
        try:
            run_dag_plan(
                invalid_plan,
                run_store=store,
                run_id="issue-294-invalid",
                transition_policy=InvalidPolicy(),
                execute_node=_pass_node,
            )
        except RuntimeError as exc:
            proof["checks"]["live_invalid_transition_rejected"] = {
                "error": str(exc).split(":", 1)[0],
            }
        else:
            raise AssertionError("invalid transition unexpectedly committed")
        proof["checks"]["invalid_store_integrity"] = store.integrity_check()
    with sqlite3.connect(invalid_db) as connection:
        committed = [
            str(row[0])
            for row in connection.execute(
                "SELECT event_type FROM dag_run_events ORDER BY seq"
            ).fetchall()
        ]
    proof["checks"]["invalid_transition_not_committed"] = {
        "event_types": committed,
        "scheduler_transition_committed_count": committed.count(
            "scheduler_transition_committed"
        ),
    }

    _assert_proof(proof)
    OUT.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, sort_keys=True))
    return 0


def _assert_proof(proof: dict[str, Any]) -> None:
    assert proof["mocked"] is False
    assert proof["live"] is True
    assert proof["checks"]["valid_run"]["status"] == "PASS"
    assert proof["checks"]["valid_replay"]["run_status"] == "PASS"
    assert proof["checks"]["valid_replay"]["transition_receipt_count"] == 1
    assert proof["checks"]["valid_replay"]["receipt_hash_matches_file"] is True
    assert proof["checks"]["receipt_mutation_rejected"]["error"] == (
        "dag_transition_receipt_hash_mismatch"
    )
    assert proof["checks"]["live_invalid_transition_rejected"]["error"] == (
        "dag_transition_unknown_cancellation"
    )
    assert proof["checks"]["invalid_transition_not_committed"][
        "scheduler_transition_committed_count"
    ] == 0
    assert proof["checks"]["valid_run"]["store_integrity"]["ok"] is True
    assert proof["checks"]["invalid_store_integrity"]["ok"] is True


def _pass_node(
    node: DagPlanNode,
    accepted_inputs: tuple[dict[str, Any], ...],
    execution: DagNodeAttempt,
) -> dict[str, Any]:
    del accepted_inputs, execution
    return {
        "node_id": node.node_id,
        "status": "PASS",
        "verdict": "PASS",
        "accepted_output": {"source_node_id": node.node_id},
    }


def _plan(run_dir: Path, node_ids: list[str]) -> DagPlan:
    run_dir.mkdir(parents=True, exist_ok=True)
    nodes: list[dict[str, object]] = []
    for index, node_id in enumerate(node_ids):
        dependencies = [node_ids[index - 1]] if index else []
        nodes.append(
            {
                "node_id": node_id,
                "role": node_id,
                "command": ["true"],
                "depends_on": dependencies,
                "accepted_context_from": dependencies,
                "receipt_path": str(run_dir / f"{node_id}.json"),
                "timeout_seconds": 1,
                "max_attempts": 1,
            }
        )
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-294-proof",
            "run_dir": str(run_dir),
            "nodes": nodes,
        },
        source_path=run_dir / "dag.json",
    )


if __name__ == "__main__":
    raise SystemExit(main())
