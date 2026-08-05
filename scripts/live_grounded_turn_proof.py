"""Live proof for tau#311: grounded + json_object Tau-native model turns.

Two live Tau-native agent nodes over scillm#28 transports on :4001:

1. A source-grounded turn (source, grounding_threshold, grounding_retries)
   whose grounding status is read back from the Tau settlement receipt.
2. A response_format json_object turn whose terminal text parses as JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from tau_ai.scillm_transport import ScillmTransportProvider  # noqa: E402
from tau_coding.dag_runtime.agent_node import AgentNodeRun, ToolPolicy  # noqa: E402
from tau_coding.dag_runtime.model import canonical_sha256  # noqa: E402

BASE_URL = os.environ.get("SCILLM_BASE_URL", "http://localhost:4001")
API_KEY = os.environ.get("SCILLM_MASTER_KEY", "sk-dev-proxy-123")
GOAL_HASH = canonical_sha256({"goal": "tau#311 grounded transport options"})
SOURCE = (
    "The Tau harness records every accepted turn in a hash-chained journal. "
    "Settlement requires required evidence receipts. Provider completion "
    "claims never settle a Tau node."
)


async def _run(node_id: str, prompt: str, **provider_options: Any) -> dict[str, Any]:
    provider = ScillmTransportProvider(
        base_url=BASE_URL,
        api_key=API_KEY,
        profile_id="claude-model-turn",
        correlation={
            "tau_run_id": f"grounding-proof-{int(time.time())}",
            "node_id": node_id,
            "attempt": 1,
            "goal_hash": GOAL_HASH,
        },
        timeout_seconds=180,
        metadata={"caller_skill": "tau-311-live-proof"},
        **provider_options,
    )
    run = AgentNodeRun(
        work_order={
            "schema": "tau.agent_node.v1",
            "run_id": f"run-{node_id}",
            "node_id": node_id,
            "attempt_id": f"{node_id}-attempt-1",
            "attempt": 1,
            "goal_hash": GOAL_HASH,
            "plan_sha256": canonical_sha256({"proof": "tau#311"}),
            "model": "profile:claude-model-turn",
        },
        policy=ToolPolicy(goal_hash=GOAL_HASH, allowed_tools=()),
        provider=provider,
        tools=[],
        max_turns=2,
    )
    await run.run(prompt)
    settlement = run.settle()
    return {
        "settlement": settlement,
        "final_text": run.turn_receipts[-1]["assistant_text"] if run.turn_receipts else "",
        "transport_id": provider.transport_id,
        "turn_results": provider.turn_results,
    }


async def main() -> int:
    out_dir = REPO / "artifacts" / "agent_native_live_proof" / f"grounding-{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)

    grounded = await _run(
        "grounded-node",
        f"Source:\n{SOURCE}\n\nUsing only this source, state in one sentence "
        "what settlement requires.",
        source=SOURCE,
        grounding_threshold=0.6,
        grounding_retries=2,
    )
    grounding = grounded["settlement"]["grounding"]
    print("grounded settlement.grounding:", json.dumps(grounding))

    structured = await _run(
        "json-node",
        'Return a JSON object with keys "harness" (string) and "journal_hash_chained" '
        "(boolean) describing the Tau agent harness.",
        response_format={"type": "json_object"},
    )
    parsed: Any = None
    try:
        parsed = json.loads(structured["final_text"])
    except json.JSONDecodeError:
        pass
    print("json_object parsed:", json.dumps(parsed))

    summary = {
        "schema": "tau.grounded_turn_proof_summary.v1",
        "goal_hash": GOAL_HASH,
        "ticket": "tau#311",
        "grounded_node": {
            "settlement_state": grounded["settlement"]["state"],
            "grounding": grounding,
            "transport_id": grounded["transport_id"],
        },
        "json_node": {
            "settlement_state": structured["settlement"]["state"],
            "parsed_json": parsed,
            "transport_id": structured["transport_id"],
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "grounded-settlement.json").write_text(
        json.dumps(grounded["settlement"], indent=2)
    )
    (out_dir / "grounded-transport-results.json").write_text(
        json.dumps(grounded["turn_results"], indent=2)
    )
    (out_dir / "json-settlement.json").write_text(
        json.dumps(structured["settlement"], indent=2)
    )
    ok = (
        grounded["settlement"]["state"] == "completed"
        and isinstance(grounding, dict)
        and grounding.get("verified") is True
        and structured["settlement"]["state"] == "completed"
        and isinstance(parsed, dict)
    )
    print(f"GROUNDED TURN PROOF {'PASS' if ok else 'FAIL'}: {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--live" not in sys.argv:
        print("refusing to run: live provider calls; pass --live")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main()))
