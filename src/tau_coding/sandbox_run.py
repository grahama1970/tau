"""Fail-closed local sandbox execution for zero-trust Tau runs."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.docker_sandbox import write_docker_sandbox_receipt
from tau_coding.sandbox_policy import SANDBOX_RUN_RECEIPT_SCHEMA, sandbox_policy_alerts

SUPPORTED_BACKENDS = {"bwrap", "docker", "docker-sbx"}


def run_sandboxed_command(
    *,
    command: Sequence[str],
    policy_profile_path: Path,
    data_boundary_path: Path,
    receipt_path: Path | None = None,
    goal_hash: str | None = None,
    timeout_seconds: float = 30.0,
    backend: str = "bwrap",
    image: str | None = None,
    stdin_text: str | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a command only when Tau can establish the requested sandbox boundary."""

    resolved_policy = policy_profile_path.expanduser().resolve()
    resolved_boundary = data_boundary_path.expanduser().resolve()
    policy_profile, policy_read_alerts = _read_json_object_for_receipt(
        resolved_policy,
        label="policy_profile",
    )
    data_boundary, boundary_read_alerts = _read_json_object_for_receipt(
        resolved_boundary,
        label="data_boundary",
    )
    alerts = [*policy_read_alerts, *boundary_read_alerts]
    if not alerts:
        alerts.extend(
            sandbox_policy_alerts(policy_profile=policy_profile, data_boundary=data_boundary)
        )
    backend_info = _backend_info(backend)
    resolved_work_dir = work_dir.expanduser().resolve() if work_dir is not None else None
    command_result: dict[str, Any] | None = None

    if not command:
        alerts.append(_alert("missing_command", "sandbox-run requires a command after --."))
    if backend not in SUPPORTED_BACKENDS:
        alerts.append(_alert("unsupported_backend", f"Unsupported sandbox backend: {backend}"))

    if backend in {"docker", "docker-sbx"} and not image:
        alerts.append(_alert("missing_docker_image", "Docker sandbox backend requires --image."))
    if resolved_work_dir is not None and not resolved_work_dir.is_dir():
        alerts.append(_alert("invalid_work_dir", "sandbox work_dir must exist and be a directory."))

    if not alerts and backend == "bwrap":
        probe = _probe_bwrap(backend_info["path"], work_dir=resolved_work_dir)
        backend_info["probe"] = probe
        if probe["ok"] is not True:
            alerts.append(
                _alert(
                    "sandbox_backend_unavailable",
                    "Bubblewrap could not create the required network-isolated sandbox.",
                    errors=[str(probe.get("stderr") or probe.get("error") or "")],
                )
            )

    if not alerts and backend == "bwrap":
        command_result = _run_bwrap_command(
            list(command),
            backend_path=Path(str(backend_info["path"])),
            timeout_seconds=timeout_seconds,
            stdin_text=stdin_text,
            work_dir=resolved_work_dir,
        )
        if command_result["returncode"] != 0:
            alerts.append(
                _alert(
                    "sandboxed_command_failed",
                    "Sandboxed command returned non-zero.",
                    errors=[str(command_result.get("stderr") or "")],
                )
            )

    if not alerts and backend in {"docker", "docker-sbx"}:
        docker_receipt_path = (
            receipt_path
            if receipt_path is not None
            else Path(tempfile.mkdtemp(prefix="tau-docker-sandbox-")) / "sandbox-receipt.json"
        )
        receipt = write_docker_sandbox_receipt(
            image=str(image),
            command=list(command),
            receipt_path=docker_receipt_path,
            backend=backend,
            execute=True,
            timeout_seconds=int(timeout_seconds),
        )
        receipt["policy_profile"] = {
            "path": str(resolved_policy),
            "exists": resolved_policy.exists(),
            "sha256": _sha256_uri_or_none(resolved_policy),
            "schema": policy_profile.get("schema"),
        }
        receipt["goal_hash"] = goal_hash
        receipt["data_boundary"] = {
            "path": str(resolved_boundary),
            "exists": resolved_boundary.exists(),
            "sha256": _sha256_uri_or_none(resolved_boundary),
            "schema": data_boundary.get("schema"),
        }
        receipt["network_egress"] = "denied"
        receipt["provider_access"] = "denied"
        receipt["external_research"] = "denied"
        receipt["public_github_mutation"] = "denied"
        if receipt_path is not None:
            _write_json(receipt_path, receipt)
        return receipt

    ok = not alerts
    receipt = {
        "schema": SANDBOX_RUN_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "checked_at": _utc_stamp(),
        "backend": backend_info,
        "image": image,
        "policy_profile": {
            "path": str(resolved_policy),
            "exists": resolved_policy.exists(),
            "sha256": _sha256_uri_or_none(resolved_policy),
            "schema": policy_profile.get("schema"),
        },
        "data_boundary": {
            "path": str(resolved_boundary),
            "exists": resolved_boundary.exists(),
            "sha256": _sha256_uri_or_none(resolved_boundary),
            "schema": data_boundary.get("schema"),
        },
        "network_egress": "denied",
        "provider_access": "denied",
        "external_research": "denied",
        "public_github_mutation": "denied",
        "command": list(command),
        "stdin_sha256": f"sha256:{_sha256_text(stdin_text)}" if stdin_text is not None else None,
        "stdin_bytes": len(stdin_text.encode("utf-8")) if stdin_text is not None else None,
        "work_dir": str(resolved_work_dir) if resolved_work_dir is not None else None,
        "command_executed": command_result is not None,
        "command_result": command_result,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau checked zero-trust sandbox policy before command execution.",
                "Tau blocked command execution when sandbox isolation could not be established.",
                "When PASS, Tau executed the command through the recorded sandbox backend.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Export-control legal sufficiency.",
                "Human identity verification.",
                "Provider/model semantic safety.",
                "Security against kernel, backend, or host escape vulnerabilities.",
                "Network isolation unless this receipt status is PASS and backend probe passed.",
            ],
        },
    }
    if receipt_path is not None:
        _write_json(receipt_path, receipt)
    return receipt


def _backend_info(backend: str) -> dict[str, Any]:
    path = shutil.which(backend) if backend else None
    return {
        "name": backend,
        "path": path,
        "available": path is not None,
    }


def _probe_bwrap(backend_path: str | None, *, work_dir: Path | None = None) -> dict[str, Any]:
    if backend_path is None:
        return {"ok": False, "error": "bwrap executable not found"}
    probe_command = _bwrap_command(
        ["/usr/bin/python3", "-c", "print('tau-sandbox-probe')"],
        backend_path=Path(backend_path),
        work_dir=work_dir,
    )
    try:
        result = subprocess.run(
            probe_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "command": probe_command,
            "error": str(exc),
        }
    return {
        "ok": result.returncode == 0,
        "command": probe_command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _run_bwrap_command(
    command: list[str],
    *,
    backend_path: Path,
    timeout_seconds: float,
    stdin_text: str | None,
    work_dir: Path | None,
) -> dict[str, Any]:
    sandbox_command = _bwrap_command(command, backend_path=backend_path, work_dir=work_dir)
    try:
        result = subprocess.run(
            sandbox_command,
            check=False,
            capture_output=True,
            text=True,
            input=stdin_text,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": sandbox_command,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": str(exc),
            "timed_out": True,
        }
    return {
        "command": sandbox_command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": False,
    }


def _bwrap_command(command: list[str], *, backend_path: Path, work_dir: Path | None) -> list[str]:
    binds: list[str] = []
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        host_path = Path(path)
        if host_path.exists():
            binds.extend(["--ro-bind", path, path])
    work_mount = (
        ["--bind", str(work_dir), "/work"] if work_dir is not None else ["--dir", "/work"]
    )
    return [
        str(backend_path),
        "--unshare-net",
        "--die-with-parent",
        *binds,
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        *work_mount,
        "--chdir",
        "/work",
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        *command,
    ]


def _read_json_object_for_receipt(
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [_alert(f"{label}_missing", f"{label} file does not exist: {path}")]
    except json.JSONDecodeError as exc:
        return {}, [
            _alert(
                f"{label}_unreadable",
                f"{label} file is not valid JSON: {path}",
                errors=[str(exc)],
            )
        ]
    if not isinstance(payload, dict):
        return {}, [_alert(f"{label}_not_object", f"{label} file must contain a JSON object")]
    return payload, []


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_uri_or_none(path: Path) -> str | None:
    try:
        return f"sha256:{_sha256(path)}"
    except OSError:
        return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _alert(code: str, message: str, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "code": code,
        "severity": "BLOCK",
        "message": message,
    }
    if errors:
        alert["errors"] = errors
    return alert


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
