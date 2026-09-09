#!/usr/bin/env python3
"""Proof for Tau#346: triage classifications are closed typed repair contracts."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.triage_error_bridge import (
    TRIAGE_CLASSIFICATION_SCHEMA,
    TRIAGE_CONTRACT_INVALID_CODE,
    TRIAGE_REPAIR_ARGS_SCHEMA,
    admit_triage_classification,
)


def _run(cmd: list[str], *, cwd: Path, timeout: int = 120) -> dict[str, Any]:
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


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": TRIAGE_CLASSIFICATION_SCHEMA,
        "code": "tau_project_dag_missing_required_evidence",
        "layer": "dag-runtime",
        "cause": "DAG node failed because required evidence was missing.",
        "repair_family": "result_contract_invalid",
        "disposition": "KNOWN_REPAIR",
        "repair_handler_id": "scheduler.correction_handler",
        "repair_args_schema": TRIAGE_REPAIR_ARGS_SCHEMA,
        "repair_args": {
            "strategy": "same_semantic_node_rerun",
            "repair_family": "result_contract_invalid",
            "classification_code": "tau_project_dag_missing_required_evidence",
        },
        "requires_human": False,
        "diagnostics": {"source": "agentic-eval"},
    }
    payload.update(overrides)
    return payload


def _invalid_case(name: str, **overrides: Any) -> dict[str, Any]:
    result = admit_triage_classification(_valid_payload(**overrides), layer="scheduler")
    return {
        "name": name,
        "code": result.get("code"),
        "disposition": result.get("disposition"),
        "requires_human": result.get("requires_human"),
        "repair_handler_present": "repair_handler_id" in result,
        "next_command_present": "next_command" in result,
        "passed": (
            result.get("code") == TRIAGE_CONTRACT_INVALID_CODE
            and result.get("disposition") == "CONTRACT_INVALID"
            and result.get("requires_human") is True
            and "repair_handler_id" not in result
            and "next_command" not in result
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
            "tests/test_triage_error_bridge.py",
            "tests/test_dag_runtime_scheduler.py::test_scheduler_blocks_downstream_after_malformed_attempt_result",
            "tests/test_dag_runtime_scheduler.py::test_scheduler_triages_adapter_exception",
            "--tau-suite=all",
            "-q",
        ],
        cwd=repo,
    )

    valid = admit_triage_classification(_valid_payload(), layer="scheduler")
    invalid_cases = [
        _invalid_case("space_code", code="bad code"),
        _invalid_case("slash_code", code="bad/slash"),
        _invalid_case("unicode_code", code="unicodé"),
        _invalid_case("oversized_code", code="x" * 129),
        _invalid_case("unknown_top_level", unexpected="nope"),
        _invalid_case("unknown_layer", layer="unknown"),
        _invalid_case("legacy_next_command", next_command="rm -rf /"),
        _invalid_case("legacy_ambiguous", ambiguous=True),
        _invalid_case("unknown_handler", repair_handler_id="shell.exec"),
        _invalid_case(
            "extra_repair_arg",
            repair_args={
                "strategy": "same_semantic_node_rerun",
                "repair_family": "result_contract_invalid",
                "classification_code": "tau_project_dag_missing_required_evidence",
                "extra": "nope",
            },
        ),
        _invalid_case(
            "nonfinite_repair_arg",
            repair_args={
                "strategy": "same_semantic_node_rerun",
                "repair_family": "result_contract_invalid",
                "classification_code": "tau_project_dag_missing_required_evidence",
                "score": math.inf,
            },
        ),
        _invalid_case(
            "shell_repair_arg",
            repair_args={
                "strategy": "same_semantic_node_rerun",
                "repair_family": "result_contract_invalid",
                "classification_code": "tau_project_dag_missing_required_evidence",
                "payload": "echo ok && rm -rf /",
            },
        ),
    ]

    checks = {
        "pytest_contract_tests_passed": pytest_result["exit_code"] == 0,
        "valid_known_repair_admitted": (
            valid.get("code") == "tau_project_dag_missing_required_evidence"
            and valid.get("repair_handler_id") == "scheduler.correction_handler"
            and valid.get("repair_args_sha256", "").startswith("sha256:")
            and "next_command" not in valid
        ),
        "invalid_payloads_fail_closed": all(case["passed"] for case in invalid_cases),
    }
    proof = {
        "schema": "tau.triage_contract_proof.v1",
        "issue": 346,
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "ok": all(checks.values()),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "github_live": False,
        "repo": str(repo),
        "checks": checks,
        "valid_known_repair": valid,
        "invalid_cases": invalid_cases,
        "commands": {"pytest": pytest_result},
        "proof_boundary": {
            "proves": "Tau admits only closed versioned triage classifications, rejects legacy next_command/ambiguous fields, refuses malformed codes/layers/repair handlers/repair args, and scheduler tests emit admissible triage_contract_invalid blocked attempts for legacy classifier output.",
            "does_not_prove": "External triage-error catalog quality, provider/model behavior, or human acceptance.",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
