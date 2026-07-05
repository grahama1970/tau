"""Deterministic DAG stress proof for Tau orchestration semantics."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DAG_STRESS_SUITE_SCHEMA = "tau.dag_stress_suite_receipt.v1"
DAG_STRESS_RUNG_SCHEMA = "tau.dag_stress_rung_receipt.v1"
DAG_STRESS_SPEC_SCHEMA = "tau.dag_stress_spec.v1"
DAG_STRESS_NODE_RECEIPT_SCHEMA = "tau.dag_stress_node_receipt.v1"
DAG_STRESS_CAMPAIGN_SCHEMA = "tau.dag_stress_campaign_receipt.v1"


def run_dag_stress_poc(
    *,
    run_root: Path,
    label: str = "tau-dag-stress-poc",
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Run increasingly complex deterministic DAG stress rungs.

    This exercises Tau's orchestration semantics locally. It intentionally does
    not launch live provider CLIs; provider-pane visibility is proven by the
    separate provider DAG POC.
    """

    if max_attempts < 1:
        raise RuntimeError("max_attempts must be at least 1")
    resolved_run_root = run_root.expanduser().resolve()
    resolved_run_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{_compact_stamp()}-{_slug(label)}"
    run_dir = resolved_run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rungs = [
        _run_linear_creator_reviewer(run_dir, max_attempts=max_attempts),
        _run_creator_reviewer_retry(run_dir, max_attempts=max_attempts),
        _run_fanout_fanin(run_dir, max_attempts=max_attempts),
        _run_multi_revision_loop(run_dir, max_attempts=max_attempts),
        _run_max_attempts_fail_closed(run_dir, max_attempts=max_attempts),
        _run_subagent_timeout(run_dir, max_attempts=max_attempts),
        _run_subagent_error(run_dir, max_attempts=max_attempts),
        _run_invalid_receipt(run_dir, max_attempts=max_attempts),
        _run_wrong_result_after_max_iterations(run_dir, max_attempts=max_attempts),
        _run_model_unavailable(run_dir, max_attempts=max_attempts),
    ]
    unexpected = [rung for rung in rungs if rung["status"] != rung["expected_status"]]
    suite_receipt = {
        "schema": DAG_STRESS_SUITE_SCHEMA,
        "ok": not unexpected,
        "status": "PASS" if not unexpected else "FAIL",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "execution": "local_deterministic_tau_scheduler",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "rung_count": len(rungs),
        "passed_rungs": len([rung for rung in rungs if rung["status"] == "PASS"]),
        "expected_blocked_rungs": len(
            [rung for rung in rungs if rung["expected_status"] == "BLOCKED"]
        ),
        "unexpected_rungs": [rung["rung_id"] for rung in unexpected],
        "rungs": rungs,
        "proof_scope": {
            "proves": [
                "Tau can execute a one-pass creator to reviewer dependency",
                "Tau can run a bounded creator/reviewer revision loop",
                "Tau can wait for fan-out creator receipts before fan-in review",
                "Tau can carry reviewer feedback into the next creator attempt",
                "Tau fails closed when reviewer revisions exhaust max_attempts",
                "Tau classifies subagent timeout as a blocked rung",
                "Tau classifies subagent execution error as a blocked rung",
                "Tau classifies invalid subagent receipts as a blocked rung",
                "Tau classifies repeated wrong results as max-iteration exhaustion",
                "Tau classifies missing provider model as a blocked rung",
            ],
            "does_not_prove": [
                "Live Codex or OpenCode semantic task completion",
                "Herdr pane visibility for these deterministic stress rungs",
                "remote Tailscale monitoring",
                "GitHub ticket closure",
                "production repository mutation",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "suite-receipt.json", suite_receipt)
    return suite_receipt


def inspect_dag_stress_run(run_dir: Path) -> dict[str, Any]:
    """Return a compact summary for a deterministic DAG stress run."""

    resolved = run_dir.expanduser().resolve()
    receipt = _read_json_object(resolved / "suite-receipt.json", label="suite receipt")
    rungs = receipt.get("rungs") if isinstance(receipt.get("rungs"), list) else []
    return {
        "schema": "tau.dag_stress_inspect.v1",
        "ok": receipt.get("ok") is True,
        "status": receipt.get("status"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "provider_live": receipt.get("provider_live"),
        "execution": receipt.get("execution"),
        "run_id": receipt.get("run_id"),
        "run_dir": str(resolved),
        "rung_count": receipt.get("rung_count"),
        "passed_rungs": receipt.get("passed_rungs"),
        "expected_blocked_rungs": receipt.get("expected_blocked_rungs"),
        "unexpected_rungs": receipt.get("unexpected_rungs"),
        "rungs": [
            {
                "rung_id": rung.get("rung_id"),
                "status": rung.get("status"),
                "expected_status": rung.get("expected_status"),
                "attempt_count": rung.get("attempt_count"),
                "event_count": rung.get("event_count"),
                "invariants": rung.get("invariants"),
                "rung_dir": rung.get("rung_dir"),
            }
            for rung in rungs
            if isinstance(rung, dict)
        ],
        "proof_scope": receipt.get("proof_scope"),
    }


def run_dag_stress_campaign(
    *,
    run_root: Path,
    label: str = "tau-dag-stress-campaign",
    max_budget: int = 5,
    repetitions: int = 3,
) -> dict[str, Any]:
    """Run a repeated stress matrix across retry budgets."""

    if max_budget < 1:
        raise RuntimeError("max_budget must be at least 1")
    if repetitions < 1:
        raise RuntimeError("repetitions must be at least 1")
    resolved_run_root = run_root.expanduser().resolve()
    resolved_run_root.mkdir(parents=True, exist_ok=True)
    campaign_id = f"{_compact_stamp()}-{_slug(label)}"
    campaign_dir = resolved_run_root / campaign_id
    suites_root = campaign_dir / "suites"
    suites_root.mkdir(parents=True, exist_ok=True)

    suite_records: list[dict[str, Any]] = []
    verdict_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for repetition in range(1, repetitions + 1):
        for budget in range(1, max_budget + 1):
            suite = run_dag_stress_poc(
                run_root=suites_root,
                label=f"{label}-budget-{budget}-rep-{repetition}",
                max_attempts=budget,
            )
            for rung in suite["rungs"]:
                verdict = str(rung.get("verdict") or "UNKNOWN")
                status = str(rung.get("status") or "UNKNOWN")
                verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
                status_counts[status] = status_counts.get(status, 0) + 1
            suite_records.append(
                {
                    "budget": budget,
                    "repetition": repetition,
                    "ok": suite["ok"],
                    "status": suite["status"],
                    "run_id": suite["run_id"],
                    "run_dir": suite["run_dir"],
                    "rung_count": suite["rung_count"],
                    "passed_rungs": suite["passed_rungs"],
                    "expected_blocked_rungs": suite["expected_blocked_rungs"],
                    "unexpected_rungs": suite["unexpected_rungs"],
                }
            )

    failed_suites = [suite for suite in suite_records if suite["ok"] is not True]
    total_rungs = sum(int(suite["rung_count"]) for suite in suite_records)
    campaign_receipt = {
        "schema": DAG_STRESS_CAMPAIGN_SCHEMA,
        "ok": not failed_suites,
        "status": "PASS" if not failed_suites else "FAIL",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "execution": "local_deterministic_tau_scheduler_campaign",
        "campaign_id": campaign_id,
        "campaign_dir": str(campaign_dir),
        "suites_root": str(suites_root),
        "max_budget": max_budget,
        "repetitions": repetitions,
        "suite_count": len(suite_records),
        "total_rungs": total_rungs,
        "failed_suite_count": len(failed_suites),
        "failed_suites": failed_suites,
        "status_counts": status_counts,
        "verdict_counts": verdict_counts,
        "suites": suite_records,
        "grading_dimensions": [
            "dependency_ordering",
            "retry_budget_accounting",
            "receipt_presence",
            "receipt_schema_rejection",
            "timeout_classification",
            "subagent_error_classification",
            "wrong_result_max_iteration_classification",
            "model_unavailable_classification",
        ],
        "proof_scope": {
            "proves": [
                "Tau deterministic DAG stress suite remains stable across repeated budgets",
                "Tau retry-budget expected statuses are consistent across repeated runs",
                "Tau fail-closed classifications are emitted as durable rung receipts",
            ],
            "does_not_prove": [
                "Live Codex or OpenCode provider behavior",
                "Herdr-visible panes for the campaign rungs",
                "remote Tailscale monitoring",
                "GitHub ticket closure",
                "production repository mutation",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(campaign_dir / "campaign-receipt.json", campaign_receipt)
    return campaign_receipt


def inspect_dag_stress_campaign(run_dir: Path) -> dict[str, Any]:
    """Return a compact summary for a stress campaign."""

    resolved = run_dir.expanduser().resolve()
    receipt = _read_json_object(resolved / "campaign-receipt.json", label="campaign receipt")
    return {
        "schema": "tau.dag_stress_campaign_inspect.v1",
        "ok": receipt.get("ok") is True,
        "status": receipt.get("status"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "provider_live": receipt.get("provider_live"),
        "execution": receipt.get("execution"),
        "campaign_id": receipt.get("campaign_id"),
        "campaign_dir": str(resolved),
        "max_budget": receipt.get("max_budget"),
        "repetitions": receipt.get("repetitions"),
        "suite_count": receipt.get("suite_count"),
        "total_rungs": receipt.get("total_rungs"),
        "failed_suite_count": receipt.get("failed_suite_count"),
        "status_counts": receipt.get("status_counts"),
        "verdict_counts": receipt.get("verdict_counts"),
        "grading_dimensions": receipt.get("grading_dimensions"),
        "proof_scope": receipt.get("proof_scope"),
    }


def _run_linear_creator_reviewer(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "01-linear-creator-reviewer", max_attempts, "PASS")
    artifact = rung["rung_dir"] / "scratch" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("creator output: accepted\n", encoding="utf-8")
    _creator_attempt(rung, "creator", 1, artifact, "PASS", "initial implementation")
    _reviewer_attempt(rung, "reviewer", 1, ["creator"], "PASS", "artifact contains accepted")
    return _finish_rung(rung)


def _run_creator_reviewer_retry(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    expected_status = "PASS" if max_attempts >= 2 else "BLOCKED"
    rung = _new_rung(suite_dir, "02-creator-reviewer-retry", max_attempts, expected_status)
    artifact = rung["rung_dir"] / "scratch" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("creator output: draft\n", encoding="utf-8")
    _creator_attempt(rung, "creator", 1, artifact, "PASS", "draft implementation")
    _reviewer_attempt(rung, "reviewer", 1, ["creator"], "REVISE", "missing accepted marker")
    if max_attempts < 2:
        return _finish_rung(rung, status="BLOCKED", verdict="MAX_ATTEMPTS_EXHAUSTED")
    _append_event(
        rung["events_path"],
        "reviewer_requested_revision",
        {"attempt": 1, "feedback": "missing accepted marker"},
    )
    artifact.write_text("creator output: accepted after revision\n", encoding="utf-8")
    _creator_attempt(rung, "creator", 2, artifact, "PASS", "applied reviewer feedback")
    _reviewer_attempt(rung, "reviewer", 2, ["creator"], "PASS", "accepted marker present")
    return _finish_rung(rung)


def _run_fanout_fanin(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "03-fanout-fanin-review", max_attempts, "PASS")
    scratch = rung["rung_dir"] / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    root = scratch / "root-plan.txt"
    root.write_text("root creator plan\n", encoding="utf-8")
    _creator_attempt(rung, "root-creator", 1, root, "PASS", "root plan")
    fanout_nodes = ["creator-a", "creator-b", "creator-c"]
    for node_id in fanout_nodes:
        artifact = scratch / f"{node_id}.txt"
        artifact.write_text(f"{node_id} output based on root plan\n", encoding="utf-8")
        _creator_attempt(rung, node_id, 1, artifact, "PASS", f"{node_id} output")
    _reviewer_attempt(
        rung,
        "merge-reviewer",
        1,
        ["root-creator", *fanout_nodes],
        "PASS",
        "all fan-out creator receipts present",
    )
    return _finish_rung(rung)


def _run_multi_revision_loop(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    expected_status = "PASS" if max_attempts >= 3 else "BLOCKED"
    rung = _new_rung(suite_dir, "04-multi-revision-loop", max_attempts, expected_status)
    artifact = rung["rung_dir"] / "scratch" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    revision_feedback = [
        ("draft: missing tests and summary\n", "REVISE", "missing tests and summary"),
        ("draft: tests added, summary missing\n", "REVISE", "summary missing"),
        ("accepted: tests added; summary added\n", "PASS", "all reviewer criteria met"),
    ]
    final_status = "BLOCKED"
    final_verdict = "MAX_ATTEMPTS_EXHAUSTED"
    for attempt, (content, reviewer_verdict, feedback) in enumerate(revision_feedback, start=1):
        if attempt > max_attempts:
            break
        artifact.write_text(content, encoding="utf-8")
        _creator_attempt(rung, "creator", attempt, artifact, "PASS", f"attempt {attempt}")
        _reviewer_attempt(rung, "reviewer", attempt, ["creator"], reviewer_verdict, feedback)
        if reviewer_verdict == "PASS":
            final_status = "PASS"
            final_verdict = "PASS"
            break
        _append_event(
            rung["events_path"],
            "reviewer_requested_revision",
            {"attempt": attempt, "feedback": feedback},
        )
    return _finish_rung(rung, status=final_status, verdict=final_verdict)


def _run_max_attempts_fail_closed(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "05-max-attempts-fail-closed", max_attempts, "BLOCKED")
    artifact = rung["rung_dir"] / "scratch" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, max_attempts + 1):
        artifact.write_text(f"attempt {attempt}: still missing required marker\n", encoding="utf-8")
        _creator_attempt(rung, "creator", attempt, artifact, "PASS", f"attempt {attempt}")
        _reviewer_attempt(rung, "reviewer", attempt, ["creator"], "REVISE", "marker still missing")
        if attempt < max_attempts:
            _append_event(
                rung["events_path"],
                "reviewer_requested_revision",
                {"attempt": attempt, "feedback": "marker still missing"},
            )
    return _finish_rung(rung, status="BLOCKED", verdict="MAX_ATTEMPTS_EXHAUSTED")


def _run_subagent_timeout(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "06-subagent-timeout", max_attempts, "BLOCKED")
    work_order_path = rung["rung_dir"] / "work-orders" / "creator-attempt-01.json"
    receipt_path = rung["rung_dir"] / "receipts" / "creator-attempt-01.json"
    _write_json(
        work_order_path,
        {
            "schema": "tau.dag_stress_work_order.v1",
            "node_id": "creator",
            "role": "creator",
            "attempt": 1,
            "timeout_seconds": 1,
            "receipt_path": str(receipt_path),
        },
    )
    _append_event(
        rung["events_path"],
        "creator_dispatch",
        {"node_id": "creator", "attempt": 1, "work_order_path": str(work_order_path)},
    )
    _append_event(
        rung["events_path"],
        "subagent_timeout",
        {"node_id": "creator", "attempt": 1, "timeout_seconds": 1},
    )
    return _finish_rung(rung, status="BLOCKED", verdict="SUBAGENT_TIMEOUT")


def _run_subagent_error(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "07-subagent-error", max_attempts, "BLOCKED")
    work_order_path = rung["rung_dir"] / "work-orders" / "creator-attempt-01.json"
    receipt_path = rung["rung_dir"] / "receipts" / "creator-attempt-01.json"
    _write_json(
        work_order_path,
        {
            "schema": "tau.dag_stress_work_order.v1",
            "node_id": "creator",
            "role": "creator",
            "attempt": 1,
            "receipt_path": str(receipt_path),
        },
    )
    _append_event(
        rung["events_path"],
        "creator_dispatch",
        {"node_id": "creator", "attempt": 1, "work_order_path": str(work_order_path)},
    )
    _append_event(
        rung["events_path"],
        "subagent_error",
        {"node_id": "creator", "attempt": 1, "exit_code": 2, "stderr": "simulated worker error"},
    )
    return _finish_rung(rung, status="BLOCKED", verdict="SUBAGENT_ERROR")


def _run_invalid_receipt(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "08-invalid-receipt", max_attempts, "BLOCKED")
    work_order_path = rung["rung_dir"] / "work-orders" / "creator-attempt-01.json"
    receipt_path = rung["rung_dir"] / "receipts" / "creator-attempt-01.json"
    _write_json(
        work_order_path,
        {
            "schema": "tau.dag_stress_work_order.v1",
            "node_id": "creator",
            "role": "creator",
            "attempt": 1,
            "receipt_path": str(receipt_path),
        },
    )
    _append_event(
        rung["events_path"],
        "creator_dispatch",
        {"node_id": "creator", "attempt": 1, "work_order_path": str(work_order_path)},
    )
    _write_json(
        receipt_path,
        {
            "schema": "wrong.schema.v1",
            "node_id": "creator",
            "attempt": 1,
            "verdict": "PASS",
        },
    )
    _append_event(
        rung["events_path"],
        "receipt_validation_failed",
        {
            "node_id": "creator",
            "attempt": 1,
            "receipt_path": str(receipt_path),
            "reason": "schema_mismatch",
        },
    )
    _remember_attempt(rung, "creator", 1, "creator", "INVALID_RECEIPT", receipt_path)
    return _finish_rung(rung, status="BLOCKED", verdict="INVALID_RECEIPT")


def _run_wrong_result_after_max_iterations(
    suite_dir: Path, *, max_attempts: int
) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "09-wrong-result-after-max-iterations", max_attempts, "BLOCKED")
    artifact = rung["rung_dir"] / "scratch" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, max_attempts + 1):
        artifact.write_text(f"attempt {attempt}: plausible but wrong result\n", encoding="utf-8")
        _creator_attempt(rung, "creator", attempt, artifact, "PASS", "creator claimed success")
        _reviewer_attempt(rung, "reviewer", attempt, ["creator"], "REVISE", "wrong result")
        _append_event(
            rung["events_path"],
            "wrong_result_observed",
            {"attempt": attempt, "reason": "artifact_missing_required_answer"},
        )
        if attempt < max_attempts:
            _append_event(
                rung["events_path"],
                "reviewer_requested_revision",
                {"attempt": attempt, "feedback": "wrong result"},
            )
    return _finish_rung(rung, status="BLOCKED", verdict="WRONG_RESULT_MAX_ITERATIONS")


def _run_model_unavailable(suite_dir: Path, *, max_attempts: int) -> dict[str, Any]:
    rung = _new_rung(suite_dir, "10-model-unavailable", max_attempts, "BLOCKED")
    work_order_path = rung["rung_dir"] / "work-orders" / "creator-attempt-01.json"
    _write_json(
        work_order_path,
        {
            "schema": "tau.dag_stress_work_order.v1",
            "node_id": "creator",
            "role": "creator",
            "attempt": 1,
            "provider_id": "codex",
            "model": "gpt-model-that-no-longer-exists",
        },
    )
    _append_event(
        rung["events_path"],
        "provider_resolution_failed",
        {
            "node_id": "creator",
            "attempt": 1,
            "provider_id": "codex",
            "model": "gpt-model-that-no-longer-exists",
            "reason": "model_not_found",
        },
    )
    return _finish_rung(rung, status="BLOCKED", verdict="MODEL_UNAVAILABLE")


def _new_rung(
    suite_dir: Path,
    rung_id: str,
    max_attempts: int,
    expected_status: str,
) -> dict[str, Any]:
    rung_dir = suite_dir / "rungs" / rung_id
    for child in ("work-orders", "receipts", "scratch"):
        (rung_dir / child).mkdir(parents=True, exist_ok=True)
    events_path = rung_dir / "events.jsonl"
    spec = {
        "schema": DAG_STRESS_SPEC_SCHEMA,
        "rung_id": rung_id,
        "max_attempts": max_attempts,
        "expected_status": expected_status,
        "events_jsonl": str(events_path),
        "work_order_dir": str(rung_dir / "work-orders"),
        "receipt_dir": str(rung_dir / "receipts"),
        "policy": {
            "orchestrator_owns_loop": True,
            "subagents_one_bounded_turn": True,
            "max_attempts": max_attempts,
        },
    }
    _write_json(rung_dir / "stress-spec.json", spec)
    _append_event(events_path, "stress_spec_created", {"rung_id": rung_id})
    return {
        "rung_id": rung_id,
        "rung_dir": rung_dir,
        "events_path": events_path,
        "spec": spec,
        "expected_status": expected_status,
        "attempts": [],
        "dispatch_index": {},
        "receipt_index": {},
    }


def _creator_attempt(
    rung: dict[str, Any],
    node_id: str,
    attempt: int,
    artifact: Path,
    verdict: str,
    summary: str,
) -> None:
    work_order_path = rung["rung_dir"] / "work-orders" / f"{node_id}-attempt-{attempt:02d}.json"
    receipt_path = rung["rung_dir"] / "receipts" / f"{node_id}-attempt-{attempt:02d}.json"
    _write_json(
        work_order_path,
        {
            "schema": "tau.dag_stress_work_order.v1",
            "node_id": node_id,
            "role": "creator",
            "attempt": attempt,
            "artifact_path": str(artifact),
            "receipt_path": str(receipt_path),
        },
    )
    _append_event(
        rung["events_path"],
        "creator_dispatch",
        {"node_id": node_id, "attempt": attempt, "work_order_path": str(work_order_path)},
    )
    _write_node_receipt(receipt_path, node_id, "creator", attempt, verdict, summary, [artifact])
    _append_event(
        rung["events_path"],
        "creator_receipt_validated",
        {"node_id": node_id, "attempt": attempt, "receipt_path": str(receipt_path)},
    )
    _remember_attempt(rung, node_id, attempt, "creator", verdict, receipt_path)


def _reviewer_attempt(
    rung: dict[str, Any],
    node_id: str,
    attempt: int,
    depends_on: list[str],
    verdict: str,
    summary: str,
) -> None:
    work_order_path = rung["rung_dir"] / "work-orders" / f"{node_id}-attempt-{attempt:02d}.json"
    receipt_path = rung["rung_dir"] / "receipts" / f"{node_id}-attempt-{attempt:02d}.json"
    _write_json(
        work_order_path,
        {
            "schema": "tau.dag_stress_work_order.v1",
            "node_id": node_id,
            "role": "reviewer",
            "attempt": attempt,
            "depends_on": depends_on,
            "receipt_path": str(receipt_path),
        },
    )
    _append_event(
        rung["events_path"],
        "reviewer_dispatch",
        {
            "node_id": node_id,
            "attempt": attempt,
            "depends_on": depends_on,
            "work_order_path": str(work_order_path),
        },
    )
    _write_node_receipt(receipt_path, node_id, "reviewer", attempt, verdict, summary, [])
    _append_event(
        rung["events_path"],
        "reviewer_receipt_validated",
        {"node_id": node_id, "attempt": attempt, "receipt_path": str(receipt_path)},
    )
    _remember_attempt(rung, node_id, attempt, "reviewer", verdict, receipt_path)


def _finish_rung(
    rung: dict[str, Any],
    *,
    status: str = "PASS",
    verdict: str = "PASS",
) -> dict[str, Any]:
    events = _read_events(rung["events_path"])
    invariants = _rung_invariants(events, rung)
    if not all(invariants.values()):
        status = "FAIL"
        verdict = "INVARIANT_FAILED"
    receipt = {
        "schema": DAG_STRESS_RUNG_SCHEMA,
        "ok": status == rung["expected_status"],
        "status": status,
        "expected_status": rung["expected_status"],
        "verdict": verdict,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "rung_id": rung["rung_id"],
        "rung_dir": str(rung["rung_dir"]),
        "stress_spec": str(rung["rung_dir"] / "stress-spec.json"),
        "events_jsonl": str(rung["events_path"]),
        "event_count": len(events),
        "attempt_count": len(
            {
                attempt["attempt"]
                for attempt in rung["attempts"]
                if attempt["role"] == "reviewer"
            }
        ),
        "attempts": rung["attempts"],
        "invariants": invariants,
        "timestamp": _utc_stamp(),
    }
    _write_json(rung["rung_dir"] / "rung-receipt.json", receipt)
    return receipt


def _rung_invariants(events: list[dict[str, Any]], rung: dict[str, Any]) -> dict[str, bool]:
    order = {
        (event["kind"], event.get("node_id"), event.get("attempt")): index
        for index, event in enumerate(events)
    }
    reviewer_after_creators = True
    for event in events:
        if event.get("kind") != "reviewer_dispatch":
            continue
        reviewer_index = events.index(event)
        for dependency in event.get("depends_on", []):
            dependency_key = ("creator_receipt_validated", dependency, event.get("attempt"))
            fallback_key = ("creator_receipt_validated", dependency, 1)
            dependency_index = order.get(dependency_key, order.get(fallback_key))
            if dependency_index is None or dependency_index > reviewer_index:
                reviewer_after_creators = False
    revision_after_review = True
    for revision_index, event in enumerate(events):
        if event.get("kind") != "reviewer_requested_revision":
            continue
        attempt = event.get("attempt")
        matching_review_before_revision = any(
            candidate.get("kind") == "reviewer_receipt_validated"
            and candidate.get("role") in {None, "reviewer"}
            and candidate.get("attempt") == attempt
            and candidate_index < revision_index
            for candidate_index, candidate in enumerate(events)
        )
        if not matching_review_before_revision:
            revision_after_review = False
    return {
        "stress_spec_exists": (rung["rung_dir"] / "stress-spec.json").exists(),
        "all_node_receipts_exist": all(
            Path(str(attempt["receipt_path"])).exists() for attempt in rung["attempts"]
        ),
        "reviewer_dispatch_after_creator_receipts": reviewer_after_creators,
        "revision_request_after_reviewer_receipt": revision_after_review,
        "max_attempts_respected": all(
            int(attempt["attempt"]) <= int(rung["spec"]["max_attempts"])
            for attempt in rung["attempts"]
        ),
    }


def _write_node_receipt(
    path: Path,
    node_id: str,
    role: str,
    attempt: int,
    verdict: str,
    summary: str,
    artifacts: list[Path],
) -> None:
    _write_json(
        path,
        {
            "schema": DAG_STRESS_NODE_RECEIPT_SCHEMA,
            "node_id": node_id,
            "role": role,
            "attempt": attempt,
            "status": "PASS" if verdict in {"PASS", "REVISE"} else "BLOCKED",
            "verdict": verdict,
            "artifacts": [str(artifact) for artifact in artifacts],
            "handoff_summary": summary,
            "timestamp": _utc_stamp(),
        },
    )


def _remember_attempt(
    rung: dict[str, Any],
    node_id: str,
    attempt: int,
    role: str,
    verdict: str,
    receipt_path: Path,
) -> None:
    rung["attempts"].append(
        {
            "node_id": node_id,
            "role": role,
            "attempt": attempt,
            "verdict": verdict,
            "receipt_path": str(receipt_path),
        }
    )


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _append_event(path: Path, kind: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "tau.dag_stress_event.v1",
        "kind": kind,
        "timestamp": _utc_stamp(),
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid {label} JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _compact_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return "-".join(part for part in slug.split("-") if part) or "run"
