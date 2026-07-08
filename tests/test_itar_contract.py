import json
from pathlib import Path

from tau_coding.init_project import initialize_tau_project
from tau_coding.itar_contract import (
    ITAR_CONTRACT_RECEIPT_SCHEMA,
    write_itar_contract_receipt,
)


def test_itar_contract_receipt_hashes_source(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    receipt = write_itar_contract_receipt(**paths)

    assert receipt["schema"] == ITAR_CONTRACT_RECEIPT_SCHEMA
    assert receipt["source_sha256"].startswith("sha256:")
    assert receipt["evidence"][0]["sha256"] == receipt["source_sha256"]


def test_itar_contract_receipt_detects_controlled_data_indicators(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    receipt = write_itar_contract_receipt(**paths)

    assert receipt["controlled_data_candidate"] is True
    assert "Clause references design drawings." in receipt["candidate_reasons"]
    assert "Clause references test procedures." in receipt["candidate_reasons"]
    assert "Clause references manufacturing process notes." in receipt["candidate_reasons"]


def test_itar_contract_receipt_routes_to_human(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    receipt = write_itar_contract_receipt(**paths)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["decision"] == "approval_required"
    assert receipt["required_human_role"] == "export_control_officer"
    assert receipt["alert_codes"] == ["human_export_control_review_required"]


def test_itar_contract_receipt_blocks_when_data_boundary_itar(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    receipt = write_itar_contract_receipt(**paths)

    assert receipt["data_boundary"]["schema"] == "tau.data_boundary.v1"
    assert receipt["access_constraint"] == "export_control_review_required"


def test_itar_contract_receipt_does_not_claim_compliance(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    receipt = write_itar_contract_receipt(**paths)

    non_claims = receipt["proof_scope"]["does_not_prove"]
    assert "ITAR compliance." in non_claims
    assert "Legal sufficiency." in non_claims
    assert "Correct USML classification." in non_claims
    assert "Human approval." in non_claims


def test_itar_contract_receipt_allows_no_indicator_public_clause(tmp_path: Path) -> None:
    initialize_tau_project(out_dir=tmp_path, profile="zero-trust")
    clause = tmp_path / "public-clause.txt"
    clause.write_text(
        "Synthetic Clause SC-002: Supplier shall submit a monthly public status summary.\n",
        encoding="utf-8",
    )
    boundary_path = tmp_path / ".tau" / "data-boundary.json"
    boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
    boundary["classification"] = "public"
    boundary["foreign_person_access"] = "allowed"
    boundary_path.write_text(
        json.dumps(boundary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    receipt = write_itar_contract_receipt(
        clause=clause,
        policy_profile=tmp_path / ".tau" / "policy-profile.json",
        data_boundary=boundary_path,
        out=tmp_path / "public-receipt.json",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["decision"] == "allow"
    assert receipt["controlled_data_candidate"] is False
    assert receipt["candidate_reasons"] == []


def _fixture_paths(tmp_path: Path) -> dict[str, Path]:
    initialize_tau_project(out_dir=tmp_path, profile="itar-airgap")
    clause = tmp_path / "synthetic-contract-clause.txt"
    clause.write_text(
        "Synthetic Clause SC-001:\n"
        "Supplier shall restrict access to controlled engineering package files,\n"
        "including design drawings, test procedures, and manufacturing process notes,\n"
        "to approved project personnel. Any external release, foreign-person access,\n"
        "or transfer outside the approved local environment requires written export\n"
        "control review and approval.\n"
        "This clause is synthetic demo text and is not copied from any real contract.\n",
        encoding="utf-8",
    )
    return {
        "clause": clause,
        "policy_profile": tmp_path / ".tau" / "policy-profile.json",
        "data_boundary": tmp_path / ".tau" / "data-boundary.json",
        "out": tmp_path / "itar-contract-receipt.json",
    }
