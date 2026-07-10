from datetime import UTC, datetime
from pathlib import Path

from tau_coding.security_capability import compile_capability_decision


def test_capability_compiler_grants_exact_policy_scope(tmp_path: Path) -> None:
    receipt = compile_capability_decision(
        dag_id="secure-capability-test",
        run_id="run-001",
        goal_hash="sha256:goal",
        security_context=_security_context(),
        command_policy=_command_policy(),
        nodes=[_node(_process_execute_request())],
        receipt_dir=tmp_path,
        issued_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    )

    assert receipt["status"] == "PASS"
    assert receipt["request_count"] == 1
    assert receipt["grant_count"] == 1
    grant = receipt["grants"][0]
    assert grant["schema"] == "tau.capability_grant.v1"
    assert grant["capability"] == "process.execute"
    assert grant["goal_hash"] == "sha256:goal"
    assert grant["security_context_sha256"] == "sha256:security-context"
    assert grant["expires_at"] == "2026-07-10T12:05:00Z"
    assert Path(grant["grant_path"]).is_file()


def test_capability_compiler_blocks_scope_escalation_without_issuing_grant(
    tmp_path: Path,
) -> None:
    request = _process_execute_request()
    request["resource_scope"] = ["/home"]

    receipt = compile_capability_decision(
        dag_id="secure-capability-test",
        run_id="run-001",
        goal_hash="sha256:goal",
        security_context=_security_context(),
        command_policy=_command_policy(),
        nodes=[_node(request)],
        receipt_dir=tmp_path,
        issued_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["grant_count"] == 0
    assert receipt["grants"] == []
    assert "capability_request_denied" in receipt["alert_codes"]
    assert not (tmp_path / "capability-grants").exists()
    assert (tmp_path / "capability-requests" / "coder" / "000.json").is_file()


def test_capability_compiler_blocks_executable_node_without_process_execute(
    tmp_path: Path,
) -> None:
    receipt = compile_capability_decision(
        dag_id="secure-capability-test",
        run_id="run-001",
        goal_hash="sha256:goal",
        security_context=_security_context(),
        command_policy=_command_policy(),
        nodes=[
            _node(
                {
                    "capability": "filesystem.read",
                    "target": "repository",
                    "resource_scope": ["src/**"],
                    "maximum_effect": {"read_only": True},
                }
            )
        ],
        receipt_dir=tmp_path,
        issued_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["grant_count"] == 0
    assert any(
        "must request process.execute" in alert["message"] for alert in receipt["alerts"]
    )


def test_capability_compiler_blocks_network_capability_when_network_is_denied(
    tmp_path: Path,
) -> None:
    policy = _command_policy()
    policy["capability_rules"].append(
        {
            "capability": "network.connect",
            "targets": ["127.0.0.1:8601"],
            "resource_scope": ["POST /intent"],
            "maximum_effect": {"max_requests": 1},
        }
    )
    receipt = compile_capability_decision(
        dag_id="secure-capability-test",
        run_id="run-001",
        goal_hash="sha256:goal",
        security_context=_security_context(),
        command_policy=policy,
        nodes=[
            {
                "node_id": "coder",
                "executor": "local",
                "attempt": 1,
                "requested_capabilities": [
                    _process_execute_request(),
                    {
                        "capability": "network.connect",
                        "target": "127.0.0.1:8601",
                        "resource_scope": ["POST /intent"],
                        "maximum_effect": {"max_requests": 1},
                    },
                ],
            }
        ],
        receipt_dir=tmp_path,
        issued_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["grant_count"] == 0
    assert any("allows_network=true" in alert["message"] for alert in receipt["alerts"])


def _security_context() -> dict[str, object]:
    return {
        "schema": "tau.security_context.v1",
        "security_mode": "secure",
        "security_context_sha256": "sha256:security-context",
        "policy_profile": {"sha256": "sha256:policy"},
        "data_boundary": {"sha256": "sha256:boundary"},
        "actor": {"actor_id": "human:operator", "sha256": "sha256:actor"},
    }


def _command_policy() -> dict[str, object]:
    return {
        "schema": "tau.command_spec_policy.v1",
        "allowed_command_roots": ["python3"],
        "allowed_cwd_roots": ["."],
        "allows_network": False,
        "allows_mutation": False,
        "capability_grant_ttl_seconds": 300,
        "capability_rules": [
            {
                "capability": "process.execute",
                "targets": ["python3"],
                "resource_scope": ["repository"],
                "maximum_effect": {"max_processes": 1},
            },
            {
                "capability": "filesystem.read",
                "targets": ["repository"],
                "resource_scope": ["src/**"],
                "maximum_effect": {"read_only": True},
            },
        ],
    }


def _node(request: dict[str, object]) -> dict[str, object]:
    return {
        "node_id": "coder",
        "executor": "local",
        "attempt": 1,
        "requested_capabilities": [request],
    }


def _process_execute_request() -> dict[str, object]:
    return {
        "capability": "process.execute",
        "target": "python3",
        "resource_scope": ["repository"],
        "maximum_effect": {"max_processes": 1},
    }
