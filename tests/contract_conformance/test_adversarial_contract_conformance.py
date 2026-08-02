from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.artifact_reference import (
    ArtifactReferenceError,
    dereference_artifact_reference,
)
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan, compile_project_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.replay import replay_dag_run
from tau_coding.dag_runtime.run_store import SqliteDagRunReader, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.transition import (
    DagDeadlineArm,
    DagEdgeSettlement,
    DagTransitionBatch,
    transition_batch_from_payload,
    transition_batch_to_payload,
)
from tau_coding.generic_dag import validate_generic_dag_spec
from tau_coding.project_dag import validate_dag_contract

MANIFEST_PATH = Path(__file__).parent / "fixtures" / "mutation_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("case_id", "mutate", "match"),
    [
        (
            "project_duplicate_node_id",
            lambda payload: payload["nodes"].append(copy.deepcopy(payload["nodes"][0])),
            "duplicate node id",
        ),
        (
            "project_join_source_nonexclusive",
            lambda payload: (
                    payload["nodes"].append(
                        {
                            "id": "join",
                            "agent": "join",
                            "executor": "virtual",
                        "max_attempts": 1,
                        "required_evidence": [],
                            "join": {
                                "schema": "tau.dag_join_policy.v1",
                                "policy": "all_terminal",
                                "timeout_seconds": 30,
                            },
                        }
                    ),
                    payload["nodes"].append(
                        {
                            "id": "other",
                            "agent": "other",
                            "executor": "virtual",
                            "max_attempts": 1,
                            "required_evidence": [],
                        }
                    ),
                    payload["edges"].append({"from": "coder", "to": "other"}),
                    payload["edges"].append({"from": "coder", "to": "join"}),
                    payload["edges"].append({"from": "other", "to": "join"}),
                    payload["edges"].append({"from": "join", "to": "human"}),
                ),
                "join_source_outgoing_not_exclusive",
        ),
    ],
)
def test_project_source_mutations_fail_before_authority_changes(
    tmp_path: Path,
    case_id: str,
    mutate: Callable[[dict[str, Any]], object],
    match: str,
) -> None:
    _assert_manifest_case(case_id)
    payload = _project_payload(tmp_path)
    mutate(payload)

    with pytest.raises(RuntimeError, match=match):
        compile_project_dag_plan(payload, source_path=tmp_path / "project.dag.json")

    assert not (tmp_path / "run" / "command-loop").exists()


@pytest.mark.parametrize(
    ("case_id", "mutate", "match"),
    [
        (
            "generic_numeric_string_timeout",
            lambda spec: spec["nodes"][0].__setitem__("timeout_seconds", "1"),
            "positive finite number",
        ),
        (
            "generic_unknown_node_field",
            lambda spec: spec["nodes"][0].__setitem__("implicit_extension", True),
            "not allowed outside extensions",
        ),
        (
            "generic_nonfinite_timeout",
            lambda spec: spec["nodes"][0].__setitem__("timeout_seconds", float("nan")),
            "NaN or Infinity",
        ),
    ],
)
def test_generic_source_mutations_reject_without_dispatch(
    tmp_path: Path,
    case_id: str,
    mutate: Callable[[dict[str, Any]], object],
    match: str,
) -> None:
    _assert_manifest_case(case_id)
    spec = _generic_spec(tmp_path)
    mutate(spec)

    with pytest.raises(RuntimeError, match=match):
        validate_generic_dag_spec(spec, source_path=tmp_path / "generic.dag.json")

    assert not (tmp_path / "run").exists()


def test_context_binding_hash_mismatch_blocks_successor_and_admission(tmp_path: Path) -> None:
    _assert_manifest_case("context_binding_hash_mismatch")
    plan = _producer_consumer_plan(tmp_path)
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "schema": "source.output.v1",
                "value": "tampered",
                "sha256": "sha256:" + "0" * 64,
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="conformance-context-binding",
        )
        admissions = store.list_admissions("node-input-manifest-test")

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_DECLARED_HASH_MISMATCH"
    assert called == ["producer"]
    assert result.completed_node_ids == ("producer",)
    assert all(item["node_id"] != "consumer" for item in admissions)


def test_attempt_result_mutation_blocks_successor_and_accepted_output(tmp_path: Path) -> None:
    _assert_manifest_case("attempt_result_pass_fail_mismatch")
    plan = _producer_consumer_plan(tmp_path)
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "FAIL",
            "accepted_output": {"source_node_id": node.node_id},
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="conformance-attempt-result",
        )
        admissions = store.list_admissions("node-input-manifest-test")

    assert result.status == "BLOCKED"
    assert result.verdict == "DAG_ATTEMPT_RESULT_INVALID"
    assert called == ["producer"]
    assert result.completed_node_ids == ()
    assert admissions == []
    assert result.node_results[0]["errors"] == ["dag_attempt_result_pass_verdict_mismatch"]


@pytest.mark.parametrize(
    ("case_id", "mutate", "match"),
    [
        (
            "transition_unknown_edge",
            lambda payload, plan: payload["edge_settlements"].append(
                {"edge_id": "missing-edge", "state": "success", "reason_code": "bad_edge"}
            ),
            "dag_transition_unknown_edge",
        ),
        (
            "transition_bad_deadline",
            lambda payload, plan: payload["deadline_arms"].append(
                {
                    "deadline_id": "deadline:bad",
                    "deadline_due_at_ms": "not-an-int",
                    "reason_code": "bad_deadline",
                }
            ),
            "dag_transition_deadline_due_invalid",
        ),
    ],
)
def test_transition_mutations_reject_before_commit(
    tmp_path: Path,
    case_id: str,
    mutate: Callable[[dict[str, Any], DagPlan], object],
    match: str,
) -> None:
    _assert_manifest_case(case_id)
    plan = _producer_consumer_plan(tmp_path)
    payload = transition_batch_to_payload(
        DagTransitionBatch(
            edge_settlements=(
                DagEdgeSettlement(
                    edge_id=plan.control_edges[0].edge_id,
                    state="success",
                    reason_code="source_passed",
                ),
            ),
            deadline_arms=(
                DagDeadlineArm(
                    deadline_id="deadline:producer",
                    deadline_monotonic=1.0,
                    reason_code="timeout",
                ),
            ),
        )
    )
    mutate(payload, plan)

    with pytest.raises(RuntimeError, match=match):
        transition_batch_from_payload(payload, plan=plan, active_deadlines={})


def test_artifact_reference_provenance_mutation_rejects_dereference(tmp_path: Path) -> None:
    _assert_manifest_case("artifact_reference_admission_missing")
    plan, store, reference = _stored_artifact_reference(tmp_path)
    binding = plan.context_bindings[0]
    mutated = {**reference, "admitted_artifact_id": "missing"}
    without_hash = {key: value for key, value in mutated.items() if key != "reference_sha256"}
    mutated = {**without_hash, "reference_sha256": canonical_sha256(without_hash)}

    with store, pytest.raises(ArtifactReferenceError, match="ARTIFACT_REFERENCE_ADMISSION_MISSING"):
        dereference_artifact_reference(
            mutated,
            run_store=store,
            expected_run_id="node-input-manifest-test",
            expected_producer_node_id="producer",
            expected_producer_attempt_id=str(reference["producer"]["attempt_id"]),
            expected_consumer_node_id="consumer",
            binding=binding,
            run_store_root=store.path.parent,
        )


def test_valid_round_trips_replay_deterministically_and_exports_are_isolated(
    tmp_path: Path,
) -> None:
    _assert_manifest_case("source_alias_exported_payload_mutation")
    _assert_manifest_case("replay_repeated_determinism")
    project_source = _project_payload(tmp_path)
    contract = validate_dag_contract(project_source)
    project_source["nodes"][0]["context"]["labels"].append("mutated")
    plan = compile_project_dag_plan(contract.payload.copy(), source_path=tmp_path / "project.json")
    exported = plan.to_payload()
    exported["nodes"][0]["static_context"]["node"]["labels"].append("mutated")

    assert contract.nodes["coder"].context["labels"] == ["initial"]
    assert plan.to_payload()["nodes"][0]["static_context"]["node"]["labels"] == ["initial"]

    replay_plan = _producer_consumer_plan(tmp_path / "replay")

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": node.node_id},
        }

    database = tmp_path / "replay.sqlite3"
    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            replay_plan,
            execute_node=execute,
            run_store=store,
            run_id="replay-run",
            lease_owner="conformance-replay",
        )
    assert result.status == "PASS"

    with SqliteDagRunReader(database) as reader:
        kwargs = {
            "plan": reader.load_plan("replay-run"),
            "run_record": reader.load_run_record("replay-run"),
            "events": tuple(
                item.to_mapping() for item in reader.load_events("replay-run", limit=5000)
            ),
            "attempts": reader.load_attempts("replay-run"),
            "runtime_projections": reader.runtime_projections("replay-run"),
        }
    first = replay_dag_run(**kwargs)
    second = replay_dag_run(**kwargs)

    assert first.node_states == second.node_states
    assert first.edge_states == second.edge_states
    assert first.terminal_states == second.terminal_states


def test_public_validator_sensitivity_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _assert_manifest_case("monkeypatched_public_validator_sensitivity")
    import tau_coding.generic_dag as generic_dag

    spec = _generic_spec(tmp_path)
    spec["nodes"][0]["implicit_extension"] = True
    with pytest.raises(RuntimeError, match="not allowed outside extensions"):
        validate_generic_dag_spec(copy.deepcopy(spec), source_path=tmp_path / "generic.dag.json")

    monkeypatch.setattr(generic_dag, "validate_generic_dag_public_boundary", lambda payload: None)
    nodes = generic_dag.validate_generic_dag_spec(
        copy.deepcopy(spec),
        source_path=tmp_path / "generic.dag.json",
    )

    assert "one" in nodes


def test_manifest_categories_are_exercised() -> None:
    expected = set(MANIFEST["categories"])
    observed = {case["category"] for case in MANIFEST["cases"]}
    assert expected == observed
    assert MANIFEST["seed"] == 29820260802


def _assert_manifest_case(case_id: str) -> None:
    assert any(case["id"] == case_id for case in MANIFEST["cases"])


def _project_payload(tmp_path: Path) -> dict[str, Any]:
    command_spec_dir = tmp_path / "specs" / "coder"
    command_spec_dir.mkdir(parents=True, exist_ok=True)
    (command_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps({"schema": "tau.agent_handoff_command.v1", "command": ["true"]}),
        encoding="utf-8",
    )
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "conformance-project",
        "goal": {
            "goal_id": "goal-conformance",
            "goal_version": 1,
            "goal_hash": "sha256:goal",
            "summary": "adversarial conformance",
            "completion_criteria": ["invalid authority changes fail closed"],
        },
        "target": {"repo": "grahama1970/tau", "target": "issue-298"},
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {"max_total_attempts": 1, "default_timeout_seconds": 60},
        "context": {},
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(command_spec_dir),
                "required_evidence": [],
                "context": {"labels": ["initial"]},
            }
        ],
        "edges": [{"from": "coder", "to": "human"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }


def _generic_spec(tmp_path: Path) -> dict[str, Any]:
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "conformance-generic",
        "run_dir": str(tmp_path / "run"),
        "nodes": [_node(tmp_path, "one")],
    }


def _producer_consumer_plan(tmp_path: Path) -> DagPlan:
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "node-input-manifest-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                _node(tmp_path, "producer"),
                _node(tmp_path, "consumer", depends_on=["producer"]),
            ],
        },
        source_path=tmp_path / "dag.json",
    )


def _node(
    tmp_path: Path,
    node_id: str,
    *,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": depends_on or [],
        "receipt_path": str(tmp_path / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


def _stored_artifact_reference(tmp_path: Path) -> tuple[DagPlan, SqliteDagRunStore, dict[str, Any]]:
    plan = replace(
        _producer_consumer_plan(tmp_path),
        context_bindings=(
            replace(
                _producer_consumer_plan(tmp_path).context_bindings[0],
                selector_kind="receipt_by_schema",
                accepted_source_schemas=("large.report.v1",),
                materialization_mode="by_reference",
                on_invalid="block",
                max_reference_bytes=4096,
            ),
        ),
    ).with_computed_hash()
    captured: list[dict[str, Any]] = []
    store = SqliteDagRunStore(tmp_path / "artifact-ref.sqlite3")

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del attempt
        if node.node_id == "consumer":
            captured.extend(accepted_inputs)
            return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}
        receipt_path = tmp_path / "receipts" / "producer.json"
        digest, size = _write_json_receipt(
            receipt_path,
            {"schema": "large.report.v1", "section": {"kept": True}},
        )
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(receipt_path),
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "large.report.v1",
                        "path": str(receipt_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    result = run_dag_plan(
        plan,
        execute_node=execute,
        run_store=store,
        lease_owner="conformance-artifact-reference",
    )
    assert result.status == "PASS"
    assert len(captured) == 1
    return plan, store, captured[0]


def _write_json_receipt(path: Path, payload: dict[str, Any]) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}", len(encoded)
