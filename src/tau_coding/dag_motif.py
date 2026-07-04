"""Validation-only reusable DAG motifs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.project_dag import load_dag_contract_payload, validate_dag_contract

try:
    import yaml
except ImportError:  # pragma: no cover - stripped environments only.
    yaml = None  # type: ignore[assignment]


DAG_MOTIF_SCHEMA = "tau.dag_motif.v1"
DAG_MOTIF_VALIDATION_RECEIPT_SCHEMA = "tau.dag_motif_validation_receipt.v1"
INDEPENDENT_DISSENT_REVIEWER = "independent_dissent_reviewer_v1"


def write_dag_motif_validation_receipt(
    *,
    dag_contract_path: Path,
    motif_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    resolved_contract_path = dag_contract_path.expanduser().resolve()
    resolved_motif_path = motif_path.expanduser().resolve()
    resolved_receipt_path = receipt_path.expanduser().resolve()
    contract_payload = load_dag_contract_payload(resolved_contract_path)
    contract = validate_dag_contract(contract_payload)
    motif = _load_object(resolved_motif_path, label="DAG motif")
    alerts = _validate_motif(contract_payload=contract_payload, motif=motif)
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": DAG_MOTIF_VALIDATION_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": "PASS" if status == "PASS" else str(alerts[0]["code"]).upper(),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "dag_id": contract.dag_id,
        "goal_hash": contract.goal["goal_hash"],
        "dag_contract": str(resolved_contract_path),
        "dag_contract_sha256": f"sha256:{_sha256(resolved_contract_path)}",
        "motif": str(resolved_motif_path),
        "motif_sha256": f"sha256:{_sha256(resolved_motif_path)}",
        "receipt_path": str(resolved_receipt_path),
        "motif_summary": _motif_summary(motif),
        "alerts": alerts,
        "applied": False,
        "mutated_source_dag": False,
        "memory_sync": False,
        "provider_calls": False,
        "proof_scope": {
            "proves": [
                "DAG motif declaration was inspected deterministically.",
                "Independent dissent reviewer topology was checked against the DAG contract.",
                "No DAG route mutation, dispatch, Memory write, GitHub mutation, provider call, or command execution occurred.",
            ],
            "does_not_prove": [
                "Reviewer semantic quality.",
                "Runtime reviewer execution.",
                "Consensus correctness.",
                "Adaptive DAG expansion application.",
                "Memory route learning.",
            ],
        },
        "errors": [],
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt_path, receipt)
    return receipt


def _validate_motif(
    *,
    contract_payload: dict[str, Any],
    motif: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if motif.get("schema") != DAG_MOTIF_SCHEMA:
        return [_alert("BLOCK", "invalid_schema", "Motif schema is not supported.", {})]
    if motif.get("kind") != INDEPENDENT_DISSENT_REVIEWER:
        return [
            _alert(
                "BLOCK",
                "unsupported_motif_kind",
                "Only independent_dissent_reviewer_v1 is supported in this slice.",
                {"kind": motif.get("kind")},
            )
        ]
    contract = validate_dag_contract(contract_payload)
    if motif.get("dag_id") != contract.dag_id:
        alerts.append(
            _alert(
                "BLOCK",
                "dag_id_mismatch",
                "Motif dag_id does not match the DAG contract.",
                {"expected": contract.dag_id, "observed": motif.get("dag_id")},
            )
        )
    if motif.get("goal_hash") != contract.goal["goal_hash"]:
        alerts.append(
            _alert(
                "BLOCK",
                "goal_hash_mismatch",
                "Motif goal_hash does not match the immutable goal hash.",
                {"expected": contract.goal["goal_hash"], "observed": motif.get("goal_hash")},
            )
        )
    nodes = _node_map(contract_payload)
    edges = _edges(contract_payload)
    producer = str(motif.get("producer_node") or "")
    reviewer_nodes = _string_list(motif.get("reviewer_nodes"))
    join_node = str(motif.get("join_node") or "")
    if producer not in nodes:
        alerts.append(_alert("BLOCK", "missing_producer_node", "Producer node is absent.", {"producer_node": producer}))
    if len(reviewer_nodes) < 2:
        alerts.append(
            _alert(
                "BLOCK",
                "insufficient_dissent_reviewers",
                "Independent dissent motif requires at least two reviewer nodes.",
                {"reviewer_nodes": reviewer_nodes},
            )
        )
    if len(set(reviewer_nodes)) != len(reviewer_nodes):
        alerts.append(
            _alert(
                "BLOCK",
                "duplicate_reviewer_node",
                "Reviewer nodes must be distinct.",
                {"reviewer_nodes": reviewer_nodes},
            )
        )
    for reviewer in reviewer_nodes:
        node = nodes.get(reviewer)
        if node is None:
            alerts.append(_alert("BLOCK", "missing_reviewer_node", "Reviewer node is absent.", {"node_id": reviewer}))
            continue
        agent = str(node.get("agent") or "")
        if "reviewer" not in agent and "review" not in reviewer:
            alerts.append(
                _alert(
                    "BLOCK",
                    "reviewer_identity_missing",
                    "Dissent reviewer node must be visibly reviewer-scoped.",
                    {"node_id": reviewer, "agent": agent},
                )
            )
        if producer and (producer, reviewer) not in edges:
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_producer_to_reviewer_edge",
                    "Producer must route independently to each dissent reviewer.",
                    {"from": producer, "to": reviewer},
                )
            )
    for left in reviewer_nodes:
        for right in reviewer_nodes:
            if left != right and (left, right) in edges:
                alerts.append(
                    _alert(
                        "BLOCK",
                        "reviewers_not_independent",
                        "Dissent reviewers must not route to each other before the join.",
                        {"from": left, "to": right},
                    )
                )
    if join_node not in nodes:
        alerts.append(_alert("BLOCK", "missing_join_node", "Join node is absent.", {"join_node": join_node}))
    elif join_node in reviewer_nodes or join_node == producer:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_join_node",
                "Join node must be distinct from producer and reviewer nodes.",
                {"join_node": join_node},
            )
        )
    else:
        for reviewer in reviewer_nodes:
            if (reviewer, join_node) not in edges:
                alerts.append(
                    _alert(
                        "BLOCK",
                        "missing_reviewer_to_join_edge",
                        "Each dissent reviewer must route to the join node.",
                        {"from": reviewer, "to": join_node},
                    )
                )
        join_agent = str(nodes[join_node].get("agent") or "")
        if "reviewer" not in join_agent and "goal-guardian" not in join_agent and "validator" not in join_agent:
            alerts.append(
                _alert(
                    "BLOCK",
                    "join_not_reconciler",
                    "Join node must be reviewer, validator, or goal-guardian scoped.",
                    {"join_node": join_node, "agent": join_agent},
                )
            )
    return alerts


def _node_map(contract_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node in _dict_list(contract_payload.get("nodes")):
        node_id = node.get("id")
        if isinstance(node_id, str):
            result[node_id] = node
    return result


def _edges(contract_payload: dict[str, Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for edge in _dict_list(contract_payload.get("edges")):
        source = edge.get("from")
        target = edge.get("to")
        if isinstance(source, str) and isinstance(target, str):
            result.add((source, target))
    return result


def _motif_summary(motif: dict[str, Any]) -> dict[str, Any]:
    return {
        "motif_id": motif.get("motif_id"),
        "kind": motif.get("kind"),
        "producer_node": motif.get("producer_node"),
        "reviewer_nodes": _string_list(motif.get("reviewer_nodes")),
        "join_node": motif.get("join_node"),
    }


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError("YAML DAG motifs require PyYAML")
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _alert(
    severity: str,
    code: str,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
