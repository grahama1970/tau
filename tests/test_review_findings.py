import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.review_findings import (
    REVIEW_FINDINGS_SCHEMA,
    validate_review_findings,
    write_review_findings_receipt,
)


def test_review_findings_blocks_on_p0() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="BLOCKED",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P0",
                    "confidence": 0.9,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "The patch skips policy validation.",
                    "evidence": ["src/example.py:3 calls apply without policy"],
                    "required_action": "block",
                }
            ],
        ),
        expected_goal_hash="sha256:goal",
    )

    assert receipt["schema"] == REVIEW_FINDINGS_SCHEMA
    assert receipt["ok"] is True
    assert receipt["derived_verdict"] == "BLOCKED"
    assert receipt["blocking_finding_count"] == 1


def test_review_findings_revises_on_p1() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="REVISE",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P1",
                    "confidence": 0.8,
                    "file": "tests/test_example.py",
                    "line": 7,
                    "claim": "Missing stale-hash regression coverage.",
                    "evidence": ["tests omit stale base hash case"],
                    "required_action": "revise",
                }
            ],
        )
    )

    assert receipt["ok"] is True
    assert receipt["derived_verdict"] == "REVISE"
    assert receipt["revision_finding_count"] == 1


def test_review_findings_passes_with_no_blocking_findings(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps(_payload(verdict="PASS", findings=[])), encoding="utf-8")

    receipt = write_review_findings_receipt(
        findings_path=findings_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["derived_verdict"] == "PASS"
    assert receipt["finding_count"] == 0
    assert receipt["findings_sha256"] == f"sha256:{_sha256_file(findings_path)}"
    assert receipt["findings_bytes"] == findings_path.stat().st_size
    assert receipt["findings_artifact"] == {
        "label": "review_findings",
        "path": str(findings_path.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256_file(findings_path)}",
        "bytes": findings_path.stat().st_size,
    }
    assert "The reviewer is correct." in receipt["proof_scope"]["does_not_prove"]


def test_review_findings_writes_blocked_receipt_for_unreadable_findings(
    tmp_path: Path,
) -> None:
    findings_path = tmp_path / "findings.json"
    receipt_path = tmp_path / "receipt.json"
    findings_path.write_text("{not-json", encoding="utf-8")

    receipt = write_review_findings_receipt(
        findings_path=findings_path,
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "review_findings_unreadable" in receipt["alert_codes"]
    assert receipt["findings_sha256"] == f"sha256:{_sha256_file(findings_path)}"
    assert receipt["findings_bytes"] == findings_path.stat().st_size
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))


def test_review_findings_writes_blocked_receipt_for_missing_findings_artifact(
    tmp_path: Path,
) -> None:
    findings_path = tmp_path / "missing-findings.json"
    receipt_path = tmp_path / "receipt.json"

    receipt = write_review_findings_receipt(
        findings_path=findings_path,
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "review_findings_missing" in receipt["alert_codes"]
    assert receipt["findings_path"] == str(findings_path.resolve())
    assert receipt["findings_sha256"] is None
    assert receipt["findings_bytes"] is None
    assert receipt["findings_artifact"] == {
        "label": "review_findings",
        "path": str(findings_path.resolve()),
        "exists": False,
        "sha256": None,
        "bytes": None,
    }
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))


def test_review_findings_writes_blocked_receipt_for_non_object_findings(
    tmp_path: Path,
) -> None:
    findings_path = tmp_path / "findings.json"
    receipt_path = tmp_path / "receipt.json"
    findings_path.write_text("[]", encoding="utf-8")

    receipt = write_review_findings_receipt(
        findings_path=findings_path,
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "review_findings_not_object" in receipt["alert_codes"]
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))


def test_review_findings_blocks_goal_hash_mismatch() -> None:
    receipt = validate_review_findings(
        _payload(verdict="PASS", findings=[]),
        expected_goal_hash="sha256:other",
    )

    assert receipt["status"] == "BLOCKED"
    assert "goal_hash_mismatch" in receipt["alert_codes"]


def test_review_findings_requires_evidence_for_p0_p1() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="BLOCKED",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P0",
                    "confidence": 0.9,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "High-risk claim without evidence.",
                    "evidence": [],
                    "required_action": "block",
                }
            ],
        )
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_finding_evidence" in receipt["alert_codes"]


def test_review_findings_blocks_p3_escalating_beyond_note() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="REVISE",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P3",
                    "confidence": 0.6,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "Low-risk note should not drive rerouting.",
                    "evidence": ["src/example.py:3"],
                    "required_action": "revise",
                }
            ],
        )
    )

    assert receipt["status"] == "BLOCKED"
    assert "finding_action_overstates_severity" in receipt["alert_codes"]
    assert receipt["derived_verdict"] == "BLOCKED"


def test_review_findings_blocks_p2_note_without_waiver() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="PASS",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P2",
                    "confidence": 0.7,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "P2 was downgraded without an explicit waiver.",
                    "evidence": ["src/example.py:3"],
                    "required_action": "note",
                }
            ],
        )
    )

    assert receipt["status"] == "BLOCKED"
    assert "finding_action_understates_severity" in receipt["alert_codes"]
    assert receipt["derived_verdict"] == "BLOCKED"


def test_review_findings_accepts_explicit_p2_waiver() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="PASS",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P2",
                    "confidence": 0.7,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "P2 finding was accepted as a non-blocking note.",
                    "evidence": ["src/example.py:3"],
                    "required_action": "note",
                    "waiver": {
                        "approved": True,
                        "approved_by": "human:review-lead",
                        "reason": "Known low-risk follow-up tracked outside this route.",
                        "evidence": ["review-log:waiver-001"],
                    },
                }
            ],
        )
    )

    assert receipt["status"] == "PASS"
    assert receipt["derived_verdict"] == "PASS"
    assert receipt["findings"][0]["waiver"]["approved_by"] == "human:review-lead"


def test_review_findings_blocks_malformed_p2_waiver() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="PASS",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P2",
                    "confidence": 0.7,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "Malformed waiver should not downgrade a P2.",
                    "evidence": ["src/example.py:3"],
                    "required_action": "note",
                    "waiver": {"approved": True, "reason": "missing approver and evidence"},
                }
            ],
        )
    )

    assert receipt["status"] == "BLOCKED"
    assert "invalid_finding_waiver" in receipt["alert_codes"]
    assert "finding_action_understates_severity" in receipt["alert_codes"]


def test_review_findings_accepts_files_inside_allowed_paths() -> None:
    payload = _payload(
        verdict="REVISE",
        findings=[
            {
                "id": "finding-001",
                "severity": "P2",
                "confidence": 0.7,
                "file": "./src/example.py",
                "line": 3,
                "claim": "Needs a narrower helper.",
                "evidence": ["src/example.py:3"],
                "required_action": "revise",
            }
        ],
    )
    payload["allowed_paths"] = ["src/**", "tests/**"]
    payload["forbidden_paths"] = ["secrets/**"]

    receipt = validate_review_findings(payload)

    assert receipt["status"] == "PASS"
    assert receipt["allowed_paths"] == ["src/**", "tests/**"]
    assert receipt["forbidden_paths"] == ["secrets/**"]
    assert receipt["findings"][0]["file"] == "src/example.py"


def test_review_findings_blocks_file_outside_allowed_paths() -> None:
    payload = _payload(
        verdict="REVISE",
        findings=[
            {
                "id": "finding-001",
                "severity": "P2",
                "confidence": 0.7,
                "file": "docs/plan.md",
                "line": 3,
                "claim": "Reviewer drifted outside coding boundary.",
                "evidence": ["docs/plan.md:3"],
                "required_action": "revise",
            }
        ],
    )
    payload["allowed_paths"] = ["src/**", "tests/**"]

    receipt = validate_review_findings(payload)

    assert receipt["status"] == "BLOCKED"
    assert "finding_path_disallowed" in receipt["alert_codes"]


def test_review_findings_blocks_file_matching_forbidden_paths() -> None:
    payload = _payload(
        verdict="REVISE",
        findings=[
            {
                "id": "finding-001",
                "severity": "P2",
                "confidence": 0.7,
                "file": "secrets/token.txt",
                "line": 1,
                "claim": "Reviewer touched forbidden material.",
                "evidence": ["secrets/token.txt:1"],
                "required_action": "revise",
            }
        ],
    )
    payload["allowed_paths"] = ["src/**", "tests/**", "secrets/**"]
    payload["forbidden_paths"] = ["secrets/**"]

    receipt = validate_review_findings(payload)

    assert receipt["status"] == "BLOCKED"
    assert "finding_path_forbidden" in receipt["alert_codes"]


def test_review_findings_blocks_malformed_allowed_paths() -> None:
    payload = _payload(
        verdict="REVISE",
        findings=[
            {
                "id": "finding-001",
                "severity": "P2",
                "confidence": 0.7,
                "file": "src/example.py",
                "line": 3,
                "claim": "Malformed scope should not become permissive.",
                "evidence": ["src/example.py:3"],
                "required_action": "revise",
            }
        ],
    )
    payload["allowed_paths"] = "src/**"

    receipt = validate_review_findings(payload)

    assert receipt["status"] == "BLOCKED"
    assert "invalid_allowed_paths" in receipt["alert_codes"]
    assert receipt["allowed_paths"] == []


def test_review_findings_blocks_malformed_forbidden_paths() -> None:
    payload = _payload(
        verdict="REVISE",
        findings=[
            {
                "id": "finding-001",
                "severity": "P2",
                "confidence": 0.7,
                "file": "src/example.py",
                "line": 3,
                "claim": "Malformed forbidden scope should not be ignored.",
                "evidence": ["src/example.py:3"],
                "required_action": "revise",
            }
        ],
    )
    payload["forbidden_paths"] = ["secrets/**", ""]

    receipt = validate_review_findings(payload)

    assert receipt["status"] == "BLOCKED"
    assert "invalid_forbidden_paths" in receipt["alert_codes"]
    assert receipt["forbidden_paths"] == []


def test_review_findings_blocks_absolute_or_escaping_file_path() -> None:
    payload = _payload(
        verdict="REVISE",
        findings=[
            {
                "id": "finding-001",
                "severity": "P2",
                "confidence": 0.7,
                "file": "../outside.py",
                "line": 1,
                "claim": "Reviewer path escaped the repo boundary.",
                "evidence": ["../outside.py:1"],
                "required_action": "revise",
            }
        ],
    )

    receipt = validate_review_findings(payload)

    assert receipt["status"] == "BLOCKED"
    assert "finding_path_escape" in receipt["alert_codes"]


def test_review_findings_zero_trust_blocks_missing_policy_boundary() -> None:
    receipt = validate_review_findings(
        _payload(verdict="PASS", findings=[]),
        zero_trust=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_expected_goal_hash" in receipt["alert_codes"]
    assert "missing_policy_profile" in receipt["alert_codes"]
    assert "missing_data_boundary" in receipt["alert_codes"]


def test_review_findings_zero_trust_requires_expected_goal_hash() -> None:
    receipt = validate_review_findings(
        _payload(verdict="PASS", findings=[]),
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["goal_hash"] == "sha256:goal"
    assert "missing_expected_goal_hash" in receipt["alert_codes"]
    assert "goal_hash_mismatch" not in receipt["alert_codes"]


def test_review_findings_zero_trust_accepts_policy_boundary() -> None:
    receipt = validate_review_findings(
        _payload(verdict="PASS", findings=[]),
        expected_goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "PASS"
    assert receipt["zero_trust"] is True
    assert receipt["policy_profile"]["profile_id"] == "test"
    assert receipt["data_boundary"]["classification"] == "public"


def test_review_findings_zero_trust_blocks_findings_without_allowed_paths() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="REVISE",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P2",
                    "confidence": 0.7,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "Zero-trust reviewer finding lacks a path boundary.",
                    "evidence": ["src/example.py:3"],
                    "required_action": "revise",
                }
            ],
        ),
        expected_goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_allowed_paths" in receipt["alert_codes"]
    assert receipt["findings"][0]["file"] == "src/example.py"


def test_review_findings_zero_trust_blocks_invalid_data_boundary() -> None:
    boundary = _data_boundary()
    boundary["classification"] = "classified-not-allowed"
    boundary.pop("foreign_person_access")

    receipt = validate_review_findings(
        _payload(verdict="PASS", findings=[]),
        expected_goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=boundary,
    )

    assert receipt["status"] == "BLOCKED"
    assert "invalid_data_boundary" in receipt["alert_codes"]
    assert "classified_not_allowed" in receipt["alert_codes"]
    assert "foreign_person_access must be one of" in receipt["alerts"][0]["errors"][0]


def test_cli_review_findings_writes_receipt(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    receipt_path = tmp_path / "receipt.json"
    findings_path.write_text(json.dumps(_payload(verdict="PASS", findings=[])), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "review-findings",
            "--findings",
            str(findings_path),
            "--out",
            str(receipt_path),
            "--goal-hash",
            "sha256:goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["schema"] == REVIEW_FINDINGS_SCHEMA
    assert payload["derived_verdict"] == "PASS"


def test_cli_review_findings_zero_trust_missing_boundary_exits_blocked(
    tmp_path: Path,
) -> None:
    findings_path = tmp_path / "findings.json"
    receipt_path = tmp_path / "receipt.json"
    findings_path.write_text(json.dumps(_payload(verdict="PASS", findings=[])), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "review-findings",
            "--findings",
            str(findings_path),
            "--out",
            str(receipt_path),
            "--goal-hash",
            "sha256:goal",
            "--zero-trust",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "missing_expected_goal_hash" not in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_cli_review_findings_zero_trust_missing_goal_hash_exits_blocked(
    tmp_path: Path,
) -> None:
    findings_path = tmp_path / "findings.json"
    receipt_path = tmp_path / "receipt.json"
    policy_path = tmp_path / "policy-profile.json"
    boundary_path = tmp_path / "data-boundary.json"
    findings_path.write_text(json.dumps(_payload(verdict="PASS", findings=[])), encoding="utf-8")
    policy_path.write_text(json.dumps(_policy_profile()), encoding="utf-8")
    boundary_path.write_text(json.dumps(_data_boundary()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "review-findings",
            "--findings",
            str(findings_path),
            "--out",
            str(receipt_path),
            "--zero-trust",
            "--policy-profile",
            str(policy_path),
            "--data-boundary",
            str(boundary_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["goal_hash"] == "sha256:goal"
    assert "missing_expected_goal_hash" in payload["alert_codes"]


def test_cli_review_findings_unreadable_writes_blocked_receipt(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    receipt_path = tmp_path / "receipt.json"
    findings_path.write_text("{not-json", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "review-findings",
            "--findings",
            str(findings_path),
            "--out",
            str(receipt_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "review_findings_unreadable" in payload["alert_codes"]


def _payload(*, verdict: str, findings: list[dict]) -> dict:
    return {
        "schema": REVIEW_FINDINGS_SCHEMA,
        "goal_hash": "sha256:goal",
        "reviewer": "reviewer",
        "verdict": verdict,
        "findings": findings,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy_profile() -> dict:
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
        "filesystem": {"write_allowlist": ["src/**", "tests/**"], "read_denylist": []},
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
