"""Required-evidence loading and matching for Traycer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_required_evidence(raw: object) -> list[dict[str, Any]]:
    """Normalize required evidence from a policy object or handoff list."""

    if isinstance(raw, Mapping):
        raw_required = raw.get("required")
        if raw_required is None:
            raw_required = raw.get("required_evidence")
    else:
        raw_required = raw
    if not isinstance(raw_required, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_required):
        if isinstance(item, str):
            normalized.append(
                {
                    "id": item,
                    "description": item,
                    "severity": "REVIEW",
                }
            )
        elif isinstance(item, Mapping):
            item_id = item.get("id") or item.get("name") or item.get("key")
            if isinstance(item_id, str) and item_id:
                severity = item.get("severity", "REVIEW")
                normalized.append(
                    {
                        "id": item_id,
                        "description": item.get("description", item_id),
                        "severity": severity if severity in {"REVIEW", "BLOCK"} else "REVIEW",
                        "required_confidence": item.get("required_confidence"),
                    }
                )
        else:
            normalized.append(
                {
                    "id": f"invalid-required-evidence-{index}",
                    "description": "invalid required evidence entry",
                    "severity": "BLOCK",
                }
            )
    return normalized


def evidence_claim_supports(claim: Mapping[str, Any], required_id: str) -> bool:
    """Return whether an evidence claim supports a required evidence id."""

    claim_payload = claim.get("claim")
    if not isinstance(claim_payload, Mapping):
        return False
    supported = claim_payload.get("supports_required_evidence", [])
    return isinstance(supported, list) and required_id in supported


def evidence_claim_confidence(claim: Mapping[str, Any]) -> str | None:
    """Return the declared confidence for an evidence claim."""

    claim_payload = claim.get("claim")
    if not isinstance(claim_payload, Mapping):
        return None
    confidence = claim_payload.get("confidence")
    return confidence if isinstance(confidence, str) else None
