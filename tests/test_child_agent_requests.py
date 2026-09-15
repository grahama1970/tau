from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tau_coding.child_agent_requests import (
    CHILD_AGENT_REQUEST_SCHEMA,
    ChildAgentRegistry,
    ChildAgentRequestError,
    build_child_agent_projection,
    child_cancel_operator_action,
    child_instruction_operator_action,
    child_terminal_admission_errors,
    normalize_child_agent_request,
)


def _request(request_id: str = "req-alpha", *, fanout_index: int = 0) -> dict[str, object]:
    return {
        "schema": CHILD_AGENT_REQUEST_SCHEMA,
        "request_id": request_id,
        "idempotency_key": request_id,
        "parent": {
            "run_id": "parent-run",
            "node_id": "parent-node",
            "depth": 0,
            "goal_hash": "sha256:issue316",
        },
        "role": "scout",
        "task": {
            "summary": "inspect one bounded source path",
            "prompt": "Read one file and report one finding.",
        },
        "budgets": {"max_depth": 1, "max_turns": 1, "timeout_seconds": 30},
        "requested": {
            "tools": ["read"],
            "paths": ["src/tau_coding"],
            "skills": ["tau"],
            "data_classes": ["public"],
            "models": ["local-test"],
        },
        "policy": {
            "allowed_tools": ["read"],
            "allowed_paths": ["src/tau_coding"],
            "allowed_skills": ["tau"],
            "allowed_data_classes": ["public"],
            "allowed_models": ["local-test"],
            "allow_network": False,
            "require_receipt": True,
        },
        "join": {
            "join_id": "parent-join",
            "policy": "all_pass",
            "output_schema": "tau.child_agent_test_output.v1",
            "required_evidence": [
                {"json_pointer": "/accepted_output/message", "equals": "child dag executed"}
            ],
        },
        "fanout_index": fanout_index,
    }


def test_child_agent_request_admission_is_byte_idempotent_and_compiles_bounded_dag(
    tmp_path: Path,
) -> None:
    registry = ChildAgentRegistry(parent_run_id="parent-run", max_children=2)

    first = registry.admit(_request(), run_root=tmp_path)
    duplicate = registry.admit(_request(), run_root=tmp_path)

    assert duplicate == first
    spec = json.loads(Path(first.dag_spec_path).read_text(encoding="utf-8"))
    assert spec["schema"] == "tau.generic_dag_spec.v1"
    assert spec["run_id"] == first.child_run_id
    assert spec["nodes"][0]["timeout_seconds"] == 30
    child_agent = spec["nodes"][0]["extensions"]["tau_child_agent"]
    assert child_agent["handle_id"] == first.handle_id
    assert child_agent["max_turns"] == 1
    assert child_agent["allowed_tools"] == ["read"]
    assert child_agent["requested"]["models"] == ["local-test"]
    assert registry.to_payload()["child_count"] == 1


def test_child_agent_request_rejects_depth_fanout_and_idempotency_conflicts(tmp_path: Path) -> None:
    registry = ChildAgentRegistry(parent_run_id="parent-run", max_children=1)
    registry.admit(_request(), run_root=tmp_path)

    changed = _request()
    changed["task"] = {"summary": "different", "prompt": "different prompt"}
    with pytest.raises(ChildAgentRequestError) as conflict:
        registry.admit(changed, run_root=tmp_path)
    assert conflict.value.code == "child_agent_idempotency_conflict"

    depth_exceeded = _request("req-depth")
    depth_exceeded["parent"] = {
        "run_id": "parent-run",
        "node_id": "parent-node",
        "depth": 1,
        "goal_hash": "sha256:issue316",
    }
    with pytest.raises(ChildAgentRequestError) as depth:
        normalize_child_agent_request(depth_exceeded, parent_run_id="parent-run")
    assert depth.value.code == "child_agent_depth_exceeded"

    with pytest.raises(ChildAgentRequestError) as fanout:
        registry.admit(_request("req-bravo", fanout_index=1), run_root=tmp_path)
    assert fanout.value.code == "child_agent_fanout_index_exceeded"

    bad_tool = _request("req-tool")
    bad_tool["requested"] = {"tools": ["write"], "paths": ["src/tau_coding"]}
    with pytest.raises(ChildAgentRequestError) as tool:
        normalize_child_agent_request(bad_tool, parent_run_id="parent-run")
    assert tool.value.code == "child_agent_tool_not_allowed"

    bad_path = _request("req-path")
    bad_path["requested"] = {"paths": ["/etc"]}
    with pytest.raises(ChildAgentRequestError) as path_error:
        normalize_child_agent_request(bad_path, parent_run_id="parent-run")
    assert path_error.value.code == "child_agent_path_not_allowed"

    bad_skill = _request("req-skill")
    bad_skill["requested"] = {"skills": ["unsafe-skill"]}
    with pytest.raises(ChildAgentRequestError) as skill:
        normalize_child_agent_request(bad_skill, parent_run_id="parent-run")
    assert skill.value.code == "child_agent_skill_not_allowed"

    bad_data = _request("req-data")
    bad_data["requested"] = {"data_classes": ["secret"]}
    with pytest.raises(ChildAgentRequestError) as data:
        normalize_child_agent_request(bad_data, parent_run_id="parent-run")
    assert data.value.code == "child_agent_data_classification_not_allowed"

    bad_model = _request("req-model")
    bad_model["requested"] = {"models": ["unapproved-model"]}
    with pytest.raises(ChildAgentRequestError) as model:
        normalize_child_agent_request(bad_model, parent_run_id="parent-run")
    assert model.value.code == "child_agent_model_not_allowed"

    bad_model_capability = _request("req-model-capability")
    bad_model_capability["requested"] = {"model_capabilities": ["vision"]}
    with pytest.raises(ChildAgentRequestError) as model_capability:
        normalize_child_agent_request(bad_model_capability, parent_run_id="parent-run")
    assert model_capability.value.code == "child_agent_model_capability_not_allowed"

    bad_side_effect = _request("req-side-effect")
    bad_side_effect["requested"] = {"side_effects": ["network.write"]}
    with pytest.raises(ChildAgentRequestError) as side_effect:
        normalize_child_agent_request(bad_side_effect, parent_run_id="parent-run")
    assert side_effect.value.code == "child_agent_side_effect_not_allowed"

    bad_sibling = _request("req-sibling")
    bad_sibling["requested"] = {"sibling_context_from": ["child-run-other"]}
    with pytest.raises(ChildAgentRequestError) as sibling:
        normalize_child_agent_request(bad_sibling, parent_run_id="parent-run")
    assert sibling.value.code == "child_agent_sibling_context_not_allowed"

    bad_attempts = _request("req-attempts")
    bad_attempts["budgets"] = {"max_depth": 1, "max_turns": 1, "max_attempts": 2}
    with pytest.raises(ChildAgentRequestError) as attempts:
        normalize_child_agent_request(bad_attempts, parent_run_id="parent-run")
    assert attempts.value.code == "child_agent_attempt_budget_exceeded"

    bad_tokens = _request("req-tokens")
    bad_tokens["parent"] = {
        **bad_tokens["parent"],
        "cumulative_tokens": 9,
        "max_cumulative_tokens": 10,
    }
    bad_tokens["budgets"] = {"max_depth": 1, "max_tokens": 2}
    with pytest.raises(ChildAgentRequestError) as tokens:
        normalize_child_agent_request(bad_tokens, parent_run_id="parent-run")
    assert tokens.value.code == "child_agent_cumulative_token_budget_exceeded"

    bad_cost = _request("req-cost")
    bad_cost["parent"] = {
        **bad_cost["parent"],
        "cumulative_cost_usd": 1.0,
        "max_cumulative_cost_usd": 1.5,
    }
    bad_cost["budgets"] = {"max_depth": 1, "max_cost_usd": 1.0}
    with pytest.raises(ChildAgentRequestError) as cost:
        normalize_child_agent_request(bad_cost, parent_run_id="parent-run")
    assert cost.value.code == "child_agent_cumulative_cost_budget_exceeded"

    bad_time = _request("req-time")
    bad_time["parent"] = {**bad_time["parent"], "elapsed_seconds": 9, "max_cumulative_seconds": 10}
    bad_time["budgets"] = {"max_depth": 1, "timeout_seconds": 2}
    with pytest.raises(ChildAgentRequestError) as time:
        normalize_child_agent_request(bad_time, parent_run_id="parent-run")
    assert time.value.code == "child_agent_cumulative_time_budget_exceeded"


def test_child_agent_compiled_dag_executes_and_registry_reads_pass_receipt(tmp_path: Path) -> None:
    registry = ChildAgentRegistry(parent_run_id="parent-run", max_children=1)
    handle = registry.admit(_request(), run_root=tmp_path)

    env = dict(os.environ)
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = (
        src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    )
    completed = subprocess.run(
        ["uv", "run", "tau", "dag-run", handle.dag_spec_path, "--no-resume"],
        check=False,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        timeout=90,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(Path(handle.result_receipt_path).read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    registry.record_terminal(handle.handle_id, receipt=receipt)
    accepted = registry.accepted_results()
    assert accepted[0]["handle_id"] == handle.handle_id
    assert accepted[0]["output"]["message"] == "child dag executed"


def test_child_agent_result_admission_requires_declared_evidence_and_projects_relationships(
    tmp_path: Path,
) -> None:
    registry = ChildAgentRegistry(parent_run_id="parent-run", max_children=1)
    handle = registry.admit(_request(), run_root=tmp_path)
    receipt_path = Path(handle.result_receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    bare_pass = {
        "status": "PASS",
        "verdict": "PASS",
        "accepted_output": {"message": "child dag executed"},
    }
    receipt_path.write_text(json.dumps(bare_pass), encoding="utf-8")

    assert "child_agent_output_schema_mismatch" in child_terminal_admission_errors(
        handle, bare_pass
    )
    assert registry.accepted_results() == []
    projection = build_child_agent_projection(registry)
    assert projection["clients_authoritative"] is False
    assert projection["viewer"]["edges"][0] == {
        "source": "parent-run",
        "target": handle.child_run_id,
        "kind": "child_agent",
        "handle_id": handle.handle_id,
    }
    assert projection["viewer"]["terminal_contributions"][0]["state"] == "REJECTED"
    assert projection["herdr"]["child_agents"][0]["terminal_contribution_state"] == "REJECTED"


def test_child_agent_operator_actions_are_agent_actionable_not_human_blockers(
    tmp_path: Path,
) -> None:
    registry = ChildAgentRegistry(parent_run_id="parent-run", max_children=1)
    handle = registry.admit(_request(), run_root=tmp_path)

    instruction = child_instruction_operator_action(
        handle,
        action_request_id="action-instruction-1",
        instruction="Continue with the smallest safe check.",
        journal_seq=7,
        journal_head_sha256="sha256:journal",
    )
    cancel = child_cancel_operator_action(
        handle,
        action_request_id="action-cancel-1",
        reason="stale child",
        journal_seq=8,
        journal_head_sha256="sha256:journal2",
    )

    assert instruction["schema"] == "tau.operator_action_request.v1"
    assert instruction["requires_human_input"] is False
    assert instruction["authorized_agent_next_steps"] == ["add_next_turn_instruction"]
    assert instruction["target"]["handle_id"] == handle.handle_id
    assert cancel["action"] == "cancel"
    assert cancel["requires_human_input"] is False
