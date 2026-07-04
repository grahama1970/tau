import json
from pathlib import Path

from tau_coding.generated_ticket import validate_agent_handoff
from tau_coding.persona_dream_dream_packet_agent import (
    _pipeline_first_blocker,
    write_persona_dream_packet_loop_proof,
    write_persona_dream_script_contract_loop_proof,
    write_persona_dream_story_contract_loop_proof,
    write_persona_dream_storyboard_panel_loop_proof,
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
    payload["next_agent"]["name"] = "story-writer"
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok
    payload["next_agent"]["name"] = "story-reviewer"
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok
    payload["next_agent"]["name"] = "script-writer"
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok
    payload["next_agent"]["name"] = "script-reviewer"
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok
    payload["next_agent"]["name"] = "storyboard-writer"
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok
    payload["next_agent"]["name"] = "storyboard-reviewer"
    assert validate_agent_handoff(payload, active_goal_hash="sha256:goal").ok

    root = Path("experiments/goal-locked-subagents/agent-command-specs")
    assert (root / "dreamer" / "tau-dispatch-command.json").is_file()
    assert (root / "dream-reviewer" / "tau-dispatch-command.json").is_file()
    assert (root / "story-writer" / "tau-dispatch-command.json").is_file()
    assert (root / "story-reviewer" / "tau-dispatch-command.json").is_file()
    assert (root / "script-writer" / "tau-dispatch-command.json").is_file()
    assert (root / "script-reviewer" / "tau-dispatch-command.json").is_file()
    assert (root / "storyboard-writer" / "tau-dispatch-command.json").is_file()
    assert (root / "storyboard-reviewer" / "tau-dispatch-command.json").is_file()


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


def test_story_proof_writer_creates_start_handoff_without_running_agents(
    tmp_path,
    monkeypatch,
) -> None:
    run_root = tmp_path / "dream-run"
    run_root.mkdir()
    dream_packet = run_root / "dream_packet.json"
    dream_packet.write_text(
        json.dumps({"schema": "persona_dream.packet.v1", "persona": "embry"}),
        encoding="utf-8",
    )
    work_order = tmp_path / "story_contract_work_order.json"
    work_order.write_text(
        json.dumps(
            {
                "schema": "persona_dream.story_contract_work_order.v1",
                "status": "WORK_ORDER_READY_STORY_REVIEW_REQUIRED",
                "source_paths": {
                    "run_root": str(run_root),
                    "dream_packet": str(dream_packet),
                },
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
    manifest = write_persona_dream_story_contract_loop_proof(
        work_order=work_order,
        out_dir=tmp_path / "proof",
        active_goal_hash="sha256:goal",
    )

    start = json.loads((tmp_path / "proof" / "start-handoff.json").read_text())
    assert manifest["mocked"] is False
    assert start["next_agent"]["name"] == "story-writer"
    assert start["context"]["persona_dream_story_contract"]["work_order"].endswith(
        "input_story_contract_work_order.json"
    )


def test_storyboard_proof_writer_creates_start_handoff_without_running_agents(
    tmp_path,
    monkeypatch,
) -> None:
    run_root = tmp_path / "dream-run"
    run_root.mkdir()
    story_contract = run_root / "story_contract.json"
    story_contract.write_text(
        json.dumps(
            {
                "schema": "persona_dream.story_contract.v1",
                "status": "ACCEPTED_AUTOMATED",
                "story": "Embry reviews evidence cards while tea steam crosses the SPARTA glow.",
                "target_duration_s": 7.5,
                "speaking_characters": ["Embry"],
            }
        ),
        encoding="utf-8",
    )
    work_order = tmp_path / "storyboard_panel_work_order.json"
    work_order.write_text(
        json.dumps(
            {
                "schema": "persona_dream.storyboard_panel_work_order.v1",
                "status": "WORK_ORDER_READY_STORYBOARD_PANEL_REQUIRED",
                "source_paths": {
                    "run_root": str(run_root),
                    "story_contract": str(story_contract),
                    "dream_packet": str(run_root / "dream_packet.json"),
                },
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
    manifest = write_persona_dream_storyboard_panel_loop_proof(
        work_order=work_order,
        out_dir=tmp_path / "proof",
        active_goal_hash="sha256:goal",
    )

    start = json.loads((tmp_path / "proof" / "start-handoff.json").read_text())
    assert manifest["mocked"] is False
    assert start["next_agent"]["name"] == "storyboard-writer"
    assert start["context"]["persona_dream_storyboard_panel"]["work_order"].endswith(
        "input_storyboard_panel_work_order.json"
    )


def test_script_proof_writer_creates_start_handoff_without_running_agents(
    tmp_path,
    monkeypatch,
) -> None:
    run_root = tmp_path / "dream-run"
    run_root.mkdir()
    prompt_payload = run_root / "prompt_payload.json"
    prompt_payload.write_text(
        json.dumps(
            {
                "schema": "persona_dream.script_prompt_payload.v1",
                "task": {"scene_count": 1, "target_pages": 1, "duration_seconds": 10},
                "source_context": {
                    "core_idea": "Embry and Kai surf Kahalu'u Bay on a hot Kona day.",
                    "story": "Embry waits for the right wave instead of cutting across locals.",
                    "interaction_matrix": [
                        {
                            "source_seed_id": "seed-embry",
                            "entity": "Embry",
                            "story_function": "Waits, chooses, and commits.",
                            "environment_interaction": "Heat and glare make fatigue visible.",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    work_order = tmp_path / "script_contract_work_order.json"
    work_order.write_text(
        json.dumps(
            {
                "schema": "persona_dream.script_contract_work_order.v1",
                "status": "WORK_ORDER_READY_SCRIPT_REQUIRED",
                "prompt_payload_path": str(prompt_payload),
                "source_paths": {
                    "run_root": str(run_root),
                    "prompt_payload": str(prompt_payload),
                },
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
    manifest = write_persona_dream_script_contract_loop_proof(
        work_order=work_order,
        out_dir=tmp_path / "proof",
        active_goal_hash="sha256:goal",
    )

    start = json.loads((tmp_path / "proof" / "start-handoff.json").read_text())
    assert manifest["mocked"] is False
    assert manifest["proof_scope"]["live"] is True
    assert start["next_agent"]["name"] == "script-writer"
    assert start["context"]["persona_dream_script_contract"]["work_order"].endswith(
        "input_script_contract_work_order.json"
    )
