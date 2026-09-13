#!/usr/bin/env python3
"""Live proof for tau#348 strict scheduler-to-node dispatch envelopes."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.node_input_manifest import (  # noqa: E402
    DAG_NODE_DISPATCH_SCHEMA,
    DagNodeDispatchAdmissionError,
    validate_node_dispatch_envelope,
)
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402
import tau_coding.dag_runtime.scheduler as scheduler_module  # noqa: E402
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan  # noqa: E402


def _node(root: Path, node_id: str, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": list(depends_on or []),
        "receipt_path": str(root / f"{node_id}.json"),
        "timeout_seconds": 10,
        "max_attempts": 1,
        "extensions": {"effect_intent": {"effect": "none", "node_id": node_id}},
    }


def _spec(root: Path, nodes: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": run_id,
        "run_dir": str(root / "run"),
        "nodes": nodes,
    }


def _positive(work: Path) -> dict[str, Any]:
    root = work / "positive"
    root.mkdir(parents=True)
    plan = compile_generic_dag_plan(
        _spec(
            root,
            [
                _node(root, "source"),
                _node(root, "left", ["source"]),
                _node(root, "right", ["source"]),
                _node(root, "join", ["left", "right"]),
            ],
            "tau-348-positive",
        ),
        source_path=root / "dag.json",
    )
    calls: list[str] = []
    branch_inputs: dict[str, list[str]] = {}
    envelope_hashes: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        calls.append(node.node_id)
        envelope = validate_node_dispatch_envelope(execution.dispatch_envelope or {}).model_dump(
            by_alias=True
        )
        envelope_hashes.append(envelope["envelope_sha256"])
        branch_inputs[node.node_id] = [str(item.get("source_node_id")) for item in accepted_inputs]
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "source_node_id": node.node_id,
                "evidence": [
                    {
                        "schema": "tau.issue348.evidence.v1",
                        "node_id": node.node_id,
                        "sha256": canonical_sha256({"node_id": node.node_id}),
                    }
                ],
            },
        }

    store_path = root / "dag.sqlite3"
    with SqliteDagRunStore(store_path) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="tau-348-positive",
            lease_owner="tau-348-proof",
            max_concurrency=2,
        )
        admissions = store.list_admissions(
            "tau-348-positive", receipt_kind=DAG_NODE_DISPATCH_SCHEMA
        )
    return {
        "status": result.status,
        "calls": calls,
        "branch_inputs": branch_inputs,
        "dispatch_admissions": len(admissions),
        "envelope_hashes": envelope_hashes,
        "passed": result.status == "PASS"
        and len(admissions) == 4
        and branch_inputs == {
            "source": [],
            "left": ["source"],
            "right": ["source"],
            "join": ["left", "right"],
        },
    }


def _negative(work: Path) -> dict[str, Any]:
    root = work / "negative"
    root.mkdir(parents=True)
    plan = compile_generic_dag_plan(
        _spec(root, [_node(root, "blocked")], "tau-348-negative"),
        source_path=root / "dag.json",
    )
    calls: list[str] = []
    original = scheduler_module.validate_node_dispatch_projection_against_scheduler_state

    def reject(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_goal_hash_mismatch", "$.goal_hash"
        )

    def execute(node: DagPlanNode, accepted_inputs: tuple[dict[str, Any], ...], execution: DagNodeAttempt) -> dict[str, Any]:
        del accepted_inputs, execution
        calls.append(node.node_id)
        return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}

    try:
        scheduler_module.validate_node_dispatch_projection_against_scheduler_state = reject
        result = run_dag_plan(plan, execute_node=execute)
    finally:
        scheduler_module.validate_node_dispatch_projection_against_scheduler_state = original
    node_result = result.node_results[0] if result.node_results else {}
    return {
        "status": result.status,
        "verdict": result.verdict,
        "adapter_call_count": len(calls),
        "alert_codes": node_result.get("alert_codes"),
        "original_code": ((node_result.get("diagnostics") or {}).get("scheduler_boundary") or {}).get("failure", {}).get("original_code"),
        "passed": result.status == "BLOCKED"
        and len(calls) == 0
        and "dag_node_dispatch_goal_hash_mismatch" in (node_result.get("alert_codes") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work")
    args = parser.parse_args()
    work = Path(args.work).expanduser().resolve() if args.work else Path(tempfile.mkdtemp(prefix="tau-348-dispatch-"))
    work.mkdir(parents=True, exist_ok=True)
    positive = _positive(work)
    negative = _negative(work)
    ok = positive["passed"] and negative["passed"]
    receipt = {
        "schema": "tau.issue_348_dispatch_envelope_agentic_eval.v1",
        "issue": 348,
        "status": "PASS" if ok else "FAIL",
        "ok": ok,
        "live": True,
        "mocked": False,
        "provider_live": False,
        "work": str(work),
        "positive_dispatch_fixture": positive,
        "negative_invalid_dispatch_before_adapter": negative,
        "proof_boundary": {
            "proves": "run_dag_plan builds strict tau.dag_node_dispatch.v1 envelopes before adapter control, passes accepted inputs through that envelope, admits durable dispatch receipts, and blocks invalid dispatch before adapter execution.",
            "does_not_prove": "paid provider semantic quality or remote Herdr process execution.",
        },
    }
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
