from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tau_coding.workflows.runner import (
    approve_packaged_workflow,
    repair_durable_repository_qualification,
    resume_packaged_workflow,
    run_durable_repository_qualification_workflow,
)


def test_targeted_repair_preserves_unaffected_work_and_publication_is_idempotent(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    first = _run(repo, run_dir, publish_path, inject_failure=True)
    before = {
        name: _sha256(run_dir / "receipts" / f"{name}.json")
        for name in (
            "capture-repository",
            "qualify-documentation",
            "qualify-package",
        )
    }

    assert first["status"] == "BLOCKED"
    assert _node(_json(run_dir / "run-receipt.json"), "qualify-tests")["errors"] == [
        "targeted_repair_required"
    ]
    assert not publish_path.exists()

    blocked_repair = repair_durable_repository_qualification(
        run_dir=run_dir, node_id="qualify-tests"
    )
    repair_approval_path = _write_repair_approval_packet(
        run_dir=run_dir,
        node_id="qualify-tests",
        path=tmp_path / "human-repair-approval.json",
    )
    repair = repair_durable_repository_qualification(
        run_dir=run_dir,
        node_id="qualify-tests",
        approval_packet=repair_approval_path,
    )
    approval_wait = resume_packaged_workflow(run_dir=run_dir)
    repaired_receipt = _json(run_dir / "run-receipt.json")

    assert blocked_repair["status"] == "BLOCKED"
    assert blocked_repair["errors"] == ["approval_packet_required"]
    assert repair["status"] == "PASS"
    assert _json(run_dir / "input" / "repair-qualify-tests.json")["origin"] == "machine"
    assert approval_wait["status"] == "BLOCKED"
    assert _node(repaired_receipt, "publish-qualification")["verdict"] == ("APPROVAL_REQUIRED")
    for name, digest in before.items():
        assert _sha256(run_dir / "receipts" / f"{name}.json") == digest
        assert _node(repaired_receipt, name)["resumed"] is True
    assert _node(repaired_receipt, "qualify-tests")["resumed"] is False
    assert _node(repaired_receipt, "reconcile-qualification")["resumed"] is False

    blocked_approval = approve_packaged_workflow(run_dir=run_dir)
    approval_path = _write_approval_packet(
        run_dir=run_dir,
        transaction_node_id="publish-qualification",
        path=tmp_path / "human-qualification-approval.json",
    )
    approved = approve_packaged_workflow(
        run_dir=run_dir,
        approval_packet=approval_path,
    )
    final = resume_packaged_workflow(run_dir=run_dir)
    again = resume_packaged_workflow(run_dir=run_dir)
    ledger = _json(publish_path / "publication-ledger.json")

    assert blocked_approval["status"] == "BLOCKED"
    assert blocked_approval["errors"] == ["approval_packet_required"]
    assert approved["status"] == "PASS"
    assert approved["approval_packet_path"] == str(run_dir / "input" / "approval.json")
    gate_summary = _json(
        run_dir / "transactions" / "publish-qualification" / "approval-gate-receipt.json"
    )["packet_summary"]
    assert isinstance(gate_summary, dict)
    assert gate_summary["authorship"] == "human_local_signature"
    assert gate_summary["machine_fabricated"] is False
    assert final["status"] == "PASS"
    assert final["result"]["status"] == "QUALIFIED"  # type: ignore[index]
    assert again["status"] == "PASS"
    assert ledger["effect_count"] == 1
    for name in (
        "durable-repository-qualification.json",
        "durable-repository-qualification.md",
    ):
        assert (publish_path / name).read_bytes() == (run_dir / "results" / name).read_bytes()


def test_repair_rejects_wrong_node_and_unblocked_run(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    _run(repo, run_dir, tmp_path / "published", inject_failure=False)

    with pytest.raises(RuntimeError, match="only qualify-tests"):
        repair_durable_repository_qualification(run_dir=run_dir, node_id="qualify-package")
    with pytest.raises(RuntimeError, match="not blocked"):
        repair_durable_repository_qualification(run_dir=run_dir, node_id="qualify-tests")


def test_staged_result_crash_resumes_without_rerunning_accepted_branches(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    with pytest.raises(RuntimeError, match="diagnostic_injected_crash_after_staged"):
        run_durable_repository_qualification_workflow(
            repo_path=repo,
            human_goal="Qualify after interruption.",
            publish_path=publish_path,
            run_dir=run_dir,
            open_viewer=False,
            browser_open=False,
            viewer_hold_seconds=None,
            crash_after_staged_node_id="qualify-tests",
            step_delay_seconds=0.05,
        )
    before = {
        name: _sha256(run_dir / "receipts" / f"{name}.json")
        for name in (
            "capture-repository",
            "qualify-documentation",
            "qualify-package",
            "qualify-tests",
        )
    }

    resumed = resume_packaged_workflow(run_dir=run_dir)
    receipt = _json(run_dir / "run-receipt.json")

    assert resumed["status"] == "BLOCKED"
    for name, digest in before.items():
        assert _sha256(run_dir / "receipts" / f"{name}.json") == digest
    assert _node(receipt, "capture-repository")["resumed"] is True
    assert _node(receipt, "qualify-documentation")["resumed"] is True
    assert _node(receipt, "qualify-package")["resumed"] is True
    recovered = _node(receipt, "qualify-tests")
    assert recovered["resumed"] is False
    assert recovered["attempt"] is None
    with sqlite3.connect(run_dir / "dag-run.sqlite3") as connection:
        events = connection.execute(
            """SELECT e.seq, e.event_type
               FROM dag_run_events e
               LEFT JOIN dag_node_attempts a ON a.attempt_id = e.attempt_id
               WHERE e.event_type = 'run_lease_taken_over' OR a.node_id = 'qualify-tests'
               ORDER BY e.seq"""
        ).fetchall()
    staged = next(seq for seq, event in events if event == "attempt_result_staged")
    takeover = next(seq for seq, event in events if event == "run_lease_taken_over")
    validated = next(seq for seq, event in events if event == "attempt_result_validated")
    assert staged < takeover < validated
    assert _node(receipt, "publish-qualification")["verdict"] == "APPROVAL_REQUIRED"


def test_durable_qualification_reviewer_blocks_invalid_candidate(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    json_artifact = artifact_root / "durable-repository-qualification.json"
    markdown_artifact = artifact_root / "durable-repository-qualification.md"
    json_artifact.parent.mkdir(parents=True)
    json_artifact.write_text(
        json.dumps(
            {
                "schema": "tau.durable_repository_qualification.v1",
                "status": "BROKEN",
                "goal": {"goal_hash": "sha256:goal"},
                "repository": {},
                "branches": {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    markdown_artifact.write_text("# Wrong Title\n", encoding="utf-8")
    feedback_path = tmp_path / "review-feedback.json"
    context_path = tmp_path / "review-context.json"
    context = {
        "schema": "tau.generic_artifact_review_context.v1",
        "run_id": "run-review-test",
        "node_id": "publish-qualification",
        "transaction_id": "publish-qualification",
        "attempt": 1,
        "producer_id": "durable_repository_qualification_producer",
        "reviewer_id": "deterministic_qualification_reviewer",
        "candidate_manifest_sha256": "sha256:candidate",
        "goal_hash": "sha256:goal",
        "validated_artifacts": [
            {
                "artifact_id": "qualification_json",
                "kind": "qualification_json",
                "media_type": "application/json",
                "path": str(json_artifact),
                "sha256": _sha256(json_artifact),
                "bytes": json_artifact.stat().st_size,
            },
            {
                "artifact_id": "qualification_markdown",
                "kind": "qualification_markdown",
                "media_type": "text/markdown",
                "path": str(markdown_artifact),
                "sha256": _sha256(markdown_artifact),
                "bytes": markdown_artifact.stat().st_size,
            },
        ],
        "output_contract": {"review_feedback_path": str(feedback_path)},
    }
    context_path.write_text(json.dumps(context, sort_keys=True), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "tau_coding.workflows.nodes.durable_repository_qualification",
            "review",
        ],
        check=True,
        env={
            **os.environ,
            "TAU_GENERIC_DAG_REVIEW_CONTEXT": str(context_path),
            "TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256": _sha256(context_path),
        },
    )

    feedback = _json(feedback_path)
    assert feedback["verdict"] == "BLOCKED"
    assert feedback["live"] is True
    assert feedback["mocked"] is False
    reasons = {item["reason"] for item in feedback["findings"]}  # type: ignore[index]
    assert "qualification_status_invalid" in reasons
    assert "qualification_repository_missing" in reasons
    assert "qualification_branches_missing" in reasons
    assert "qualification_markdown_title_missing" in reasons


def _run(
    repo: Path, run_dir: Path, publish_path: Path, *, inject_failure: bool
) -> dict[str, object]:
    return run_durable_repository_qualification_workflow(
        repo_path=repo,
        human_goal="Qualify this repository durably.",
        publish_path=publish_path,
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
        inject_test_branch_failure=inject_failure,
        step_delay_seconds=0.01,
    )


def _node(receipt: dict[str, object], node_id: str) -> dict[str, object]:
    return next(item for item in receipt["nodes"] if item["node_id"] == node_id)  # type: ignore[index,union-attr]


def _json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_approval_packet(*, run_dir: Path, transaction_node_id: str, path: Path) -> Path:
    gate_path = run_dir / "transactions" / transaction_node_id / "approval-gate-receipt.json"
    target = _json(gate_path)["expected_target"]
    _write_signed_packet(
        path,
        action="generic_dag_transaction_continue",
        target=target,
        reason="approve exact deterministic continuation",
        evidence=[str(gate_path)],
    )
    return path


def _write_repair_approval_packet(*, run_dir: Path, node_id: str, path: Path) -> Path:
    request = _json(run_dir / "input" / "durable-qualification-request.json")
    goal = request["goal"]
    assert isinstance(goal, dict)
    target = {
        "id": f"durable-repository-qualification:{node_id}:{goal['goal_hash']}",
        "workflow_id": "durable-repository-qualification",
        "node_id": node_id,
        "goal_hash": str(goal["goal_hash"]),
    }
    _write_signed_packet(
        path,
        action="workflow_repair",
        target=target,
        reason="approve exact targeted repair",
        evidence=[str(run_dir / "receipts" / f"{node_id}.json")],
    )
    return path


def _write_signed_packet(
    path: Path,
    *,
    action: str,
    target: object,
    reason: str,
    evidence: list[str],
) -> None:
    payload = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "actor": {"id": "human:test", "auth_method": "local-signature"},
        "action": action,
        "target": target,
        "reason": reason,
        "evidence": evidence,
        "nonce": hashlib.sha256(json.dumps(target, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    payload["signature"] = _local_signature(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _local_signature(payload: dict[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("signature", None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"local-signature-sha256:{digest}"


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# Qualification fixture\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_fixture.py").write_text(
        "def test_fixture():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Tau Test",
            "-c",
            "user.email=tau@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return path
