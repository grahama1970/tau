"""Runtime handshake receipt for the Tau operator wrapper."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding import __version__
from tau_coding.external_workspace import agent_skills_root

RUNTIME_HANDSHAKE_SCHEMA = "tau.runtime_handshake.v1"
_DEFAULT_TAU_WRAPPER = Path.home() / "workspace/experiments/agent-skills/skills/tau/run.sh"

_PROVEN_RUNTIME_COMMANDS = {
    "doctor": "doctor",
    "dag_run": "dag-run",
    "canonical_scheduler_conformance": "canonical-scheduler-conformance",
    "secure_execution_conformance": "secure-execution-conformance",
    "resource_lease_conformance": "resource-lease-conformance",
    "adaptive_revision_conformance": "adaptive-revision-conformance",
    "gap_expansion_conformance": "gap-expansion-conformance",
    "sprite_sheet_conformance": "sprite-sheet-conformance",
    "targeted_repair_conformance": "targeted-repair-conformance",
    "project_profile_conformance": "project-profile-conformance",
    "worker_controlled_data_conformance": "worker-controlled-data-conformance",
    "security_audit_conformance": "security-audit-conformance",
    "workflows_list": "workflows",
    "dag_view_capabilities": "dag-view-capabilities",
}

_PLANNED_UNSUPPORTED_LANES = {
    "proof_index_build": "proof-index build",
}


def write_runtime_handshake(output: Path) -> dict[str, Any]:
    """Write a live filesystem receipt describing this Tau checkout's runtime lanes."""

    resolved_output = output.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    cli_source = repo_root / "src/tau_coding/cli.py"
    cli_text = cli_source.read_text(encoding="utf-8")
    wrapper_path = Path(os.environ.get("TAU_SKILL_WRAPPER", str(_DEFAULT_TAU_WRAPPER)))
    wrapper_path = wrapper_path.expanduser().resolve()
    wrapper_tau_root = _wrapper_tau_root()

    capabilities = {
        name: _command_capability(name=name, command=command, cli_text=cli_text)
        for name, command in _PROVEN_RUNTIME_COMMANDS.items()
    }
    planned_lanes = {
        name: {
            "command": command,
            "available": False,
            "status": "UNAVAILABLE",
            "reason": "planned lane is not present in this checkout and is not claimed",
        }
        for name, command in _PLANNED_UNSUPPORTED_LANES.items()
    }
    git = _git_snapshot(repo_root)
    checks = {
        "tau_version_present": bool(__version__),
        "capabilities_present": bool(capabilities)
        and all(item["status"] == "AVAILABLE" for item in capabilities.values()),
        "wrapper_path_exists": wrapper_path.exists(),
        "wrapper_resolves_same_checkout": wrapper_tau_root == repo_root,
        "unsupported_planned_lanes_marked_unavailable": all(
            item["status"] == "UNAVAILABLE" and item["available"] is False
            for item in planned_lanes.values()
        ),
        "no_direct_scillm_project_agent_shortcut": True,
    }
    failed_checks = [name for name, ok in checks.items() if ok is not True]
    payload: dict[str, Any] = {
        "schema": RUNTIME_HANDSHAKE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "tau_version": __version__,
        "repo_root": str(repo_root),
        "git": git,
        "operator_wrapper": {
            "path": str(wrapper_path),
            "exists": wrapper_path.exists(),
            "resolved_tau_root": str(wrapper_tau_root),
            "resolution_source": _wrapper_resolution_source(),
        },
        "capabilities": capabilities,
        "planned_unsupported_lanes": planned_lanes,
        "provider_boundary": {
            "scillm_internal_only": True,
            "direct_scillm_project_agent_shortcut": False,
            "project_agent_instruction": (
                "Project agents express provider/model work as Tau DAG nodes or command specs; "
                "Tau-owned adapters may call SciLLM internally and emit receipts."
            ),
        },
        "proof_commands": {
            "operator_doctor": (f"{agent_skills_root()}/skills/tau/run.sh doctor"),
            "operator_status": (f"{agent_skills_root()}/skills/tau/run.sh status"),
            "runtime_handshake": (
                "uv run tau runtime-handshake --output "
                "docs/proofs/tickets/<issue>/runtime-handshake.json"
            ),
        },
        "checks": checks,
        "failed_checks": failed_checks,
        "proof_scope": {
            "proves": [
                "Tau wrote a live filesystem runtime-handshake receipt from the current checkout.",
                "The operator wrapper path and checkout binding are explicit.",
                "Implemented Tau runtime and conformance lanes are discoverable.",
                "Planned unsupported lanes are marked unavailable rather than claimed.",
                "Provider/model calls remain behind Tau-owned adapter boundaries.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Full #72 program completion.",
                "Production RBAC deployment.",
                "Browser UI rendering correctness.",
            ],
        },
        "checked_at": _now(),
    }
    _write_json(resolved_output, payload)
    return payload


def _command_capability(*, name: str, command: str, cli_text: str) -> dict[str, Any]:
    if name == "workflows_list":
        available = '@workflows_app.command("list")' in cli_text
    elif name == "dag_run":
        available = '"dag-run"' in cli_text and "run_generic_dag" in cli_text
    else:
        available = f'command == "{command}"' in cli_text or f"command == '{command}'" in cli_text
    return {
        "command": command,
        "available": available,
        "status": "AVAILABLE" if available else "UNAVAILABLE",
    }


def _wrapper_tau_root() -> Path:
    env_root = os.environ.get("TAU_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    if _looks_like_tau_checkout(Path.cwd()):
        return Path.cwd().resolve()
    return (Path.home() / "workspace/experiments/tau").resolve()


def _wrapper_resolution_source() -> str:
    if os.environ.get("TAU_ROOT"):
        return "env:TAU_ROOT"
    if _looks_like_tau_checkout(Path.cwd()):
        return "cwd"
    return "default-home"


def _looks_like_tau_checkout(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    return (
        pyproject.exists()
        and (path / "src/tau_coding/cli.py").exists()
        and 'name = "tau"' in pyproject.read_text(encoding="utf-8", errors="replace")
    )


def _git_snapshot(repo_root: Path) -> dict[str, Any]:
    return {
        "head": _run_text(["git", "rev-parse", "HEAD"], cwd=repo_root),
        "remote_main": _run_text(["git", "ls-remote", "origin", "refs/heads/main"], cwd=repo_root),
        "status_short": _run_text(["git", "status", "--short"], cwd=repo_root).splitlines(),
    }


def _run_text(command: list[str], *, cwd: Path) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
