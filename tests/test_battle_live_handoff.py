import json
from pathlib import Path

import httpx

from tau_coding.battle_live_handoff import (
    build_subagent_receipt,
    write_battle_live_handoff_proof,
)
from tau_coding.battle_scillm import classify_scillm_http_error
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


def test_battle_live_proof_can_emit_worker_handoffs_from_battle_receipts(tmp_path: Path) -> None:
    run_root = tmp_path / "battle-run"
    context_dir = run_root / "context"
    context_dir.mkdir(parents=True)
    bundle = context_dir / "tau-battle-context-bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "schema": "tau.battle_context_bundle.v1",
                "artifacts": {"warm_pond": str(context_dir / "warm-pond-receipt.json")},
                "summary": {
                    "teams": {
                        "red": {"persona": "brandon-bailey"},
                        "blue": {"persona": "coder"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    for team, persona, worker_ids in (
        ("red", "brandon-bailey", ("red-0-exploit-a", "red-1-exploit-b")),
        ("blue", "coder", ("blue-0-defense-a", "blue-1-defense-b")),
    ):
        worker_refs = []
        for index, worker_id in enumerate(worker_ids):
            combination_id = f"combo-{index}"
            worker_path = run_root / team / "workers" / worker_id / "worker-receipt.json"
            worker_path.parent.mkdir(parents=True)
            worker_path.write_text(
                json.dumps(
                    {
                        "schema": "battle.worker_receipt.v1",
                        "status": "PASS",
                        "team": team,
                        "worker_id": worker_id,
                        "combination_id": combination_id,
                        "persona": persona,
                        "model": "gpt-5.5",
                        "research_dispatch": {"research_boost": 0.2},
                        "outcome": {"ok": True},
                    }
                ),
                encoding="utf-8",
            )
            attempt_path = (
                run_root / "scorekeeper" / "replays" / combination_id / "attempt-receipt.json"
            )
            attempt_path.parent.mkdir(parents=True, exist_ok=True)
            attempt_path.write_text(
                json.dumps({"schema": "battle.scorekeeper_attempt_receipt.v1"}),
                encoding="utf-8",
            )
            worker_refs.append(str(worker_path.relative_to(run_root)))
        team_receipt = run_root / team / "team-receipt.json"
        team_receipt.write_text(
            json.dumps(
                {
                    "schema": "battle.team_receipt.v1",
                    "status": "PASS",
                    "worker_count": len(worker_refs),
                    "worker_receipts": worker_refs,
                }
            ),
            encoding="utf-8",
        )

    manifest = write_battle_live_handoff_proof(
        out_dir=run_root / "tau-live-worker",
        battle_id="battle-003",
        run_id="battle-run-1",
        scenario_id="battle-003",
        red_persona="brandon-bailey",
        blue_persona="coder",
        api_key="",
        battle_context_json=bundle,
        handoff_granularity="worker",
    )

    assert manifest["status"] == "BLOCKED"
    assert manifest["scheduling"]["granularity"] == "worker"
    assert manifest["scheduling"]["team_count"] == 2
    assert manifest["scheduling"]["handoff_count"] == 4
    assert manifest["scheduling"]["worker_count"] == 4
    handoff = json.loads(
        (
            run_root / "tau-live-worker" / "red" / "workers" / "red-0-exploit-a" / "handoff.json"
        ).read_text()
    )
    receipt = json.loads(
        (
            run_root
            / "tau-live-worker"
            / "blue"
            / "workers"
            / "blue-1-defense-b"
            / "tau-subagent-receipt.json"
        ).read_text()
    )

    assert handoff["context"]["worker_context"]["worker_id"] == "red-0-exploit-a"
    assert handoff["context"]["worker_context"]["combination_id"] == "combo-0"
    assert handoff["context"]["worker_context"]["research_dispatch"]["research_boost"] == 0.2
    assert receipt["context"]["battle"]["worker"]["worker_id"] == "blue-1-defense-b"
    assert any("attempt-receipt.json" in item for item in receipt["evidence"])


def test_battle_live_worker_fanout_backpressure_writes_structured_manifest(
    tmp_path: Path,
) -> None:
    run_root = _write_worker_context_bundle(tmp_path, worker_count_per_team=2)

    manifest = write_battle_live_handoff_proof(
        out_dir=run_root / "tau-live-backpressure",
        battle_id="battle-005",
        run_id="battle-run-1",
        scenario_id="generated-sqli-xss-warm-pond-001",
        red_persona="brandon-bailey",
        blue_persona="coder",
        api_key="unused",
        battle_context_json=run_root / "context" / "tau-battle-context-bundle.json",
        handoff_granularity="worker",
        max_live_handoffs=3,
    )

    assert manifest["status"] == "BACKPRESSURE"
    assert manifest["reason"] == "tau_live_handoff_backpressure"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["scheduling"]["handoff_count"] == 4
    assert manifest["scheduling"]["mode"] == "preflight_backpressure"
    assert manifest["backpressure"]["requested_handoff_count"] == 4
    assert manifest["backpressure"]["max_live_handoffs"] == 3
    assert manifest["backpressure"]["would_start_scillm_calls"] is False
    assert manifest["backpressure"]["error_family"] == "tau_live_backpressure"
    assert not (run_root / "tau-live-backpressure" / "red" / "workers").exists()


def test_scillm_timeout_errors_are_structured_and_nonblank() -> None:
    error = classify_scillm_http_error(httpx.ReadTimeout(""), timeout_s=90)

    assert error["error_family"] == "scillm_stream_timeout"
    assert error["error_type"] == "ReadTimeout"
    assert error["error_message"]
    assert error["error"] != "scillm_http_error: "
    assert error["backpressure_likely"] is True
    assert error["retryable"] is True


def _write_worker_context_bundle(tmp_path: Path, *, worker_count_per_team: int) -> Path:
    run_root = tmp_path / "battle-run"
    context_dir = run_root / "context"
    context_dir.mkdir(parents=True)
    bundle = context_dir / "tau-battle-context-bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "schema": "tau.battle_context_bundle.v1",
                "artifacts": {"warm_pond": str(context_dir / "warm-pond-receipt.json")},
                "summary": {
                    "teams": {
                        "red": {"persona": "brandon-bailey"},
                        "blue": {"persona": "coder"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    for team, persona, prefix in (
        ("red", "brandon-bailey", "exploit"),
        ("blue", "coder", "defense"),
    ):
        worker_refs = []
        for index in range(worker_count_per_team):
            worker_id = f"{team}-{index}-{prefix}-a"
            combination_id = f"combo-{index}"
            worker_path = run_root / team / "workers" / worker_id / "worker-receipt.json"
            worker_path.parent.mkdir(parents=True)
            worker_path.write_text(
                json.dumps(
                    {
                        "schema": "battle.worker_receipt.v1",
                        "status": "PASS",
                        "team": team,
                        "worker_id": worker_id,
                        "combination_id": combination_id,
                        "persona": persona,
                        "model": "gpt-5.5",
                        "outcome": {"ok": True},
                    }
                ),
                encoding="utf-8",
            )
            attempt_path = (
                run_root / "scorekeeper" / "replays" / combination_id / "attempt-receipt.json"
            )
            attempt_path.parent.mkdir(parents=True, exist_ok=True)
            attempt_path.write_text(
                json.dumps({"schema": "battle.scorekeeper_attempt_receipt.v1"}),
                encoding="utf-8",
            )
            worker_refs.append(str(worker_path.relative_to(run_root)))
        (run_root / team / "team-receipt.json").write_text(
            json.dumps(
                {
                    "schema": "battle.team_receipt.v1",
                    "status": "PASS",
                    "worker_count": len(worker_refs),
                    "worker_receipts": worker_refs,
                }
            ),
            encoding="utf-8",
        )
    return run_root
