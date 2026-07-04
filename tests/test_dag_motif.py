import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_motif import (
    DAG_MOTIF_VALIDATION_RECEIPT_SCHEMA,
    write_dag_motif_validation_receipt,
)


def test_dag_motif_validate_accepts_independent_dissent_reviewers(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path, _contract())
    motif_path = _write_motif(tmp_path, _motif())

    receipt = write_dag_motif_validation_receipt(
        dag_contract_path=contract_path,
        motif_path=motif_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["schema"] == DAG_MOTIF_VALIDATION_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["applied"] is False
    assert receipt["mutated_source_dag"] is False
    assert receipt["memory_sync"] is False
    assert receipt["provider_calls"] is False
    assert receipt["alerts"] == []


@pytest.mark.parametrize(
    ("contract_mutation", "motif_mutation", "expected_code"),
    [
        (None, lambda m: m.update({"reviewer_nodes": ["reviewer-a"]}), "insufficient_dissent_reviewers"),
        (
            lambda c: c["edges"].append({"from": "reviewer-a", "to": "reviewer-b"}),
            None,
            "reviewers_not_independent",
        ),
        (
            lambda c: c.update(
                {
                    "edges": [
                        edge
                        for edge in c["edges"]
                        if edge != {"from": "reviewer-b", "to": "join-review"}
                    ]
                }
            ),
            None,
            "missing_reviewer_to_join_edge",
        ),
        (None, lambda m: m.update({"goal_hash": "sha256:changed"}), "goal_hash_mismatch"),
    ],
)
def test_dag_motif_validate_blocks_invalid_topologies(
    tmp_path: Path,
    contract_mutation: object,
    motif_mutation: object,
    expected_code: str,
) -> None:
    contract = _contract()
    motif = _motif()
    if contract_mutation is not None:
        contract_mutation(contract)
    if motif_mutation is not None:
        motif_mutation(motif)
    contract_path = _write_contract(tmp_path, contract)
    motif_path = _write_motif(tmp_path, motif)

    receipt = write_dag_motif_validation_receipt(
        dag_contract_path=contract_path,
        motif_path=motif_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert any(alert["code"] == expected_code for alert in receipt["alerts"])


def test_cli_dag_motif_validate_writes_receipt(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path, _contract())
    motif_path = _write_motif(tmp_path, _motif())
    receipt_path = tmp_path / "receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-motif-validate",
            "--dag-contract",
            str(contract_path),
            "--motif",
            str(motif_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert receipt_path.exists()
    assert payload["schema"] == DAG_MOTIF_VALIDATION_RECEIPT_SCHEMA
    assert payload["ok"] is True


def _write_contract(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "dag-contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_motif(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "dag-motif.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _contract() -> dict[str, object]:
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "dissent-test",
        "goal": {
            "goal_id": "dissent-test",
            "goal_version": 1,
            "goal_hash": "sha256:dissent-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-dissent",
        },
        "entry_node": "creator",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 5,
        },
        "nodes": [
            {
                "id": "creator",
                "agent": "creator",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer-a",
                "agent": "reviewer-a",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["reviewer_verdict"],
            },
            {
                "id": "reviewer-b",
                "agent": "reviewer-b",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["reviewer_verdict"],
            },
            {
                "id": "join-review",
                "agent": "reviewer-join",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["dissent_reconciliation"],
            },
        ],
        "edges": [
            {"from": "creator", "to": "reviewer-a"},
            {"from": "creator", "to": "reviewer-b"},
            {"from": "reviewer-a", "to": "join-review"},
            {"from": "reviewer-b", "to": "join-review"},
            {"from": "join-review", "to": "human"},
        ],
        "required_evidence": ["creator_artifact", "reviewer_verdict", "dissent_reconciliation"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
        ],
    }


def _motif() -> dict[str, object]:
    return {
        "schema": "tau.dag_motif.v1",
        "motif_id": "independent-dissent-test",
        "kind": "independent_dissent_reviewer_v1",
        "dag_id": "dissent-test",
        "goal_hash": "sha256:dissent-goal",
        "producer_node": "creator",
        "reviewer_nodes": ["reviewer-a", "reviewer-b"],
        "join_node": "join-review",
    }
