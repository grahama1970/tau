#!/usr/bin/env python3
"""Verify a Tau immutable-goal acceptance readback."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


class VerifyError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerifyError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise VerifyError(f"json_not_object:{path}")
    return payload


def _check_index_entry(entry: dict[str, Any], *, kind: str) -> None:
    path = Path(str(entry.get("path") or ""))
    expected = str(entry.get("sha256") or "")
    if not path.exists():
        raise VerifyError(f"{kind}_missing:{path}")
    if _sha256(path) != expected:
        raise VerifyError(f"{kind}_hash_mismatch:{path}")


def verify(readback_path: Path) -> dict[str, Any]:
    readback = _load_json(readback_path.resolve())
    if readback.get("schema") != "tau.immutable_goal_acceptance_readback.v1":
        raise VerifyError("readback_schema_mismatch")
    if readback.get("status") != "HUMAN_ACCEPTANCE_REQUIRED":
        raise VerifyError("readback_status_not_human_acceptance_required")
    if readback.get("immutable_goal_status") != "NOT_MET":
        raise VerifyError("readback_must_not_claim_immutable_goal_achieved")
    if readback.get("mocked") is not False or readback.get("live") is not True:
        raise VerifyError("readback_live_mocked_flags_invalid")

    audit_path = Path(str(readback.get("audit_path") or ""))
    manifest_path = Path(str(readback.get("artifact_manifest_path") or ""))
    if not audit_path.exists():
        raise VerifyError("audit_missing")
    if not manifest_path.exists():
        raise VerifyError("artifact_manifest_missing")
    if _sha256(audit_path) != readback.get("audit_sha256"):
        raise VerifyError("audit_hash_mismatch")
    if _sha256(manifest_path) != readback.get("artifact_manifest_sha256"):
        raise VerifyError("artifact_manifest_hash_mismatch")

    audit = _load_json(audit_path)
    if audit.get("schema") != "tau.immutable_goal_audit.v1":
        raise VerifyError("audit_schema_mismatch")
    if audit.get("source_ref") != readback.get("source_ref"):
        raise VerifyError("source_ref_mismatch")
    if audit.get("first_unmet_criterion") != 10:
        raise VerifyError("first_unmet_criterion_not_10")

    remaining = readback.get("remaining_unmet_criteria")
    if remaining != [{
        "criterion": 10,
        "evidence": ["Human acceptance is not recorded by this automated audit."],
        "status": "MISSING",
    }]:
        raise VerifyError("remaining_unmet_criteria_mismatch")
    human_acceptance = readback.get("human_acceptance")
    if not isinstance(human_acceptance, dict) or human_acceptance.get("accepted") is not False:
        raise VerifyError("human_acceptance_must_be_missing")

    proofs = readback.get("proof_index")
    screenshots = readback.get("screenshot_index")
    if not isinstance(proofs, list) or len(proofs) != 10:
        raise VerifyError("proof_index_count_mismatch")
    if not isinstance(screenshots, list) or len(screenshots) != 16:
        raise VerifyError("screenshot_index_count_mismatch")
    for proof in proofs:
        if proof.get("mocked") is not False or proof.get("live") is not True:
            raise VerifyError(f"proof_live_mocked_flags_invalid:{proof.get('label')}")
        _check_index_entry(proof, kind="proof")
    for screenshot in screenshots:
        _check_index_entry(screenshot, kind="screenshot")

    return {
        "schema": "tau.immutable_goal_acceptance_readback_verification.v1",
        "status": "PASS",
        "immutable_goal_status": "NOT_MET",
        "readback": str(readback_path.resolve()),
        "source_ref": readback.get("source_ref"),
        "remaining_unmet_criteria": remaining,
        "proof_count": len(proofs),
        "screenshot_count": len(screenshots),
        "mocked": False,
        "live": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("readback", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.readback), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
