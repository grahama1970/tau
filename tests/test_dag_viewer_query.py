"""Bounded DAG viewer query tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.dag_viewer.http import parse_view_query
from tau_coding.dag_viewer.query import DagViewQuery, query_dag_view
from tau_coding.dag_viewer.receipt_index import IndexedReceipt, ReceiptIndex

CURSOR_KEY = b"test-cursor-key"


def _query(
    *,
    tmp_path: Path,
    query: DagViewQuery,
    run_id: str = "run-1",
    view_sequence: int = 9,
) -> dict[str, object]:
    snapshot = _snapshot()
    return query_dag_view(
        run_id=run_id,
        view_sequence=view_sequence,
        snapshot=snapshot,
        events=tuple(snapshot["recent_events"]),  # type: ignore[arg-type]
        receipts=ReceiptIndex(tmp_path, ()),
        query=query,
        cursor_key=CURSOR_KEY,
    )


def _snapshot() -> dict[str, object]:
    return {
        "journal_sequence": 9,
        "nodes": [
            {
                "node_id": "worker",
                "updated_sequence": 8,
                "scheduler": {"state": "retry_pending", "attempt": 2},
                "admission": {"state": "rejected"},
                "runtime": {"state": "ALIVE"},
            }
        ],
        "edges": [{"edge_id": "edge-1", "state": "success", "last_change_sequence": 7}],
        "terminals": [],
        "routes": [],
        "joins": [],
        "corrections": [
            {
                "incident_id": "incident-1",
                "state": "VERIFIED",
                "journal_sequence": 9,
                "incident": {"node_id": "worker", "token": "must-not-index"},
            }
        ],
        "attention_items": [
            {
                "attention_id": "attention-1",
                "state": "OPEN",
                "severity": "BLOCKER",
                "reason_code": "REVIEW_BLOCKED_RUN",
                "required_action_code": "REVIEW_BLOCKED_RUN",
                "opened_sequence": 6,
                "subject": {"kind": "NODE", "id": "worker"},
            }
        ],
        "recent_events": [
            {
                "seq": 5,
                "event_type": "attempt_dispatched",
                "entity_type": "node",
                "entity_id": "worker",
                "payload": {"attempt": 2, "password": "must-not-index"},
            }
        ],
    }


def test_query_filters_closed_fields_and_paginates_without_duplicates(tmp_path: Path) -> None:
    first_query = DagViewQuery(node_id="worker", limit=2)
    first = _query(tmp_path=tmp_path, query=first_query)
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = _query(
        tmp_path=tmp_path,
        query=DagViewQuery(node_id="worker", limit=2, cursor=first["next_cursor"]),
    )
    first_ids = {item["entity_id"] for item in first["items"]}
    second_ids = {item["entity_id"] for item in second["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert first["items"][0]["sequence"] >= first["items"][1]["sequence"]


@pytest.mark.parametrize(
    ("query", "expected_kind"),
    [
        ("entity_kind=NODE", "NODE"),
        ("entity_id=incident-1", "CORRECTION"),
        ("attempt=2", "NODE"),
        ("event_type=attempt_dispatched", "EVENT"),
        ("state=OPEN", "ATTENTION"),
        ("attention_state=OPEN", "ATTENTION"),
        ("attention_severity=BLOCKER", "ATTENTION"),
        ("sequence_from=9", "CORRECTION"),
        ("sequence_to=5", "EVENT"),
        ("q=REVIEW_BLOCKED_RUN", "ATTENTION"),
    ],
)
def test_query_supports_each_closed_filter(
    tmp_path: Path, query: str, expected_kind: str
) -> None:
    parsed = parse_view_query(query)
    result = _query(tmp_path=tmp_path, query=parsed)
    assert result["items"]
    assert result["items"][0]["entity_kind"] == expected_kind


def test_query_never_searches_raw_payload_or_accepts_unbounded_input(tmp_path: Path) -> None:
    for secret in ("must-not-index", "password"):
        result = _query(tmp_path=tmp_path, query=DagViewQuery(q=secret))
        assert result["items"] == []
    with pytest.raises(RuntimeError, match="dag_viewer_query_invalid"):
        parse_view_query(f"q={'x' * 201}")
    with pytest.raises(RuntimeError, match="dag_viewer_query_invalid"):
        parse_view_query("unknown=value")


def test_query_cursor_is_bound_to_run_sequence_and_normalized_query(tmp_path: Path) -> None:
    first = _query(tmp_path=tmp_path, query=DagViewQuery(limit=1))
    cursor = first["next_cursor"]
    assert cursor
    for run_id, sequence, query in (
        ("run-2", 9, DagViewQuery(limit=1, cursor=cursor)),
        ("run-1", 8, DagViewQuery(limit=1, cursor=cursor)),
        ("run-1", 9, DagViewQuery(limit=1, state="OPEN", cursor=cursor)),
    ):
        with pytest.raises(RuntimeError, match="dag_viewer_query_cursor_invalid"):
            _query(
                tmp_path=tmp_path,
                run_id=run_id,
                view_sequence=sequence,
                query=query,
            )


def test_query_cursor_rejects_forged_boundary(tmp_path: Path) -> None:
    first = _query(tmp_path=tmp_path, query=DagViewQuery(limit=1))
    cursor = str(first["next_cursor"])
    encoded, signature = cursor.split(".", 1)
    forged = ("A" if encoded[0] != "A" else "B") + encoded[1:] + "." + signature
    with pytest.raises(RuntimeError, match="dag_viewer_query_cursor_invalid"):
        _query(tmp_path=tmp_path, query=DagViewQuery(limit=1, cursor=forged))


def test_query_uses_full_prefix_not_recent_timeline(tmp_path: Path) -> None:
    snapshot = _snapshot()
    events = tuple(
        {
            "seq": sequence,
            "event_type": "older_match" if sequence == 1 else "noise",
            "entity_type": "run",
            "entity_id": "run-1",
            "payload": {},
        }
        for sequence in range(1, 251)
    )
    result = query_dag_view(
        run_id="run-1",
        view_sequence=250,
        snapshot=snapshot,
        events=events,
        receipts=ReceiptIndex(tmp_path, ()),
        query=DagViewQuery(event_type="older_match"),
        cursor_key=CURSOR_KEY,
    )
    assert result["total_match_count"] == 1
    assert result["items"][0]["sequence"] == 1


def test_query_receipt_uses_actual_commit_sequence(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}", encoding="utf-8")
    digest = "sha256:" + "a" * 64
    receipt = IndexedReceipt(
        receipt_id="receipt-1",
        schema="tau.test_receipt.v1",
        path=receipt_path,
        path_display="receipt.json",
        sha256=digest,
    )
    events = (
        {
            "seq": 4,
            "event_type": "scheduler_transition_committed",
            "entity_type": "run",
            "entity_id": "run-1",
            "payload": {
                "transition": {
                    "receipt_refs": [
                        {"path": str(receipt_path.resolve()), "file_sha256": digest}
                    ]
                }
            },
        },
        {
            "seq": 9,
            "event_type": "run_completed",
            "entity_type": "run",
            "entity_id": "run-1",
            "payload": {},
        },
    )
    result = query_dag_view(
        run_id="run-1",
        view_sequence=9,
        snapshot=_snapshot(),
        events=events,
        receipts=ReceiptIndex(tmp_path, (receipt,)),
        query=DagViewQuery(entity_kind="RECEIPT"),
        cursor_key=CURSOR_KEY,
    )
    assert result["items"][0]["sequence"] == 4
