import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_branch_locks import (
    DAG_BRANCH_LOCK_VALIDATION_RECEIPT_SCHEMA,
    write_dag_branch_lock_validation_receipt,
)


def test_branch_locks_validate_accepts_provider_and_mutating_locks(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    locks_path = _write_locks(tmp_path, _locks())

    receipt = write_dag_branch_lock_validation_receipt(
        dag_contract_path=contract_path,
        locks_path=locks_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["schema"] == DAG_BRANCH_LOCK_VALIDATION_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["required_lock_count"] == 2
    assert receipt["provider_calls"] is False
    assert receipt["route_mutation"] is False


def test_branch_locks_validate_blocks_missing_provider_lock(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    locks = _locks()
    locks["locks"] = [lock for lock in locks["locks"] if lock["branch_type"] != "provider"]
    locks_path = _write_locks(tmp_path, locks)

    receipt = write_dag_branch_lock_validation_receipt(
        dag_contract_path=contract_path,
        locks_path=locks_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert any(alert["code"] == "missing_branch_lock" for alert in receipt["alerts"])


def test_branch_locks_validate_blocks_goal_hash_mismatch(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    locks = _locks()
    locks["goal_hash"] = "sha256:changed"
    locks_path = _write_locks(tmp_path, locks)

    receipt = write_dag_branch_lock_validation_receipt(
        dag_contract_path=contract_path,
        locks_path=locks_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "goal_hash_mismatch"


def test_cli_branch_locks_validate_writes_receipt(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    locks_path = _write_locks(tmp_path, _locks())
    receipt_path = tmp_path / "receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-branch-locks-validate",
            "--dag-contract",
            str(contract_path),
            "--locks",
            str(locks_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_BRANCH_LOCK_VALIDATION_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert receipt_path.exists()


def _write_contract(tmp_path: Path) -> Path:
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "branch-lock-test",
        "goal": {
            "goal_id": "branch-lock-test",
            "goal_version": 1,
            "goal_hash": "sha256:branch-lock-test",
        },
        "target": {"repo": "grahama1970/tau", "target": "branch-locks"},
        "entry_node": "provider-node",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 3},
        "nodes": [
            {
                "id": "provider-node",
                "agent": "provider-agent",
                "executor": "provider",
                "max_attempts": 1,
                "required_evidence": ["provider_receipt"],
                "provider": {"name": "local-provider"},
            },
            {
                "id": "mutating-node",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["mutation_receipt"],
                "mutates": True,
            },
        ],
        "edges": [
            {"from": "provider-node", "to": "mutating-node"},
            {"from": "mutating-node", "to": "human"},
        ],
        "required_evidence": ["provider_receipt", "mutation_receipt"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "missing_required_evidence",
            "max_attempts_exceeded",
        ],
    }
    path = tmp_path / "dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_locks(tmp_path: Path, locks: dict[str, object]) -> Path:
    path = tmp_path / "locks.json"
    path.write_text(json.dumps(locks), encoding="utf-8")
    return path


def _locks() -> dict[str, object]:
    return {
        "schema": "tau.dag_branch_locks.v1",
        "dag_id": "branch-lock-test",
        "goal_hash": "sha256:branch-lock-test",
        "locks": [
            {
                "node_id": "provider-node",
                "branch_type": "provider",
                "lock_id": "lock-provider-001",
                "owner": "goal-guardian",
            },
            {
                "node_id": "mutating-node",
                "branch_type": "mutating",
                "lock_id": "lock-mutating-001",
                "owner": "goal-guardian",
            },
        ],
    }
