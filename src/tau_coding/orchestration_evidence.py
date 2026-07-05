"""Traycer-inspired orchestration evidence derived from Tau run receipts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any

ORCHESTRATION_EVIDENCE_SCHEMA = "tau.orchestration_evidence_receipt.v1"


def build_orchestration_evidence(run_dir: Path, *, write_receipt: bool = True) -> dict[str, Any]:
    """Build a read-only orchestration evidence receipt for a provider-DAG run."""

    resolved = run_dir.expanduser().resolve()
    run_receipt = _read_json_object(resolved / "run-receipt.json", label="run receipt")
    manifest = _read_json_object(resolved / "runtime-manifest.json", label="runtime manifest")
    dag_spec = _read_json_object(Path(str(run_receipt["dag_spec"])), label="DAG spec")
    events = _read_events(Path(str(run_receipt["events_jsonl"])))
    attempts = run_receipt.get("attempts") if isinstance(run_receipt.get("attempts"), list) else []
    node_receipts = _load_node_receipts(attempts)
    provider_sessions = _dict(run_receipt.get("provider_sessions"))
    visible_subagents = _dict(run_receipt.get("visible_subagents"))
    evidence = {
        "schema": ORCHESTRATION_EVIDENCE_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": bool(run_receipt.get("live") is True),
        "provider_live": bool(run_receipt.get("live") is True),
        "execution": "read_only_projection_from_tau_provider_dag_run",
        "run_id": run_receipt.get("run_id"),
        "source_run_dir": str(resolved),
        "source_run_receipt": str(resolved / "run-receipt.json"),
        "source_runtime_manifest": str(resolved / "runtime-manifest.json"),
        "source_dag_spec": str(Path(str(run_receipt["dag_spec"])).expanduser().resolve()),
        "features": {
            "agent_lineage": _agent_lineage(run_receipt, visible_subagents, provider_sessions),
            "execution_timeline": _execution_timeline(events),
            "provider_capabilities": _provider_capabilities(provider_sessions),
            "worktree_session_bindings": _worktree_session_bindings(run_receipt, visible_subagents),
            "review_comments": _review_comments(node_receipts),
            "agent_messages": _agent_messages(events),
            "doctor": _doctor_receipt(
                run_dir=resolved,
                run_receipt=run_receipt,
                manifest=manifest,
                dag_spec=dag_spec,
                provider_sessions=provider_sessions,
                visible_subagents=visible_subagents,
                node_receipts=node_receipts,
            ),
        },
        "feature_counts": {},
        "proof_scope": {
            "proves": [
                "Tau can project agent lineage from a real provider-DAG run receipt",
                "Tau can project a normalized execution timeline from provider-DAG events",
                "Tau can derive provider capability records from structured readiness/session data",
                "Tau can bind visible subagents to Herdr sessions and scratch worktree evidence",
                "Tau can convert reviewer node receipts into typed review-comment records",
                "Tau can represent DAG dispatches as typed agent-message side-channel records",
                "Tau can emit a doctor/status receipt over real run artifacts",
            ],
            "does_not_prove": [
                "new live provider execution by this command",
                "cloud collaboration",
                "remote Tailscale monitoring",
                "GitHub ticket closure",
                "production repository mutation",
                "agent-to-agent transport delivery outside the Tau receipt channel",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    evidence["feature_counts"] = {
        name: len(value) if isinstance(value, list) else 1
        for name, value in evidence["features"].items()
    }
    errors = _evidence_errors(evidence)
    if errors:
        evidence["ok"] = False
        evidence["status"] = "BLOCKED"
        evidence["errors"] = errors
    else:
        evidence["errors"] = []
    if write_receipt:
        _write_json(resolved / "orchestration-evidence-receipt.json", evidence)
    return evidence


def _agent_lineage(
    run_receipt: dict[str, Any],
    visible_subagents: dict[str, Any],
    provider_sessions: dict[str, Any],
) -> list[dict[str, Any]]:
    run_id = str(run_receipt.get("run_id") or "")
    lineage: list[dict[str, Any]] = [
        {
            "schema": "tau.agent_lineage.v1",
            "run_id": run_id,
            "agent_id": "planner",
            "parent_agent_id": None,
            "node_id": "planner",
            "role": "planner",
            "provider_id": _provider_for_role("planner", visible_subagents, provider_sessions),
            **_session_fields(visible_subagents.get("planner")),
        },
        {
            "schema": "tau.agent_lineage.v1",
            "run_id": run_id,
            "agent_id": "orchestrator",
            "parent_agent_id": "planner",
            "node_id": "orchestrator",
            "role": "orchestrator",
            "provider_id": _provider_for_role("orchestrator", visible_subagents, provider_sessions),
            **_session_fields(visible_subagents.get("orchestrator")),
        },
    ]
    for role in ("coder", "reviewer"):
        lineage.append(
            {
                "schema": "tau.agent_lineage.v1",
                "run_id": run_id,
                "agent_id": role,
                "parent_agent_id": "orchestrator",
                "node_id": role,
                "role": role,
                "provider_id": _provider_for_role(role, visible_subagents, provider_sessions),
                **_session_fields(visible_subagents.get(role)),
            }
        )
    return lineage


def _execution_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = []
    for index, event in enumerate(events, start=1):
        timeline.append(
            {
                "schema": "tau.execution_event.v1",
                "seq": index,
                "timestamp": event.get("timestamp"),
                "kind": event.get("kind"),
                "run_id": event.get("run_id"),
                "attempt": event.get("attempt"),
                "actor": event.get("actor") or _actor_from_event(event),
                "provider_id": event.get("provider_id"),
                "pane_id": event.get("pane_id"),
                "work_order_path": event.get("work_order_path"),
                "receipt_path": event.get("receipt_path"),
            }
        )
    return timeline


def _provider_capabilities(provider_sessions: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = []
    for provider_id, session in sorted(provider_sessions.items()):
        record = _dict(session)
        command = str(record.get("foreground_command") or provider_id)
        capabilities.append(
            {
                "schema": "tau.provider_capability.v1",
                "provider_id": provider_id,
                "surface": "herdr_pane",
                "roles_supported": [str(record.get("role") or provider_id)],
                "structured_readiness_supported": bool(
                    record.get("source") == "herdr_process_info"
                ),
                "a2a_supported": False,
                "transcript_supported": bool(record.get("visible_log_path")),
                "stop_supported": True,
                "workspace_binding_supported": bool(record.get("workspace_id")),
                "models": [],
                "foreground_command": command,
                "executable_found": _command_available(command),
                "readiness_state": record.get("state"),
                "ready": bool(record.get("ready") is True),
            }
        )
    return capabilities


def _worktree_session_bindings(
    run_receipt: dict[str, Any],
    visible_subagents: dict[str, Any],
) -> list[dict[str, Any]]:
    scratch = str(run_receipt.get("scratch_worktree") or "")
    bindings = []
    for role, session in sorted(visible_subagents.items()):
        fields = _session_fields(session)
        bindings.append(
            {
                "schema": "tau.worktree_session_binding.v1",
                "run_id": run_receipt.get("run_id"),
                "agent_id": role,
                "node_id": role,
                "worktree_path": scratch,
                "worktree_exists": Path(scratch).exists() if scratch else False,
                "session_bound": bool(fields.get("workspace_id") and fields.get("pane_id")),
                **fields,
            }
        )
    return bindings


def _review_comments(node_receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comments = []
    reviewer_receipts = [
        receipt for receipt in node_receipts if receipt.get("node_id") == "reviewer"
    ]
    reviewer_receipts.sort(key=lambda receipt: int(receipt.get("attempt") or 0))
    pass_attempts = [
        int(receipt.get("attempt") or 0)
        for receipt in reviewer_receipts
        if str(receipt.get("verdict") or receipt.get("status") or "").upper() == "PASS"
    ]
    for receipt in reviewer_receipts:
        if receipt.get("node_id") != "reviewer":
            continue
        attempt = int(receipt.get("attempt") or 0)
        status = str(receipt.get("status") or "UNKNOWN").upper()
        verdict = str(receipt.get("verdict") or status).upper()
        errors = receipt.get("errors") if isinstance(receipt.get("errors"), list) else []
        repair_attempt = next(
            (candidate for candidate in pass_attempts if candidate > attempt),
            None,
        )
        if errors:
            for index, error in enumerate(errors, start=1):
                comments.append(
                    _review_comment(
                        receipt,
                        index=index,
                        severity="major",
                        status="resolved_by_repair" if repair_attempt is not None else "open",
                        body=str(error),
                        repair_attempt=repair_attempt,
                    )
                )
            continue
        body = str(receipt.get("handoff_summary") or f"reviewer verdict: {verdict}")
        comments.append(
            _review_comment(
                receipt,
                index=1,
                severity="info" if verdict == "PASS" else "major",
                status=(
                    "resolved"
                    if verdict == "PASS"
                    else "resolved_by_repair"
                    if repair_attempt is not None
                    else "open"
                ),
                body=body,
                repair_attempt=repair_attempt,
            )
        )
    return comments


def _agent_messages(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = []
    for index, event in enumerate(events, start=1):
        kind = str(event.get("kind") or "")
        if kind == "coder_dispatch":
            sender, receiver = "orchestrator", "coder"
        elif kind == "reviewer_dispatch":
            sender, receiver = "orchestrator", "reviewer"
        elif kind == "reviewer_requested_revision":
            sender, receiver = "reviewer", "coder"
        else:
            continue
        messages.append(
            {
                "schema": "tau.agent_message.v1",
                "message_id": f"msg-{index:04d}",
                "run_id": event.get("run_id"),
                "sender_agent_id": sender,
                "receiver_agent_id": receiver,
                "expects_reply": True,
                "response_id": f"response-{index:04d}",
                "body": _message_body(event),
                "created_at": event.get("timestamp"),
                "delivery_surface": "tau_receipt_side_channel",
                "work_order_path": event.get("work_order_path"),
                "receipt_path": event.get("receipt_path"),
            }
        )
    return messages


def _doctor_receipt(
    *,
    run_dir: Path,
    run_receipt: dict[str, Any],
    manifest: dict[str, Any],
    dag_spec: dict[str, Any],
    provider_sessions: dict[str, Any],
    visible_subagents: dict[str, Any],
    node_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    checks = [
        _check("run_dir_exists", run_dir.exists(), str(run_dir)),
        _check(
            "run_receipt_schema",
            run_receipt.get("schema") == "tau.dag_run_receipt.v1",
            str(run_dir / "run-receipt.json"),
        ),
        _check(
            "runtime_manifest_schema",
            manifest.get("schema") == "tau.provider_dag_runtime_manifest.v1",
            str(run_dir / "runtime-manifest.json"),
        ),
        _check(
            "dag_spec_schema",
            dag_spec.get("schema") == "tau.dag_run_spec.v1",
            str(run_receipt.get("dag_spec")),
        ),
        _check(
            "events_jsonl_exists",
            Path(str(run_receipt.get("events_jsonl"))).exists(),
            str(run_receipt.get("events_jsonl")),
        ),
        _check(
            "scratch_worktree_exists",
            Path(str(run_receipt.get("scratch_worktree"))).exists(),
            str(run_receipt.get("scratch_worktree")),
        ),
        _check("provider_sessions_present", bool(provider_sessions), "provider_sessions"),
        _check("visible_subagents_present", bool(visible_subagents), "visible_subagents"),
        _check("node_receipts_present", bool(node_receipts), "receipts"),
    ]
    for provider_id, session in sorted(provider_sessions.items()):
        record = _dict(session)
        checks.append(
            _check(
                f"{provider_id}_ready",
                record.get("ready") is True and record.get("state") == "ready",
                str(record.get("provider_readiness_path") or provider_id),
            )
        )
        log_path = record.get("visible_log_path")
        if isinstance(log_path, str) and log_path:
            checks.append(
                _check(
                    f"{provider_id}_visible_log_exists",
                    Path(log_path).exists(),
                    log_path,
                )
            )
    return {
        "schema": "tau.doctor_status_receipt.v1",
        "mocked": False,
        "live": bool(run_receipt.get("live") is True),
        "status": "PASS" if all(check["ok"] for check in checks) else "BLOCKED",
        "checks": checks,
    }


def _evidence_errors(evidence: dict[str, Any]) -> list[str]:
    features = _dict(evidence.get("features"))
    errors = []
    for name in (
        "agent_lineage",
        "execution_timeline",
        "provider_capabilities",
        "worktree_session_bindings",
        "review_comments",
        "agent_messages",
    ):
        value = features.get(name)
        if not isinstance(value, list) or not value:
            errors.append(f"{name} is empty")
    doctor = _dict(features.get("doctor"))
    if doctor.get("status") != "PASS":
        errors.append("doctor status is not PASS")
    return errors


def _load_node_receipts(attempts: list[Any]) -> list[dict[str, Any]]:
    receipts = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        for key in ("coder_receipt_path", "reviewer_receipt_path"):
            raw_path = attempt.get(key)
            if not isinstance(raw_path, str) or not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if path.exists():
                receipts.append(_read_json_object(path, label=f"{key} receipt"))
    return receipts


def _review_comment(
    receipt: dict[str, Any],
    *,
    index: int,
    severity: str,
    status: str,
    body: str,
    repair_attempt: int | None = None,
) -> dict[str, Any]:
    comment = {
        "schema": "tau.review_comment.v1",
        "comment_id": f"{receipt.get('node_id')}-attempt-{receipt.get('attempt')}-{index}",
        "artifact_path": receipt.get("work_order_path"),
        "anchor": {"node_id": receipt.get("node_id"), "attempt": receipt.get("attempt")},
        "severity": severity,
        "status": status,
        "author_agent_id": receipt.get("node_id"),
        "body": body,
        "evidence_path": receipt.get("work_order_path"),
        "created_at": _utc_stamp(),
    }
    if repair_attempt is not None:
        comment["repair_attempt"] = repair_attempt
        comment["resolved_by"] = {
            "node_id": "reviewer",
            "attempt": repair_attempt,
            "status": "PASS",
        }
    return comment


def _session_fields(value: Any) -> dict[str, Any]:
    record = _dict(value)
    return {
        "workspace_id": record.get("workspace_id"),
        "pane_id": record.get("pane_id"),
        "terminal_id": record.get("terminal_id"),
        "tab_id": record.get("tab_id"),
        "visible": bool(record.get("visible") is True),
        "process_alive": record.get("process_alive"),
        "visible_log_path": record.get("visible_log_path"),
    }


def _provider_for_role(
    role: str,
    visible_subagents: dict[str, Any],
    provider_sessions: dict[str, Any],
) -> str | None:
    visible = _dict(visible_subagents.get(role))
    if isinstance(visible.get("provider_id"), str):
        return str(visible["provider_id"])
    for provider_id, session in provider_sessions.items():
        if _dict(session).get("role") == role:
            return provider_id
    return None


def _actor_from_event(event: dict[str, Any]) -> str | None:
    kind = str(event.get("kind") or "")
    if kind.startswith("coder_"):
        return "coder"
    if kind.startswith("reviewer_"):
        return "reviewer"
    return None


def _message_body(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "dispatch")
    if event.get("work_order_path"):
        return f"{kind}: execute work order {event['work_order_path']}"
    if event.get("feedback"):
        return f"{kind}: {event['feedback']}"
    return kind


def _command_available(command: str) -> bool:
    first = command.strip().split(" ", 1)[0]
    if not first:
        return False
    path = Path(first)
    if path.is_absolute():
        return path.exists()
    return which(first) is not None


def _check(name: str, ok: bool, evidence: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "evidence": evidence}


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing {label}: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def _read_events(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing events JSONL: {path}") from exc
    events = []
    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict):
            events.append(event)
    return events


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
