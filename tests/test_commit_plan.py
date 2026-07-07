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
        "policy_read_denied": False,
        "policy_write_allowed": None,
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


def test_commit_plan_blocks_untracked_sensitive_files(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    assert payload["status"] == "BLOCKED"
    assert "untracked_sensitive_files" in payload["alert_codes"]
    assert payload["sensitive_untracked_files"][0]["path"] == ".env"
    assert payload["sensitive_untracked_files"][0]["status"] == "??"


def test_commit_plan_blocks_untracked_sensitive_file_under_secret_dir(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    secret = repo / "secrets" / "token.txt"
    secret.parent.mkdir()
    secret.write_text("secret\n", encoding="utf-8")

    payload = write_commit_plan_receipt(repo=repo, output_path=repo / "commit-plan.json")

    assert payload["status"] == "BLOCKED"
    assert "untracked_sensitive_files" in payload["alert_codes"]
    assert payload["sensitive_untracked_files"][0]["path"] == "secrets/token.txt"


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
                "mocked": False,
                "live": True,
                "provider_live": False,
                "inspected_artifacts": [{"path": str(repo / "src.py")}],
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
    assert payload["evidence_receipts"][0]["schema_supported"] is True
    assert payload["evidence_receipts"][0]["mocked"] is False
    assert payload["evidence_receipts"][0]["live"] is True
    assert payload["evidence_receipts"][0]["provider_live"] is False
    assert payload["evidence_receipts"][0]["covered_paths"] == ["src.py"]
    assert payload["evidence_receipts"][0]["exists"] is True
    assert payload["evidence_receipts"][0]["sha256"].startswith("sha256:")
    assert payload["evidence_receipts"][0]["bytes"] == evidence.stat().st_size


def test_commit_plan_blocks_source_change_with_unrelated_evidence_receipt(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "other-diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "inspected_artifacts": [{"path": str(repo / "other.py")}],
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
    assert "source_changes_lack_relevant_evidence" in payload["alert_codes"]
    assert payload["evidence_receipts"][0]["covered_paths"] == ["other.py"]


def test_commit_plan_blocks_source_change_with_partial_evidence_coverage(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "one.py").write_text("one = 1\n", encoding="utf-8")
    (repo / "src" / "two.py").write_text("two = 2\n", encoding="utf-8")
    evidence = repo / "one-diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "inspected_artifacts": [{"path": str(repo / "src" / "one.py")}],
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
    assert "source_changes_lack_relevant_evidence" in payload["alert_codes"]
    alert = next(
        item
        for item in payload["alerts"]
        if item["code"] == "source_changes_lack_relevant_evidence"
    )
    assert alert["errors"] == ["src/two.py"]


def test_commit_plan_blocks_source_change_with_unsupported_evidence_schema(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "generic-pass.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.generic_pass_receipt.v1",
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

    assert payload["status"] == "BLOCKED"
    assert payload["evidence_receipt_count"] == 1
    assert payload["evidence_receipts"][0]["schema_supported"] is False
    assert "unsupported_evidence_receipt_schema" in payload["alert_codes"]


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


def test_commit_plan_blocks_mocked_evidence_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "mocked-diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": True,
                "live": True,
                "inspected_artifacts": [{"path": str(repo / "src.py")}],
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
    assert "evidence_receipt_mocked" in payload["alert_codes"]
    assert payload["evidence_receipts"][0]["mocked"] is True


def test_commit_plan_blocks_non_live_evidence_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "non-live-diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": False,
                "inspected_artifacts": [{"path": str(repo / "src.py")}],
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
    assert "evidence_receipt_not_live" in payload["alert_codes"]
    assert payload["evidence_receipts"][0]["live"] is False


def test_commit_plan_warns_when_docs_mix_with_runtime_changes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "docs").mkdir()
    (repo / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "docs" / "example.md").write_text("# Example\n", encoding="utf-8")
    evidence = repo / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "inspected_artifacts": [{"path": str(repo / "src" / "example.py")}],
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
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        apply=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "approval_required_to_apply" in payload["alert_codes"]
    assert payload["approval_receipt"] is None
    assert payload["apply_eligible"] is False


def test_commit_plan_apply_accepts_working_tree_approval_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    readme = repo / "README.md"
    readme.write_text("# Demo\n", encoding="utf-8")
    approval = _write_approval_receipt(repo, requested_action="working_tree_mutation")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        apply=True,
        approval_receipt_path=approval,
    )

    assert payload["status"] == "PASS"
    assert payload["apply_requested"] is True
    assert payload["dry_run"] is False
    assert payload["apply_eligible"] is True
    assert payload["approval_receipt"]["schema"] == "tau.approval_gate_receipt.v1"
    assert payload["approval_receipt"]["requested_action"] == "working_tree_mutation"
    assert payload["approval_receipt"]["sha256"] == f"sha256:{_sha256(approval)}"
    assert payload["approval_receipt"]["bytes"] == approval.stat().st_size
    assert Path(payload["approval_receipt"]["path"]).name not in {
        item["path"] for item in payload["changed_files"]
    }
    assert subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    ).returncode != 0


def test_commit_plan_apply_blocks_wrong_approval_action(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    approval = _write_approval_receipt(repo, requested_action="github_apply")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        apply=True,
        approval_receipt_path=approval,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["apply_eligible"] is False
    assert "approval_receipt_wrong_action" in payload["alert_codes"]
    assert "approval_required_to_apply" in payload["alert_codes"]


def test_commit_plan_apply_blocks_mocked_approval_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    approval = _write_approval_receipt(
        repo,
        requested_action="working_tree_mutation",
        mocked=True,
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        apply=True,
        approval_receipt_path=approval,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["apply_eligible"] is False
    assert payload["approval_receipt"] is None
    assert "approval_receipt_mocked" in payload["alert_codes"]
    assert "approval_required_to_apply" in payload["alert_codes"]


def test_commit_plan_high_risk_path_accepts_working_tree_approval(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    (repo / "uv.lock").write_text("locked\n", encoding="utf-8")
    approval = _write_approval_receipt(repo, requested_action="working_tree_mutation")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        approval_receipt_path=approval,
    )

    assert payload["status"] == "PASS"
    assert payload["approval_required"] is True
    assert "high_risk_paths_touched" not in payload["alert_codes"]
    assert payload["approval_receipt"]["requested_action"] == "working_tree_mutation"
    assert Path(payload["approval_receipt"]["path"]).name not in {
        item["path"] for item in payload["changed_files"]
    }


def test_commit_plan_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        zero_trust=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_commit_plan_zero_trust_accepts_policy_boundary(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "goal_hash": "sha256:goal",
                "inspected_artifacts": [{"path": str(repo / "src.py")}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(write_allowlist=["src.py", "diagnostics.json"]),
        data_boundary=_data_boundary(),
        evidence_receipt_paths=[evidence],
    )

    assert payload["status"] == "PASS"
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["zero_trust"] is True
    assert payload["policy_profile"]["profile_id"] == "test"
    assert payload["data_boundary"]["classification"] == "public"
    assert payload["evidence_receipts"][0]["goal_hash_matches"] is True


def test_commit_plan_zero_trust_blocks_invalid_data_boundary(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    boundary = _data_boundary()
    boundary["classification"] = "classified-not-allowed"
    boundary.pop("foreign_person_access")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=boundary,
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_data_boundary" in payload["alert_codes"]
    assert "classified_not_allowed" in payload["alert_codes"]
    assert "foreign_person_access must be one of" in payload["alerts"][0]["errors"][0]


def test_commit_plan_zero_trust_honors_policy_read_denylist(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    secret = repo / "secrets" / "token.py"
    secret.parent.mkdir()
    secret.write_text("token = 'do-not-read'\n", encoding="utf-8")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(
            write_allowlist=[],
            read_denylist=["secrets/**"],
        ),
        data_boundary=_data_boundary(),
    )

    changed = payload["changed_files"][0]
    assert payload["status"] == "BLOCKED"
    assert "policy_read_denied" in payload["alert_codes"]
    assert changed["path"] == "secrets/token.py"
    assert changed["policy_read_denied"] is True
    assert changed["exists"] is None
    assert changed["sha256"] is None
    assert changed["bytes"] is None
    assert payload["changed_file_artifacts"][0]["policy_read_denied"] is True


def test_commit_plan_zero_trust_honors_policy_write_allowlist(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "docs").mkdir()
    (repo / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "docs" / "example.md").write_text("# Example\n", encoding="utf-8")
    evidence = repo / "diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "goal_hash": "sha256:goal",
                "inspected_artifacts": [{"path": str(repo / "src" / "example.py")}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(write_allowlist=["src/**"], read_denylist=[]),
        data_boundary=_data_boundary(),
        evidence_receipt_paths=[evidence],
    )

    source = next(item for item in payload["changed_files"] if item["path"] == "src/example.py")
    docs = next(item for item in payload["changed_files"] if item["path"] == "docs/example.md")
    assert payload["status"] == "BLOCKED"
    assert "policy_write_disallowed" in payload["alert_codes"]
    assert source["policy_write_allowed"] is True
    assert docs["policy_write_allowed"] is False
    assert next(
        item for item in payload["changed_file_artifacts"] if item["path"] == "docs/example.md"
    )["policy_write_allowed"] is False


def test_commit_plan_blocks_malformed_policy_read_denylist(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(read_denylist="secrets/**", write_allowlist=[]),
        data_boundary=_data_boundary(),
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_policy_read_denylist" in payload["alert_codes"]


def test_commit_plan_blocks_malformed_policy_write_allowlist(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(read_denylist=[], write_allowlist="src/**"),
        data_boundary=_data_boundary(),
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_policy_write_allowlist" in payload["alert_codes"]


def test_commit_plan_blocks_evidence_receipt_goal_hash_mismatch(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "diagnostics.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.lsp_diagnostics_receipt.v1",
                "ok": True,
                "status": "PASS",
                "goal_hash": "sha256:other",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_commit_plan_receipt(
        repo=repo,
        output_path=repo / "commit-plan.json",
        goal_hash="sha256:goal",
        evidence_receipt_paths=[evidence],
    )

    assert payload["status"] == "BLOCKED"
    assert "evidence_receipt_goal_hash_mismatch" in payload["alert_codes"]
    assert payload["evidence_receipts"][0]["goal_hash"] == "sha256:other"
    assert payload["evidence_receipts"][0]["goal_hash_matches"] is False


def test_commit_plan_blocks_evidence_receipt_missing_goal_hash_when_expected(
    tmp_path: Path,
) -> None:
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
        goal_hash="sha256:goal",
        evidence_receipt_paths=[evidence],
    )

    assert payload["status"] == "BLOCKED"
    assert "evidence_receipt_missing_goal_hash" in payload["alert_codes"]
    assert payload["evidence_receipts"][0]["goal_hash"] is None
    assert payload["evidence_receipts"][0]["goal_hash_matches"] is False


def test_cli_commit_plan_writes_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    evidence = repo / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "tau.review_findings.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "goal_hash": "sha256:goal",
                "findings": [{"file": "src.py"}],
            }
        )
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
            "--goal-hash",
            "sha256:goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == COMMIT_PLAN_RECEIPT_SCHEMA
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["evidence_receipt_count"] == 1
    assert payload["evidence_receipts"][0]["schema_supported"] is True


def test_cli_commit_plan_apply_accepts_approval_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    approval = _write_approval_receipt(repo, requested_action="working_tree_mutation")
    out = repo / "commit-plan.json"

    result = CliRunner().invoke(
        app,
        [
            "commit-plan",
            "--repo",
            str(repo),
            "--out",
            str(out),
            "--apply",
            "--approval-receipt",
            str(approval),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["apply_requested"] is True
    assert payload["apply_eligible"] is True
    assert payload["approval_receipt"]["requested_action"] == "working_tree_mutation"


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
    assert "missing_goal_hash" in payload["alert_codes"]
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


def _write_approval_receipt(
    repo: Path,
    *,
    requested_action: str,
    mocked: bool = False,
) -> Path:
    path = repo / f"approval-{requested_action}.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.approval_gate_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": mocked,
                "live": False,
                "approved": True,
                "requested_action": requested_action,
                "packet_summary": {
                    "target_id": f"repo:{repo}",
                    "actor_id": "human:test",
                    "actor_auth_method": "manual",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _policy_profile(
    *,
    write_allowlist: object | None = None,
    read_denylist: object | None = None,
) -> dict:
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
        "filesystem": {
            "write_allowlist": [] if write_allowlist is None else write_allowlist,
            "read_denylist": [] if read_denylist is None else read_denylist,
        },
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
