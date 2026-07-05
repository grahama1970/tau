import json
import sys
from pathlib import Path

from tau_coding.visible_dag_poc import inspect_visible_dag_run
from tau_coding.visible_dag_worker import main as worker_main


def test_visible_dag_worker_writes_fixture_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    work_order = tmp_path / "work-order.json"
    receipt = tmp_path / "receipt.json"
    events = tmp_path / "events.jsonl"
    work_order.write_text(
        json.dumps(
            {
                "schema": "tau.visible_dag_work_order.v1",
                "summary": "fixture worker test",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "visible_dag_worker",
            "--run-id",
            "run-1",
            "--node-id",
            "creator",
            "--role",
            "creator",
            "--work-order",
            str(work_order),
            "--receipt",
            str(receipt),
            "--events",
            str(events),
            "--sleep-seconds",
            "0",
        ],
    )

    assert worker_main() == 0

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    event_lines = events.read_text(encoding="utf-8").splitlines()
    assert payload["schema"] == "tau.visible_dag_node_receipt.v1"
    assert payload["ok"] is True
    assert payload["mocked"] is True
    assert payload["live"] is True
    assert payload["work_order_summary"] == "fixture worker test"
    assert len(event_lines) == 2


def test_inspect_visible_dag_run_summarizes_artifacts(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    creator_receipt = tmp_path / "receipts" / "creator.receipt.json"
    reviewer_receipt = tmp_path / "receipts" / "reviewer.receipt.json"
    creator_receipt.parent.mkdir()
    events.write_text(
        json.dumps({"schema": "tau.visible_dag_event.v1", "kind": "dag_created"}) + "\n",
        encoding="utf-8",
    )
    creator_receipt.write_text(
        json.dumps({"schema": "tau.visible_dag_node_receipt.v1", "ok": True, "status": "PASS"}),
        encoding="utf-8",
    )
    reviewer_receipt.write_text(
        json.dumps({"schema": "tau.visible_dag_node_receipt.v1", "ok": True, "status": "PASS"}),
        encoding="utf-8",
    )
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.visible_dag_runtime_manifest.v1",
                "run_id": "run-1",
                "events_jsonl": str(events),
                "workstation_manifest": "/tmp/workstation.json",
                "inspect_path": "/tmp/inspect.json",
                "nodes": [
                    {"node_id": "creator", "role": "creator", "receipt_path": str(creator_receipt)},
                    {
                        "node_id": "reviewer",
                        "role": "reviewer",
                        "receipt_path": str(reviewer_receipt),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "run-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.visible_dag_run_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": True,
                "live": True,
                "proof_scope": {"proves": ["fixture"], "does_not_prove": ["semantic quality"]},
            }
        ),
        encoding="utf-8",
    )

    summary = inspect_visible_dag_run(tmp_path)

    assert summary["schema"] == "tau.visible_dag_inspect.v1"
    assert summary["ok"] is True
    assert summary["run_id"] == "run-1"
    assert summary["events_count"] == 1
    assert summary["nodes"] == [
        {
            "node_id": "creator",
            "role": "creator",
            "status": "PASS",
            "ok": True,
            "receipt_path": str(creator_receipt),
        },
        {
            "node_id": "reviewer",
            "role": "reviewer",
            "status": "PASS",
            "ok": True,
            "receipt_path": str(reviewer_receipt),
        },
    ]
