from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tau_coding.battle_live_handoff import (
    _context_for_team,
    _handoff,
    _materialize_team_artifact,
)


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


def test_team_context_projection_excludes_opposing_memory() -> None:
    context = {
        "summary": {
            "scenario": "arena-1",
            "teams": {
                "red": {"objective": "use red-memory"},
                "blue": {"objective": "use blue-memory"},
            },
        },
        "team_contexts": {
            "red": {"memory_key": "red-memory", "memory_sha256": "red-sha"},
            "blue": {"memory_key": "blue-memory", "memory_sha256": "blue-sha"},
        },
    }

    red = _context_for_team(context, "red")
    blue = _context_for_team(context, "blue")

    assert red["team_context"]["memory_key"] == "red-memory"
    assert blue["team_context"]["memory_key"] == "blue-memory"
    assert set(red["summary"]["teams"]) == {"red"}
    assert set(blue["summary"]["teams"]) == {"blue"}
    assert "team_contexts" not in red
    assert "team_contexts" not in blue
    assert "blue-memory" not in json.dumps(red)
    assert "red-memory" not in json.dumps(blue)


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


def test_red_materialization_accepts_exact_local_importlib_loader(tmp_path: Path) -> None:
    script = (
        "import importlib.util\n"
        "from pathlib import Path\n"
        "app_path = Path.cwd() / 'app.py'\n"
        "spec = importlib.util.spec_from_file_location('battle_local_app', str(app_path))\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "import_zip = module.import_zip\n"
        "import_zip('payload.zip', 'destination')\n"
        "print('RED_EXPLOIT_CONFIRMED')\n"
        "# --expect-vulnerable\n"
    )

    receipt = _materialize_team_artifact(
        team_dir=tmp_path,
        team="red",
        scillm_call={
            "parsed_json": {
                "artifact_type": "red_exploit",
                "exploit_py": script,
                "strategy_genome": _genome("red"),
            }
        },
    )

    assert receipt["status"] == "PASS"
    assert Path(receipt["path"]).read_text(encoding="utf-8") == script


def test_red_materialization_rejects_importlib_loader_for_other_path(
    tmp_path: Path,
) -> None:
    script = (
        "import importlib.util\n"
        "from pathlib import Path\n"
        "app_path = Path.cwd() / 'other.py'\n"
        "spec = importlib.util.spec_from_file_location('other', str(app_path))\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "import_zip = module.import_zip\n"
        "import_zip('payload.zip', 'destination')\n"
        "print('RED_EXPLOIT_CONFIRMED')\n"
        "# --expect-vulnerable\n"
    )

    receipt = _materialize_team_artifact(
        team_dir=tmp_path,
        team="red",
        scillm_call={
            "parsed_json": {
                "artifact_type": "red_exploit",
                "exploit_py": script,
                "strategy_genome": _genome("red"),
            }
        },
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "red_artifact_missing_local_app_import"


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
