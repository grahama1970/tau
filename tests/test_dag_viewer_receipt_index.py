"""Receipt allowlist and tamper checks for the DAG viewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.dag_runtime.transition import DagCommittedReceipt
from tau_coding.dag_viewer.receipt_index import build_receipt_index


def _ref(path: Path) -> DagCommittedReceipt:
    import hashlib

    return DagCommittedReceipt(
        str(path), f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    )


def test_receipt_index_hashes_and_redacts_only_receipts(tmp_path: Path) -> None:
    receipt = tmp_path / "route-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "tau.dag_route_decision_receipt.v1",
                "status": "PASS",
                "authorization": "Bearer secret",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "source-dag.json").write_text(json.dumps({"schema": "tau.dag_contract.v1"}))
    index = build_receipt_index(tmp_path, (_ref(receipt),))
    assert len(index.entries) == 1
    projection = index.read_projection(index.entries[0].receipt_id)
    assert projection["receipt"]["authorization"] == "[REDACTED]"
    assert "Bearer secret" not in json.dumps(projection)


def test_receipt_index_blocks_hash_change_and_symlink_escape(tmp_path: Path) -> None:
    receipt = tmp_path / "node-receipt.json"
    receipt.write_text(json.dumps({"schema": "tau.node_receipt.v1", "status": "PASS"}))
    index = build_receipt_index(tmp_path, (_ref(receipt),))
    receipt.write_text(json.dumps({"schema": "tau.node_receipt.v1", "status": "BLOCKED"}))
    with pytest.raises(RuntimeError, match="dag_viewer_receipt_hash_mismatch"):
        index.read_projection(index.entries[0].receipt_id)

    external = tmp_path.parent / "external-receipt.json"
    external.write_text(json.dumps({"schema": "tau.external_receipt.v1"}))
    escaped = tmp_path / "escaped-receipt.json"
    escaped.symlink_to(external)
    with pytest.raises(RuntimeError, match="dag_viewer_receipt_symlink_escape"):
        build_receipt_index(tmp_path, (_ref(escaped),))


def test_receipt_fetch_remains_size_bounded_after_startup(tmp_path: Path) -> None:
    receipt = tmp_path / "node-receipt.json"
    receipt.write_text(json.dumps({"schema": "tau.node_receipt.v1", "status": "PASS"}))
    index = build_receipt_index(tmp_path, (_ref(receipt),))
    receipt.write_bytes(b"x" * (5 * 1024 * 1024 + 1))

    with pytest.raises(RuntimeError, match="dag_viewer_receipt_too_large"):
        index.read_projection(index.entries[0].receipt_id)


def test_receipt_index_is_run_scoped_and_allows_identical_content(tmp_path: Path) -> None:
    first = tmp_path / "first-receipt.json"
    second = tmp_path / "second-receipt.json"
    unrelated = tmp_path / "unrelated-receipt.json"
    payload = json.dumps({"schema": "tau.node_receipt.v1", "status": "PASS"})
    for path in (first, second, unrelated):
        path.write_text(payload, encoding="utf-8")

    index = build_receipt_index(tmp_path, (_ref(first), _ref(second)))

    assert {entry.path_display for entry in index.entries} == {
        "first-receipt.json",
        "second-receipt.json",
    }
    assert len({entry.receipt_id for entry in index.entries}) == 2


@pytest.mark.parametrize(
    "schema",
    [
        "tau.dag_route_decision.v1",
        "tau.dag_join_decision.v1",
        "tau.dag_terminal_contribution.v1",
    ],
)
def test_receipt_index_accepts_canonical_transition_artifacts(
    tmp_path: Path, schema: str
) -> None:
    artifact = tmp_path / f"{schema}.json"
    artifact.write_text(json.dumps({"schema": schema, "status": "PASS"}), encoding="utf-8")

    index = build_receipt_index(tmp_path, (_ref(artifact),))

    assert index.entries[0].schema == schema
