from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, FrozenJson, canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan


def test_concurrent_writer_emits_signal_and_blocks_unreconciled_reader(
    tmp_path: Path,
) -> None:
    old_sha = _sha("old")
    new_sha = _sha("new")
    store_path = tmp_path / "run" / "dag-run.sqlite3"
    plan = _plan(tmp_path, reader_sha=old_sha, reader_policy="require_reconciliation")
    both_started = threading.Barrier(2)
    writer_returned = threading.Event()

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        both_started.wait(timeout=2)
        if node.node_id == "a-writer":
            writer_returned.set()
            return _pass(
                node.node_id,
                workspace_changes=[
                    _change(path="src/app.py", previous_sha256=old_sha, new_sha256=new_sha)
                ],
            )
        assert writer_returned.wait(timeout=2)
        return _pass(node.node_id)

    with SqliteDagRunStore(store_path) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-stale-read-unresolved",
            max_concurrency=2,
        )
        signals = store.list_workspace_change_signals(plan.plan_id)

    assert result.status == "BLOCKED"
    assert result.verdict == "STALE_WORKSPACE_READ_RECONCILIATION_REQUIRED"
    assert len(signals) == 1
    assert signals[0]["path"] == "src/app.py"
    assert signals[0]["prior_sha256"] == old_sha
    assert signals[0]["new_sha256"] == new_sha

    with SqliteDagRunStore(store_path) as reopened:
        unresolved = reopened.unresolved_workspace_change_signals(
            plan.plan_id,
            str(signals[0]["reader_attempt_id"]),
        )
    assert len(unresolved) == 1


def test_reread_reconciliation_resolves_signal_and_allows_pass(tmp_path: Path) -> None:
    old_sha = _sha("old")
    new_sha = _sha("new")
    store_path = tmp_path / "run" / "dag-run.sqlite3"
    plan = _plan(tmp_path, reader_sha=old_sha, reader_policy="require_reconciliation")
    both_started = threading.Barrier(2)

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs
        both_started.wait(timeout=2)
        if node.node_id == "a-writer":
            return _pass(
                node.node_id,
                workspace_changes=[
                    _change(path="src/app.py", previous_sha256=old_sha, new_sha256=new_sha)
                ],
            )
        signal = _wait_for_signal(store_path, plan.plan_id, attempt.attempt_id)
        return _pass(
            node.node_id,
            workspace_reads=[
                _read(path="src/app.py", blob_sha256=new_sha, observation_source="reread")
            ],
            stale_read_reconciliations=[
                {
                    "schema": "tau.stale_read_reconciliation.v1",
                    "signal_id": signal["signal_id"],
                    "disposition": "reread_bound_new_hash",
                    "observed_sha256": new_sha,
                }
            ],
        )

    with SqliteDagRunStore(store_path) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-stale-read-reread",
            max_concurrency=2,
        )
        signals = store.list_workspace_change_signals(plan.plan_id)
        unresolved = store.unresolved_workspace_change_signals(
            plan.plan_id,
            str(signals[0]["reader_attempt_id"]),
        )

    assert result.status == "PASS"
    assert len(signals) == 1
    assert unresolved == ()


def test_model_statement_reconciliation_is_not_enough(tmp_path: Path) -> None:
    old_sha = _sha("old")
    new_sha = _sha("new")
    store_path = tmp_path / "run" / "dag-run.sqlite3"
    plan = _plan(tmp_path, reader_sha=old_sha, reader_policy="require_reconciliation")
    both_started = threading.Barrier(2)

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs
        both_started.wait(timeout=2)
        if node.node_id == "a-writer":
            return _pass(
                node.node_id,
                workspace_changes=[
                    _change(path="src/app.py", previous_sha256=old_sha, new_sha256=new_sha)
                ],
            )
        signal = _wait_for_signal(store_path, plan.plan_id, attempt.attempt_id)
        evidence = {"kind": "model_statement", "claim": "irrelevant"}
        return _pass(
            node.node_id,
            stale_read_reconciliations=[
                {
                    "schema": "tau.stale_read_reconciliation.v1",
                    "signal_id": signal["signal_id"],
                    "disposition": "outside_checked_scope",
                    "evidence": evidence,
                    "evidence_sha256": canonical_sha256(evidence),
                }
            ],
        )

    with SqliteDagRunStore(store_path) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-stale-read-model-statement",
            max_concurrency=2,
        )
        signals = store.list_workspace_change_signals(plan.plan_id)
        unresolved = store.unresolved_workspace_change_signals(
            plan.plan_id,
            str(signals[0]["reader_attempt_id"]),
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "STALE_WORKSPACE_READ_RECONCILIATION_REQUIRED"
    assert len(unresolved) == 1


@pytest.mark.parametrize(
    ("change_path", "change_worktree", "new_suffix"),
    [
        ("src/other.py", "default", "new"),
        ("src/app.py", "other-worktree", "new"),
        ("src/app.py", "default", "old"),
    ],
)
def test_non_conflicts_do_not_emit_false_signals(
    tmp_path: Path,
    change_path: str,
    change_worktree: str,
    new_suffix: str,
) -> None:
    old_sha = _sha("old")
    new_sha = _sha(new_suffix)
    store_path = tmp_path / "run" / "dag-run.sqlite3"
    plan = _plan(tmp_path, reader_sha=old_sha, reader_policy="require_reconciliation")
    both_started = threading.Barrier(2)

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        both_started.wait(timeout=2)
        if node.node_id == "a-writer":
            return _pass(
                node.node_id,
                workspace_changes=[
                    _change(
                        path=change_path,
                        previous_sha256=old_sha,
                        new_sha256=new_sha,
                        worktree_id=change_worktree,
                    )
                ],
            )
        return _pass(node.node_id)

    with SqliteDagRunStore(store_path) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-stale-read-non-conflict",
            max_concurrency=2,
        )
        signals = store.list_workspace_change_signals(plan.plan_id)

    assert result.status == "PASS"
    assert signals == ()


def test_absolute_source_binding_records_without_path_escape(tmp_path: Path) -> None:
    work_order = tmp_path / "work-order.json"
    work_order.write_text('{"task":"read this"}\n', encoding="utf-8")
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "workspace-absolute-source-binding-test",
        "run_dir": str(tmp_path / "run"),
        "nodes": [
            {
                **_node(tmp_path, "reader"),
                "work_order_path": str(work_order),
            }
        ],
    }
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        return _pass(node.node_id)

    with SqliteDagRunStore(tmp_path / "run" / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-absolute-source-binding",
        )

    assert result.status == "PASS"


def _wait_for_signal(
    store_path: Path,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with SqliteDagRunStore(store_path) as store:
            signals = store.unresolved_workspace_change_signals(run_id, attempt_id)
        if signals:
            return dict(signals[0])
        time.sleep(0.01)
    raise AssertionError("stale read signal was not recorded")


def _plan(tmp_path: Path, *, reader_sha: str, reader_policy: str) -> DagPlan:
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "workspace-stale-read-test",
        "run_dir": str(tmp_path / "run"),
        "nodes": [
            _node(tmp_path, "a-writer"),
            _node(tmp_path, "z-reader"),
        ],
    }
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    nodes = []
    for node in plan.nodes:
        if node.node_id == "z-reader":
            nodes.append(
                replace(
                    node,
                    source_bindings=(
                        FrozenJson.from_value(_read(path="src/app.py", blob_sha256=reader_sha)),
                    ),
                    source_extensions=FrozenJson.from_value({"stale_read_policy": reader_policy}),
                )
            )
        else:
            nodes.append(node)
    return replace(plan, nodes=tuple(nodes)).with_computed_hash()


def _node(tmp_path: Path, node_id: str) -> dict[str, object]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": [],
        "accepted_context_from": [],
        "receipt_path": str(tmp_path / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


def _pass(node_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "accepted_output": {"node_id": node_id},
        **extra,
    }


def _read(
    *,
    path: str,
    blob_sha256: str,
    observation_source: str = "test_fixture",
) -> dict[str, Any]:
    return {
        "schema": "tau.workspace_read.v1",
        "repository_id": "repo",
        "worktree_id": "default",
        "path": path,
        "blob_sha256": blob_sha256,
        "observation_source": observation_source,
    }


def _change(
    *,
    path: str,
    previous_sha256: str,
    new_sha256: str,
    worktree_id: str = "default",
) -> dict[str, Any]:
    return {
        "schema": "tau.workspace_change.v1",
        "repository_id": "repo",
        "worktree_id": worktree_id,
        "path": path,
        "previous_sha256": previous_sha256,
        "new_sha256": new_sha256,
        "change_source": "test_fixture",
    }


def _sha(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
