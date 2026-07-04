"""Offline Traycer validation for one subagent trace and final handoff."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tau_coding.generated_ticket import TAU_AGENT_HANDOFF_SCHEMA, validate_agent_handoff
from tau_coding.traycer.evidence import (
    evidence_claim_confidence,
    evidence_claim_supports,
    normalize_required_evidence,
)
from tau_coding.traycer.models import (
    EVIDENCE_CLAIM_SCHEMA,
    MONITOR_RECEIPT_SCHEMA,
    SEVERITY_RANK,
    STATUS_BY_SEVERITY,
    SUBAGENT_TRACE_SCHEMA,
    TraycerValidationOptions,
)
from tau_coding.traycer.receipts import monitor_alert, sha256_file, utc_now_iso, write_json


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} must be valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def _jsonl_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise RuntimeError(f"trace not found: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: malformed JSON: {exc}")
            continue
        if not isinstance(row, dict):
            errors.append(f"line {line_number}: JSONL row must be an object")
            continue
        row["_traycer_line_number"] = line_number
        rows.append(row)
    return rows, errors


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


def _target(payload: Mapping[str, Any]) -> dict[str, str] | None:
    github = payload.get("github")
    if not isinstance(github, Mapping):
        return None
    repo = github.get("repo")
    target = github.get("target")
    if isinstance(repo, str) and isinstance(target, str):
        return {"repo": repo, "target": target}
    return None


def _trace_id(row: Mapping[str, Any]) -> str:
    value = row.get("trace_id") or row.get("claim_id")
    if isinstance(value, str) and value:
        return value
    line_number = row.get("_traycer_line_number")
    return f"line-{line_number}"


def _run_id(rows: list[dict[str, Any]], handoff_path: Path) -> str:
    for row in rows:
        value = row.get("run_id")
        if isinstance(value, str) and value:
            return value
    return handoff_path.stem


def _observed_agent(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        agent = row.get("agent")
        if isinstance(agent, Mapping):
            name = agent.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def _severity_status(alerts: list[dict[str, Any]]) -> tuple[str, bool, bool, str]:
    max_severity = "NONE"
    for alert in alerts:
        severity = alert.get("severity")
        if isinstance(severity, str) and SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(
            max_severity,
            0,
        ):
            max_severity = severity
    if max_severity == "NONE":
        return "PASS", True, True, max_severity
    status = STATUS_BY_SEVERITY[max_severity]
    ok = max_severity == "WARN"
    return status, ok, ok, max_severity


def _required_evidence_authority(
    *,
    options: TraycerValidationOptions,
    final_handoff: Mapping[str, Any],
) -> tuple[str | None, str | None, list[dict[str, Any]], dict[str, Any] | None]:
    if options.required_evidence_path is not None:
        payload = _load_json_object(options.required_evidence_path, label="required evidence")
        return (
            "required_evidence",
            None,
            normalize_required_evidence(payload),
            {
                "source": "required_evidence",
                "path": str(options.required_evidence_path),
                "sha256": sha256_file(options.required_evidence_path),
            },
        )
    if options.start_handoff_path is not None:
        payload = _load_json_object(options.start_handoff_path, label="start handoff")
        return (
            "start_handoff",
            None,
            normalize_required_evidence(payload.get("required_evidence")),
            {
                "source": "start_handoff",
                "path": str(options.start_handoff_path),
                "sha256": sha256_file(options.start_handoff_path),
            },
        )
    if options.advisory_final_handoff_evidence:
        return (
            "final_handoff_fallback",
            "required evidence was derived from the observed agent's final handoff",
            normalize_required_evidence(final_handoff.get("required_evidence")),
            None,
        )
    return None, None, [], None


def validate_traycer_trace(options: TraycerValidationOptions) -> dict[str, Any]:
    """Validate one trace and final handoff, then write a monitor receipt."""

    rows, parse_errors = _jsonl_rows(options.trace_path)
    final_handoff = _load_json_object(options.handoff_path, label="final handoff")
    run_id = _run_id(rows, options.handoff_path)
    observed_agent = _observed_agent(rows)
    alerts: list[dict[str, Any]] = []

    def add_alert(
        severity: str,
        code: str,
        message: str,
        trace_ids: list[str] | None = None,
        deterministic: bool = True,
        recommended_action: dict[str, Any] | None = None,
    ) -> None:
        alerts.append(
            monitor_alert(
                run_id=run_id,
                index=len(alerts) + 1,
                observed_agent=observed_agent,
                severity=severity,
                code=code,
                message=message,
                evidence_trace_ids=trace_ids,
                deterministic=deterministic,
                recommended_action=recommended_action,
            )
        )

    for error in parse_errors:
        add_alert("BLOCK", "malformed_jsonl", error)

    last_sequence: int | None = None
    first_target: dict[str, str] | None = None
    evidence_claims: list[dict[str, Any]] = []

    for row in rows:
        schema = row.get("schema")
        if schema not in {SUBAGENT_TRACE_SCHEMA, EVIDENCE_CLAIM_SCHEMA}:
            add_alert(
                "BLOCK",
                "unsupported_trace_schema",
                f"unsupported trace schema: {schema!r}",
                [_trace_id(row)],
            )
            continue

        sequence = row.get("sequence")
        if not isinstance(sequence, int):
            add_alert(
                "BLOCK",
                "invalid_sequence",
                "trace row sequence must be an integer",
                [_trace_id(row)],
            )
        elif last_sequence is not None and sequence <= last_sequence:
            add_alert(
                "BLOCK",
                "non_monotonic_sequence",
                f"trace sequence {sequence} is not greater than {last_sequence}",
                [_trace_id(row)],
            )
        elif isinstance(sequence, int):
            last_sequence = sequence

        goal = _mapping(row, "goal")
        goal_hash = goal.get("goal_hash")
        if goal_hash != options.active_goal_hash:
            add_alert(
                "BLOCK",
                "goal_hash_mismatch",
                f"trace goal_hash {goal_hash!r} does not match active goal hash",
                [_trace_id(row)],
            )

        row_target = _target(row)
        if row_target is not None and first_target is None:
            first_target = row_target
        elif row_target is not None and row_target != first_target:
            add_alert(
                "BLOCK",
                "target_changed",
                f"trace target changed from {first_target!r} to {row_target!r}",
                [_trace_id(row)],
            )

        event = row.get("event")
        if isinstance(event, Mapping) and event.get("kind") == "scope_expansion_requested":
            add_alert(
                "REROUTE",
                "scope_expansion_detected",
                "trace requested scope expansion; route must be reconciled outside the creator",
                [_trace_id(row)],
                recommended_action={
                    "type": "reroute",
                    "next_agent": "goal-guardian",
                    "reason": "Scope expansion requires goal/scope reconciliation.",
                },
            )

        if schema == EVIDENCE_CLAIM_SCHEMA:
            evidence_claims.append(row)
            if evidence_claim_confidence(row) not in {None, "deterministic"}:
                add_alert(
                    "WARN",
                    "weak_evidence_confidence",
                    "evidence claim confidence is not deterministic",
                    [_trace_id(row)],
                    deterministic=False,
                )

    final_schema = final_handoff.get("schema")
    if final_schema != TAU_AGENT_HANDOFF_SCHEMA:
        add_alert(
            "BLOCK",
            "invalid_handoff_schema",
            f"final handoff schema must be {TAU_AGENT_HANDOFF_SCHEMA!r}; got {final_schema!r}",
        )

    final_goal_hash = _mapping(final_handoff, "goal").get("goal_hash")
    if final_goal_hash != options.active_goal_hash:
        add_alert("BLOCK", "goal_hash_mismatch", "final handoff may not change goal.goal_hash")

    final_target = _target(final_handoff)
    if first_target is not None and final_target is not None and final_target != first_target:
        add_alert(
            "BLOCK",
            "target_changed",
            f"final handoff target changed from {first_target!r} to {final_target!r}",
        )

    previous_subagent = final_handoff.get("previous_subagent")
    if observed_agent is not None and previous_subagent != observed_agent:
        add_alert(
            "BLOCK",
            "previous_subagent_mismatch",
            "final handoff previous_subagent "
            f"{previous_subagent!r} does not match observed agent {observed_agent!r}",
        )

    handoff_validation = validate_agent_handoff(
        final_handoff,
        active_goal_hash=options.active_goal_hash,
    )
    if not handoff_validation.ok:
        add_alert(
            "BLOCK",
            "invalid_handoff",
            "; ".join(handoff_validation.errors),
        )

    (
        evidence_authority,
        authority_warning,
        required,
        authority_artifact,
    ) = _required_evidence_authority(options=options, final_handoff=final_handoff)
    if evidence_authority is None:
        add_alert(
            "BLOCK",
            "required_evidence_authority_missing",
            "--required-evidence or --start-handoff is required unless advisory fallback "
            "is explicit",
        )

    for item in required:
        required_id = item["id"]
        matches = [
            claim for claim in evidence_claims if evidence_claim_supports(claim, required_id)
        ]
        required_confidence = item.get("required_confidence")
        if required_confidence:
            matches = [
                claim
                for claim in matches
                if evidence_claim_confidence(claim) == required_confidence
            ]
        if not matches:
            add_alert(
                item.get("severity", "REVIEW"),
                "missing_required_evidence",
                f"required evidence was not satisfied: {required_id}",
            )

    status, ok, next_allowed, max_severity = _severity_status(alerts)
    summary = {
        "max_severity": max_severity,
        "warning_count": sum(1 for alert in alerts if alert.get("severity") == "WARN"),
        "review_count": sum(1 for alert in alerts if alert.get("severity") == "REVIEW"),
        "reroute_alert_count": sum(1 for alert in alerts if alert.get("severity") == "REROUTE"),
        "blocking_alert_count": sum(1 for alert in alerts if alert.get("severity") == "BLOCK"),
        "unresolved_review_count": sum(1 for alert in alerts if alert.get("severity") == "REVIEW"),
    }
    receipt = {
        "schema": MONITOR_RECEIPT_SCHEMA,
        "ok": ok,
        "status": status,
        "mocked": False,
        "live": False,
        "run_id": run_id,
        "observed_agent": observed_agent,
        "active_goal_hash": options.active_goal_hash,
        "evidence_authority": evidence_authority,
        "authority_warning": authority_warning,
        "trace": {
            "path": str(options.trace_path),
            "sha256": sha256_file(options.trace_path),
            "event_count": len(rows),
            "last_sequence": last_sequence,
        },
        "final_handoff": {
            "path": str(options.handoff_path),
            "sha256": sha256_file(options.handoff_path),
        },
        "required_evidence": {
            **(authority_artifact or {"source": evidence_authority}),
            "count": len(required),
            "ids": [item["id"] for item in required],
        },
        "alerts": alerts,
        "summary": summary,
        "verdict": {
            "status": status,
            "next_allowed": next_allowed,
            "review_required": status == "REVIEW",
            "recommended_next_agent": "reviewer" if ok else "goal-guardian",
        },
        "proves": [
            "offline trace rows parsed",
            "active goal hash and target invariants checked",
            "required evidence ids matched against evidence claims",
            (
                "final handoff passed existing Tau handoff validator"
                if handoff_validation.ok
                else "final handoff validator ran"
            ),
        ],
        "does_not_prove": [
            "hidden chain-of-thought correctness",
            "semantic code quality beyond declared evidence",
            "human acceptance of goal changes",
            "live Herdr steering or DAG validation",
        ],
        "artifacts": [
            str(options.trace_path),
            str(options.handoff_path),
            str(options.receipt_path),
        ],
        "errors": [
            alert["violation"]["message"]
            for alert in alerts
            if alert.get("severity") != "WARN"
        ],
        "created_at": utc_now_iso(),
    }
    write_json(options.receipt_path, receipt)
    return receipt
