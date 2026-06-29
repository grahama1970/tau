import json
from pathlib import Path

from tau_coding.battle_live_handoff import (
    build_subagent_receipt,
    write_battle_live_handoff_proof,
)
from tau_coding.subagent_receipt import validate_subagent_receipt


def test_battle_live_subagent_receipt_validates_for_pass() -> None:
    goal = {
        "goal_id": "goal-battle-test",
        "goal_version": 1,
        "goal_hash": "sha256:active-goal",
    }
    receipt = build_subagent_receipt(
        goal=goal,
        run_id="run-1",
        battle_id="battle-003",
        scenario_id="battle-003",
        team="red",
        persona="brandon-bailey",
        scillm_call={
            "schema": "tau.battle_scillm_call_receipt.v1",
            "status": "PASS",
            "model": "text",
        },
        artifacts=["red/handoff.json", "red/scillm-call-receipt.json"],
    )

    result = validate_subagent_receipt(receipt, active_goal_hash="sha256:active-goal")

    assert result.ok is True
    assert result.next_subagent == "battle-scorekeeper"
    assert receipt["result"]["mocked"] is False
    assert receipt["result"]["live"] is True


def test_battle_live_proof_writes_blocked_receipts_without_scillm_key(tmp_path: Path) -> None:
    manifest = write_battle_live_handoff_proof(
        out_dir=tmp_path,
        battle_id="battle-003",
        run_id="battle-run-1",
        scenario_id="battle-003",
        red_persona="brandon-bailey",
        blue_persona="coder",
        api_key="",
    )

    assert manifest["schema"] == "tau.battle_live_handoff_proof.v1"
    assert manifest["status"] == "BLOCKED"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["scheduling"]["mode"] == "asyncio.as_completed"
    assert {item["team"] for item in manifest["scheduling"]["completion_order"]} == {
        "red",
        "blue",
    }
    assert {team["team"] for team in manifest["teams"]} == {"red", "blue"}

    for team in ("red", "blue"):
        receipt = json.loads((tmp_path / team / "tau-subagent-receipt.json").read_text())
        validation = validate_subagent_receipt(
            receipt,
            active_goal_hash=receipt["goal"]["goal_hash"],
        )
        assert validation.ok is True
        assert receipt["result"]["status"] == "BLOCKED"
        assert receipt["next"]["subagent"] == "human"
