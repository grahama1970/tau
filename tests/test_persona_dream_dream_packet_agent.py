import json
from pathlib import Path

from tau_coding.generated_ticket import validate_agent_handoff
from tau_coding.persona_dream_dream_packet_agent import (
    _pipeline_first_blocker,
    write_persona_dream_packet_loop_proof,
)


def test_dreamer_routes_are_valid_and_command_specs_exist() -> None:
    payload = {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "issue#41"},
        "goal": {"goal_id": "goal-41", "goal_version": 1, "goal_hash": "sha256:goal"},
        "previous_subagent": "human",
        "context": {"summary": "Start dream packet loop.", "artifacts": []},
        "result": {"status": "COMPLETED", "summary": "Start.", "evidence": []},
        "rationale": "Dreamer owns the first bounded creator turn.",
        "next_agent": {
            "name": "dreamer",
            "executor": "local",
            "reason": "Create or fail closed on dream_packet.json.",
        },
        "required_evidence": ["dreamer receipt"],
        "stop_condition": "Dreamer routes to dream-reviewer.",
    }
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok
    payload["next_agent"]["name"] = "dream-reviewer"
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok

    root = Path("experiments/goal-locked-subagents/agent-command-specs")
    assert (root / "dreamer" / "tau-dispatch-command.json").is_file()
    assert (root / "dream-reviewer" / "tau-dispatch-command.json").is_file()


def test_pipeline_first_blocker_accepts_nested_validation_shape() -> None:
    blocker = {"phase": "story_contract", "reason": "missing_artifact"}
    payload = {
        "schema": "persona_dream.pipeline_loop_status.v1",
        "validation": {"first_blocker": blocker},
    }
    assert _pipeline_first_blocker(payload) == blocker


def test_proof_writer_creates_start_handoff_without_running_agents(tmp_path, monkeypatch) -> None:
    work_order = tmp_path / "dream_packet_work_order.json"
    work_order.write_text(
        json.dumps(
            {
                "schema": "persona_dream.dream_packet_work_order.v1",
                "status": "WORK_ORDER_READY_DREAM_PACKET_REQUIRED",
            }
        ),
        encoding="utf-8",
    )

    class FakeLoop:
        def as_dict(self) -> dict:
            return {
                "ok": True,
                "status": "WAITING",
                "terminal_agent": "human",
                "stop_reason": "next_agent_is_human",
            }

    def fake_loop(*args, **kwargs) -> FakeLoop:
        return FakeLoop()

    monkeypatch.setattr(
        "tau_coding.handoff_dispatch.write_agent_handoff_command_loop_receipt",
        fake_loop,
    )
    manifest = write_persona_dream_packet_loop_proof(
        work_order=work_order,
        out_dir=tmp_path / "proof",
        active_goal_hash="sha256:goal",
    )
    start = json.loads((tmp_path / "proof" / "start-handoff.json").read_text())
    assert manifest["mocked"] is False
    assert start["next_agent"]["name"] == "dreamer"
    assert start["context"]["persona_dream_dream_packet"]["work_order"].endswith(
        "input_dream_packet_work_order.json"
    )
