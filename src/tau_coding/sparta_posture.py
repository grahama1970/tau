"""Export Tau run posture for Sparta Explorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SPARTA_POSTURE_SCHEMA = "tau.sparta_posture_contract.v1"

RECEIPT_FILENAMES = {
    "policy_profile": "policy-profile.json",
    "data_boundary": "data-boundary.json",
    "local_provider": "local-provider-readiness-receipt.json",
    "airgap_no_egress": "airgap-no-egress-receipt.json",
    "itar_contract": "itar-contract-receipt.json",
    "evidence_manifest": "evidence-manifest.json",
    "evidence_validation": "evidence-validation-receipt.json",
}


def write_sparta_posture_contract(
    *,
    run_dir: Path,
    out: Path,
    program: str = "synthetic-f36",
    system: str = "sparta-explorer",
    demo: bool = True,
) -> dict[str, Any]:
    """Export a Sparta-readable posture contract from Tau receipts."""

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_out = out.expanduser().resolve()
    receipts = _receipt_links(resolved_run_dir)
    loaded = {
        name: _read_json_if_present(resolved_run_dir / filename)
        for name, filename in RECEIPT_FILENAMES.items()
    }
    blockers = _top_blockers(loaded)
    missing_count = sum(1 for path in receipts.values() if path is None)
    stale_count = 0
    status = "NOT_SIGNOFF_READY" if blockers or missing_count else "SIGNOFF_REVIEW_READY"
    gate = blockers[0]["code"] if blockers else "human_review_required"
    summary = (
        "Synthetic ITAR-style clause requires human export-control review."
        if blockers
        else "Receipt set is present for human review; final authority remains human-only."
    )
    contract = {
        "schema": SPARTA_POSTURE_SCHEMA,
        "ok": status == "SIGNOFF_REVIEW_READY",
        "scope": {
            "program": program,
            "system": system,
            "demo": demo,
        },
        "readiness": {
            "status": status,
            "gate": gate,
            "summary": summary,
        },
        "top_blockers": blockers,
        "evidence_freshness": {
            "status": "current" if stale_count == 0 else "stale",
            "stale_count": stale_count,
            "missing_count": missing_count,
        },
        "receipts": receipts,
        "human_actions": _human_actions(blockers),
        "chat_boundary": {
            "chat_may_explain": True,
            "chat_may_author_verdict": False,
        },
        "non_claims": [
            "Does not prove ITAR compliance.",
            "Does not prove human approval.",
            "Does not prove operational readiness.",
        ],
    }
    resolved_out.parent.mkdir(parents=True, exist_ok=True)
    resolved_out.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return contract


def _receipt_links(run_dir: Path) -> dict[str, str | None]:
    links: dict[str, str | None] = {}
    for name, filename in RECEIPT_FILENAMES.items():
        path = run_dir / filename
        links[name] = str(path) if path.exists() else None
    return links


def _top_blockers(loaded: dict[str, dict[str, Any] | None]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    itar_receipt = loaded.get("itar_contract")
    if itar_receipt and itar_receipt.get("status") == "BLOCKED":
        for code in itar_receipt.get("alert_codes", []):
            human_action = (
                "export_control_review"
                if code == "human_export_control_review_required"
                else "human_review"
            )
            blockers.append(
                {
                    "id": f"BLOCKER-{len(blockers) + 1:03d}",
                    "severity": "BLOCK",
                    "code": code,
                    "source_receipt": RECEIPT_FILENAMES["itar_contract"],
                    "human_action": human_action,
                }
            )
    return blockers


def _human_actions(blockers: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not blockers:
        return [
            {
                "action": "human_review",
                "required_role": "authorized_reviewer",
                "reason": "Chat cannot author final signoff verdicts.",
            }
        ]
    actions: list[dict[str, str]] = []
    for blocker in blockers:
        if blocker.get("human_action") == "export_control_review":
            actions.append(
                {
                    "action": "export_control_review",
                    "required_role": "export_control_officer",
                    "reason": "Final compliance decision cannot be made by agent.",
                }
            )
        else:
            actions.append(
                {
                    "action": "human_review",
                    "required_role": "authorized_reviewer",
                    "reason": "Final signoff decision cannot be made by chat.",
                }
            )
    return actions


def _read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None
