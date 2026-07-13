from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.transition import AllSuccessTransitionPolicy, DagPolicyReplayState
from tau_coding.generic_dag import run_generic_dag


class InjectedCrash(RuntimeError):
    pass


def test_sqlite_store_uses_wal_is_append_only_and_passes_integrity_check(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, ["producer"])
    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        check = store.integrity_check()

        assert check == {
            "ok": True,
            "integrity_check": ["ok"],
            "foreign_key_check": [],
            "journal_mode": "wal",
        }
        with sqlite3.connect(database) as connection, pytest.raises(
            sqlite3.IntegrityError, match="append-only"
        ):
            connection.execute("UPDATE dag_run_events SET event_type = 'tampered'")
        store.release_lease(lease)


def test_store_fences_live_and_expired_leases(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        old = store.acquire_run(
            plan=plan,
            run_id="run-1",
            owner_id="owner-a",
            ttl_seconds=0.1,
        )
        with pytest.raises(DagRunStoreError, match="dag_run_lease_held"):
            store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-b")
        time.sleep(0.12)
        with pytest.raises(DagRunStoreError, match="dag_run_lease_takeover_required"):
            store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-b")
        current = store.acquire_run(
            plan=plan,
            run_id="run-1",
            owner_id="owner-b",
            allow_takeover=True,
        )

        assert current.epoch == old.epoch + 1
        with pytest.raises(DagRunStoreError, match="dag_run_lease_lost"):
            store.reserve_attempt(
                old,
                plan_sha256=plan.plan_sha256,
                node_id="producer",
                attempt=1,
            )


def test_store_reuses_unfinished_generation_and_advances_finished_run(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        assert store.execution_run_id("run-1") == "run-1"
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        assert store.execution_run_id("run-1") == "run-1"
        store.mark_run_finished(lease, status="PASS", verdict="PASS")
        store.release_lease(lease)
        assert store.execution_run_id("run-1") == "run-1:generation:1"


def test_store_persists_scheduler_concurrency_high_water_mark(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        store.record_observed_concurrency(lease, 1)
        store.record_observed_concurrency(lease, 3)
        store.record_observed_concurrency(lease, 2)
        assert store.max_observed_concurrency("run-1") == 3
        events = [
            event
            for event in store.load_events("run-1")
            if event["event_type"] == "scheduler_concurrency_observed"
        ]
        assert [event["payload"]["concurrency"] for event in events] == [1, 3]


def test_store_deduplicates_identical_result_and_blocks_conflicting_result(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, ["producer"])
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        identity = store.reserve_attempt(
            lease,
            plan_sha256=plan.plan_sha256,
            node_id="producer",
            attempt=1,
        )
        store.mark_dispatched(lease, identity.attempt_id)
        result = _pass_result("producer")

        assert store.stage_result(lease, identity.attempt_id, result) == result
        assert store.stage_result(lease, identity.attempt_id, result) == result
        with pytest.raises(DagRunStoreError, match="dag_attempt_result_conflict"):
            store.stage_result(
                lease,
                identity.attempt_id,
                {**result, "accepted_output": {"value": "different"}},
            )
        staged = [
            event
            for event in store.load_events("run-1")
            if event["event_type"] == "attempt_result_staged"
        ]
        assert len(staged) == 1


@pytest.mark.parametrize("projection", ["staged", "committed"])
def test_store_blocks_tampered_output_projection(tmp_path: Path, projection: str) -> None:
    plan = _plan(tmp_path, ["producer"])
    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        identity = store.reserve_attempt(
            lease,
            plan_sha256=plan.plan_sha256,
            node_id="producer",
            attempt=1,
        )
        store.mark_dispatched(lease, identity.attempt_id)
        result = _pass_result("producer")
        store.stage_result(lease, identity.attempt_id, result)
        store.validate_result(
            lease,
            identity.attempt_id,
            {
                "schema": "tau.dag_attempt_validation.v1",
                "status": "PASS",
                "node_id": "producer",
                "result_sha256": canonical_sha256(result),
            },
        )
        if projection == "committed":
            store.commit_output(lease, identity.attempt_id)
    column = f"{projection}_json"
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"UPDATE dag_attempt_outputs SET {column} = ? WHERE attempt_id = ?",
            (json.dumps({**result, "verdict": "TAMPERED"}), identity.attempt_id),
        )
    with SqliteDagRunStore(database) as store, pytest.raises(
        DagRunStoreError, match="dag_attempt_output_hash_mismatch"
    ):
        store.list_attempts("run-1")


def test_scheduler_replays_committed_nodes_without_rerunning_adapters(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer", "consumer"])
    calls: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        calls.append(node.node_id)
        return _pass_result(node.node_id)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        first = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
        )
    with SqliteDagRunStore(database) as store:
        second = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
        )
        check = store.integrity_check()

    assert first.status == second.status == "PASS"
    assert calls == ["producer", "consumer"]
    assert second.replayed_event_count > 0
    assert check["ok"] is True


def test_scheduler_replay_preserves_blocked_run_verdict(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])

    def blocked(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs, attempt
        return {
            "node_id": node.node_id,
            "status": "BLOCKED",
            "verdict": "REVIEW_REQUIRED",
            "errors": ["human review required"],
        }

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        first = run_dag_plan(
            plan,
            execute_node=blocked,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
        )
    with SqliteDagRunStore(database) as store:
        second = run_dag_plan(
            plan,
            execute_node=lambda *_: pytest.fail("blocked adapter reran"),
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
        )

    assert first.status == second.status == "BLOCKED"
    assert first.verdict == second.verdict == "REVIEW_REQUIRED"


def test_scheduler_settles_malformed_adapter_result_and_replays_block(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, ["producer"])
    calls: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        calls.append(node.node_id)
        return {"node_id": node.node_id}

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        first = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
        )
    with SqliteDagRunStore(database) as store:
        second = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
        )

    assert first.status == second.status == "BLOCKED"
    assert first.verdict == second.verdict == "DAG_ATTEMPT_RESULT_INVALID"
    assert calls == ["producer"]
    assert second.replayed_event_count > 0


def test_replay_validation_failure_does_not_overwrite_terminal_outcome(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, ["producer"])
    database = tmp_path / "run.sqlite3"

    class ReplayPolicy(AllSuccessTransitionPolicy):
        def __init__(self, *, fail_restore: bool) -> None:
            self.fail_restore = fail_restore

        def restore(self, plan: DagPlan, replay: DagPolicyReplayState) -> None:
            if self.fail_restore:
                raise RuntimeError("dag_transition_receipt_missing")
            super().restore(plan, replay)

    with SqliteDagRunStore(database) as store:
        first = run_dag_plan(
            plan,
            execute_node=lambda node, *_: _pass_result(node.node_id),
            transition_policy=ReplayPolicy(fail_restore=False),
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
        )
    with SqliteDagRunStore(database) as store:
        failed_replay = run_dag_plan(
            plan,
            execute_node=lambda *_: pytest.fail("terminal adapter reran"),
            transition_policy=ReplayPolicy(fail_restore=True),
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
        )
        assert store.run_outcome("run-1") == ("PASS", "PASS")
    with SqliteDagRunStore(database) as store:
        recovered = run_dag_plan(
            plan,
            execute_node=lambda *_: pytest.fail("terminal adapter reran"),
            transition_policy=ReplayPolicy(fail_restore=False),
            run_store=store,
            run_id="run-1",
            lease_owner="owner-c",
        )

    assert first.status == recovered.status == "PASS"
    assert failed_replay.status == "BLOCKED"
    assert failed_replay.verdict == "DAG_TRANSITION_RECEIPT_MISSING"


def test_scheduler_replays_block_committed_before_run_outcome(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])

    def blocked(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs, attempt
        return {
            "node_id": node.node_id,
            "status": "BLOCKED",
            "verdict": "REVIEW_REQUIRED",
            "errors": ["human review required"],
        }

    def crash(point: str, context: Mapping[str, Any]) -> None:
        del context
        if point == "after_transition_committed":
            raise InjectedCrash(point)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCrash):
        run_dag_plan(
            plan,
            execute_node=blocked,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
            fault_injector=crash,
        )
    time.sleep(0.12)
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            execute_node=lambda *_: pytest.fail("blocked adapter reran"),
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
            allow_lease_takeover=True,
        )

    assert resumed.status == "BLOCKED"
    assert resumed.verdict == "REVIEW_REQUIRED"
    assert resumed.node_results[0]["durably_replayed"] is True


def test_scheduler_default_owner_fences_concurrent_invocation(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    started = Event()
    release = Event()
    database = tmp_path / "run.sqlite3"

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs, attempt
        started.set()
        assert release.wait(timeout=2)
        return _pass_result(node.node_id)

    def first_run():  # type: ignore[no-untyped-def]
        with SqliteDagRunStore(database) as store:
            return run_dag_plan(plan, execute_node=execute, run_store=store, run_id="run-1")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_run)
        assert started.wait(timeout=2)
        with SqliteDagRunStore(database) as store, pytest.raises(
            DagRunStoreError, match="dag_run_lease_held"
        ):
            run_dag_plan(
                plan,
                execute_node=lambda *_: pytest.fail("concurrent adapter ran"),
                run_store=store,
                run_id="run-1",
            )
        release.set()
        assert future.result(timeout=2).status == "PASS"


def test_clean_concurrent_cancellation_settles_all_dispatched_attempts(
    tmp_path: Path,
) -> None:
    plan = _parallel_plan(tmp_path, ["blocker", "worker"])

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs
        if node.node_id == "blocker":
            return {
                "node_id": node.node_id,
                "status": "BLOCKED",
                "verdict": "REVIEW_REQUIRED",
                "errors": ["review required"],
            }
        assert attempt.cancel_event.wait(timeout=2)
        return {
            "node_id": node.node_id,
            "status": "BLOCKED",
            "verdict": "CANCELLED",
            "errors": ["cancelled by scheduler"],
        }

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        first = run_dag_plan(
            plan,
            execute_node=execute,
            max_concurrency=2,
            run_store=store,
            run_id="run-1",
        )
        states = {item.identity.node_id: item.state for item in store.list_attempts("run-1")}
    with SqliteDagRunStore(database) as store:
        second = run_dag_plan(
            plan,
            execute_node=lambda *_: pytest.fail("settled adapter reran"),
            max_concurrency=2,
            run_store=store,
            run_id="run-1",
        )

    assert first.status == second.status == "BLOCKED"
    assert first.verdict == second.verdict == "REVIEW_REQUIRED"
    assert states == {"blocker": "SETTLED", "worker": "SETTLED"}


def test_parallel_cancellation_renews_lease_while_worker_stops(tmp_path: Path) -> None:
    plan = _parallel_plan(tmp_path, ["blocker", "worker"])

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs
        if node.node_id == "blocker":
            return {
                "node_id": node.node_id,
                "status": "BLOCKED",
                "verdict": "REVIEW_REQUIRED",
                "errors": ["review required"],
            }
        assert attempt.cancel_event.wait(timeout=2)
        time.sleep(0.25)
        return {
            "node_id": node.node_id,
            "status": "BLOCKED",
            "verdict": "CANCELLED",
            "errors": ["cancelled by scheduler"],
        }

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            max_concurrency=2,
            run_store=store,
            run_id="run-1",
            lease_ttl_seconds=0.1,
        )
        states = {item.identity.node_id: item.state for item in store.list_attempts("run-1")}
        renewal_events = [
            event
            for event in store.load_events("run-1")
            if event["event_type"] == "run_lease_renewed"
        ]

    assert result.status == "BLOCKED"
    assert result.verdict == "REVIEW_REQUIRED"
    assert states == {"blocker": "SETTLED", "worker": "SETTLED"}
    assert renewal_events


def test_generic_wrapper_takes_over_expired_crash_lease(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    spec_path = tmp_path / "dag.json"
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "run-1",
        "run_dir": str(tmp_path / "run"),
        "nodes": [
            {
                "node_id": "producer",
                "role": "producer",
                "command": [
                    "python",
                    "-c",
                    (
                        "import json,pathlib;"
                        f"pathlib.Path({str(receipt_path)!r}).write_text(json.dumps("
                        "{'schema':'tau.generic_dag_node_receipt.v1','node_id':'producer',"
                        "'status':'PASS','verdict':'PASS','artifacts':[],"
                        "'commands_run':[],'handoff_summary':'producer complete',"
                        "'errors':[],'policy_exceptions':[]}))"
                    ),
                ],
                "receipt_path": str(receipt_path),
            }
        ],
    }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    plan = compile_generic_dag_plan(spec, source_path=spec_path)
    database = tmp_path / "run" / "dag-run.sqlite3"
    with SqliteDagRunStore(database) as store:
        store.acquire_run(
            plan=plan,
            run_id="run-1",
            owner_id="crashed-owner",
            ttl_seconds=0.1,
        )
    time.sleep(0.12)

    receipt = run_generic_dag(spec_path=spec_path, resume=True)

    assert receipt["status"] == "PASS", receipt


def test_generic_wrapper_fences_before_writing_shared_checkpoint(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    run_dir = tmp_path / "run"
    spec_path = tmp_path / "dag.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "run-1",
                "run_dir": str(run_dir),
                "nodes": [
                    {
                        "node_id": "producer",
                        "role": "producer",
                        "command": [
                            "python",
                            "-c",
                            (
                                "import json,pathlib,time;time.sleep(1);"
                                f"pathlib.Path({str(receipt_path)!r}).write_text(json.dumps("
                                "{'schema':'tau.generic_dag_node_receipt.v1',"
                                "'node_id':'producer','status':'PASS','verdict':'PASS',"
                                "'artifacts':[],'commands_run':[],"
                                "'handoff_summary':'producer complete','errors':[],"
                                "'policy_exceptions':[]}))"
                            ),
                        ],
                        "receipt_path": str(receipt_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run_generic_dag, spec_path=spec_path, resume=True)
        checkpoint_path = run_dir / "checkpoint.json"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if checkpoint_path.exists():
                checkpoint = json.loads(checkpoint_path.read_text())
                if checkpoint.get("active_node_id") == "producer":
                    break
            time.sleep(0.01)
        else:
            pytest.fail("first generic run did not publish its active checkpoint")
        checkpoint_before = checkpoint_path.read_bytes()
        events_before = (run_dir / "events.jsonl").read_bytes()

        with pytest.raises(DagRunStoreError, match="dag_run_lease_held"):
            run_generic_dag(spec_path=spec_path, resume=True)

        assert checkpoint_path.read_bytes() == checkpoint_before
        assert (run_dir / "events.jsonl").read_bytes() == events_before
        assert first.result(timeout=3)["status"] == "PASS"


def test_scheduler_renews_lease_while_node_is_running(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs, attempt
        time.sleep(0.2)
        return _pass_result(node.node_id)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
        )
        renewal_events = [
            event
            for event in store.load_events("run-1")
            if event["event_type"] == "run_lease_renewed"
        ]

    assert result.status == "PASS"
    assert renewal_events


def test_restart_after_transition_commit_does_not_duplicate_accepted_effect(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, ["producer", "consumer"])
    calls: list[str] = []

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs, attempt
        calls.append(node.node_id)
        return _pass_result(node.node_id)

    def crash(point: str, context: Mapping[str, Any]) -> None:
        if point == "after_transition_committed" and context["node_id"] == "producer":
            raise InjectedCrash(point)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCrash):
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
            fault_injector=crash,
        )
    time.sleep(0.12)
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
            allow_lease_takeover=True,
        )

    assert resumed.status == "PASS"
    assert calls == ["producer", "consumer"]


def test_restart_commits_staged_result_without_rerunning_adapter(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    call_count = 0

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        nonlocal call_count
        del accepted_inputs, attempt
        call_count += 1
        return _pass_result(node.node_id)

    def crash(point: str, context: Mapping[str, Any]) -> None:
        del context
        if point == "after_result_staged":
            raise InjectedCrash(point)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCrash):
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
            fault_injector=crash,
        )
    time.sleep(0.12)
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
            allow_lease_takeover=True,
        )

    assert resumed.status == "PASS"
    assert call_count == 1


@pytest.mark.parametrize(
    "fault_point",
    ["after_result_validated", "after_output_committed"],
)
def test_restart_commits_validated_or_output_committed_result_without_rerun(
    tmp_path: Path,
    fault_point: str,
) -> None:
    plan = _plan(tmp_path, ["producer"])
    call_count = 0

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        nonlocal call_count
        del accepted_inputs, attempt
        call_count += 1
        return _pass_result(node.node_id)

    def crash(point: str, context: Mapping[str, Any]) -> None:
        del context
        if point == fault_point:
            raise InjectedCrash(point)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCrash):
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
            fault_injector=crash,
        )
    time.sleep(0.12)
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
            allow_lease_takeover=True,
        )

    assert resumed.status == "PASS"
    assert call_count == 1


def test_reserved_attempt_reuses_stable_identity_after_restart(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    reserved: list[dict[str, Any]] = []
    executed_attempts: list[DagNodeAttempt] = []

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs
        executed_attempts.append(attempt)
        return _pass_result(node.node_id)

    def crash(point: str, context: Mapping[str, Any]) -> None:
        if point == "after_attempt_reserved":
            reserved.append(dict(context))
            raise InjectedCrash(point)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCrash):
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
            fault_injector=crash,
        )
    time.sleep(0.12)
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
            allow_lease_takeover=True,
        )

    assert resumed.status == "PASS"
    assert len(executed_attempts) == 1
    assert executed_attempts[0].attempt_id == reserved[0]["attempt_id"]
    assert executed_attempts[0].idempotency_key == reserved[0]["idempotency_key"]
    assert executed_attempts[0].recovered is True


def test_retry_schedule_replays_once_without_duplicate_attempt_history(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    plan = replace(plan, nodes=(replace(plan.nodes[0], max_attempts=2),))
    calls: list[int] = []

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs
        calls.append(attempt.attempt)
        if attempt.attempt == 1:
            return {
                "node_id": node.node_id,
                "status": "BLOCKED",
                "verdict": "RETRYABLE_FAILURE",
                "errors": ["retry once"],
            }
        return _pass_result(node.node_id)

    def crash(point: str, context: Mapping[str, Any]) -> None:
        del context
        if point == "after_retry_scheduled":
            raise InjectedCrash(point)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCrash):
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
            fault_injector=crash,
        )
    time.sleep(0.12)
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
            allow_lease_takeover=True,
        )

    assert resumed.status == "PASS"
    assert calls == [1, 2]
    assert resumed.node_results[0]["scheduler_attempts"] == [
        {
            "attempt": 1,
            "errors": ["retry once"],
            "status": "BLOCKED",
            "verdict": "RETRYABLE_FAILURE",
        },
        {
            "attempt": 2,
            "errors": [],
            "status": "PASS",
            "verdict": "PASS",
        },
    ]


def test_restart_blocks_dispatched_attempt_with_uncertain_effect(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["producer"])
    call_count = 0

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        nonlocal call_count
        del accepted_inputs, attempt
        call_count += 1
        return _pass_result(node.node_id)

    def crash(point: str, context: Mapping[str, Any]) -> None:
        del context
        if point == "after_attempt_dispatched":
            raise InjectedCrash(point)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCrash):
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            lease_ttl_seconds=0.1,
            fault_injector=crash,
        )
    time.sleep(0.12)
    with SqliteDagRunStore(database) as store:
        resumed = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-b",
            allow_lease_takeover=True,
        )
        uncertain = [
            attempt
            for attempt in store.list_attempts("run-1")
            if attempt.state == "UNCERTAIN"
        ]
    with SqliteDagRunStore(database) as store, pytest.raises(
        DagRunStoreError, match="dag_run_reconciliation_required"
    ):
        run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-c",
        )

    assert resumed.status == "BLOCKED"
    assert resumed.verdict == "DAG_ATTEMPT_EFFECT_UNCERTAIN"
    assert call_count == 0
    assert len(uncertain) == 1


def _plan(tmp_path: Path, node_ids: list[str]) -> DagPlan:
    nodes: list[dict[str, object]] = []
    for index, node_id in enumerate(node_ids):
        dependencies = [node_ids[index - 1]] if index else []
        nodes.append(
            {
                "node_id": node_id,
                "role": node_id,
                "command": ["true"],
                "depends_on": dependencies,
                "accepted_context_from": dependencies,
                "receipt_path": str(tmp_path / f"{node_id}.json"),
                "timeout_seconds": 1,
                "max_attempts": 1,
            }
        )
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "durable-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": nodes,
        },
        source_path=tmp_path / "dag.json",
    )


def _parallel_plan(tmp_path: Path, node_ids: list[str]) -> DagPlan:
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "durable-parallel-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": node_id,
                    "role": node_id,
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / f"{node_id}.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
                for node_id in node_ids
            ],
        },
        source_path=tmp_path / "parallel-dag.json",
    )


def _pass_result(node_id: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "accepted_output": {"source_node_id": node_id},
    }
