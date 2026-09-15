#!/usr/bin/env python3
"""Real-world retained eval for tau.project_dream_receipt.v1 validation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tau_coding.project_dream_receipt import validate_project_dream_receipt_path
from tau_coding.proof_index import build_proof_index
from tau_coding.run_report import write_run_report

Mutation = Callable[[dict[str, Any], Path], None]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    out = args.out.expanduser().resolve()
    root = out.parent / "project-dream-work"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    positives = [
        _run_case(root, "positive-shadow", "SHADOW_STAGED"),
        _run_case(root, "positive-no-change", "NO_CHANGE"),
        _run_case(root, "positive-review", "NEEDS_HUMAN_REVIEW"),
        _run_case(root, "positive-promoted", "PROMOTED"),
        _run_case(root, "positive-rolled-back", "ROLLED_BACK"),
    ]
    adversarial_specs: list[tuple[str, str, Mutation, str]] = [
        (
            "mutated-evidence-hash",
            "SHADOW_STAGED",
            lambda p, _r: p["evidence_packet"].update(sha256="sha256:" + "0" * 64),
            "evidence_packet.sha256 mismatch",
        ),
        (
            "undeclared-command-capability",
            "SHADOW_STAGED",
            _undeclared_capability,
            "undeclared capabilities",
        ),
        (
            "false-promoted",
            "SHADOW_STAGED",
            lambda p, _r: p.update(terminal_status="PROMOTED"),
            "PROMOTED requires exactly one accepted effect",
        ),
        (
            "approval-missing",
            "PROMOTED",
            lambda p, _r: p.pop("approval_receipt"),
            "approval_receipt ref is required",
        ),
        (
            "approval-wrong-project",
            "PROMOTED",
            _wrong_approval_project,
            "approval_receipt.project_id",
        ),
        (
            "approval-wrong-candidate",
            "PROMOTED",
            _wrong_approval_candidate,
            "approval_receipt.candidate_digest",
        ),
        ("approval-expired", "PROMOTED", _expired_approval, "approval_receipt is expired"),
        ("cas-conflict", "PROMOTED", _cas_conflict, "Graph Memory CAS"),
        ("head-after-mismatch", "PROMOTED", _head_mismatch, "head mismatch"),
        (
            "duplicate-accepted-effects",
            "PROMOTED",
            lambda p, _r: p.update(accepted_effect_count=2),
            "accepted_effect_count mismatch",
        ),
        (
            "incremental-deprecation",
            "SHADOW_STAGED",
            _incremental_deprecation,
            "incremental ingest",
        ),
        (
            "dream-worker-transcript",
            "SHADOW_STAGED",
            _dream_worker_transcript,
            "dream worker transcript",
        ),
        (
            "cross-project-escape",
            "PROMOTED",
            lambda p, _r: p["effects"][0].update(project_id="other"),
            "escapes data boundary",
        ),
        (
            "aggregate-terminal-row-omitted",
            "SHADOW_STAGED",
            lambda p, _r: p["aggregate"].update(observed_terminal_row_count=0),
            "terminal_row_count mismatch",
        ),
        (
            "rollback-deletes-history",
            "ROLLED_BACK",
            _rollback_deletes_history,
            "rollback must not delete",
        ),
        (
            "telemetry-unknown-zero",
            "SHADOW_STAGED",
            lambda p, _r: p["telemetry"].update(cost_usd=0),
            "must be unknown/null",
        ),
        (
            "contradictory-stage-ref",
            "SHADOW_STAGED",
            _contradictory_stage,
            "stage_response.candidate_digest",
        ),
    ]
    adversarial = [
        _run_case(root, name, status, mutate=mutate, expect_error=needle)
        for name, status, mutate, needle in adversarial_specs
    ]

    index_receipt = build_proof_index(root, output_path=root / "proof-index.jsonl")
    report_receipt = write_run_report(run_dir=root, out_path=root / "run-report.html", force=True)
    report_html = (root / "run-report.html").read_text(encoding="utf-8")
    proof_index_text = (root / "proof-index.jsonl").read_text(encoding="utf-8")
    errors = []
    errors.extend(item["error"] for item in positives + adversarial if item.get("error"))
    if "Project Dream Gate Chain" not in report_html:
        errors.append("run report did not render project dream gate chain")
    if "tau.project_dream_receipt.v1" not in proof_index_text:
        errors.append("proof index did not include project dream receipts")

    proof = {
        "schema": "tau.project_dream_agentic_eval_proof.v1",
        "status": "PASS" if not errors else "FAIL",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "positive_cases": positives,
        "adversarial_cases": adversarial,
        "proof_index_receipt": index_receipt,
        "run_report_receipt": report_receipt,
        "report_contains_project_dream_gate_chain": "Project Dream Gate Chain" in report_html,
        "proof_index_contains_project_dream_receipts": "tau.project_dream_receipt.v1"
        in proof_index_text,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau validates project-dream receipts from retained hash-bound fixtures.",
                "Tau rejects adversarial promotion, approval, CAS, boundary, aggregate, "
                "telemetry, and contradictory-reference claims.",
                "Tau proof-index and static-report paths retain project-dream gate-chain "
                "and non-claim evidence.",
            ],
            "does_not_prove": [
                "The model-generated project knowledge is semantically true.",
                "Production Graph Memory persistence.",
                "Provider/model quality.",
            ],
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": proof["status"], "out": str(out), "errors": errors}, indent=2))
    return 0 if not errors else 1


def _run_case(
    root: Path,
    name: str,
    status: str,
    mutate: Mutation | None = None,
    expect_error: str | None = None,
) -> dict[str, Any]:
    case_root = root / name
    case_root.mkdir()
    receipt = _case(case_root, name, status)
    payload = json.loads(receipt.read_text())
    if mutate is not None:
        mutate(payload, case_root)
        receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    validation = validate_project_dream_receipt_path(
        receipt, output_path=case_root / "validation-receipt.json"
    )
    expected_pass = expect_error is None
    ok = validation["ok"] is expected_pass
    if expect_error is not None:
        ok = ok and any(expect_error in error for error in validation["errors"])
    return {
        "name": name,
        "terminal_status": status,
        "receipt_path": str(receipt),
        "validation_path": str(case_root / "validation-receipt.json"),
        "validation_status": validation["status"],
        "expected_error": expect_error,
        "error": None if ok else {"expected_pass": expected_pass, "validation": validation},
    }


def _case(root: Path, name: str, status: str) -> Path:
    digest = f"sha256:{hashlib.sha256(name.encode()).hexdigest()}"
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
            {"schema": "tau.project_dream_candidate.v1", "candidate_digest": digest},
        ),
        "deterministic_validation": _artifact(
            root,
            "validation.json",
            {
                "schema": "tau.project_dream_deterministic_validation.v1",
                "candidate_digest": digest,
                "candidate_count": 1,
                "terminal_row_count": 1,
            },
        ),
        "stage_response": _artifact(
            root,
            "stage-response.json",
            {"schema": "graph_memory.project_topic_stage_receipt.v1", "candidate_digest": digest},
        ),
    }
    payload: dict[str, Any] = {
        "schema": "tau.project_dream_receipt.v1",
        "run_id": f"run-{name}",
        "attempt_id": "attempt-001",
        "project_id": "tau",
        "immutable_goal_hash": "sha256:goal",
        "proof_scope": "project-memory dream receipt replay",
        "provider": {"provider": "fixture", "model": "deterministic"},
        "candidate_digest": digest,
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
            "duration_ms": None,
        },
        "explicit_non_claims": [
            "A receipt does not prove semantic truth.",
            "Model agreement is not a trust anchor.",
            "Receipt existence is not a trust anchor.",
        ],
        **refs,
    }
    if status == "NEEDS_HUMAN_REVIEW":
        payload["promotion_policy"] = {
            "policy_class": "human_approval_required",
            "decision": "requires_human_approval",
            "human_approval_required": True,
        }
    if status == "NO_CHANGE":
        payload["aggregate"].update(expected_candidate_count=0, observed_candidate_count=0)
        _rewrite(
            root / "validation.json",
            {
                "schema": "tau.project_dream_deterministic_validation.v1",
                "candidate_digest": digest,
                "candidate_count": 0,
                "terminal_row_count": 1,
            },
        )
        payload["deterministic_validation"]["sha256"] = _digest(root / "validation.json")
    if status == "PROMOTED":
        _promoted(payload, root, digest)
    if status == "ROLLED_BACK":
        payload["rollback_receipt"] = _artifact(
            root,
            "rollback.json",
            {
                "schema": "graph_memory.project_topic_rollback_receipt.v1",
                "deletes_intervening_history": False,
            },
        )
        payload.update(
            accepted_effect_count=1,
            accepted_effects=[{"type": "graph_memory_rollback", "project_id": "tau"}],
        )
    receipt = root / "project-dream-receipt.json"
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return receipt


def _promoted(payload: dict[str, Any], root: Path, digest: str) -> None:
    payload.update(
        promotion_policy={
            "policy_class": "human_approval_required",
            "decision": "approved_by_human",
            "human_approval_required": True,
        },
        approval_receipt=_artifact(
            root,
            "approval.json",
            {
                "schema": "tau.project_dream_approval_receipt.v1",
                "decision": "approved",
                "project_id": "tau",
                "candidate_digest": digest,
                "expires_at": "2999-01-01T00:00:00Z",
            },
        ),
        promotion_receipt=_artifact(
            root,
            "promotion.json",
            {
                "schema": "graph_memory.project_topic_promotion_receipt.v1",
                "status": "PASS",
                "cas_outcome": "APPLIED",
                "candidate_digest": digest,
                "head_before_generation": 1,
                "head_before_digest": "sha256:before",
                "head_after_generation": 2,
                "head_after_digest": "sha256:after",
            },
        ),
        head_readback=_artifact(
            root,
            "head-readback.json",
            {
                "schema": "graph_memory.project_topic_head_readback.v1",
                "generation": 2,
                "digest": "sha256:after",
            },
        ),
        expected_head={
            "before_generation": 1,
            "before_digest": "sha256:before",
            "after_generation": 2,
            "after_digest": "sha256:after",
        },
        accepted_effect_count=1,
        accepted_effects=[{"type": "graph_memory_cas", "project_id": "tau"}],
        effects=[
            {
                "type": "project_topic_head",
                "project_id": "tau",
                "path": str(root / "head-readback.json"),
            }
        ],
    )


def _artifact(
    root: Path, filename: str, payload: dict[str, Any], *, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    path = root / filename
    _rewrite(path, payload)
    ref = {"path": filename, "sha256": _digest(path), "schema": payload["schema"]}
    if extra:
        ref.update(copy.deepcopy(extra))
    return ref


def _rewrite(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _mutate_artifact(root: Path, ref: dict[str, Any], update: dict[str, Any]) -> None:
    path = root / ref["path"]
    payload = json.loads(path.read_text())
    payload.update(update)
    _rewrite(path, payload)
    ref["sha256"] = _digest(path)


def _undeclared_capability(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(
        root, payload["command_spec"], {"uses_capabilities": ["filesystem.read", "network"]}
    )


def _wrong_approval_project(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(root, payload["approval_receipt"], {"project_id": "other"})


def _wrong_approval_candidate(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(root, payload["approval_receipt"], {"candidate_digest": "sha256:other"})


def _expired_approval(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(root, payload["approval_receipt"], {"expires_at": "2000-01-01T00:00:00Z"})


def _cas_conflict(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(
        root, payload["promotion_receipt"], {"status": "BLOCKED", "cas_outcome": "CONFLICT"}
    )


def _head_mismatch(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(root, payload["head_readback"], {"digest": "sha256:wrong"})


def _incremental_deprecation(payload: dict[str, Any], root: Path) -> None:
    payload["code_ingest"].update(coverage_scope="incremental")
    _mutate_artifact(
        root,
        payload["code_ingest"],
        {"coverage_scope": "incremental", "candidate": {"claim_type": "deprecation"}},
    )


def _dream_worker_transcript(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(root, payload["transcript_corpus"], {"includes_dream_worker_transcript": True})


def _rollback_deletes_history(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(root, payload["rollback_receipt"], {"deletes_intervening_history": True})


def _contradictory_stage(payload: dict[str, Any], root: Path) -> None:
    _mutate_artifact(root, payload["stage_response"], {"candidate_digest": "sha256:other"})


if __name__ == "__main__":
    raise SystemExit(main())
