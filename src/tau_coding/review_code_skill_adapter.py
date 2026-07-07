"""Adapter from review-code skill artifacts into Tau review findings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from tau_coding.review_findings import (
    REVIEW_FINDINGS_SCHEMA,
    write_review_findings_receipt,
)

REVIEW_CODE_RESULT_SCHEMA = "review_code.result.v1"
REVIEW_CODE_ADAPTER_RECEIPT_SCHEMA = "tau.review_code_skill_adapter_receipt.v1"


def write_review_code_skill_adapter_receipt(
    *,
    review_path: Path,
    output_path: Path,
    repo_root: Path,
    expected_goal_hash: str | None = None,
) -> dict[str, Any]:
    resolved_review = review_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_repo = repo_root.expanduser().resolve()
    errors: list[str] = []
    source = _read_json_object(resolved_review, errors=errors, label="review-code result")
    if source:
        _validate_source(source, expected_goal_hash=expected_goal_hash, errors=errors)

    findings_path = resolved_output.parent / "review-code-findings.json"
    findings_receipt_path = resolved_output.parent / "review-findings-receipt.json"
    findings_payload = _review_code_to_review_findings(
        source,
        repo_root=resolved_repo,
        errors=errors,
    )
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text(
        json.dumps(findings_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    findings_receipt = write_review_findings_receipt(
        findings_path=findings_path,
        receipt_path=findings_receipt_path,
        expected_goal_hash=expected_goal_hash,
    )
    if findings_receipt.get("ok") is not True:
        errors.append("review findings receipt blocked")

    ok = not errors
    payload = {
        "schema": REVIEW_CODE_ADAPTER_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "source_review_path": str(resolved_review),
        "source_review_sha256": _sha256_uri(resolved_review) if resolved_review.is_file() else None,
        "repo_root": str(resolved_repo),
        "goal_hash": source.get("goal_hash"),
        "expected_goal_hash": expected_goal_hash,
        "reviewer": findings_payload.get("reviewer"),
        "declared_review_code_verdict": _source_verdict(source),
        "tau_review_findings_path": str(findings_path),
        "tau_review_findings_sha256": _sha256_uri(findings_path),
        "tau_review_findings_receipt_path": str(findings_receipt_path),
        "tau_review_findings_receipt_status": findings_receipt.get("status"),
        "tau_review_findings_derived_verdict": findings_receipt.get("derived_verdict"),
        "finding_count": findings_receipt.get("finding_count"),
        "blocking_finding_count": findings_receipt.get("blocking_finding_count"),
        "revision_finding_count": findings_receipt.get("revision_finding_count"),
        "alert_codes": findings_receipt.get("alert_codes", []),
        "errors": errors,
        "course_correction": _course_correction(
            errors,
            derived_verdict=findings_receipt.get("derived_verdict"),
        ),
        "proof_scope": {
            "proves": [
                "Tau ingested a review-code result artifact.",
                "Tau normalized advisory review-code findings into tau.review_findings.v1.",
                "Tau validated normalized findings with the existing review findings gate.",
            ],
            "does_not_prove": [
                "The reviewer is correct.",
                "The code is semantically correct.",
                "The task is complete.",
                "Reviewer consensus is proof.",
            ],
        },
    }
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _validate_source(
    source: dict[str, Any],
    *,
    expected_goal_hash: str | None,
    errors: list[str],
) -> None:
    if source.get("schema") != REVIEW_CODE_RESULT_SCHEMA:
        errors.append(f"schema must be {REVIEW_CODE_RESULT_SCHEMA}")
    goal_hash = source.get("goal_hash")
    if not isinstance(goal_hash, str) or not goal_hash:
        errors.append("goal_hash is required")
    elif expected_goal_hash and goal_hash != expected_goal_hash:
        errors.append("goal_hash mismatches expected_goal_hash")
    if _source_verdict(source) not in {"PASS", "REVISE", "BLOCKED"}:
        errors.append("review-code verdict must map to PASS, REVISE, or BLOCKED")


def _review_code_to_review_findings(
    source: dict[str, Any],
    *,
    repo_root: Path,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "schema": REVIEW_FINDINGS_SCHEMA,
        "goal_hash": source.get("goal_hash"),
        "reviewer": _reviewer(source),
        "verdict": _source_verdict(source),
        "allowed_paths": _string_list(source.get("allowed_paths")),
        "forbidden_paths": _string_list(source.get("forbidden_paths")),
        "findings": [
            _normalize_finding(index, item, repo_root=repo_root, errors=errors)
            for index, item in enumerate(_source_findings(source))
        ],
    }


def _normalize_finding(
    index: int,
    item: Any,
    *,
    repo_root: Path,
    errors: list[str],
) -> dict[str, Any]:
    if not isinstance(item, dict):
        errors.append(f"finding[{index}] must be an object")
        item = {}
    severity = _severity(item, default="P1")
    action = _required_action(item, severity=severity)
    file_value = _finding_file(item)
    return {
        "id": _non_empty_str(item.get("id"))
        or _non_empty_str(item.get("finding_id"))
        or f"review-code-{index + 1:04d}",
        "severity": severity,
        "confidence": _confidence(item),
        "file": _repo_relative_file(file_value, repo_root=repo_root),
        "line": _line(item.get("line")),
        "claim": _claim(item),
        "evidence": _evidence(item),
        "required_action": action,
        "waiver": item.get("waiver") if isinstance(item.get("waiver"), dict) else None,
    }


def _source_verdict(source: dict[str, Any]) -> str:
    raw = source.get("verdict", source.get("status", source.get("latest_verdict")))
    if isinstance(raw, dict):
        raw = raw.get("verdict", raw.get("status"))
    if not isinstance(raw, str):
        return "BLOCKED"
    normalized = raw.strip().upper().replace("-", "_").replace(" ", "_")
    if normalized in {"PASS", "SATISFIED", "APPROVED"}:
        return "PASS"
    if normalized in {"NEEDS_CHANGES", "NEEDS_PATCH", "REVISE", "REVISION_REQUIRED"}:
        return "REVISE"
    return "BLOCKED"


def _source_findings(source: dict[str, Any]) -> list[Any]:
    findings: list[Any] = []
    for field in ("findings", "blocking_findings", "non_blocking_findings"):
        value = source.get(field)
        if isinstance(value, list):
            findings.extend(value)
    aggregate = source.get("aggregate_verdict")
    if isinstance(aggregate, dict):
        for field in ("findings", "blocking_findings", "non_blocking_findings"):
            value = aggregate.get(field)
            if isinstance(value, list):
                findings.extend(value)
    return findings


def _reviewer(source: dict[str, Any]) -> str:
    reviewer = _non_empty_str(source.get("reviewer"))
    if reviewer:
        return reviewer
    provider = _non_empty_str(source.get("provider"))
    model = _non_empty_str(source.get("model"))
    if provider and model:
        return f"review-code:{provider}:{model}"
    if provider:
        return f"review-code:{provider}"
    return "review-code"


def _severity(item: dict[str, Any], *, default: str) -> str:
    raw = item.get("severity", item.get("priority", item.get("level")))
    if not isinstance(raw, str):
        if item.get("blocking") is True:
            return "P0"
        return default
    normalized = raw.strip().upper().replace("CRITICAL", "P0").replace("HIGH", "P1")
    mapping = {
        "BLOCKER": "P0",
        "BLOCKING": "P0",
        "MEDIUM": "P2",
        "LOW": "P3",
        "INFO": "P3",
        "NOTE": "P3",
    }
    normalized = mapping.get(normalized, normalized)
    return normalized if normalized in {"P0", "P1", "P2", "P3"} else default


def _required_action(item: dict[str, Any], *, severity: str) -> str:
    raw = item.get("required_action", item.get("action"))
    if isinstance(raw, str):
        normalized = raw.strip().lower().replace("-", "_")
        if normalized in {"block", "blocked"}:
            return "block"
        if normalized in {"revise", "needs_changes", "fix", "change"}:
            return "revise"
        if normalized in {"note", "informational", "none"}:
            return "note"
    if severity == "P0" or item.get("blocking") is True:
        return "block"
    if severity in {"P1", "P2"}:
        return "revise"
    return "note"


def _finding_file(item: dict[str, Any]) -> Any:
    for field in ("file", "path", "file_path", "source_path"):
        value = item.get(field)
        if isinstance(value, str) and value:
            return value
    location = item.get("location")
    if isinstance(location, dict):
        for field in ("file", "path"):
            value = location.get(field)
            if isinstance(value, str) and value:
                return value
    return "review-code-bundle.md"


def _repo_relative_file(value: Any, *, repo_root: Path) -> str:
    if not isinstance(value, str) or not value:
        return "review-code-bundle.md"
    path = Path(value).expanduser()
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            return path.as_posix()
    posix = PurePosixPath(value.replace("\\", "/"))
    return posix.as_posix()


def _claim(item: dict[str, Any]) -> str:
    for field in ("claim", "message", "summary", "title", "description"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Review-code finding requires adjudication."


def _evidence(item: dict[str, Any]) -> list[str]:
    for field in ("evidence", "source_refs", "references"):
        value = item.get(field)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, str) and entry]
        if isinstance(value, str) and value:
            return [value]
    diff = item.get("diff")
    if isinstance(diff, str) and diff.strip():
        return ["review-code diff suggestion"]
    return []


def _confidence(item: dict[str, Any]) -> float:
    value = item.get("confidence")
    if isinstance(value, int | float) and 0 <= value <= 1:
        return float(value)
    return 0.8


def _line(value: Any) -> int | None:
    if isinstance(value, int) and value >= 1:
        return value
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return value
    return []


def _course_correction(
    errors: list[str],
    *,
    derived_verdict: Any,
) -> dict[str, Any] | None:
    if errors:
        return {
            "schema": "tau.course_correction.v1",
            "trigger": "invalid_review_code_artifact",
            "required_next_action": "route_reviewer",
            "allowed_next_routes": ["review-code", "human"],
            "forbidden_next_routes": ["claim_pass_from_invalid_review"],
            "required_evidence_before_retry": [
                "review_result.json",
                "tau.review_findings.v1",
            ],
        }
    if derived_verdict == "BLOCKED":
        return {
            "schema": "tau.course_correction.v1",
            "trigger": "reviewer_blocked",
            "required_next_action": "route_human",
            "allowed_next_routes": ["human", "goal-guardian", "review-code"],
            "forbidden_next_routes": ["ordinary_continuation"],
            "required_evidence_before_retry": ["resolved_review_findings"],
        }
    if derived_verdict == "REVISE":
        return {
            "schema": "tau.course_correction.v1",
            "trigger": "reviewer_revise",
            "required_next_action": "retry_node",
            "allowed_next_routes": ["code-runner", "coder", "review-code"],
            "forbidden_next_routes": ["claim_pass_without_patch"],
            "required_evidence_before_retry": ["patch_receipt", "test_receipt"],
        }
    return None


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


def _non_empty_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
