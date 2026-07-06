import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.coding_worker_adapters import (
    OMP_WORKER_RECEIPT_SCHEMA,
    SCILLM_WORKER_RECEIPT_SCHEMA,
    write_omp_worker_receipt,
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


def test_omp_worker_accepts_schema_valid_result_and_routes_reviewer(tmp_path: Path) -> None:
    work_order = _write_work_order(tmp_path, schema="tau.executor.omp.v1")
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["schema"] == OMP_WORKER_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["next_recommended_route"] == "reviewer"


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


def test_high_stakes_herdr_worker_requires_binding(tmp_path: Path) -> None:
    work_order = _write_work_order(
        tmp_path,
        schema="tau.executor.omp.v1",
        high_stakes=True,
        execution_substrate="herdr-visible",
        herdr_binding=None,
    )
    result = _write_result(tmp_path, schema="tau.omp_worker_result.v1")

    payload = write_omp_worker_receipt(
        work_order_path=work_order,
        result_path=result,
        output_path=tmp_path / "receipt.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "herdr_binding_required" in payload["alert_codes"]


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


def _write_work_order(
    tmp_path: Path,
    *,
    schema: str,
    high_stakes: bool = False,
    execution_substrate: str | None = "docker-sandbox",
    policy_profile: dict | None = {"profile_id": "test-zero-trust"},
    data_boundary: dict | None = {"classification": "public"},
    sandbox_receipt_path: str | None = "sandbox-receipt.json",
    herdr_binding: dict | None = {"workspace_id": "w1", "pane_id": "w1:p1"},
    model_provider_route: dict | None = None,
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
                    "mocked": True,
                    "live": False,
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
        "required_artifacts": [],
        "result_path": "worker-result.json",
        "receipt_path": "worker-receipt.json",
        "high_stakes": high_stakes,
        "model_provider_route": model_provider_route or {},
    }
    if execution_substrate is not None:
        payload["execution_substrate"] = execution_substrate
    if policy_profile is not None:
        payload["policy_profile"] = policy_profile
    if data_boundary is not None:
        payload["data_boundary"] = data_boundary
    if sandbox_receipt_path is not None:
        payload["sandbox_receipt_path"] = sandbox_receipt_path
    if herdr_binding is not None:
        payload["herdr_binding"] = herdr_binding
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_result(
    tmp_path: Path,
    *,
    schema: str,
    goal_hash: str = "sha256:goal",
    changed_files: list[str] | None = None,
    tests_run: list[dict] | None = None,
) -> Path:
    payload = {
        "schema": schema,
        "status": "NEEDS_REVIEW",
        "goal_hash": goal_hash,
        "changed_files": changed_files if changed_files is not None else ["src/example.py"],
        "artifacts": [],
        "tests_run": tests_run if tests_run is not None else [],
        "findings": [],
        "next_recommended_route": "reviewer",
    }
    path = tmp_path / "worker-result.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
