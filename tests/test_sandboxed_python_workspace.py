from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.runtime_backends.python_workspace import (
    DEFAULT_WORKSPACE_IMAGE,
    SandboxedPythonWorkspace,
    SandboxedPythonWorkspaceRequest,
    admit_python_execution_artifact,
    stale_snapshot_errors,
)


def test_request_requires_pinned_docker_image() -> None:
    with pytest.raises(RuntimeError, match="pinned"):
        _request(image="python:3.12-slim")


def test_stale_snapshot_rejects_goal_attempt_policy_boundary_and_manifest() -> None:
    request = _request()
    manifest = {"sha256": canonical_sha256({"manifest": "current"})}
    snapshot = {
        "schema": "tau.python_workspace_snapshot.v1",
        "workspace_request_sha256": request.sha256,
        "goal_hash": request.goal_hash,
        "plan_hash": request.plan_hash,
        "attempt_id": request.attempt_id,
        "policy_sha256": request.policy_sha256,
        "data_boundary_sha256": request.data_boundary_sha256,
        "package_manifest_sha256": manifest["sha256"],
    }

    assert stale_snapshot_errors(snapshot, request, manifest) == []

    mutated = {
        **snapshot,
        "workspace_request_sha256": canonical_sha256({"other": "workspace"}),
        "goal_hash": canonical_sha256({"other": "goal"}),
        "attempt_id": "attempt-other",
        "policy_sha256": canonical_sha256({"other": "policy"}),
        "data_boundary_sha256": canonical_sha256({"other": "boundary"}),
        "package_manifest_sha256": canonical_sha256({"other": "manifest"}),
    }

    errors = stale_snapshot_errors(mutated, request, manifest)

    assert "stale_snapshot:workspace_request_sha256" in errors
    assert "stale_snapshot:goal_hash" in errors
    assert "stale_snapshot:attempt_id" in errors
    assert "stale_snapshot:policy_sha256" in errors
    assert "stale_snapshot:data_boundary_sha256" in errors
    assert "stale_snapshot:package_manifest_sha256" in errors


def test_artifact_admission_blocks_until_receipt_artifact_hash_matches(tmp_path: Path) -> None:
    artifact = tmp_path / "out.json"
    artifact.write_text('{"ok": true}\n', encoding="utf-8")
    receipt = {
        "schema": "tau.python_execution_receipt.v1",
        "status": "OK",
        "execution_id": "exec",
        "exports": {"ok": True},
        "output_artifact": {
            "path": str(artifact),
            "sha256": "sha256:" + "0" * 64,
        },
    }

    blocked = admit_python_execution_artifact(receipt)
    receipt["output_artifact"]["sha256"] = (
        "sha256:" + __import__("hashlib").sha256(artifact.read_bytes()).hexdigest()
    )
    admitted = admit_python_execution_artifact(receipt)

    assert blocked["status"] == "BLOCKED"
    assert "output_artifact_sha256_mismatch" in blocked["errors"]
    assert admitted["status"] == "PASS"
    assert admitted["accepted_output"]["exports"] == {"ok": True}


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker unavailable")
def test_live_docker_workspace_persists_snapshots_and_rejects_duplicate_conflict(
    tmp_path: Path,
) -> None:
    _require_local_image(DEFAULT_WORKSPACE_IMAGE)
    workspace = SandboxedPythonWorkspace(_request(), state_dir=tmp_path / "workspace")
    start = workspace.start()
    if start["status"] != "READY":
        pytest.skip(f"workspace backend unavailable: {start['errors']}")
    try:
        first = workspace.execute(
            "turn-1",
            "import math\n"
            "value = 40\n"
            "def helper(x):\n"
            "    return math.sqrt(x) + value\n"
            "print('turn1', helper(4))\n",
        )
        second = workspace.execute(
            "turn-2",
            "print('turn2', helper(9))\ntau_exports = {'answer': helper(4), 'value': value}\n",
        )
        duplicate = workspace.execute(
            "turn-2",
            "print('turn2', helper(9))\ntau_exports = {'answer': helper(4), 'value': value}\n",
        )
        conflict = workspace.execute("turn-2", "print('different')\n")
        snapshot = workspace.snapshot("snap-1")
    finally:
        workspace.stop()

    assert start["package_manifest"]["environment_allowlist"] == []
    assert start["sandbox_attestation"]["network"] == "none"
    assert first["status"] == "OK"
    assert "turn2 43.0" in second["stdout"]
    assert second["tau_admission_status"] == "not_admitted"
    assert duplicate["idempotent_replay"] is True
    assert conflict["status"] == "BLOCKED"
    assert "duplicate_execution_id_conflict" in conflict["errors"]
    assert snapshot["serializable_state"]["value"] == 40
    assert "helper" in snapshot["unsupported_state"]
    assert "math" in snapshot["unsupported_state"]


def _request(*, image: str = DEFAULT_WORKSPACE_IMAGE) -> SandboxedPythonWorkspaceRequest:
    return SandboxedPythonWorkspaceRequest(
        run_id="run-317",
        node_id="python-node",
        attempt_id="attempt-1",
        attempt_number=1,
        goal_hash=canonical_sha256({"goal": "issue-317"}),
        plan_hash=canonical_sha256({"plan": "issue-317"}),
        work_order_sha256=canonical_sha256({"work": "issue-317"}),
        policy_sha256=canonical_sha256({"policy": "zero-trust"}),
        data_boundary_sha256=canonical_sha256({"boundary": "local"}),
        worktree_sha256=canonical_sha256({"worktree": "test"}),
        sandbox_attestation_sha256=canonical_sha256({"sandbox": "docker-none"}),
        image=image,
    )


def _require_local_image(image: str) -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip(f"workspace image unavailable locally: {image}")
