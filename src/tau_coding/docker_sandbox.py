"""Strict Docker sandbox policy checks for Tau agent commands."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SANDBOX_RUN_RECEIPT_SCHEMA = "tau.sandbox_run_receipt.v1"
SUPPORTED_BACKENDS = {"docker", "docker-sbx"}


def write_docker_sandbox_receipt(
    *,
    image: str,
    command: list[str],
    receipt_path: Path,
    backend: str = "docker",
    network: str = "none",
    user: str = "65532:65532",
    read_only_rootfs: bool = True,
    cap_drop: list[str] | None = None,
    no_new_privileges: bool = True,
    privileged: bool = False,
    host_network: bool = False,
    docker_socket_mounted: bool = False,
    mounts: list[str] | None = None,
    execute: bool = False,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Validate Docker sandbox policy and optionally execute a constrained run."""

    resolved_receipt = receipt_path.expanduser().resolve()
    cap_drop_values = cap_drop or ["ALL"]
    mount_values = mounts or []
    alerts = _validate_policy(
        image=image,
        backend=backend,
        network=network,
        user=user,
        read_only_rootfs=read_only_rootfs,
        cap_drop=cap_drop_values,
        no_new_privileges=no_new_privileges,
        privileged=privileged,
        host_network=host_network,
        docker_socket_mounted=docker_socket_mounted,
        mounts=mount_values,
    )
    if timeout_seconds <= 0:
        alerts.append(_alert("invalid_timeout", "Docker sandbox timeout must be positive."))
    ok = not alerts
    cidfile = resolved_receipt.with_suffix(".container-id.txt") if execute and ok else None
    docker_command = (
        _docker_run_command(
            image=image,
            command=command,
            network=network,
            user=user,
            read_only_rootfs=read_only_rootfs,
            cap_drop=cap_drop_values,
            no_new_privileges=no_new_privileges,
            mounts=mount_values,
            cidfile=cidfile,
        )
        if ok
        else []
    )
    execution = _execute_docker_command(
        docker_command=docker_command,
        receipt_path=resolved_receipt,
        timeout_seconds=timeout_seconds,
    ) if execute and ok else None
    if execution is not None:
        alerts.extend(execution["alerts"])
        ok = execution["ok"]
    receipt: dict[str, Any] = {
        "schema": SANDBOX_RUN_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": bool(execution),
        "provider_live": False,
        "backend": {
            "name": backend,
            "image": image,
            "image_digest": _image_digest(image),
        },
        "policy": {
            "network": network,
            "read_only_rootfs": read_only_rootfs,
            "cap_drop": cap_drop_values,
            "no_new_privileges": no_new_privileges,
            "privileged": privileged,
            "docker_socket_mounted": docker_socket_mounted,
            "host_network": host_network,
            "user": user,
            "mounts": mount_values,
        },
        "command": command,
        "docker_command": docker_command,
        "command_executed": bool(execution and execution["command_executed"]),
        "execution": execution,
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "recommended_action": _recommended_action(alerts),
        "receipt_path": str(resolved_receipt),
        "proof_scope": {
            "proves": [
                "Tau inspected Docker sandbox policy before container execution.",
                "Tau blocked unsafe Docker settings before building an executable command.",
                (
                    "Tau executed the Docker command and captured stdout/stderr artifacts."
                    if execution and execution["command_executed"]
                    else "No Docker command was executed by this policy check."
                ),
            ],
            "does_not_prove": [
                "Runtime sandbox isolation.",
                "Docker Sandboxes microVM availability.",
                "ITAR compliance.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _validate_policy(
    *,
    image: str,
    backend: str,
    network: str,
    user: str,
    read_only_rootfs: bool,
    cap_drop: list[str],
    no_new_privileges: bool,
    privileged: bool,
    host_network: bool,
    docker_socket_mounted: bool,
    mounts: list[str],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if backend not in SUPPORTED_BACKENDS:
        alerts.append(_alert("unsupported_backend", f"backend must be one of {sorted(SUPPORTED_BACKENDS)}"))
    if not _image_digest(image):
        alerts.append(_alert("unpinned_image", "Docker image must be pinned by sha256 digest."))
    if network != "none":
        alerts.append(_alert("network_not_none", "Docker sandbox network must be none by default."))
    if host_network:
        alerts.append(_alert("host_network_requested", "Docker sandbox must not use host network."))
    if privileged:
        alerts.append(_alert("privileged_requested", "Docker sandbox must not run privileged."))
    if docker_socket_mounted or any("docker.sock" in mount for mount in mounts):
        alerts.append(_alert("docker_socket_mount_requested", "Docker socket mount is forbidden."))
    if any(_is_broad_home_mount(mount) for mount in mounts):
        alerts.append(_alert("broad_home_mount_requested", "Broad $HOME mounts are forbidden."))
    if not read_only_rootfs:
        alerts.append(_alert("rootfs_not_read_only", "Docker sandbox rootfs must be read-only."))
    if "ALL" not in cap_drop:
        alerts.append(_alert("cap_drop_all_missing", "Docker sandbox must drop all capabilities."))
    if not no_new_privileges:
        alerts.append(_alert("no_new_privileges_missing", "Docker sandbox requires no-new-privileges."))
    if user in {"", "0", "0:0", "root"}:
        alerts.append(_alert("root_user_requested", "Docker sandbox must run as a non-root user."))
    return alerts


def _docker_run_command(
    *,
    image: str,
    command: list[str],
    network: str,
    user: str,
    read_only_rootfs: bool,
    cap_drop: list[str],
    no_new_privileges: bool,
    mounts: list[str],
    cidfile: Path | None = None,
) -> list[str]:
    docker_command = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--user",
        user,
    ]
    if cidfile is not None:
        docker_command.extend(["--cidfile", str(cidfile)])
    if read_only_rootfs:
        docker_command.append("--read-only")
    for cap in cap_drop:
        docker_command.extend(["--cap-drop", cap])
    if no_new_privileges:
        docker_command.extend(["--security-opt", "no-new-privileges:true"])
    for mount in mounts:
        docker_command.extend(["--mount", mount])
    docker_command.append(image)
    docker_command.extend(command)
    return docker_command


def _execute_docker_command(
    *,
    docker_command: list[str],
    receipt_path: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    stdout_path = receipt_path.with_suffix(".stdout.txt")
    stderr_path = receipt_path.with_suffix(".stderr.txt")
    cidfile_path = receipt_path.with_suffix(".container-id.txt")
    alerts: list[dict[str, Any]] = []
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        alerts.append(_alert("docker_unavailable", "Docker executable was not found on PATH."))
        return {
            "ok": False,
            "command_executed": False,
            "exit_code": None,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "container_id_path": str(cidfile_path),
            "container_id": None,
            "alerts": alerts,
        }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            docker_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text(exc.stderr or "", encoding="utf-8")
        alerts.append(
            _alert(
                "docker_command_timeout",
                f"Docker sandbox command timed out after {timeout_seconds} seconds.",
            )
        )
        return {
            "ok": False,
            "command_executed": True,
            "exit_code": None,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "container_id_path": str(cidfile_path),
            "container_id": _read_cidfile(cidfile_path),
            "alerts": alerts,
        }
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        alerts.append(
            _alert(
                "docker_command_nonzero",
                f"Docker sandbox command exited with code {completed.returncode}.",
            )
        )
    return {
        "ok": completed.returncode == 0,
        "command_executed": True,
        "exit_code": completed.returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "container_id_path": str(cidfile_path),
        "container_id": _read_cidfile(cidfile_path),
        "alerts": alerts,
    }


def _read_cidfile(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _image_digest(image: str) -> str | None:
    if "@sha256:" not in image:
        return None
    digest = image.rsplit("@sha256:", 1)[1]
    if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
        return None
    return f"sha256:{digest.lower()}"


def _is_broad_home_mount(mount: str) -> bool:
    lowered = mount.lower()
    return "source=/home" in lowered or "src=/home" in lowered or "source=$home" in lowered


def _recommended_action(alerts: list[dict[str, Any]]) -> dict[str, str]:
    if not alerts:
        return {
            "type": "continue",
            "next_agent": "orchestrator",
            "reason": "Docker sandbox policy passed; a separate execution rung may run it.",
        }
    return {
        "type": "repair_sandbox_policy",
        "next_agent": "goal-guardian",
        "reason": "Repair Docker sandbox policy before executing any agent command.",
    }


def _alert(code: str, message: str) -> dict[str, Any]:
    return {"severity": "BLOCK", "code": code, "message": message, "evidence": {}}


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
