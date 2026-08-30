#!/usr/bin/env python3
"""Live proof for Herdr-backed Tau headless worker endpoints."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256  # noqa: E402
from tau_coding.runtime_backends.herdr import (  # noqa: E402
    HerdrRuntimeBackend,
    herdr_cleanup_authorization,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--session", default="default")
    args = parser.parse_args()
    out_path = Path(args.out).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    marker = f"issue315_tau_herdr_worker_{int(time.time() * 1000)}"
    backend = HerdrRuntimeBackend(session=args.session, command_timeout_seconds=10)
    errors: list[str] = []
    scope = backend.ensure_scope(
        FrozenJson.from_value(
            {"run_id": marker, "owner": "tau", "cwd": str(work), "label": "issue315-herdr"}
        )
    ).to_value()
    leases = []
    try:
        for index, node_id in enumerate(("agent-a", "agent-b"), start=1):
            receipt = work / f"{node_id}-settlement.json"
            worker_path = work / f"{node_id}-worker.py"
            worker_payload = {
                "schema": "tau.agent_node_settlement.v1",
                "node_id": node_id,
                "status": "PASS",
                "verdict": "PASS",
                "marker": marker,
                "mocked": False,
                "live": True,
                "provider_live": False,
                "turn_count": 2,
                "tool_effect_count": 1,
                "evidence_count": 1,
                "errors": [],
            }
            worker_path.write_text(
                "import json, time\n"
                "from pathlib import Path\n"
                f"path=Path({json.dumps(str(receipt))})\n"
                f"payload={worker_payload!r}\n"
                "path.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\\n', encoding='utf-8')\n"
                "print(json.dumps(payload, sort_keys=True))\n"
                "time.sleep(0.5)\n",
                encoding="utf-8",
            )
            lease = backend.spawn(
                FrozenJson.from_value(
                    {
                        "run_id": marker,
                        "scope_id": scope["workspace_id"],
                        "command": [f"{sys.executable} {worker_path}"],
                        "cwd": str(work),
                        "owner": "tau",
                        "attempt_number": index,
                        "attempt_id": f"attempt-{index}",
                        "node_id": node_id,
                        "plan_revision": canonical_sha256({"issue": 315, "plan": 1}),
                        "dag_id": "issue315-herdr-agent-node",
                        "execution_token": f"token-{index}",
                        "work_order_sha256": canonical_sha256({"node_id": node_id, "marker": marker}),
                        "goal_hash": canonical_sha256({"issue": 315, "goal": "herdr-agent-node"}),
                        "lease_seconds": 120,
                    }
                )
            )
            leases.append(lease)
        time.sleep(1.5)
        events = [backend.observe(lease).to_payload() for lease in leases]
        owned = [lease.to_payload() for lease in backend.list_owned(marker)]
        receipts = [
            json.loads((work / f"{node_id}-settlement.json").read_text(encoding="utf-8"))
            for node_id in ("agent-a", "agent-b")
        ]
        if len({lease.scope_id for lease in leases}) != 1:
            errors.append("workers_not_in_one_workspace")
        if len({lease.endpoint_id for lease in leases}) != 2:
            errors.append("workers_not_distinct_endpoints")
        if not all(item.get("status") == "PASS" for item in receipts):
            errors.append("worker_receipt_missing_pass")
        if len(owned) != 2:
            errors.append(f"owned_endpoint_count:{len(owned)}")
        terminations = [
            backend.terminate(lease, herdr_cleanup_authorization(lease)).to_value()
            for lease in leases
        ]
        if not all(item.get("status") == "PASS" for item in terminations):
            errors.append("termination_not_verified")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"herdr_live_error:{type(exc).__name__}:{exc}")
        events = []
        owned = []
        receipts = []
        terminations = []
    payload = {
        "schema": "tau.herdr_agent_node_backend_proof.v1",
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "marker": marker,
        "scope": scope,
        "endpoint_ids": [lease.endpoint_id for lease in leases],
        "workspace_ids": [lease.scope_id for lease in leases],
        "owned_endpoint_count": len(owned),
        "events": events,
        "settlement_receipts": receipts,
        "terminations": terminations,
        "proof_boundary": {
            "proves": "HerdrRuntimeBackend can host two Tau-owned headless worker commands in one live Herdr workspace, bind exact endpoint leases, observe backend liveness, read worker settlement receipts from disk, and cleanup only exact owned endpoints.",
            "does_not_prove": "provider semantic quality, full scheduler kill/restart adoption, or GOAL.md completion",
        },
        "errors": errors,
    }
    write_durable_json(out_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
