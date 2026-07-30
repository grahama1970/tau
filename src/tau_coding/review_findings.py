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
REVIEW_SCOPE_SCHEMA = "tau.review_scope.v1"

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
    current_review_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate structured reviewer findings and derive Tau routing state."""

    alerts: list[dict[str, Any]] = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        expected_goal_hash=expected_goal_hash,
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
    if zero_trust and findings_raw and not allowed_paths:
        alerts.append(
            _alert(
                "missing_allowed_paths",
                "zero-trust review findings with findings require allowed_paths",
            )
        )
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

    review_scope_receipt = validate_review_scope(
        payload.get("review_scope"),
        current_review_scope=current_review_scope,
        expected_goal_hash=expected_goal_hash,
    )
    if review_scope_receipt["status"] == "BLOCKED":
        alerts.extend(review_scope_receipt["alerts"])

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
        "review_scope_validation": review_scope_receipt,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau parsed reviewer output as structured findings.",
                "Tau derived PASS, REVISE, or BLOCKED routing from severity and required_action.",
                "Tau blocked high-severity findings without evidence.",
                "When a current review scope is supplied, Tau compared reviewer scope "
                "against current DAG state before accepting PASS.",
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
    current_review_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    read_alerts: list[dict[str, Any]] = []
    payload = _read_json_object(findings_path.expanduser().resolve(), read_alerts)
    receipt = validate_review_findings(
        payload,
        expected_goal_hash=expected_goal_hash,
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        current_review_scope=current_review_scope,
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


def build_review_scope(
    *,
    goal_hash: str,
    plan_sha256: str,
    reviewed_node_ids: list[str] | tuple[str, ...],
    reviewed_attempt_ids: list[str] | tuple[str, ...],
    admitted_artifacts: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    journal_sequence_start: int,
    journal_sequence_end: int,
) -> dict[str, Any]:
    """Build a canonical review scope that can be compared against run state."""

    scope = {
        "schema": REVIEW_SCOPE_SCHEMA,
        "goal_hash": goal_hash,
        "plan_sha256": plan_sha256,
        "reviewed_node_ids": sorted(set(reviewed_node_ids)),
        "reviewed_attempt_ids": sorted(set(reviewed_attempt_ids)),
        "admitted_artifacts": _normalize_artifact_descriptors(admitted_artifacts),
        "journal_sequence_start": journal_sequence_start,
        "journal_sequence_end": journal_sequence_end,
    }
    return {**scope, "scope_sha256": review_scope_sha256(scope)}


def review_scope_sha256(scope: Mapping[str, Any]) -> str:
    """Return the stable digest for a normalized review scope."""

    preimage = dict(scope)
    preimage.pop("scope_sha256", None)
    return f"sha256:{hashlib.sha256(_canonical_json(preimage).encode('utf-8')).hexdigest()}"


def validate_review_scope(
    declared_scope: object,
    *,
    current_review_scope: Mapping[str, Any] | None = None,
    expected_goal_hash: str | None = None,
) -> dict[str, Any]:
    """Validate reviewer scope and fail closed when it is stale."""

    alerts: list[dict[str, Any]] = []
    normalized_declared, declared_alerts = _normalize_review_scope(
        declared_scope,
        label="declared",
        require_present=current_review_scope is not None,
    )
    alerts.extend(declared_alerts)
    normalized_current: dict[str, Any] | None = None
    if current_review_scope is not None:
        normalized_current, current_alerts = _normalize_review_scope(
            current_review_scope,
            label="current",
            require_present=True,
        )
        alerts.extend(current_alerts)

    if (
        expected_goal_hash
        and normalized_declared is not None
        and normalized_declared.get("goal_hash") != expected_goal_hash
    ):
        alerts.append(
            _alert("review_scope_goal_hash_mismatch", "review scope goal_hash mismatches expected")
        )

    stale_reasons: list[dict[str, Any]] = []
    if normalized_declared is not None and normalized_current is not None:
        stale_reasons = _review_scope_stale_reasons(normalized_declared, normalized_current)
        if stale_reasons:
            alerts.append(
                _alert(
                    "review_scope_stale",
                    "review scope no longer matches current DAG run state",
                    errors=[reason["code"] for reason in stale_reasons],
                )
            )

    ok = not alerts
    return {
        "schema": "tau.review_scope_validation.v1",
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "declared_scope": normalized_declared,
        "current_scope": normalized_current,
        "declared_scope_sha256": (
            normalized_declared.get("scope_sha256") if normalized_declared is not None else None
        ),
        "current_scope_sha256": (
            normalized_current.get("scope_sha256") if normalized_current is not None else None
        ),
        "stale_reasons": stale_reasons,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau compared the reviewer-declared scope to the supplied current run scope.",
                "Tau fails closed when plan, nodes, attempts, admitted artifacts, "
                "or journal window changed.",
            ],
            "does_not_prove": [
                "The reviewer is semantically correct.",
                "The current DAG result is acceptable.",
                "A live provider or browser reviewed this scope.",
            ],
        },
    }


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
        "waiver": None,
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
    waiver, waiver_alerts = _validate_waiver(index, item.get("waiver"))
    normalized["waiver"] = waiver
    alerts.extend(waiver_alerts)
    expected_action = _expected_action(severity)
    if expected_action == "block" and action != "block":
        alerts.append(
            _alert(
                "finding_action_understates_severity",
                f"findings[{index}] P0 must block",
            )
        )
    if (
        expected_action == "revise"
        and action not in {"revise", "block"}
        and not _finding_has_valid_p2_waiver(severity, action, waiver, waiver_alerts)
    ):
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


def _normalize_review_scope(
    value: object,
    *,
    label: str,
    require_present: bool,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if value is None:
        if require_present:
            return None, [_alert("review_scope_missing", f"{label} review scope is required")]
        return None, []
    if not isinstance(value, Mapping):
        return None, [_alert("invalid_review_scope", f"{label} review scope must be an object")]
    alerts: list[dict[str, Any]] = []
    if value.get("schema") != REVIEW_SCOPE_SCHEMA:
        alerts.append(
            _alert("invalid_review_scope_schema", f"{label} review scope schema is invalid")
        )
    goal_hash = _scope_hash_value(value.get("goal_hash"), f"{label}.goal_hash", alerts)
    plan_sha256 = _scope_hash_value(value.get("plan_sha256"), f"{label}.plan_sha256", alerts)
    node_ids = _scope_string_set(
        value.get("reviewed_node_ids"),
        f"{label}.reviewed_node_ids",
        alerts,
    )
    attempt_ids = _scope_string_set(
        value.get("reviewed_attempt_ids"),
        f"{label}.reviewed_attempt_ids",
        alerts,
    )
    artifacts = _normalize_artifact_descriptors(
        value.get("admitted_artifacts"),
        field=f"{label}.admitted_artifacts",
        alerts=alerts,
    )
    journal_start = _scope_sequence(
        value.get("journal_sequence_start"),
        f"{label}.journal_sequence_start",
        alerts,
    )
    journal_end = _scope_sequence(
        value.get("journal_sequence_end"),
        f"{label}.journal_sequence_end",
        alerts,
    )
    if (
        journal_start is not None
        and journal_end is not None
        and journal_end < journal_start
    ):
        alerts.append(
            _alert(
                "invalid_review_scope",
                f"{label}.journal_sequence_end must be >= journal_sequence_start",
            )
        )
    normalized = {
        "schema": REVIEW_SCOPE_SCHEMA,
        "goal_hash": goal_hash,
        "plan_sha256": plan_sha256,
        "reviewed_node_ids": node_ids,
        "reviewed_attempt_ids": attempt_ids,
        "admitted_artifacts": artifacts,
        "journal_sequence_start": journal_start,
        "journal_sequence_end": journal_end,
    }
    normalized["scope_sha256"] = review_scope_sha256(normalized)
    declared_hash = value.get("scope_sha256")
    if declared_hash is not None and declared_hash != normalized["scope_sha256"]:
        alerts.append(
            _alert("review_scope_hash_mismatch", f"{label} review scope hash does not match")
        )
    return normalized, alerts


def _review_scope_stale_reasons(
    declared: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    comparisons = [
        ("goal_hash", "review_scope_goal_changed"),
        ("plan_sha256", "review_scope_plan_changed"),
        ("reviewed_node_ids", "review_scope_nodes_changed"),
        ("reviewed_attempt_ids", "review_scope_attempts_changed"),
        ("admitted_artifacts", "review_scope_artifacts_changed"),
        ("journal_sequence_start", "review_scope_journal_start_changed"),
        ("journal_sequence_end", "review_scope_journal_advanced"),
    ]
    for field, code in comparisons:
        if declared.get(field) != current.get(field):
            reasons.append(
                {
                    "code": code,
                    "field": field,
                    "declared": declared.get(field),
                    "current": current.get(field),
                }
            )
    if declared.get("scope_sha256") != current.get("scope_sha256"):
        reasons.append(
            {
                "code": "review_scope_hash_changed",
                "field": "scope_sha256",
                "declared": declared.get("scope_sha256"),
                "current": current.get("scope_sha256"),
            }
        )
    return reasons


def _scope_hash_value(
    value: object,
    field: str,
    alerts: list[dict[str, Any]],
) -> str | None:
    if isinstance(value, str) and value.startswith("sha256:"):
        return value
    alerts.append(_alert("invalid_review_scope", f"{field} must be a sha256 value"))
    return None


def _scope_string_set(
    value: object,
    field: str,
    alerts: list[dict[str, Any]],
) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        alerts.append(_alert("invalid_review_scope", f"{field} must be a list of strings"))
        return []
    unique = sorted(set(value))
    if unique != value:
        alerts.append(_alert("review_scope_not_canonical", f"{field} must be sorted and unique"))
    return unique


def _scope_sequence(
    value: object,
    field: str,
    alerts: list[dict[str, Any]],
) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    alerts.append(_alert("invalid_review_scope", f"{field} must be a non-negative integer"))
    return None


def _normalize_artifact_descriptors(
    value: object,
    *,
    field: str = "admitted_artifacts",
    alerts: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    local_alerts = alerts if alerts is not None else []
    if not isinstance(value, list | tuple):
        local_alerts.append(_alert("invalid_review_scope", f"{field} must be a list"))
        return []
    descriptors: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            local_alerts.append(
                _alert("invalid_review_scope", f"{field}[{index}] must be an object")
            )
            continue
        schema = item.get("schema")
        sha256 = item.get("sha256")
        path = item.get("path")
        artifact_id = item.get("id", item.get("artifact_id"))
        if not isinstance(schema, str) or not schema:
            local_alerts.append(
                _alert("invalid_review_scope", f"{field}[{index}].schema is required")
            )
            continue
        if not isinstance(sha256, str) or not sha256.startswith("sha256:"):
            local_alerts.append(
                _alert("invalid_review_scope", f"{field}[{index}].sha256 is required")
            )
            continue
        if not isinstance(path, str) and not isinstance(artifact_id, str):
            local_alerts.append(
                _alert(
                    "invalid_review_scope",
                    f"{field}[{index}] requires path or id",
                )
            )
            continue
        descriptor = {"schema": schema, "sha256": sha256}
        if isinstance(path, str) and path:
            descriptor["path"] = path
        if isinstance(artifact_id, str) and artifact_id:
            descriptor["id"] = artifact_id
        descriptors.append(descriptor)
    unique = sorted(
        {tuple(sorted(item.items())) for item in descriptors},
        key=lambda pairs: json.dumps(dict(pairs), sort_keys=True),
    )
    return [dict(item) for item in unique]


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_waiver(
    index: int,
    value: object,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if value is None:
        return None, []
    if not isinstance(value, Mapping):
        return None, [
            _alert("invalid_finding_waiver", f"findings[{index}].waiver must be an object")
        ]
    approved = value.get("approved")
    approved_by = value.get("approved_by")
    reason = value.get("reason")
    evidence = value.get("evidence")
    normalized: dict[str, Any] = {
        "approved": approved,
        "approved_by": approved_by if isinstance(approved_by, str) else None,
        "reason": reason if isinstance(reason, str) else None,
        "evidence": evidence if isinstance(evidence, list) else [],
    }
    alerts: list[dict[str, Any]] = []
    if approved is not True:
        alerts.append(
            _alert("invalid_finding_waiver", f"findings[{index}].waiver.approved must be true")
        )
    if not isinstance(approved_by, str) or not approved_by:
        alerts.append(
            _alert(
                "invalid_finding_waiver",
                f"findings[{index}].waiver.approved_by is required",
            )
        )
    if not isinstance(reason, str) or not reason:
        alerts.append(
            _alert("invalid_finding_waiver", f"findings[{index}].waiver.reason is required")
        )
    if not isinstance(evidence, list) or not all(
        isinstance(entry, str) and entry for entry in evidence
    ):
        alerts.append(
            _alert(
                "invalid_finding_waiver",
                f"findings[{index}].waiver.evidence must be a list of strings",
            )
        )
    elif not evidence:
        alerts.append(
            _alert(
                "invalid_finding_waiver",
                f"findings[{index}].waiver.evidence is required",
            )
        )
    return normalized, alerts


def _finding_has_valid_p2_waiver(
    severity: object,
    action: object,
    waiver: dict[str, Any] | None,
    waiver_alerts: list[dict[str, Any]],
) -> bool:
    return (
        severity == "P2"
        and action == "note"
        and waiver is not None
        and not waiver_alerts
    )


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
        finding.get("severity") in {"P1", "P2"}
        and not _finding_has_valid_p2_waiver(
            finding.get("severity"),
            finding.get("required_action"),
            finding.get("waiver") if isinstance(finding.get("waiver"), dict) else None,
            [],
        )
        or finding.get("required_action") == "revise"
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
    expected_goal_hash: str | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if zero_trust and not expected_goal_hash:
        alerts.append(
            _alert(
                "missing_expected_goal_hash",
                "zero-trust review findings require caller expected_goal_hash",
            )
        )
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
