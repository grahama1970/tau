import json
import sys
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import tau_coding.sandbox_run as sandbox_run
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


def test_sandbox_run_writes_blocked_receipt_for_missing_policy_file(
    tmp_path: Path,
) -> None:
    boundary_path = tmp_path / "data-boundary.json"
    boundary_path.write_text(json.dumps(_data_boundary()), encoding="utf-8")
    receipt_path = tmp_path / "sandbox-receipt.json"

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=tmp_path / "missing-policy.json",
        data_boundary_path=boundary_path,
        receipt_path=receipt_path,
    )

    assert receipt["schema"] == "tau.sandbox_run_receipt.v1"
    assert receipt["status"] == "BLOCKED"
    assert receipt["command_executed"] is False
    assert "policy_profile_missing" in receipt["alert_codes"]
    assert receipt["policy_profile"]["exists"] is False
    assert receipt["policy_profile"]["sha256"] is None
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


def test_sandbox_run_writes_blocked_receipt_for_invalid_boundary_json(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "policy-profile.json"
    policy_path.write_text(json.dumps(_policy_profile()), encoding="utf-8")
    boundary_path = tmp_path / "data-boundary.json"
    boundary_path.write_text("{not-json", encoding="utf-8")
    receipt_path = tmp_path / "sandbox-receipt.json"

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["command_executed"] is False
    assert "data_boundary_unreadable" in receipt["alert_codes"]
    assert receipt["data_boundary"]["exists"] is True
    assert receipt["data_boundary"]["sha256"].startswith("sha256:")
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


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


def test_sandbox_run_passes_stdin_and_work_dir_to_bwrap(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)
    work_dir = tmp_path / "worker"
    work_dir.mkdir()
    observed: dict[str, Any] = {}

    def fake_probe(backend_path: str | None, *, work_dir: Path | None = None) -> dict:
        observed["probe_backend_path"] = backend_path
        observed["probe_work_dir"] = work_dir
        return {"ok": True, "command": ["bwrap", "probe"], "stdout": "ok\n", "stderr": ""}

    def fake_run(
        command: list[str],
        *,
        backend_path: Path,
        timeout_seconds: float,
        stdin_text: str | None,
        work_dir: Path | None,
    ) -> dict:
        observed["command"] = command
        observed["backend_path"] = backend_path
        observed["timeout_seconds"] = timeout_seconds
        observed["stdin_text"] = stdin_text
        observed["work_dir"] = work_dir
        return {
            "command": ["bwrap", "--bind", str(work_dir), "/work", *command],
            "returncode": 0,
            "stdout": "worker-ok\n",
            "stderr": "",
            "timed_out": False,
        }

    monkeypatch.setattr(sandbox_run, "_probe_bwrap", fake_probe)
    monkeypatch.setattr(sandbox_run, "_run_bwrap_command", fake_run)

    receipt = run_sandboxed_command(
        command=["/work/fake-omp", "--mode", "rpc", "--no-session"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        stdin_text='{"type":"prompt"}\n',
        work_dir=work_dir,
        backend="bwrap",
        timeout_seconds=12,
    )

    assert receipt["status"] == "PASS"
    assert receipt["command_executed"] is True
    assert receipt["work_dir"] == str(work_dir.resolve())
    assert receipt["stdin_bytes"] == len('{"type":"prompt"}\n'.encode("utf-8"))
    assert receipt["stdin_sha256"].startswith("sha256:")
    assert observed["probe_work_dir"] == work_dir.resolve()
    assert observed["stdin_text"] == '{"type":"prompt"}\n'
    assert observed["work_dir"] == work_dir.resolve()


def test_sandbox_run_records_goal_hash_for_worker_substrate(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)

    monkeypatch.setattr(
        sandbox_run,
        "_probe_bwrap",
        lambda backend_path, *, work_dir=None: {
            "ok": True,
            "command": ["bwrap", "probe"],
            "stdout": "ok\n",
            "stderr": "",
        },
    )
    monkeypatch.setattr(
        sandbox_run,
        "_run_bwrap_command",
        lambda command, *, backend_path, timeout_seconds, stdin_text, work_dir: {
            "command": ["bwrap", *command],
            "returncode": 0,
            "stdout": "worker-ok\n",
            "stderr": "",
            "timed_out": False,
        },
    )

    receipt = run_sandboxed_command(
        command=["/usr/bin/python3", "-c", "print('worker-ok')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        backend="bwrap",
        goal_hash="sha256:goal",
    )

    assert receipt["status"] == "PASS"
    assert receipt["goal_hash"] == "sha256:goal"
    assert receipt["command_executed"] is True


def test_sandbox_run_records_work_order_sha256_for_worker_substrate(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)
    work_order_sha256 = "sha256:" + ("a" * 64)

    monkeypatch.setattr(
        sandbox_run,
        "_probe_bwrap",
        lambda backend_path, *, work_dir=None: {
            "ok": True,
            "command": ["bwrap", "probe"],
            "stdout": "ok\n",
            "stderr": "",
        },
    )
    monkeypatch.setattr(
        sandbox_run,
        "_run_bwrap_command",
        lambda command, *, backend_path, timeout_seconds, stdin_text, work_dir: {
            "command": ["bwrap", *command],
            "returncode": 0,
            "stdout": "worker-ok\n",
            "stderr": "",
            "timed_out": False,
        },
    )

    receipt = run_sandboxed_command(
        command=["/usr/bin/python3", "-c", "print('worker-ok')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        backend="bwrap",
        goal_hash="sha256:goal",
        work_order_sha256=work_order_sha256,
    )

    assert receipt["status"] == "PASS"
    assert receipt["goal_hash"] == "sha256:goal"
    assert receipt["work_order_sha256"] == work_order_sha256
    assert receipt["command_executed"] is True


def test_sandbox_run_blocks_invalid_work_order_sha256_before_execution(
    tmp_path: Path,
) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        work_order_sha256="sha256:NOT-A-DIGEST",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["work_order_sha256"] == "sha256:NOT-A-DIGEST"
    assert receipt["command_executed"] is False
    assert "invalid_work_order_sha256" in receipt["alert_codes"]


def test_sandbox_run_blocks_missing_work_dir(tmp_path: Path) -> None:
    policy_path, boundary_path = _write_policy_inputs(tmp_path)

    receipt = run_sandboxed_command(
        command=[sys.executable, "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        work_dir=tmp_path / "missing",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["command_executed"] is False
    assert "invalid_work_dir" in receipt["alert_codes"]


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


def test_cli_sandbox_run_writes_blocked_receipt_for_missing_policy_file(
    tmp_path: Path,
) -> None:
    boundary_path = tmp_path / "data-boundary.json"
    boundary_path.write_text(json.dumps(_data_boundary()), encoding="utf-8")
    receipt_path = tmp_path / "sandbox-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "sandbox-run",
            "--policy-profile",
            str(tmp_path / "missing-policy.json"),
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
    assert "policy_profile_missing" in payload["alert_codes"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == payload


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


def test_cli_sandbox_run_accepts_stdin_file_and_work_dir(tmp_path: Path) -> None:
    policy = _policy_profile()
    policy["network"]["default"] = "allow"
    policy_path, boundary_path = _write_policy_inputs(tmp_path, policy=policy)
    stdin_path = tmp_path / "request.jsonl"
    stdin_path.write_text('{"type":"prompt"}\n', encoding="utf-8")
    work_dir = tmp_path / "worker"
    work_dir.mkdir()
    receipt_path = tmp_path / "sandbox-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "sandbox-run",
            "--policy-profile",
            str(policy_path),
            "--data-boundary",
            str(boundary_path),
            "--stdin-file",
            str(stdin_path),
            "--work-dir",
            str(work_dir),
            "--out",
            str(receipt_path),
            "--",
            "/work/fake-omp",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["status"] == "BLOCKED"
    assert payload["command_executed"] is False
    assert payload["stdin_bytes"] == stdin_path.stat().st_size
    assert payload["stdin_sha256"].startswith("sha256:")
    assert payload["work_dir"] == str(work_dir.resolve())
    assert "network_not_default_deny" in payload["alert_codes"]


def test_cli_sandbox_run_records_goal_hash_on_blocked_receipt(tmp_path: Path) -> None:
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
            "--goal-hash",
            "sha256:goal",
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
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["command_executed"] is False
    assert "network_not_default_deny" in payload["alert_codes"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == payload


def test_cli_sandbox_run_records_work_order_sha256_on_blocked_receipt(
    tmp_path: Path,
) -> None:
    policy = _policy_profile()
    policy["network"]["default"] = "allow"
    policy_path, boundary_path = _write_policy_inputs(tmp_path, policy=policy)
    receipt_path = tmp_path / "sandbox-receipt.json"
    work_order_sha256 = "sha256:" + ("b" * 64)

    result = CliRunner().invoke(
        app,
        [
            "sandbox-run",
            "--policy-profile",
            str(policy_path),
            "--data-boundary",
            str(boundary_path),
            "--goal-hash",
            "sha256:goal",
            "--work-order-sha256",
            work_order_sha256,
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
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["work_order_sha256"] == work_order_sha256
    assert payload["command_executed"] is False
    assert "network_not_default_deny" in payload["alert_codes"]
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
