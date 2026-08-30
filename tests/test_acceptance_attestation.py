"""Human acceptance attestation verifier tests for tau#305."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tau_coding.acceptance_attestation import (
    ACCEPTANCE_ATTESTATION_SCHEMA,
    DEFAULT_ACCEPTANCE_ATTESTATION,
    DEFAULT_ACCEPTANCE_BASELINE,
    sha256_file,
    verify_acceptance_attestation,
)

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _write_json(root / DEFAULT_ACCEPTANCE_BASELINE, {"schema": "tau.provider_live_baseline.v1", "ok": True})
    _write_json(
        root / "docs" / "proofs" / "tickets" / "issue-304" / "provider-live-acceptance-receipt.json",
        {"schema": "tau.workflow_provider_live_acceptance_receipt.v1", "provider_live": True},
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _valid_attestation(root: Path, head: str) -> dict[str, object]:
    proof_path = "docs/proofs/tickets/issue-304/provider-live-acceptance-receipt.json"
    return {
        "schema": ACCEPTANCE_ATTESTATION_SCHEMA,
        "signer": {
            "id": "graham",
            "name": "Graham Anderson",
            "authority_class": "human_operator",
        },
        "decision": "ACCEPTED",
        "attested_at": "2026-08-30T11:59:00Z",
        "expires_at": "2026-12-31T00:00:00Z",
        "source_commit": head,
        "baseline": {
            "path": DEFAULT_ACCEPTANCE_BASELINE.as_posix(),
            "sha256": sha256_file(root / DEFAULT_ACCEPTANCE_BASELINE),
        },
        "proof_receipt": {
            "path": proof_path,
            "sha256": sha256_file(root / proof_path),
        },
    }


def test_valid_attestation_accepts_exact_baseline_commit_and_receipt(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    _write_json(tmp_path / DEFAULT_ACCEPTANCE_ATTESTATION, _valid_attestation(tmp_path, head))

    result = verify_acceptance_attestation(tmp_path, now=_NOW)

    assert result["status"] == "ACCEPTED"
    assert result["ok"] is True
    assert result["state"] == "VERIFIED_ACCEPTANCE"
    assert result["failure_codes"] == []
    assert result["source_commit"] == head
    if output_path := os.environ.get("TAU_ACCEPTANCE_ATTESTATION_PROOF"):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_missing_attestation_is_pending_not_accepted(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    result = verify_acceptance_attestation(tmp_path, now=_NOW)

    assert result["status"] == "PENDING_HUMAN_SIGNATURE"
    assert result["ok"] is False
    assert result["failure_codes"] == ["human_acceptance_attestation_missing"]


def test_mutated_baseline_digest_is_rejected(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    attestation = _valid_attestation(tmp_path, head)
    _write_json(tmp_path / DEFAULT_ACCEPTANCE_ATTESTATION, attestation)
    _write_json(tmp_path / DEFAULT_ACCEPTANCE_BASELINE, {"schema": "mutated", "ok": False})

    result = verify_acceptance_attestation(tmp_path, now=_NOW)

    assert result["status"] == "BLOCKED"
    assert "baseline_receipt_digest_mismatch" in result["failure_codes"]


def test_mutated_source_commit_signer_timestamp_and_proof_digest_are_rejected(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    attestation = copy.deepcopy(_valid_attestation(tmp_path, head))
    attestation["source_commit"] = "0" * 40
    attestation["signer"] = {"id": "bot", "authority_class": "project_watchdog"}
    attestation["attested_at"] = (_NOW + timedelta(days=1)).isoformat()
    proof = attestation["proof_receipt"]
    assert isinstance(proof, dict)
    proof["sha256"] = "sha256:" + "0" * 64
    _write_json(tmp_path / DEFAULT_ACCEPTANCE_ATTESTATION, attestation)

    result = verify_acceptance_attestation(tmp_path, now=_NOW)

    assert result["status"] == "BLOCKED"
    assert "source_commit_mismatch" in result["failure_codes"]
    assert "signer_authority_invalid" in result["failure_codes"]
    assert "attestation_from_future" in result["failure_codes"]
    assert "proof_receipt_digest_mismatch" in result["failure_codes"]


def test_expired_or_non_accepted_attestation_is_rejected(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    attestation = _valid_attestation(tmp_path, head)
    attestation["decision"] = "REJECTED"
    attestation["expires_at"] = "2026-08-01T00:00:00Z"
    _write_json(tmp_path / DEFAULT_ACCEPTANCE_ATTESTATION, attestation)

    result = verify_acceptance_attestation(tmp_path, now=_NOW)

    assert result["status"] == "BLOCKED"
    assert "decision_not_accepted" in result["failure_codes"]
    assert "attestation_expired" in result["failure_codes"]
