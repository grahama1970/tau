"""Run the first canonical Tau DAG and produce a validated goal summary.

Inputs are the repository ``GOAL.md`` and a caller-selected run root. Outputs
are a goal digest, a human-readable summary, generic node receipts, and Tau's
durable scheduler journal. Invalid goal content or missing accepted upstream
context fails the corresponding node before it can write a passing receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag

TOPOLOGY_LABELS = (
    "Simple linear DAG",
    "Multi-step sequential DAG",
    "Concurrent fan-out/fan-in DAG",
    "Mixed sequential/concurrent DAG",
    "Durable mixed-topology DAG",
)


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _context(expected_node_id: str) -> dict[str, Any]:
    context_path = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context.get("node_id") != expected_node_id:
        raise RuntimeError("dag_context_node_mismatch")
    return context


def _artifact(path: Path, *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": str(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _write_node_receipt(
    path: Path,
    *,
    node_id: str,
    summary: str,
    artifact: dict[str, Any],
    goal_hash: str,
) -> None:
    _write_json(
        path,
        {
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": node_id,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": goal_hash,
            "artifacts": [artifact],
            "accepted_output": {
                "schema": "tau.canonical_dag_result.v1",
                "summary": summary,
                "status": "ACCEPTED",
                "artifacts": [artifact],
            },
            "commands_run": [f"canonical-dag-01:{node_id}"],
            "handoff_summary": summary,
            "errors": [],
            "policy_exceptions": [],
        },
    )


def extract_goal(*, goal_path: Path, output: Path, receipt: Path, delay: float) -> None:
    _context("extract-goal")
    time.sleep(max(0.0, delay))
    text = goal_path.read_text(encoding="utf-8")
    missing = [label for label in TOPOLOGY_LABELS if label not in text]
    if missing:
        raise RuntimeError(f"goal_topology_labels_missing:{','.join(missing)}")
    goal_section = text.split("## Goal", 1)[1].split("## Required Product Outcome", 1)[0]
    statement = " ".join(line.strip() for line in goal_section.splitlines() if line.strip())
    _write_json(
        output,
        {
            "schema": "tau.canonical_goal_digest.v1",
            "goal_source": str(goal_path),
            "goal_source_sha256": _sha256(goal_path),
            "goal_statement": statement,
            "canonical_dag_count": len(TOPOLOGY_LABELS),
            "canonical_dags": list(TOPOLOGY_LABELS),
        },
    )
    _write_node_receipt(
        receipt,
        node_id="extract-goal",
        summary="Extracted the immutable Tau goal and canonical DAG ladder.",
        artifact=_artifact(output, kind="goal-digest"),
        goal_hash=_sha256(goal_path),
    )


def validate_goal(
    *, goal_path: Path, digest: Path, output: Path, receipt: Path, delay: float
) -> None:
    _context("validate-goal")
    time.sleep(max(0.0, delay))
    payload = json.loads(digest.read_text(encoding="utf-8"))
    if payload.get("goal_source_sha256") != _sha256(goal_path):
        raise RuntimeError("goal_digest_source_hash_mismatch")
    if tuple(payload.get("canonical_dags", ())) != TOPOLOGY_LABELS:
        raise RuntimeError("goal_digest_topology_ladder_mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# Tau Goal Summary\n\n"
        f"{payload['goal_statement']}\n\n"
        "## Canonical DAG Ladder\n\n"
        + "\n".join(f"{index}. {label}" for index, label in enumerate(TOPOLOGY_LABELS, 1))
        + "\n",
        encoding="utf-8",
    )
    _write_node_receipt(
        receipt,
        node_id="validate-goal",
        summary="Validated the goal digest and wrote the human-readable summary.",
        artifact=_artifact(output, kind="goal-summary"),
        goal_hash=_sha256(goal_path),
    )


def _materialize_spec(*, repo_root: Path, run_root: Path, delay: float) -> Path:
    workflow = Path(__file__).resolve()
    goal = repo_root / "GOAL.md"
    artifacts = run_root / "artifacts"
    receipts = run_root / "receipts"
    run_dir = run_root / "run"
    digest = artifacts / "tau-goal-digest.json"
    summary = artifacts / "tau-goal-summary.md"
    command = [sys.executable, str(workflow)]
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "canonical-01-simple-linear",
        "run_dir": str(run_dir),
        "goal_hash": _sha256(goal),
        "goal": {
            "version": 1,
            "sha256": _sha256(goal),
            "statement": (
                "Tau lets a human launch and supervise a small ladder of real, "
                "goal-locked agent DAGs."
            ),
        },
        "workflow": {
            "schema": "tau.workflow_metadata.v1",
            "workflow_id": "canonical-01-simple-linear",
            "workflow_version": 1,
            "title": "Simple linear DAG",
            "summary": "Extract and validate the immutable Tau goal.",
            "topology": "LINEAR",
            "result_node_id": "validate-goal",
            "result_schema": "tau.canonical_dag_result.v1",
        },
        "nodes": [
            {
                "node_id": "extract-goal",
                "role": "goal-reader",
                "command": command
                + [
                    "extract-goal",
                    "--goal",
                    str(goal),
                    "--output",
                    str(digest),
                    "--receipt",
                    str(receipts / "extract-goal.json"),
                    "--delay",
                    str(delay),
                ],
                "depends_on": [],
                "receipt_path": str(receipts / "extract-goal.json"),
                "timeout_seconds": 30,
                "max_attempts": 1,
            },
            {
                "node_id": "validate-goal",
                "role": "deterministic-validator",
                "command": command
                + [
                    "validate-goal",
                    "--goal",
                    str(goal),
                    "--digest",
                    str(digest),
                    "--output",
                    str(summary),
                    "--receipt",
                    str(receipts / "validate-goal.json"),
                    "--delay",
                    str(delay),
                ],
                "depends_on": ["extract-goal"],
                "accepted_context_from": ["extract-goal"],
                "receipt_path": str(receipts / "validate-goal.json"),
                "timeout_seconds": 30,
                "max_attempts": 1,
            },
        ],
    }
    spec = run_root / "dag.json"
    _write_json(spec, payload)
    return spec


def run_workflow(
    *, run_root: Path, delay: float, view: bool, open_browser: bool, serve_after: float
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    run_root = run_root.expanduser().resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    spec = _materialize_spec(repo_root=repo_root, run_root=run_root, delay=delay)
    run_dir = run_root / "run"
    holder: dict[str, Any] = {}

    def execute() -> None:
        try:
            holder["receipt"] = run_generic_dag(spec_path=spec, resume=False)
        except Exception as exc:  # Preserve worker failure for the main thread.
            holder["error"] = exc

    worker = threading.Thread(target=execute, name="tau-canonical-01", daemon=True)
    worker.start()
    server = None
    server_thread = None
    viewer_url = None
    try:
        if view:
            deadline = time.monotonic() + 10
            database = run_dir / "dag-run.sqlite3"
            while not database.is_file() and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not database.is_file():
                raise RuntimeError("canonical_dag_viewer_store_unavailable")
            server = create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            viewer_url = f"http://127.0.0.1:{server.port}/"
            print(f"VIEWER_URL={viewer_url}", flush=True)
            if open_browser:
                webbrowser.open(viewer_url)
        worker.join()
        if "error" in holder:
            raise holder["error"]
        receipt = holder["receipt"]
        if receipt.get("status") != "PASS":
            raise RuntimeError(
                f"canonical_dag_failed:{receipt.get('status')}:{receipt.get('verdict')}"
            )
        if view and serve_after > 0:
            time.sleep(serve_after)
    finally:
        if server is not None:
            server.shutdown()
        if server_thread is not None:
            server_thread.join(timeout=2)
    summary = run_root / "artifacts" / "tau-goal-summary.md"
    result = {
        "schema": "tau.canonical_dag_example_result.v1",
        "dag_id": "canonical-01-simple-linear",
        "status": receipt["status"],
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_dir": str(run_dir),
        "spec_path": str(spec),
        "output_artifact": str(summary),
        "output_sha256": _sha256(summary),
        "viewer_command": f"uv run tau dag-view --run-dir {run_dir}",
        "viewer_url": viewer_url,
        "node_count": receipt["node_count"],
        "completed_node_count": receipt["completed_node_count"],
        "max_observed_concurrency": receipt["max_observed_concurrency"],
    }
    _write_json(run_root / "example-result.json", result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--run-root", type=Path, required=True)
    run.add_argument("--step-delay-seconds", type=float, default=1.5)
    run.add_argument("--view", action="store_true")
    run.add_argument("--no-open", action="store_true")
    run.add_argument("--serve-after-seconds", type=float, default=15.0)
    extract = commands.add_parser("extract-goal")
    extract.add_argument("--goal", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--receipt", type=Path, required=True)
    extract.add_argument("--delay", type=float, default=0.0)
    validate = commands.add_parser("validate-goal")
    validate.add_argument("--goal", type=Path, required=True)
    validate.add_argument("--digest", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--receipt", type=Path, required=True)
    validate.add_argument("--delay", type=float, default=0.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "extract-goal":
        extract_goal(
            goal_path=args.goal,
            output=args.output,
            receipt=args.receipt,
            delay=args.delay,
        )
        return 0
    if args.command == "validate-goal":
        validate_goal(
            goal_path=args.goal,
            digest=args.digest,
            output=args.output,
            receipt=args.receipt,
            delay=args.delay,
        )
        return 0
    result = run_workflow(
        run_root=args.run_root,
        delay=args.step_delay_seconds,
        view=args.view,
        open_browser=not args.no_open,
        serve_after=args.serve_after_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
