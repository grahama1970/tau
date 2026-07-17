"""Run canonical Tau DAGs 2-5 with the shared durable scheduler and viewer."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag


@dataclass(frozen=True)
class NodeDefinition:
    node_id: str
    role: str
    depends_on: tuple[str, ...] = ()
    fail_once: bool = False
    approval_gate: bool = False
    repair_gate: bool = False


@dataclass(frozen=True)
class DagDefinition:
    dag_id: str
    title: str
    max_concurrency: int
    nodes: tuple[NodeDefinition, ...]


DAGS = {
    2: DagDefinition(
        "canonical-02-multi-step-sequential",
        "Multi-step sequential",
        1,
        (
            NodeDefinition("intake", "bounded-intake"),
            NodeDefinition("analyze", "goal-analysis", ("intake",)),
            NodeDefinition("draft", "artifact-draft", ("analyze",)),
            NodeDefinition("verify", "deterministic-verification", ("draft",)),
        ),
    ),
    3: DagDefinition(
        "canonical-03-concurrent-fanout-fanin",
        "Concurrent fan-out/fan-in",
        3,
        (
            NodeDefinition("source", "source-lock"),
            NodeDefinition("docs", "documentation-analysis", ("source",)),
            NodeDefinition("tests", "test-analysis", ("source",)),
            NodeDefinition("risks", "risk-analysis", ("source",)),
            NodeDefinition("integrate", "accepted-branch-integration", ("docs", "tests", "risks")),
        ),
    ),
    4: DagDefinition(
        "canonical-04-mixed-retry-approval",
        "Mixed topology with retry and human approval",
        2,
        (
            NodeDefinition("plan", "goal-locked-plan"),
            NodeDefinition("implement", "bounded-implementation", ("plan",)),
            NodeDefinition("test", "independent-test", ("plan",)),
            NodeDefinition("review", "typed-review", ("implement", "test"), fail_once=True),
            NodeDefinition(
                "release", "approval-gated-side-effect", ("review",), approval_gate=True
            ),
        ),
    ),
    5: DagDefinition(
        "canonical-05-durable-resume-repair",
        "Durable mixed topology with targeted repair",
        3,
        (
            NodeDefinition("discover", "goal-and-source-lock"),
            NodeDefinition("build", "bounded-build", ("discover",)),
            NodeDefinition("test", "independent-test", ("discover",)),
            NodeDefinition("document", "operator-documentation", ("discover",)),
            NodeDefinition(
                "reconcile",
                "branch-reconciliation",
                ("build", "test", "document"),
                repair_gate=True,
            ),
            NodeDefinition("release", "human-release", ("reconcile",), approval_gate=True),
        ),
    ),
}


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _goal_statement(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    section = text.split("## Goal", 1)[1].split("## Required Product Outcome", 1)[0]
    return " ".join(line.strip() for line in section.splitlines() if line.strip())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _materialize_spec(
    definition: DagDefinition,
    *,
    run_root: Path,
    goal: Path,
    delay: float,
    fail_node: str | None,
) -> Path:
    worker = Path(__file__).with_name("worker.py").resolve()
    approval = run_root / "authorizations" / "human-release.json"
    repair = run_root / "authorizations" / "targeted-repair.json"
    nodes = []
    for item in definition.nodes:
        output = run_root / "artifacts" / f"{item.node_id}.json"
        receipt = run_root / "receipts" / f"{item.node_id}.json"
        command = [
            sys.executable,
            str(worker),
            "--node-id",
            item.node_id,
            "--role",
            item.role,
            "--output",
            str(output),
            "--receipt",
            str(receipt),
            "--goal",
            str(goal),
            "--delay",
            str(delay),
        ]
        if item.fail_once:
            command += ["--fail-once-marker", str(run_root / "state" / f"{item.node_id}.retry")]
        if item.approval_gate:
            command += ["--approval", str(approval)]
        if item.repair_gate:
            command += ["--repair-authorization", str(repair)]
        for dependency in item.depends_on:
            command += ["--input", str(run_root / "artifacts" / f"{dependency}.json")]
        if item.approval_gate:
            command += ["--rollback", str(run_root / "rollback" / f"{item.node_id}.json")]
        if fail_node == item.node_id:
            command += ["--blocked-reason", f"operator_requested_failure:{item.node_id}"]
        nodes.append(
            {
                "node_id": item.node_id,
                "role": item.role,
                "command": command,
                "depends_on": list(item.depends_on),
                "receipt_path": str(receipt),
                "timeout_seconds": 30,
                "max_attempts": 2 if item.fail_once else 1,
            }
        )
    spec = run_root / "dag.json"
    _write_json(
        spec,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": definition.dag_id,
            "run_dir": str(run_root / "run"),
            "goal_hash": _sha256(goal),
            "goal": {
                "version": 1,
                "sha256": _sha256(goal),
                "statement": _goal_statement(goal),
            },
            "max_concurrency": definition.max_concurrency,
            "nodes": nodes,
        },
    )
    return spec


def _authorization(path: Path, *, kind: str, dag_id: str) -> None:
    _write_json(
        path,
        {
            "schema": "tau.canonical_dag_authorization.v1",
            "kind": kind,
            "dag_id": dag_id,
            "authorized_by": "human-cli-flag",
        },
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    definition = DAGS[args.dag]
    run_root = args.run_root.expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    goal = repo_root / "GOAL.md"
    if args.approve:
        _authorization(
            run_root / "authorizations" / "human-release.json",
            kind="human-release",
            dag_id=definition.dag_id,
        )
    if args.repair:
        _authorization(
            run_root / "authorizations" / "targeted-repair.json",
            kind="targeted-repair",
            dag_id=definition.dag_id,
        )
    spec = _materialize_spec(
        definition,
        run_root=run_root,
        goal=goal,
        delay=args.step_delay_seconds,
        fail_node=args.fail_node,
    )
    holder: dict[str, Any] = {}

    def execute() -> None:
        try:
            holder["receipt"] = run_generic_dag(spec_path=spec, resume=args.resume)
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    server = None
    server_thread = None
    viewer_url = None
    try:
        if args.view:
            database = run_root / "run" / "dag-run.sqlite3"
            deadline = time.monotonic() + 10
            while not database.is_file() and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not database.is_file():
                raise RuntimeError("canonical_dag_viewer_store_unavailable")
            server = create_dag_viewer_server(run_dir=run_root / "run", host="127.0.0.1", port=0)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            viewer_url = f"http://127.0.0.1:{server.port}/"
            print(f"VIEWER_URL={viewer_url}", flush=True)
            if not args.no_open:
                webbrowser.open(viewer_url)
        thread.join()
        if "error" in holder:
            raise holder["error"]
        receipt = holder["receipt"]
        if args.view and args.serve_after_seconds > 0:
            time.sleep(args.serve_after_seconds)
    finally:
        if server:
            server.shutdown()
        if server_thread:
            server_thread.join(timeout=2)
    result = {
        "schema": "tau.canonical_dag_example_result.v1",
        "dag_id": definition.dag_id,
        "title": definition.title,
        "status": receipt["status"],
        "verdict": receipt["verdict"],
        "mocked": False,
        "live": receipt["live"],
        "provider_live": False,
        "durable": receipt["durable"],
        "node_count": receipt["node_count"],
        "completed_node_count": receipt["completed_node_count"],
        "max_observed_concurrency": receipt["max_observed_concurrency"],
        "resumed_node_count": sum(node.get("resumed") is True for node in receipt["nodes"]),
        "run_dir": receipt["run_dir"],
        "viewer_url": viewer_url,
        "viewer_command": f"uv run tau dag-view --run-dir {receipt['run_dir']}",
        "output_artifact": str(run_root / "artifacts" / f"{definition.nodes[-1].node_id}.json"),
    }
    _write_json(run_root / "example-result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dag", type=int, choices=sorted(DAGS), required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--step-delay-seconds", type=float, default=1.25)
    parser.add_argument("--view", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--serve-after-seconds", type=float, default=15.0)
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--resume", action="store_true")
    node_ids = {node.node_id for dag in DAGS.values() for node in dag.nodes}
    parser.add_argument("--fail-node", choices=sorted(node_ids))
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
