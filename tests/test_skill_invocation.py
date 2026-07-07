import json
from pathlib import Path

from tau_coding.skill_invocation import (
    SKILL_ARTIFACT_BINDING_SCHEMA,
    SKILL_INVOCATION_RECEIPT_SCHEMA,
    SKILL_INVOCATION_REQUEST_SCHEMA,
    write_skill_invocation_receipt,
)


def test_skill_invocation_dry_run_does_not_execute(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    request_path = _write_request(
        tmp_path,
        {
            "mode": "dry_run",
            "command": ["python3", "-c", f"open({str(marker)!r}, 'w').write('ran')"],
        },
    )

    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=tmp_path / "receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["schema"] == SKILL_INVOCATION_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["execution"] is None
    assert not marker.exists()


def test_skill_invocation_execute_records_stdout_stderr(tmp_path: Path) -> None:
    request_path = _write_request(
        tmp_path,
        {
            "mode": "execute",
            "command": [
                "python3",
                "-c",
                "import sys; print('skill-out'); print('skill-err', file=sys.stderr)",
            ],
        },
    )

    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=tmp_path / "receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "PASS"
    assert receipt["execution"]["exit_code"] == 0
    assert receipt["execution"]["stdout"] == "skill-out\n"
    assert receipt["execution"]["stderr"] == "skill-err\n"


def test_skill_invocation_ingest_existing_hashes_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "debugger-proof.json"
    artifact.write_text('{"schema":"debugger.proof.v1"}\n', encoding="utf-8")
    request_path = _write_request(
        tmp_path,
        {
            "mode": "ingest_existing",
            "artifacts": [
                {
                    "path": "debugger-proof.json",
                    "schema": "debugger.proof.v1",
                }
            ],
        },
    )

    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=tmp_path / "receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "PASS"
    assert receipt["artifacts"][0]["schema"] == SKILL_ARTIFACT_BINDING_SCHEMA
    assert receipt["artifacts"][0]["declared_schema"] == "debugger.proof.v1"
    assert receipt["artifacts"][0]["sha256"].startswith("sha256:")
    assert receipt["artifacts"][0]["bytes"] == artifact.stat().st_size


def test_skill_invocation_blocks_missing_goal_hash_in_zero_trust(tmp_path: Path) -> None:
    request_path = _write_request(
        tmp_path,
        {
            "mode": "dry_run",
            "zero_trust": True,
            "goal_hash": None,
            "command": ["echo", "nope"],
        },
    )

    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=tmp_path / "receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["ok"] is False
    assert "goal_hash is required when zero_trust is true" in receipt["errors"]


def test_skill_invocation_blocks_mocked_when_live_required(tmp_path: Path) -> None:
    request_path = _write_request(
        tmp_path,
        {
            "mode": "dry_run",
            "zero_trust": True,
            "live_required": True,
            "mocked": True,
            "live": False,
            "command": ["echo", "mocked"],
        },
    )

    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=tmp_path / "receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "live execution is required when live_required is true" in receipt["errors"]
    assert "mocked execution is forbidden when live_required is true" in receipt["errors"]


def test_skill_invocation_blocks_artifact_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-skill-artifact.txt"
    outside.write_text("outside\n", encoding="utf-8")
    request_path = _write_request(
        tmp_path,
        {
            "mode": "ingest_existing",
            "artifacts": [{"path": str(outside), "schema": "debugger.proof.v1"}],
        },
    )

    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=tmp_path / "receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["ok"] is False
    assert any("escapes repo root" in error for error in receipt["errors"])


def _write_request(tmp_path: Path, updates: dict) -> Path:
    payload = {
        "schema": SKILL_INVOCATION_REQUEST_SCHEMA,
        "skill": "debugger",
        "capability": "debug_runtime_state",
        "mode": "dry_run",
        "run_id": "run-001",
        "dag_id": "dag-001",
        "node_id": "debug-node",
        "goal_hash": "sha256:goal",
        "work_order_sha256": "sha256:work-order",
        "command": ["echo", "debug"],
        "artifacts": [],
        "policy_profile_sha256": "sha256:policy",
        "data_boundary_sha256": "sha256:boundary",
        "mocked": False,
        "live": False,
        "provider_live": False,
    }
    payload.update(updates)
    request_path = tmp_path / "skill-invocation-request.json"
    request_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return request_path
