import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.coding_worker_adapters import (
    OMP_WORKER_LAUNCH_RECEIPT_SCHEMA,
    OMP_WORKER_RECEIPT_SCHEMA,
    SCILLM_WORKER_LAUNCH_RECEIPT_SCHEMA,
    SCILLM_WORKER_RECEIPT_SCHEMA,
    write_omp_worker_launch_receipt,
    write_omp_worker_receipt,
    write_scillm_worker_launch_receipt,
    write_scillm_worker_receipt,
)


def test_omp_worker_blocks_missing_result(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=tmp_path / "missing-result.json",
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "worker_result_missing" in payload["alert_codes"]
    assert "invalid_result_schema" in payload["alert_codes"]


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
        required_artifacts=["logs/pytest.log"],
    )
    work_order_payload = json.loads(work_order.read_text(encoding="utf-8"))
    repo = Path(work_order_payload["repo"])
    log = repo / "logs" / "pytest.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("1 passed\n", encoding="utf-8")
    result = _write_result(
        tmp_path,
        schema="tau.omp_worker_result.v1",
        artifacts=["logs/pytest.log"],
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
            "artifact": "logs/pytest.log",
        }
    ]


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
    assert payload["request_payload"]["timeout_s"] == 600
    assert payload["request_payload"]["scillm_metadata"]["goal_hash"] == "sha256:goal"


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
    assert payload["data_boundary"]["schema"] == "tau.data_boundary.v1"
    assert payload["data_boundary"]["classification"] == "public"


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
    assert payload["data_boundary"]["schema"] == "tau.data_boundary.v1"
    assert payload["data_boundary"]["classification"] == "public"


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
    assert payload["response_artifacts"] == ["events.jsonl"]
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
    response_payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert response_payload["run_id"] == "run-123"
    assert requests[0]["path"] == "/v1/scillm/opencode/runs"
    assert requests[0]["authorization"] == "Bearer test-token"
    assert requests[0]["caller_skill"] == "tau-test"
    assert requests[0]["payload"]["agent"] == "build"
    assert requests[0]["payload"]["skills"] == ["memory", "debugger", "scillm"]
    assert "test-token" not in json.dumps(payload)


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


def test_scillm_worker_launch_local_apply_blocks_without_auth_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SCILLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("SCILLM_API_KEY", raising=False)
    monkeypatch.delenv("SCILLM_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("SCILLM_ENV_PATH", str(tmp_path / "missing.env"))
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
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
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
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
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
    return path


def _write_fake_omp(tmp_path: Path, *, marker: Path | None = None) -> Path:
    script = tmp_path / "fake-omp"
    marker_line = (
        f"Path({str(marker)!r}).write_text('ran\\n', encoding='utf-8')"
        if marker is not None
        else "None"
    )
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "from pathlib import Path",
                marker_line,
                "payload = json.loads(sys.stdin.readline())",
                "print(json.dumps({",
                "    'schema': 'fake.omp.rpc.response',",
                "    'received_type': payload.get('type'),",
                "    'metadata': payload.get('metadata'),",
                "}, sort_keys=True))",
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
                "artifacts": ["events.jsonl"],
                }
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
    path = tmp_path / "worker-result.json"
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
