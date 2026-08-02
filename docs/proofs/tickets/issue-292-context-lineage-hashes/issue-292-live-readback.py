from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan

ROOT = Path(__file__).resolve().parents[4]
PROOF_DIR = ROOT / "docs/proofs/tickets/issue-292-context-lineage-hashes"
ARTIFACT = PROOF_DIR / "live-readback.json"


def main() -> None:
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    cases = [
        _run_lineage_mismatch(PROOF_DIR / "lineage"),
        _run_hash_mismatch(PROOF_DIR / "hash"),
    ]
    receipt = {
        "schema": "tau.issue_292_live_readback.v1",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "cases": cases,
        "status": "PASS" if all(case["ok"] for case in cases) else "FAIL",
    }
    ARTIFACT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if receipt["status"] != "PASS":
        raise SystemExit(json.dumps(receipt, indent=2, sort_keys=True))
    print(f"issue-292 live readback PASS: {ARTIFACT}")


def _run_lineage_mismatch(run_dir: Path) -> dict[str, Any]:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-292-lineage",
            "run_dir": str(run_dir),
            "nodes": [
                _node(run_dir, "producer_a"),
                _node(run_dir, "producer_b"),
                _node(
                    run_dir,
                    "consumer",
                    depends_on=["producer_a", "producer_b"],
                    accepted_context_from=["producer_a"],
                ),
            ],
        },
        source_path=run_dir / "dag.json",
    )
    b_to_consumer = next(
        edge
        for edge in plan.control_edges
        if edge.source_node_id == "producer_b" and edge.target_id == "consumer"
    )
    invalid = replace(
        plan,
        context_bindings=(
            replace(plan.context_bindings[0], control_edge_id=b_to_consumer.edge_id),
        ),
    ).with_computed_hash()
    calls: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        calls.append(node.node_id)
        return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}

    result = run_dag_plan(invalid, execute_node=execute)
    return {
        "name": "lineage_mismatch",
        "ok": result.status == "BLOCKED"
        and result.verdict == "dag_context_binding_edge_mismatch"
        and calls == [],
        "status": result.status,
        "verdict": result.verdict,
        "calls": calls,
    }


def _run_hash_mismatch(run_dir: Path) -> dict[str, Any]:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-292-hash",
            "run_dir": str(run_dir),
            "nodes": [
                _node(run_dir, "producer"),
                _node(
                    run_dir,
                    "consumer",
                    depends_on=["producer"],
                    accepted_context_from=["producer"],
                ),
            ],
        },
        source_path=run_dir / "dag.json",
    )
    calls: list[str] = []
    computed = canonical_sha256({"schema": "source.output.v1", "value": "tampered"})

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
            "verdict": "PASS",
            "accepted_output": {
                "schema": "source.output.v1",
                "value": "tampered",
                "sha256": "sha256:" + "0" * 64,
            },
        }

    result = run_dag_plan(plan, execute_node=execute)
    return {
        "name": "declared_hash_mismatch",
        "ok": result.status == "BLOCKED"
        and result.verdict == "NODE_INPUT_DECLARED_HASH_MISMATCH"
        and calls == ["producer"],
        "status": result.status,
        "verdict": result.verdict,
        "calls": calls,
        "computed_sha256": computed,
    }


def _node(
    run_dir: Path,
    node_id: str,
    *,
    depends_on: list[str] | None = None,
    accepted_context_from: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": (
            accepted_context_from if accepted_context_from is not None else depends_on or []
        ),
        "receipt_path": str(run_dir / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


if __name__ == "__main__":
    main()
