import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_expansion import (
    DAG_EXPANSION_VALIDATION_RECEIPT_SCHEMA,
    write_dag_expansion_validation_receipt,
)


def test_dag_expansion_validate_accepts_reviewer_validator_insert(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    proposal_path = _write_proposal(tmp_path, _valid_proposal())
    receipt_path = tmp_path / "receipt.json"
    preview_path = tmp_path / "expanded-dag.preview.json"

    receipt = write_dag_expansion_validation_receipt(
        dag_contract_path=contract_path,
        proposal_path=proposal_path,
        receipt_path=receipt_path,
        preview_path=preview_path,
    )
    preview = json.loads(preview_path.read_text(encoding="utf-8"))

    assert receipt["schema"] == DAG_EXPANSION_VALIDATION_RECEIPT_SCHEMA
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
    assert receipt["preview_path"] == str(preview_path.resolve())
    assert len(preview["nodes"]) == 3
    assert any(node["id"] == "validator" for node in preview["nodes"])
    assert any(edge == {"from": "coder", "to": "validator"} for edge in preview["edges"])


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda p: p.update({"proposed_by": "creator"}), "unauthorized_expansion_author"),
        (lambda p: p.update({"goal_hash": "sha256:changed"}), "goal_hash_mismatch"),
        (
            lambda p: p.update({"target": {"repo": "grahama1970/tau", "target": "changed"}}),
            "target_change_not_allowed",
        ),
        (lambda p: p.update({"terminal_nodes": ["release"]}), "terminal_node_change_not_allowed"),
        (
            lambda p: p["new_nodes"][0].update({"executor": "provider"}),
            "new_executor_not_allowed",
        ),
        (
            lambda p: p["new_nodes"][0].update({"command_spec": "new/spec.json"}),
            "command_spec_change_not_allowed",
        ),
        (
            lambda p: p.update(
                {
                    "new_nodes": [
                        _new_node("validator-a", "validator"),
                        _new_node("validator-b", "goal-guardian"),
                        _new_node("validator-c", "research-auditor"),
                    ]
                }
            ),
            "max_new_nodes_exceeded",
        ),
        (
            lambda p: p.update(
                {
                    "new_edges": [
                        {"from": "coder", "to": "validator"},
                        {"from": "validator", "to": "reviewer"},
                        {"from": "coder", "to": "reviewer"},
                        {"from": "reviewer", "to": "coder"},
                        {"from": "validator", "to": "coder"},
                    ]
                }
            ),
            "max_new_edges_exceeded",
        ),
        (
            lambda p: p["new_nodes"][0].update({"agent": "coder"}),
            "disallowed_new_node_agent",
        ),
        (
            lambda p: p.update(
                {
                    "new_nodes": [
                        _new_node("validator-a", "validator"),
                        _new_node("validator-b", "goal-guardian"),
                    ],
                    "new_edges": [
                        {"from": "coder", "to": "validator-a"},
                        {"from": "validator-a", "to": "validator-b"},
                        {"from": "validator-b", "to": "reviewer"},
                    ],
                }
            ),
            "max_depth_delta_exceeded",
        ),
    ],
)
def test_dag_expansion_validate_blocks_invalid_proposals(
    tmp_path: Path,
    mutation: object,
    expected_code: str,
) -> None:
    contract_path = _write_contract(tmp_path)
    proposal = _valid_proposal()
    mutation(proposal)
    proposal_path = _write_proposal(tmp_path, proposal)
    receipt_path = tmp_path / "receipt.json"
    preview_path = tmp_path / "expanded-dag.preview.json"

    receipt = write_dag_expansion_validation_receipt(
        dag_contract_path=contract_path,
        proposal_path=proposal_path,
        receipt_path=receipt_path,
        preview_path=preview_path,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert any(alert["code"] == expected_code for alert in receipt["alerts"])
    assert receipt["preview_path"] is None
    assert receipt_path.exists()
    assert not preview_path.exists()


def test_dag_expansion_validate_allows_planner_only_pre_run(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    proposal = _valid_proposal()
    proposal["proposed_by"] = "planner"
    proposal["phase"] = "pre_run"
    proposal_path = _write_proposal(tmp_path, proposal)

    receipt = write_dag_expansion_validation_receipt(
        dag_contract_path=contract_path,
        proposal_path=proposal_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is True


def test_dag_expansion_validate_blocks_planner_after_run_start(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    proposal = _valid_proposal()
    proposal["proposed_by"] = "planner"
    proposal["phase"] = "running"
    proposal_path = _write_proposal(tmp_path, proposal)

    receipt = write_dag_expansion_validation_receipt(
        dag_contract_path=contract_path,
        proposal_path=proposal_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "planner_expansion_not_pre_run"


def test_cli_dag_expansion_validate_writes_receipt_and_preview(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    proposal_path = _write_proposal(tmp_path, _valid_proposal())
    receipt_path = tmp_path / "receipt.json"
    preview_path = tmp_path / "expanded-dag.preview.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-expansion-validate",
            "--dag-contract",
            str(contract_path),
            "--proposal",
            str(proposal_path),
            "--receipt",
            str(receipt_path),
            "--preview",
            str(preview_path),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_EXPANSION_VALIDATION_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert receipt_path.exists()
    assert preview_path.exists()


def test_cli_dag_expansion_validate_invalid_exits_nonzero_but_writes_receipt(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    proposal = _valid_proposal()
    proposal["proposed_by"] = "worker"
    proposal_path = _write_proposal(tmp_path, proposal)
    receipt_path = tmp_path / "receipt.json"
    preview_path = tmp_path / "expanded-dag.preview.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-expansion-validate",
            "--dag-contract",
            str(contract_path),
            "--proposal",
            str(proposal_path),
            "--receipt",
            str(receipt_path),
            "--preview",
            str(preview_path),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["alerts"][0]["code"] == "unauthorized_expansion_author"
    assert receipt_path.exists()
    assert not preview_path.exists()


def _write_contract(tmp_path: Path) -> Path:
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "expansion-test",
        "goal": {
            "goal_id": "expansion-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-expansion",
        },
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 3,
        },
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["reviewer_verdict"],
            },
        ],
        "edges": [
            {"from": "coder", "to": "reviewer"},
            {"from": "reviewer", "to": "human"},
        ],
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
        ],
    }
    path = tmp_path / "dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_proposal(tmp_path: Path, proposal: dict[str, object]) -> Path:
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(proposal), encoding="utf-8")
    return path


def _valid_proposal() -> dict[str, object]:
    return {
        "schema": "tau.dag_expansion_proposal.v1",
        "proposal_id": "proposal-001",
        "parent_dag_id": "expansion-test",
        "goal_hash": "sha256:active-goal",
        "proposed_by": "reviewer",
        "reason": "Add deterministic validation before reviewer continuation.",
        "new_nodes": [_new_node("validator", "validator")],
        "new_edges": [
            {"from": "coder", "to": "validator"},
            {"from": "validator", "to": "reviewer"},
        ],
    }


def _new_node(node_id: str, agent: str) -> dict[str, object]:
    return {
        "id": node_id,
        "agent": agent,
        "executor": "local",
        "max_attempts": 1,
        "required_evidence": ["validation_receipt"],
    }
