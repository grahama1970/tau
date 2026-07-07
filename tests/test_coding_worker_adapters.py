import hashlib
import json
import socket
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.coding_worker_adapters import (
    OMP_WORKER_DOCTOR_RECEIPT_SCHEMA,
    OMP_WORKER_LAUNCH_RECEIPT_SCHEMA,
    OMP_WORKER_RECEIPT_SCHEMA,
    SCILLM_WORKER_LAUNCH_RECEIPT_SCHEMA,
    SCILLM_WORKER_RECEIPT_SCHEMA,
    write_omp_worker_doctor_receipt,
    write_omp_worker_launch_receipt,
    write_omp_worker_receipt,
    write_scillm_worker_launch_receipt,
    write_scillm_worker_receipt,
)


def test_omp_worker_blocks_missing_result(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=tmp_path / "repo" / "worker-result.json",
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "worker_result_missing" in payload["alert_codes"]
    assert "invalid_result_schema" in payload["alert_codes"]
    assert payload["course_correction"]["schema"] == "tau.course_correction.v1"
    assert payload["course_correction"]["trigger"] == "worker_result_missing"
    assert payload["course_correction"]["required_next_action"] == (
        "retry_node_or_route_goal_guardian"
    )
    assert payload["course_correction_artifacts"] == [payload["course_correction_path"]]
    assert Path(payload["course_correction_path"]).exists()


def test_omp_worker_blocks_goal_hash_mismatch(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        goal_hash="sha256:other",
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "goal_hash_mismatch" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "goal_hash_mismatch"
    assert payload["course_correction"]["required_next_action"] == "route_goal_guardian"
    assert Path(payload["course_correction_path"]).exists()


def test_scillm_worker_blocks_dollar_schema_key(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.scillm_worker.v1")
    result = _write_result(tmp_path, schema="tau.scillm_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["$schema"] = payload.pop("schema")
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_scillm_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "invalid_result_schema" in receipt["alert_codes"]
    assert "result_schema_key_misspelled" in receipt["alert_codes"]


def test_omp_worker_blocks_disallowed_changed_file(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        changed_files=["secrets/token.txt"],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "disallowed_changed_file" in payload["alert_codes"]
    assert payload["course_correction"]["schema"] == "tau.course_correction.v1"
    assert payload["course_correction"]["trigger"] == "worker_changed_forbidden_path"
    assert payload["course_correction"]["required_next_action"] == "route_goal_guardian"
    assert payload["course_correction_artifacts"] == [payload["course_correction_path"]]
    assert Path(payload["course_correction_path"]).exists()


def test_worker_blocks_malformed_allowed_paths(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    work_order_payload["allowed_paths"] = "src/**"
    work_order.write_text(
        json.dumps(work_order_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_allowed_paths" in payload["alert_codes"]


def test_worker_blocks_malformed_forbidden_paths(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    work_order_payload["forbidden_paths"] = "secrets/**"
    work_order.write_text(
        json.dumps(work_order_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_forbidden_paths" in payload["alert_codes"]


def test_worker_blocks_result_path_outside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    work_order_payload["result_path"] = str(tmp_path / "outside" / "worker-result.json")
    work_order.write_text(
        json.dumps(work_order_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "worker_result_path_outside_repo" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "worker_result_missing"


def test_worker_blocks_result_argument_outside_repo_before_reading(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    outside = tmp_path / "outside" / "worker-result.json"
    outside.parent.mkdir()
    outside.write_text(
        json.dumps(
            {
                "schema": "tau.omp_worker_result.v1",
                "status": "NEEDS_REVIEW",
                "goal_hash": "sha256:goal",
                "changed_files": ["src/example.py"],
                "artifacts": [],
                "tests_run": [],
                "findings": [],
                "next_recommended_route": "reviewer",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=outside,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "worker_result_argument_outside_repo" in payload["alert_codes"]
    assert "invalid_result_schema" in payload["alert_codes"]
    assert payload["result_sha256"] is None
    assert payload["result_bytes"] is None
    assert payload["validated_artifacts"][1]["admissible"] is False
    assert payload["course_correction"]["trigger"] == "worker_result_missing"


def test_worker_blocks_result_argument_mismatch_before_reading(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    mismatched = repo / "other-result.json"
    mismatched.write_text(
        json.dumps(
            {
                "schema": "tau.omp_worker_result.v1",
                "status": "NEEDS_REVIEW",
                "goal_hash": "sha256:goal",
                "changed_files": ["src/example.py"],
                "artifacts": [],
                "tests_run": [],
                "findings": [],
                "next_recommended_route": "reviewer",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=mismatched,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "worker_result_path_mismatch" in payload["alert_codes"]
    assert "invalid_result_schema" in payload["alert_codes"]
    assert payload["result_sha256"] is None
    assert payload["result_bytes"] is None
    assert payload["validated_artifacts"][1]["admissible"] is False
    assert payload["course_correction"]["trigger"] == "worker_result_missing"


def test_worker_blocks_receipt_path_outside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    work_order_payload["receipt_path"] = str(tmp_path / "outside" / "worker-receipt.json")
    work_order.write_text(
        json.dumps(work_order_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "worker_receipt_path_outside_repo" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "invalid_receipt"


def test_worker_accepts_absolute_changed_file_inside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    changed = repo / "src" / "example.py"
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        changed_files=[str(changed)],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["course_correction"] is None
    assert payload["course_correction_path"] is None
    assert payload["course_correction_artifacts"] == []
    assert payload["changed_files"] == [str(changed)]
    assert payload["normalized_changed_files"] == ["src/example.py"]


def test_worker_blocks_absolute_changed_file_outside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    outside = tmp_path / "outside" / "src" / "example.py"
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        changed_files=[str(outside)],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "changed_file_outside_repo" in payload["alert_codes"]
    assert "disallowed_changed_file" in payload["alert_codes"]
    assert payload["normalized_changed_files"] == [str(outside)]


def test_omp_worker_accepts_schema_valid_result_and_routes_reviewer(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["schema"] == OMP_WORKER_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["next_recommended_route"] == "reviewer"
    assert payload["work_order_sha256"] == f"sha256:{_sha256(work_order)}"
    assert payload["work_order_bytes"] == work_order.stat().st_size
    assert payload["result_sha256"] == f"sha256:{_sha256(result)}"
    assert payload["result_bytes"] == result.stat().st_size
    assert payload["validated_artifacts"] == [
        {
            "label": "work_order",
            "path": str(work_order.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(work_order)}",
            "bytes": work_order.stat().st_size,
        },
        {
            "label": "worker_result",
            "path": str(result.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(result)}",
            "bytes": result.stat().st_size,
        },
    ]
    assert payload["substrate_receipts"] == [
        {
            "label": "sandbox_receipt",
            "path": str(sandbox_receipt.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(sandbox_receipt)}",
            "bytes": sandbox_receipt.stat().st_size,
            "schema": "tau.sandbox_run_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": False,
        }
    ]


def test_worker_blocks_tests_passed_without_logs(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        tests_run=[{"name": "pytest", "status": "PASS"}],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "tests_passed_without_logs" in payload["alert_codes"]


def test_worker_blocks_prose_only_result(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        changed_files=[],
        artifacts=[],
        tests_run=[],
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["assistant_text"] = "I changed the code and everything looks good."
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "prose_only_result" in receipt["alert_codes"]


def test_worker_blocks_public_github_mutation_without_policy_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {"target": "github:grahama1970/tau#67", "action": "comment"}
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "github_mutation_requires_policy" in receipt["alert_codes"]


def test_worker_accepts_public_github_mutation_with_policy_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    policy_receipt = repo / "receipts" / "github-apply-policy.json"
    _write_reference_receipt(
        policy_receipt,
        schema="tau.github_apply_policy_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=False,
        extra={
            "target": {"repo": "grahama1970/tau", "target": "issue#67"},
            "actions": ["comment"],
            "requirements": _github_policy_requirements(),
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {
            "target": "github:grahama1970/tau#67",
            "action": "comment",
            "github_apply_policy_receipt": "receipts/github-apply-policy.json",
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["side_effect_receipts"] == [
        {
            "label": "github_apply_policy_receipt",
            "path": str(policy_receipt.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(policy_receipt)}",
            "bytes": policy_receipt.stat().st_size,
            "schema": "tau.github_apply_policy_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": False,
            "provider_live": False,
            "receipt_target": {"repo": "grahama1970/tau", "target": "issue#67"},
            "receipt_actions": ["comment"],
            "receipt_requirements": _github_policy_requirements(),
            "mutation_index": 0,
            "target": "github:grahama1970/tau#67",
            "action": "comment",
        }
    ]


def test_worker_blocks_public_github_mutation_with_non_pass_policy_receipt(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    policy_receipt = repo / "receipts" / "github-apply-policy.json"
    _write_reference_receipt(
        policy_receipt,
        schema="tau.github_apply_policy_receipt.v1",
        status="BLOCKED",
        ok=False,
        mocked=False,
        live=False,
        extra={
            "target": {"repo": "grahama1970/tau", "target": "issue#67"},
            "actions": ["comment"],
            "requirements": _github_policy_requirements(),
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {
            "target": "github:grahama1970/tau#67",
            "action": "comment",
            "github_apply_policy_receipt": "receipts/github-apply-policy.json",
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "github_apply_policy_receipt_not_pass" in receipt["alert_codes"]
    assert receipt["side_effect_receipts"][0]["status"] == "BLOCKED"


def test_worker_blocks_public_github_mutation_policy_target_mismatch(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    policy_receipt = repo / "receipts" / "github-apply-policy.json"
    _write_reference_receipt(
        policy_receipt,
        schema="tau.github_apply_policy_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=False,
        extra={
            "target": {"repo": "grahama1970/tau", "target": "issue#999"},
            "actions": ["comment"],
            "requirements": _github_policy_requirements(),
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {
            "target": "github:grahama1970/tau#67",
            "action": "comment",
            "github_apply_policy_receipt": "receipts/github-apply-policy.json",
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "github_apply_policy_receipt_target_mismatch" in receipt["alert_codes"]
    assert receipt["side_effect_receipts"][0]["receipt_target"] == {
        "repo": "grahama1970/tau",
        "target": "issue#999",
    }


def test_worker_blocks_public_github_mutation_policy_action_mismatch(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    policy_receipt = repo / "receipts" / "github-apply-policy.json"
    _write_reference_receipt(
        policy_receipt,
        schema="tau.github_apply_policy_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=False,
        extra={
            "target": {"repo": "grahama1970/tau", "target": "issue#67"},
            "actions": ["label"],
            "requirements": _github_policy_requirements(),
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {
            "target": "github:grahama1970/tau#67",
            "action": "comment",
            "github_apply_policy_receipt": "receipts/github-apply-policy.json",
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "github_apply_policy_receipt_action_mismatch" in receipt["alert_codes"]
    assert receipt["side_effect_receipts"][0]["receipt_actions"] == ["label"]


def test_worker_blocks_public_github_mutation_policy_without_requirements(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    policy_receipt = repo / "receipts" / "github-apply-policy.json"
    _write_reference_receipt(
        policy_receipt,
        schema="tau.github_apply_policy_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=False,
        extra={
            "target": {"repo": "grahama1970/tau", "target": "issue#67"},
            "actions": ["comment"],
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {
            "target": "github:grahama1970/tau#67",
            "action": "comment",
            "github_apply_policy_receipt": "receipts/github-apply-policy.json",
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "github_apply_policy_receipt_requirements_invalid" in receipt["alert_codes"]


def test_worker_blocks_public_github_comment_policy_without_redaction_requirement(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    policy_receipt = repo / "receipts" / "github-apply-policy.json"
    _write_reference_receipt(
        policy_receipt,
        schema="tau.github_apply_policy_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=False,
        extra={
            "target": {"repo": "grahama1970/tau", "target": "issue#67"},
            "actions": ["comment"],
            "requirements": {
                "approval_packet": True,
                "preflight": True,
                "redaction": False,
            },
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {
            "target": "github:grahama1970/tau#67",
            "action": "comment",
            "github_apply_policy_receipt": "receipts/github-apply-policy.json",
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert (
        "github_apply_policy_receipt_missing_redaction_requirement"
        in receipt["alert_codes"]
    )


def test_worker_blocks_public_github_mutation_policy_without_approval_or_preflight(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    policy_receipt = repo / "receipts" / "github-apply-policy.json"
    _write_reference_receipt(
        policy_receipt,
        schema="tau.github_apply_policy_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=False,
        extra={
            "target": {"repo": "grahama1970/tau", "target": "issue#67"},
            "actions": ["label"],
            "requirements": {
                "approval_packet": False,
                "preflight": False,
                "redaction": True,
            },
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["requested_mutations"] = [
        {
            "target": "github:grahama1970/tau#67",
            "action": "label",
            "github_apply_policy_receipt": "receipts/github-apply-policy.json",
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert (
        "github_apply_policy_receipt_missing_approval_requirement"
        in receipt["alert_codes"]
    )
    assert (
        "github_apply_policy_receipt_missing_preflight_requirement"
        in receipt["alert_codes"]
    )


def test_worker_blocks_external_research_without_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "external_research_requires_receipt" in receipt["alert_codes"]


def test_worker_accepts_external_research_with_query_safety_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    research_receipt = repo / "receipts" / "research-query-safety.json"
    _write_reference_receipt(
        research_receipt,
        schema="tau.research_query_safety_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=True,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    payload["research_query_safety_receipt"] = "receipts/research-query-safety.json"
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["research_receipts"] == [
        {
            "label": "research_query_safety_receipt",
            "path": str(research_receipt.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(research_receipt)}",
            "bytes": research_receipt.stat().st_size,
            "schema": "tau.research_query_safety_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": False,
        }
    ]


def test_worker_legacy_accepts_external_research_with_source_receipt(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    research_receipt = repo / "receipts" / "research-source.json"
    _write_reference_receipt(
        research_receipt,
        schema="tau.research_source_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=True,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    payload["research_source_receipt"] = "receipts/research-source.json"
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["research_receipts"][0]["label"] == "research_source_receipt"
    assert receipt["research_receipts"][0]["schema"] == "tau.research_source_receipt.v1"


def test_high_stakes_worker_blocks_external_research_without_query_safety_receipt(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    research_receipt = repo / "receipts" / "research-source.json"
    _write_reference_receipt(
        research_receipt,
        schema="tau.research_source_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=True,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    payload["research_source_receipt"] = "receipts/research-source.json"
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "external_research_requires_query_safety_receipt" in receipt["alert_codes"]
    assert "external_research_denied_by_policy" in receipt["alert_codes"]
    assert "external_research_denied_by_data_boundary" in receipt["alert_codes"]
    assert receipt["research_receipts"][0]["label"] == "research_source_receipt"


def test_high_stakes_worker_accepts_research_query_receipt_bound_to_boundary(
    tmp_path: Path,
) -> None:
    policy = _policy_profile()
    policy["research"]["external_search"] = "allow_with_review"
    boundary = _data_boundary()
    boundary["external_research_allowed"] = True
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        policy_profile=policy,
        data_boundary=boundary,
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    research_receipt = repo / "receipts" / "research-query-safety.json"
    _write_reference_receipt(
        research_receipt,
        schema="tau.research_query_safety_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=True,
        extra={
            "policy_profile": {
                "schema": "tau.policy_profile.v1",
                "sha256": _inline_json_sha256(work_order_payload["policy_profile"]),
            },
            "data_boundary": {
                "schema": "tau.data_boundary.v1",
                "sha256": _inline_json_sha256(work_order_payload["data_boundary"]),
            },
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    payload["research_query_safety_receipt"] = "receipts/research-query-safety.json"
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["research_receipts"][0]["receipt_policy_profile"]["sha256"] == (
        _inline_json_sha256(work_order_payload["policy_profile"])
    )
    assert receipt["research_receipts"][0]["receipt_data_boundary"]["sha256"] == (
        _inline_json_sha256(work_order_payload["data_boundary"])
    )


def test_high_stakes_worker_blocks_research_query_receipt_without_boundary_hashes(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    research_receipt = repo / "receipts" / "research-query-safety.json"
    _write_reference_receipt(
        research_receipt,
        schema="tau.research_query_safety_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=True,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    payload["research_query_safety_receipt"] = "receipts/research-query-safety.json"
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "research_query_safety_policy_binding_missing" in receipt["alert_codes"]
    assert "research_query_safety_boundary_binding_missing" in receipt["alert_codes"]


def test_high_stakes_worker_blocks_research_query_receipt_boundary_mismatch(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    research_receipt = repo / "receipts" / "research-query-safety.json"
    _write_reference_receipt(
        research_receipt,
        schema="tau.research_query_safety_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=True,
        extra={
            "policy_profile": {
                "schema": "tau.policy_profile.v1",
                "sha256": _inline_json_sha256(work_order_payload["policy_profile"]),
            },
            "data_boundary": {
                "schema": "tau.data_boundary.v1",
                "sha256": "sha256:stale",
            },
        },
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    payload["research_query_safety_receipt"] = "receipts/research-query-safety.json"
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "research_query_safety_boundary_mismatch" in receipt["alert_codes"]
    assert "research_query_safety_policy_mismatch" not in receipt["alert_codes"]


def test_worker_blocks_external_research_receipt_outside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    outside_receipt = tmp_path / "outside" / "research-query-safety.json"
    _write_reference_receipt(
        outside_receipt,
        schema="tau.research_query_safety_receipt.v1",
        status="PASS",
        ok=True,
        mocked=False,
        live=True,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["external_research_used"] = True
    payload["research_query_safety_receipt"] = str(outside_receipt)
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "research_query_safety_receipt_outside_repo" in receipt["alert_codes"]
    assert receipt["research_receipts"] == []


def test_worker_records_test_log_artifact_descriptors(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    test_log = repo / "logs" / "pytest.log"
    test_log.parent.mkdir(parents=True, exist_ok=True)
    test_log.write_text("1 passed\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        tests_run=[
            {
                "name": "pytest",
                "status": "PASS",
                "log_path": "logs/pytest.log",
            }
        ],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["test_log_artifacts"] == [
        {
            "label": "test_log",
            "path": str(test_log.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(test_log)}",
            "bytes": test_log.stat().st_size,
            "test_index": 0,
            "test_name": "pytest",
            "test_status": "PASS",
            "artifact": "logs/pytest.log",
        }
    ]


def test_worker_accepts_live_descriptor_artifacts_and_test_results(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        required_artifacts=["logs/pytest.log"],
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    work_order_payload["allowed_paths"] = ["src/**", "tests/**", "logs/**"]
    work_order.write_text(
        json.dumps(work_order_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    repo = Path(work_order_payload["repo"])
    test_log = repo / "logs" / "pytest.log"
    test_log.parent.mkdir(parents=True, exist_ok=True)
    test_log.write_text("5 passed\n", encoding="utf-8")
    result = _write_result(tmp_path, schema="tau.scillm_worker_result.v1")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["result_artifacts"] = [
        {
            "artifact": "logs/pytest.log",
            "sha256": f"sha256:{_sha256(test_log)}",
            "bytes": test_log.stat().st_size,
            "path": str(test_log.resolve()),
        }
    ]
    payload["test_results"] = [
        {
            "test_name": "pytest",
            "test_status": "PASS",
            "artifact": "logs/pytest.log",
            "passed": 5,
            "failed": 0,
        }
    ]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_scillm_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["result_artifacts"] == ["logs/pytest.log"]
    assert receipt["required_artifact_descriptors"] == [
        {
            "label": "required_artifact",
            "path": str(test_log.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(test_log)}",
            "bytes": test_log.stat().st_size,
            "artifact": "logs/pytest.log",
        }
    ]
    assert receipt["test_log_artifacts"] == [
        {
            "label": "test_log",
            "path": str(test_log.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(test_log)}",
            "bytes": test_log.stat().st_size,
            "test_index": 0,
            "test_name": "pytest",
            "test_status": "PASS",
            "artifact": "logs/pytest.log",
        }
    ]


def test_worker_blocks_claimed_required_artifact_that_does_not_exist(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        required_artifacts=["receipts/debug-session-receipt.json"],
    )
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=["receipts/debug-session-receipt.json"],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_required_artifact" in payload["alert_codes"]


def test_worker_records_required_artifact_descriptors(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        required_artifacts=["tests/pytest.log"],
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    log = repo / "tests" / "pytest.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("1 passed\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=["tests/pytest.log"],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["required_artifact_descriptors"] == [
        {
            "label": "required_artifact",
            "path": str(log.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(log)}",
            "bytes": log.stat().st_size,
            "artifact": "tests/pytest.log",
        }
    ]
    assert payload["result_artifact_descriptors"] == [
        {
            "label": "result_artifact",
            "path": str(log.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(log)}",
            "bytes": log.stat().st_size,
            "artifact": "tests/pytest.log",
        }
    ]


def test_worker_blocks_missing_result_artifact(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=["tests/missing-artifact.log"],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_result_artifact" in payload["alert_codes"]
    assert payload["result_artifact_descriptors"] == []


def test_worker_blocks_result_artifact_outside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    outside = tmp_path / "outside" / "worker-artifact.log"
    outside.parent.mkdir()
    outside.write_text("outside evidence\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=[str(outside)],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "artifact_outside_repo" in payload["alert_codes"]
    assert "missing_result_artifact" in payload["alert_codes"]
    assert payload["result_artifact_descriptors"] == []


def test_worker_blocks_result_artifact_outside_allowed_paths(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    artifact = repo / "docs" / "review.md"
    artifact.parent.mkdir()
    artifact.write_text("review evidence\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=["docs/review.md"],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "disallowed_result_artifact" in payload["alert_codes"]
    assert payload["normalized_result_artifacts"] == ["docs/review.md"]
    assert payload["result_artifact_descriptors"] == [
        {
            "label": "result_artifact",
            "path": str(artifact.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(artifact)}",
            "bytes": artifact.stat().st_size,
            "artifact": "docs/review.md",
        }
    ]


def test_worker_blocks_result_artifact_in_forbidden_path(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    artifact = repo / "secrets" / "debug.log"
    artifact.parent.mkdir()
    artifact.write_text("secret evidence\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=["secrets/debug.log"],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "disallowed_result_artifact" in payload["alert_codes"]
    assert payload["result_artifact_descriptors"][0]["path"] == str(artifact.resolve())


def test_worker_blocks_required_artifact_outside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        required_artifacts=[str(tmp_path / "outside" / "worker-artifact.log")],
    )
    outside = tmp_path / "outside" / "worker-artifact.log"
    outside.parent.mkdir()
    outside.write_text("outside evidence\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=[str(outside)],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "artifact_outside_repo" in payload["alert_codes"]
    assert "missing_required_artifact" in payload["alert_codes"]
    assert payload["required_artifact_descriptors"] == []


def test_worker_blocks_pass_test_log_outside_repo(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    outside = tmp_path / "outside" / "pytest.log"
    outside.parent.mkdir()
    outside.write_text("1 passed\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        tests_run=[
            {
                "name": "pytest",
                "status": "PASS",
                "log_path": str(outside),
            }
        ],
    )

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "test_log_outside_repo" in payload["alert_codes"]
    assert "tests_passed_without_logs" in payload["alert_codes"]
    assert payload["test_log_artifacts"] == []


def test_high_stakes_worker_requires_substrate(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate=None,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "substrate_required" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "receipt_timeout"
    assert payload["course_correction"]["required_next_action"] == (
        "retry_node_or_route_goal_guardian"
    )


def test_high_stakes_sandbox_worker_requires_sandbox_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        sandbox_receipt_path=None,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_required" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "receipt_timeout"
    assert payload["course_correction"]["required_evidence_before_retry"] == [
        "fresh_work_order",
        "node_receipt_or_timeout_diagnostics",
    ]


def test_high_stakes_sandbox_worker_blocks_non_pass_sandbox_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.sandbox_run_receipt.v1",
                "status": "BLOCKED",
                "ok": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_not_pass" in payload["alert_codes"]


def test_high_stakes_sandbox_worker_blocks_mocked_sandbox_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.sandbox_run_receipt.v1",
                "status": "PASS",
                "ok": True,
                "mocked": True,
                "live": True,
                "provider_live": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_mocked" in payload["alert_codes"]
    assert payload["substrate_receipts"][0]["mocked"] is True
    assert payload["course_correction"]["trigger"] == "receipt_timeout"


def test_high_stakes_sandbox_worker_blocks_non_live_sandbox_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.sandbox_run_receipt.v1",
                "status": "PASS",
                "ok": True,
                "mocked": False,
                "live": False,
                "provider_live": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_not_live" in payload["alert_codes"]
    assert payload["substrate_receipts"][0]["live"] is False


def test_high_stakes_sandbox_worker_blocks_goal_hash_mismatch(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.sandbox_run_receipt.v1",
                "status": "PASS",
                "ok": True,
                "mocked": False,
                "live": True,
                "provider_live": False,
                "goal_hash": "sha256:other-goal",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_goal_hash_mismatch" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "receipt_timeout"


def test_high_stakes_sandbox_worker_blocks_missing_goal_hash(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_payload = json.loads(sandbox_receipt.read_text(encoding="utf-8"))
    sandbox_payload.pop("goal_hash", None)
    sandbox_receipt.write_text(
        json.dumps(sandbox_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_missing_goal_hash" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "receipt_timeout"


def test_high_stakes_sandbox_worker_blocks_missing_work_order_sha256(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_payload = json.loads(sandbox_receipt.read_text(encoding="utf-8"))
    sandbox_payload.pop("work_order_sha256", None)
    sandbox_receipt.write_text(
        json.dumps(sandbox_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_missing_work_order_sha256" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "receipt_timeout"


def test_high_stakes_sandbox_worker_blocks_work_order_sha256_mismatch(
    tmp_path: Path,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_payload = json.loads(sandbox_receipt.read_text(encoding="utf-8"))
    sandbox_payload["work_order_sha256"] = "sha256:stale"
    sandbox_receipt.write_text(
        json.dumps(sandbox_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_work_order_sha256_mismatch" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "receipt_timeout"


def test_high_stakes_sandbox_worker_blocks_external_sandbox_receipt(
    tmp_path: Path,
) -> None:
    outside_receipt = tmp_path / "outside" / "sandbox-receipt.json"
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
        sandbox_receipt_path=str(outside_receipt),
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "sandbox_receipt_outside_repo" in payload["alert_codes"]
    assert payload["substrate_receipts"] == []
    assert payload["course_correction"]["trigger"] == "receipt_timeout"


def test_high_stakes_herdr_worker_requires_binding(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        herdr_binding=None,
        herdr_receipt_path="herdr-observation-gate.json",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_binding_required" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "herdr_stale"
    assert payload["course_correction"]["required_next_action"] == (
        "send_reminder_or_route_human"
    )


def test_high_stakes_herdr_worker_requires_receipt_path(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path=None,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_required" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "herdr_stale"
    assert "herdr_monitor_snapshot" in payload["course_correction"][
        "required_evidence_before_retry"
    ]


def test_high_stakes_herdr_worker_blocks_non_pass_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        herdr_binding=None,
        herdr_receipt_path="herdr-observation-gate.json",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    herdr_receipt = Path(work_order_payload["repo"]) / "herdr-observation-gate.json"
    herdr_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.herdr_observation_gate_receipt.v1",
                "status": "BLOCKED",
                "ok": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_not_pass" in payload["alert_codes"]


def test_high_stakes_herdr_worker_blocks_mocked_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path="herdr-observation-gate.json",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    herdr_receipt = Path(work_order_payload["repo"]) / "herdr-observation-gate.json"
    herdr_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.herdr_observation_gate_receipt.v1",
                "status": "PASS",
                "ok": True,
                "mocked": True,
                "live": True,
                "provider_live": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_mocked" in payload["alert_codes"]
    assert payload["substrate_receipts"][0]["mocked"] is True
    assert payload["course_correction"]["trigger"] == "herdr_stale"


def test_high_stakes_herdr_worker_blocks_goal_hash_mismatch(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path="herdr-observation-gate.json",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    herdr_receipt = Path(work_order_payload["repo"]) / "herdr-observation-gate.json"
    herdr_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.herdr_observation_gate_receipt.v1",
                "status": "PASS",
                "ok": True,
                "mocked": False,
                "live": True,
                "provider_live": False,
                "goal_hash": "sha256:other-goal",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_goal_hash_mismatch" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "herdr_stale"


def test_high_stakes_herdr_worker_blocks_missing_goal_hash(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path="herdr-observation-gate.json",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    herdr_receipt = Path(work_order_payload["repo"]) / "herdr-observation-gate.json"
    herdr_payload = json.loads(herdr_receipt.read_text(encoding="utf-8"))
    herdr_payload.pop("goal_hash", None)
    herdr_receipt.write_text(
        json.dumps(herdr_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_missing_goal_hash" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "herdr_stale"


def test_high_stakes_herdr_worker_blocks_work_order_sha256_mismatch(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path="herdr-observation-gate.json",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    herdr_receipt = Path(work_order_payload["repo"]) / "herdr-observation-gate.json"
    herdr_payload = json.loads(herdr_receipt.read_text(encoding="utf-8"))
    herdr_payload["work_order_sha256"] = "sha256:stale"
    herdr_receipt.write_text(
        json.dumps(herdr_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_work_order_sha256_mismatch" in payload["alert_codes"]
    assert payload["course_correction"]["trigger"] == "herdr_stale"


def test_high_stakes_herdr_worker_blocks_missing_receipt_path(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        herdr_receipt_path="missing-herdr-receipt.json",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    (Path(work_order_payload["repo"]) / "missing-herdr-receipt.json").unlink()
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_missing" in payload["alert_codes"]


def test_high_stakes_herdr_worker_blocks_external_receipt_path(tmp_path: Path) -> None:
    outside_receipt = tmp_path / "outside" / "herdr-observation-gate.json"
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path=str(outside_receipt),
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_receipt_outside_repo" in payload["alert_codes"]
    assert payload["substrate_receipts"] == []
    assert payload["course_correction"]["trigger"] == "herdr_stale"


def test_high_stakes_herdr_worker_records_receipt_descriptor(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path="herdr-observation-gate.json",
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    herdr_receipt = Path(work_order_payload["repo"]) / "herdr-observation-gate.json"
    herdr_receipt.write_text(
        json.dumps(
                {
                    "schema": "tau.herdr_observation_gate_receipt.v1",
                    "status": "PASS",
                    "ok": True,
                    "mocked": False,
                    "live": True,
                    "provider_live": False,
                    "goal_hash": "sha256:goal",
                    "work_order_sha256": f"sha256:{_sha256(work_order)}",
                },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["substrate_receipts"] == [
        {
            "label": "herdr_receipt",
            "path": str(herdr_receipt.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(herdr_receipt)}",
            "bytes": herdr_receipt.stat().st_size,
            "schema": "tau.herdr_observation_gate_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": False,
        }
    ]


def test_zero_trust_worker_blocks_missing_policy(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        policy_profile=None,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_policy_profile" in payload["alert_codes"]


def test_zero_trust_worker_blocks_missing_data_boundary(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        data_boundary=None,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_data_boundary" in payload["alert_codes"]


def test_zero_trust_worker_blocks_invalid_policy_schema(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        policy_profile={"profile_id": "missing-schema"},
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_policy_profile_schema" in payload["alert_codes"]


def test_zero_trust_worker_blocks_invalid_data_boundary_schema(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        data_boundary={"classification": "public"},
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_data_boundary_schema" in payload["alert_codes"]


def test_zero_trust_worker_blocks_invalid_data_boundary(tmp_path: Path) -> None:
    boundary = _data_boundary()
    boundary["classification"] = "classified-not-allowed"
    boundary.pop("foreign_person_access")
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        data_boundary=boundary,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_data_boundary" in payload["alert_codes"]
    assert "classified_not_allowed" in payload["alert_codes"]
    assert "foreign_person_access must be one of" in payload["alerts"][0]["errors"][0]


def test_scillm_worker_records_model_provider_route(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        model_provider_route={
            "surface": "opencode_serve",
            "agent": "build",
            "model": "gpt-5.5",
        },
    )
    result = _write_result(tmp_path, schema="tau.scillm_worker_result.v1")

    payload = write_scillm_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["schema"] == SCILLM_WORKER_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["model_provider_route"]["surface"] == "opencode_serve"
    assert payload["model_provider_route"]["agent"] == "build"
    assert payload["work_order_sha256"] == f"sha256:{_sha256(work_order)}"
    assert payload["result_sha256"] == f"sha256:{_sha256(result)}"
    assert [artifact["label"] for artifact in payload["validated_artifacts"]] == [
        "work_order",
        "worker_result",
    ]


def test_scillm_worker_nonclaims_model_truth(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.scillm_worker.v1")
    result = _write_result(tmp_path, schema="tau.scillm_worker_result.v1")

    payload = write_scillm_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert "Provider/model semantic quality." in payload["proof_scope"]["does_not_prove"]
    assert "The worker is trustworthy." in payload["proof_scope"]["does_not_prove"]


def test_omp_worker_launch_builds_dry_run_rpc_request(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
    )

    assert payload["schema"] == OMP_WORKER_LAUNCH_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["dry_run"] is True
    assert payload["live"] is False
    assert payload["work_order_sha256"] == f"sha256:{_sha256(work_order)}"
    assert payload["work_order_bytes"] == work_order.stat().st_size
    assert payload["work_order_artifact"] == {
        "label": "work_order",
        "path": str(work_order.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256(work_order)}",
        "bytes": work_order.stat().st_size,
    }
    assert payload["command"] == ["omp", "--mode", "rpc", "--no-session"]
    assert payload["stdin_jsonl"][0]["type"] == "prompt"
    assert payload["stdin_jsonl"][0]["metadata"]["goal_hash"] == "sha256:goal"
    assert payload["substrate_receipts"] == [
        {
            "label": "sandbox_receipt",
            "path": str(sandbox_receipt.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(sandbox_receipt)}",
            "bytes": sandbox_receipt.stat().st_size,
            "schema": "tau.sandbox_run_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": False,
        }
    ]


def test_omp_worker_launch_blocks_wrong_surface(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_swarm"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_omp_surface" in payload["alert_codes"]


def test_omp_worker_doctor_blocks_missing_binary(tmp_path: Path) -> None:
    payload = write_omp_worker_doctor_receipt(
        output_path=tmp_path / "omp-doctor-receipt.json",
        omp_bin=str(tmp_path / "missing-omp"),
        timeout_s=5,
    )

    assert payload["schema"] == OMP_WORKER_DOCTOR_RECEIPT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert payload["ok"] is False
    assert payload["command_found"] is False
    assert payload["version_executed"] is False
    assert "omp_command_missing" in payload["alert_codes"]


def test_omp_worker_doctor_records_version_probe(tmp_path: Path) -> None:
    fake_omp = _write_fake_omp(tmp_path)
    out = tmp_path / "omp-doctor-receipt.json"

    payload = write_omp_worker_doctor_receipt(
        output_path=out,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["schema"] == OMP_WORKER_DOCTOR_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["command_found"] is True
    assert payload["command_path"] == str(fake_omp.resolve())
    assert payload["version_command"] == [str(fake_omp.resolve()), "--version"]
    assert payload["version_executed"] is True
    assert payload["version_exit_code"] == 0
    assert payload["version_stdout_path"]
    stdout_path = Path(payload["version_stdout_path"])
    assert stdout_path.read_text(encoding="utf-8") == "fake-omp 0.0.0\n"
    assert payload["version_stdout_sha256"] == f"sha256:{_sha256(stdout_path)}"
    assert payload["identity_artifacts"][0]["label"] == "version_stdout"


def test_cli_omp_worker_doctor_writes_receipt(tmp_path: Path) -> None:
    fake_omp = _write_fake_omp(tmp_path)
    out = tmp_path / "omp-doctor-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "omp-worker-doctor",
            "--out",
            str(out),
            "--omp-bin",
            str(fake_omp),
            "--timeout-s",
            "5",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["version_stdout_sha256"]


def test_cli_omp_worker_launch_writes_dry_run_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )
    out = tmp_path / "omp-launch-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "omp-worker-launch",
            "--work-order",
            str(work_order),
            "--out",
            str(out),
            "--caller-skill",
            "tau-test",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == OMP_WORKER_LAUNCH_RECEIPT_SCHEMA
    assert payload["caller_skill"] == "tau-test"


def test_omp_worker_launch_apply_runs_process_and_records_logs(tmp_path: Path) -> None:
    fake_omp = _write_fake_omp(tmp_path)
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )
    out = tmp_path / "omp-launch-receipt.json"

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=out,
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "PASS"
    assert payload["dry_run"] is False
    assert payload["live"] is True
    assert payload["process_executed"] is True
    assert payload["launch_skipped"] is False
    assert payload["exit_code"] == 0
    assert payload["command"][0] == str(fake_omp)
    assert payload["stdout_jsonl_valid"] is True
    assert payload["response_frame_count"] == 1
    assert payload["response_schemas"] == ["fake.omp.rpc.response"]
    assert payload["response_frames"][0]["schema"] == "fake.omp.rpc.response"
    assert payload["response_frames"][0]["received_type"] == "prompt"
    assert payload["response_metadata"] == [payload["stdin_jsonl"][0]["metadata"]]
    assert payload["stdout_path"]
    assert payload["stderr_path"]
    stdout_path = Path(payload["stdout_path"])
    stderr_path = Path(payload["stderr_path"])
    assert payload["stdout_sha256"] == f"sha256:{_sha256(stdout_path)}"
    assert payload["stdout_bytes"] == stdout_path.stat().st_size
    assert payload["stderr_sha256"] == f"sha256:{_sha256(stderr_path)}"
    assert payload["stderr_bytes"] == stderr_path.stat().st_size
    assert payload["log_artifacts"] == [
        {
            "label": "stdout",
            "path": str(stdout_path),
            "exists": True,
            "sha256": f"sha256:{_sha256(stdout_path)}",
            "bytes": stdout_path.stat().st_size,
        },
        {
            "label": "stderr",
            "path": str(stderr_path),
            "exists": True,
            "sha256": f"sha256:{_sha256(stderr_path)}",
            "bytes": stderr_path.stat().st_size,
        },
    ]
    stdout_payload = json.loads(Path(payload["stdout_path"]).read_text(encoding="utf-8"))
    assert stdout_payload["schema"] == "fake.omp.rpc.response"
    assert stdout_payload["received_type"] == "prompt"
    assert Path(payload["stderr_path"]).read_text(encoding="utf-8") == ""


def test_omp_worker_launch_apply_blocks_missing_response_metadata(
    tmp_path: Path,
) -> None:
    fake_omp = _write_fake_omp(tmp_path, stdout_mode="no-metadata")
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is True
    assert payload["stdout_jsonl_valid"] is True
    assert payload["response_frame_count"] == 1
    assert payload["response_metadata"] == []
    assert "omp_response_metadata_missing" in payload["alert_codes"]


def test_omp_worker_launch_apply_blocks_response_metadata_mismatch(
    tmp_path: Path,
) -> None:
    fake_omp = _write_fake_omp(tmp_path, stdout_mode="wrong-metadata")
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is True
    assert payload["response_metadata"][0]["dag_id"] == "wrong-dag"
    assert "omp_metadata_mismatch" in payload["alert_codes"]
    mismatch_alert = next(
        alert for alert in payload["alerts"] if alert["code"] == "omp_metadata_mismatch"
    )
    assert "metadata.dag_id expected" in mismatch_alert["errors"][0]
    assert "metadata.goal_hash expected" in mismatch_alert["errors"][1]


def test_omp_worker_launch_apply_blocks_empty_stdout_rpc_response(
    tmp_path: Path,
) -> None:
    fake_omp = _write_fake_omp(tmp_path, stdout_mode="empty")
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is True
    assert payload["exit_code"] == 0
    assert payload["stdout_jsonl_valid"] is False
    assert payload["response_frame_count"] == 0
    assert payload["response_frames"] == []
    assert "omp_stdout_jsonl_empty" in payload["alert_codes"]


def test_omp_worker_launch_apply_blocks_malformed_stdout_rpc_response(
    tmp_path: Path,
) -> None:
    fake_omp = _write_fake_omp(tmp_path, stdout_mode="malformed")
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is True
    assert payload["exit_code"] == 0
    assert payload["stdout_jsonl_valid"] is False
    assert payload["response_frame_count"] == 0
    assert "omp_stdout_jsonl_invalid" in payload["alert_codes"]


def test_omp_worker_launch_apply_skips_process_when_preflight_blocks(tmp_path: Path) -> None:
    marker = tmp_path / "fake-omp-ran"
    fake_omp = _write_fake_omp(tmp_path, marker=marker)
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_swarm"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is False
    assert payload["launch_skipped"] is True
    assert "invalid_omp_surface" in payload["alert_codes"]
    assert not marker.exists()


def test_omp_worker_launch_apply_skips_process_when_path_policy_malformed(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "fake-omp-ran"
    fake_omp = _write_fake_omp(tmp_path, marker=marker)
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    work_order_payload["allowed_paths"] = "src/**"
    work_order.write_text(
        json.dumps(work_order_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is False
    assert payload["launch_skipped"] is True
    assert "invalid_allowed_paths" in payload["alert_codes"]
    assert not marker.exists()


def test_omp_worker_launch_apply_skips_process_when_substrate_blocks(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "fake-omp-ran"
    fake_omp = _write_fake_omp(tmp_path, marker=marker)
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    sandbox_receipt = Path(work_order_payload["repo"]) / "sandbox-receipt.json"
    sandbox_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.sandbox_run_receipt.v1",
                "status": "PASS",
                "ok": True,
                "mocked": True,
                "live": True,
                "provider_live": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is False
    assert payload["launch_skipped"] is True
    assert "sandbox_receipt_mocked" in payload["alert_codes"]
    assert payload["substrate_receipts"][0]["mocked"] is True
    assert not marker.exists()


def test_omp_worker_launch_apply_skips_process_when_substrate_receipt_outside_repo(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "fake-omp-ran"
    fake_omp = _write_fake_omp(tmp_path, marker=marker)
    outside_receipt = tmp_path / "outside" / "sandbox-receipt.json"
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
        sandbox_receipt_path=str(outside_receipt),
        model_provider_route={"surface": "omp_rpc"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
        apply=True,
        omp_bin=str(fake_omp),
        timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["process_executed"] is False
    assert payload["launch_skipped"] is True
    assert "sandbox_receipt_outside_repo" in payload["alert_codes"]
    assert payload["substrate_receipts"] == []
    assert not marker.exists()


def test_cli_omp_worker_launch_apply_records_process_receipt(tmp_path: Path) -> None:
    fake_omp = _write_fake_omp(tmp_path)
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        model_provider_route={"surface": "omp_rpc"},
    )
    out = tmp_path / "omp-launch-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "omp-worker-launch",
            "--work-order",
            str(work_order),
            "--out",
            str(out),
            "--apply",
            "--omp-bin",
            str(fake_omp),
            "--timeout-s",
            "5",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["dry_run"] is False
    assert payload["process_executed"] is True
    assert payload["exit_code"] == 0


def test_scillm_worker_launch_builds_dry_run_opencode_request(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
            "skills": ["memory", "debugger", "scillm"],
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
    )

    assert payload["schema"] == SCILLM_WORKER_LAUNCH_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["dry_run"] is True
    assert payload["live"] is False
    assert payload["work_order_sha256"] == f"sha256:{_sha256(work_order)}"
    assert payload["work_order_bytes"] == work_order.stat().st_size
    assert payload["work_order_artifact"] == {
        "label": "work_order",
        "path": str(work_order.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256(work_order)}",
        "bytes": work_order.stat().st_size,
    }
    assert payload["url"] == "http://localhost:4001/v1/scillm/opencode/runs"
    assert payload["request_payload"]["agent"] == "build"
    assert payload["request_payload"]["skills"] == ["memory", "debugger", "scillm"]
    assert payload["request_timeout_s"] == 600
    assert "timeout_s" not in payload["request_payload"]
    assert payload["request_payload"]["scillm_metadata"]["goal_hash"] == "sha256:goal"
    prompt = payload["request_payload"]["prompt"]
    assert "Result path: worker-result.json" in prompt
    assert "Receipt path: worker-receipt.json" in prompt
    assert "Write a tau.scillm_worker_result.v1 JSON artifact at Result path." in prompt
    assert "Use a top-level key named schema; do not use $schema." in prompt
    assert "status, goal_hash, changed_files, artifacts, tests_run, findings" in prompt


def test_scillm_worker_launch_honors_explicit_worker_timeout(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        timeout_s=120,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
            "skills": ["memory", "debugger", "scillm"],
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["request_payload"]["timeout_s"] == 120


def test_scillm_worker_launch_blocks_invalid_worker_timeout(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        timeout_s=0,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
        apply=True,
        auth_token="token",
        request_timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert "invalid_scillm_worker_timeout" in payload["alert_codes"]
    assert "timeout_s" not in payload["request_payload"]


def test_omp_worker_launch_records_substrate_metadata(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path="herdr-observation-gate.json",
        model_provider_route={"surface": "omp_rpc"},
    )

    payload = write_omp_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "omp-launch-receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["execution_substrate"] == "herdr-visible"
    assert payload["herdr_binding"] == {"pane_id": "w1:p1", "workspace_id": "w1"}
    assert payload["herdr_receipt_path"] == "herdr-observation-gate.json"
    assert payload["substrate_receipts"][0]["schema"] == "tau.herdr_observation_gate_receipt.v1"
    assert payload["substrate_receipts"][0]["mocked"] is False
    assert payload["substrate_receipts"][0]["live"] is True
    assert payload["high_stakes"] is True
    assert payload["policy_profile"]["schema"] == "tau.policy_profile.v1"
    assert payload["policy_profile"]["profile_id"] == "test-zero-trust"
    assert payload["policy_profile_sha256"] == _inline_json_sha256(payload["policy_profile"])
    assert payload["policy_profile_bytes"] == _inline_json_bytes(payload["policy_profile"])
    assert payload["policy_profile_artifact"] == {
        "label": "inline_policy_profile",
        "path": None,
        "exists": True,
        "sha256": _inline_json_sha256(payload["policy_profile"]),
        "bytes": _inline_json_bytes(payload["policy_profile"]),
    }
    assert payload["data_boundary"]["schema"] == "tau.data_boundary.v1"
    assert payload["data_boundary"]["classification"] == "public"
    assert payload["data_boundary_sha256"] == _inline_json_sha256(payload["data_boundary"])
    assert payload["data_boundary_bytes"] == _inline_json_bytes(payload["data_boundary"])
    assert payload["data_boundary_artifact"] == {
        "label": "inline_data_boundary",
        "path": None,
        "exists": True,
        "sha256": _inline_json_sha256(payload["data_boundary"]),
        "bytes": _inline_json_bytes(payload["data_boundary"]),
    }


def test_scillm_worker_launch_records_substrate_metadata(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        sandbox_receipt_path=None,
        herdr_receipt_path="herdr-observation-gate.json",
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
    )

    assert payload["status"] == "PASS"
    assert payload["execution_substrate"] == "herdr-visible"
    assert payload["herdr_binding"] == {"pane_id": "w1:p1", "workspace_id": "w1"}
    assert payload["herdr_receipt_path"] == "herdr-observation-gate.json"
    assert payload["substrate_receipts"][0]["schema"] == "tau.herdr_observation_gate_receipt.v1"
    assert payload["substrate_receipts"][0]["mocked"] is False
    assert payload["substrate_receipts"][0]["live"] is True
    assert payload["high_stakes"] is True
    assert payload["policy_profile"]["schema"] == "tau.policy_profile.v1"
    assert payload["policy_profile"]["profile_id"] == "test-zero-trust"
    assert payload["policy_profile_sha256"] == _inline_json_sha256(payload["policy_profile"])
    assert payload["policy_profile_artifact"]["label"] == "inline_policy_profile"
    assert payload["data_boundary"]["schema"] == "tau.data_boundary.v1"
    assert payload["data_boundary"]["classification"] == "public"
    assert payload["data_boundary_sha256"] == _inline_json_sha256(payload["data_boundary"])
    assert payload["data_boundary_artifact"]["label"] == "inline_data_boundary"


def test_scillm_worker_launch_apply_posts_request_and_records_response(tmp_path: Path) -> None:
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
            "skills": ["memory", "debugger", "scillm"],
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            caller_skill="tau-test",
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "PASS"
    assert payload["dry_run"] is False
    assert payload["live"] is True
    assert payload["provider_live"] is False
    assert payload["http_executed"] is True
    assert payload["http_status"] == 200
    assert payload["response_schema"] == "scillm.opencode_serve.run.v1"
    assert payload["run_id"] == "run-123"
    assert payload["session_id"] == "sess-123"
    assert payload["scillm_run_status"] == "completed"
    assert payload["response_artifacts"] == ["events.jsonl", "worker-result.json"]
    assert payload["response_path"]
    response_path = Path(payload["response_path"])
    assert payload["response_sha256"] == f"sha256:{_sha256(response_path)}"
    assert payload["response_bytes"] == response_path.stat().st_size
    assert payload["error_sha256"] is None
    assert payload["error_bytes"] is None
    assert payload["http_artifacts"] == [
        {
            "label": "response",
            "path": str(response_path),
            "exists": True,
            "sha256": f"sha256:{_sha256(response_path)}",
            "bytes": response_path.stat().st_size,
        }
    ]
    result_artifact = Path(payload["response_result_path"])
    assert payload["expected_worker_result_path"] == "worker-result.json"
    assert payload["response_result_artifact"] == {
        "label": "scillm_worker_result",
        "path": str(result_artifact),
        "exists": True,
        "sha256": f"sha256:{_sha256(result_artifact)}",
        "bytes": result_artifact.stat().st_size,
        "artifact": "worker-result.json",
    }
    assert payload["response_result_sha256"] == f"sha256:{_sha256(result_artifact)}"
    assert payload["response_result_bytes"] == result_artifact.stat().st_size
    response_payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert response_payload["run_id"] == "run-123"
    assert response_payload["result_path"] == "worker-result.json"
    assert requests[0]["path"] == "/v1/scillm/opencode/runs"
    assert requests[0]["authorization"] == "Bearer test-token"
    assert requests[0]["caller_skill"] == "tau-test"
    assert requests[0]["payload"]["agent"] == "build"
    assert requests[0]["payload"]["skills"] == ["memory", "debugger", "scillm"]
    assert "test-token" not in json.dumps(payload)


def test_scillm_worker_launch_accepts_round_tripped_metadata_result_path(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server(
        response={
            "schema": "scillm.opencode_run.result.v1",
            "run_id": "run-123",
            "session_id": "sess-123",
            "status": "completed",
            "assistant_text": "worker wrote result artifact",
            "artifacts": [],
            "scillm_metadata": {"result_path": "worker-result.json"},
        }
    )
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "PASS"
    assert payload["http_executed"] is True
    assert payload["http_status"] == 200
    assert payload["response_artifacts"] == ["worker-result.json"]
    assert payload["response_scillm_metadata"] == {"result_path": "worker-result.json"}
    result_artifact = Path(payload["response_result_path"])
    assert payload["response_result_artifact"] == {
        "label": "scillm_worker_result",
        "path": str(result_artifact),
        "exists": True,
        "sha256": f"sha256:{_sha256(result_artifact)}",
        "bytes": result_artifact.stat().st_size,
        "artifact": "worker-result.json",
    }
    assert requests[0]["payload"]["scillm_metadata"]["result_path"] == "worker-result.json"


def test_scillm_worker_launch_blocks_response_metadata_mismatch(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server(
        response={
            "schema": "scillm.opencode_run.result.v1",
            "run_id": "run-123",
            "session_id": "sess-123",
            "status": "completed",
            "assistant_text": "worker wrote result artifact",
            "artifacts": ["worker-result.json"],
            "scillm_metadata": {
                "schema": "tau.executor.scillm_worker.v1",
                "dag_id": "wrong-dag",
                "node_id": "coder",
                "attempt": 1,
                "goal_hash": "sha256:wrong-goal",
                "result_path": "worker-result.json",
                "receipt_path": "worker-receipt.json",
            },
        }
    )
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is True
    assert payload["response_result_artifact"] is not None
    assert "scillm_metadata_mismatch" in payload["alert_codes"]
    mismatch_alert = next(
        alert for alert in payload["alerts"] if alert["code"] == "scillm_metadata_mismatch"
    )
    assert "scillm_metadata.dag_id expected" in mismatch_alert["errors"][0]
    assert "scillm_metadata.goal_hash expected" in mismatch_alert["errors"][1]
    assert requests[0]["payload"]["scillm_metadata"]["dag_id"] == "coding-dag"


def test_scillm_worker_launch_apply_blocks_incomplete_success_response(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server(response={})
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is True
    assert payload["http_status"] == 200
    assert requests[0]["path"] == "/v1/scillm/opencode/runs"
    assert "missing_scillm_run_status" in payload["alert_codes"]
    assert "missing_scillm_run_identifier" in payload["alert_codes"]


def test_scillm_worker_launch_apply_blocks_missing_result_artifact(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server(
        response={
            "schema": "scillm.opencode_serve.run.v1",
            "run_id": "run-123",
            "session_id": "sess-123",
            "status": "completed",
            "assistant_text": "fixture response without worker result",
            "artifacts": ["events.jsonl"],
        }
    )
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is True
    assert payload["http_status"] == 200
    assert payload["response_result_artifact"] is None
    assert payload["response_result_sha256"] is None
    assert "missing_scillm_worker_result_artifact" in payload["alert_codes"]
    assert requests[0]["path"] == "/v1/scillm/opencode/runs"


def test_scillm_worker_launch_apply_uses_local_env_auth_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SCILLM_MASTER_KEY", "local-test-token")
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "PASS"
    assert payload["http_executed"] is True
    assert payload["headers"]["authorization"] == "REDACTED"
    assert payload["headers"]["authorization_source"] == "env:SCILLM_MASTER_KEY"
    assert requests[0]["authorization"] == "Bearer local-test-token"
    assert "local-test-token" not in json.dumps(payload)


def test_scillm_worker_launch_apply_uses_local_docker_auth_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SCILLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("SCILLM_API_KEY", raising=False)
    monkeypatch.delenv("SCILLM_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("SCILLM_ENV_PATH", str(tmp_path / "missing.env"))

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="PATH=/usr/local/bin\nLITELLM_MASTER_KEY=docker-test-token\n",
            stderr="",
        )

    monkeypatch.setattr("tau_coding.coding_worker_adapters.subprocess.run", fake_run)
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "PASS"
    assert payload["http_executed"] is True
    assert payload["headers"]["authorization"] == "REDACTED"
    assert (
        payload["headers"]["authorization_source"]
        == "docker:docker-scillm-proxy-1:LITELLM_MASTER_KEY"
    )
    assert requests[0]["authorization"] == "Bearer docker-test-token"
    assert "docker-test-token" not in json.dumps(payload)


def test_scillm_worker_launch_local_apply_blocks_without_auth_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SCILLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("SCILLM_API_KEY", raising=False)
    monkeypatch.delenv("SCILLM_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("SCILLM_ENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setenv("SCILLM_DOCKER_AUTH_DISCOVERY", "0")
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert requests == []
    assert payload["headers"]["authorization"] == "REDACTED_REQUIRED"
    assert payload["headers"]["authorization_source"] == "missing"
    assert "missing_scillm_auth_token" in payload["alert_codes"]


def test_scillm_worker_launch_remote_apply_requires_auth_token(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
        scillm_base_url="https://scillm.example.invalid",
        apply=True,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert "missing_scillm_auth_token" in payload["alert_codes"]


def test_scillm_worker_launch_apply_skips_http_when_route_blocks(tmp_path: Path) -> None:
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "chat",
            "endpoint": "/v1/chat/completions",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert "invalid_scillm_surface" in payload["alert_codes"]
    assert requests == []


def test_scillm_worker_launch_apply_skips_http_when_substrate_blocks(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        herdr_receipt_path="missing-herdr-receipt.json",
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    (Path(work_order_payload["repo"]) / "missing-herdr-receipt.json").unlink()
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert "herdr_receipt_missing" in payload["alert_codes"]
    assert requests == []


def test_scillm_worker_launch_apply_skips_http_when_substrate_receipt_outside_repo(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server()
    outside_receipt = tmp_path / "outside" / "sandbox-receipt.json"
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        execution_substrate="docker-sandbox",
        sandbox_receipt_path=str(outside_receipt),
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert "sandbox_receipt_outside_repo" in payload["alert_codes"]
    assert payload["substrate_receipts"] == []
    assert requests == []


def test_scillm_worker_launch_apply_skips_http_when_result_path_outside_repo(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    work_order_payload["result_path"] = str(tmp_path / "outside" / "worker-result.json")
    work_order.write_text(
        json.dumps(work_order_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=5,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert "worker_result_path_outside_repo" in payload["alert_codes"]
    assert requests == []


def test_scillm_worker_launch_apply_blocks_invalid_timeout_before_http(
    tmp_path: Path,
) -> None:
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    try:
        payload = write_scillm_worker_launch_receipt(
            work_order_path=work_order,
            output_path=tmp_path / "launch-receipt.json",
            scillm_base_url=base_url,
            apply=True,
            auth_token="test-token",
            request_timeout_s=0,
        )
    finally:
        server.shutdown()

    assert payload["status"] == "BLOCKED"
    assert payload["request_timeout_s"] == 0
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert payload["timed_out"] is False
    assert "invalid_timeout" in payload["alert_codes"]
    assert requests == []


def test_scillm_worker_launch_apply_records_socket_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )

    def raise_timeout(*args, **kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
        scillm_base_url="http://localhost:4001",
        apply=True,
        auth_token="test-token",
        request_timeout_s=1,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is True
    assert payload["launch_skipped"] is False
    assert payload["timed_out"] is True
    assert payload["error_path"] is None
    assert "scillm_launch_timeout" in payload["alert_codes"]
    assert "scillm_connection_error" not in payload["alert_codes"]


def test_scillm_worker_launch_apply_records_urlerror_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )

    def raise_timeout(*args, **kwargs):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
        scillm_base_url="http://localhost:4001",
        apply=True,
        auth_token="test-token",
        request_timeout_s=1,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is True
    assert payload["launch_skipped"] is False
    assert payload["timed_out"] is True
    assert payload["error_path"] is None
    assert "scillm_launch_timeout" in payload["alert_codes"]
    assert "scillm_connection_error" not in payload["alert_codes"]


def test_cli_scillm_worker_launch_apply_records_http_receipt(tmp_path: Path) -> None:
    server, base_url, requests = _start_fake_scillm_server()
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    out = tmp_path / "launch-receipt.json"
    try:
        result = CliRunner().invoke(
            app,
            [
                "scillm-worker-launch",
                "--work-order",
                str(work_order),
                "--out",
                str(out),
                "--scillm-base-url",
                base_url,
                "--caller-skill",
                "tau-test",
                "--apply",
                "--auth-token",
                "test-token",
                "--request-timeout-s",
                "5",
            ],
        )
    finally:
        server.shutdown()

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["dry_run"] is False
    assert payload["http_executed"] is True
    assert payload["http_status"] == 200
    assert requests[0]["authorization"] == "Bearer test-token"


def test_scillm_worker_launch_blocks_chat_model_as_agent(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "opencode-go/kimi-k2.6",
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "chat_model_used_as_agent" in payload["alert_codes"]


def test_scillm_worker_launch_blocks_wrong_endpoint(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "chat",
            "endpoint": "/v1/chat/completions",
            "agent": "build",
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_scillm_surface" in payload["alert_codes"]
    assert "invalid_scillm_endpoint" in payload["alert_codes"]


def test_scillm_worker_launch_blocks_raw_opencode_local_port(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
        scillm_base_url="http://127.0.0.1:4096",
        apply=True,
        auth_token="test-token",
        request_timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert "raw_opencode_base_url" in payload["alert_codes"]


def test_scillm_worker_launch_blocks_malformed_base_url(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )

    payload = write_scillm_worker_launch_receipt(
        work_order_path=work_order,
        output_path=tmp_path / "launch-receipt.json",
        scillm_base_url="file:///tmp/not-scillm",
        apply=True,
        auth_token="test-token",
        request_timeout_s=5,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["http_executed"] is False
    assert payload["launch_skipped"] is True
    assert "invalid_scillm_base_url_scheme" in payload["alert_codes"]
    assert "invalid_scillm_base_url_host" in payload["alert_codes"]


def test_cli_scillm_worker_launch_writes_dry_run_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.scillm_worker.v1",
        high_stakes=True,
        model_provider_route={
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    )
    out = tmp_path / "launch-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "scillm-worker-launch",
            "--work-order",
            str(work_order),
            "--out",
            str(out),
            "--caller-skill",
            "tau-test",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == SCILLM_WORKER_LAUNCH_RECEIPT_SCHEMA
    assert payload["headers"]["x_caller_skill"] == "tau-test"
    assert payload["request_timeout_s"] == 600
    assert "timeout_s" not in payload["request_payload"]


def test_cli_scillm_worker_validate_writes_receipt(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.scillm_worker.v1")
    result_path = _write_result(tmp_path, schema="tau.scillm_worker_result.v1")
    out = tmp_path / "receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "scillm-worker-validate",
            "--work-order",
            str(work_order),
            "--result",
            str(result_path),
            "--out",
            str(out),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == SCILLM_WORKER_RECEIPT_SCHEMA


_DEFAULT_METADATA = object()


def _write_work_order(
    tmp_path: Path,
    *,
    schema: str,
    high_stakes: bool = False,
    execution_substrate: str | None = "docker-sandbox",
    policy_profile: object = _DEFAULT_METADATA,
    data_boundary: object = _DEFAULT_METADATA,
    sandbox_receipt_path: str | None = "sandbox-receipt.json",
    herdr_binding: dict | None = {"workspace_id": "w1", "pane_id": "w1:p1"},
    herdr_receipt_path: str | None = None,
    model_provider_route: dict | None = None,
    required_artifacts: list[str] | None = None,
    timeout_s: object | None = None,
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    payload = {
        "schema": schema,
        "dag_id": "coding-dag",
        "node_id": "coder",
        "agent": "coder",
        "goal_hash": "sha256:goal",
        "attempt": 1,
        "repo": str(repo),
        "allowed_paths": ["src/**", "tests/**"],
        "forbidden_paths": ["secrets/**"],
        "task": "Make a bounded coding change.",
        "required_artifacts": required_artifacts if required_artifacts is not None else [],
        "result_path": "worker-result.json",
        "receipt_path": "worker-receipt.json",
        "high_stakes": high_stakes,
        "model_provider_route": model_provider_route or {},
    }
    if timeout_s is not None:
        payload["timeout_s"] = timeout_s
    if execution_substrate is not None:
        payload["execution_substrate"] = execution_substrate
    if policy_profile is _DEFAULT_METADATA:
        if high_stakes:
            payload["policy_profile"] = _policy_profile()
    elif policy_profile is not None:
        payload["policy_profile"] = policy_profile
    if data_boundary is _DEFAULT_METADATA:
        if high_stakes:
            payload["data_boundary"] = _data_boundary()
    elif data_boundary is not None:
        payload["data_boundary"] = data_boundary
    if sandbox_receipt_path is not None:
        payload["sandbox_receipt_path"] = sandbox_receipt_path
    if herdr_binding is not None:
        payload["herdr_binding"] = herdr_binding
    if herdr_receipt_path is not None:
        payload["herdr_receipt_path"] = herdr_receipt_path
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    work_order_sha256 = f"sha256:{_sha256(path)}"
    if sandbox_receipt_path is not None:
        sandbox_receipt = Path(sandbox_receipt_path)
        if not sandbox_receipt.is_absolute():
            sandbox_receipt = repo / sandbox_receipt
        sandbox_receipt.parent.mkdir(parents=True, exist_ok=True)
        sandbox_receipt.write_text(
            json.dumps(
                {
                    "schema": "tau.sandbox_run_receipt.v1",
                    "status": "PASS",
                    "ok": True,
                    "mocked": False,
                    "live": True,
                    "provider_live": False,
                    "goal_hash": "sha256:goal",
                    "work_order_sha256": work_order_sha256,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    if herdr_receipt_path is not None:
        herdr_receipt = Path(herdr_receipt_path)
        if not herdr_receipt.is_absolute():
            herdr_receipt = repo / herdr_receipt
        herdr_receipt.parent.mkdir(parents=True, exist_ok=True)
        herdr_receipt.write_text(
            json.dumps(
                {
                    "schema": "tau.herdr_observation_gate_receipt.v1",
                    "status": "PASS",
                    "ok": True,
                    "mocked": False,
                    "live": True,
                    "provider_live": False,
                    "goal_hash": "sha256:goal",
                    "work_order_sha256": work_order_sha256,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return path


def _write_fake_omp(
    tmp_path: Path,
    *,
    marker: Path | None = None,
    stdout_mode: str = "json",
) -> Path:
    script = tmp_path / "fake-omp"
    marker_line = (
        f"Path({str(marker)!r}).write_text('ran\\n', encoding='utf-8')"
        if marker is not None
        else "None"
    )
    if stdout_mode == "empty":
        output_lines = ["payload = json.loads(sys.stdin.readline())"]
    elif stdout_mode == "malformed":
        output_lines = [
            "payload = json.loads(sys.stdin.readline())",
            "print('not-json')",
        ]
    elif stdout_mode == "no-metadata":
        output_lines = [
            "payload = json.loads(sys.stdin.readline())",
            "print(json.dumps({",
            "    'schema': 'fake.omp.rpc.response',",
            "    'received_type': payload.get('type'),",
            "}, sort_keys=True))",
        ]
    elif stdout_mode == "wrong-metadata":
        output_lines = [
            "payload = json.loads(sys.stdin.readline())",
            "metadata = dict(payload.get('metadata') or {})",
            "metadata['dag_id'] = 'wrong-dag'",
            "metadata['goal_hash'] = 'sha256:wrong'",
            "print(json.dumps({",
            "    'schema': 'fake.omp.rpc.response',",
            "    'received_type': payload.get('type'),",
            "    'metadata': metadata,",
            "}, sort_keys=True))",
        ]
    else:
        output_lines = [
            "payload = json.loads(sys.stdin.readline())",
            "print(json.dumps({",
            "    'schema': 'fake.omp.rpc.response',",
            "    'received_type': payload.get('type'),",
            "    'metadata': payload.get('metadata'),",
            "}, sort_keys=True))",
        ]
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "from pathlib import Path",
                "if '--version' in sys.argv:",
                "    print('fake-omp 0.0.0')",
                "    raise SystemExit(0)",
                marker_line,
                *output_lines,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _start_fake_scillm_server(
    response: dict | None = None,
) -> tuple[ThreadingHTTPServer, str, list[dict]]:
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "caller_skill": self.headers.get("X-Caller-Skill"),
                    "payload": json.loads(body),
                }
            )
            response_payload = (
                response
                if response is not None
                else {
                "schema": "scillm.opencode_serve.run.v1",
                "run_id": "run-123",
                "session_id": "sess-123",
                "status": "completed",
                "assistant_text": "fixture response",
                "artifacts": ["events.jsonl", "worker-result.json"],
                "result_path": "worker-result.json",
                }
            )
            metadata = requests[-1]["payload"].get("scillm_metadata")
            result_path = (
                response_payload.get("result_path")
                if isinstance(response_payload, dict)
                else None
            )
            if not result_path and isinstance(response_payload, dict):
                response_metadata = response_payload.get("scillm_metadata")
                if isinstance(response_metadata, dict):
                    result_path = response_metadata.get("result_path")
            if not result_path and isinstance(metadata, dict):
                result_path = metadata.get("result_path")
            if result_path:
                repo = Path(str(requests[-1]["payload"].get("cwd")))
                result = Path(str(result_path))
                if not result.is_absolute():
                    result = repo / result
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(
                    json.dumps(
                        {
                            "schema": "tau.scillm_worker_result.v1",
                            "status": "NEEDS_REVIEW",
                            "goal_hash": "sha256:goal",
                            "changed_files": [],
                            "artifacts": [],
                            "tests_run": [],
                            "findings": [{"id": "fixture"}],
                            "next_recommended_route": "reviewer",
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            encoded = json.dumps(response_payload, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}", requests


def _write_result(
    tmp_path: Path,
    *,
    schema: str,
    goal_hash: str = "sha256:goal",
    changed_files: list[str] | None = None,
    artifacts: list[str] | None = None,
    tests_run: list[dict] | None = None,
) -> Path:
    payload = {
        "schema": schema,
        "status": "NEEDS_REVIEW",
        "goal_hash": goal_hash,
        "changed_files": changed_files if changed_files is not None else ["src/example.py"],
        "artifacts": artifacts if artifacts is not None else [],
        "tests_run": tests_run if tests_run is not None else [],
        "findings": [],
        "next_recommended_route": "reviewer",
    }
    path = tmp_path / "repo" / "worker-result.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_reference_receipt(
    path: Path,
    *,
    schema: str,
    status: str,
    ok: bool,
    mocked: bool,
    live: bool,
    extra: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": schema,
        "status": status,
        "ok": ok,
        "mocked": mocked,
        "live": live,
        "provider_live": False,
    }
    if extra:
        payload.update(extra)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _github_policy_requirements() -> dict:
    return {
        "approval_packet": True,
        "preflight": True,
        "redaction": True,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inline_json_sha256(payload: dict) -> str:
    return f"sha256:{hashlib.sha256(_inline_json_bytes_payload(payload)).hexdigest()}"


def _inline_json_bytes(payload: dict) -> int:
    return len(_inline_json_bytes_payload(payload))


def _inline_json_bytes_payload(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _policy_profile() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "test-zero-trust",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {
            "external_search": "deny",
            "manual_sanitized_receipt": "allow_with_review",
        },
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": ["src/**", "tests/**"], "read_denylist": []},
    }


def _data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }
