import json
import sys
from pathlib import Path

import pytest

from tau_coding.project_dag import (
    _project_dag_resume_watchdog_journal,
    resume_project_dag_command_spec_nodes,
    run_project_dag_contract,
    validate_dag_contract,
)


def test_command_spec_resume_preserves_creator_and_reruns_reviewer_only(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(
        tmp_path,
        "reviewer",
        {"status": "BLOCKED", "verdict": "SCILLM_PROVIDER_ROUTE_FAILED", "errors": ["HTTP 429"]},
    )

    first = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        command_spec_root=tmp_path / "specs",
        scheduler="bounded-ready-queue",
    )

    assert first["status"] == "BLOCKED"
    assert (tmp_path / "coder-count.txt").read_text() == "1"
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff())

    resumed = resume_project_dag_command_spec_nodes(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        command_spec_root=tmp_path / "specs",
        preserve_nodes=("coder",),
        rerun_nodes=("reviewer",),
        execute=True,
    )

    assert resumed["status"] == "PASS"
    assert resumed["preserved_nodes"] == ["coder"]
    assert resumed["rerun_nodes"] == ["reviewer"]
    assert resumed["safe_admission"]["run_store_archived_before_mutation"] is True
    assert Path(resumed["archive_path"]).is_file()
    assert Path(resumed["resume_contract_path"]).is_file()
    assert (tmp_path / "coder-count.txt").read_text() == "1"
    assert (tmp_path / "reviewer-count.txt").read_text() == "2"


def test_command_spec_resume_accepts_watchdog_running_journal_with_active_lease(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    contract = validate_dag_contract(json.loads(contract_path.read_text(encoding="utf-8")))
    receipt_dir = tmp_path / "ask" / "ask-tau-repair-fixture" / "tau-receipts"
    receipt_dir.mkdir(parents=True)
    journal = tmp_path / "operation.json"
    journal.write_text(
        json.dumps(
            {
                "schema": "agent_skills.project_watchdog.primary_operation.v2",
                "phase": "running",
                "run_id": "project-watchdog-fixture",
                "ask_run_dir": str(receipt_dir.parent.parent),
                "tau_settled": True,
                "lease_released": False,
                "owner_token": "fixture",
                "lease_agent": "project-watchdog-fixture",
                "lease_actor": "fixture",
                "lease_before_event": {"id": 6, "event": "unlabeled"},
                "lease_event": {
                    "id": 7,
                    "event": "labeled",
                    "actor": "fixture",
                    "created_at": "2026-09-09T00:00:00Z",
                },
                "issue_number": 1628,
            }
        ),
        encoding="utf-8",
    )

    observed = _project_dag_resume_watchdog_journal(
        journal, contract=contract, receipt_dir=receipt_dir
    )

    assert observed is not None
    assert observed["phase"] == "running"
    assert observed["lease_released"] is False


def test_command_spec_resume_rejects_watchdog_running_journal_without_active_lease(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    contract = validate_dag_contract(json.loads(contract_path.read_text(encoding="utf-8")))
    receipt_dir = tmp_path / "ask" / "ask-tau-repair-fixture" / "tau-receipts"
    receipt_dir.mkdir(parents=True)
    journal = tmp_path / "operation.json"
    journal.write_text(
        json.dumps(
            {
                "schema": "agent_skills.project_watchdog.primary_operation.v2",
                "phase": "running",
                "run_id": "project-watchdog-fixture",
                "ask_run_dir": str(receipt_dir.parent.parent),
                "tau_settled": True,
                "lease_released": True,
                "issue_number": 1628,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="confirmed active lease"):
        _project_dag_resume_watchdog_journal(journal, contract=contract, receipt_dir=receipt_dir)


def _write_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    spec_root = tmp_path / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "ask-tau-repair-test",
        "goal": {
            "goal_id": "ask-tau-repair-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {"repo": "grahama1970/tau", "target": "same-run-resume"},
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 3},
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "coder" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {"reviews_node": "coder", "requires_goal_hash": True},
            },
        ],
        "edges": [{"from": "coder", "to": "reviewer"}, {"from": "reviewer", "to": "human"}],
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
            "malformed_handoff",
        ],
    }
    path = tmp_path / "dag.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_response_spec(tmp_path: Path, agent: str, response: dict[str, object]) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path = tmp_path / f"{agent}-count.txt"
    code = "\n".join(
        [
            "import json",
            "from pathlib import Path",
            f"counter = Path({str(counter_path)!r})",
            "count = int(counter.read_text()) if counter.exists() else 0",
            "counter.write_text(str(count + 1))",
            f"print(json.dumps({json.dumps(response)}))",
        ]
    )
    spec_path.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5, "cwd": str(tmp_path)}),
        encoding="utf-8",
    )


def _handoff(agent: str, next_agent: str, evidence: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "same-run-resume"},
        "goal": {
            "goal_id": "ask-tau-repair-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": agent,
        "context": {"summary": f"{agent} node response.", "artifacts": []},
        "result": {"status": "PASS", "summary": f"{agent} completed.", "evidence": evidence},
        "rationale": "The DAG contract controls the next route.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Continue along the DAG route.",
        },
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human.",
    }


def _creator_evidence() -> list[dict[str, object]]:
    return [
        {
            "kind": "creator_artifact",
            "path": "artifact.txt",
            "sha256": "sha256:creator",
            "summary": "created",
            "goal_hash": "sha256:active-goal",
        }
    ]


def _reviewer_handoff() -> dict[str, object]:
    return _handoff(
        "reviewer",
        "human",
        [
            {
                "kind": "reviewer_verdict",
                "schema": "tau.reviewer_verdict.v1",
                "reviewer_node_id": "reviewer",
                "reviewed_node_id": "coder",
                "verdict": "PASS",
                "summary": "reviewed",
                "goal_hash": "sha256:active-goal",
            }
        ],
    )
