"""Live conformance receipt for Tau secure command execution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.secure_executor import execute_secure_command
from tau_coding.security_capability import compile_capability_decision

SECURE_EXECUTION_CONFORMANCE_SCHEMA = "tau.secure_execution_conformance.v1"
RUN_ID = "secure-execution-conformance-run"
DAG_ID = "secure-execution-conformance-dag"
NODE_ID = "secure-worker"
GOAL_HASH = "sha256:secure-execution-conformance"
SECURITY_CONTEXT_SHA256 = "sha256:secure-execution-context"
ACTOR_SHA256 = "sha256:secure-execution-actor"


def write_secure_execution_conformance(
    output: Path,
    *,
    allow_live_sandbox: bool,
    backend: str = "auto",
) -> dict[str, Any]:
    """Exercise secure execution fail-closed behavior with live sandbox calls."""

    if not allow_live_sandbox:
        raise RuntimeError("--allow-live-sandbox is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    fixture_dir = proof_dir / "fixtures"
    receipt_root = proof_dir / "runs"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    selected_backend, selected_image, backend_selection = _select_backend(backend)

    policy_path = fixture_dir / "policy-profile.json"
    boundary_path = fixture_dir / "data-boundary.json"
    host_secret_path = fixture_dir / "host-secret.txt"
    host_write_target = fixture_dir / "host-write-target.txt"
    _write_json(policy_path, _policy_profile())
    _write_json(boundary_path, _data_boundary())
    host_secret_path.write_text("tau-host-secret-must-not-cross-boundary\n", encoding="utf-8")
    host_write_target.write_text("unchanged\n", encoding="utf-8")

    policy_sha256 = _sha256_uri(policy_path)
    boundary_sha256 = _sha256_uri(boundary_path)
    grants = _compile_grants(
        receipt_dir=receipt_root / "capability-decision",
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
    )

    runs: dict[str, dict[str, Any]] = {}
    runs["positive_isolated_command"] = _secure_run(
        name="positive-isolated-command",
        receipt_root=receipt_root,
        command=[
            "python3",
            "-c",
            (
                "import json, os; "
                "print(json.dumps({"
                "'secure': os.environ.get('TAU_SECURITY_MODE'), "
                "'run_id': os.environ.get('TAU_RUN_ID'), "
                "'host_secret_present': 'TAU_CONFORMANCE_HOST_SECRET' in os.environ"
                "}, sort_keys=True))"
            ),
        ],
        grants=grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
        child_environment={"TAU_CONFORMANCE_ALLOWED": "yes"},
    )
    runs["undeclared_read_denied"] = _secure_run(
        name="undeclared-read-denied",
        receipt_root=receipt_root,
        command=[
            "python3",
            "-c",
            f"from pathlib import Path; print(Path({str(host_secret_path)!r}).read_text())",
        ],
        grants=grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
    )
    runs["path_escape_denied"] = _secure_run(
        name="path-escape-denied",
        receipt_root=receipt_root,
        command=["python3", "-c", "open('/etc/shadow', 'rb').read(1)"],
        grants=grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
    )
    runs["undeclared_write_denied"] = _secure_run(
        name="undeclared-write-denied",
        receipt_root=receipt_root,
        command=[
            "python3",
            "-c",
            f"from pathlib import Path; Path({str(host_write_target)!r}).write_text('changed')",
        ],
        grants=grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
    )
    runs["secret_environment_denied"] = _secure_run(
        name="secret-environment-denied",
        receipt_root=receipt_root,
        command=[
            "python3",
            "-c",
            (
                "import os, sys; "
                "sys.exit(0 if 'TAU_CONFORMANCE_HOST_SECRET' not in os.environ else 23)"
            ),
        ],
        grants=grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
    )
    runs["undeclared_egress_denied"] = _secure_run(
        name="undeclared-egress-denied",
        receipt_root=receipt_root,
        command=[
            "python3",
            "-c",
            (
                "import socket; "
                "socket.create_connection(('93.184.216.34', 80), timeout=1.0)"
            ),
        ],
        grants=grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
    )
    runs["wrong_attempt_grant_denied"] = _secure_run(
        name="wrong-attempt-grant-denied",
        receipt_root=receipt_root,
        command=["python3", "-c", "print('must-not-run')"],
        grants=grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
        attempt=2,
    )
    expired_grants = [dict(grant, expires_at="2000-01-01T00:00:00Z") for grant in grants]
    expired_grants = [
        {**grant, "grant_sha256": _grant_sha256(grant)}
        for grant in expired_grants
    ]
    runs["expired_grant_denied"] = _secure_run(
        name="expired-grant-denied",
        receipt_root=receipt_root,
        command=["python3", "-c", "print('must-not-run')"],
        grants=expired_grants,
        policy_path=policy_path,
        boundary_path=boundary_path,
        policy_sha256=policy_sha256,
        boundary_sha256=boundary_sha256,
        backend=selected_backend,
        image=selected_image,
    )

    checks = _checks(runs, host_write_target=host_write_target)
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    status = "PASS" if not failed_checks else "BLOCKED"
    payload = {
        "schema": SECURE_EXECUTION_CONFORMANCE_SCHEMA,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "backend": {
            "requested": backend,
            "selected": selected_backend,
            "image": selected_image,
            "selection": backend_selection,
            "path": shutil.which(selected_backend),
            "available": shutil.which(selected_backend) is not None,
        },
        "output": str(resolved_output),
        "fixture_dir": str(fixture_dir),
        "receipt_root": str(receipt_root),
        "policy_profile": _artifact(policy_path),
        "data_boundary": _artifact(boundary_path),
        "host_secret": _artifact(host_secret_path),
        "host_write_target": _artifact(host_write_target),
        "checks": checks,
        "failed_checks": failed_checks,
        "runs": runs,
        "proof_scope": {
            "proves": [
                (
                    "Tau launched the positive command through secure_executor and the "
                    "recorded sandbox backend when status is PASS."
                ),
                "Tau did not inherit the host environment into the secure child process.",
                (
                    "Tau denied host file reads, path escapes, host writes, undeclared "
                    "egress, expired grants, and wrong-attempt grants in this live "
                    "sandbox environment when status is PASS."
                ),
                (
                    "Tau did not fall back to direct subprocess execution for "
                    "pre-dispatch grant denials."
                ),
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Kernel or Bubblewrap vulnerability absence.",
                "Arbitrary production workload safety.",
                "Provider/model semantic quality.",
                "Network allow-grant behavior.",
            ],
        },
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    _write_json(resolved_output, payload)
    return payload


def _secure_run(
    *,
    name: str,
    receipt_root: Path,
    command: list[str],
    grants: list[dict[str, Any]],
    policy_path: Path,
    boundary_path: Path,
    policy_sha256: str,
    boundary_sha256: str,
    backend: str,
    image: str | None,
    attempt: int = 1,
    child_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    os.environ["TAU_CONFORMANCE_HOST_SECRET"] = "must-not-cross-boundary"
    try:
        result = execute_secure_command(
            command=command,
            stdin_text="",
            timeout_seconds=10,
            backend=backend,
            receipt_dir=receipt_root / name,
            policy_profile_path=policy_path,
            data_boundary_path=boundary_path,
            grants=grants,
            run_id=RUN_ID,
            dag_id=DAG_ID,
            node_id=NODE_ID,
            attempt=attempt,
            goal_hash=GOAL_HASH,
            security_context_sha256=SECURITY_CONTEXT_SHA256,
            policy_profile_sha256=policy_sha256,
            data_boundary_sha256=boundary_sha256,
            image=image,
            child_environment=child_environment,
        )
    finally:
        os.environ.pop("TAU_CONFORMANCE_HOST_SECRET", None)
    parsed_stdout = _parse_json_stdout(result.stdout)
    return {
        "status": result.receipt.get("status"),
        "exit_code": result.returncode,
        "command_executed": result.receipt.get("command_executed"),
        "alert_codes": list(result.receipt.get("alert_codes", [])),
        "receipt_path": result.receipt.get("receipt_path"),
        "stdout": result.receipt.get("stdout"),
        "stderr": result.receipt.get("stderr"),
        "stdout_json": parsed_stdout,
        "sandbox_command": result.receipt.get("sandbox_command"),
        "host_environment_inherited": result.receipt.get("host_environment_inherited"),
        "network_egress": result.receipt.get("network_egress"),
    }


def _checks(runs: Mapping[str, Mapping[str, Any]], *, host_write_target: Path) -> dict[str, bool]:
    positive = runs["positive_isolated_command"]
    stdout_json = positive.get("stdout_json")
    stdout_map = stdout_json if isinstance(stdout_json, Mapping) else {}
    return {
        "positive_isolated_command_passed": positive.get("status") == "PASS"
        and positive.get("command_executed") is True
        and stdout_map.get("secure") == "secure"
        and stdout_map.get("run_id") == RUN_ID,
        "host_environment_not_inherited": positive.get("host_environment_inherited") is False
        and stdout_map.get("host_secret_present") is False,
        "undeclared_read_denied": _sandbox_operation_denied(runs["undeclared_read_denied"]),
        "path_escape_denied": _sandbox_operation_denied(runs["path_escape_denied"]),
        "secret_access_denied": runs["secret_environment_denied"].get("status") == "PASS",
        "undeclared_write_denied": _sandbox_operation_denied(runs["undeclared_write_denied"])
        and host_write_target.read_text(encoding="utf-8") == "unchanged\n",
        "undeclared_egress_denied": _sandbox_operation_denied(runs["undeclared_egress_denied"]),
        "wrong_attempt_grant_denied_before_execution": (
            runs["wrong_attempt_grant_denied"].get("status") == "BLOCKED"
            and runs["wrong_attempt_grant_denied"].get("command_executed") is False
            and "secure_executor_grant_binding_mismatch"
            in runs["wrong_attempt_grant_denied"].get("alert_codes", [])
        ),
        "expired_grant_denied_before_execution": (
            runs["expired_grant_denied"].get("status") == "BLOCKED"
            and runs["expired_grant_denied"].get("command_executed") is False
            and "secure_executor_grant_expired"
            in runs["expired_grant_denied"].get("alert_codes", [])
        ),
        "secure_mode_did_not_fallback_to_direct_subprocess": (
            runs["wrong_attempt_grant_denied"].get("command_executed") is False
            and runs["expired_grant_denied"].get("command_executed") is False
        ),
    }


def _sandbox_operation_denied(run: Mapping[str, Any]) -> bool:
    return (
        run.get("status") == "BLOCKED"
        and run.get("command_executed") is True
        and (
            "sandboxed_command_failed" in run.get("alert_codes", [])
            or "docker_command_nonzero" in run.get("alert_codes", [])
        )
    )


def _compile_grants(
    *,
    receipt_dir: Path,
    policy_sha256: str,
    boundary_sha256: str,
) -> list[dict[str, Any]]:
    receipt = compile_capability_decision(
        dag_id=DAG_ID,
        run_id=RUN_ID,
        goal_hash=GOAL_HASH,
        security_context=_security_context(
            policy_sha256=policy_sha256,
            boundary_sha256=boundary_sha256,
        ),
        command_policy={
            "schema": "tau.command_spec_policy.v1",
            "allowed_command_roots": ["python3"],
            "allows_network": False,
            "allows_mutation": False,
            "capability_grant_ttl_seconds": 300,
            "capability_rules": [
                {
                    "capability": "process.execute",
                    "targets": ["python3"],
                    "resource_scope": ["empty-workdir"],
                    "maximum_effect": {"max_processes": 1},
                }
            ],
        },
        nodes=[
            {
                "node_id": NODE_ID,
                "executor": "local",
                "attempt": 1,
                "requested_capabilities": [
                    {
                        "capability": "process.execute",
                        "target": "python3",
                        "resource_scope": ["empty-workdir"],
                        "maximum_effect": {"max_processes": 1},
                    }
                ],
            }
        ],
        receipt_dir=receipt_dir,
    )
    if receipt.get("status") != "PASS":
        raise RuntimeError(f"capability grant compilation failed: {receipt.get('alert_codes')}")
    grants = receipt.get("grants")
    if not isinstance(grants, list) or not all(isinstance(item, dict) for item in grants):
        raise RuntimeError("capability grant compilation did not produce grants")
    return grants


def _select_backend(requested: str) -> tuple[str, str | None, dict[str, Any]]:
    if requested != "auto":
        explicit_image = _default_docker_image() if requested in {"docker", "docker-sbx"} else None
        return requested, explicit_image, {
            "mode": "explicit",
            "requested": requested,
        }
    bwrap_path = shutil.which("bwrap")
    bwrap_probe = _probe_bwrap()
    if bwrap_probe.get("ok") is True:
        return "bwrap", None, {
            "mode": "auto",
            "reason": "bwrap_probe_passed",
            "bwrap_path": bwrap_path,
            "bwrap_probe": bwrap_probe,
        }
    docker_image = _default_docker_image()
    docker_path = shutil.which("docker")
    if docker_path is not None and docker_image is not None:
        return "docker", docker_image, {
            "mode": "auto",
            "reason": "bwrap_probe_blocked_docker_digest_available",
            "bwrap_path": bwrap_path,
            "bwrap_probe": bwrap_probe,
            "docker_path": docker_path,
            "docker_image": docker_image,
        }
    return "bwrap", None, {
        "mode": "auto",
        "reason": "no_supported_live_backend_available",
        "bwrap_path": bwrap_path,
        "bwrap_probe": bwrap_probe,
        "docker_path": docker_path,
        "docker_image": docker_image,
    }


def _probe_bwrap() -> dict[str, Any]:
    bwrap_path = shutil.which("bwrap")
    if bwrap_path is None:
        return {"ok": False, "error": "bwrap executable not found"}
    command = [
        bwrap_path,
        "--unshare-net",
        "--die-with-parent",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/work",
        "--chdir",
        "/work",
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin:/usr/local/bin",
        "python3",
        "-c",
        "print('tau-secure-conformance-probe')",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": command, "error": str(exc)}
    return {
        "ok": result.returncode == 0,
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _default_docker_image() -> str | None:
    try:
        result = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "python:3.12-slim",
                "--format",
                "{{json .RepoDigests}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        digests = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(digests, list):
        return None
    for digest in digests:
        if isinstance(digest, str) and digest.startswith("python@sha256:"):
            return digest
    return None


def _security_context(*, policy_sha256: str, boundary_sha256: str) -> dict[str, Any]:
    return {
        "schema": "tau.security_context.v1",
        "security_mode": "secure",
        "security_context_sha256": SECURITY_CONTEXT_SHA256,
        "policy_profile": {"sha256": policy_sha256},
        "data_boundary": {"sha256": boundary_sha256},
        "actor": {"actor_id": "human:operator", "sha256": ACTOR_SHA256},
    }


def _policy_profile() -> dict[str, Any]:
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


def _data_boundary() -> dict[str, Any]:
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


def _parse_json_stdout(value: str) -> dict[str, Any] | None:
    if not value.strip():
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _grant_sha256(grant: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in grant.items()
        if key not in {"grant_sha256", "grant_path"}
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _artifact(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "sha256": _sha256_uri(resolved),
        "bytes": resolved.stat().st_size,
    }


def _sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
