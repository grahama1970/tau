"""Fixture worker used by the visible Herdr DAG proof of concept."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def main() -> int:
    """Run one bounded fixture DAG node and write a Tau receipt."""

    parser = argparse.ArgumentParser(description="Run one visible DAG fixture worker.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--work-order", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--depends-on", action="append", default=[])
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--hold-seconds", type=float, default=0.0)
    args = parser.parse_args()

    work_order_path = Path(args.work_order).expanduser().resolve()
    receipt_path = Path(args.receipt).expanduser().resolve()
    events_path = Path(args.events).expanduser().resolve()
    dependency_paths = tuple(Path(raw).expanduser().resolve() for raw in args.depends_on)

    _append_event(
        events_path,
        {
            "schema": "tau.visible_dag_event.v1",
            "kind": "node_started",
            "run_id": args.run_id,
            "node_id": args.node_id,
            "role": args.role,
            "work_order_path": str(work_order_path),
            "receipt_path": str(receipt_path),
            "timestamp": _utc_stamp(),
        },
    )
    time.sleep(args.sleep_seconds)

    work_order = _read_json_object(work_order_path, label="work order")
    dependency_receipts = [
        _read_json_object(path, label=f"dependency receipt {path}") for path in dependency_paths
    ]
    dependency_errors = [
        f"dependency {item.get('node_id')} did not report ok=true"
        for item in dependency_receipts
        if item.get("ok") is not True
    ]
    ok = not dependency_errors
    receipt: dict[str, Any] = {
        "schema": "tau.visible_dag_node_receipt.v1",
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": True,
        "live": True,
        "proof_scope": {
            "proves": [
                "Herdr-launched pane process executed this fixture node",
                "work order path was readable",
                "node receipt was written at the expected path",
            ],
            "does_not_prove": [
                "semantic LLM agent quality",
                "provider integration quality",
                "ticket closure readiness",
            ],
        },
        "run_id": args.run_id,
        "node_id": args.node_id,
        "role": args.role,
        "work_order_path": str(work_order_path),
        "receipt_path": str(receipt_path),
        "dependency_receipts": [str(path) for path in dependency_paths],
        "dependency_errors": dependency_errors,
        "work_order_summary": work_order.get("summary"),
        "timestamp": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    _append_event(
        events_path,
        {
            "schema": "tau.visible_dag_event.v1",
            "kind": "node_receipt_written",
            "run_id": args.run_id,
            "node_id": args.node_id,
            "role": args.role,
            "ok": ok,
            "receipt_path": str(receipt_path),
            "timestamp": _utc_stamp(),
        },
    )
    if args.hold_seconds > 0:
        time.sleep(args.hold_seconds)
    return 0 if ok else 2


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


def _append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
