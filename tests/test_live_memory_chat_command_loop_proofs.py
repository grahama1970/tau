import json
from pathlib import Path


PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / "experiments/goal-locked-subagents/proofs/live-memory-chat-command-loop-20260628T013609Z"
)


def test_live_memory_chat_command_loop_proof_manifest_matches_raw_receipts() -> None:
    manifest_path = PROOF_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema"] == "tau.live_memory_chat_command_loop_proof_manifest.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert [route["route"] for route in manifest["routes"]] == [
        "ANSWER",
        "COMPLIANCE",
        "RESEARCH",
    ]

    for route in manifest["routes"]:
        start = json.loads((PROOF_DIR / f"{route['route'].lower()}-start-handoff.json").read_text(encoding="utf-8"))
        receipt = json.loads(
            (PROOF_DIR / f"{route['route'].lower()}-command-loop-receipt.json").read_text(encoding="utf-8")
        )
        dispatch = receipt["dispatches"][0]

        assert route["start_next_agent"] == start["next_agent"]["name"]
        assert route["selected_agent"] == dispatch["selected_agent"]
        assert route["command_exit"] == dispatch["command_results"][0]["exit_code"]
        assert receipt["schema"] == "tau.agent_handoff_command_loop_receipt.v1"
        assert receipt["ok"] is True
        assert receipt["mocked"] is False
        assert receipt["live"] is True
        assert receipt["step_count"] == 1
        assert receipt["terminal_agent"] == "human"
        assert receipt["stop_reason"] == "next_agent_is_human"
