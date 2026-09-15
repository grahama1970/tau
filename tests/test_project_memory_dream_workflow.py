"""Focused scheduler/replay/recovery tests for the packaged project-memory-dream workflow.

Every test drives the real packaged command (``examples/project-memory-dream/
workflow.py run``) through the canonical Tau scheduler over deterministic local
fixture workspaces, then reads back the produced artifacts. No mocks: failures
are injected as workspace data and verified from terminal rows, receipts, and
#318 validation readbacks.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "project-memory-dream"
WORKFLOW = EXAMPLE_DIR / "workflow.py"


def _fixture_module():
    spec = importlib.util.spec_from_file_location(
        "project_memory_dream_fixture_workspace", EXAMPLE_DIR / "fixture_workspace.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fw = _fixture_module()


def _run_workflow(config: Path, run_dir: Path, timeout: int = 240) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            str(WORKFLOW),
            "run",
            "--config",
            str(config),
            "--run-dir",
            str(run_dir),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert completed.returncode in (0, 1), completed.stderr
    payload = json.loads(completed.stdout[completed.stdout.index("{") :])
    assert payload["schema"] == "tau.workflow_run_receipt.v1"
    return payload


def _summary(run_dir: Path) -> dict:
    return json.loads((run_dir / "run-summary.json").read_text(encoding="utf-8"))


def _rows(run_dir: Path) -> dict[str, dict]:
    return {row["project_id"]: row for row in _summary(run_dir)["terminal_rows"]}


def _assert_receipts_validated(run_dir: Path) -> None:
    for path in sorted((run_dir / "validations").glob("*-validation-receipt.json")):
        validation = json.loads(path.read_text(encoding="utf-8"))
        assert validation["ok"] is True, (path.name, validation["errors"])
        assert validation["mocked"] is False


def test_two_eligible_projects_and_shadow_default(tmp_path: Path) -> None:
    """Matrix: two eligible projects + unchanged skip + join before packet + shadow head unchanged."""

    workspace = tmp_path / "workspace"
    pids = fw.default_workspace(workspace)
    config = fw.write_config(workspace, projects=pids)
    run_dir = tmp_path / "run"

    receipt = _run_workflow(config, run_dir)
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    rows = _rows(run_dir)
    assert rows["alpha"]["terminal_status"] == "SHADOW_STAGED"
    assert rows["beta"]["terminal_status"] == "SHADOW_STAGED"
    assert rows["alpha"]["receipt_path"] != rows["beta"]["receipt_path"]
    assert rows["gamma"]["terminal_status"] == "NO_CHANGE"
    assert rows["gamma"]["skip_reason"] == "unchanged_project"

    # independent terminal receipts validate under tau.project_dream_receipt.v1
    _assert_receipts_validated(run_dir)
    final_validation = json.loads(
        (run_dir / "validations" / "aggregate-validation-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert final_validation["ok"] is True

    # shadow default: no active head moved, zero accepted effects
    for pid in pids:
        head = json.loads(
            (workspace / "memory" / pid / "head.json").read_text(encoding="utf-8")
        )
        assert head["generation"] == 1
        assert head["candidate_digest"] is None
    for pid in ("alpha", "beta"):
        project_receipt = json.loads(
            Path(rows[pid]["receipt_path"]).read_text(encoding="utf-8")
        )
        assert project_receipt["accepted_effect_count"] == 0
        assert project_receipt["promotion_policy"]["decision"] == "shadow_only"

    # join before packet: scheduler events order join settlement before packet dispatch
    events = [
        json.loads(line[line.index("{") :])
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kinds = [(event.get("kind"), event.get("node_id")) for event in events]
    for pid in ("alpha", "beta"):
        join_settled = max(
            index
            for index, (kind, node) in enumerate(kinds)
            if node == f"join.{pid}" and kind == "node_receipt_validated"
        )
        packet_dispatched = min(
            index
            for index, (kind, node) in enumerate(kinds)
            if node == f"packet.{pid}" and kind == "node_dispatch"
        )
        assert join_settled < packet_dispatched, f"join.{pid} must settle before packet.{pid}"
        packet_output = json.loads(
            (run_dir / "node-receipts" / f"packet.{pid}.json").read_text(encoding="utf-8")
        )["accepted_output"]
        assert packet_output["evidence_packet"]["path"].endswith("evidence-packet.json")
        join_artifact = json.loads(
            (run_dir / "artifacts" / pid / "join" / "join.json").read_text(encoding="utf-8")
        )
        assert join_artifact["joined_before_packet"] is True
        assert set(join_artifact["joined_inputs"]) == {
            "transcript_corpus",
            "archival_compaction",
            "project_state",
            "code_ingest",
        }


def test_unchanged_project_skipped_with_typed_reason(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pids = fw.default_workspace(workspace)
    config = fw.write_config(workspace, projects=pids)
    run_dir = tmp_path / "run"
    _run_workflow(config, run_dir)
    decision = json.loads(
        (run_dir / "artifacts" / "gamma" / "delta" / "dispatch-decision.json").read_text(
            encoding="utf-8"
        )
    )
    assert decision["eligible"] is False
    assert decision["reasons"] == ["unchanged_project"]
    row = _rows(run_dir)["gamma"]
    assert row["terminal_status"] == "NO_CHANGE"
    assert row["skip_reason"] == "unchanged_project"
    assert row["candidate_digest"] is None
    assert not (run_dir / "artifacts" / "gamma" / "synthesize").exists()


def test_malformed_transcript_blocks_only_its_project(tmp_path: Path) -> None:
    """Matrix: malformed transcript blocks only its project; siblings keep terminals."""

    workspace = tmp_path / "workspace"
    fw.build_project(workspace, "alpha")
    fw.build_project(workspace, "beta")
    fw.corrupt_transcripts(workspace, "beta")
    notes = workspace / "projects" / "alpha" / "worktree" / "notes.md"
    notes.write_text(notes.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    config = fw.write_config(workspace, projects=["alpha", "beta"])
    run_dir = tmp_path / "run"

    receipt = _run_workflow(config, run_dir)
    assert receipt["status"] == "PASS"
    rows = _rows(run_dir)
    assert rows["beta"]["terminal_status"] == "BLOCKED_INPUT"
    assert rows["beta"]["first_failed_gate"] == "transcript_malformed"
    assert rows["alpha"]["terminal_status"] == "SHADOW_STAGED"
    _assert_receipts_validated(run_dir)
    # no model output for the blocked project
    assert not (run_dir / "artifacts" / "beta" / "synthesize").exists()


def test_state_and_ingest_failures_remain_distinct(tmp_path: Path) -> None:
    """Matrix: project-state failure and ingest failure stay distinct typed blockers."""

    workspace = tmp_path / "workspace"
    fw.build_project(workspace, "alpha", faults={"state": "fail"})
    fw.build_project(workspace, "beta", faults={"ingest": "fail"})
    for pid in ("alpha", "beta"):
        notes = workspace / "projects" / pid / "worktree" / "notes.md"
        notes.write_text(notes.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    config = fw.write_config(workspace, projects=["alpha", "beta"])
    run_dir = tmp_path / "run"

    receipt = _run_workflow(config, run_dir)
    assert receipt["status"] == "PASS"
    rows = _rows(run_dir)
    assert rows["alpha"]["first_failed_gate"] == "project_state_unavailable"
    assert rows["beta"]["first_failed_gate"] == "code_ingest_unavailable"
    assert rows["alpha"]["terminal_status"] == "BLOCKED_INPUT"
    assert rows["beta"]["terminal_status"] == "BLOCKED_INPUT"


def test_undeclared_capability_rejected_before_dispatch(tmp_path: Path) -> None:
    """Matrix: undeclared model capability fails closed at preflight; no model dispatch."""

    workspace = tmp_path / "workspace"
    pids = fw.default_workspace(workspace)
    config = fw.write_config(
        workspace,
        projects=pids,
        extra={"synthesis_uses_capabilities_extra": ["arangodb.write"]},
    )
    run_dir = tmp_path / "run"

    receipt = _run_workflow(config, run_dir)
    assert receipt["status"] == "BLOCKED"
    preflight = json.loads(
        (run_dir / "node-receipts" / "preflight.json").read_text(encoding="utf-8")
    )
    assert preflight["verdict"] == "BLOCKED"
    assert any("undeclared_capabilities" in error for error in preflight["errors"])
    assert any("arangodb.write" in error for error in preflight["errors"])
    dispatched = sorted((run_dir / "node-receipts").glob("synthesize.*"))
    assert dispatched == []
    assert not list((run_dir / "artifacts").glob("*/synthesize/raw-model-output.json"))


def test_restart_after_stage_does_not_duplicate(tmp_path: Path) -> None:
    """Matrix: restart after staging reuses durable receipts; no duplicate candidates."""

    workspace = tmp_path / "workspace"
    pids = fw.default_workspace(workspace)
    config = fw.write_config(workspace, projects=pids)
    run_dir = tmp_path / "run"

    first = _run_workflow(config, run_dir)
    assert first["status"] == "PASS"

    def snapshot(root: Path) -> dict[str, str]:
        return {
            str(path.relative_to(run_dir)): path.read_bytes()
            for path in sorted((root / "artifacts").glob("*/stage/*/*"))
        }

    stage_before = snapshot(run_dir)
    second = _run_workflow(config, run_dir)
    assert second["status"] == "PASS"
    assert second["resumed"] is True
    assert snapshot(run_dir) == stage_before
    summary = _summary(run_dir)
    assert summary["counts"]["receipts"] == 2
    assert summary["counts"]["shadow_staged"] == 2
    digests = [
        row["candidate_digest"]
        for row in summary["terminal_rows"]
        if row["candidate_digest"]
    ]
    assert len(digests) == len(set(digests)) == 2


def test_human_approval_blocks_without_approval_and_promotes_once_with_it(
    tmp_path: Path,
) -> None:
    """Approval gate: blocks without approval; approved promotion applies exactly one CAS effect."""

    workspace = tmp_path / "workspace"
    pids = fw.default_workspace(workspace)
    shadow_config = fw.write_config(workspace, projects=pids)
    run_dir = tmp_path / "shadow-run"
    _run_workflow(shadow_config, run_dir)
    alpha_digest = _rows(run_dir)["alpha"]["candidate_digest"]
    assert alpha_digest

    # human-approval mode without an approval artifact blocks
    blocked_config = fw.write_config(workspace, projects=pids, promotion_mode="human_approval")
    blocked_run = tmp_path / "blocked-run"
    _run_workflow(blocked_config, blocked_run)
    blocked_rows = _rows(blocked_run)
    assert blocked_rows["alpha"]["terminal_status"] == "BLOCKED_APPROVAL"
    assert blocked_rows["alpha"]["first_failed_gate"] == "approval_missing"

    # with an approval binding the candidate and its base head generation, one CAS effect
    expires = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat().replace(
        "+00:00", "Z"
    )
    fw.write_approval(
        workspace,
        "alpha",
        alpha_digest,
        expires_at=expires,
        base_head_generation=1,
    )
    promoted_run = tmp_path / "promoted-run"
    promoted = _run_workflow(blocked_config, promoted_run)
    assert promoted["status"] == "PASS"
    rows = _rows(promoted_run)
    assert rows["alpha"]["terminal_status"] == "PROMOTED"
    project_receipt = json.loads(
        Path(rows["alpha"]["receipt_path"]).read_text(encoding="utf-8")
    )
    assert project_receipt["accepted_effect_count"] == 1
    assert project_receipt["accepted_effects"][0]["type"] == "graph_memory_topic_promote"
    promotion = json.loads(
        Path(project_receipt["promotion_receipt"]["path"]).read_text(encoding="utf-8")
    )
    readback = json.loads(
        Path(project_receipt["head_readback"]["path"]).read_text(encoding="utf-8")
    )
    assert promotion["cas_outcome"] == "APPLIED"
    assert promotion["head_before_generation"] == 1
    assert promotion["head_after_generation"] == 2
    assert readback["generation"] == promotion["head_after_generation"]
    assert readback["digest"] == promotion["head_after_digest"]
    head = json.loads(
        (workspace / "memory" / "alpha" / "head.json").read_text(encoding="utf-8")
    )
    assert head["generation"] == 2
    assert head["digest"] == alpha_digest
    _assert_receipts_validated(promoted_run)

    # re-running against the advanced head is a CAS conflict, not an overwrite
    conflict_run = tmp_path / "conflict-run"
    _run_workflow(blocked_config, conflict_run)
    conflict_rows = _rows(conflict_run)
    assert conflict_rows["alpha"]["terminal_status"] == "BLOCKED_CAS"
    assert conflict_rows["alpha"]["first_failed_gate"] == "cas_conflict"
    head_after_conflict = json.loads(
        (workspace / "memory" / "alpha" / "head.json").read_text(encoding="utf-8")
    )
    assert head_after_conflict == head


@pytest.mark.parametrize(
    "scenario",
    ["malformed", "distinct", "undeclared"],
)
def test_scenario_runs_are_deterministic(tmp_path: Path, scenario: str) -> None:
    """Replay determinism: identical scenarios produce identical candidate digests."""

    workspace_a = tmp_path / f"{scenario}-a"
    workspace_b = tmp_path / f"{scenario}-b"
    for workspace in (workspace_a, workspace_b):
        if scenario == "malformed":
            fw.build_project(workspace, "alpha")
            fw.build_project(workspace, "beta")
            fw.corrupt_transcripts(workspace, "beta")
            notes = workspace / "projects" / "alpha" / "worktree" / "notes.md"
            notes.write_text(notes.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
            fw.write_config(workspace, projects=["alpha", "beta"])
        elif scenario == "distinct":
            fw.build_project(workspace, "alpha", faults={"state": "fail"})
            fw.build_project(workspace, "beta", faults={"ingest": "fail"})
            for pid in ("alpha", "beta"):
                notes = workspace / "projects" / pid / "worktree" / "notes.md"
                notes.write_text(
                    notes.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8"
                )
            fw.write_config(workspace, projects=["alpha", "beta"])
        else:
            pids = fw.default_workspace(workspace)
            fw.write_config(workspace, projects=pids)

    def digests(workspace: Path) -> list[str | None]:
        run_dir = tmp_path / f"{scenario}-run-{workspace.name}"
        _run_workflow(workspace / "run-config.json", run_dir)
        return [
            row["candidate_digest"]
            for row in _summary(run_dir)["terminal_rows"]
        ]

    assert digests(workspace_a) == digests(workspace_b)
