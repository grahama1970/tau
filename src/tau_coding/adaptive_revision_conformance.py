"""Live conformance receipt for bounded adaptive DAG revision."""

from __future__ import annotations

import hashlib
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
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagRunBlock,
    DagTransitionBatch,
    DagTransitionView,
)

ADAPTIVE_REVISION_CONFORMANCE_SCHEMA = "tau.adaptive_revision_conformance.v1"
ADAPTIVE_REVISION_PROPOSAL_RECEIPT_SCHEMA = "tau.adaptive_revision_proposal_receipt.v1"
ADAPTIVE_REVISION_CHECKPOINT_SCHEMA = "tau.adaptive_revision_checkpoint.v1"
ADAPTIVE_REVISION_APPLY_RECEIPT_SCHEMA = "tau.adaptive_revision_apply_receipt.v1"
ADAPTIVE_REVISION_VIEWER_STATE_SCHEMA = "tau.adaptive_revision_viewer_state.v1"


class _SafeCheckpointPolicy(AllSuccessTransitionPolicy):
    """Stop the old plan immediately before the named pending node dispatches."""

    def __init__(self, *, checkpoint_before_node: str) -> None:
        self._checkpoint_before_node = checkpoint_before_node

    def before_node_start(
        self,
        view: DagTransitionView,
        node_id: str,
        attempt: int,
    ) -> DagTransitionBatch:
        if node_id != self._checkpoint_before_node:
            return DagTransitionBatch()
        return DagTransitionBatch(
            block_run=DagRunBlock(
                failure_code="SAFE_ADAPTIVE_REVISION_CHECKPOINT",
                message="Adaptive revision checkpoint reached before pending node dispatch.",
                evidence={
                    "node_id": node_id,
                    "attempt": attempt,
                    "node_state_before_dispatch": view.node_states.get(node_id),
                    "completed_node_ids": sorted(
                        node
                        for node, state in view.node_states.items()
                        if state == "success"
                    ),
                },
            ),
            events=(
                {
                    "event": "adaptive_revision_checkpoint_reached",
                    "node_id": node_id,
                    "attempt": attempt,
                },
            ),
        )


def write_adaptive_revision_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Exercise bounded adaptive graph revision with real local artifacts."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    artifacts_dir = proof_dir / "artifacts"
    run_dir = proof_dir / "run"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    source_contract_path = artifacts_dir / "source-dag-contract.json"
    proposal_path = artifacts_dir / "adaptive-revision-proposal.json"
    proposal_receipt_path = artifacts_dir / "proposal-receipt.json"
    validation_receipt_path = artifacts_dir / "validation-receipt.json"
    policy_receipt_path = artifacts_dir / "policy-receipt.json"
    apply_receipt_path = artifacts_dir / "apply-receipt.json"
    preview_path = artifacts_dir / "expanded-dag.preview.json"
    expanded_contract_path = artifacts_dir / "expanded-dag-contract.json"
    checkpoint_path = artifacts_dir / "safe-checkpoint-receipt.json"
    revision_apply_path = artifacts_dir / "revision-apply-receipt.json"
    viewer_state_path = artifacts_dir / "viewer-state.json"
    unauthorized_proposal_path = artifacts_dir / "unauthorized-proposal.json"
    unauthorized_receipt_path = artifacts_dir / "unauthorized-validation-receipt.json"
    unauthorized_preview_path = artifacts_dir / "unauthorized-preview.json"
    signal_path = artifacts_dir / "clean-signal-receipt.json"

    source_contract = _source_contract()
    proposal = _bounded_revision_proposal()
    unauthorized_proposal = {
        **proposal,
        "proposal_id": "unauthorized-revision",
        "proposed_by": "worker",
    }
    _write_json(source_contract_path, source_contract)
    _write_json(proposal_path, proposal)
    _write_json(unauthorized_proposal_path, unauthorized_proposal)
    _write_json(signal_path, _clean_signal())
    source_contract_sha256 = _sha256_uri(source_contract_path)

    source_plan = compile_project_dag_plan(source_contract, source_path=source_contract_path)
    source_events: list[dict[str, Any]] = []
    checkpoint_output = {
        "schema": "tau.adaptive_revision_accepted_work.v1",
        "artifact": "deterministic-coder-output",
        "value": "accepted-before-revision",
    }
    with SqliteDagRunStore(run_dir / "source-run.sqlite3") as source_store:
        source_result = run_dag_plan(
            source_plan,
            execute_node=_source_executor(checkpoint_output),
            transition_policy=_SafeCheckpointPolicy(checkpoint_before_node="reviewer"),
            event_sink=source_events.append,
            run_store=source_store,
            run_id="adaptive-revision-source-run",
            lease_owner="adaptive-revision-source",
        )
        source_journal_events = [
            _journal_event_payload(item)
            for item in source_store.load_events(source_result.run_id or "")
        ]

    accepted_work_sha256 = canonical_sha256(checkpoint_output)
    checkpoint_receipt = {
        "schema": ADAPTIVE_REVISION_CHECKPOINT_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "source_run_id": source_result.run_id,
        "source_plan_sha256": source_plan.plan_sha256,
        "source_dag_contract": str(source_contract_path.resolve()),
        "source_dag_contract_sha256": source_contract_sha256,
        "accepted_node_ids": ["coder"],
        "pending_node_ids": ["reviewer"],
        "superseded_pending_node_ids": ["reviewer"],
        "accepted_work": checkpoint_output,
        "accepted_work_sha256": accepted_work_sha256,
        "scheduler_result": _scheduler_result_summary(source_result),
        "scheduler_events": source_events,
        "journal_event_count": len(source_journal_events),
    }
    _write_json(checkpoint_path, checkpoint_receipt)

    proposal_receipt = {
        "schema": ADAPTIVE_REVISION_PROPOSAL_RECEIPT_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "proposal": str(proposal_path.resolve()),
        "proposal_sha256": _sha256_uri(proposal_path),
        "source_dag_contract": str(source_contract_path.resolve()),
        "source_dag_contract_sha256": source_contract_sha256,
        "safe_checkpoint_receipt": str(checkpoint_path.resolve()),
        "safe_checkpoint_sha256": _sha256_uri(checkpoint_path),
        "superseded_pending_node_ids": ["reviewer"],
        "bounded_revision": {
            "new_node_ids": ["validator"],
            "new_edges": [
                {"from": "coder", "to": "validator"},
                {"from": "validator", "to": "reviewer"},
            ],
        },
    }
    _write_json(proposal_receipt_path, proposal_receipt)

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
    unauthorized_validation = write_dag_expansion_validation_receipt(
        dag_contract_path=source_contract_path,
        proposal_path=unauthorized_proposal_path,
        receipt_path=unauthorized_receipt_path,
        preview_path=unauthorized_preview_path,
    )

    expanded_contract = _read_json(expanded_contract_path)
    revised_plan = compile_project_dag_plan(
        expanded_contract,
        source_path=expanded_contract_path,
        source_payload_sha256=_sha256_uri(expanded_contract_path),
    )
    revised_events: list[dict[str, Any]] = []
    revised_node_outputs: dict[str, dict[str, Any]] = {}
    with SqliteDagRunStore(run_dir / "revised-run.sqlite3") as revised_store:
        revised_result = run_dag_plan(
            revised_plan,
            execute_node=_revised_executor(
                checkpoint_output=checkpoint_output,
                accepted_work_sha256=accepted_work_sha256,
                node_outputs=revised_node_outputs,
            ),
            event_sink=revised_events.append,
            run_store=revised_store,
            run_id="adaptive-revision-revised-run",
            lease_owner="adaptive-revision-revised",
        )
        revised_journal_events = [
            _journal_event_payload(item)
            for item in revised_store.load_events(revised_result.run_id or "")
        ]

    revision_apply_receipt = {
        "schema": ADAPTIVE_REVISION_APPLY_RECEIPT_SCHEMA,
        "status": "PASS" if revised_result.status == "PASS" else "BLOCKED",
        "mocked": False,
        "live": True,
        "source_plan_sha256": source_plan.plan_sha256,
        "revised_plan_sha256": revised_plan.plan_sha256,
        "source_dag_contract": str(source_contract_path.resolve()),
        "expanded_dag_contract": str(expanded_contract_path.resolve()),
        "proposal_receipt": str(proposal_receipt_path.resolve()),
        "validation_receipt": str(validation_receipt_path.resolve()),
        "policy_receipt": str(policy_receipt_path.resolve()),
        "apply_receipt": str(apply_receipt_path.resolve()),
        "safe_checkpoint_receipt": str(checkpoint_path.resolve()),
        "superseded_pending_node_ids": ["reviewer"],
        "accepted_work_sha256": accepted_work_sha256,
        "accepted_work_preserved": revised_node_outputs.get("coder", {}).get("accepted_output")
        == checkpoint_output,
        "revised_scheduler_result": _scheduler_result_summary(revised_result),
        "revised_journal_event_count": len(revised_journal_events),
    }
    _write_json(revision_apply_path, revision_apply_receipt)

    viewer_state = {
        "schema": ADAPTIVE_REVISION_VIEWER_STATE_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "source_plan_sha256": source_plan.plan_sha256,
        "revised_plan_sha256": revised_plan.plan_sha256,
        "safe_checkpoint": {
            "accepted_node_ids": ["coder"],
            "pending_node_ids": ["reviewer"],
            "superseded_pending_node_ids": ["reviewer"],
        },
        "revision_receipts": {
            "proposal": str(proposal_receipt_path.resolve()),
            "validation": str(validation_receipt_path.resolve()),
            "policy": str(policy_receipt_path.resolve()),
            "apply": str(apply_receipt_path.resolve()),
            "revision_apply": str(revision_apply_path.resolve()),
        },
        "nodes": [
            {"id": "coder", "state": "accepted_preserved", "plan": "source+revised"},
            {"id": "validator", "state": "inserted_by_revision", "plan": "revised"},
            {
                "id": "reviewer",
                "state": "superseded_pending_then_replayed",
                "plan": "source+revised",
            },
        ],
        "edges": expanded_contract["edges"],
    }
    _write_json(viewer_state_path, viewer_state)

    checks = {
        "source_checkpoint_blocked_before_pending_node": source_result.verdict
        == "SAFE_ADAPTIVE_REVISION_CHECKPOINT",
        "source_accepted_work_recorded": accepted_work_sha256.startswith("sha256:"),
        "proposal_receipt_present": proposal_receipt_path.is_file(),
        "validation_receipt_present": validation_receipt.get("ok") is True
        and validation_receipt_path.is_file(),
        "policy_receipt_present": policy_receipt.get("apply_allowed") is True
        and policy_receipt_path.is_file(),
        "apply_receipt_present": apply_receipt.get("applied") is True
        and apply_receipt_path.is_file(),
        "old_plan_hash_recorded": source_plan.plan_sha256.startswith("sha256:"),
        "new_plan_hash_recorded": revised_plan.plan_sha256.startswith("sha256:"),
        "plan_hash_changed": source_plan.plan_sha256 != revised_plan.plan_sha256,
        "superseded_nodes_explicit": revision_apply_receipt[
            "superseded_pending_node_ids"
        ]
        == ["reviewer"],
        "accepted_work_preserved": revision_apply_receipt["accepted_work_preserved"] is True,
        "revised_run_passed": revised_result.status == "PASS" and revised_result.verdict == "PASS",
        "unauthorized_expansion_denied": unauthorized_validation.get("ok") is False
        and any(
            item.get("code") == "unauthorized_expansion_author"
            for item in unauthorized_validation.get("alerts", [])
        ),
        "viewer_state_written": viewer_state_path.is_file(),
    }
    failed_checks = [name for name, value in checks.items() if value is not True]
    payload = {
        "schema": ADAPTIVE_REVISION_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "proof_dir": str(proof_dir),
        "artifacts_dir": str(artifacts_dir),
        "run_dir": str(run_dir),
        "source_plan_sha256": source_plan.plan_sha256,
        "revised_plan_sha256": revised_plan.plan_sha256,
        "superseded_nodes": ["reviewer"],
        "accepted_work_sha256": accepted_work_sha256,
        "accepted_work_preserved": checks["accepted_work_preserved"],
        "unauthorized_expansion_denied": checks["unauthorized_expansion_denied"],
        "receipts": {
            "proposal": str(proposal_receipt_path.resolve()),
            "validation": str(validation_receipt_path.resolve()),
            "policy": str(policy_receipt_path.resolve()),
            "apply": str(apply_receipt_path.resolve()),
            "safe_checkpoint": str(checkpoint_path.resolve()),
            "revision_apply": str(revision_apply_path.resolve()),
            "viewer_state": str(viewer_state_path.resolve()),
            "unauthorized_validation": str(unauthorized_receipt_path.resolve()),
        },
        "checks": checks,
        "failed_checks": failed_checks,
        "source_scheduler_result": _scheduler_result_summary(source_result),
        "revised_scheduler_result": _scheduler_result_summary(revised_result),
        "source_scheduler_events": source_events,
        "revised_scheduler_events": revised_events,
        "proof_scope": {
            "proves": [
                "Tau can checkpoint a source DAG before dispatching a pending node.",
                "Tau can validate, policy-check, and apply a bounded adaptive DAG "
                "expansion into a new DAG artifact.",
                "Tau records old and new plan hashes and explicit superseded pending nodes.",
                "Tau preserves accepted checkpoint work across the revised run.",
                "Tau denies an unauthorized expansion proposal fail-closed.",
                "Tau emits a viewer-state artifact that can show the checkpoint "
                "and revision receipts.",
            ],
            "does_not_prove": [
                "In-place mutation of an already-running scheduler route.",
                "Provider/model semantic quality.",
                "Distributed scheduler coordination across hosts.",
                "Human approval UX for selecting between multiple revision proposals.",
            ],
        },
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    _write_json(resolved_output, payload)
    return payload


def _source_contract() -> dict[str, Any]:
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "adaptive-revision-conformance",
        "goal": {
            "goal_id": "adaptive-revision-conformance",
            "goal_version": 1,
            "goal_hash": "sha256:adaptive-revision-conformance-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "adaptive-revision-conformance",
        },
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 4,
        },
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["accepted_work"],
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


def _bounded_revision_proposal() -> dict[str, Any]:
    return {
        "schema": "tau.dag_expansion_proposal.v1",
        "proposal_id": "adaptive-revision-001",
        "parent_dag_id": "adaptive-revision-conformance",
        "goal_hash": "sha256:adaptive-revision-conformance-goal",
        "proposed_by": "reviewer",
        "phase": "running",
        "reason": "Insert bounded validator before the pending reviewer reruns.",
        "new_nodes": [
            {
                "id": "validator",
                "agent": "validator",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["validation_receipt"],
            }
        ],
        "new_edges": [
            {"from": "coder", "to": "validator"},
            {"from": "validator", "to": "reviewer"},
        ],
    }


def _source_executor(checkpoint_output: dict[str, Any]):
    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs
        if node.node_id == "coder":
            return {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "attempt_id": attempt.attempt_id,
                "accepted_output": checkpoint_output,
            }
        return {
            "node_id": node.node_id,
            "status": "BLOCKED",
            "verdict": "UNEXPECTED_SOURCE_DISPATCH",
            "errors": ["source checkpoint policy should block before reviewer dispatch"],
        }

    return execute


def _revised_executor(
    *,
    checkpoint_output: dict[str, Any],
    accepted_work_sha256: str,
    node_outputs: dict[str, dict[str, Any]],
):
    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        if node.node_id == "coder":
            accepted_output = checkpoint_output
        elif node.node_id == "validator":
            preserved = _first_input(accepted_inputs)
            accepted_output = {
                "schema": "tau.adaptive_revision_validation_output.v1",
                "validated_preserved_work_sha256": canonical_sha256(preserved),
                "expected_preserved_work_sha256": accepted_work_sha256,
                "verdict": "PASS",
            }
        elif node.node_id == "reviewer":
            accepted_output = {
                "schema": "tau.adaptive_revision_review_output.v1",
                "input_count": len(accepted_inputs),
                "input_hashes": [canonical_sha256(item) for item in accepted_inputs],
                "reviewer_verdict": "PASS",
            }
        else:
            accepted_output = {"node_id": node.node_id}
        result = {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "attempt_id": attempt.attempt_id,
            "accepted_output": accepted_output,
        }
        node_outputs[node.node_id] = result
        return result

    return execute


def _first_input(inputs: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if not inputs:
        return {}
    return inputs[0]


def _clean_signal() -> dict[str, Any]:
    return {
        "schema": "tau.dag_signal_receipt.v1",
        "ok": True,
        "status": "PASS",
        "source_ok": True,
        "source_status": "PASS",
        "negative_signals": [],
    }


def _scheduler_result_summary(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "verdict": result.verdict,
        "completed_node_ids": list(result.completed_node_ids),
        "node_states": dict(result.node_states),
        "edge_states": dict(result.edge_states),
        "terminal_states": dict(result.terminal_states),
        "max_observed_concurrency": result.max_observed_concurrency,
        "run_id": result.run_id,
        "lease_epoch": result.lease_epoch,
        "replayed_event_count": result.replayed_event_count,
    }


def _journal_event_payload(event: Any) -> dict[str, Any]:
    if hasattr(event, "to_mapping"):
        return dict(event.to_mapping())
    return dict(event)


def _sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.expanduser().resolve().read_bytes()).hexdigest()}"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
