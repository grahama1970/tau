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
    assert [
        workflow["workflow_id"]
        for workflow in json.loads(listed.stdout)["workflows"]
    ] == [
        "approved-release-bundle",
        "repository-evidence-map",
        "repository-readiness",
        "tau-operator-reference",
    ]
    assert described.exit_code == 0, described.output
    assert json.loads(described.stdout)["topology"] == "LINEAR"


def test_workflows_operator_reference_description_and_run_help() -> None:
    runner = CliRunner()
    described = runner.invoke(
        app, ["workflows", "describe", "tau-operator-reference", "--json"]
    )
    help_probe = subprocess.run(
        ["tau", "workflows", "run", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert described.exit_code == 0, described.output
    assert json.loads(described.stdout)["topology"] == "MULTI_STEP_SEQUENTIAL"
    assert help_probe.returncode == 0, help_probe.stderr
    assert "--required-workflow" in help_probe.stdout
    assert "--require-tests" in help_probe.stdout


def test_workflows_describes_evidence_map() -> None:
    result = CliRunner().invoke(
        app, ["workflows", "describe", "repository-evidence-map", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["topology"] == "FAN_OUT_FAN_IN"


def test_workflows_approve_and_resume_release_bundle(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "approved-release-bundle",
            "--repo",
            str(repo),
            "--goal",
            "Publish an approved release bundle.",
            "--publish-path",
            str(publish_path),
            "--run-dir",
            str(run_dir),
        ],
    )
    approved = runner.invoke(app, ["workflows", "approve", str(run_dir)])
    resumed = runner.invoke(app, ["workflows", "resume", str(run_dir)])

    assert first.exit_code == 1
    assert json.loads(first.stdout)["status"] == "BLOCKED"
    assert approved.exit_code == 0, approved.output
    assert resumed.exit_code == 0, resumed.output
    assert json.loads(resumed.stdout)["result"]["status"] == "APPROVED"
    assert (publish_path / "approved-release-bundle.json").is_file()


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


def test_workflows_run_dispatches_operator_reference(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "operator-reference"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "tau-operator-reference",
            "--repo",
            str(repo),
            "--run-dir",
            str(run_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["workflow_id"] == "tau-operator-reference"
    assert payload["result"]["status"] == "ACCEPTED"


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
