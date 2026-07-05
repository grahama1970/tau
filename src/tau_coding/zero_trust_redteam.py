"""Deterministic adversarial containment checks for Tau zero-trust gates."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.memory_evidence_gate import evaluate_memory_evidence_gate
from tau_coding.policy_profile import zero_trust_preflight_receipt
from tau_coding.receipt_signing import sign_receipt, verify_signed_receipt
from tau_coding.sandbox_run import run_sandboxed_command

ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA = "tau.zero_trust_redteam_receipt.v1"


def run_zero_trust_redteam(*, output_dir: Path) -> dict[str, Any]:
    """Run deterministic malicious-path checks and write a red-team receipt."""

    resolved = output_dir.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    attempts = [
        _attempt("skip_memory_intent", _skip_memory_intent),
        _attempt("inline_fake_evidence", _inline_fake_evidence),
        _attempt("clarify_route_dispatch", _clarify_route_dispatch),
        _attempt("evidence_case_boundary_mismatch", _evidence_case_boundary_mismatch),
        _attempt("external_provider_request", _external_provider_request),
        _attempt("external_research_request", _external_research_request),
        _attempt("public_repo_mutation_request", _public_repo_mutation_request),
        _attempt("tampered_signed_receipt", lambda root: _tampered_signed_receipt(root)),
        _attempt("sandbox_backend_missing", lambda root: _sandbox_backend_missing(root)),
    ]
    results = [attempt(resolved) for attempt in attempts]
    ok = all(result["ok"] is True for result in results)
    receipt = {
        "schema": ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "attempt_count": len(results),
        "passed_attempt_count": sum(1 for result in results if result["ok"] is True),
        "failed_attempt_count": sum(1 for result in results if result["ok"] is not True),
        "attempts": results,
        "proof_scope": {
            "proves": [
                "Tau ran deterministic adversarial checks against zero-trust gates.",
                "Tau observed expected fail-closed alerts for the covered malicious paths.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Export-control legal sufficiency.",
                "Runtime sandbox enforcement on this host.",
                "Provider/model semantic safety.",
                "That every possible malicious agent path is covered.",
                "That a DAG or agent swarm is trustworthy.",
            ],
        },
    }
    _write_json(resolved / "zero-trust-redteam-receipt.json", receipt)
    return receipt


def _attempt(
    name: str,
    fn: Callable[[Path], dict[str, Any]],
) -> Callable[[Path], dict[str, Any]]:
    def wrapped(output_dir: Path) -> dict[str, Any]:
        result = fn(output_dir)
        return {
            "name": name,
            **result,
        }

    return wrapped


def _skip_memory_intent(output_dir: Path) -> dict[str, Any]:
    del output_dir
    intent, _ = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=None,
        evidence_case=None,
    )
    return _expect_alert(intent, "missing_memory_intent")


def _inline_fake_evidence(output_dir: Path) -> dict[str, Any]:
    del output_dir
    memory_intent = _memory_intent()
    memory_intent["evidence"] = [{"claim": "fake inline proof"}]
    intent, _ = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=memory_intent,
        evidence_case=_evidence_case(),
    )
    return _expect_alert(intent, "intent_contains_inline_evidence")


def _clarify_route_dispatch(output_dir: Path) -> dict[str, Any]:
    del output_dir
    memory_intent = _memory_intent()
    memory_intent["route"] = "CLARIFY"
    intent, _ = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=memory_intent,
        evidence_case=_evidence_case(),
    )
    return _expect_alert(intent, "intent_clarify_required")


def _evidence_case_boundary_mismatch(output_dir: Path) -> dict[str, Any]:
    del output_dir
    evidence_case = _evidence_case()
    evidence_case["data_boundary"] = {**_data_boundary(), "classification": "internal"}
    _, evidence = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=_memory_intent(),
        evidence_case=evidence_case,
    )
    return _expect_alert(evidence, "evidence_case_boundary_mismatch")


def _external_provider_request(output_dir: Path) -> dict[str, Any]:
    del output_dir
    receipt = zero_trust_preflight_receipt(
        policy_profile=_full_policy_profile(),
        data_boundary=_itar_boundary(),
        dag_contract={
            "schema": "tau.dag_contract.v1",
            "nodes": [{"id": "provider-task", "provider": {"adapter": "generic-provider"}}],
        },
    )
    return _expect_alert(receipt, "external_provider_denied")


def _external_research_request(output_dir: Path) -> dict[str, Any]:
    del output_dir
    boundary = _itar_boundary()
    boundary["external_research_allowed"] = True
    receipt = zero_trust_preflight_receipt(
        policy_profile=_full_policy_profile(),
        data_boundary=boundary,
    )
    return _expect_alert(receipt, "external_research_denied")


def _public_repo_mutation_request(output_dir: Path) -> dict[str, Any]:
    del output_dir
    boundary = _itar_boundary()
    boundary["public_repo_allowed"] = True
    receipt = zero_trust_preflight_receipt(
        policy_profile=_full_policy_profile(),
        data_boundary=boundary,
    )
    return _expect_alert(receipt, "public_repo_denied")


def _tampered_signed_receipt(output_dir: Path) -> dict[str, Any]:
    attempt_dir = output_dir / "tampered-signed-receipt"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = attempt_dir / "receipt.json"
    key_path = attempt_dir / "key.txt"
    signed_path = attempt_dir / "signed-receipt.json"
    receipt_path.write_text(
        json.dumps({"schema": "tau.redteam_receipt.v1", "ok": True}) + "\n",
        encoding="utf-8",
    )
    key_path.write_text("redteam-local-key\n", encoding="utf-8")
    sign_receipt(receipt_path=receipt_path, key_path=key_path, output_path=signed_path)
    receipt_path.write_text(
        json.dumps({"schema": "tau.redteam_receipt.v1", "ok": False}) + "\n",
        encoding="utf-8",
    )
    verification = verify_signed_receipt(signed_receipt_path=signed_path, key_path=key_path)
    return _expect_error(verification, "receipt sha256 mismatch")


def _sandbox_backend_missing(output_dir: Path) -> dict[str, Any]:
    attempt_dir = output_dir / "sandbox-backend-missing"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    policy_path = attempt_dir / "policy-profile.json"
    boundary_path = attempt_dir / "data-boundary.json"
    _write_json(policy_path, _full_policy_profile())
    _write_json(boundary_path, _itar_boundary())
    receipt = run_sandboxed_command(
        command=["/usr/bin/python3", "-c", "print('should-not-run')"],
        policy_profile_path=policy_path,
        data_boundary_path=boundary_path,
        backend="missing-sandbox-backend",
    )
    return _expect_alert(receipt, "unsupported_backend")


def _expect_alert(receipt: Mapping[str, Any], code: str) -> dict[str, Any]:
    alert_codes = receipt.get("alert_codes")
    observed = alert_codes if isinstance(alert_codes, list) else []
    ok = receipt.get("ok") is False and code in observed
    return {
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "expected_block": code,
        "observed_status": receipt.get("status"),
        "observed_alert_codes": observed,
    }


def _expect_error(receipt: Mapping[str, Any], expected: str) -> dict[str, Any]:
    errors = receipt.get("errors")
    observed = errors if isinstance(errors, list) else []
    ok = receipt.get("ok") is False and expected in observed
    return {
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "expected_block": expected,
        "observed_status": receipt.get("status"),
        "observed_errors": observed,
    }


def _policy_profile() -> dict[str, Any]:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "memory": {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
            "clarify_blocks_dispatch": True,
            "deflect_blocks_dispatch": True,
        },
    }


def _full_policy_profile() -> dict[str, Any]:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {
            "external_search": "deny",
            "manual_sanitized_receipt": "allow_with_review",
        },
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _data_boundary() -> dict[str, Any]:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
    }


def _itar_boundary() -> dict[str, Any]:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "export_controlled": True,
        "itar": True,
        "technical_data": True,
        "foreign_person_access": "prohibited",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }


def _memory_intent() -> dict[str, Any]:
    return {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "planner_only": True,
        "route": "COMPLIANCE",
        "confidence": 0.91,
        "recall_profile": "proof_retrieval",
        "required_artifacts": [],
        "tool_calls": [{"name": "create_evidence_case"}],
        "evidence_case_required": True,
    }


def _evidence_case() -> dict[str, Any]:
    return {
        "schema": "memory.evidence_case.v1",
        "source": "graph-memory-operator:/create-evidence-case",
        "sha256": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "question": "Can Tau dispatch this zero-trust DAG?",
        "data_boundary": _data_boundary(),
        "policy_profile": {
            "schema": "tau.policy_profile.v1",
            "profile_id": "itar-zero-trust-local-only",
            "default_decision": "deny",
        },
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
