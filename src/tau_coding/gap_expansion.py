"""Bridge admitted node evidence gaps into bounded DAG expansion proposals."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_expansion import DAG_EXPANSION_PROPOSAL_SCHEMA
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.node_completion_boundary import NODE_COMPLETION_BOUNDARY_SCHEMA
from tau_coding.project_dag import load_dag_contract_payload, validate_dag_contract

GAP_CANDIDATE_SCHEMA = "tau.gap_candidate.v1"
EXPANSION_ENVELOPE_SCHEMA = "tau.expansion_envelope.v1"
GAP_EXPANSION_BRIDGE_RECEIPT_SCHEMA = "tau.gap_expansion_bridge_receipt.v1"
GAP_EXPANSION_REVISION_EVENT_SCHEMA = "tau.gap_expansion_revision_event.v1"

GAP_DISPOSITIONS = frozenset(
    {
        "eligible_for_policy",
        "human_required",
        "out_of_envelope",
        "duplicate_or_superseded",
        "budget_exhausted",
        "invalid",
    }
)

_IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class GapExpansionResult:
    candidate: dict[str, Any]
    disposition: str
    proposal: dict[str, Any] | None
    alerts: tuple[dict[str, Any], ...]


def write_gap_expansion_bridge_receipt(
    *,
    dag_contract_path: Path,
    boundary_path: Path,
    envelope_path: Path,
    receipt_path: Path,
    proposals_dir: Path,
    source_run_id: str,
    existing_lineages: Iterable[str] = (),
    approved_lineages: Iterable[str] = (),
    used_budget: int = 0,
) -> dict[str, Any]:
    """Derive gap candidates and proposal files without mutating a DAG."""

    resolved_contract_path = dag_contract_path.expanduser().resolve()
    resolved_boundary_path = boundary_path.expanduser().resolve()
    resolved_envelope_path = envelope_path.expanduser().resolve()
    resolved_receipt_path = receipt_path.expanduser().resolve()
    resolved_proposals_dir = proposals_dir.expanduser().resolve()

    contract_payload = load_dag_contract_payload(resolved_contract_path)
    contract = validate_dag_contract(contract_payload)
    boundary = _load_json(resolved_boundary_path, label="node completion boundary")
    envelope = _load_json(resolved_envelope_path, label="expansion envelope")
    results = derive_gap_expansion_results(
        contract_payload=contract_payload,
        boundary=boundary,
        envelope=envelope,
        source_run_id=source_run_id,
        existing_lineages=existing_lineages,
        approved_lineages=approved_lineages,
        used_budget=used_budget,
    )
    resolved_proposals_dir.mkdir(parents=True, exist_ok=True)
    proposal_paths: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    dispositions: dict[str, int] = {key: 0 for key in sorted(GAP_DISPOSITIONS)}
    for result in results:
        candidate = dict(result.candidate)
        candidate["disposition"] = result.disposition
        candidate["alerts"] = list(result.alerts)
        candidates.append(candidate)
        dispositions[result.disposition] += 1
        if result.proposal is not None:
            proposal_path = resolved_proposals_dir / f"{result.proposal['proposal_id']}.json"
            _write_json(proposal_path, result.proposal)
            proposal_paths.append(
                {
                    "candidate_id": str(candidate["candidate_id"]),
                    "canonical_gap_identity": str(candidate["canonical_gap_identity"]),
                    "proposal_id": str(result.proposal["proposal_id"]),
                    "path": str(proposal_path),
                    "sha256": _sha256_uri(proposal_path),
                }
            )

    receipt = {
        "schema": GAP_EXPANSION_BRIDGE_RECEIPT_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "dag_id": contract.dag_id,
        "goal_hash": contract.goal["goal_hash"],
        "dag_contract": str(resolved_contract_path),
        "dag_contract_sha256": _sha256_uri(resolved_contract_path),
        "boundary": str(resolved_boundary_path),
        "boundary_sha256": _sha256_uri(resolved_boundary_path),
        "envelope": str(resolved_envelope_path),
        "envelope_sha256": _sha256_uri(resolved_envelope_path),
        "source_run_id": source_run_id,
        "receipt_path": str(resolved_receipt_path),
        "proposals_dir": str(resolved_proposals_dir),
        "candidate_count": len(candidates),
        "proposal_count": len(proposal_paths),
        "dispositions": dispositions,
        "candidates": candidates,
        "proposal_paths": proposal_paths,
        "direct_graph_mutation": False,
        "model_confidence_used_for_disposition": False,
        "producer_scope_claim_authoritative": False,
        "proof_scope": {
            "proves": [
                "Admitted node evidence gaps were converted into bounded gap candidates.",
                "Candidate dispositions were computed from declared facts and a closed "
                "expansion envelope.",
                "Eligible candidates were translated to tau.dag_expansion_proposal.v1 files.",
                "No graph mutation, provider call, model-confidence override, or Memory "
                "write occurred.",
            ],
            "does_not_prove": [
                "Expansion validation, policy, apply, or child execution.",
                "Human approval UX.",
                "Provider/model semantic quality.",
            ],
        },
        "created_at": _utc_stamp(),
    }
    _write_json(resolved_receipt_path, receipt)
    return receipt


def derive_gap_expansion_results(
    *,
    contract_payload: Mapping[str, Any],
    boundary: Mapping[str, Any],
    envelope: Mapping[str, Any],
    source_run_id: str,
    existing_lineages: Iterable[str] = (),
    approved_lineages: Iterable[str] = (),
    used_budget: int = 0,
) -> tuple[GapExpansionResult, ...]:
    contract = validate_dag_contract(dict(contract_payload))
    boundary_alerts = _validate_boundary(boundary)
    envelope_alerts = _validate_envelope(envelope)
    existing = set(existing_lineages)
    approved = set(approved_lineages)
    evidence_gaps = _dict_list(boundary.get("evidence_gaps"))
    results: list[GapExpansionResult] = []
    for index, gap in enumerate(evidence_gaps):
        candidate = _candidate_from_gap(
            contract_payload=dict(contract_payload),
            boundary=boundary,
            gap=gap,
            source_run_id=source_run_id,
            index=index,
        )
        alerts = [*boundary_alerts, *envelope_alerts]
        if not alerts:
            alerts.extend(
                _candidate_envelope_alerts(
                    candidate=candidate,
                    contract_payload=dict(contract_payload),
                    envelope=envelope,
                    existing_lineages=existing,
                    approved_lineages=approved,
                    used_budget=used_budget,
                )
            )
        disposition = _disposition(alerts)
        proposal = (
            _candidate_to_expansion_proposal(candidate, contract_payload=dict(contract_payload))
            if disposition == "eligible_for_policy"
            else None
        )
        results.append(
            GapExpansionResult(
                candidate=candidate,
                disposition=disposition,
                proposal=proposal,
                alerts=tuple(alerts),
            )
        )
        if disposition == "eligible_for_policy":
            used_budget += 1
            existing.add(str(candidate["canonical_gap_identity"]))
    if not evidence_gaps:
        candidate = _invalid_empty_candidate(contract.dag_id, source_run_id, boundary)
        results.append(
            GapExpansionResult(
                candidate=candidate,
                disposition="invalid",
                proposal=None,
                alerts=(
                    _alert(
                        "invalid",
                        "missing_evidence_gaps",
                        "Boundary contains no evidence_gaps to convert.",
                        {},
                    ),
                ),
            )
        )
    return tuple(results)


def gap_expansion_revision_event_payload(
    *,
    bridge_receipt: Mapping[str, Any],
    validation_receipt: Mapping[str, Any],
    policy_receipt: Mapping[str, Any],
    apply_receipt: Mapping[str, Any],
    runnable_child_ids: Iterable[str],
) -> dict[str, Any]:
    """Build a journal payload showing expansion gates before child dispatch."""

    return {
        "schema": GAP_EXPANSION_REVISION_EVENT_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "bridge_receipt": bridge_receipt.get("receipt_path"),
        "bridge_receipt_sha256": canonical_sha256(dict(bridge_receipt)),
        "validation_receipt": validation_receipt.get("receipt_path"),
        "validation_status": validation_receipt.get("status"),
        "policy_receipt": policy_receipt.get("receipt_path"),
        "policy_status": policy_receipt.get("status"),
        "apply_receipt": apply_receipt.get("receipt_path"),
        "apply_status": apply_receipt.get("status"),
        "expanded_dag": apply_receipt.get("expanded_dag"),
        "runnable_child_ids": sorted(str(item) for item in runnable_child_ids),
        "requires_validation_policy_apply_before_child_runnable": True,
        "direct_graph_mutation": False,
        "created_at": _utc_stamp(),
    }


def _candidate_from_gap(
    *,
    contract_payload: dict[str, Any],
    boundary: Mapping[str, Any],
    gap: Mapping[str, Any],
    source_run_id: str,
    index: int,
) -> dict[str, Any]:
    contract = validate_dag_contract(contract_payload)
    boundary_hash = canonical_sha256(dict(boundary))
    source_node_id = str(boundary.get("node_id") or "")
    source_attempt_id = str(boundary.get("attempt_id") or "")
    source_node = contract.nodes.get(source_node_id)
    proposed_node = (
        gap.get("proposed_node") if isinstance(gap.get("proposed_node"), Mapping) else {}
    )
    proposed_role = str(
        proposed_node.get("role")
        or proposed_node.get("agent")
        or gap.get("proposed_role")
        or "validator"
    )
    adapter = str(proposed_node.get("adapter") or proposed_node.get("executor") or "local")
    child_id = str(
        proposed_node.get("id") or _child_id(source_node_id, str(gap.get("id") or index))
    )
    canonical_gap_identity = canonical_sha256(
        {
            "schema": "tau.canonical_gap_identity.v1",
            "dag_id": contract.dag_id,
            "goal_hash": contract.goal["goal_hash"],
            "source_node_id": source_node_id,
            "gap_id": str(gap.get("id") or ""),
            "boundary_hash": boundary_hash,
        }
    )
    return {
        "schema": GAP_CANDIDATE_SCHEMA,
        "candidate_id": _short_digest(
            {
                "source_run_id": source_run_id,
                "source_attempt_id": source_attempt_id,
                "canonical_gap_identity": canonical_gap_identity,
            },
            prefix="gap-candidate",
        ),
        "canonical_gap_identity": canonical_gap_identity,
        "source": {
            "run_id": source_run_id,
            "node_id": source_node_id,
            "node_role": source_node.agent if source_node is not None else None,
            "attempt_id": source_attempt_id,
            "boundary_sha256": boundary_hash,
        },
        "gap": {
            "id": str(gap.get("id") or ""),
            "description": str(gap.get("statement") or ""),
            "evidence_refs": _dict_list(gap.get("evidence_refs")),
        },
        "proposed_node": {
            "id": child_id,
            "role": proposed_role,
            "adapter": adapter,
            "output_evidence": _string_list(
                proposed_node.get("output_evidence") or proposed_node.get("required_evidence")
            )
            or ["validation_receipt"],
            "max_attempts": _positive_int(proposed_node.get("max_attempts"), default=1),
        },
        "requested_paths": _string_list(gap.get("requested_paths")),
        "requested_capabilities": _string_list(gap.get("requested_capabilities")),
        "requested_resources": _string_list(gap.get("requested_resources")),
        "data_classes": _string_list(gap.get("data_classes")),
        "side_effect_class": str(gap.get("side_effect_class") or "none"),
        "requested_depth_delta": 1,
        "budget": _budget(gap.get("budget")),
        "producer_scope_claim": {
            "claim": gap.get("scope_claim"),
            "authoritative": False,
        },
        "requires_human_approval": bool(gap.get("requires_human_approval")),
    }


def _candidate_to_expansion_proposal(
    candidate: Mapping[str, Any],
    *,
    contract_payload: dict[str, Any],
) -> dict[str, Any]:
    contract = validate_dag_contract(contract_payload)
    source_node_id = str(candidate["source"]["node_id"])
    child = candidate["proposed_node"]
    target = _first_successor(contract_payload, source_node_id)
    if target is None:
        raise RuntimeError(
            f"source node has no existing child to preserve lineage: {source_node_id}"
        )
    node_id = str(child["id"])
    return {
        "schema": DAG_EXPANSION_PROPOSAL_SCHEMA,
        "proposal_id": _short_digest(candidate, prefix="gap-proposal"),
        "parent_dag_id": contract.dag_id,
        "goal_hash": contract.goal["goal_hash"],
        "proposed_by": "validator",
        "phase": "running",
        "reason": f"Bounded follow-up for evidence gap {candidate['gap']['id']}.",
        "source_gap_lineage": {
            "schema": GAP_CANDIDATE_SCHEMA,
            "candidate_id": candidate["candidate_id"],
            "canonical_gap_identity": candidate["canonical_gap_identity"],
            "source": candidate["source"],
            "producer_scope_claim_authoritative": False,
        },
        "new_nodes": [
            {
                "id": node_id,
                "agent": str(child["role"]),
                "executor": str(child["adapter"]),
                "max_attempts": int(child["max_attempts"]),
                "required_evidence": list(child["output_evidence"]),
                "context": {
                    "gap_candidate_id": candidate["candidate_id"],
                    "canonical_gap_identity": candidate["canonical_gap_identity"],
                },
                "source_gap_lineage": {
                    "candidate_id": candidate["candidate_id"],
                    "canonical_gap_identity": candidate["canonical_gap_identity"],
                },
            }
        ],
        "new_edges": [
            {"from": source_node_id, "to": node_id},
            {"from": node_id, "to": target},
        ],
    }


def _candidate_envelope_alerts(
    *,
    candidate: Mapping[str, Any],
    contract_payload: dict[str, Any],
    envelope: Mapping[str, Any],
    existing_lineages: set[str],
    approved_lineages: set[str],
    used_budget: int,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    contract = validate_dag_contract(contract_payload)
    source = candidate["source"]
    child = candidate["proposed_node"]
    lineage = str(candidate["canonical_gap_identity"])
    if lineage in existing_lineages:
        alerts.append(
            _alert(
                "duplicate_or_superseded",
                "duplicate_gap_lineage",
                "Gap lineage already has a candidate or accepted child.",
                {"canonical_gap_identity": lineage},
            )
        )
    max_added = _positive_int(envelope.get("max_added_nodes"), default=1)
    if used_budget >= max_added:
        alerts.append(
            _alert(
                "budget_exhausted",
                "max_added_nodes_exhausted",
                "Expansion envelope has no remaining node budget.",
                {"max_added_nodes": max_added, "used_budget": used_budget},
            )
        )
    source_node_id = str(source["node_id"])
    source_node = contract.nodes.get(source_node_id)
    if source_node is None:
        alerts.append(
            _alert(
                "invalid",
                "source_node_missing",
                "Candidate source node is not in the DAG contract.",
                {"source_node_id": source_node_id},
            )
        )
        return alerts
    if not _allows(envelope, "permitted_parent_nodes", source_node_id):
        alerts.append(
            _out(
                "parent_node_out_of_envelope",
                "Source node is not permitted by the expansion envelope.",
                {"node_id": source_node_id},
            )
        )
    if not _allows(envelope, "permitted_parent_roles", source_node.agent):
        alerts.append(
            _out(
                "parent_role_out_of_envelope",
                "Source node role is not permitted by the expansion envelope.",
                {"role": source_node.agent},
            )
        )
    if not _allows(envelope, "permitted_child_roles", str(child["role"])):
        alerts.append(
            _out(
                "child_role_out_of_envelope",
                "Child role is not permitted by the expansion envelope.",
                {"role": child["role"]},
            )
        )
    if not _allows(envelope, "permitted_adapters", str(child["adapter"])):
        alerts.append(
            _out(
                "adapter_out_of_envelope",
                "Child adapter is not permitted by the expansion envelope.",
                {"adapter": child["adapter"]},
            )
        )
    for field, envelope_field, code in (
        ("requested_paths", "allowed_paths", "path_out_of_envelope"),
        ("requested_capabilities", "allowed_capabilities", "capability_out_of_envelope"),
        ("requested_resources", "allowed_resources", "resource_out_of_envelope"),
        ("data_classes", "allowed_data_classes", "data_class_out_of_envelope"),
    ):
        disallowed = _not_allowed(candidate.get(field), envelope.get(envelope_field))
        if disallowed:
            alerts.append(
                _out(
                    code,
                    f"{field} contains values outside the expansion envelope.",
                    {"values": disallowed},
                )
            )
    if not _allows(envelope, "allowed_side_effect_classes", str(candidate["side_effect_class"])):
        alerts.append(
            _out(
                "side_effect_class_out_of_envelope",
                "Side-effect class is not permitted by the expansion envelope.",
                {"side_effect_class": candidate["side_effect_class"]},
            )
        )
    depth_alert = _depth_alert(candidate.get("requested_depth_delta"), envelope)
    if depth_alert is not None:
        alerts.append(depth_alert)
    budget_alert = _budget_alert(candidate.get("budget"), envelope)
    if budget_alert is not None:
        alerts.append(budget_alert)
    if (
        bool(envelope.get("human_approval_required"))
        or candidate.get("requires_human_approval") is True
    ) and lineage not in approved_lineages:
        alerts.append(
            _alert(
                "human_required",
                "human_approval_required",
                "Exact approval is required before this gap can reach policy/apply.",
                {"canonical_gap_identity": lineage},
            )
        )
    return alerts


def _validate_boundary(boundary: Mapping[str, Any]) -> list[dict[str, Any]]:
    if boundary.get("schema") != NODE_COMPLETION_BOUNDARY_SCHEMA:
        return [
            _alert(
                "invalid",
                "invalid_boundary_schema",
                "Boundary schema is unsupported.",
                {"schema": boundary.get("schema")},
            )
        ]
    if not isinstance(boundary.get("evidence_gaps"), list):
        return [
            _alert(
                "invalid",
                "boundary_evidence_gaps_not_list",
                "Boundary evidence_gaps must be a list.",
                {},
            )
        ]
    return []


def _validate_envelope(envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if envelope.get("schema") != EXPANSION_ENVELOPE_SCHEMA:
        alerts.append(
            _alert(
                "invalid",
                "invalid_envelope_schema",
                "Expansion envelope schema is unsupported.",
                {"schema": envelope.get("schema")},
            )
        )
    if _positive_int(envelope.get("max_added_nodes"), default=0) < 1:
        alerts.append(
            _alert(
                "budget_exhausted",
                "max_added_nodes_missing",
                "Expansion envelope must allow at least one node.",
                {},
            )
        )
    return alerts


def _disposition(alerts: Iterable[Mapping[str, Any]]) -> str:
    severities = [str(alert.get("severity") or "") for alert in alerts]
    for disposition in (
        "invalid",
        "duplicate_or_superseded",
        "budget_exhausted",
        "human_required",
        "out_of_envelope",
    ):
        if disposition in severities:
            return disposition
    return "eligible_for_policy"


def _budget_alert(budget: object, envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(budget, Mapping):
        return None
    for budget_key, envelope_key in (
        ("max_attempts", "max_attempts"),
        ("max_seconds", "max_seconds"),
        ("max_tokens", "max_tokens"),
        ("max_cost_usd", "max_cost_usd"),
    ):
        requested = budget.get(budget_key)
        allowed = envelope.get(envelope_key)
        if requested is None or allowed is None:
            continue
        try:
            if float(requested) > float(allowed):
                return _alert(
                    "budget_exhausted",
                    f"{budget_key}_exceeded",
                    "Candidate budget exceeds the expansion envelope.",
                    {"requested": requested, "allowed": allowed},
                )
        except TypeError, ValueError:
            return _alert(
                "invalid",
                f"{budget_key}_invalid",
                "Candidate budget field is not numeric.",
                {"value": requested},
            )
    return None


def _depth_alert(
    requested_depth_delta: object, envelope: Mapping[str, Any]
) -> dict[str, Any] | None:
    requested = _integer(requested_depth_delta)
    if requested is None or requested < 0:
        return _alert(
            "invalid",
            "requested_depth_delta_invalid",
            "Candidate requested_depth_delta is not a non-negative integer.",
            {"value": requested_depth_delta},
        )
    for envelope_key in ("max_depth_delta", "max_depth"):
        allowed = envelope.get(envelope_key)
        if allowed is None:
            continue
        allowed_number = _integer(allowed)
        if allowed_number is None or allowed_number < 0:
            return _alert(
                "invalid",
                f"{envelope_key}_invalid",
                "Expansion envelope depth field is not a non-negative integer.",
                {"value": allowed},
            )
        if requested > allowed_number:
            return _out(
                "depth_out_of_envelope",
                "Candidate depth delta exceeds the expansion envelope.",
                {"requested_depth_delta": requested, envelope_key: allowed_number},
            )
    return None


def _invalid_empty_candidate(
    dag_id: str, source_run_id: str, boundary: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema": GAP_CANDIDATE_SCHEMA,
        "candidate_id": "gap-candidate-empty",
        "canonical_gap_identity": canonical_sha256(
            {"dag_id": dag_id, "source_run_id": source_run_id, "boundary": dict(boundary)}
        ),
        "source": {
            "run_id": source_run_id,
            "node_id": boundary.get("node_id"),
            "attempt_id": boundary.get("attempt_id"),
        },
        "gap": {"id": "", "description": "", "evidence_refs": []},
        "proposed_node": {
            "id": "",
            "role": "",
            "adapter": "",
            "output_evidence": [],
            "max_attempts": 0,
        },
        "requested_paths": [],
        "requested_capabilities": [],
        "requested_resources": [],
        "data_classes": [],
        "side_effect_class": "",
        "requested_depth_delta": 0,
        "budget": {},
        "producer_scope_claim": {"claim": None, "authoritative": False},
        "requires_human_approval": False,
    }


def _first_successor(contract_payload: Mapping[str, Any], source_node_id: str) -> str | None:
    nodes = {str(item.get("id")) for item in _dict_list(contract_payload.get("nodes"))}
    for edge in _dict_list(contract_payload.get("edges")):
        if edge.get("from") == source_node_id and edge.get("to") in nodes:
            return str(edge["to"])
    return None


def _allows(envelope: Mapping[str, Any], field: str, value: str) -> bool:
    allowed = envelope.get(field)
    if allowed in (None, "*"):
        return True
    allowed_values = set(_string_list(allowed))
    return "*" in allowed_values or value in allowed_values


def _not_allowed(values: object, allowed: object) -> list[str]:
    observed = _string_list(values)
    if allowed in (None, "*"):
        return []
    allowed_values = set(_string_list(allowed))
    if "*" in allowed_values:
        return []
    return sorted(value for value in observed if value not in allowed_values)


def _budget(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(str(item) for item in value if isinstance(item, str) and item.strip())


def _positive_int(value: object, *, default: int) -> int:
    try:
        number = int(value)
    except TypeError, ValueError:
        return default
    return number if number > 0 else default


def _integer(value: object) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _child_id(node_id: str, gap_id: str) -> str:
    raw = f"gap-{node_id}-{gap_id}".strip("-") or "gap-followup"
    cleaned = _IDENTIFIER_RE.sub("-", raw)
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"gap-{cleaned}"
    return cleaned[:64]


def _short_digest(value: object, *, prefix: str) -> str:
    digest = canonical_sha256(value).removeprefix("sha256:")
    return f"{prefix}-{digest[:16]}"


def _out(code: str, message: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return _alert("out_of_envelope", code, message, evidence)


def _alert(severity: str, code: str, message: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "evidence": evidence}


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_uri(path: Path) -> str:
    return f"sha256:{__import__('hashlib').sha256(path.read_bytes()).hexdigest()}"


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
