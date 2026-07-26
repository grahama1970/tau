"""Deterministic advisory roundtable and competition receipts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256

ROUNDTABLE_ADVISORY_RECEIPT_SCHEMA = "tau.roundtable_advisory_receipt.v1"
COMPETITION_ADVISORY_RECEIPT_SCHEMA = "tau.competition_advisory_receipt.v1"

SeatFn = Callable[[dict[str, Any]], dict[str, Any]]
JudgeFn = Callable[[dict[str, Any]], dict[str, Any]]


def run_roundtable_advisory(
    *,
    trigger_receipt: Mapping[str, Any],
    shared_context: Mapping[str, Any],
    seats: Mapping[str, SeatFn],
    research_brief: Mapping[str, Any] | None = None,
    max_rounds: int = 3,
) -> dict[str, Any]:
    """Run a bounded, same-context advisory roundtable with deterministic seats."""

    if max_rounds < 1 or max_rounds > 3:
        raise ValueError("max_rounds must be between 1 and 3")
    if not seats:
        raise ValueError("at least one roundtable seat is required")
    _require_trigger(trigger_receipt, trigger="goal_not_met_after_failure_report")
    rounds: list[dict[str, Any]] = []
    prior_positions: list[dict[str, Any]] = []
    converged = False
    for round_number in range(1, max_rounds + 1):
        dispatch_payload = {
            "schema": "tau.roundtable_dispatch_payload.v1",
            "round": round_number,
            "trigger_receipt": dict(trigger_receipt),
            "shared_context": dict(shared_context),
            "research_brief": dict(research_brief or {}),
            "prior_positions": list(prior_positions),
            "advisory_only": True,
            "does_not_satisfy_goal": True,
        }
        dispatch_hash = canonical_sha256(dispatch_payload)
        responses: list[dict[str, Any]] = []
        for seat_name, seat in seats.items():
            response = dict(seat(dict(dispatch_payload)))
            responses.append(
                {
                    "seat": seat_name,
                    "dispatch_payload_sha256": dispatch_hash,
                    "position": response.get("position"),
                    "rationale": response.get("rationale"),
                    "dissent": bool(response.get("dissent", False)),
                    "dissent_summary": response.get("dissent_summary"),
                }
            )
        prior_positions.extend(responses)
        positions = {
            str(response.get("position"))
            for response in responses
            if response.get("position") is not None
        }
        converged = len(positions) == 1 and not any(
            response.get("dissent") is True for response in responses
        )
        rounds.append(
            {
                "round": round_number,
                "dispatch_payload_sha256": dispatch_hash,
                "seat_count": len(seats),
                "responses": responses,
                "converged": converged,
            }
        )
        if converged:
            break
    dissent = [
        response
        for round_item in rounds
        for response in round_item["responses"]
        if response.get("dissent") is True
    ]
    return {
        "schema": ROUNDTABLE_ADVISORY_RECEIPT_SCHEMA,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "advisory_only": True,
        "does_not_satisfy_goal": True,
        "goal_met": False,
        "trigger": trigger_receipt.get("trigger"),
        "required_next_action": trigger_receipt.get("required_next_action"),
        "max_rounds": max_rounds,
        "round_count": len(rounds),
        "converged": converged,
        "round_cap_reached": len(rounds) == max_rounds and not converged,
        "rounds": rounds,
        "surviving_dissent": dissent,
        "synthesis": {
            "position": rounds[-1]["responses"][0].get("position") if rounds else None,
            "dissent_count": len(dissent),
            "human_release_required": True,
        },
        "proof_scope": {
            "proves": [
                "Every seat in a round received an identical dispatch payload hash.",
                "Roundtable iteration stopped at convergence or the three-round cap.",
                "Surviving dissent was preserved as advisory evidence.",
            ],
            "does_not_prove": [
                "The immutable goal is met.",
                "Panel consensus is deterministic proof.",
                "Human release has occurred.",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def run_competition_advisory(
    *,
    trigger_receipt: Mapping[str, Any],
    shared_context: Mapping[str, Any],
    competitors: Mapping[str, SeatFn],
    judge: JudgeFn,
) -> dict[str, Any]:
    """Run deterministic independent candidates and advisory judging."""

    if not competitors:
        raise ValueError("at least one competitor is required")
    _require_trigger(trigger_receipt, trigger="wide_solution_space_after_failure_report")
    candidate_payload = {
        "schema": "tau.competition_candidate_payload.v1",
        "trigger_receipt": dict(trigger_receipt),
        "shared_context": dict(shared_context),
        "advisory_only": True,
        "does_not_satisfy_goal": True,
    }
    candidates = [
        {
            "competitor": name,
            "payload_sha256": canonical_sha256(candidate_payload),
            "candidate": dict(competitor(dict(candidate_payload))),
        }
        for name, competitor in competitors.items()
    ]
    judgment_payload = {
        "schema": "tau.competition_judgment_payload.v1",
        "trigger_receipt": dict(trigger_receipt),
        "shared_context": dict(shared_context),
        "candidates": candidates,
        "advisory_only": True,
        "does_not_satisfy_goal": True,
    }
    judgment = dict(judge(dict(judgment_payload)))
    return {
        "schema": COMPETITION_ADVISORY_RECEIPT_SCHEMA,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "advisory_only": True,
        "does_not_satisfy_goal": True,
        "goal_met": False,
        "trigger": trigger_receipt.get("trigger"),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "judgment_payload_sha256": canonical_sha256(judgment_payload),
        "judgment": judgment,
        "human_release_required": True,
        "proof_scope": {
            "proves": [
                "Independent candidates were generated from a shared advisory payload.",
                "The judge received the complete candidate set.",
            ],
            "does_not_prove": [
                "The immutable goal is met.",
                "The winning candidate is correct.",
                "Human release has occurred.",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def _require_trigger(trigger_receipt: Mapping[str, Any], *, trigger: str) -> None:
    if trigger_receipt.get("trigger") != trigger:
        raise ValueError(f"trigger_receipt.trigger must be {trigger}")
    if trigger_receipt.get("status") != "REQUIRED":
        raise ValueError("trigger_receipt.status must be REQUIRED")
    observed = trigger_receipt.get("observed_state")
    if not isinstance(observed, Mapping) or not observed.get("failure_report_path"):
        raise ValueError("trigger_receipt must cite failure_report_path")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
