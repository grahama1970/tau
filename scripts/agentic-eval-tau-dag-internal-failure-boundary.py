#!/usr/bin/env python3
"""Retained eval for Tau DAG internal failure boundary triage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import tau_coding.dag_runtime.scheduler as scheduler
from tau_coding.dag_runtime.attempt_result import admit_dag_attempt_result
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import DagAttemptIdentity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    classifications: list[dict[str, Any]] = []

    def classify(signal: str, *, layer: str) -> dict[str, Any]:
        record = {
            "code": "tau_unclassified_eval0001",
            "layer": layer,
            "cause": signal,
            "next_command": "skills/triage-error/run.sh triage --text <signal> --layer tau",
            "ambiguous": True,
        }
        classifications.append(record)
        return record

    scheduler.classify_tau_failure = classify

    plan = compile_generic_dag_plan(_generic_spec(work), source_path=work / "dag.json")

    def execute(node, accepted_inputs, execution):  # type: ignore[no-untyped-def]
        del node, accepted_inputs, execution
        return {"node_id": "producer", "status": "PASS", "verdict": "FAIL"}

    result = scheduler.run_dag_plan(plan, execute_node=execute)
    node_result = result.node_results[0]

    identity = DagAttemptIdentity(
        run_id="eval-run",
        node_id="producer",
        attempt=1,
        attempt_id="attempt-1",
        idempotency_key="idem-1",
    )
    admission = admit_dag_attempt_result(
        plan_sha256="sha256:" + "5" * 64,
        identity=identity,
        node_id="producer",
        result={
            "node_id": "producer",
            "status": "BLOCKED",
            "verdict": "tau_unclassified_eval0001",
            "errors": ["classified"],
        },
    )

    attempt_result_source = Path("src/tau_coding/dag_runtime/attempt_result.py").read_text()
    checks = {
        "scheduler_blocked": result.status == "BLOCKED",
        "triage_verdict_used": result.verdict == "tau_unclassified_eval0001",
        "failure_schema_present": node_result.get("failure", {}).get("schema")
        == "tau.internal_failure.v1",
        "triage_next_command_present": bool(
            node_result.get("failure", {}).get("triage", {}).get("next_command")
        ),
        "pydantic_model_present": "class DagAttemptResultModel(BaseModel)" in attempt_result_source,
        "regex_compile_absent": "re.compile" not in attempt_result_source,
        "triage_code_admitted": admission.normalized["verdict"] == "tau_unclassified_eval0001",
    }
    proof = {
        "schema": "tau.dag_internal_failure_boundary_proof.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "result_status": result.status,
        "result_verdict": result.verdict,
        "node_result": node_result,
        "classifications": classifications,
        "proof_boundary": {
            "live_provider": False,
            "mocked": ["triage-error classifier response", "DAG node adapter"],
            "real": [
                "Tau generic DAG compiler",
                "Tau scheduler",
                "Pydantic attempt-result admission",
            ],
        },
    }
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["status"] == "PASS" else 1


def _generic_spec(work: Path) -> dict[str, object]:
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "dag-internal-failure-boundary-eval",
        "run_dir": str(work / "run"),
        "nodes": [
            {
                "node_id": "producer",
                "role": "producer",
                "command": ["true"],
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(work / "receipts" / "producer.json"),
                "timeout_seconds": 1,
                "max_attempts": 1,
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
