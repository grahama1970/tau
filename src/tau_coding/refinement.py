"""Governed refinement proposal ledger and v1 Memory/prompt adapters.

This module implements preview-first refinement proposals for Tau. Inputs are
untrusted JSON proposal and decision records; persistent mutations are allowed
only through explicit target adapters, exact hash checks, and human-bound
approval. Preview and validation read targets but never mutate them.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

REFINEMENT_OBSERVATION_SCHEMA = "tau.refinement_observation.v1"
REFINEMENT_PROPOSAL_SCHEMA = "tau.refinement_proposal.v1"
REFINEMENT_DIFF_SCHEMA = "tau.refinement_diff.v1"
REFINEMENT_DECISION_SCHEMA = "tau.refinement_decision.v1"
REFINEMENT_APPLY_RECEIPT_SCHEMA = "tau.refinement_apply_receipt.v1"
REFINEMENT_VERIFICATION_RECEIPT_SCHEMA = "tau.refinement_verification_receipt.v1"
REFINEMENT_ROLLBACK_RECEIPT_SCHEMA = "tau.refinement_rollback_receipt.v1"
REFINEMENT_LEDGER_SCHEMA = "tau.refinement_ledger.v1"
REFINEMENT_PREVIEW_RECEIPT_SCHEMA = "tau.refinement_preview_receipt.v1"
REFINEMENT_VIEW_SCHEMA = "tau.refinement_view.v1"

EMPTY_TARGET_HASH = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
TARGETABLE_KINDS = {"memory_document", "supplemental_prompt"}
UNSUPPORTED_KINDS = {"executable_skill", "worker_profile", "dag_template"}
IMMUTABLE_KINDS = {
    "immutable_goal",
    "base_prompt",
    "base_system_prompt",
    "evidence_requirement",
    "route",
    "provider_profile",
    "security_policy",
    "approval_policy",
}
ALLOWED_SCOPES = {"local", "session", "project", "global"}
PROMPT_SCOPES = {"local", "session", "project"}
ACTIVE_STATES = {
    "OBSERVED",
    "PROPOSED",
    "DIFF_RENDERED",
    "VALIDATED",
    "APPROVED",
    "APPLIED",
    "VERIFICATION_FAILED",
}


class RefinementState(StrEnum):
    OBSERVED = "OBSERVED"
    PROPOSED = "PROPOSED"
    DIFF_RENDERED = "DIFF_RENDERED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    APPLIED = "APPLIED"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ACCEPTED = "ACCEPTED"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_BLOCKED = "ROLLBACK_BLOCKED"


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    hash: str
    exists: bool
    content: Any
    source_version: str | None = None
    raw: dict[str, Any] | None = None


class RefinementError(RuntimeError):
    """Raised when a refinement command cannot complete its requested action."""


def write_refinement_preview_receipt(
    *,
    proposal_path: Path,
    ledger_dir: Path,
    receipt_path: Path,
    diff_path: Path | None = None,
    memory_url: str = "http://127.0.0.1:8601",
    memory_auth_token: str | None = None,
) -> dict[str, Any]:
    """Validate and render a proposal diff without mutating the target."""

    proposal_file = proposal_path.expanduser().resolve()
    ledger = _load_ledger(ledger_dir)
    proposal = _read_json_object(proposal_file, label="refinement proposal")
    proposal_hash = _file_hash(proposal_file)
    alerts = _proposal_alerts(proposal)
    duplicate = _duplicate_alert(ledger, proposal, proposal_hash)
    if duplicate is not None:
        alerts.append(duplicate)
    before_snapshot = _target_snapshot(
        proposal,
        memory_url=memory_url,
        memory_auth_token=memory_auth_token,
        allow_offline=True,
    )
    rendered_diff = _render_diff(proposal, before_snapshot)
    after_snapshot = _target_snapshot(
        proposal,
        memory_url=memory_url,
        memory_auth_token=memory_auth_token,
        allow_offline=True,
    )
    no_mutation = before_snapshot.hash == after_snapshot.hash
    if not no_mutation:
        alerts.append(_alert("BLOCK", "preview_mutated_target", "Preview changed target bytes."))
    diff_file = diff_path.expanduser().resolve() if diff_path else None
    if diff_file is not None:
        _write_json(diff_file, rendered_diff)
    if not alerts:
        _journal(ledger, proposal, RefinementState.PROPOSED, proposal_hash=proposal_hash)
        _journal(ledger, proposal, RefinementState.DIFF_RENDERED, proposal_hash=proposal_hash)
        _journal(ledger, proposal, RefinementState.VALIDATED, proposal_hash=proposal_hash)
        _save_ledger(ledger_dir, ledger)
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": REFINEMENT_PREVIEW_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "mocked": False,
        "live": True,
        "proposal": str(proposal_file),
        "proposal_sha256": proposal_hash,
        "proposal_id": proposal.get("proposal_id"),
        "idempotency_key": proposal.get("idempotency_key"),
        "kind": proposal.get("kind"),
        "scope": proposal.get("scope"),
        "target_ref": proposal.get("target_ref"),
        "before_hash": before_snapshot.hash,
        "after_preview_hash": after_snapshot.hash,
        "target_mutated": not no_mutation,
        "diff": rendered_diff,
        "diff_path": str(diff_file) if diff_file else None,
        "alerts": alerts,
        "receipt_path": str(receipt_path.expanduser().resolve()),
        "proof_scope": _proof_scope(
            proves=[
                "Proposal schema and governance fields were inspected.",
                "A read-only diff was rendered.",
                "Target state was read before and after preview to prove no mutation.",
            ],
            gaps=["Memory fact truth.", "Future improvement quality.", "Human approval."],
        ),
        "timestamp": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    return receipt


def write_refinement_apply_receipt(
    *,
    proposal_path: Path,
    decision_path: Path,
    ledger_dir: Path,
    receipt_path: Path,
    memory_url: str = "http://127.0.0.1:8601",
    memory_auth_token: str | None = None,
) -> dict[str, Any]:
    """Apply an approved proposal through its governed adapter."""

    proposal_file = proposal_path.expanduser().resolve()
    decision_file = decision_path.expanduser().resolve()
    ledger = _load_ledger(ledger_dir)
    proposal = _read_json_object(proposal_file, label="refinement proposal")
    decision = _read_json_object(decision_file, label="refinement decision")
    proposal_hash = _file_hash(proposal_file)
    alerts = _proposal_alerts(proposal)
    alerts.extend(_decision_alerts(proposal, decision, proposal_hash=proposal_hash))
    current = _target_snapshot(
        proposal,
        memory_url=memory_url,
        memory_auth_token=memory_auth_token,
        allow_offline=True,
    )
    if current.raw and current.raw.get("offline") is True:
        receipt = _apply_receipt(
            proposal,
            proposal_file=proposal_file,
            proposal_hash=proposal_hash,
            decision_file=decision_file,
            receipt_path=receipt_path,
            status="PENDING_DEGRADED",
            alerts=[
                _alert(
                    "BLOCK",
                    "memory_offline_pending_degraded",
                    "Memory is unavailable; proposal remains pending/degraded.",
                    {"error": current.raw.get("error")},
                )
            ],
            before=current,
            after=current,
            mutation_performed=False,
            idempotent_replay=False,
            memory_url=memory_url,
        )
        _write_json(receipt_path, receipt)
        return receipt
    effect_key = str(proposal.get("idempotency_key") or "")
    prior_effect = ledger["effects"].get(effect_key)
    if prior_effect and prior_effect.get("mutation_performed") is True:
        if prior_effect.get("proposal_sha256") != proposal_hash:
            alerts.append(
                _alert(
                    "BLOCK",
                    "idempotency_key_reused_different_proposal",
                    "The idempotency key is already bound to a different proposal hash.",
                )
            )
        elif not alerts:
            receipt = _apply_receipt(
                proposal,
                proposal_file=proposal_file,
                proposal_hash=proposal_hash,
                decision_file=decision_file,
                receipt_path=receipt_path,
                status="PASS",
                alerts=[],
                before=current,
                after=current,
                mutation_performed=False,
                idempotent_replay=True,
                memory_url=memory_url,
                extra={"prior_effect": prior_effect},
            )
            _write_json(receipt_path, receipt)
            return receipt
    if current.hash != proposal.get("before_hash"):
        alerts.append(
            _alert(
                "BLOCK",
                "target_hash_conflict_requires_reconciliation",
                "Current target hash differs from proposal before_hash.",
                {"current_hash": current.hash, "proposal_before_hash": proposal.get("before_hash")},
            )
        )
    after = current
    mutation_performed = False
    if not alerts:
        _journal(ledger, proposal, RefinementState.APPROVED, proposal_hash=proposal_hash)
        before_content = current.content
        _apply_target(proposal, memory_url=memory_url, memory_auth_token=memory_auth_token)
        after = _target_snapshot(
            proposal,
            memory_url=memory_url,
            memory_auth_token=memory_auth_token,
            allow_offline=False,
        )
        if after.hash != proposal.get("after_hash"):
            alerts.append(
                _alert(
                    "BLOCK",
                    "post_apply_readback_hash_mismatch",
                    "Applied target readback did not match proposal after_hash.",
                    {
                        "readback_hash": after.hash,
                        "proposal_after_hash": proposal.get("after_hash"),
                    },
                )
            )
        else:
            mutation_performed = True
            ledger["effects"][effect_key] = {
                "proposal_id": proposal["proposal_id"],
                "proposal_sha256": proposal_hash,
                "target_ref": proposal["target_ref"],
                "kind": proposal["kind"],
                "before_hash": current.hash,
                "after_hash": after.hash,
                "before_content": before_content,
                "applied_at": _utc_stamp(),
                "mutation_performed": True,
            }
            _journal(ledger, proposal, RefinementState.APPLIED, proposal_hash=proposal_hash)
            _save_ledger(ledger_dir, ledger)
    status = "PASS" if not alerts else "BLOCKED"
    receipt = _apply_receipt(
        proposal,
        proposal_file=proposal_file,
        proposal_hash=proposal_hash,
        decision_file=decision_file,
        receipt_path=receipt_path,
        status=status,
        alerts=alerts,
        before=current,
        after=after,
        mutation_performed=mutation_performed,
        idempotent_replay=False,
        memory_url=memory_url,
    )
    _write_json(receipt_path, receipt)
    return receipt


def write_refinement_verification_receipt(
    *,
    proposal_path: Path,
    ledger_dir: Path,
    receipt_path: Path,
    memory_url: str = "http://127.0.0.1:8601",
    memory_auth_token: str | None = None,
) -> dict[str, Any]:
    """Run the proposal verifier and accept only exact target readback."""

    proposal_file = proposal_path.expanduser().resolve()
    ledger = _load_ledger(ledger_dir)
    proposal = _read_json_object(proposal_file, label="refinement proposal")
    proposal_hash = _file_hash(proposal_file)
    alerts = _proposal_alerts(proposal)
    snapshot = _target_snapshot(
        proposal,
        memory_url=memory_url,
        memory_auth_token=memory_auth_token,
        allow_offline=True,
    )
    plan = proposal.get("verification_plan")
    if not isinstance(plan, dict) or not plan.get("declared"):
        alerts.append(_alert("BLOCK", "missing_verification_plan", "Verifier plan is missing."))
    if snapshot.hash != proposal.get("after_hash"):
        alerts.append(
            _alert(
                "BLOCK",
                "verification_hash_mismatch",
                "Target readback hash does not match the proposal after_hash.",
                {"readback_hash": snapshot.hash, "proposal_after_hash": proposal.get("after_hash")},
            )
        )
    if not alerts:
        _journal(ledger, proposal, RefinementState.VERIFIED, proposal_hash=proposal_hash)
        _journal(ledger, proposal, RefinementState.ACCEPTED, proposal_hash=proposal_hash)
    else:
        _journal(ledger, proposal, RefinementState.VERIFICATION_FAILED, proposal_hash=proposal_hash)
    _save_ledger(ledger_dir, ledger)
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": REFINEMENT_VERIFICATION_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "proposal": str(proposal_file),
        "proposal_sha256": proposal_hash,
        "proposal_id": proposal.get("proposal_id"),
        "target_ref": proposal.get("target_ref"),
        "readback_hash": snapshot.hash,
        "expected_hash": proposal.get("after_hash"),
        "lifecycle_state": "ACCEPTED" if status == "PASS" else "VERIFICATION_FAILED",
        "alerts": alerts,
        "mocked": False,
        "live": True,
        "receipt_path": str(receipt_path.expanduser().resolve()),
        "proof_scope": _proof_scope(
            proves=["Declared verifier read back the real target and compared the exact hash."],
            gaps=["Applied refinement quality beyond its declared verifier."],
        ),
        "timestamp": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    return receipt


def write_refinement_rollback_receipt(
    *,
    proposal_path: Path,
    ledger_dir: Path,
    receipt_path: Path,
    memory_url: str = "http://127.0.0.1:8601",
    memory_auth_token: str | None = None,
) -> dict[str, Any]:
    """Rollback an applied proposal to the ledger-recorded before hash."""

    proposal_file = proposal_path.expanduser().resolve()
    ledger = _load_ledger(ledger_dir)
    proposal = _read_json_object(proposal_file, label="refinement proposal")
    proposal_hash = _file_hash(proposal_file)
    effect = ledger["effects"].get(str(proposal.get("idempotency_key") or ""))
    alerts = _proposal_alerts(proposal)
    if not effect:
        alerts.append(_alert("BLOCK", "missing_apply_effect", "No applied effect is recorded."))
    before = _target_snapshot(
        proposal,
        memory_url=memory_url,
        memory_auth_token=memory_auth_token,
        allow_offline=True,
    )
    after = before
    rollback_performed = False
    if not alerts and effect:
        _restore_target(
            proposal,
            effect["before_content"],
            memory_url=memory_url,
            memory_auth_token=memory_auth_token,
        )
        after = _target_snapshot(
            proposal,
            memory_url=memory_url,
            memory_auth_token=memory_auth_token,
            allow_offline=False,
        )
        if after.hash != effect.get("before_hash"):
            alerts.append(
                _alert(
                    "BLOCK",
                    "rollback_readback_hash_mismatch",
                    "Rollback readback did not match the recorded before hash.",
                    {"readback_hash": after.hash, "before_hash": effect.get("before_hash")},
                )
            )
        else:
            rollback_performed = True
            _journal(ledger, proposal, RefinementState.ROLLED_BACK, proposal_hash=proposal_hash)
            _save_ledger(ledger_dir, ledger)
    if alerts:
        _journal(ledger, proposal, RefinementState.ROLLBACK_BLOCKED, proposal_hash=proposal_hash)
        _save_ledger(ledger_dir, ledger)
    status = "PASS" if not alerts else "BLOCKED"
    receipt = {
        "schema": REFINEMENT_ROLLBACK_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "proposal": str(proposal_file),
        "proposal_sha256": proposal_hash,
        "proposal_id": proposal.get("proposal_id"),
        "target_ref": proposal.get("target_ref"),
        "pre_rollback_hash": before.hash,
        "restored_hash": after.hash,
        "expected_restored_hash": effect.get("before_hash") if effect else None,
        "rollback_performed": rollback_performed,
        "lifecycle_state": "ROLLED_BACK" if status == "PASS" else "ROLLBACK_BLOCKED",
        "alerts": alerts,
        "mocked": False,
        "live": True,
        "receipt_path": str(receipt_path.expanduser().resolve()),
        "proof_scope": _proof_scope(
            proves=["Rollback restored the exact hash recorded before apply."],
            gaps=["Memory service internal history retention."],
        ),
        "timestamp": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    return receipt


def refinement_view_payload(*, ledger_dir: Path, proposal_id: str | None = None) -> dict[str, Any]:
    """Return a read-only proposal viewer payload from the durable ledger."""

    ledger = _load_ledger(ledger_dir)
    proposals = list(ledger["proposals"].values())
    if proposal_id is not None:
        proposals = [
            proposal for proposal in proposals if proposal.get("proposal_id") == proposal_id
        ]
    rows = []
    for proposal in proposals:
        target_ref = proposal.get("target_ref")
        target = target_ref if isinstance(target_ref, dict) else {}
        rows.append(
            {
                "proposal_id": proposal.get("proposal_id"),
                "kind": proposal.get("kind"),
                "scope": proposal.get("scope"),
                "target": target,
                "state": proposal.get("state"),
                "risk": proposal.get("risks"),
                "approval_class": proposal.get("approval_class"),
                "required_actor": proposal.get("required_actor"),
                "verification_plan": proposal.get("verification_plan"),
                "rollback_state": _rollback_state(ledger, proposal),
                "diff_state": proposal.get("diff_state", "rendered"),
                "proposal_sha256": proposal.get("proposal_sha256"),
            }
        )
    return {
        "schema": REFINEMENT_VIEW_SCHEMA,
        "ok": True,
        "mocked": False,
        "live": True,
        "ledger": str(_ledger_path(ledger_dir)),
        "proposal_count": len(rows),
        "proposals": rows,
        "journal_count": len(ledger["journal"]),
        "timestamp": _utc_stamp(),
    }


def render_refinement_view(payload: dict[str, Any]) -> str:
    """Render a concise read-only refinement viewer."""

    lines = [
        "Tau refinement proposals",
        f"ledger: {payload['ledger']}",
        f"proposal_count: {payload['proposal_count']}",
    ]
    for row in payload["proposals"]:
        lines.extend(
            [
                "",
                f"- {row['proposal_id']} [{row['state']}]",
                f"  kind/scope: {row['kind']} / {row['scope']}",
                f"  target: {json.dumps(row['target'], sort_keys=True)}",
                f"  risk: {row['risk']}",
                f"  approval: {row['approval_class']} by {row['required_actor']}",
                f"  verification: {json.dumps(row['verification_plan'], sort_keys=True)}",
                f"  rollback: {row['rollback_state']}",
            ]
        )
    return "\n".join(lines) + "\n"


def _proposal_alerts(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    required = [
        "proposal_id",
        "idempotency_key",
        "source",
        "accepted_evidence_hashes",
        "observation",
        "problem_statement",
        "kind",
        "scope",
        "target_ref",
        "before_hash",
        "proposed_content",
        "after_hash",
        "rationale",
        "expected_outcome",
        "risks",
        "conflicts",
        "validation_plan",
        "verification_plan",
        "validity_window",
        "required_actor",
        "approval_class",
        "policy_version",
        "data_boundary_version",
        "redaction_version",
        "goal_hash",
    ]
    for field in required:
        if field not in proposal:
            alerts.append(_alert("BLOCK", f"missing_{field}", f"{field} is required."))
    if proposal.get("schema") != REFINEMENT_PROPOSAL_SCHEMA:
        alerts.append(
            _alert("BLOCK", "invalid_schema", f"schema must be {REFINEMENT_PROPOSAL_SCHEMA}.")
        )
    if proposal.get("kind") in IMMUTABLE_KINDS:
        alerts.append(
            _alert(
                "BLOCK",
                "immutable_target_not_applyable",
                "V1 cannot apply immutable goal/base prompt/policy/route/provider changes.",
            )
        )
    if proposal.get("kind") in UNSUPPORTED_KINDS:
        alerts.append(
            _alert(
                "BLOCK",
                "unsupported_pending_adapter",
                "V1 records this kind but cannot execute or install it.",
            )
        )
    if proposal.get("kind") not in TARGETABLE_KINDS | UNSUPPORTED_KINDS | IMMUTABLE_KINDS:
        alerts.append(_alert("BLOCK", "unknown_kind", "kind is not recognized."))
    if proposal.get("scope") not in ALLOWED_SCOPES:
        alerts.append(_alert("BLOCK", "invalid_scope", "scope is outside the closed vocabulary."))
    if proposal.get("kind") == "supplemental_prompt" and proposal.get("scope") not in PROMPT_SCOPES:
        alerts.append(
            _alert("BLOCK", "prompt_scope_not_v1", "V1 prompts are local/session/project only.")
        )
    if proposal.get("kind") == "supplemental_prompt" and _prompt_policy_alert(
        str(proposal.get("proposed_content"))
    ):
        alerts.append(
            _alert(
                "BLOCK", "prompt_injection_policy", "Supplemental prompt content violates policy."
            )
        )
    if not _valid_hash(proposal.get("before_hash")):
        alerts.append(_alert("BLOCK", "invalid_before_hash", "before_hash must be sha256:<hex>."))
    if not _valid_hash(proposal.get("after_hash")):
        alerts.append(_alert("BLOCK", "invalid_after_hash", "after_hash must be sha256:<hex>."))
    if _proposal_after_hash(proposal) != proposal.get("after_hash"):
        alerts.append(
            _alert("BLOCK", "after_hash_mismatch", "after_hash does not match proposed content.")
        )
    if _validity_expired(proposal.get("validity_window")):
        alerts.append(_alert("BLOCK", "proposal_expired", "validity window is expired."))
    if proposal.get("approval_class") != "human_exact":
        alerts.append(
            _alert("BLOCK", "human_approval_required", "V1 requires exact human approval.")
        )
    return alerts


def _decision_alerts(
    proposal: dict[str, Any],
    decision: dict[str, Any],
    *,
    proposal_hash: str,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if decision.get("schema") != REFINEMENT_DECISION_SCHEMA:
        alerts.append(_alert("BLOCK", "invalid_decision_schema", "Decision schema is invalid."))
    if decision.get("decision") != "APPROVED":
        alerts.append(_alert("BLOCK", "decision_not_approved", "Decision must be APPROVED."))
    actor = decision.get("actor")
    if not isinstance(actor, dict) or actor.get("type") != "human":
        alerts.append(_alert("BLOCK", "approval_actor_not_human", "Approval actor must be human."))
    exact = {
        "proposal_id": proposal.get("proposal_id"),
        "proposal_sha256": proposal_hash,
        "idempotency_key": proposal.get("idempotency_key"),
        "target_ref_hash": _target_ref_hash(proposal),
        "before_hash": proposal.get("before_hash"),
        "after_hash": proposal.get("after_hash"),
        "goal_hash": proposal.get("goal_hash"),
        "policy_version": proposal.get("policy_version"),
        "data_boundary_version": proposal.get("data_boundary_version"),
        "redaction_version": proposal.get("redaction_version"),
        "approval_class": proposal.get("approval_class"),
    }
    for key, expected in exact.items():
        if decision.get(key) != expected:
            alerts.append(
                _alert("BLOCK", f"decision_{key}_mismatch", f"Decision {key} is not exact.")
            )
    if _validity_expired(decision.get("validity_window")):
        alerts.append(_alert("BLOCK", "decision_expired", "Decision validity window is expired."))
    return alerts


def _target_snapshot(
    proposal: dict[str, Any],
    *,
    memory_url: str,
    memory_auth_token: str | None,
    allow_offline: bool,
) -> TargetSnapshot:
    kind = proposal.get("kind")
    target = proposal.get("target_ref") if isinstance(proposal.get("target_ref"), dict) else {}
    if kind == "memory_document":
        try:
            return _memory_snapshot(
                memory_url,
                str(target.get("collection") or ""),
                str(target.get("key") or ""),
                memory_auth_token,
            )
        except (httpx.HTTPError, json.JSONDecodeError, RefinementError) as exc:
            if allow_offline:
                return TargetSnapshot(
                    hash=EMPTY_TARGET_HASH,
                    exists=False,
                    content=None,
                    raw={"offline": True, "error": str(exc)},
                )
            raise
    if kind == "supplemental_prompt":
        return _prompt_snapshot(Path(str(target.get("path") or "")).expanduser())
    return TargetSnapshot(hash=EMPTY_TARGET_HASH, exists=False, content=None)


def _memory_snapshot(
    memory_url: str,
    collection: str,
    key: str,
    memory_auth_token: str | None,
) -> TargetSnapshot:
    if not collection or not key:
        raise RefinementError("memory target requires collection and key")
    payload = _memory_list(memory_url, collection, key, memory_auth_token)
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        return TargetSnapshot(hash=EMPTY_TARGET_HASH, exists=False, content=None, raw=payload)
    doc = documents[0]
    if not isinstance(doc, dict):
        raise RefinementError("memory /list document must be an object")
    content = _stable_memory_content(doc)
    return TargetSnapshot(
        hash=_hash_memory_content(content),
        exists=True,
        content=content,
        source_version=str(doc.get("_rev")) if doc.get("_rev") else None,
        raw=doc,
    )


def _prompt_snapshot(path: Path) -> TargetSnapshot:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return TargetSnapshot(hash=EMPTY_TARGET_HASH, exists=False, content=None)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = resolved.read_text(encoding="utf-8")
    return TargetSnapshot(hash=_hash_prompt_content(payload), exists=True, content=payload)


def _apply_target(
    proposal: dict[str, Any], *, memory_url: str, memory_auth_token: str | None
) -> None:
    target = proposal["target_ref"]
    if proposal["kind"] == "memory_document":
        _memory_upsert(
            memory_url,
            str(target["collection"]),
            [proposal["proposed_content"]],
            memory_auth_token=memory_auth_token,
        )
        return
    if proposal["kind"] == "supplemental_prompt":
        path = Path(str(target["path"])).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical_json(proposal["proposed_content"]), encoding="utf-8")
        return
    raise RefinementError(f"unsupported target kind: {proposal['kind']}")


def _restore_target(
    proposal: dict[str, Any],
    before_content: Any,
    *,
    memory_url: str,
    memory_auth_token: str | None,
) -> None:
    target = proposal["target_ref"]
    if proposal["kind"] == "memory_document":
        if before_content is None:
            raise RefinementError("cannot delete Memory documents through v1 rollback")
        _memory_upsert(
            memory_url,
            str(target["collection"]),
            [before_content],
            memory_auth_token=memory_auth_token,
        )
        return
    path = Path(str(target["path"])).expanduser().resolve()
    if before_content is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(before_content), encoding="utf-8")


def _memory_list(
    memory_url: str,
    collection: str,
    key: str,
    memory_auth_token: str | None,
) -> dict[str, Any]:
    headers = {"X-Caller-Skill": "tau-refinement"}
    if memory_auth_token:
        headers["Authorization"] = f"Bearer {memory_auth_token}"
    with httpx.Client(
        base_url=memory_url.rstrip("/"), timeout=httpx.Timeout(10.0, connect=2.0)
    ) as client:
        response = client.post(
            "/list",
            json={"collection": collection, "filters": {"_key": key}, "limit": 1},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RefinementError("memory /list returned a non-object payload")
    return payload


def _memory_upsert(
    memory_url: str,
    collection: str,
    documents: list[dict[str, Any]],
    *,
    memory_auth_token: str | None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    headers = {"X-Caller-Skill": "tau-refinement"}
    if memory_auth_token:
        headers["Authorization"] = f"Bearer {memory_auth_token}"
    with httpx.Client(
        base_url=memory_url.rstrip(),
        timeout=httpx.Timeout(timeout_seconds, connect=2.0),
    ) as client:
        response = client.post(
            "/upsert",
            json={"collection": collection, "documents": documents},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {"status_code": response.status_code}
    if not isinstance(payload, dict):
        raise RefinementError("memory /upsert returned a non-object payload")
    return payload


def _load_ledger(ledger_dir: Path) -> dict[str, Any]:
    path = _ledger_path(ledger_dir)
    if not path.exists():
        return {"schema": REFINEMENT_LEDGER_SCHEMA, "proposals": {}, "journal": [], "effects": {}}
    payload = _read_json_object(path, label="refinement ledger")
    if payload.get("schema") != REFINEMENT_LEDGER_SCHEMA:
        raise RefinementError(f"ledger schema must be {REFINEMENT_LEDGER_SCHEMA}: {path}")
    payload.setdefault("proposals", {})
    payload.setdefault("journal", [])
    payload.setdefault("effects", {})
    return payload


def _save_ledger(ledger_dir: Path, ledger: dict[str, Any]) -> None:
    _write_json(_ledger_path(ledger_dir), ledger)


def _ledger_path(ledger_dir: Path) -> Path:
    return ledger_dir.expanduser().resolve() / "refinement-ledger.json"


def _journal(
    ledger: dict[str, Any],
    proposal: dict[str, Any],
    state: RefinementState,
    *,
    proposal_hash: str,
) -> None:
    proposal_id = str(proposal["proposal_id"])
    current = ledger["proposals"].get(proposal_id, {})
    current.update(proposal)
    current["proposal_sha256"] = proposal_hash
    current["state"] = state.value
    current["diff_state"] = (
        "rendered"
        if state in {RefinementState.DIFF_RENDERED, RefinementState.VALIDATED}
        else current.get("diff_state")
    )
    ledger["proposals"][proposal_id] = current
    transition_id = _hash_json(
        {
            "proposal_id": proposal_id,
            "proposal_sha256": proposal_hash,
            "state": state.value,
            "idempotency_key": proposal.get("idempotency_key"),
        }
    )
    if any(row.get("transition_id") == transition_id for row in ledger["journal"]):
        return
    ledger["journal"].append(
        {
            "transition_id": transition_id,
            "proposal_id": proposal_id,
            "proposal_sha256": proposal_hash,
            "state": state.value,
            "idempotency_key": proposal.get("idempotency_key"),
            "timestamp": _utc_stamp(),
        }
    )


def _render_diff(proposal: dict[str, Any], before: TargetSnapshot) -> dict[str, Any]:
    before_lines = (
        _canonical_json(before.content).splitlines() if before.content is not None else []
    )
    after_lines = _canonical_json(proposal.get("proposed_content")).splitlines()
    return {
        "schema": REFINEMENT_DIFF_SCHEMA,
        "proposal_id": proposal.get("proposal_id"),
        "target_ref": proposal.get("target_ref"),
        "before_hash": before.hash,
        "after_hash": proposal.get("after_hash"),
        "unified_diff": list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        ),
        "scope": proposal.get("scope"),
        "risk": proposal.get("risks"),
        "approval_class": proposal.get("approval_class"),
        "verification_plan": proposal.get("verification_plan"),
        "rollback": "restore recorded before_content by adapter CAS",
    }


def _duplicate_alert(
    ledger: dict[str, Any],
    proposal: dict[str, Any],
    proposal_hash: str,
) -> dict[str, Any] | None:
    for existing in ledger["proposals"].values():
        if existing.get("proposal_id") == proposal.get("proposal_id"):
            if existing.get("proposal_sha256") != proposal_hash:
                return _alert(
                    "BLOCK",
                    "proposal_id_reused_with_different_hash",
                    "proposal_id already exists with a different hash.",
                )
            return None
        if (
            existing.get("kind") == proposal.get("kind")
            and existing.get("target_ref") == proposal.get("target_ref")
            and existing.get("after_hash") == proposal.get("after_hash")
            and existing.get("state") in ACTIVE_STATES
        ):
            return _alert(
                "BLOCK", "duplicate_active_proposal", "Equivalent active proposal exists."
            )
    return None


def _apply_receipt(
    proposal: dict[str, Any],
    *,
    proposal_file: Path,
    proposal_hash: str,
    decision_file: Path,
    receipt_path: Path,
    status: str,
    alerts: list[dict[str, Any]],
    before: TargetSnapshot,
    after: TargetSnapshot,
    mutation_performed: bool,
    idempotent_replay: bool,
    memory_url: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": REFINEMENT_APPLY_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "proposal": str(proposal_file),
        "proposal_sha256": proposal_hash,
        "decision": str(decision_file),
        "decision_sha256": _file_hash(decision_file),
        "proposal_id": proposal.get("proposal_id"),
        "idempotency_key": proposal.get("idempotency_key"),
        "kind": proposal.get("kind"),
        "scope": proposal.get("scope"),
        "target_ref": proposal.get("target_ref"),
        "before_hash": before.hash,
        "readback_hash": after.hash,
        "expected_after_hash": proposal.get("after_hash"),
        "mutation_performed": mutation_performed,
        "idempotent_replay": idempotent_replay,
        "memory_url": memory_url if proposal.get("kind") == "memory_document" else None,
        "lifecycle_state": "APPLIED" if status == "PASS" else status,
        "alerts": alerts,
        "mocked": False,
        "live": True,
        "receipt_path": str(receipt_path.expanduser().resolve()),
        "proof_scope": _proof_scope(
            proves=["Exact approval, target before hash, and adapter readback gated mutation."],
            gaps=["Human identity proof beyond decision record fields."],
        ),
        "timestamp": _utc_stamp(),
    }
    if extra:
        payload.update(extra)
    return payload


def _stable_memory_content(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in doc.items()
        if key
        not in {
            "_id",
            "_rev",
            "qdrant_collection",
            "qdrant_point_id",
            "embedding_model",
            "embedding_version",
            "text_hash",
            "semantic_sync_state",
        }
    }


def _proposal_after_hash(proposal: dict[str, Any]) -> str:
    if proposal.get("kind") == "memory_document":
        content = proposal.get("proposed_content")
        return _hash_memory_content(content if isinstance(content, dict) else {})
    if proposal.get("kind") == "supplemental_prompt":
        return _hash_prompt_content(proposal.get("proposed_content"))
    return str(proposal.get("after_hash") or "")


def _hash_memory_content(content: dict[str, Any]) -> str:
    return _hash_json(_stable_memory_content(content))


def _hash_prompt_content(content: Any) -> str:
    return _hash_json(content)


def _hash_json(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    resolved = path.expanduser().resolve()
    return "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _target_ref_hash(proposal: dict[str, Any]) -> str:
    return _hash_json(proposal.get("target_ref"))


def _valid_hash(value: Any) -> bool:
    if value == EMPTY_TARGET_HASH:
        return True
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


def _validity_expired(window: Any) -> bool:
    if not isinstance(window, dict):
        return True
    until = window.get("not_after")
    if not isinstance(until, str):
        return True
    try:
        parsed = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed <= datetime.now(UTC)


def _prompt_policy_alert(text: str) -> bool:
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in [
            "ignore policy",
            "bypass validation",
            "override immutable goal",
            "self-approve",
            "mint approval",
        ]
    )


def _rollback_state(ledger: dict[str, Any], proposal: dict[str, Any]) -> str:
    effect = ledger["effects"].get(str(proposal.get("idempotency_key") or ""))
    if proposal.get("state") == RefinementState.ROLLED_BACK.value:
        return "rolled_back"
    if effect:
        return "available"
    return "not_applied"


def _alert(
    level: str, code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, "details": details or {}}


def _proof_scope(*, proves: list[str], gaps: list[str]) -> dict[str, list[str]]:
    return {"proves": proves, "does_not_prove": gaps}


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefinementError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RefinementError(f"{label} must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(_canonical_json(payload), encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
