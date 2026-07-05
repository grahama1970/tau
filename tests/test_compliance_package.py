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


def _write_zero_trust_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    contract_path = tmp_path / "dag-contract-source.json"
    policy_path = tmp_path / "policy-profile-source.json"
    boundary_path = tmp_path / "data-boundary-source.json"
    policy = {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "requires_data_boundary": True,
    }
    boundary = {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
    }
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
