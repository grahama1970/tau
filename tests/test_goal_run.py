import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.goal_run import (
    TAU_GOAL_RUN_RECEIPT_SCHEMA,
    evaluate_goal_completion,
    run_goal_until_complete,
)


def test_evaluate_goal_completion_requires_terminal_pass_evidence_and_criteria() -> None:
    handoff = _handoff(
        previous_subagent="worker",
        next_agent="human",
        result_status="PASS",
        evidence=["artifact.json"],
        completed_criteria=["tests pass"],
    )

    result = evaluate_goal_completion(
        handoff,
        required_criteria=("tests pass", "docs updated"),
    )

    assert result.solved is False
    assert result.reason == "missing_completion_criteria"
    assert result.missing_criteria == ("docs updated",)


def test_goal_run_repeats_ticks_until_completion_criteria_pass(tmp_path: Path) -> None:
    start_path, goal_helper_path, agents_root, spec_root = _write_goal_run_fixture(tmp_path)

    receipt = run_goal_until_complete(
        start_path=start_path,
        goal_helper_path=goal_helper_path,
        receipt_dir=tmp_path / "receipts",
        agent_registry_root=agents_root,
        command_spec_root=spec_root,
        active_goal_hash="sha256:active-goal",
        timeout_s=10,
        max_steps_per_tick=1,
    )

    assert receipt["schema"] == TAU_GOAL_RUN_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["stop_reason"] == "completion_criteria_satisfied"
    assert receipt["tick_count"] == 2
    assert receipt["completion_evaluation"]["solved"] is True
    assert receipt["completion_evaluation"]["completed_criteria"] == [
        "implementation patched",
        "tests passed",
    ]
    assert (tmp_path / "receipts" / "goal-run-receipt.json").exists()


def test_goal_run_times_out_across_repeated_ticks(tmp_path: Path) -> None:
    start_path, goal_helper_path, agents_root, spec_root = _write_goal_run_fixture(
        tmp_path,
        never_complete=True,
    )

    receipt = run_goal_until_complete(
        start_path=start_path,
        goal_helper_path=goal_helper_path,
        receipt_dir=tmp_path / "receipts",
        agent_registry_root=agents_root,
        command_spec_root=spec_root,
        active_goal_hash="sha256:active-goal",
        timeout_s=0.05,
        max_steps_per_tick=1,
        poll_interval_s=0.02,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "TIMEOUT"
    assert receipt["stop_reason"] == "deadline_expired"
    assert receipt["deadline_exceeded"] is True
    assert receipt["tick_count"] >= 1


def test_goal_run_caps_command_timeout_by_deadline(tmp_path: Path) -> None:
    start_path, goal_helper_path, agents_root, spec_root = _write_goal_run_fixture(
        tmp_path,
        sleep_s=1.0,
    )

    receipt = run_goal_until_complete(
        start_path=start_path,
        goal_helper_path=goal_helper_path,
        receipt_dir=tmp_path / "receipts",
        agent_registry_root=agents_root,
        command_spec_root=spec_root,
        active_goal_hash="sha256:active-goal",
        timeout_s=0.05,
        max_steps_per_tick=1,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "TIMEOUT"
    assert receipt["stop_reason"] == "deadline_expired"
    assert receipt["ticks"][0]["loop_stop_reason"] == "command_timeout"


def test_goal_run_cli_writes_receipt(tmp_path: Path) -> None:
    start_path, goal_helper_path, agents_root, spec_root = _write_goal_run_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "goal",
            "run",
            "--until-complete",
            "--start",
            str(start_path),
            "--goal-helper",
            str(goal_helper_path),
            "--receipt-dir",
            str(tmp_path / "cli-receipts"),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            str(spec_root),
            "--active-goal-hash",
            "sha256:active-goal",
            "--timeout-s",
            "10",
            "--tick-max-steps",
            "1",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == TAU_GOAL_RUN_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["tick_count"] == 2
    assert (tmp_path / "cli-receipts" / "goal-run-receipt.json").exists()


def _write_goal_run_fixture(
    tmp_path: Path,
    *,
    never_complete: bool = False,
    sleep_s: float = 0.0,
) -> tuple[Path, Path, Path, Path]:
    agents_root = tmp_path / "agents"
    spec_root = tmp_path / "specs"
    worker_agent = agents_root / "worker"
    worker_spec = spec_root / "worker"
    worker_agent.mkdir(parents=True)
    worker_spec.mkdir(parents=True)
    (worker_agent / "AGENTS.md").write_text("---\nid: worker\n---\n", encoding="utf-8")
    state_path = tmp_path / "worker-count.txt"
    script_path = tmp_path / "worker.py"
    script_path.write_text(
        _worker_script(never_complete=never_complete, sleep_s=sleep_s),
        encoding="utf-8",
    )
    (worker_spec / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [sys.executable, str(script_path), str(state_path)],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )
    start_path = tmp_path / "start-handoff.json"
    start_path.write_text(
        json.dumps(
            _handoff(
                previous_subagent="human",
                next_agent="worker",
                result_status="REQUESTED",
                evidence=["goal-helper.json"],
            )
        ),
        encoding="utf-8",
    )
    goal_helper_path = tmp_path / "goal-helper.json"
    goal_helper_path.write_text(
        json.dumps(
            {
                "schema": "tau.goal_helper.v1",
                "goal": {
                    "goal_id": "goal-run-test",
                    "goal_version": 1,
                    "goal_hash": "sha256:active-goal",
                },
                "completion_criteria": [
                    "implementation patched",
                    "tests passed",
                ],
            }
        ),
        encoding="utf-8",
    )
    return start_path, goal_helper_path, agents_root, spec_root


def _worker_script(*, never_complete: bool, sleep_s: float) -> str:
    complete_branch = (
        "False"
        if never_complete
        else "count >= 2"
    )
    return f"""
import json
import sys
import time
from pathlib import Path

state = Path(sys.argv[1])
count = int(state.read_text()) if state.exists() else 0
count += 1
state.write_text(str(count))
if {sleep_s!r}:
    time.sleep({sleep_s!r})
payload = json.load(sys.stdin)
payload["previous_subagent"] = "worker"
payload["context"] = payload.get("context", {{}})
payload["context"]["artifacts"] = payload["context"].get("artifacts", []) + [str(state)]
if {complete_branch}:
    payload["result"] = {{
        "status": "PASS",
        "summary": "Explicit completion criteria are satisfied.",
        "evidence": ["implementation patched", "tests passed"],
        "completed_criteria": ["implementation patched", "tests passed"],
    }}
    payload["next_agent"] = {{
        "name": "human",
        "executor": "human",
        "reason": "Goal completion criteria are ready for human inspection.",
    }}
else:
    payload["result"] = {{
        "status": "NEEDS_MORE",
        "summary": "More bounded work is required.",
        "evidence": ["attempt recorded"],
        "completed_criteria": ["implementation patched"] if count > 1 else [],
    }}
    payload["next_agent"] = {{
        "name": "worker",
        "executor": "local",
        "reason": "Continue the bounded worker loop.",
    }}
payload["rationale"] = "Goal-run worker emitted the next bounded handoff."
payload["required_evidence"] = ["explicit completion criteria"]
payload["stop_condition"] = "Human route with PASS and completed criteria."
print(json.dumps(payload))
"""


def _handoff(
    *,
    previous_subagent: str,
    next_agent: str,
    result_status: str,
    evidence: list[str],
    completed_criteria: list[str] | None = None,
) -> dict:
    result = {
        "status": result_status,
        "summary": "Goal run test handoff.",
        "evidence": evidence,
    }
    if completed_criteria is not None:
        result["completed_criteria"] = completed_criteria
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/tau",
            "target": "issue#goal-run",
        },
        "goal": {
            "goal_id": "goal-run-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": previous_subagent,
        "context": {
            "summary": "Goal-run test context.",
            "artifacts": ["goal-helper.json"],
        },
        "result": result,
        "rationale": "Goal-run test route.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Route required for goal-run test.",
        },
        "required_evidence": ["explicit completion criteria"],
        "stop_condition": "Completion criteria pass or deadline expires.",
    }
