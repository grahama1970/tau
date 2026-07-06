import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.sandbox_policy import sandbox_policy_alerts
from tau_coding.sandbox_run import run_sandboxed_command


def test_sandbox_policy_accepts_zero_trust_local_only_profile() -> None:
    alerts = sandbox_policy_alerts(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert alerts == []


def test_sandbox_policy_blocks_network_allow_profile() -> None:
    policy = _policy_profile()
    policy["network"]["default"] = "allow"

    alerts = sandbox_policy_alerts(policy_profile=policy, data_boundary=_data_boundary())

    assert alerts[0]["code"] == "network_not_default_deny"


def test_sandbox_run_blocks_unsupported_backend_before_execution(tmp_path: Path) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        backend="not-a-sandbox",
    )

    assert receipt["schema"] == "tau.sandbox_run_receipt.v1"
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["command_executed"] is False
    assert "unsupported_backend" in receipt["alert_codes"]


def test_sandbox_run_blocks_missing_bwrap_backend_before_execution(tmp_path: Path) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        backend="bwrap-missing",
    )

    assert receipt["ok"] is False
    assert receipt["command_executed"] is False
    assert "unsupported_backend" in receipt["alert_codes"]


def test_sandbox_run_docker_backend_requires_image(tmp_path: Path) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        backend="docker",
    )

    assert receipt["ok"] is False
    assert receipt["command_executed"] is False
    assert "missing_docker_image" in receipt["alert_codes"]


def test_sandbox_run_docker_backend_uses_strict_docker_policy(tmp_path: Path) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        backend="docker",
        image="python:3.12",
    )

    assert receipt["ok"] is False
    assert receipt["command_executed"] is False
    assert receipt["backend"]["name"] == "docker"
    assert "unpinned_image" in receipt["alert_codes"]
    assert receipt["policy_profile"]["schema"] == "tau.policy_profile.v1"
    assert receipt["data_boundary"]["schema"] == "tau.data_boundary.v1"


def test_cli_sandbox_run_writes_blocked_receipt_for_policy_rejection(tmp_path: Path) -> None:
    policy = _policy_profile()
    policy["network"]["default"] = "allow"
    policy_path, boundary_path = _write_policy_inputs(tmp_path, policy=policy)
    receipt_path = tmp_path / "sandbox-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "sandbox-run",
            "--policy-profile",
            str(policy_path),
            "--data-boundary",
            str(boundary_path),
            "--out",
            str(receipt_path),
            "--",
            sys.executable,
            "-c",
            "print('should-not-run')",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == "tau.sandbox_run_receipt.v1"
    assert payload["status"] == "BLOCKED"
    assert payload["command_executed"] is False
    assert "network_not_default_deny" in payload["alert_codes"]
    assert receipt_path.exists()


def test_cli_sandbox_run_docker_backend_writes_blocked_receipt(tmp_path: Path) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)
    receipt_path = tmp_path / "sandbox-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "sandbox-run",
            "--policy-profile",
            str(policy_path),
            "--data-boundary",
            str(boundary_path),
            "--backend",
            "docker",
            "--image",
            "python:3.12",
            "--out",
            str(receipt_path),
            "--",
            sys.executable,
            "-c",
            "print('should-not-run')",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["status"] == "BLOCKED"
    assert payload["command_executed"] is False
    assert "unpinned_image" in payload["alert_codes"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == payload


def _write_policy_inputs(
    tmp_path: Path,
    *,
    policy: dict | None = None,
    boundary: dict | None = None,
) -> tuple[Path, Path]:
    policy_path = tmp_path / "policy-profile.json"
    boundary_path = tmp_path / "data-boundary.json"
    policy_path.write_text(json.dumps(policy or _policy_profile()), encoding="utf-8")
    boundary_path.write_text(json.dumps(boundary or _data_boundary()), encoding="utf-8")
    return policy_path, boundary_path


def _policy_profile() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
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
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "export_controlled": True,
        "itar": True,
        "technical_data": True,
        "foreign_person_access": "prohibited",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }
