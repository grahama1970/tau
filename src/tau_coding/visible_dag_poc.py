"""Visible Herdr-backed DAG proof of concept for Tau."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.herdr_cleanup import resolve_herdr_session

VISIBLE_DAG_RUN_SCHEMA = "tau.visible_dag_run_receipt.v1"
VISIBLE_DAG_MANIFEST_SCHEMA = "tau.visible_dag_runtime_manifest.v1"


@dataclass(frozen=True, slots=True)
class VisibleDagNode:
    """One fixture DAG node."""

    node_id: str
    role: str
    depends_on: tuple[str, ...] = ()


CREATOR_REVIEWER_DAG = (
    VisibleDagNode(node_id="creator", role="creator"),
    VisibleDagNode(node_id="reviewer", role="reviewer", depends_on=("creator",)),
)


def run_visible_dag_poc(
    *,
    repo: Path,
    run_root: Path,
    label: str = "tau-visible-dag-poc",
    herdr_workstation: Path | None = None,
    herdr_bin: str = "herdr",
    session: str | None = None,
    receipt_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run a two-node creator->reviewer DAG in visible Herdr panes."""

    session = resolve_herdr_session(session)
    resolved_repo = repo.expanduser().resolve()
    if not resolved_repo.exists():
        raise RuntimeError(f"repo does not exist: {resolved_repo}")
    skill_root = _resolve_herdr_workstation(herdr_workstation)
    resolved_run_root = run_root.expanduser().resolve()
    resolved_run_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{_compact_stamp()}-{_slug(label)}"
    run_dir = resolved_run_root / run_id
    work_order_dir = run_dir / "work-orders"
    receipt_dir = run_dir / "receipts"
    logs_dir = run_dir / "logs"
    for path in (work_order_dir, receipt_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"

    dag_spec = _dag_spec(run_id=run_id, label=label)
    _write_json(run_dir / "dag.json", dag_spec)
    _append_event(
        events_path,
        "dag_created",
        {"run_id": run_id, "dag_path": str(run_dir / "dag.json")},
    )

    doctor = _run_skill(
        skill_root,
        ["doctor", "--json", "--herdr-bin", herdr_bin, *_session_args(session)],
        cwd=resolved_repo,
    )
    doctor_payload = _parse_json_stdout(doctor.stdout, label="herdr-workstation doctor")
    if doctor.returncode != 0 or doctor_payload.get("ok") is not True:
        receipt = _blocked_run_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=["herdr-workstation doctor failed"],
            command_results=[_command_result_dict(doctor)],
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt

    workstation = _run_skill(
        skill_root,
        [
            "workstation",
            "create",
            "--repo",
            str(resolved_repo),
            "--label",
            label,
            "--run-root",
            str(run_dir / "herdr-workstations"),
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
            "--tab",
            "agents",
            "--tab",
            "logs",
            "--tab",
            "receipts",
            "--json",
        ],
        cwd=resolved_repo,
    )
    command_results = [_command_result_dict(doctor), _command_result_dict(workstation)]
    if workstation.returncode != 0:
        receipt = _blocked_run_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=["workstation create failed"],
            command_results=command_results,
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt
    workstation_manifest = _parse_json_stdout(
        workstation.stdout,
        label="herdr-workstation manifest",
    )
    workstation_manifest_path = Path(str(workstation_manifest["run_dir"])) / "workstation.json"

    node_receipts: list[dict[str, Any]] = []
    node_receipt_paths: dict[str, Path] = {}
    for index, node in enumerate(CREATOR_REVIEWER_DAG):
        work_order_path = work_order_dir / f"{node.node_id}.json"
        receipt_path = receipt_dir / f"{node.node_id}.receipt.json"
        dependency_receipts = [node_receipt_paths[dependency] for dependency in node.depends_on]
        node_receipt_paths[node.node_id] = receipt_path
        _write_json(
            work_order_path,
            _work_order(
                run_id=run_id,
                node=node,
                work_order_path=work_order_path,
                receipt_path=receipt_path,
                dependency_receipts=dependency_receipts,
            ),
        )
        _append_event(
            events_path,
            "work_order_written",
            {
                "run_id": run_id,
                "node_id": node.node_id,
                "work_order_path": str(work_order_path),
                "receipt_path": str(receipt_path),
            },
        )
        command = _worker_command(
            run_id=run_id,
            node=node,
            work_order_path=work_order_path,
            receipt_path=receipt_path,
            events_path=events_path,
            dependency_receipts=dependency_receipts,
        )
        start = _run_skill(
            skill_root,
            [
                "agent",
                "start",
                str(workstation_manifest_path),
                "--name",
                f"{run_id}-{node.node_id}",
                "--role",
                node.role,
                "--command",
                command,
                "--tab",
                "agents",
                "--work-order",
                str(work_order_path),
                "--env",
                f"PYTHONPATH={Path(__file__).resolve().parents[1]}",
                "--env",
                f"TAU_VISIBLE_DAG_RUN_ID={run_id}",
                "--env",
                f"TAU_VISIBLE_DAG_NODE_ID={node.node_id}",
                "--herdr-bin",
                herdr_bin,
                *_session_args(session),
                *([] if index == 0 else ["--split", "right"]),
                "--json",
            ],
            cwd=resolved_repo,
        )
        command_results.append(_command_result_dict(start))
        if start.returncode != 0:
            receipt = _blocked_run_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[f"agent start failed for {node.node_id}"],
                command_results=command_results,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt
        _append_event(
            events_path,
            "agent_started",
            {"run_id": run_id, "node_id": node.node_id, "role": node.role},
        )
        send = _run_skill(
            skill_root,
            [
                "agent",
                "send",
                f"{run_id}-{node.node_id}",
                "--text",
                f"Tau work order: {work_order_path}\nExpected receipt: {receipt_path}",
                "--events",
                str(events_path),
                "--from-agent",
                "tau-project-agent",
                "--herdr-bin",
                herdr_bin,
                *_session_args(session),
            ],
            cwd=resolved_repo,
        )
        command_results.append(_command_result_dict(send))
        if send.returncode != 0:
            receipt = _blocked_run_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[f"agent send failed for {node.node_id}"],
                command_results=command_results,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt
        try:
            node_receipt = _wait_for_receipt(receipt_path, timeout_seconds=receipt_timeout_seconds)
        except RuntimeError as exc:
            receipt = _blocked_run_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[str(exc)],
                command_results=command_results,
                node_receipts=node_receipts,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt
        node_receipts.append(node_receipt)
        if node_receipt.get("ok") is not True:
            receipt = _blocked_run_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[f"node {node.node_id} receipt did not report ok=true"],
                command_results=command_results,
                node_receipts=node_receipts,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt

    inspect = _run_skill(
        skill_root,
        [
            "workstation",
            "inspect",
            str(workstation_manifest_path),
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
        ],
        cwd=resolved_repo,
    )
    command_results.append(_command_result_dict(inspect))
    inspect_path = run_dir / "inspect.json"
    inspect_payload = _parse_json_stdout(inspect.stdout, label="workstation inspect")
    _write_json(inspect_path, inspect_payload)

    runtime_manifest = {
        "schema": VISIBLE_DAG_MANIFEST_SCHEMA,
        "backend_session_id": session,
        "run_id": run_id,
        "label": label,
        "repo": str(resolved_repo),
        "run_dir": str(run_dir),
        "dag_path": str(run_dir / "dag.json"),
        "events_jsonl": str(events_path),
        "workstation_manifest": str(workstation_manifest_path),
        "inspect_path": str(inspect_path),
        "nodes": [
            {
                "node_id": node.node_id,
                "role": node.role,
                "depends_on": list(node.depends_on),
                "work_order_path": str(work_order_dir / f"{node.node_id}.json"),
                "receipt_path": str(receipt_dir / f"{node.node_id}.receipt.json"),
            }
            for node in CREATOR_REVIEWER_DAG
        ],
        "proves_success": False,
    }
    _write_json(run_dir / "runtime-manifest.json", runtime_manifest)
    final_receipt = {
        "schema": VISIBLE_DAG_RUN_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": True,
        "live": True,
        "proof_scope": {
            "proves": [
                "Tau compiled a two-node DAG into bounded work orders",
                "Tau created a Herdr-backed visible workstation",
                "Tau started visible Herdr panes for creator and reviewer nodes",
                "Tau sent bounded work-order notifications to the panes",
                "Tau waited for node receipts and advanced reviewer after creator receipt",
                "Tau captured runtime manifest, event log, workstation manifest, "
                "inspect payload, and final receipt",
            ],
            "does_not_prove": [
                "semantic LLM agent quality",
                "provider-specific Codex/OpenCode/Claude integration",
                "remote Tailscale monitoring",
                "ticket closure readiness",
            ],
        },
        "run_id": run_id,
        "run_dir": str(run_dir),
        "runtime_manifest": str(run_dir / "runtime-manifest.json"),
        "events_jsonl": str(events_path),
        "workstation_manifest": str(workstation_manifest_path),
        "inspect_path": str(inspect_path),
        "node_receipts": node_receipts,
        "command_results": command_results,
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "run-receipt.json", final_receipt)
    return final_receipt


def inspect_visible_dag_run(run_dir: Path) -> dict[str, Any]:
    """Return a summary for a visible DAG POC run directory."""

    resolved = run_dir.expanduser().resolve()
    manifest = _read_json_object(resolved / "runtime-manifest.json", label="runtime manifest")
    receipt = _read_json_object(resolved / "run-receipt.json", label="run receipt")
    events_path = Path(str(manifest["events_jsonl"]))
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    node_summaries = []
    for node in manifest.get("nodes", []):
        if isinstance(node, dict):
            receipt_path = Path(str(node["receipt_path"]))
            node_receipt = _read_json_object(receipt_path, label=f"node receipt {receipt_path}")
            node_summaries.append(
                {
                    "node_id": node.get("node_id"),
                    "role": node.get("role"),
                    "status": node_receipt.get("status"),
                    "ok": node_receipt.get("ok"),
                    "receipt_path": str(receipt_path),
                }
            )
    return {
        "schema": "tau.visible_dag_inspect.v1",
        "ok": receipt.get("ok") is True,
        "run_id": manifest.get("run_id"),
        "status": receipt.get("status"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "backend_session_id": manifest.get("backend_session_id"),
        "run_dir": str(resolved),
        "workstation_manifest": manifest.get("workstation_manifest"),
        "inspect_path": manifest.get("inspect_path"),
        "events_count": len(events),
        "nodes": node_summaries,
        "proof_scope": receipt.get("proof_scope"),
    }


def _resolve_herdr_workstation(path: Path | None) -> Path:
    if path is not None:
        resolved = path.expanduser().resolve()
    else:
        resolved = Path("/home/graham/workspace/experiments/agent-skills/skills/herdr-workstation")
    run_sh = resolved / "run.sh"
    if not run_sh.exists():
        raise RuntimeError(f"herdr-workstation run.sh not found: {run_sh}")
    return resolved


def _run_skill(skill_root: Path, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(skill_root / "run.sh"), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )


def _worker_command(
    *,
    run_id: str,
    node: VisibleDagNode,
    work_order_path: Path,
    receipt_path: Path,
    events_path: Path,
    dependency_receipts: list[Path],
) -> str:
    argv = [
        sys.executable,
        "-m",
        "tau_coding.visible_dag_worker",
        "--run-id",
        run_id,
        "--node-id",
        node.node_id,
        "--role",
        node.role,
        "--work-order",
        str(work_order_path),
        "--receipt",
        str(receipt_path),
        "--events",
        str(events_path),
        "--hold-seconds",
        "300",
    ]
    for dependency in dependency_receipts:
        argv.extend(["--depends-on", str(dependency)])
    return " ".join(_shell_quote(part) for part in argv)


def _dag_spec(*, run_id: str, label: str) -> dict[str, Any]:
    return {
        "schema": "tau.visible_dag_spec.v1",
        "run_id": run_id,
        "label": label,
        "nodes": [
            {
                "node_id": node.node_id,
                "role": node.role,
                "depends_on": list(node.depends_on),
                "receipt_required": True,
                "stop_conditions": ["receipt_written", "blocked_with_reason"],
            }
            for node in CREATOR_REVIEWER_DAG
        ],
        "edges": [{"from": "creator", "to": "reviewer"}],
        "receipt_policy": {
            "per_node_receipt_required": True,
            "final_receipt_required": True,
        },
    }


def _work_order(
    *,
    run_id: str,
    node: VisibleDagNode,
    work_order_path: Path,
    receipt_path: Path,
    dependency_receipts: list[Path],
) -> dict[str, Any]:
    return {
        "schema": "tau.visible_dag_work_order.v1",
        "run_id": run_id,
        "node_id": node.node_id,
        "role": node.role,
        "summary": f"Fixture {node.role} node for visible Tau+Herdr DAG POC.",
        "owns": ["write one node receipt"],
        "does_not_own": [
            "global project completion",
            "ticket closure",
            "semantic LLM review quality",
        ],
        "dag_spec": {
            "mode": "single_node",
            "inputs_required": [str(work_order_path), *(str(path) for path in dependency_receipts)],
            "receipts": [str(receipt_path)],
            "stop_conditions": ["receipt_written", "blocked_with_reason"],
        },
        "receipt_path": str(receipt_path),
        "dependency_receipts": [str(path) for path in dependency_receipts],
        "status_reporting": {
            "required": True,
            "recipient": "tau-project-agent",
            "stream_modes": ["jsonl_event_stream", "final_response_json"],
            "heartbeat_interval_seconds": 30,
            "stale_after_seconds": 120,
        },
    }


def _wait_for_receipt(path: Path, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return _read_json_object(path, label=f"receipt {path}")
            except RuntimeError as exc:
                last_error = str(exc)
        time.sleep(0.1)
    raise RuntimeError(f"receipt did not appear before timeout: {path}; last_error={last_error}")


def _blocked_run_receipt(
    *,
    run_id: str,
    run_dir: Path,
    events_path: Path,
    errors: list[str],
    command_results: list[dict[str, Any]],
    node_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": VISIBLE_DAG_RUN_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": True,
        "live": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "events_jsonl": str(events_path),
        "node_receipts": node_receipts or [],
        "command_results": command_results,
        "errors": errors,
        "timestamp": _utc_stamp(),
    }


def _command_result_dict(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "argv": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _parse_json_stdout(stdout: str, *, label: str) -> dict[str, Any]:
    stripped = stdout.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} did not emit JSON: {exc}: {stripped[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} JSON root must be an object")
    return payload


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_event(path: Path, kind: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "tau.visible_dag_event.v1",
        "kind": kind,
        "timestamp": _utc_stamp(),
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _session_args(session: str | None) -> list[str]:
    return [] if session is None else ["--session", session]


def _compact_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "-" for char in value.strip()]
    return "-".join(part for part in "".join(chars).split("-") if part)[:80] or "visible-dag"


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
