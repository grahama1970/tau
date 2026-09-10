#!/usr/bin/env python3
"""Proof for Tau#347: strict DAG attempt-result and output-contract admission."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tau_coding.dag_runtime.attempt_result import (
    OUTPUT_CONTRACT_ANY_OBJECT,
    OUTPUT_CONTRACT_NONE,
    OUTPUT_CONTRACT_SOURCE_NODE,
    DagAttemptResultAdmissionError,
    admit_dag_attempt_result,
)
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import DagAttemptIdentity
from tau_coding.dag_runtime.scheduler import run_dag_plan


def _run(cmd: list[str], *, cwd: Path, timeout: int = 180) -> dict[str, Any]:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _identity(node_id: str = "producer") -> DagAttemptIdentity:
    return DagAttemptIdentity(
        run_id="eval-run",
        node_id=node_id,
        attempt=1,
        attempt_id=f"attempt-{node_id}-1",
        idempotency_key=f"idem-{node_id}-1",
        recovered=False,
    )


def _admit(result: dict[str, Any], contract: str = OUTPUT_CONTRACT_ANY_OBJECT) -> dict[str, Any]:
    return admit_dag_attempt_result(
        plan_sha256="sha256:" + "1" * 64,
        identity=_identity(str(result.get("node_id") or "producer")),
        node_id=str(result.get("node_id") or "producer"),
        result=result,
        output_contract_id=contract,
    ).normalized


def _rejects(name: str, result: dict[str, Any], contract: str, code: str) -> dict[str, Any]:
    try:
        _admit(result, contract)
    except DagAttemptResultAdmissionError as exc:
        return {"name": name, "code": exc.code, "path": exc.path, "passed": exc.code == code}
    return {"name": name, "code": "ADMITTED", "path": None, "passed": False}


def _scheduler_blocks_invalid_successor(tmp_path: Path) -> dict[str, Any]:
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "attempt-result-contract-eval",
        "run_dir": str(tmp_path / "run"),
        "nodes": [
            {
                "node_id": "producer",
                "role": "producer",
                "command": ["python", "-c", "print('producer')"],
                "receipt_path": str(tmp_path / "producer.json"),
                "extensions": {"output_contract_id": OUTPUT_CONTRACT_SOURCE_NODE},
            },
            {
                "node_id": "consumer",
                "role": "consumer",
                "command": ["python", "-c", "print('consumer')"],
                "receipt_path": str(tmp_path / "consumer.json"),
                "depends_on": ["producer"],
            },
        ],
    }
    plan = compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")
    called: list[str] = []

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs, attempt
        called.append(node.node_id)
        if node.node_id == "producer":
            return {
                "node_id": "producer",
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"source_node_id": "producer", "extra": "forbidden"},
            }
        return {
            "node_id": "consumer",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": "consumer"},
        }

    result = run_dag_plan(plan, execute_node=execute)
    producer = result.node_results[0] if result.node_results else {}
    return {
        "status": result.status,
        "verdict": result.verdict,
        "called": called,
        "completed_node_ids": list(result.completed_node_ids),
        "producer_status": producer.get("status"),
        "producer_alert_codes": producer.get("alert_codes", []),
        "producer_accepted_output": producer.get("accepted_output"),
        "passed": (
            result.status == "BLOCKED"
            and called == ["producer"]
            and result.completed_node_ids == ()
            and producer.get("accepted_output") is None
            and "dag_attempt_result_output_schema_invalid" in producer.get("alert_codes", [])
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    pytest_result = _run(
        [
            "uv",
            "run",
            "pytest",
            "tests/test_dag_attempt_result.py",
            "tests/test_dag_runtime_scheduler.py",
            "tests/test_dag_runtime_run_store.py::test_scheduler_settles_malformed_adapter_result_and_replays_block",
            "tests/test_dag_runtime_replay.py::test_blocked_replay_preserves_earlier_completed_nodes",
            "tests/test_dag_viewer_server.py::test_server_is_loopback_read_only_and_serves_declared_contracts",
            "tests/test_project_dag.py::test_ready_queue_allows_required_reviewer_all_success_join_policy",
            "tests/test_project_dag.py::test_ready_queue_blocks_failed_referenced_receipt_verdict",
            "tests/test_project_dag.py::test_project_dag_durable_replay_preserves_receipt_evidence",
            "tests/test_project_dag.py::test_project_dag_bounded_ready_queue_runs_independent_nodes_concurrently",
            "tests/test_project_dag.py::test_project_dag_ready_queue_blocks_pointless_unit_test_drift",
            "--tau-suite=all",
            "-q",
        ],
        cwd=repo,
    )

    diagnostics = _admit(
        {
            "node_id": "producer",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"ok": True},
            "diagnostics": {"adapter": {"note": "retained"}},
            "extensions": {"adapter": {"debug_id": "abc"}},
        }
    )
    valid_source = _admit(
        {
            "node_id": "producer",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": "producer"},
        },
        OUTPUT_CONTRACT_SOURCE_NODE,
    )
    none_valid = _admit(
        {"node_id": "producer", "status": "PASS", "verdict": "PASS"},
        OUTPUT_CONTRACT_NONE,
    )
    malicious_schema = _rejects(
        "producer_schema_substitution",
        {
            "node_id": "producer",
            "status": "PASS",
            "verdict": "PASS",
            "output_contract_id": OUTPUT_CONTRACT_ANY_OBJECT,
            "accepted_output": {"source_node_id": "producer", "extra": "forbidden"},
        },
        OUTPUT_CONTRACT_SOURCE_NODE,
        "dag_attempt_result_output_contract_mismatch",
    )
    invalid_cases = [
        _rejects(
            "unknown_top_level",
            {
                "node_id": "producer",
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {},
                "unexpected": True,
            },
            OUTPUT_CONTRACT_ANY_OBJECT,
            "dag_attempt_result_unknown_field",
        ),
        _rejects(
            "wrong_accepted_output_type",
            {"node_id": "producer", "status": "PASS", "verdict": "PASS", "accepted_output": []},
            OUTPUT_CONTRACT_ANY_OBJECT,
            "dag_attempt_result_accepted_output_invalid",
        ),
        _rejects(
            "missing_required_output",
            {"node_id": "producer", "status": "PASS", "verdict": "PASS"},
            OUTPUT_CONTRACT_SOURCE_NODE,
            "dag_attempt_result_output_required",
        ),
        _rejects(
            "source_node_extra_field",
            {
                "node_id": "producer",
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"source_node_id": "producer", "extra": True},
            },
            OUTPUT_CONTRACT_SOURCE_NODE,
            "dag_attempt_result_output_schema_invalid",
        ),
        _rejects(
            "forbidden_output",
            {
                "node_id": "producer",
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"x": 1},
            },
            OUTPUT_CONTRACT_NONE,
            "dag_attempt_result_output_forbidden",
        ),
        _rejects(
            "wrong_schema_id",
            {
                "node_id": "producer",
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"x": 1},
            },
            "tau.accepted_output.unknown.v1",
            "dag_attempt_result_output_contract_unknown",
        ),
        _rejects(
            "noncanonical_json",
            {
                "node_id": "producer",
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"score": math.inf},
            },
            OUTPUT_CONTRACT_ANY_OBJECT,
            "dag_attempt_result_non_canonical_json",
        ),
        malicious_schema,
    ]

    with TemporaryDirectory() as tmp:
        scheduler_block = _scheduler_blocks_invalid_successor(Path(tmp))

    checks = {
        "pytest_contract_tests_passed": pytest_result["exit_code"] == 0,
        "unknown_top_level_rejected": invalid_cases[0]["passed"],
        "diagnostics_extensions_retained": diagnostics["diagnostics"]["adapter"]["note"]
        == "retained"
        and diagnostics["extensions"]["adapter"]["debug_id"] == "abc"
        and diagnostics["status"] == "PASS"
        and diagnostics["verdict"] == "PASS",
        "valid_output_hash_bound": valid_source["output_contract_id"] == OUTPUT_CONTRACT_SOURCE_NODE
        and str(valid_source["accepted_output_sha256"]).startswith("sha256:"),
        "none_contract_forbids_payload_but_allows_absence": none_valid["output_contract_id"]
        == OUTPUT_CONTRACT_NONE
        and none_valid["accepted_output"] is None,
        "invalid_outputs_fail_closed": all(item["passed"] for item in invalid_cases),
        "malicious_schema_substitution_rejected": malicious_schema["passed"],
        "scheduler_blocks_before_successor": scheduler_block["passed"],
    }
    proof = {
        "schema": "tau.attempt_result_contract_proof.v1",
        "issue": 347,
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "ok": all(checks.values()),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "github_live": False,
        "repo": str(repo),
        "checks": checks,
        "invalid_cases": invalid_cases,
        "scheduler_block": scheduler_block,
        "commands": {"pytest": pytest_result},
        "proof_boundary": {
            "proves": (
                "Tau's node-to-scheduler attempt-result boundary forbids unknown top-level "
                "fields, retains only explicit diagnostics/extensions, binds accepted output "
                "to trusted output contracts and hashes, revalidates run-store/replay "
                "paths through focused tests, and blocks successor activation for invalid "
                "producer output."
            ),
            "does_not_prove": (
                "Semantic model quality, external provider availability, GitHub closure, "
                "or full developer readiness."
            ),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
