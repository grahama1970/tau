import json
from pathlib import Path

from tau_coding.init_project import initialize_tau_project
from tau_coding.itar_contract import write_itar_contract_receipt
from tau_coding.sparta_posture import SPARTA_POSTURE_SCHEMA, write_sparta_posture_contract


def test_sparta_posture_exports_not_signoff_ready_for_blocked_itar_receipt(
    tmp_path: Path,
) -> None:
    run_dir = _blocked_run_dir(tmp_path)

    contract = write_sparta_posture_contract(
        run_dir=run_dir,
        out=run_dir / "sparta-posture-contract.json",
    )

    assert contract["schema"] == SPARTA_POSTURE_SCHEMA
    assert contract["readiness"]["status"] == "NOT_SIGNOFF_READY"
    assert contract["readiness"]["gate"] == "human_export_control_review_required"
    assert contract["ok"] is False


def test_sparta_posture_includes_top_blocker(tmp_path: Path) -> None:
    run_dir = _blocked_run_dir(tmp_path)

    contract = write_sparta_posture_contract(
        run_dir=run_dir,
        out=run_dir / "sparta-posture-contract.json",
    )

    assert contract["top_blockers"] == [
        {
            "id": "BLOCKER-001",
            "severity": "BLOCK",
            "code": "human_export_control_review_required",
            "source_receipt": "itar-contract-receipt.json",
            "human_action": "export_control_review",
        }
    ]
    assert contract["human_actions"][0]["required_role"] == "export_control_officer"


def test_sparta_posture_links_receipts(tmp_path: Path) -> None:
    run_dir = _blocked_run_dir(tmp_path)

    contract = write_sparta_posture_contract(
        run_dir=run_dir,
        out=run_dir / "sparta-posture-contract.json",
    )

    assert contract["receipts"]["policy_profile"] == str(run_dir / "policy-profile.json")
    assert contract["receipts"]["data_boundary"] == str(run_dir / "data-boundary.json")
    assert contract["receipts"]["itar_contract"] == str(run_dir / "itar-contract-receipt.json")


def test_sparta_posture_chat_cannot_author_verdict(tmp_path: Path) -> None:
    run_dir = _blocked_run_dir(tmp_path)

    contract = write_sparta_posture_contract(
        run_dir=run_dir,
        out=run_dir / "sparta-posture-contract.json",
    )

    assert contract["chat_boundary"] == {
        "chat_may_explain": True,
        "chat_may_author_verdict": False,
    }


def test_sparta_posture_non_claims_present(tmp_path: Path) -> None:
    run_dir = _blocked_run_dir(tmp_path)

    contract = write_sparta_posture_contract(
        run_dir=run_dir,
        out=run_dir / "sparta-posture-contract.json",
    )

    assert "Does not prove ITAR compliance." in contract["non_claims"]
    assert "Does not prove human approval." in contract["non_claims"]
    assert "Does not prove operational readiness." in contract["non_claims"]


def _blocked_run_dir(tmp_path: Path) -> Path:
    initialize_tau_project(out_dir=tmp_path, profile="itar-airgap")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in ("policy-profile.json", "data-boundary.json"):
        (run_dir / name).write_text(
            (tmp_path / ".tau" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    clause = run_dir / "synthetic-contract-clause.txt"
    clause.write_text(
        "Synthetic Clause SC-001: design drawings, test procedures, "
        "manufacturing process notes, foreign-person access, and external release.\n",
        encoding="utf-8",
    )
    write_itar_contract_receipt(
        clause=clause,
        policy_profile=run_dir / "policy-profile.json",
        data_boundary=run_dir / "data-boundary.json",
        out=run_dir / "itar-contract-receipt.json",
    )
    (run_dir / "local-provider-readiness-receipt.json").write_text(
        json.dumps(
            {"schema": "tau.local_provider_readiness_receipt.v1", "status": "PASS"},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "airgap-no-egress-receipt.json").write_text(
        json.dumps(
            {"schema": "tau.airgap_no_egress_receipt.v1", "status": "PASS"},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir
