"""gs001-closure-audit packaged workflow.

Tau judges PDF Lab GS001 artifacts deterministically: validate schemas
(fail closed), recompute the closure verdict, project dry-run defect
tickets fingerprinted by defect_key, and publish a hash-bound closure
report. No network, no provider, no mutation.
"""

import hashlib
import json
from pathlib import Path

from tau_coding.workflows.runner import run_gs001_closure_audit_workflow


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_artifacts(base: Path, *, closed: bool = True, goal_pending: bool = False) -> dict:
    base.mkdir(parents=True, exist_ok=True)
    comparison = {
        "schema_version": "pdf-lab.comparison.v2",
        "passed": closed,
        "blockers": [] if closed else ["unwaived_extras:2"],
        "defect_vector": {
            "matched_expected": 11,
            "missing_expected": 0,
            "ambiguous_expected": 0,
            "unwaived_extras": 0 if closed else 2,
            "waived_extras": 3,
            "type_mismatches": 0,
        },
    }
    backlog = {
        "schema_version": "pdf_lab.second_pass_backlog.v1",
        "finding_count": 2,
        "backlog_count": 1,
        "entries": [
            {
                "defect_key": _sha("defect-1"),
                "observation_id": _sha("obs-1"),
                "finding_id": "f1",
                "page": 12,
                "kind": "table_false_positive",
                "classification": "page_frame_false_positive",
                "target_id": "actual:p12:table:0",
                "reason": "near page frame with sparse pseudo-columns",
                "recommended_engine_fix": "tighten frame filter",
                "proposed_owner_layer": "pdf_oxide_core_bug",
            }
        ],
    }
    triage = {
        "schema_version": "pdf-lab.human-triage-queue.v1",
        "task_count": 0,
        "agent_resolved_findings": [{"finding_id": "f1"}, {"finding_id": "f2"}],
    }
    contract = {
        "schema_version": "pdf_lab.golden_slice_expected.v3",
        "contract_status": "locked",
        "expected_elements": [
            {"id": "r1", "page": 27, "type": "chapter_label", "text": "CHAPTER ONE", "bbox": [0, 0, 1, 1]}
        ],
    }
    goal_lines = [
        "# Goal",
        "",
        "## Goal-lock pins",
        "",
        "- `goal_id`: PDF-EXTRACTION-GS001-TAU-V1",
        "- `goal_version`: PENDING_LOCK" if goal_pending else "- `goal_version`: 1",
        "",
        "## End",
    ]
    paths = {
        "comparison_json": base / "comparison.json",
        "backlog_json": base / "backlog.json",
        "triage_queue_json": base / "triage.json",
        "expected_contract_json": base / "contract.json",
        "goal_md": base / "GOAL.md",
    }
    paths["comparison_json"].write_text(json.dumps(comparison), encoding="utf-8")
    paths["backlog_json"].write_text(json.dumps(backlog), encoding="utf-8")
    paths["triage_queue_json"].write_text(json.dumps(triage), encoding="utf-8")
    paths["expected_contract_json"].write_text(json.dumps(contract), encoding="utf-8")
    paths["goal_md"].write_text("\n".join(goal_lines) + "\n", encoding="utf-8")
    return paths


def _run(paths: dict, run_dir: Path) -> dict:
    return run_gs001_closure_audit_workflow(
        comparison_json=paths["comparison_json"],
        backlog_json=paths["backlog_json"],
        triage_queue_json=paths["triage_queue_json"],
        expected_contract_json=paths["expected_contract_json"],
        goal_md=paths["goal_md"],
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
    )


def test_closed_state_publishes_closed_report_with_dry_run_tickets(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path / "artifacts", closed=True)
    run_dir = tmp_path / "run"

    receipt = _run(paths, run_dir)

    assert receipt["ok"] is True
    report = json.loads(
        (run_dir / "results" / "gs001-closure-report.json").read_text(encoding="utf-8")
    )
    assert report["schema"] == "tau.gs001_closure_report.v1"
    assert report["closed"] is True
    assert report["blockers"] == []
    assert report["ticket_count"] == 1
    assert report["dry_run_tickets_only"] is True
    assert report["semantic_truth"] == "NOT_CLAIMED"

    projection = json.loads(
        (run_dir / "intermediate" / "gs001-ticket-projection.json").read_text(
            encoding="utf-8"
        )
    )
    ticket = projection["tickets"][0]
    assert ticket["schema"] == "tau.generated_ticket.v1"
    assert ticket["apply"] is False
    assert ticket["dedup"]["defect_key"] == _sha("defect-1")
    assert "next:coder" in ticket["labels"]

    run_receipt = json.loads((run_dir / "run-receipt.json").read_text(encoding="utf-8"))
    node_ids = [node["node_id"] for node in run_receipt["nodes"]]
    assert node_ids == [
        "validate-artifacts",
        "verdict-closure",
        "project-tickets",
        "publish-closure",
    ]
    goal_hash = receipt["goal"]["goal_hash"]
    assert all(node["goal_hash"] == goal_hash for node in run_receipt["nodes"])
    assert (run_dir / "results" / "gs001-closure-report.md").is_file()


def test_open_defects_and_pending_goal_produce_not_closed_verdict(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path / "artifacts", closed=False, goal_pending=True)
    run_dir = tmp_path / "run"

    receipt = _run(paths, run_dir)

    assert receipt["ok"] is True, "audit completes; NOT_CLOSED is a verdict, not a failure"
    report = json.loads(
        (run_dir / "results" / "gs001-closure-report.json").read_text(encoding="utf-8")
    )
    assert report["closed"] is False
    assert any(b.startswith("comparison:") for b in report["blockers"])
    assert any(b.startswith("goal_not_locked:") for b in report["blockers"])


def test_missing_artifact_blocks_validation_and_never_publishes(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path / "artifacts", closed=True)
    paths["backlog_json"].unlink()
    run_dir = tmp_path / "run"

    receipt = _run(paths, run_dir)

    assert receipt["ok"] is False
    run_receipt = json.loads((run_dir / "run-receipt.json").read_text(encoding="utf-8"))
    validator = run_receipt["nodes"][0]
    assert validator["node_id"] == "validate-artifacts"
    assert validator["status"] == "BLOCKED"
    assert any("backlog_json" in error for error in validator["errors"])
    assert not (run_dir / "results" / "gs001-closure-report.json").exists()
