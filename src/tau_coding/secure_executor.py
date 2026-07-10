"""Grant-bound Bubblewrap execution for secure Tau DAG nodes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.sandbox_run import run_sandboxed_command

SECURE_EXECUTION_RECEIPT_SCHEMA = "tau.secure_execution_receipt.v1"
SUPPORTED_SECURE_BACKENDS = {"bwrap"}


@dataclass(frozen=True)
class SecureExecutionResult:
    receipt: dict[str, Any]
    stdout: str
    stderr: str
    returncode: int | None


def execute_secure_command(
    *,
    command: Sequence[str],
    stdin_text: str,
    timeout_seconds: float,
    backend: str,
    receipt_dir: Path,
    policy_profile_path: Path,
    data_boundary_path: Path,
    grants: Sequence[Mapping[str, Any]],
    run_id: str,
    dag_id: str,
    node_id: str,
    attempt: int,
    goal_hash: str,
    security_context_sha256: str,
    policy_profile_sha256: str,
    data_boundary_sha256: str,
    child_environment: Mapping[str, str] | None = None,
    checked_at: datetime | None = None,
) -> SecureExecutionResult:
    """Validate grants and execute one command through Bubblewrap only."""

    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
    now = (checked_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    alerts: list[dict[str, Any]] = []

    if backend not in SUPPORTED_SECURE_BACKENDS:
        alerts.append(
            _alert(
                "secure_executor_backend_unsupported",
                f"Secure execution backend is not supported: {backend}",
            )
        )
    if not command or not all(isinstance(item, str) and item for item in command):
        alerts.append(_alert("secure_executor_missing_command", "Secure command is missing."))

    policy_hash = _sha256_uri(policy_profile_path)
    boundary_hash = _sha256_uri(data_boundary_path)
    if policy_hash != policy_profile_sha256:
        alerts.append(
            _alert(
                "secure_executor_policy_hash_mismatch",
                "Resolved policy profile changed after security-context compilation.",
            )
        )
    if boundary_hash != data_boundary_sha256:
        alerts.append(
            _alert(
                "secure_executor_boundary_hash_mismatch",
                "Resolved data boundary changed after security-context compilation.",
            )
        )

    grant_alerts, accepted_grant_hashes = _validate_grants(
        grants=grants,
        command=command,
        run_id=run_id,
        dag_id=dag_id,
        node_id=node_id,
        attempt=attempt,
        goal_hash=goal_hash,
        security_context_sha256=security_context_sha256,
        policy_profile_sha256=policy_profile_sha256,
        data_boundary_sha256=data_boundary_sha256,
        checked_at=now,
    )
    alerts.extend(grant_alerts)

    environment = {
        "TAU_SECURITY_MODE": "secure",
        "TAU_RUN_ID": run_id,
        "TAU_DAG_ID": dag_id,
        "TAU_DAG_NODE_ID": node_id,
        "TAU_DAG_ATTEMPT": str(attempt),
        "TAU_GOAL_HASH": goal_hash,
        "TAU_SECURITY_CONTEXT_SHA256": security_context_sha256,
    }
    if child_environment:
        environment.update(
            {
                str(key): str(value)
                for key, value in child_environment.items()
                if _safe_environment_name(str(key))
            }
        )

    stdout = ""
    stderr = ""
    returncode: int | None = None
    sandbox_receipt: dict[str, Any] | None = None
    if not alerts:
        environment_command = [
            "/usr/bin/env",
            *[f"{key}={value}" for key, value in sorted(environment.items())],
            *command,
        ]
        sandbox_receipt = run_sandboxed_command(
            command=environment_command,
            policy_profile_path=policy_profile_path,
            data_boundary_path=data_boundary_path,
            goal_hash=goal_hash,
            timeout_seconds=timeout_seconds,
            backend=backend,
            stdin_text=stdin_text,
            work_dir=None,
        )
        command_result = sandbox_receipt.get("command_result")
        if isinstance(command_result, Mapping):
            stdout = str(command_result.get("stdout") or "")
            stderr = str(command_result.get("stderr") or "")
            raw_returncode = command_result.get("returncode")
            returncode = raw_returncode if isinstance(raw_returncode, int) else None
        if sandbox_receipt.get("status") != "PASS":
            alerts.extend(
                _alert(
                    str(alert.get("code") or "secure_executor_sandbox_blocked"),
                    str(alert.get("message") or "Bubblewrap execution was blocked."),
                )
                for alert in sandbox_receipt.get("alerts", [])
                if isinstance(alert, Mapping)
            )

    stdout_path = resolved_receipt_dir / "stdout.txt"
    stderr_path = resolved_receipt_dir / "stderr.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    receipt_path = resolved_receipt_dir / "secure-execution-receipt.json"
    status = "PASS" if not alerts and returncode == 0 else "BLOCKED"
    sandbox_command = None
    backend_details = None
    if isinstance(sandbox_receipt, Mapping):
        backend_details = sandbox_receipt.get("backend")
        command_result = sandbox_receipt.get("command_result")
        if isinstance(command_result, Mapping):
            sandbox_command = command_result.get("command")
    receipt = {
        "schema": SECURE_EXECUTION_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_id": run_id,
        "dag_id": dag_id,
        "node_id": node_id,
        "attempt": attempt,
        "goal_hash": goal_hash,
        "security_context_sha256": security_context_sha256,
        "policy_profile_sha256": policy_profile_sha256,
        "data_boundary_sha256": data_boundary_sha256,
        "backend": backend_details or {"name": backend, "available": False},
        "sandbox_command": sandbox_command,
        "grant_sha256s": accepted_grant_hashes,
        "child_environment_names": sorted(environment),
        "host_environment_inherited": False,
        "network_egress": "denied",
        "command_executed": bool(
            isinstance(sandbox_receipt, Mapping)
            and sandbox_receipt.get("command_executed") is True
        ),
        "exit_code": returncode,
        "timed_out": bool(
            isinstance(sandbox_receipt, Mapping)
            and isinstance(sandbox_receipt.get("command_result"), Mapping)
            and sandbox_receipt["command_result"].get("timed_out") is True
        ),
        "stdout": _artifact_descriptor(stdout_path),
        "stderr": _artifact_descriptor(stderr_path),
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "receipt_path": str(receipt_path),
        "proof_scope": {
            "proves": [
                "Tau checked the process.execute grant binding before secure command launch.",
                "Tau used Bubblewrap with a new network namespace and an explicit empty-base "
                "child environment when command_executed is true.",
                "Tau did not fall back to direct subprocess execution when secure execution "
                "was blocked.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Kernel or Bubblewrap vulnerability absence.",
                "Scoped host filesystem access.",
                "Secret-reference injection.",
                "Network allow-grant enforcement.",
                "Secure retries beyond attempt 1.",
                "Provider/model semantic quality.",
            ],
        },
        "checked_at": _stamp(now),
    }
    _write_json(receipt_path, receipt)
    return SecureExecutionResult(
        receipt=receipt,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


def _validate_grants(
    *,
    grants: Sequence[Mapping[str, Any]],
    command: Sequence[str],
    run_id: str,
    dag_id: str,
    node_id: str,
    attempt: int,
    goal_hash: str,
    security_context_sha256: str,
    policy_profile_sha256: str,
    data_boundary_sha256: str,
    checked_at: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    alerts: list[dict[str, Any]] = []
    accepted: list[str] = []
    process_execute_found = False
    command_target = Path(str(command[0])).name if command else ""
    for index, grant in enumerate(grants):
        label = f"grant[{index}]"
        if grant.get("schema") != "tau.capability_grant.v1":
            alerts.append(_alert("secure_executor_grant_schema_mismatch", f"{label} schema."))
            continue
        bindings = {
            "run_id": run_id,
            "dag_id": dag_id,
            "node_id": node_id,
            "attempt": attempt,
            "goal_hash": goal_hash,
            "security_context_sha256": security_context_sha256,
            "policy_profile_sha256": policy_profile_sha256,
            "data_boundary_sha256": data_boundary_sha256,
        }
        mismatch = next(
            (field for field, expected in bindings.items() if grant.get(field) != expected),
            None,
        )
        if mismatch is not None:
            alerts.append(
                _alert(
                    "secure_executor_grant_binding_mismatch",
                    f"{label} does not match {mismatch}.",
                )
            )
            continue
        expected_hash = _grant_sha256(grant)
        if grant.get("grant_sha256") != expected_hash:
            alerts.append(
                _alert("secure_executor_grant_hash_mismatch", f"{label} hash mismatch.")
            )
            continue
        expires_at = _parse_stamp(grant.get("expires_at"))
        if expires_at is None or expires_at <= checked_at:
            alerts.append(_alert("secure_executor_grant_expired", f"{label} is expired."))
            continue
        if grant.get("capability") == "process.execute":
            target = str(grant.get("target") or "")
            if target not in {str(command[0]), command_target}:
                alerts.append(
                    _alert(
                        "secure_executor_command_target_mismatch",
                        f"{label} does not authorize command target {command[0]!r}.",
                    )
                )
                continue
            process_execute_found = True
        accepted.append(str(grant["grant_sha256"]))
    if not process_execute_found:
        alerts.append(
            _alert(
                "secure_executor_process_execute_grant_missing",
                "Secure command requires a matching process.execute grant.",
            )
        )
    return alerts, accepted


def _grant_sha256(grant: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in grant.items()
        if key not in {"grant_sha256", "grant_path"}
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sha256_uri(path: Path) -> str:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return ""
    return f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}"


def _artifact_descriptor(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256_uri(path),
        "bytes": path.stat().st_size,
    }


def _safe_environment_name(value: str) -> bool:
    return bool(value) and value.replace("_", "A").isalnum() and value[0].isalpha()


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _alert(code: str, message: str) -> dict[str, Any]:
    return {"severity": "BLOCK", "code": code, "message": message, "evidence": {}}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
