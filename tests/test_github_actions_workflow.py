from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tau-handoff-dispatch.yml"


def test_tau_handoff_dispatch_workflow_runs_bounded_command_loop() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "issue_comment:" in text
    assert "contains(github.event.comment.body, '/tau handoff-dispatch')" in text
    assert "apply_github_transport:" in text
    assert "issues: write" in text
    assert 'TAU_START_HANDOFF="experiments/goal-locked-subagents/proofs/ui-handoff-command-loop-20260628T125900Z/start-handoff.json"' in text
    assert 'TAU_APPLY_GITHUB_TRANSPORT="false"' in text
    assert "uv run tau handoff-command-loop" in text
    assert "handoff-command-loop-github-transport" in text
    assert "GH_TOKEN: ${{ github.token }}" in text
    assert "TAU_APPLY_GITHUB_TRANSPORT" in text
    assert "transport_args+=(--apply)" in text
    assert "--start \"$TAU_START_HANDOFF\"" in text
    assert "--active-goal-hash \"$TAU_ACTIVE_GOAL_HASH\"" in text
    assert "--max-steps \"$TAU_MAX_STEPS\"" in text
    assert "experiments/goal-locked-subagents/agent-command-specs" in text
    assert "actions/upload-artifact@v4" in text
