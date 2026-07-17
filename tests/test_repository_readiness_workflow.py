import json
import subprocess
from pathlib import Path

from tau_coding.workflows.runner import run_repository_readiness_workflow

GOAL = "Determine whether this checkout is ready for focused work."


def test_clean_repository_produces_hash_bound_readiness_reports(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    before = _repository_snapshot(repo)
    run_dir = tmp_path / "run"

    receipt = run_repository_readiness_workflow(
        repo_path=repo,
        human_goal=GOAL,
        require_clean=True,
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    report = json.loads(
        (run_dir / "results" / "repository-readiness.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "READY"
    assert report["summary"] == "Repository is ready for focused work."
    assert (run_dir / "results" / "repository-readiness.md").is_file()
    run_receipt = _read(run_dir / "run-receipt.json")
    assert [node["node_id"] for node in run_receipt["nodes"]] == [
        "inspect-repository",
        "validate-readiness",
        "publish-readiness",
    ]
    goal_hash = receipt["goal"]["goal_hash"]
    assert all(node["goal_hash"] == goal_hash for node in run_receipt["nodes"])
    assert run_receipt["nodes"][-1]["accepted_output"]["status"] == "READY"
    assert _repository_snapshot(repo) == before


def test_dirty_repository_blocks_validation_and_never_publishes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    run_dir = tmp_path / "run"

    receipt = run_repository_readiness_workflow(
        repo_path=repo,
        human_goal=GOAL,
        require_clean=True,
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    run_receipt = _read(run_dir / "run-receipt.json")
    assert [node["node_id"] for node in run_receipt["nodes"]] == [
        "inspect-repository",
        "validate-readiness",
    ]
    validator = run_receipt["nodes"][1]
    assert validator["status"] == "BLOCKED"
    assert validator["errors"] == ["dirty_repository"]
    assert validator["accepted_output"] is None
    assert not (run_dir / "receipts" / "publish-readiness.json").exists()
    assert not (run_dir / "results" / "repository-readiness.json").exists()
    assert not (run_dir / "results" / "repository-readiness.md").exists()


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
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


def _repository_snapshot(repo: Path) -> tuple[str, str]:
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return head, status


def _read(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload
