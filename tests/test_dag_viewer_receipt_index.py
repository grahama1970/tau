"""Receipt allowlist and tamper checks for the DAG viewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.dag_viewer.receipt_index import build_receipt_index


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
    index = build_receipt_index(tmp_path)
    assert len(index.entries) == 1
    projection = index.read_projection(index.entries[0].receipt_id)
    assert projection["receipt"]["authorization"] == "[REDACTED]"
    assert "Bearer secret" not in json.dumps(projection)


def test_receipt_index_blocks_hash_change_and_symlink_escape(tmp_path: Path) -> None:
    receipt = tmp_path / "node-receipt.json"
    receipt.write_text(json.dumps({"schema": "tau.node_receipt.v1", "status": "PASS"}))
    index = build_receipt_index(tmp_path)
    receipt.write_text(json.dumps({"schema": "tau.node_receipt.v1", "status": "BLOCKED"}))
    with pytest.raises(RuntimeError, match="dag_viewer_receipt_hash_mismatch"):
        index.read_projection(index.entries[0].receipt_id)

    external = tmp_path.parent / "external-receipt.json"
    external.write_text(json.dumps({"schema": "tau.external_receipt.v1"}))
    escaped = tmp_path / "escaped-receipt.json"
    escaped.symlink_to(external)
    with pytest.raises(RuntimeError, match="dag_viewer_receipt_symlink_escape"):
        build_receipt_index(tmp_path)
