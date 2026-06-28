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
        "RESEARCH_AUTHORIZED_RECEIPT",
    ]
    assert {
        route["route"]: route["response_result_status"] for route in manifest["routes"]
    } == {
        "ANSWER": "COMPLETED",
        "COMPLIANCE": "COMPLETED",
        "RESEARCH": "REFUSED",
        "RESEARCH_AUTHORIZED_RECEIPT": "COMPLETED",
    }

    for route in manifest["routes"]:
        start = json.loads((PROOF_DIR / Path(route["start_handoff"]).name).read_text(encoding="utf-8"))
        receipt = json.loads(
            (PROOF_DIR / Path(route["command_loop_receipt"]).name).read_text(encoding="utf-8")
        )
        dispatch = receipt["dispatches"][0]

        assert route["start_next_agent"] == start["next_agent"]["name"]
        assert receipt["schema"] == "tau.agent_handoff_command_loop_receipt.v1"
        assert receipt["ok"] is True
        assert receipt["mocked"] is False
        assert receipt["live"] is True
        assert receipt["step_count"] == route["step_count"]
        assert receipt["terminal_agent"] == "human"
        assert receipt["stop_reason"] == "next_agent_is_human"

        if route["route"] == "RESEARCH_AUTHORIZED_RECEIPT":
            assert route["selected_agents"] == [
                dispatch["selected_agent"],
                receipt["dispatches"][1]["selected_agent"],
            ]
            assert route["command_exits"] == [
                step["command_results"][0]["exit_code"] for step in receipt["dispatches"]
            ]
            assert route["external_research_receipt_live"] is False
            receipt_path = Path(__file__).resolve().parents[1] / route["external_research_receipt"]
            research_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            assert research_receipt["schema"] == "tau.external_research_receipt.v1"
            assert research_receipt["method"] == "brave-search"
        else:
            assert route["selected_agent"] == dispatch["selected_agent"]
            assert route["command_exit"] == dispatch["command_results"][0]["exit_code"]
            assert receipt["step_count"] == 1
