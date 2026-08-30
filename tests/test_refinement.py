"""Regression tests for governed refinement proposals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tau_coding.refinement import (
    EMPTY_TARGET_HASH,
    _hash_json,
    _hash_prompt_content,
    _target_ref_hash,
    refinement_view_payload,
    write_refinement_apply_receipt,
    write_refinement_preview_receipt,
    write_refinement_rollback_receipt,
    write_refinement_verification_receipt,
)


def test_supplemental_prompt_preview_apply_verify_idempotent_and_viewer(tmp_path: Path) -> None:
    target = tmp_path / "prompts" / "project" / "prompt.json"
    before = {"schema": "tau.supplemental_prompt_resource.v1", "content": "before"}
    after = {"schema": "tau.supplemental_prompt_resource.v1", "content": "after"}
    _write_json(target, before)
    proposal = _proposal(
        tmp_path,
        proposal_id="prompt-1",
        idempotency_key="idem-prompt-1",
        target=target,
        before_hash=_hash_prompt_content(before),
        after=after,
    )
    decision = _decision(proposal, out=tmp_path / "memory-decision.json")

    preview = write_refinement_preview_receipt(
        proposal_path=tmp_path / "proposal.json",
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "preview.json",
        diff_path=tmp_path / "diff.json",
    )
    assert preview["ok"] is True
    assert preview["target_mutated"] is False
    assert json.loads(target.read_text(encoding="utf-8")) == before

    apply = write_refinement_apply_receipt(
        proposal_path=tmp_path / "proposal.json",
        decision_path=decision,
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "apply.json",
    )
    verify = write_refinement_verification_receipt(
        proposal_path=tmp_path / "proposal.json",
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "verify.json",
    )
    second = write_refinement_apply_receipt(
        proposal_path=tmp_path / "proposal.json",
        decision_path=decision,
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "apply-second.json",
    )

    assert apply["ok"] is True
    assert apply["mutation_performed"] is True
    assert verify["lifecycle_state"] == "ACCEPTED"
    assert second["ok"] is True
    assert second["idempotent_replay"] is True
    assert second["mutation_performed"] is False
    view = refinement_view_payload(ledger_dir=tmp_path / "ledger")
    assert view["proposal_count"] == 1
    assert view["proposals"][0]["state"] == "ACCEPTED"
    assert view["proposals"][0]["rollback_state"] == "available"


def test_reused_idempotency_key_with_changed_proposal_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "prompt.json"
    before = {"schema": "tau.supplemental_prompt_resource.v1", "content": "before"}
    after = {"schema": "tau.supplemental_prompt_resource.v1", "content": "after"}
    _write_json(target, before)
    proposal = _proposal(
        tmp_path,
        proposal_id="prompt-1",
        idempotency_key="idem-prompt-1",
        target=target,
        before_hash=_hash_prompt_content(before),
        after=after,
    )
    decision = _decision(proposal, out=tmp_path / "memory-decision.json")
    write_refinement_preview_receipt(
        proposal_path=tmp_path / "proposal.json",
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "preview.json",
    )
    write_refinement_apply_receipt(
        proposal_path=tmp_path / "proposal.json",
        decision_path=decision,
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "apply.json",
    )

    tampered = dict(proposal)
    tampered["proposed_content"] = {
        "schema": "tau.supplemental_prompt_resource.v1",
        "content": "tampered",
    }
    _write_json(tmp_path / "tampered-proposal.json", tampered)
    tampered_decision = _decision(tampered, out=tmp_path / "tampered-decision.json")
    receipt = write_refinement_apply_receipt(
        proposal_path=tmp_path / "tampered-proposal.json",
        decision_path=tampered_decision,
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "tampered-apply.json",
    )

    assert receipt["ok"] is False
    assert _has_alert(receipt, "idempotency_key_reused_different_proposal")
    assert json.loads(target.read_text(encoding="utf-8")) == after


def test_verification_failure_rolls_back_to_before_hash(tmp_path: Path) -> None:
    target = tmp_path / "prompt.json"
    after = {"schema": "tau.supplemental_prompt_resource.v1", "content": "after"}
    proposal = _proposal(
        tmp_path,
        proposal_id="rollback-1",
        idempotency_key="idem-rollback-1",
        target=target,
        before_hash=EMPTY_TARGET_HASH,
        after=after,
    )
    decision = _decision(proposal, out=tmp_path / "memory-decision.json")
    write_refinement_preview_receipt(
        proposal_path=tmp_path / "proposal.json",
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "preview.json",
    )
    write_refinement_apply_receipt(
        proposal_path=tmp_path / "proposal.json",
        decision_path=decision,
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "apply.json",
    )
    target.write_text("tampered after apply\n", encoding="utf-8")
    failed = write_refinement_verification_receipt(
        proposal_path=tmp_path / "proposal.json",
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "failed-verify.json",
    )
    rollback = write_refinement_rollback_receipt(
        proposal_path=tmp_path / "proposal.json",
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "rollback.json",
    )

    assert failed["ok"] is False
    assert rollback["ok"] is True
    assert rollback["restored_hash"] == EMPTY_TARGET_HASH
    assert not target.exists()


def test_memory_outage_returns_pending_degraded_without_source_mutation(tmp_path: Path) -> None:
    source_truth = tmp_path / "source-truth.txt"
    source_truth.write_text("unchanged\n", encoding="utf-8")
    source_hash = _file_hash(source_truth)
    proposed = {
        "_key": "offline",
        "kind": "tau_refinement_test",
        "retrieval_text": "offline proposal",
    }
    proposal = _memory_proposal(tmp_path, proposed=proposed)
    decision = _decision(proposal, out=tmp_path / "memory-decision.json")

    receipt = write_refinement_apply_receipt(
        proposal_path=tmp_path / "memory-proposal.json",
        decision_path=decision,
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "memory-apply.json",
        memory_url="http://127.0.0.1:9",
    )

    assert receipt["status"] == "PENDING_DEGRADED"
    assert receipt["mutation_performed"] is False
    assert _file_hash(source_truth) == source_hash


def test_immutable_goal_and_malicious_memory_actor_cannot_apply(tmp_path: Path) -> None:
    target = tmp_path / "prompt.json"
    after = {"schema": "tau.supplemental_prompt_resource.v1", "content": "after"}
    proposal = _proposal(
        tmp_path,
        proposal_id="immutable-1",
        idempotency_key="idem-immutable-1",
        target=target,
        before_hash=EMPTY_TARGET_HASH,
        after=after,
        kind="immutable_goal",
    )
    decision = _decision(proposal)
    receipt = write_refinement_apply_receipt(
        proposal_path=tmp_path / "proposal.json",
        decision_path=decision,
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "apply.json",
    )
    assert receipt["ok"] is False
    assert _has_alert(receipt, "immutable_target_not_applyable")

    mutable = _proposal(
        tmp_path,
        proposal_id="malicious-1",
        idempotency_key="idem-malicious-1",
        target=target,
        before_hash=EMPTY_TARGET_HASH,
        after=after,
    )
    decision_path = _decision(mutable, out=tmp_path / "mutable-decision.json")
    malicious_decision = json.loads(decision_path.read_text(encoding="utf-8"))
    malicious_decision["actor"] = {"type": "memory_recall", "id": "untrusted"}
    _write_json(tmp_path / "malicious-decision.json", malicious_decision)
    malicious_receipt = write_refinement_apply_receipt(
        proposal_path=tmp_path / "proposal.json",
        decision_path=tmp_path / "malicious-decision.json",
        ledger_dir=tmp_path / "ledger",
        receipt_path=tmp_path / "malicious-apply.json",
    )
    assert mutable["proposal_id"] == "malicious-1"
    assert malicious_receipt["ok"] is False
    assert _has_alert(malicious_receipt, "approval_actor_not_human")


def _proposal(
    tmp_path: Path,
    *,
    proposal_id: str,
    idempotency_key: str,
    target: Path,
    before_hash: str,
    after: dict[str, Any],
    kind: str = "supplemental_prompt",
) -> dict[str, Any]:
    proposal = {
        "schema": "tau.refinement_proposal.v1",
        "proposal_id": proposal_id,
        "idempotency_key": idempotency_key,
        "source": {"run_id": "run", "node_id": "node", "attempt": 1, "turn": 1},
        "accepted_evidence_hashes": ["sha256:" + "a" * 64],
        "observation": {
            "schema": "tau.refinement_observation.v1",
            "text": "observed",
        },
        "problem_statement": "problem",
        "kind": kind,
        "scope": "project",
        "target_ref": {"adapter": "supplemental_prompt", "path": str(target)},
        "before_hash": before_hash,
        "proposed_content": after,
        "after_hash": _hash_prompt_content(after),
        "rationale": "rationale",
        "expected_outcome": "outcome",
        "risks": ["risk"],
        "conflicts": [],
        "validation_plan": {"declared": True},
        "verification_plan": {"declared": True},
        "validity_window": {
            "not_before": "2026-01-01T00:00:00Z",
            "not_after": "2099-01-01T00:00:00Z",
        },
        "supersedes": [],
        "superseded_by": None,
        "required_actor": "human",
        "approval_class": "human_exact",
        "policy_version": "policy.v1",
        "data_boundary_version": "boundary.v1",
        "redaction_version": "redaction.v1",
        "goal_hash": "sha256:" + "b" * 64,
    }
    _write_json(tmp_path / "proposal.json", proposal)
    return proposal


def _memory_proposal(tmp_path: Path, *, proposed: dict[str, Any]) -> dict[str, Any]:
    proposal = {
        "schema": "tau.refinement_proposal.v1",
        "proposal_id": "memory-1",
        "idempotency_key": "idem-memory-1",
        "source": {"run_id": "run", "node_id": "node", "attempt": 1, "turn": 1},
        "accepted_evidence_hashes": ["sha256:" + "a" * 64],
        "observation": {"schema": "tau.refinement_observation.v1", "text": "observed"},
        "problem_statement": "problem",
        "kind": "memory_document",
        "scope": "project",
        "target_ref": {"adapter": "memory", "collection": "tau_refinement_test", "key": "offline"},
        "before_hash": EMPTY_TARGET_HASH,
        "proposed_content": proposed,
        "after_hash": _hash_json(proposed),
        "rationale": "rationale",
        "expected_outcome": "outcome",
        "risks": ["risk"],
        "conflicts": [],
        "validation_plan": {"declared": True},
        "verification_plan": {"declared": True},
        "validity_window": {
            "not_before": "2026-01-01T00:00:00Z",
            "not_after": "2099-01-01T00:00:00Z",
        },
        "supersedes": [],
        "superseded_by": None,
        "required_actor": "human",
        "approval_class": "human_exact",
        "policy_version": "policy.v1",
        "data_boundary_version": "boundary.v1",
        "redaction_version": "redaction.v1",
        "goal_hash": "sha256:" + "b" * 64,
    }
    _write_json(tmp_path / "memory-proposal.json", proposal)
    return proposal


def _decision(proposal: dict[str, Any], *, out: Path | None = None) -> Path:
    if out is not None:
        path = out
    elif "path" in proposal["target_ref"]:
        path = Path(proposal["target_ref"]["path"]).parents[1] / "decision.json"
    else:
        raise AssertionError("Memory proposal tests must pass an explicit decision path")
    decision = {
        "schema": "tau.refinement_decision.v1",
        "decision": "APPROVED",
        "proposal_id": proposal["proposal_id"],
        "proposal_sha256": _hash_json(proposal),
        "idempotency_key": proposal["idempotency_key"],
        "target_ref_hash": _target_ref_hash(proposal),
        "before_hash": proposal["before_hash"],
        "after_hash": proposal["after_hash"],
        "goal_hash": proposal["goal_hash"],
        "policy_version": proposal["policy_version"],
        "data_boundary_version": proposal["data_boundary_version"],
        "redaction_version": proposal["redaction_version"],
        "approval_class": proposal["approval_class"],
        "actor": {"type": "human", "id": "tester"},
        "validity_window": proposal["validity_window"],
    }
    _write_json(path, decision)
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_hash(path: Path) -> str:
    return "sha256:" + __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def _has_alert(receipt: dict[str, Any], code: str) -> bool:
    return any(alert.get("code") == code for alert in receipt.get("alerts", []))
