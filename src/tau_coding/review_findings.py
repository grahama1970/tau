"""Structured review findings receipts for Tau coding workflows."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from tau_coding.policy_profile import (
    DATA_BOUNDARY_SCHEMA,
    POLICY_PROFILE_SCHEMA,
    validate_data_boundary,
    validate_policy_profile,
)

REVIEW_FINDINGS_SCHEMA = "tau.review_findings.v1"

SEVERITIES = {"P0", "P1", "P2", "P3"}
VERDICTS = {"PASS", "REVISE", "BLOCKED"}
REQUIRED_ACTIONS = {"block", "revise", "note"}


def validate_review_findings(
    payload: Mapping[str, Any],
    *,
    expected_goal_hash: str | None = None,
    zero_trust: bool = False,
    policy_profile: dict[str, Any] | None = None,
    data_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate structured reviewer findings and derive Tau routing state."""

    alerts: list[dict[str, Any]] = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    if payload.get("schema") != REVIEW_FINDINGS_SCHEMA:
        alerts.append(_alert("invalid_schema", f"schema must be {REVIEW_FINDINGS_SCHEMA}"))
    goal_hash = payload.get("goal_hash")
    if not isinstance(goal_hash, str) or not goal_hash:
        alerts.append(_alert("missing_goal_hash", "review findings goal_hash is required"))
    elif expected_goal_hash is not None and goal_hash != expected_goal_hash:
        alerts.append(
            _alert(
                "goal_hash_mismatch",
                "review findings goal_hash did not match expected goal",
            )
        )
    if not isinstance(payload.get("reviewer"), str) or not payload.get("reviewer"):
        alerts.append(_alert("missing_reviewer", "reviewer is required"))
    declared_verdict = payload.get("verdict")
    if declared_verdict not in VERDICTS:
        alerts.append(_alert("invalid_verdict", "verdict must be PASS, REVISE, or BLOCKED"))

    findings_raw = payload.get("findings")
    if not isinstance(findings_raw, list):
        alerts.append(_alert("invalid_findings", "findings must be a list"))
        findings_raw = []

    allowed_paths, allowed_paths_alerts = _optional_string_list(
        payload.get("allowed_paths"),
        field="allowed_paths",
    )
    forbidden_paths, forbidden_paths_alerts = _optional_string_list(
        payload.get("forbidden_paths"),
        field="forbidden_paths",
    )
    alerts.extend(allowed_paths_alerts)
    alerts.extend(forbidden_paths_alerts)
    normalized_findings: list[dict[str, Any]] = []
    for index, item in enumerate(findings_raw):
        normalized, item_alerts = _validate_finding(
            index,
            item,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
        )
        normalized_findings.append(normalized)
        alerts.extend(item_alerts)

    route = _route_for_findings(normalized_findings)
    if declared_verdict == "PASS" and route != "PASS":
        alerts.append(
            _alert(
                "verdict_understates_findings",
                "PASS verdict conflicts with blocking findings",
            )
        )
    if declared_verdict == "REVISE" and route == "BLOCKED":
        alerts.append(
            _alert(
                "verdict_understates_findings",
                "REVISE verdict conflicts with P0 findings",
            )
        )

    ok = not alerts
    return {
        "schema": REVIEW_FINDINGS_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "goal_hash": goal_hash,
        "reviewer": payload.get("reviewer"),
        "declared_verdict": declared_verdict,
        "derived_verdict": route if ok else "BLOCKED",
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "finding_count": len(normalized_findings),
        "blocking_finding_count": sum(
            1 for finding in normalized_findings if finding.get("required_action") == "block"
        ),
        "revision_finding_count": sum(
            1 for finding in normalized_findings if finding.get("required_action") == "revise"
        ),
        "findings": normalized_findings,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau parsed reviewer output as structured findings.",
                "Tau derived PASS, REVISE, or BLOCKED routing from severity and required_action.",
                "Tau blocked high-severity findings without evidence.",
            ],
            "does_not_prove": [
                "The reviewer is correct.",
                "The code is semantically correct.",
                "All possible issues were found.",
                "The underlying agent is trustworthy.",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def write_review_findings_receipt(
    *,
    findings_path: Path,
    receipt_path: Path | None = None,
    expected_goal_hash: str | None = None,
    zero_trust: bool = False,
    policy_profile: dict[str, Any] | None = None,
    data_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    read_alerts: list[dict[str, Any]] = []
    payload = _read_json_object(findings_path.expanduser().resolve(), read_alerts)
    receipt = validate_review_findings(
        payload,
        expected_goal_hash=expected_goal_hash,
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    if read_alerts:
        receipt["alerts"] = read_alerts + list(receipt["alerts"])
        receipt["alert_codes"] = [alert["code"] for alert in receipt["alerts"]]
        receipt["ok"] = False
        receipt["status"] = "BLOCKED"
        receipt["derived_verdict"] = "BLOCKED"
    resolved_receipt = (
        receipt_path.expanduser().resolve()
        if receipt_path is not None
        else findings_path.expanduser().resolve().with_name("review-findings-receipt.json")
    )
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt["findings_path"] = str(findings_path.expanduser().resolve())
    receipt["findings_sha256"] = _artifact_sha256_uri(findings_path.expanduser().resolve())
    receipt["findings_bytes"] = _artifact_size(findings_path.expanduser().resolve())
    receipt["findings_artifact"] = _artifact_descriptor(
        "review_findings",
        findings_path.expanduser().resolve(),
    )
    receipt["receipt_path"] = str(resolved_receipt)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _validate_finding(
    index: int,
    item: object,
    *,
    allowed_paths: list[str],
    forbidden_paths: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    alerts: list[dict[str, Any]] = []
    normalized: dict[str, Any] = {
        "index": index,
        "id": None,
        "severity": None,
        "confidence": None,
        "file": None,
        "line": None,
        "claim": None,
        "evidence": [],
        "required_action": None,
    }
    if not isinstance(item, Mapping):
        return normalized, [_alert("invalid_finding", f"findings[{index}] must be an object")]
    for key in ("id", "file", "claim"):
        value = item.get(key)
        if isinstance(value, str) and value:
            normalized[key] = value
        else:
            alerts.append(_alert("invalid_finding", f"findings[{index}].{key} is required"))
    normalized_file, file_alerts = _normalize_finding_file(
        index=index,
        value=normalized["file"],
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
    )
    normalized["file"] = normalized_file
    alerts.extend(file_alerts)
    severity = item.get("severity")
    if severity in SEVERITIES:
        normalized["severity"] = severity
    else:
        alerts.append(_alert("invalid_finding_severity", f"findings[{index}].severity is invalid"))
    action = item.get("required_action")
    if action in REQUIRED_ACTIONS:
        normalized["required_action"] = action
    else:
        alerts.append(
            _alert("invalid_required_action", f"findings[{index}].required_action is invalid")
        )
    confidence = item.get("confidence")
    if isinstance(confidence, int | float) and 0 <= confidence <= 1:
        normalized["confidence"] = float(confidence)
    else:
        alerts.append(_alert("invalid_confidence", f"findings[{index}].confidence must be 0..1"))
    line = item.get("line")
    if isinstance(line, int) and line >= 1:
        normalized["line"] = line
    elif line is not None:
        alerts.append(_alert("invalid_line", f"findings[{index}].line must be a positive integer"))
    evidence = item.get("evidence")
    if isinstance(evidence, list) and all(isinstance(entry, str) and entry for entry in evidence):
        normalized["evidence"] = evidence
    else:
        alerts.append(
            _alert(
                "missing_finding_evidence",
                f"findings[{index}].evidence must be a list of strings",
            )
        )
    if severity in {"P0", "P1"} and not normalized["evidence"]:
        alerts.append(
            _alert("missing_finding_evidence", f"findings[{index}] P0/P1 requires evidence")
        )
    expected_action = _expected_action(severity)
    if expected_action == "block" and action != "block":
        alerts.append(
            _alert(
                "finding_action_understates_severity",
                f"findings[{index}] P0 must block",
            )
        )
    if expected_action == "revise" and action not in {"revise", "block"}:
        alerts.append(
            _alert(
                "finding_action_understates_severity",
                f"findings[{index}] P1/P2 must revise or block",
            )
        )
    if expected_action == "note" and action != "note":
        alerts.append(
            _alert(
                "finding_action_overstates_severity",
                f"findings[{index}] P3 findings are note-only",
            )
        )
    return normalized, alerts


def _expected_action(severity: object) -> str:
    if severity == "P0":
        return "block"
    if severity in {"P1", "P2"}:
        return "revise"
    return "note"


def _route_for_findings(findings: list[dict[str, Any]]) -> str:
    if any(
        finding.get("severity") == "P0" or finding.get("required_action") == "block"
        for finding in findings
    ):
        return "BLOCKED"
    if any(
        finding.get("severity") in {"P1", "P2"} or finding.get("required_action") == "revise"
        for finding in findings
    ):
        return "REVISE"
    return "PASS"


def _read_json_object(path: Path, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        alerts.append(_alert("review_findings_missing", "review findings artifact is missing"))
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(
            _alert(
                "review_findings_unreadable",
                f"review findings are not readable JSON: {exc}",
            )
        )
        return {}
    if not isinstance(payload, dict):
        alerts.append(
            _alert("review_findings_not_object", "review findings root must be an object")
        )
        return {}
    return payload


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if errors:
        alert["errors"] = errors
    return alert


def _coding_policy_alerts(
    *,
    zero_trust: bool,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if zero_trust and policy_profile is None:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust review findings require policy_profile")
        )
    if zero_trust and data_boundary is None:
        alerts.append(
            _alert("missing_data_boundary", "zero-trust review findings require data_boundary")
        )
    if policy_profile is not None and policy_profile.get("schema") != POLICY_PROFILE_SCHEMA:
        alerts.append(_alert("invalid_policy_profile_schema", "policy_profile schema is invalid"))
    elif policy_profile is not None:
        errors = validate_policy_profile(policy_profile)
        if errors:
            alerts.append(
                _alert("invalid_policy_profile", "policy_profile is invalid", errors=errors)
            )
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    elif data_boundary is not None:
        errors = validate_data_boundary(data_boundary)
        if errors:
            alerts.append(
                _alert("invalid_data_boundary", "data_boundary is invalid", errors=errors)
            )
        if data_boundary.get("classification") == "classified-not-allowed":
            alerts.append(
                _alert(
                    "classified_not_allowed",
                    "classified-not-allowed data may not be routed to review findings",
                )
            )
    return alerts


def _normalize_finding_file(
    *,
    index: int,
    value: object,
    allowed_paths: list[str],
    forbidden_paths: list[str],
) -> tuple[str | None, list[dict[str, str]]]:
    if not isinstance(value, str) or not value:
        return None, []
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    alerts: list[dict[str, str]] = []
    if path.is_absolute() or normalized.startswith("~"):
        alerts.append(
            _alert("finding_path_escape", f"findings[{index}].file must be repo-relative")
        )
        return normalized, alerts
    if any(part in {"", ".", ".."} for part in path.parts):
        alerts.append(
            _alert("finding_path_escape", f"findings[{index}].file must not escape its boundary")
        )
        return normalized, alerts
    normalized = path.as_posix()
    if allowed_paths and not _path_allowed(normalized, allowed_paths):
        alerts.append(
            _alert(
                "finding_path_disallowed",
                f"findings[{index}].file is outside allowed_paths",
            )
        )
    if _path_forbidden(normalized, forbidden_paths):
        alerts.append(
            _alert("finding_path_forbidden", f"findings[{index}].file matches forbidden_paths")
        )
    return normalized, alerts


def _optional_string_list(
    value: object,
    *,
    field: str,
) -> tuple[list[str], list[dict[str, str]]]:
    if value is None:
        return [], []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        return [], [
            _alert(
                f"invalid_{field}",
                f"review findings {field} must be a list of non-empty strings",
            )
        ]
    return [item for item in value], []


def _path_allowed(path: str, patterns: list[str]) -> bool:
    return bool(patterns) and any(
        fnmatch.fnmatch(path, _normalize_policy_glob(pattern)) for pattern in patterns
    )


def _path_forbidden(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, _normalize_policy_glob(pattern)) for pattern in patterns)


def _normalize_policy_glob(pattern: str) -> str:
    return pattern.removeprefix("./")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _artifact_sha256_uri(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except OSError:
        return None


def _artifact_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _artifact_descriptor(label: str, path: Path | None) -> dict[str, Any]:
    return {
        "label": label,
        "path": str(path) if path is not None else None,
        "exists": bool(path is not None and path.exists()),
        "sha256": _artifact_sha256_uri(path),
        "bytes": _artifact_size(path),
    }
