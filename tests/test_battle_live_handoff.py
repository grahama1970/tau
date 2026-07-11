from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tau_coding.battle_live_handoff import _handoff, _materialize_team_artifact


def test_battle_handoff_requires_strategy_genome_for_both_teams() -> None:
    for team in ("red", "blue"):
        handoff = _handoff(
            battle_id="battle-004",
            run_id="run-1",
            scenario_id="arena-1",
            team=team,
            persona=f"battle-{team}",
            context={"artifacts": {}, "summary": {}},
            worker_id=f"{team}-0",
            worker_index=0,
            lane_id=f"lane-{team}",
        )
        assert "strategy_genome" in handoff["instructions"]
        assert "selected_methods" in handoff["instructions"]


def test_red_materialization_declares_artifact_and_genome_hashes(tmp_path: Path) -> None:
    genome = _genome("red")
    script = (
        "from app import import_zip\n"
        "import argparse\n"
        "print('RED_EXPLOIT_CONFIRMED')\n"
        "import_zip('payload.zip', 'destination')\n"
        "# --expect-vulnerable\n"
    )
    receipt = _materialize_team_artifact(
        team_dir=tmp_path,
        team="red",
        scillm_call={
            "parsed_json": {
                "artifact_type": "red_exploit",
                "exploit_py": script,
                "strategy_genome": genome,
                "rationale": "bounded test",
            }
        },
    )

    path = Path(receipt["path"])
    assert receipt["status"] == "PASS"
    assert receipt["artifact_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["artifact_bytes"] == path.stat().st_size
    assert receipt["strategy_genome_sha256"] == _json_sha(genome)


def test_blue_materialization_declares_artifact_and_genome_hashes(tmp_path: Path) -> None:
    genome = _genome("blue")
    receipt = _materialize_team_artifact(
        team_dir=tmp_path,
        team="blue",
        scillm_call={
            "parsed_json": {
                "artifact_type": "blue_patch",
                "app_py": "def import_zip(zip_path, destination):\n    return []\n",
                "strategy_genome": genome,
                "rationale": "bounded test",
            }
        },
    )

    path = Path(receipt["path"])
    assert receipt["status"] == "PASS"
    assert receipt["artifact_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["artifact_bytes"] == path.stat().st_size
    assert receipt["strategy_genome_sha256"] == _json_sha(genome)


def test_materialization_rejects_syntax_without_host_compile(tmp_path: Path) -> None:
    receipt = _materialize_team_artifact(
        team_dir=tmp_path,
        team="blue",
        scillm_call={
            "parsed_json": {
                "artifact_type": "blue_patch",
                "app_py": "def import_zip(\n",
                "strategy_genome": _genome("blue"),
            }
        },
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "blue_app_py_syntax_invalid"


def _genome(team: str) -> dict[str, object]:
    return {
        "selected_methods": [f"{team}-method"],
        "rejected_methods": [],
        "parameters": {},
        "mutation_origin": "provider",
        "expected_observation": "Judge evaluates the artifact.",
    }


def _json_sha(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
