from __future__ import annotations

from pathlib import Path

from tau_coding.dag_runtime.compiler import (
    compile_project_dag_plan,
    compile_project_node_runtime_requirement,
)
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.ticket_repair_release import (
    RELEASE_REQUEST_SCHEMA,
    build_ticket_handoff_packet,
    release_policy_blockers,
)


def _handoff(tmp_path: Path) -> dict[str, object]:
    return build_ticket_handoff_packet(
        agent="coder",
        repo="grahama1970/tau",
        repo_path=tmp_path,
        worktree_path=tmp_path / "worktree",
        tau_location=tmp_path,
        issue_id="326",
        target_paths=("src/tau_coding/dag_runtime",),
        required_skills=("tau", "project-watchdog", "ticket", "handoff", "agentic-evals"),
        context_files=("GOAL.md", "src/tau_coding/dag_runtime"),
        proof_command=("uv", "run", "pytest"),
        depends_on=({"ref": "grahama1970/tau#325", "state": "closed"},),
        goal_hash=canonical_sha256({"issue": 326}),
        dag_id="tau-issue-326",
        node_id="creator",
    )


def test_ticket_handoff_context_carries_launch_fields(tmp_path: Path) -> None:
    packet = _handoff(tmp_path)
    context = packet["context"]

    assert context["schema"] == "tau.ticket_repair_handoff_context.v1"
    assert context["repo_path"] == str(tmp_path.resolve())
    assert context["cwd"] == str((tmp_path / "worktree").resolve())
    assert context["worktree_path"] == context["cwd"]
    assert context["tau_location"] == str(tmp_path.resolve())
    assert context["issue_id"] == "326"
    assert context["launch"]["automatic"] is True
    assert context["launch"]["human_terminal_selection_required"] is False


def test_release_policy_refuses_reviewer_and_dependency_gaps(tmp_path: Path) -> None:
    proof = tmp_path / "proof.json"
    proof.write_text('{"status":"PASS","ok":true}\n', encoding="utf-8")
    handoff = _handoff(tmp_path)
    handoff["context"]["depends_on"] = [{"ref": "grahama1970/tau#325", "state": "open"}]
    request = {
        "schema": RELEASE_REQUEST_SCHEMA,
        "actor_role": "reviewer",
        "repo": "grahama1970/tau",
        "repo_path": str(tmp_path),
        "source_worktree_path": str(tmp_path / "worktree"),
        "tau_location": str(tmp_path),
        "issue_id": "326",
        "handoff": handoff,
        "reviewer_verdict": "FAIL",
        "proof_gate_status": "PASS",
        "proof_artifact_path": str(proof),
        "push_authority": True,
        "readback_authority": True,
    }

    blockers = release_policy_blockers(request)

    assert "release_actor_not_releaser" in blockers
    assert "missing_reviewer_pass" in blockers
    assert "unsatisfied_depends_on" in blockers


def test_compiler_classifies_releaser_node_boundary() -> None:
    adapter_kind, requirement = compile_project_node_runtime_requirement(
        {"id": "releaser", "agent": "releaser", "executor": "local"},
        executor="local",
    )

    assert adapter_kind == "project_releaser"
    assert requirement.session_scope == "ticket_repair_release"


def test_project_dag_can_declare_releaser_terminal_node() -> None:
    plan = compile_project_dag_plan(
        {
            "schema": "tau.dag_contract.v1",
            "dag_id": "tau-issue-326-releaser-smoke",
            "goal": {
                "goal_id": "issue-326",
                "goal_version": 1,
                "goal_hash": "sha256:" + "1" * 64,
            },
            "target": {"repo": "grahama1970/tau", "target": "issue#326"},
            "entry_node": "creator",
            "terminal_nodes": ["releaser"],
            "limits": {"default_timeout_seconds": 30, "max_total_attempts": 4},
            "context": {},
            "nodes": [
                {
                    "id": "creator",
                    "agent": "coder",
                    "executor": "local",
                    "max_attempts": 1,
                    "required_evidence": ["creator_commit"],
                },
                {
                    "id": "reviewer",
                    "agent": "reviewer",
                    "executor": "local",
                    "max_attempts": 1,
                    "required_evidence": ["reviewer_verdict"],
                },
                {
                    "id": "proof-gate",
                    "agent": "proof-gate",
                    "executor": "local",
                    "max_attempts": 1,
                    "required_evidence": ["proof_artifact"],
                },
                {
                    "id": "releaser",
                    "agent": "releaser",
                    "executor": "local",
                    "max_attempts": 1,
                    "required_evidence": ["release_receipt"],
                },
            ],
            "edges": [
                {"from": "creator", "to": "reviewer"},
                {"from": "reviewer", "to": "proof-gate"},
                {"from": "proof-gate", "to": "releaser"},
            ],
            "required_evidence": ["reviewer_verdict", "proof_artifact", "release_receipt"],
            "fail_closed_on": ["malformed_handoff", "missing_required_evidence"],
        }
    )

    node = next(item for item in plan.nodes if item.node_id == "releaser")
    terminal = next(item for item in plan.terminal_endpoints if item.terminal_id == "releaser")
    assert node.adapter_kind == "project_releaser"
    assert terminal.kind == "declared_node"
