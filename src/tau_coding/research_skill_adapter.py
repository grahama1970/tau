"""Adapter from research skill artifacts into Tau research source receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.research_query_gate import RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA
from tau_coding.research_source_receipt import (
    RESEARCH_SOURCE_PACKET_SCHEMA,
    write_research_source_receipt,
)

RESEARCH_SKILL_ADAPTER_RECEIPT_SCHEMA = "tau.research_skill_adapter_receipt.v1"


def write_research_skill_adapter_receipt(
    *,
    report_path: Path,
    query_safety_receipt_path: Path,
    output_path: Path,
    repo_root: Path,
    method: str = "dogpile",
    source_type: str = "web",
    classification: str = "design_input",
) -> dict[str, Any]:
    resolved_report = report_path.expanduser().resolve()
    resolved_safety = query_safety_receipt_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_repo = repo_root.expanduser().resolve()
    errors: list[str] = []
    _require_inside_repo(
        resolved_report,
        repo_root=resolved_repo,
        field="report_path",
        errors=errors,
    )
    _require_inside_repo(
        resolved_safety,
        repo_root=resolved_repo,
        field="query_safety_receipt_path",
        errors=errors,
    )
    report = _read_json_object(resolved_report, errors=errors, label="research report")
    safety = _read_json_object(resolved_safety, errors=errors, label="query safety receipt")
    if safety:
        _validate_safety_receipt(safety, report=report, errors=errors)
    else:
        errors.append("query safety receipt did not pass")
    source_packet_path = resolved_output.parent / "research-source-packet.json"
    source_receipt_path = resolved_output.parent / "research-source-receipt.json"
    source_packet = _research_report_to_source_packet(
        report,
        method=method,
        source_type=source_type,
        classification=classification,
        errors=errors,
    )
    source_packet_path.parent.mkdir(parents=True, exist_ok=True)
    source_packet_path.write_text(
        json.dumps(source_packet, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    source_receipt = write_research_source_receipt(
        source_path=source_packet_path,
        receipt_path=source_receipt_path,
    )
    if source_receipt.get("ok") is not True:
        errors.append("research source receipt blocked")

    ok = not errors
    payload = {
        "schema": RESEARCH_SKILL_ADAPTER_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "source_report_path": str(resolved_report),
        "source_report_sha256": _sha256_uri(resolved_report) if resolved_report.is_file() else None,
        "query_safety_receipt_path": str(resolved_safety),
        "query_safety_receipt_sha256": (
            _sha256_uri(resolved_safety) if resolved_safety.is_file() else None
        ),
        "research_source_packet_path": str(source_packet_path),
        "research_source_packet_sha256": _sha256_uri(source_packet_path),
        "research_source_receipt_path": str(source_receipt_path),
        "research_source_receipt_status": source_receipt.get("status"),
        "method": method,
        "source_type": source_type,
        "classification": classification,
        "query": source_packet.get("query"),
        "source_count": source_receipt.get("source_count"),
        "provider_counts": _provider_counts(source_packet.get("sources")),
        "degraded_providers": _degraded_providers(report),
        "review_required": source_receipt.get("review_required"),
        "errors": errors,
        "course_correction": _course_correction(errors),
        "proof_scope": {
            "proves": [
                "Tau ingested a research skill artifact after a query safety receipt.",
                "Tau converted the artifact into tau.research_source_packet.v1.",
                "Tau validated the packet with tau.research_source_receipt.v1.",
            ],
            "does_not_prove": [
                "The cited sources are true.",
                "The research is closure proof.",
                "The external tool was called by this adapter.",
                "The research is sufficient for implementation.",
            ],
        },
    }
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _validate_safety_receipt(
    safety: dict[str, Any],
    *,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    if safety.get("schema") != RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA:
        errors.append(f"query safety receipt schema must be {RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA}")
    if safety.get("ok") is not True:
        errors.append("query safety receipt did not pass")
    query = _query(report)
    query_hash = safety.get("query_sha256")
    if isinstance(query_hash, str) and query and query_hash != f"sha256:{_text_sha256(query)}":
        errors.append("research report query does not match query safety receipt")


def _research_report_to_source_packet(
    report: dict[str, Any],
    *,
    method: str,
    source_type: str,
    classification: str,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "schema": RESEARCH_SOURCE_PACKET_SCHEMA,
        "source_type": source_type,
        "method": method,
        "query": _query(report),
        "retrieved_at": _retrieved_at(report),
        "classification": classification,
        "sources": [
            _normalize_source(index, source, errors=errors)
            for index, source in enumerate(_sources(report))
        ],
        "summary": _summary(report),
        "limitations": _limitations(report),
    }


def _normalize_source(index: int, source: Any, *, errors: list[str]) -> dict[str, Any]:
    if not isinstance(source, dict):
        errors.append(f"sources[{index}] must be an object")
        source = {}
    claims = source.get("claims_supported", source.get("claims", []))
    if isinstance(claims, str):
        claims = [claims]
    return {
        "title": _string(source.get("title")) or f"Research source {index + 1}",
        "url": _string(source.get("url")) or _string(source.get("link")) or "",
        "arxiv_id": source.get("arxiv_id") if isinstance(source.get("arxiv_id"), str) else None,
        "doi": source.get("doi") if isinstance(source.get("doi"), str) else None,
        "version": source.get("version") if isinstance(source.get("version"), str) else None,
        "pdf_sha256": (
            source.get("pdf_sha256") if isinstance(source.get("pdf_sha256"), str) else None
        ),
        "html_sha256": (
            source.get("html_sha256") if isinstance(source.get("html_sha256"), str) else None
        ),
        "extraction_artifact": (
            source.get("extraction_artifact")
            if isinstance(source.get("extraction_artifact"), str)
            else None
        ),
        "provider": source.get("provider") if isinstance(source.get("provider"), str) else None,
        "stage": source.get("stage") if isinstance(source.get("stage"), str) else None,
        "relevance": _relevance(source.get("relevance")),
        "claims_supported": [item for item in claims if isinstance(item, str) and item],
    }


def _sources(report: dict[str, Any]) -> list[Any]:
    for field in ("sources", "results", "provider_results"):
        value = report.get(field)
        if isinstance(value, list):
            return value
    results = report.get("results")
    if isinstance(results, dict):
        return _dogpile_sources(results)
    return []


def _query(report: dict[str, Any]) -> str:
    for field in (
        "query",
        "requested_query",
        "effective_query",
        "original_query",
        "sanitized_query",
    ):
        value = report.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _summary(report: dict[str, Any]) -> str:
    for field in ("summary", "synthesis", "final_report"):
        value = report.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Research skill report ingested by Tau; reviewer inspection is required."


def _limitations(report: dict[str, Any]) -> list[str]:
    value = report.get("limitations")
    if isinstance(value, list):
        items = [item for item in value if isinstance(item, str) and item]
    else:
        items = []
    if "Research is design input only." not in items:
        items.append("Research is design input only.")
    if "Local Tau proof remains required before closure." not in items:
        items.append("Local Tau proof remains required before closure.")
    return items


def _retrieved_at(report: dict[str, Any]) -> str:
    for field in ("retrieved_at", "completed_at", "timestamp"):
        value = report.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _relevance(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"HIGH", "MEDIUM", "LOW"}:
            return normalized
    return "MEDIUM"


def _dogpile_sources(results: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stage, providers in results.items():
        if not isinstance(providers, dict):
            continue
        for provider, value in providers.items():
            for item in _walk_source_items(value):
                url = _string(item.get("url")) or _string(item.get("link"))
                title = _string(item.get("title"))
                if not url or not title:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                found.append(
                    {
                        **item,
                        "provider": str(provider),
                        "stage": str(stage),
                        "claims_supported": _claims_supported(item, provider=str(provider)),
                    }
                )
    return found


def _walk_source_items(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("title"), str) and (
            isinstance(value.get("url"), str) or isinstance(value.get("link"), str)
        ):
            found.append(value)
        for nested in value.values():
            if isinstance(nested, dict | list):
                found.extend(_walk_source_items(nested))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict | list):
                found.extend(_walk_source_items(item))
    return found


def _claims_supported(source: dict[str, Any], *, provider: str) -> list[str]:
    claims = source.get("claims_supported", source.get("claims"))
    if isinstance(claims, str) and claims.strip():
        return [claims.strip()]
    if isinstance(claims, list):
        values = [claim for claim in claims if isinstance(claim, str) and claim.strip()]
        if values:
            return values
    description = source.get("description", source.get("snippet", source.get("summary")))
    if isinstance(description, str) and description.strip():
        return [description.strip()[:240]]
    return [f"Dogpile {provider} result for the research query."]


def _provider_counts(sources: Any) -> dict[str, int]:
    if not isinstance(sources, list):
        return {}
    counts: dict[str, int] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        provider = _string(source.get("provider")) or "unknown"
        counts[provider] = counts.get(provider, 0) + 1
    return dict(sorted(counts.items()))


def _degraded_providers(report: dict[str, Any]) -> list[dict[str, str]]:
    results = report.get("results")
    if not isinstance(results, dict):
        return []
    degraded: list[dict[str, str]] = []
    for stage, providers in results.items():
        if not isinstance(providers, dict):
            continue
        for provider, value in providers.items():
            reason = _degraded_reason(value)
            if reason:
                degraded.append(
                    {
                        "stage": str(stage),
                        "provider": str(provider),
                        "reason": reason,
                    }
                )
    return degraded


def _degraded_reason(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("error", "skipped"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        title = _string(value[0].get("title"))
        if title.lower().startswith("error "):
            return title
    if isinstance(value, str) and value.lower().startswith("error"):
        return value.strip()
    return None


def _course_correction(errors: list[str]) -> dict[str, Any] | None:
    if not errors:
        return None
    return {
        "schema": "tau.course_correction.v1",
        "trigger": "research_required_before_retry",
        "required_next_action": "run_research_query_gate",
        "allowed_next_routes": ["dogpile", "brave-search", "arxiv", "research-auditor", "human"],
        "forbidden_next_routes": ["use_research_without_query_safety_receipt"],
        "required_evidence_before_retry": [
            "tau.research_query_safety_receipt.v1",
            "tau.research_source_receipt.v1",
        ],
    }


def _read_json_object(path: Path, *, errors: list[str], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object: {path}")
        return {}
    return payload


def _require_inside_repo(path: Path, *, repo_root: Path, field: str, errors: list[str]) -> None:
    if not _is_relative_to(path, repo_root):
        errors.append(f"{field} escapes repo root: {path}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
