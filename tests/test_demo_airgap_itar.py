import json
from pathlib import Path

from tau_coding.demo_airgap_itar import DEMO_RECEIPT_SCHEMA, run_demo_airgap_itar_basic


def test_demo_airgap_itar_basic_writes_expected_artifacts(tmp_path: Path) -> None:
    receipt = run_demo_airgap_itar_basic(out=tmp_path)

    assert receipt["schema"] == DEMO_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    for filename in (
        "policy-profile.json",
        "data-boundary.json",
        "local-provider-readiness-receipt.json",
        "airgap-no-egress-receipt.json",
        "synthetic-contract-clause.txt",
        "itar-contract-receipt.json",
        "evidence-manifest.json",
        "evidence-validation-receipt.json",
        "dag-contract.json",
        "dag-receipt.json",
        "sparta-posture-contract.json",
        "proof-index.jsonl",
        "run-status.json",
        "run-receipt.json",
    ):
        assert (tmp_path / filename).exists(), filename


def test_demo_airgap_itar_basic_posture_not_signoff_ready(tmp_path: Path) -> None:
    receipt = run_demo_airgap_itar_basic(out=tmp_path)
    posture = json.loads((tmp_path / "sparta-posture-contract.json").read_text(encoding="utf-8"))

    assert receipt["demo_verdict"] == "NOT_SIGNOFF_READY"
    assert receipt["gate"] == "human_export_control_review_required"
    assert receipt["top_blocker"] == "human_export_control_review_required"
    assert posture["readiness"]["status"] == "NOT_SIGNOFF_READY"


def test_demo_airgap_itar_basic_contains_non_claims(tmp_path: Path) -> None:
    receipt = run_demo_airgap_itar_basic(out=tmp_path)

    assert "Synthetic data only." in receipt["non_claims"]
    assert "Does not prove ITAR compliance." in receipt["non_claims"]
    assert "Does not prove model approval." in receipt["non_claims"]
    assert "Does not prove airgap certification." in receipt["non_claims"]


def test_demo_airgap_itar_basic_builds_evidence_manifest(tmp_path: Path) -> None:
    run_demo_airgap_itar_basic(out=tmp_path)
    evidence_receipt = json.loads(
        (tmp_path / "evidence-validation-receipt.json").read_text(encoding="utf-8")
    )

    assert evidence_receipt["schema"] == "tau.evidence_validation_receipt.v1"
    assert evidence_receipt["status"] == "PASS"
    assert evidence_receipt["item_count"] == 5


def test_demo_airgap_itar_basic_run_status_available(tmp_path: Path) -> None:
    run_demo_airgap_itar_basic(out=tmp_path)
    run_status = json.loads((tmp_path / "run-status.json").read_text(encoding="utf-8"))

    assert run_status["schema"] == "tau.run_status.v1"
    assert run_status["ok"] is True
    assert run_status["status"] == "PASS"
    assert run_status["detected_type"] == "demo_airgap_itar_basic_receipt"
