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

from tau_coding.acceptance_attestation import (
    DEFAULT_ACCEPTANCE_ATTESTATION,
    DEFAULT_ACCEPTANCE_BASELINE,
    verify_acceptance_attestation,
)
from tau_coding.run_ledger import (
    DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX,
    verify_agentic_eval_ledger_evidence_index,
)

PROJECT_STATUS_SCHEMA = "tau.project_status.v1"
DEVELOPER_SHARE_STATUS_SCHEMA = "tau.developer_share_status.v1"
GENERATOR_VERSION = "1.0.0"

# Fields excluded from the semantic-content digest so repeated builds from the
# same sources are semantically identical. ``git`` is self-referential
# provenance (the commit hash and clean-tree flag change on the very commit that
# lands the status file); it is informational, not a bound input source.
_VOLATILE_FIELDS = ("generated_at", "git")

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
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.strip()


def _digest_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _sha256_bytes(path.read_bytes())


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
                entries.append({"path": evidence.relative_to(repo).as_posix(), "sha256": digest})
    combined = _sha256_text(_canonical(entries))
    return {"count": len(entries), "entries": entries, "digest": combined}


def _agentic_eval_evidence_index(repo: Path) -> dict[str, Any]:
    path = repo / DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX
    if not path.is_file():
        return {
            "present": False,
            "status": "MISSING",
            "ok": False,
            "source": DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX,
            "sha256": None,
            "failure_codes": ["agentic_eval_evidence_index_missing"],
        }
    digest = _digest_file(path)
    verification = verify_agentic_eval_ledger_evidence_index(
        path,
        repo,
        require_clean=False,
        require_current_sha=False,
        require_live_reports=True,
    )
    return {
        "present": True,
        "status": verification["status"],
        "ok": verification["ok"],
        "source": DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX,
        "sha256": digest,
        "report_count": verification["report_count"],
        "artifact_count": verification["artifact_count"],
        "failure_codes": verification["failure_codes"],
        "retained_reports_live_readback": verification["retained_reports_live_readback"],
    }


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
        return {
            "version": UNKNOWN,
            "description": UNKNOWN,
            "source": "pyproject.toml",
            "sha256": None,
        }
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
    available = {}
    for name in _FIVE_WORKFLOWS:
        candidate = definitions / f"{name}.json"
        available[name] = candidate.is_file()
    return {
        "expected": list(_FIVE_WORKFLOWS),
        "available": available,
        "all_present": all(available.values()),
        "source": "src/tau_coding/workflows/definitions/",
    }


def _capabilities(repo: Path) -> dict[str, Any]:
    runtime = repo / "src" / "tau_coding" / "dag_runtime"
    checks = {
        "receipt_admission": (runtime / "admission.py").is_file()
        and (runtime / "write_intent.py").is_file(),
        "reconciliation": (runtime / "reconciliation.py").is_file(),
        "system_settlement": (runtime / "system_settlement.py").is_file(),
        "accepted_effect_ledger": (runtime / "effects.py").is_file(),
        "memory_projection_outbox": (runtime / "memory_projection.py").is_file(),
    }
    return {
        "present": checks,
        "all_present": all(checks.values()),
        "source": "src/tau_coding/dag_runtime/",
    }


def _acceptance(repo: Path) -> dict[str, Any]:
    receipt = repo / DEFAULT_ACCEPTANCE_BASELINE
    attestation = repo / DEFAULT_ACCEPTANCE_ATTESTATION
    if not receipt.is_file():
        return {
            "baseline_present": False,
            "signature_present": attestation.is_file(),
            "attestation_present": attestation.is_file(),
            "state": UNKNOWN,
            "source": DEFAULT_ACCEPTANCE_BASELINE.as_posix(),
            "sha256": None,
            "attestation_source": DEFAULT_ACCEPTANCE_ATTESTATION.as_posix(),
            "attestation_sha256": _digest_file(attestation),
            "verification": {
                "status": "BLOCKED",
                "ok": False,
                "failure_codes": ["baseline_receipt_missing"],
            },
        }
    raw = receipt.read_bytes()
    verification = dict(verify_acceptance_attestation(repo))
    verification.pop("checked_at", None)
    signature_present = bool(verification.get("attestation_present"))
    if verification.get("ok"):
        state = "VERIFIED_ACCEPTANCE"
    elif signature_present:
        state = "INVALID_HUMAN_SIGNATURE"
    else:
        state = "PENDING_HUMAN_SIGNATURE"
    return {
        "baseline_present": True,
        "signature_present": signature_present,
        "attestation_present": signature_present,
        # Human acceptance is a distinct field: a clean-wheel baseline existing
        # is NOT a verified signed human acceptance.
        "state": state,
        "source": DEFAULT_ACCEPTANCE_BASELINE.as_posix(),
        "sha256": _sha256_bytes(raw),
        "attestation_source": DEFAULT_ACCEPTANCE_ATTESTATION.as_posix(),
        "attestation_sha256": _digest_file(attestation),
        "verification": verification,
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


def build_project_status(
    repo: Path,
    *,
    generated_at: str,
    github_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble CURRENT_STATE from explicit sources. Never raises on offline."""

    repo = repo.resolve()
    commit = _git(repo, "rev-parse", "HEAD")
    porcelain = _git(repo, "status", "--porcelain")
    clean_tree = porcelain == "" if porcelain is not None else None

    goal = _immutable_goal(repo)
    package = _package(repo)
    proof_index = _proof_index(repo)
    agentic_eval_evidence_index = _agentic_eval_evidence_index(repo)
    workflows = _workflows(repo)
    capabilities = _capabilities(repo)
    acceptance = _acceptance(repo)
    github = _github_block(github_snapshot)

    source_digests = {
        "GOAL.md": goal["sha256"],
        "pyproject.toml": package["sha256"],
        "proof_index": proof_index["digest"],
        "agentic_eval_evidence_index": agentic_eval_evidence_index["sha256"],
        "acceptance_receipt": acceptance["sha256"],
        "human_acceptance_attestation": acceptance.get("attestation_sha256"),
    }
    if github_snapshot is not None:
        # Bind the GitHub snapshot so editing docs/status/github-snapshot.json
        # without regenerating is caught by the freshness verifier.
        source_digests["github_snapshot"] = _sha256_text(_canonical(github_snapshot))

    status: dict[str, Any] = {
        "schema": PROJECT_STATUS_SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "generated_at": generated_at,
        "git": {"commit": commit or UNKNOWN, "clean_tree": clean_tree},
        "package": package,
        "immutable_goal": goal,
        "workflows": workflows,
        "capabilities": capabilities,
        "human_acceptance": acceptance,
        "github": github,
        "proof_index": proof_index,
        "agentic_eval_evidence_index": agentic_eval_evidence_index,
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
                "checked-in retained agentic-eval evidence index digest and verifier status",
                "clean-wheel acceptance baseline presence and verified signature binding",
            ]
            + (
                ["GitHub branch-protection, required checks, and open/closed critical issues"]
                if github_snapshot is not None
                else []
            ),
            "not_checked": [
                "provider semantic correctness",
                "runtime settlement authority (this artifact has none)",
            ]
            + (
                []
                if github_snapshot is not None
                else ["GitHub CI/issue/protection state (no snapshot; reported STALE/UNKNOWN)"]
            ),
        },
    }
    status["semantic_content_digest"] = semantic_digest(status)
    return status


def semantic_digest(status: dict[str, Any]) -> str:
    """Digest of the status with volatile fields and the digest itself removed."""

    reduced = {
        key: value
        for key, value in status.items()
        if key not in _VOLATILE_FIELDS and key != "semantic_content_digest"
    }
    return _sha256_text(_canonical(reduced))


def verify_freshness(
    status: dict[str, Any],
    repo: Path,
    *,
    github_snapshot: dict[str, Any] | None = None,
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

    recomputed = semantic_digest(status)
    if recomputed != status.get("semantic_content_digest"):
        errors.append(
            f"semantic_content_digest_drift:{status.get('semantic_content_digest')}!={recomputed}"
        )

    fresh = build_project_status(
        repo,
        generated_at=status.get("generated_at", ""),
        github_snapshot=github_snapshot,
    )
    for source, bound in status.get("source_digests", {}).items():
        current = fresh["source_digests"].get(source)
        if current != bound:
            errors.append(f"source_drift:{source}:{bound}!={current}")
    return errors


def _waiver_for(waivers: dict[str, Any] | None, gate_id: str) -> dict[str, Any] | None:
    if not isinstance(waivers, dict):
        return None
    items = waivers.get("waivers", [])
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("gate") != gate_id:
            continue
        signer = item.get("signer") if isinstance(item.get("signer"), dict) else {}
        if signer.get("authority_class") == "human_operator" and item.get("scope"):
            return item
    return None


def evaluate_developer_share_status(
    status: dict[str, Any],
    repo: Path,
    *,
    github_snapshot: dict[str, Any] | None = None,
    waivers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate share readiness from existing current-state inputs only."""

    repo = repo.resolve()
    current_commit = _git(repo, "rev-parse", "HEAD") or UNKNOWN
    current_clean = _git(repo, "status", "--porcelain") == ""
    freshness_errors = verify_freshness(status, repo, github_snapshot=github_snapshot)
    gates: list[dict[str, Any]] = []

    def add(
        gate_id: str,
        passed: bool | None,
        *,
        source: str,
        observed: Any,
        next_command: str,
        waivable: bool = False,
    ) -> None:
        waiver = _waiver_for(waivers, gate_id) if waivable else None
        if passed is True:
            state = "PASS"
        elif waiver is not None:
            state = "WAIVED"
        elif passed is False:
            state = "FAIL"
        else:
            state = "UNKNOWN"
        gates.append(
            {
                "id": gate_id,
                "state": state,
                "source": source,
                "observed": observed,
                "waivable": waivable,
                "waiver": waiver,
                "next_command": None if state in {"PASS", "WAIVED"} else next_command,
            }
        )

    add(
        "status_source_fresh",
        not freshness_errors,
        source="docs/status/CURRENT_STATE.json source_digests",
        observed=freshness_errors,
        next_command=(
            "tau project-status build --out docs/status/CURRENT_STATE.json "
            "--github-snapshot docs/status/github-snapshot.json && "
            "tau project-status render docs/status/CURRENT_STATE.json "
            "--out docs/status/CURRENT_STATE.md"
        ),
    )
    add(
        "status_source_commit_current",
        status.get("git", {}).get("commit") == current_commit,
        source="git rev-parse HEAD",
        observed={
            "status_commit": status.get("git", {}).get("commit"),
            "current_commit": current_commit,
        },
        next_command="regenerate CURRENT_STATE from the exact reviewed HEAD",
    )
    add(
        "canonical_status_clean_tree",
        status.get("git", {}).get("clean_tree") is True and current_clean,
        source="git status --porcelain and status.git.clean_tree",
        observed={
            "status_clean_tree": status.get("git", {}).get("clean_tree"),
            "current_clean_tree": current_clean,
        },
        next_command="land or remove unrelated changes before generating share status",
    )
    add(
        "github_snapshot_fresh",
        status.get("github", {}).get("freshness") == "FRESH",
        source="docs/status/github-snapshot.json",
        observed=status.get("github", {}).get("freshness", UNKNOWN),
        next_command="refresh docs/status/github-snapshot.json or attach a scoped human waiver",
        waivable=True,
    )
    open_critical = status.get("github", {}).get("open_critical_issues")
    add(
        "no_unresolved_critical_security_findings",
        len(open_critical) == 0 if isinstance(open_critical, list) else None,
        source="github.open_critical_issues",
        observed=open_critical,
        next_command="resolve the owning security ticket before developer sharing",
    )
    proof_index = status.get("proof_index", {})
    add(
        "proof_index_structurally_valid",
        proof_index.get("count", 0) > 0,
        source="docs/proofs/tickets/*/closure-evidence.json",
        observed=proof_index,
        next_command="rebuild or repair the proof index inputs",
    )
    eval_index = status.get("agentic_eval_evidence_index", {})
    add(
        "agentic_eval_evidence_index_pass",
        eval_index.get("ok") is True and eval_index.get("status") == "PASS",
        source=str(DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX),
        observed=eval_index,
        next_command="rebuild the retained agentic-eval evidence index after repairing failures",
    )
    add(
        "five_canonical_workflows_present",
        status.get("workflows", {}).get("all_present") is True,
        source="src/tau_coding/workflows/definitions/",
        observed=status.get("workflows", {}),
        next_command="restore the five canonical workflow definitions",
    )
    evidence = status.get("developer_share_evidence", {})
    for gate_id, field in (
        (
            "clean_checkout_installed_wheel_launch_proof_present",
            "clean_checkout_installed_wheel_launch",
        ),
        ("viewer_browser_proof_present", "viewer_browser"),
        ("repair_self_heal_proof_present", "repair_self_heal"),
    ):
        add(
            gate_id,
            evidence.get(field) is True,
            source=f"developer_share_evidence.{field}",
            observed=evidence.get(field, UNKNOWN),
            next_command=f"run the focused proof owner for {field}",
        )
    human_state = status.get("human_acceptance", {}).get("state")
    add(
        "human_acceptance_matches_goal",
        human_state == "VERIFIED_ACCEPTANCE",
        source="GOAL.md + human_acceptance",
        observed=human_state,
        next_command="obtain the explicit GOAL.md human acceptance attestation",
        waivable=False,
    )

    machine_blockers = [
        gate
        for gate in gates
        if gate["state"] in {"FAIL", "UNKNOWN"} and gate["id"] != "human_acceptance_matches_goal"
    ]
    human_gate = next(gate for gate in gates if gate["id"] == "human_acceptance_matches_goal")
    if not machine_blockers and human_gate["state"] == "PASS":
        readiness = "IMMUTABLE_GOAL_READY"
    elif not machine_blockers:
        readiness = "EXPERIMENTAL_PREVIEW_READY"
    else:
        readiness = "NOT_READY"

    return {
        "schema": DEVELOPER_SHARE_STATUS_SCHEMA,
        "status": "PASS",
        "readiness": readiness,
        "repo": str(repo),
        "source_commit": status.get("git", {}).get("commit", UNKNOWN),
        "current_commit": current_commit,
        "gates": gates,
        "failing_gates": [gate["id"] for gate in gates if gate["state"] in {"FAIL", "UNKNOWN"}],
        "proof_boundary": {
            "mocked": False,
            "live": True,
            "provider_live": False,
            "proves": "Existing share-readiness inputs were evaluated without "
            "regenerating evidence.",
            "does_not_prove": "Runtime correctness, provider quality, or human acceptance.",
        },
    }


def render_markdown(status: dict[str, Any]) -> RenderResult:
    """Render the bounded human summary. Binds the semantic-content digest."""

    digest = status.get("semantic_content_digest", "")
    gh = status["github"]
    accept = status["human_acceptance"]
    caps = status["capabilities"]["present"]
    agentic_eval_index = status.get("agentic_eval_evidence_index", {})

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
        f"- **Package**: `{status['package']['version']}` — {status['package']['description']}",
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
        f"- **Proof index**: {status['proof_index']['count']} checked-in closure-evidence records",
        f"- **Agentic-eval evidence index**: {agentic_eval_index.get('status', UNKNOWN)} "
        f"({agentic_eval_index.get('report_count', 0)} reports, "
        f"{agentic_eval_index.get('artifact_count', 0)} artifacts)",
        "",
        f"This section is generated from explicit sources; it carries no runtime "
        f"authority. Bound semantic-content digest: `{digest}`.",
        "<!-- END GENERATED CURRENT STATE -->",
    ]
    return RenderResult("\n".join(lines) + "\n", digest)


__all__ = [
    "PROJECT_STATUS_SCHEMA",
    "DEVELOPER_SHARE_STATUS_SCHEMA",
    "GENERATOR_VERSION",
    "ProjectStatusError",
    "RenderResult",
    "build_project_status",
    "semantic_digest",
    "verify_freshness",
    "evaluate_developer_share_status",
    "render_markdown",
]
