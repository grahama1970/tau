"""Live proof for tau#313: durable agent events read in-flight from SQLite.

Runs a live Tau-native tool-calling node (claude-model-turn over scillm#28)
under the canonical scheduler with a durable store. While the node is still
running — inside the tool executor, after the effect is admitted but before
the next model turn — a SEPARATE SQLite connection reads the persisted agent
events, proving external clients can follow the run without the live object.
Afterwards the projection is rebuilt from the store alone and the
`tau agent-events` cursor surface is read back.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from tau_agent.tools import AgentTool, AgentToolResult  # noqa: E402
from tau_ai.scillm_transport import ScillmTransportProvider  # noqa: E402
from tau_coding.dag_runtime.agent_events import (  # noqa: E402
    load_agent_events,
    rebuild_agent_projection,
)
from tau_coding.dag_runtime.agent_node_adapter import execute_tau_agent_node  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402
from tau_coding.dag_runtime.watched_run import run_dag_plan_watched  # noqa: E402

BASE_URL = os.environ.get("SCILLM_BASE_URL", "http://localhost:4001")
API_KEY = os.environ.get("SCILLM_MASTER_KEY", "sk-dev-proxy-123")
GOAL_HASH = canonical_sha256({"goal": "durable agent events live proof", "ticket": "tau#313"})


def main() -> int:
    run_id = f"agent-events-live-{int(time.time())}"
    out_dir = REPO / "artifacts" / "agent_native_live_proof" / run_id
    run_dir = out_dir / "run"
    workspace = out_dir / "workspace"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "nodes": [
            {
                "node_id": "worker",
                "role": "backend",
                "tau_agent": {
                    "prompt": (
                        "Call write_file exactly once with path='proof.txt' and "
                        "content='durable agent events'. Then state what you wrote."
                    ),
                    "role": "backend",
                    "model": "profile:claude-model-turn",
                },
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(out_dir / "receipts" / "worker.json"),
                "timeout_seconds": 300,
                "max_attempts": 1,
            }
        ],
    }
    plan = compile_generic_dag_plan(spec, source_path=out_dir / "dag-spec.json")
    probe: dict[str, Any] = {}

    def _tool() -> AgentTool:
        async def _executor(arguments: Any, signal: Any = None) -> AgentToolResult:
            rel = str(arguments.get("path", ""))
            target = (workspace / rel).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(arguments.get("content", "")))
            # IN-FLIGHT READBACK: separate connection, same canonical store,
            # while this node's model turn loop is still running.
            reader = SqliteDagRunStore(run_dir / "dag-run.sqlite3")
            try:
                entries = load_agent_events(
                    reader, f"generic:{run_id}", node_id="worker"
                )
            finally:
                reader.close()
            probe["inflight_event_types"] = [
                item["agent_event"]["event_type"] for item in entries
            ]
            probe["inflight_count"] = len(entries)
            return AgentToolResult(
                tool_call_id="", name="write_file", ok=True, content=f"wrote {rel}"
            )

        return AgentTool(
            name="write_file",
            description="Write a file under the workspace.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            executor=_executor,
        )

    def factory(store: SqliteDagRunStore, get_lease: Any) -> Any:
        def execute(plan_node: Any, accepted_inputs: Any, execution: Any) -> dict[str, Any]:
            return execute_tau_agent_node(
                plan_node,
                accepted_inputs,
                execution,
                goal_hash=GOAL_HASH,
                provider_factory=lambda node, config: ScillmTransportProvider(
                    base_url=BASE_URL,
                    api_key=API_KEY,
                    profile_id="claude-model-turn",
                    correlation={
                        "tau_run_id": run_id,
                        "node_id": node.node_id,
                        "attempt": 1,
                        "goal_hash": GOAL_HASH,
                    },
                    timeout_seconds=240,
                ),
                tools_factory=lambda node, config: [_tool()],
                run_store=store,
                lease=get_lease(),
            )

        return execute

    watched = run_dag_plan_watched(
        plan, execute_node_factory=factory, run_dir=run_dir, watch=False
    )

    # Post-run: rebuild the projection purely from the persisted store.
    reader = SqliteDagRunStore(run_dir / "dag-run.sqlite3")
    try:
        entries = load_agent_events(reader, f"generic:{run_id}", node_id="worker")
        projection = rebuild_agent_projection(
            entries, run_id=f"generic:{run_id}", node_id="worker"
        )
    finally:
        reader.close()

    # CLI cursor surface readback.
    cli = subprocess.run(
        [
            "uv", "run", "tau", "agent-events",
            "--run-dir", str(run_dir), "--node", "worker", "--after-seq", "2",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    cli_payload = json.loads(cli.stdout) if cli.returncode == 0 else {"ok": False}

    summary = {
        "schema": "tau.agent_events_live_proof_summary.v1",
        "run_id": run_id,
        "ticket": "tau#313",
        "scheduler_status": watched.result.status,
        "inflight_readback": {
            "count": probe.get("inflight_count"),
            "event_types": probe.get("inflight_event_types"),
            "tool_request_admitted_visible": (
                "tool_request_admitted" in (probe.get("inflight_event_types") or [])
            ),
        },
        "persisted_event_count": len(entries),
        "rebuilt_projection_lifecycle": projection["lifecycle"],
        "rebuilt_projection_turns": projection["turns"],
        "cli_cursor_readback_ok": cli_payload.get("ok") is True,
        "cli_cursor_count": cli_payload.get("count"),
        "artifact_written": (workspace / "proof.txt").is_file(),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "persisted-agent-events.json").write_text(json.dumps(list(entries), indent=2))
    (out_dir / "rebuilt-projection.json").write_text(json.dumps(projection, indent=2))
    print(json.dumps(summary, indent=2))
    ok = (
        watched.result.status == "PASS"
        and summary["inflight_readback"]["tool_request_admitted_visible"]
        and summary["rebuilt_projection_lifecycle"] == "completed"
        and summary["cli_cursor_readback_ok"]
        and summary["artifact_written"]
    )
    print(f"AGENT EVENTS LIVE PROOF {'PASS' if ok else 'FAIL'}: {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--live" not in sys.argv:
        print("refusing to run: live provider calls; pass --live")
        raise SystemExit(2)
    raise SystemExit(main())
