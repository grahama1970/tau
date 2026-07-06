"""Pre-query exfiltration gate for external research requests."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import validate_data_boundary, validate_policy_profile

RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA = "tau.research_query_safety_receipt.v1"
RESEARCH_QUERY_AUTHORIZATION_SCHEMA = "tau.research_query_authorization.v1"

EXTERNAL_METHODS = {"arxiv", "brave", "brave-search", "dogpile", "webgpt", "perplexity"}
LOCAL_METHODS = {"manual", "local-manual"}
CONTROLLED_MARKERS = {
    "itar",
    "export controlled",
    "export-controlled",
    "controlled technical data",
    "technical data",
    "defense article",
    "defense service",
    "cui",
    "classified",
    "distribution statement",
}


def write_research_query_safety_receipt(
    *,
    query: str,
    method: str,
    policy_profile_path: Path,
    data_boundary_path: Path,
    receipt_path: Path,
    authorization_path: Path | None = None,
    controlled_artifact_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Validate an external research query before any networked research call."""

    resolved_policy = policy_profile_path.expanduser().resolve()
    resolved_boundary = data_boundary_path.expanduser().resolve()
    resolved_auth = authorization_path.expanduser().resolve() if authorization_path else None
    resolved_receipt = receipt_path.expanduser().resolve()
    artifacts = [path.expanduser().resolve() for path in controlled_artifact_paths or []]

    errors: list[str] = []
    policy = _read_json_object(resolved_policy, errors=errors, label="policy_profile")
    boundary = _read_json_object(resolved_boundary, errors=errors, label="data_boundary")
    authorization = (
        _read_json_object(resolved_auth, errors=errors, label="authorization")
        if resolved_auth is not None
        else None
    )

    alerts = _evaluate_query(
        query=query,
        method=method,
        policy=policy,
        boundary=boundary,
        authorization=authorization,
        controlled_artifact_paths=artifacts,
        initial_errors=errors,
    )
    ok = not alerts
    receipt: dict[str, Any] = {
        "schema": RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "method": method,
        "external_tool_called": False,
        "query_sha256": f"sha256:{_text_sha256(query)}",
        "query_length": len(query),
        "policy_profile": _source_payload(resolved_policy, policy),
        "data_boundary": _source_payload(resolved_boundary, boundary),
        "authorization": (
            _source_payload(resolved_auth, authorization) if resolved_auth is not None else None
        ),
        "controlled_artifact_paths": [str(path) for path in artifacts],
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "recommended_action": _recommended_action(alerts),
        "receipt_path": str(resolved_receipt),
        "checked_at": _utc_stamp(),
        "proof_scope": {
            "proves": [
                "Tau inspected a research query before any external research call.",
                "Tau checked policy, data-boundary, authorization, and controlled-artifact constraints deterministically.",
                "No Brave, WebGPT, Dogpile, provider, GitHub, Memory, or browser action was executed.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Export-control legal sufficiency.",
                "Human identity verification.",
                "The external research result is true or sufficient.",
                "The query was actually sent to an external service.",
            ],
        },
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _evaluate_query(
    *,
    query: str,
    method: str,
    policy: Mapping[str, Any],
    boundary: Mapping[str, Any],
    authorization: Mapping[str, Any] | None,
    controlled_artifact_paths: list[Path],
    initial_errors: list[str],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for error in initial_errors:
        alerts.append(_alert("input_unreadable", error))
    if not query.strip():
        alerts.append(_alert("query_missing", "Research query must be non-empty."))
    policy_errors = validate_policy_profile(policy) if policy else ["policy_profile missing"]
    for error in policy_errors:
        alerts.append(_alert("invalid_policy_profile", error))
    boundary_errors = validate_data_boundary(boundary) if boundary else ["data_boundary missing"]
    for error in boundary_errors:
        alerts.append(_alert("invalid_data_boundary", error))

    normalized_method = method.strip().lower()
    external_method = normalized_method in EXTERNAL_METHODS
    if normalized_method not in EXTERNAL_METHODS | LOCAL_METHODS:
        alerts.append(
            _alert(
                "research_method_unknown",
                f"Research method is not recognized for safety gating: {method}",
                method=method,
            )
        )
        external_method = True

    if boundary.get("external_research_allowed") is False and external_method:
        alerts.append(
            _alert(
                "external_research_not_allowed",
                "Data boundary prohibits external research for this query.",
                method=method,
            )
        )
    research_policy = _section_value(policy, "research", "external_search")
    if research_policy == "deny" and external_method:
        alerts.append(
            _alert(
                "external_research_denied_by_policy",
                "Policy profile denies external research.",
                method=method,
            )
        )

    marker_hits = _controlled_marker_hits(query)
    if marker_hits and _boundary_is_controlled(boundary):
        alerts.append(
            _alert(
                "controlled_marker_in_query",
                "Research query contains controlled-data markers under a controlled boundary.",
                markers=marker_hits,
            )
        )
    snippet_hits = _artifact_snippet_hits(query, controlled_artifact_paths)
    if snippet_hits:
        alerts.append(
            _alert(
                "controlled_artifact_snippet_in_query",
                "Research query contains text copied from a controlled artifact path.",
                artifact_paths=snippet_hits,
            )
        )

    auth_errors = _authorization_errors(
        authorization,
        method=method,
        boundary=boundary,
        query=query,
    )
    if external_method and auth_errors:
        alerts.append(
            _alert(
                "research_authorization_invalid",
                "External research requires a matching, unexpired research authorization.",
                errors=auth_errors,
            )
        )
    return alerts


def _authorization_errors(
    authorization: Mapping[str, Any] | None,
    *,
    method: str,
    boundary: Mapping[str, Any],
    query: str,
) -> list[str]:
    if authorization is None:
        return ["authorization packet missing"]
    errors: list[str] = []
    if authorization.get("schema") != RESEARCH_QUERY_AUTHORIZATION_SCHEMA:
        errors.append(f"schema must be {RESEARCH_QUERY_AUTHORIZATION_SCHEMA}")
    if authorization.get("approved") is not True:
        errors.append("approved must be true")
    allowed_methods = authorization.get("allowed_methods")
    if not isinstance(allowed_methods, list) or method not in allowed_methods:
        errors.append("allowed_methods must include requested method")
    query_hash = _authorized_query_hash(authorization)
    if query_hash is None:
        errors.append("sanitized_query_sha256 or query_sha256 is required")
    elif query_hash != f"sha256:{_text_sha256(query)}":
        errors.append("authorized query hash does not match requested query")
    boundary_classification = authorization.get("data_boundary_classification")
    if boundary_classification != boundary.get("classification"):
        errors.append("data_boundary_classification must match data_boundary.classification")
    approver = authorization.get("approver")
    if not isinstance(approver, Mapping) or not approver.get("id"):
        errors.append("approver.id is required")
    expires_at = authorization.get("expires_at")
    if not isinstance(expires_at, str) or _parse_timestamp(expires_at) is None:
        errors.append("expires_at must be an ISO-8601 timestamp string")
    else:
        parsed = _parse_timestamp(expires_at)
        if parsed is not None and parsed <= datetime.now(UTC):
            errors.append("authorization is expired")
    return errors


def _authorized_query_hash(authorization: Mapping[str, Any]) -> str | None:
    for key in ("sanitized_query_sha256", "query_sha256"):
        value = authorization.get(key)
        if isinstance(value, str) and value.strip():
            normalized = value.strip()
            return normalized if normalized.startswith("sha256:") else f"sha256:{normalized}"
    return None


def _read_json_object(path: Path, *, errors: list[str], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} root must be a JSON object: {path}")
        return {}
    return payload


def _source_payload(path: Path, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": f"sha256:{_file_sha256(path)}",
        "schema": payload.get("schema") if isinstance(payload, Mapping) else None,
    }


def _section_value(payload: Mapping[str, Any], section: str, key: str) -> str | None:
    section_value = payload.get(section)
    if not isinstance(section_value, Mapping):
        return None
    value = section_value.get(key)
    return value if isinstance(value, str) else None


def _boundary_is_controlled(boundary: Mapping[str, Any]) -> bool:
    return bool(
        boundary.get("classification") in {"CUI", "ITAR", "EAR", "classified-not-allowed"}
        or boundary.get("export_controlled") is True
        or boundary.get("itar") is True
        or boundary.get("technical_data") is True
    )


def _controlled_marker_hits(query: str) -> list[str]:
    lowered = query.lower()
    return sorted(marker for marker in CONTROLLED_MARKERS if marker in lowered)


def _artifact_snippet_hits(query: str, paths: list[Path]) -> list[str]:
    hits: list[str] = []
    normalized_query = _normalize_snippet(query)
    if not normalized_query:
        return hits
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for snippet in _candidate_snippets(text):
            if snippet in normalized_query:
                hits.append(str(path))
                break
    return hits


def _candidate_snippets(text: str) -> list[str]:
    normalized = _normalize_snippet(text)
    if len(normalized) < 32:
        return []
    words = normalized.split()
    snippets: list[str] = []
    for window_size in (8, 10, 12):
        if len(words) < window_size:
            continue
        for index in range(0, len(words) - window_size + 1):
            snippets.append(" ".join(words[index : index + window_size]))
            if len(snippets) >= 60:
                return snippets
    return snippets


def _normalize_snippet(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _recommended_action(alerts: list[dict[str, Any]]) -> dict[str, str]:
    if not alerts:
        return {
            "type": "continue",
            "next_agent": "research-auditor",
            "reason": "Research query passed the pre-query safety gate.",
        }
    return {
        "type": "repair_research_query",
        "next_agent": "goal-guardian",
        "reason": (
            "Use a human-sanitized query and matching authorization before any external "
            "research call."
        ),
    }


def _alert(code: str, message: str, **evidence: object) -> dict[str, Any]:
    return {
        "severity": "BLOCK",
        "code": code,
        "message": message,
        "evidence": evidence,
    }


def _parse_timestamp(value: str) -> datetime | None:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
