from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tau_coding.workflows.runner import (
    approve_approved_release_bundle,
    resume_approved_release_bundle,
    run_approved_release_bundle_workflow,
)


def test_release_bundle_revises_waits_for_approval_and_resumes_once(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    first = _run(repo, run_dir, publish_path)
    first_dag = _json(run_dir / "run-receipt.json")
    notes = _node(first_dag, "draft-release-notes")
    notes_hash = notes["accepted_manifest_sha256"]

    assert first["status"] == "BLOCKED"
    assert first_dag["verdict"] == "APPROVAL_REQUIRED"
    assert notes["attempt_count"] == 2
    assert [item["review_verdict"] for item in notes["attempts"]] == ["REVISE", "PASS"]
    assert not publish_path.exists()
    assert not (run_dir / "results").exists()

    approval = approve_approved_release_bundle(run_dir=run_dir)
    final = resume_approved_release_bundle(run_dir=run_dir)
    final_dag = _json(run_dir / "run-receipt.json")

    assert approval["status"] == "PASS"
    assert final["status"] == "PASS"
    assert final["result"]["status"] == "APPROVED"
    assert _node(final_dag, "draft-release-notes")["accepted_manifest_sha256"] == notes_hash
    assert _node(final_dag, "publish-approved-release")["transaction_state"] == "CONTINUED"
    assert _node(final_dag, "finalize-approved-release")["status"] == "PASS"
    for name in ("approved-release-bundle.json", "approved-release-bundle.md"):
        assert (publish_path / name).read_bytes() == (run_dir / "results" / name).read_bytes()


def test_terminal_policy_failure_prevents_assembly_and_publication(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    receipt = _run(repo, run_dir, publish_path, force_terminal_failure=True)
    dag = _json(run_dir / "run-receipt.json")

    assert receipt["status"] == "BLOCKED"
    assert _node(dag, "verify-release-policy")["errors"] == ["release_policy_rejected"]
    assert not (run_dir / "receipts" / "assemble-release-bundle.json").exists()
    assert not (run_dir / "transactions" / "publish-approved-release").exists()
    assert not publish_path.exists()


def test_failed_post_write_verification_rolls_back_publication(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    first = _run(
        repo,
        run_dir,
        publish_path,
        simulate_publish_verification_failure=True,
    )
    assert first["status"] == "BLOCKED"
    approve_approved_release_bundle(run_dir=run_dir)

    resumed = resume_approved_release_bundle(run_dir=run_dir)
    rollback = _json(run_dir / "receipts" / "publication-rollback.json")

    assert resumed["status"] == "BLOCKED"
    assert rollback["status"] == "ROLLED_BACK"
    assert rollback["reason"] == "post_write_verification_failed"
    assert not publish_path.exists()
    assert not (run_dir / "results").exists()


def _run(
    repo: Path,
    run_dir: Path,
    publish_path: Path,
    *,
    force_terminal_failure: bool = False,
    simulate_publish_verification_failure: bool = False,
) -> dict[str, object]:
    return run_approved_release_bundle_workflow(
        repo_path=repo,
        human_goal="Publish an approved release bundle.",
        publish_path=publish_path,
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
        force_terminal_failure=force_terminal_failure,
        simulate_publish_verification_failure=simulate_publish_verification_failure,
        step_delay_seconds=0.01,
    )


def _node(receipt: dict[str, object], node_id: str) -> dict[str, object]:
    return next(item for item in receipt["nodes"] if item["node_id"] == node_id)  # type: ignore[index,union-attr]


def _json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# Release fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
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
