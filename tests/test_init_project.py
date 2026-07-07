import json
from pathlib import Path

from tau_coding.init_project import INIT_RECEIPT_SCHEMA, initialize_tau_project
from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA


def test_init_zero_trust_creates_starter_files(tmp_path: Path) -> None:
    receipt = initialize_tau_project(out_dir=tmp_path, profile="zero-trust")

    assert receipt["schema"] == INIT_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    assert receipt["provider_live"] is False
    assert len(receipt["created_files"]) == 5

    tau_dir = tmp_path / ".tau"
    policy = json.loads((tau_dir / "policy-profile.json").read_text(encoding="utf-8"))
    boundary = json.loads((tau_dir / "data-boundary.json").read_text(encoding="utf-8"))
    command_policy = json.loads((tau_dir / "command-policy.json").read_text(encoding="utf-8"))
    dag = json.loads((tau_dir / "dag-template.json").read_text(encoding="utf-8"))
    readme = (tau_dir / "README.md").read_text(encoding="utf-8")

    assert policy["schema"] == POLICY_PROFILE_SCHEMA
    assert policy["default_decision"] == "deny"
    assert policy["requires_data_boundary"] is True
    assert boundary["schema"] == DATA_BOUNDARY_SCHEMA
    assert boundary["external_provider_allowed"] is False
    assert command_policy["schema"] == "tau.command_spec_policy.v1"
    assert command_policy["allows_network"] is False
    assert command_policy["allows_mutation"] is False
    assert dag["schema"] == "tau.dag_contract.v1"
    assert dag["policy_profile"] == ".tau/policy-profile.json"
    assert dag["data_boundary"] == ".tau/data-boundary.json"
    assert dag["command_policy"] == ".tau/command-policy.json"
    assert "does not prove ITAR compliance" in readme


def test_init_coding_zero_trust_creates_coding_evidence_template(tmp_path: Path) -> None:
    receipt = initialize_tau_project(out_dir=tmp_path, profile="coding-zero-trust")

    assert receipt["schema"] == INIT_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["profile"] == "coding-zero-trust"
    assert len(receipt["created_files"]) == 5

    tau_dir = tmp_path / ".tau"
    command_policy = json.loads((tau_dir / "command-policy.json").read_text(encoding="utf-8"))
    dag = json.loads((tau_dir / "dag-template.json").read_text(encoding="utf-8"))
    readme = (tau_dir / "README.md").read_text(encoding="utf-8")

    assert command_policy["schema"] == "tau.command_spec_policy.v1"
    assert "git" in command_policy["allowed_command_roots"]
    assert command_policy["allows_network"] is False
    assert dag["schema"] == "tau.dag_contract.v1"
    assert dag["dag_id"] == "coding-zero-trust"
    assert dag["coding_contract"] == {
        "schema": "tau.coding_contract.v1",
        "patch_receipts_required": True,
        "review_findings_required": True,
        "diagnostics_required": True,
        "commit_plan_dry_run_required": True,
        "course_correction_required_for_blocked_routes": True,
        "agent_truthfulness": "NOT_CLAIMED",
    }
    assert "tau.code_patch_receipt.v1 before applying code changes" in dag["required_evidence"]
    assert "tau.review_findings.v1 before PASS routing" in dag["required_evidence"]
    assert "semantic code correctness" in readme


def test_init_zero_trust_blocks_existing_files_without_force(tmp_path: Path) -> None:
    first = initialize_tau_project(out_dir=tmp_path, profile="zero-trust")
    second = initialize_tau_project(out_dir=tmp_path, profile="zero-trust")

    assert first["ok"] is True
    assert second["schema"] == INIT_RECEIPT_SCHEMA
    assert second["ok"] is False
    assert second["status"] == "BLOCKED"
    assert second["errors"] == ["existing_files"]
    assert ".tau/policy-profile.json" in second["existing_files"]


def test_init_zero_trust_force_rewrites_existing_files(tmp_path: Path) -> None:
    initialize_tau_project(out_dir=tmp_path, profile="zero-trust")
    marker = tmp_path / ".tau" / "README.md"
    marker.write_text("stale\n", encoding="utf-8")

    receipt = initialize_tau_project(out_dir=tmp_path, profile="zero-trust", force=True)

    assert receipt["ok"] is True
    assert "stale" not in marker.read_text(encoding="utf-8")


def test_init_blocks_unknown_profile(tmp_path: Path) -> None:
    try:
        initialize_tau_project(out_dir=tmp_path, profile="default")
    except ValueError as exc:
        assert "unsupported init profile" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected unsupported profile to raise ValueError")
