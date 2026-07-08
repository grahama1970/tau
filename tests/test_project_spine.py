import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.project_spine import (
    PROJECT_SPINE_CHECK_RECEIPT_SCHEMA,
    PROJECT_SPINE_SCHEMA,
    check_project_spine,
)


def test_project_spine_passes_clean_revision_bound_state() -> None:
    receipt = check_project_spine(_clean_spine())

    assert receipt["schema"] == PROJECT_SPINE_CHECK_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["defect_count"] == 0
    assert receipt["course_correction_count"] == 0
    assert receipt["live"] is True
    assert receipt["provider_live"] is False


def test_project_spine_blocks_stale_lineage_with_course_correction() -> None:
    spine = _clean_spine()
    spine["artifact_lineage_index"][0]["revision_id"] = "rev-001"

    receipt = check_project_spine(spine)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["defects"][0]["trigger"] == "stale_lineage"
    correction = receipt["course_corrections"][0]
    assert correction["schema"] == "tau.course_correction.v1"
    assert correction["trigger"] == "stale_lineage"
    assert correction["required_next_action"] == "recompute_replan_plan"
    assert "promote_stale_artifact" in correction["forbidden_next_routes"]


def test_project_spine_blocks_false_progress_from_owner_state() -> None:
    spine = _clean_spine()
    spine["local_progress"]["reported_percent"] = 100
    spine["local_progress"]["derived_percent"] = 50

    receipt = check_project_spine(spine)

    assert receipt["ok"] is False
    assert receipt["defects"][0]["trigger"] == "false_progress"
    correction = receipt["course_corrections"][0]
    assert correction["trigger"] == "false_progress"
    assert correction["required_next_action"] == "derive_progress_from_receipts"
    assert "claim_ready_from_owner_state" in correction["forbidden_next_routes"]


def test_project_spine_blocks_forbidden_side_effect_without_final_gate() -> None:
    spine = _clean_spine()
    spine["side_effects"] = [
        {
            "side_effect_id": "provider-call-001",
            "kind": "paid_provider_call",
            "status": "requested",
            "final_gate": {"status": "DRY_RUN_NOT_LIVE_SUBMITTABLE"},
        }
    ]

    receipt = check_project_spine(spine)

    assert receipt["ok"] is False
    assert receipt["defects"][0]["trigger"] == "forbidden_side_effect"
    correction = receipt["course_corrections"][0]
    assert correction["trigger"] == "forbidden_side_effect"
    assert correction["required_next_action"] == "block_side_effect_route_human"
    assert "execute_side_effect" in correction["forbidden_next_routes"]


def test_project_spine_allows_provider_ready_side_effect() -> None:
    spine = _clean_spine()
    spine["side_effects"] = [
        {
            "side_effect_id": "provider-call-001",
            "kind": "paid_provider_call",
            "status": "requested",
            "final_gate": {"status": "PROVIDER_READY"},
        }
    ]

    receipt = check_project_spine(spine)

    assert receipt["status"] == "PASS"
    assert receipt["defect_count"] == 0


def test_project_spine_cli_writes_receipt(tmp_path: Path) -> None:
    spine_path = tmp_path / "project-spine.json"
    out = tmp_path / "project-spine-check-receipt.json"
    spine_path.write_text(json.dumps(_clean_spine()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "project",
            "check-spine",
            "--spine",
            str(spine_path),
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "PASS"
    assert json.loads(out.read_text(encoding="utf-8")) == payload
    assert payload["source_project_spine"] == str(spine_path.resolve())
    assert payload["source_project_spine_sha256"].startswith("sha256:")


def test_project_spine_cli_exits_nonzero_for_blocked_spine(tmp_path: Path) -> None:
    spine = _clean_spine()
    spine["active_work_queue"][0]["revision_id"] = "rev-001"
    spine_path = tmp_path / "project-spine.json"
    out = tmp_path / "project-spine-check-receipt.json"
    spine_path.write_text(json.dumps(spine), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "project",
            "check-spine",
            "--spine",
            str(spine_path),
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "BLOCKED"
    assert payload["course_corrections"][0]["trigger"] == "stale_lineage"
    assert out.exists()


def _clean_spine() -> dict:
    return {
        "schema": PROJECT_SPINE_SCHEMA,
        "project_id": "tau-persona-dream-transfer",
        "run_id": "run-001",
        "dag_id": "dag-001",
        "goal": {
            "goal_id": "goal-001",
            "active_revision_id": "rev-002",
            "goal_hash": "sha256:goal",
        },
        "change_events": [
            {"event_id": "change-001", "status": "applied"},
        ],
        "artifact_lineage_index": [
            {
                "artifact_id": "storyboard-panel-001",
                "revision_id": "rev-002",
                "depends_on_change_events": ["change-001"],
            },
        ],
        "active_work_queue": [
            {
                "work_id": "panel-review-001",
                "revision_id": "rev-002",
                "status": "done",
                "artifact_id": "storyboard-panel-001",
            },
        ],
        "work_lease_index": [],
        "accepted_evidence_index": [
            {
                "artifact_id": "storyboard-panel-001",
                "revision_id": "rev-002",
                "receipt_sha256": "sha256:panel",
            },
        ],
        "local_progress": {
            "reported_percent": 100,
            "derived_percent": 100,
            "source": "accepted_evidence_index",
        },
        "side_effects": [],
    }
