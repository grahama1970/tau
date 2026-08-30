"""Tamper-evident, replayable run ledger (tau.run_ledger.v1).

A security officer must be able to REPLAY a Tau run and VERIFY the record was
not edited. This module builds an append-only hash chain over a run's ordered
entries (journal events + admitted receipts + agentic-eval scores) and verifies
it by recomputation, naming the first tampered index.

Design (grahama1970/tau#327): the DEFAULT tier is a simple prev-hash chain
(git / Certificate-Transparency style) -- most runs need tamper-EVIDENCE, not a
full Merkle transparency log. The security-officer tier (Merkle checkpoint +
proof-of-inclusion) layers on top and is intentionally out of scope for this
default core.

Pure stdlib (hashlib/json) so any layer can build/verify a ledger without Tau's
runtime deps. Entry hashing is over canonical JSON, bound to the run's
goal_hash, so a chain from one run cannot be spliced into another.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_LEDGER_SCHEMA = "tau.run_ledger.v1"
RUN_LEDGER_TRACE_SCHEMA = "tau.run_ledger_trace.v1"
AGENTIC_EVAL_RECEIPT_SCHEMA = "tau.agentic_eval_receipt.v1"
ARTIFACT_DIGEST_SCHEMA = "tau.run_ledger_artifact_digest.v1"
RUN_LEDGER_AUDIT_PROJECTION_SCHEMA = "tau.run_ledger_audit_projection.v1"
RUN_LEDGER_AUDIT_VERIFICATION_SCHEMA = "tau.run_ledger_audit_verification.v1"
AGENTIC_EVAL_EVIDENCE_INDEX_SCHEMA = "tau.agentic_eval_evidence_index.v1"
AGENTIC_EVAL_EVIDENCE_VERIFICATION_SCHEMA = "tau.agentic_eval_evidence_verification.v1"
AGENTIC_EVAL_EVIDENCE_SELFTEST_SCHEMA = "tau.agentic_eval_evidence_selftest_receipt.v1"
AUDIT_POLICY_VERSION = "tau.run_ledger_audit_policy.v1"
AUDIT_VERIFIER_VERSION = "tau.run_ledger_audit_verifier.v1"
AGENTIC_EVAL_EVIDENCE_VERIFIER_VERSION = "tau.agentic_eval_evidence_verifier.v1"
AGENTIC_EVAL_REPORT_SCHEMA = "agentic_evals.report.v2"
GENESIS = "GENESIS"
DEFAULT_CLOCK_SOURCE = "ledger_projection_clock"
DEFAULT_RETENTION_CLASS = "standard"
DEFAULT_REDACTION_STATUS = "none"
DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX = (
    "local/agentic-evals/tau-agentic-eval-evidence-index.json"
)
DEFAULT_AGENTIC_EVAL_EVIDENCE_PASS_RECEIPT = (
    "local/agentic-evals/tau-agentic-eval-evidence-index-pass-receipt.json"
)
SELF_AGENTIC_EVAL_EVIDENCE_REPORT = (
    "local/agentic-evals/tau-agentic-eval-evidence-index-agentic-evals-report.json"
)
RETENTION_CLASSES = frozenset(
    {"standard", "restricted", "legal_hold", "ephemeral", "public", "audit_failure"}
)
REDACTION_STATUSES = frozenset({"none", "partial", "redacted", "not_applicable"})


def _canonical(obj: Any) -> str:
    """Deterministic JSON for hashing (stable key order, no incidental spacing)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _entry_hash(prev_hash: str, payload: dict[str, Any], goal_hash: str) -> str:
    material = f"{prev_hash}\n{goal_hash}\n{_canonical(payload)}"
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_sha256(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def admit_agentic_eval(report: dict[str, Any]) -> dict[str, Any]:
    """Project an /agentic-evals report (agentic_evals.report.v2) into an admitted
    ledger evidence entry, preserving readiness, per-case outcomes, and the
    mocked/live proof boundary. This is why the ledger also serves /agentic-evals
    and /triage-error: the score that proves a fix becomes an auditable row.
    """
    cases = []
    for case in report.get("cases", report.get("results", [])) or []:
        outcomes = [t.get("outcome") for t in case.get("trials", []) if isinstance(t, dict)]
        cases.append(
            {
                "name": case.get("name"),
                "type": case.get("type"),
                "real_world": bool(case.get("real_world")),
                "outcomes": outcomes,
                "passed": bool(outcomes) and all(o == "PASS" for o in outcomes),
            }
        )
    return {
        "schema": AGENTIC_EVAL_RECEIPT_SCHEMA,
        "kind": "agentic_eval",
        "skill": report.get("skill"),
        "readiness": report.get("readiness"),
        "live": bool(report.get("live")),
        "mocked": bool(report.get("mocked")),
        "trial_count": report.get("trial_count"),
        "cases": cases,
    }


def build_ledger(
    entries: list[dict[str, Any]],
    *,
    goal_hash: str,
    run_id: str,
    dag_id: str | None = None,
) -> dict[str, Any]:
    """Build a tamper-evident tau.run_ledger.v1 from ordered entries.

    Each entry is wrapped with a monotonic seq and an entry_hash chained to the
    prior entry_hash and the run's goal_hash. entries are opaque payloads
    (journal events, admitted receipts, admit_agentic_eval outputs, ...).
    """
    chained: list[dict[str, Any]] = []
    prev = GENESIS
    for seq, payload in enumerate(entries):
        wrapped = {"seq": seq, "payload": payload}
        h = _entry_hash(prev, wrapped, goal_hash)
        wrapped["prev_hash"] = prev
        wrapped["entry_hash"] = h
        chained.append(wrapped)
        prev = h
    ledger = {
        "schema": RUN_LEDGER_SCHEMA,
        "run_id": run_id,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "built_at": _utc_now(),
        "entry_count": len(chained),
        "head_hash": prev,
        "entries": chained,
    }
    ledger["trace"] = _trace_from_chained_entries(chained)
    ledger["audit_projection"] = build_audit_projection(ledger)
    return ledger


def build_run_ledger_from_run_dir(
    run_dir: Path,
    *,
    agentic_eval_reports: Sequence[Path] = (),
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build and optionally write a ledger for an existing Tau DAG run directory."""
    resolved_run_dir = run_dir.expanduser().resolve()
    receipt_path = resolved_run_dir / "dag-receipt.json"
    generic_receipt = False
    if not receipt_path.is_file():
        receipt_path = resolved_run_dir / "run-receipt.json"
        generic_receipt = True
    receipt = _read_json_object(receipt_path)
    source_dag = _optional_json_object(resolved_run_dir / "source-dag.json")
    source_goal = source_dag.get("goal") if isinstance(source_dag.get("goal"), dict) else {}
    goal_hash = str(
        receipt.get("active_goal_hash")
        or receipt.get("goal_hash")
        or source_dag.get("goal_hash")
        or source_goal.get("sha256")
        or ""
    )
    if not goal_hash:
        raise ValueError("run ledger source receipt is missing goal_hash")
    entries: list[dict[str, Any]] = []
    if generic_receipt:
        entries.extend(_generic_dag_event_entries(receipt, resolved_run_dir))
        entries.extend(_generic_dag_node_entries(receipt))
    else:
        for event in receipt.get("scheduler_events", []) or []:
            if isinstance(event, dict):
                entries.append({"kind": "scheduler_event", **event})
        for dispatch in receipt.get("dispatches", []) or []:
            if isinstance(dispatch, dict):
                entries.append(
                    {
                        "kind": "dispatch_receipt",
                        "schema": dispatch.get("schema"),
                        "selected_agent": dispatch.get("selected_agent"),
                        "status": dispatch.get("status"),
                        "stop_reason": dispatch.get("stop_reason"),
                        "mocked": dispatch.get("mocked"),
                        "live": dispatch.get("live"),
                    }
                )
    entries.extend(_artifact_digest_entries(receipt, resolved_run_dir))
    for path in agentic_eval_reports:
        entries.append(admit_agentic_eval(_read_json_object(path.expanduser().resolve())))
    ledger = build_ledger(
        entries,
        goal_hash=goal_hash,
        run_id=str(
            receipt.get("scheduler_run_id")
            or receipt.get("dag_id")
            or receipt.get("run_id")
            or resolved_run_dir.name
        ),
        dag_id=str(receipt.get("dag_id") or source_dag.get("run_id") or receipt.get("run_id")),
    )
    ledger["source_run_dir"] = str(resolved_run_dir)
    ledger["source_receipt_path"] = str(receipt_path)
    if output_path is not None:
        write_ledger(output_path, ledger)
    return ledger


def _optional_json_object(path: Path) -> dict[str, Any]:
    try:
        return _read_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _generic_dag_event_entries(receipt: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    events_path = Path(str(receipt.get("events_jsonl") or run_dir / "events.jsonl")).expanduser()
    if not events_path.is_absolute():
        events_path = run_dir / events_path
    entries: list[dict[str, Any]] = []
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [
            {
                "kind": "generic_dag_event_source_missing",
                "path": str(events_path),
            }
        ]
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            entries.append(
                {
                    "kind": "generic_dag_event_source_invalid",
                    "source_event_index": index,
                    "path": str(events_path),
                }
            )
            continue
        if not isinstance(event, dict):
            continue
        source_kind = event.get("kind")
        payload = dict(event)
        payload["kind"] = "generic_dag_event"
        payload["event"] = source_kind if isinstance(source_kind, str) else None
        payload["source_event_index"] = index
        entries.append(payload)
    return entries


def _generic_dag_node_entries(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in receipt.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        rows.append(
            {
                "kind": "generic_node_receipt",
                "event": "node_receipt",
                "node_id": node.get("node_id"),
                "attempt": node.get("attempt") or node.get("scheduler_attempt"),
                "attempt_id": node.get("attempt_id") or node.get("scheduler_attempt_id"),
                "status": node.get("status"),
                "verdict": node.get("verdict"),
                "receipt_path": node.get("receipt_path"),
                "resumed": node.get("resumed"),
                "mocked": node.get("mocked"),
                "live": node.get("live"),
            }
        )
    return rows


def _artifact_digest_entries(receipt: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """Project receipt artifacts into digest rows a security engineer can trace.

    The ledger is built before its final `run_ledger` pointer is written back to
    `dag-receipt.json`, so the receipt itself is deliberately excluded: hashing
    it would make every default ledger stale the moment the pointer is attached.
    """
    candidates: list[str] = []
    for key in ("progress_path", "events_jsonl", "checkpoint_path", "current_state_path"):
        value = receipt.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for default_name in ("source-dag.json", "dag-progress.json"):
        default_path = run_dir / default_name
        if default_path.is_file():
            candidates.append(str(default_path))
    artifacts = receipt.get("artifacts")
    if isinstance(artifacts, list):
        candidates.extend(str(path) for path in artifacts if isinstance(path, str))
    seen: set[Path] = set()
    entries: list[dict[str, Any]] = []
    for raw_path in candidates:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = run_dir / path
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if (
            resolved in seen
            or resolved.name.startswith("run-ledger")
            or resolved.name == "dag-receipt.json"
        ):
            continue
        seen.add(resolved)
        relative_path: str | None
        try:
            relative_path = str(resolved.relative_to(run_dir))
        except ValueError:
            relative_path = None
        if not resolved.is_file():
            entries.append(
                {
                    "schema": ARTIFACT_DIGEST_SCHEMA,
                    "kind": "artifact_missing",
                    "path": str(resolved),
                    "relative_path": relative_path,
                }
            )
            continue
        data = resolved.read_bytes()
        entries.append(
            {
                "schema": ARTIFACT_DIGEST_SCHEMA,
                "kind": "artifact_digest",
                "path": str(resolved),
                "relative_path": relative_path,
                "bytes": len(data),
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "json_schema": _json_schema(resolved),
            }
        )
    return entries


def _json_schema(path: Path) -> str | None:
    if path.suffix.lower() != ".json":
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("schema"), str):
        return payload["schema"]
    return None


def _trace_from_chained_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = [
        entry.get("payload") for entry in entries if isinstance(entry.get("payload"), dict)
    ]
    kinds = Counter(
        str(payload.get("kind") or payload.get("schema") or "unknown") for payload in payloads
    )
    node_attempts: dict[tuple[str, str], dict[str, Any]] = {}
    artifact_rows: list[dict[str, Any]] = []
    agentic_eval_rows: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    for entry in entries:
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        seq = entry.get("seq")
        kind = str(payload.get("kind") or payload.get("schema") or "unknown")
        event = payload.get("event") or payload.get("event_type")
        node_id = payload.get("node_id") or payload.get("selected_agent") or payload.get("node")
        attempt = payload.get("attempt") or payload.get("attempt_number")
        status = payload.get("status") or payload.get("verdict") or payload.get("readiness")
        timeline.append(
            {
                "seq": seq,
                "kind": kind,
                "event": event if isinstance(event, str) else None,
                "node_id": node_id if isinstance(node_id, str) else None,
                "attempt": attempt,
                "status": status if isinstance(status, str) else None,
            }
        )
        if isinstance(node_id, str) and node_id:
            key = (node_id, str(attempt or "unknown"))
            row = node_attempts.setdefault(
                key,
                {"node_id": node_id, "attempt": attempt, "events": [], "latest_status": None},
            )
            if isinstance(event, str):
                row["events"].append(event)
            if isinstance(status, str):
                row["latest_status"] = status
        if payload.get("schema") == ARTIFACT_DIGEST_SCHEMA:
            artifact_rows.append(
                {
                    "seq": seq,
                    "kind": kind,
                    "path": payload.get("relative_path") or payload.get("path"),
                    "sha256": payload.get("sha256"),
                    "bytes": payload.get("bytes"),
                    "json_schema": payload.get("json_schema"),
                }
            )
        if payload.get("schema") == AGENTIC_EVAL_RECEIPT_SCHEMA:
            agentic_eval_rows.append(
                {
                    "seq": seq,
                    "skill": payload.get("skill"),
                    "readiness": payload.get("readiness"),
                    "live": payload.get("live"),
                    "mocked": payload.get("mocked"),
                    "trial_count": payload.get("trial_count"),
                }
            )
    return {
        "schema": RUN_LEDGER_TRACE_SCHEMA,
        "entry_count": len(entries),
        "entry_kind_counts": {key: kinds[key] for key in sorted(kinds)},
        "timeline": timeline,
        "node_attempts": sorted(
            node_attempts.values(), key=lambda item: (str(item["node_id"]), str(item["attempt"]))
        ),
        "artifact_count": len(artifact_rows),
        "artifact_digests": artifact_rows,
        "agentic_eval_count": len(agentic_eval_rows),
        "agentic_evals": agentic_eval_rows,
    }


def build_audit_projection(
    ledger: dict[str, Any],
    *,
    policy_version: str = AUDIT_POLICY_VERSION,
    verifier_version: str = AUDIT_VERIFIER_VERSION,
    external_anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compliance-officer projection from the append-only ledger.

    The projection is derived data. It deliberately keeps raw ledger entry
    hashes, payload hashes, and prior-entry hashes so a separate verifier can
    prove that this human-readable view has not omitted, reordered, or rewritten
    the underlying history.
    """
    events = _audit_events_from_ledger(
        ledger,
        policy_version=policy_version,
        verifier_version=verifier_version,
    )
    audit_write_failures = [
        event for event in events if _is_audit_write_failure_action(str(event.get("action") or ""))
    ]
    projection = {
        "schema": RUN_LEDGER_AUDIT_PROJECTION_SCHEMA,
        "projection_version": 1,
        "policy_version": policy_version,
        "verifier_version": verifier_version,
        "projected_at": _utc_now(),
        "raw_history_preserved": True,
        "run_id": ledger.get("run_id"),
        "dag_id": ledger.get("dag_id"),
        "goal_hash": ledger.get("goal_hash"),
        "ledger_schema": ledger.get("schema"),
        "ledger_entry_count": ledger.get("entry_count"),
        "ledger_head_hash": ledger.get("head_hash"),
        "ledger_built_at": ledger.get("built_at"),
        "external_anchor": _normalize_external_anchor(external_anchor, ledger),
        "audit_write_failure_boundary": {
            "status": "RECORDED" if audit_write_failures else "NO_FAILURE_OBSERVED",
            "count": len(audit_write_failures),
            "event_ids": [str(event.get("event_id")) for event in audit_write_failures],
        },
        "events": events,
    }
    projection["milestone_head_anchor"] = projection["external_anchor"]
    projection["summary"] = {
        "event_count": len(events),
        "correction_count": sum(1 for event in events if event.get("correction")),
        "audit_write_failure_count": len(audit_write_failures),
        "retention_classes": sorted({str(event.get("retention_class")) for event in events}),
        "redaction_statuses": sorted({str(event.get("redaction_status")) for event in events}),
        "human_acceptance_event_count": sum(
            1 for event in events if event.get("human_acceptance_event") is True
        ),
    }
    return projection


def render_audit_projection_markdown(projection: dict[str, Any]) -> str:
    """Render a compact Markdown view for compliance-officer review."""
    lines = [
        "# Tau Run Ledger Audit Projection",
        "",
        f"- Schema: `{projection.get('schema')}`",
        f"- Run: `{projection.get('run_id')}`",
        f"- DAG: `{projection.get('dag_id')}`",
        f"- Goal hash: `{projection.get('goal_hash')}`",
        f"- Ledger head: `{projection.get('ledger_head_hash')}`",
        f"- Policy: `{projection.get('policy_version')}`",
        f"- Verifier: `{projection.get('verifier_version')}`",
        f"- External anchor: `{_anchor_status(projection.get('external_anchor'))}`",
        "",
        "| Seq | Event ID | Actor | Action | Time | Outcome | Object | Evidence digest |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for event in projection.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                _md_cell(event.get(key))
                for key in (
                    "ledger_seq",
                    "event_id",
                    "actor",
                    "action",
                    "timestamp",
                    "outcome",
                    "affected_object",
                    "evidence_digest",
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_audit_projection(
    path: Path,
    projection: dict[str, Any],
    *,
    markdown_path: Path | None = None,
) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown_path is not None:
        md = markdown_path.expanduser().resolve()
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_audit_projection_markdown(projection), encoding="utf-8")


def verify_audit_projection(
    ledger: dict[str, Any],
    projection: dict[str, Any],
    *,
    require_audit_write_failure_event: bool = False,
    max_clock_skew_seconds: int = 0,
) -> dict[str, Any]:
    """Independently verify the audit projection against the raw ledger.

    The verifier rebuilds expected audit rows from ledger entries and fails
    closed with stable codes for mutation, omission, reordering, duplicate event
    IDs, time skew, missing required officer fields, invalid correction links,
    retention/redaction class drift, audit-write-failure omissions, and invalid
    milestone head anchoring.
    """
    failures: list[dict[str, Any]] = []
    chain_result = _verify_ledger_chain(ledger, check_trace=True)
    if chain_result.get("ok") is not True:
        failures.append(
            _audit_failure(
                _chain_reason_to_audit_code(str(chain_result.get("reason") or "ledger_invalid")),
                first_bad_index=chain_result.get("first_bad_index"),
                reason=chain_result.get("reason"),
            )
        )
    if projection.get("schema") != RUN_LEDGER_AUDIT_PROJECTION_SCHEMA:
        failures.append(_audit_failure("projection_schema_invalid"))
    for key in ("run_id", "dag_id", "goal_hash"):
        if projection.get(key) != ledger.get(key):
            failures.append(_audit_failure("projection_ledger_binding_mismatch", field=key))
    if projection.get("ledger_head_hash") != ledger.get("head_hash"):
        failures.append(_audit_failure("projection_head_hash_mismatch"))
    if projection.get("ledger_entry_count") != ledger.get("entry_count"):
        failures.append(_audit_failure("projection_event_count_mismatch"))

    expected = _audit_events_from_ledger(
        ledger,
        policy_version=str(projection.get("policy_version") or AUDIT_POLICY_VERSION),
        verifier_version=str(projection.get("verifier_version") or AUDIT_VERIFIER_VERSION),
    )
    actual = projection.get("events")
    if not isinstance(actual, list):
        actual = []
        failures.append(_audit_failure("projection_events_missing"))
    if len(actual) != len(expected):
        failures.append(
            _audit_failure(
                "omission",
                expected_event_count=len(expected),
                actual_event_count=len(actual),
            )
        )

    seen_ids: set[str] = set()
    prior_timestamps: list[tuple[int, datetime]] = []
    expected_by_seq = {event["ledger_seq"]: event for event in expected}
    for index, row in enumerate(actual):
        if not isinstance(row, dict):
            failures.append(_audit_failure("projection_event_invalid", index=index))
            continue
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            failures.append(_audit_failure("missing_required_field", index=index, field="event_id"))
        elif event_id in seen_ids:
            failures.append(_audit_failure("duplicate_event_id", index=index, event_id=event_id))
        else:
            seen_ids.add(event_id)

        seq = row.get("ledger_seq")
        if seq != index:
            failures.append(_audit_failure("sequence_gap", index=index, ledger_seq=seq))
            failures.append(_audit_failure("reorder", index=index, ledger_seq=seq))
        expected_row = expected_by_seq.get(seq) if isinstance(seq, int) else None
        if expected_row is None:
            failures.append(_audit_failure("reorder", index=index, ledger_seq=seq))
            continue
        for field in (
            "actor",
            "action",
            "timestamp",
            "clock_source",
            "outcome",
            "event_schema",
            "evidence_digest",
            "prior_event_hash",
            "retention_class",
            "redaction_status",
        ):
            if not row.get(field):
                failures.append(_audit_failure("missing_required_field", index=index, field=field))
        for field in (
            "actor",
            "action",
            "timestamp",
            "clock_source",
            "outcome",
            "event_schema",
            "affected_object",
            "run_id",
            "attempt_id",
            "retention_class",
            "redaction_status",
            "human_acceptance_event",
            "event_id",
            "ledger_entry_hash",
            "prior_event_hash",
            "payload_sha256",
            "evidence_digest",
        ):
            if row.get(field) != expected_row.get(field):
                code = "mutation"
                if field == "prior_event_hash":
                    code = "reorder"
                if field == "evidence_digest":
                    code = "evidence_digest_mismatch"
                failures.append(_audit_failure(code, index=index, field=field))
        if row.get("retention_class") not in RETENTION_CLASSES:
            failures.append(_audit_failure("invalid_retention_class", index=index))
        if row.get("redaction_status") not in REDACTION_STATUSES:
            failures.append(_audit_failure("invalid_redaction_status", index=index))
        correction = row.get("correction")
        if correction not in (None, {}) and not isinstance(correction, dict):
            failures.append(_audit_failure("invalid_correction_link", index=index))
        elif isinstance(correction, dict):
            for field in ("corrects_event_id", "supersedes_event_id"):
                target = correction.get(field)
                if target and target not in seen_ids:
                    failures.append(
                        _audit_failure("invalid_correction_link", index=index, field=field)
                    )
        if row.get("human_acceptance_inferred") is True:
            failures.append(_audit_failure("human_acceptance_inferred", index=index))
        timestamp = _parse_timestamp(row.get("timestamp"))
        if timestamp is None:
            failures.append(_audit_failure("invalid_timestamp", index=index))
        else:
            prior_timestamps.append((index, timestamp))

    for (left_index, left), (right_index, right) in zip(prior_timestamps, prior_timestamps[1:]):
        if (right - left).total_seconds() < -max_clock_skew_seconds:
            failures.append(
                _audit_failure(
                    "time_skew",
                    previous_index=left_index,
                    index=right_index,
                    max_clock_skew_seconds=max_clock_skew_seconds,
                )
            )

    expected_failure_count = sum(
        1 for event in expected if _is_audit_write_failure_action(str(event.get("action") or ""))
    )
    projected_boundary = projection.get("audit_write_failure_boundary")
    projected_failure_count = (
        projected_boundary.get("count") if isinstance(projected_boundary, dict) else None
    )
    if projected_failure_count != expected_failure_count:
        failures.append(_audit_failure("audit_write_failure_projection_mismatch"))
    if require_audit_write_failure_event and expected_failure_count == 0:
        failures.append(_audit_failure("audit_write_failure_missing"))

    anchor_failure = _verify_external_anchor(projection.get("external_anchor"), ledger)
    if anchor_failure is not None:
        failures.append(anchor_failure)
    milestone_anchor_failure = _verify_external_anchor(
        projection.get("milestone_head_anchor", projection.get("external_anchor")), ledger
    )
    if milestone_anchor_failure is not None:
        failures.append(milestone_anchor_failure)

    ok = not failures
    return {
        "schema": RUN_LEDGER_AUDIT_VERIFICATION_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "policy_version": projection.get("policy_version") or AUDIT_POLICY_VERSION,
        "verifier_version": projection.get("verifier_version") or AUDIT_VERIFIER_VERSION,
        "ledger_head_hash": ledger.get("head_hash"),
        "event_count": len(actual),
        "failure_count": len(failures),
        "failure_codes": sorted({str(failure.get("code")) for failure in failures}),
        "failures": failures,
    }


def verify_audit_projection_files(
    *,
    ledger_path: Path,
    projection_path: Path,
    require_audit_write_failure_event: bool = False,
    max_clock_skew_seconds: int = 0,
) -> dict[str, Any]:
    ledger = read_ledger(ledger_path)
    projection = _read_json_object(projection_path.expanduser().resolve())
    result = verify_audit_projection(
        ledger,
        projection,
        require_audit_write_failure_event=require_audit_write_failure_event,
        max_clock_skew_seconds=max_clock_skew_seconds,
    )
    return {
        **result,
        "ledger_path": str(ledger_path.expanduser().resolve()),
        "projection_path": str(projection_path.expanduser().resolve()),
    }


def build_agentic_eval_ledger_evidence_index(
    repo: Path,
    *,
    report_paths: Sequence[Path] = (),
    output_path: Path | None = None,
    generating_command: Sequence[str] = (),
    milestone_id: str = "tau-agentic-evals",
) -> dict[str, Any]:
    """Build a verifier-owned digest index for retained agentic-eval reports.

    The index records report bytes, report-declared commands, report-declared
    source SHA/ref, and every retained artifact hash named by trial
    ``artifact_hashes``. It intentionally excludes its own verifier report by
    default to avoid a self-referential digest loop.
    """

    resolved_repo = repo.expanduser().resolve()
    reports = (
        [path.expanduser() for path in report_paths]
        if report_paths
        else _default_agentic_eval_report_paths(resolved_repo)
    )
    current_sha = _git_text(resolved_repo, "rev-parse", "HEAD")
    current_ref = _git_text(resolved_repo, "branch", "--show-current")
    porcelain = _git_text(resolved_repo, "status", "--porcelain", "--untracked-files=all")
    dirty_tree = porcelain != "" if porcelain is not None else None
    report_entries: list[dict[str, Any]] = []
    artifact_entries: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for raw_report in sorted(reports, key=lambda item: item.as_posix()):
        report_path = raw_report
        if not report_path.is_absolute():
            report_path = resolved_repo / report_path
        try:
            resolved_report = report_path.resolve()
            report = _read_json_object(resolved_report)
            rel_report = _repo_relative(resolved_repo, resolved_report)
            report_entry = _agentic_eval_report_entry(resolved_repo, resolved_report, report)
            report_entries.append(report_entry)
            for artifact in _agentic_eval_artifact_entries(resolved_repo, resolved_report, report):
                artifact_entries[artifact["path"]] = artifact
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(
                {
                    "code": "report_unreadable",
                    "path": _repo_relative(resolved_repo, report_path),
                    "message": str(exc),
                }
            )
            continue
        if report.get("schema") != AGENTIC_EVAL_REPORT_SCHEMA:
            errors.append(
                {
                    "code": "report_schema_mismatch",
                    "path": rel_report,
                    "expected": AGENTIC_EVAL_REPORT_SCHEMA,
                    "actual": report.get("schema"),
                }
            )
    artifacts = [artifact_entries[key] for key in sorted(artifact_entries)]
    index = {
        "schema": AGENTIC_EVAL_EVIDENCE_INDEX_SCHEMA,
        "index_version": 1,
        "milestone_id": milestone_id,
        "verifier_version": AGENTIC_EVAL_EVIDENCE_VERIFIER_VERSION,
        "agentic_evals_runner_version": AGENTIC_EVAL_REPORT_SCHEMA,
        "generated_at": _utc_now(),
        "generating_command": list(generating_command),
        "repo": {
            "root": str(resolved_repo),
            "sha": current_sha,
            "ref": current_ref,
            "dirty_tree": dirty_tree,
            "dirty_tree_declaration": "dirty" if dirty_tree else "clean",
            "status_porcelain": porcelain or "",
        },
        "excluded_reports": [
            {
                "path": SELF_AGENTIC_EVAL_EVIDENCE_REPORT,
                "reason": "self_referential_verifier_report_excluded_from_milestone_index",
            }
        ],
        "report_count": len(report_entries),
        "artifact_count": len(artifacts),
        "reports": report_entries,
        "artifacts": artifacts,
        "errors": errors,
        "ok": not errors,
        "status": "PASS" if not errors else "FAIL",
        "proof_boundary": {
            "mocked": False,
            "live": False,
            "provider_live": False,
            "proves": [
                "Retained agentic-eval report bytes are content-addressed.",
                "Report-referenced retained artifacts are content-addressed.",
                "Report commands, report schema version, report source SHA/ref, and checkout dirty state are recorded.",
            ],
            "does_not_prove": [
                "Provider semantic quality.",
                "Human acceptance of the full GOAL.md outcome.",
                "That excluded self-referential verifier reports are milestone inputs.",
            ],
        },
    }
    index["index_sha256"] = "sha256:" + hashlib.sha256(
        _canonical({key: value for key, value in index.items() if key != "index_sha256"}).encode(
            "utf-8"
        )
    ).hexdigest()
    if output_path is not None:
        _write_json(output_path.expanduser(), index)
    return index


def verify_agentic_eval_ledger_evidence_index(
    index_path: Path,
    repo: Path,
    *,
    require_clean: bool = True,
    require_current_sha: bool = False,
    require_live_reports: bool = True,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Verify an agentic-eval evidence index without regenerating reports."""

    resolved_repo = repo.expanduser().resolve()
    resolved_index = index_path.expanduser().resolve()
    failures: list[dict[str, Any]] = []
    try:
        index = _read_json_object(resolved_index)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        result = _agentic_eval_verification_result(
            index_path=resolved_index,
            repo=resolved_repo,
            failures=[{"code": "index_unreadable", "message": str(exc)}],
            require_clean=require_clean,
            require_current_sha=require_current_sha,
            require_live_reports=require_live_reports,
            index={},
        )
        if receipt_path is not None:
            _write_json(receipt_path.expanduser(), result)
        return result

    if index.get("schema") != AGENTIC_EVAL_EVIDENCE_INDEX_SCHEMA:
        failures.append(
            {
                "code": "index_schema_mismatch",
                "expected": AGENTIC_EVAL_EVIDENCE_INDEX_SCHEMA,
                "actual": index.get("schema"),
            }
        )
    if index.get("verifier_version") != AGENTIC_EVAL_EVIDENCE_VERIFIER_VERSION:
        failures.append(
            {
                "code": "verifier_version_mismatch",
                "expected": AGENTIC_EVAL_EVIDENCE_VERIFIER_VERSION,
                "actual": index.get("verifier_version"),
            }
        )
    current_sha = _git_text(resolved_repo, "rev-parse", "HEAD")
    porcelain = _git_text(resolved_repo, "status", "--porcelain", "--untracked-files=all")
    current_dirty = porcelain != "" if porcelain is not None else None
    index_repo = index.get("repo") if isinstance(index.get("repo"), dict) else {}
    if require_current_sha and index_repo.get("sha") != current_sha:
        failures.append(
            {
                "code": "index_source_sha_mismatch",
                "expected": index_repo.get("sha"),
                "actual": current_sha,
            }
        )
    if require_clean and current_dirty is not False:
        failures.append(
            {
                "code": "dirty_tree_mismatch",
                "expected": "clean",
                "actual": "dirty" if current_dirty else "unknown",
            }
        )
    _verify_agentic_eval_reports(
        resolved_repo,
        index.get("reports") if isinstance(index.get("reports"), list) else [],
        failures,
        require_live_reports=require_live_reports,
    )
    _verify_agentic_eval_artifacts(
        resolved_repo,
        index.get("artifacts") if isinstance(index.get("artifacts"), list) else [],
        failures,
    )
    result = _agentic_eval_verification_result(
        index_path=resolved_index,
        repo=resolved_repo,
        failures=failures,
        require_clean=require_clean,
        require_current_sha=require_current_sha,
        require_live_reports=require_live_reports,
        index=index,
    )
    if receipt_path is not None:
        _write_json(receipt_path.expanduser(), result)
    return result


def write_agentic_eval_ledger_evidence_selftest(
    repo: Path,
    *,
    mode: str,
    out: Path,
) -> dict[str, Any]:
    """Write positive or adversarial receipts for the evidence-index verifier."""

    resolved_repo = repo.expanduser().resolve()
    resolved_out = out.expanduser()
    if mode == "positive":
        index_path = resolved_repo / DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX
        pass_receipt = resolved_repo / DEFAULT_AGENTIC_EVAL_EVIDENCE_PASS_RECEIPT
        index = build_agentic_eval_ledger_evidence_index(
            resolved_repo,
            output_path=index_path,
            generating_command=[
                "uv",
                "run",
                "tau",
                "proof-index",
                "agentic-evals",
                "build",
                "--out",
                DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX,
            ],
        )
        verification = verify_agentic_eval_ledger_evidence_index(
            index_path,
            resolved_repo,
            require_clean=False,
            require_current_sha=False,
            require_live_reports=True,
            receipt_path=pass_receipt,
        )
        payload = _agentic_eval_selftest_payload(
            mode=mode,
            ok=index.get("ok") is True and verification.get("ok") is True,
            verification=verification,
            index_path=index_path,
            expected_failure_code=None,
        )
        _write_json(resolved_out, payload)
        return payload
    return _write_agentic_eval_negative_selftest(resolved_repo, mode=mode, out=resolved_out)


def _write_agentic_eval_negative_selftest(repo: Path, *, mode: str, out: Path) -> dict[str, Any]:
    expected_codes = {
        "mutated-report": "report_digest_mismatch",
        "substituted-report": "report_repo_sha_mismatch",
        "deleted-artifact": "artifact_missing",
        "dirty-tree": "dirty_tree_mismatch",
    }
    if mode not in expected_codes:
        raise ValueError(f"unknown agentic-eval evidence selftest mode: {mode}")
    with tempfile.TemporaryDirectory(prefix=f"tau-agentic-eval-evidence-{mode}-") as tmp:
        mirror = Path(tmp) / "repo"
        shutil.copytree(
            repo,
            mirror,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".mypy_cache"),
        )
        _git_init_commit(mirror)
        index_path = Path(tmp) / "index.json"
        build_agentic_eval_ledger_evidence_index(mirror, output_path=index_path)
        if mode == "mutated-report":
            report = _first_index_report(index_path)
            (mirror / report).write_text(
                (mirror / report).read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
        elif mode == "substituted-report":
            reports = _index_report_paths(index_path)
            if not reports:
                raise RuntimeError("substituted-report selftest requires one retained report")
            report_path = mirror / reports[0]
            substituted = _read_json_object(report_path)
            repo_block = substituted.get("repo") if isinstance(substituted.get("repo"), dict) else {}
            substituted["repo"] = {**repo_block, "sha": "substituted-from-another-sha"}
            _write_json(report_path, substituted)
        elif mode == "deleted-artifact":
            artifact = _first_index_artifact(index_path)
            (mirror / artifact).unlink()
        elif mode == "dirty-tree":
            (mirror / "DIRTY-TREE-CONTROL.txt").write_text("dirty\n", encoding="utf-8")
        verification = verify_agentic_eval_ledger_evidence_index(
            index_path,
            mirror,
            require_clean=mode == "dirty-tree",
            require_current_sha=False,
            require_live_reports=True,
        )
        expected_code = expected_codes[mode]
        detected = expected_code in verification.get("failure_codes", [])
        payload = _agentic_eval_selftest_payload(
            mode=mode,
            ok=detected and verification.get("ok") is False,
            verification=verification,
            index_path=index_path,
            expected_failure_code=expected_code,
        )
        payload["adversarial_detection"] = detected
        payload["verifier_exit_code"] = 1 if verification.get("ok") is False else 0
        _write_json(out, payload)
        return payload


def _agentic_eval_report_entry(
    repo: Path, report_path: Path, report: dict[str, Any]
) -> dict[str, Any]:
    repo_block = report.get("repo") if isinstance(report.get("repo"), dict) else {}
    commands: list[list[str]] = []
    for case in report.get("cases", []) or []:
        if isinstance(case, dict) and isinstance(case.get("argv"), list):
            command = [str(part) for part in case["argv"]]
            if command not in commands:
                commands.append(command)
    return {
        "path": _repo_relative(repo, report_path),
        "sha256": "sha256:" + _sha256_file(report_path),
        "bytes": report_path.stat().st_size,
        "schema": report.get("schema"),
        "agentic_evals_runner_version": report.get("schema"),
        "skill": report.get("skill"),
        "source_manifest": report.get("source"),
        "fixture_sha256": report.get("fixture_sha256"),
        "readiness": report.get("readiness"),
        "mocked": report.get("mocked"),
        "live": report.get("live"),
        "repo_sha": repo_block.get("sha"),
        "repo_ref": repo_block.get("ref"),
        "case_count": report.get("case_count"),
        "trial_count": report.get("trial_count"),
        "generating_commands": commands,
    }


def _agentic_eval_artifact_entries(
    repo: Path, report_path: Path, report: dict[str, Any]
) -> list[dict[str, Any]]:
    source = report.get("source") if isinstance(report.get("source"), str) else "evals"
    fixture_dir = (repo / source).parent
    by_path: dict[str, dict[str, Any]] = {}
    for case in report.get("cases", []) or []:
        if not isinstance(case, dict):
            continue
        case_name = str(case.get("name") or "")
        for trial in case.get("trials", []) or []:
            if not isinstance(trial, dict):
                continue
            artifact_hashes = trial.get("artifact_hashes")
            if not isinstance(artifact_hashes, dict):
                continue
            for raw_ref, expected_sha in artifact_hashes.items():
                if not isinstance(raw_ref, str) or not isinstance(expected_sha, str):
                    continue
                artifact_path = Path(raw_ref)
                if not artifact_path.is_absolute():
                    artifact_path = fixture_dir / artifact_path
                resolved_artifact = artifact_path.resolve()
                rel = _repo_relative(repo, resolved_artifact)
                entry = by_path.setdefault(
                    rel,
                    {
                        "path": rel,
                        "absolute_path": str(resolved_artifact),
                        "sha256": None,
                        "bytes": None,
                        "reports": [],
                        "reported_sha256_values": [],
                    },
                )
                reference = {
                    "report": _repo_relative(repo, report_path),
                    "case": case_name,
                    "trial_id": trial.get("trial_id"),
                    "reference": raw_ref,
                }
                if reference not in entry["reports"]:
                    entry["reports"].append(reference)
                if expected_sha not in entry["reported_sha256_values"]:
                    entry["reported_sha256_values"].append(expected_sha)
                if resolved_artifact.is_file():
                    entry["sha256"] = "sha256:" + _sha256_file(resolved_artifact)
                    entry["bytes"] = resolved_artifact.stat().st_size
    return [by_path[key] for key in sorted(by_path)]


def _verify_agentic_eval_reports(
    repo: Path,
    reports: list[Any],
    failures: list[dict[str, Any]],
    *,
    require_live_reports: bool,
) -> None:
    for entry in reports:
        if not isinstance(entry, dict):
            failures.append({"code": "index_report_entry_invalid"})
            continue
        path = repo / str(entry.get("path") or "")
        if not path.is_file():
            failures.append({"code": "report_missing", "path": entry.get("path")})
            continue
        actual_sha = "sha256:" + _sha256_file(path)
        if actual_sha != entry.get("sha256"):
            failures.append(
                {
                    "code": "report_digest_mismatch",
                    "path": entry.get("path"),
                    "expected": entry.get("sha256"),
                    "actual": actual_sha,
                }
            )
        try:
            report = _read_json_object(path)
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            failures.append({"code": "report_unreadable", "path": entry.get("path"), "message": str(exc)})
            continue
        repo_block = report.get("repo") if isinstance(report.get("repo"), dict) else {}
        comparisons = {
            "schema": report.get("schema"),
            "skill": report.get("skill"),
            "source_manifest": report.get("source"),
            "fixture_sha256": report.get("fixture_sha256"),
            "repo_sha": repo_block.get("sha"),
            "repo_ref": repo_block.get("ref"),
        }
        for field, actual in comparisons.items():
            if actual != entry.get(field):
                failures.append(
                    {
                        "code": "report_identity_mismatch" if field != "repo_sha" else "report_repo_sha_mismatch",
                        "path": entry.get("path"),
                        "field": field,
                        "expected": entry.get(field),
                        "actual": actual,
                    }
                )
        if report.get("readiness") != "READY":
            failures.append({"code": "retained_report_not_ready", "path": entry.get("path")})
        if report.get("mocked") is not False:
            failures.append({"code": "retained_report_mocked", "path": entry.get("path")})
        if require_live_reports and report.get("live") is not True:
            failures.append({"code": "retained_report_not_live", "path": entry.get("path")})


def _verify_agentic_eval_artifacts(
    repo: Path, artifacts: list[Any], failures: list[dict[str, Any]]
) -> None:
    for entry in artifacts:
        if not isinstance(entry, dict):
            failures.append({"code": "index_artifact_entry_invalid"})
            continue
        path = repo / str(entry.get("path") or "")
        if not path.is_file():
            failures.append({"code": "artifact_missing", "path": entry.get("path")})
            continue
        actual_sha = "sha256:" + _sha256_file(path)
        if actual_sha != entry.get("sha256"):
            failures.append(
                {
                    "code": "artifact_digest_mismatch",
                    "path": entry.get("path"),
                    "expected": entry.get("sha256"),
                    "actual": actual_sha,
                }
            )
        reported_values = entry.get("reported_sha256_values")
        if isinstance(reported_values, list) and reported_values and actual_sha not in reported_values:
            failures.append(
                {
                    "code": "artifact_reported_digest_mismatch",
                    "path": entry.get("path"),
                    "actual": actual_sha,
                    "reported_sha256_values": reported_values,
                }
            )


def _agentic_eval_verification_result(
    *,
    index_path: Path,
    repo: Path,
    failures: list[dict[str, Any]],
    require_clean: bool,
    require_current_sha: bool,
    require_live_reports: bool,
    index: dict[str, Any],
) -> dict[str, Any]:
    current_sha = _git_text(repo, "rev-parse", "HEAD")
    current_ref = _git_text(repo, "branch", "--show-current")
    porcelain = _git_text(repo, "status", "--porcelain", "--untracked-files=all")
    current_dirty = porcelain != "" if porcelain is not None else None
    reports = index.get("reports") if isinstance(index.get("reports"), list) else []
    artifacts = index.get("artifacts") if isinstance(index.get("artifacts"), list) else []
    ok = not failures
    return {
        "schema": AGENTIC_EVAL_EVIDENCE_VERIFICATION_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "verifier_version": AGENTIC_EVAL_EVIDENCE_VERIFIER_VERSION,
        "agentic_evals_runner_version": index.get("agentic_evals_runner_version"),
        "index_path": str(index_path),
        "index_sha256": "sha256:" + _sha256_file(index_path) if index_path.is_file() else None,
        "repo": {
            "root": str(repo),
            "sha": current_sha,
            "ref": current_ref,
            "dirty_tree": current_dirty,
        },
        "index_repo": index.get("repo"),
        "require_clean": require_clean,
        "require_current_sha": require_current_sha,
        "require_live_reports": require_live_reports,
        "report_count": len(reports),
        "artifact_count": len(artifacts),
        "retained_reports_live_readback": {
            "mocked": any(not isinstance(row, dict) or row.get("mocked") is not False for row in reports),
            "all_unmocked": all(
                isinstance(row, dict) and row.get("mocked") is False for row in reports
            ),
            "live": all(isinstance(row, dict) and row.get("live") is True for row in reports),
            "ready": all(isinstance(row, dict) and row.get("readiness") == "READY" for row in reports),
            "count": len(reports),
        },
        "failure_count": len(failures),
        "failure_codes": sorted({str(failure.get("code")) for failure in failures}),
        "failures": failures,
        "proof_scope": {
            "proves": [
                "The verifier re-read the evidence index, retained reports, and referenced artifacts by digest without regenerating reports.",
                "Missing, mutated, substituted, wrong-SHA, and dirty-tree evidence can be represented as stable failure codes.",
            ],
            "does_not_prove": [
                "Provider semantic quality.",
                "Human acceptance of the full GOAL.md outcome.",
            ],
        },
    }


def _agentic_eval_selftest_payload(
    *,
    mode: str,
    ok: bool,
    verification: dict[str, Any],
    index_path: Path,
    expected_failure_code: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": AGENTIC_EVAL_EVIDENCE_SELFTEST_SCHEMA,
        "mode": mode,
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "index_path": str(index_path),
        "verification": verification,
        "expected_failure_code": expected_failure_code,
        "failure_codes": verification.get("failure_codes", []),
        "retained_reports_live_readback": verification.get("retained_reports_live_readback"),
    }
    return payload


def _default_agentic_eval_report_paths(repo: Path) -> list[Path]:
    reports = []
    for path in sorted((repo / "local" / "agentic-evals").glob("*agentic-evals-report.json")):
        if _repo_relative(repo, path) == SELF_AGENTIC_EVAL_EVIDENCE_REPORT:
            continue
        reports.append(path)
    return reports


def _index_report_paths(index_path: Path) -> list[Path]:
    index = _read_json_object(index_path)
    reports = index.get("reports") if isinstance(index.get("reports"), list) else []
    return [Path(str(item.get("path"))) for item in reports if isinstance(item, dict)]


def _first_index_report(index_path: Path) -> Path:
    reports = _index_report_paths(index_path)
    if not reports:
        raise RuntimeError("agentic-eval evidence index has no reports")
    return reports[0]


def _first_index_artifact(index_path: Path) -> Path:
    index = _read_json_object(index_path)
    artifacts = index.get("artifacts") if isinstance(index.get("artifacts"), list) else []
    for artifact in artifacts:
        if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
            return Path(artifact["path"])
    raise RuntimeError("agentic-eval evidence index has no artifacts")


def _git_text(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _git_init_commit(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=tau@example.invalid",
            "-c",
            "user.name=Tau Evidence Selftest",
            "commit",
            "-q",
            "-m",
            "selftest fixture",
        ],
        check=True,
    )


def _repo_relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _audit_events_from_ledger(
    ledger: dict[str, Any],
    *,
    policy_version: str,
    verifier_version: str,
) -> list[dict[str, Any]]:
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    events: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        payload_hash = _payload_sha256(payload)
        event_id = str(
            payload.get("event_id")
            or payload.get("id")
            or payload.get("receipt_id")
            or f"{ledger.get('run_id') or 'run'}:{entry.get('seq', index)}"
        )
        action = _audit_action(payload)
        evidence = _audit_evidence(payload, payload_hash)
        timestamp = _audit_timestamp(payload, ledger)
        event = {
            "event_id": event_id,
            "ledger_seq": entry.get("seq"),
            "actor": _audit_actor(payload),
            "action": action,
            "timestamp": timestamp,
            "clock_source": _audit_clock_source(payload),
            "outcome": _audit_outcome(payload),
            "event_schema": _audit_event_schema(payload),
            "affected_object": _audit_affected_object(payload, ledger),
            "run_id": payload.get("run_id") or ledger.get("run_id"),
            "attempt_id": payload.get("attempt_id")
            or payload.get("attempt")
            or payload.get("attempt_number"),
            "policy_version": policy_version,
            "verifier_version": verifier_version,
            "evidence": evidence,
            "evidence_digest": _combined_evidence_digest(evidence),
            "prior_event_hash": entry.get("prev_hash"),
            "ledger_entry_hash": entry.get("entry_hash"),
            "payload_sha256": payload_hash,
            "correction": _audit_correction(payload),
            "retention_class": str(payload.get("retention_class") or DEFAULT_RETENTION_CLASS),
            "redaction_status": str(payload.get("redaction_status") or DEFAULT_REDACTION_STATUS),
            "human_acceptance_event": _is_human_acceptance_event(payload),
        }
        events.append(event)
    return events


def _audit_actor(payload: dict[str, Any]) -> str:
    actor = (
        payload.get("actor")
        or payload.get("selected_agent")
        or payload.get("agent")
        or payload.get("previous_subagent")
        or payload.get("node_id")
        or payload.get("node")
    )
    return str(actor or "tau")


def _audit_action(payload: dict[str, Any]) -> str:
    action = (
        payload.get("action")
        or payload.get("event")
        or payload.get("event_type")
        or payload.get("kind")
        or payload.get("schema")
    )
    return str(action or "ledger_event")


def _audit_timestamp(payload: dict[str, Any], ledger: dict[str, Any]) -> str:
    timestamp = (
        payload.get("timestamp")
        or payload.get("time")
        or payload.get("created_at")
        or payload.get("observed_at")
        or payload.get("started_at")
        or payload.get("ended_at")
        or ledger.get("built_at")
    )
    return str(timestamp or _utc_now())


def _audit_clock_source(payload: dict[str, Any]) -> str:
    return str(
        payload.get("clock_source") or payload.get("timestamp_source") or DEFAULT_CLOCK_SOURCE
    )


def _audit_outcome(payload: dict[str, Any]) -> str:
    outcome = (
        payload.get("outcome")
        or payload.get("status")
        or payload.get("verdict")
        or payload.get("readiness")
        or payload.get("result")
    )
    if isinstance(outcome, dict):
        outcome = outcome.get("status")
    return str(outcome or "RECORDED")


def _audit_event_schema(payload: dict[str, Any]) -> str:
    return str(payload.get("schema") or payload.get("kind") or "tau.run_ledger_event.v1")


def _audit_affected_object(payload: dict[str, Any], ledger: dict[str, Any]) -> str:
    affected = (
        payload.get("affected_object")
        or payload.get("object")
        or payload.get("node_id")
        or payload.get("selected_agent")
        or payload.get("node")
        or payload.get("relative_path")
        or payload.get("path")
        or ledger.get("dag_id")
        or ledger.get("run_id")
    )
    return str(affected or "run")


def _audit_evidence(payload: dict[str, Any], payload_hash: str) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    raw_evidence = payload.get("evidence")
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if isinstance(item, dict):
                evidence.append(_normalize_evidence_item(item))
            else:
                evidence.append({"reference": str(item), "digest": payload_hash})
    for field in ("sha256", "evidence_hash", "evidence_digest", "artifact_sha256"):
        if isinstance(payload.get(field), str):
            evidence.append({"reference": field, "digest": payload[field]})
    if isinstance(payload.get("path"), str):
        evidence.append(
            {
                "reference": payload.get("relative_path") or payload.get("path"),
                "digest": payload.get("sha256") or payload_hash,
                "bytes": payload.get("bytes"),
            }
        )
    if not evidence:
        evidence.append({"reference": "payload", "digest": payload_hash})
    return evidence


def _normalize_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    digest = (
        item.get("digest")
        or item.get("sha256")
        or item.get("evidence_hash")
        or item.get("artifact_sha256")
    )
    reference = (
        item.get("reference") or item.get("path") or item.get("artifact") or item.get("kind")
    )
    return {
        "reference": str(reference or "evidence"),
        "digest": str(digest or _payload_sha256(item)),
        **({"bytes": item.get("bytes")} if item.get("bytes") is not None else {}),
    }


def _combined_evidence_digest(evidence: list[dict[str, Any]]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(evidence).encode("utf-8")).hexdigest()


def _audit_correction(payload: dict[str, Any]) -> dict[str, Any] | None:
    correction = {
        "corrects_event_id": payload.get("corrects_event_id") or payload.get("correction_of"),
        "supersedes_event_id": payload.get("supersedes_event_id") or payload.get("supersedes"),
        "reason": payload.get("correction_reason") or payload.get("supersession_reason"),
    }
    compact = {key: value for key, value in correction.items() if value}
    return compact or None


def _is_human_acceptance_event(payload: dict[str, Any]) -> bool:
    kind = " ".join(
        str(payload.get(key) or "")
        for key in ("kind", "schema", "event", "event_type", "action")
    ).lower()
    return "human_acceptance" in kind or "human accepted" in kind


def _is_audit_write_failure_action(action: str) -> bool:
    normalized = action.lower().replace("-", "_").replace(" ", "_")
    return "audit_write_failure" in normalized or "audit_write_failed" in normalized


def _normalize_external_anchor(
    external_anchor: dict[str, Any] | None, ledger: dict[str, Any]
) -> dict[str, Any]:
    if external_anchor is None:
        return {
            "status": "NOT_CONFIGURED",
            "boundary": "External milestone head anchoring was not configured for this run.",
        }
    anchor = dict(external_anchor)
    anchor.setdefault("status", "CONFIGURED")
    anchor.setdefault("ledger_head_hash", ledger.get("head_hash"))
    return anchor


def _verify_external_anchor(anchor: Any, ledger: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(anchor, dict):
        return _audit_failure("invalid_external_anchor", reason="anchor_not_object")
    status = str(anchor.get("status") or "")
    if status == "NOT_CONFIGURED":
        if not anchor.get("boundary"):
            return _audit_failure(
                "invalid_external_anchor", reason="missing_not_configured_boundary"
            )
        return None
    if status not in {"CONFIGURED", "ANCHORED", "PASS"}:
        return _audit_failure("invalid_external_anchor", reason="invalid_status")
    if anchor.get("ledger_head_hash") != ledger.get("head_hash"):
        return _audit_failure("invalid_external_anchor", reason="head_hash_mismatch")
    return None


def _anchor_status(anchor: Any) -> str:
    return str(anchor.get("status")) if isinstance(anchor, dict) else "INVALID"


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|")


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _chain_reason_to_audit_code(reason: str) -> str:
    return {
        "entry_hash_mismatch": "mutation",
        "head_hash_mismatch": "omission",
        "prev_hash_mismatch": "reorder",
        "sequence_break": "sequence_gap",
        "entry_count_mismatch": "omission",
        "trace_mismatch": "mutation",
    }.get(reason, "ledger_invalid")


def _audit_failure(code: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, **extra}


def write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_ledger(path: Path) -> dict[str, Any]:
    return _read_json_object(path.expanduser().resolve())


def verify_ledger_file(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    result = verify_ledger(read_ledger(resolved))
    return {**result, "ledger_path": str(resolved)}


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Recompute the chain. Returns {ok, first_bad_index, reason, head_hash}.

    ok is False and first_bad_index names the earliest entry whose recomputed
    hash, prev linkage, or sequence does not match -- the officer's tamper check.
    """
    result = _verify_ledger_chain(ledger, check_trace=True)
    if result.get("ok") is not True:
        return result
    projection = ledger.get("audit_projection")
    if projection is not None:
        if not isinstance(projection, dict):
            return {
                "ok": False,
                "first_bad_index": None,
                "reason": "audit_projection_mismatch",
                "head_hash": result.get("head_hash"),
            }
        projection_result = verify_audit_projection(ledger, projection)
        if projection_result.get("ok") is not True:
            return {
                "ok": False,
                "first_bad_index": None,
                "reason": "audit_projection_mismatch",
                "head_hash": result.get("head_hash"),
                "audit_verification": projection_result,
            }
    return result


def _verify_ledger_chain(ledger: dict[str, Any], *, check_trace: bool) -> dict[str, Any]:
    if ledger.get("schema") != RUN_LEDGER_SCHEMA:
        return {"ok": False, "first_bad_index": None, "reason": "wrong_schema"}
    entries = ledger.get("entries", [])
    if not isinstance(entries, list):
        return {"ok": False, "first_bad_index": None, "reason": "entries_not_list"}
    goal_hash = ledger.get("goal_hash", "")
    prev = GENESIS
    for idx, entry in enumerate(entries):
        if entry.get("seq") != idx:
            return {"ok": False, "first_bad_index": idx, "reason": "sequence_break"}
        if entry.get("prev_hash") != prev:
            return {"ok": False, "first_bad_index": idx, "reason": "prev_hash_mismatch"}
        recomputed = _entry_hash(
            prev, {"seq": entry.get("seq"), "payload": entry.get("payload")}, goal_hash
        )
        if entry.get("entry_hash") != recomputed:
            return {"ok": False, "first_bad_index": idx, "reason": "entry_hash_mismatch"}
        prev = entry["entry_hash"]
    if ledger.get("entry_count") != len(entries):
        return {"ok": False, "first_bad_index": len(entries), "reason": "entry_count_mismatch"}
    if prev != ledger.get("head_hash"):
        return {
            "ok": False,
            "first_bad_index": len(ledger.get("entries", [])),
            "reason": "head_hash_mismatch",
        }
    if check_trace and "trace" in ledger and ledger.get("trace") != _trace_from_chained_entries(
        list(ledger.get("entries", []))
    ):
        return {
            "ok": False,
            "first_bad_index": None,
            "reason": "trace_mismatch",
            "head_hash": prev,
        }
    return {"ok": True, "first_bad_index": None, "reason": None, "head_hash": prev}
