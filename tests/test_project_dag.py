import hashlib
import json
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import tau_coding.project_dag as project_dag
from tau_coding.cli import app
from tau_coding.dag_route_decision import ROUTE_DECISION_VALIDATION_CODES
from tau_coding.handoff_dispatch import TAU_COMMAND_SPEC_POLICY_SCHEMA
from tau_coding.project_dag import (
    DAG_ERROR_SCHEMA,
    DAG_PROGRESS_SCHEMA,
    DAG_RECEIPT_SCHEMA,
    FAIL_CLOSED_REGISTRY_SCHEMA,
    fail_closed_registry_payload,
    run_project_dag_contract,
    write_fail_closed_registry_receipt,
)


def test_transition_receipt_classification_accepts_windows_paths() -> None:
    paths = (
        r"C:\\run\\route-decisions\\route.json",
        r"C:\\run\\terminal-contributions\\edge.json",
        r"C:\\run\\join-decisions\\join.json",
    )

    assert project_dag._transition_receipts_in_directory(paths, "route-decisions") == [
        paths[0]
    ]
    assert project_dag._transition_receipts_in_directory(
        paths, "terminal-contributions"
    ) == [paths[1]]
    assert project_dag._transition_receipts_in_directory(paths, "join-decisions") == [
        paths[2]
    ]


def test_provider_live_requires_accepted_live_provider_route_receipt() -> None:
    response = {
        "result": {
            "evidence": [
                {
                    "kind": "provider_route_receipt",
                    "provider_receipt": {
                        "ok": True,
                        "live": True,
                        "provider_live": True,
                    },
                }
            ]
        }
    }

    assert project_dag._accepted_provider_live(response) is True
    response["result"]["evidence"][0]["provider_receipt"]["ok"] = False
    assert project_dag._accepted_provider_live(response) is False


def test_project_dag_runs_creator_reviewer_loop(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["schema"] == DAG_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["selected_agents"] == ["coder", "reviewer"]
    assert receipt["observed_edges"] == [
        {
            "from_agent": "coder",
            "from_node": "coder",
            "to_agent": "reviewer",
            "to_node": "reviewer",
        },
        {
            "from_agent": "reviewer",
            "from_node": "reviewer",
            "to_agent": "human",
            "to_node": "human",
        },
    ]
    assert receipt["reviewer_verdicts"] == [
        {
            "goal_hash": "sha256:active-goal",
            "kind": "reviewer_verdict",
            "reviewed_node_id": "coder",
            "verdict": "PASS",
        }
    ]
    assert Path(str(receipt["command_loop_receipt"])).exists()


def test_ready_queue_blocks_failed_referenced_receipt_verdict(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["nodes"][0]["required_evidence"] = ["handler_response_receipt"]
    contract["required_evidence"] = ["handler_response_receipt", "reviewer_verdict"]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    handler_receipt = tmp_path / "handler-node-receipt.json"
    handler_receipt.write_text(
        json.dumps(
            {
                "schema": "ask.tau_dag_handler_receipt.v1",
                "node_id": "coder",
                "status": "PASS",
                "verdict": "FAIL",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _write_response_spec(
        tmp_path,
        "coder",
        _handoff(
            "coder",
            "reviewer",
            [
                {
                    "kind": "handler_response_receipt",
                    "node_id": "coder",
                    "path": str(handler_receipt),
                    "status": "PASS",
                }
            ],
        ),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "FAIL"
    assert receipt["alerts"][0]["code"] == "evidence_receipt_verdict_failed"
    assert receipt["alerts"][0]["evidence"]["receipt_verdict"] == "FAIL"
    assert receipt["node_attempts"] == {"coder": 1}
    assert "reviewer" not in receipt["node_attempts"]


def test_project_dag_durable_replay_preserves_receipt_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp_counter = iter(range(10_000))
    monkeypatch.setattr(
        project_dag,
        "_utc_stamp",
        lambda: f"2026-07-13T20:00:{next(stamp_counter):04d}Z",
    )
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    run_dir = tmp_path / "run"

    first = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )
    second = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )
    third = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert first["status"] == second["status"] == "PASS"
    assert second["replayed_event_count"] > 0
    assert second["command_executed"] is True
    assert second["selected_agents"] == first["selected_agents"]
    assert second["reviewer_verdicts"] == first["reviewer_verdicts"]
    assert second["node_attempts"] == first["node_attempts"]
    assert second["max_observed_concurrency"] == first["max_observed_concurrency"]
    assert len(third["scheduler_events"]) == len(second["scheduler_events"])
    normalized_events = [
        {
            key: value
            for key, value in event.items()
            if key not in {"durably_replayed", "ts"}
        }
        for event in third["scheduler_events"]
    ]
    assert len({json.dumps(event, sort_keys=True) for event in normalized_events}) == len(
        normalized_events
    )


def test_project_dag_replays_when_derived_receipt_is_truncated(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    run_dir = tmp_path / "run"

    first = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )
    (run_dir / "dag-receipt.json").write_text('{"status":', encoding="utf-8")

    recovered = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert recovered["status"] == "PASS"
    assert recovered["replayed_event_count"] > 0
    assert recovered["max_observed_concurrency"] == first["max_observed_concurrency"]
    assert recovered["max_observed_concurrency"] > 0


def test_project_dag_preserves_semantic_node_block_verdict(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    blocked = _handoff("coder", "reviewer", [])
    blocked["result"] = {
        "status": "BLOCKED",
        "summary": "Provider output is missing.",
        "evidence": ["provider-authorship-receipt.json"],
    }
    _write_response_spec(tmp_path, "coder", blocked, exit_code=1)
    _write_response_spec(
        tmp_path,
        "reviewer",
        _reviewer_handoff(goal_hash="sha256:active-goal"),
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "NODE_BLOCKED"
    loop = json.loads(Path(receipt["command_loop_receipt"]).read_text(encoding="utf-8"))
    assert loop["stop_reason"] == "node_blocked"


def test_project_dag_writes_live_subagent_progress_for_handoff_loop(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    progress_path = tmp_path / "run" / "dag-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert receipt["progress_path"] == str(progress_path)
    assert progress["schema"] == DAG_PROGRESS_SCHEMA
    assert progress["status"] == "PASS"
    assert progress["mocked"] is False
    assert progress["live"] is True
    assert progress["active_subagents"] == []
    assert progress["completed_subagents"] == [
        {"agent": "coder", "node_id": "coder"},
        {"agent": "reviewer", "node_id": "reviewer"},
    ]
    assert [
        (event["event"], event["selected_agent"])
        for event in progress["events"]
        if event["event"] == "step_started"
    ] == [("step_started", "coder"), ("step_started", "reviewer")]


def test_project_dag_allows_repeated_agent_roles_with_node_addressing(
    tmp_path: Path,
) -> None:
    contract_path = _write_repeated_reviewer_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "coder",
        _repeated_reviewer_handoff("coder", "reviewer-a", _creator_evidence()),
    )
    _write_response_spec(
        tmp_path,
        "reviewer-a",
        _repeated_reviewer_handoff(
            "reviewer-a",
            "reviewer-b",
            [
                {
                    "kind": "reviewer_verdict",
                    "reviewed_node_id": "coder",
                    "goal_hash": "sha256:active-goal",
                    "verdict": "PASS",
                }
            ],
        ),
    )
    _write_response_spec(
        tmp_path,
        "reviewer-b",
        _repeated_reviewer_handoff(
            "reviewer-b",
            "human",
            [
                {
                    "kind": "reviewer_verdict",
                    "reviewed_node_id": "coder",
                    "goal_hash": "sha256:active-goal",
                    "verdict": "PASS",
                }
            ],
        ),
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["selected_agents"] == ["coder", "reviewer-a", "reviewer-b"]
    assert receipt["observed_edges"] == [
        {
            "from_agent": "coder",
            "from_node": "coder",
            "to_agent": "reviewer",
            "to_node": "reviewer-a",
        },
        {
            "from_agent": "reviewer",
            "from_node": "reviewer-a",
            "to_agent": "reviewer",
            "to_node": "reviewer-b",
        },
        {
            "from_agent": "reviewer",
            "from_node": "reviewer-b",
            "to_agent": "human",
            "to_node": "human",
        },
    ]


def test_project_dag_blocks_unexpected_edge(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "UNEXPECTED_EDGE"
    assert receipt["alerts"][0]["code"] == "unexpected_edge"
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "unexpected_edge"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["failed_agent"] == "coder"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "reroute",
        "next_agent": "goal-guardian",
        "reason": "Reconcile DAG route, goal, or target drift before continuing.",
    }


def test_project_dag_blocks_reviewer_goal_hash_mismatch(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:stale-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "REVIEWER_GOAL_HASH_MISMATCH"
    assert receipt["alerts"][0]["code"] == "reviewer_goal_hash_mismatch"


def test_cli_dag_run_dispatches_project_dag_contract(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "cli-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert payload["selected_agents"] == ["coder", "reviewer"]
    assert payload["reviewer_verdicts"][0]["goal_hash"] == "sha256:active-goal"


def test_cli_dag_run_dispatches_yaml_project_dag_contract(tmp_path: Path) -> None:
    contract_path = _write_yaml_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "cli-yaml-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert payload["contract_path"].endswith(".dag.yml")
    assert payload["selected_agents"] == ["coder", "reviewer"]


def test_project_dag_propagates_safe_context_to_command_stdin(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["context"] = {
        "persona_dream_panel": {
            "panel_id": "panel_contract",
            "run_root": "/tmp/persona-dream-active-run",
            "image_path": "/tmp/persona-dream-active-run/artifacts/panel.png",
        },
        "artifacts": ["/tmp/contract-artifact.json"],
    }
    payload["nodes"][0]["context"] = {
        "persona_dream_panel": {
            "panel_id": "panel_node",
            "run_root": "/tmp/persona-dream-node-run",
            "image_path": "/tmp/persona-dream-node-run/artifacts/panel.png",
            "panel_prompt": "Use active Embry/Kai storyboard context.",
        }
    }
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_stdin_capture_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "reviewer", _creator_evidence()),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    request = json.loads(
        (
            tmp_path
            / "run"
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-001"
            / "request.json"
        ).read_text(encoding="utf-8")
    )
    context = request["context"]
    assert receipt["ok"] is True
    assert context["persona_dream_panel"] == {
        "panel_id": "panel_node",
        "run_root": "/tmp/persona-dream-node-run",
        "image_path": "/tmp/persona-dream-node-run/artifacts/panel.png",
        "panel_prompt": "Use active Embry/Kai storyboard context.",
    }
    assert context["artifacts"] == [str(contract_path.resolve()), "/tmp/contract-artifact.json"]
    assert context["summary"] == "Dispatch DAG contract creator-reviewer-test."


def test_project_dag_bounded_ready_queue_runs_independent_nodes_concurrently(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
        sleep_seconds=0.25,
    )
    _write_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
        sleep_seconds=0.25,
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["scheduler"] == "bounded-ready-queue"
    assert receipt["execution"] == "project_agent_dag_plan_ready_queue"
    assert receipt["dag_plan_sha256"].startswith("sha256:")
    assert receipt["max_observed_concurrency"] >= 2
    assert set(receipt["selected_agents"]) == {"research-auditor", "coder", "reviewer"}
    assert receipt["node_attempts"] == {
        "coder": 1,
        "research": 1,
        "reviewer": 1,
    }
    assert receipt["reviewer_verdicts"][0]["goal_hash"] == "sha256:active-goal"
    assert any(
        event["event"] == "virtual_node_completed" and event["node_id"] == "start"
        for event in receipt["scheduler_events"]
    )


def test_project_dag_ready_queue_propagates_node_context_to_command_stdin(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["context"] = {
                "persona_dream_panel": {
                    "panel_id": "panel_ready_queue",
                    "run_root": "/tmp/persona-dream-ready-queue-run",
                    "image_path": "/tmp/persona-dream-ready-queue-run/artifacts/panel.png",
                }
            }
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_stdin_capture_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    request = json.loads(
        (tmp_path / "run" / "ready-queue" / "coder" / "attempt-001" / "request.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["ok"] is True
    assert request["context"]["persona_dream_panel"] == {
        "panel_id": "panel_ready_queue",
        "run_root": "/tmp/persona-dream-ready-queue-run",
        "image_path": "/tmp/persona-dream-ready-queue-run/artifacts/panel.png",
    }


def test_shared_project_scheduler_persists_running_progress(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    for agent, response in (
        ("research-auditor", _handoff("research-auditor", "human", [{"kind": "source_summary"}])),
        ("coder", _handoff("coder", "human", _creator_evidence())),
        ("reviewer", _reviewer_handoff(goal_hash="sha256:active-goal")),
    ):
        _write_response_spec(tmp_path, agent, response, sleep_seconds=0.4)
    run_dir = tmp_path / "run"
    outcome: list[dict[str, object]] = []

    worker = threading.Thread(
        target=lambda: outcome.append(
            run_project_dag_contract(
                contract_path=contract_path,
                receipt_dir=run_dir,
                agents_root=tmp_path / "agents",
                scheduler="bounded-ready-queue",
            )
        )
    )
    worker.start()
    progress_path = run_dir / "dag-progress.json"
    deadline = time.monotonic() + 2
    progress: dict[str, object] = {}
    while time.monotonic() < deadline:
        if progress_path.is_file():
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            if progress.get("active_subagents"):
                break
        time.sleep(0.01)
    worker.join(timeout=3)

    assert progress.get("status") == "RUNNING"
    assert progress.get("active_subagents")
    assert outcome and outcome[0]["status"] == "PASS"


def test_shared_project_scheduler_cancels_running_sibling_on_block(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", []),
        exit_code=1,
    )
    marker = tmp_path / "coder-completed.txt"
    coder_spec = tmp_path / "specs" / "coder" / "tau-dispatch-command.json"
    coder_spec.parent.mkdir(parents=True, exist_ok=True)
    coder_spec.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import time; from pathlib import Path; time.sleep(2); "
                        f"Path({str(marker)!r}).write_text('unexpected')"
                    ),
                ],
                "timeout_s": 5,
                "cwd": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )
    progress = json.loads((tmp_path / "run" / "dag-progress.json").read_text(encoding="utf-8"))

    assert receipt["status"] == "BLOCKED"
    assert marker.exists() is False
    assert any(item["status"] == "BLOCKED" for item in progress["node_progress"])
    assert progress["active_subagents"] == []


def test_project_dag_bounded_ready_queue_recovers_after_timeout_retry(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 2
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
        sleep_seconds=0.25,
    )
    _write_flaky_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
        first_failure="timeout",
        timeout_s=0.05,
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["node_attempts"]["coder"] == 2
    assert receipt["max_observed_concurrency"] >= 2
    first_lease = json.loads(
        (
            tmp_path
            / "run"
            / "ready-queue"
            / "coder"
            / "attempt-001"
            / "runtime"
            / "runtime-endpoint-lease.json"
        ).read_text(encoding="utf-8")
    )
    second_lease = json.loads(
        (
            tmp_path
            / "run"
            / "ready-queue"
            / "coder"
            / "attempt-002"
            / "runtime"
            / "runtime-endpoint-lease.json"
        ).read_text(encoding="utf-8")
    )
    assert first_lease["attempt_number"] == 1
    assert second_lease["attempt_number"] == 2
    assert first_lease["run_id"] == second_lease["run_id"] == payload["dag_id"]
    assert [
        event
        for event in receipt["scheduler_events"]
        if event["event"] == "node_attempt_failed" and event["node_id"] == "coder"
    ][0]["stop_reason"] == "command_timeout"


def test_project_dag_bounded_ready_queue_recovers_after_non_json_retry(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 2
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_flaky_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
        first_failure="non-json",
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["node_attempts"]["coder"] == 2
    assert [
        event
        for event in receipt["scheduler_events"]
        if event["event"] == "node_attempt_failed" and event["node_id"] == "coder"
    ][0]["stop_reason"] == "invalid_command_json"


def test_project_dag_bounded_ready_queue_blocks_after_max_retries(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 2
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_always_non_json_spec(tmp_path, "coder")
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "INVALID_COMMAND_JSON"
    assert receipt["node_attempts"]["coder"] == 2
    assert receipt["alerts"][0]["evidence"]["attempts"] == 2
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "invalid_command_json"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["failed_agent"] == "coder"
    assert receipt["dag_error"]["attempts"] == 2
    assert receipt["dag_error"]["max_attempts"] == 2
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_then_retry_or_reroute",
        "next_agent": "goal-guardian",
        "reason": "Repair the node command or subagent response contract before retrying.",
    }
    retrying = [
        event["retrying"]
        for event in receipt["scheduler_events"]
        if event["event"] == "node_attempt_failed"
    ]
    assert retrying == [
        True,
        False,
    ]


def test_project_dag_ready_queue_blocks_pointless_unit_test_drift(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 3
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_pointless_unit_test_failure_spec(tmp_path, "coder")
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "POINTLESS_UNIT_TEST_DRIFT"
    assert receipt["alerts"][0]["code"] == "pointless_unit_test_drift"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "reroute",
        "next_agent": "reviewer",
        "reason": "Inspect missing or inconsistent evidence before normal continuation.",
    }
    assert len(receipt["course_correction_artifacts"]) == 1
    course_correction = json.loads(
        Path(receipt["course_correction_artifacts"][0]).read_text(encoding="utf-8")
    )
    assert course_correction["code"] == "pointless_unit_test_drift"
    assert course_correction["trigger"] == "pointless_unit_test_drift"
    assert course_correction["required_next_action"] == (
        "stop_test_churn_report_blocker_and_replan"
    )
    assert "run_more_unrelated_tests" in course_correction["forbidden_next_routes"]
    assert course_correction["required_action"]["skill_reference"] == "$brave-search"
    assert course_correction["blocked_report_required"] == {
        "required": True,
        "fields": [
            "blocker_summary",
            "attempted_fix",
            "why_test_churn_is_not_progress",
            "next_non_test_action",
            "brave_search_receipt_path",
        ],
        "reason": (
            "Blocked subagents must report the blocker and course correction "
            "instead of continuing non-essential deterministic unit tests."
        ),
    }

    replayed = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )
    assert replayed["course_correction_artifacts"] == receipt["course_correction_artifacts"]


def test_project_dag_ready_queue_requires_brave_search_after_two_failed_attempts(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 3
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_always_non_json_spec(tmp_path, "coder")
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "BRAVE_SEARCH_REQUIRED_AFTER_TWO_ATTEMPTS"
    assert receipt["node_attempts"]["coder"] == 2
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "run_brave_search_then_retry",
        "next_agent": "goal-guardian",
        "reason": "Require $brave-search research before another attempt.",
    }
    assert len(receipt["course_correction_artifacts"]) == 1
    course_correction = json.loads(
        Path(receipt["course_correction_artifacts"][0]).read_text(encoding="utf-8")
    )
    assert course_correction["schema"] == "tau.course_correction.v1"
    assert course_correction["code"] == "brave_search_required_after_two_attempts"
    assert course_correction["trigger"] == "brave_search_required_after_two_attempts"
    assert course_correction["required_next_action"] == "run_brave_search_then_retry"
    assert "retry_without_research_receipt" in course_correction["forbidden_next_routes"]
    assert course_correction["required_action"]["skill"] == "brave-search"
    assert "brave_search.py" in course_correction["required_action"]["command"][1]
    assert course_correction["blocked_report_required"]["required"] is True
    assert "blocker_summary" in course_correction["blocked_report_required"]["fields"]


def test_project_dag_bounded_ready_queue_blocks_provider_nodes(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_response_spec(tmp_path, "coder", _handoff("coder", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["executor"] = "provider"
            node["provider"] = {"adapter": "generic-provider-dag-node"}
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "NON_LOCAL_READY_QUEUE_NODE_NOT_ALLOWED"
    assert {alert["code"] for alert in receipt["alerts"]} == {
        "non_local_ready_queue_node_not_allowed",
        "provider_node_not_allowed",
    }


def test_project_dag_zero_trust_blocks_missing_data_boundary(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_DATA_BOUNDARY"
    assert receipt["alerts"][0]["code"] == "missing_data_boundary"
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "missing_data_boundary"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_then_retry_or_reroute",
        "next_agent": "goal-guardian",
        "reason": "Repair zero-trust policy/data-boundary gates before DAG dispatch.",
    }
    assert Path(str(receipt["zero_trust_preflight_receipt"])).exists()


def test_project_dag_zero_trust_blocks_invalid_data_boundary(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    boundary_path = _write_data_boundary(tmp_path, {"classification": None})
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["data_boundary"] = str(boundary_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "invalid_data_boundary"
    assert "classification must be one of" in receipt["errors"][0]


def test_project_dag_zero_trust_allows_valid_public_boundary(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["data_boundary"] = _public_data_boundary()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"


def test_project_dag_memory_gate_blocks_missing_intent(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update(
        {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
        }
    )
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_MEMORY_INTENT"
    assert receipt["alerts"][0]["code"] == "missing_memory_intent"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_memory_evidence_gate",
        "next_agent": "goal-guardian",
        "reason": (
            "Repair Graph Memory intent and create-evidence-case artifacts before "
            "zero-trust DAG dispatch."
        ),
    }
    assert Path(str(receipt["memory_intent_gate_receipt"])).exists()
    assert Path(str(receipt["evidence_case_gate_receipt"])).exists()


def test_project_dag_memory_gate_blocks_inline_intent_evidence(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update({"intent_required": True, "min_intent_confidence": 0.75})
    intent = _memory_intent()
    intent["evidence"] = [{"claim": "inline evidence should be rejected"}]
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    payload["memory_intent"] = intent
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "intent_contains_inline_evidence"


def test_project_dag_memory_gate_honors_policy_min_intent_confidence(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update(
        {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
        }
    )
    intent = _memory_intent()
    intent["confidence"] = 0.6
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    payload["memory_intent"] = intent
    payload["evidence_case"] = _memory_evidence_case()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["alerts"][0]["code"] == "memory_intent_low_confidence"
    memory_receipt = json.loads(
        Path(str(receipt["memory_intent_gate_receipt"])).read_text(encoding="utf-8")
    )
    assert memory_receipt["alerts"][0]["evidence"] == {
        "confidence": 0.6,
        "minimum": 0.75,
    }


def test_project_dag_memory_gate_allows_valid_intent_and_evidence_case(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update(
        {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
        }
    )
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    payload["memory_intent"] = _memory_intent()
    payload["evidence_case"] = _memory_evidence_case()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert Path(str(receipt["memory_intent_gate_receipt"])).exists()
    assert Path(str(receipt["evidence_case_gate_receipt"])).exists()


def test_project_dag_zero_trust_blocks_external_provider_when_policy_denies(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["data_boundary"] = _public_data_boundary()
    payload["nodes"][0]["provider"] = {"adapter": "generic-provider-dag-node"}
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "external_provider_denied"


def test_project_dag_blocks_underspecified_provider_sensitive_contract_before_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_provider_sensitive_contract(tmp_path, complete=False)

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    alert_codes = {alert["code"] for alert in receipt["alerts"]}
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "PROVIDER_POLICY_MISSING"
    assert receipt["selected_agents"] == []
    assert not (tmp_path / "run" / "command-loop").exists()
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "provider_policy_missing"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_provider_policy",
        "next_agent": "goal-guardian",
        "reason": (
            "Add explicit model_policy, prompt_contract, provider/auth/model route, "
            "and provider-route evidence before dispatch."
        ),
    }
    assert {
        "provider_policy_missing",
        "model_unspecified",
        "missing_prompt_contract",
        "missing_required_evidence",
    }.issubset(alert_codes)
    assert all(
        alert["evidence"]["node_id"] in {"panel-creator", "panel-reviewer"}
        for alert in receipt["alerts"]
    )


def test_project_dag_allows_provider_sensitive_contract_with_policy_prompt_and_evidence(
    tmp_path: Path,
) -> None:
    contract_path = _write_provider_sensitive_contract(tmp_path, complete=True)
    _write_stdin_capture_response_spec(
        tmp_path,
        "panel-creator",
        _persona_dream_provider_handoff(
            "panel-creator",
            "panel-reviewer",
            [
                {"kind": "storyboard_creator_receipt.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )
    _write_response_spec(
        tmp_path,
        "panel-reviewer",
        _persona_dream_provider_handoff(
            "panel-reviewer",
            "human",
            [
                {"kind": "storyboard_review_verdict.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["selected_agents"] == ["panel-creator", "panel-reviewer"]
    compiled_creator_spec = json.loads(
        (
            tmp_path
            / "run"
            / "compiled-command-specs"
            / "panel-creator"
            / "tau-dispatch-command.json"
        ).read_text(encoding="utf-8")
    )
    request = json.loads(
        (
            tmp_path
            / "run"
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-001"
            / "request.json"
        ).read_text(encoding="utf-8")
    )
    model_policy = {
        "provider": "scillm",
        "auth": "codex-oauth",
        "model": "gpt-image-2",
    }
    prompt_contract = {
        "schema": "tau.prompt_contract.v1",
        "system_prompt": "Stay inside the immutable storyboard goal.",
        "user_template": "Use the provider route evidence before claiming PASS.",
    }
    assert compiled_creator_spec["tau_dag_node"]["model_policy"] == model_policy
    assert compiled_creator_spec["tau_dag_node"]["prompt_contract"] == prompt_contract
    assert compiled_creator_spec["tau_dag_node"]["required_evidence"] == [
        "storyboard_creator_receipt.json",
        "provider_route_receipt",
    ]
    assert request["context"]["model_policy"] == model_policy
    assert request["context"]["prompt_contract"] == prompt_contract
    assert request["context"]["tau_dag_node"]["model_policy"] == model_policy
    assert request["context"]["tau_dag_node"]["prompt_contract"] == prompt_contract


def test_project_dag_propagates_persistent_subagent_surface_to_node(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    embry_surface = _embry_voice_persistent_subagent()
    contract["nodes"][0]["persistent_subagent"] = embry_surface
    contract["nodes"][0]["required_evidence"].append("persistent_subagent_receipt")
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    _write_stdin_capture_response_spec(
        tmp_path,
        "coder",
        _handoff(
            "coder",
            "reviewer",
            [
                {"kind": "creator_artifact"},
                {"kind": "persistent_subagent_receipt"},
            ],
        ),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    compiled_coder_spec = json.loads(
        (
            tmp_path
            / "run"
            / "compiled-command-specs"
            / "coder"
            / "tau-dispatch-command.json"
        ).read_text(encoding="utf-8")
    )
    request = json.loads(
        (
            tmp_path
            / "run"
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-001"
            / "request.json"
        ).read_text(encoding="utf-8")
    )
    assert compiled_coder_spec["tau_dag_node"]["persistent_subagent"] == embry_surface
    assert request["context"]["persistent_subagent"] == embry_surface
    assert request["context"]["tau_dag_node"]["persistent_subagent"] == embry_surface


def test_project_dag_rejects_under_specified_persistent_subagent(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["nodes"][0]["persistent_subagent"] = {
        "schema": "tau.persistent_subagent.v1",
        "surface_id": "embry-voice",
        "surface_url": "https://example.invalid/#embry-voice",
        "session_mode": "ephemeral",
        "tau_control": "autonomous_loop",
        "dag_parameter": "embry_voice_surface",
        "required_receipts": [],
        "unbounded_autonomy_allowed": True,
    }

    with pytest.raises(RuntimeError) as excinfo:
        project_dag.validate_dag_contract(contract)

    error = str(excinfo.value)
    assert "surface_url must use a local UX route" in error
    assert "session_mode must be persistent" in error
    assert "tau_control must be bounded_receipt_gated_ticks" in error
    assert "unbounded_autonomy_allowed must be false" in error
    assert "required_receipts must name at least one receipt schema" in error
    assert "required_evidence must include persistent_subagent_receipt" in error


def test_project_dag_provider_sensitive_command_without_spec_timeout_uses_tau_policy(
    tmp_path: Path,
) -> None:
    contract_path = _write_provider_sensitive_contract(tmp_path, complete=True)
    _write_stdin_capture_response_spec(
        tmp_path,
        "panel-creator",
        _persona_dream_provider_handoff(
            "panel-creator",
            "panel-reviewer",
            [
                {"kind": "storyboard_creator_receipt.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )
    creator_spec_path = tmp_path / "specs" / "panel-creator" / "tau-dispatch-command.json"
    creator_spec = json.loads(creator_spec_path.read_text(encoding="utf-8"))
    creator_spec.pop("timeout_s")
    creator_spec_path.write_text(json.dumps(creator_spec), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "panel-reviewer",
        _persona_dream_provider_handoff(
            "panel-reviewer",
            "human",
            [
                {"kind": "storyboard_review_verdict.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )
    loop_receipt = json.loads(
        Path(str(receipt["command_loop_receipt"])).read_text(encoding="utf-8")
    )
    command_result = loop_receipt["dispatches"][0]["command_results"][0]
    compiled_creator_spec = json.loads(
        (
            tmp_path
            / "run"
            / "compiled-command-specs"
            / "panel-creator"
            / "tau-dispatch-command.json"
        ).read_text(encoding="utf-8")
    )
    request = json.loads(
        (
            tmp_path
            / "run"
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-001"
            / "request.json"
        ).read_text(encoding="utf-8")
    )

    assert receipt["ok"] is True
    assert command_result["timeout_s"] == 900.0
    assert command_result["timeout_s_source"] == "tau_provider_command_timeout_policy"
    assert command_result["timeout_policy"]["schema"] == "tau.provider_command_timeout_policy.v1"
    assert compiled_creator_spec["tau_dag_node"]["timeout_policy"]["timeout_s"] == 900.0
    assert request["context"]["tau_dag_node"]["timeout_policy"]["timeout_s"] == 900.0


def test_project_dag_memory_evidence_gate_allows_valid_artifacts(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(tmp_path)
    evidence_case_path = _write_evidence_case(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert Path(tmp_path / "run" / "memory-intent-gate-receipt.json").exists()
    assert Path(tmp_path / "run" / "evidence-case-gate-receipt.json").exists()


def test_project_dag_memory_evidence_gate_blocks_inline_evidence(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(
        tmp_path,
        overrides={"evidence": [{"statement": "inline evidence must not dispatch"}]},
    )
    evidence_case_path = _write_evidence_case(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "INLINE_MEMORY_EVIDENCE_REJECTED"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "inline_memory_evidence_rejected"
    assert receipt["memory_intent_gate_receipt"] == str(
        tmp_path / "run" / "memory-intent-gate-receipt.json"
    )
    assert receipt["evidence_case_gate_receipt"] == str(
        tmp_path / "run" / "evidence-case-gate-receipt.json"
    )
    assert receipt["dag_error"]["recommended_action"]["type"] == "repair_memory_intent"


def test_project_dag_memory_evidence_gate_blocks_clarify_route(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(tmp_path, overrides={"route": "CLARIFY"})
    evidence_case_path = _write_evidence_case(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MEMORY_ROUTE_NOT_DISPATCHABLE"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "memory_route_not_dispatchable"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "request_memory_clarification",
        "next_agent": "human",
        "reason": (
            "Memory routed to clarification or deflection; resolve that route before DAG dispatch."
        ),
    }


def test_project_dag_memory_evidence_gate_blocks_missing_case_hash(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(tmp_path)
    evidence_case_path = _write_evidence_case(tmp_path, overrides={"case_sha256": None})
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_EVIDENCE_CASE_HASH"
    assert receipt["alerts"][0]["code"] == "missing_evidence_case_hash"
    assert receipt["dag_error"]["recommended_action"]["type"] == "repair_evidence_case"


def test_project_dag_legacy_contract_still_runs_without_policy_profile(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt.get("zero_trust_preflight_receipt") is None


def test_project_dag_controlled_boundary_requires_explicit_secure_mode(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["data_boundary"] = {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "export_controlled": True,
        "itar": True,
        "technical_data": True,
        "foreign_person_access": "prohibited",
        "goal_hash": "sha256:active-goal",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "CONTROLLED_BOUNDARY_REQUIRES_SECURE_MODE"
    assert receipt["selected_agents"] == []
    assert receipt["command_executed"] is False
    assert receipt["provider_invoked"] is False
    assert receipt["filesystem_mutation_performed"] is False
    assert receipt["alerts"][0]["code"] == "controlled_boundary_requires_secure_mode"
    assert receipt["security_context_receipt"].endswith("security-context-receipt.json")


def test_project_dag_secure_relative_itar_boundary_blocks_before_dispatch_without_actor(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    boundary_path = _write_data_boundary(
        tmp_path,
        {
            "classification": "ITAR",
            "export_controlled": True,
            "itar": True,
            "technical_data": True,
            "foreign_person_access": "prohibited",
        },
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["data_boundary"] = boundary_path.name
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["command_policy"] = str(
        _write_command_policy(tmp_path, allowed_roots=[Path(sys.executable).name])
    )
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    run_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        security_mode="secure",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_ACTOR_ACCESS_MANIFEST"
    assert receipt["selected_agents"] == []
    assert receipt["command_executed"] is False
    assert receipt["provider_live"] is False
    assert receipt["provider_invoked"] is False
    assert receipt["filesystem_mutation_performed"] is False
    assert receipt["dag_error"]["failure_code"] == "missing_actor_access_manifest"
    assert (run_dir / "security-context-receipt.json").exists()
    assert (run_dir / "dag-receipt.json").exists()
    assert (run_dir / "environment-manifest.json").exists()
    assert not (run_dir / "command-loop").exists()
    assert not (run_dir / "compiled-command-specs").exists()
    assert not list(run_dir.rglob("*provider*receipt*.json"))


def test_project_dag_secure_mode_blocks_missing_node_capability_before_compilation(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    boundary_path = _write_data_boundary(
        tmp_path,
        {
            "classification": "public",
            "export_controlled": False,
            "itar": False,
            "technical_data": False,
            "foreign_person_access": "allowed",
        },
    )
    process_capability = _process_execute_capability(tmp_path)
    command_policy = _write_command_policy(
        tmp_path,
        allowed_roots=[Path(sys.executable).name],
        capability_rules=[
            {
                "capability": process_capability["capability"],
                "targets": [process_capability["target"]],
                "resource_scope": process_capability["resource_scope"],
                "maximum_effect": process_capability["maximum_effect"],
            }
        ],
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["security_mode"] = "secure"
    payload["data_boundary"] = boundary_path.name
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["actor_access_manifest"] = str(_write_actor_access_manifest(tmp_path))
    payload["command_policy"] = str(command_policy)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    run_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "CAPABILITY_REQUEST_DENIED"
    assert receipt["selected_agents"] == []
    assert receipt["command_executed"] is False
    assert receipt["provider_invoked"] is False
    assert receipt["filesystem_mutation_performed"] is False
    assert receipt["capability_decision_receipt"].endswith(
        "capability-decision-receipt.json"
    )
    decision = json.loads(
        (run_dir / "capability-decision-receipt.json").read_text(encoding="utf-8")
    )
    assert decision["status"] == "BLOCKED"
    assert decision["grant_count"] == 0
    assert not (run_dir / "capability-grants").exists()
    assert not (run_dir / "compiled-command-specs").exists()


def test_secure_ready_queue_blocks_before_capability_grant_artifacts(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    boundary_path = _write_data_boundary(
        tmp_path,
        {
            "classification": "public",
            "export_controlled": False,
            "itar": False,
            "technical_data": False,
            "foreign_person_access": "allowed",
        },
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["security_mode"] = "secure"
    payload["data_boundary"] = boundary_path.name
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["actor_access_manifest"] = str(_write_actor_access_manifest(tmp_path))
    payload["command_policy"] = str(
        _write_command_policy(tmp_path, allowed_roots=[Path(sys.executable).name])
    )
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    run_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "SECURE_MODE_REQUIRES_HANDOFF_LOOP"
    assert receipt["selected_agents"] == []
    assert receipt["capability_decision_receipt"] is None
    assert not (run_dir / "capability-decision-receipt.json").exists()
    assert not (run_dir / "capability-grants").exists()
    assert not (run_dir / "command-loop").exists()


def test_project_dag_routes_secure_containment_run_through_bwrap(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    itar_receipt = _write_gate_receipt(
        tmp_path / "itar-access-preflight-receipt.json",
        schema="tau.itar_access_preflight_receipt.v1",
        goal_hash="sha256:active-goal",
    )
    research_receipt = _write_gate_receipt(
        tmp_path / "research-query-safety-receipt.json",
        schema="tau.research_query_safety_receipt.v1",
        goal_hash="sha256:active-goal",
    )
    sandbox_receipt = _write_gate_receipt(
        tmp_path / "sandbox-run-receipt.json",
        schema="tau.sandbox_run_receipt.v1",
        goal_hash="sha256:active-goal",
    )
    package_receipt = _write_gate_receipt(
        tmp_path / "compliance-package-validation-receipt.json",
        schema="tau.compliance_package_validation_receipt.v1",
        goal_hash="sha256:active-goal",
        extra={"review_ready": True, "compliant": "NOT_CLAIMED"},
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    actor_manifest = _write_actor_access_manifest(tmp_path)
    process_capability = _process_execute_capability(tmp_path)
    command_policy = _write_command_policy(
        tmp_path,
        allowed_roots=[Path(sys.executable).name],
        capability_rules=[
            {
                "capability": process_capability["capability"],
                "targets": [process_capability["target"]],
                "resource_scope": process_capability["resource_scope"],
                "maximum_effect": process_capability["maximum_effect"],
            }
        ],
    )
    payload["data_boundary"] = {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "export_controlled": True,
        "itar": True,
        "technical_data": True,
        "foreign_person_access": "prohibited",
        "goal_hash": "sha256:active-goal",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }
    payload["security_mode"] = "secure"
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["actor_access_manifest"] = str(actor_manifest)
    payload["command_policy"] = str(command_policy)
    for node in payload["nodes"]:
        if node.get("executor") not in {"human", "scheduler", "virtual"}:
            node["requested_capabilities"] = [process_capability]
    payload["requires_external_research"] = True
    payload["requires_sandbox"] = True
    payload["requires_compliance_package_validation"] = True
    payload["itar_access_preflight_receipt"] = str(itar_receipt)
    payload["research_query_safety_receipt"] = str(research_receipt)
    payload["sandbox_run_receipt"] = str(sandbox_receipt)
    payload["compliance_package_validation_receipt"] = str(package_receipt)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["containment_gate_receipts"] == {
        "itar_access_preflight": str(itar_receipt.resolve()),
        "research_query_safety": str(research_receipt.resolve()),
        "sandbox_run": str(sandbox_receipt.resolve()),
        "compliance_package_validation": str(package_receipt.resolve()),
    }
    secure_receipt_path = (
        tmp_path
        / "run"
        / "secure-execution"
        / "coder"
        / "attempt-001"
        / "secure-execution-receipt.json"
    )
    secure_receipt = json.loads(secure_receipt_path.read_text(encoding="utf-8"))
    assert secure_receipt["mocked"] is False
    assert secure_receipt["live"] is True
    assert secure_receipt["host_environment_inherited"] is False
    if secure_receipt["status"] == "PASS":
        assert receipt["ok"] is True
        assert receipt["selected_agents"] == ["coder", "reviewer"]
    else:
        assert receipt["status"] == "BLOCKED"
        assert receipt["verdict"] == "SECURE_EXECUTION_BLOCKED"
        assert secure_receipt["command_executed"] is False
        assert "sandbox_backend_unavailable" in secure_receipt["alert_codes"]


def test_cli_dag_run_bad_project_contract_returns_course_correction_json(tmp_path: Path) -> None:
    contract_path = tmp_path / "bad-project-dag.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "bad-contract",
                "goal": {"goal_id": "bad", "goal_hash": "sha256:bad"},
                "target": {"repo": "grahama1970/tau"},
                "nodes": [],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "bad-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == DAG_ERROR_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "dag_contract_invalid"
    assert payload["verdict"] == "DAG_CONTRACT_INVALID"
    assert "goal.goal_version must be an integer or string" in payload["message"]
    assert "target must be a non-empty string" in payload["message"]
    assert payload["recommended_action"] == {
        "type": "repair_then_retry_or_reroute",
        "next_agent": "goal-guardian",
        "reason": "Repair the DAG contract so it satisfies tau.dag_contract.v1 before dispatch.",
    }
    assert payload["proof_scope"]["proves"] == [
        "Tau rejected a malformed or incomplete DAG contract before dispatch.",
        "Tau packaged the contract failure as a project-agent course-correction payload.",
        "No DAG route, goal, target, command, or handoff was executed.",
    ]


def test_cli_dag_run_skill_node_under_project_schema_names_generic_schema(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["nodes"][0].pop("command_spec")
    payload["nodes"][0]["skill"] = {
        "schema": "tau.skill_dag_node.v1",
        "capability": "review",
        "provider": "webgpt",
        "request": {"prompt": "review this"},
    }
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "skill-node-project-schema-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == DAG_ERROR_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "dag_contract_invalid"
    assert "nodes[0].skill is not supported by tau.dag_contract.v1" in payload["message"]
    assert "skill nodes require schema tau.generic_dag_spec.v1" in payload["message"]
    assert payload["evidence"]["primary_alert"]["message"] == payload["message"]


def test_fail_closed_registry_payload_names_executable_invariants() -> None:
    payload = fail_closed_registry_payload()

    assert payload["schema"] == FAIL_CLOSED_REGISTRY_SCHEMA
    assert payload["ok"] is True
    assert payload["status"] == "ACTIVE"
    assert payload["mocked"] is False
    assert payload["live"] is False
    assert payload["provider_live"] is False
    assert payload["invariant_count"] == len(payload["invariants"])
    assert payload["invariants"]["goal_hash_mismatch"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.handoff.active_goal_hash",
    }
    assert payload["invariants"]["unexpected_edge"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.observed_edges",
    }
    assert payload["invariants"]["reviewer_goal_hash_mismatch"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.reviewer_goal_hash",
    }
    assert payload["invariants"]["missing_work_order_sha256"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.provider_work_order.sha256",
    }
    for code in ROUTE_DECISION_VALIDATION_CODES:
        assert payload["invariants"][code] == {
            "severity": "BLOCK",
            "implemented_by": "tau.validators.dag.typed_route_decision",
        }
    assert "Unknown fail_closed_on codes fail closed" in payload["proof_scope"]["proves"][2]


def test_fail_closed_registry_receipt_can_be_written(tmp_path: Path) -> None:
    output_path = tmp_path / "fail-closed-registry.json"

    payload = write_fail_closed_registry_receipt(output_path)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload == written
    assert payload["schema"] == FAIL_CLOSED_REGISTRY_SCHEMA
    assert payload["receipt_path"] == str(output_path.resolve())
    assert payload["invariants"]["target_changed"]["implemented_by"] == (
        "tau.validators.handoff.github_target"
    )


@pytest.mark.parametrize("unsafe_node_id", ["../escape", "node/path", "node\\path", "."])
def test_dag_contract_rejects_node_ids_that_are_unsafe_as_artifact_paths(
    tmp_path: Path,
    unsafe_node_id: str,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["nodes"][0]["id"] = unsafe_node_id
    payload["entry_node"] = unsafe_node_id
    payload["edges"][0]["from"] = unsafe_node_id

    with pytest.raises(RuntimeError, match="must match"):
        project_dag.validate_dag_contract(payload)


def test_cli_dag_run_unknown_fail_closed_code_returns_course_correction_json(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["fail_closed_on"].append("nebulous_goal")
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "unknown-fail-closed-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    error_payload = json.loads(result.output)

    assert result.exit_code == 1
    assert error_payload["schema"] == DAG_ERROR_SCHEMA
    assert error_payload["failure_code"] == "dag_contract_invalid"
    assert (
        "fail_closed_on contains unknown invariant code(s): nebulous_goal"
        in error_payload["message"]
    )
    assert "goal_hash_mismatch" in error_payload["message"]
    assert error_payload["recommended_action"]["next_agent"] == "goal-guardian"


def test_cli_dag_run_missing_command_spec_returns_course_correction_json(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "missing-spec-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == DAG_ERROR_SCHEMA
    assert payload["failure_code"] == "dag_contract_invalid"
    assert "command_spec for node coder does not exist" in payload["message"]
    assert payload["evidence"]["alert_codes"] == ["dag_contract_invalid"]


def test_project_dag_evidence_manifest_allows_dispatch_when_required_kinds_exist(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    manifest = _write_evidence_manifest(tmp_path, ["creator_artifact", "reviewer_verdict"])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["evidence_manifest"] = str(manifest)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["selected_agents"] == ["coder", "reviewer"]
    assert receipt["evidence_validation_receipt"] == str(
        tmp_path / "run" / "evidence-validation-receipt.json"
    )
    assert Path(str(receipt["evidence_validation_receipt"])).exists()


def test_project_dag_evidence_manifest_blocks_missing_required_kind(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    manifest = _write_evidence_manifest(tmp_path, ["creator_artifact"])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["evidence_manifest"] = str(manifest)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "EVIDENCE_MANIFEST_MISSING_REQUIRED_EVIDENCE"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "evidence_manifest_missing_required_evidence"
    assert receipt["alerts"][0]["evidence"]["missing"] == ["reviewer_verdict"]
    assert receipt["dag_error"]["failure_code"] == "evidence_manifest_missing_required_evidence"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_evidence_manifest",
        "next_agent": "reviewer",
        "reason": "Repair or regenerate the typed evidence manifest before normal continuation.",
    }
    assert Path(str(receipt["evidence_validation_receipt"])).exists()


def test_project_dag_evidence_manifest_blocks_invalid_manifest_before_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    manifest = _write_evidence_manifest(
        tmp_path,
        ["creator_artifact", "reviewer_verdict"],
        bad_sha_for="reviewer_verdict",
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["evidence_manifest"] = str(manifest)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "EVIDENCE_MANIFEST_INVALID"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "evidence_manifest_invalid"
    assert "items[1].sha256 mismatch" in receipt["alerts"][0]["evidence"]["errors"][0]
    assert receipt["dag_error"]["failure_code"] == "evidence_manifest_invalid"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_evidence_manifest",
        "next_agent": "reviewer",
        "reason": "Repair or regenerate the typed evidence manifest before normal continuation.",
    }


def test_project_dag_command_policy_records_hashes_in_command_results(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    policy_path = _write_command_policy(tmp_path, allowed_roots=[Path(sys.executable).name])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )
    loop_receipt = json.loads(
        Path(str(receipt["command_loop_receipt"])).read_text(encoding="utf-8")
    )
    command_result = loop_receipt["dispatches"][0]["command_results"][0]

    assert receipt["ok"] is True
    assert command_result["command_policy_path"] == str(policy_path.resolve())
    assert str(command_result["command_policy_sha256"]).startswith("sha256:")
    assert str(command_result["command_spec_sha256"]).startswith("sha256:")


def test_project_dag_command_policy_blocks_denied_command_before_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    policy_path = _write_command_policy(
        tmp_path,
        allowed_roots=[Path(sys.executable).name, "rm"],
        denied=["rm"],
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    coder_spec = tmp_path / "specs" / "coder" / "tau-dispatch-command.json"
    coder_spec.write_text(
        json.dumps({"command": ["rm", "-rf", "/tmp/tau-denied"], "timeout_s": 5}),
        encoding="utf-8",
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "COMMAND_POLICY_REJECTED"
    assert "command is denied by command policy: rm" in "\n".join(receipt["errors"])
    assert receipt["dag_error"]["failure_code"] == "command_policy_rejected"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_command_policy",
        "next_agent": "goal-guardian",
        "reason": "Repair the command spec or trust policy before retrying the DAG.",
    }


def test_project_dag_command_policy_blocks_implicit_cwd_when_roots_are_restricted(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    policy_path = _write_command_policy(tmp_path, allowed_roots=[Path(sys.executable).name])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "reviewer", _creator_evidence()),
        include_cwd=False,
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "COMMAND_POLICY_REJECTED"
    assert "cwd must be explicit when command policy allowed_cwd_roots is set" in "\n".join(
        receipt["errors"]
    )


def test_project_dag_ready_queue_command_policy_blocks_denied_command(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    policy_path = _write_command_policy(
        tmp_path,
        allowed_roots=[Path(sys.executable).name, "rm"],
        denied=["rm"],
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_response_spec(tmp_path, "coder", _handoff("coder", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    coder_spec = tmp_path / "specs" / "coder" / "tau-dispatch-command.json"
    coder_spec.write_text(
        json.dumps({"command": ["rm", "-rf", "/tmp/tau-denied"], "timeout_s": 5}),
        encoding="utf-8",
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["failure_code"] == "command_policy_rejected"
    assert "command is denied by command policy: rm" in "\n".join(receipt["errors"])


def test_project_dag_blocks_missing_required_evidence_with_reviewer_action(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", []))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_REQUIRED_EVIDENCE"
    assert receipt["dag_error"]["failure_code"] == "missing_required_evidence"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "reroute",
        "next_agent": "reviewer",
        "reason": "Inspect missing or inconsistent evidence before normal continuation.",
    }


def test_project_dag_blocks_provider_auth_failure_when_auto_repair_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        project_dag,
        "preflight_battle_scillm_auth",
        lambda **_: {
            "schema": "tau.battle_scillm_auth_preflight.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": True,
            "model": "gpt-5.5",
            "base_url": "http://127.0.0.1:4001",
            "repair_allowed": True,
            "repair_attempted": True,
            "repair_status": "BLOCKED",
            "errors": ["Scillm auth remained blocked after proxy repair"],
        },
    )
    contract_path = _write_parallel_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_response_spec(
        tmp_path,
        "coder",
        _handoff(
            "coder",
            "reviewer",
            [
                {
                    "kind": "storyboard_identity_review",
                    "schema": "persona_dream.identity_continuity_review.v1",
                    "status": "FAIL",
                    "blocking_findings": [
                        "identity review call failed: HTTP Error 401: Unauthorized",
                        "image generation failed: 403 PERMISSION_DENIED: leaked API key",
                    ],
                    "model_policy": {
                        "provider": "codex",
                        "auth": "codex-oauth",
                        "model": "gpt-5.5",
                    },
                }
            ],
        ),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "PROVIDER_AUTH_REQUIRED"
    assert receipt["alerts"][0]["code"] == "provider_auth_required"
    assert receipt["dag_error"]["failure_code"] == "provider_auth_required"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_provider_auth",
        "next_agent": "goal-guardian",
        "reason": "Refresh provider OAuth/readiness before retrying the DAG node.",
    }
    assert len(receipt["course_correction_artifacts"]) == 1
    repair_receipts = list((tmp_path / "run" / "provider-auth-repair").glob("*.json"))
    assert len(repair_receipts) == 1
    repair_receipt = json.loads(repair_receipts[0].read_text(encoding="utf-8"))
    assert repair_receipt["schema"] == "tau.battle_scillm_auth_preflight.v1"
    assert repair_receipt["status"] == "BLOCKED"
    assert repair_receipt["tau_auto_repair"]["trigger"] == "provider_auth_required"
    course_correction = json.loads(
        Path(receipt["course_correction_artifacts"][0]).read_text(encoding="utf-8")
    )
    assert course_correction["schema"] == "tau.course_correction.v1"
    assert course_correction["trigger"] == "provider_auth_required"
    assert (
        course_correction["required_next_action"]
        == "repair_provider_auth_then_retry_or_route_human"
    )
    assert "regenerate_artifacts_before_auth_repair" in course_correction["forbidden_next_routes"]
    assert "provider_auth_repair_receipt" in course_correction["required_evidence_before_retry"]
    assert course_correction["required_action"]["type"] == "repair_provider_auth"
    assert (
        course_correction["required_action"]["repair_function"]
        == "tau_coding.battle_scillm.preflight_battle_scillm_auth"
    )
    assert course_correction["required_action"]["required_receipt_schemas"] == [
        "tau.battle_scillm_auth_preflight.v1",
        "tau.provider_readiness_run_receipt.v1",
    ]
    assert (
        "identity review call failed: HTTP Error 401: Unauthorized"
        in course_correction["observed_state"]["errors"]
    )
    assert (
        "image generation failed: 403 PERMISSION_DENIED: leaked API key"
        in course_correction["observed_state"]["errors"]
    )


def test_project_dag_auto_repairs_provider_auth_and_retries_same_node(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_preflight(**kwargs):
        calls.append(dict(kwargs))
        return {
            "schema": "tau.battle_scillm_auth_preflight.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "model": kwargs["model"],
            "base_url": kwargs["scillm_base_url"],
            "repair_allowed": True,
            "repair_attempted": True,
            "repair_status": "PASS",
            "errors": [],
        }

    monkeypatch.setattr(project_dag, "preflight_battle_scillm_auth", fake_preflight)
    monkeypatch.setattr(
        project_dag,
        "resolve_active_scillm_proxy_key",
        lambda: ("active-proxy-key", "docker:docker-scillm-proxy-1", []),
    )
    monkeypatch.delenv("SCILLM_PROXY_KEY", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 2
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_flaky_auth_then_response_spec(
        tmp_path,
        "coder",
        auth_failure=_handoff(
            "coder",
            "reviewer",
            [
                {
                    "kind": "storyboard_identity_review",
                    "schema": "persona_dream.identity_continuity_review.v1",
                    "status": "FAIL",
                    "blocking_findings": [
                        "identity review call failed: HTTP Error 401: Unauthorized"
                    ],
                    "model_policy": {
                        "provider": "codex",
                        "auth": "codex-oauth",
                        "model": "gpt-5.5",
                    },
                }
            ],
        ),
        success=_handoff("coder", "reviewer", _creator_evidence()),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["node_attempts"]["coder"] == 2
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-5.5"
    assert calls[0]["allow_repair"] is True
    repair_receipts = list((tmp_path / "run" / "provider-auth-repair").glob("*.json"))
    assert len(repair_receipts) == 1
    repair_receipt = json.loads(repair_receipts[0].read_text(encoding="utf-8"))
    assert repair_receipt["status"] == "PASS"
    assert repair_receipt["tau_auto_repair"]["node_id"] == "coder"
    assert repair_receipt["tau_auto_repair"]["env_refresh"] == {
        "status": "PASS",
        "ok": True,
        "source": "docker:docker-scillm-proxy-1",
        "updated_env": ["SCILLM_PROXY_KEY", "LITELLM_MASTER_KEY"],
        "errors": [],
    }
    assert os.environ["SCILLM_PROXY_KEY"] == "active-proxy-key"
    assert os.environ["LITELLM_MASTER_KEY"] == "active-proxy-key"
    assert "active-proxy-key" not in repair_receipts[0].read_text(encoding="utf-8")
    assert receipt["course_correction_artifacts"] == []
    repair_events = [
        event
        for event in receipt["scheduler_events"]
        if event["event"] == "provider_auth_repair_attempted"
    ]
    assert repair_events == [
        {
            "event": "provider_auth_repair_attempted",
            "node_id": "coder",
            "agent": "coder",
            "attempt": 1,
            "repair_status": "PASS",
            "repair_ok": True,
            "retrying": True,
            "provider_auth_repair_receipt": str(repair_receipts[0]),
            "ts": repair_events[0]["ts"],
        }
    ]


def test_project_dag_blocks_reviewer_target_mismatch(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    reviewer = _reviewer_handoff(goal_hash="sha256:active-goal")
    reviewer["result"]["evidence"][0]["reviewed_node_id"] = "different-node"  # type: ignore[index]
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", reviewer)

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "REVIEWER_TARGET_MISMATCH"
    assert receipt["dag_error"]["failure_code"] == "reviewer_target_mismatch"
    assert receipt["dag_error"]["failed_node"] == "reviewer"


def test_project_dag_bounded_ready_queue_blocks_cycles(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"].append({"from": "reviewer", "to": "start"})
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "CYCLE_DETECTED"
    assert receipt["dag_error"]["failure_code"] == "cycle_detected"
    assert receipt["dag_error"]["recommended_action"]["next_agent"] == "goal-guardian"


@pytest.mark.parametrize(
    ("mode", "route_fields", "expected_agents"),
    [
        ("exclusive", {"route": "ACCEPT"}, ["router", "accept"]),
        ("first_match", {"route": "BOTH"}, ["router", "accept"]),
        ("fanout", {"route": "BOTH"}, ["router", "accept", "revise"]),
        ("all_matching", {"route": "BOTH"}, ["router", "accept", "revise"]),
    ],
)
def test_project_dag_typed_route_modes_dispatch_only_activated_branches(
    tmp_path: Path,
    mode: str,
    route_fields: dict[str, str],
    expected_agents: list[str],
) -> None:
    contract_path = _write_routed_contract(tmp_path, mode=mode)
    router_response = _handoff("router", "accept", _creator_evidence())
    router_response["result"].update(route_fields)  # type: ignore[union-attr]
    _write_response_spec(tmp_path, "router", router_response)
    _write_response_spec(tmp_path, "accept", _handoff("accept", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "revise", _handoff("revise", "human", _creator_evidence()))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "PASS"
    assert receipt["execution"] == "project_agent_dag_plan_ready_queue"
    assert receipt["selected_agents"][0] == expected_agents[0]
    assert set(receipt["selected_agents"][1:]) == set(expected_agents[1:])
    assert len(receipt["route_decision_receipts"]) == 1
    decision_path = Path(receipt["route_decision_receipts"][0])
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["schema"] == "tau.dag_route_decision.v1"
    assert decision["status"] == "PASS"
    assert decision["source_result_sha256"].startswith("sha256:")
    assert decision["source_fields_sha256"].startswith("sha256:")
    assert decision["route_contract_sha256"].startswith("sha256:")
    assert decision["decision_sha256"].startswith("sha256:")
    route_event_index = next(
        index
        for index, event in enumerate(receipt["scheduler_events"])
        if event["event"] == "route_decided"
    )
    selected_start_indexes = [
        index
        for index, event in enumerate(receipt["scheduler_events"])
        if event["event"] == "node_started" and event["node_id"] in {"accept", "revise"}
    ]
    assert selected_start_indexes
    assert route_event_index < min(selected_start_indexes)


def test_project_dag_ambiguous_exclusive_route_blocks_without_branch_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="exclusive", both_match=True)
    router_response = _handoff("router", "accept", _creator_evidence())
    router_response["result"].update({"route": "BOTH"})  # type: ignore[union-attr]
    _write_response_spec(tmp_path, "router", router_response)
    _write_response_spec(tmp_path, "accept", _handoff("accept", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "revise", _handoff("revise", "human", _creator_evidence()))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "ROUTE_AMBIGUOUS_EXCLUSIVE"
    assert receipt["selected_agents"] == ["router"]
    assert not (tmp_path / "run" / "ready-queue" / "accept").exists()
    assert not (tmp_path / "run" / "ready-queue" / "revise").exists()
    decision = json.loads(
        Path(receipt["route_decision_receipts"][0]).read_text(encoding="utf-8")
    )
    assert decision["status"] == "BLOCKED"
    assert decision["failure_code"] == "route_ambiguous_exclusive"
    assert decision["selected_targets"] == []


def test_project_dag_fanout_activates_only_matching_subset(tmp_path: Path) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="fanout")
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"][2]["condition"]["value"] = "REVISE"
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    router_response = _handoff("router", "accept", _creator_evidence())
    router_response["result"].update({"route": "BOTH"})  # type: ignore[union-attr]
    _write_response_spec(tmp_path, "router", router_response)
    _write_response_spec(tmp_path, "accept", _handoff("accept", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "revise", _handoff("revise", "human", _creator_evidence()))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "PASS"
    assert receipt["selected_agents"] == ["router", "accept"]
    assert not (tmp_path / "run" / "ready-queue" / "revise").exists()


def test_project_dag_route_no_match_does_not_stall_or_dispatch_branch(tmp_path: Path) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="fanout")
    router_response = _handoff("router", "accept", _creator_evidence())
    router_response["result"].update({"route": "UNKNOWN"})  # type: ignore[union-attr]
    _write_response_spec(tmp_path, "router", router_response)
    _write_response_spec(tmp_path, "accept", _handoff("accept", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "revise", _handoff("revise", "human", _creator_evidence()))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "ROUTE_NO_MATCH"
    assert receipt["selected_agents"] == ["router"]
    assert "ready_queue_stalled" not in [alert["code"] for alert in receipt["alerts"]]


def test_project_dag_skipped_only_terminal_route_blocks(tmp_path: Path) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="exclusive")
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if not (edge["from"] == "accept" and edge["to"] == "human")
    ]
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    router_response = _handoff("router", "accept", _creator_evidence())
    router_response["result"]["route"] = "ACCEPT"  # type: ignore[index]
    _write_response_spec(tmp_path, "router", router_response)
    _write_response_spec(tmp_path, "accept", _handoff("accept", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "revise", _handoff("revise", "human", _creator_evidence()))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_TERMINAL_ROUTE"
    assert receipt["activated_terminals"] == []
    assert receipt["selected_agents"] == ["router", "accept"]
    assert "ready_queue_stalled" not in [alert["code"] for alert in receipt["alerts"]]


def test_project_dag_typed_route_requires_ready_queue_before_dispatch(tmp_path: Path) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="exclusive")
    run_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="handoff-loop",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "TYPED_ROUTE_REQUIRES_BOUNDED_READY_QUEUE"
    assert receipt["selected_agents"] == []
    assert not (run_dir / "compiled-command-specs").exists()
    assert not (run_dir / "command-loop").exists()


def test_cli_dag_run_executes_typed_exclusive_route(tmp_path: Path) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="exclusive")
    router_response = _handoff("router", "accept", _creator_evidence())
    router_response["result"].update({"route": "ACCEPT"})  # type: ignore[union-attr]
    _write_response_spec(tmp_path, "router", router_response)
    _write_response_spec(tmp_path, "accept", _handoff("accept", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "revise", _handoff("revise", "human", _creator_evidence()))

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "run"),
            "--agents-root",
            str(tmp_path / "agents"),
            "--scheduler",
            "bounded-ready-queue",
        ],
    )
    receipt = json.loads(result.output)

    assert result.exit_code == 0
    assert receipt["status"] == "PASS"
    assert receipt["selected_agents"] == ["router", "accept"]
    assert len(receipt["route_decision_receipts"]) == 1


def test_cli_dag_run_rejects_expression_condition_before_dispatch(tmp_path: Path) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="exclusive")
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"][1]["condition"] = "result.route == 'ACCEPT'"
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    run_dir = tmp_path / "run"

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(run_dir),
            "--agents-root",
            str(tmp_path / "agents"),
            "--scheduler",
            "bounded-ready-queue",
        ],
    )
    receipt = json.loads(result.output)

    assert result.exit_code == 1
    assert receipt["verdict"] == "UNSUPPORTED_READY_QUEUE_CONDITION"
    assert receipt["selected_agents"] == []
    assert not (run_dir / "compiled-command-specs").exists()
    assert not (run_dir / "ready-queue").exists()


def test_project_dag_typed_route_rejects_implicit_join_before_dispatch(tmp_path: Path) -> None:
    contract_path = _write_routed_contract(tmp_path, mode="exclusive")
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"].append({"from": "start", "to": "accept"})
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    run_dir = tmp_path / "run"
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "CONDITIONAL_TARGET_MULTIPLE_PREDECESSORS"
    assert receipt["selected_agents"] == []
    assert not (run_dir / "compiled-command-specs").exists()
    assert not (run_dir / "ready-queue").exists()


def test_project_dag_bounded_ready_queue_blocks_unsupported_condition_before_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"][0]["condition"] = "reviewer_pass"
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    run_dir = tmp_path / "run"
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "UNSUPPORTED_READY_QUEUE_CONDITION"
    assert receipt["selected_agents"] == []
    assert receipt["command_executed"] is False
    assert receipt["alerts"] == [
        {
            "severity": "BLOCK",
            "code": "unsupported_ready_queue_condition",
        "message": (
            "Bounded ready-queue accepts only tau.route_condition.v1 objects. Replace the "
            "legacy or untyped condition with a closed typed route condition before dispatch."
        ),
            "evidence": {
                "edges": [
                    {
                        "from": "start",
                        "to": "research",
                        "condition": "reviewer_pass",
                    }
                ]
            },
        }
    ]
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "unsupported_ready_queue_condition"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_dag_route_contract",
        "next_agent": "goal-guardian",
        "reason": "Repair the typed route contract or source result before continuing.",
    }
    assert not (run_dir / "compiled-command-specs").exists()
    assert not (run_dir / "command-loop").exists()
    assert not (run_dir / "ready-queue").exists()


@pytest.mark.parametrize(
    "condition",
    [{}, [], 1, True, {"route": "reviewer_pass"}],
    ids=["empty-object", "empty-list", "integer", "boolean", "route-object"],
)
def test_project_dag_bounded_ready_queue_blocks_non_string_condition_before_dispatch(
    tmp_path: Path,
    condition: object,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"][0]["condition"] = condition
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    run_dir = tmp_path / "run"
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "UNSUPPORTED_READY_QUEUE_CONDITION"
    assert receipt["selected_agents"] == []
    assert receipt["command_executed"] is False
    assert receipt["alerts"][0]["evidence"]["edges"] == [
        {
            "from": "start",
            "to": "research",
            "condition": condition,
        }
    ]
    assert not (run_dir / "compiled-command-specs").exists()
    assert not (run_dir / "command-loop").exists()
    assert not (run_dir / "ready-queue").exists()


def test_project_dag_bounded_ready_queue_blocks_yaml_date_condition_with_receipt(
    tmp_path: Path,
) -> None:
    json_contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(json_contract_path.read_text(encoding="utf-8"))
    payload["edges"][0]["condition"] = date(2024, 1, 1)
    contract_path = tmp_path / "parallel.dag.yml"
    contract_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    run_dir = tmp_path / "run"
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "UNSUPPORTED_READY_QUEUE_CONDITION"
    assert receipt["selected_agents"] == []
    assert receipt["command_executed"] is False
    assert receipt["alerts"][0]["evidence"]["edges"][0]["condition"] == {
        "type": "date",
        "value": "2024-01-01",
    }
    assert (run_dir / "dag-receipt.json").is_file()
    assert not (run_dir / "compiled-command-specs").exists()
    assert not (run_dir / "command-loop").exists()
    assert not (run_dir / "ready-queue").exists()


def test_project_dag_bounded_ready_queue_blocks_mutating_nodes(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["mutates"] = True
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MUTATING_NODE_NOT_ALLOWED"
    assert receipt["dag_error"]["failure_code"] == "mutating_node_not_allowed"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "request_policy_gate",
        "next_agent": "goal-guardian",
        "reason": "This branch requires an explicit policy or branch-lock gate before execution.",
    }


def _write_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    spec_root = tmp_path / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "creator-reviewer-test",
        "goal": {
            "goal_id": "creator-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-creator-reviewer",
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
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
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
            "malformed_handoff",
        ],
    }
    path = tmp_path / "dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _embry_voice_persistent_subagent() -> dict[str, object]:
    return {
        "schema": "tau.persistent_subagent.v1",
        "surface_id": "embry-voice",
        "surface_url": "http://localhost:3002/#embry-voice",
        "session_mode": "persistent",
        "tau_control": "bounded_receipt_gated_ticks",
        "dag_parameter": "embry_voice_surface",
        "required_receipts": ["embry.chatterbox_voice_receipt.v1"],
        "unbounded_autonomy_allowed": False,
        "memory_write_requires_receipt": True,
        "notes": [
            "The Embry voice UI can stay open across DAG ticks.",
            "Tau still dispatches bounded work and accepts only receipt-backed outputs.",
        ],
    }


def _write_gate_receipt(
    path: Path,
    *,
    schema: str,
    goal_hash: str,
    extra: dict[str, object] | None = None,
) -> Path:
    payload: dict[str, object] = {
        "schema": schema,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "goal_hash": goal_hash,
        "receipt_path": str(path),
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_repeated_reviewer_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    spec_root = tmp_path / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "repeated-reviewer-test",
        "goal": {
            "goal_id": "repeated-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-repeated-reviewer",
        },
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 4,
        },
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
                "id": "reviewer-a",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer-a" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
            {
                "id": "reviewer-b",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer-b" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
        ],
        "edges": [
            {"from": "coder", "to": "reviewer-a"},
            {"from": "reviewer-a", "to": "reviewer-b"},
            {"from": "reviewer-b", "to": "human"},
        ],
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
    path = tmp_path / "repeated-reviewer-dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_provider_sensitive_contract(tmp_path: Path, *, complete: bool) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    spec_root = tmp_path / "specs"
    creator_evidence = ["storyboard_creator_receipt.json"]
    reviewer_evidence = ["storyboard_review_verdict.json"]
    if complete:
        creator_evidence.append("provider_route_receipt")
        reviewer_evidence.append("provider_route_receipt")
    nodes: list[dict[str, object]] = [
        {
            "id": "panel-creator",
            "agent": "panel-creator",
            "executor": "local",
            "max_attempts": 2,
            "command_spec": str(spec_root / "panel-creator" / "tau-dispatch-command.json"),
            "required_evidence": creator_evidence,
        },
        {
            "id": "panel-reviewer",
            "agent": "panel-reviewer",
            "executor": "local",
            "max_attempts": 2,
            "command_spec": str(spec_root / "panel-reviewer" / "tau-dispatch-command.json"),
            "required_evidence": reviewer_evidence,
        },
        {
            "id": "human",
            "agent": "human",
            "executor": "human",
        },
    ]
    if complete:
        for node in nodes:
            if node["id"] == "human":
                continue
            node["model_policy"] = {
                "provider": "scillm",
                "auth": "codex-oauth",
                "model": "gpt-image-2",
            }
            node["prompt_contract"] = {
                "schema": "tau.prompt_contract.v1",
                "system_prompt": "Stay inside the immutable storyboard goal.",
                "user_template": "Use the provider route evidence before claiming PASS.",
            }
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "persona-dream-phase-07-storyboard-packet-panel-review",
        "provider_sensitive": True,
        "goal": {
            "goal_id": "persona-dream-phase-07-storyboard-panels-accepted",
            "goal_version": 1,
            "goal_hash": "sha256:phase07-storyboard-panels-accepted-20260705",
        },
        "target": {
            "repo": "grahama1970/agent-skills",
            "target": "skills/persona-dream/reports/pipeline-complete/phase_07_storyboard_live_tau",
        },
        "entry_node": "panel-creator",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": False,
            "default_timeout_seconds": 120,
            "max_total_attempts": 4,
        },
        "nodes": nodes,
        "edges": [
            {"from": "panel-creator", "to": "panel-reviewer"},
            {"from": "panel-reviewer", "to": "human"},
        ],
        "required_evidence": [
            "dag-receipt.json",
            *creator_evidence,
            *reviewer_evidence,
        ],
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
    path = tmp_path / "provider-sensitive-dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _persona_dream_provider_handoff(
    previous: str,
    next_agent: str,
    evidence: list[dict],
) -> dict:
    payload = _handoff(previous, next_agent, evidence)
    payload["github"] = {
        "repo": "grahama1970/agent-skills",
        "target": "skills/persona-dream/reports/pipeline-complete/phase_07_storyboard_live_tau",
    }
    payload["goal"] = {
        "goal_id": "persona-dream-phase-07-storyboard-panels-accepted",
        "goal_version": 1,
        "goal_hash": "sha256:phase07-storyboard-panels-accepted-20260705",
    }
    return payload


def _write_yaml_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    spec_root = tmp_path / "specs"
    path = tmp_path / "creator-reviewer.dag.yml"
    path.write_text(
        f"""schema: tau.dag_contract.v1
dag_id: creator-reviewer-test
goal:
  goal_id: creator-reviewer-test
  goal_version: 1
  goal_hash: sha256:active-goal
target:
  repo: grahama1970/tau
  target: scratch-creator-reviewer
entry_node: coder
terminal_nodes:
  - human
limits:
  resume: true
  default_timeout_seconds: 30
  max_total_attempts: 3
nodes:
  - id: coder
    agent: coder
    executor: local
    max_attempts: 1
    command_spec: {spec_root / "coder" / "tau-dispatch-command.json"}
    required_evidence:
      - creator_artifact
  - id: reviewer
    agent: reviewer
    executor: local
    max_attempts: 1
    command_spec: {spec_root / "reviewer" / "tau-dispatch-command.json"}
    required_evidence:
      - reviewer_verdict
    reviewer:
      reviews_node: coder
      requires_goal_hash: true
edges:
  - from: coder
    to: reviewer
  - from: reviewer
    to: human
required_evidence:
  - creator_artifact
  - reviewer_verdict
fail_closed_on:
  - goal_hash_mismatch
  - target_changed
  - unexpected_node
  - unexpected_edge
  - missing_required_evidence
  - max_attempts_exceeded
  - malformed_handoff
""",
        encoding="utf-8",
    )
    return path


def _write_zero_trust_policy(tmp_path: Path) -> Path:
    path = tmp_path / "zero-trust-policy.json"
    path.write_text(json.dumps(_zero_trust_policy()), encoding="utf-8")
    return path


def _write_data_boundary(tmp_path: Path, overrides: dict[str, object] | None = None) -> Path:
    path = tmp_path / "data-boundary.json"
    payload = _public_data_boundary()
    if overrides:
        payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_actor_access_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "actor-access-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.actor_access_manifest.v1",
                "actor_id": "human:tester",
                "actor_type": "human",
                "roles": ["approver"],
                "trusted": True,
                "verified": True,
                "eligibility": {
                    "us_person": "verified",
                    "foreign_person": False,
                    "export_control_training_current": True,
                    "approved_for_boundary": ["ITAR"],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_memory_intent(
    tmp_path: Path,
    *,
    overrides: dict[str, object] | None = None,
) -> Path:
    payload: dict[str, object] = {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "route": "ANSWER",
        "confidence": 0.91,
        "goal_hash": "sha256:active-goal",
        "target": {"repo": "grahama1970/tau", "target": "scratch-creator-reviewer"},
    }
    if overrides:
        payload.update(overrides)
    path = tmp_path / "memory-intent.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_evidence_case(
    tmp_path: Path,
    *,
    overrides: dict[str, object | None] | None = None,
) -> Path:
    payload: dict[str, object | None] = {
        "schema": "tau.evidence_case.v1",
        "case_id": "case-001",
        "case_sha256": "sha256:" + ("1" * 64),
        "goal_hash": "sha256:active-goal",
        "target": {"repo": "grahama1970/tau", "target": "scratch-creator-reviewer"},
        "support_artifacts": [],
    }
    if overrides:
        payload.update(overrides)
    path = tmp_path / "evidence-case.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _zero_trust_policy() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {"external_search": "deny", "manual_sanitized_receipt": "allow_with_review"},
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _public_data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }


def _memory_intent() -> dict:
    return {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "planner_only": True,
        "route": "COMPLIANCE",
        "confidence": 0.91,
        "recall_profile": "proof_retrieval",
        "required_artifacts": [],
        "tool_calls": [{"name": "create_evidence_case"}],
        "evidence_case_required": True,
    }


def _memory_evidence_case() -> dict:
    return {
        "schema": "memory.evidence_case.v1",
        "source": "graph-memory-operator:/create-evidence-case",
        "sha256": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        "question": "Can Tau dispatch this zero-trust DAG?",
        "data_boundary": _public_data_boundary(),
        "policy_profile": {
            "schema": "tau.policy_profile.v1",
            "profile_id": "itar-zero-trust-local-only",
            "default_decision": "deny",
        },
    }


def _write_routed_contract(
    tmp_path: Path,
    *,
    mode: str,
    both_match: bool = False,
) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    spec_root = tmp_path / "specs"
    both_modes = {"first_match", "fanout", "all_matching"}
    accept_value = "BOTH" if both_match or mode in both_modes else "ACCEPT"
    revise_value = "BOTH" if both_match or mode in both_modes else "REVISE"

    def condition(value: str) -> dict[str, object]:
        return {
            "schema": "tau.route_condition.v1",
            "op": "eq",
            "field": "route",
            "value": value,
        }
    payload = {
        "schema": "tau.dag_contract.v1",
        "dag_id": f"typed-route-{mode}",
        "goal": {
            "goal_id": "creator-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-creator-reviewer",
        },
        "entry_node": "start",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 4,
            "max_concurrency": 2,
        },
        "nodes": [
            {
                "id": "start",
                "agent": "goal-guardian",
                "executor": "scheduler",
                "max_attempts": 1,
                "required_evidence": [],
            },
            {
                "id": "router",
                "agent": "router",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "router" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
                "route": {"mode": mode},
            },
            *[
                {
                    "id": node_id,
                    "agent": node_id,
                    "executor": "local",
                    "max_attempts": 1,
                    "command_spec": str(
                        spec_root / node_id / "tau-dispatch-command.json"
                    ),
                    "required_evidence": ["creator_artifact"],
                }
                for node_id in ("accept", "revise")
            ],
        ],
        "edges": [
            {"from": "start", "to": "router"},
            {
                "from": "router",
                "to": "accept",
                "condition": condition(accept_value),
            },
            {
                "from": "router",
                "to": "revise",
                "condition": condition(revise_value),
            },
            {"from": "accept", "to": "human"},
            {"from": "revise", "to": "human"},
        ],
        "required_evidence": ["creator_artifact"],
        "fail_closed_on": [
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
        ],
    }
    path = tmp_path / f"typed-route-{mode}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_parallel_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    spec_root = tmp_path / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "parallel-creator-reviewer-test",
        "goal": {
            "goal_id": "creator-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-creator-reviewer",
        },
        "entry_node": "start",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 4,
            "max_concurrency": 2,
        },
        "nodes": [
            {
                "id": "start",
                "agent": "goal-guardian",
                "executor": "scheduler",
                "max_attempts": 1,
                "required_evidence": [],
            },
            {
                "id": "research",
                "agent": "research-auditor",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "research-auditor" / "tau-dispatch-command.json"),
                "required_evidence": ["source_summary"],
            },
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
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
        ],
        "edges": [
            {"from": "start", "to": "research"},
            {"from": "start", "to": "coder"},
            {"from": "research", "to": "reviewer"},
            {"from": "coder", "to": "reviewer"},
            {"from": "reviewer", "to": "human"},
        ],
        "required_evidence": ["source_summary", "creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "missing_required_join",
            "max_attempts_exceeded",
            "malformed_handoff",
        ],
    }
    path = tmp_path / "parallel-dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_evidence_manifest(
    tmp_path: Path,
    kinds: list[str],
    *,
    bad_sha_for: str | None = None,
) -> Path:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, object]] = []
    for kind in kinds:
        evidence_path = evidence_dir / f"{kind}.json"
        schema = f"tau.{kind}.v1"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "kind": kind,
                    "goal_hash": "sha256:active-goal",
                    "status": "PASS",
                }
            ),
            encoding="utf-8",
        )
        digest = f"sha256:{hashlib.sha256(evidence_path.read_bytes()).hexdigest()}"
        if kind == bad_sha_for:
            digest = "sha256:" + ("0" * 64)
        items.append(
            {
                "kind": kind,
                "path": str(evidence_path),
                "sha256": digest,
                "schema": schema,
                "validator": f"tau evidence-validate {kind}",
                "valid": True,
            }
        )
    manifest_path = tmp_path / "evidence-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "tau.evidence_manifest.v1",
                "run_id": "run-001",
                "dag_id": "creator-reviewer-test",
                "goal_hash": "sha256:active-goal",
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _write_command_policy(
    tmp_path: Path,
    *,
    allowed_roots: list[str],
    denied: list[str] | None = None,
    capability_rules: list[dict[str, object]] | None = None,
) -> Path:
    policy_path = tmp_path / "command-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema": TAU_COMMAND_SPEC_POLICY_SCHEMA,
                "allowed_command_roots": allowed_roots,
                "denied_commands": denied or [],
                "allowed_cwd_roots": [str(tmp_path)],
                "capability_grant_ttl_seconds": 300,
                "capability_rules": capability_rules or [],
            }
        ),
        encoding="utf-8",
    )
    return policy_path


def _process_execute_capability(tmp_path: Path) -> dict[str, object]:
    return {
        "capability": "process.execute",
        "target": Path(sys.executable).name,
        "resource_scope": [str(tmp_path)],
        "maximum_effect": {"max_processes": 1},
    }


def _write_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    sleep_seconds: float = 0.0,
    timeout_s: float = 5,
    include_cwd: bool = True,
    exit_code: int = 0,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = ""
    if sleep_seconds:
        code += f"import time; time.sleep({sleep_seconds!r}); "
    code += f"print({json.dumps(json.dumps(response))}); raise SystemExit({exit_code})"
    payload: dict[str, object] = {
        "command": [
            sys.executable,
            "-c",
            code,
        ],
        "timeout_s": timeout_s,
    }
    if include_cwd:
        payload["cwd"] = str(tmp_path)
    spec_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_flaky_auth_then_response_spec(
    tmp_path: Path,
    agent: str,
    *,
    auth_failure: dict[str, object],
    success: dict[str, object],
    timeout_s: float = 5,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path = tmp_path / f"{agent}-attempt-count.txt"
    code = "\n".join(
        [
            "import json",
            "from pathlib import Path",
            f"counter_path = Path({str(counter_path)!r})",
            "count = int(counter_path.read_text() or '0') if counter_path.exists() else 0",
            "counter_path.write_text(str(count + 1))",
            f"auth_failure = json.loads({json.dumps(auth_failure)!r})",
            f"success = json.loads({json.dumps(success)!r})",
            "print(json.dumps(auth_failure if count == 0 else success))",
        ]
    )
    spec_path.write_text(
        json.dumps(
            {
                "command": [sys.executable, "-c", code],
                "timeout_s": timeout_s,
                "cwd": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )


def _write_stdin_capture_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    timeout_s: float = 5,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"""
import json
import os
import sys
from pathlib import Path

payload = json.loads(sys.stdin.readline())
artifact_dir = Path(os.environ["TAU_HANDOFF_COMMAND_ARTIFACT_DIR"])
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "request.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
print({json.dumps(json.dumps(response))})
"""
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    code,
                ],
                "timeout_s": timeout_s,
            }
        ),
        encoding="utf-8",
    )


def _write_flaky_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    first_failure: str,
    timeout_s: float = 5,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    state_path = spec_path.parent / "attempt-count.txt"
    if first_failure == "timeout":
        failure_code = "import time; time.sleep(0.2)"
    elif first_failure == "non-json":
        failure_code = "print('not json')"
    else:  # pragma: no cover - helper contract guard.
        raise AssertionError(f"unknown first_failure: {first_failure}")
    code = f"""
from pathlib import Path
import json
state = Path({str(state_path)!r})
count = int(state.read_text() or '0') if state.exists() else 0
state.write_text(str(count + 1))
if count == 0:
    {failure_code}
else:
    print({json.dumps(json.dumps(response))})
"""
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    code,
                ],
                "timeout_s": timeout_s,
            }
        ),
        encoding="utf-8",
    )


def _write_always_non_json_spec(tmp_path: Path, agent: str) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        json.dumps(
            {
                "command": [sys.executable, "-c", "print('not json')"],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )


def _write_pointless_unit_test_failure_spec(tmp_path: Path, agent: str) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "print('pytest tests/test_unrelated.py -q\\ncollected 1 item\\nFAILED'); "
                        "raise SystemExit(1)"
                    ),
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )


def _handoff(
    previous_subagent: str,
    next_agent: str,
    evidence: list[object],
) -> dict[str, object]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/tau",
            "target": "scratch-creator-reviewer",
        },
        "goal": {
            "goal_id": "creator-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": previous_subagent,
        "context": {
            "summary": f"{previous_subagent} node response.",
            "artifacts": [],
        },
        "result": {
            "status": "PASS",
            "summary": f"{previous_subagent} completed.",
            "evidence": evidence,
        },
        "rationale": "The DAG contract controls the next route.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Continue along the DAG route.",
        },
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human.",
    }


def _repeated_reviewer_handoff(
    previous_subagent: str,
    next_agent: str,
    evidence: list[object],
) -> dict[str, object]:
    payload = _handoff(previous_subagent, next_agent, evidence)
    payload["github"] = {
        "repo": "grahama1970/tau",
        "target": "scratch-repeated-reviewer",
    }
    payload["goal"] = {
        "goal_id": "repeated-reviewer-test",
        "goal_version": 1,
        "goal_hash": "sha256:active-goal",
    }
    return payload


def _creator_evidence() -> list[object]:
    return [{"kind": "creator_artifact", "path": "/tmp/creator-artifact.txt"}]


def _reviewer_handoff(*, goal_hash: str) -> dict[str, object]:
    response = _handoff(
        "reviewer",
        "human",
        [
            {
                "kind": "reviewer_verdict",
                "reviewed_node_id": "coder",
                "goal_hash": goal_hash,
                "verdict": "PASS",
            }
        ],
    )
    response["goal"] = {
        "goal_id": "creator-reviewer-test",
        "goal_version": 1,
        "goal_hash": "sha256:active-goal",
    }
    return response
