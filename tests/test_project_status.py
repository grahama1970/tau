"""Tests for the machine-generated project status (#224)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tau_coding.acceptance_attestation import DEFAULT_ACCEPTANCE_ATTESTATION, sha256_file
from tau_coding.project_status import (
    PROJECT_STATUS_SCHEMA,
    build_project_status,
    render_markdown,
    semantic_digest,
    verify_freshness,
)
from tau_coding.run_ledger import (
    DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX,
    build_agentic_eval_ledger_evidence_index,
)

_AT = "2026-07-28T00:00:00Z"


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "GOAL.md").write_text("# Tau Immutable Goal\n\n**Status:** Active\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tau"\nversion = "0.1.0"\n'
        'description = "Zero-trust DAG admission and supervision plane."\n',
        encoding="utf-8",
    )
    defs = root / "src" / "tau_coding" / "workflows" / "definitions"
    defs.mkdir(parents=True)
    for name in (
        "repository-readiness", "tau-operator-reference", "repository-evidence-map",
        "approved-release-bundle", "durable-repository-qualification",
    ):
        (defs / f"{name}.json").write_text("{}", encoding="utf-8")
    runtime = root / "src" / "tau_coding" / "dag_runtime"
    runtime.mkdir(parents=True)
    for name in ("admission", "write_intent", "reconciliation", "system_settlement",
                 "effects", "memory_projection"):
        (runtime / f"{name}.py").write_text("# stub\n", encoding="utf-8")
    accept = root / "docs" / "proofs" / "acceptance"
    accept.mkdir(parents=True)
    (accept / "rungs-evidence-receipt.json").write_text(
        json.dumps({"schema": "x", "signature": None}), encoding="utf-8"
    )
    (accept / "provider-live-receipt.json").write_text(
        json.dumps({"schema": "tau.workflow_provider_live_acceptance_receipt.v1", "provider_live": True}),
        encoding="utf-8",
    )
    ticket = root / "docs" / "proofs" / "tickets" / "issue-1-demo"
    ticket.mkdir(parents=True)
    (ticket / "closure-evidence.json").write_text(
        json.dumps({"ticket": "#1"}), encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"], check=True,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_deterministic_apart_from_timestamp(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    a = build_project_status(tmp_path, generated_at="2026-01-01T00:00:00Z")
    b = build_project_status(tmp_path, generated_at="2099-12-31T23:59:59Z")
    assert a["generated_at"] != b["generated_at"]
    assert a["semantic_content_digest"] == b["semantic_content_digest"]
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


def test_offline_reports_degraded_not_green(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT, github_snapshot=None)
    gh = status["github"]
    assert gh["freshness"] == "STALE"
    assert gh["branch_protection"] == "UNKNOWN"
    assert gh["open_critical_issues"] == "UNKNOWN"
    # offline build still succeeds and is verifiable
    assert verify_freshness(status, tmp_path, github_snapshot=None) == []


def test_github_snapshot_is_fresh_and_distinct(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    snap = {
        "branch_protection": {"required_status_checks": True},
        "required_checks": ["ci"],
        "open_critical_issues": [{"number": 221}],
        "recently_completed": [{"number": 211}],
    }
    status = build_project_status(tmp_path, generated_at=_AT, github_snapshot=snap)
    assert status["github"]["freshness"] == "FRESH"
    # proof separation: CI/issue state and human acceptance are distinct fields
    assert status["human_acceptance"]["state"] == "PENDING_HUMAN_SIGNATURE"
    assert status["github"]["open_critical_issues"] == [{"number": 221}]


def test_source_mutation_without_rebuild_fails_verifier(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT)
    # mutate a bound source (add a new closure-evidence record) without rebuilding
    new = tmp_path / "docs" / "proofs" / "tickets" / "issue-2-demo"
    new.mkdir(parents=True)
    (new / "closure-evidence.json").write_text(json.dumps({"ticket": "#2"}), encoding="utf-8")
    errors = verify_freshness(status, tmp_path)
    assert any(e.startswith("source_drift:proof_index") for e in errors)


def test_edited_status_text_fails_semantic_digest(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT)
    # tamper with generated content without updating the bound digest
    status["package"]["description"] = "totally different claim"
    errors = verify_freshness(status, tmp_path)
    assert any(e.startswith("semantic_content_digest_drift") for e in errors)


def test_render_binds_semantic_digest(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT)
    rendered = render_markdown(status)
    assert rendered.semantic_content_digest == status["semantic_content_digest"]
    assert status["semantic_content_digest"] in rendered.markdown
    assert "BEGIN GENERATED CURRENT STATE" in rendered.markdown
    assert PROJECT_STATUS_SCHEMA in rendered.markdown


def test_project_status_distinguishes_verified_and_invalid_human_acceptance(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    attestation = {
        "schema": "tau.human_acceptance_attestation.v1",
        "signer": {"id": "graham", "authority_class": "human_operator"},
        "decision": "ACCEPTED",
        "attested_at": "2026-07-28T00:00:00Z",
        "source_commit": head,
        "baseline": {
            "path": "docs/proofs/acceptance/rungs-evidence-receipt.json",
            "sha256": sha256_file(tmp_path / "docs" / "proofs" / "acceptance" / "rungs-evidence-receipt.json"),
        },
        "proof_receipt": {
            "path": "docs/proofs/acceptance/provider-live-receipt.json",
            "sha256": sha256_file(tmp_path / "docs" / "proofs" / "acceptance" / "provider-live-receipt.json"),
        },
    }
    _write_json(tmp_path / DEFAULT_ACCEPTANCE_ATTESTATION, attestation)

    accepted = build_project_status(tmp_path, generated_at=_AT)
    assert accepted["human_acceptance"]["state"] == "VERIFIED_ACCEPTANCE"
    assert accepted["human_acceptance"]["verification"]["ok"] is True

    attestation["baseline"]["sha256"] = "sha256:" + "0" * 64  # type: ignore[index]
    _write_json(tmp_path / DEFAULT_ACCEPTANCE_ATTESTATION, attestation)
    invalid = build_project_status(tmp_path, generated_at=_AT)
    assert invalid["human_acceptance"]["state"] == "INVALID_HUMAN_SIGNATURE"
    assert "baseline_receipt_digest_mismatch" in invalid["human_acceptance"]["verification"]["failure_codes"]


def test_capabilities_and_workflows_reflect_present_sources(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT)
    assert status["workflows"]["all_present"] is True
    assert status["capabilities"]["all_present"] is True
    assert status["proof_index"]["count"] == 1


def test_git_provenance_excluded_from_semantic_digest(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT)
    before = semantic_digest(status)
    # commit hash / clean-tree flag are self-referential provenance; changing
    # them must not change the semantic digest (else the file churns on its own
    # landing commit).
    status["git"]["commit"] = "deadbeef" * 5
    status["git"]["clean_tree"] = False
    assert semantic_digest(status) == before


def test_github_snapshot_edit_without_rebuild_fails_verifier(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    snap = {"branch_protection": {"required_status_checks": True}, "required_checks": ["ci"],
            "open_critical_issues": [], "recently_completed": []}
    status = build_project_status(tmp_path, generated_at=_AT, github_snapshot=snap)
    assert verify_freshness(status, tmp_path, github_snapshot=snap) == []
    # a mutated snapshot at verify time is drift
    snap_mutated = dict(snap, open_critical_issues=[{"number": 999}])
    errors = verify_freshness(status, tmp_path, github_snapshot=snap_mutated)
    assert any(e.startswith("source_drift:github_snapshot") for e in errors)


def test_project_status_reports_agentic_eval_evidence_index_mismatch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    report = tmp_path / "local" / "agentic-evals" / "demo-agentic-evals-report.json"
    _write_json(
        report,
        {
            "schema": "agentic_evals.report.v2",
            "source": "evals/demo_agentic_eval.json",
            "fixture_sha256": "sha256:fixture",
            "repo": {"sha": "source-sha", "ref": "main"},
            "mocked": False,
            "live": True,
            "skill": "demo",
            "readiness": "READY",
            "case_count": 1,
            "trial_count": 2,
            "cases": [],
        },
    )
    _write_json(tmp_path / "evals" / "demo_agentic_eval.json", {"version": 2, "skill": "demo"})
    index_path = tmp_path / DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX
    build_agentic_eval_ledger_evidence_index(tmp_path, output_path=index_path)
    report.write_text(report.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    status = build_project_status(tmp_path, generated_at=_AT)

    evidence = status["agentic_eval_evidence_index"]
    assert evidence["status"] == "FAIL"
    assert "report_digest_mismatch" in evidence["failure_codes"]


def test_package_description_reflects_control_plane_product() -> None:
    # Tested through package metadata: the real repo's pyproject description must
    # name the assurance/control-plane product, not the old minimalist framing.
    import tomllib

    repo_root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    description = data["project"]["description"].lower()
    assert "admission" in description or "supervision" in description or "control" in description
    assert "minimalist pi-style coding-agent harness" not in description
