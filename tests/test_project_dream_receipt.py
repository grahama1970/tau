from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from tau_coding.project_dream_receipt import validate_project_dream_receipt_path


def test_project_dream_shadow_receipt_validates(tmp_path: Path) -> None:
    receipt = _write_project_dream_case(tmp_path, "shadow")

    result = validate_project_dream_receipt_path(receipt)

    assert result["status"] == "PASS"
    assert result["errors"] == []


def test_project_dream_promoted_requires_approval_cas_and_head_readback(tmp_path: Path) -> None:
    receipt = _write_project_dream_case(tmp_path, "promoted", status="PROMOTED")

    result = validate_project_dream_receipt_path(receipt)

    assert result["status"] == "PASS"


def test_project_dream_rejects_tampered_evidence_hash(tmp_path: Path) -> None:
    receipt = _write_project_dream_case(tmp_path, "tampered-hash")
    payload = json.loads(receipt.read_text())
    payload["evidence_packet"]["sha256"] = "sha256:" + "0" * 64
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    result = validate_project_dream_receipt_path(receipt)

    assert result["status"] == "BLOCKED"
    assert any("evidence_packet.sha256 mismatch" in error for error in result["errors"])


def test_project_dream_rejects_false_promotion_without_effect(tmp_path: Path) -> None:
    receipt = _write_project_dream_case(tmp_path, "false-promoted")
    payload = json.loads(receipt.read_text())
    payload["terminal_status"] = "PROMOTED"
    payload["promotion_policy"] = {
        "policy_class": "human_approval_required",
        "decision": "approved_by_human",
        "human_approval_required": True,
    }
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    result = validate_project_dream_receipt_path(receipt)

    assert result["status"] == "BLOCKED"
    assert any(
        "PROMOTED requires exactly one accepted effect" in error for error in result["errors"]
    )


def test_project_dream_rejects_wrong_project_approval(tmp_path: Path) -> None:
    receipt = _write_project_dream_case(tmp_path, "wrong-approval", status="PROMOTED")
    payload = json.loads(receipt.read_text())
    approval_path = tmp_path / "wrong-approval" / payload["approval_receipt"]["path"]
    approval = json.loads(approval_path.read_text())
    approval["project_id"] = "other-project"
    _rewrite_artifact(approval_path, approval)
    payload["approval_receipt"]["sha256"] = (
        "sha256:" + hashlib.sha256(approval_path.read_bytes()).hexdigest()
    )
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    result = validate_project_dream_receipt_path(receipt)

    assert result["status"] == "BLOCKED"
    assert any("approval_receipt.project_id" in error for error in result["errors"])


def _write_project_dream_case(tmp_path: Path, name: str, *, status: str = "SHADOW_STAGED") -> Path:
    root = tmp_path / name
    root.mkdir()
    candidate_digest = "sha256:candidate"
    refs = {
        "policy_profile": _artifact(
            root, "policy-profile.json", {"schema": "tau.policy_profile.v1"}
        ),
        "data_boundary": _artifact(
            root,
            "data-boundary.json",
            {
                "schema": "tau.data_boundary.v1",
                "allowed_projects": ["tau"],
                "allowed_paths": [str(root.resolve())],
            },
        ),
        "transcript_corpus": _artifact(
            root,
            "transcript-corpus.json",
            {"schema": "tau.transcript_corpus.v1", "includes_dream_worker_transcript": False},
        ),
        "archival_compaction": _artifact(
            root, "archival-compaction.json", {"schema": "tau.archival_compaction.v1"}
        ),
        "project_state": _artifact(root, "project-state.json", {"schema": "tau.project_state.v1"}),
        "code_ingest": _artifact(
            root,
            "code-ingest.json",
            {
                "schema": "tau.code_ingest_manifest.v1",
                "git_commit": "abc123",
                "coverage_scope": "full",
                "reconciliation_eligible": True,
            },
            extra={
                "git_commit": "abc123",
                "coverage_scope": "full",
                "reconciliation_eligible": True,
            },
        ),
        "evidence_packet": _artifact(
            root,
            "evidence-packet.json",
            {"schema": "tau.evidence_packet.v1", "input_digest": "sha256:input"},
        ),
        "command_spec": _artifact(
            root,
            "command-spec.json",
            {
                "schema": "tau.opencode_command_spec.v1",
                "declared_capabilities": ["filesystem.read"],
                "uses_capabilities": ["filesystem.read"],
            },
        ),
        "provider_readiness": _artifact(
            root, "provider-readiness.json", {"schema": "tau.provider_readiness_receipt.v1"}
        ),
        "prompt_schema_policy": _artifact(
            root, "prompt-policy.json", {"schema": "tau.prompt_schema_policy.v1"}
        ),
        "raw_model_output": _artifact(
            root, "raw-model-output.json", {"schema": "tau.raw_model_output.v1"}
        ),
        "candidate": _artifact(
            root,
            "candidate.json",
            {"schema": "tau.project_dream_candidate.v1", "candidate_digest": candidate_digest},
        ),
        "deterministic_validation": _artifact(
            root,
            "validation.json",
            {
                "schema": "tau.project_dream_deterministic_validation.v1",
                "candidate_digest": candidate_digest,
                "candidate_count": 1,
                "terminal_row_count": 1,
            },
        ),
        "stage_response": _artifact(
            root,
            "stage-response.json",
            {
                "schema": "graph_memory.project_topic_stage_receipt.v1",
                "candidate_digest": candidate_digest,
            },
        ),
    }
    payload = {
        "schema": "tau.project_dream_receipt.v1",
        "run_id": f"run-{name}",
        "attempt_id": "attempt-001",
        "project_id": "tau",
        "immutable_goal_hash": "sha256:goal",
        "proof_scope": "project-memory dream receipt replay",
        "provider": {"provider": "fixture", "model": "deterministic"},
        "candidate_digest": candidate_digest,
        "terminal_status": status,
        "first_failed_gate": None,
        "promotion_policy": {
            "policy_class": "shadow_only",
            "decision": "shadow_only",
            "human_approval_required": False,
        },
        "aggregate": {
            "expected_candidate_count": 1,
            "observed_candidate_count": 1,
            "expected_terminal_row_count": 1,
            "observed_terminal_row_count": 1,
        },
        "accepted_effect_count": 0,
        "accepted_effects": [],
        "effects": [],
        "telemetry": {
            "status": "unknown",
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
        },
        "explicit_non_claims": [
            "A receipt does not prove semantic truth.",
            "Model agreement is not a trust anchor.",
            "Receipt existence is not a trust anchor.",
        ],
        **refs,
    }
    if status == "PROMOTED":
        approval = _artifact(
            root,
            "approval.json",
            {
                "schema": "tau.project_dream_approval_receipt.v1",
                "decision": "approved",
                "project_id": "tau",
                "candidate_digest": candidate_digest,
                "expires_at": "2999-01-01T00:00:00Z",
            },
        )
        promotion = _artifact(
            root,
            "promotion.json",
            {
                "schema": "graph_memory.project_topic_promotion_receipt.v1",
                "status": "PASS",
                "cas_outcome": "APPLIED",
                "candidate_digest": candidate_digest,
                "head_before_generation": 1,
                "head_before_digest": "sha256:before",
                "head_after_generation": 2,
                "head_after_digest": "sha256:after",
            },
        )
        head = _artifact(
            root,
            "head-readback.json",
            {
                "schema": "graph_memory.project_topic_head_readback.v1",
                "generation": 2,
                "digest": "sha256:after",
            },
        )
        payload.update(
            {
                "promotion_policy": {
                    "policy_class": "human_approval_required",
                    "decision": "approved_by_human",
                    "human_approval_required": True,
                },
                "approval_receipt": approval,
                "promotion_receipt": promotion,
                "head_readback": head,
                "expected_head": {
                    "before_generation": 1,
                    "before_digest": "sha256:before",
                    "after_generation": 2,
                    "after_digest": "sha256:after",
                },
                "accepted_effect_count": 1,
                "accepted_effects": [{"type": "graph_memory_cas", "project_id": "tau"}],
                "effects": [
                    {
                        "type": "project_topic_head",
                        "project_id": "tau",
                        "path": str(root / "head-readback.json"),
                    }
                ],
            }
        )
    receipt = root / "project-dream-receipt.json"
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return receipt


def _artifact(
    root: Path, filename: str, payload: dict[str, object], *, extra: dict[str, object] | None = None
) -> dict[str, object]:
    path = root / filename
    _rewrite_artifact(path, payload)
    ref: dict[str, object] = {
        "path": filename,
        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema": payload["schema"],
    }
    if extra:
        ref.update(copy.deepcopy(extra))
    return ref


def _rewrite_artifact(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
