from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
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

    repair = repair_durable_repository_qualification(
        run_dir=run_dir, node_id="qualify-tests"
    )
    approval_wait = resume_packaged_workflow(run_dir=run_dir)
    repaired_receipt = _json(run_dir / "run-receipt.json")

    assert repair["status"] == "PASS"
    assert approval_wait["status"] == "BLOCKED"
    assert _node(repaired_receipt, "publish-qualification")["verdict"] == (
        "APPROVAL_REQUIRED"
    )
    for name, digest in before.items():
        assert _sha256(run_dir / "receipts" / f"{name}.json") == digest
        assert _node(repaired_receipt, name)["resumed"] is True
    assert _node(repaired_receipt, "qualify-tests")["resumed"] is False
    assert _node(repaired_receipt, "reconcile-qualification")["resumed"] is False

    approve_packaged_workflow(run_dir=run_dir)
    final = resume_packaged_workflow(run_dir=run_dir)
    again = resume_packaged_workflow(run_dir=run_dir)
    ledger = _json(publish_path / "publication-ledger.json")

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
