import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app


def test_workflows_list_and_describe() -> None:
    runner = CliRunner()

    listed = runner.invoke(app, ["workflows", "list", "--json"])
    described = runner.invoke(
        app, ["workflows", "describe", "repository-readiness", "--json"]
    )

    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["workflows"][0]["workflow_id"] == (
        "repository-readiness"
    )
    assert described.exit_code == 0, described.output
    assert json.loads(described.stdout)["topology"] == "LINEAR"


def test_workflows_run_executes_packaged_definition(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "repository-readiness",
            "--repo",
            str(repo),
            "--goal",
            "Determine whether this checkout is ready for focused work.",
            "--require-clean",
            "--run-dir",
            str(run_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["workflow_id"] == "repository-readiness"
    assert payload["result"]["status"] == "READY"


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
