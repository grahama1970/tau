from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tau-handoff-dispatch.yml"


def test_tau_handoff_dispatch_workflow_runs_bounded_command_loop() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "uv run tau handoff-command-loop" in text
    assert "--start \"$TAU_START_HANDOFF\"" in text
    assert "--active-goal-hash \"$TAU_ACTIVE_GOAL_HASH\"" in text
    assert "--max-steps \"$TAU_MAX_STEPS\"" in text
    assert "experiments/goal-locked-subagents/agent-command-specs" in text
    assert "actions/upload-artifact@v4" in text
