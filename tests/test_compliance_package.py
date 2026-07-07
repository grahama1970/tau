import hashlib
import json
from pathlib import Path

from tau_coding.compliance_package import (
    COMPLIANCE_PACKAGE_SCHEMA,
    build_compliance_evidence_package,
)


def test_compliance_package_collects_zero_trust_dag_artifacts(tmp_path: Path) -> None:
    run_dir = _write_zero_trust_run(tmp_path)
    out_dir = tmp_path / "package"

    manifest = build_compliance_evidence_package(run_dir=run_dir, out_dir=out_dir)

    assert manifest["schema"] == COMPLIANCE_PACKAGE_SCHEMA
    assert manifest["ok"] is True
    assert manifest["status"] == "PASS"
    assert manifest["mocked"] is False
    assert manifest["live"] is False
    assert (out_dir / "package-manifest.json").exists()
    assert (out_dir / "dag-receipt.json").exists()
    assert (out_dir / "dag-contract.json").exists()
    assert (out_dir / "goal.json").exists()
    assert (out_dir / "policy-profile.json").exists()
    assert (out_dir / "data-boundary.json").exists()
    assert (out_dir / "zero-trust-preflight-receipt.json").exists()
    assert (out_dir / "memory-intent-gate-receipt.json").exists()
    assert (out_dir / "evidence-case-gate-receipt.json").exists()
    assert (out_dir / "evidence-validation-receipt.json").exists()
    assert (out_dir / "non-claims.md").exists()
    assert "ITAR compliance." in (out_dir / "non-claims.md").read_text(encoding="utf-8")
    item_kinds = {item["kind"] for item in manifest["items"]}
    assert {
        "dag_receipt",
        "dag_contract",
        "goal",
        "policy_profile",
        "data_boundary",
        "zero_trust_preflight",
        "memory_intent_gate",
        "evidence_case_gate",
        "evidence_validation",
        "non_claims",
    }.issubset(item_kinds)
    assert all(str(item["sha256"]).startswith("sha256:") for item in manifest["items"])


def test_compliance_package_collects_coding_evidence_receipts(tmp_path: Path) -> None:
    run_dir = _write_zero_trust_run(tmp_path)
    receipt_dir = run_dir / "receipts"
    receipt_dir.mkdir()
    coding_receipts = {
        "code-patch-receipt.json": {
            "schema": "tau.code_patch_receipt.v1",
            "status": "PASS",
            "ok": True,
        },
        "test-run-receipt.json": {
            "schema": "tau.test_run_receipt.v1",
            "status": "PASS",
            "ok": True,
        },
        "review-findings-receipt.json": {
            "schema": "tau.review_findings.v1",
            "status": "PASS",
            "ok": True,
        },
        "commit-plan-receipt.json": {
            "schema": "tau.commit_plan_receipt.v1",
            "status": "PASS",
            "ok": True,
        },
        "github-read-receipt.json": {
            "schema": "tau.github_read_receipt.v1",
            "status": "PASS",
            "ok": True,
        },
        "course-correction-receipt.json": {
            "schema": "tau.course_correction.v1",
            "status": "REQUIRED",
            "ok": False,
            "trigger": "patch_stale",
        },
        "orchestration-reliability-receipt.json": {
            "schema": "tau.orchestration_reliability_receipt.v1",
            "status": "PASS",
            "ok": True,
        },
    }
    for name, payload in coding_receipts.items():
        _write_json(receipt_dir / name, payload)
    out_dir = tmp_path / "package"

    manifest = build_compliance_evidence_package(run_dir=run_dir, out_dir=out_dir)

    assert manifest["ok"] is True
    copied_dir = out_dir / "coding-evidence-receipts"
    for name in coding_receipts:
        assert (copied_dir / name).exists()
    coding_items = [
        item for item in manifest["items"] if item["kind"] == "coding-evidence-receipts"
    ]
    assert len(coding_items) == len(coding_receipts)
    assert {item["schema"] for item in coding_items} == {
        payload["schema"] for payload in coding_receipts.values()
    }
    assert not any(
        missing["kind"] == "coding-evidence-receipts"
        for missing in manifest["missing_expected_items"]
    )


def test_compliance_package_manifest_hash_scope_is_explicit(tmp_path: Path) -> None:
    run_dir = _write_zero_trust_run(tmp_path)
    out_dir = tmp_path / "package"

    manifest = build_compliance_evidence_package(run_dir=run_dir, out_dir=out_dir)
    written = json.loads((out_dir / "package-manifest.json").read_text(encoding="utf-8"))
    payload_without_manifest_metadata = dict(written)
    for key in (
        "manifest_hash_scope",
        "manifest_path",
        "manifest_payload_bytes",
        "manifest_payload_sha256",
    ):
        payload_without_manifest_metadata.pop(key)
    canonical = (
        json.dumps(payload_without_manifest_metadata, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    assert manifest == written
    assert written["manifest_payload_sha256"] == (
        f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    )
    assert written["manifest_payload_bytes"] == len(canonical)
    assert "cannot contain a stable SHA-256 hash of their own final bytes" in written[
        "manifest_hash_scope"
    ]


def test_compliance_package_blocks_nonempty_output_without_force(tmp_path: Path) -> None:
    run_dir = _write_zero_trust_run(tmp_path)
    out_dir = tmp_path / "package"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("keep", encoding="utf-8")

    manifest = build_compliance_evidence_package(run_dir=run_dir, out_dir=out_dir)

    assert manifest["ok"] is False
    assert manifest["status"] == "BLOCKED"
    assert "out_dir is not empty" in manifest["errors"][0]
    assert (out_dir / "existing.txt").exists()


def test_compliance_package_force_rewrites_output(tmp_path: Path) -> None:
    run_dir = _write_zero_trust_run(tmp_path)
    out_dir = tmp_path / "package"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("remove", encoding="utf-8")

    manifest = build_compliance_evidence_package(run_dir=run_dir, out_dir=out_dir, force=True)

    assert manifest["ok"] is True
    assert not (out_dir / "existing.txt").exists()
    assert (out_dir / "package-manifest.json").exists()


def test_compliance_package_blocks_invalid_packaged_data_boundary(tmp_path: Path) -> None:
    run_dir = _write_zero_trust_run(
        tmp_path,
        data_boundary={
            **_valid_data_boundary(),
            "classification": "classified-not-allowed",
            "foreign_person_access": "invalid",
        },
    )
    out_dir = tmp_path / "package"

    manifest = build_compliance_evidence_package(run_dir=run_dir, out_dir=out_dir)

    assert manifest["ok"] is False
    assert manifest["status"] == "BLOCKED"
    assert any(error.startswith("invalid_data_boundary:") for error in manifest["errors"])
    assert any(error.startswith("classified_not_allowed:") for error in manifest["errors"])
    assert "ITAR compliance." in manifest["proof_scope"]["does_not_prove"]


def _write_zero_trust_run(
    tmp_path: Path,
    *,
    policy_profile: dict | None = None,
    data_boundary: dict | None = None,
) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    contract_path = tmp_path / "dag-contract-source.json"
    policy_path = tmp_path / "policy-profile-source.json"
    boundary_path = tmp_path / "data-boundary-source.json"
    policy = policy_profile or _valid_policy_profile()
    boundary = data_boundary or _valid_data_boundary()
    _write_json(policy_path, policy)
    _write_json(boundary_path, boundary)
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "package-test",
        "goal": {
            "goal_id": "package-test",
            "goal_version": 1,
            "goal_hash": "sha256:package-test",
        },
        "policy_profile": str(policy_path),
        "data_boundary": str(boundary_path),
    }
    _write_json(contract_path, contract)
    receipts = {
        "dag-receipt.json": {
            "schema": "tau.dag_receipt.v1",
            "ok": True,
            "status": "PASS",
            "contract_path": str(contract_path),
            "zero_trust_preflight_receipt": str(run_dir / "zero-trust-preflight-receipt.json"),
            "memory_intent_gate_receipt": str(run_dir / "memory-intent-gate-receipt.json"),
            "evidence_case_gate_receipt": str(run_dir / "evidence-case-gate-receipt.json"),
            "evidence_validation_receipt": str(run_dir / "evidence-validation-receipt.json"),
        },
        "zero-trust-preflight-receipt.json": {
            "schema": "tau.zero_trust_preflight_receipt.v1",
            "ok": True,
            "status": "PASS",
        },
        "memory-intent-gate-receipt.json": {
            "schema": "tau.memory_intent_gate_receipt.v1",
            "ok": True,
            "status": "PASS",
        },
        "evidence-case-gate-receipt.json": {
            "schema": "tau.evidence_case_gate_receipt.v1",
            "ok": True,
            "status": "PASS",
        },
        "evidence-validation-receipt.json": {
            "schema": "tau.evidence_validation_receipt.v1",
            "ok": True,
            "status": "PASS",
        },
    }
    for name, payload in receipts.items():
        _write_json(run_dir / name, payload)
    return run_dir


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _valid_policy_profile() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
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
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _valid_data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": True,
        "notes": [],
    }
