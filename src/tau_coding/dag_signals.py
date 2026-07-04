"""Deterministic signal receipts derived from Tau project DAG receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DAG_SIGNAL_RECEIPT_SCHEMA = "tau.dag_signal_receipt.v1"
SOURCE_DAG_RECEIPT_SCHEMA = "tau.dag_receipt.v1"


def write_dag_signal_receipt(
    source: Path,
    *,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect an existing DAG receipt and write a local signal receipt.

    The signal receipt is observational only. It does not mutate the DAG, update
    Memory, call providers, or rewrite routing state.
    """

    source_path = _resolve_source_dag_receipt(source)
    source_receipt = _read_json_object(source_path, label="DAG receipt")
    if source_receipt.get("schema") != SOURCE_DAG_RECEIPT_SCHEMA:
        raise RuntimeError(
            f"DAG receipt schema must be {SOURCE_DAG_RECEIPT_SCHEMA}: {source_path}"
        )
    resolved_receipt_path = _resolve_signal_receipt_path(
        source=source,
        source_path=source_path,
        receipt_path=receipt_path,
    )
    signal_receipt = _build_signal_receipt(source_receipt, source_path, resolved_receipt_path)
    _write_json(resolved_receipt_path, signal_receipt)
    return signal_receipt


def _resolve_source_dag_receipt(source: Path) -> Path:
    resolved = source.expanduser().resolve()
    if resolved.is_dir():
        resolved = resolved / "dag-receipt.json"
    if not resolved.is_file():
        raise RuntimeError(f"DAG receipt does not exist: {resolved}")
    return resolved


def _resolve_signal_receipt_path(
    *,
    source: Path,
    source_path: Path,
    receipt_path: Path | None,
) -> Path:
    if receipt_path is not None:
        return receipt_path.expanduser().resolve()
    resolved_source = source.expanduser().resolve()
    if resolved_source.is_dir():
        return resolved_source / "dag-signal-receipt.json"
    return source_path.parent / "dag-signal-receipt.json"


def _build_signal_receipt(
    source: dict[str, Any],
    source_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    alerts = _alerts(source)
    reviewer_verdicts = _reviewer_verdicts(source)
    node_signals = _node_signals(source, alerts, reviewer_verdicts)
    edge_signals = _edge_signals(source, node_signals)
    negative_signals = _negative_signals(source, alerts, reviewer_verdicts)
    route_reinforcement_candidates = _route_reinforcement_candidates(source, edge_signals)
    source_status = str(source.get("status") or ("PASS" if source.get("ok") is True else "BLOCKED"))
    return {
        "schema": DAG_SIGNAL_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": bool(source.get("mocked", False)),
        "live": bool(source.get("live", True)),
        "provider_live": bool(source.get("provider_live", False)),
        "source_dag_receipt": str(source_path),
        "source_dag_receipt_sha256": f"sha256:{_sha256(source_path)}",
        "receipt_path": str(receipt_path),
        "dag_id": source.get("dag_id"),
        "goal_hash": source.get("active_goal_hash") or source.get("goal_hash"),
        "source_ok": source.get("ok") is True,
        "source_status": source_status,
        "source_verdict": source.get("verdict"),
        "scheduler": source.get("scheduler"),
        "node_signals": node_signals,
        "edge_signals": edge_signals,
        "route_reinforcement_candidates": route_reinforcement_candidates,
        "negative_signals": negative_signals,
        "memory_sync_candidate": True,
        "sync_status": "NOT_SYNCED",
        "sync_reason": "first_slice_local_only",
        "proof_scope": {
            "proves": [
                "Existing DAG receipt was inspected.",
                "Node and edge quality signals were derived deterministically.",
                "No DAG expansion or route mutation was applied.",
            ],
            "does_not_prove": [
                "Adaptive DAG expansion.",
                "Parallel DAG scheduling.",
                "Provider/model semantic quality.",
                "Memory route learning.",
            ],
        },
        "errors": [],
        "timestamp": _utc_stamp(),
    }


def _node_signals(
    source: dict[str, Any],
    alerts: list[dict[str, Any]],
    reviewer_verdicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    node_ids = _node_ids(source, alerts, reviewer_verdicts)
    attempts = _mapping(source.get("node_attempts"))
    artifacts = _mapping(source.get("node_artifacts"))
    node_agents = _node_agent_map(source)
    selected_agents = _string_list(source.get("selected_agents"))
    signals: list[dict[str, Any]] = []
    for node_id in node_ids:
        node_alerts = [alert for alert in alerts if _alert_node(alert) == node_id]
        node_verdicts = [
            verdict
            for verdict in reviewer_verdicts
            if str(verdict.get("reviewed_node_id") or "") == node_id
            or (node_id == "reviewer" and verdict.get("kind") == "reviewer_verdict")
        ]
        missing_evidence = _missing_evidence(node_alerts)
        reviewer_blockers = _reviewer_blockers(node_alerts, node_verdicts)
        negative_reason = _negative_reason(node_alerts, reviewer_blockers)
        attempt_count = _int_or_zero(attempts.get(node_id))
        artifact_count = len(_string_list(artifacts.get(node_id)))
        proof_strength = _proof_strength(source, node_alerts, reviewer_blockers)
        route_reinforcement = round(proof_strength, 3)
        signals.append(
            {
                "node_id": node_id,
                "agent": node_agents.get(node_id) or _agent_for_node(node_id, selected_agents),
                "status": "PASS" if negative_reason is None else "ATTENUATED",
                "attempt_count": attempt_count,
                "required_evidence_missing": missing_evidence,
                "reviewer_pass": any(str(item.get("verdict")) == "PASS" for item in node_verdicts),
                "reviewer_blockers": reviewer_blockers,
                "stop_reason": negative_reason,
                "artifact_count": artifact_count,
                "proof_strength": proof_strength,
                "route_reinforcement": route_reinforcement,
                "negative_signal_reason": negative_reason,
            }
        )
    return signals


def _edge_signals(
    source: dict[str, Any],
    node_signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_node = {
        str(signal.get("node_id")): signal
        for signal in node_signals
        if isinstance(signal.get("node_id"), str)
    }
    signals: list[dict[str, Any]] = []
    for edge in _dict_list(source.get("observed_edges")):
        from_node = str(edge.get("from_node") or "")
        to_node = str(edge.get("to_node") or "")
        from_signal = by_node.get(from_node)
        to_signal = by_node.get(to_node)
        edge_ok = (
            source.get("ok") is True
            and from_signal is not None
            and from_signal.get("negative_signal_reason") is None
            and (to_signal is None or to_signal.get("negative_signal_reason") is None)
        )
        signals.append(
            {
                "from_node": from_node,
                "from_agent": edge.get("from_agent"),
                "to_node": to_node,
                "to_agent": edge.get("to_agent"),
                "status": "REINFORCE" if edge_ok else "DECAY",
                "reinforcement": 1.0 if edge_ok else 0.0,
                "decay": not edge_ok,
                "reason": (
                    "source_dag_passed_without_node_negative_signal"
                    if edge_ok
                    else "source_dag_blocked_or_node_negative_signal"
                ),
            }
        )
    return signals


def _negative_signals(
    source: dict[str, Any],
    alerts: list[dict[str, Any]],
    reviewer_verdicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for alert in alerts:
        signals.append(
            {
                "type": "alert",
                "severity": alert.get("severity"),
                "code": alert.get("code"),
                "node_id": _alert_node(alert),
                "message": alert.get("message"),
                "deterministic": True,
            }
        )
    for verdict in reviewer_verdicts:
        if str(verdict.get("verdict")) == "PASS":
            continue
        signals.append(
            {
                "type": "reviewer_verdict",
                "severity": "BLOCK",
                "code": "reviewer_non_pass",
                "node_id": str(verdict.get("reviewed_node_id") or "reviewer"),
                "message": "Reviewer verdict was not PASS.",
                "deterministic": True,
            }
        )
    if source.get("ok") is not True and not signals:
        signals.append(
            {
                "type": "source_dag_status",
                "severity": "BLOCK",
                "code": str(source.get("verdict") or "source_dag_blocked").lower(),
                "node_id": None,
                "message": "Source DAG receipt did not pass.",
                "deterministic": True,
            }
        )
    return signals


def _route_reinforcement_candidates(
    source: dict[str, Any],
    edge_signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if source.get("ok") is not True:
        return []
    candidates: list[dict[str, Any]] = []
    for edge in edge_signals:
        if edge.get("status") != "REINFORCE":
            continue
        candidates.append(
            {
                "from_node": edge.get("from_node"),
                "to_node": edge.get("to_node"),
                "from_agent": edge.get("from_agent"),
                "to_agent": edge.get("to_agent"),
                "confidence": edge.get("reinforcement"),
                "source": "deterministic_dag_receipt_pass",
                "memory_sync_candidate": True,
                "sync_status": "NOT_SYNCED",
                "sync_reason": "first_slice_local_only",
            }
        )
    return candidates


def _node_ids(
    source: dict[str, Any],
    alerts: list[dict[str, Any]],
    reviewer_verdicts: list[dict[str, Any]],
) -> list[str]:
    ids: set[str] = set()
    ids.update(str(key) for key in _mapping(source.get("node_attempts")))
    ids.update(str(key) for key in _mapping(source.get("node_artifacts")))
    for edge in _dict_list(source.get("observed_edges")):
        if isinstance(edge.get("from_node"), str):
            ids.add(edge["from_node"])
        if isinstance(edge.get("to_node"), str) and edge.get("to_node") != "human":
            ids.add(edge["to_node"])
    for alert in alerts:
        node_id = _alert_node(alert)
        if node_id:
            ids.add(node_id)
    for verdict in reviewer_verdicts:
        reviewed = verdict.get("reviewed_node_id")
        if isinstance(reviewed, str):
            ids.add(reviewed)
    return sorted(ids)


def _node_agent_map(source: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for edge in _dict_list(source.get("observed_edges")):
        from_node = edge.get("from_node")
        from_agent = edge.get("from_agent")
        to_node = edge.get("to_node")
        to_agent = edge.get("to_agent")
        if isinstance(from_node, str) and isinstance(from_agent, str):
            mapping[from_node] = from_agent
        if isinstance(to_node, str) and isinstance(to_agent, str) and to_node != "human":
            mapping[to_node] = to_agent
    return mapping


def _agent_for_node(node_id: str, selected_agents: list[str]) -> str | None:
    if node_id in selected_agents:
        return node_id
    return None


def _alerts(source: dict[str, Any]) -> list[dict[str, Any]]:
    return _dict_list(source.get("alerts"))


def _reviewer_verdicts(source: dict[str, Any]) -> list[dict[str, Any]]:
    return _dict_list(source.get("reviewer_verdicts"))


def _alert_node(alert: dict[str, Any]) -> str | None:
    evidence = alert.get("evidence")
    if isinstance(evidence, dict):
        node_id = evidence.get("node_id")
        if isinstance(node_id, str):
            return node_id
        selected_agent = evidence.get("selected_agent")
        if isinstance(selected_agent, str):
            return selected_agent
        from_node = evidence.get("from_node")
        if isinstance(from_node, str):
            return from_node
    return None


def _missing_evidence(alerts: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for alert in alerts:
        if alert.get("code") != "missing_required_evidence":
            continue
        evidence = alert.get("evidence")
        if isinstance(evidence, dict):
            missing.extend(_string_list(evidence.get("missing")))
    return sorted(set(missing))


def _reviewer_blockers(
    alerts: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
) -> list[str]:
    blockers = [
        str(alert.get("code"))
        for alert in alerts
        if str(alert.get("code", "")).startswith("reviewer_")
        or alert.get("code") == "missing_reviewer_verdict"
    ]
    blockers.extend(
        f"reviewer_verdict:{verdict.get('verdict')}"
        for verdict in verdicts
        if str(verdict.get("verdict")) != "PASS"
    )
    return sorted(set(blockers))


def _negative_reason(alerts: list[dict[str, Any]], reviewer_blockers: list[str]) -> str | None:
    if reviewer_blockers:
        return reviewer_blockers[0]
    if alerts:
        return str(alerts[0].get("code") or "alert")
    return None


def _proof_strength(
    source: dict[str, Any],
    alerts: list[dict[str, Any]],
    reviewer_blockers: list[str],
) -> float:
    if source.get("ok") is not True:
        return 0.0
    if reviewer_blockers:
        return 0.25
    if alerts:
        return 0.5
    return 1.0


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
