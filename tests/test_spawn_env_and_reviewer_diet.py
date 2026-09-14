"""Tests for the deny-by-default spawn env and the reviewer information diet."""

from __future__ import annotations

from tau_coding.dag_runtime.reviewer_information_diet import (
    REVIEWER_INPUT_DIET_CODE,
    is_reviewer_role,
    reviewer_diet_violation,
)
from tau_coding.dag_runtime.spawn_env import (
    BASE_SPAWN_ENV_VARS,
    build_governed_spawn_env,
)

_HOST = {
    "PATH": "/usr/bin",
    "HOME": "/home/t",
    "LANG": "en_US.UTF-8",
    "OPENAI_API_KEY": "sk-secret",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
    "GH_TOKEN": "gh-secret",
}


def test_spawn_env_denies_undeclared_secrets() -> None:
    spawn = build_governed_spawn_env(source=_HOST)
    assert spawn.env["PATH"] == "/usr/bin"
    assert spawn.env["HOME"] == "/home/t"
    for secret in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GH_TOKEN"):
        assert secret not in spawn.env
    assert set(spawn.env) <= set(BASE_SPAWN_ENV_VARS)
    assert spawn.missing_passthrough == ()
    assert spawn.diagnostic() is None


def test_spawn_env_passes_only_declared_variables() -> None:
    spawn = build_governed_spawn_env(["GH_TOKEN"], source=_HOST)
    assert spawn.env["GH_TOKEN"] == "gh-secret"
    assert "OPENAI_API_KEY" not in spawn.env


def test_spawn_env_names_missing_declared_variable() -> None:
    spawn = build_governed_spawn_env(["NOT_SET_ANYWHERE"], source=_HOST)
    assert spawn.missing_passthrough == ("NOT_SET_ANYWHERE",)
    diagnostic = spawn.diagnostic()
    assert diagnostic is not None
    assert "env_passthrough" in diagnostic
    assert "NOT_SET_ANYWHERE" in diagnostic


def test_reviewer_role_detection() -> None:
    assert is_reviewer_role("reviewer")
    assert is_reviewer_role("semantic-Review")
    assert not is_reviewer_role("creator")
    assert not is_reviewer_role(None)


def test_reviewer_diet_allows_diff_and_contract() -> None:
    inputs = (
        {"schema": "tau.code_patch.v1", "target_file": "src/app.py", "patch": "[]"},
        {"schema": "tau.acceptance_contract.v1", "criteria": ["x"]},
    )
    assert reviewer_diet_violation(inputs) is None


def test_reviewer_diet_blocks_workspace_and_transcript_even_nested() -> None:
    inputs = (
        {"artifact": {"meta": {"worktree_path": "/tmp/wt"}}},
    )
    violation = reviewer_diet_violation(inputs)
    assert violation == f"{REVIEWER_INPUT_DIET_CODE}:worktree_path"
    nested_list = ({"items": [{"producer_transcript": "..."}]},)
    assert reviewer_diet_violation(nested_list) == (
        f"{REVIEWER_INPUT_DIET_CODE}:producer_transcript"
    )


def test_reviewer_diet_enforced_through_input_manifest_resolution(tmp_path) -> None:
    from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
    from tau_coding.dag_runtime.node_input_manifest import resolve_node_input_manifest
    from tau_coding.dag_runtime.run_store import DagAttemptIdentity

    def _node(node_id: str, depends_on: list[str] | None = None) -> dict:
        return {
            "node_id": node_id,
            "role": node_id,
            "command": ["true"],
            "depends_on": depends_on or [],
            "accepted_context_from": depends_on or [],
            "receipt_path": str(tmp_path / f"{node_id}.json"),
            "timeout_seconds": 1,
            "max_attempts": 1,
        }

    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "diet-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [_node("creator"), _node("reviewer", ["creator"])],
        },
        source_path=tmp_path / "dag.json",
    )
    binding = plan.context_bindings[0]
    reviewer = next(node for node in plan.nodes if node.node_id == "reviewer")
    identity = DagAttemptIdentity(
        run_id="diet-test", node_id="reviewer", attempt=1,
        attempt_id="attempt-reviewer-1", idempotency_key="attempt-reviewer-1:effect",
    )

    def resolve(accepted_output: dict) -> object:
        return resolve_node_input_manifest(
            plan=plan, node=reviewer, identity=identity, bindings=(binding,),
            edge_states={binding.control_edge_id: "success"},
            results={"creator": {"accepted_output": accepted_output}},
        )

    contaminated = resolve(
        {"schema": "tau.code_patch.v1", "worktree_path": "/tmp/producer-wt"}
    )
    assert contaminated.blocked_result is not None
    assert contaminated.blocked_result["verdict"] == (
        "REVIEWER_INPUT_DIET_VIOLATION:worktree_path"
    )

    clean = resolve({"schema": "tau.code_patch.v1", "patch": "[]"})
    assert clean.blocked_result is None
    assert len(clean.accepted_inputs) == 1
