"""Deterministic terminal-contribution and DAG join decisions."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tau_coding.dag_route_decision import sha256_json

JOIN_POLICY_SCHEMA = "tau.dag_join_policy.v1"
TERMINAL_CONTRIBUTION_SCHEMA = "tau.dag_terminal_contribution.v1"
JOIN_DECISION_SCHEMA = "tau.dag_join_decision.v1"

TERMINAL_STATES = frozenset(
    {"success", "failed", "blocked", "skipped", "cancelled", "timed_out"}
)
ADVERSE_STATES = frozenset({"failed", "blocked", "cancelled", "timed_out"})
JOIN_POLICIES = frozenset(
    {
        "all_success",
        "all_terminal",
        "exact_success_count",
        "minimum_success_count",
        "quorum",
        "any_success",
        "fail_fast",
        "collect_failures",
    }
)
COUNT_POLICIES = frozenset({"exact_success_count", "minimum_success_count"})
JOIN_FAILURE_CODES = frozenset(
    {
        "invalid_join_policy",
        "join_all_success_not_met",
        "join_any_success_not_met",
        "join_command_not_allowed",
        "join_decision_hash_mismatch",
        "join_decision_receipt_write_failed",
        "join_decision_replay_mismatch",
        "join_exact_success_count_impossible",
        "join_exact_success_count_not_met",
        "join_fail_fast_adverse_input",
        "join_minimum_success_count_impossible",
        "join_policy_requires_bounded_ready_queue",
        "join_policy_hash_mismatch",
        "join_provider_not_allowed",
        "join_quorum_fraction_unexpected",
        "join_quorum_fraction_invalid",
        "join_quorum_fraction_missing",
        "join_quorum_impossible",
        "join_required_successes_invalid",
        "join_required_successes_missing",
        "join_required_successes_out_of_range",
        "join_required_successes_unexpected",
        "join_reviewer_not_allowed",
        "join_route_not_allowed",
        "join_requires_multiple_inputs",
        "join_source_binding_mismatch",
        "join_timeout_invalid",
        "join_capabilities_not_allowed",
        "terminal_contribution_conflict",
        "terminal_contribution_duplicate",
        "terminal_contribution_edge_mismatch",
        "terminal_contribution_hash_mismatch",
        "terminal_contribution_reason_invalid",
        "terminal_contribution_receipt_write_failed",
        "terminal_contribution_state_invalid",
    }
)
MAX_JOIN_TIMEOUT_SECONDS = 86_400
MAX_QUORUM_DENOMINATOR = 1_000

CONTRIBUTION_PROOF_SCOPE = {
    "proves": [
        "Tau recorded one terminal state for the declared incoming DAG edge.",
        "Tau hash-bound the contribution to the DAG, goal, edge, and join policy.",
    ],
    "does_not_prove": [
        "The source result is semantically true.",
        "A cancelled contribution terminated the underlying process.",
        "Provider or model quality.",
    ],
}
JOIN_DECISION_PROOF_SCOPE = {
    "proves": [
        "Tau evaluated the closed join policy over ordered terminal contributions.",
        "Tau hash-bound the terminal join decision to its policy and contributions.",
    ],
    "does_not_prove": [
        "Any successful branch result is semantically true.",
        "Operating-system isolation or durable restart recovery.",
        "Provider or model quality.",
    ],
}


class JoinDecisionError(ValueError):
    """A fail-closed join contract, contribution, or replay error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_join_policy(value: object, *, incoming_count: int) -> dict[str, Any]:
    """Validate and normalize a closed join-policy declaration."""

    if incoming_count < 2:
        raise JoinDecisionError(
            "join_requires_multiple_inputs", "join requires at least two incoming edges"
        )
    if not isinstance(value, Mapping):
        raise JoinDecisionError("invalid_join_policy", "join policy must be an object")
    allowed = {
        "schema",
        "policy",
        "timeout_seconds",
        "required_successes",
        "quorum_fraction",
    }
    unexpected = set(value) - allowed
    if unexpected:
        raise JoinDecisionError(
            "invalid_join_policy",
            f"join policy has unsupported fields: {sorted(unexpected)}",
        )
    if value.get("schema") != JOIN_POLICY_SCHEMA:
        raise JoinDecisionError(
            "invalid_join_policy", f"join schema must be {JOIN_POLICY_SCHEMA}"
        )
    policy = value.get("policy")
    if policy not in JOIN_POLICIES:
        raise JoinDecisionError("invalid_join_policy", f"unsupported join policy: {policy}")
    timeout = value.get("timeout_seconds")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or timeout > MAX_JOIN_TIMEOUT_SECONDS
    ):
        raise JoinDecisionError(
            "join_timeout_invalid",
            f"join timeout_seconds must be an integer from 1 to {MAX_JOIN_TIMEOUT_SECONDS}",
        )

    normalized: dict[str, Any] = {
        "schema": JOIN_POLICY_SCHEMA,
        "policy": policy,
        "timeout_seconds": timeout,
    }
    required_successes = value.get("required_successes")
    quorum_fraction = value.get("quorum_fraction")
    if policy in COUNT_POLICIES:
        if required_successes is None:
            raise JoinDecisionError(
                "join_required_successes_missing",
                f"{policy} requires required_successes",
            )
        if (
            isinstance(required_successes, bool)
            or not isinstance(required_successes, int)
            or not 1 <= required_successes <= incoming_count
        ):
            raise JoinDecisionError(
                "join_required_successes_out_of_range",
                "required_successes must be within the declared incoming edge count",
            )
        if quorum_fraction is not None:
            raise JoinDecisionError(
                "join_quorum_fraction_unexpected",
                "count policies do not accept quorum_fraction",
            )
        normalized["required_successes"] = required_successes
    elif policy == "quorum":
        if required_successes is not None:
            raise JoinDecisionError(
                "join_required_successes_unexpected",
                "quorum does not accept required_successes",
            )
        normalized["quorum_fraction"] = _normalize_quorum_fraction(quorum_fraction)
    else:
        if required_successes is not None:
            raise JoinDecisionError(
                "join_required_successes_unexpected",
                f"{policy} does not accept required_successes",
            )
        if quorum_fraction is not None:
            raise JoinDecisionError(
                "join_quorum_fraction_unexpected",
                f"{policy} does not accept quorum_fraction",
            )
    return normalized


def normalize_incoming_edges(
    edges: Sequence[Mapping[str, Any]], *, join_node_id: str
) -> list[dict[str, Any]]:
    """Return stable incoming edge identities in contract order."""

    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for edge in edges:
        if set(edge) - {"edge_index", "source_node_id", "target_node_id", "condition"}:
            raise JoinDecisionError(
                "terminal_contribution_edge_mismatch",
                "incoming edge contains unsupported fields",
            )
        edge_index = edge.get("edge_index")
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        if (
            isinstance(edge_index, bool)
            or not isinstance(edge_index, int)
            or edge_index < 0
            or edge_index in seen
            or not isinstance(source, str)
            or not source
            or target != join_node_id
        ):
            raise JoinDecisionError(
                "terminal_contribution_edge_mismatch", "invalid incoming edge identity"
            )
        seen.add(edge_index)
        normalized.append(
            {
                "edge_index": edge_index,
                "source_node_id": source,
                "target_node_id": target,
                "condition": edge.get("condition"),
            }
        )
    normalized.sort(key=lambda item: item["edge_index"])
    if len(normalized) < 2:
        raise JoinDecisionError(
            "join_requires_multiple_inputs", "join requires at least two incoming edges"
        )
    return normalized


def build_terminal_contribution(
    *,
    dag_id: str,
    goal_hash: str,
    join_node_id: str,
    edge_contract: Mapping[str, Any],
    state: str,
    reason_code: str,
    basis: Mapping[str, Any],
    join_policy: Mapping[str, Any],
    incoming_count: int,
    source_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable hash-bound terminal contribution."""

    if state not in TERMINAL_STATES:
        raise JoinDecisionError(
            "terminal_contribution_state_invalid", f"unsupported terminal state: {state}"
        )
    if not isinstance(reason_code, str) or re.fullmatch(r"[a-z][a-z0-9_]*", reason_code) is None:
        raise JoinDecisionError(
            "terminal_contribution_reason_invalid", "reason_code is required"
        )
    edge = _normalize_single_edge(edge_contract, join_node_id=join_node_id)
    normalized_policy = normalize_join_policy(join_policy, incoming_count=incoming_count)
    payload: dict[str, Any] = {
        "schema": TERMINAL_CONTRIBUTION_SCHEMA,
        "status": "PASS",
        "dag_id": _required_text(dag_id, "dag_id"),
        "goal_hash": _required_text(goal_hash, "goal_hash"),
        "join_node_id": _required_text(join_node_id, "join_node_id"),
        "edge_contract": edge,
        "edge_contract_sha256": sha256_json(edge),
        "state": state,
        "reason_code": reason_code,
        "source_binding": dict(source_binding or {}),
        "basis": dict(basis),
        "join_policy_sha256": sha256_json(normalized_policy),
        "proof_scope": CONTRIBUTION_PROOF_SCOPE,
    }
    payload["contribution_sha256"] = sha256_json(payload)
    return payload


def validate_terminal_contribution(
    value: object,
    *,
    dag_id: str,
    goal_hash: str,
    join_node_id: str,
    edge_contract: Mapping[str, Any],
    join_policy: Mapping[str, Any],
    incoming_count: int,
) -> dict[str, Any]:
    """Validate a contribution against trusted run and edge bindings."""

    if not isinstance(value, Mapping):
        raise JoinDecisionError(
            "terminal_contribution_hash_mismatch", "terminal contribution must be an object"
        )
    payload = dict(value)
    contribution_hash = payload.pop("contribution_sha256", None)
    if contribution_hash != sha256_json(payload):
        raise JoinDecisionError(
            "terminal_contribution_hash_mismatch", "terminal contribution hash mismatch"
        )
    if payload.get("schema") != TERMINAL_CONTRIBUTION_SCHEMA:
        raise JoinDecisionError(
            "terminal_contribution_hash_mismatch", "terminal contribution schema mismatch"
        )
    if payload.get("proof_scope") != CONTRIBUTION_PROOF_SCOPE:
        raise JoinDecisionError(
            "terminal_contribution_hash_mismatch", "terminal contribution proof scope mismatch"
        )
    if (
        payload.get("dag_id") != dag_id
        or payload.get("goal_hash") != goal_hash
        or payload.get("join_node_id") != join_node_id
    ):
        raise JoinDecisionError(
            "terminal_contribution_edge_mismatch", "terminal contribution run binding mismatch"
        )
    expected_edge = _normalize_single_edge(edge_contract, join_node_id=join_node_id)
    if payload.get("edge_contract") != expected_edge:
        raise JoinDecisionError(
            "terminal_contribution_edge_mismatch", "terminal contribution edge mismatch"
        )
    if payload.get("edge_contract_sha256") != sha256_json(expected_edge):
        raise JoinDecisionError(
            "terminal_contribution_hash_mismatch", "terminal edge contract hash mismatch"
        )
    normalized_policy = normalize_join_policy(join_policy, incoming_count=incoming_count)
    if payload.get("join_policy_sha256") != sha256_json(normalized_policy):
        raise JoinDecisionError(
            "join_policy_hash_mismatch", "terminal contribution join-policy hash mismatch"
        )
    if payload.get("state") not in TERMINAL_STATES:
        raise JoinDecisionError(
            "terminal_contribution_state_invalid", "terminal contribution state is invalid"
        )
    reason_code = payload.get("reason_code")
    if not isinstance(reason_code, str) or re.fullmatch(r"[a-z][a-z0-9_]*", reason_code) is None:
        raise JoinDecisionError(
            "terminal_contribution_reason_invalid", "terminal contribution reason is invalid"
        )
    source_binding = payload.get("source_binding")
    if not isinstance(source_binding, Mapping):
        raise JoinDecisionError(
            "join_source_binding_mismatch", "terminal contribution source binding is invalid"
        )
    source_node_id = source_binding.get("source_node_id")
    if source_node_id is not None and source_node_id != expected_edge["source_node_id"]:
        raise JoinDecisionError(
            "join_source_binding_mismatch", "terminal contribution source node mismatch"
        )
    attempt = source_binding.get("attempt")
    if attempt is not None and (
        isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1
    ):
        raise JoinDecisionError(
            "join_source_binding_mismatch", "terminal contribution attempt is invalid"
        )
    basis = payload.get("basis")
    if not isinstance(basis, Mapping):
        raise JoinDecisionError(
            "join_source_binding_mismatch", "terminal contribution basis is invalid"
        )
    if basis.get("kind") == "source_terminal" and (
        source_node_id != expected_edge["source_node_id"]
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 1
    ):
        raise JoinDecisionError(
            "join_source_binding_mismatch",
            "source-terminal contribution requires matching source and positive attempt",
        )
    return dict(value)


def evaluate_join_decision(
    *,
    dag_id: str,
    goal_hash: str,
    join_node_id: str,
    join_policy: Mapping[str, Any],
    incoming_edges: Sequence[Mapping[str, Any]],
    contributions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate a pure join decision over ordered immutable contributions."""

    edges = normalize_incoming_edges(incoming_edges, join_node_id=join_node_id)
    policy = normalize_join_policy(join_policy, incoming_count=len(edges))
    edge_by_index = {edge["edge_index"]: edge for edge in edges}
    contribution_by_index: dict[int, dict[str, Any]] = {}
    for value in contributions:
        edge_value = value.get("edge_contract") if isinstance(value, Mapping) else None
        edge_index = edge_value.get("edge_index") if isinstance(edge_value, Mapping) else None
        if isinstance(edge_index, bool) or not isinstance(edge_index, int):
            raise JoinDecisionError(
                "terminal_contribution_edge_mismatch", "contribution edge index is invalid"
            )
        if edge_index not in edge_by_index:
            raise JoinDecisionError(
                "terminal_contribution_edge_mismatch", "contribution targets an undeclared edge"
            )
        if edge_index in contribution_by_index:
            raise JoinDecisionError(
                "terminal_contribution_duplicate", "incoming edge contributed more than once"
            )
        validated = validate_terminal_contribution(
            value,
            dag_id=dag_id,
            goal_hash=goal_hash,
            join_node_id=join_node_id,
            edge_contract=edge_by_index[edge_index],
            join_policy=policy,
            incoming_count=len(edges),
        )
        contribution_by_index[edge_index] = validated

    counts = {state: 0 for state in sorted(TERMINAL_STATES)}
    for contribution in contribution_by_index.values():
        counts[str(contribution["state"])] += 1
    counts["total"] = len(edges)
    pending = len(edges) - len(contribution_by_index)
    decision, reason_code, threshold = _evaluate_policy(policy, counts=counts, pending=pending)
    if decision == "wait":
        return {
            "schema": JOIN_DECISION_SCHEMA,
            "status": "WAIT",
            "decision": "wait",
            "reason_code": reason_code,
            "pending_edge_indexes": sorted(set(edge_by_index) - set(contribution_by_index)),
            "counts": {**counts, "pending": pending},
            "computed_success_threshold": threshold,
        }
    if pending:
        return {
            "schema": JOIN_DECISION_SCHEMA,
            "status": "TERMINAL_INTENT",
            "decision": decision,
            "reason_code": reason_code,
            "pending_edge_indexes": sorted(set(edge_by_index) - set(contribution_by_index)),
            "counts": {**counts, "pending": pending},
            "computed_success_threshold": threshold,
        }

    ordered_contributions = [
        {
            "edge_index": edge_index,
            "state": contribution_by_index[edge_index]["state"],
            "receipt_sha256": sha256_json(contribution_by_index[edge_index]),
            "contribution_sha256": contribution_by_index[edge_index]["contribution_sha256"],
            "source_node_id": contribution_by_index[edge_index]["edge_contract"][
                "source_node_id"
            ],
            "reason_code": contribution_by_index[edge_index]["reason_code"],
            "attempt": contribution_by_index[edge_index]["source_binding"].get("attempt"),
        }
        for edge_index in sorted(contribution_by_index)
    ]
    incoming_summary = [
        {
            "edge_index": edge["edge_index"],
            "source_node_id": edge["source_node_id"],
            "target_node_id": edge["target_node_id"],
        }
        for edge in edges
    ]
    collected_failures = [
        item for item in ordered_contributions if item["state"] in ADVERSE_STATES
    ]
    payload: dict[str, Any] = {
        "schema": JOIN_DECISION_SCHEMA,
        "status": "BLOCKED" if decision == "block" else "PASS",
        "decision": decision,
        "reason_code": reason_code,
        "dag_id": _required_text(dag_id, "dag_id"),
        "goal_hash": _required_text(goal_hash, "goal_hash"),
        "join_node_id": _required_text(join_node_id, "join_node_id"),
        "join_policy": policy,
        "join_policy_sha256": sha256_json(policy),
        "incoming_edges": incoming_summary,
        "incoming_edges_sha256": sha256_json(incoming_summary),
        "contributions": ordered_contributions,
        "contributions_sha256": sha256_json(ordered_contributions),
        "counts": {**counts, "pending": 0},
        "computed_success_threshold": threshold,
        "collected_failures": collected_failures,
        "proof_scope": JOIN_DECISION_PROOF_SCOPE,
    }
    payload["decision_sha256"] = sha256_json(payload)
    return payload


def validate_join_decision(
    value: object,
    *,
    dag_id: str,
    goal_hash: str,
    join_node_id: str,
    join_policy: Mapping[str, Any],
    incoming_edges: Sequence[Mapping[str, Any]],
    contributions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and replay a terminal join decision."""

    if not isinstance(value, Mapping):
        raise JoinDecisionError("join_decision_hash_mismatch", "join decision must be an object")
    payload = dict(value)
    decision_hash = payload.pop("decision_sha256", None)
    if decision_hash != sha256_json(payload):
        raise JoinDecisionError("join_decision_hash_mismatch", "join decision hash mismatch")
    replay = evaluate_join_decision(
        dag_id=dag_id,
        goal_hash=goal_hash,
        join_node_id=join_node_id,
        join_policy=join_policy,
        incoming_edges=incoming_edges,
        contributions=contributions,
    )
    if replay != dict(value):
        raise JoinDecisionError("join_decision_replay_mismatch", "join decision replay mismatch")
    return replay


def write_immutable_json(path: Path, payload: Mapping[str, Any], *, conflict_code: str) -> None:
    """Atomically create an immutable JSON receipt or accept an identical replay."""

    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") == encoded:
                return
            raise JoinDecisionError(
                conflict_code, f"conflicting receipt already exists: {path}"
            ) from None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def file_sha256(path: Path) -> str:
    """Hash receipt bytes for package and decision bindings."""

    import hashlib

    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _normalize_quorum_fraction(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != {"numerator", "denominator"}:
        raise JoinDecisionError(
            "join_quorum_fraction_missing", "quorum requires numerator and denominator"
        )
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        isinstance(numerator, bool)
        or isinstance(denominator, bool)
        or not isinstance(numerator, int)
        or not isinstance(denominator, int)
        or numerator < 1
        or denominator < 1
        or numerator > denominator
        or denominator > MAX_QUORUM_DENOMINATOR
        or math.gcd(numerator, denominator) != 1
    ):
        raise JoinDecisionError(
            "join_quorum_fraction_invalid", "quorum fraction must be positive, reduced, and <= 1"
        )
    return {"numerator": numerator, "denominator": denominator}


def _evaluate_policy(
    policy: Mapping[str, Any], *, counts: Mapping[str, int], pending: int
) -> tuple[str, str, int | None]:
    total = counts["total"]
    success = counts["success"]
    skipped = counts["skipped"]
    adverse = sum(counts[state] for state in ADVERSE_STATES)
    possible_successes = success + pending
    if pending == 0 and skipped == total:
        return "skip", "join_all_inputs_skipped", None

    name = policy["policy"]
    threshold: int | None = None
    if name == "all_success":
        if adverse or skipped:
            return "block", "join_all_success_not_met", total
        if pending:
            return "wait", "join_waiting_for_all_success", total
        if success == total:
            return "release", "join_all_success", total
        return "block", "join_all_success_not_met", total
    if name == "all_terminal":
        if pending:
            return "wait", "join_waiting_for_all_terminal", None
        return "release", "join_all_terminal", None
    if name == "exact_success_count":
        threshold = policy["required_successes"]
        if success > threshold or possible_successes < threshold:
            return "block", "join_exact_success_count_impossible", threshold
        if pending:
            return "wait", "join_waiting_for_exact_success_count", threshold
        if success == threshold:
            return "release", "join_exact_success_count_met", threshold
        return "block", "join_exact_success_count_not_met", threshold
    if name == "minimum_success_count":
        threshold = policy["required_successes"]
        if success >= threshold:
            return "release", "join_minimum_success_count_met", threshold
        if possible_successes < threshold:
            return "block", "join_minimum_success_count_impossible", threshold
        return "wait", "join_waiting_for_minimum_success_count", threshold
    if name == "quorum":
        fraction = policy["quorum_fraction"]
        threshold = (total * fraction["numerator"] + fraction["denominator"] - 1) // fraction[
            "denominator"
        ]
        if success >= threshold:
            return "release", "join_quorum_met", threshold
        if possible_successes < threshold:
            return "block", "join_quorum_impossible", threshold
        return "wait", "join_waiting_for_quorum", threshold
    if name == "any_success":
        if success:
            return "release", "join_any_success", 1
        if pending:
            return "wait", "join_waiting_for_any_success", 1
        return "block", "join_any_success_not_met", 1
    if name == "fail_fast":
        if adverse:
            return "block", "join_fail_fast_adverse_input", None
        if pending:
            return "wait", "join_waiting_for_fail_fast", None
        if success:
            return "release", "join_fail_fast_complete", None
        return "skip", "join_fail_fast_no_success", None
    if name == "collect_failures":
        if pending:
            return "wait", "join_waiting_to_collect_failures", None
        if adverse:
            return "release", "join_failures_collected", None
        return "skip", "join_no_failures_to_collect", None
    raise JoinDecisionError("invalid_join_policy", f"unsupported join policy: {name}")


def _normalize_single_edge(value: Mapping[str, Any], *, join_node_id: str) -> dict[str, Any]:
    if set(value) - {"edge_index", "source_node_id", "target_node_id", "condition"}:
        raise JoinDecisionError(
            "terminal_contribution_edge_mismatch", "edge contains unsupported fields"
        )
    edge_index = value.get("edge_index")
    source = value.get("source_node_id")
    target = value.get("target_node_id")
    if (
        isinstance(edge_index, bool)
        or not isinstance(edge_index, int)
        or edge_index < 0
        or not isinstance(source, str)
        or not source
        or target != join_node_id
    ):
        raise JoinDecisionError(
            "terminal_contribution_edge_mismatch", "invalid incoming edge identity"
        )
    return {
        "edge_index": edge_index,
        "source_node_id": source,
        "target_node_id": target,
        "condition": value.get("condition"),
    }


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise JoinDecisionError("join_source_binding_mismatch", f"{label} is required")
    return value


__all__ = [
    "ADVERSE_STATES",
    "COUNT_POLICIES",
    "JOIN_DECISION_SCHEMA",
    "JOIN_POLICIES",
    "JOIN_POLICY_SCHEMA",
    "TERMINAL_CONTRIBUTION_SCHEMA",
    "TERMINAL_STATES",
    "JoinDecisionError",
    "build_terminal_contribution",
    "evaluate_join_decision",
    "file_sha256",
    "normalize_incoming_edges",
    "normalize_join_policy",
    "validate_join_decision",
    "validate_terminal_contribution",
    "write_immutable_json",
]
