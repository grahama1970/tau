"""Deterministic and adversarial tests for typed DAG route decisions."""

import copy
import json

import pytest

from tau_coding.dag_route_decision import (
    ROUTE_CONDITION_SCHEMA,
    RouteDecisionError,
    build_route_contract,
    evaluate_route_condition,
    evaluate_route_decision,
    normalize_route_condition,
    sha256_json,
    validate_route_decision_receipt,
    write_route_decision_receipt,
)


def _condition(op: str, field: str = "status", value: object = "PASS") -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": ROUTE_CONDITION_SCHEMA,
        "op": op,
        "field": field,
    }
    if op != "exists":
        payload["value"] = value
    return payload


@pytest.mark.parametrize(
    ("condition", "result", "expected"),
    [
        (_condition("eq"), {"status": "PASS"}, True),
        (_condition("neq"), {"status": "BLOCKED"}, True),
        (_condition("in", value=["PASS", "ACCEPTED"]), {"status": "PASS"}, True),
        (_condition("not_in", value=["BLOCKED"]), {"status": "PASS"}, True),
        (_condition("exists", field="approval_code"), {"approval_code": None}, True),
        (_condition("exists", field="approval_code"), {}, False),
        (_condition("eq", value=True), {"status": True}, True),
        (_condition("eq", value=2), {"status": 2}, True),
        (_condition("eq", value=["a", "b"]), {"status": ["a", "b"]}, True),
        (_condition("eq", value=["a", "b"]), {"status": ["b", "a"]}, False),
    ],
)
def test_route_condition_uses_strict_typed_values(
    condition: dict[str, object], result: dict[str, object], expected: bool
) -> None:
    normalized = normalize_route_condition(condition)
    assert evaluate_route_condition(normalized, result) is expected


def test_route_condition_evaluates_compound_nodes_without_hiding_errors() -> None:
    condition = normalize_route_condition(
        {
            "schema": ROUTE_CONDITION_SCHEMA,
            "op": "all",
            "conditions": [
                _condition("eq"),
                {
                    "schema": ROUTE_CONDITION_SCHEMA,
                    "op": "not",
                    "condition": _condition("exists", field="blocker"),
                },
            ],
        }
    )
    assert evaluate_route_condition(condition, {"status": "PASS"}) is True


@pytest.mark.parametrize("op", ["any", "all"])
def test_compound_route_conditions_short_circuit_unreachable_errors(op: str) -> None:
    first = _condition("eq", value="PASS" if op == "any" else "BLOCKED")
    condition = normalize_route_condition(
        {
            "schema": ROUTE_CONDITION_SCHEMA,
            "op": op,
            "conditions": [first, _condition("eq", field="missing")],
        }
    )

    assert evaluate_route_condition(condition, {"status": "PASS"}) is (op == "any")


@pytest.mark.parametrize(
    ("condition", "code"),
    [
        (_condition("eq", field="result.status"), "invalid_route_condition"),
        (_condition("eq", field="evidence[0]"), "invalid_route_condition"),
        (_condition("eq", field="summary"), "invalid_route_condition"),
        (_condition("in", value=[]), "invalid_route_condition"),
        (_condition("in", value=[1, "1"]), "invalid_route_condition"),
        (
            {"schema": ROUTE_CONDITION_SCHEMA, "op": "python", "expr": "True"},
            "invalid_route_condition",
        ),
        (
            {
                "schema": ROUTE_CONDITION_SCHEMA,
                "op": "eq",
                "field": "status",
                "value": "{{x}}",
                "expr": "x",
            },
            "invalid_route_condition",
        ),
        ("status == 'PASS'", "unsupported_ready_queue_condition"),
    ],
)
def test_route_condition_rejects_expression_and_untyped_forms(
    condition: object, code: str
) -> None:
    with pytest.raises(RouteDecisionError) as exc_info:
        normalize_route_condition(condition)
    assert exc_info.value.code == code


def test_missing_field_blocks_negative_operator() -> None:
    with pytest.raises(RouteDecisionError) as exc_info:
        evaluate_route_condition(normalize_route_condition(_condition("neq")), {})
    assert exc_info.value.code == "route_field_missing"


def _route_contract(mode: str, outcomes: list[str]) -> dict[str, object]:
    return build_route_contract(
        source_node_id="reviewer",
        mode=mode,
        edges=[
            {
                "edge_index": index,
                "target": f"{target.lower()}-{index}",
                "condition": _condition("eq", value=target),
            }
            for index, target in enumerate(outcomes)
        ],
    )


def _validate_receipt(
    receipt: dict[str, object],
    source_result: dict[str, object],
    **binding_overrides: object,
) -> list[str]:
    bindings = {
        "expected_dag_id": "dag-1",
        "expected_goal_hash": "sha256:goal",
        "expected_source_node_id": "reviewer",
        "expected_attempt": 1,
    }
    bindings.update(binding_overrides)
    return validate_route_decision_receipt(
        receipt,
        source_result=source_result,
        **bindings,
    )


@pytest.mark.parametrize(
    ("mode", "outcomes", "status", "selected", "failure"),
    [
        ("exclusive", ["PASS", "REVISE"], "PASS", ["pass-0"], None),
        ("exclusive", ["PASS", "PASS"], "BLOCKED", [], "route_ambiguous_exclusive"),
        ("exclusive", ["REVISE"], "BLOCKED", [], "route_no_match"),
        ("first_match", ["PASS", "PASS"], "PASS", ["pass-0"], None),
        ("fanout", ["PASS", "REVISE", "PASS"], "PASS", ["pass-0", "pass-2"], None),
        ("all_matching", ["PASS", "PASS"], "PASS", ["pass-0", "pass-1"], None),
        (
            "all_matching",
            ["PASS", "REVISE"],
            "BLOCKED",
            [],
            "route_all_matching_incomplete",
        ),
    ],
)
def test_route_modes_have_closed_deterministic_semantics(
    mode: str,
    outcomes: list[str],
    status: str,
    selected: list[str],
    failure: str | None,
) -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS", "summary": "untrusted prose"},
        route_contract=_route_contract(mode, outcomes),
    )
    assert decision["status"] == status
    assert decision["selected_targets"] == selected
    assert decision["failure_code"] == failure
    if mode == "first_match":
        assert decision["evaluations"][1]["evaluated"] is False


def test_route_decision_preserves_first_edge_failure_code() -> None:
    route_contract = build_route_contract(
        source_node_id="reviewer",
        mode="fanout",
        edges=[
            {
                "edge_index": 0,
                "target": "missing",
                "condition": _condition("eq", field="status"),
            },
            {
                "edge_index": 1,
                "target": "invalid",
                "condition": _condition("eq", field="bad"),
            },
        ],
    )

    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"bad": 1},
        route_contract=route_contract,
    )

    assert decision["failure_code"] == "route_field_missing"
    assert [item["error_code"] for item in decision["evaluations"]] == [
        "route_field_missing",
        "route_comparison_type_mismatch",
    ]


def test_route_decision_is_replayable_and_tamper_evident() -> None:
    inputs = {
        "dag_id": "dag-1",
        "goal_hash": "sha256:goal",
        "source_node_id": "reviewer",
        "attempt": 1,
        "source_result": {"status": "PASS", "summary": "not a route field"},
        "route_contract": _route_contract("exclusive", ["PASS", "REVISE"]),
    }
    first = evaluate_route_decision(**inputs)
    second = evaluate_route_decision(**copy.deepcopy(inputs))

    assert first == second
    assert first["decision_sha256"] == second["decision_sha256"]
    assert "timestamp" not in first
    assert _validate_receipt(first, inputs["source_result"]) == []

    tampered = copy.deepcopy(first)
    tampered["selected_targets"] = ["revise"]
    assert _validate_receipt(tampered, inputs["source_result"]) == [
        "route_decision_hash_mismatch",
        "route_decision_replay_mismatch",
    ]

    assert _validate_receipt(first, {"status": "REVISE"}) == [
        "route_decision_replay_mismatch",
        "route_source_result_hash_mismatch",
    ]

    overclaimed = copy.deepcopy(first)
    overclaimed["proof_scope"]["proves"].append("The model is truthful.")
    assert _validate_receipt(overclaimed, inputs["source_result"]) == [
        "route_decision_proof_scope_mismatch",
        "route_decision_hash_mismatch",
        "route_decision_replay_mismatch",
    ]

    wrong_schema = copy.deepcopy(first)
    wrong_schema["schema"] = "tau.future_route_decision.v9"
    assert _validate_receipt(wrong_schema, inputs["source_result"]) == [
        "route_decision_schema_mismatch",
        "route_decision_hash_mismatch",
        "route_decision_replay_mismatch",
    ]


def test_route_decision_detaches_embedded_contract_from_caller_mutation() -> None:
    route_contract = _route_contract("exclusive", ["PASS", "REVISE"])
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=route_contract,
    )
    route_contract["edges"][0]["target"] = "tampered"  # type: ignore[index]

    assert decision["route_contract"]["edges"][0]["target"] == "pass-0"
    assert _validate_receipt(decision, {"status": "PASS"}) == []


def test_route_decision_snapshots_mutable_source_values() -> None:
    status = ["PASS"]
    source_result = {"status": status}
    route_contract = build_route_contract(
        source_node_id="reviewer",
        mode="exclusive",
        edges=[
            {
                "edge_index": 0,
                "target": "accept",
                "condition": _condition("eq", value=["PASS"]),
            }
        ],
    )

    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result=source_result,
        route_contract=route_contract,
    )
    status.append("MUTATED")

    assert decision["source_fields"][0]["value"] == ["PASS"]
    assert _validate_receipt(decision, {"status": ["PASS"]}) == []


@pytest.mark.parametrize("edge_index", [True, 1.5, "1", -1])
def test_route_contract_rejects_coercive_edge_indexes(edge_index: object) -> None:
    with pytest.raises(RouteDecisionError) as exc_info:
        build_route_contract(
            source_node_id="router",
            mode="exclusive",
            edges=[
                {
                    "edge_index": edge_index,
                    "target": "accept",
                    "condition": _condition("eq"),
                }
            ],
        )
    assert exc_info.value.code == "invalid_route_condition"


def test_route_contract_rejects_duplicate_targets_and_indexes() -> None:
    for second_edge in (
        {"edge_index": 0, "target": "revise", "condition": _condition("eq")},
        {"edge_index": 1, "target": "accept", "condition": _condition("eq")},
    ):
        with pytest.raises(RouteDecisionError) as exc_info:
            build_route_contract(
                source_node_id="router",
                mode="fanout",
                edges=[
                    {"edge_index": 0, "target": "accept", "condition": _condition("eq")},
                    second_edge,
                ],
            )
        assert exc_info.value.code == "invalid_route_condition"


def test_first_match_uses_canonical_edge_index_order() -> None:
    route_contract = build_route_contract(
        source_node_id="reviewer",
        mode="first_match",
        edges=[
            {"edge_index": 1, "target": "later", "condition": _condition("eq")},
            {"edge_index": 0, "target": "first", "condition": _condition("eq")},
        ],
    )

    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=route_contract,
    )

    assert [edge["target"] for edge in route_contract["edges"]] == ["first", "later"]
    assert decision["selected_targets"] == ["first"]


def test_route_condition_rejects_oversized_strings() -> None:
    with pytest.raises(RouteDecisionError) as exc_info:
        normalize_route_condition(_condition("eq", value="x" * 4097))
    assert exc_info.value.code == "invalid_route_condition"


def test_route_condition_rejects_excessive_depth_and_total_objects() -> None:
    too_deep: dict[str, object] = _condition("eq")
    for _ in range(8):
        too_deep = {
            "schema": ROUTE_CONDITION_SCHEMA,
            "op": "not",
            "condition": too_deep,
        }
    with pytest.raises(RouteDecisionError) as depth_error:
        normalize_route_condition(too_deep)
    assert depth_error.value.code == "invalid_route_condition"

    too_many = {
        "schema": ROUTE_CONDITION_SCHEMA,
        "op": "all",
        "conditions": [_condition("eq") for _ in range(64)],
    }
    with pytest.raises(RouteDecisionError) as count_error:
        normalize_route_condition(too_many)
    assert count_error.value.code == "invalid_route_condition"


def test_route_receipt_validation_fails_closed_on_noncanonical_projection() -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    decision["source_fields"].append(copy.deepcopy(decision["source_fields"][0]))
    decision["source_fields"][0]["value"] = float("nan")

    errors = _validate_receipt(decision, {"status": "PASS"})

    assert errors == ["route_decision_hash_mismatch"]


def test_route_receipt_replay_rejects_rehashed_metadata_tampering() -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    decision["mode"] = "fanout"
    decision["decision_sha256"] = sha256_json(
        {key: value for key, value in decision.items() if key != "decision_sha256"}
    )

    assert _validate_receipt(decision, {"status": "PASS"}) == [
        "route_decision_replay_mismatch"
    ]


def test_route_receipt_rejects_unknown_unhashed_fields() -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    decision["authoritative_override"] = "accept"

    assert _validate_receipt(decision, {"status": "PASS"}) == [
        "route_decision_fields_mismatch"
    ]


def test_route_receipt_replay_uses_trusted_source_not_forged_projection() -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    decision["source_fields"][0]["value"] = "REVISE"
    decision["source_fields_sha256"] = sha256_json(decision["source_fields"])
    decision["evaluations"][0].update({"matched": False})
    decision["evaluations"][1].update({"matched": True})
    decision["selected_targets"] = ["revise-1"]
    decision["decision_sha256"] = sha256_json(
        {key: item for key, item in decision.items() if key != "decision_sha256"}
    )

    assert _validate_receipt(decision, {"status": "PASS"}) == [
        "route_decision_replay_mismatch"
    ]


def test_route_receipt_write_is_atomic_and_allows_only_receipt_path(tmp_path) -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    receipt_path = tmp_path / "route.json"

    write_route_decision_receipt(receipt_path, decision)
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert persisted["receipt_path"] == str(receipt_path)
    assert _validate_receipt(
        persisted,
        {"status": "PASS"},
        expected_receipt_path=receipt_path,
    ) == []
    persisted["receipt_path"] = str(tmp_path / "forged.json")
    assert _validate_receipt(
        persisted,
        {"status": "PASS"},
        expected_receipt_path=receipt_path,
    ) == ["route_receipt_path_mismatch"]
    assert list(tmp_path.glob(".route.json.*.tmp")) == []


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("dag_id", "other-dag", "route_dag_id_mismatch"),
        ("goal_hash", "sha256:other", "route_goal_hash_mismatch"),
        ("source_node_id", "other-node", "route_source_node_mismatch"),
        ("attempt", 2, "route_attempt_mismatch"),
        ("attempt", True, "route_attempt_mismatch"),
    ],
)
def test_route_receipt_validation_binds_external_run_context(
    field: str,
    value: object,
    error_code: str,
) -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    decision[field] = value
    decision["decision_sha256"] = sha256_json(
        {key: item for key, item in decision.items() if key != "decision_sha256"}
    )

    errors = _validate_receipt(decision, {"status": "PASS"})

    assert error_code in errors
    assert "route_decision_hash_mismatch" not in errors


def test_route_receipt_replay_normalizes_before_traversing_conditions() -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    decision["route_contract"]["edges"][0]["condition"] = {
        "schema": ROUTE_CONDITION_SCHEMA,
        "op": "all",
    }

    errors = _validate_receipt(decision, {"status": "PASS"})

    assert "route_contract_hash_mismatch" in errors
    assert "route_decision_replay_mismatch" in errors


def test_route_evaluator_rejects_unknown_mode_and_source_mismatch() -> None:
    route_contract = _route_contract("exclusive", ["PASS", "REVISE"])
    route_contract["mode"] = "arbitrary"
    with pytest.raises(RouteDecisionError) as mode_error:
        evaluate_route_decision(
            dag_id="dag-1",
            goal_hash="sha256:goal",
            source_node_id="reviewer",
            attempt=1,
            source_result={"status": "PASS"},
            route_contract=route_contract,
        )
    assert mode_error.value.code == "invalid_route_mode"

    route_contract = _route_contract("exclusive", ["PASS", "REVISE"])
    with pytest.raises(RouteDecisionError) as binding_error:
        evaluate_route_decision(
            dag_id="dag-1",
            goal_hash="sha256:goal",
            source_node_id="different-source",
            attempt=1,
            source_result={"status": "PASS"},
            route_contract=route_contract,
        )
    assert binding_error.value.code == "route_source_binding_mismatch"


@pytest.mark.parametrize(
    "overrides",
    [
        {"dag_id": ""},
        {"goal_hash": ""},
        {"source_node_id": ""},
        {"attempt": True},
        {"attempt": 0},
    ],
)
def test_route_evaluator_rejects_invalid_decision_bindings(
    overrides: dict[str, object],
) -> None:
    inputs: dict[str, object] = {
        "dag_id": "dag-1",
        "goal_hash": "sha256:goal",
        "source_node_id": "reviewer",
        "attempt": 1,
        "source_result": {"status": "PASS"},
        "route_contract": _route_contract("exclusive", ["PASS", "REVISE"]),
    }
    inputs.update(overrides)

    with pytest.raises(RouteDecisionError) as exc_info:
        evaluate_route_decision(**inputs)  # type: ignore[arg-type]

    assert exc_info.value.code == "route_source_binding_mismatch"


def test_route_evaluator_rejects_invalid_exists_value_and_nonfinite_result() -> None:
    exists = normalize_route_condition(_condition("exists", field="approval"))
    with pytest.raises(RouteDecisionError) as exists_error:
        evaluate_route_condition(exists, {"approval": {"untrusted": True}})
    assert exists_error.value.code == "route_field_type_invalid"

    with pytest.raises(RouteDecisionError) as source_error:
        evaluate_route_decision(
            dag_id="dag-1",
            goal_hash="sha256:goal",
            source_node_id="reviewer",
            attempt=1,
            source_result={"status": "PASS", "unreferenced": float("nan")},
            route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
        )
    assert source_error.value.code == "route_source_result_invalid"


def test_route_evaluator_bounds_unreferenced_source_result_data() -> None:
    with pytest.raises(RouteDecisionError) as source_error:
        evaluate_route_decision(
            dag_id="dag-1",
            goal_hash="sha256:goal",
            source_node_id="reviewer",
            attempt=1,
            source_result={"status": "PASS", "unrelated": [0] * 1025},
            route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
        )

    assert source_error.value.code == "route_source_result_invalid"


def test_route_receipt_validation_bounds_untrusted_receipt_and_source_data() -> None:
    decision = evaluate_route_decision(
        dag_id="dag-1",
        goal_hash="sha256:goal",
        source_node_id="reviewer",
        attempt=1,
        source_result={"status": "PASS"},
        route_contract=_route_contract("exclusive", ["PASS", "REVISE"]),
    )
    deeply_nested: object = "value"
    for _ in range(33):
        deeply_nested = {"nested": deeply_nested}
    oversized_receipt = copy.deepcopy(decision)
    oversized_receipt["source_fields"] = deeply_nested

    assert _validate_receipt(oversized_receipt, {"status": "PASS"}) == [
        "route_decision_hash_mismatch"
    ]
    assert _validate_receipt(decision, {"status": "PASS", "extra": [0] * 1025}) == [
        "route_decision_replay_mismatch",
        "route_source_result_hash_mismatch",
    ]
