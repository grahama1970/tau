import hashlib
import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.commit_plan import COMMIT_PLAN_RECEIPT_SCHEMA, write_commit_plan_receipt


def test_commit_plan_groups_related_changes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "tests" / "test_example.py").write_text("def test_value(): pass\n", encoding="utf-8")

    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    assert payload["schema"] == COMMIT_PLAN_RECEIPT_SCHEMA
    assert payload["dry_run"] is True
    assert payload["changed_file_count"] == 2
    assert payload["dependency_order"] == ["source", "tests"]
    source_file = next(
        item for item in payload["changed_files"] if item["path"] == "src/example.py"
    )
    assert source_file["exists"] is True
    assert source_file["bytes"] == len("value = 1\n")
    assert source_file["sha256"] == f"sha256:{_sha256(repo / 'src' / 'example.py')}"
    assert source_file in payload["proposed_commit_groups"][0]["files"]
    assert {
        "path": "src/example.py",
        "status": "??",
        "original_path": "",
        "exists": True,
        "bytes": len("value = 1\n"),
        "sha256": f"sha256:{_sha256(repo / 'src' / 'example.py')}",
    } in payload["changed_file_artifacts"]


def test_commit_plan_records_deleted_file_artifact(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    source = repo / "src.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    source.unlink()

    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    deleted = payload["changed_files"][0]
    assert deleted["path"] == "src.py"
    assert deleted["status"] == "D"
    assert deleted["exists"] is False
    assert deleted["bytes"] is None
    assert deleted["sha256"] is None


def test_commit_plan_allows_empty_clean_tree(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    assert payload["ok"] is True
    assert payload["changed_file_count"] == 0
    assert payload["proposed_commit_groups"] == []


def test_commit_plan_flags_high_risk_paths(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    assert payload["status"] == "BLOCKED"
    assert "high_risk_paths_touched" in payload["alert_codes"]
    assert payload["approval_required"] is True


def test_commit_plan_is_dry_run_by_default(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    assert payload["status"] == "BLOCKED"
    assert payload["dry_run"] is True
    assert payload["apply_requested"] is False
    assert "source_changes_lack_tests_or_evidence" in payload["alert_codes"]
    assert "Commits were created." in payload["proof_scope"]["does_not_prove"]


def test_commit_plan_accepts_source_change_with_evidence_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "lsp-diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        evidence_receipt_paths=[evidence],
    )

    assert payload["status"] == "PASS"
    assert payload["evidence_receipt_count"] == 1
    assert payload["evidence_receipts"][0]["schema"] == "tau.lsp_diagnostics_receipt.v1"
    assert payload["evidence_receipts"][0]["sha256"].startswith("sha256:")


def test_commit_plan_blocks_source_change_with_blocked_evidence_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "blocked-diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": False,
                "status": "BLOCKED",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        evidence_receipt_paths=[evidence],
    )

    assert payload["status"] == "BLOCKED"
    assert payload["evidence_receipt_count"] == 1
    assert "evidence_receipt_not_pass" in payload["alert_codes"]


def test_commit_plan_warns_when_docs_mix_with_runtime_changes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "docs").mkdir()
    (repo / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "docs" / "example.md").write_text("# Example\n", encoding="utf-8")
    evidence = repo / "evidence.json"
    evidence.write_text(
        json.dumps({"schema": "tau.lsp_diagnostics_receipt.v1", "ok": True, "status": "PASS"})
        + "\n",
        encoding="utf-8",
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        evidence_receipt_paths=[evidence],
    )

    assert payload["status"] == "PASS"
    assert "mixed_docs_with_runtime_changes" in payload["warning_codes"]
    assert "mixed_docs_with_runtime_changes" not in payload["alert_codes"]


def test_commit_plan_warns_when_lockfiles_mix_with_other_changes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    (repo / "requirements.lock").write_text("demo==1.0\n", encoding="utf-8")

    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    assert payload["status"] == "PASS"
    assert "mixed_lockfiles_with_other_changes" in payload["warning_codes"]
    assert "mixed_lockfiles_with_other_changes" not in payload["alert_codes"]


def test_commit_plan_requires_approval_to_apply(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        apply=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "approval_required_to_apply" in payload["alert_codes"]


def test_commit_plan_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        zero_trust=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_commit_plan_zero_trust_accepts_policy_boundary(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "diagnostics.json"
    evidence.write_text(
        json.dumps({"schema": "tau.lsp_diagnostics_receipt.v1", "ok": True, "status": "PASS"})
        + "\n",
        encoding="utf-8",
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        zero_trust=True,
        policy_profile={"schema": "tau.policy_profile.v1", "profile_id": "test"},
        data_boundary={"schema": "tau.data_boundary.v1", "classification": "public"},
        evidence_receipt_paths=[evidence],
    )

    assert payload["status"] == "PASS"
    assert payload["zero_trust"] is True
    assert payload["policy_profile"]["profile_id"] == "test"
    assert payload["data_boundary"]["classification"] == "public"


def test_cli_commit_plan_writes_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "evidence.json"
    evidence.write_text(
        json.dumps({"schema": "tau.review_findings_receipt.v1", "ok": True, "status": "PASS"})
        + "\n",
        encoding="utf-8",
    )
    out = repo / "commit-plan.json"

    result = CliRunner().invoke(
        app,
        [
            "commit-plan",
            "--repo",
            str(repo),
            "--out",
            str(out),
            "--evidence-receipt",
            str(evidence),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == COMMIT_PLAN_RECEIPT_SCHEMA
    assert payload["evidence_receipt_count"] == 1


def test_cli_commit_plan_zero_trust_missing_boundary_exits_blocked(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    out = repo / "commit-plan.json"

    result = CliRunner().invoke(
        app,
        ["commit-plan", "--repo", str(repo), "--out", str(out), "--zero-trust"],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tau-test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Tau Test"], cwd=repo, check=True)
    return repo


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
