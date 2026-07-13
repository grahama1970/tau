"""Deterministic tests for terminal contributions and DAG join policies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.dag_join_decision import (
    JOIN_DECISION_SCHEMA,
    JoinDecisionError,
    build_terminal_contribution,
    evaluate_join_decision,
    normalize_join_policy,
    validate_join_decision,
    validate_terminal_contribution,
    write_immutable_json,
)

DAG_ID = "join-policy-test"
GOAL_HASH = "sha256:join-policy-goal"
JOIN_NODE = "join"


def _edges(count: int = 3) -> list[dict[str, object]]:
    return [
        {
            "edge_index": index,
            "source_node_id": f"branch-{index}",
            "target_node_id": JOIN_NODE,
            "condition": None,
        }
        for index in range(count)
    ]


def _policy(name: str, **parameters: object) -> dict[str, object]:
    return {
        "schema": "tau.dag_join_policy.v1",
        "policy": name,
        "timeout_seconds": 30,
        **parameters,
    }


def _contributions(
    states: list[str], policy: dict[str, object]
) -> list[dict[str, object]]:
    edges = _edges(len(states))
    return [
        build_terminal_contribution(
            dag_id=DAG_ID,
            goal_hash=GOAL_HASH,
            join_node_id=JOIN_NODE,
            edge_contract=edge,
            state=state,
            reason_code=f"fixture_{state}",
            basis={"kind": "source_terminal", "basis_sha256": f"sha256:basis-{index}"},
            join_policy=policy,
            incoming_count=len(edges),
            source_binding={"source_node_id": edge["source_node_id"], "attempt": 1},
        )
        for index, (edge, state) in enumerate(zip(edges, states, strict=True))
    ]


@pytest.mark.parametrize(
    ("policy", "states", "decision"),
    [
        (_policy("all_success"), ["success", "success", "success"], "release"),
        (_policy("all_success"), ["success", "failed", "success"], "block"),
        (_policy("all_terminal"), ["success", "blocked", "skipped"], "release"),
        (
            _policy("exact_success_count", required_successes=2),
            ["success", "success", "failed"],
            "release",
        ),
        (
            _policy("exact_success_count", required_successes=2),
            ["success", "success", "success"],
            "block",
        ),
        (
            _policy("minimum_success_count", required_successes=2),
            ["success", "success", "blocked"],
            "release",
        ),
        (
            _policy("minimum_success_count", required_successes=2),
            ["success", "blocked", "failed"],
            "block",
        ),
        (
            _policy("quorum", quorum_fraction={"numerator": 2, "denominator": 3}),
            ["success", "success", "skipped"],
            "release",
        ),
        (
            _policy("quorum", quorum_fraction={"numerator": 2, "denominator": 3}),
            ["success", "blocked", "failed"],
            "block",
        ),
        (_policy("any_success"), ["failed", "success", "blocked"], "release"),
        (_policy("any_success"), ["failed", "blocked", "skipped"], "block"),
        (_policy("fail_fast"), ["success", "skipped", "success"], "release"),
        (_policy("fail_fast"), ["success", "blocked", "success"], "block"),
        (_policy("collect_failures"), ["success", "failed", "blocked"], "release"),
        (_policy("collect_failures"), ["success", "success", "success"], "skip"),
    ],
)
def test_join_policy_positive_and_negative_fixtures(
    policy: dict[str, object], states: list[str], decision: str
) -> None:
    receipt = evaluate_join_decision(
        dag_id=DAG_ID,
        goal_hash=GOAL_HASH,
        join_node_id=JOIN_NODE,
        join_policy=policy,
        incoming_edges=_edges(),
        contributions=_contributions(states, policy),
    )

    assert receipt["schema"] == JOIN_DECISION_SCHEMA
    assert receipt["decision"] == decision
    assert receipt["status"] == ("BLOCKED" if decision == "block" else "PASS")
    assert receipt["counts"]["pending"] == 0
    assert receipt["decision_sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    "name",
    [
        "all_success",
        "all_terminal",
        "exact_success_count",
        "minimum_success_count",
        "quorum",
        "any_success",
        "fail_fast",
        "collect_failures",
    ],
)
def test_all_skipped_is_a_universal_skip(name: str) -> None:
    parameters: dict[str, object] = {}
    if name in {"exact_success_count", "minimum_success_count"}:
        parameters["required_successes"] = 2
    if name == "quorum":
        parameters["quorum_fraction"] = {"numerator": 2, "denominator": 3}
    policy = _policy(name, **parameters)

    receipt = evaluate_join_decision(
        dag_id=DAG_ID,
        goal_hash=GOAL_HASH,
        join_node_id=JOIN_NODE,
        join_policy=policy,
        incoming_edges=_edges(),
        contributions=_contributions(["skipped", "skipped", "skipped"], policy),
    )

    assert receipt["decision"] == "skip"
    assert receipt["reason_code"] == "join_all_inputs_skipped"


def test_early_release_returns_intent_until_pending_edges_are_cancelled() -> None:
    policy = _policy("minimum_success_count", required_successes=2)
    contributions = _contributions(["success", "success", "blocked"], policy)[:2]

    intent = evaluate_join_decision(
        dag_id=DAG_ID,
        goal_hash=GOAL_HASH,
        join_node_id=JOIN_NODE,
        join_policy=policy,
        incoming_edges=_edges(),
        contributions=contributions,
    )

    assert intent["status"] == "TERMINAL_INTENT"
    assert intent["decision"] == "release"
    assert intent["pending_edge_indexes"] == [2]


def test_partial_all_terminal_waits_without_authoritative_hash() -> None:
    policy = _policy("all_terminal")
    partial = _contributions(["success", "blocked", "skipped"], policy)[:2]

    result = evaluate_join_decision(
        dag_id=DAG_ID,
        goal_hash=GOAL_HASH,
        join_node_id=JOIN_NODE,
        join_policy=policy,
        incoming_edges=_edges(),
        contributions=partial,
    )

    assert result["status"] == "WAIT"
    assert result["decision"] == "wait"
    assert "decision_sha256" not in result


def test_quorum_uses_declared_edge_count_including_skips() -> None:
    policy = _policy("quorum", quorum_fraction={"numerator": 2, "denominator": 3})
    receipt = evaluate_join_decision(
        dag_id=DAG_ID,
        goal_hash=GOAL_HASH,
        join_node_id=JOIN_NODE,
        join_policy=policy,
        incoming_edges=_edges(),
        contributions=_contributions(["success", "skipped", "failed"], policy),
    )

    assert receipt["computed_success_threshold"] == 2
    assert receipt["decision"] == "block"


@pytest.mark.parametrize(
    ("policy", "code"),
    [
        (_policy("quorum"), "join_quorum_fraction_missing"),
        (
            _policy("quorum", quorum_fraction={"numerator": 2, "denominator": 4}),
            "join_quorum_fraction_invalid",
        ),
        (
            _policy("all_success", required_successes=1),
            "join_required_successes_unexpected",
        ),
        (
            _policy("minimum_success_count", required_successes=4),
            "join_required_successes_out_of_range",
        ),
    ],
)
def test_join_policy_rejects_ambiguous_or_out_of_range_parameters(
    policy: dict[str, object], code: str
) -> None:
    with pytest.raises(JoinDecisionError) as caught:
        normalize_join_policy(policy, incoming_count=3)
    assert caught.value.code == code


def test_contribution_and_join_replay_are_hash_bound() -> None:
    policy = _policy("all_success")
    contributions = _contributions(["success", "success", "success"], policy)
    receipt = evaluate_join_decision(
        dag_id=DAG_ID,
        goal_hash=GOAL_HASH,
        join_node_id=JOIN_NODE,
        join_policy=policy,
        incoming_edges=_edges(),
        contributions=contributions,
    )

    replay = validate_join_decision(
        receipt,
        dag_id=DAG_ID,
        goal_hash=GOAL_HASH,
        join_node_id=JOIN_NODE,
        join_policy=policy,
        incoming_edges=_edges(),
        contributions=contributions,
    )

    assert replay == receipt
    assert json.dumps(replay, sort_keys=True) == json.dumps(receipt, sort_keys=True)


def test_tampered_contribution_is_rejected() -> None:
    policy = _policy("all_success")
    contribution = _contributions(["success", "success", "success"], policy)[0]
    contribution["state"] = "failed"

    with pytest.raises(JoinDecisionError) as caught:
        validate_terminal_contribution(
            contribution,
            dag_id=DAG_ID,
            goal_hash=GOAL_HASH,
            join_node_id=JOIN_NODE,
            edge_contract=_edges()[0],
            join_policy=policy,
            incoming_count=3,
        )
    assert caught.value.code == "terminal_contribution_hash_mismatch"


def test_immutable_writer_accepts_identical_replay_and_blocks_conflict(tmp_path: Path) -> None:
    path = tmp_path / "contribution.json"
    write_immutable_json(path, {"state": "skipped"}, conflict_code="contribution_conflict")
    write_immutable_json(path, {"state": "skipped"}, conflict_code="contribution_conflict")

    with pytest.raises(JoinDecisionError) as caught:
        write_immutable_json(path, {"state": "success"}, conflict_code="contribution_conflict")
    assert caught.value.code == "contribution_conflict"
