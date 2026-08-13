from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import pytest

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.runtime_backends.kernel import (
    create_python_workspace,
    reconcile_kernel_process_state,
    write_python_workspace_canary,
)
from tau_coding.runtime_backends.kernel_contracts import (
    PYTHON_KERNEL_FEATURES,
    PythonExecutionRequest,
    PythonKernelContractError,
    PythonPackageManifest,
    PythonWorkspaceRequest,
    build_python_package_manifest,
    verify_execution_binding,
)

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


def test_package_manifest_records_live_kernel_environment() -> None:
    manifest = build_python_package_manifest()
    payload = manifest.to_payload()

    assert manifest.available is True
    assert PythonPackageManifest.from_payload(payload) == manifest
    assert payload["packages"]["jupyter_client"]["available"] is True
    assert payload["packages"]["ipykernel"]["available"] is True
    assert payload["security_profile"]["note"].endswith("not Tau acceptance.")


def test_workspace_persists_namespace_and_success_is_not_tau_acceptance(tmp_path: Path) -> None:
    workspace, workspace_receipt = create_python_workspace(
        _workspace_request(attempt_id="attempt-persist", worktree=tmp_path),
        state_dir=tmp_path / "state",
    )
    try:
        first = workspace.execute("x = 41\nprint('stored', x)")
        second = workspace.execute("print('persisted', x + 1)")
        pass_receipt = workspace.execute("print('PASS')")
    finally:
        workspace.stop()

    assert workspace_receipt.status == "READY"
    assert workspace_receipt.endpoint_lease is not None
    assert first.status == "OK"
    assert second.status == "OK"
    assert "persisted" in _artifact_text(second)
    assert pass_receipt.status == "OK"
    assert pass_receipt.tau_admission_status == "not_admitted"


def test_workspace_serializes_concurrent_execute_calls(tmp_path: Path) -> None:
    workspace, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-serialized", worktree=tmp_path),
        state_dir=tmp_path / "state",
    )
    receipts = []

    def execute_cell(name: str) -> None:
        receipts.append(
            workspace.execute(
                f"import time\nprint('start-{name}')\ntime.sleep(0.2)\nprint('end-{name}')",
                execution_id=f"exec-{name}",
            )
        )

    try:
        threads = [threading.Thread(target=execute_cell, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
    finally:
        workspace.stop()

    assert len(receipts) == 2
    ordered = sorted(receipts, key=lambda receipt: receipt.started_at)
    assert _parse_time(ordered[1].started_at) >= _parse_time(ordered[0].finished_at)


def test_new_attempts_and_branch_nodes_do_not_share_namespace(tmp_path: Path) -> None:
    first, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-one", node_id="branch-a", worktree=tmp_path),
        state_dir=tmp_path / "state-a",
    )
    second, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-two", node_id="branch-b", worktree=tmp_path),
        state_dir=tmp_path / "state-b",
    )
    try:
        assert first.execute("x = 'branch-a'").status == "OK"
        isolation = second.execute("print('x_present', 'x' in globals())")
    finally:
        first.stop()
        second.stop()

    assert "x_present False" in _artifact_text(isolation)


def test_unknown_capability_fails_before_endpoint_creation(tmp_path: Path) -> None:
    with pytest.raises(PythonKernelContractError, match="unknown python kernel features"):
        _workspace_request(
            attempt_id="attempt-unknown",
            worktree=tmp_path,
            required_features=("not_a_kernel_feature",),
        )


def test_execution_binding_mutation_invalidates_receipt(tmp_path: Path) -> None:
    workspace, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-binding", worktree=tmp_path),
        state_dir=tmp_path / "state",
    )
    try:
        receipt = workspace.execute("print('bound')")
    finally:
        workspace.stop()

    request = PythonExecutionRequest(
        workspace_request_sha256=receipt.workspace_request_sha256,
        endpoint_lease_sha256=receipt.endpoint_lease_sha256,
        generation_id=receipt.generation_id,
        execution_id=receipt.execution_id,
        goal_hash=canonical_sha256({"mutated": "goal"}),
        policy_sha256=canonical_sha256({"policy": "kernel"}),
        data_boundary_sha256=canonical_sha256({"data": "local"}),
        code="print('bound')",
    )

    verification = verify_execution_binding(receipt, request)

    assert verification["status"] == "BLOCKED"
    assert "binding_mismatch:execution_request_sha256" in verification["errors"]


def test_output_projection_truncates_but_keeps_full_artifact_hash(tmp_path: Path) -> None:
    workspace, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-output", worktree=tmp_path),
        state_dir=tmp_path / "state",
    )
    try:
        receipt = workspace.execute("print('A' * 2000)")
    finally:
        workspace.stop()

    projection = receipt.output_projection.to_value()
    artifact = receipt.output_artifact.to_value()

    assert projection["truncated"] is True
    assert projection["full_output_sha256"] == artifact["sha256"]
    assert Path(artifact["path"]).is_file()


def test_late_async_output_is_classified(tmp_path: Path) -> None:
    workspace, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-late", worktree=tmp_path),
        state_dir=tmp_path / "state",
    )
    try:
        receipt = workspace.execute(
            "import threading\n"
            "threading.Timer(0.1, lambda: print('late-output')).start()\n"
            "print('cell-done')",
            late_output_grace_seconds=0.5,
        )
    finally:
        workspace.stop()

    assert any(
        item.to_value()["classification"] == "late_async_output"
        for item in receipt.late_async_outputs
    )


def test_import_failure_keeps_error_evidence(tmp_path: Path) -> None:
    workspace, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-import", worktree=tmp_path),
        state_dir=tmp_path / "state",
    )
    try:
        receipt = workspace.execute("import tau_nonexistent_package_for_receipt")
    finally:
        workspace.stop()

    assert receipt.status == "ERROR"
    assert "ModuleNotFoundError" in receipt.errors
    assert "tau_nonexistent_package_for_receipt" in _artifact_text(receipt)


def test_interrupt_returns_control_receipt_for_infinite_loop(tmp_path: Path) -> None:
    workspace, _ = create_python_workspace(
        _workspace_request(attempt_id="attempt-interrupt", worktree=tmp_path),
        state_dir=tmp_path / "state",
    )
    try:
        thread = threading.Thread(
            target=lambda: workspace.execute("while True:\n    pass", timeout_seconds=30),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=0.4)
        receipt = workspace.interrupt()
        thread.join(timeout=5)
    finally:
        workspace.stop()

    assert receipt.status in {"READY", "QUARANTINED"}
    assert receipt.action == "interrupt"


def test_reconcile_does_not_kill_unrelated_reused_pid(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "endpoint_lease": {"schema": "tau.runtime_endpoint_lease.v1", "id": "fake"},
                "process_identity": {
                    "pid": os.getpid(),
                    "start_time_ticks": "definitely-not-this-process",
                    "alive": True,
                },
            }
        ),
        encoding="utf-8",
    )

    receipt = reconcile_kernel_process_state(state_path)

    assert receipt["status"] == "PASS"
    assert receipt["action"] == "skip_unrelated_pid_reuse"


def test_live_canary_writes_attempt_scoped_receipt(tmp_path: Path) -> None:
    receipt = write_python_workspace_canary(tmp_path / "canary")

    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["checks"]["endpoint_lease_present"] is True
    assert receipt["checks"]["namespace_persisted"] is True
    assert (tmp_path / "canary" / "python-workspace-canary.json").is_file()


def _workspace_request(
    *,
    attempt_id: str,
    worktree: Path,
    node_id: str = "python-node",
    required_features: tuple[str, ...] | None = None,
) -> PythonWorkspaceRequest:
    return PythonWorkspaceRequest(
        run_id="run",
        plan_revision=canonical_sha256({"plan": "kernel-test"}),
        dag_id="dag",
        node_id=node_id,
        attempt_id=attempt_id,
        attempt_number=1,
        worktree=str(worktree),
        goal_hash=canonical_sha256({"goal": "kernel-test"}),
        policy_sha256=canonical_sha256({"policy": "kernel"}),
        data_boundary_sha256=canonical_sha256({"data": "local"}),
        required_features=required_features or tuple(sorted(PYTHON_KERNEL_FEATURES)),
        startup_timeout_seconds=20,
    )


def _artifact_text(receipt: object) -> str:
    payload = receipt.output_artifact.to_value()  # type: ignore[attr-defined]
    return Path(str(payload["path"])).read_text(encoding="utf-8")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
