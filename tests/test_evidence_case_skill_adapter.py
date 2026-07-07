import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.evidence_case_skill_adapter import (
    EVIDENCE_CASE_SKILL_ADAPTER_RECEIPT_SCHEMA,
    write_evidence_case_skill_adapter_receipt,
)
from tau_coding.memory_evidence_gate import EVIDENCE_CASE_GATE_RECEIPT_SCHEMA
from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA


def test_evidence_case_adapter_accepts_valid_case(tmp_path: Path) -> None:
    case = _write_case(tmp_path)

    receipt = write_evidence_case_skill_adapter_receipt(
        case_path=case,
        output_path=tmp_path / "evidence-case-adapter-receipt.json",
        repo_root=tmp_path,
        expected_goal_hash="sha256:goal",
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    gate = json.loads((tmp_path / "evidence-case-gate-receipt.json").read_text(encoding="utf-8"))
    normalized = json.loads((tmp_path / "evidence-case.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == EVIDENCE_CASE_SKILL_ADAPTER_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["support_artifact_count"] == 1
    assert receipt["evidence_case_gate_status"] == "PASS"
    assert gate["schema"] == EVIDENCE_CASE_GATE_RECEIPT_SCHEMA
    assert normalized["schema"] == "memory.evidence_case.v1"
    assert normalized["support_artifacts"][0]["sha256"].startswith("sha256:")


def test_evidence_case_adapter_blocks_missing_question_or_claim(tmp_path: Path) -> None:
    case = _write_case(tmp_path)
    payload = json.loads(case.read_text(encoding="utf-8"))
    payload.pop("question")
    payload.pop("claim", None)
    case.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_evidence_case_skill_adapter_receipt(
        case_path=case,
        output_path=tmp_path / "evidence-case-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "question or claim is required" in receipt["errors"]
    assert receipt["course_correction"]["required_next_action"] == "route_evidence_case"


def test_evidence_case_adapter_blocks_goal_hash_mismatch(tmp_path: Path) -> None:
    case = _write_case(tmp_path)

    receipt = write_evidence_case_skill_adapter_receipt(
        case_path=case,
        output_path=tmp_path / "evidence-case-adapter-receipt.json",
        repo_root=tmp_path,
        expected_goal_hash="sha256:other",
    )

    assert receipt["status"] == "BLOCKED"
    assert "goal_hash mismatches expected_goal_hash" in receipt["errors"]
    assert "evidence_case_goal_hash_mismatch" in receipt["evidence_case_gate_alert_codes"]


def test_evidence_case_adapter_blocks_support_artifact_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-evidence.txt"
    outside.write_text("outside\n", encoding="utf-8")
    case = _write_case(tmp_path, support_artifacts=[str(outside)])

    receipt = write_evidence_case_skill_adapter_receipt(
        case_path=case,
        output_path=tmp_path / "evidence-case-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert any("escapes repo root" in error for error in receipt["errors"])


def test_evidence_case_adapter_blocks_boundary_policy_mismatch(tmp_path: Path) -> None:
    case = _write_case(tmp_path, data_boundary={**_data_boundary(), "classification": "internal"})

    receipt = write_evidence_case_skill_adapter_receipt(
        case_path=case,
        output_path=tmp_path / "evidence-case-adapter-receipt.json",
        repo_root=tmp_path,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "BLOCKED"
    assert "data_boundary mismatches create-evidence-case artifact" in receipt["errors"]


def test_evidence_case_adapter_blocks_boundary_id_mismatch(tmp_path: Path) -> None:
    case = _write_case(tmp_path, data_boundary={**_data_boundary(), "boundary_id": "public"})

    receipt = write_evidence_case_skill_adapter_receipt(
        case_path=case,
        output_path=tmp_path / "evidence-case-adapter-receipt.json",
        repo_root=tmp_path,
        data_boundary={**_data_boundary(), "boundary_id": "controlled"},
    )

    assert receipt["status"] == "BLOCKED"
    assert "data_boundary mismatches create-evidence-case artifact" in receipt["errors"]


def test_cli_evidence_case_skill_adapter_writes_receipt(tmp_path: Path) -> None:
    case = _write_case(tmp_path)
    out = tmp_path / "evidence-case-adapter-receipt.json"
    policy = tmp_path / "policy-profile.json"
    boundary = tmp_path / "data-boundary.json"
    policy.write_text(json.dumps(_policy_profile()), encoding="utf-8")
    boundary.write_text(json.dumps(_data_boundary()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evidence-case-skill-adapter",
            "--case",
            str(case),
            "--out",
            str(out),
            "--repo-root",
            str(tmp_path),
            "--goal-hash",
            "sha256:goal",
            "--policy-profile",
            str(policy),
            "--data-boundary",
            str(boundary),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == EVIDENCE_CASE_SKILL_ADAPTER_RECEIPT_SCHEMA
    assert payload["evidence_case_gate_status"] == "PASS"


def _write_case(
    tmp_path: Path,
    *,
    support_artifacts: list[str | dict[str, object]] | None = None,
    data_boundary: dict | None = None,
) -> Path:
    support = tmp_path / "support.json"
    support.write_text('{"source":"unit"}\n', encoding="utf-8")
    payload = {
        "schema": "create_evidence_case.result.v1",
        "status": "PASS",
        "goal_hash": "sha256:goal",
        "question": "Does Tau have evidence before dispatch?",
        "claim": "Tau requires separate evidence artifacts.",
        "answer": "Evidence is bound through receipts.",
        "verdict": "SATISFIED",
        "evidence_case": {"chains": [], "confidence": 0.8},
        "support_artifacts": support_artifacts
        if support_artifacts is not None
        else [{"path": "support.json", "schema": "unit.support.v1"}],
        "data_boundary": data_boundary if data_boundary is not None else _data_boundary(),
        "policy_profile": _policy_profile(),
    }
    path = tmp_path / "create-evidence-case-result.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _policy_profile() -> dict:
    return {
        "schema": POLICY_PROFILE_SCHEMA,
        "profile_id": "test",
        "default_decision": "deny",
    }


def _data_boundary() -> dict:
    return {
        "schema": DATA_BOUNDARY_SCHEMA,
        "boundary_id": "public",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
    }
