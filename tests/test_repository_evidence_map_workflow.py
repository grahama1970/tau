import json
import subprocess
from pathlib import Path

from tau_coding.workflows.runner import run_repository_evidence_map_workflow


def test_evidence_map_runs_three_branches_concurrently_and_publishes(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo", with_tests=True)
    run_dir = tmp_path / "run"

    receipt = run_repository_evidence_map_workflow(
        repo_path=repo,
        human_goal="Map this repository for focused work.",
        require_tests=True,
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
        step_delay_seconds=0.1,
    )

    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    dag_receipt = json.loads((run_dir / "run-receipt.json").read_text(encoding="utf-8"))
    assert dag_receipt["max_observed_concurrency"] == 3
    assert {node["node_id"]: node["status"] for node in dag_receipt["nodes"]} == {
        "inventory-repository": "PASS",
        "analyze-documentation": "PASS",
        "analyze-tests": "PASS",
        "analyze-package": "PASS",
        "publish-evidence-map": "PASS",
    }
    result = json.loads(
        (run_dir / "results" / "repository-evidence-map.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["status"] == "ACCEPTED"
    inventory_hash = result["repository"]["inventory_sha256"]
    assert {
        result["documentation"]["inventory_sha256"],
        result["tests"]["inventory_sha256"],
        result["package"]["inventory_sha256"],
    } == {inventory_hash}
    assert (run_dir / "results" / "repository-evidence-map.md").is_file()
    goal_hash = receipt["goal"]["goal_hash"]
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["goal_hash"] == goal_hash
        for path in (run_dir / "receipts").glob("*.json")
    )


def test_evidence_map_missing_required_tests_blocks_join_without_result(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo", with_tests=False)
    run_dir = tmp_path / "run"

    receipt = run_repository_evidence_map_workflow(
        repo_path=repo,
        human_goal="Map this repository for focused work.",
        require_tests=True,
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
        step_delay_seconds=0.1,
    )

    assert receipt["status"] == "BLOCKED"
    test_receipt = json.loads(
        (run_dir / "receipts" / "analyze-tests.json").read_text(encoding="utf-8")
    )
    assert test_receipt["status"] == "BLOCKED"
    assert test_receipt["errors"] == ["test_surface_missing"]
    assert test_receipt["accepted_output"] is None
    assert not (run_dir / "receipts" / "publish-evidence-map.json").exists()
    assert not (run_dir / "results").exists()
    package = json.loads(
        (run_dir / "receipts" / "analyze-package.json").read_text(encoding="utf-8")
    )
    documentation = json.loads(
        (run_dir / "receipts" / "analyze-documentation.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["status"] == documentation["status"] == "PASS"


def _git_repo(path: Path, *, with_tests: bool) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    if with_tests:
        (path / "tests").mkdir()
        (path / "tests" / "test_fixture.py").write_text(
            "def test_fixture():\n    assert True\n", encoding="utf-8"
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
