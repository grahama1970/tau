"""Machine-generated authoritative project status for Tau (#224).

Tau's implementation moves faster than its narrative documents, and a stale
README or PROJECT_KNOWLEDGE causes a later agent to reopen completed work or
cite an obsolete blocker. This module generates ONE bounded current-state
artifact from explicit sources (git, package metadata, the immutable-goal
document, the checked-in proof index, the acceptance receipt, and an optional
GitHub snapshot) so status is derived, never asserted in free-form prose.

Three operations, exposed via ``tau project-status``:

  * ``build``  -> CURRENT_STATE.json from explicit sources, each item naming its
                  source and binding a sha256 of that source.
  * ``render`` -> CURRENT_STATE.md, a bounded human summary that binds the JSON's
                  semantic-content digest so edited text cannot masquerade as
                  fresh status.
  * ``verify`` -> recompute every bound source digest and the semantic-content
                  digest; any drift is an explicit failure.

Determinism: build output is byte-identical for the same sources apart from the
explicitly excluded ``generated_at`` field. Degraded freshness: when the GitHub
snapshot is absent (offline/installed Tau), GitHub-derived fields report
``UNKNOWN``; they are NEVER inferred green from the absence of an error. The
artifact carries no runtime authority — it does not settle workflows, admit
receipts, or change the immutable goal.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_STATUS_SCHEMA = "tau.project_status.v1"
GENERATOR_VERSION = "1.1.0"

# Fields excluded from the semantic-content digest so repeated builds from the
# same sources are semantically identical. Commit identity is provenance; source
# freshness is enforced by ``source_input_digest`` instead.
_VOLATILE_FIELDS = (
    "generated_at",
    "git",
    "generated_from_commit",
    "rendered_status_digest",
    "semantic_content_digest",
)

_FIVE_WORKFLOWS = (
    "repository-readiness",
    "tau-operator-reference",
    "repository-evidence-map",
    "approved-release-bundle",
    "durable-repository-qualification",
)

UNKNOWN = "UNKNOWN"


class ProjectStatusError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderResult:
    markdown: str
    semantic_content_digest: str


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.strip()


def _digest_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _sha256_bytes(path.read_bytes())


def _digest_tree(repo: Path, root: Path, pattern: str) -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    if root.is_dir():
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                entries.append(
                    {"path": path.relative_to(repo).as_posix(), "sha256": _digest_file(path) or ""}
                )
    return {"count": len(entries), "entries": entries, "digest": _sha256_text(_canonical(entries))}


def _proof_index(repo: Path) -> dict[str, Any]:
    """Digest of the checked-in closure-evidence proof index.

    Bound as a single ordered digest so adding, removing, or mutating any
    closure-evidence file changes the source digest and fails the verifier.
    """

    tickets_dir = repo / "docs" / "proofs" / "tickets"
    entries: list[dict[str, str]] = []
    if tickets_dir.is_dir():
        for evidence in sorted(tickets_dir.glob("*/closure-evidence.json")):
            digest = _digest_file(evidence)
            if digest is not None:
                entries.append(
                    {"path": evidence.relative_to(repo).as_posix(), "sha256": digest}
                )
    combined = _sha256_text(_canonical(entries))
    return {"count": len(entries), "entries": entries, "digest": combined}


def _immutable_goal(repo: Path) -> dict[str, Any]:
    goal = repo / "GOAL.md"
    if not goal.is_file():
        return {"status": UNKNOWN, "source": "GOAL.md", "sha256": None}
    text = goal.read_text(encoding="utf-8")
    status = UNKNOWN
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("**status:**"):
            status = stripped.split("**Status:**", 1)[-1].strip() or UNKNOWN
            break
    return {"status": status, "source": "GOAL.md", "sha256": _sha256_text(text)}


def _package(repo: Path) -> dict[str, Any]:
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        return {"version": UNKNOWN, "description": UNKNOWN, "source": "pyproject.toml",
                "sha256": None}
    raw = pyproject.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    project = data.get("project", {})
    return {
        "version": project.get("version", UNKNOWN),
        "description": project.get("description", UNKNOWN),
        "source": "pyproject.toml",
        "sha256": _sha256_bytes(raw),
    }


def _workflows(repo: Path) -> dict[str, Any]:
    definitions = repo / "src" / "tau_coding" / "workflows" / "definitions"
    digest = _digest_tree(repo, definitions, "*.json")
    available = {}
    for name in _FIVE_WORKFLOWS:
        candidate = definitions / f"{name}.json"
        available[name] = candidate.is_file()
    return {
        "expected": list(_FIVE_WORKFLOWS),
        "available": available,
        "all_present": all(available.values()),
        "source": "src/tau_coding/workflows/definitions/",
        "digest": digest["digest"],
        "inputs": digest["entries"],
    }


def _capabilities(repo: Path) -> dict[str, Any]:
    runtime = repo / "src" / "tau_coding" / "dag_runtime"
    digest = _digest_tree(repo, runtime, "*.py")
    checks = {
        "receipt_admission": (runtime / "admission.py").is_file()
        and (runtime / "write_intent.py").is_file(),
        "reconciliation": (runtime / "reconciliation.py").is_file(),
        "system_settlement": (runtime / "system_settlement.py").is_file(),
        "accepted_effect_ledger": (runtime / "effects.py").is_file(),
        "memory_projection_outbox": (runtime / "memory_projection.py").is_file(),
    }
    return {"present": checks, "all_present": all(checks.values()),
            "source": "src/tau_coding/dag_runtime/", "digest": digest["digest"],
            "inputs": digest["entries"]}


def _acceptance(repo: Path) -> dict[str, Any]:
    receipt = repo / "docs" / "proofs" / "acceptance" / "rungs-evidence-receipt.json"
    if not receipt.is_file():
        return {"baseline_present": False, "signature_present": False,
                "state": UNKNOWN, "source": receipt.name, "sha256": None}
    raw = receipt.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    signature_present = bool(data.get("signature") or data.get("signer"))
    return {
        "baseline_present": True,
        "signature_present": signature_present,
        # Human acceptance is a distinct field: a clean-wheel baseline existing
        # is NOT a signed human acceptance.
        "state": "SIGNED" if signature_present else "PENDING_HUMAN_SIGNATURE",
        "source": "docs/proofs/acceptance/rungs-evidence-receipt.json",
        "sha256": _sha256_bytes(raw),
    }


def _github_block(github_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """GitHub-derived status, or an explicit degraded block when unavailable.

    Absence of a snapshot is NEVER inferred green: every field reports UNKNOWN
    and ``freshness`` is STALE so an offline build cannot claim CI/issue state.
    """

    if github_snapshot is None:
        return {
            "freshness": "STALE",
            "note": "No GitHub snapshot supplied; offline/installed build. "
                    "Live refresh is an explicit maintainer action.",
            "branch_protection": UNKNOWN,
            "required_checks": UNKNOWN,
            "open_critical_issues": UNKNOWN,
            "recently_completed": UNKNOWN,
        }
    return {
        "freshness": "FRESH",
        "branch_protection": github_snapshot.get("branch_protection", UNKNOWN),
        "required_checks": github_snapshot.get("required_checks", UNKNOWN),
        "open_critical_issues": github_snapshot.get("open_critical_issues", UNKNOWN),
        "recently_completed": github_snapshot.get("recently_completed", UNKNOWN),
        "snapshot_sha256": _sha256_text(_canonical(github_snapshot)),
    }


def _source_input_records(
    *,
    goal: dict[str, Any],
    package: dict[str, Any],
    proof_index: dict[str, Any],
    workflows: dict[str, Any],
    capabilities: dict[str, Any],
    acceptance: dict[str, Any],
    github_snapshot: dict[str, Any] | None,
    project_status_source_sha256: str | None,
) -> list[dict[str, Any]]:
    records = [
        {"name": "GOAL.md", "sha256": goal["sha256"]},
        {"name": "pyproject.toml", "sha256": package["sha256"]},
        {"name": "proof_index", "sha256": proof_index["digest"]},
        {"name": "acceptance_receipt", "sha256": acceptance["sha256"]},
        {"name": "workflow_catalogue", "sha256": workflows["digest"]},
        {"name": "dag_runtime_capabilities", "sha256": capabilities["digest"]},
        {"name": "project_status_generator", "sha256": project_status_source_sha256},
    ]
    if github_snapshot is not None:
        records.append({"name": "github_snapshot", "sha256": _sha256_text(_canonical(github_snapshot))})
    return records


def source_input_digest(records: list[dict[str, Any]]) -> str:
    """Digest every declared source input that may alter generated status."""

    return _sha256_text(_canonical(records))


def build_project_status(
    repo: Path,
    *,
    generated_at: str,
    github_snapshot: dict[str, Any] | None = None,
    canonical: bool = True,
) -> dict[str, Any]:
    """Assemble CURRENT_STATE from explicit sources. Never raises on offline."""

    repo = repo.resolve()
    commit = _git(repo, "rev-parse", "HEAD")
    porcelain = _git(repo, "status", "--porcelain")
    clean_tree = porcelain == "" if porcelain is not None else None
    if canonical and clean_tree is not True:
        raise ProjectStatusError("canonical_status_generated_from_dirty_tree")

    goal = _immutable_goal(repo)
    package = _package(repo)
    proof_index = _proof_index(repo)
    workflows = _workflows(repo)
    capabilities = _capabilities(repo)
    acceptance = _acceptance(repo)
    github = _github_block(github_snapshot)
    project_status_source_sha256 = _digest_file(repo / "src" / "tau_coding" / "project_status.py")
    source_inputs = _source_input_records(
        goal=goal,
        package=package,
        proof_index=proof_index,
        workflows=workflows,
        capabilities=capabilities,
        acceptance=acceptance,
        github_snapshot=github_snapshot,
        project_status_source_sha256=project_status_source_sha256,
    )

    source_digests = {
        "GOAL.md": goal["sha256"],
        "pyproject.toml": package["sha256"],
        "proof_index": proof_index["digest"],
        "acceptance_receipt": acceptance["sha256"],
        "workflow_catalogue": workflows["digest"],
        "dag_runtime_capabilities": capabilities["digest"],
        "project_status_generator": project_status_source_sha256,
    }
    if github_snapshot is not None:
        # Bind the GitHub snapshot so editing docs/status/github-snapshot.json
        # without regenerating is caught by the freshness verifier.
        source_digests["github_snapshot"] = _sha256_text(_canonical(github_snapshot))

    status: dict[str, Any] = {
        "schema": PROJECT_STATUS_SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "canonicality": "CANONICAL" if canonical else "NONCANONICAL",
        "generated_at": generated_at,
        "generated_from_commit": commit or UNKNOWN,
        "generated_from_clean_tree": clean_tree,
        "git": {"commit": commit or UNKNOWN, "clean_tree": clean_tree},
        "package": package,
        "immutable_goal": goal,
        "workflows": workflows,
        "capabilities": capabilities,
        "human_acceptance": acceptance,
        "github": github,
        "proof_index": proof_index,
        "source_inputs": source_inputs,
        "source_input_digest": source_input_digest(source_inputs),
        "source_digests": source_digests,
        "proof_boundary": {
            "mocked": False,
            "live": github_snapshot is not None,
            "provider_live": False,
            "checked": [
                "local git commit and clean-tree state",
                "package version/description from pyproject.toml",
                "immutable-goal status from GOAL.md",
                "checked-in closure-evidence proof index digest",
                "clean-wheel acceptance baseline presence and signature presence",
            ] + (["GitHub branch-protection, required checks, and open/closed critical issues"]
                 if github_snapshot is not None else []),
            "not_checked": [
                "human acceptance signature validity (distinct from presence)",
                "provider semantic correctness",
                "runtime settlement authority (this artifact has none)",
            ] + ([] if github_snapshot is not None else
                 ["GitHub CI/issue/protection state (no snapshot; reported STALE/UNKNOWN)"]),
        },
    }
    status["semantic_content_digest"] = semantic_digest(status)
    status["rendered_status_digest"] = _sha256_text(render_markdown(status).markdown)
    return status


def semantic_digest(status: dict[str, Any]) -> str:
    """Digest of the status with volatile fields and the digest itself removed."""

    reduced = {
        key: value
        for key, value in status.items()
        if key not in _VOLATILE_FIELDS
    }
    return _sha256_text(_canonical(reduced))


def verify_freshness(
    status: dict[str, Any],
    repo: Path,
    *,
    github_snapshot: dict[str, Any] | None = None,
    rendered_markdown: str | None = None,
) -> list[str]:
    """Recompute bound source digests and the semantic digest; report drift.

    Returns a list of typed drift messages (empty == fresh). A changed source
    that was not regenerated, or edited status text whose bound digest no longer
    matches, both surface here.
    """

    errors: list[str] = []
    if status.get("schema") != PROJECT_STATUS_SCHEMA:
        errors.append(f"schema_mismatch:{status.get('schema')}")
        return errors
    if status.get("canonicality", "CANONICAL") != "CANONICAL":
        errors.append("noncanonical_status_not_authoritative")
    if status.get("generated_from_clean_tree") is not True:
        errors.append("canonical_status_generated_from_dirty_tree")

    recomputed = semantic_digest(status)
    if recomputed != status.get("semantic_content_digest"):
        errors.append(
            f"semantic_content_digest_drift:{status.get('semantic_content_digest')}"
            f"!={recomputed}"
        )

    fresh = build_project_status(
        repo,
        generated_at=status.get("generated_at", ""),
        github_snapshot=github_snapshot,
        canonical=False,
    )
    if fresh.get("source_input_digest") != status.get("source_input_digest"):
        errors.append(
            "status_source_input_digest_mismatch:"
            f"{status.get('source_input_digest')}!={fresh.get('source_input_digest')}"
        )
    status_github_digest = status.get("source_digests", {}).get("github_snapshot")
    current_github_digest = fresh["source_digests"].get("github_snapshot")
    if status_github_digest != current_github_digest:
        errors.append(f"github_snapshot_digest_mismatch:{status_github_digest}!={current_github_digest}")
    if status_github_digest != status.get("github", {}).get("snapshot_sha256"):
        errors.append(
            "github_snapshot_digest_mismatch:"
            f"{status.get('github', {}).get('snapshot_sha256')}!={status_github_digest}"
        )
    for source, bound in status.get("source_digests", {}).items():
        current = fresh["source_digests"].get(source)
        if current != bound:
            if source != "github_snapshot":
                errors.append(f"status_source_input_digest_mismatch:{source}:{bound}!={current}")
    if rendered_markdown is not None:
        expected = render_markdown(status).markdown
        expected_digest = _sha256_text(expected)
        if rendered_markdown != expected or status.get("rendered_status_digest") != expected_digest:
            errors.append("rendered_status_divergence")
    return errors


def render_markdown(status: dict[str, Any]) -> RenderResult:
    """Render the bounded human summary. Binds the semantic-content digest."""

    digest = status.get("semantic_content_digest", "")
    gh = status["github"]
    accept = status["human_acceptance"]
    caps = status["capabilities"]["present"]

    def _mark(value: Any) -> str:
        if value is True:
            return "yes"
        if value is False:
            return "no"
        return str(value)

    def _bp(value: Any) -> str:
        if isinstance(value, dict):
            return "enabled" if value.get("required_status_checks") else "no required checks"
        return str(value)

    def _issue_numbers(value: Any) -> str:
        if isinstance(value, list):
            nums = sorted(item.get("number") for item in value if isinstance(item, dict))
            return f"{len(nums)} (#" + ", #".join(str(n) for n in nums) + ")" if nums else "0"
        return str(value)

    lines = [
        "<!-- BEGIN GENERATED CURRENT STATE (tau project-status; do not edit by hand) -->",
        f"## Current State (generated, {PROJECT_STATUS_SCHEMA})",
        "",
        f"- **Source commit**: `{status['git']['commit']}` (clean tree: "
        f"{_mark(status['git']['clean_tree'])})",
        f"- **Generated from**: `{status.get('generated_from_commit', UNKNOWN)}` "
        f"(clean tree: {_mark(status.get('generated_from_clean_tree'))}, "
        f"{status.get('canonicality', 'CANONICAL')})",
        f"- **Source input digest**: `{status.get('source_input_digest', UNKNOWN)}`",
        f"- **Package**: `{status['package']['version']}` — "
        f"{status['package']['description']}",
        f"- **Immutable goal**: {status['immutable_goal']['status']} (GOAL.md)",
        f"- **Five workflows present**: {_mark(status['workflows']['all_present'])}",
        f"- **Receipt-admission / effect-ledger / outbox**: admission="
        f"{_mark(caps['receipt_admission'])}, effects="
        f"{_mark(caps['accepted_effect_ledger'])}, outbox="
        f"{_mark(caps['memory_projection_outbox'])}",
        f"- **Human acceptance**: {accept['state']} "
        f"(baseline present: {_mark(accept['baseline_present'])}, "
        f"signature present: {_mark(accept['signature_present'])})",
        f"- **GitHub freshness**: {gh['freshness']} "
        f"(branch protection: {_bp(gh['branch_protection'])}, "
        f"open critical issues: {_issue_numbers(gh['open_critical_issues'])})",
        f"- **Proof index**: {status['proof_index']['count']} checked-in "
        "closure-evidence records",
        "",
        f"This section is generated from explicit sources; it carries no runtime "
        f"authority. Bound semantic-content digest: `{digest}`.",
        "<!-- END GENERATED CURRENT STATE -->",
    ]
    return RenderResult("\n".join(lines) + "\n", digest)


__all__ = [
    "PROJECT_STATUS_SCHEMA",
    "GENERATOR_VERSION",
    "ProjectStatusError",
    "RenderResult",
    "build_project_status",
    "semantic_digest",
    "source_input_digest",
    "verify_freshness",
    "render_markdown",
]
