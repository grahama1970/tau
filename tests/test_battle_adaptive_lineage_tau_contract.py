from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tau_coding import battle_live_handoff as handoff
from tau_coding.battle_adaptive_lineage_compatibility import verify_pair


def test_pressure_backed_parent_decision_materializes_causal_red_child(
    tmp_path: Path,
    monkeypatch,
) -> None:
    parent_path, parent = _parent_receipt(tmp_path)
    pressure_path, pressure = _pressure_receipt(tmp_path)

    def fake_json_task(**kwargs: Any) -> dict[str, Any]:
        task = kwargs["task"]
        return {
            "status": "PASS",
            "parsed_json": {
                "decision": "strategic_pre_kill",
                "reason_codes": ["defender_pressure_before_parent_terminal"],
                "inherited_evidence_receipt_ids": task[
                    "required_inherited_evidence_receipt_ids"
                ],
            },
        }

    monkeypatch.setattr(handoff, "call_battle_json_task", fake_json_task)
    decision = handoff._run_parent_spawn_decision(
        out_dir=tmp_path,
        battle_id="battle-004",
        run_id="adaptive-lineage-test",
        scenario_id="zip-slip",
        persona="battle-red",
        model="test-model",
        scillm_base_url="http://127.0.0.1:4001",
        timeout_s=5,
        context={},
        parent_receipt=parent,
        pressure_receipt=pressure,
    )
    decision_path = tmp_path / "spawn-decision-receipt.json"
    handoff._write_json(decision_path, decision)
    decision["receipt_path"] = str(decision_path)

    assert decision["status"] == "PASS"
    assert decision["decision"] == "strategic_pre_kill"
    assert {item["kind"] for item in decision["inherited_evidence_refs"]} == {
        "parent_pressure",
        "parent_tau_subagent",
        "parent_materialized_artifact",
    }
    assert handoff._prekill_spawn_input_errors(
        pressure_receipt=pressure,
        decision_receipt=decision,
    ) == []

    monkeypatch.setattr(
        handoff,
        "call_battle_subagent",
        lambda **_: {
            "status": "PASS",
            "http_status": 200,
            "parsed_json": _red_artifact(),
        },
    )
    child = handoff._run_spawned_red_child(
        out_dir=tmp_path,
        battle_id="battle-004",
        run_id="adaptive-lineage-test",
        scenario_id="zip-slip",
        persona="battle-red-variant-1",
        model="test-model",
        scillm_base_url="http://127.0.0.1:4001",
        timeout_s=5,
        context={},
        parent_receipt=parent,
        worker_id="red-1",
        worker_index=1,
        lane_id="payload-857-red-1",
        pressure_receipt=pressure,
        spawn_decision_receipt=decision,
    )
    spawn = handoff._write_spawn_receipt(
        out_dir=tmp_path,
        parent_receipt=parent,
        child=child,
        battle_id="battle-004",
        run_id="adaptive-lineage-test",
    )["spawn"]

    assert child["status"] == "PASS"
    assert child["worker_id"] == "red-1"
    assert child["parent_lane_id"] == "payload-857-receipt"
    assert child["spawn_decision_receipt_id"] == decision["receipt_id"]
    assert child["inherited_evidence_refs"] == decision["inherited_evidence_refs"]
    assert spawn["spawn_type"] == "strategic_pre_kill"
    assert spawn["spawn_decision_receipt_id"] == decision["receipt_id"]
    assert spawn["inherited_evidence_refs"] == decision["inherited_evidence_refs"]


def test_spawn_rejects_tampered_pressure_binding(tmp_path: Path) -> None:
    pressure_path, pressure = _pressure_receipt(tmp_path)
    decision = {
        "status": "PASS",
        "decision": "strategic_pre_kill",
        "author": {"team": "red", "worker_id": "red-0"},
        "pressure_receipt": {
            "receipt_id": pressure["receipt_id"],
            "sha256": "0" * 64,
        },
        "inherited_evidence_refs": [
            {"kind": "parent_pressure", "receipt_id": "pressure", "sha256": "1" * 64},
            {
                "kind": "parent_tau_subagent",
                "receipt_id": "parent",
                "sha256": "2" * 64,
            },
            {
                "kind": "parent_materialized_artifact",
                "receipt_id": "artifact",
                "sha256": "3" * 64,
            },
        ],
    }

    errors = handoff._prekill_spawn_input_errors(
        pressure_receipt=pressure,
        decision_receipt=decision,
    )

    assert pressure_path.exists()
    assert "spawn-decision receipt cites the wrong pressure hash" in errors


def test_adaptive_lineage_cli_surface_accepts_locked_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "battle-live-handoff",
            "--out-dir",
            "/tmp/out",
            "--battle-id",
            "battle-004",
            "--run-id",
            "run",
            "--scenario-id",
            "zip-slip",
            "--red-persona",
            "red",
            "--blue-persona",
            "blue",
            "--model",
            "model",
            "--scillm-base-url",
            "http://127.0.0.1:4001",
            "--red-workers",
            "1",
            "--blue-workers",
            "0",
            "--spawn-red-child",
            "--parent-subagent-receipt",
            "/tmp/parent.json",
            "--pressure-receipt",
            "/tmp/pressure.json",
            "--spawn-decision-receipt",
            "/tmp/decision.json",
        ],
    )

    args = handoff._parse_args()

    assert args.spawn_red_child is True
    assert args.pressure_receipt == Path("/tmp/pressure.json")
    assert args.spawn_decision_receipt == Path("/tmp/decision.json")


def test_pair_compatibility_uses_actual_worktree_commits(tmp_path: Path) -> None:
    tau_root = tmp_path / "tau"
    agent_skills_root = tmp_path / "agent-skills"
    tau_source = tau_root / "src" / "tau_coding" / "battle_live_handoff.py"
    battle_source = (
        agent_skills_root
        / "skills"
        / "battle"
        / "src"
        / "battle_skill"
        / "arena_live_battle_proof.py"
    )
    tau_source.parent.mkdir(parents=True)
    battle_source.parent.mkdir(parents=True)
    tau_source.write_text(
        "# ADAPTIVE_LINEAGE_GATE_V2\n"
        'FLAGS = ["--parent-spawn-decision", "--pressure-receipt", '
        '"--spawn-decision-receipt"]\n',
        encoding="utf-8",
    )
    battle_source.write_text(tau_source.read_text(encoding="utf-8"), encoding="utf-8")
    for root in (tau_root, agent_skills_root):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Tau",
                "-c",
                "user.email=tau@example.test",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )

    payload = verify_pair(tau_root=tau_root, agent_skills_root=agent_skills_root)

    assert payload["status"] == "PASS"
    assert payload["compatible_pair"]["tau"]
    assert payload["compatible_pair"]["agent_skills"]
    assert set(payload["checks"].values()) == {True}


def _parent_receipt(root: Path) -> tuple[Path, dict[str, Any]]:
    materialized_path = root / "red" / "materialized-artifact-receipt.json"
    materialized_path.parent.mkdir(parents=True)
    materialized_path.write_text(
        json.dumps({"status": "PASS", "path": str(root / "red" / "red.py")}) + "\n",
        encoding="utf-8",
    )
    path = root / "red" / "tau-subagent-receipt.json"
    payload = {
        "context": {
            "battle": {
                "team": "red",
                "worker_id": "red-0",
                "lane_id": "payload-857-receipt",
            }
        },
        "result": {"artifacts": [str(materialized_path)]},
        "receipt_path": str(path),
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path, payload


def _pressure_receipt(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / "prekill-pressure-receipt.json"
    payload = {
        "schema": "battle.prekill_parent_pressure_receipt.v1",
        "status": "PASS",
        "receipt_id": "prekill-pressure:red-0__blue-0",
        "parent_worker_id": "red-0",
        "parent_lane_id": "payload-857-receipt",
        "parent_terminal_confirmed": False,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    payload["receipt_path"] = str(path)
    return path, payload


def _red_artifact() -> dict[str, Any]:
    return {
        "artifact_type": "red_exploit",
        "exploit_py": (
            "from app import import_zip\n"
            "import_zip('payload.zip', 'destination')\n"
            "print('RED_EXPLOIT_CONFIRMED')\n"
            "# --expect-vulnerable\n"
        ),
        "strategy_genome": {
            "selected_methods": ["inherit-parent-pressure"],
            "rejected_methods": [],
            "parameters": {},
            "mutation_origin": "parent-decision",
            "expected_observation": "Judge executes red-1 after the parent terminal receipt.",
        },
    }
