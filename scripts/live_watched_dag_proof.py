"""Live proof for tau#312: watched store-backed run with a live viewer URL.

Runs a two-node Tau-native DAG (live scillm#28 transports) through
``run_dag_plan_watched``. During execution of the first node the returned
viewer URL is fetched and must serve the Tau Live DAG app; afterwards the run
receipt is read back containing the ``tau.dag_viewer_link.v1`` payload.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from tau_ai.scillm_transport import ScillmTransportProvider  # noqa: E402
from tau_coding.dag_runtime.agent_node_adapter import execute_tau_agent_node  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.watched_run import run_dag_plan_watched  # noqa: E402

BASE_URL = os.environ.get("SCILLM_BASE_URL", "http://localhost:4001")
API_KEY = os.environ.get("SCILLM_MASTER_KEY", "sk-dev-proxy-123")
GOAL_HASH = canonical_sha256({"goal": "watched live DAG proof", "ticket": "tau#312"})
PROFILE_BY_NODE = {"worker": "claude-model-turn", "verifier": "codex-model-turn"}


def main() -> int:
    run_id = f"watched-live-{int(time.time())}"
    out_dir = REPO / "artifacts" / "agent_native_live_proof" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": run_id,
        "run_dir": str(out_dir / "run"),
        "nodes": [
            {
                "node_id": node_id,
                "role": role,
                "tau_agent": {
                    "prompt": prompt,
                    "role": role,
                    "model": f"profile:{PROFILE_BY_NODE[node_id]}",
                },
                "depends_on": deps,
                "accepted_context_from": deps,
                "receipt_path": str(out_dir / "receipts" / f"{node_id}.json"),
                "timeout_seconds": 300,
                "max_attempts": 1,
            }
            for node_id, role, prompt, deps in (
                (
                    "worker",
                    "backend",
                    "State in one sentence what a hash-chained journal guarantees.",
                    [],
                ),
                (
                    "verifier",
                    "review",
                    "Given the upstream answer, reply 'VERDICT: PASS' if it is a "
                    "coherent one-sentence claim about hash-chained journals, else "
                    "'VERDICT: FAIL' with the reason.",
                    ["worker"],
                ),
            )
        ],
    }
    plan = compile_generic_dag_plan(spec, source_path=out_dir / "dag-spec.json")
    probe: dict[str, Any] = {}

    def provider_factory(node: Any, config: Any) -> ScillmTransportProvider:
        return ScillmTransportProvider(
            base_url=BASE_URL,
            api_key=API_KEY,
            profile_id=PROFILE_BY_NODE[node.node_id],
            correlation={
                "tau_run_id": run_id,
                "node_id": node.node_id,
                "attempt": 1,
                "goal_hash": GOAL_HASH,
            },
            timeout_seconds=240,
        )

    def execute(plan_node: Any, accepted_inputs: Any, execution: Any) -> dict[str, Any]:
        if "status_during_run" not in probe:
            # First node is executing and has NOT settled: the viewer must
            # already serve the Tau Live DAG app at the announced URL.
            with urllib.request.urlopen(probe["url"], timeout=10) as response:
                probe["status_during_run"] = response.status
                probe["body_is_dag_app"] = "Tau" in response.read(4096).decode(
                    "utf-8", "replace"
                )
            with urllib.request.urlopen(probe["url"] + "healthz", timeout=10) as response:
                probe["healthz"] = json.loads(response.read().decode())
        return execute_tau_agent_node(
            plan_node,
            accepted_inputs,
            execution,
            goal_hash=GOAL_HASH,
            provider_factory=provider_factory,
            tools_factory=lambda node, config: [],
        )

    watched = run_dag_plan_watched(
        plan,
        execute_node=execute,
        run_dir=out_dir / "run",
        on_viewer_url=lambda url: probe.setdefault("url", url),
    )
    receipt = watched.receipt
    (out_dir / "watched-receipt.json").write_text(json.dumps(receipt, indent=2))
    (out_dir / "viewer-probe.json").write_text(json.dumps(probe, indent=2))
    summary = {
        "schema": "tau.watched_live_proof_summary.v1",
        "run_id": run_id,
        "ticket": "tau#312",
        "scheduler_status": watched.result.status,
        "viewer_url": probe.get("url"),
        "viewer_http_status_during_run": probe.get("status_during_run"),
        "viewer_serves_dag_app": probe.get("body_is_dag_app"),
        "viewer_healthz": probe.get("healthz"),
        "run_store_exists": Path(receipt["run_store_path"]).is_file(),
        "receipt_has_viewer_link": receipt["dag_viewer_link"]["schema"]
        == "tau.dag_viewer_link.v1",
        "receipt_viewer_url": (receipt["viewer"] or {}).get("url"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    ok = (
        watched.result.status == "PASS"
        and probe.get("status_during_run") == 200
        and probe.get("body_is_dag_app") is True
        and summary["run_store_exists"]
        and summary["receipt_has_viewer_link"]
        and summary["receipt_viewer_url"] == probe.get("url")
    )
    print(f"WATCHED LIVE PROOF {'PASS' if ok else 'FAIL'}: {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--live" not in sys.argv:
        print("refusing to run: live provider calls; pass --live")
        raise SystemExit(2)
    raise SystemExit(main())
