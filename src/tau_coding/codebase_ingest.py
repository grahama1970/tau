"""Non-blocking codebase-ingest coordination for Tau."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CODEBASE_INGEST_RECEIPT_SCHEMA = "tau.codebase_ingest_receipt.v1"
DEFAULT_INGEST_CODE_RUNNER = (
    "/home/graham/workspace/experiments/agent-skills/skills/ingest-code/run.sh"
)


def write_codebase_ingest_receipt(
    *,
    repo_path: Path,
    receipt_path: Path,
    state_path: Path,
    ingest_runner: str = DEFAULT_INGEST_CODE_RUNNER,
    scope: str = "monitor-tau",
    start: bool = False,
) -> dict[str, Any]:
    """Write a resumable non-blocking ingest receipt, optionally launching a worker."""

    repo = repo_path.expanduser().resolve()
    if not repo.is_dir():
        raise RuntimeError(f"repo path must be a directory: {repo}")
    resolved_receipt = receipt_path.expanduser().resolve()
    resolved_state = state_path.expanduser().resolve()
    prior_state = _read_json_object(resolved_state) if resolved_state.exists() else {}
    current_commit = _git_value(repo, ["rev-parse", "HEAD"], default="unknown")
    current_files = _file_manifest(repo)
    changed_files = _changed_files(current_files, prior_state.get("files"))
    command = [
        ingest_runner,
        "rescan",
        "-c",
        str(repo),
        "--treesitter",
        "--code-index",
        "--scope",
        scope,
    ]
    if changed_files:
        command.extend(["--since", "0h"])
    status = "SKIPPED" if not changed_files else "QUEUED"
    process: dict[str, Any] | None = None
    if start and changed_files:
        log_path = resolved_receipt.with_suffix(".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        child = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(repo),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        process = {"pid": child.pid, "log_path": str(log_path)}
        status = "STARTED"
    state = {
        "schema": "tau.codebase_ingest_state.v1",
        "repo_path": str(repo),
        "commit": current_commit,
        "files": current_files,
        "updated_at": _utc_stamp(),
    }
    receipt = {
        "schema": CODEBASE_INGEST_RECEIPT_SCHEMA,
        "ok": True,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo_path": str(repo),
        "commit": current_commit,
        "state_path": str(resolved_state),
        "receipt_path": str(resolved_receipt),
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "command": command,
        "started": bool(process),
        "process": process,
        "interactive_blocking": False,
        "resumable": True,
        "memory_writes_performed_by_tau": False,
        "proof_scope": {
            "proves": [
                "Tau detected changed repository files without scanning unchanged files.",
                "Tau produced a resumable ingest state marker.",
                "Tau prepared the existing ingest-code rescan command behind the skill boundary.",
                "Tau does not block the interactive path unless start=true is explicitly "
                "requested.",
            ],
            "does_not_prove": [
                "Memory graph write completeness.",
                "Tree-sitter extraction correctness.",
                "Point-in-time Memory recall.",
                "TUI scheduler integration.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_state, state)
    _write_json(resolved_receipt, receipt)
    return receipt


def _file_manifest(repo: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name.endswith((".pyc", ".pyo")):
            continue
        rel = path.relative_to(repo).as_posix()
        files[rel] = _sha256(path)
    return files


def _changed_files(current: dict[str, str], prior: object) -> list[str]:
    if not isinstance(prior, dict):
        return sorted(current)
    changed = [path for path, digest in current.items() if prior.get(path) != digest]
    deleted = [path for path in prior if isinstance(path, str) and path not in current]
    return sorted(changed + deleted)


def _git_value(repo: Path, args: list[str], *, default: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return default
    return result.stdout.strip() or default


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
