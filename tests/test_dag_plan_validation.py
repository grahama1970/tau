from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import (
    DagPlan,
    DagPlanEdge,
    DagPlanTerminal,
    DagPlanValidationError,
    canonical_json,
    validate_dag_plan,
)
from tau_coding.dag_runtime.run_store import (
    DagRunStoreError,
    SqliteDagRunReader,
    SqliteDagRunStore,
)
from tau_coding.dag_runtime.scheduler import run_dag_plan


def test_duplicate_node_rehash_is_blocked_before_store_or_dispatch(tmp_path: Path) -> None:
    plan = _invalid_rehashed_plan(
        _base_plan(tmp_path),
        lambda plan: replace(plan, nodes=(plan.nodes[0], plan.nodes[0])),
    )
    calls: list[str] = []
    events: list[dict[str, Any]] = []

    def execute(node, accepted_inputs, attempt):  # type: ignore[no-untyped-def]
        del accepted_inputs, attempt
        calls.append(node.node_id)
        return _pass_result(node.node_id)

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            run_id="invalid-run",
            event_sink=events.append,
        )
        row_count = store._connection.execute("SELECT count(*) FROM dag_runs").fetchone()[0]
        assert row_count == 0

    assert result.status == "BLOCKED"
    assert result.verdict == "duplicate_node_id"
    assert calls == []
    assert events[0]["event"] == "scheduler_plan_blocked"
    assert events[0]["dag_plan_validation"]["codes"][0] == "duplicate_node_id"


@pytest.mark.parametrize(
    ("case_name", "mutate", "expected_code"),
    [
        (
            "invalid_target_kind",
            lambda plan: replace(
                plan,
                control_edges=(
                    replace(plan.control_edges[0], target_kind="unknown-target"),
                ),
            ),
            "edge_target_kind_invalid",
        ),
        (
            "duplicate_edge_id",
            lambda plan: replace(
                plan,
                control_edges=(plan.control_edges[0], plan.control_edges[0]),
            ),
            "duplicate_edge_id",
        ),
        (
            "duplicate_binding_id",
            lambda plan: replace(
                plan,
                context_bindings=(plan.context_bindings[0], plan.context_bindings[0]),
            ),
            "duplicate_binding_id",
        ),
        (
            "cycle",
            lambda plan: replace(
                plan,
                control_edges=(
                    *plan.control_edges,
                    DagPlanEdge(
                        edge_id="cycle-edge",
                        source_node_id="reviewer",
                        target_id="planner",
                        target_kind="node",
                        condition=None,
                        source_ordinal=None,
                    ),
                ),
            ),
            "cycle_detected",
        ),
        (
            "unreachable_node",
            lambda plan: replace(
                plan,
                nodes=(*plan.nodes, replace(plan.nodes[0], node_id="orphan")),
            ),
            "node_unreachable",
        ),
        (
            "dead_end_node",
            lambda plan: replace(
                plan,
                terminal_endpoints=(DagPlanTerminal("done", "external", "declared"),),
            ),
            "node_dead_end",
        ),
        (
            "malformed_terminal",
            lambda plan: replace(
                plan,
                terminal_endpoints=(DagPlanTerminal("reviewer", "bogus", "declared"),),
            ),
            "terminal_kind_invalid",
        ),
    ],
)
def test_plan_validator_reports_stable_structural_codes(
    tmp_path: Path,
    case_name: str,
    mutate: Any,
    expected_code: str,
) -> None:
    del case_name
    plan = _invalid_rehashed_plan(_base_plan(tmp_path), mutate)

    validation = validate_dag_plan(plan)

    assert expected_code in validation.codes


@pytest.mark.parametrize(
    ("mutate_node", "expected_code"),
    [
        (lambda node: replace(node, timeout_seconds=0), "timeout_seconds_invalid"),
        (lambda node: replace(node, max_attempts=True), "max_attempts_invalid"),
    ],
)
def test_nested_field_local_invariants_raise_stable_codes(
    tmp_path: Path,
    mutate_node: Any,
    expected_code: str,
) -> None:
    plan = _base_plan(tmp_path)

    with pytest.raises(DagPlanValidationError) as exc:
        mutate_node(plan.nodes[0])

    assert exc.value.validation.codes == (expected_code,)


@pytest.mark.parametrize(
    ("case_name", "mutate", "expected_code"),
    [
        (
            "source_mismatch",
            lambda plan: replace(
                plan,
                context_bindings=(
                    replace(plan.context_bindings[0], source_node_id="reviewer"),
                ),
            ),
            "dag_context_binding_edge_mismatch",
        ),
        (
            "target_mismatch",
            lambda plan: replace(
                plan,
                context_bindings=(
                    replace(plan.context_bindings[0], target_node_id="reviewer"),
                ),
            ),
            "dag_context_binding_edge_mismatch",
        ),
        (
            "terminal_control_edge",
            lambda plan: replace(
                plan,
                control_edges=(
                    replace(
                        plan.control_edges[0],
                        target_kind="terminal",
                        target_id="done",
                    ),
                    *plan.control_edges[1:],
                ),
                terminal_endpoints=(
                    *plan.terminal_endpoints,
                    DagPlanTerminal("done", "external", "declared"),
                ),
            ),
            "dag_context_binding_target_not_node",
        ),
        (
            "missing_source",
            lambda plan: replace(
                plan,
                context_bindings=(
                    replace(plan.context_bindings[0], source_node_id="missing-source"),
                ),
            ),
            "dag_context_binding_source_missing",
        ),
        (
            "missing_target",
            lambda plan: replace(
                plan,
                context_bindings=(
                    replace(plan.context_bindings[0], target_node_id="missing-target"),
                ),
            ),
            "dag_context_binding_target_missing",
        ),
        (
            "missing_control_edge",
            lambda plan: replace(
                plan,
                context_bindings=(
                    replace(plan.context_bindings[0], control_edge_id="missing-edge"),
                ),
            ),
            "dag_context_binding_control_edge_missing",
        ),
        (
            "duplicate_binding_id",
            lambda plan: replace(
                plan,
                context_bindings=(
                    plan.context_bindings[0],
                    replace(
                        plan.context_bindings[1],
                        binding_id=plan.context_bindings[0].binding_id,
                    ),
                ),
            ),
            "duplicate_binding_id",
        ),
    ],
)
def test_context_binding_lineage_reports_stable_codes(
    tmp_path: Path,
    case_name: str,
    mutate: Any,
    expected_code: str,
) -> None:
    del case_name
    plan = _invalid_rehashed_plan(_base_plan(tmp_path), mutate)

    validation = validate_dag_plan(plan)

    assert expected_code in validation.codes


def test_stored_plan_load_uses_same_canonical_validator(tmp_path: Path) -> None:
    plan = _base_plan(tmp_path)
    invalid = _invalid_rehashed_plan(
        plan,
        lambda current: replace(current, nodes=(current.nodes[0], current.nodes[0])),
    )
    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner")
        store.release_lease(lease)

    payload = invalid.to_payload()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE dag_runs SET plan_json = ?, plan_sha256 = ? WHERE run_id = 'run-1'",
            (canonical_json(payload), invalid.plan_sha256),
        )

    with SqliteDagRunReader(database) as reader, pytest.raises(
        DagRunStoreError, match="duplicate_node_id"
    ):
        reader.load_plan("run-1")


def test_valid_project_and_generic_plan_hashes_still_round_trip(tmp_path: Path) -> None:
    plan = _base_plan(tmp_path)
    payload = json.loads(canonical_json(plan.to_payload()))

    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner")
        store.release_lease(lease)
    with SqliteDagRunReader(database) as reader:
        loaded = reader.load_plan("run-1")

    assert payload == loaded.to_payload()
    assert loaded.plan_sha256 == plan.plan_sha256


def _invalid_rehashed_plan(plan: DagPlan, mutate: Any) -> DagPlan:
    return mutate(plan).with_computed_hash()


def _base_plan(tmp_path: Path) -> DagPlan:
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "validation-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                _node(tmp_path, "planner", depends_on=[]),
                _node(tmp_path, "coder", depends_on=["planner"]),
                _node(tmp_path, "reviewer", depends_on=["coder"]),
            ],
        },
        source_path=tmp_path / "dag.json",
    )


def _node(tmp_path: Path, node_id: str, *, depends_on: list[str]) -> dict[str, object]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on,
        "accepted_context_from": depends_on,
        "receipt_path": str(tmp_path / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


def _pass_result(node_id: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "accepted_output": {"source_node_id": node_id},
    }


CONCURRENT_FANOUT_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "concurrent-fanout-dag.json"
)


def _materialize_fanout_contract(tmp_path: Path, handlers: tuple[str, ...]) -> Path:
    """Materialize a real concurrent fan-out contract with local command specs.

    The fixture is a captured production ``tau.dag_contract.v1`` roundtable DAG,
    not a hand-rolled approximation, so the test exercises the shape /ask actually
    emits.
    """

    contract = json.loads(CONCURRENT_FANOUT_FIXTURE.read_text())
    keep = {f"handler-{name}" for name in handlers} | {"join"}
    contract["nodes"] = [node for node in contract["nodes"] if node["id"] in keep]
    contract["edges"] = [
        edge
        for edge in contract["edges"]
        if edge["from"] in keep and (edge["to"] in keep or edge["to"] == "human")
    ]
    contract["entry_node"] = f"handler-{handlers[0]}"

    for node in contract["nodes"]:
        spec_dir = tmp_path / "command-specs" / node["id"]
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_path = spec_dir / "tau-dispatch-command.json"
        spec_path.write_text(json.dumps({"argv": ["/bin/echo", node["id"]]}))
        node["command_spec"] = str(spec_path)

    path = tmp_path / "dag.json"
    path.write_text(json.dumps(contract))
    return path


def test_concurrent_fanout_roots_are_all_entry_nodes(tmp_path: Path) -> None:
    """Regression: a concurrent roundtable must not lose its non-declared roots.

    Before this fix the project_dag compiler set ``entry_node_ids`` to the single
    scalar ``contract.entry_node``, so plan reachability was seeded from one root
    and every other participant was reported ``node_unreachable``. The plan was
    rejected pre-dispatch and no participant was ever contacted, which reads like a
    provider outage rather than a contract defect.
    """

    from tau_coding.dag_runtime.compiler import compile_project_dag_plan

    handlers = ("webgpt", "webclaude", "webkimi", "webgrok", "webgemini")
    source = _materialize_fanout_contract(tmp_path, handlers)
    plan = compile_project_dag_plan(json.loads(source.read_text()), source_path=source)

    assert set(plan.entry_node_ids) == {f"handler-{name}" for name in handlers}
    # the declared entry stays first so entry_preserved diffs keep holding
    assert plan.entry_node_ids[0] == "handler-webgpt"

    result = validate_dag_plan(plan)
    codes = {issue.code for issue in result.issues}
    assert "node_unreachable" not in codes, sorted(codes)
    assert result.ok, sorted(codes)


def test_single_root_entry_is_unchanged(tmp_path: Path) -> None:
    """A single-root pipeline must still report exactly one entry node."""

    from tau_coding.dag_runtime.compiler import compile_project_dag_plan

    source = _materialize_fanout_contract(tmp_path, ("webgpt",))
    plan = compile_project_dag_plan(json.loads(source.read_text()), source_path=source)

    assert plan.entry_node_ids == ("handler-webgpt",)
    assert validate_dag_plan(plan).ok
