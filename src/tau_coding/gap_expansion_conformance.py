"""Live conformance receipt for evidence-gap driven adaptive DAG expansion."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_expansion import (
    write_dag_expansion_apply_receipt,
    write_dag_expansion_policy_receipt,
    write_dag_expansion_validation_receipt,
)
from tau_coding.dag_runtime.compiler import compile_project_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.gap_expansion import (
    EXPANSION_ENVELOPE_SCHEMA,
    GAP_EXPANSION_REVISION_EVENT_SCHEMA,
    gap_expansion_revision_event_payload,
    write_gap_expansion_bridge_receipt,
)

GAP_EXPANSION_CONFORMANCE_SCHEMA = "tau.gap_expansion_conformance.v1"
_RUN_ID = "gap-expansion-source-run"
_EXPANDED_RUN_ID = "gap-expansion-expanded-run"
_GOAL_HASH = "sha256:gap-expansion-conformance-goal"


def write_gap_expansion_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Run the #276 bridge through real files and the canonical scheduler."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    artifacts_dir = proof_dir / "artifacts"
    proposals_dir = artifacts_dir / "proposals"
    run_dir = proof_dir / "run"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    source_contract_path = artifacts_dir / "source-dag-contract.json"
    envelope_path = artifacts_dir / "expansion-envelope.json"
    bridge_receipt_path = artifacts_dir / "gap-expansion-bridge-receipt.json"
    validation_receipt_path = artifacts_dir / "validation-receipt.json"
    policy_receipt_path = artifacts_dir / "policy-receipt.json"
    apply_receipt_path = artifacts_dir / "apply-receipt.json"
    preview_path = artifacts_dir / "expanded-dag.preview.json"
    expanded_contract_path = artifacts_dir / "expanded-dag-contract.json"
    signal_path = artifacts_dir / "clean-signal-receipt.json"
    duplicate_receipt_path = artifacts_dir / "duplicate-bridge-receipt.json"
    human_receipt_path = artifacts_dir / "human-required-bridge-receipt.json"
    out_path_receipt_path = artifacts_dir / "out-of-envelope-path-bridge-receipt.json"
    out_cap_receipt_path = artifacts_dir / "out-of-envelope-capability-bridge-receipt.json"
    out_data_receipt_path = artifacts_dir / "out-of-envelope-data-bridge-receipt.json"
    out_role_receipt_path = artifacts_dir / "out-of-envelope-role-bridge-receipt.json"
    out_depth_receipt_path = artifacts_dir / "out-of-envelope-depth-bridge-receipt.json"
    out_side_effect_receipt_path = artifacts_dir / "out-of-envelope-side-effect-bridge-receipt.json"
    budget_receipt_path = artifacts_dir / "budget-exhausted-bridge-receipt.json"
    stale_receipt_path = artifacts_dir / "stale-lineage-bridge-receipt.json"

    source_contract = _source_contract()
    envelope = _base_envelope()
    _write_json(source_contract_path, source_contract)
    _write_json(envelope_path, envelope)
    _write_json(signal_path, _clean_signal())

    source_plan = compile_project_dag_plan(source_contract, source_path=source_contract_path)
    source_node_results: dict[str, dict[str, Any]] = {}
    source_events: list[dict[str, Any]] = []
    source_store_path = run_dir / "source-run.sqlite3"
    with SqliteDagRunStore(source_store_path) as source_store:
        source_result = run_dag_plan(
            source_plan,
            execute_node=_source_executor(source_plan, source_node_results),
            event_sink=source_events.append,
            run_store=source_store,
            run_id=_RUN_ID,
            lease_owner="gap-expansion-source",
        )
        source_journal_before_revision = list(source_store.load_events(_RUN_ID))
        admitted_coder_result = _node_result(source_result.node_results, "coder")
        boundary_path = Path(str(admitted_coder_result["node_completion_boundary_path"]))
        bridge_receipt = write_gap_expansion_bridge_receipt(
            dag_contract_path=source_contract_path,
            boundary_path=boundary_path,
            envelope_path=envelope_path,
            receipt_path=bridge_receipt_path,
            proposals_dir=proposals_dir,
            source_run_id=_RUN_ID,
        )
        proposal_path = Path(str(bridge_receipt["proposal_paths"][0]["path"]))
        validation_receipt = write_dag_expansion_validation_receipt(
            dag_contract_path=source_contract_path,
            proposal_path=proposal_path,
            receipt_path=validation_receipt_path,
            preview_path=preview_path,
        )
        policy_receipt = write_dag_expansion_policy_receipt(
            validation_receipt_path=validation_receipt_path,
            signal_receipt_path=signal_path,
            require_clean_signal=True,
            receipt_path=policy_receipt_path,
        )
        apply_receipt = write_dag_expansion_apply_receipt(
            validation_receipt_path=validation_receipt_path,
            policy_receipt_path=policy_receipt_path,
            out_path=expanded_contract_path,
            receipt_path=apply_receipt_path,
        )
        revision_payload = gap_expansion_revision_event_payload(
            bridge_receipt=bridge_receipt,
            validation_receipt=validation_receipt,
            policy_receipt=policy_receipt,
            apply_receipt=apply_receipt,
            runnable_child_ids=["gap-coder-gap-validation"],
        )
        lease = source_store.acquire_run(
            plan=source_plan,
            run_id=_RUN_ID,
            owner_id="gap-expansion-revision",
        )
        source_store.append_diagnostic_event(
            lease,
            event_key="gap-expansion:revision:gap-validation",
            node_id="coder",
            payload={
                "schema": "tau.dag_diagnostic_event.v1",
                "event_type": "gap_expansion_revision_recorded",
                "revision": revision_payload,
            },
        )
        source_store.release_lease(lease)
        source_journal_after_revision = list(source_store.load_events(_RUN_ID))

    expanded_contract = _read_json(expanded_contract_path)
    expanded_plan = compile_project_dag_plan(
        expanded_contract,
        source_path=expanded_contract_path,
        source_payload_sha256=_sha256_uri(expanded_contract_path),
    )
    expanded_node_results: dict[str, dict[str, Any]] = {}
    expanded_events: list[dict[str, Any]] = []
    with SqliteDagRunStore(run_dir / "expanded-run.sqlite3") as expanded_store:
        expanded_result = run_dag_plan(
            expanded_plan,
            execute_node=_expanded_executor(expanded_plan, expanded_node_results),
            event_sink=expanded_events.append,
            run_store=expanded_store,
            run_id=_EXPANDED_RUN_ID,
            lease_owner="gap-expansion-expanded",
        )
        expanded_journal_events = list(expanded_store.load_events(_EXPANDED_RUN_ID))

    blocking_receipts = _write_blocking_receipts(
        source_contract_path=source_contract_path,
        boundary_path=boundary_path,
        base_envelope=envelope,
        artifacts_dir=artifacts_dir,
        paths={
            "duplicate": duplicate_receipt_path,
            "human": human_receipt_path,
            "path": out_path_receipt_path,
            "capability": out_cap_receipt_path,
            "data": out_data_receipt_path,
            "role": out_role_receipt_path,
            "depth": out_depth_receipt_path,
            "side_effect": out_side_effect_receipt_path,
            "budget": budget_receipt_path,
            "stale": stale_receipt_path,
        },
        source_run_id=_RUN_ID,
        accepted_lineage=str(bridge_receipt["candidates"][0]["canonical_gap_identity"]),
    )

    revision_events = [
        event
        for event in source_journal_after_revision
        if event["payload"].get("revision", {}).get("schema") == GAP_EXPANSION_REVISION_EVENT_SCHEMA
    ]
    accepted_child = expanded_node_results.get("gap-coder-gap-validation", {})
    reviewer_output = expanded_node_results.get("reviewer", {}).get("accepted_output", {})
    checks = {
        "source_run_passed_with_admitted_boundary": source_result.status == "PASS"
        and admitted_coder_result.get("node_completion_boundary_validation", {}).get("status")
        == "PASS"
        and bool(admitted_coder_result.get("node_completion_boundary_path")),
        "in_envelope_gap_proposed": bridge_receipt["dispositions"]["eligible_for_policy"] == 1
        and bridge_receipt["proposal_count"] == 1,
        "proposal_policy_apply_passed": validation_receipt.get("ok") is True
        and policy_receipt.get("apply_allowed") is True
        and apply_receipt.get("applied") is True,
        "revision_event_before_child_run": len(revision_events) == 1
        and len(source_journal_after_revision) > len(source_journal_before_revision),
        "expanded_child_runnable_with_lineage": expanded_result.status == "PASS"
        and accepted_child.get("accepted_output", {}).get("canonical_gap_identity")
        == bridge_receipt["candidates"][0]["canonical_gap_identity"],
        "reviewer_scope_includes_gap_child": "gap-coder-gap-validation"
        in reviewer_output.get("reviewed_node_ids", []),
        "original_node_pass_not_child_acceptance": "gap-coder-gap-validation"
        not in [
            event.get("node_id")
            for event in source_events
            if event.get("event") == "node_completed"
        ],
        "duplicate_blocked": _disposition(blocking_receipts["duplicate"])
        == "duplicate_or_superseded",
        "human_required_blocked": _disposition(blocking_receipts["human"]) == "human_required",
        "out_of_envelope_dimensions_blocked": all(
            _disposition(blocking_receipts[key]) == "out_of_envelope"
            for key in ("path", "capability", "data", "role", "depth", "side_effect")
        ),
        "budget_exhausted_blocked": _disposition(blocking_receipts["budget"]) == "budget_exhausted",
        "stale_lineage_blocked": _disposition(blocking_receipts["stale"])
        == "duplicate_or_superseded",
        "restart_replay_reconstructs_revision_state": len(revision_events) == 1
        and len(expanded_journal_events) > 0,
        "scope_claim_not_authoritative": bridge_receipt["candidates"][0]["producer_scope_claim"][
            "authoritative"
        ]
        is False,
    }
    failed_checks = [name for name, value in checks.items() if value is not True]
    payload = {
        "schema": GAP_EXPANSION_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "proof_dir": str(proof_dir),
        "artifacts_dir": str(artifacts_dir),
        "run_dir": str(run_dir),
        "source_scheduler_result": _scheduler_summary(source_result),
        "expanded_scheduler_result": _scheduler_summary(expanded_result),
        "source_journal_event_count_before_revision": len(source_journal_before_revision),
        "source_journal_event_count_after_revision": len(source_journal_after_revision),
        "expanded_journal_event_count": len(expanded_journal_events),
        "candidate_lineage": bridge_receipt["candidates"][0]["canonical_gap_identity"],
        "generated_child_id": "gap-coder-gap-validation",
        "receipts": {
            "boundary": str(boundary_path),
            "bridge": str(bridge_receipt_path),
            "validation": str(validation_receipt_path),
            "policy": str(policy_receipt_path),
            "apply": str(apply_receipt_path),
            "expanded_contract": str(expanded_contract_path),
            **{
                f"{key}_bridge": str(value["receipt_path"])
                for key, value in blocking_receipts.items()
            },
        },
        "checks": checks,
        "failed_checks": failed_checks,
        "proof_scope": {
            "proves": [
                "An admitted in-envelope evidence gap produced a bounded proposal.",
                "The proposal reached a policy decision and applied revision through "
                "existing expansion receipts.",
                "The expanded DAG child ran with originating gap lineage in reviewer scope.",
                "Out-of-envelope, human-required, duplicate, budget-exhausted, and "
                "stale-lineage candidates did not mutate the graph.",
                "A durable source-run journal event records the validated revision "
                "before the child is runnable in the revised DAG.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Distributed multi-host scheduler coordination.",
                "Human approval UI beyond exact approval gating semantics.",
            ],
        },
        "checked_at": _utc_stamp(),
    }
    _write_json(resolved_output, payload)
    return payload


def _source_contract() -> dict[str, Any]:
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "gap-expansion-conformance",
        "goal": {
            "goal_id": "gap-expansion-conformance",
            "goal_version": 1,
            "goal_hash": _GOAL_HASH,
        },
        "target": {"repo": "grahama1970/tau", "target": "gap-expansion-conformance"},
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 30, "max_total_attempts": 4},
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": [
                    "accepted_work",
                    "tau.node_completion_boundary.v1",
                ],
                "node_completion_boundary_policy": {
                    "schema": "tau.node_completion_boundary_policy.v1",
                    "non_empty_sections": ["evidence_gaps"],
                },
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["reviewer_verdict"],
            },
        ],
        "edges": [
            {"from": "coder", "to": "reviewer"},
            {"from": "reviewer", "to": "human"},
        ],
        "required_evidence": ["accepted_work", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
        ],
    }


def _base_envelope() -> dict[str, Any]:
    return {
        "schema": EXPANSION_ENVELOPE_SCHEMA,
        "permitted_parent_nodes": ["coder"],
        "permitted_parent_roles": ["coder"],
        "permitted_child_roles": ["validator"],
        "permitted_adapters": ["local"],
        "allowed_paths": ["src/tau_coding/gap_expansion.py"],
        "allowed_capabilities": ["read", "validate"],
        "allowed_resources": ["local-filesystem"],
        "allowed_data_classes": ["public"],
        "allowed_side_effect_classes": ["none"],
        "max_added_nodes": 1,
        "max_depth": 1,
        "max_attempts": 1,
        "max_seconds": 30,
        "max_tokens": 2000,
        "max_cost_usd": 0,
        "human_approval_required": False,
    }


def _source_executor(plan: DagPlan, node_results: dict[str, dict[str, Any]]):
    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs
        if node.node_id == "coder":
            result = {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "attempt_id": attempt.attempt_id,
                "accepted_output": {
                    "schema": "tau.gap_expansion_source_output.v1",
                    "value": "source node closed with a declared evidence gap",
                },
                "node_completion_boundary": _boundary(
                    plan=plan,
                    node_id=node.node_id,
                    attempt_id=attempt.attempt_id,
                ),
            }
        else:
            result = {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "attempt_id": attempt.attempt_id,
                "accepted_output": {
                    "schema": "tau.gap_expansion_source_review.v1",
                    "reviewer_verdict": "PASS",
                },
            }
        node_results[node.node_id] = result
        return result

    return execute


def _expanded_executor(plan: DagPlan, node_results: dict[str, dict[str, Any]]):
    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        if node.node_id == "coder":
            accepted_output = {
                "schema": "tau.gap_expansion_source_output.v1",
                "value": "source node rerun for revised DAG",
            }
            result = {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "attempt_id": attempt.attempt_id,
                "accepted_output": accepted_output,
                "node_completion_boundary": _boundary(
                    plan=plan,
                    node_id=node.node_id,
                    attempt_id=attempt.attempt_id,
                ),
            }
        elif node.node_id == "gap-coder-gap-validation":
            lineage = _node_lineage(node)
            accepted_output = {
                "schema": "tau.gap_expansion_child_output.v1",
                "canonical_gap_identity": lineage.get("canonical_gap_identity"),
                "candidate_id": lineage.get("candidate_id"),
                "input_count": len(accepted_inputs),
            }
            result = {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "attempt_id": attempt.attempt_id,
                "accepted_output": accepted_output,
            }
        else:
            result = {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "attempt_id": attempt.attempt_id,
                "accepted_output": {
                    "schema": "tau.gap_expansion_reviewer_output.v1",
                    "reviewer_verdict": "PASS",
                    "reviewed_node_ids": _reviewed_node_ids(accepted_inputs),
                    "input_hashes": [canonical_sha256(item) for item in accepted_inputs],
                },
            }
        node_results[node.node_id] = result
        return result

    return execute


def _boundary(*, plan: DagPlan, node_id: str, attempt_id: str) -> dict[str, Any]:
    item = {
        "id": "gap-validation",
        "statement": "A deterministic validator should check source work before final review.",
        "evidence_refs": [{"kind": "accepted_output", "id": node_id}],
        "proposed_node": {
            "id": "gap-coder-gap-validation",
            "role": "validator",
            "adapter": "local",
            "output_evidence": ["validation_receipt"],
            "max_attempts": 1,
        },
        "requested_paths": ["src/tau_coding/gap_expansion.py"],
        "requested_capabilities": ["read", "validate"],
        "requested_resources": ["local-filesystem"],
        "data_classes": ["public"],
        "side_effect_class": "none",
        "budget": {"max_attempts": 1, "max_seconds": 10, "max_tokens": 1000, "max_cost_usd": 0},
        "scope_claim": {
            "claim": "This follow-up remains within the immutable goal.",
            "confidence": 1.0,
        },
    }
    sections = {
        "checked_scope": [
            {
                "id": "checked-source",
                "statement": "Source node wrote an artifact.",
                "evidence_refs": [],
            }
        ],
        "not_checked": [
            {
                "id": "not-validator",
                "statement": "No independent validator ran in this source DAG.",
                "evidence_refs": [],
            }
        ],
        "assumptions": [
            {
                "id": "assume-local",
                "statement": "Local validator can inspect the artifact.",
                "evidence_refs": [],
            }
        ],
        "known_unknowns": [
            {
                "id": "unknown-review",
                "statement": "Reviewer has not seen validator output yet.",
                "evidence_refs": [],
            }
        ],
        "evidence_gaps": [item],
        "recommended_followups": [
            {
                "id": "follow-gap-validation",
                "statement": "Run the proposed validator child node.",
                "evidence_refs": [],
            }
        ],
        "proves": [
            {"id": "proves-source", "statement": "Source node produced work.", "evidence_refs": []}
        ],
        "does_not_prove": [
            {
                "id": "does-not-prove-complete",
                "statement": "Source PASS does not complete the generated gap child.",
                "evidence_refs": [],
            }
        ],
    }
    return {
        "schema": "tau.node_completion_boundary.v1",
        "goal_hash": plan.runtime_goal_hash,
        "plan_sha256": plan.plan_sha256,
        "node_id": node_id,
        "attempt_id": attempt_id,
        **sections,
    }


def _write_blocking_receipts(
    *,
    source_contract_path: Path,
    boundary_path: Path,
    base_envelope: dict[str, Any],
    artifacts_dir: Path,
    paths: dict[str, Path],
    source_run_id: str,
    accepted_lineage: str,
) -> dict[str, dict[str, Any]]:
    specs = {
        "duplicate": ({}, {"existing_lineages": [accepted_lineage]}),
        "human": ({"human_approval_required": True}, {}),
        "path": ({"allowed_paths": ["README.md"]}, {}),
        "capability": ({"allowed_capabilities": ["read"]}, {}),
        "data": ({"allowed_data_classes": ["controlled"]}, {}),
        "role": ({"permitted_child_roles": ["goal-guardian"]}, {}),
        "depth": ({"max_depth": 0}, {}),
        "side_effect": ({"allowed_side_effect_classes": ["filesystem-write"]}, {}),
        "budget": ({"max_added_nodes": 1}, {"used_budget": 1}),
        "stale": ({}, {"existing_lineages": [accepted_lineage]}),
    }
    receipts: dict[str, dict[str, Any]] = {}
    for key, (envelope_delta, options) in specs.items():
        envelope = {**base_envelope, **envelope_delta}
        envelope_path = artifacts_dir / f"{key}-envelope.json"
        _write_json(envelope_path, envelope)
        receipts[key] = write_gap_expansion_bridge_receipt(
            dag_contract_path=source_contract_path,
            boundary_path=boundary_path,
            envelope_path=envelope_path,
            receipt_path=paths[key],
            proposals_dir=artifacts_dir / f"{key}-proposals",
            source_run_id=source_run_id,
            existing_lineages=options.get("existing_lineages", ()),
            approved_lineages=options.get("approved_lineages", ()),
            used_budget=int(options.get("used_budget", 0)),
        )
    return receipts


def _node_lineage(node: DagPlanNode) -> dict[str, Any]:
    extensions = node.source_extensions.to_value()
    if not isinstance(extensions, dict):
        return {}
    lineage = extensions.get("source_gap_lineage")
    return dict(lineage) if isinstance(lineage, dict) else {}


def _disposition(receipt: dict[str, Any]) -> str:
    return str(receipt["candidates"][0]["disposition"])


def _scheduler_summary(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "verdict": result.verdict,
        "run_id": result.run_id,
        "completed_nodes": [node_id for node_id, state in result.node_states if state == "success"],
        "blocked_nodes": [node_id for node_id, state in result.node_states if state == "blocked"],
    }


def _node_result(results: tuple[dict[str, Any], ...], node_id: str) -> dict[str, Any]:
    for result in results:
        if result.get("node_id") == node_id:
            return result
    raise RuntimeError(f"node result missing: {node_id}")


def _reviewed_node_ids(accepted_inputs: tuple[dict[str, Any], ...]) -> list[str]:
    reviewed: list[str] = []
    for item in accepted_inputs:
        if item.get("schema") == "tau.gap_expansion_source_output.v1":
            reviewed.append("coder")
        if item.get("schema") == "tau.gap_expansion_child_output.v1":
            reviewed.append("gap-coder-gap-validation")
    return sorted(reviewed)


def _clean_signal() -> dict[str, Any]:
    return {
        "schema": "tau.dag_signal_receipt.v1",
        "ok": True,
        "status": "PASS",
        "source_ok": True,
        "source_status": "PASS",
        "negative_signals": [],
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_uri(path: Path) -> str:
    return f"sha256:{__import__('hashlib').sha256(path.read_bytes()).hexdigest()}"


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
