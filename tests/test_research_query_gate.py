from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.research_query_gate import (
    RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA,
    write_research_query_safety_receipt,
)


def test_research_query_gate_blocks_external_query_when_boundary_denies(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path, external_search="allow_with_approval")
    boundary_path = _write_boundary(tmp_path, external_research_allowed=False)

    receipt = write_research_query_safety_receipt(
        query="Find public papers about guidance systems",
        method="brave-search",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["schema"] == RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "external_research_not_allowed" in receipt["alert_codes"]
    assert receipt["recommended_action"] == {
        "type": "repair_research_query",
        "next_agent": "goal-guardian",
        "reason": (
            "Use a human-sanitized query and matching authorization before any external "
            "research call."
        ),
    }
    assert json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8")) == receipt


def test_research_query_gate_blocks_controlled_marker_under_itar_boundary(
    tmp_path: Path,
) -> None:
    policy_path = _write_policy(tmp_path, external_search="allow_with_approval")
    boundary_path = _write_boundary(tmp_path, external_research_allowed=True)
    query = "Search for ITAR controlled technical data handling examples"
    auth_path = _write_authorization(tmp_path, query=query)

    receipt = write_research_query_safety_receipt(
        query=query,
        method="brave-search",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        authorization_path=auth_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "controlled_marker_in_query" in receipt["alert_codes"]
    marker_alert = next(
        alert for alert in receipt["alerts"] if alert["code"] == "controlled_marker_in_query"
    )
    assert "itar" in marker_alert["evidence"]["markers"]


def test_research_query_gate_blocks_controlled_artifact_snippet(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path, external_search="allow_with_approval")
    boundary_path = _write_boundary(tmp_path, external_research_allowed=True)
    query = (
        "Please search this exact phrase: rotor actuator calibration detail "
        "alpha bravo charlie delta echo foxtrot"
    )
    auth_path = _write_authorization(tmp_path, query=query)
    controlled = tmp_path / "controlled.txt"
    controlled.write_text(
        "Rotor actuator calibration detail alpha bravo charlie delta echo foxtrot.",
        encoding="utf-8",
    )

    receipt = write_research_query_safety_receipt(
        query=query,
        method="brave-search",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        authorization_path=auth_path,
        controlled_artifact_paths=[controlled],
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "controlled_artifact_snippet_in_query" in receipt["alert_codes"]


def test_research_query_gate_passes_sanitized_authorized_query(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path, external_search="allow_with_approval")
    boundary_path = _write_boundary(tmp_path, external_research_allowed=True)
    query = "Find public NIST publications on secure research workflow review"
    auth_path = _write_authorization(tmp_path, query=query)

    receipt = write_research_query_safety_receipt(
        query=query,
        method="brave-search",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        authorization_path=auth_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["live"] is True
    assert receipt["external_tool_called"] is False
    assert receipt["alert_codes"] == []
    assert receipt["recommended_action"]["next_agent"] == "research-auditor"
    assert "ITAR compliance." in receipt["proof_scope"]["does_not_prove"]


def test_research_query_gate_blocks_query_swapped_after_authorization(
    tmp_path: Path,
) -> None:
    policy_path = _write_policy(tmp_path, external_search="allow_with_approval")
    boundary_path = _write_boundary(tmp_path, external_research_allowed=True)
    auth_path = _write_authorization(
        tmp_path,
        query="Find public NIST publications on secure research workflow review",
    )

    receipt = write_research_query_safety_receipt(
        query="Find public NIST publications on secure workflow review plus actuator tuning",
        method="brave-search",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        authorization_path=auth_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "research_authorization_invalid" in receipt["alert_codes"]
    auth_alert = next(
        alert for alert in receipt["alerts"] if alert["code"] == "research_authorization_invalid"
    )
    assert "authorized query hash does not match requested query" in auth_alert["evidence"]["errors"]


def test_research_query_gate_blocks_expired_authorization(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path, external_search="allow_with_approval")
    boundary_path = _write_boundary(tmp_path, external_research_allowed=True)
    query = "Find public NIST publications on secure research workflow review"
    auth_path = _write_authorization(tmp_path, query=query, expired=True)

    receipt = write_research_query_safety_receipt(
        query=query,
        method="brave-search",
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        authorization_path=auth_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "research_authorization_invalid" in receipt["alert_codes"]
    auth_alert = next(
        alert for alert in receipt["alerts"] if alert["code"] == "research_authorization_invalid"
    )
    assert "authorization is expired" in auth_alert["evidence"]["errors"]


def test_cli_research_query_gate_writes_fail_closed_receipt(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path, external_search="deny")
    boundary_path = _write_boundary(tmp_path, external_research_allowed=False)
    receipt_path = tmp_path / "query-safety-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "research-query-gate",
            "--query",
            "Find public papers about controlled technical data",
            "--method",
            "brave-search",
            "--policy-profile",
            str(policy_path),
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
    assert payload["schema"] == RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert {
        "external_research_not_allowed",
        "external_research_denied_by_policy",
        "controlled_marker_in_query",
        "research_authorization_invalid",
    }.issubset(set(payload["alert_codes"]))


def _write_policy(tmp_path: Path, *, external_search: str) -> Path:
    path = tmp_path / "policy-profile.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.policy_profile.v1",
                "profile_id": "itar-local-research-gate",
                "default_decision": "deny",
                "requires_data_boundary": True,
                "network": {"default": "deny"},
                "providers": {"cloud_llm": "deny", "local_model": "allow"},
                "research": {
                    "external_search": external_search,
                    "manual_sanitized_receipt": "allow",
                },
                "memory": {"read": "allow", "write": "approval_required"},
                "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
                "filesystem": {"write_allowlist": [str(tmp_path)], "read_denylist": []},
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_boundary(tmp_path: Path, *, external_research_allowed: bool) -> Path:
    path = tmp_path / "data-boundary.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.data_boundary.v1",
                "classification": "ITAR",
                "export_controlled": True,
                "itar": True,
                "technical_data": True,
                "external_provider_allowed": False,
                "external_research_allowed": external_research_allowed,
                "public_repo_allowed": False,
                "foreign_person_access": "prohibited",
                "notes": ["fixture boundary for research query safety tests"],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_authorization(tmp_path: Path, *, query: str, expired: bool = False) -> Path:
    path = tmp_path / "research-query-authorization.json"
    expires_at = datetime.now(UTC) + timedelta(days=1)
    if expired:
        expires_at = datetime.now(UTC) - timedelta(minutes=1)
    path.write_text(
        json.dumps(
            {
                "schema": "tau.research_query_authorization.v1",
                "approved": True,
                "allowed_methods": ["brave-search"],
                "sanitized_query_sha256": f"sha256:{_sha256_text(query)}",
                "data_boundary_classification": "ITAR",
                "approver": {"id": "human:graham"},
                "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ),
        encoding="utf-8",
    )
    return path


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
