"""Tests for the machine-generated project status (#224)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tau_coding.project_status import (
    ProjectStatusError,
    PROJECT_STATUS_SCHEMA,
    build_project_status,
    render_markdown,
    semantic_digest,
    source_input_digest,
    verify_freshness,
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
    assert any(e.startswith("status_source_input_digest_mismatch") for e in errors)


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
    assert any(e.startswith("github_snapshot_digest_mismatch") for e in errors)


def test_source_input_digest_records_bound_input_families(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    snap = {"branch_protection": {}, "required_checks": [], "open_critical_issues": [],
            "recently_completed": []}
    status = build_project_status(tmp_path, generated_at=_AT, github_snapshot=snap)
    names = {item["name"] for item in status["source_inputs"]}
    assert {
        "GOAL.md",
        "pyproject.toml",
        "proof_index",
        "acceptance_receipt",
        "workflow_catalogue",
        "dag_runtime_capabilities",
        "project_status_generator",
        "github_snapshot",
    } <= names
    assert status["source_input_digest"] == source_input_digest(status["source_inputs"])


@pytest.mark.parametrize(
    ("path", "text", "reason"),
    [
        ("GOAL.md", "# Tau Immutable Goal\n\n**Status:** Revised\n", "status_source_input_digest_mismatch"),
        ("pyproject.toml", '[project]\nname = "tau"\nversion = "0.2.0"\ndescription = "x"\n',
         "status_source_input_digest_mismatch"),
        ("src/tau_coding/workflows/definitions/repository-readiness.json", "{\"changed\": true}\n",
         "status_source_input_digest_mismatch"),
        ("docs/status/github-snapshot.json", "{\"open_critical_issues\":[{\"number\":999}]}\n",
         "github_snapshot_digest_mismatch"),
    ],
)
def test_bound_input_family_mutations_fail_with_stable_reasons(
    tmp_path: Path, path: str, text: str, reason: str
) -> None:
    _init_repo(tmp_path)
    snap_path = tmp_path / "docs" / "status" / "github-snapshot.json"
    snap_path.parent.mkdir(parents=True)
    snap = {"branch_protection": {}, "required_checks": [], "open_critical_issues": [],
            "recently_completed": []}
    snap_path.write_text(json.dumps(snap), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", str(snap_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "add github snapshot"],
        check=True,
    )
    status = build_project_status(tmp_path, generated_at=_AT, github_snapshot=snap)
    (tmp_path / path).write_text(text, encoding="utf-8")
    current_snap = json.loads(snap_path.read_text(encoding="utf-8"))
    errors = verify_freshness(status, tmp_path, github_snapshot=current_snap)
    assert any(error.startswith(reason) for error in errors)


def test_unbound_file_mutation_does_not_invalidate_status(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    snap = {"branch_protection": {}, "required_checks": [], "open_critical_issues": [],
            "recently_completed": []}
    status = build_project_status(tmp_path, generated_at=_AT, github_snapshot=snap)
    (tmp_path / "notes.txt").write_text("not a bound input\n", encoding="utf-8")
    assert verify_freshness(status, tmp_path, github_snapshot=snap) == []


def test_canonical_build_refuses_dirty_tree_without_writing_status(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status_path = tmp_path / "docs" / "status" / "CURRENT_STATE.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text("original\n", encoding="utf-8")
    (tmp_path / "GOAL.md").write_text("# Tau Immutable Goal\n\n**Status:** Dirty\n", encoding="utf-8")
    with pytest.raises(ProjectStatusError, match="canonical_status_generated_from_dirty_tree"):
        build_project_status(tmp_path, generated_at=_AT)
    assert status_path.read_text(encoding="utf-8") == "original\n"


def test_noncanonical_build_marks_dirty_diagnostic_status(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "GOAL.md").write_text("# Tau Immutable Goal\n\n**Status:** Dirty\n", encoding="utf-8")
    status = build_project_status(tmp_path, generated_at=_AT, canonical=False)
    assert status["canonicality"] == "NONCANONICAL"
    assert status["generated_from_clean_tree"] is False
    errors = verify_freshness(status, tmp_path)
    assert "noncanonical_status_not_authoritative" in errors
    assert "canonical_status_generated_from_dirty_tree" in errors


def test_rendered_markdown_divergence_fails_verifier(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT)
    rendered = render_markdown(status).markdown
    errors = verify_freshness(status, tmp_path, rendered_markdown=rendered.replace("Current State", "Old State"))
    assert "rendered_status_divergence" in errors


def test_package_description_reflects_control_plane_product() -> None:
    # Tested through package metadata: the real repo's pyproject description must
    # name the assurance/control-plane product, not the old minimalist framing.
    import tomllib

    repo_root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    description = data["project"]["description"].lower()
    assert "admission" in description or "supervision" in description or "control" in description
    assert "minimalist pi-style coding-agent harness" not in description
