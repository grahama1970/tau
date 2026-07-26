from tau_coding.advisory_escalation import (
    COMPETITION_ADVISORY_RECEIPT_SCHEMA,
    ROUNDTABLE_ADVISORY_RECEIPT_SCHEMA,
    run_competition_advisory,
    run_roundtable_advisory,
)
from tau_coding.course_correction import build_course_correction_receipt


def test_roundtable_uses_identical_context_preserves_dissent_and_caps_rounds() -> None:
    captured: dict[str, list[dict[str, object]]] = {"a": [], "b": []}

    def seat_a(payload: dict[str, object]) -> dict[str, object]:
        captured["a"].append(payload)
        return {"position": "repair-scheduler", "rationale": "same evidence"}

    def seat_b(payload: dict[str, object]) -> dict[str, object]:
        captured["b"].append(payload)
        return {
            "position": "repair-scheduler",
            "rationale": "same evidence",
            "dissent": True,
            "dissent_summary": "Need human release after panel.",
        }

    receipt = run_roundtable_advisory(
        trigger_receipt=_roundtable_trigger(),
        shared_context={"goal_hash": "sha256:goal", "failure_report": "not met"},
        research_brief={"searches": ["memory", "brave-search"]},
        seats={"a": seat_a, "b": seat_b},
    )

    assert receipt["schema"] == ROUNDTABLE_ADVISORY_RECEIPT_SCHEMA
    assert receipt["round_count"] == 3
    assert receipt["round_cap_reached"] is True
    assert receipt["converged"] is False
    assert receipt["does_not_satisfy_goal"] is True
    assert receipt["goal_met"] is False
    assert receipt["surviving_dissent"][0]["seat"] == "b"
    assert "The immutable goal is met." in receipt["proof_scope"]["does_not_prove"]
    for round_index in range(3):
        assert captured["a"][round_index] == captured["b"][round_index]
        response_hashes = {
            response["dispatch_payload_sha256"]
            for response in receipt["rounds"][round_index]["responses"]
        }
        assert len(response_hashes) == 1


def test_roundtable_stops_on_convergence_before_round_cap() -> None:
    calls = 0

    def seat(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"position": "same-plan", "rationale": str(payload["round"])}

    receipt = run_roundtable_advisory(
        trigger_receipt=_roundtable_trigger(),
        shared_context={"goal_hash": "sha256:goal"},
        seats={"a": seat, "b": seat},
    )

    assert receipt["round_count"] == 1
    assert receipt["converged"] is True
    assert receipt["round_cap_reached"] is False
    assert calls == 2


def test_competition_advisory_judges_candidates_without_satisfying_goal() -> None:
    seen_by_judge: dict[str, object] = {}

    def competitor_a(payload: dict[str, object]) -> dict[str, object]:
        return {"approach": "state-first", "payload_roundtrip": payload["schema"]}

    def competitor_b(payload: dict[str, object]) -> dict[str, object]:
        return {"approach": "minimal-patch", "payload_roundtrip": payload["schema"]}

    def judge(payload: dict[str, object]) -> dict[str, object]:
        seen_by_judge.update(payload)
        return {"winner": "a", "rationale": "lower blast radius"}

    receipt = run_competition_advisory(
        trigger_receipt=_competition_trigger(),
        shared_context={"goal_hash": "sha256:goal", "failure_report": "wide space"},
        competitors={"a": competitor_a, "b": competitor_b},
        judge=judge,
    )

    assert receipt["schema"] == COMPETITION_ADVISORY_RECEIPT_SCHEMA
    assert receipt["candidate_count"] == 2
    assert len(seen_by_judge["candidates"]) == 2
    assert receipt["judgment"]["winner"] == "a"
    assert receipt["does_not_satisfy_goal"] is True
    assert receipt["goal_met"] is False
    assert receipt["human_release_required"] is True


def _roundtable_trigger() -> dict[str, object]:
    return build_course_correction_receipt(
        trigger="goal_not_met_after_failure_report",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="goal-guardian",
        agent="goal-guardian",
        observed_state={
            "failure_report_path": "/tmp/failure-report.json",
            "search_ladder_exhausted": True,
        },
    )


def _competition_trigger() -> dict[str, object]:
    return build_course_correction_receipt(
        trigger="wide_solution_space_after_failure_report",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="goal-guardian",
        agent="goal-guardian",
        observed_state={
            "failure_report_path": "/tmp/failure-report.json",
            "search_ladder_exhausted": True,
            "wide_solution_space": True,
        },
    )
