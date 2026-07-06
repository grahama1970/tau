from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.itar_boundary import (
    ITAR_ACCESS_PREFLIGHT_RECEIPT_SCHEMA,
    write_itar_access_preflight_receipt,
)


def test_itar_access_preflight_blocks_unverified_us_person(tmp_path: Path) -> None:
    actor_path = _write_actor(tmp_path, us_person="unknown")
    boundary_path = _write_boundary(tmp_path)

    receipt = write_itar_access_preflight_receipt(
        actor_manifest_path=actor_path,
        data_boundary_path=boundary_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["schema"] == ITAR_ACCESS_PREFLIGHT_RECEIPT_SCHEMA
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "us_person_not_verified" in receipt["alert_codes"]
    assert receipt["recommended_action"] == {
        "type": "repair_actor_access",
        "next_agent": "human",
        "reason": (
            "Provide verified actor/access metadata and a matching human approval packet "
            "before controlled-boundary work proceeds."
        ),
    }


def test_itar_access_preflight_blocks_foreign_person_actor(tmp_path: Path) -> None:
    actor_path = _write_actor(tmp_path, foreign_person=True)
    boundary_path = _write_boundary(tmp_path)

    receipt = write_itar_access_preflight_receipt(
        actor_manifest_path=actor_path,
        data_boundary_path=boundary_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "foreign_person_actor_blocked" in receipt["alert_codes"]


def test_itar_access_preflight_blocks_agent_as_approver(tmp_path: Path) -> None:
    actor_path = _write_actor(tmp_path, actor_type="agent", roles=["approver"])
    boundary_path = _write_boundary(tmp_path)

    receipt = write_itar_access_preflight_receipt(
        actor_manifest_path=actor_path,
        data_boundary_path=boundary_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "agent_as_approver_rejected" in receipt["alert_codes"]


def test_itar_access_preflight_blocks_approval_actor_mismatch(tmp_path: Path) -> None:
    actor_path = _write_actor(tmp_path, actor_id="human:graham")
    boundary_path = _write_boundary(tmp_path)
    approval_path = _write_approval(tmp_path, actor_id="human:other")

    receipt = write_itar_access_preflight_receipt(
        actor_manifest_path=actor_path,
        data_boundary_path=boundary_path,
        approval_packet_path=approval_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "approval_actor_mismatch" in receipt["alert_codes"]


def test_itar_access_preflight_passes_verified_human_actor(tmp_path: Path) -> None:
    actor_path = _write_actor(tmp_path)
    boundary_path = _write_boundary(tmp_path)
    approval_path = _write_approval(tmp_path)

    receipt = write_itar_access_preflight_receipt(
        actor_manifest_path=actor_path,
        data_boundary_path=boundary_path,
        approval_packet_path=approval_path,
        receipt_path=tmp_path / "receipt.json",
    )
    written = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))

    assert receipt == written
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["alert_codes"] == []
    assert receipt["recommended_action"]["next_agent"] == "orchestrator"
    assert "ITAR compliance." in receipt["proof_scope"]["does_not_prove"]


def test_cli_itar_access_preflight_writes_fail_closed_receipt(tmp_path: Path) -> None:
    actor_path = _write_actor(tmp_path, verified=False)
    boundary_path = _write_boundary(tmp_path)
    receipt_path = tmp_path / "itar-access-preflight-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "itar-access-preflight",
            "--actor-manifest",
            str(actor_path),
            "--data-boundary",
            str(boundary_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    written = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert payload == written
    assert payload["schema"] == ITAR_ACCESS_PREFLIGHT_RECEIPT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert "actor_not_verified" in payload["alert_codes"]


def _write_actor(
    tmp_path: Path,
    *,
    actor_id: str = "human:graham",
    actor_type: str = "human",
    roles: list[str] | None = None,
    trusted: bool = True,
    verified: bool = True,
    us_person: str = "verified",
    foreign_person: bool = False,
    training_current: bool = True,
    approved_for_boundary: list[str] | None = None,
) -> Path:
    path = tmp_path / "actor-access-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.actor_access_manifest.v1",
                "actor_id": actor_id,
                "actor_type": actor_type,
                "roles": roles or ["approver"],
                "trusted": trusted,
                "verified": verified,
                "eligibility": {
                    "us_person": us_person,
                    "foreign_person": foreign_person,
                    "export_control_training_current": training_current,
                    "approved_for_boundary": approved_for_boundary or ["ITAR"],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_boundary(tmp_path: Path) -> Path:
    path = tmp_path / "itar-data-boundary.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.data_boundary.v1",
                "classification": "ITAR",
                "export_controlled": True,
                "itar": True,
                "technical_data": True,
                "external_provider_allowed": False,
                "external_research_allowed": False,
                "public_repo_allowed": False,
                "foreign_person_access": "prohibited",
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_approval(tmp_path: Path, *, actor_id: str = "human:graham") -> Path:
    path = tmp_path / "approval-packet.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.human_approval_packet.v1",
                "approved": True,
                "actor": {"id": actor_id, "auth_method": "manual"},
                "action": "provider_branch_scheduling",
                "target": {"id": "itar-boundary-demo"},
                "reason": "Fixture approval for actor/access preflight.",
                "evidence": ["actor access manifest"],
                "nonce": "itar-access-fixture",
                "signature": "fixture-signature",
            }
        ),
        encoding="utf-8",
    )
    return path
