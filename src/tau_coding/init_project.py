"""Project initializer for Tau zero-trust starter files."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.handoff_dispatch import TAU_COMMAND_SPEC_POLICY_SCHEMA
from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

INIT_RECEIPT_SCHEMA = "tau.init_receipt.v1"
SUPPORTED_PROFILES = {"zero-trust", "coding-zero-trust"}


def initialize_tau_project(
    *,
    out_dir: Path,
    profile: str,
    force: bool = False,
) -> dict[str, Any]:
    """Create Tau project starter files and return a receipt."""

    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported init profile: {profile}")

    root = out_dir.expanduser().resolve()
    tau_dir = root / ".tau"
    files = _starter_files(profile)
    existing = [
        str((tau_dir / name).relative_to(root))
        for name in files
        if (tau_dir / name).exists()
    ]
    if existing and not force:
        return {
            "schema": INIT_RECEIPT_SCHEMA,
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "profile": profile,
            "out_dir": str(root),
            "tau_dir": str(tau_dir),
            "created_files": [],
            "existing_files": existing,
            "errors": ["existing_files"],
            "proof_scope": _proof_scope(),
        }

    tau_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[dict[str, str]] = []
    for name, content in files.items():
        path = tau_dir / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(content, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        created_files.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(root)),
                "sha256": f"sha256:{_sha256(path)}",
            }
        )

    return {
        "schema": INIT_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "profile": profile,
        "initialized_at": _utc_stamp(),
        "out_dir": str(root),
        "tau_dir": str(tau_dir),
        "created_files": created_files,
        "existing_files": [],
        "errors": [],
        "proof_scope": _proof_scope(),
    }


def _starter_files(profile: str) -> dict[str, dict[str, Any] | str]:
    return {
        "policy-profile.json": _zero_trust_policy_profile(),
        "data-boundary.json": _zero_trust_data_boundary(),
        "command-policy.json": _zero_trust_command_policy(profile),
        "dag-template.json": _zero_trust_dag_template(profile),
        "README.md": _zero_trust_readme(profile),
    }


def _zero_trust_policy_profile() -> dict[str, Any]:
    return {
        "schema": POLICY_PROFILE_SCHEMA,
        "profile_id": "zero-trust-local-only",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {
            "default": "deny",
            "allowed_domains": [],
        },
        "providers": {
            "cloud_llm": "deny",
            "local_model": "allow_with_approval",
        },
        "research": {
            "external_search": "deny",
            "manual_sanitized_receipt": "allow_with_review",
        },
        "memory": {
            "read": "allow",
            "write": "approval_required",
        },
        "github": {
            "public_mutation": "deny",
            "dry_run_projection": "allow",
        },
        "filesystem": {
            "write_allowlist": ["./receipts/**", "./scratch/**"],
            "read_denylist": ["~/.ssh/**", "secrets/**"],
        },
    }


def _zero_trust_data_boundary() -> dict[str, Any]:
    return {
        "schema": DATA_BOUNDARY_SCHEMA,
        "classification": "internal",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "restricted",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [
            "Starter boundary for local zero-trust Tau demos.",
            "Replace with an authorized project-specific classification before high-stakes use.",
            "Not a legal or export-control determination.",
        ],
    }


def _zero_trust_command_policy(profile: str) -> dict[str, Any]:
    allowed_command_roots = ["python", "python3", "uv"]
    if profile == "coding-zero-trust":
        allowed_command_roots.append("git")
    return {
        "schema": TAU_COMMAND_SPEC_POLICY_SCHEMA,
        "allowed_command_roots": allowed_command_roots,
        "denied_commands": ["curl", "wget", "ssh", "scp", "gh"],
        "allowed_cwd_roots": ["."],
        "allows_network": False,
        "allows_mutation": False,
        "requires_clean_worktree": False,
    }


def _zero_trust_dag_template(profile: str) -> dict[str, Any]:
    template: dict[str, Any] = {
        "schema": "tau.dag_contract.v1",
        "dag_id": profile,
        "goal": {
            "goal_id": profile,
            "goal_version": 1,
            "goal_hash": "sha256:replace-with-goal-hash",
        },
        "target": {
            "repo": "local",
            "target": profile,
            "allowed_paths": ["./scratch/**", "./receipts/**"],
            "forbidden_paths": ["secrets/**", ".env", ".env.*"],
        },
        "policy_profile": ".tau/policy-profile.json",
        "data_boundary": ".tau/data-boundary.json",
        "command_policy": ".tau/command-policy.json",
        "entry_node": "human",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": False,
            "default_timeout_seconds": 120,
            "max_total_attempts": 1,
        },
        "nodes": [
            {
                "id": "human",
                "agent": "human",
                "executor": "human",
            }
        ],
        "edges": [],
        "required_evidence": [
            "zero-trust-preflight-receipt.json",
            "human approval before executable nodes are added",
        ],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "malformed_handoff",
        ],
    }
    if profile == "coding-zero-trust":
        template["target"]["allowed_paths"] = [
            "src/**",
            "tests/**",
            "docs/**",
            "scratch/**",
            "receipts/**",
        ]
        template["required_evidence"] = [
            "zero-trust-preflight-receipt.json",
            "tau.code_patch_receipt.v1 before applying code changes",
            "tau.lsp_diagnostics_receipt.v1 before and after patch application",
            "tau.review_findings.v1 before PASS routing",
            "tau.commit_plan_receipt.v1 before commit approval",
            "tau.course_correction.v1 for BLOCKED or repeated-failure routes",
        ]
        template["coding_contract"] = {
            "schema": "tau.coding_contract.v1",
            "patch_receipts_required": True,
            "review_findings_required": True,
            "diagnostics_required": True,
            "commit_plan_dry_run_required": True,
            "course_correction_required_for_blocked_routes": True,
            "agent_truthfulness": "NOT_CLAIMED",
        }
    return template


def _zero_trust_readme(profile: str) -> str:
    if profile == "coding-zero-trust":
        return """# Tau Coding Zero-Trust Starter

This directory was created by `tau init --profile coding-zero-trust`.

Files:

- `policy-profile.json`: default-deny local Tau policy profile.
- `data-boundary.json`: starter data boundary. Replace it before high-stakes use.
- `command-policy.json`: default-deny command policy starter with local `git`
  available for read-only coding evidence collection.
- `dag-template.json`: coding evidence DAG template that requires hash-bound
  patch receipts, LSP diagnostics, structured review findings, dry-run commit
  planning, and course-correction receipts.

Agents remain untrusted. The template treats code patches, reviewer output, and
worker results as claims until Tau binds them to policy, hashes, receipts, and
evidence.

This starter does not prove ITAR compliance, export-control legal sufficiency,
sandbox isolation, signed provenance, human identity verification,
provider/model semantic safety, semantic code correctness, or compliance
package completeness.
"""
    return """# Tau Zero-Trust Starter

This directory was created by `tau init --profile zero-trust`.

Files:

- `policy-profile.json`: default-deny local Tau policy profile.
- `data-boundary.json`: starter data boundary. Replace it before high-stakes use.
- `command-policy.json`: default-deny command policy starter.
- `dag-template.json`: human-terminal DAG template for adding explicit nodes later.

This starter does not prove ITAR compliance, export-control legal sufficiency,
sandbox isolation, signed provenance, human identity verification,
provider/model semantic safety, or compliance package completeness.
"""


def _proof_scope() -> dict[str, list[str]]:
    return {
        "proves": [
            "Tau wrote a Tau starter file set.",
            "The starter includes policy, data-boundary, command-policy, DAG template, "
            "and README files.",
        ],
        "does_not_prove": [
            "ITAR compliance.",
            "Export-control legal sufficiency.",
            "Runtime sandbox enforcement.",
            "Human identity verification.",
            "Provider/model semantic safety.",
            "Semantic code correctness.",
            "Compliance package completeness.",
        ],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
