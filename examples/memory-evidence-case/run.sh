#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EXAMPLE_DIR}/../.." && pwd)"
OUT="${1:-"${TMPDIR:-/tmp}/tau-memory-evidence-case"}"

rm -rf "${OUT}"
mkdir -p "${OUT}"

cd "${REPO_ROOT}"

uv run python - "${OUT}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

from tau_coding.memory_evidence_gate import (
    EVIDENCE_CASE_GATE_RECEIPT_SCHEMA,
    MEMORY_INTENT_GATE_RECEIPT_SCHEMA,
    evaluate_memory_evidence_gate,
    write_memory_evidence_gate_receipts,
)
from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

out = Path(sys.argv[1]).resolve()
goal_hash = "sha256:memory-evidence-demo-goal"

policy_profile = {
    "schema": POLICY_PROFILE_SCHEMA,
    "profile_id": "memory-evidence-zero-trust",
    "default_decision": "deny",
    "requires_data_boundary": True,
    "network": {"default": "deny", "allowed_domains": []},
    "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
    "research": {
        "external_search": "deny",
        "manual_sanitized_receipt": "allow_with_review",
    },
    "memory": {
        "read": "allow",
        "write": "approval_required",
        "intent_required": True,
        "evidence_case_required_for": ["COMPLIANCE", "RESEARCH", "SUBAGENT"],
        "min_intent_confidence": 0.75,
        "clarify_blocks_dispatch": True,
        "deflect_blocks_dispatch": True,
    },
    "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
    "filesystem": {"write_allowlist": ["."], "read_denylist": []},
}

data_boundary = {
    "schema": DATA_BOUNDARY_SCHEMA,
    "classification": "public",
    "export_controlled": False,
    "itar": False,
    "technical_data": False,
    "foreign_person_access": "allowed",
    "external_provider_allowed": False,
    "external_research_allowed": False,
    "public_repo_allowed": False,
    "notes": ["copyable memory/evidence gate example"],
}

memory_intent = {
    "schema": "memory.intent.v1",
    "goal_hash": goal_hash,
    "memory_first": True,
    "planner_only": True,
    "route": "COMPLIANCE",
    "confidence": 0.91,
    "recall_profile": "proof_retrieval",
    "required_artifacts": ["evidence-case.json"],
    "tool_calls": [{"name": "create_evidence_case"}],
    "evidence_case_required": True,
}

evidence_basis = {
    "question": "Can Tau dispatch this memory-first zero-trust coding route?",
    "artifacts": ["memory-intent.json", "policy-profile.json", "data-boundary.json"],
    "goal_hash": goal_hash,
}
case_hash = "sha256:" + hashlib.sha256(
    json.dumps(evidence_basis, sort_keys=True).encode("utf-8")
).hexdigest()
evidence_case = {
    "schema": "memory.evidence_case.v1",
    "source": "graph-memory-operator:/create-evidence-case",
    "sha256": case_hash,
    "goal_hash": goal_hash,
    "question": evidence_basis["question"],
    "data_boundary": data_boundary,
    "policy_profile": {
        "schema": POLICY_PROFILE_SCHEMA,
        "profile_id": policy_profile["profile_id"],
        "default_decision": policy_profile["default_decision"],
    },
    "support_artifacts": evidence_basis["artifacts"],
}

for name, payload in (
    ("policy-profile.json", policy_profile),
    ("data-boundary.json", data_boundary),
    ("memory-intent.json", memory_intent),
    ("evidence-case.json", evidence_case),
):
    (out / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

intent_receipt, evidence_receipt = evaluate_memory_evidence_gate(
    policy_profile=policy_profile,
    data_boundary=data_boundary,
    memory_intent=memory_intent,
    evidence_case=evidence_case,
    memory_intent_path=out / "memory-intent.json",
    evidence_case_path=out / "evidence-case.json",
)
intent_receipt, evidence_receipt = write_memory_evidence_gate_receipts(
    receipt_dir=out,
    intent_receipt=intent_receipt,
    evidence_receipt=evidence_receipt,
)

ok = (
    intent_receipt.get("schema") == MEMORY_INTENT_GATE_RECEIPT_SCHEMA
    and intent_receipt.get("ok") is True
    and evidence_receipt.get("schema") == EVIDENCE_CASE_GATE_RECEIPT_SCHEMA
    and evidence_receipt.get("ok") is True
    and evidence_receipt.get("allowed_to_dispatch") is True
)
demo = {
    "schema": "tau.memory_evidence_case_example_receipt.v1",
    "ok": ok,
    "status": "PASS" if ok else "BLOCKED",
    "mocked": False,
    "live": False,
    "provider_live": False,
    "goal_hash": goal_hash,
    "artifacts": [
        "policy-profile.json",
        "data-boundary.json",
        "memory-intent.json",
        "evidence-case.json",
        "memory-intent-gate-receipt.json",
        "evidence-case-gate-receipt.json",
    ],
    "required_receipt_schemas": [
        MEMORY_INTENT_GATE_RECEIPT_SCHEMA,
        EVIDENCE_CASE_GATE_RECEIPT_SCHEMA,
    ],
    "intent_receipt_path": intent_receipt["receipt_path"],
    "evidence_receipt_path": evidence_receipt["receipt_path"],
    "intent_alert_codes": intent_receipt.get("alert_codes", []),
    "evidence_alert_codes": evidence_receipt.get("alert_codes", []),
    "proof_scope": {
        "proves": [
            "Tau evaluated a Graph Memory intent-shaped object before dispatch.",
            "Tau evaluated a separate create-evidence-case-shaped object before dispatch.",
            "Tau wrote memory intent and evidence-case gate receipts.",
        ],
        "does_not_prove": [
            "Memory facts are true.",
            "The evidence case is sufficient for closure.",
            "ITAR compliance.",
            "Legal sufficiency.",
            "Provider/model semantic quality.",
            "Semantic code correctness.",
        ],
    },
}
(out / "demo-receipt.json").write_text(json.dumps(demo, indent=2, sort_keys=True) + "\n")
print(json.dumps(demo, indent=2, sort_keys=True))
raise SystemExit(0 if ok else 1)
PY
