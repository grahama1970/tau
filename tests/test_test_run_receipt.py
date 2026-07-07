import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.test_run_receipt import (
    TEST_RUN_RECEIPT_SCHEMA,
    write_test_run_receipt,
)


def test_test_run_receipt_records_passing_pytest_artifacts(tmp_path: Path) -> None:
    _write_passing_test(tmp_path)

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q"],
        tested_paths=["src/example.py", "./tests/test_example.py"],
        goal_hash="sha256:goal",
    )

    assert payload["schema"] == TEST_RUN_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["ok"] is True
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["tests_passed"] is True
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["tested_paths"] == ["src/example.py", "tests/test_example.py"]
    assert payload["command_result"]["returncode"] == 0
    assert payload["stdout_artifact"]["exists"] is True
    assert payload["stderr_artifact"]["exists"] is True
    assert "1 passed" in Path(payload["stdout_artifact"]["path"]).read_text(encoding="utf-8")


def test_test_run_receipt_blocks_failing_pytest(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "def test_example():\n    assert False\n",
        encoding="utf-8",
    )

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q"],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["tests_passed"] is False
    assert "test_command_failed" in payload["alert_codes"]
    assert payload["command_result"]["returncode"] != 0


def test_test_run_receipt_blocks_disallowed_command_without_execution(tmp_path: Path) -> None:
    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=["bash", "-lc", "echo unsafe"],
    )

    assert payload["status"] == "BLOCKED"
    assert "disallowed_test_command" in payload["alert_codes"]
    assert payload["command_result"] is None
    assert payload["live"] is False


def test_test_run_receipt_blocks_escaped_tested_path_without_execution(tmp_path: Path) -> None:
    _write_passing_test(tmp_path)

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q"],
        tested_paths=["../secret.py", "/tmp/outside.py"],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert payload["live"] is False
    assert payload["tested_paths"] == []
    assert "invalid_tested_path" in payload["alert_codes"]


def test_test_run_receipt_blocks_external_pytest_path_without_execution(
    tmp_path: Path,
) -> None:
    _write_passing_test(tmp_path)
    outside = tmp_path.parent / "outside-test-target.py"
    outside.write_text("def test_outside():\n    assert True\n", encoding="utf-8")

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q", str(outside)],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert payload["live"] is False
    assert "test_command_path_escape" in payload["alert_codes"]


def test_test_run_receipt_blocks_external_pytest_rootdir_without_execution(
    tmp_path: Path,
) -> None:
    _write_passing_test(tmp_path)
    outside = tmp_path.parent / "outside-root"
    outside.mkdir()

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q", f"--rootdir={outside}"],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert payload["live"] is False
    assert "test_command_path_escape" in payload["alert_codes"]


def test_test_run_receipt_blocks_external_pytest_basetemp_without_execution(
    tmp_path: Path,
) -> None:
    _write_passing_test(tmp_path)
    outside = tmp_path.parent / "outside-basetemp"

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q", f"--basetemp={outside}"],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert payload["live"] is False
    assert "test_command_path_escape" in payload["alert_codes"]


def test_test_run_receipt_blocks_external_pytest_confcutdir_without_execution(
    tmp_path: Path,
) -> None:
    _write_passing_test(tmp_path)
    outside = tmp_path.parent / "outside-confcutdir"
    outside.mkdir()

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--confcutdir",
            str(outside),
        ],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert payload["live"] is False
    assert "test_command_path_escape" in payload["alert_codes"]


def test_test_run_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    _write_passing_test(tmp_path)

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q"],
        zero_trust=True,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_test_run_zero_trust_blocks_receipt_outside_repo_without_execution(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_passing_test(repo)
    outside = tmp_path / "outside"
    outside.mkdir()

    payload = write_test_run_receipt(
        repo=repo,
        output_path=outside / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q"],
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert payload["live"] is False
    assert "test_run_receipt_outside_repo" in payload["alert_codes"]


def test_test_run_legacy_allows_receipt_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_passing_test(repo)
    outside = tmp_path / "outside"
    outside.mkdir()

    payload = write_test_run_receipt(
        repo=repo,
        output_path=outside / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q"],
    )

    assert payload["status"] == "PASS"
    assert payload["command_result"] is not None
    assert payload["live"] is True
    assert "test_run_receipt_outside_repo" not in payload["alert_codes"]


def test_test_run_receipt_does_not_claim_semantic_correctness(tmp_path: Path) -> None:
    _write_passing_test(tmp_path)

    payload = write_test_run_receipt(
        repo=tmp_path,
        output_path=tmp_path / "test-run.json",
        command=[sys.executable, "-m", "pytest", "-q"],
    )

    assert "Semantic code correctness." in payload["proof_scope"]["does_not_prove"]
    assert "Provider/model quality." in payload["proof_scope"]["does_not_prove"]


def test_cli_test_run_writes_receipt(tmp_path: Path) -> None:
    _write_passing_test(tmp_path)
    out = tmp_path / "test-run.json"

    result = CliRunner().invoke(
        app,
        [
            "test-run",
            "--repo",
            str(tmp_path),
            "--out",
            str(out),
            "--goal-hash",
            "sha256:goal",
            "--command",
            sys.executable,
            "--command",
            "-m",
            "--command",
            "pytest",
            "--command",
            "-q",
            "--tested-path",
            "src/example.py",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == TEST_RUN_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["tests_passed"] is True
    assert payload["tested_paths"] == ["src/example.py"]


def test_cli_test_run_failing_pytest_exits_blocked(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "def test_example():\n    assert False\n",
        encoding="utf-8",
    )
    out = tmp_path / "test-run.json"

    result = CliRunner().invoke(
        app,
        [
            "test-run",
            "--repo",
            str(tmp_path),
            "--out",
            str(out),
            "--command",
            sys.executable,
            "--command",
            "-m",
            "--command",
            "pytest",
            "--command",
            "-q",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "test_command_failed" in payload["alert_codes"]


def test_cli_test_run_blocks_escaped_tested_path(tmp_path: Path) -> None:
    _write_passing_test(tmp_path)
    out = tmp_path / "test-run.json"

    result = CliRunner().invoke(
        app,
        [
            "test-run",
            "--repo",
            str(tmp_path),
            "--out",
            str(out),
            "--command",
            sys.executable,
            "--command",
            "-m",
            "--command",
            "pytest",
            "--command",
            "-q",
            "--tested-path",
            "../secret.py",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert "invalid_tested_path" in payload["alert_codes"]


def test_cli_test_run_zero_trust_blocks_receipt_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_passing_test(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    out = outside / "test-run.json"
    policy_path = repo / "policy-profile.json"
    boundary_path = repo / "data-boundary.json"
    policy_path.write_text(json.dumps(_policy_profile()), encoding="utf-8")
    boundary_path.write_text(json.dumps(_data_boundary()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "test-run",
            "--repo",
            str(repo),
            "--out",
            str(out),
            "--goal-hash",
            "sha256:goal",
            "--zero-trust",
            "--policy-profile",
            str(policy_path),
            "--data-boundary",
            str(boundary_path),
            "--command",
            sys.executable,
            "--command",
            "-m",
            "--command",
            "pytest",
            "--command",
            "-q",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["command_result"] is None
    assert "test_run_receipt_outside_repo" in payload["alert_codes"]


def _write_passing_test(path: Path) -> None:
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "def test_example():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )


def _policy_profile() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "test",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {
            "external_search": "deny",
            "manual_sanitized_receipt": "allow_with_review",
        },
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": ["."], "read_denylist": []},
    }


def _data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }
