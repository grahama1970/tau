"""Live local API proof for operator-action POST/GET control receipts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore, _operator_action_head
from tau_coding.dag_viewer.server import create_dag_viewer_server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("local/agentic-evals/tau-operator-control-api/work"),
    )
    args = parser.parse_args()
    proof = run_proof(args.work_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, sort_keys=True))
    if proof["status"] != "PASS":
        raise SystemExit(1)


def run_proof(work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    run_dir = work_dir / "run"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "operator-control-api-run",
        "run_dir": str(run_dir),
        "nodes": [
            {
                "node_id": "worker",
                "role": "worker",
                "command": ["true"],
                "receipt_path": str(run_dir / "worker-receipt.json"),
            }
        ],
    }
    plan = compile_generic_dag_plan(plan_payload, source_path=run_dir / "dag.json")
    database = run_dir / "dag-run.sqlite3"
    checks: dict[str, bool] = {}
    server = None
    thread: threading.Thread | None = None
    post_payload: dict[str, Any] = {}
    receipt_payload: dict[str, Any] = {}
    projection_payload: dict[str, Any] = {}
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(
            plan=plan, run_id="operator-control-api-run", owner_id="scheduler"
        )
        store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="worker", attempt=1)
        head_seq, head_sha256 = _operator_action_head(store._connection, "operator-control-api-run")
        request = _pause_request(plan, head_seq=head_seq, head_sha256=head_sha256)
        request_path = work_dir / "pause-request.json"
        request_path.write_text(
            json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        server = create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = server.url.rstrip("/")
            post_payload = _curl_json(
                [
                    "curl",
                    "-fsS",
                    "-H",
                    "Content-Type: application/json",
                    "--data-binary",
                    f"@{request_path}",
                    f"{base_url}/api/v1/operator-actions",
                ]
            )
            checks["post_validated"] = post_payload.get("status") == "VALIDATED"
            claimed = store.claim_operator_action(lease)
            checks["scheduler_lease_claimed"] = (
                isinstance(claimed, dict) and claimed.get("action_request_id") == "pause-1"
            )
            completed = store.complete_operator_action(
                lease,
                action_request_id="pause-1",
                status="APPLIED",
                outcome="paused",
                code="operator_action_pause_applied",
                canonical_transition={"safe_point": "scheduler_idle_boundary"},
            )
            checks["scheduler_lease_applied"] = completed.get("status") == "APPLIED"
            receipt_payload = _curl_json(
                ["curl", "-fsS", f"{base_url}/api/v1/operator-actions/pause-1/receipt"]
            )
            projection_payload = _curl_json(
                ["curl", "-fsS", f"{base_url}/api/v1/operator-actions/pause-1"]
            )
            checks["receipt_status_applied"] = receipt_payload.get("status") == "APPLIED"
            checks["receipt_schema"] = (
                receipt_payload.get("schema") == "tau.operator_action_receipt.v1"
            )
            checks["projection_lifecycle_received_to_applied"] = [
                item.get("status") for item in projection_payload.get("lifecycle", [])
            ] == ["RECEIVED", "VALIDATED", "CLAIMED", "APPLIED"]
        finally:
            server.shutdown()
            thread.join(timeout=2)
    ok = all(checks.values())
    return {
        "schema": "tau.operator_control_api_proof.v1",
        "status": "PASS" if ok else "BLOCKED",
        "ok": ok,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_dir": str(run_dir),
        "checks": checks,
        "post_status": post_payload.get("status"),
        "receipt_status": receipt_payload.get("status"),
        "projection_status": projection_payload.get("status"),
        "lifecycle": projection_payload.get("lifecycle"),
        "proof_scope": {
            "proves": [
                "The loopback local API accepts tau.operator_action_request.v1 via POST.",
                "The request is journaled before scheduler-owned completion.",
                "A scheduler lease-holder can apply the request and the API returns "
                "the terminal receipt.",
            ],
            "does_not_prove": [
                "Authentication or non-loopback production authorization.",
                "Semantic quality of provider/model output.",
            ],
        },
    }


def _pause_request(plan: Any, *, head_seq: int, head_sha256: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "schema": "tau.operator_action_request.v1",
        "action_request_id": "pause-1",
        "idempotency_key": "pause-1",
        "run_id": "operator-control-api-run",
        "plan_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "goal_hash": plan.runtime_goal_hash,
        "node_id": "worker",
        "attempt": 1,
        "action": "pause",
        "actor": "graham",
        "principal": "graham",
        "authority_class": "human_operator",
        "observed_journal_seq": head_seq,
        "observed_journal_head_sha256": head_sha256,
        "requested_safe_point": "scheduler_boundary",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "arguments": {},
        "client_correlation": {"source": "agentic-eval"},
    }


def _curl_json(argv: list[str]) -> dict[str, Any]:
    completed = subprocess.run(argv, check=True, text=True, capture_output=True)
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=True) as readback:
        readback.write(completed.stdout)
        readback.flush()
        readback.seek(0)
        payload = json.load(readback)
    if not isinstance(payload, dict):
        raise RuntimeError("curl_json_payload_not_object")
    return payload


if __name__ == "__main__":
    main()
