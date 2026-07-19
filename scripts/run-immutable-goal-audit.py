#!/usr/bin/env python3
"""Audit Tau's immutable five-workflow goal from one exact clean checkout.

SQLite WAL establishes committed database recovery, not application-level
exactly-once effects. Tau's ledger and repeated-resume evidence remain the
publication authority. Sources: https://sqlite.org/wal.html and
https://sqlite.org/walformat.html.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sysconfig
import tempfile
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path
from typing import Any

SCHEMA = "tau.immutable_goal_audit.v1"
WORKFLOW_IDS = (
    "repository-readiness",
    "tau-operator-reference",
    "repository-evidence-map",
    "approved-release-bundle",
    "durable-repository-qualification",
)
CATALOG_ORDER = (
    "approved-release-bundle",
    "durable-repository-qualification",
    "repository-evidence-map",
    "repository-readiness",
    "tau-operator-reference",
)
TOPOLOGIES = {
    "repository-readiness": "LINEAR",
    "tau-operator-reference": "MULTI_STEP_SEQUENTIAL",
    "repository-evidence-map": "FAN_OUT_FAN_IN",
    "approved-release-bundle": "MIXED_RETRY_APPROVAL",
    "durable-repository-qualification": "DURABLE_MIXED_REPAIR_APPROVAL",
}
RESULT_NAMES = {workflow_id: workflow_id for workflow_id in WORKFLOW_IDS}
RESEARCH_SOURCES = ["https://sqlite.org/wal.html", "https://sqlite.org/walformat.html"]
BROWSER_REQUIRED_CHECKS = {
    "readiness_positive_browser": (
        "workflow_title_visible",
        "goal_summary_visible",
        "inspect_running_observed",
        "validate_running_observed",
        "publish_running_observed",
        "publish_accepted_observed",
        "final_result_visible",
        "result_artifact_refs_visible",
        "no_manual_reload",
        "read_only_requests",
        "layout_non_overlapping",
    ),
    "readiness_negative_browser": (
        "inspect_accepted_observed",
        "validate_blocked_observed",
        "dirty_repository_visible",
        "publish_not_executed",
        "final_result_absent",
        "no_manual_reload",
        "read_only_requests",
        "layout_non_overlapping",
    ),
    "operator_positive_browser": (
        "workflow_title_visible",
        "goal_summary_visible",
        "capture_running_observed",
        "compose_running_observed",
        "validate_accepted_observed",
        "final_result_visible",
        "desktop_layout_non_overlapping",
        "mobile_layout_non_overlapping",
        "no_manual_reload",
        "read_only_requests",
    ),
    "operator_negative_browser": (
        "first_three_accepted",
        "validate_blocked_observed",
        "required_workflow_missing_exact",
        "final_result_absent",
        "desktop_layout_non_overlapping",
        "mobile_layout_non_overlapping",
        "no_manual_reload",
        "read_only_requests",
    ),
    "evidence_positive_browser": (
        "workflow_title_visible",
        "goal_visible",
        "all_branches_running_together",
        "all_branches_accepted",
        "publish_accepted",
        "final_result_visible",
        "desktop_layout_non_overlapping",
        "mobile_layout_non_overlapping",
        "no_manual_reload",
        "read_only_requests",
    ),
    "evidence_negative_browser": (
        "inventory_accepted",
        "test_surface_missing_exact",
        "publish_not_dispatched",
        "final_result_absent",
        "desktop_layout_non_overlapping",
        "mobile_layout_non_overlapping",
        "no_manual_reload",
        "read_only_requests",
    ),
    "slice04_browser": (
        "workflow_title_visible",
        "goal_visible",
        "parallel_branches_running",
        "revise_then_pass_visible",
        "approval_required_visible",
        "no_publication_before_approval",
        "publish_running_after_resume",
        "final_result_visible",
        "final_transaction_evidence_visible",
        "desktop_layout_non_overlapping",
        "mobile_layout_non_overlapping",
        "no_manual_reload",
        "read_only_requests",
    ),
    "slice05_browser": (
        "workflow_title_visible",
        "goal_visible",
        "parallel_branches_running",
        "targeted_repair_blocker_visible",
        "recovery_takeover_visible",
        "repaired_branch_running",
        "approval_required_visible",
        "publication_effect_count_one",
        "final_result_visible",
        "desktop_layout_non_overlapping",
        "mobile_layout_non_overlapping",
        "no_manual_reload",
        "read_only_requests",
    ),
}


class AuditError(RuntimeError):
    """Fail-closed audit error with a stable, non-secret message."""


class CommandError(AuditError):
    def __init__(self, argv: list[str], returncode: int) -> None:
        super().__init__(f"command_failed:{returncode}:{' '.join(argv)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--readiness-positive-browser-proof", type=Path, required=True)
    parser.add_argument("--readiness-negative-browser-proof", type=Path, required=True)
    parser.add_argument("--operator-positive-browser-proof", type=Path, required=True)
    parser.add_argument("--operator-negative-browser-proof", type=Path, required=True)
    parser.add_argument("--evidence-positive-browser-proof", type=Path, required=True)
    parser.add_argument("--evidence-negative-browser-proof", type=Path, required=True)
    parser.add_argument("--slice04-browser-proof", type=Path, required=True)
    parser.add_argument("--slice04-rerun-proof", type=Path, required=True)
    parser.add_argument("--slice05-browser-proof", type=Path, required=True)
    parser.add_argument("--slice05-wheel-proof", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []
    try:
        audit = _run_audit(args, commands)
    except Exception as exc:
        audit = _blocked_audit(args.ref, commands, exc)
        _write_json(out, audit)
        print(json.dumps(audit, indent=2, sort_keys=True))
        return 1

    _write_json(out, audit)
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0


def _run_audit(args: argparse.Namespace, commands: list[dict[str, Any]]) -> dict[str, Any]:
    repo = _repository_root(args.repo, commands)
    out = args.out.expanduser().resolve()
    if out == repo or repo in out.parents:
        raise AuditError("audit_output_must_be_outside_caller_worktree")

    caller_status = _git_status(repo, commands)
    source_ref = _verified_ref(repo, args.ref, commands)
    proofs = _validate_supplied_proofs(
        expected_source_ref=source_ref,
        readiness_positive_browser=args.readiness_positive_browser_proof,
        readiness_negative_browser=args.readiness_negative_browser_proof,
        operator_positive_browser=args.operator_positive_browser_proof,
        operator_negative_browser=args.operator_negative_browser_proof,
        evidence_positive_browser=args.evidence_positive_browser_proof,
        evidence_negative_browser=args.evidence_negative_browser_proof,
        slice04_browser=args.slice04_browser_proof,
        slice04_rerun=args.slice04_rerun_proof,
        slice05_browser=args.slice05_browser_proof,
        slice05_wheel=args.slice05_wheel_proof,
    )

    with tempfile.TemporaryDirectory(prefix="tau-immutable-goal-audit-") as temporary:
        root = Path(temporary)
        checkout = root / "checkout"
        added = False
        body_error: Exception | None = None
        try:
            _run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(checkout), source_ref],
                cwd=repo,
                commands=commands,
            )
            added = True
            if _git_head(checkout, commands) != source_ref:
                raise AuditError("detached_worktree_ref_mismatch")
            if _git_status(checkout, commands):
                raise AuditError("detached_worktree_not_clean")

            wheel = _build_wheel(checkout, root, commands)
            supplied_wheel = next(
                item for item in proofs if item["label"] == "slice05_wheel"
            )
            if supplied_wheel.get("wheel_sha256") != _sha256(wheel):
                raise AuditError("slice05_wheel_hash_mismatch")
            tau, python, purelib, installed_env = _install_wheel(wheel, root, commands)
            public_interface = _verify_public_interface(
                tau=tau,
                checkout=checkout,
                cwd=root,
                env=installed_env,
                commands=commands,
            )

            fixture = _git_fixture(root / "fixture", commands, with_tests=True)
            dirty_fixture = _git_fixture(
                root / "dirty-fixture", commands, with_tests=True, dirty=True
            )
            no_tests_fixture = _git_fixture(root / "no-tests-fixture", commands, with_tests=False)
            fixture_head = _git_head(fixture, commands)

            workflows = _run_all_workflows(
                tau=tau,
                checkout=checkout,
                fixture=fixture,
                dirty_fixture=dirty_fixture,
                no_tests_fixture=no_tests_fixture,
                root=root,
                env=installed_env,
                commands=commands,
            )

            if _git_head(fixture, commands) != fixture_head or _git_status(fixture, commands):
                raise AuditError("inspected_repository_was_mutated")
            if _git_status(checkout, commands):
                raise AuditError("detached_worktree_was_mutated")

            source = {
                "repository": str(repo),
                "source_ref": source_ref,
                "wheel": {
                    "filename": wheel.name,
                    "sha256": _sha256(wheel),
                    "bytes": wheel.stat().st_size,
                },
                "installed_import": _installed_import(
                    python=python,
                    purelib=purelib,
                    cwd=root,
                    env=installed_env,
                    commands=commands,
                ),
                "python_version": _run_text(
                    [str(python), "--version"], root, commands, installed_env
                ).strip(),
                "uv_version": _run_text(["uv", "--version"], checkout, commands).strip(),
                "readme_sha256": _sha256(checkout / "README.md"),
                "goal_contract": _goal_contract(checkout),
                "public_interface": public_interface,
                "caller_worktree_status_sha256": _sha256_text(caller_status),
            }
        except Exception as exc:
            body_error = exc
        finally:
            if added:
                cleanup = _run_unchecked(
                    ["git", "-C", str(repo), "worktree", "remove", "--force", str(checkout)],
                    repo,
                    commands,
                )
                if cleanup.returncode != 0 and body_error is None:
                    body_error = AuditError("detached_worktree_removal_failed")

        if body_error is not None:
            raise body_error

    if _git_status(repo, commands) != caller_status:
        raise AuditError("caller_worktree_changed_during_audit")
    if [item["workflow_id"] for item in workflows] != list(WORKFLOW_IDS):
        raise AuditError("workflow_record_order_mismatch")

    return {
        "schema": SCHEMA,
        "status": "PASS",
        "source_ref": source_ref,
        "requested_ref": args.ref,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source": source,
        "commands": commands,
        "workflows": workflows,
        "supplied_proofs": proofs,
        "criteria": _established_criteria(workflows, proofs),
        "first_unmet_criterion": 10,
        "claims": _claims(),
        "research_sources": RESEARCH_SOURCES,
    }


def _build_wheel(checkout: Path, root: Path, commands: list[dict[str, Any]]) -> Path:
    wheel_dir = root / "wheel"
    wheel_dir.mkdir()
    _run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=checkout,
        commands=commands,
        timeout=300,
    )
    wheels = sorted(wheel_dir.glob("tau-*.whl"))
    if len(wheels) != 1:
        raise AuditError(f"expected_one_tau_wheel_found:{len(wheels)}")
    return wheels[0]


def _install_wheel(
    wheel: Path, root: Path, commands: list[dict[str, Any]]
) -> tuple[Path, Path, Path, dict[str, str]]:
    environment = root / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    tau = bin_dir / ("tau.exe" if os.name == "nt" else "tau")
    purelib = Path(
        _run_text(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            root,
            commands,
        ).strip()
    )
    dependency_site = Path(sysconfig.get_path("purelib")).resolve()
    (purelib / "tau-audit-dependencies.pth").write_text(
        str(dependency_site) + "\n", encoding="utf-8"
    )
    env = _installed_environment(bin_dir)
    _run(
        [str(python), "-m", "pip", "install", "--quiet", "--no-index", "--no-deps", str(wheel)],
        cwd=root,
        commands=commands,
        env=env,
    )
    if not tau.is_file():
        raise AuditError("installed_tau_command_missing")
    return tau, python, purelib, env


def _installed_import(
    *,
    python: Path,
    purelib: Path,
    cwd: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
) -> str:
    value = _run_text(
        [str(python), "-c", "import tau_coding; print(tau_coding.__file__)"],
        cwd,
        commands,
        env,
    ).strip()
    path = Path(value).resolve()
    if not path.is_relative_to(purelib.resolve()):
        raise AuditError("installed_tau_not_loaded_from_built_wheel")
    return str(path)


def _verify_public_interface(
    *,
    tau: Path,
    checkout: Path,
    cwd: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    catalog = _run_json([str(tau), "workflows", "list", "--json"], cwd, commands, env)
    values = catalog.get("workflows")
    if not isinstance(values, list):
        raise AuditError("workflow_catalog_missing")
    ids = [item.get("workflow_id") for item in values if isinstance(item, dict)]
    if ids != list(CATALOG_ORDER) or set(ids) != set(WORKFLOW_IDS):
        raise AuditError("installed_workflow_catalog_mismatch")

    descriptions: dict[str, Any] = {}
    for workflow_id in WORKFLOW_IDS:
        item = _run_json(
            [str(tau), "workflows", "describe", workflow_id, "--json"],
            cwd,
            commands,
            env,
        )
        if item.get("workflow_id") != workflow_id:
            raise AuditError(f"workflow_description_id_mismatch:{workflow_id}")
        if item.get("topology") != TOPOLOGIES[workflow_id]:
            raise AuditError(f"workflow_description_topology_mismatch:{workflow_id}")
        descriptions[workflow_id] = {
            "topology": item["topology"],
            "result_schema": item.get("result_schema"),
            "result_node_id": item.get("result_node_id"),
        }

    run_help = _run_text([str(tau), "workflows", "run", "--help"], cwd, commands, env)
    viewer_capabilities = _run_json(
        [str(tau), "dag-view-capabilities", "--json"], cwd, commands, env
    )
    run_options = ["--repo", "--run-dir", "--goal", "--publish-path", "--require-tests"]
    if any(option not in run_help for option in run_options):
        raise AuditError("workflow_help_contract_missing")
    expected_viewer_capabilities = {
        "schema": "tau.dag_viewer_capabilities.v1",
        "manifest_schema": "tau.dag_view_manifest.v1",
        "read_only": True,
        "supports_live": True,
    }
    if any(
        viewer_capabilities.get(key) != value for key, value in expected_viewer_capabilities.items()
    ):
        raise AuditError("viewer_capabilities_contract_missing")

    readme = (checkout / "README.md").read_text(encoding="utf-8")
    documented = [
        "tau workflows list --json",
        *[f"tau workflows run {workflow_id}" for workflow_id in WORKFLOW_IDS],
        "tau workflows repair",
        "tau workflows approve",
        "tau workflows resume",
        "tau dag-view --run-dir",
    ]
    if any(command not in readme for command in documented):
        raise AuditError("readme_public_command_missing")

    return {
        "catalog_schema": catalog.get("schema"),
        "workflow_ids": ids,
        "descriptions": descriptions,
        "run_help_verified_options": run_options,
        "viewer_capabilities": viewer_capabilities,
        "readme_commands_verified": documented,
    }


def _run_all_workflows(
    *,
    tau: Path,
    checkout: Path,
    fixture: Path,
    dirty_fixture: Path,
    no_tests_fixture: Path,
    root: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    simple_specs = (
        (
            "repository-readiness",
            fixture,
            [
                "--goal",
                "Determine whether this checkout is ready for focused work.",
                "--require-clean",
            ],
            dirty_fixture,
            [
                "--goal",
                "Determine whether this checkout is ready for focused work.",
                "--require-clean",
            ],
        ),
        (
            "tau-operator-reference",
            checkout,
            [],
            checkout,
            ["--required-workflow", "deliberately-absent-workflow"],
        ),
        (
            "repository-evidence-map",
            fixture,
            ["--goal", "Map this repository for focused work.", "--require-tests"],
            no_tests_fixture,
            ["--goal", "Map this repository for focused work.", "--require-tests"],
        ),
    )
    records = [
        _run_simple_workflow(
            tau=tau,
            workflow_id=workflow_id,
            positive_repo=positive_repo,
            positive_options=positive_options,
            negative_repo=negative_repo,
            negative_options=negative_options,
            root=root,
            env=env,
            commands=commands,
        )
        for (
            workflow_id,
            positive_repo,
            positive_options,
            negative_repo,
            negative_options,
        ) in simple_specs
    ]
    records.append(_run_approved_release(tau, fixture, root, env, commands))
    records.append(_run_durable_qualification(tau, fixture, root, env, commands))
    return records


def _run_simple_workflow(
    *,
    tau: Path,
    workflow_id: str,
    positive_repo: Path,
    positive_options: list[str],
    negative_repo: Path,
    negative_options: list[str],
    root: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    positive_run = root / "runs" / f"{workflow_id}-positive"
    negative_run = root / "runs" / f"{workflow_id}-negative"
    positive = _workflow_run(
        tau, workflow_id, positive_repo, positive_run, positive_options, root, env, commands
    )
    negative = _workflow_run(
        tau,
        workflow_id,
        negative_repo,
        negative_run,
        negative_options,
        root,
        env,
        commands,
        expected=(1,),
    )
    _assert_workflow(positive, workflow_id, "PASS")
    _assert_workflow(negative, workflow_id, "BLOCKED")
    _assert_results_absent(negative_run, RESULT_NAMES[workflow_id])

    return _workflow_record(
        workflow_id=workflow_id,
        run_dir=positive_run,
        result=_result_evidence(positive_run, RESULT_NAMES[workflow_id]),
        viewers=[
            _viewer_evidence(tau, positive_run, workflow_id, root, env, commands),
            _viewer_evidence(tau, negative_run, workflow_id, root, env, commands),
        ],
        negative={"status": "BLOCKED"},
    )


def _run_approved_release(
    tau: Path,
    fixture: Path,
    root: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    workflow_id = "approved-release-bundle"
    run_dir = root / "runs" / workflow_id
    publish = root / "published" / workflow_id
    initial = _workflow_run(
        tau,
        workflow_id,
        fixture,
        run_dir,
        ["--goal", "Publish an approved local release bundle.", "--publish-path", str(publish)],
        root,
        env,
        commands,
        expected=(1,),
    )
    _assert_workflow(initial, workflow_id, "BLOCKED")
    _assert_results_absent(run_dir, workflow_id)
    viewers = [_viewer_evidence(tau, run_dir, workflow_id, root, env, commands)]

    approval = _run_json([str(tau), "workflows", "approve", str(run_dir)], root, commands, env)
    if approval.get("status") != "PASS":
        raise AuditError("approved_release_approval_failed")
    final = _run_json([str(tau), "workflows", "resume", str(run_dir)], root, commands, env)
    _assert_workflow(final, workflow_id, "PASS")
    before = _result_evidence(run_dir, workflow_id)
    repeated = _run_json([str(tau), "workflows", "resume", str(run_dir)], root, commands, env)
    _assert_workflow(repeated, workflow_id, "PASS")
    after = _result_evidence(run_dir, workflow_id)
    if before != after:
        raise AuditError("approved_release_changed_after_repeated_resume")
    _assert_publication(publish, run_dir, workflow_id)
    viewers.append(_viewer_evidence(tau, run_dir, workflow_id, root, env, commands))

    return _workflow_record(
        workflow_id=workflow_id,
        run_dir=run_dir,
        result=after,
        viewers=viewers,
        negative={"status": "BLOCKED", "boundary": "APPROVAL_REQUIRED"},
        extra={"repeated_resume_status": "PASS", "publication_verified": True},
    )


def _run_durable_qualification(
    tau: Path,
    fixture: Path,
    root: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    workflow_id = "durable-repository-qualification"
    run_dir = root / "runs" / workflow_id
    publish = root / "published" / workflow_id
    initial = _workflow_run(
        tau,
        workflow_id,
        fixture,
        run_dir,
        [
            "--goal",
            "Qualify this repository through durable recovery.",
            "--publish-path",
            str(publish),
            "--inject-test-branch-failure",
        ],
        root,
        env,
        commands,
        expected=(1,),
    )
    _assert_workflow(initial, workflow_id, "BLOCKED")
    _assert_results_absent(run_dir, workflow_id)
    viewers = [_viewer_evidence(tau, run_dir, workflow_id, root, env, commands)]

    repair = _run_json(
        [str(tau), "workflows", "repair", str(run_dir), "--node", "qualify-tests"],
        root,
        commands,
        env,
    )
    if repair.get("status") != "PASS" or repair.get("node_id") != "qualify-tests":
        raise AuditError("durable_repair_contract_failed")

    waiting = _run_json(
        [str(tau), "workflows", "resume", str(run_dir)],
        root,
        commands,
        env,
        expected=(1,),
    )
    _assert_workflow(waiting, workflow_id, "BLOCKED")
    viewers.append(_viewer_evidence(tau, run_dir, workflow_id, root, env, commands))

    approval = _run_json([str(tau), "workflows", "approve", str(run_dir)], root, commands, env)
    if approval.get("status") != "PASS":
        raise AuditError("durable_approval_failed")
    final = _run_json([str(tau), "workflows", "resume", str(run_dir)], root, commands, env)
    _assert_workflow(final, workflow_id, "PASS")
    before = _result_evidence(run_dir, workflow_id)
    repeated = _run_json([str(tau), "workflows", "resume", str(run_dir)], root, commands, env)
    _assert_workflow(repeated, workflow_id, "PASS")
    after = _result_evidence(run_dir, workflow_id)
    if before != after:
        raise AuditError("durable_result_changed_after_repeated_resume")
    _assert_publication(publish, run_dir, workflow_id)

    ledger = _read_object(publish / "publication-ledger.json", "publication ledger")
    if ledger.get("effect_count") != 1:
        raise AuditError("durable_publication_effect_count_invalid")
    receipt = _read_object(run_dir / "run-receipt.json", "durable run receipt")
    reused = {
        item.get("node_id"): item.get("resumed")
        for item in receipt.get("nodes", [])
        if isinstance(item, dict)
        and item.get("node_id")
        in {"capture-repository", "qualify-documentation", "qualify-package"}
    }
    if reused != {
        "capture-repository": True,
        "qualify-documentation": True,
        "qualify-package": True,
    }:
        raise AuditError("durable_unaffected_work_not_reused")

    viewers.append(_viewer_evidence(tau, run_dir, workflow_id, root, env, commands))
    return _workflow_record(
        workflow_id=workflow_id,
        run_dir=run_dir,
        result=after,
        viewers=viewers,
        negative={"status": "BLOCKED", "boundary": "targeted_repair_required"},
        extra={
            "repair_packet_path": repair.get("repair_packet_path"),
            "approval_wait_status": waiting["status"],
            "repeated_resume_status": "PASS",
            "publication_effect_count": 1,
            "reused_node_ids": sorted(reused),
        },
    )


def _workflow_run(
    tau: Path,
    workflow_id: str,
    repo: Path,
    run_dir: Path,
    options: list[str],
    cwd: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
    expected: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    argv = [
        str(tau),
        "workflows",
        "run",
        workflow_id,
        "--repo",
        str(repo),
        "--run-dir",
        str(run_dir),
        *options,
    ]
    return _run_json(argv, cwd, commands, env, expected)


def _viewer_evidence(
    tau: Path,
    run_dir: Path,
    workflow_id: str,
    cwd: Path,
    env: dict[str, str],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    port = _open_port()
    argv = [
        str(tau),
        "dag-view",
        "--run-dir",
        str(run_dir),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-open",
    ]
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    endpoint = f"http://127.0.0.1:{port}/api/v1/manifest"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    evidence: dict[str, Any] | None = None
    failure: Exception | None = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and evidence is None:
            if process.poll() is not None:
                raise AuditError(f"viewer_exited_before_http:{workflow_id}")
            try:
                with opener.open(endpoint, timeout=1) as response:  # noqa: S310
                    status = int(response.status)
                    manifest = json.loads(response.read())
                evidence = _validate_viewer_manifest(
                    manifest=manifest,
                    status=status,
                    run_dir=run_dir,
                    workflow_id=workflow_id,
                    endpoint=endpoint,
                )
            except OSError, urllib.error.URLError, json.JSONDecodeError:
                time.sleep(0.05)
        if evidence is None:
            raise AuditError(f"viewer_http_timeout:{workflow_id}")
    except Exception as exc:
        failure = exc

    exited_early = process.poll() is not None
    if not exited_early:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGINT)
    try:
        returncode = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            returncode = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            returncode = 124
    commands.append({"argv": argv, "cwd": str(cwd), "returncode": returncode})

    if failure is not None:
        raise failure
    if exited_early:
        raise AuditError(f"viewer_exited_before_termination:{workflow_id}")
    if returncode == 124 or evidence is None:
        raise AuditError(f"viewer_termination_failed:{workflow_id}")
    evidence.update({"process_returncode": returncode, "terminated_cleanly": True})
    return evidence


def _validate_viewer_manifest(
    *,
    manifest: Any,
    status: int,
    run_dir: Path,
    workflow_id: str,
    endpoint: str,
) -> dict[str, Any]:
    if not isinstance(manifest, dict) or status != 200:
        raise AuditError(f"viewer_manifest_invalid:{workflow_id}")
    workflow = manifest.get("workflow")
    if not isinstance(workflow, dict) or workflow.get("workflow_id") != workflow_id:
        raise AuditError(f"viewer_workflow_identity_mismatch:{workflow_id}")
    run_id = manifest.get("run_id")
    source = _read_object(run_dir / "workflow" / "dag.json", "workflow source DAG")
    logical_run_id = source.get("run_id")
    if not isinstance(run_id, str) or not isinstance(logical_run_id, str):
        raise AuditError(f"viewer_run_identity_missing:{workflow_id}")
    if run_id != logical_run_id and not run_id.startswith(f"{logical_run_id}:generation:"):
        raise AuditError(f"viewer_run_identity_mismatch:{workflow_id}")
    if manifest.get("schema") != "tau.dag_view_manifest.v1":
        raise AuditError(f"viewer_manifest_schema_invalid:{workflow_id}")
    if manifest.get("source_available") is not True:
        raise AuditError(f"viewer_source_not_retained:{workflow_id}")
    return {
        "endpoint": endpoint,
        "http_status": status,
        "schema": manifest["schema"],
        "workflow_id": workflow_id,
        "logical_run_id": logical_run_id,
        "run_id": run_id,
        "plan_sha256": manifest.get("plan_sha256"),
        "manifest_sha256": _sha256_json(manifest),
        "source_available": True,
    }


def _workflow_record(
    *,
    workflow_id: str,
    run_dir: Path,
    result: dict[str, Any],
    viewers: list[dict[str, Any]],
    negative: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "workflow_id": workflow_id,
        "topology": TOPOLOGIES[workflow_id],
        "temporary_run_dir": str(run_dir),
        "positive": {"status": "PASS", "result": result},
        "negative": negative,
        "viewer_evidence": viewers,
    }
    record.update(extra or {})
    return record


def _result_evidence(run_dir: Path, basename: str) -> dict[str, Any]:
    json_path = run_dir / "results" / f"{basename}.json"
    markdown_path = run_dir / "results" / f"{basename}.md"
    if any(not path.is_file() or path.stat().st_size < 1 for path in (json_path, markdown_path)):
        raise AuditError(f"workflow_result_missing_or_empty:{basename}")
    payload = _read_object(json_path, f"{basename} result")
    return {
        "json": {
            "filename": json_path.name,
            "schema": payload.get("schema"),
            "status": payload.get("status"),
            "summary": payload.get("summary"),
            "sha256": _sha256(json_path),
            "bytes": json_path.stat().st_size,
        },
        "markdown": {
            "filename": markdown_path.name,
            "sha256": _sha256(markdown_path),
            "bytes": markdown_path.stat().st_size,
        },
    }


def _assert_workflow(payload: dict[str, Any], workflow_id: str, status: str) -> None:
    if payload.get("workflow_id") != workflow_id or payload.get("status") != status:
        raise AuditError(f"workflow_result_mismatch:{workflow_id}:{status}")
    if (status == "PASS") != (payload.get("ok") is True):
        raise AuditError(f"workflow_ok_mismatch:{workflow_id}:{status}")


def _assert_results_absent(run_dir: Path, basename: str) -> None:
    if any((run_dir / "results" / f"{basename}.{suffix}").exists() for suffix in ("json", "md")):
        raise AuditError(f"blocked_workflow_published_result:{basename}")


def _assert_publication(publish: Path, run_dir: Path, basename: str) -> None:
    for suffix in ("json", "md"):
        target = publish / f"{basename}.{suffix}"
        result = run_dir / "results" / f"{basename}.{suffix}"
        if not target.is_file() or target.read_bytes() != result.read_bytes():
            raise AuditError(f"published_result_mismatch:{basename}.{suffix}")


def _validate_supplied_proofs(
    *,
    expected_source_ref: str,
    readiness_positive_browser: Path,
    readiness_negative_browser: Path,
    operator_positive_browser: Path,
    operator_negative_browser: Path,
    evidence_positive_browser: Path,
    evidence_negative_browser: Path,
    slice04_browser: Path,
    slice04_rerun: Path,
    slice05_browser: Path,
    slice05_wheel: Path,
) -> list[dict[str, Any]]:
    supplied = (
        ("readiness_positive_browser", readiness_positive_browser),
        ("readiness_negative_browser", readiness_negative_browser),
        ("operator_positive_browser", operator_positive_browser),
        ("operator_negative_browser", operator_negative_browser),
        ("evidence_positive_browser", evidence_positive_browser),
        ("evidence_negative_browser", evidence_negative_browser),
        ("slice04_browser", slice04_browser),
        ("slice04_no_accepted_producer_rerun", slice04_rerun),
        ("slice05_browser", slice05_browser),
        ("slice05_wheel", slice05_wheel),
    )
    records = []
    browser_labels = set(BROWSER_REQUIRED_CHECKS)
    for label, value in supplied:
        path = value.expanduser().resolve()
        payload = _read_object(path, label)
        if payload.get("status") != "PASS":
            raise AuditError(f"supplied_proof_not_pass:{label}")
        if payload.get("live") is not True or payload.get("mocked") is not False:
            raise AuditError(f"supplied_proof_not_live_nonmocked:{label}")
        if payload.get("provider_live") is True:
            raise AuditError(f"supplied_proof_provider_live_unexpected:{label}")
        schema = payload.get("schema")
        if not isinstance(schema, str) or not schema:
            raise AuditError(f"supplied_proof_schema_missing:{label}")
        if label != "slice05_wheel" and payload.get("source_ref") != expected_source_ref:
            raise AuditError(f"supplied_proof_source_ref_mismatch:{label}")

        checks = payload.get("checks")
        if label in browser_labels:
            required_checks = BROWSER_REQUIRED_CHECKS[label]
            if not isinstance(checks, dict) or not all(
                checks.get(check) is True for check in required_checks
            ):
                raise AuditError(f"supplied_proof_checks_failed:{label}")
            methods = payload.get("request_methods")
            if set(methods or []) != {"GET"}:
                raise AuditError(f"supplied_proof_browser_not_get_only:{label}")
            screenshots = [
                _screenshot_binding(payload, label, "desktop"),
                _screenshot_binding(payload, label, "mobile"),
            ]
        else:
            methods = payload.get("request_methods")
            screenshots = []

        if label == "slice05_wheel":
            if set(payload.get("installed_workflow_ids", [])) != set(WORKFLOW_IDS):
                raise AuditError("slice05_wheel_catalog_mismatch")
            if payload.get("publication_effect_count") != 1:
                raise AuditError("slice05_wheel_publication_count_invalid")
            if payload.get("repeated_resume_status") != "PASS":
                raise AuditError("slice05_wheel_repeated_resume_missing")
        if label == "slice04_no_accepted_producer_rerun" and (
            not isinstance(checks, dict)
            or not checks
            or not all(value is True for value in checks.values())
        ):
            raise AuditError("slice04_rerun_checks_failed")

        records.append(
            {
                "label": label,
                "path": str(path),
                "schema": schema,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "provider_live": payload.get("provider_live", False),
                "check_count": len(checks) if isinstance(checks, dict) else None,
                "request_methods": methods,
                "screenshots": screenshots,
                "publication_effect_count": payload.get("publication_effect_count"),
                "repeated_resume_status": payload.get("repeated_resume_status"),
                "source_ref": payload.get("source_ref"),
                "wheel_sha256": payload.get("wheel_sha256"),
            }
        )
    return records


def _screenshot_binding(payload: dict[str, Any], label: str, kind: str) -> dict[str, Any]:
    value = payload.get(f"{kind}_screenshot")
    declared = payload.get(f"{kind}_screenshot_sha256")
    if not isinstance(value, str) or not isinstance(declared, str):
        raise AuditError(f"supplied_proof_screenshot_binding_missing:{label}:{kind}")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size < 1 or _sha256(path) != declared:
        raise AuditError(f"supplied_proof_screenshot_invalid:{label}:{kind}")
    return {"kind": kind, "path": str(path), "sha256": declared, "bytes": path.stat().st_size}


def _goal_contract(checkout: Path) -> dict[str, Any]:
    path = checkout / "GOAL.md"
    text = path.read_text(encoding="utf-8")
    snippets = [
        "all five canonical DAGs execute",
        "the ladder visibly progresses",
        "each DAG has a deterministic acceptance contract",
        "the advanced DAG demonstrates crash-safe resume",
        "the human-gated DAG demonstrates an exact approval boundary and rollback",
        "the same viewer renders fresh authoritative progress for all five DAGs",
        "the React Flow graph visibly updates during execution without manual reload",
        "a clean checkout can launch the DAGs and viewer using documented commands",
        "final proof reports `mocked: no`, `live: yes`",
        "the human accepts that the workflows and viewer make Tau's value and state",
    ]
    missing = [snippet for snippet in snippets if snippet not in text]
    if missing:
        raise AuditError(f"goal_completion_criterion_missing:{missing[0]}")
    return {
        "path": "GOAL.md",
        "sha256": _sha256(path),
        "completion_criterion_count": 10,
        "completion_criteria_verified": snippets,
    }


def _established_criteria(
    workflow_records: list[dict[str, Any]], proofs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    workflow_ids = [item["workflow_id"] for item in workflow_records]
    proof_labels = [item["label"] for item in proofs]
    evidence = {
        1: ["Five installed workflows produced non-empty JSON/Markdown results.", *workflow_ids],
        2: [f"{item}:{TOPOLOGIES[item]}" for item in WORKFLOW_IDS],
        3: ["Every workflow record includes a deterministic blocked or approval path."],
        4: ["slice05_browser", "slice05_wheel", "Repeated resume retained effect_count=1."],
        5: ["slice04_browser", "slice04_no_accepted_producer_rerun"],
        6: ["Every workflow served an identified tau.dag_view_manifest.v1 over HTTP GET."],
        7: [*BROWSER_REQUIRED_CHECKS],
        8: ["Exact-ref wheel install, five public sequences, results, and viewers passed."],
        9: proof_labels,
    }
    return [
        {"criterion": number, "status": "ESTABLISHED", "evidence": evidence[number]}
        for number in range(1, 10)
    ] + [
        {
            "criterion": 10,
            "status": "MISSING",
            "evidence": ["Human acceptance is not recorded by this automated audit."],
        }
    ]


def _claims() -> dict[str, list[str]]:
    return {
        "proves": [
            "The exact source ref was checked out in a detached clean Git worktree.",
            "A wheel from that ref was installed and imported from a temporary environment.",
            "The installed public tau command discovered exactly the five locked workflows.",
            "All five public workflow sequences produced useful results and blocked paths.",
            "The installed shared viewer served an identified GET manifest for every run.",
            "Positive and negative browser proofs for all five workflows were retained "
            "and hash-bound.",
            "The durable ledger and repeated resume retained one publication effect.",
        ],
        "does_not_prove": [
            "Human acceptance of the immutable goal.",
            "Provider or model semantic quality.",
            "Production deployment readiness, a formal airgap, or secret absence.",
            "Automatic recovery from process loss before a result is durably staged.",
            "Application-level exactly-once publication from SQLite WAL semantics alone.",
        ],
    }


def _blocked_audit(
    requested_ref: str, commands: list[dict[str, Any]], exc: Exception
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "BLOCKED",
        "source_ref": None,
        "requested_ref": requested_ref,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "commands": commands,
        "workflows": [],
        "supplied_proofs": [],
        "criteria": [
            {"criterion": number, "status": "MISSING", "evidence": ["Audit did not complete."]}
            for number in range(1, 11)
        ],
        "first_unmet_criterion": 1,
        "failure": {"type": type(exc).__name__, "message": _failure_message(exc)},
        "claims": {
            "proves": ["Only that the audit failed closed and retained command records."],
            "does_not_prove": ["Any immutable-goal completion criterion."],
        },
        "research_sources": RESEARCH_SOURCES,
    }


def _repository_root(path: Path, commands: list[dict[str, Any]]) -> Path:
    candidate = path.expanduser().resolve()
    root = _run_text(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"], candidate, commands
    ).strip()
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise AuditError("repository_root_missing")
    return resolved


def _verified_ref(repo: Path, requested: str, commands: list[dict[str, Any]]) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{40}", requested) is None:
        raise AuditError("source_ref_must_be_full_commit_sha")
    resolved = _run_text(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{requested}^{{commit}}"],
        repo,
        commands,
    ).strip()
    if resolved.casefold() != requested.casefold():
        raise AuditError("source_ref_resolution_mismatch")
    return resolved


def _git_fixture(
    path: Path,
    commands: list[dict[str, Any]],
    *,
    with_tests: bool,
    dirty: bool = False,
) -> Path:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# Immutable goal audit fixture\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname = "tau-audit-fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    if with_tests:
        (path / "tests").mkdir()
        (path / "tests" / "test_fixture.py").write_text(
            "def test_fixture():\n    assert True\n", encoding="utf-8"
        )
    _run(["git", "init", "-q", "-b", "main", str(path)], path.parent, commands)
    _run(["git", "-C", str(path), "add", "."], path, commands)
    _run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Tau Audit",
            "-c",
            "user.email=tau-audit@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        path,
        commands,
    )
    if dirty:
        (path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    return path


def _installed_environment(bin_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PIP_NO_INDEX"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _git_status(path: Path, commands: list[dict[str, Any]]) -> str:
    return _run_text(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"],
        path,
        commands,
    )


def _git_head(path: Path, commands: list[dict[str, Any]]) -> str:
    return _run_text(["git", "-C", str(path), "rev-parse", "HEAD"], path, commands).strip()


def _open_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_json(
    argv: list[str],
    cwd: Path,
    commands: list[dict[str, Any]],
    env: dict[str, str] | None = None,
    expected: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    result = _run(argv, cwd, commands, env, expected)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AuditError(f"command_stdout_not_json:{argv[0]}:{argv[1:3]}") from exc
    if not isinstance(payload, dict):
        raise AuditError("command_stdout_json_not_object")
    return payload


def _run_text(
    argv: list[str],
    cwd: Path,
    commands: list[dict[str, Any]],
    env: dict[str, str] | None = None,
) -> str:
    return _run(argv, cwd, commands, env).stdout


def _run(
    argv: list[str],
    cwd: Path,
    commands: list[dict[str, Any]],
    env: dict[str, str] | None = None,
    expected: tuple[int, ...] = (0,),
    timeout: float = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        commands.append({"argv": argv, "cwd": str(cwd), "returncode": 124})
        raise AuditError(f"command_timeout:{' '.join(argv)}") from exc
    commands.append({"argv": argv, "cwd": str(cwd), "returncode": result.returncode})
    if result.returncode not in expected:
        raise CommandError(argv, result.returncode)
    return result


def _run_unchecked(
    argv: list[str], cwd: Path, commands: list[dict[str, Any]]
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=30, check=False
        )
    except OSError, subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(argv, 124, "", "")
    commands.append({"argv": argv, "cwd": str(cwd), "returncode": result.returncode})
    return result


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"{label}_missing_or_malformed") from exc
    if not isinstance(payload, dict):
        raise AuditError(f"{label}_not_object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _sha256_json(value: dict[str, Any]) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _failure_message(exc: Exception) -> str:
    return (str(exc).replace("\n", " ").strip() or type(exc).__name__)[:1000]


if __name__ == "__main__":
    raise SystemExit(main())
