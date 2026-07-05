"""Source-bearing research receipts for Tau design input."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESEARCH_SOURCE_PACKET_SCHEMA = "tau.research_source_packet.v1"
RESEARCH_SOURCE_RECEIPT_SCHEMA = "tau.research_source_receipt.v1"
SOURCE_TYPES = {"github", "manual", "paper", "video", "web"}
METHODS = {"arxiv", "brave", "dogpile", "manual", "webgpt"}
RELEVANCE = {"HIGH", "MEDIUM", "LOW"}
CLASSIFICATIONS = {
    "design_input",
    "evidence_candidate",
    "implementation_constraint",
    "not_closure_proof",
}


def write_research_source_receipt(
    *,
    source_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    """Validate a research source packet and write a review-required receipt."""

    resolved_source = source_path.expanduser().resolve()
    resolved_receipt = receipt_path.expanduser().resolve()
    errors: list[str] = []
    source = _read_json_object(resolved_source, errors=errors)
    validation = _validate_source_packet(source) if source else []
    errors.extend(validation)
    sources = source.get("sources") if isinstance(source.get("sources"), list) else []
    ok = not errors
    receipt = {
        "schema": RESEARCH_SOURCE_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "source_packet": str(resolved_source),
        "source_packet_sha256": _safe_file_sha256(resolved_source),
        "source_type": source.get("source_type"),
        "method": source.get("method"),
        "query": source.get("query"),
        "retrieved_at": source.get("retrieved_at"),
        "source_count": len(sources),
        "arxiv_source_count": _arxiv_source_count(sources),
        "sources": sources,
        "classification": source.get("classification"),
        "summary": source.get("summary"),
        "limitations": (
            source.get("limitations") if isinstance(source.get("limitations"), list) else []
        ),
        "review_required": True,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "A research source packet was inspected deterministically.",
                "Required source metadata, source URLs, relevance labels, and "
                "classifications were validated.",
                "Research is marked review-required before it can affect Tau routing "
                "or implementation.",
            ],
            "does_not_prove": [
                "The cited sources are true.",
                "The research was fetched live by this command.",
                "The research is sufficient for implementation or closure.",
                "Any Tau runtime behavior changed because of the research.",
            ],
        },
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt["receipt_path"] = str(resolved_receipt)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _read_json_object(path: Path, *, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"research source packet is unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"research source packet root must be a JSON object: {path}")
        return {}
    return payload


def _validate_source_packet(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("schema") != RESEARCH_SOURCE_PACKET_SCHEMA:
        errors.append(f"schema must be {RESEARCH_SOURCE_PACKET_SCHEMA}")
    if packet.get("source_type") not in SOURCE_TYPES:
        errors.append(f"source_type must be one of {sorted(SOURCE_TYPES)}")
    if packet.get("method") not in METHODS:
        errors.append(f"method must be one of {sorted(METHODS)}")
    _require_non_empty_string(packet, "query", errors=errors)
    retrieved_at = packet.get("retrieved_at")
    if not isinstance(retrieved_at, str) or _parse_timestamp(retrieved_at) is None:
        errors.append("retrieved_at must be an ISO-8601 timestamp string")
    classification = packet.get("classification")
    if classification not in CLASSIFICATIONS:
        errors.append(f"classification must be one of {sorted(CLASSIFICATIONS)}")
    sources = packet.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources must be a non-empty list")
        sources = []
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            errors.append(f"sources[{index}] must be an object")
            continue
        _validate_source_entry(source, index=index, method=packet.get("method"), errors=errors)
    if not isinstance(packet.get("summary"), str) or not packet["summary"].strip():
        errors.append("summary must be a non-empty string")
    limitations = packet.get("limitations")
    if limitations is not None and not _is_string_list(limitations):
        errors.append("limitations must be a list of strings when present")
    return errors


def _validate_source_entry(
    source: Mapping[str, Any],
    *,
    index: int,
    method: str | None = None,
    errors: list[str],
) -> None:
    prefix = f"sources[{index}]"
    _require_non_empty_string(source, "title", errors=errors, prefix=prefix)
    _require_non_empty_string(source, "url", errors=errors, prefix=prefix)
    relevance = source.get("relevance")
    if relevance not in RELEVANCE:
        errors.append(f"{prefix}.relevance must be one of {sorted(RELEVANCE)}")
    claims = source.get("claims_supported")
    if not _is_string_list(claims):
        errors.append(f"{prefix}.claims_supported must be a list of strings")
    arxiv_id = source.get("arxiv_id")
    if arxiv_id is not None and (not isinstance(arxiv_id, str) or not arxiv_id.strip()):
        errors.append(f"{prefix}.arxiv_id must be a non-empty string when present")
    if method == "arxiv":
        url = source.get("url")
        if not isinstance(arxiv_id, str) or not arxiv_id.strip():
            errors.append(f"{prefix}.arxiv_id is required when method is arxiv")
        if not isinstance(url, str) or "arxiv.org/" not in url:
            errors.append(f"{prefix}.url must cite arxiv.org when method is arxiv")
    for optional in ("doi", "version", "pdf_sha256", "html_sha256", "extraction_artifact"):
        value = source.get(optional)
        if value is not None and not isinstance(value, str):
            errors.append(f"{prefix}.{optional} must be a string when present")


def _require_non_empty_string(
    value: Mapping[str, Any],
    key: str,
    *,
    errors: list[str],
    prefix: str | None = None,
) -> None:
    label = f"{prefix}.{key}" if prefix else key
    if not isinstance(value.get(key), str) or not value[key].strip():
        errors.append(f"{label} must be a non-empty string")


def _parse_timestamp(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _arxiv_source_count(sources: list[Any]) -> int:
    return sum(1 for source in sources if isinstance(source, Mapping) and source.get("arxiv_id"))


def _safe_file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
