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


def test_battle_live_proof_preserves_empty_artifacts_without_context(tmp_path: Path) -> None:
    write_battle_live_handoff_proof(
        out_dir=tmp_path,
        battle_id="battle-003",
        run_id="battle-run-1",
        scenario_id="battle-003",
        red_persona="brandon-bailey",
        blue_persona="coder",
        api_key="",
    )

    red_handoff = json.loads((tmp_path / "red" / "handoff.json").read_text())
    blue_handoff = json.loads((tmp_path / "blue" / "handoff.json").read_text())

    assert red_handoff["context"]["artifacts"] == []
    assert red_handoff["context"]["battle_context"] is None
    assert blue_handoff["context"]["artifacts"] == []
    assert blue_handoff["context"]["battle_context"] is None


def test_battle_live_proof_passes_artifact_backed_context_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "battle-context.json"
    bundle.write_text(
        json.dumps(
            {
                "schema": "tau.battle_context_bundle.v1",
                "artifacts": {
                    "run_receipt": "/tmp/battle/run-receipt.json",
                    "fast_scan": "/tmp/battle/context/fast-scan-receipt.json",
                    "research_broker": "/tmp/battle/context/research-broker-receipt.json",
                    "warm_pond": "/tmp/battle/context/warm-pond-receipt.json",
                },
                "summary": {
                    "run_receipt": {"status": "PASS"},
                    "tau_live_manifest": {"status": "PASS"},
                    "research_broker": {
                        "status": "PASS",
                        "passed_lane_count": 5,
                    },
                    "warm_pond": {
                        "status": "PASS",
                        "research_weighted_candidate_count": 6,
                    },
                    "teams": {
                        "red": {
                            "persona": "brandon-bailey",
                            "research_dispatch": {"research_boost": 0.2},
                        },
                        "blue": {
                            "persona": "coder",
                            "research_dispatch": {"research_boost": 0.2},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    manifest = write_battle_live_handoff_proof(
        out_dir=tmp_path / "tau-live",
        battle_id="battle-003",
        run_id="battle-run-1",
        scenario_id="battle-003",
        red_persona="brandon-bailey",
        blue_persona="coder",
        api_key="",
        battle_context_json=bundle,
    )

    red_handoff = json.loads((tmp_path / "tau-live" / "red" / "handoff.json").read_text())
    blue_handoff = json.loads((tmp_path / "tau-live" / "blue" / "handoff.json").read_text())
    red_context = red_handoff["context"]["battle_context"]
    blue_context = blue_handoff["context"]["battle_context"]

    assert manifest["battle_context"]["research_broker_passed_lane_count"] == 5
    assert manifest["battle_context"]["warm_pond_research_weighted_candidate_count"] == 6
    assert str(bundle.resolve()) in red_handoff["context"]["artifacts"]
    assert "/tmp/battle/context/research-broker-receipt.json" in red_handoff["context"]["artifacts"]
    assert red_context["research_broker_passed_lane_count"] == 5
    assert red_context["warm_pond_research_weighted_candidate_count"] == 6
    assert red_context["team_summary"]["research_dispatch"]["research_boost"] == 0.2
    assert blue_context["team_summary"]["research_dispatch"]["research_boost"] == 0.2
