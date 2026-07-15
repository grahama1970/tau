#!/usr/bin/env python3
"""Run and observe a deterministic creator-reviewer DAG through the live API."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag


def _request_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310 - loopback URL
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise RuntimeError("dag_viewer_smoke_response_invalid")
    return payload


def _materialize_spec(*, root: Path, delay: float) -> Path:
    example = Path(__file__).resolve().parents[1] / "examples" / "dag-viewer-creator-reviewer"
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    work_order = root / "work-order.json"
    work_order.write_text('{"task":"deterministic creator-reviewer smoke"}\n')
    python = sys.executable
    delay_arg = str(delay)
    creator_receipt = root / "creator-receipt.json"
    continuation_receipt = root / "continuation-receipt.json"
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "dag-viewer-creator-reviewer",
        "run_dir": str(run_dir),
        "nodes": [
            {
                "node_id": "creator-reviewer",
                "role": "artifact-transaction",
                "command": [
                    python,
                    str(example / "producer.py"),
                    "--artifact-root",
                    str(root / "artifacts"),
                    "--receipt",
                    str(creator_receipt),
                    "--work-order",
                    str(work_order),
                    "--step-delay-seconds",
                    delay_arg,
                ],
                "depends_on": [],
                "receipt_path": str(creator_receipt),
                "work_order_path": str(work_order),
                "max_attempts": 2,
                "transaction": {
                    "schema": "tau.generic_artifact_transaction.v1",
                    "transaction_id": "tx-creator-reviewer",
                    "artifact_root": str(root / "artifacts"),
                    "producer_id": "creator",
                    "validator": {
                        "validator_id": "deterministic-validator",
                        "command": [
                            python,
                            str(example / "validator.py"),
                            "--step-delay-seconds",
                            delay_arg,
                        ],
                    },
                    "reviewer": {
                        "reviewer_id": "deterministic-reviewer",
                        "command": [
                            python,
                            str(example / "reviewer.py"),
                            "--step-delay-seconds",
                            delay_arg,
                        ],
                    },
                    "acceptance": {"require_output_change_after_revise": True},
                },
            },
            {
                "node_id": "continuation",
                "role": "dependent-continuation",
                "command": [
                    python,
                    str(example / "continuation.py"),
                    "--receipt",
                    str(continuation_receipt),
                    "--marker",
                    str(root / "continuation.txt"),
                    "--step-delay-seconds",
                    delay_arg,
                ],
                "depends_on": ["creator-reviewer"],
                "accepted_context_from": ["creator-reviewer"],
                "receipt_path": str(continuation_receipt),
            },
        ],
    }
    spec_path = root / "dag.json"
    spec_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return spec_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--step-delay-seconds", type=float, default=0.25)
    parser.add_argument("--viewer-url-out", type=Path)
    parser.add_argument("--serve-after-seconds", type=float, default=0.0)
    args = parser.parse_args()
    if args.step_delay_seconds < 0:
        raise SystemExit("--step-delay-seconds must be non-negative")
    output = args.out.expanduser().resolve()
    root = (args.run_root or output.with_suffix("")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    spec_path = _materialize_spec(root=root, delay=args.step_delay_seconds)
    run_dir = root / "run"
    result: dict[str, Any] = {}
    failure: list[BaseException] = []

    def execute() -> None:
        try:
            result.update(
                run_generic_dag(
                    spec_path=spec_path,
                    resume=False,
                    diagnostic_step_delay_seconds=args.step_delay_seconds,
                )
            )
        except BaseException as exc:  # preserved for the calling thread
            failure.append(exc)

    worker = threading.Thread(target=execute, name="tau-dag-viewer-smoke", daemon=True)
    worker.start()
    deadline = time.monotonic() + 15.0
    server = None
    while time.monotonic() < deadline and server is None:
        if failure:
            raise failure[0]
        try:
            server = create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except Exception:
            time.sleep(0.05)
    if server is None:
        raise RuntimeError("dag_viewer_smoke_server_start_timeout")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    if args.viewer_url_out is not None:
        args.viewer_url_out.parent.mkdir(parents=True, exist_ok=True)
        args.viewer_url_out.write_text(server.url + "\n", encoding="utf-8")
    observed = {
        "creator_attempt_1_visible": False,
        "reviewer_revise_visible": False,
        "creator_attempt_2_visible": False,
        "accepted_only_after_receipt": False,
        "dependent_released_after_acceptance": False,
        "page_refresh_reconstructed_state": False,
    }
    snapshots: list[dict[str, Any]] = []
    try:
        while time.monotonic() < deadline and (worker.is_alive() or not all(observed.values())):
            snapshot = _request_json(f"{server.url}api/v1/state")
            snapshots.append(snapshot)
            nodes = {item["node_id"]: item for item in snapshot["nodes"]}
            creator = nodes["creator-reviewer"]
            transaction = creator.get("transaction") or {}
            attempts = {item["attempt"]: item for item in transaction.get("attempts", [])}
            observed["creator_attempt_1_visible"] |= 1 in attempts
            observed["reviewer_revise_visible"] |= (
                attempts.get(1, {}).get("reviewer_verdict") == "REVISE"
            )
            observed["creator_attempt_2_visible"] |= 2 in attempts
            observed["accepted_only_after_receipt"] |= (
                attempts.get(2, {}).get("reviewer_verdict") == "PASS"
                and creator["admission"]["accepted"] is False
                and transaction.get("state") == "AWAITING_RECEIPT"
            )
            continuation = nodes["continuation"]
            observed["dependent_released_after_acceptance"] |= (
                creator["admission"]["accepted"] is True
                and continuation["scheduler"]["state"] in {"ready", "running", "settled"}
            )
            if not worker.is_alive() and result:
                first = _request_json(f"{server.url}api/v1/state")
                second = _request_json(f"{server.url}api/v1/state")
                observed["page_refresh_reconstructed_state"] = (
                    first["snapshot_sha256"] == second["snapshot_sha256"]
                )
            if not worker.is_alive() and all(observed.values()):
                break
            time.sleep(0.05)
        if args.serve_after_seconds > 0:
            time.sleep(args.serve_after_seconds)
    finally:
        worker.join(timeout=5.0)
        server.shutdown()
        server_thread.join(timeout=5.0)
    if failure:
        raise failure[0]
    receipt = {
        "schema": "tau.dag_viewer_live_smoke_receipt.v1",
        "status": (
            "PASS"
            if result.get("status") == "PASS" and all(observed.values())
            else "BLOCKED"
        ),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checks": observed,
        "snapshot_count": len(snapshots),
        "run_dir": str(run_dir),
        "run_id": result.get("scheduler_run_id"),
        "proof_scope": {
            "proves": [
                "A real local creator-reviewer transaction was observed through Tau snapshots.",
                "The dependent node was released only after committed Tau admission.",
            ],
            "does_not_prove": [
                "Provider or model semantic quality.",
                "Production deployment readiness.",
            ],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
