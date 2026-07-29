"""Canonical DAG discovery and launch receipts for Tau's immutable goal.

This module provides a thin product-facing catalog over existing non-mocked Tau
sanity DAG checks. It does not implement a separate DAG runtime: launch commands
delegate to the documented Tau real-world sanity entrypoint, then read back the
produced receipts and viewer contract.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import quote

from tau_coding.run_status import build_dag_viewer_link

CATALOG_SCHEMA = "tau.canonical_dag_catalog.v1"
LAUNCH_RECEIPT_SCHEMA = "tau.canonical_dag_launch_receipt.v1"
DEFAULT_RUN_ROOT = Path("experiments/goal-locked-subagents/proofs/canonical-dag-launch")
DEFAULT_CANONICAL_DAG_VIEWER_BASE_URL = "http://localhost:3002/#tau/dag"


@dataclass(frozen=True, slots=True)
class CanonicalDag:
    """One user-selectable canonical DAG launch surface."""

    dag_id: str
    name: str
    topology: str
    complexity_order: int
    sanity_level: str
    sanity_check_id: str
    useful_output: str
    proof_expectations: tuple[str, ...]
    proof_boundary: str


CANONICAL_DAGS: tuple[CanonicalDag, ...] = (
    CanonicalDag(
        dag_id="simple-linear",
        name="Simple Linear DAG",
        topology="linear creator -> reviewer -> human",
        complexity_order=1,
        sanity_level="simple",
        sanity_check_id="simple.project_dag_creator_reviewer",
        useful_output="creator artifact reviewed against the immutable goal",
        proof_expectations=(
            "project DAG receipt",
            "creator_artifact evidence",
            "reviewer_verdict evidence",
            "DAG viewer link",
        ),
        proof_boundary="Launch/discovery proof only; semantic output quality is covered by #252.",
    ),
    CanonicalDag(
        dag_id="multi-step-sequential",
        name="Multi-Step Sequential DAG",
        topology="creator -> reviewer -> revise -> creator -> reviewer -> human",
        complexity_order=2,
        sanity_level="medium",
        sanity_check_id="medium.project_dag_reviewer_repair_loop",
        useful_output="revised creator artifact accepted by reviewer",
        proof_expectations=(
            "DAG-level repair loop",
            "multiple attempts",
            "accepted reviewer_verdict",
            "DAG viewer link",
        ),
        proof_boundary="Launch/discovery proof only; full useful-output corpus is covered by #252.",
    ),
    CanonicalDag(
        dag_id="fanout-fanin",
        name="Concurrent Fan-Out/Fan-In DAG",
        topology="start -> research + coder in parallel -> reviewer join -> human",
        complexity_order=3,
        sanity_level="medium",
        sanity_check_id="medium.project_dag_ready_queue_parallel_join",
        useful_output="joined source summary and creator artifact reviewed together",
        proof_expectations=(
            "bounded ready queue",
            "parallel branch dispatch",
            "join at reviewer",
            "DAG viewer link",
        ),
        proof_boundary="Launch/discovery proof only; dynamic visible progress is covered by #255.",
    ),
    CanonicalDag(
        dag_id="mixed-retry-approval",
        name="Mixed Sequential/Concurrent DAG",
        topology="parallel branches with retry recovery before reviewer join",
        complexity_order=4,
        sanity_level="advanced",
        sanity_check_id="advanced.project_dag_ready_queue_timeout_retry_recovery",
        useful_output="accepted joined result after retry recovery",
        proof_expectations=(
            "bounded ready queue",
            "failed attempt evidence",
            "retry recovery",
            "DAG viewer link",
        ),
        proof_boundary=(
            "Launch/discovery proof only; exact approval and rollback semantics are covered "
            "by #257."
        ),
    ),
    CanonicalDag(
        dag_id="durable-recovery",
        name="Durable Mixed-Topology DAG",
        topology="parallel branches with invalid-output recovery before reviewer join",
        complexity_order=5,
        sanity_level="advanced",
        sanity_check_id="advanced.project_dag_ready_queue_non_json_retry_recovery",
        useful_output="accepted joined result after fail-closed branch repair",
        proof_expectations=(
            "bounded ready queue",
            "invalid-output blocker",
            "retry recovery",
            "DAG viewer link",
        ),
        proof_boundary=(
            "Launch/discovery proof only; crash-safe resume and targeted repair semantics "
            "are covered by #256."
        ),
    ),
)


def canonical_dag_catalog() -> dict[str, Any]:
    """Return the five canonical DAG entries in immutable-goal order."""

    return {
        "schema": CATALOG_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "count": len(CANONICAL_DAGS),
        "dags": [_dag_payload(dag) for dag in CANONICAL_DAGS],
        "proof_scope": {
            "proves": [
                "Tau exposes exactly five named canonical DAG launch choices",
                "Each catalog entry names topology, useful output, and required proof expectations",
            ],
            "does_not_prove": [
                "DAG execution",
                "browser rendering",
                "provider/model semantic quality",
                "human acceptance",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def launch_canonical_dag(
    dag_id: str,
    *,
    repo: Path | None = None,
    run_root: Path | None = None,
    uv_bin: str = "uv",
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Launch one canonical DAG through the existing live sanity runner."""

    dag = _canonical_dag(dag_id)
    repo_root = (repo or Path.cwd()).expanduser().resolve()
    resolved_run_root = (run_root or repo_root / DEFAULT_RUN_ROOT).expanduser()
    if not resolved_run_root.is_absolute():
        resolved_run_root = repo_root / resolved_run_root
    resolved_run_root = resolved_run_root.resolve()
    script_path = repo_root / "scripts" / "run-real-world-sanity.py"
    command = [
        uv_bin,
        "run",
        "--project",
        str(repo_root),
        "python",
        str(script_path),
        "--repo",
        str(repo_root),
        "--run-root",
        str(resolved_run_root),
        "--label",
        f"canonical-{dag.dag_id}",
        "--levels",
        dag.sanity_level,
        "--checks",
        dag.sanity_check_id,
        "--receipt-timeout-seconds",
        str(timeout_seconds),
    ]

    blocker = _launch_blocker(repo_root=repo_root, script_path=script_path, uv_bin=uv_bin)
    if blocker:
        return _blocked_launch(
            dag,
            repo_root=repo_root,
            run_root=resolved_run_root,
            blocker=blocker,
        )

    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=timeout_seconds + 30,
        check=False,
    )
    suite_receipt = _parse_json_payload(completed.stdout)
    check_record = _first_check(suite_receipt)
    run_dir = _launched_run_dir(suite_receipt=suite_receipt, check_record=check_record)
    viewer = build_dag_viewer_link(run_dir) if run_dir else None
    viewer_url = _viewer_url(viewer, run_dir)
    output_receipt = _output_receipt_path(check_record, run_dir)
    dag_execution_ok = bool(
        isinstance(suite_receipt, dict)
        and suite_receipt.get("ok") is True
        and isinstance(check_record, dict)
        and check_record.get("ok") is True
    )
    errors = _launch_errors(
        completed=completed,
        suite_receipt=suite_receipt,
        check_record=check_record,
        run_dir=run_dir,
        viewer=viewer,
        viewer_url=viewer_url,
        output_receipt=output_receipt,
    )
    ok = not errors
    return {
        "schema": LAUNCH_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": bool(suite_receipt.get("provider_live"))
        if isinstance(suite_receipt, dict)
        else False,
        "dag_execution_ok": dag_execution_ok,
        "dag_execution_status": suite_receipt.get("status")
        if isinstance(suite_receipt, dict)
        else None,
        "dag_execution_blocker": _dag_execution_blocker(check_record),
        "canonical_dag": _dag_payload(dag),
        "repo": str(repo_root),
        "run_root": str(resolved_run_root),
        "run_id": suite_receipt.get("run_id") if isinstance(suite_receipt, dict) else None,
        "suite_receipt_path": _suite_receipt_path(suite_receipt),
        "run_dir": str(run_dir) if run_dir else None,
        "output_receipt_path": str(output_receipt) if output_receipt else None,
        "viewer_url": viewer_url,
        "legacy_viewer_url": _legacy_viewer_url(run_dir),
        "dag_viewer": viewer.get("dag_viewer") if isinstance(viewer, dict) else None,
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau launched the selected canonical DAG through the documented sanity entrypoint",
                "Tau read back the produced suite receipt and canonical run directory",
                "Tau produced a progress-view URL contract for the launched run",
            ],
            "does_not_prove": [
                "full five-DAG corpus completion",
                "dynamic browser rendering",
                "durable crash-safe resume",
                "exact human approval rollback",
                "human acceptance",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def _dag_payload(dag: CanonicalDag) -> dict[str, Any]:
    payload = asdict(dag)
    payload["proof_expectations"] = list(dag.proof_expectations)
    return payload


def _canonical_dag(dag_id: str) -> CanonicalDag:
    for dag in CANONICAL_DAGS:
        if dag.dag_id == dag_id:
            return dag
    known = ", ".join(dag.dag_id for dag in CANONICAL_DAGS)
    raise RuntimeError(f"unknown canonical DAG {dag_id!r}; expected one of: {known}")


def _launch_blocker(*, repo_root: Path, script_path: Path, uv_bin: str) -> str | None:
    if not (repo_root / "pyproject.toml").is_file():
        return f"repo_root_missing_pyproject:{repo_root}"
    if not script_path.is_file():
        return f"missing_real_world_sanity_entrypoint:{script_path}"
    if which(uv_bin) is None:
        return f"missing_uv_binary:{uv_bin}"
    return None


def _blocked_launch(
    dag: CanonicalDag,
    *,
    repo_root: Path,
    run_root: Path,
    blocker: str,
) -> dict[str, Any]:
    return {
        "schema": LAUNCH_RECEIPT_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "canonical_dag": _dag_payload(dag),
        "repo": str(repo_root),
        "run_root": str(run_root),
        "blocker": blocker,
        "errors": [blocker],
        "timestamp": _utc_stamp(),
    }


def _first_check(suite_receipt: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(suite_receipt, dict):
        return None
    checks = suite_receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        return None
    first = checks[0]
    return first if isinstance(first, dict) else None


def _launched_run_dir(
    *,
    suite_receipt: dict[str, Any] | None,
    check_record: dict[str, Any] | None,
) -> Path | None:
    candidates: list[str] = []
    if check_record:
        summary = check_record.get("receipt_summary")
        if isinstance(summary, dict) and isinstance(summary.get("run_dir"), str):
            candidates.append(summary["run_dir"])
        if isinstance(check_record.get("output_receipt_path"), str):
            candidates.append(str(Path(check_record["output_receipt_path"]).parent))
    if isinstance(suite_receipt, dict) and isinstance(suite_receipt.get("run_dir"), str):
        suite_dir = Path(suite_receipt["run_dir"])
        check_id = check_record.get("check_id") if check_record else None
        if isinstance(check_id, str):
            for candidate in suite_dir.glob("**/dag-receipt.json"):
                if check_id.split(".")[-1].split("_")[0] in str(candidate):
                    candidates.append(str(candidate.parent))
        candidates.append(str(suite_dir))
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists():
            return path.resolve()
    return None


def _output_receipt_path(check_record: dict[str, Any] | None, run_dir: Path | None) -> Path | None:
    if check_record and isinstance(check_record.get("output_receipt_path"), str):
        path = Path(check_record["output_receipt_path"]).expanduser()
        if path.exists():
            return path.resolve()
    if run_dir:
        path = run_dir / "dag-receipt.json"
        if path.exists():
            return path.resolve()
    return None


def _launch_errors(
    *,
    completed: subprocess.CompletedProcess[str],
    suite_receipt: dict[str, Any] | None,
    check_record: dict[str, Any] | None,
    run_dir: Path | None,
    viewer: dict[str, Any] | None,
    viewer_url: str | None,
    output_receipt: Path | None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(suite_receipt, dict):
        errors.append("missing_suite_json_receipt")
    if not isinstance(check_record, dict):
        errors.append("missing_check_record")
    if run_dir is None:
        errors.append("missing_launch_run_dir")
    if output_receipt is None:
        errors.append("missing_output_receipt")
    if not isinstance(viewer, dict):
        errors.append("missing_dag_viewer_contract")
    elif not viewer_url:
        errors.append("missing_viewer_url")
    if errors and completed.returncode != 0:
        errors.append(f"launch_exit_code:{completed.returncode}")
    return errors


def _suite_receipt_path(suite_receipt: dict[str, Any] | None) -> str | None:
    if not isinstance(suite_receipt, dict) or not isinstance(suite_receipt.get("run_dir"), str):
        return None
    path = Path(suite_receipt["run_dir"]) / "real-world-sanity-receipt.json"
    return str(path.resolve()) if path.exists() else str(path)


def _dag_execution_blocker(check_record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(check_record, dict) or check_record.get("ok") is True:
        return None
    summary = check_record.get("receipt_summary")
    return {
        "check_status": check_record.get("status"),
        "check_errors": check_record.get("errors"),
        "receipt_status": summary.get("status") if isinstance(summary, dict) else None,
        "receipt_verdict": summary.get("verdict") if isinstance(summary, dict) else None,
        "alert_codes": summary.get("alert_codes") if isinstance(summary, dict) else None,
    }


def _viewer_url(viewer: dict[str, Any] | None, run_dir: Path | None) -> str | None:
    if not isinstance(viewer, dict):
        return _legacy_viewer_url(run_dir)
    dag_viewer = viewer.get("dag_viewer")
    if isinstance(dag_viewer, dict) and isinstance(dag_viewer.get("url"), str):
        return dag_viewer["url"]
    launch_command = dag_viewer.get("launch_command") if isinstance(dag_viewer, dict) else None
    if isinstance(launch_command, list) and launch_command:
        return " ".join(str(part) for part in launch_command)
    return _legacy_viewer_url(run_dir)


def _legacy_viewer_url(run_dir: Path | None) -> str | None:
    if run_dir is None:
        return None
    return f"{DEFAULT_CANONICAL_DAG_VIEWER_BASE_URL}?run={quote(str(run_dir), safe='')}"


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if stripped[index + end :].strip():
            continue
        return payload if isinstance(payload, dict) else None
    return None


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
