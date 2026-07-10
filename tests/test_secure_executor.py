import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from tau_coding.secure_executor import execute_secure_command
from tau_coding.security_capability import compile_capability_decision


def test_secure_executor_real_bwrap_passes_or_fails_closed(
    tmp_path: Path,
) -> None:
    policy_path, boundary_path = _write_inputs(tmp_path)
    command = [
        "/usr/bin/python3",
        "-c",
        (
            "import json, os; "
            "print(json.dumps({'secure': os.environ['TAU_SECURITY_MODE'], "
            "'host_secret_present': 'TAU_TEST_HOST_SECRET' in os.environ}))"
        ),
    ]
    decision, context = _decision(tmp_path, command_target="python3")
    os.environ["TAU_TEST_HOST_SECRET"] = "must-not-cross-boundary"
    try:
        result = execute_secure_command(
            command=command,
            stdin_text="",
            timeout_seconds=10,
            backend="bwrap",
            receipt_dir=tmp_path / "execution",
            policy_profile_path=policy_path,
            data_boundary_path=boundary_path,
            grants=decision["grants"],
            run_id="run-001",
            dag_id="dag-001",
            node_id="coder",
            attempt=1,
            goal_hash="sha256:goal",
            security_context_sha256=str(context["security_context_sha256"]),
            policy_profile_sha256=_sha256_uri(policy_path),
            data_boundary_sha256=_sha256_uri(boundary_path),
        )
    finally:
        os.environ.pop("TAU_TEST_HOST_SECRET", None)

    assert result.receipt["mocked"] is False
    assert result.receipt["live"] is True
    assert result.receipt["host_environment_inherited"] is False
    assert result.receipt["network_egress"] == "denied"
    assert Path(result.receipt["receipt_path"]).is_file()
    if result.receipt["status"] == "PASS":
        assert result.receipt["command_executed"] is True
        assert json.loads(result.stdout) == {
            "secure": "secure",
            "host_secret_present": False,
        }
    else:
        assert result.receipt["command_executed"] is False
        assert result.stdout == ""
        assert "sandbox_backend_unavailable" in result.receipt["alert_codes"]


def test_secure_executor_blocks_missing_process_grant_without_execution(
    tmp_path: Path,
) -> None:
    policy_path, boundary_path = _write_inputs(tmp_path)
    context = _context(policy_path, boundary_path)

    result = execute_secure_command(
        command=["/usr/bin/python3", "-c", "print('must-not-run')"],
        stdin_text="",
        timeout_seconds=10,
        backend="bwrap",
        receipt_dir=tmp_path / "execution",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        grants=[],
        run_id="run-001",
        dag_id="dag-001",
        node_id="coder",
        attempt=1,
        goal_hash="sha256:goal",
        security_context_sha256=str(context["security_context_sha256"]),
        policy_profile_sha256=_sha256_uri(policy_path),
        data_boundary_sha256=_sha256_uri(boundary_path),
    )

    assert result.receipt["status"] == "BLOCKED"
    assert result.receipt["command_executed"] is False
    assert result.stdout == ""
    assert "secure_executor_process_execute_grant_missing" in result.receipt["alert_codes"]


def test_secure_executor_blocks_attempt_reuse_without_execution(tmp_path: Path) -> None:
    policy_path, boundary_path = _write_inputs(tmp_path)
    decision, context = _decision(tmp_path, command_target="python3")

    result = execute_secure_command(
        command=["/usr/bin/python3", "-c", "print('must-not-run')"],
        stdin_text="",
        timeout_seconds=10,
        backend="bwrap",
        receipt_dir=tmp_path / "execution",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        grants=decision["grants"],
        run_id="run-001",
        dag_id="dag-001",
        node_id="coder",
        attempt=2,
        goal_hash="sha256:goal",
        security_context_sha256=str(context["security_context_sha256"]),
        policy_profile_sha256=_sha256_uri(policy_path),
        data_boundary_sha256=_sha256_uri(boundary_path),
    )

    assert result.receipt["status"] == "BLOCKED"
    assert result.receipt["command_executed"] is False
    assert "secure_executor_grant_binding_mismatch" in result.receipt["alert_codes"]


def _decision(tmp_path: Path, *, command_target: str) -> tuple[dict, dict]:
    policy_path = tmp_path / "policy-profile.json"
    boundary_path = tmp_path / "data-boundary.json"
    context = _context(policy_path, boundary_path)
    decision = compile_capability_decision(
        dag_id="dag-001",
        run_id="run-001",
        goal_hash="sha256:goal",
        security_context=context,
        command_policy={
            "schema": "tau.command_spec_policy.v1",
            "allowed_command_roots": ["python3"],
            "allows_network": False,
            "allows_mutation": False,
            "capability_grant_ttl_seconds": 300,
            "capability_rules": [
                {
                    "capability": "process.execute",
                    "targets": [command_target],
                    "resource_scope": ["empty-workdir"],
                    "maximum_effect": {"max_processes": 1},
                }
            ],
        },
        nodes=[
            {
                "node_id": "coder",
                "executor": "local",
                "attempt": 1,
                "requested_capabilities": [
                    {
                        "capability": "process.execute",
                        "target": command_target,
                        "resource_scope": ["empty-workdir"],
                        "maximum_effect": {"max_processes": 1},
                    }
                ],
            }
        ],
        receipt_dir=tmp_path / "grants",
        issued_at=datetime.now(UTC),
    )
    assert decision["status"] == "PASS"
    return decision, context


def _context(policy_path: Path, boundary_path: Path) -> dict:
    return {
        "schema": "tau.security_context.v1",
        "security_mode": "secure",
        "security_context_sha256": "sha256:security-context",
        "policy_profile": {"sha256": _sha256_uri(policy_path)},
        "data_boundary": {"sha256": _sha256_uri(boundary_path)},
        "actor": {"actor_id": "human:operator", "sha256": "sha256:actor"},
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    policy_path = tmp_path / "policy-profile.json"
    policy_path.write_text(
        json.dumps(
            {
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
                "github": {
                    "public_mutation": "deny",
                    "dry_run_projection": "allow",
                },
                "filesystem": {"write_allowlist": [], "read_denylist": []},
            }
        ),
        encoding="utf-8",
    )
    boundary_path = tmp_path / "data-boundary.json"
    boundary_path.write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )
    return policy_path, boundary_path


def _sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
