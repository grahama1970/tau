#!/usr/bin/env python3
"""Proof for Tau#352 developer-share readiness gates."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tau_coding.project_status import (
    build_project_status,
    evaluate_developer_share_status,
    semantic_digest,
)

SNAPSHOT = {
    "branch_protection": {"required_status_checks": False},
    "required_checks": [],
    "open_critical_issues": [],
    "recently_completed": [],
}


def _run(cmd: list[str], *, cwd: Path, timeout: int = 120) -> dict[str, Any]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _init_repo(root: Path) -> None:
    _run(["git", "init", "-q", "-b", "main", str(root)], cwd=root.parent)
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
    (accept / "rungs-evidence-receipt.json").write_text('{"schema":"x"}\n', encoding="utf-8")
    ticket = root / "docs" / "proofs" / "tickets" / "issue-1-demo"
    ticket.mkdir(parents=True)
    (ticket / "closure-evidence.json").write_text('{"ticket":"#1"}\n', encoding="utf-8")
    _run(["git", "add", "."], cwd=root)
    _run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=root
    )


def _ready_status(root: Path) -> dict[str, Any]:
    status = build_project_status(
        root, generated_at="2026-01-01T00:00:00Z", github_snapshot=SNAPSHOT
    )
    status["agentic_eval_evidence_index"] = {"ok": True, "status": "PASS"}
    status["developer_share_evidence"] = {
        "clean_checkout_installed_wheel_launch": True,
        "viewer_browser": True,
        "repair_self_heal": True,
    }
    status["semantic_content_digest"] = semantic_digest(status)
    return status


def _gate_state(result: dict[str, Any], gate_id: str) -> str:
    return next(gate["state"] for gate in result["gates"] if gate["id"] == gate_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    work = (
        Path(args.work).expanduser().resolve()
        if args.work
        else Path(tempfile.mkdtemp(prefix="tau-352-share-status-")).resolve()
    )
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    fixture_repo = work / "fixture-repo"
    fixture_repo.mkdir()
    _init_repo(fixture_repo)
    ready = _ready_status(fixture_repo)

    cases: dict[str, dict[str, Any]] = {}
    stale = dict(ready)
    stale["git"] = {**ready["git"], "commit": "deadbeef"}
    stale["semantic_content_digest"] = semantic_digest(stale)
    cases["source_mismatch"] = evaluate_developer_share_status(
        stale, fixture_repo, github_snapshot=SNAPSHOT
    )

    dirty_path = fixture_repo / "dirty.txt"
    dirty_path.write_text("dirty\n", encoding="utf-8")
    cases["dirty_tree"] = evaluate_developer_share_status(
        ready, fixture_repo, github_snapshot=SNAPSHOT
    )
    dirty_path.unlink()

    (fixture_repo / "GOAL.md").write_text(
        "# Tau Immutable Goal\n\n**Status:** Changed\n", encoding="utf-8"
    )
    cases["source_mutated_after_report"] = evaluate_developer_share_status(
        ready, fixture_repo, github_snapshot=SNAPSHOT
    )
    (fixture_repo / "GOAL.md").write_text(
        "# Tau Immutable Goal\n\n**Status:** Active\n", encoding="utf-8"
    )

    stale_github = dict(ready)
    stale_github["github"] = {**ready["github"], "freshness": "STALE"}
    stale_github["semantic_content_digest"] = semantic_digest(stale_github)
    cases["stale_github"] = evaluate_developer_share_status(
        stale_github, fixture_repo, github_snapshot=SNAPSHOT
    )
    cases["waived_github"] = evaluate_developer_share_status(
        stale_github,
        fixture_repo,
        github_snapshot=SNAPSHOT,
        waivers={
            "waivers": [
                {
                    "gate": "github_snapshot_fresh",
                    "scope": "offline preview",
                    "signer": {"id": "graham", "authority_class": "human_operator"},
                }
            ]
        },
    )

    eval_fail = dict(ready)
    eval_fail["agentic_eval_evidence_index"] = {"ok": False, "status": "FAIL"}
    eval_fail["semantic_content_digest"] = semantic_digest(eval_fail)
    cases["eval_index_fail"] = evaluate_developer_share_status(
        eval_fail, fixture_repo, github_snapshot=SNAPSHOT
    )

    security_fail = dict(ready)
    security_fail["github"] = {**ready["github"], "open_critical_issues": [{"number": 343}]}
    security_fail["semantic_content_digest"] = semantic_digest(security_fail)
    cases["security_fail"] = evaluate_developer_share_status(
        security_fail, fixture_repo, github_snapshot=SNAPSHOT
    )

    missing_proofs = dict(ready)
    missing_proofs["developer_share_evidence"] = {}
    missing_proofs["semantic_content_digest"] = semantic_digest(missing_proofs)
    cases["missing_proofs"] = evaluate_developer_share_status(
        missing_proofs, fixture_repo, github_snapshot=SNAPSHOT
    )

    human_missing = evaluate_developer_share_status(ready, fixture_repo, github_snapshot=SNAPSHOT)
    cases["human_waiver_rejected"] = evaluate_developer_share_status(
        ready,
        fixture_repo,
        github_snapshot=SNAPSHOT,
        waivers={
            "waivers": [
                {
                    "gate": "human_acceptance_matches_goal",
                    "scope": "not legal",
                    "signer": {"id": "graham", "authority_class": "human_operator"},
                }
            ]
        },
    )
    human_ready = dict(ready)
    human_ready["human_acceptance"] = {**ready["human_acceptance"], "state": "VERIFIED_ACCEPTANCE"}
    human_ready["semantic_content_digest"] = semantic_digest(human_ready)
    cases["immutable_ready"] = evaluate_developer_share_status(
        human_ready, fixture_repo, github_snapshot=SNAPSHOT
    )
    cases["immutable_ready_repeat"] = evaluate_developer_share_status(
        human_ready, fixture_repo, github_snapshot=SNAPSHOT
    )

    dist = work / "dist"
    venv = work / "venv"
    build = _run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=repo, timeout=180)
    wheels = sorted(dist.glob("tau-*.whl"))
    venv_create = _run([sys.executable, "-m", "venv", str(venv)], cwd=repo)
    pip = {"exit_code": 1, "stdout": "", "stderr": "no wheel"}
    cli = {"exit_code": 1, "stdout": "", "stderr": "not run"}
    if wheels:
        python = venv / "bin" / "python"
        pip = _run([str(python), "-m", "pip", "install", str(wheels[-1])], cwd=work, timeout=180)
        tau = venv / "bin" / "tau"
        cli = _run(
            [
                "bash",
                "-lc",
                f"HOME={work / 'home'} PYTHONPATH= {tau} developer-share status --json "
                f"--repo {repo} --status {repo / 'docs/status/CURRENT_STATE.json'} "
                f"--github-snapshot {repo / 'docs/status/github-snapshot.json'}",
            ],
            cwd=work,
        )
    cli_payload = json.loads(cli["stdout"]) if cli.get("stdout", "").strip().startswith("{") else {}

    checks = {
        "source_mismatch_gate": _gate_state(
            cases["source_mismatch"], "status_source_commit_current"
        )
        == "FAIL",
        "dirty_tree_gate": _gate_state(cases["dirty_tree"], "canonical_status_clean_tree")
        == "FAIL",
        "source_mutation_gate": _gate_state(
            cases["source_mutated_after_report"], "status_source_fresh"
        )
        == "FAIL",
        "stale_github_gate": _gate_state(cases["stale_github"], "github_snapshot_fresh") == "FAIL",
        "waiver_scoped": _gate_state(cases["waived_github"], "github_snapshot_fresh") == "WAIVED",
        "eval_index_gate": _gate_state(cases["eval_index_fail"], "agentic_eval_evidence_index_pass")
        == "FAIL",
        "security_gate": _gate_state(
            cases["security_fail"], "no_unresolved_critical_security_findings"
        )
        == "FAIL",
        "missing_viewer_gate": _gate_state(cases["missing_proofs"], "viewer_browser_proof_present")
        == "FAIL",
        "missing_repair_gate": _gate_state(
            cases["missing_proofs"], "repair_self_heal_proof_present"
        )
        == "FAIL",
        "human_missing_preview": human_missing["readiness"] == "EXPERIMENTAL_PREVIEW_READY",
        "human_waiver_rejected": _gate_state(
            cases["human_waiver_rejected"], "human_acceptance_matches_goal"
        )
        == "FAIL",
        "immutable_ready": cases["immutable_ready"]["readiness"] == "IMMUTABLE_GOAL_READY",
        "byte_stable_ready": json.dumps(cases["immutable_ready"], sort_keys=True)
        == json.dumps(cases["immutable_ready_repeat"], sort_keys=True),
        "cli_readback": cli_payload.get("schema") == "tau.developer_share_status.v1",
    }
    errors = [name for name, ok in checks.items() if not ok]
    proof = {
        "schema": "tau.developer_share_status_proof.v1",
        "issue": 352,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "fixture_repo": str(fixture_repo),
        "cases": cases,
        "checks": checks,
        "installed_wheel": str(wheels[-1]) if wheels else None,
        "installed_wheel_cli_readback": cli_payload,
        "commands": {"build": build, "venv_create": venv_create, "pip_install": pip, "cli": cli},
        "errors": errors,
        "proof_boundary": {
            "proves": "Developer-share status fails closed on stale/missing inputs and exposes "
            "a read-only installed-wheel status command.",
            "does_not_prove": "Provider quality, runtime proof freshness beyond supplied "
            "inputs, or human acceptance for the real repository.",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
