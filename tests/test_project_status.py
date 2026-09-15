"""Tests for the machine-generated project status (#224)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from tau_coding.acceptance_attestation import DEFAULT_ACCEPTANCE_ATTESTATION, sha256_file
from tau_coding.cli import project_agent_developer_share_command
from tau_coding.developer_surface_inventory import build_developer_surface_inventory
from tau_coding.project_status import (
    _DEVELOPER_SHARE_EVIDENCE_BINDINGS,
    DEVELOPER_SHARE_STATUS_SCHEMA,
    PROJECT_STATUS_SCHEMA,
    build_project_status,
    evaluate_developer_share_status,
    render_markdown,
    semantic_digest,
    verify_freshness,
)
from tau_coding.run_ledger import (
    DEFAULT_AGENTIC_EVAL_EVIDENCE_INDEX,
    build_agentic_eval_ledger_evidence_index,
)

_AT = "2026-07-28T00:00:00Z"
_SHARE_SNAPSHOT = {
    "branch_protection": {"required_status_checks": False},
    "required_checks": [],
    "open_critical_issues": [],
    "recently_completed": [],
}


def _write_passing_share_security_receipt(root: Path) -> None:
    """tau#343: share-ready fixtures need a current, clean security-gate receipt."""
    import json as _json
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    gate_dir = root / "docs" / "proofs" / "tickets" / "issue-343-security-gate"
    gate_dir.mkdir(parents=True, exist_ok=True)
    receipt = gate_dir / "security-gate.json"
    payload = {
        "status": "PASS",
        "share_readiness": "READY",
        "source_commit": commit,
        "counts": {"unresolved_blockers": 0, "reconciled": True},
    }
    # A committed receipt cannot self-reference its own commit hash and stay
    # tree-clean; commit a placeholder, mark skip-worktree, then write the
    # real payload so porcelain stays empty for the clean-tree gate.
    receipt.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", str(receipt)], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fixture receipt"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "update-index", "--skip-worktree", str(receipt)],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    payload["source_commit"] = commit
    receipt.write_text(_json.dumps(payload), encoding="utf-8")


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
        "repository-readiness",
        "tau-operator-reference",
        "repository-evidence-map",
        "approved-release-bundle",
        "durable-repository-qualification",
    ):
        (defs / f"{name}.json").write_text("{}", encoding="utf-8")
    runtime = root / "src" / "tau_coding" / "dag_runtime"
    runtime.mkdir(parents=True)
    for name in (
        "admission",
        "write_intent",
        "reconciliation",
        "system_settlement",
        "effects",
        "memory_projection",
    ):
        (runtime / f"{name}.py").write_text("# stub\n", encoding="utf-8")
    accept = root / "docs" / "proofs" / "acceptance"
    accept.mkdir(parents=True)
    (accept / "rungs-evidence-receipt.json").write_text(
        json.dumps({"schema": "x", "signature": None}), encoding="utf-8"
    )
    (accept / "provider-live-receipt.json").write_text(
        json.dumps(
            {"schema": "tau.workflow_provider_live_acceptance_receipt.v1", "provider_live": True}
        ),
        encoding="utf-8",
    )
    ticket = root / "docs" / "proofs" / "tickets" / "issue-1-demo"
    ticket.mkdir(parents=True)
    (ticket / "closure-evidence.json").write_text(json.dumps({"ticket": "#1"}), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            message,
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


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
            "sha256": sha256_file(
                tmp_path / "docs" / "proofs" / "acceptance" / "rungs-evidence-receipt.json"
            ),
        },
        "proof_receipt": {
            "path": "docs/proofs/acceptance/provider-live-receipt.json",
            "sha256": sha256_file(
                tmp_path / "docs" / "proofs" / "acceptance" / "provider-live-receipt.json"
            ),
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
    assert (
        "baseline_receipt_digest_mismatch"
        in invalid["human_acceptance"]["verification"]["failure_codes"]
    )


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
    snap = {
        "branch_protection": {"required_status_checks": True},
        "required_checks": ["ci"],
        "open_critical_issues": [],
        "recently_completed": [],
    }
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


def _share_ready_status(root: Path) -> dict[str, object]:
    _write_passing_share_security_receipt(root)
    status = build_project_status(root, generated_at=_AT, github_snapshot=_SHARE_SNAPSHOT)
    status["agentic_eval_evidence_index"] = {"ok": True, "status": "PASS"}
    status["developer_surface_inventory"] = {
        "status": "PASS",
        "unclassified_count": 0,
        "bug_stub_count": 0,
    }
    status["developer_share_evidence"] = {
        "clean_checkout_installed_wheel_launch": True,
        "viewer_browser": True,
        "repair_self_heal": True,
    }
    status["semantic_content_digest"] = semantic_digest(status)
    return status


def test_developer_share_status_fails_source_mismatch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = _share_ready_status(tmp_path)
    (tmp_path / "extra.txt").write_text("new source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "extra.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "advance",
        ],
        check=True,
    )

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)

    assert result["schema"] == DEVELOPER_SHARE_STATUS_SCHEMA
    assert result["readiness"] == "NOT_READY"
    assert "status_source_commit_current" in result["failing_gates"]


def test_developer_share_status_reports_individual_blockers(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = build_project_status(tmp_path, generated_at=_AT, github_snapshot=None)

    result = evaluate_developer_share_status(status, tmp_path)

    failing = set(result["failing_gates"])
    assert "github_snapshot_fresh" in failing
    assert "agentic_eval_evidence_index_pass" in failing
    assert "developer_surface_inventory_clean" in failing
    assert "clean_checkout_installed_wheel_launch_proof_present" in failing
    assert "viewer_browser_proof_present" in failing
    assert "repair_self_heal_proof_present" in failing
    assert "human_acceptance_matches_goal" in failing


# tau#362: the developer_share_evidence producer must bind real retained
# proof artifacts fail-closed. These tests pin the semantics per gate:
# present+matching=PASS, absent=FAIL, stale/mutated=FAIL.
_EVIDENCE_GATE_IDS = (
    "clean_checkout_installed_wheel_launch_proof_present",
    "viewer_browser_proof_present",
    "repair_self_heal_proof_present",
)
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _evidence_gate(result: dict[str, object], gate_id: str) -> dict[str, object]:
    return next(g for g in result["gates"] if g["id"] == gate_id)


def _copy_retained_developer_share_proofs(root: Path) -> None:
    for bindings in _DEVELOPER_SHARE_EVIDENCE_BINDINGS.values():
        for binding in bindings:
            src = _REPO_ROOT / binding["path"]
            dst = root / binding["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def test_developer_share_evidence_gates_pass_from_retained_proofs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _copy_retained_developer_share_proofs(tmp_path)

    status = build_project_status(tmp_path, generated_at=_AT)
    evidence = status["developer_share_evidence"]
    assert evidence["clean_checkout_installed_wheel_launch"] is True
    assert evidence["viewer_browser"] is True
    assert evidence["repair_self_heal"] is True

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)
    for gate_id in _EVIDENCE_GATE_IDS:
        assert _evidence_gate(result, gate_id)["state"] == "PASS"


def test_developer_share_evidence_gates_fail_when_proofs_absent(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    status = build_project_status(tmp_path, generated_at=_AT)
    evidence = status["developer_share_evidence"]
    assert evidence["clean_checkout_installed_wheel_launch"] is False
    assert evidence["viewer_browser"] is False
    assert evidence["repair_self_heal"] is False

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)
    for gate_id in _EVIDENCE_GATE_IDS:
        assert _evidence_gate(result, gate_id)["state"] == "FAIL"


def test_developer_share_evidence_gates_fail_when_proof_mutated(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _copy_retained_developer_share_proofs(tmp_path)
    # Mutate one retained artifact per evidence class: digest no longer matches
    # the pinned retained digest, so every class must fail closed.
    for relative in (
        "docs/proofs/tickets/issue-304-provider-live-acceptance-20260830T190303Z/provider-live-acceptance-receipt.json",
        "docs/proofs/tickets/issue-332-live-viewer-ledger-correlation-20260830T172554Z/proof-bundle/browser-proof.json",
        "local/agentic-evals/tau-triage-contract/proof.json",
    ):
        target = tmp_path / relative
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    status = build_project_status(tmp_path, generated_at=_AT)
    evidence = status["developer_share_evidence"]
    assert evidence["clean_checkout_installed_wheel_launch"] is False
    assert evidence["viewer_browser"] is False
    assert evidence["repair_self_heal"] is False
    mutated_detail = [
        check
        for check in evidence["detail"]["repair_self_heal"]
        if check["path"].endswith("triage-contract/proof.json")
    ]
    assert mutated_detail[0]["present"] is True
    assert "digest mismatch" in mutated_detail[0]["check"]

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)
    for gate_id in _EVIDENCE_GATE_IDS:
        assert _evidence_gate(result, gate_id)["state"] == "FAIL"


def test_developer_share_evidence_fails_when_pinned_fields_go_stale(
    tmp_path: Path, monkeypatch
) -> None:
    """A refreshed artifact whose status fields no longer match the pin fails."""

    _init_repo(tmp_path)
    binding = {
        "owner": "fixture",
        "path": "docs/proofs/tickets/fixture/proof.json",
        "schema": "tau.fixture_proof.v1",
        "fields": {"status": "PASS", "live": True, "mocked": False},
    }
    payload = {
        "schema": "tau.fixture_proof.v1",
        "status": "PASS",
        "live": True,
        "mocked": False,
    }
    _write_json(tmp_path / binding["path"], payload)
    binding["sha256"] = sha256_file(tmp_path / binding["path"])
    monkeypatch.setattr(
        "tau_coding.project_status._DEVELOPER_SHARE_EVIDENCE_BINDINGS",
        {"viewer_browser": (binding,)},
    )

    matching = build_project_status(tmp_path, generated_at=_AT)
    assert matching["developer_share_evidence"]["viewer_browser"] is True

    # The retained artifact is regenerated with a newer (stale for the pin)
    # status field; presence alone must not synthesize green. The digest pin
    # is refreshed too, so the failure must come from the field check — the
    # digest check alone cannot catch a correctly-pinned but regressed proof.
    payload["live"] = False
    _write_json(tmp_path / binding["path"], payload)
    binding["sha256"] = sha256_file(tmp_path / binding["path"])
    stale = build_project_status(tmp_path, generated_at=_AT)
    evidence = stale["developer_share_evidence"]
    assert evidence["viewer_browser"] is False
    detail = evidence["detail"]["viewer_browser"][0]
    assert detail["present"] is True
    assert detail["mismatched_fields"] == ["live"]


def test_developer_share_evidence_binds_real_retained_proofs_at_repo_tip() -> None:
    """The pinned retained proofs resolve from the actual checkout (tau#362)."""

    evidence = build_project_status(_REPO_ROOT, generated_at=_AT)["developer_share_evidence"]
    assert evidence["clean_checkout_installed_wheel_launch"] is True
    assert evidence["viewer_browser"] is True
    assert evidence["repair_self_heal"] is True


def test_developer_surface_inventory_detects_seeded_normal_route_stub(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    normal_route = tmp_path / "src" / "tau_coding" / "project_status.py"
    normal_route.write_text(
        "def advertised():\n    return 'placeholder_result'\n", encoding="utf-8"
    )

    inventory = build_developer_surface_inventory(tmp_path)

    assert inventory["status"] == "FAIL"
    assert any(
        item["file"] == "src/tau_coding/project_status.py"
        and "placeholder_result" in item["marker"]
        for item in inventory["unclassified_markers"]
    )


def test_developer_share_status_fails_on_unclassified_surface_marker(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = _share_ready_status(tmp_path)
    status["developer_surface_inventory"] = {
        "status": "FAIL",
        "unclassified_count": 1,
        "bug_stub_count": 0,
    }
    status["semantic_content_digest"] = semantic_digest(status)

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)

    states = {gate["id"]: gate["state"] for gate in result["gates"]}
    assert states["developer_surface_inventory_clean"] == "FAIL"
    assert result["readiness"] == "NOT_READY"


def test_developer_share_status_allows_only_scoped_freshness_waiver(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    status = _share_ready_status(tmp_path)
    status["github"]["freshness"] = "STALE"  # type: ignore[index]
    status["semantic_content_digest"] = semantic_digest(status)
    waivers = {
        "waivers": [
            {
                "gate": "github_snapshot_fresh",
                "scope": "offline developer preview",
                "signer": {"authority_class": "human_operator", "id": "graham"},
            }
        ]
    }

    result = evaluate_developer_share_status(
        status, tmp_path, github_snapshot=_SHARE_SNAPSHOT, waivers=waivers
    )

    states = {gate["id"]: gate["state"] for gate in result["gates"]}
    assert states["github_snapshot_fresh"] == "WAIVED"
    assert states["human_acceptance_matches_goal"] == "FAIL"
    assert result["readiness"] == "EXPERIMENTAL_PREVIEW_READY"


def test_developer_share_status_uses_repo_relative_default_status_path(
    tmp_path: Path, monkeypatch
) -> None:
    _init_repo(tmp_path)
    status = _share_ready_status(tmp_path)
    _write_json(tmp_path / "docs/status/CURRENT_STATE.json", status)
    _write_json(tmp_path / "docs/status/github-snapshot.json", _SHARE_SNAPSHOT)
    monkeypatch.chdir(tmp_path.parent)

    result = project_agent_developer_share_command(
        [
            "status",
            "--json",
            "--repo",
            str(tmp_path),
            "--github-snapshot",
            "docs/status/github-snapshot.json",
        ]
    )

    assert result["schema"] == DEVELOPER_SHARE_STATUS_SCHEMA
    assert result["status"] == "PASS"


def test_developer_share_status_reaches_immutable_ready_with_human_acceptance(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    status = _share_ready_status(tmp_path)
    status["human_acceptance"]["state"] = "VERIFIED_ACCEPTANCE"  # type: ignore[index]
    status["semantic_content_digest"] = semantic_digest(status)

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)

    assert result["readiness"] == "IMMUTABLE_GOAL_READY"
    assert result["failing_gates"] == []


def test_package_description_reflects_control_plane_product() -> None:
    # Tested through package metadata: the real repo's pyproject description must
    # name the assurance/control-plane product, not the old minimalist framing.
    import tomllib

    repo_root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    description = data["project"]["description"].lower()
    assert "admission" in description or "supervision" in description or "control" in description
    assert "minimalist pi-style coding-agent harness" not in description


def test_share_security_gate_blocks_when_receipt_missing(tmp_path: Path) -> None:
    """tau#343: no retained security-gate receipt => share NOT_READY, gate fails."""
    repo = tmp_path / "repo"
    repo.mkdir()
    status = {
        "git": {"commit": "x" * 40, "clean_tree": True},
        "github": {"open_critical_issues": [], "freshness": "FRESH"},
    }
    result = evaluate_developer_share_status(status, repo)
    gate = next(
        g for g in result["gates"] if g["id"] == "share_security_gate_receipt_current_and_clean"
    )
    assert gate["state"] == "FAIL"
    assert result["readiness"] == "NOT_READY"


def test_share_security_gate_accepts_ancestor_receipt_with_proof_only_diff(tmp_path: Path) -> None:
    """tau#343: retaining the proof receipt must not invalidate its source scan."""
    _init_repo(tmp_path)
    scanned_commit = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _write_json(
        tmp_path / "docs" / "proofs" / "tickets" / "issue-343-security-gate" / "security-gate.json",
        {
            "status": "PASS",
            "share_readiness": "READY",
            "source_commit": scanned_commit,
            "counts": {"unresolved_blockers": 0, "reconciled": True},
        },
    )
    current_commit = _commit_all(tmp_path, "retain issue 343 receipt")
    status = {
        "git": {"commit": current_commit, "clean_tree": True},
        "github": {"open_critical_issues": [], "freshness": "FRESH"},
    }

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)

    gate = next(
        g for g in result["gates"] if g["id"] == "share_security_gate_receipt_current_and_clean"
    )
    assert gate["state"] == "PASS"
    assert gate["observed"]["receipt_source_is_ancestor"] is True
    assert gate["observed"]["runtime_source_paths_changed"] == []


def test_share_security_gate_blocks_ancestor_receipt_after_source_change(tmp_path: Path) -> None:
    """tau#343: an ancestor receipt is stale once scanned runtime source changes."""
    _init_repo(tmp_path)
    scanned_commit = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _write_json(
        tmp_path / "docs" / "proofs" / "tickets" / "issue-343-security-gate" / "security-gate.json",
        {
            "status": "PASS",
            "share_readiness": "READY",
            "source_commit": scanned_commit,
            "counts": {"unresolved_blockers": 0, "reconciled": True},
        },
    )
    (tmp_path / "src" / "tau_coding" / "new_runtime.py").write_text(
        "# source changed\n",
        encoding="utf-8",
    )
    current_commit = _commit_all(tmp_path, "change scanned source after receipt")
    status = {
        "git": {"commit": current_commit, "clean_tree": True},
        "github": {"open_critical_issues": [], "freshness": "FRESH"},
    }

    result = evaluate_developer_share_status(status, tmp_path, github_snapshot=_SHARE_SNAPSHOT)

    gate = next(
        g for g in result["gates"] if g["id"] == "share_security_gate_receipt_current_and_clean"
    )
    assert gate["state"] == "FAIL"
    assert gate["observed"]["receipt_source_is_ancestor"] is True
    assert gate["observed"]["runtime_source_paths_changed"] == ["src/tau_coding/new_runtime.py"]


def test_share_security_gate_blocks_on_unresolved_blockers(tmp_path: Path) -> None:
    """tau#343: retained receipt with unresolved blockers => gate fails."""
    repo = tmp_path / "repo"
    receipt_dir = repo / "docs" / "proofs" / "tickets" / "issue-343-security-gate"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "security-gate.json").write_text(
        json.dumps(
            {
                "status": "BLOCKED",
                "share_readiness": "BLOCKED",
                "source_commit": "x" * 40,
                "counts": {"unresolved_blockers": 2, "reconciled": True},
            }
        ),
        encoding="utf-8",
    )
    status = {
        "git": {"commit": "x" * 40, "clean_tree": True},
        "github": {"open_critical_issues": [], "freshness": "FRESH"},
    }
    result = evaluate_developer_share_status(status, repo)
    gate = next(
        g for g in result["gates"] if g["id"] == "share_security_gate_receipt_current_and_clean"
    )
    assert gate["state"] == "FAIL"
