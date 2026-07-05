import json
from pathlib import Path

from tau_coding.dag_stress_poc import (
    inspect_dag_stress_campaign,
    inspect_dag_stress_run,
    run_dag_stress_campaign,
    run_dag_stress_poc,
)


def test_dag_stress_poc_runs_increasingly_complex_rungs(tmp_path: Path) -> None:
    receipt = run_dag_stress_poc(run_root=tmp_path, label="stress", max_attempts=3)

    assert receipt["schema"] == "tau.dag_stress_suite_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    assert receipt["provider_live"] is False
    assert receipt["execution"] == "local_deterministic_tau_scheduler"
    assert receipt["rung_count"] == 10
    assert receipt["passed_rungs"] == 4
    assert receipt["expected_blocked_rungs"] == 6
    assert receipt["unexpected_rungs"] == []

    by_id = {rung["rung_id"]: rung for rung in receipt["rungs"]}
    assert by_id["01-linear-creator-reviewer"]["attempt_count"] == 1
    assert by_id["02-creator-reviewer-retry"]["attempt_count"] == 2
    assert by_id["03-fanout-fanin-review"]["attempt_count"] == 1
    assert by_id["04-multi-revision-loop"]["attempt_count"] == 3
    assert by_id["05-max-attempts-fail-closed"]["attempt_count"] == 3
    assert by_id["05-max-attempts-fail-closed"]["status"] == "BLOCKED"
    assert by_id["06-subagent-timeout"]["verdict"] == "SUBAGENT_TIMEOUT"
    assert by_id["07-subagent-error"]["verdict"] == "SUBAGENT_ERROR"
    assert by_id["08-invalid-receipt"]["verdict"] == "INVALID_RECEIPT"
    assert by_id["09-wrong-result-after-max-iterations"]["verdict"] == (
        "WRONG_RESULT_MAX_ITERATIONS"
    )
    assert by_id["10-model-unavailable"]["verdict"] == "MODEL_UNAVAILABLE"
    for rung in receipt["rungs"]:
        assert all(rung["invariants"].values())
        assert Path(rung["events_jsonl"]).exists()
        assert Path(rung["stress_spec"]).exists()


def test_creator_reviewer_retry_orders_reviewer_after_creator(tmp_path: Path) -> None:
    receipt = run_dag_stress_poc(run_root=tmp_path, label="stress", max_attempts=3)
    retry_rung = next(
        rung for rung in receipt["rungs"] if rung["rung_id"] == "02-creator-reviewer-retry"
    )
    events = [
        json.loads(line)
        for line in Path(retry_rung["events_jsonl"]).read_text(encoding="utf-8").splitlines()
    ]
    event_keys = [(event["kind"], event.get("attempt")) for event in events]

    assert event_keys.index(("creator_receipt_validated", 1)) < event_keys.index(
        ("reviewer_dispatch", 1)
    )
    assert event_keys.index(("reviewer_receipt_validated", 1)) < event_keys.index(
        ("reviewer_requested_revision", 1)
    )
    assert event_keys.index(("reviewer_requested_revision", 1)) < event_keys.index(
        ("creator_dispatch", 2)
    )


def test_dag_stress_inspect_summarizes_suite(tmp_path: Path) -> None:
    receipt = run_dag_stress_poc(run_root=tmp_path, label="stress", max_attempts=3)
    summary = inspect_dag_stress_run(Path(receipt["run_dir"]))

    assert summary["schema"] == "tau.dag_stress_inspect.v1"
    assert summary["ok"] is True
    assert summary["rung_count"] == 10
    assert summary["passed_rungs"] == 4
    assert summary["expected_blocked_rungs"] == 6
    assert len(summary["rungs"]) == 10
    assert summary["rungs"][-1]["expected_status"] == "BLOCKED"


def test_dag_stress_expected_status_tracks_retry_budget_one(tmp_path: Path) -> None:
    receipt = run_dag_stress_poc(run_root=tmp_path, label="stress", max_attempts=1)

    assert receipt["ok"] is True
    assert receipt["unexpected_rungs"] == []
    by_id = {rung["rung_id"]: rung for rung in receipt["rungs"]}
    assert by_id["02-creator-reviewer-retry"]["status"] == "BLOCKED"
    assert by_id["02-creator-reviewer-retry"]["expected_status"] == "BLOCKED"
    assert by_id["04-multi-revision-loop"]["status"] == "BLOCKED"
    assert by_id["04-multi-revision-loop"]["expected_status"] == "BLOCKED"
    assert receipt["expected_blocked_rungs"] == 8


def test_dag_stress_expected_status_tracks_retry_budget_two(tmp_path: Path) -> None:
    receipt = run_dag_stress_poc(run_root=tmp_path, label="stress", max_attempts=2)

    assert receipt["ok"] is True
    assert receipt["unexpected_rungs"] == []
    by_id = {rung["rung_id"]: rung for rung in receipt["rungs"]}
    assert by_id["02-creator-reviewer-retry"]["status"] == "PASS"
    assert by_id["02-creator-reviewer-retry"]["expected_status"] == "PASS"
    assert by_id["04-multi-revision-loop"]["status"] == "BLOCKED"
    assert by_id["04-multi-revision-loop"]["expected_status"] == "BLOCKED"
    assert receipt["expected_blocked_rungs"] == 7


def test_dag_stress_records_advanced_fail_closed_modes(tmp_path: Path) -> None:
    receipt = run_dag_stress_poc(run_root=tmp_path, label="stress", max_attempts=3)
    by_id = {rung["rung_id"]: rung for rung in receipt["rungs"]}

    expected = {
        "06-subagent-timeout": "SUBAGENT_TIMEOUT",
        "07-subagent-error": "SUBAGENT_ERROR",
        "08-invalid-receipt": "INVALID_RECEIPT",
        "09-wrong-result-after-max-iterations": "WRONG_RESULT_MAX_ITERATIONS",
        "10-model-unavailable": "MODEL_UNAVAILABLE",
    }
    for rung_id, verdict in expected.items():
        rung = by_id[rung_id]
        assert rung["status"] == "BLOCKED"
        assert rung["expected_status"] == "BLOCKED"
        assert rung["ok"] is True
        assert rung["verdict"] == verdict


def test_dag_stress_campaign_runs_repeated_budget_matrix(tmp_path: Path) -> None:
    receipt = run_dag_stress_campaign(
        run_root=tmp_path,
        label="campaign",
        max_budget=3,
        repetitions=2,
    )

    assert receipt["schema"] == "tau.dag_stress_campaign_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    assert receipt["provider_live"] is False
    assert receipt["suite_count"] == 6
    assert receipt["total_rungs"] == 60
    assert receipt["failed_suite_count"] == 0
    assert receipt["status_counts"]["PASS"] == 18
    assert receipt["status_counts"]["BLOCKED"] == 42
    assert receipt["verdict_counts"]["MODEL_UNAVAILABLE"] == 6


def test_dag_stress_campaign_inspect_summarizes_aggregate(tmp_path: Path) -> None:
    receipt = run_dag_stress_campaign(
        run_root=tmp_path,
        label="campaign",
        max_budget=2,
        repetitions=2,
    )
    summary = inspect_dag_stress_campaign(Path(receipt["campaign_dir"]))

    assert summary["schema"] == "tau.dag_stress_campaign_inspect.v1"
    assert summary["ok"] is True
    assert summary["suite_count"] == 4
    assert summary["total_rungs"] == 40
    assert summary["failed_suite_count"] == 0
    assert "timeout_classification" in summary["grading_dimensions"]
