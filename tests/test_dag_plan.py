import json
from pathlib import Path

import pytest

from tau_coding.dag_runtime import (
    compile_generic_dag_plan,
    compile_project_dag_plan,
    write_dag_plan,
)


def test_project_contract_compiles_routes_joins_security_and_evidence(tmp_path: Path) -> None:
    payload = _project_payload(tmp_path)

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")
    exported = plan.to_payload()

    assert plan.schema == "tau.dag_plan.v1"
    assert plan.source_schema == "tau.dag_contract.v1"
    assert plan.entry_node_ids == ("router",)
    assert [item.to_payload() for item in plan.terminal_endpoints] == [
        {"terminal_id": "human", "kind": "external", "origin": "declared"}
    ]
    assert plan.required_evidence == ("review_receipt",)
    assert exported["security_declarations"]["security_mode"] == "secure"
    assert [node.node_id for node in plan.nodes] == ["branch-a", "branch-b", "join", "router"]
    assert exported["route_contracts"][0]["mode"] == "exclusive"
    assert len(exported["route_contracts"][0]["ordered_edge_ids"]) == 2
    assert exported["join_contracts"][0]["join_node_id"] == "join"
    assert exported["join_contracts"][0]["policy"]["policy"] == "all_terminal"
    assert plan.plan_sha256.startswith("sha256:")


def test_project_contract_preserves_context_layers_and_evidence_manifest(
    tmp_path: Path,
) -> None:
    payload = _project_payload(tmp_path)
    payload["context"] = {"summary": "contract summary", "shared": "contract"}
    payload["nodes"][0]["context"] = {"shared": "node", "node_only": True}
    manifest = tmp_path / "evidence-manifest.json"
    manifest.write_text('{"schema":"tau.evidence_manifest.v1"}\n', encoding="utf-8")
    payload["evidence_manifest"] = "evidence-manifest.json"

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")
    router = next(node for node in plan.nodes if node.node_id == "router")
    declarations = plan.to_payload()["security_declarations"]["declarations"]

    assert router.static_context.to_value() == {
        "merge_policy": "project_handoff_context_v1",
        "contract": {"summary": "contract summary", "shared": "contract"},
        "node": {"shared": "node", "node_only": True},
    }
    assert next(
        item for item in declarations if item["binding_id"] == "project:evidence-manifest"
    )["content_sha256"].startswith("sha256:")


def test_project_contract_compilation_is_byte_and_hash_stable(tmp_path: Path) -> None:
    first = compile_project_dag_plan(
        _project_payload(tmp_path), source_path=tmp_path / "dag.json"
    )
    second = compile_project_dag_plan(
        _project_payload(tmp_path), source_path=tmp_path / "dag.json"
    )

    assert first.plan_sha256 == second.plan_sha256
    assert json.dumps(first.to_payload(), sort_keys=True) == json.dumps(
        second.to_payload(), sort_keys=True
    )


def test_project_contract_preserves_declared_node_terminal(tmp_path: Path) -> None:
    payload = _project_payload(tmp_path)
    payload["terminal_nodes"] = ["join"]
    payload["edges"] = [edge for edge in payload["edges"] if edge["to"] != "human"]

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")

    assert [item.to_payload() for item in plan.terminal_endpoints] == [
        {"terminal_id": "join", "kind": "declared_node", "origin": "declared"}
    ]


def test_project_route_source_order_changes_plan_hash(tmp_path: Path) -> None:
    first_payload = _project_payload(tmp_path)
    second_payload = _project_payload(tmp_path)
    second_payload["edges"][0], second_payload["edges"][1] = (
        second_payload["edges"][1],
        second_payload["edges"][0],
    )

    first = compile_project_dag_plan(first_payload, source_path=tmp_path / "dag.json")
    second = compile_project_dag_plan(second_payload, source_path=tmp_path / "dag.json")

    assert first.plan_sha256 != second.plan_sha256
    assert first.to_payload()["route_contracts"] != second.to_payload()["route_contracts"]


def test_project_contract_cycle_fails_before_plan_creation(tmp_path: Path) -> None:
    payload = _project_payload(tmp_path)
    payload["edges"].append({"from": "join", "to": "router"})

    with pytest.raises(RuntimeError, match="cycle_detected"):
        compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_project_contract_rejects_mixed_route_edges(tmp_path: Path) -> None:
    payload = _project_payload(tmp_path)
    payload["edges"].append({"from": "router", "to": "human"})

    with pytest.raises(RuntimeError, match="mixed_conditional_unconditional_routes"):
        compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_project_contract_rejects_nonexclusive_join_source(tmp_path: Path) -> None:
    payload = _project_payload(tmp_path)
    payload["edges"].append({"from": "branch-a", "to": "human"})

    with pytest.raises(RuntimeError, match="join_source_outgoing_not_exclusive"):
        compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_generic_spec_compiles_control_and_context_edges(tmp_path: Path) -> None:
    payload = _generic_payload(tmp_path)

    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")

    assert plan.source_schema == "tau.generic_dag_spec.v1"
    assert plan.entry_node_ids == ("planner",)
    assert [item.terminal_id for item in plan.terminal_endpoints] == ["reviewer"]
    assert [node.adapter_kind for node in plan.nodes] == [
        "generic_command",
        "generic_command",
        "generic_command",
    ]
    edges = {(edge.source_node_id, edge.target_id) for edge in plan.control_edges}
    assert edges == {("coder", "reviewer"), ("planner", "coder"), ("planner", "reviewer")}
    bindings = {(item.source_node_id, item.target_node_id) for item in plan.context_bindings}
    assert bindings == {("coder", "reviewer"), ("planner", "coder")}
    assert [item.to_value() for item in plan.runtime_bindings] == [
        {
            "binding_id": "generic:events-jsonl",
            "kind": "event_log",
            "declared_path": "events.jsonl",
            "anchor": "generic_run_directory",
            "portable": True,
            "origin": "derived_default",
        }
    ]
    for node in plan.nodes:
        working_directory = next(
            item
            for item in node.to_payload()["source_bindings"]
            if item["kind"] == "working_directory"
        )
        assert working_directory == {
            "binding_id": f"node:{node.node_id}:working-directory",
            "kind": "working_directory",
            "declared_path": str(tmp_path),
            "anchor": "filesystem_root",
            "portable": False,
        }
        receipt_binding = next(
            item
            for item in node.to_payload()["source_bindings"]
            if item["kind"] == "output_path"
        )
        assert receipt_binding["anchor"] == "filesystem_root"
        assert receipt_binding["portable"] is False
        assert receipt_binding["declared_path"] == str(tmp_path / f"{node.node_id}.json")


def test_generic_relative_run_dir_retains_invocation_cwd_anchor(tmp_path: Path) -> None:
    payload = _portable_generic_payload()

    plan = compile_generic_dag_plan(
        payload,
        source_path=tmp_path / "elsewhere" / "dag.json",
    )
    binding = next(
        item
        for item in plan.nodes[0].to_payload()["source_bindings"]
        if item["kind"] == "working_directory"
    )

    assert binding == {
        "binding_id": "node:worker:working-directory",
        "kind": "working_directory",
        "declared_path": "run",
        "anchor": "process_invocation_directory",
        "portable": True,
    }


def test_generic_explicit_event_log_retains_invocation_cwd_anchor(tmp_path: Path) -> None:
    payload = _portable_generic_payload()
    payload["events_jsonl"] = "logs/custom-events.jsonl"

    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")

    assert [item.to_value() for item in plan.runtime_bindings] == [
        {
            "binding_id": "generic:events-jsonl",
            "kind": "event_log",
            "declared_path": "logs/custom-events.jsonl",
            "anchor": "process_invocation_directory",
            "portable": True,
            "origin": "explicit",
        }
    ]


def test_project_provider_only_node_uses_provider_adapter(tmp_path: Path) -> None:
    payload = _project_payload(tmp_path)
    provider = payload["nodes"][1]
    provider["executor"] = "provider"
    provider["provider"] = {"adapter": "generic-provider-dag-node"}

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")
    node = next(item for item in plan.nodes if item.node_id == "branch-a")

    assert node.adapter_kind == "project_provider"
    assert node.adapter_config.to_value() == {
        "provider": {"adapter": "generic-provider-dag-node"}
    }


def test_generic_skill_output_directory_is_source_anchored(tmp_path: Path) -> None:
    work_order = tmp_path / "work-order.json"
    work_order.write_text("{}\n", encoding="utf-8")
    payload = _portable_generic_payload()
    node = payload["nodes"][0]
    node.pop("command")
    node["work_order_path"] = "work-order.json"
    node["skill"] = {
        "schema": "tau.skill_dag_node.v1",
        "provider": "webgpt",
        "capability": "architecture_review",
        "output_dir": "skill-output",
    }

    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    compiled = plan.nodes[0].to_payload()
    output_binding = next(
        item
        for item in compiled["source_bindings"]
        if item["kind"] == "output_directory"
    )

    assert compiled["adapter"]["config"]["output_dir"] == "skill-output"
    assert output_binding["declared_path"] == "skill-output"
    assert output_binding["anchor"] == "source_document_directory"


def test_generic_spec_preserves_multiple_roots_and_leaves(tmp_path: Path) -> None:
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "multi-root-leaf",
        "run_dir": str(tmp_path),
        "nodes": [
            {
                "node_id": "root-a",
                "command": ["python", "-c", "pass"],
                "depends_on": [],
                "receipt_path": str(tmp_path / "root-a.json"),
            },
            {
                "node_id": "root-b",
                "command": ["python", "-c", "pass"],
                "depends_on": [],
                "receipt_path": str(tmp_path / "root-b.json"),
            },
            {
                "node_id": "leaf-a",
                "command": ["python", "-c", "pass"],
                "depends_on": ["root-a"],
                "receipt_path": str(tmp_path / "leaf-a.json"),
            },
            {
                "node_id": "leaf-b",
                "command": ["python", "-c", "pass"],
                "depends_on": ["root-b"],
                "receipt_path": str(tmp_path / "leaf-b.json"),
            },
        ],
    }

    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")

    assert plan.entry_node_ids == ("root-a", "root-b")
    assert tuple(item.terminal_id for item in plan.terminal_endpoints) == (
        "leaf-a",
        "leaf-b",
    )


def test_generic_spec_rejects_duplicate_dependency_edges(tmp_path: Path) -> None:
    payload = _generic_payload(tmp_path)
    payload["nodes"][1]["depends_on"] = ["planner", "planner"]
    payload["nodes"][1]["accepted_context_from"] = ["planner"]

    with pytest.raises(RuntimeError, match="duplicate dependency edges"):
        compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_generic_spec_invalid_context_fails_before_plan_creation(tmp_path: Path) -> None:
    payload = _generic_payload(tmp_path)
    payload["nodes"][1]["accepted_context_from"] = ["reviewer"]

    with pytest.raises(RuntimeError, match="must be a subset of depends_on"):
        compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_generic_spec_rejects_command_and_skill_adapter_mix(tmp_path: Path) -> None:
    payload = _portable_generic_payload()
    payload["nodes"][0]["skill"] = {
        "schema": "tau.skill_dag_node.v1",
        "provider": "webgpt",
        "capability": "architecture_review",
        "output_dir": "skill-output",
    }

    with pytest.raises(RuntimeError, match="cannot declare both command and skill"):
        compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_non_finite_source_extension_is_not_canonical_json(tmp_path: Path) -> None:
    payload = _portable_generic_payload()
    payload["source_score"] = float("nan")

    with pytest.raises(RuntimeError, match="not canonical JSON"):
        compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_source_extensions_are_preserved_and_hash_bound(tmp_path: Path) -> None:
    first_payload = _portable_generic_payload()
    first_payload["project_extension"] = {"revision": 1}
    second_payload = _portable_generic_payload()
    second_payload["project_extension"] = {"revision": 2}

    first = compile_generic_dag_plan(first_payload, source_path=tmp_path / "dag.json")
    second = compile_generic_dag_plan(second_payload, source_path=tmp_path / "dag.json")

    assert first.to_payload()["source_extensions"] == {
        "project_extension": {"revision": 1}
    }
    assert first.plan_sha256 != second.plan_sha256


def test_dag_plan_export_writes_hash_bound_nonexecuting_artifact(tmp_path: Path) -> None:
    source = tmp_path / "generic-dag.json"
    source.write_text(json.dumps(_generic_payload(tmp_path)), encoding="utf-8")
    output = tmp_path / "compiled" / "plan.json"

    receipt = write_dag_plan(source, output_path=output)

    exported = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    assert receipt["provider_live"] is False
    assert receipt["plan_sha256"] == exported["plan_sha256"]
    assert receipt["node_count"] == 3
    assert "No DAG node or provider was dispatched" in receipt["proof_scope"]["proves"][2]


def test_dag_plan_export_refuses_to_overwrite_source(tmp_path: Path) -> None:
    source = tmp_path / "generic-dag.json"
    source.write_text(json.dumps(_generic_payload(tmp_path)), encoding="utf-8")

    with pytest.raises(RuntimeError, match="must not overwrite the source"):
        write_dag_plan(source, output_path=source)


def test_generic_plan_hash_is_portable_across_checkout_roots(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _portable_generic_payload()
    second = _portable_generic_payload()

    first_plan = compile_generic_dag_plan(first, source_path=first_root / "dag.json")
    second_plan = compile_generic_dag_plan(second, source_path=second_root / "dag.json")

    assert first_plan.plan_sha256 == second_plan.plan_sha256
    assert "/tmp/" not in json.dumps(first_plan.to_payload())


def test_export_mutation_does_not_mutate_frozen_plan(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _portable_generic_payload(), source_path=tmp_path / "dag.json"
    )
    exported = plan.to_payload()
    exported["goal_binding"]["kind"] = "tampered"

    assert plan.to_payload()["goal_binding"]["kind"] == "none"


def _project_payload(tmp_path: Path) -> dict[str, object]:
    spec_path = tmp_path / "specs" / "router.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text('{"schema":"tau.agent_command_spec.v1"}\n', encoding="utf-8")
    condition_a = {
        "schema": "tau.route_condition.v1",
        "op": "eq",
        "field": "route",
        "value": "A",
    }
    condition_b = {**condition_a, "value": "B"}
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "dag-plan-project",
        "goal": {
            "goal_id": "dag-plan-project",
            "goal_version": 1,
            "goal_hash": "sha256:dag-plan-project",
        },
        "target": {"repo": "grahama1970/tau", "target": "issue#77"},
        "entry_node": "router",
        "terminal_nodes": ["human"],
        "limits": {"max_total_attempts": 4, "default_timeout_seconds": 30},
        "security_mode": "secure",
        "policy_profile": {"schema": "tau.policy_profile.v1", "profile_id": "test"},
        "data_boundary": {"schema": "tau.data_boundary.v1", "classification": "internal"},
        "nodes": [
            {
                "id": "router",
                "agent": "router",
                "executor": "local",
                "command_spec": "specs/router.json",
                "route": {"mode": "exclusive"},
                "required_evidence": ["review_receipt"],
            },
            {"id": "branch-a", "agent": "coder-a", "executor": "local"},
            {"id": "branch-b", "agent": "coder-b", "executor": "local"},
            {
                "id": "join",
                "agent": "join",
                "executor": "local",
                "join": {
                    "schema": "tau.dag_join_policy.v1",
                    "policy": "all_terminal",
                    "timeout_seconds": 30,
                },
            },
        ],
        "edges": [
            {"from": "router", "to": "branch-a", "condition": condition_a},
            {"from": "router", "to": "branch-b", "condition": condition_b},
            {"from": "branch-a", "to": "join"},
            {"from": "branch-b", "to": "join"},
            {"from": "join", "to": "human"},
        ],
        "required_evidence": ["review_receipt"],
        "fail_closed_on": ["unexpected_node", "unexpected_edge"],
    }


def _portable_generic_payload() -> dict[str, object]:
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "portable",
        "run_dir": "run",
        "nodes": [
            {
                "node_id": "worker",
                "command": ["python", "-c", "pass"],
                "receipt_path": "receipts/worker.json",
            }
        ],
    }


def _generic_payload(tmp_path: Path) -> dict[str, object]:
    def node(
        node_id: str,
        *,
        depends_on: list[str],
        accepted_context_from: list[str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "node_id": node_id,
            "role": node_id,
            "command": ["python", "-c", "pass"],
            "depends_on": depends_on,
            "receipt_path": str(tmp_path / f"{node_id}.json"),
            "timeout_seconds": 10,
            "max_attempts": 2,
        }
        if accepted_context_from is not None:
            payload["accepted_context_from"] = accepted_context_from
        return payload

    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "dag-plan-generic",
        "run_dir": str(tmp_path),
        "goal_hash": "sha256:dag-plan-generic",
        "nodes": [
            node("planner", depends_on=[]),
            node("coder", depends_on=["planner"]),
            node(
                "reviewer",
                depends_on=["planner", "coder"],
                accepted_context_from=["coder"],
            ),
        ],
    }
