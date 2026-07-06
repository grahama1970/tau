#!/usr/bin/env python3
"""Run Tau real-world, non-mocked sanity checks.

The runner intentionally excludes Tau commands whose own receipts report
``mocked: true``. It writes one inspectable receipt under
``experiments/goal-locked-subagents/proofs/real-world-sanity``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.real_world_sanity_suite_receipt.v1"
CHECK_SCHEMA = "tau.real_world_sanity_check_receipt.v1"
HERDR_GC_DEFAULT_LABEL_PREFIXES = (
    "rw-sanity-generic-provider-",
    "rw-sanity-provider-",
    "tau-live-provider-",
    "tau-provider-dag-",
    "tau-generic-provider-",
    "tau-traycer-",
)
HERDR_GC_DEFAULT_TARGET_ID = f"herdr-gc:{','.join(HERDR_GC_DEFAULT_LABEL_PREFIXES)}"


@dataclass(frozen=True)
class Check:
    check_id: str
    level: str
    purpose: str
    command: list[str]
    timeout_seconds: int
    expected_exit_codes: tuple[int, ...] = (0,)
    expected_status: str | None = None
    expected_verdict: str | None = None
    expected_provider_live: bool | None = None
    require_mocked_false: bool = True
    require_json_receipt: bool = True
    output_receipt: Path | None = None
    expected_min_provider_session_states: int = 0
    expected_min_resumed_nodes: int = 0
    attempts: int = 1
    post_cleanup_mode: str = "off"
    post_cleanup_uv_bin: str = "uv"
    post_cleanup_herdr_bin: str = "herdr"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("experiments/goal-locked-subagents/proofs/real-world-sanity"),
    )
    parser.add_argument("--label", default="tau-real-world-sanity")
    parser.add_argument(
        "--levels",
        default="simple,medium,advanced",
        help="Comma-separated levels to run. Default runs all levels.",
    )
    parser.add_argument(
        "--checks",
        default="",
        help="Optional comma-separated check ids to run after level filtering.",
    )
    parser.add_argument("--uv-bin", default=os.environ.get("UV_BIN", "uv"))
    parser.add_argument("--herdr-bin", default=os.environ.get("HERDR_BIN", "herdr"))
    parser.add_argument("--receipt-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--provider-cleanup-mode",
        choices=("off", "audit", "dry-run", "apply"),
        default="dry-run",
        help=(
            "Cleanup mode for provider-owned Herdr resources created by advanced checks. "
            "Default records dry-run receipts; apply closes only run-owned workspaces."
        ),
    )
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    if not repo.exists():
        raise SystemExit(f"repo does not exist: {repo}")
    run_root = args.run_root.expanduser()
    if not run_root.is_absolute():
        run_root = repo / run_root
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{compact_stamp()}-{slug(args.label)}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    selected_levels = {level.strip() for level in args.levels.split(",") if level.strip()}

    checks = build_checks(
        repo=repo,
        run_dir=run_dir,
        uv_bin=args.uv_bin,
        herdr_bin=args.herdr_bin,
        receipt_timeout_seconds=args.receipt_timeout_seconds,
        provider_cleanup_mode=args.provider_cleanup_mode,
    )
    selected_checks = [check for check in checks if check.level in selected_levels]
    selected_check_ids = {
        check_id.strip() for check_id in args.checks.split(",") if check_id.strip()
    }
    if selected_check_ids:
        selected_checks = [
            check for check in selected_checks if check.check_id in selected_check_ids
        ]
        missing = sorted(selected_check_ids - {check.check_id for check in selected_checks})
        if missing:
            raise SystemExit(f"unknown or unselected check ids: {', '.join(missing)}")
    if not selected_checks:
        raise SystemExit(f"no checks selected by levels={args.levels!r}")

    records: list[dict[str, Any]] = []
    for check in selected_checks:
        records.append(run_check(check, repo=repo, run_dir=run_dir))
        write_suite_receipt(
            repo=repo,
            run_dir=run_dir,
            run_id=run_id,
            records=records,
            selected_levels=sorted(selected_levels),
            complete=False,
        )

    receipt = write_suite_receipt(
        repo=repo,
        run_dir=run_dir,
        run_id=run_id,
        records=records,
        selected_levels=sorted(selected_levels),
        complete=True,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] is True else 1


def build_checks(
    *,
    repo: Path,
    run_dir: Path,
    uv_bin: str,
    herdr_bin: str,
    receipt_timeout_seconds: int,
    provider_cleanup_mode: str,
) -> list[Check]:
    uv_tau = [uv_bin, "run", "--project", str(repo), "tau"]
    live_provider_receipt_timeout_seconds = max(receipt_timeout_seconds, 300)
    handoff = create_handoff_loop_fixture(run_dir, repo=repo)
    project_dag_simple = create_project_dag_fixture(
        run_dir,
        scenario="simple",
        goal_hash="sha256:rw-sanity-project-dag-simple",
    )
    project_dag_yaml = create_project_dag_fixture(
        run_dir,
        scenario="yaml",
        goal_hash="sha256:rw-sanity-project-dag-yaml",
        contract_format="yaml",
    )
    project_dag_medium = create_project_dag_fixture(
        run_dir,
        scenario="medium",
        goal_hash="sha256:rw-sanity-project-dag-medium",
    )
    project_dag_concurrent = create_project_dag_fixture(
        run_dir,
        scenario="concurrent",
        goal_hash="sha256:rw-sanity-project-dag-concurrent",
    )
    project_dag_concurrent_timeout_retry = create_project_dag_fixture(
        run_dir,
        scenario="concurrent-timeout-retry",
        goal_hash="sha256:rw-sanity-project-dag-concurrent-timeout-retry",
    )
    project_dag_concurrent_non_json_retry = create_project_dag_fixture(
        run_dir,
        scenario="concurrent-non-json-retry",
        goal_hash="sha256:rw-sanity-project-dag-concurrent-non-json-retry",
    )
    project_dag_concurrent_max_retries = create_project_dag_fixture(
        run_dir,
        scenario="concurrent-max-retries",
        goal_hash="sha256:rw-sanity-project-dag-concurrent-max-retries",
    )
    project_dag_concurrent_pointless_test_drift = create_project_dag_fixture(
        run_dir,
        scenario="concurrent-pointless-test-drift",
        goal_hash="sha256:rw-sanity-project-dag-concurrent-pointless-test-drift",
    )
    project_dag_concurrent_brave_required = create_project_dag_fixture(
        run_dir,
        scenario="concurrent-brave-required",
        goal_hash="sha256:rw-sanity-project-dag-concurrent-brave-required",
    )
    project_dag_complex = create_project_dag_fixture(
        run_dir,
        scenario="complex",
        goal_hash="sha256:rw-sanity-project-dag-complex",
    )
    project_dag_timeout = create_project_dag_fixture(
        run_dir,
        scenario="timeout",
        goal_hash="sha256:rw-sanity-project-dag-timeout",
    )
    project_dag_non_json = create_project_dag_fixture(
        run_dir,
        scenario="non-json",
        goal_hash="sha256:rw-sanity-project-dag-non-json",
    )
    project_dag_max_steps = create_project_dag_fixture(
        run_dir,
        scenario="max-steps",
        goal_hash="sha256:rw-sanity-project-dag-max-steps",
    )
    project_dag_bad_contract = create_project_dag_bad_contract_fixture(run_dir)
    project_dag_cycle = create_project_dag_policy_fixture(
        run_dir,
        scenario="ready-queue-cycle",
        mutation="cycle",
    )
    project_dag_mutating = create_project_dag_policy_fixture(
        run_dir,
        scenario="ready-queue-mutating",
        mutation="mutating",
    )
    project_dag_provider_policy = create_project_dag_policy_fixture(
        run_dir,
        scenario="ready-queue-provider-policy",
        mutation="provider",
    )
    project_dag_evidence_manifest_goal_drift = create_project_dag_evidence_manifest_fixture(run_dir)
    project_dag_memory_evidence_valid = create_project_dag_memory_evidence_fixture(
        run_dir,
        scenario="memory-evidence-valid",
        mutation="valid",
    )
    project_dag_memory_evidence_inline = create_project_dag_memory_evidence_fixture(
        run_dir,
        scenario="memory-evidence-inline",
        mutation="inline_evidence",
    )
    project_dag_memory_evidence_clarify = create_project_dag_memory_evidence_fixture(
        run_dir,
        scenario="memory-evidence-clarify",
        mutation="clarify_route",
    )
    project_dag_memory_evidence_missing_hash = create_project_dag_memory_evidence_fixture(
        run_dir,
        scenario="memory-evidence-missing-hash",
        mutation="missing_case_hash",
    )
    evidence_manifest_valid = create_evidence_manifest_valid_fixture(run_dir)
    project_dag_command_policy_network = create_project_dag_command_policy_fixture(
        run_dir,
        scenario="command-policy-network",
        spec_flag="requires_network",
    )
    project_dag_command_policy_mutation = create_project_dag_command_policy_fixture(
        run_dir,
        scenario="command-policy-mutation",
        spec_flag="mutates",
    )
    project_dag_command_policy_allowed = create_project_dag_command_policy_fixture(
        run_dir,
        scenario="command-policy-allowed",
        spec_flag=None,
    )
    project_dag_provider_metadata = create_project_dag_provider_metadata_fixture(run_dir)
    project_dag_containment_missing_itar = create_project_dag_containment_gate_fixture(
        run_dir,
        scenario="containment-missing-itar",
        mutation="missing_itar",
    )
    project_dag_containment_all_gates = create_project_dag_containment_gate_fixture(
        run_dir,
        scenario="containment-all-gates",
        mutation="all_gates",
    )
    dag_expansion_tamper = create_dag_expansion_fixture(
        run_dir,
        scenario="dag-expansion-tampered-preview",
    )
    dag_expansion_source_tamper = create_dag_expansion_fixture(
        run_dir,
        scenario="dag-expansion-tampered-source",
    )
    dag_branch_locks = create_dag_branch_locks_fixture(run_dir)
    route_memory_apply = create_route_memory_fixture(run_dir)
    research_source = create_research_source_fixture(run_dir)
    proof_index = create_proof_index_fixture(run_dir)
    github_apply_policy = create_github_apply_policy_fixture(run_dir)
    generic_dag_spec = create_generic_dag_fixture(run_dir)
    generic_dag_resume_spec = create_generic_dag_resume_fixture(run_dir)
    generic_dag_stale_work_order_spec = create_generic_dag_stale_work_order_fixture(run_dir)
    generic_dag_timeout_spec = create_generic_dag_timeout_fixture(run_dir)
    approval = create_approval_gate_fixtures(run_dir)
    cleanup = create_cleanup_status_fixture(run_dir)
    cleanup_session = create_cleanup_session_fixture(run_dir)
    cleanup_gc = create_cleanup_gc_fixture(run_dir)
    orchestration_evidence = create_orchestration_evidence_status_fixture(run_dir)
    provider_lifecycle = create_provider_lifecycle_status_fixture(run_dir)
    provider_readiness_status = create_provider_readiness_status_fixture(run_dir)
    provider_pane_status = create_provider_pane_status_fixture(run_dir)
    provider_dag_status = create_provider_dag_status_fixture(run_dir)
    provider_root = run_dir / "advanced-provider-runs"
    generic_provider_adapter_spec = create_generic_provider_adapter_fixture(
        run_dir,
        repo=repo,
        uv_tau=uv_tau,
        herdr_bin=herdr_bin,
        receipt_timeout_seconds=live_provider_receipt_timeout_seconds,
        provider_cleanup_mode=provider_cleanup_mode,
    )
    return [
        Check(
            check_id="simple.version",
            level="simple",
            purpose="Tau CLI starts from the checked-out project and reports its version.",
            command=[uv_bin, "run", "--project", str(repo), "tau", "--version"],
            timeout_seconds=60,
            require_json_receipt=False,
            require_mocked_false=False,
        ),
        Check(
            check_id="simple.command_spec_catalog",
            level="simple",
            purpose="Planner, orchestrator, coder, and reviewer command specs are real JSON files.",
            command=[
                sys.executable,
                "-c",
                command_spec_probe(repo),
            ],
            timeout_seconds=60,
        ),
        Check(
            check_id="simple.local_handoff_loop",
            level="simple",
            purpose="A real Tau handoff command loop routes goal-guardian to verifier to human.",
            command=[
                *uv_tau,
                "handoff-command-loop",
                "--start",
                str(handoff["start"]),
                "--agents-root",
                str(handoff["agents_root"]),
                "--command-spec-root",
                str(handoff["command_spec_root"]),
                "--active-goal-hash",
                "sha256:active-goal",
                "--receipt-dir",
                str(handoff["receipt_dir"]),
                "--max-steps",
                "4",
            ],
            timeout_seconds=90,
            expected_status="WAITING",
            output_receipt=handoff["receipt_dir"] / "command-loop-receipt.json",
        ),
        Check(
            check_id="simple.project_dag_creator_reviewer",
            level="simple",
            purpose=(
                "Tau runs a tau.dag_contract.v1 creator-reviewer DAG, executes real local "
                "subprocess workers, and records a reviewer verdict against the immutable goal."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_simple["contract"]),
                "--receipt-dir",
                str(project_dag_simple["run_dir"]),
                "--agents-root",
                str(project_dag_simple["agents_root"]),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            expected_verdict="PASS",
        ),
        Check(
            check_id="simple.project_dag_yaml_creator_reviewer",
            level="simple",
            purpose=(
                "Tau runs a YAML tau.dag_contract.v1 creator-reviewer DAG through the "
                "same non-mocked local subprocess handoff path."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_yaml["contract"]),
                "--receipt-dir",
                str(project_dag_yaml["run_dir"]),
                "--agents-root",
                str(project_dag_yaml["agents_root"]),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            expected_verdict="PASS",
        ),
        Check(
            check_id="medium.provider_dag_plan",
            level="medium",
            purpose="Tau planner emits a receipt-backed scratch coder/reviewer DAG without providers.",
            command=[
                *uv_tau,
                "provider-dag-plan",
                "--label",
                "rw-sanity-plan",
                "--run-root",
                str(run_dir / "medium-provider-dag-plan"),
                "--max-attempts",
                "2",
            ],
            timeout_seconds=90,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.provider_dag_plan_status",
            level="medium",
            purpose="Tau summarizes a provider DAG planner-only run through the read-only run-status surface.",
            command=[
                sys.executable,
                "-c",
                run_status_after_json_command(
                    producer_command=[
                        *uv_tau,
                        "provider-dag-plan",
                        "--label",
                        "rw-sanity-plan-status",
                        "--run-root",
                        str(run_dir / "medium-provider-dag-plan-status"),
                        "--max-attempts",
                        "2",
                    ],
                    status_command_prefix=[*uv_tau, "run-status"],
                    run_dir_key="run_dir",
                ),
            ],
            timeout_seconds=120,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.generic_dag_run",
            level="medium",
            purpose="Tau executes a generic schema-validated local subprocess DAG with planner -> coder -> reviewer dependencies.",
            command=[
                *uv_tau,
                "dag-run",
                str(generic_dag_spec),
            ],
            timeout_seconds=90,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.generic_dag_status",
            level="medium",
            purpose="Tau summarizes a generic DAG run through the read-only run-status surface.",
            command=[
                uv_bin,
                "run",
                "--project",
                str(repo),
                "python",
                "-c",
                run_status_after_json_command(
                    producer_command=[
                        *uv_tau,
                        "dag-run",
                        str(generic_dag_spec),
                    ],
                    status_command_prefix=[*uv_tau, "run-status"],
                    run_dir_key="run_dir",
                ),
            ],
            timeout_seconds=90,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.generic_dag_resume",
            level="medium",
            purpose=(
                "Tau resumes a generic DAG from an existing valid node receipt and "
                "does not rerun that node command."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(generic_dag_resume_spec),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            expected_min_resumed_nodes=1,
        ),
        Check(
            check_id="medium.generic_dag_resume_from_run_dir",
            level="medium",
            purpose=(
                "Tau resumes a generic DAG from run-directory checkpoint metadata "
                "without requiring the operator to pass the original spec path."
            ),
            command=[
                uv_bin,
                "run",
                "--project",
                str(repo),
                "python",
                "-c",
                resume_from_run_dir_json_command(
                    first_command=[
                        *uv_tau,
                        "dag-run",
                        str(generic_dag_resume_spec),
                    ],
                    resume_command_prefix=[*uv_tau, "dag-resume"],
                ),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            expected_min_resumed_nodes=1,
        ),
        Check(
            check_id="medium.generic_dag_stale_work_order_blocks",
            level="medium",
            purpose=(
                "Tau refuses to resume a generic DAG node from a stale work-order "
                "receipt and blocks when rerun fails."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(generic_dag_stale_work_order_spec),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="SUBAGENT_ERROR",
        ),
        Check(
            check_id="medium.generic_dag_stale_work_order_status",
            level="medium",
            purpose=(
                "Tau summarizes a stale work-order blocked generic DAG through "
                "the read-only run-status surface without requiring provider artifacts."
            ),
            command=[
                uv_bin,
                "run",
                "--project",
                str(repo),
                "python",
                "-c",
                run_status_after_json_command(
                    producer_command=[
                        *uv_tau,
                        "dag-run",
                        str(generic_dag_stale_work_order_spec),
                    ],
                    status_command_prefix=[*uv_tau, "run-status"],
                    run_dir_key="run_dir",
                    producer_expected_exit_codes=(1,),
                ),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
        ),
        Check(
            check_id="medium.generic_dag_timeout_fail_closed",
            level="medium",
            purpose=(
                "Tau retries a timed-out generic DAG worker up to max_attempts "
                "and then blocks with SUBAGENT_TIMEOUT."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(generic_dag_timeout_spec),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="SUBAGENT_TIMEOUT",
        ),
        Check(
            check_id="medium.approval_gate_pass",
            level="medium",
            purpose="Tau accepts an explicit human approval packet before a gated working-tree mutation.",
            command=[
                *uv_tau,
                "approval-gate-check",
                "--approval-packet",
                str(approval["pass_packet"]),
                "--requested-action",
                "working_tree_mutation",
                "--run-dir",
                str(approval["pass_run_dir"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.approval_gate_fail_closed",
            level="medium",
            purpose="Tau blocks a GitHub ticket-closure gate when the human approval packet names a different action.",
            command=[
                *uv_tau,
                "approval-gate-check",
                "--approval-packet",
                str(approval["mismatch_packet"]),
                "--requested-action",
                "github_ticket_closure",
                "--run-dir",
                str(approval["blocked_run_dir"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
        ),
        Check(
            check_id="medium.approval_gate_expired_fail_closed",
            level="medium",
            purpose="Tau blocks a gated mutation when the human approval packet is expired.",
            command=[
                *uv_tau,
                "approval-gate-check",
                "--approval-packet",
                str(approval["expired_packet"]),
                "--requested-action",
                "working_tree_mutation",
                "--run-dir",
                str(approval["expired_run_dir"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
        ),
        Check(
            check_id="medium.approval_gate_status",
            level="medium",
            purpose="Tau summarizes the blocked approval-gate receipt through the read-only run-status surface.",
            command=[
                *uv_tau,
                "run-status",
                str(approval["blocked_run_dir"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
        ),
        Check(
            check_id="medium.herdr_cleanup_dry_run",
            level="medium",
            purpose="Tau identifies run-owned Herdr cleanup candidates without mutating Herdr.",
            command=[
                *uv_tau,
                "herdr-cleanup",
                "dry-run",
                "--run-dir",
                str(cleanup["run_dir"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.herdr_cleanup_status",
            level="medium",
            purpose="Tau summarizes a standalone Herdr cleanup receipt through the read-only run-status surface.",
            command=[
                *uv_tau,
                "run-status",
                str(cleanup["run_dir"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.herdr_cleanup_session_apply_fail_closed",
            level="medium",
            purpose=(
                "Tau blocks Herdr cleanup apply when a run records a session candidate, "
                "because session stop/delete requires explicit session ownership."
            ),
            command=[
                *uv_tau,
                "herdr-cleanup",
                "apply",
                "--run-dir",
                str(cleanup_session["blocked_run_dir"]),
                "--workspace-lease",
                str(cleanup_session["blocked_workspace_lease"]),
                "--herdr-bin",
                str(cleanup_session["blocked_herdr"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
        ),
        Check(
            check_id="medium.herdr_cleanup_session_apply_with_ownership",
            level="medium",
            purpose=(
                "Tau stops owned Herdr sessions only when a session ownership receipt "
                "covers the recorded session candidate."
            ),
            command=[
                *uv_tau,
                "herdr-cleanup",
                "apply",
                "--run-dir",
                str(cleanup_session["owned_run_dir"]),
                "--workspace-lease",
                str(cleanup_session["owned_workspace_lease"]),
                "--session-ownership",
                str(cleanup_session["session_ownership"]),
                "--herdr-bin",
                str(cleanup_session["owned_herdr"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.herdr_gc_apply_requires_approval",
            level="medium",
            purpose=(
                "Tau blocks broad Herdr GC apply when no approval receipt authorizes "
                "label-based workspace cleanup."
            ),
            command=[
                *uv_tau,
                "herdr-cleanup",
                "gc",
                "--run-dir",
                str(cleanup_gc["run_dir"]),
                "--apply",
                "--herdr-bin",
                str(cleanup_gc["herdr_bin"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
        ),
        Check(
            check_id="medium.herdr_gc_apply_with_approval",
            level="medium",
            purpose=(
                "Tau allows broad Herdr GC apply only after a generated approval "
                "receipt authorizes label-based workspace cleanup."
            ),
            command=[
                sys.executable,
                "-c",
                herdr_gc_apply_with_approval_command(
                    uv_tau=uv_tau,
                    fixture_dir=cleanup_gc["run_dir"],
                    herdr_bin=cleanup_gc["herdr_bin"],
                    approval_packet_path=cleanup_gc["approval_packet"],
                    approval_run_dir=cleanup_gc["approval_run_dir"],
                    receipt_path=cleanup_gc["run_dir"] / "herdr-gc-receipt.json",
                ),
            ],
            timeout_seconds=60,
            expected_status="PASS",
            output_receipt=cleanup_gc["run_dir"] / "herdr-gc-receipt.json",
        ),
        Check(
            check_id="medium.herdr_gc_apply_wrong_approval_target",
            level="medium",
            purpose=(
                "Tau blocks broad Herdr GC apply when the approval receipt target "
                "does not match the configured GC label-prefix scope."
            ),
            command=[
                sys.executable,
                "-c",
                herdr_gc_apply_wrong_target_command(
                    uv_tau=uv_tau,
                    fixture_dir=cleanup_gc["wrong_target_run_dir"],
                    herdr_bin=cleanup_gc["wrong_target_herdr_bin"],
                    approval_packet_path=cleanup_gc["wrong_target_approval_packet"],
                    approval_run_dir=cleanup_gc["wrong_target_approval_run_dir"],
                    receipt_path=cleanup_gc["wrong_target_run_dir"] / "herdr-gc-receipt.json",
                ),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            output_receipt=cleanup_gc["wrong_target_run_dir"] / "herdr-gc-receipt.json",
        ),
        Check(
            check_id="medium.orchestration_evidence_status",
            level="medium",
            purpose="Tau summarizes a standalone orchestration evidence receipt through the read-only run-status surface.",
            command=[
                *uv_tau,
                "run-status",
                str(orchestration_evidence["run_dir"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.provider_lifecycle_status",
            level="medium",
            purpose="Tau summarizes provider lifecycle state artifacts through the read-only run-status surface.",
            command=[
                *uv_tau,
                "run-status",
                str(provider_lifecycle["run_dir"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
            expected_min_provider_session_states=2,
        ),
        Check(
            check_id="medium.provider_lifecycle_crashed_ready_fail_closed",
            level="medium",
            purpose=(
                "Tau normalizes a provider readiness record that claims ready but has "
                "no live foreground process as crashed, not schedulable."
            ),
            command=[
                uv_bin,
                "run",
                "--project",
                str(repo),
                "python",
                "-c",
                _provider_lifecycle_crashed_ready_probe_code(),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.provider_readiness_status",
            level="medium",
            purpose="Tau summarizes structured provider readiness records through the read-only run-status surface.",
            command=[
                *uv_tau,
                "run-status",
                str(provider_readiness_status["run_dir"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
            expected_min_provider_session_states=2,
        ),
        Check(
            check_id="medium.provider_pane_status",
            level="medium",
            purpose="Tau summarizes provider-pane allocation records through the read-only run-status surface.",
            command=[
                *uv_tau,
                "run-status",
                str(provider_pane_status["run_dir"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
        ),
        Check(
            check_id="medium.provider_dag_status",
            level="medium",
            purpose="Tau summarizes provider DAG visibility, cleanup, and orchestration evidence through the read-only run-status surface.",
            command=[
                *uv_tau,
                "run-status",
                str(provider_dag_status["run_dir"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.dag_stress_poc",
            level="medium",
            purpose="Tau executes simple, retry, fan-out/fan-in, timeout, error, invalid-receipt, wrong-result, and model-missing scheduler rungs.",
            command=[
                *uv_tau,
                "dag-stress-poc",
                "--label",
                "rw-sanity-dag-stress",
                "--run-root",
                str(run_dir / "medium-dag-stress-poc"),
                "--max-attempts",
                "4",
            ],
            timeout_seconds=120,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.dag_stress_status",
            level="medium",
            purpose="Tau summarizes a deterministic DAG stress suite through the read-only run-status surface.",
            command=[
                sys.executable,
                "-c",
                run_status_after_json_command(
                    producer_command=[
                        *uv_tau,
                        "dag-stress-poc",
                        "--label",
                        "rw-sanity-dag-stress-status",
                        "--run-root",
                        str(run_dir / "medium-dag-stress-status"),
                        "--max-attempts",
                        "4",
                    ],
                    status_command_prefix=[*uv_tau, "run-status"],
                    run_dir_key="run_dir",
                ),
            ],
            timeout_seconds=180,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.dag_stress_campaign",
            level="medium",
            purpose="Tau repeats the DAG stress suite across retry budgets for stability.",
            command=[
                *uv_tau,
                "dag-stress-campaign",
                "--label",
                "rw-sanity-dag-campaign",
                "--run-root",
                str(run_dir / "medium-dag-stress-campaign"),
                "--max-budget",
                "3",
                "--repetitions",
                "2",
            ],
            timeout_seconds=180,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.dag_stress_campaign_status",
            level="medium",
            purpose="Tau summarizes a deterministic DAG stress campaign through the read-only run-status surface.",
            command=[
                sys.executable,
                "-c",
                run_status_after_json_command(
                    producer_command=[
                        *uv_tau,
                        "dag-stress-campaign",
                        "--label",
                        "rw-sanity-dag-campaign-status",
                        "--run-root",
                        str(run_dir / "medium-dag-stress-campaign-status"),
                        "--max-budget",
                        "3",
                        "--repetitions",
                        "2",
                    ],
                    status_command_prefix=[*uv_tau, "run-status"],
                    run_dir_key="campaign_dir",
                ),
            ],
            timeout_seconds=240,
            expected_status="PASS",
        ),
        Check(
            check_id="medium.proof_index_build",
            level="medium",
            purpose=(
                "Tau builds a machine-readable proof index over a dedicated fixture source "
                "inside the current sanity run directory and writes a proof-index build receipt."
            ),
            command=[
                *uv_tau,
                "proof-index",
                "build",
                str(proof_index["source_dir"]),
                "--out",
                str(proof_index["output"]),
                "--receipt",
                str(proof_index["receipt"]),
            ],
            timeout_seconds=120,
            expected_status="PASS",
            output_receipt=proof_index["receipt"],
        ),
        Check(
            check_id="medium.project_dag_reviewer_repair_loop",
            level="medium",
            purpose=(
                "Tau runs a DAG-level creator-reviewer repair loop where reviewer returns "
                "REVISE once, creator reruns, and reviewer then returns PASS."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_medium["contract"]),
                "--receipt-dir",
                str(project_dag_medium["run_dir"]),
                "--agents-root",
                str(project_dag_medium["agents_root"]),
            ],
            timeout_seconds=120,
            expected_status="PASS",
            expected_verdict="PASS",
        ),
        Check(
            check_id="medium.project_dag_ready_queue_parallel_join",
            level="medium",
            purpose=(
                "Tau runs a bounded ready-queue project DAG with concurrent researcher/coder "
                "branches and a reviewer join against the immutable goal."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_concurrent["contract"]),
                "--receipt-dir",
                str(project_dag_concurrent["run_dir"]),
                "--agents-root",
                str(project_dag_concurrent["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=120,
            expected_status="PASS",
            expected_verdict="PASS",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_timeout_retry_recovery",
            level="advanced",
            purpose=(
                "Tau retries a timed-out concurrent ready-queue DAG node, preserves the failed "
                "attempt evidence, and continues to the reviewer join after the retry passes."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_concurrent_timeout_retry["contract"]),
                "--receipt-dir",
                str(project_dag_concurrent_timeout_retry["run_dir"]),
                "--agents-root",
                str(project_dag_concurrent_timeout_retry["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=120,
            expected_status="PASS",
            expected_verdict="PASS",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_non_json_retry_recovery",
            level="advanced",
            purpose=(
                "Tau retries a concurrent ready-queue DAG node that emits non-JSON once, then "
                "continues to the reviewer join after a valid retry response."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_concurrent_non_json_retry["contract"]),
                "--receipt-dir",
                str(project_dag_concurrent_non_json_retry["run_dir"]),
                "--agents-root",
                str(project_dag_concurrent_non_json_retry["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=120,
            expected_status="PASS",
            expected_verdict="PASS",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_max_retries_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a concurrent ready-queue DAG node after repeated non-JSON "
                "responses exhaust node max_attempts."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_concurrent_max_retries["contract"]),
                "--receipt-dir",
                str(project_dag_concurrent_max_retries["run_dir"]),
                "--agents-root",
                str(project_dag_concurrent_max_retries["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=120,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="INVALID_COMMAND_JSON",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_pointless_test_drift_course_correction",
            level="advanced",
            purpose=(
                "Tau blocks a ready-queue subagent that emits test-only churn instead of "
                "task evidence and writes a course-correction artifact."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_concurrent_pointless_test_drift["contract"]),
                "--receipt-dir",
                str(project_dag_concurrent_pointless_test_drift["run_dir"]),
                "--agents-root",
                str(project_dag_concurrent_pointless_test_drift["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=120,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="POINTLESS_UNIT_TEST_DRIFT",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_brave_required_after_two_attempts",
            level="advanced",
            purpose=(
                "Tau stops normal retry after two failed ready-queue attempts and requires "
                "$brave-search before a third attempt can proceed."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_concurrent_brave_required["contract"]),
                "--receipt-dir",
                str(project_dag_concurrent_brave_required["run_dir"]),
                "--agents-root",
                str(project_dag_concurrent_brave_required["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=120,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="BRAVE_SEARCH_REQUIRED_AFTER_TWO_ATTEMPTS",
        ),
        Check(
            check_id="advanced.project_dag_reviewer_goal_drift_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a project-agent DAG when the reviewer verdict cites a goal hash "
                "that does not match the immutable DAG goal."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_complex["contract"]),
                "--receipt-dir",
                str(project_dag_complex["run_dir"]),
                "--agents-root",
                str(project_dag_complex["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="REVIEWER_GOAL_HASH_MISMATCH",
        ),
        Check(
            check_id="advanced.project_dag_timeout_fail_closed",
            level="advanced",
            purpose="Tau blocks a project-agent DAG when a selected node command times out.",
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_timeout["contract"]),
                "--receipt-dir",
                str(project_dag_timeout["run_dir"]),
                "--agents-root",
                str(project_dag_timeout["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="COMMAND_TIMEOUT",
        ),
        Check(
            check_id="advanced.project_dag_non_json_fail_closed",
            level="advanced",
            purpose="Tau blocks a project-agent DAG when a selected node emits non-JSON stdout.",
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_non_json["contract"]),
                "--receipt-dir",
                str(project_dag_non_json["run_dir"]),
                "--agents-root",
                str(project_dag_non_json["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="INVALID_COMMAND_JSON",
        ),
        Check(
            check_id="advanced.project_dag_max_steps_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a project-agent DAG when a reviewer keeps routing back and "
                "the DAG max_total_attempts budget is exhausted."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_max_steps["contract"]),
                "--receipt-dir",
                str(project_dag_max_steps["run_dir"]),
                "--agents-root",
                str(project_dag_max_steps["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="MAX_STEPS_EXHAUSTED",
        ),
        Check(
            check_id="advanced.project_dag_bad_contract_course_correction",
            level="advanced",
            purpose=(
                "Tau rejects a malformed project-agent DAG before dispatch and returns a "
                "project-agent-readable tau.dag_error.v1 course-correction payload."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_bad_contract["contract"]),
                "--receipt-dir",
                str(project_dag_bad_contract["run_dir"]),
                "--agents-root",
                str(project_dag_bad_contract["agents_root"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="DAG_CONTRACT_INVALID",
        ),
        Check(
            check_id="advanced.project_dag_evidence_manifest_goal_hash_fail_closed",
            level="advanced",
            purpose=(
                "Tau rejects a project DAG before dispatch when its typed evidence manifest "
                "contains an artifact with a goal hash that differs from the immutable DAG goal."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_evidence_manifest_goal_drift["contract"]),
                "--receipt-dir",
                str(project_dag_evidence_manifest_goal_drift["run_dir"]),
                "--agents-root",
                str(project_dag_evidence_manifest_goal_drift["agents_root"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="EVIDENCE_MANIFEST_INVALID",
        ),
        Check(
            check_id="advanced.evidence_manifest_validates_clean_artifact",
            level="advanced",
            purpose=(
                "Tau validates a typed evidence manifest whose artifact hash, schema, "
                "validator namespace, and goal hash all match."
            ),
            command=[
                *uv_tau,
                "evidence-validate",
                str(evidence_manifest_valid["manifest"]),
                "--receipt",
                str(evidence_manifest_valid["receipt"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
            output_receipt=evidence_manifest_valid["receipt"],
        ),
        Check(
            check_id="advanced.project_dag_memory_evidence_gate_valid_dispatches",
            level="advanced",
            purpose=(
                "Tau accepts valid Memory intent plus separate evidence case "
                "and still dispatches the project DAG to coder and reviewer."
            ),
            command=[
                *uv_tau,
                "run",
                str(project_dag_memory_evidence_valid["contract"]),
                "--receipt-dir",
                str(project_dag_memory_evidence_valid["run_dir"]),
                "--agents-root",
                str(project_dag_memory_evidence_valid["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(0,),
            expected_status="PASS",
            expected_verdict="PASS",
            output_receipt=project_dag_memory_evidence_valid["run_dir"] / "dag-receipt.json",
        ),
        Check(
            check_id="advanced.project_dag_memory_evidence_gate_inline_evidence_fail_closed",
            level="advanced",
            purpose=(
                "Tau rejects a project DAG before dispatch when Memory intent "
                "contains inline evidence instead of a separate evidence case."
            ),
            command=[
                *uv_tau,
                "run",
                str(project_dag_memory_evidence_inline["contract"]),
                "--receipt-dir",
                str(project_dag_memory_evidence_inline["run_dir"]),
                "--agents-root",
                str(project_dag_memory_evidence_inline["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="INLINE_MEMORY_EVIDENCE_REJECTED",
            output_receipt=project_dag_memory_evidence_inline["run_dir"] / "dag-receipt.json",
        ),
        Check(
            check_id="advanced.project_dag_memory_evidence_gate_clarify_route_fail_closed",
            level="advanced",
            purpose=(
                "Tau rejects a project DAG before dispatch when Memory routes "
                "to CLARIFY instead of an actionable execution route."
            ),
            command=[
                *uv_tau,
                "run",
                str(project_dag_memory_evidence_clarify["contract"]),
                "--receipt-dir",
                str(project_dag_memory_evidence_clarify["run_dir"]),
                "--agents-root",
                str(project_dag_memory_evidence_clarify["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="MEMORY_ROUTE_NOT_DISPATCHABLE",
            output_receipt=project_dag_memory_evidence_clarify["run_dir"] / "dag-receipt.json",
        ),
        Check(
            check_id="advanced.project_dag_memory_evidence_gate_missing_case_hash_fail_closed",
            level="advanced",
            purpose=(
                "Tau rejects a project DAG before dispatch when the separate "
                "evidence case lacks a stable case hash."
            ),
            command=[
                *uv_tau,
                "run",
                str(project_dag_memory_evidence_missing_hash["contract"]),
                "--receipt-dir",
                str(project_dag_memory_evidence_missing_hash["run_dir"]),
                "--agents-root",
                str(project_dag_memory_evidence_missing_hash["agents_root"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="MISSING_EVIDENCE_CASE_HASH",
            output_receipt=project_dag_memory_evidence_missing_hash["run_dir"] / "dag-receipt.json",
        ),
        Check(
            check_id="advanced.project_dag_command_policy_network_fail_closed",
            level="advanced",
            purpose=(
                "Tau returns a project-agent-readable command_policy_rejected DAG error "
                "when a command spec declares network use without policy approval."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_command_policy_network["contract"]),
                "--receipt-dir",
                str(project_dag_command_policy_network["run_dir"]),
                "--agents-root",
                str(project_dag_command_policy_network["agents_root"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="COMMAND_POLICY_REJECTED",
        ),
        Check(
            check_id="advanced.project_dag_command_policy_allows_local_spec",
            level="advanced",
            purpose=(
                "Tau runs a project DAG through a command-spec trust policy when the "
                "local command root and cwd are allowed."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_command_policy_allowed["contract"]),
                "--receipt-dir",
                str(project_dag_command_policy_allowed["run_dir"]),
                "--agents-root",
                str(project_dag_command_policy_allowed["agents_root"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
        ),
        Check(
            check_id="advanced.project_dag_command_policy_mutation_fail_closed",
            level="advanced",
            purpose=(
                "Tau returns a project-agent-readable command_policy_rejected DAG error "
                "when a command spec declares mutation without policy approval."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_command_policy_mutation["contract"]),
                "--receipt-dir",
                str(project_dag_command_policy_mutation["run_dir"]),
                "--agents-root",
                str(project_dag_command_policy_mutation["agents_root"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="COMMAND_POLICY_REJECTED",
        ),
        Check(
            check_id="advanced.project_dag_provider_metadata_propagates",
            level="advanced",
            purpose=(
                "Tau propagates provider-sensitive node model_policy and prompt_contract "
                "into the dispatched node handoff so the node can emit provider_route_receipt."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_provider_metadata["contract"]),
                "--receipt-dir",
                str(project_dag_provider_metadata["run_dir"]),
                "--agents-root",
                str(project_dag_provider_metadata["agents_root"]),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            expected_verdict="PASS",
            output_receipt=project_dag_provider_metadata["run_dir"] / "dag-receipt.json",
        ),
        Check(
            check_id="advanced.project_dag_itar_access_gate_missing_fail_closed",
            level="advanced",
            purpose=(
                "Tau rejects an ITAR-classified project DAG before dispatch when the "
                "actor/access preflight receipt is missing."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_containment_missing_itar["contract"]),
                "--receipt-dir",
                str(project_dag_containment_missing_itar["run_dir"]),
                "--agents-root",
                str(project_dag_containment_missing_itar["agents_root"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="MISSING_ITAR_ACCESS_PREFLIGHT",
            output_receipt=project_dag_containment_missing_itar["run_dir"] / "dag-receipt.json",
        ),
        Check(
            check_id="advanced.project_dag_containment_gates_pass",
            level="advanced",
            purpose=(
                "Tau dispatches a project DAG only after PASS ITAR actor/access, "
                "research-query safety, sandbox, and compliance-package validation receipts "
                "are referenced by the contract."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_containment_all_gates["contract"]),
                "--receipt-dir",
                str(project_dag_containment_all_gates["run_dir"]),
                "--agents-root",
                str(project_dag_containment_all_gates["agents_root"]),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            expected_verdict="PASS",
            output_receipt=project_dag_containment_all_gates["run_dir"] / "dag-receipt.json",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_cycle_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a bounded ready-queue DAG whose declared graph contains a cycle "
                "before dispatching node commands."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_cycle["contract"]),
                "--receipt-dir",
                str(project_dag_cycle["run_dir"]),
                "--agents-root",
                str(project_dag_cycle["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="CYCLE_DETECTED",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_mutating_branch_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a bounded ready-queue DAG that marks a branch as mutating before "
                "branch locks and mutation policy are present."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_mutating["contract"]),
                "--receipt-dir",
                str(project_dag_mutating["run_dir"]),
                "--agents-root",
                str(project_dag_mutating["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="MUTATING_NODE_NOT_ALLOWED",
        ),
        Check(
            check_id="advanced.project_dag_ready_queue_provider_policy_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks provider/non-local ready-queue branches until provider concurrency "
                "and branch-lock policy are explicitly supported."
            ),
            command=[
                *uv_tau,
                "dag-run",
                str(project_dag_provider_policy["contract"]),
                "--receipt-dir",
                str(project_dag_provider_policy["run_dir"]),
                "--agents-root",
                str(project_dag_provider_policy["agents_root"]),
                "--scheduler",
                "bounded-ready-queue",
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="NON_LOCAL_READY_QUEUE_NODE_NOT_ALLOWED",
        ),
        Check(
            check_id="advanced.dag_expansion_apply_tampered_preview_fail_closed",
            level="advanced",
            purpose=(
                "Tau validates an adaptive DAG expansion, records policy, then refuses "
                "to apply a tampered preview whose sha256 no longer matches validation."
            ),
            command=[
                sys.executable,
                "-c",
                dag_expansion_tamper_apply_command(
                    uv_tau=uv_tau,
                    contract=dag_expansion_tamper["contract"],
                    proposal=dag_expansion_tamper["proposal"],
                    validation_receipt=dag_expansion_tamper["validation_receipt"],
                    policy_receipt=dag_expansion_tamper["policy_receipt"],
                    apply_receipt=dag_expansion_tamper["apply_receipt"],
                    preview=dag_expansion_tamper["preview"],
                    out=dag_expansion_tamper["out"],
                ),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="PREVIEW_HASH_MISMATCH",
            output_receipt=dag_expansion_tamper["apply_receipt"],
        ),
        Check(
            check_id="advanced.dag_expansion_apply_tampered_source_fail_closed",
            level="advanced",
            purpose=(
                "Tau validates an adaptive DAG expansion, records policy, then refuses "
                "to apply when the source DAG contract no longer matches validation."
            ),
            command=[
                sys.executable,
                "-c",
                dag_expansion_source_tamper_apply_command(
                    uv_tau=uv_tau,
                    contract=dag_expansion_source_tamper["contract"],
                    proposal=dag_expansion_source_tamper["proposal"],
                    validation_receipt=dag_expansion_source_tamper["validation_receipt"],
                    policy_receipt=dag_expansion_source_tamper["policy_receipt"],
                    apply_receipt=dag_expansion_source_tamper["apply_receipt"],
                    out=dag_expansion_source_tamper["out"],
                ),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="SOURCE_DAG_CONTRACT_HASH_MISMATCH",
            output_receipt=dag_expansion_source_tamper["apply_receipt"],
        ),
        Check(
            check_id="advanced.dag_branch_locks_validate_pass",
            level="advanced",
            purpose=(
                "Tau validates provider and mutating DAG branch locks with approval "
                "packet hash binding before side-effecting branches are schedulable."
            ),
            command=[
                *uv_tau,
                "dag-branch-locks-validate",
                "--dag-contract",
                str(dag_branch_locks["contract"]),
                "--locks",
                str(dag_branch_locks["valid_locks"]),
                "--receipt",
                str(dag_branch_locks["valid_receipt"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
            output_receipt=dag_branch_locks["valid_receipt"],
        ),
        Check(
            check_id="advanced.dag_branch_locks_missing_workspace_lease_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks provider branch scheduling when branch locks omit the "
                "required Herdr workspace lease reference."
            ),
            command=[
                *uv_tau,
                "dag-branch-locks-validate",
                "--dag-contract",
                str(dag_branch_locks["contract"]),
                "--locks",
                str(dag_branch_locks["missing_workspace_lease_locks"]),
                "--receipt",
                str(dag_branch_locks["missing_workspace_lease_receipt"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="MISSING_WORKSPACE_LEASE",
            output_receipt=dag_branch_locks["missing_workspace_lease_receipt"],
        ),
        Check(
            check_id="advanced.dag_route_memory_apply_requires_approval",
            level="advanced",
            purpose=(
                "Tau projects route-memory candidates locally, then blocks Memory "
                "sync apply when no memory_upsert approval receipt is supplied."
            ),
            command=[
                sys.executable,
                "-c",
                route_memory_apply_without_approval_command(
                    uv_tau=uv_tau,
                    signal=route_memory_apply["signal"],
                    candidate_receipt=route_memory_apply["candidate_receipt"],
                    sync_receipt=route_memory_apply["sync_receipt"],
                ),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="MISSING_APPROVAL_RECEIPT",
            output_receipt=route_memory_apply["sync_receipt"],
        ),
        Check(
            check_id="advanced.dag_route_memory_apply_approval_target_fail_closed",
            level="advanced",
            purpose=(
                "Tau projects route-memory candidates locally, then blocks Memory "
                "sync apply when the memory_upsert approval targets a different DAG."
            ),
            command=[
                sys.executable,
                "-c",
                route_memory_apply_wrong_approval_target_command(
                    uv_tau=uv_tau,
                    signal=route_memory_apply["signal"],
                    candidate_receipt=route_memory_apply["approval_mismatch_candidate_receipt"],
                    sync_receipt=route_memory_apply["approval_mismatch_sync_receipt"],
                    approval_receipt=route_memory_apply["approval_mismatch_receipt"],
                ),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="APPROVAL_TARGET_MISMATCH",
            output_receipt=route_memory_apply["approval_mismatch_sync_receipt"],
        ),
        Check(
            check_id="advanced.dag_route_memory_apply_with_approval_syncs",
            level="advanced",
            purpose=(
                "Tau projects route-memory candidates locally, then performs an "
                "approved Memory /upsert against a local HTTP endpoint."
            ),
            command=[
                sys.executable,
                "-c",
                route_memory_apply_with_approval_command(
                    uv_tau=uv_tau,
                    signal=route_memory_apply["signal"],
                    candidate_receipt=route_memory_apply["approved_candidate_receipt"],
                    sync_receipt=route_memory_apply["approved_sync_receipt"],
                    approval_receipt=route_memory_apply["approved_receipt"],
                    memory_requests=route_memory_apply["approved_memory_requests"],
                ),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            output_receipt=route_memory_apply["approved_sync_receipt"],
        ),
        Check(
            check_id="advanced.dag_route_memory_dry_run_projects_documents",
            level="advanced",
            purpose=(
                "Tau projects clean route-memory candidates into Memory document shape "
                "without writing to Memory."
            ),
            command=[
                sys.executable,
                "-c",
                route_memory_dry_run_command(
                    uv_tau=uv_tau,
                    signal=route_memory_apply["signal"],
                    candidate_receipt=route_memory_apply["dry_run_candidate_receipt"],
                    sync_receipt=route_memory_apply["dry_run_sync_receipt"],
                ),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            output_receipt=route_memory_apply["dry_run_sync_receipt"],
        ),
        Check(
            check_id="advanced.research_source_arxiv_packet_passes",
            level="advanced",
            purpose=(
                "Tau accepts a source-bearing ArXiv research packet as review-required "
                "design input without treating it as closure proof."
            ),
            command=[
                *uv_tau,
                "research-source-receipt",
                "--source",
                str(research_source["valid_source"]),
                "--receipt",
                str(research_source["valid_receipt"]),
            ],
            timeout_seconds=60,
            expected_status="PASS",
            output_receipt=research_source["valid_receipt"],
        ),
        Check(
            check_id="advanced.research_source_arxiv_metadata_fail_closed",
            level="advanced",
            purpose=(
                "Tau rejects a packet that claims method:arxiv but cites a generic "
                "non-ArXiv source without an arxiv_id."
            ),
            command=[
                *uv_tau,
                "research-source-receipt",
                "--source",
                str(research_source["invalid_source"]),
                "--receipt",
                str(research_source["invalid_receipt"]),
            ],
            timeout_seconds=60,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            output_receipt=research_source["invalid_receipt"],
        ),
        Check(
            check_id="advanced.github_apply_policy_missing_gates_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a GitHub apply projection when policy-required "
                "approval, preflight, and redaction gates are missing."
            ),
            command=[
                *uv_tau,
                "github-apply-policy-check",
                "--projection",
                str(github_apply_policy["projection"]),
                "--policy",
                str(github_apply_policy["policy"]),
                "--receipt",
                str(github_apply_policy["receipt"]),
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            output_receipt=github_apply_policy["receipt"],
        ),
        Check(
            check_id="advanced.github_apply_policy_all_gates_pass",
            level="advanced",
            purpose=(
                "Tau allows a GitHub apply projection to pass local policy only when "
                "approval, preflight, and redaction evidence are all supplied."
            ),
            command=[
                *uv_tau,
                "github-apply-policy-check",
                "--projection",
                str(github_apply_policy["positive_projection"]),
                "--policy",
                str(github_apply_policy["positive_policy"]),
                "--receipt",
                str(github_apply_policy["positive_receipt"]),
                "--approval-receipt",
                str(github_apply_policy["positive_approval"]),
                "--redaction-receipt",
                str(github_apply_policy["positive_redaction"]),
                "--preflight-ready",
            ],
            timeout_seconds=90,
            expected_status="PASS",
            output_receipt=github_apply_policy["positive_receipt"],
        ),
        Check(
            check_id="advanced.github_apply_policy_redaction_hash_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a GitHub apply projection when the redacted projection "
                "artifact no longer matches the passing redaction receipt hash."
            ),
            command=[
                *uv_tau,
                "github-apply-policy-check",
                "--projection",
                str(github_apply_policy["tamper_projection"]),
                "--policy",
                str(github_apply_policy["tamper_policy"]),
                "--receipt",
                str(github_apply_policy["tamper_receipt"]),
                "--approval-receipt",
                str(github_apply_policy["tamper_approval"]),
                "--redaction-receipt",
                str(github_apply_policy["tamper_redaction"]),
                "--preflight-ready",
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            output_receipt=github_apply_policy["tamper_receipt"],
        ),
        Check(
            check_id="advanced.github_apply_policy_approval_target_fail_closed",
            level="advanced",
            purpose=(
                "Tau blocks a GitHub apply projection when the approval receipt "
                "target does not match the projection repo and issue target."
            ),
            command=[
                *uv_tau,
                "github-apply-policy-check",
                "--projection",
                str(github_apply_policy["approval_mismatch_projection"]),
                "--policy",
                str(github_apply_policy["approval_mismatch_policy"]),
                "--receipt",
                str(github_apply_policy["approval_mismatch_receipt"]),
                "--approval-receipt",
                str(github_apply_policy["approval_mismatch_approval"]),
                "--redaction-receipt",
                str(github_apply_policy["approval_mismatch_redaction"]),
                "--preflight-ready",
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            output_receipt=github_apply_policy["approval_mismatch_receipt"],
        ),
        Check(
            check_id="advanced.github_handoff_transport_apply_requires_policy_receipt",
            level="advanced",
            purpose=(
                "Tau blocks handoff-github-transport --apply before running gh commands "
                "when no PASS GitHub apply-policy receipt is supplied."
            ),
            command=[
                *uv_tau,
                "handoff-github-transport",
                str(github_apply_policy["handoff"]),
                "--active-goal-hash",
                str(github_apply_policy["handoff_goal_hash"]),
                "--receipt",
                str(github_apply_policy["transport_missing_policy_receipt"]),
                "--apply",
            ],
            timeout_seconds=90,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            output_receipt=github_apply_policy["transport_missing_policy_receipt"],
        ),
        Check(
            check_id="advanced.zero_trust_redteam_itar_containment",
            level="advanced",
            purpose=(
                "Tau runs the deterministic containment red-team suite and requires every "
                "ITAR/exfiltration/Docker/public-mutation attack fixture to fail closed."
            ),
            command=[
                *uv_tau,
                "zero-trust-redteam",
                "--run-dir",
                str(run_dir / "zero-trust-redteam-itar-containment"),
            ],
            timeout_seconds=90,
            expected_status="PASS",
            output_receipt=(
                run_dir
                / "zero-trust-redteam-itar-containment"
                / "zero-trust-redteam-receipt.json"
            ),
        ),
        Check(
            check_id="advanced.itar_grade_containment_demo",
            level="advanced",
            purpose=(
                "Tau runs the copyable ITAR-grade containment demo that blocks unsafe "
                "research, actor access, and Docker policy paths before accepting local-only receipts."
            ),
            command=[
                str(repo / "examples" / "itar-grade-containment" / "run.sh"),
                str(run_dir / "itar-grade-containment-demo"),
            ],
            timeout_seconds=120,
            expected_status="PASS",
            output_receipt=run_dir / "itar-grade-containment-demo" / "demo-receipt.json",
        ),
        Check(
            check_id="advanced.provider_readiness",
            level="advanced",
            purpose="Herdr allocates visible Codex and OpenCode provider panes and Tau records structured readiness.",
            command=[
                *uv_tau,
                "provider-readiness-poc",
                "--label",
                "rw-sanity-provider-readiness",
                "--run-root",
                str(provider_root / "readiness"),
                "--herdr-bin",
                herdr_bin,
                "--no-install-integrations",
            ],
            timeout_seconds=240,
            expected_status="PASS",
            expected_min_provider_session_states=2,
            attempts=2,
            post_cleanup_mode=provider_cleanup_mode,
            post_cleanup_uv_bin=uv_bin,
            post_cleanup_herdr_bin=herdr_bin,
        ),
        Check(
            check_id="advanced.provider_dag_one_pass",
            level="advanced",
            purpose="Live visible Codex coder and OpenCode reviewer complete a one-pass scratch DAG.",
            command=[
                *uv_tau,
                "provider-dag-poc",
                "--label",
                "rw-sanity-provider-dag-one-pass",
                "--run-root",
                str(provider_root / "one-pass"),
                "--max-attempts",
                "1",
                "--receipt-timeout-seconds",
                str(live_provider_receipt_timeout_seconds),
                "--herdr-bin",
                herdr_bin,
                "--no-install-integrations",
                "--cleanup-mode",
                provider_cleanup_mode,
            ],
            timeout_seconds=live_provider_receipt_timeout_seconds + 180,
            expected_status="PASS",
        ),
        Check(
            check_id="advanced.generic_provider_dag_adapter",
            level="advanced",
            purpose="Generic DAG executes a provider-backed adapter node and carries provider_live evidence.",
            command=[
                *uv_tau,
                "dag-run",
                str(generic_provider_adapter_spec),
                "--no-resume",
            ],
            timeout_seconds=live_provider_receipt_timeout_seconds + 240,
            expected_status="PASS",
            expected_provider_live=True,
        ),
        Check(
            check_id="advanced.generic_provider_dag_adapter_resume",
            level="advanced",
            purpose=(
                "Generic DAG resumes an already-completed live provider-backed adapter node "
                "without launching the provider adapter a second time."
            ),
            command=[
                sys.executable,
                "-c",
                rerun_json_command_with_resume(
                    first_command=[
                        *uv_tau,
                        "dag-run",
                        str(generic_provider_adapter_spec),
                        "--no-resume",
                    ],
                    second_command=[
                        *uv_tau,
                        "dag-run",
                        str(generic_provider_adapter_spec),
                    ],
                ),
            ],
            timeout_seconds=(live_provider_receipt_timeout_seconds * 2) + 300,
            expected_status="PASS",
            expected_provider_live=True,
            expected_min_resumed_nodes=1,
            attempts=2,
        ),
        Check(
            check_id="advanced.provider_dag_repair_loop",
            level="advanced",
            purpose=(
                "Visible provider DAG handles reviewer REVISE, retries a visible deterministic "
                "coder, then accepts reviewer PASS."
            ),
            command=[
                *uv_tau,
                "provider-dag-poc",
                "--label",
                "rw-sanity-provider-dag-repair",
                "--run-root",
                str(provider_root / "repair-loop"),
                "--max-attempts",
                "2",
                "--receipt-timeout-seconds",
                str(live_provider_receipt_timeout_seconds),
                "--force-reviewer-revise-first",
                "--coder-mode",
                "deterministic-visible",
                "--herdr-bin",
                herdr_bin,
                "--no-install-integrations",
                "--cleanup-mode",
                provider_cleanup_mode,
            ],
            timeout_seconds=(live_provider_receipt_timeout_seconds * 2) + 180,
            expected_status="PASS",
        ),
        Check(
            check_id="advanced.provider_dag_max_attempts_fail_closed",
            level="advanced",
            purpose="Live provider DAG blocks when reviewer revisions exhaust max attempts.",
            command=[
                *uv_tau,
                "provider-dag-poc",
                "--label",
                "rw-sanity-provider-dag-max-exhaustion",
                "--run-root",
                str(provider_root / "max-exhaustion"),
                "--max-attempts",
                "2",
                "--receipt-timeout-seconds",
                str(live_provider_receipt_timeout_seconds),
                "--force-reviewer-revise-attempts",
                "1,2",
                "--allow-final-forced-revise",
                "--coder-mode",
                "deterministic-visible",
                "--herdr-bin",
                herdr_bin,
                "--no-install-integrations",
                "--cleanup-mode",
                provider_cleanup_mode,
            ],
            timeout_seconds=(live_provider_receipt_timeout_seconds * 2) + 180,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="REVISE",
        ),
        Check(
            check_id="advanced.provider_dag_invalid_model_fail_closed",
            level="advanced",
            purpose="Live provider DAG blocks and preserves artifacts when the reviewer model does not exist.",
            command=[
                *uv_tau,
                "provider-dag-poc",
                "--label",
                "rw-sanity-provider-dag-invalid-model",
                "--run-root",
                str(provider_root / "invalid-model"),
                "--max-attempts",
                "1",
                "--receipt-timeout-seconds",
                "45",
                "--reviewer-model",
                "openai/not-a-real-model-20260703",
                "--coder-mode",
                "deterministic-visible",
                "--herdr-bin",
                herdr_bin,
                "--no-install-integrations",
                "--cleanup-mode",
                provider_cleanup_mode,
            ],
            timeout_seconds=180,
            expected_exit_codes=(1,),
            expected_status="BLOCKED",
            expected_verdict="REVIEWER_SEND_FAILED",
        ),
        Check(
            check_id="advanced.browser_cdp_proof",
            level="advanced",
            purpose=(
                "Tau opens a local proof page through Surf browser transport, observes "
                "required Tau proof text, and records a screenshot artifact."
            ),
            command=[
                *uv_tau,
                "browser-cdp-proof",
                "--out-dir",
                str(run_dir / "browser-cdp-proof"),
                "--run-id",
                f"{run_dir.name}-browser-cdp-proof",
            ],
            timeout_seconds=90,
            expected_status="PASS",
            expected_verdict="PASS",
            expected_provider_live=False,
        ),
    ]


def create_generic_dag_fixture(run_dir: Path) -> Path:
    fixture_dir = run_dir / "medium-generic-dag"
    receipts = fixture_dir / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    nodes = []
    for node_id, depends_on in (
        ("planner", []),
        ("coder", ["planner"]),
        ("reviewer", ["coder"]),
    ):
        receipt_path = receipts / f"{node_id}.json"
        nodes.append(
            {
                "node_id": node_id,
                "role": node_id,
                "depends_on": depends_on,
                "command": [
                    "python3",
                    "-c",
                    generic_dag_receipt_writer(receipt_path, node_id=node_id),
                ],
                "receipt_path": str(receipt_path),
                "timeout_seconds": 30,
                "max_attempts": 1,
            }
        )
    spec_path = fixture_dir / "dag-spec.json"
    write_json(
        spec_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "rw-sanity-generic-dag",
            "run_dir": str(fixture_dir),
            "events_jsonl": str(fixture_dir / "events.jsonl"),
            "nodes": nodes,
        },
    )
    return spec_path


def create_project_dag_fixture(
    run_dir: Path,
    *,
    scenario: str,
    goal_hash: str,
    contract_format: str = "json",
) -> dict[str, Path]:
    fixture_dir = run_dir / f"{scenario}-project-dag"
    command_spec_root = fixture_dir / "command-specs"
    agents_root = fixture_dir / "agents"
    run_output_dir = fixture_dir / "run"
    agents_root.mkdir(parents=True, exist_ok=True)
    worker = fixture_dir / "project_dag_worker.py"
    write_text(worker, project_dag_worker_script())
    concurrent_scenarios = {
        "concurrent",
        "concurrent-timeout-retry",
        "concurrent-non-json-retry",
        "concurrent-max-retries",
        "concurrent-pointless-test-drift",
        "concurrent-brave-required",
        "ready-queue-cycle",
        "ready-queue-mutating",
        "ready-queue-provider-policy",
    }
    agents = (
        ("research-auditor", "coder", "reviewer")
        if scenario in concurrent_scenarios
        else ("coder", "reviewer")
    )
    for agent in agents:
        if scenario == "timeout" and agent == "coder":
            command = ["python3", "-c", "import time; time.sleep(5)"]
            timeout_s = 0.1
        elif scenario == "concurrent-timeout-retry" and agent == "coder":
            state = command_spec_root / agent / "attempt-count.txt"
            command = [
                "python3",
                "-c",
                _flaky_project_dag_command(
                    worker=worker,
                    role=agent,
                    scenario=scenario,
                    state=state,
                    first_failure="timeout",
                ),
            ]
            timeout_s = 1
        elif scenario == "concurrent-non-json-retry" and agent == "coder":
            state = command_spec_root / agent / "attempt-count.txt"
            command = [
                "python3",
                "-c",
                _flaky_project_dag_command(
                    worker=worker,
                    role=agent,
                    scenario=scenario,
                    state=state,
                    first_failure="non-json",
                ),
            ]
            timeout_s = 20
        elif scenario == "concurrent-max-retries" and agent == "coder":
            command = ["python3", "-c", "print('not json')"]
            timeout_s = 20
        elif scenario == "concurrent-pointless-test-drift" and agent == "coder":
            command = [
                "python3",
                "-c",
                (
                    "print('============================= test session starts ============================='); "
                    "print('collected 12 items'); "
                    "print('tests/test_probe.py ....'); "
                    "raise SystemExit(1)"
                ),
            ]
            timeout_s = 20
        elif scenario == "concurrent-brave-required" and agent == "coder":
            command = ["python3", "-c", "print('not json')"]
            timeout_s = 20
        elif scenario == "non-json" and agent == "reviewer":
            command = ["python3", "-c", "print('not json')"]
            timeout_s = 20
        else:
            command = [
                "python3",
                str(worker),
                "--role",
                agent,
                "--scenario",
                scenario,
            ]
            timeout_s = 20
        write_json(
            command_spec_root / agent / "tau-dispatch-command.json",
            {
                "command": command,
                "timeout_s": timeout_s,
            },
        )

    max_attempts = 4 if scenario in {"medium", *concurrent_scenarios} else 3
    if scenario == "max-steps":
        max_attempts = 2
    node_max_attempts = 2 if scenario in {"medium", *concurrent_scenarios} else 1
    if scenario == "max-steps":
        node_max_attempts = 3
    if scenario == "concurrent-brave-required":
        node_max_attempts = 3
    nodes = [
        {
            "id": "coder",
            "agent": "coder",
            "executor": "local",
            "max_attempts": node_max_attempts,
            "command_spec": str(command_spec_root / "coder" / "tau-dispatch-command.json"),
            "required_evidence": ["creator_artifact"],
        },
        {
            "id": "reviewer",
            "agent": "reviewer",
            "executor": "local",
            "max_attempts": node_max_attempts,
            "command_spec": str(command_spec_root / "reviewer" / "tau-dispatch-command.json"),
            "required_evidence": ["reviewer_verdict"],
            "reviewer": {
                "reviews_node": "coder",
                "requires_goal_hash": True,
            },
        },
    ]
    edges = [
        {"from": "coder", "to": "reviewer"},
        {"from": "reviewer", "to": "coder", "condition": "reviewer_requests_revision"},
        {"from": "reviewer", "to": "human", "condition": "reviewer_pass_or_block"},
    ]
    required_evidence = ["creator_artifact", "reviewer_verdict"]
    entry_node = "coder"
    limits = {
        "resume": True,
        "default_timeout_seconds": 20,
        "max_total_attempts": max_attempts,
    }
    if scenario in concurrent_scenarios:
        entry_node = "start"
        limits["max_concurrency"] = 2
        nodes = [
            {
                "id": "start",
                "agent": "goal-guardian",
                "executor": "scheduler",
                "max_attempts": 1,
                "required_evidence": [],
            },
            {
                "id": "research",
                "agent": "research-auditor",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(
                    command_spec_root / "research-auditor" / "tau-dispatch-command.json"
                ),
                "required_evidence": ["source_summary"],
            },
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": node_max_attempts,
                "command_spec": str(command_spec_root / "coder" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(command_spec_root / "reviewer" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
        ]
        edges = [
            {"from": "start", "to": "research"},
            {"from": "start", "to": "coder"},
            {"from": "research", "to": "reviewer"},
            {"from": "coder", "to": "reviewer"},
            {"from": "reviewer", "to": "human", "condition": "reviewer_pass_or_block"},
        ]
        required_evidence = ["source_summary", "creator_artifact", "reviewer_verdict"]
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": f"rw-sanity-project-dag-{scenario}",
        "goal": {
            "goal_id": f"rw-sanity-project-dag-{scenario}",
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": f"scratch-project-dag-{scenario}",
        },
        "entry_node": entry_node,
        "terminal_nodes": ["human"],
        "limits": limits,
        "nodes": nodes,
        "edges": edges,
        "required_evidence": required_evidence,
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
            "malformed_handoff",
            "reviewer_goal_hash_mismatch",
        ],
    }
    if contract_format == "yaml":
        contract_path = fixture_dir / "dag-contract.dag.yml"
        write_text(contract_path, _simple_yaml(contract))
    else:
        contract_path = fixture_dir / "dag-contract.json"
        write_json(contract_path, contract)
    return {
        "contract": contract_path,
        "agents_root": agents_root,
        "run_dir": run_output_dir,
    }


def create_project_dag_bad_contract_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "bad-contract-project-dag"
    agents_root = fixture_dir / "agents"
    run_output_dir = fixture_dir / "run"
    agents_root.mkdir(parents=True, exist_ok=True)
    contract_path = fixture_dir / "dag-contract.json"
    write_json(
        contract_path,
        {
            "schema": "tau.dag_contract.v1",
            "dag_id": "rw-sanity-project-dag-bad-contract",
            "goal": {
                "goal_id": "rw-sanity-project-dag-bad-contract",
                "goal_hash": "sha256:rw-sanity-project-dag-bad-contract",
            },
            "target": {"repo": "grahama1970/tau"},
            "nodes": [],
            "edges": [],
        },
    )
    return {
        "contract": contract_path,
        "agents_root": agents_root,
        "run_dir": run_output_dir,
    }


def create_project_dag_policy_fixture(
    run_dir: Path,
    *,
    scenario: str,
    mutation: str,
) -> dict[str, Path]:
    fixture = create_project_dag_fixture(
        run_dir,
        scenario=scenario,
        goal_hash=f"sha256:rw-sanity-project-dag-{scenario}",
    )
    contract_path = fixture["contract"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if mutation == "cycle":
        contract["edges"].append({"from": "reviewer", "to": "start"})
    elif mutation == "mutating":
        for node in contract["nodes"]:
            if node.get("id") == "coder":
                node["mutates"] = True
    elif mutation == "provider":
        for node in contract["nodes"]:
            if node.get("id") == "coder":
                node["executor"] = "provider"
                node["provider"] = {"adapter": "generic-provider-dag-node"}
    else:  # pragma: no cover - fixture guard.
        raise AssertionError(f"unknown project DAG policy mutation: {mutation}")
    write_json(contract_path, contract)
    return fixture


def create_evidence_manifest_valid_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "evidence-manifest-valid"
    evidence_dir = fixture_dir / "evidence"
    goal_hash = "sha256:rw-sanity-evidence-manifest-valid"
    artifact = write_json(
        evidence_dir / "reviewer-verdict.json",
        {
            "schema": "tau.reviewer_verdict.v1",
            "kind": "reviewer_verdict",
            "goal_hash": goal_hash,
            "reviewed_node_id": "coder",
            "verdict": "PASS",
        },
    )
    manifest = write_json(
        fixture_dir / "evidence-manifest.json",
        {
            "schema": "tau.evidence_manifest.v1",
            "run_id": "rw-sanity-evidence-manifest-valid",
            "dag_id": "rw-sanity-evidence-manifest-valid",
            "goal_hash": goal_hash,
            "items": [
                {
                    "kind": "reviewer_verdict",
                    "path": str(artifact),
                    "sha256": f"sha256:{sha256_file(artifact)}",
                    "schema": "tau.reviewer_verdict.v1",
                    "validator": "tau evidence-validate reviewer-verdict",
                    "valid": True,
                }
            ],
        },
    )
    return {
        "manifest": manifest,
        "artifact": artifact,
        "receipt": fixture_dir / "evidence-validation-receipt.json",
    }


def create_project_dag_evidence_manifest_fixture(run_dir: Path) -> dict[str, Path]:
    fixture = create_project_dag_fixture(
        run_dir,
        scenario="evidence-manifest-goal-drift",
        goal_hash="sha256:rw-sanity-project-dag-evidence-manifest-goal-drift",
    )
    contract_path = fixture["contract"]
    contract = read_json(contract_path)
    fixture_dir = contract_path.parent
    evidence_dir = fixture_dir / "preflight-evidence"
    creator_artifact = write_json(
        evidence_dir / "creator-artifact.json",
        {
            "schema": "tau.creator_artifact.v1",
            "kind": "creator_artifact",
            "goal_hash": contract["goal"]["goal_hash"],
            "summary": "Preflight evidence manifest creator artifact.",
        },
    )
    stale_reviewer_verdict = write_json(
        evidence_dir / "reviewer-verdict.json",
        {
            "schema": "tau.reviewer_verdict.v1",
            "kind": "reviewer_verdict",
            "goal_hash": "sha256:stale-rw-sanity-reviewer-goal",
            "reviewed_node_id": "coder",
            "verdict": "PASS",
        },
    )
    manifest = write_json(
        fixture_dir / "evidence-manifest.json",
        {
            "schema": "tau.evidence_manifest.v1",
            "dag_id": contract["dag_id"],
            "goal_hash": contract["goal"]["goal_hash"],
            "items": [
                {
                    "kind": "creator_artifact",
                    "path": str(creator_artifact),
                    "sha256": f"sha256:{sha256_file(creator_artifact)}",
                    "schema": "tau.creator_artifact.v1",
                    "validator": "tau evidence-validate creator-artifact",
                    "valid": True,
                },
                {
                    "kind": "reviewer_verdict",
                    "path": str(stale_reviewer_verdict),
                    "sha256": f"sha256:{sha256_file(stale_reviewer_verdict)}",
                    "schema": "tau.reviewer_verdict.v1",
                    "validator": "tau evidence-validate reviewer-verdict",
                    "valid": True,
                },
            ],
        },
    )
    contract["evidence_manifest"] = str(manifest)
    write_json(contract_path, contract)
    return fixture


def create_project_dag_memory_evidence_fixture(
    run_dir: Path,
    *,
    scenario: str,
    mutation: str,
) -> dict[str, Path]:
    fixture = create_project_dag_fixture(
        run_dir,
        scenario=scenario,
        goal_hash=f"sha256:rw-sanity-project-dag-{scenario}",
    )
    contract_path = fixture["contract"]
    contract = read_json(contract_path)
    fixture_dir = contract_path.parent
    memory_intent = {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "route": "ANSWER",
        "confidence": 0.91,
        "goal_hash": contract["goal"]["goal_hash"],
        "target": contract["target"],
        "summary": "Memory intent routes the DAG to deterministic local execution.",
    }
    evidence_case = {
        "schema": "tau.evidence_case.v1",
        "case_id": f"{scenario}-case",
        "case_sha256": "sha256:" + ("1" * 64),
        "goal_hash": contract["goal"]["goal_hash"],
        "target": contract["target"],
        "support_artifacts": [],
    }
    if mutation == "valid":
        pass
    elif mutation == "inline_evidence":
        memory_intent["evidence"] = [
            {
                "statement": "Inline evidence must be rejected; use a separate evidence case.",
            }
        ]
    elif mutation == "clarify_route":
        memory_intent["route"] = "CLARIFY"
        memory_intent["summary"] = "Memory needs human clarification before execution."
    elif mutation == "missing_case_hash":
        evidence_case.pop("case_sha256", None)
    else:  # pragma: no cover - fixture guard.
        raise AssertionError(f"unknown project DAG memory/evidence mutation: {mutation}")
    memory_intent_path = write_json(fixture_dir / "memory-intent.json", memory_intent)
    evidence_case_path = write_json(fixture_dir / "evidence-case.json", evidence_case)
    contract["memory_intent"] = str(memory_intent_path)
    contract["evidence_case"] = str(evidence_case_path)
    write_json(contract_path, contract)
    return fixture


def create_project_dag_command_policy_fixture(
    run_dir: Path,
    *,
    scenario: str,
    spec_flag: str | None,
) -> dict[str, Path]:
    fixture = create_project_dag_fixture(
        run_dir,
        scenario=scenario,
        goal_hash=f"sha256:rw-sanity-project-dag-{scenario}",
    )
    contract_path = fixture["contract"]
    contract = read_json(contract_path)
    fixture_dir = contract_path.parent
    policy_path = write_json(
        fixture_dir / "command-policy.json",
        {
            "schema": "tau.command_spec_policy.v1",
            "allowed_command_roots": ["python3"],
            "allowed_cwd_roots": [str(fixture_dir)],
            "allows_network": False,
            "allows_mutation": False,
        },
    )
    coder_spec_path = Path(contract["nodes"][0]["command_spec"])
    for node in contract.get("nodes", []):
        if not isinstance(node, dict):
            continue
        spec_path_text = node.get("command_spec")
        if not isinstance(spec_path_text, str):
            continue
        spec_path = Path(spec_path_text)
        spec = read_json(spec_path)
        spec["cwd"] = str(fixture_dir)
        write_json(spec_path, spec)
    if spec_flag is not None:
        coder_spec = read_json(coder_spec_path)
        coder_spec[spec_flag] = True
        write_json(coder_spec_path, coder_spec)
    contract["command_policy"] = str(policy_path)
    write_json(contract_path, contract)
    return fixture


def create_project_dag_provider_metadata_fixture(run_dir: Path) -> dict[str, Path]:
    fixture = create_project_dag_fixture(
        run_dir,
        scenario="provider-metadata",
        goal_hash="sha256:rw-sanity-project-dag-provider-metadata",
    )
    contract_path = fixture["contract"]
    contract = read_json(contract_path)
    contract["provider_sensitive"] = True
    for node in contract["nodes"]:
        if not isinstance(node, dict) or node.get("executor") == "human":
            continue
        node["model_policy"] = {
            "provider": "scillm",
            "auth": "codex-oauth",
            "model": "gpt-image-2",
        }
        node["prompt_contract"] = {
            "schema": "tau.prompt_contract.v1",
            "system_prompt": "Stay inside the immutable provider metadata sanity goal.",
            "user_template": "Use provider route metadata before claiming PASS.",
        }
        evidence = node.get("required_evidence")
        if isinstance(evidence, list) and "provider_route_receipt" not in evidence:
            evidence.append("provider_route_receipt")
    if "provider_route_receipt" not in contract["required_evidence"]:
        contract["required_evidence"].append("provider_route_receipt")
    write_json(contract_path, contract)
    return fixture


def create_project_dag_containment_gate_fixture(
    run_dir: Path,
    *,
    scenario: str,
    mutation: str,
) -> dict[str, Path]:
    fixture = create_project_dag_fixture(
        run_dir,
        scenario=scenario,
        goal_hash=f"sha256:rw-sanity-project-dag-{scenario}",
    )
    contract_path = fixture["contract"]
    contract = read_json(contract_path)
    fixture_dir = contract_path.parent
    goal_hash = contract["goal"]["goal_hash"]
    contract["data_boundary"] = {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "goal_hash": goal_hash,
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
    }
    if mutation == "missing_itar":
        write_json(contract_path, contract)
        return fixture
    if mutation != "all_gates":  # pragma: no cover - fixture guard.
        raise AssertionError(f"unknown project DAG containment mutation: {mutation}")
    contract["requires_external_research"] = True
    contract["requires_sandbox"] = True
    contract["requires_compliance_package_validation"] = True
    itar_receipt = write_gate_receipt(
        fixture_dir / "itar-access-preflight-receipt.json",
        schema="tau.itar_access_preflight_receipt.v1",
        goal_hash=goal_hash,
    )
    research_receipt = write_gate_receipt(
        fixture_dir / "research-query-safety-receipt.json",
        schema="tau.research_query_safety_receipt.v1",
        goal_hash=goal_hash,
    )
    sandbox_receipt = write_gate_receipt(
        fixture_dir / "sandbox-run-receipt.json",
        schema="tau.sandbox_run_receipt.v1",
        goal_hash=goal_hash,
    )
    package_receipt = write_gate_receipt(
        fixture_dir / "compliance-package-validation-receipt.json",
        schema="tau.compliance_package_validation_receipt.v1",
        goal_hash=goal_hash,
        extra={"review_ready": True, "compliant": "NOT_CLAIMED"},
    )
    contract["itar_access_preflight_receipt"] = str(itar_receipt)
    contract["research_query_safety_receipt"] = str(research_receipt)
    contract["sandbox_run_receipt"] = str(sandbox_receipt)
    contract["compliance_package_validation_receipt"] = str(package_receipt)
    write_json(contract_path, contract)
    return fixture


def create_dag_expansion_fixture(
    run_dir: Path,
    *,
    scenario: str = "dag-expansion-tampered-preview",
) -> dict[str, Path]:
    fixture_dir = run_dir / scenario
    fixture_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "rw-sanity-dag-expansion",
        "goal": {
            "goal_id": "rw-sanity-dag-expansion",
            "goal_version": 1,
            "goal_hash": "sha256:rw-sanity-dag-expansion",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-dag-expansion",
        },
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 3,
        },
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["reviewer_verdict"],
            },
        ],
        "edges": [
            {"from": "coder", "to": "reviewer"},
            {"from": "reviewer", "to": "human"},
        ],
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
        ],
    }
    proposal = {
        "schema": "tau.dag_expansion_proposal.v1",
        "proposal_id": "rw-sanity-expansion-proposal",
        "parent_dag_id": contract["dag_id"],
        "goal_hash": contract["goal"]["goal_hash"],
        "proposed_by": "reviewer",
        "reason": "Add deterministic validator before reviewer continuation.",
        "new_nodes": [
            {
                "id": "validator",
                "agent": "validator",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["validation_receipt"],
            }
        ],
        "new_edges": [
            {"from": "coder", "to": "validator"},
            {"from": "validator", "to": "reviewer"},
        ],
    }
    return {
        "contract": write_json(fixture_dir / "dag-contract.json", contract),
        "proposal": write_json(fixture_dir / "proposal.json", proposal),
        "validation_receipt": fixture_dir / "validation-receipt.json",
        "policy_receipt": fixture_dir / "policy-receipt.json",
        "apply_receipt": fixture_dir / "apply-receipt.json",
        "preview": fixture_dir / "expanded-dag.preview.json",
        "out": fixture_dir / "expanded-dag.json",
    }


def create_dag_branch_locks_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "dag-branch-locks"
    goal_hash = "sha256:rw-sanity-dag-branch-locks"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "rw-sanity-dag-branch-locks",
        "goal": {
            "goal_id": "rw-sanity-dag-branch-locks",
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-dag-branch-locks",
        },
        "entry_node": "provider-node",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 3,
        },
        "nodes": [
            {
                "id": "provider-node",
                "agent": "provider-agent",
                "executor": "provider",
                "max_attempts": 1,
                "required_evidence": ["provider_receipt"],
                "provider": {"adapter": "generic-provider-dag-node"},
            },
            {
                "id": "mutating-node",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": ["mutation_receipt"],
                "mutates": True,
            },
            {
                "id": "human",
                "agent": "human",
                "executor": "human",
            },
        ],
        "edges": [
            {"from": "provider-node", "to": "mutating-node"},
            {"from": "mutating-node", "to": "human"},
        ],
        "required_evidence": ["provider_receipt", "mutation_receipt"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "missing_required_evidence",
            "max_attempts_exceeded",
        ],
    }
    contract_path = write_json(fixture_dir / "dag-contract.json", contract)
    provider_approval = write_json(
        fixture_dir / "provider-approval.json",
        {
            "schema": "tau.human_approval_packet.v1",
            "approved": True,
            "actor": {"id": "human:graham", "auth_method": "manual"},
            "action": "provider_branch_scheduling",
            "target": {"id": "rw-sanity-dag-branch-locks"},
            "reason": "Approve provider branch lock fixture.",
            "evidence": ["dag-contract.json"],
            "nonce": "rw-sanity-provider-branch-lock",
            "signature": "manual-rw-sanity-signature",
        },
    )
    mutating_approval = write_json(
        fixture_dir / "mutating-approval.json",
        {
            "schema": "tau.human_approval_packet.v1",
            "approved": True,
            "actor": {"id": "human:graham", "auth_method": "manual"},
            "action": "working_tree_mutation",
            "target": {"id": "rw-sanity-dag-branch-locks"},
            "reason": "Approve mutating branch lock fixture.",
            "evidence": ["dag-contract.json"],
            "nonce": "rw-sanity-mutating-branch-lock",
            "signature": "manual-rw-sanity-signature",
        },
    )
    locks = {
        "schema": "tau.dag_branch_locks.v1",
        "dag_id": contract["dag_id"],
        "goal_hash": goal_hash,
        "approval_packets": [
            str(provider_approval.name),
            str(mutating_approval.name),
        ],
        "locks": [
            {
                "node_id": "provider-node",
                "branch_type": "provider",
                "lock_id": "rw-sanity-lock-provider",
                "owner": "goal-guardian",
                "actor_identity": "human:graham",
                "approval_packet_sha256": f"sha256:{sha256_file(provider_approval)}",
                "allowed_paths": ["experiments/goal-locked-subagents/proofs/provider/**"],
                "side_effect_class": "provider",
                "workspace_lease": "rw-sanity-workspace-lease",
                "expires_at": "2099-01-01T00:00:00Z",
                "rollback_policy": "required",
            },
            {
                "node_id": "mutating-node",
                "branch_type": "mutating",
                "lock_id": "rw-sanity-lock-mutating",
                "owner": "goal-guardian",
                "actor_identity": "human:graham",
                "approval_packet_sha256": f"sha256:{sha256_file(mutating_approval)}",
                "allowed_paths": ["src/tau_coding/example.py"],
                "side_effect_class": "filesystem",
                "expires_at": "2099-01-01T00:00:00Z",
                "rollback_policy": "required",
            },
        ],
    }
    missing_workspace_lease = json.loads(json.dumps(locks))
    missing_workspace_lease["locks"][0].pop("workspace_lease")
    return {
        "contract": contract_path,
        "valid_locks": write_json(fixture_dir / "branch-locks.valid.json", locks),
        "missing_workspace_lease_locks": write_json(
            fixture_dir / "branch-locks.missing-workspace-lease.json",
            missing_workspace_lease,
        ),
        "valid_receipt": fixture_dir / "branch-lock-validation.valid.receipt.json",
        "missing_workspace_lease_receipt": fixture_dir
        / "branch-lock-validation.missing-workspace-lease.receipt.json",
    }


def dag_expansion_tamper_apply_command(
    *,
    uv_tau: list[str],
    contract: Path,
    proposal: Path,
    validation_receipt: Path,
    policy_receipt: Path,
    apply_receipt: Path,
    preview: Path,
    out: Path,
) -> str:
    return f"""
import json
import subprocess
import sys
from pathlib import Path

uv_tau = {uv_tau!r}
contract = Path({str(contract)!r})
proposal = Path({str(proposal)!r})
validation_receipt = Path({str(validation_receipt)!r})
policy_receipt = Path({str(policy_receipt)!r})
apply_receipt = Path({str(apply_receipt)!r})
preview = Path({str(preview)!r})
out = Path({str(out)!r})


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


run([
    *uv_tau,
    "dag-expansion-validate",
    "--dag-contract",
    str(contract),
    "--proposal",
    str(proposal),
    "--receipt",
    str(validation_receipt),
    "--preview",
    str(preview),
], 0)
run([
    *uv_tau,
    "dag-expansion-policy",
    "--validation-receipt",
    str(validation_receipt),
    "--receipt",
    str(policy_receipt),
], 0)
payload = json.loads(preview.read_text(encoding="utf-8"))
payload["nodes"].append({{
    "id": "tampered",
    "agent": "validator",
    "executor": "local",
    "max_attempts": 1,
    "required_evidence": ["tampered_receipt"],
}})
payload["edges"].append({{"from": "validator", "to": "tampered"}})
preview.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
completed = run([
    *uv_tau,
    "dag-expansion-apply",
    "--validation-receipt",
    str(validation_receipt),
    "--policy-receipt",
    str(policy_receipt),
    "--out",
    str(out),
    "--receipt",
    str(apply_receipt),
], 1)
raise SystemExit(completed.returncode)
"""


def dag_expansion_source_tamper_apply_command(
    *,
    uv_tau: list[str],
    contract: Path,
    proposal: Path,
    validation_receipt: Path,
    policy_receipt: Path,
    apply_receipt: Path,
    out: Path,
) -> str:
    return f"""
import json
import subprocess
import sys
from pathlib import Path

uv_tau = {uv_tau!r}
contract = Path({str(contract)!r})
proposal = Path({str(proposal)!r})
validation_receipt = Path({str(validation_receipt)!r})
policy_receipt = Path({str(policy_receipt)!r})
apply_receipt = Path({str(apply_receipt)!r})
out = Path({str(out)!r})
preview = contract.parent / "expanded-dag.preview.json"


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


run([
    *uv_tau,
    "dag-expansion-validate",
    "--dag-contract",
    str(contract),
    "--proposal",
    str(proposal),
    "--receipt",
    str(validation_receipt),
    "--preview",
    str(preview),
], 0)
run([
    *uv_tau,
    "dag-expansion-policy",
    "--validation-receipt",
    str(validation_receipt),
    "--receipt",
    str(policy_receipt),
], 0)
payload = json.loads(contract.read_text(encoding="utf-8"))
payload["nodes"].append({{
    "id": "source-tamper",
    "agent": "validator",
    "executor": "local",
    "max_attempts": 1,
    "required_evidence": ["tampered_source"],
}})
contract.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
completed = run([
    *uv_tau,
    "dag-expansion-apply",
    "--validation-receipt",
    str(validation_receipt),
    "--policy-receipt",
    str(policy_receipt),
    "--out",
    str(out),
    "--receipt",
    str(apply_receipt),
], 1)
raise SystemExit(completed.returncode)
"""


def create_route_memory_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "dag-route-memory-approval"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    signal = {
        "schema": "tau.dag_signal_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "receipt_path": str(fixture_dir / "dag-signal-receipt.json"),
        "source_dag_receipt": str(fixture_dir / "dag-receipt.json"),
        "dag_id": "rw-sanity-route-memory",
        "goal_hash": "sha256:rw-sanity-route-memory",
        "source_ok": True,
        "source_status": "PASS",
        "source_verdict": "PASS",
        "scheduler": "bounded-ready-queue",
        "negative_signals": [],
        "route_reinforcement_candidates": [
            {
                "from_node": "coder",
                "from_agent": "coder",
                "to_node": "reviewer",
                "to_agent": "reviewer",
                "confidence": 1.0,
                "source": "deterministic_dag_receipt_pass",
                "memory_sync_candidate": True,
                "sync_status": "NOT_SYNCED",
                "sync_reason": "first_slice_local_only",
            }
        ],
    }
    return {
        "signal": write_json(fixture_dir / "dag-signal-receipt.json", signal),
        "candidate_receipt": fixture_dir / "candidate-receipt.json",
        "sync_receipt": fixture_dir / "sync-receipt.json",
        "approval_mismatch_candidate_receipt": fixture_dir
        / "approval-mismatch-candidate-receipt.json",
        "approval_mismatch_sync_receipt": fixture_dir / "approval-mismatch-sync-receipt.json",
        "approval_mismatch_receipt": write_json(
            fixture_dir / "approval-mismatch-receipt.json",
            {
                "schema": "tau.approval_gate_receipt.v1",
                "ok": True,
                "status": "PASS",
                "approved": True,
                "requested_action": "memory_upsert",
                "packet_summary": {
                    "target_id": "route-memory:other-dag:tau_route_memory",
                },
            },
        ),
        "approved_candidate_receipt": fixture_dir / "approved-candidate-receipt.json",
        "approved_sync_receipt": fixture_dir / "approved-sync-receipt.json",
        "approved_memory_requests": fixture_dir / "approved-memory-requests.json",
        "approved_receipt": write_json(
            fixture_dir / "approved-memory-upsert-receipt.json",
            {
                "schema": "tau.approval_gate_receipt.v1",
                "ok": True,
                "status": "PASS",
                "approved": True,
                "requested_action": "memory_upsert",
                "packet_summary": {
                    "target_id": "route-memory:rw-sanity-route-memory:tau_route_memory",
                },
            },
        ),
        "dry_run_candidate_receipt": fixture_dir / "dry-run-candidate-receipt.json",
        "dry_run_sync_receipt": fixture_dir / "dry-run-sync-receipt.json",
    }


def route_memory_dry_run_command(
    *,
    uv_tau: list[str],
    signal: Path,
    candidate_receipt: Path,
    sync_receipt: Path,
) -> str:
    return f"""
import subprocess
import sys
from pathlib import Path

uv_tau = {uv_tau!r}
signal = Path({str(signal)!r})
candidate_receipt = Path({str(candidate_receipt)!r})
sync_receipt = Path({str(sync_receipt)!r})


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


run([
    *uv_tau,
    "dag-route-memory-candidates",
    "--signal-receipt",
    str(signal),
    "--receipt",
    str(candidate_receipt),
], 0)
completed = run([
    *uv_tau,
    "dag-route-memory-sync",
    "--candidate-receipt",
    str(candidate_receipt),
    "--receipt",
    str(sync_receipt),
], 0)
raise SystemExit(completed.returncode)
"""


def route_memory_apply_without_approval_command(
    *,
    uv_tau: list[str],
    signal: Path,
    candidate_receipt: Path,
    sync_receipt: Path,
) -> str:
    return f"""
import subprocess
import sys
from pathlib import Path

uv_tau = {uv_tau!r}
signal = Path({str(signal)!r})
candidate_receipt = Path({str(candidate_receipt)!r})
sync_receipt = Path({str(sync_receipt)!r})


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


run([
    *uv_tau,
    "dag-route-memory-candidates",
    "--signal-receipt",
    str(signal),
    "--receipt",
    str(candidate_receipt),
], 0)
completed = run([
    *uv_tau,
    "dag-route-memory-sync",
    "--candidate-receipt",
    str(candidate_receipt),
    "--receipt",
    str(sync_receipt),
    "--apply",
], 1)
raise SystemExit(completed.returncode)
"""


def route_memory_apply_wrong_approval_target_command(
    *,
    uv_tau: list[str],
    signal: Path,
    candidate_receipt: Path,
    sync_receipt: Path,
    approval_receipt: Path,
) -> str:
    return f"""
import subprocess
import sys
from pathlib import Path

uv_tau = {uv_tau!r}
signal = Path({str(signal)!r})
candidate_receipt = Path({str(candidate_receipt)!r})
sync_receipt = Path({str(sync_receipt)!r})
approval_receipt = Path({str(approval_receipt)!r})


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


run([
    *uv_tau,
    "dag-route-memory-candidates",
    "--signal-receipt",
    str(signal),
    "--receipt",
    str(candidate_receipt),
], 0)
completed = run([
    *uv_tau,
    "dag-route-memory-sync",
    "--candidate-receipt",
    str(candidate_receipt),
    "--receipt",
    str(sync_receipt),
    "--apply",
    "--approval-receipt",
    str(approval_receipt),
], 1)
raise SystemExit(completed.returncode)
"""


def route_memory_apply_with_approval_command(
    *,
    uv_tau: list[str],
    signal: Path,
    candidate_receipt: Path,
    sync_receipt: Path,
    approval_receipt: Path,
    memory_requests: Path,
) -> str:
    return f"""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

uv_tau = {uv_tau!r}
signal = Path({str(signal)!r})
candidate_receipt = Path({str(candidate_receipt)!r})
sync_receipt = Path({str(sync_receipt)!r})
approval_receipt = Path({str(approval_receipt)!r})
memory_requests = Path({str(memory_requests)!r})
requests = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8")) if body else {{}}
        requests.append({{"path": self.path, "payload": payload}})
        documents = payload.get("documents") if isinstance(payload, dict) else []
        response = {{
            "ok": True,
            "received": len(documents) if isinstance(documents, list) else 0,
        }}
        response_bytes = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def log_message(self, format, *args):
        return


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    run([
        *uv_tau,
        "dag-route-memory-candidates",
        "--signal-receipt",
        str(signal),
        "--receipt",
        str(candidate_receipt),
    ], 0)
    completed = run([
        *uv_tau,
        "dag-route-memory-sync",
        "--candidate-receipt",
        str(candidate_receipt),
        "--receipt",
        str(sync_receipt),
        "--apply",
        "--approval-receipt",
        str(approval_receipt),
        "--memory-url",
        f"http://127.0.0.1:{{server.server_port}}",
    ], 0)
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

memory_requests.write_text(json.dumps(requests, indent=2, sort_keys=True), encoding="utf-8")
payload = json.loads(sync_receipt.read_text(encoding="utf-8"))
if payload.get("memory_sync") is not True:
    raise SystemExit("expected memory_sync true")
if payload.get("sync_status") != "SYNCED":
    raise SystemExit(f"expected SYNCED, got {{payload.get('sync_status')}}")
if len(requests) != 1 or requests[0].get("path") != "/upsert":
    raise SystemExit(f"expected one /upsert request, got {{requests}}")
documents = requests[0]["payload"].get("documents")
if not isinstance(documents, list) or len(documents) != payload.get("projected_document_count"):
    raise SystemExit("Memory /upsert document count did not match receipt")
raise SystemExit(completed.returncode)
"""


def create_research_source_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "research-source-arxiv"
    invalid_dir = run_dir / "research-source-arxiv-invalid"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    invalid_dir.mkdir(parents=True, exist_ok=True)
    valid_packet = {
        "schema": "tau.research_source_packet.v1",
        "source_type": "paper",
        "method": "arxiv",
        "query": "adaptive DAG multi-agent routing references for Tau",
        "retrieved_at": "2026-07-05T13:40:00Z",
        "classification": "design_input",
        "sources": [
            {
                "title": "Graph of Thoughts: Solving Elaborate Problems with Large Language Models",
                "url": "https://arxiv.org/abs/2308.09687",
                "arxiv_id": "2308.09687",
                "relevance": "HIGH",
                "claims_supported": ["graph-structured reasoning inspiration"],
            },
            {
                "title": "Adaptive Graph of Thoughts: Test-Time Adaptive Reasoning",
                "url": "https://arxiv.org/abs/2502.05078",
                "arxiv_id": "2502.05078",
                "relevance": "HIGH",
                "claims_supported": ["bounded dynamic DAG expansion inspiration"],
            },
        ],
        "summary": "Source-bearing ArXiv packet for Tau adaptive DAG design review.",
        "limitations": [
            "Research is design input only.",
            "Local Tau receipts and validators remain required before closure.",
        ],
    }
    invalid_packet = {
        **valid_packet,
        "sources": [
            {
                "title": "Generic web article mislabeled as ArXiv",
                "url": "https://example.com/not-arxiv",
                "relevance": "HIGH",
                "claims_supported": ["generic distributed-agent claim"],
            }
        ],
    }
    return {
        "valid_source": write_json(fixture_dir / "research-source-packet.json", valid_packet),
        "valid_receipt": fixture_dir / "research-source-receipt.json",
        "invalid_source": write_json(
            invalid_dir / "research-source-packet.json",
            invalid_packet,
        ),
        "invalid_receipt": invalid_dir / "research-source-receipt.json",
    }


def create_proof_index_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-proof-index"
    source_dir = fixture_dir / "source"
    write_json(
        source_dir / "dag" / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dag_id": "rw-sanity-proof-index",
            "goal_hash": "sha256:rw-sanity-proof-index",
            "proof_scope": {
                "proves": ["fixture DAG receipt is indexable"],
                "does_not_prove": ["provider/model semantic quality"],
            },
        },
    )
    write_json(
        source_dir / "monitor" / "monitor-receipt.json",
        {
            "schema": "tau.monitor_receipt.v1",
            "ok": False,
            "status": "REVIEW",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dag_id": "rw-sanity-proof-index",
            "goal": {"goal_hash": "sha256:rw-sanity-proof-index"},
            "claims": {
                "proves": ["fixture monitor receipt is indexable"],
                "does_not_prove": ["hidden chain-of-thought correctness"],
            },
        },
    )
    write_json(
        source_dir / "ignore" / "metadata.json",
        {
            "schema": "tau.metadata.v1",
            "status": "PASS",
        },
    )
    return {
        "source_dir": source_dir,
        "output": fixture_dir / "proof-index.jsonl",
        "receipt": fixture_dir / "proof-index-build-receipt.json",
    }


def create_github_apply_policy_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "github-apply-policy-missing-gates"
    positive_dir = run_dir / "github-apply-policy-all-gates"
    tamper_dir = run_dir / "github-apply-policy-redaction-tamper"
    approval_mismatch_dir = run_dir / "github-apply-policy-approval-mismatch"
    transport_dir = run_dir / "github-handoff-transport-apply-missing-policy"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    positive_dir.mkdir(parents=True, exist_ok=True)
    tamper_dir.mkdir(parents=True, exist_ok=True)
    approval_mismatch_dir.mkdir(parents=True, exist_ok=True)
    transport_dir.mkdir(parents=True, exist_ok=True)
    goal_hash = "sha256:rw-sanity-github-apply-policy"
    projection = {
        "schema": "tau.agent_handoff_projection_receipt.v1",
        "ok": True,
        "target": {
            "repo": "grahama1970/tau",
            "target": "issue#47",
        },
        "comment": {
            "body": "## Tau Agent Handoff\n\nDry-run projection for policy gate sanity.",
        },
        "labels": {
            "add": ["agent-work"],
            "remove": ["agent-active"],
        },
        "errors": [],
    }
    policy = {
        "schema": "tau.github_apply_policy.v1",
        "allowed_repos": ["grahama1970/tau"],
        "allowed_actions": ["comment", "label"],
        "denied_actions": ["close", "merge", "release"],
        "requires_approval_packet": True,
        "requires_preflight": True,
        "requires_redaction": True,
    }
    positive_projection_path = positive_dir / "projection.json"
    positive_redacted_projection_path = positive_dir / "projection.redacted.json"
    tamper_projection_path = tamper_dir / "projection.json"
    tamper_redacted_projection_path = tamper_dir / "projection.redacted.json"
    approval_mismatch_projection_path = approval_mismatch_dir / "projection.json"
    approval_mismatch_redacted_projection_path = approval_mismatch_dir / "projection.redacted.json"
    write_json(positive_projection_path, projection)
    write_json(positive_redacted_projection_path, projection)
    write_json(tamper_projection_path, projection)
    write_json(tamper_redacted_projection_path, projection)
    write_json(approval_mismatch_projection_path, projection)
    write_json(approval_mismatch_redacted_projection_path, projection)
    approval_receipt = {
        "schema": "tau.approval_gate_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "approved": True,
        "requested_action": "github_apply",
        "packet_summary": {
            "target_id": "grahama1970/tau:issue#47",
        },
        "errors": [],
    }
    redaction_receipt = {
        "schema": "tau.github_projection_redaction_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "projection": str(positive_projection_path.resolve()),
        "redacted_projection": str(positive_redacted_projection_path.resolve()),
        "redacted_projection_sha256": sha256_file(positive_redacted_projection_path),
        "redaction_count": 1,
        "errors": [],
    }
    tamper_redaction_receipt = dict(redaction_receipt)
    tamper_redaction_receipt["projection"] = str(tamper_projection_path.resolve())
    tamper_redaction_receipt["redacted_projection"] = str(tamper_redacted_projection_path.resolve())
    tamper_redaction_receipt["redacted_projection_sha256"] = sha256_file(
        tamper_redacted_projection_path
    )
    write_json(tamper_redacted_projection_path, {"tampered": True})
    approval_mismatch_receipt = dict(approval_receipt)
    approval_mismatch_receipt["packet_summary"] = {
        "target_id": "grahama1970/tau:issue#999",
    }
    approval_mismatch_redaction_receipt = dict(redaction_receipt)
    approval_mismatch_redaction_receipt["projection"] = str(
        approval_mismatch_projection_path.resolve()
    )
    approval_mismatch_redaction_receipt["redacted_projection"] = str(
        approval_mismatch_redacted_projection_path.resolve()
    )
    approval_mismatch_redaction_receipt["redacted_projection_sha256"] = sha256_file(
        approval_mismatch_redacted_projection_path
    )
    handoff = {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/tau",
            "target": "issue#47",
        },
        "goal": {
            "goal_id": "rw-sanity-github-apply-policy",
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "previous_subagent": "coder",
        "context": {
            "summary": "Real-world sanity GitHub transport apply policy fixture.",
            "artifacts": [],
        },
        "result": {
            "status": "COMPLETED",
            "summary": "Projection is intentionally used to prove apply fails closed without policy receipt.",
            "evidence": ["github transport missing policy negative control"],
        },
        "rationale": "GitHub mutation must remain policy-gated.",
        "next_agent": {
            "name": "reviewer",
            "executor": "local",
            "reason": "Reviewer route is valid for the handoff projection.",
        },
        "required_evidence": ["github apply policy receipt before live apply"],
        "stop_condition": "Stop before live apply without policy receipt.",
    }
    return {
        "projection": write_json(fixture_dir / "projection.json", projection),
        "policy": write_json(fixture_dir / "github-apply-policy.json", policy),
        "receipt": fixture_dir / "github-apply-policy-receipt.json",
        "positive_projection": positive_projection_path,
        "positive_redacted_projection": positive_redacted_projection_path,
        "positive_policy": write_json(positive_dir / "github-apply-policy.json", policy),
        "positive_approval": write_json(
            positive_dir / "approval-gate-receipt.json",
            approval_receipt,
        ),
        "positive_redaction": write_json(
            positive_dir / "github-redaction-receipt.json",
            redaction_receipt,
        ),
        "positive_receipt": positive_dir / "github-apply-policy-receipt.json",
        "tamper_projection": tamper_projection_path,
        "tamper_redacted_projection": tamper_redacted_projection_path,
        "tamper_policy": write_json(tamper_dir / "github-apply-policy.json", policy),
        "tamper_approval": write_json(
            tamper_dir / "approval-gate-receipt.json",
            approval_receipt,
        ),
        "tamper_redaction": write_json(
            tamper_dir / "github-redaction-receipt.json",
            tamper_redaction_receipt,
        ),
        "tamper_receipt": tamper_dir / "github-apply-policy-receipt.json",
        "approval_mismatch_projection": approval_mismatch_projection_path,
        "approval_mismatch_redacted_projection": approval_mismatch_redacted_projection_path,
        "approval_mismatch_policy": write_json(
            approval_mismatch_dir / "github-apply-policy.json",
            policy,
        ),
        "approval_mismatch_approval": write_json(
            approval_mismatch_dir / "approval-gate-receipt.json",
            approval_mismatch_receipt,
        ),
        "approval_mismatch_redaction": write_json(
            approval_mismatch_dir / "github-redaction-receipt.json",
            approval_mismatch_redaction_receipt,
        ),
        "approval_mismatch_receipt": approval_mismatch_dir / "github-apply-policy-receipt.json",
        "handoff": write_json(transport_dir / "handoff.json", handoff),
        "handoff_goal_hash": goal_hash,
        "transport_missing_policy_receipt": transport_dir
        / "github-transport-missing-policy-receipt.json",
    }


def _simple_yaml(value: Any, *, indent: int = 0) -> str:
    """Emit the simple YAML subset used by Tau DAG fixtures."""

    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, dict):
                lines.append(f"{prefix}{key}:")
                lines.append(_simple_yaml(item, indent=indent + 2).rstrip())
            elif isinstance(item, list):
                lines.append(f"{prefix}{key}:")
                lines.append(_simple_yaml(item, indent=indent + 2).rstrip())
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return "\n".join(lines) + "\n"
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{prefix}-")
                lines.append(_simple_yaml(item, indent=indent + 2).rstrip())
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.append(_simple_yaml(item, indent=indent + 2).rstrip())
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return "\n".join(lines) + "\n"
    return f"{prefix}{_yaml_scalar(value)}\n"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if value is None:
        return "null"
    text = str(value)
    if not text or any(char in text for char in ":#{}[],&*?|-<>=!%@`'\""):
        return json.dumps(text)
    return text


def _flaky_project_dag_command(
    *,
    worker: Path,
    role: str,
    scenario: str,
    state: Path,
    first_failure: str,
) -> str:
    if first_failure == "timeout":
        failure = "import time; time.sleep(5)"
    elif first_failure == "non-json":
        failure = "print('not json')"
    else:
        raise ValueError(f"unknown first_failure: {first_failure}")
    return f"""
import runpy
import sys
from pathlib import Path

state = Path({str(state)!r})
state.parent.mkdir(parents=True, exist_ok=True)
count = int(state.read_text() or '0') if state.exists() else 0
state.write_text(str(count + 1))
if count == 0:
    {failure}
else:
    sys.argv = [{str(worker)!r}, '--role', {role!r}, '--scenario', {scenario!r}]
    runpy.run_path({str(worker)!r}, run_name='__main__')
"""


def project_dag_worker_script() -> str:
    return r"""#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    artifact_dir = Path(os.environ["TAU_HANDOFF_COMMAND_ARTIFACT_DIR"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.role == "coder":
        response = coder_response(payload, artifact_dir, args.scenario)
    elif args.role == "research-auditor":
        response = research_response(payload, artifact_dir, args.scenario)
    elif args.role == "reviewer":
        response = reviewer_response(payload, artifact_dir, args.scenario)
    else:
        raise SystemExit(f"unknown role: {args.role}")
    print(json.dumps(response, sort_keys=True))
    return 0


def coder_response(payload, artifact_dir, scenario):
    if scenario.startswith("concurrent"):
        time.sleep(0.4)
    prior = reviewer_verdicts(payload)
    attempt = 2 if any(item.get("verdict") == "REVISE" for item in prior) else 1
    artifact = artifact_dir / f"creator-artifact-attempt-{attempt}.json"
    artifact_payload = {
        "schema": "tau.creator_artifact.v1",
        "attempt": attempt,
        "scenario": scenario,
        "goal_hash": payload["goal"]["goal_hash"],
        "summary": "Creator artifact for real-world project DAG sanity.",
    }
    artifact.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence = [
        {
            "kind": "creator_artifact",
            "path": str(artifact),
            "attempt": attempt,
            "goal_hash": payload["goal"]["goal_hash"],
        }
    ]
    if scenario == "provider-metadata" and provider_metadata_present(payload):
        provider_artifact = write_provider_route_artifact(payload, artifact_dir, "coder")
        evidence.append(
            {
                "kind": "provider_route_receipt",
                "path": str(provider_artifact),
                "goal_hash": payload["goal"]["goal_hash"],
            }
        )
    return handoff(
        payload,
        previous_subagent="coder",
        result_status="PASS",
        evidence=evidence,
        next_agent="reviewer",
        next_executor="local",
        summary=f"Creator produced attempt {attempt} artifact for reviewer.",
    )


def research_response(payload, artifact_dir, scenario):
    if scenario.startswith("concurrent"):
        time.sleep(0.4)
    artifact = artifact_dir / "source-summary.json"
    artifact_payload = {
        "schema": "tau.source_summary.v1",
        "scenario": scenario,
        "goal_hash": payload["goal"]["goal_hash"],
        "summary": "Research branch source summary for real-world project DAG sanity.",
    }
    artifact.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return handoff(
        payload,
        previous_subagent="research-auditor",
        result_status="PASS",
        evidence=[
            {
                "kind": "source_summary",
                "path": str(artifact),
                "goal_hash": payload["goal"]["goal_hash"],
            }
        ],
        next_agent="human",
        next_executor="human",
        summary="Research branch produced source summary evidence.",
    )


def reviewer_response(payload, artifact_dir, scenario):
    creator = creator_artifacts(payload)
    attempt = int(creator[-1].get("attempt", 1)) if creator else 0
    active_goal_hash = os.environ["TAU_HANDOFF_ACTIVE_GOAL_HASH"]
    verdict_goal_hash = "sha256:stale-reviewer-goal" if scenario == "complex" else active_goal_hash
    if scenario == "max-steps":
        verdict = "REVISE"
        next_agent = "coder"
        next_executor = "local"
    elif scenario == "medium" and attempt < 2:
        verdict = "REVISE"
        next_agent = "coder"
        next_executor = "local"
    else:
        verdict = "PASS"
        next_agent = "human"
        next_executor = "human"
    artifact = artifact_dir / f"reviewer-verdict-attempt-{max(attempt, 1)}.json"
    artifact_payload = {
        "schema": "tau.reviewer_verdict.v1",
        "scenario": scenario,
        "reviewed_node_id": "coder",
        "creator_artifact_count": len(creator),
        "creator_attempt": attempt,
        "goal_hash": verdict_goal_hash,
        "active_goal_hash": active_goal_hash,
        "goal_matches": verdict_goal_hash == active_goal_hash,
        "verdict": verdict,
    }
    artifact.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence = [
        {
            "kind": "reviewer_verdict",
            "path": str(artifact),
            "reviewed_node_id": "coder",
            "creator_attempt": attempt,
            "goal_hash": verdict_goal_hash,
            "verdict": verdict,
        }
    ]
    if scenario == "provider-metadata" and provider_metadata_present(payload):
        provider_artifact = write_provider_route_artifact(payload, artifact_dir, "reviewer")
        evidence.append(
            {
                "kind": "provider_route_receipt",
                "path": str(provider_artifact),
                "goal_hash": payload["goal"]["goal_hash"],
            }
        )
    return handoff(
        payload,
        previous_subagent="reviewer",
        result_status=verdict,
        evidence=evidence,
        next_agent=next_agent,
        next_executor=next_executor,
        summary=f"Reviewer returned {verdict} for creator attempt {attempt}.",
    )


def provider_metadata_present(payload):
    context = payload.get("context") if isinstance(payload, dict) else {}
    if not isinstance(context, dict):
        return False
    node = context.get("tau_dag_node")
    return (
        isinstance(context.get("model_policy"), dict)
        and isinstance(context.get("prompt_contract"), dict)
        and isinstance(node, dict)
        and isinstance(node.get("model_policy"), dict)
        and isinstance(node.get("prompt_contract"), dict)
    )


def write_provider_route_artifact(payload, artifact_dir, role):
    artifact = artifact_dir / f"{role}-provider-route-receipt.json"
    context = payload["context"]
    artifact_payload = {
        "schema": "tau.provider_route_receipt.v1",
        "kind": "provider_route_receipt",
        "role": role,
        "goal_hash": payload["goal"]["goal_hash"],
        "model_policy": context["model_policy"],
        "prompt_contract": context["prompt_contract"],
    }
    artifact.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def handoff(payload, *, previous_subagent, result_status, evidence, next_agent, next_executor, summary):
    return {
        "schema": "tau.agent_handoff.v1",
        "github": payload["github"],
        "goal": payload["goal"],
        "previous_subagent": previous_subagent,
        "context": {
            "summary": summary,
            "artifacts": [item["path"] for item in evidence if isinstance(item, dict) and "path" in item],
        },
        "result": {
            "status": result_status,
            "summary": summary,
            "evidence": evidence,
        },
        "rationale": "The DAG contract controls routing and immutable-goal checks.",
        "next_agent": {
            "name": next_agent,
            "executor": next_executor,
            "reason": "Continue according to the project DAG contract.",
        },
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human or a fail-closed DAG invariant.",
    }


def creator_artifacts(payload):
    return evidence_items(payload, "creator_artifact")


def reviewer_verdicts(payload):
    return evidence_items(payload, "reviewer_verdict")


def evidence_items(payload, kind):
    evidence = payload.get("result", {}).get("evidence", [])
    return [item for item in evidence if isinstance(item, dict) and item.get("kind") == kind]


if __name__ == "__main__":
    raise SystemExit(main())
"""


def create_generic_dag_resume_fixture(run_dir: Path) -> Path:
    fixture_dir = run_dir / "medium-generic-dag-resume"
    receipts = fixture_dir / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    planner_receipt = receipts / "planner.json"
    write_json(
        planner_receipt,
        {
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": "planner",
            "status": "PASS",
            "verdict": "PASS",
            "artifacts": [],
            "commands_run": ["preexisting planner receipt for resume proof"],
            "handoff_summary": "planner receipt existed before dag-run",
            "errors": [],
            "policy_exceptions": [],
        },
    )
    coder_receipt = receipts / "coder.json"
    spec_path = fixture_dir / "dag-spec.json"
    write_json(
        spec_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "rw-sanity-generic-dag-resume",
            "run_dir": str(fixture_dir),
            "events_jsonl": str(fixture_dir / "events.jsonl"),
            "nodes": [
                {
                    "node_id": "planner",
                    "role": "planner",
                    "depends_on": [],
                    "command": [
                        "python3",
                        "-c",
                        "raise SystemExit('planner command should have been resumed')",
                    ],
                    "receipt_path": str(planner_receipt),
                    "timeout_seconds": 30,
                    "max_attempts": 1,
                },
                {
                    "node_id": "coder",
                    "role": "coder",
                    "depends_on": ["planner"],
                    "command": [
                        "python3",
                        "-c",
                        generic_dag_receipt_writer(coder_receipt, node_id="coder"),
                    ],
                    "receipt_path": str(coder_receipt),
                    "timeout_seconds": 30,
                    "max_attempts": 1,
                },
            ],
        },
    )
    return spec_path


def create_generic_dag_stale_work_order_fixture(run_dir: Path) -> Path:
    fixture_dir = run_dir / "medium-generic-dag-stale-work-order"
    receipts = fixture_dir / "receipts"
    work_orders = fixture_dir / "work-orders"
    receipts.mkdir(parents=True, exist_ok=True)
    work_orders.mkdir(parents=True, exist_ok=True)
    work_order = work_orders / "planner.json"
    work_order.write_text('{"task":"old planner work"}\n', encoding="utf-8")
    old_hash = sha256_file(work_order)
    planner_receipt = receipts / "planner.json"
    write_json(
        planner_receipt,
        {
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": "planner",
            "status": "PASS",
            "verdict": "PASS",
            "work_order_sha256": old_hash,
            "artifacts": [],
            "commands_run": ["preexisting stale planner receipt for resume guard proof"],
            "handoff_summary": "planner receipt was written before the work order changed",
            "errors": [],
            "policy_exceptions": [],
        },
    )
    work_order.write_text('{"task":"changed planner work"}\n', encoding="utf-8")
    spec_path = fixture_dir / "dag-spec.json"
    write_json(
        spec_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "rw-sanity-generic-dag-stale-work-order",
            "run_dir": str(fixture_dir),
            "events_jsonl": str(fixture_dir / "events.jsonl"),
            "nodes": [
                {
                    "node_id": "planner",
                    "role": "planner",
                    "depends_on": [],
                    "work_order_path": str(work_order),
                    "command": [
                        "python3",
                        "-c",
                        "raise SystemExit('stale work-order receipt should not be resumed')",
                    ],
                    "receipt_path": str(planner_receipt),
                    "timeout_seconds": 30,
                    "max_attempts": 1,
                }
            ],
        },
    )
    return spec_path


def create_generic_dag_timeout_fixture(run_dir: Path) -> Path:
    fixture_dir = run_dir / "medium-generic-dag-timeout"
    receipts = fixture_dir / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    spec_path = fixture_dir / "dag-spec.json"
    write_json(
        spec_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "rw-sanity-generic-dag-timeout",
            "run_dir": str(fixture_dir),
            "events_jsonl": str(fixture_dir / "events.jsonl"),
            "nodes": [
                {
                    "node_id": "slow",
                    "role": "worker",
                    "depends_on": [],
                    "command": [
                        "python3",
                        "-c",
                        "import time; time.sleep(5)",
                    ],
                    "receipt_path": str(receipts / "slow.json"),
                    "timeout_seconds": 0.1,
                    "max_attempts": 2,
                }
            ],
        },
    )
    return spec_path


def create_approval_gate_fixtures(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-approval-gates"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    pass_packet = fixture_dir / "working-tree-approval.json"
    mismatch_packet = fixture_dir / "closure-mismatch-approval.json"
    expired_packet = fixture_dir / "expired-working-tree-approval.json"
    write_json(
        pass_packet,
        approval_packet(
            action="working_tree_mutation",
            target_id="scratch-working-tree",
            reason="Approve a bounded scratch working-tree mutation gate for sanity proof.",
        ),
    )
    write_json(
        mismatch_packet,
        approval_packet(
            action="working_tree_mutation",
            target_id="github-ticket-closure",
            reason="Intentionally mismatched action for fail-closed sanity proof.",
        ),
    )
    expired_payload = approval_packet(
        action="working_tree_mutation",
        target_id="scratch-working-tree",
        reason="Intentionally expired approval for fail-closed sanity proof.",
    )
    expired_payload["expires_at"] = "2000-01-01T00:00:00Z"
    write_json(expired_packet, expired_payload)
    return {
        "pass_packet": pass_packet,
        "mismatch_packet": mismatch_packet,
        "expired_packet": expired_packet,
        "pass_run_dir": fixture_dir / "pass",
        "blocked_run_dir": fixture_dir / "blocked",
        "expired_run_dir": fixture_dir / "expired",
    }


def create_cleanup_status_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-herdr-cleanup"
    write_json(
        fixture_dir / "runtime-manifest.json",
        {
            "schema": "tau.provider_dag_runtime_manifest.v1",
            "run_id": "rw-sanity-herdr-cleanup",
            "provider_sessions": {
                "codex": {
                    "workspace_id": "w-rw-sanity-cleanup",
                    "pane_id": "w-rw-sanity-cleanup:p5",
                    "terminal_id": "term-codex",
                }
            },
            "visible_subagents": {
                "planner": {
                    "workspace_id": "w-rw-sanity-cleanup",
                    "pane_id": "w-rw-sanity-cleanup:p7",
                    "terminal_id": "term-planner",
                },
                "orchestrator": {
                    "workspace_id": "w-rw-sanity-cleanup",
                    "pane_id": "w-rw-sanity-cleanup:p8",
                    "terminal_id": "term-orchestrator",
                },
            },
        },
    )
    return {"run_dir": fixture_dir}


def create_cleanup_session_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-herdr-cleanup-session"
    manifest_payload = {
        "schema": "tau.provider_dag_runtime_manifest.v1",
        "run_id": "rw-sanity-herdr-cleanup-session",
        "provider_sessions": {
            "codex": {
                "workspace_id": "w-rw-sanity-session-cleanup",
                "pane_id": "w-rw-sanity-session-cleanup:p5",
                "terminal_id": "term-codex",
                "session": "session-rw-sanity-codex",
            }
        },
    }
    blocked_dir = fixture_dir / "blocked"
    owned_dir = fixture_dir / "owned"
    blocked_manifest_path = write_json(blocked_dir / "runtime-manifest.json", manifest_payload)
    owned_manifest_path = write_json(owned_dir / "runtime-manifest.json", manifest_payload)
    now = datetime.now(UTC).replace(microsecond=0)
    lease_payload = {
        "schema": "tau.herdr_workspace_lease.v1",
        "run_id": "rw-sanity-herdr-cleanup-session",
        "dag_id": "rw-sanity-herdr-cleanup-session",
        "owner": "tau-real-world-sanity",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "cleanup_policy": "apply",
        "workspace_ids": ["w-rw-sanity-session-cleanup"],
    }
    blocked_workspace_lease = write_json(
        blocked_dir / "herdr-workspace-lease.json",
        {
            **lease_payload,
            "source_runtime_manifest": str(blocked_manifest_path),
        },
    )
    owned_workspace_lease = write_json(
        owned_dir / "herdr-workspace-lease.json",
        {
            **lease_payload,
            "source_runtime_manifest": str(owned_manifest_path),
        },
    )
    session_ownership = write_json(
        owned_dir / "herdr-session-ownership.json",
        {
            "schema": "tau.herdr_session_ownership.v1",
            "run_id": "rw-sanity-herdr-cleanup-session",
            "dag_id": "rw-sanity-herdr-cleanup-session",
            "owner": "tau-real-world-sanity",
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "cleanup_policy": "apply",
            "session_ids": ["session-rw-sanity-codex"],
            "source_runtime_manifest": str(owned_manifest_path),
        },
    )
    owned_herdr = owned_dir / "fake-herdr-owned-session"
    owned_herdr_calls = owned_dir / "owned-herdr-calls.jsonl"
    owned_herdr.write_text(
        "#!/usr/bin/env bash\n"
        f"CALLS={json.dumps(str(owned_herdr_calls))}\n"
        'printf \'{"argv":[\' >> "$CALLS"\n'
        "first=1\n"
        'for arg in "$@"; do\n'
        '  if [ "$first" = 0 ]; then printf \',\' >> "$CALLS"; fi\n'
        "  first=0\n"
        '  python3 -c \'import json,sys; print(json.dumps(sys.argv[1]), end="")\' "$arg" >> "$CALLS"\n'
        "done\n"
        "printf ']}\\n' >> \"$CALLS\"\n"
        'if [ "$1 $2 $3" = "session get session-rw-sanity-codex" ]; then\n'
        '  printf \'{"error":{"code":"session_not_found","message":"session not found"}}\\n\'\n'
        "  exit 1\n"
        "fi\n"
        'if [ "$1 $2 $3" = "workspace get w-rw-sanity-session-cleanup" ]; then\n'
        '  printf \'{"error":{"code":"workspace_not_found","message":"workspace not found"}}\\n\'\n'
        "  exit 1\n"
        "fi\n"
        'printf \'{"result":{"type":"ok"}}\\n\'\n',
        encoding="utf-8",
    )
    owned_herdr.chmod(0o755)
    return {
        "blocked_run_dir": blocked_dir,
        "owned_run_dir": owned_dir,
        "blocked_workspace_lease": blocked_workspace_lease,
        "owned_workspace_lease": owned_workspace_lease,
        "session_ownership": session_ownership,
        "blocked_herdr": blocked_dir / "should-not-run-herdr",
        "owned_herdr": owned_herdr,
    }


def create_cleanup_gc_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-herdr-gc-approval"
    wrong_target_dir = run_dir / "medium-herdr-gc-wrong-target"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    wrong_target_dir.mkdir(parents=True, exist_ok=True)
    workspaces_path = write_json(
        fixture_dir / "workspaces.json",
        {
            "result": {
                "workspaces": [
                    {
                        "workspace_id": "w-rw-sanity-gc-stale",
                        "label": "rw-sanity-provider-readiness-gc",
                        "agent_status": "done",
                        "focused": False,
                        "pane_count": 2,
                        "tab_count": 1,
                    }
                ]
            }
        },
    )
    wrong_target_workspaces_path = write_json(
        wrong_target_dir / "workspaces.json",
        {
            "result": {
                "workspaces": [
                    {
                        "workspace_id": "w-rw-sanity-gc-wrong-target",
                        "label": "rw-sanity-provider-readiness-gc",
                        "agent_status": "done",
                        "focused": False,
                        "pane_count": 2,
                        "tab_count": 1,
                    }
                ]
            }
        },
    )
    calls_path = fixture_dir / "herdr-calls.jsonl"
    wrong_target_calls_path = wrong_target_dir / "herdr-calls.jsonl"
    herdr_bin = fixture_dir / "fake-herdr"
    wrong_target_herdr_bin = wrong_target_dir / "fake-herdr"
    approval_packet_path = write_json(
        fixture_dir / "herdr-gc-approval.json",
        approval_packet(
            action="herdr_gc_apply",
            target_id=HERDR_GC_DEFAULT_TARGET_ID,
            reason="Authorize fake-Herdr GC apply for real-world sanity proof.",
        ),
    )
    wrong_target_approval_packet_path = write_json(
        wrong_target_dir / "herdr-gc-wrong-target-approval.json",
        approval_packet(
            action="herdr_gc_apply",
            target_id="herdr-gc:other-prefix",
            reason="Intentionally wrong Herdr GC target for fail-closed sanity proof.",
        ),
    )

    def write_fake_herdr(path: Path, *, calls: Path, workspaces: Path) -> None:
        path.write_text(
            "#!/usr/bin/env bash\n"
            f"HERDR_GC_CALLS={str(calls)!r}\n"
            f"HERDR_GC_WORKSPACES={str(workspaces)!r}\n"
            'printf \'{"argv":[\' >> "$HERDR_GC_CALLS"\n'
            "first=1\n"
            'for arg in "$@"; do\n'
            '  if [ "$first" = 0 ]; then printf \',\' >> "$HERDR_GC_CALLS"; fi\n'
            "  first=0\n"
            '  python3 -c \'import json,sys; print(json.dumps(sys.argv[1]), end="")\' "$arg" >> "$HERDR_GC_CALLS"\n'
            "done\n"
            "printf ']}\\n' >> \"$HERDR_GC_CALLS\"\n"
            'if [ "$1 $2" = "workspace list" ]; then\n'
            '  cat "$HERDR_GC_WORKSPACES"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1 $2" = "workspace get" ]; then\n'
            '  printf \'{"error":{"code":"workspace_not_found","message":"workspace not found"}}\\n\'\n'
            "  exit 1\n"
            "fi\n"
            'printf \'{"result":{"type":"ok"}}\\n\'\n',
            encoding="utf-8",
        )
        path.chmod(0o755)

    write_fake_herdr(herdr_bin, calls=calls_path, workspaces=workspaces_path)
    write_fake_herdr(
        wrong_target_herdr_bin,
        calls=wrong_target_calls_path,
        workspaces=wrong_target_workspaces_path,
    )
    return {
        "run_dir": fixture_dir,
        "herdr_bin": herdr_bin,
        "workspaces": workspaces_path,
        "calls": calls_path,
        "approval_packet": approval_packet_path,
        "approval_run_dir": fixture_dir / "approval",
        "wrong_target_run_dir": wrong_target_dir,
        "wrong_target_herdr_bin": wrong_target_herdr_bin,
        "wrong_target_workspaces": wrong_target_workspaces_path,
        "wrong_target_calls": wrong_target_calls_path,
        "wrong_target_approval_packet": wrong_target_approval_packet_path,
        "wrong_target_approval_run_dir": wrong_target_dir / "approval",
    }


def herdr_gc_apply_with_approval_command(
    *,
    uv_tau: list[str],
    fixture_dir: Path,
    herdr_bin: Path,
    approval_packet_path: Path,
    approval_run_dir: Path,
    receipt_path: Path,
) -> str:
    return f"""
import json
import subprocess
import sys
from pathlib import Path

uv_tau = {uv_tau!r}
fixture_dir = Path({str(fixture_dir)!r})
herdr_bin = Path({str(herdr_bin)!r})
approval_packet_path = Path({str(approval_packet_path)!r})
approval_run_dir = Path({str(approval_run_dir)!r})
receipt_path = Path({str(receipt_path)!r})
approval_receipt = approval_run_dir / "approval-gate-receipt.json"


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


run([
    *uv_tau,
    "approval-gate-check",
    "--approval-packet",
    str(approval_packet_path),
    "--requested-action",
    "herdr_gc_apply",
    "--run-dir",
    str(approval_run_dir),
], 0)
completed = run([
    *uv_tau,
    "herdr-cleanup",
    "gc",
    "--run-dir",
    str(fixture_dir),
    "--apply",
    "--approval-receipt",
    str(approval_receipt),
    "--herdr-bin",
    str(herdr_bin),
], 0)
payload = json.loads(receipt_path.read_text(encoding="utf-8"))
if payload.get("applied_action_count") != 1:
    raise SystemExit("expected exactly one applied action")
if payload.get("post_verified_absent_count") != 1:
    raise SystemExit("expected exactly one post-verified absent workspace")
raise SystemExit(completed.returncode)
"""


def herdr_gc_apply_wrong_target_command(
    *,
    uv_tau: list[str],
    fixture_dir: Path,
    herdr_bin: Path,
    approval_packet_path: Path,
    approval_run_dir: Path,
    receipt_path: Path,
) -> str:
    return f"""
import json
import subprocess
import sys
from pathlib import Path

uv_tau = {uv_tau!r}
fixture_dir = Path({str(fixture_dir)!r})
herdr_bin = Path({str(herdr_bin)!r})
approval_packet_path = Path({str(approval_packet_path)!r})
approval_run_dir = Path({str(approval_run_dir)!r})
receipt_path = Path({str(receipt_path)!r})
approval_receipt = approval_run_dir / "approval-gate-receipt.json"


def run(command, expected_exit):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != expected_exit:
        raise SystemExit(completed.returncode)
    return completed


run([
    *uv_tau,
    "approval-gate-check",
    "--approval-packet",
    str(approval_packet_path),
    "--requested-action",
    "herdr_gc_apply",
    "--run-dir",
    str(approval_run_dir),
], 0)
completed = run([
    *uv_tau,
    "herdr-cleanup",
    "gc",
    "--run-dir",
    str(fixture_dir),
    "--apply",
    "--approval-receipt",
    str(approval_receipt),
    "--herdr-bin",
    str(herdr_bin),
], 1)
payload = json.loads(receipt_path.read_text(encoding="utf-8"))
codes = [alert.get("code") for alert in payload.get("alerts", [])]
if "approval_target_mismatch" not in codes:
    raise SystemExit(f"expected approval_target_mismatch, got {{codes}}")
if payload.get("applied_actions"):
    raise SystemExit("expected no applied actions for wrong approval target")
raise SystemExit(completed.returncode)
"""


def create_orchestration_evidence_status_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-orchestration-evidence"
    write_json(
        fixture_dir / "orchestration-evidence-receipt.json",
        {
            "schema": "tau.orchestration_evidence_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "execution": "real_world_sanity_static_orchestration_evidence_status_fixture",
            "run_id": "rw-sanity-orchestration-evidence",
            "source_run_dir": str(fixture_dir),
            "feature_counts": {
                "agent_lineage": 4,
                "execution_timeline": 2,
                "provider_capabilities": 2,
                "worktree_session_bindings": 4,
                "review_comments": 1,
                "agent_messages": 1,
                "doctor": 1,
            },
            "errors": [],
        },
    )
    return {"run_dir": fixture_dir}


def create_provider_lifecycle_status_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-provider-lifecycle"
    readiness_dir = fixture_dir / "readiness"
    codex_state = readiness_dir / "codex.session-state.json"
    opencode_state = readiness_dir / "opencode.session-state.json"
    write_json(
        codex_state,
        {
            "schema": "tau.provider_session_state.v1",
            "provider_id": "codex",
            "workspace_id": "w-rw-sanity-lifecycle",
            "pane_id": "w-rw-sanity-lifecycle:p5",
            "terminal_id": "term-codex",
            "state": "ready",
            "ready": True,
            "source": "real_world_sanity_static_provider_lifecycle_fixture",
            "process": {"alive": True, "command": "codex"},
            "evidence": {"visible_log_path": str(fixture_dir / "logs/codex.visible.txt")},
        },
    )
    write_json(
        opencode_state,
        {
            "schema": "tau.provider_session_state.v1",
            "provider_id": "opencode",
            "workspace_id": "w-rw-sanity-lifecycle",
            "pane_id": "w-rw-sanity-lifecycle:p6",
            "terminal_id": "term-opencode",
            "state": "auth_required",
            "ready": False,
            "source": "real_world_sanity_static_provider_lifecycle_fixture",
            "process": {"alive": True, "command": "opencode"},
            "evidence": {"visible_log_path": str(fixture_dir / "logs/opencode.visible.txt")},
        },
    )
    write_json(
        fixture_dir / "runtime-manifest.json",
        {
            "schema": "tau.provider_readiness_runtime_manifest.v1",
            "run_id": "rw-sanity-provider-lifecycle",
            "provider_session_states": [str(codex_state), str(opencode_state)],
        },
    )
    write_json(
        fixture_dir / "run-receipt.json",
        {
            "schema": "tau.provider_readiness_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "run_id": "rw-sanity-provider-lifecycle",
            "all_provider_structured_ready": False,
        },
    )
    return {"run_dir": fixture_dir}


def _provider_lifecycle_crashed_ready_probe_code() -> str:
    return (
        "import json; "
        "from tau_coding.provider_lifecycle import build_provider_session_state; "
        "readiness = {"
        "'schema':'tau.provider_readiness.v1',"
        "'run_id':'rw-sanity-provider-lifecycle-crashed-ready',"
        "'provider_id':'codex',"
        "'workspace_id':'w-rw-sanity-lifecycle',"
        "'pane_id':'w-rw-sanity-lifecycle:p5',"
        "'terminal_id':'term-codex',"
        "'state':'ready',"
        "'ready':True,"
        "'source':'real_world_sanity_lifecycle_probe',"
        "'evidence':{"
        "'process_alive':False,"
        "'foreground_command':'',"
        "'visible_log_path':'/tmp/codex.visible.txt'"
        "},"
        "'diagnostics':{"
        "'visible_prompt_observed':True,"
        "'visible_prompt_is_gate':False,"
        "'interstitial_visible':False"
        "}"
        "}; "
        "state = build_provider_session_state(readiness); "
        "passed = state.get('state') == 'crashed' and state.get('ready') is False; "
        "print(json.dumps({"
        "'schema':'tau.provider_lifecycle_probe_receipt.v1',"
        "'ok':passed,"
        "'status':'PASS' if passed else 'BLOCKED',"
        "'mocked':False,"
        "'live':False,"
        "'normalized_state':state.get('state'),"
        "'ready':state.get('ready'),"
        "'errors':[] if passed else ['dead ready provider was not normalized as crashed']"
        "}, sort_keys=True)); "
        "raise SystemExit(0 if passed else 1)"
    )


def create_provider_readiness_status_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-provider-readiness-status"
    readiness_dir = fixture_dir / "readiness"
    codex_readiness = readiness_dir / "codex.readiness.json"
    opencode_readiness = readiness_dir / "opencode.readiness.json"
    codex_state = readiness_dir / "codex.session-state.json"
    opencode_state = readiness_dir / "opencode.session-state.json"
    write_json(
        codex_readiness,
        {
            "schema": "tau.provider_readiness.v1",
            "provider_id": "codex",
            "workspace_id": "w-rw-sanity-readiness",
            "pane_id": "w-rw-sanity-readiness:p5",
            "terminal_id": "term-codex",
            "state": "ready",
            "ready": True,
            "source": "real_world_sanity_static_provider_readiness_fixture",
            "diagnostics": {
                "visible_prompt_observed": True,
                "visible_prompt_is_gate": False,
            },
            "evidence": {
                "visible_log_path": str(fixture_dir / "logs/codex.visible.txt"),
                "provider_readiness_path": str(codex_readiness),
                "provider_session_state_path": str(codex_state),
            },
        },
    )
    write_json(
        opencode_readiness,
        {
            "schema": "tau.provider_readiness.v1",
            "provider_id": "opencode",
            "workspace_id": "w-rw-sanity-readiness",
            "pane_id": "w-rw-sanity-readiness:p6",
            "terminal_id": "term-opencode",
            "state": "ready",
            "ready": True,
            "source": "real_world_sanity_static_provider_readiness_fixture",
            "diagnostics": {
                "visible_prompt_observed": True,
                "visible_prompt_is_gate": False,
            },
            "evidence": {
                "visible_log_path": str(fixture_dir / "logs/opencode.visible.txt"),
                "provider_readiness_path": str(opencode_readiness),
                "provider_session_state_path": str(opencode_state),
            },
        },
    )
    for state_path, provider_id, pane_id, command in (
        (codex_state, "codex", "w-rw-sanity-readiness:p5", "codex"),
        (opencode_state, "opencode", "w-rw-sanity-readiness:p6", "opencode"),
    ):
        write_json(
            state_path,
            {
                "schema": "tau.provider_session_state.v1",
                "provider_id": provider_id,
                "workspace_id": "w-rw-sanity-readiness",
                "pane_id": pane_id,
                "terminal_id": f"term-{provider_id}",
                "state": "ready",
                "ready": True,
                "source": "real_world_sanity_static_provider_readiness_fixture",
                "process": {"alive": True, "command": command},
                "evidence": {
                    "visible_log_path": str(fixture_dir / f"logs/{provider_id}.visible.txt")
                },
            },
        )
    write_text(
        fixture_dir / "events.jsonl",
        json.dumps(
            {
                "schema": "tau.provider_pane_event.v1",
                "kind": "provider_readiness_recorded",
                "run_id": "rw-sanity-provider-readiness-status",
            },
            sort_keys=True,
        )
        + "\n",
    )
    write_json(
        fixture_dir / "runtime-manifest.json",
        {
            "schema": "tau.provider_readiness_runtime_manifest.v1",
            "run_id": "rw-sanity-provider-readiness-status",
            "events_jsonl": str(fixture_dir / "events.jsonl"),
            "readiness_records": [str(codex_readiness), str(opencode_readiness)],
            "provider_session_states": [str(codex_state), str(opencode_state)],
            "workstation_manifest": str(fixture_dir / "workstation.json"),
            "inspect_path": str(fixture_dir / "inspect.json"),
        },
    )
    write_json(
        fixture_dir / "run-receipt.json",
        {
            "schema": "tau.provider_readiness_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "run_id": "rw-sanity-provider-readiness-status",
            "all_provider_structured_ready": True,
        },
    )
    return {"run_dir": fixture_dir}


def create_provider_pane_status_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-provider-pane-status"
    write_text(
        fixture_dir / "events.jsonl",
        json.dumps(
            {
                "schema": "tau.provider_pane_event.v1",
                "kind": "provider_pane_settled",
                "run_id": "rw-sanity-provider-pane-status",
            },
            sort_keys=True,
        )
        + "\n",
    )
    write_json(
        fixture_dir / "runtime-manifest.json",
        {
            "schema": "tau.provider_pane_runtime_manifest.v1",
            "run_id": "rw-sanity-provider-pane-status",
            "events_jsonl": str(fixture_dir / "events.jsonl"),
            "workstation_manifest": str(fixture_dir / "workstation.json"),
            "inspect_path": str(fixture_dir / "inspect.json"),
            "providers": [
                {
                    "provider_id": "codex",
                    "role": "codex",
                    "pane_id": "w-rw-sanity-pane:p5",
                    "terminal_id": "term-codex",
                    "work_order_path": str(fixture_dir / "work-orders/codex.json"),
                    "ready_prompt_observed": True,
                    "readiness_actions": ["codex_update_prompt_skipped"],
                    "visible_log": str(fixture_dir / "logs/codex.visible.txt"),
                    "read_returncode": 0,
                },
                {
                    "provider_id": "opencode",
                    "role": "opencode",
                    "pane_id": "w-rw-sanity-pane:p6",
                    "terminal_id": "term-opencode",
                    "work_order_path": str(fixture_dir / "work-orders/opencode.json"),
                    "ready_prompt_observed": False,
                    "readiness_actions": [],
                    "visible_log": str(fixture_dir / "logs/opencode.visible.txt"),
                    "read_returncode": 0,
                },
            ],
        },
    )
    write_json(
        fixture_dir / "run-receipt.json",
        {
            "schema": "tau.provider_pane_run_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "run_id": "rw-sanity-provider-pane-status",
            "proof_scope": {
                "proves": ["provider-pane allocation artifacts can fail closed"],
                "does_not_prove": ["structured provider readiness"],
            },
        },
    )
    return {"run_dir": fixture_dir}


def create_provider_dag_status_fixture(run_dir: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "medium-provider-dag-status"
    events = fixture_dir / "events.jsonl"
    write_text(
        events,
        json.dumps(
            {
                "schema": "tau.provider_dag_event.v1",
                "kind": "coder_dispatch",
                "run_id": "rw-sanity-provider-dag-status",
            },
            sort_keys=True,
        )
        + "\n",
    )
    write_json(
        fixture_dir / "runtime-manifest.json",
        {
            "schema": "tau.provider_dag_runtime_manifest.v1",
            "run_id": "rw-sanity-provider-dag-status",
            "events_jsonl": str(events),
            "scratch_worktree": str(fixture_dir / "scratch-worktree"),
        },
    )
    write_json(
        fixture_dir / "run-receipt.json",
        {
            "schema": "tau.dag_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "run_id": "rw-sanity-provider-dag-status",
            "scratch_worktree": str(fixture_dir / "scratch-worktree"),
            "attempt_count": 1,
            "max_attempts": 2,
            "provider_sessions": {
                "codex": {
                    "role": "coder",
                    "provider_id": "codex",
                    "workspace_id": "w-rw-provider-dag",
                    "pane_id": "w-rw-provider-dag:p5",
                    "terminal_id": "term-codex",
                    "visible": True,
                    "ready": True,
                    "state": "ready",
                },
                "opencode": {
                    "role": "reviewer",
                    "provider_id": "opencode",
                    "workspace_id": "w-rw-provider-dag",
                    "pane_id": "w-rw-provider-dag:p6",
                    "terminal_id": "term-opencode",
                    "visible": True,
                    "ready": True,
                    "state": "ready",
                },
            },
            "visible_subagents": {
                "planner": {
                    "role": "planner",
                    "workspace_id": "w-rw-provider-dag",
                    "pane_id": "w-rw-provider-dag:p7",
                    "terminal_id": "term-planner",
                    "visible": True,
                },
                "orchestrator": {
                    "role": "orchestrator",
                    "workspace_id": "w-rw-provider-dag",
                    "pane_id": "w-rw-provider-dag:p8",
                    "terminal_id": "term-orchestrator",
                    "visible": True,
                },
                "coder": {
                    "role": "coder",
                    "provider_id": "codex",
                    "workspace_id": "w-rw-provider-dag",
                    "pane_id": "w-rw-provider-dag:p5",
                    "terminal_id": "term-codex",
                    "visible": True,
                },
                "reviewer": {
                    "role": "reviewer",
                    "provider_id": "opencode",
                    "workspace_id": "w-rw-provider-dag",
                    "pane_id": "w-rw-provider-dag:p6",
                    "terminal_id": "term-opencode",
                    "visible": True,
                },
            },
            "attempts": [
                {
                    "attempt": 1,
                    "coder_status": "PASS",
                    "coder_verdict": "PASS",
                    "reviewer_status": "PASS",
                    "reviewer_verdict": "PASS",
                    "errors": [],
                }
            ],
            "herdr_cleanup_receipt": str(fixture_dir / "herdr-cleanup-receipt.json"),
            "herdr_cleanup": {
                "status": "PASS",
                "mocked": False,
                "live": False,
                "mode": "dry-run",
                "candidate_count": 1,
            },
            "orchestration_evidence_receipt": str(
                fixture_dir / "orchestration-evidence-receipt.json"
            ),
            "orchestration_evidence": {
                "status": "PASS",
                "mocked": False,
                "live": False,
                "provider_live": False,
                "feature_counts": {
                    "agent_lineage": 4,
                    "provider_capabilities": 2,
                    "worktree_session_bindings": 4,
                },
            },
        },
    )
    return {"run_dir": fixture_dir}


def approval_packet(*, action: str, target_id: str, reason: str) -> dict[str, Any]:
    return {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "action": action,
        "actor": {"id": "human:graham", "auth_method": "manual"},
        "target": {"id": target_id},
        "reason": reason,
        "evidence": ["real-world-sanity approval fixture"],
        "nonce": f"real-world-sanity:{action}:{target_id}",
        "signature": "manual-real-world-sanity-fixture",
    }


def create_generic_provider_adapter_fixture(
    run_dir: Path,
    *,
    repo: Path,
    uv_tau: list[str],
    herdr_bin: str,
    receipt_timeout_seconds: int,
    provider_cleanup_mode: str,
) -> Path:
    fixture_dir = run_dir / "advanced-generic-provider-dag-adapter"
    receipt_path = fixture_dir / "receipts" / "provider-task.json"
    provider_run_root = fixture_dir / "provider-runs"
    work_order_path = fixture_dir / "work-orders" / "provider-task.json"
    adapter_work_order = {
        "schema": "tau.generic_provider_adapter_work_order.v1",
        "node_id": "provider_task",
        "purpose": (
            "Exercise generic DAG -> provider adapter -> visible provider DAG "
            "-> generic node receipt."
        ),
    }
    adapter_work_order["work_order_sha256"] = canonical_payload_sha256(adapter_work_order)
    write_json(work_order_path, adapter_work_order)
    spec_path = fixture_dir / "dag-spec.json"
    write_json(
        spec_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "rw-sanity-generic-provider-dag-adapter",
            "run_dir": str(fixture_dir),
            "events_jsonl": str(fixture_dir / "events.jsonl"),
            "nodes": [
                {
                    "node_id": "provider_task",
                    "role": "provider_dag_adapter",
                    "depends_on": [],
                    "work_order_path": str(work_order_path),
                    "receipt_path": str(receipt_path),
                    "timeout_seconds": receipt_timeout_seconds + 180,
                    "max_attempts": 1,
                    "command": [
                        *uv_tau,
                        "generic-provider-dag-node",
                        "--node-id",
                        "provider_task",
                        "--receipt-path",
                        str(receipt_path),
                        "--work-order-path",
                        str(work_order_path),
                        "--provider-run-root",
                        str(provider_run_root),
                        "--repo",
                        str(repo),
                        "--label",
                        "rw-sanity-generic-provider-dag-adapter",
                        "--max-attempts",
                        "1",
                        "--receipt-timeout-seconds",
                        str(receipt_timeout_seconds),
                        "--herdr-bin",
                        herdr_bin,
                        "--no-install-integrations",
                        "--cleanup-mode",
                        provider_cleanup_mode,
                    ],
                }
            ],
        },
    )
    return spec_path


def generic_dag_receipt_writer(receipt_path: Path, *, node_id: str) -> str:
    payload = {
        "schema": "tau.generic_dag_node_receipt.v1",
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "artifacts": [],
        "commands_run": ["python3 inline receipt writer"],
        "handoff_summary": f"{node_id} completed by real-world sanity worker",
        "errors": [],
        "policy_exceptions": [],
    }
    return (
        "import json; from pathlib import Path; "
        f"path=Path({str(receipt_path)!r}); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        f"path.write_text(json.dumps({payload!r}, sort_keys=True), encoding='utf-8')"
    )


def run_status_after_json_command(
    *,
    producer_command: list[str],
    status_command_prefix: list[str],
    run_dir_key: str,
    producer_expected_exit_codes: tuple[int, ...] = (0,),
) -> str:
    return "\n".join(
        [
            "import json, subprocess, sys",
            f"producer = {producer_command!r}",
            f"status_prefix = {status_command_prefix!r}",
            f"run_dir_key = {run_dir_key!r}",
            f"producer_expected = {producer_expected_exit_codes!r}",
            "first = subprocess.run(producer, text=True, capture_output=True)",
            "sys.stderr.write(first.stderr)",
            "if first.returncode not in producer_expected:",
            "    sys.stdout.write(first.stdout)",
            "    raise SystemExit(first.returncode)",
            "payload = json.loads(first.stdout)",
            "run_dir = payload[run_dir_key]",
            "second = subprocess.run([*status_prefix, run_dir], text=True, capture_output=True)",
            "sys.stderr.write(second.stderr)",
            "sys.stdout.write(second.stdout)",
            "raise SystemExit(second.returncode)",
        ]
    )


def rerun_json_command_with_resume(
    *,
    first_command: list[str],
    second_command: list[str],
) -> str:
    return "\n".join(
        [
            "import subprocess, sys",
            f"first = {first_command!r}",
            f"second = {second_command!r}",
            "first_result = subprocess.run(first, text=True, capture_output=True)",
            "sys.stderr.write(first_result.stderr)",
            "if first_result.returncode != 0:",
            "    sys.stdout.write(first_result.stdout)",
            "    raise SystemExit(first_result.returncode)",
            "second_result = subprocess.run(second, text=True, capture_output=True)",
            "sys.stderr.write(second_result.stderr)",
            "sys.stdout.write(second_result.stdout)",
            "raise SystemExit(second_result.returncode)",
        ]
    )


def resume_from_run_dir_json_command(
    *,
    first_command: list[str],
    resume_command_prefix: list[str],
) -> str:
    return "\n".join(
        [
            "import json, subprocess, sys",
            f"first = {first_command!r}",
            f"resume_prefix = {resume_command_prefix!r}",
            "first_result = subprocess.run(first, text=True, capture_output=True)",
            "sys.stderr.write(first_result.stderr)",
            "if first_result.returncode != 0:",
            "    sys.stdout.write(first_result.stdout)",
            "    raise SystemExit(first_result.returncode)",
            "payload = json.loads(first_result.stdout)",
            "run_dir = payload.get('run_dir')",
            "if not isinstance(run_dir, str) or not run_dir:",
            "    sys.stdout.write(first_result.stdout)",
            "    raise SystemExit('dag-run output did not include run_dir')",
            "second_result = subprocess.run([*resume_prefix, run_dir], text=True, capture_output=True)",
            "sys.stderr.write(second_result.stderr)",
            "sys.stdout.write(second_result.stdout)",
            "raise SystemExit(second_result.returncode)",
        ]
    )


def create_handoff_loop_fixture(run_dir: Path, *, repo: Path) -> dict[str, Path]:
    fixture_dir = run_dir / "simple-handoff-loop"
    agents_root = fixture_dir / "agents"
    command_spec_root = fixture_dir / "command-specs"
    receipt_dir = fixture_dir / "receipts"
    for path in (
        agents_root / "project-or-harness-verifier",
        command_spec_root / "goal-guardian",
        command_spec_root / "project-or-harness-verifier",
        receipt_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (agents_root / "project-or-harness-verifier" / "AGENTS.md").write_text(
        "---\nid: project-or-harness-verifier\n---\n",
        encoding="utf-8",
    )
    start = valid_handoff()
    start["previous_subagent"] = "human"
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Goal preservation should be checked first.",
    }
    start_path = fixture_dir / "start-handoff.json"
    write_json(start_path, start)
    write_json(
        command_spec_root / "goal-guardian" / "tau-dispatch-command.json",
        {
            "command": [
                "uv",
                "run",
                "--project",
                str(repo),
                "python",
                "-c",
                (
                    "import json; "
                    "from tau_coding.cli import "
                    "project_agent_handoff_goal_guardian_adapter_command; "
                    "print(json.dumps(project_agent_handoff_goal_guardian_adapter_command("
                    "next_agent='project-or-harness-verifier', "
                    "next_executor='local', "
                    "next_reason='Verifier should inspect preserved-goal receipt.', "
                    "required_evidence='Verifier posts the next schema-valid route.', "
                    "stop_condition='Verifier route is posted.'"
                    ")))"
                ),
            ],
            "timeout_s": 10,
        },
    )
    write_json(
        command_spec_root / "project-or-harness-verifier" / "tau-dispatch-command.json",
        {
            "command": [
                "uv",
                "run",
                "--project",
                str(repo),
                "python",
                "-c",
                (
                    "import json; "
                    "from tau_coding.cli import project_agent_handoff_adapter_command; "
                    "print(json.dumps(project_agent_handoff_adapter_command("
                    "result_status='COMPLETED', "
                    "result_summary='Verifier adapter consumed the guardian handoff.', "
                    "next_agent='human', "
                    "next_executor='human', "
                    "next_reason='Human should decide the next bounded step.', "
                    "required_evidence='Human posts the next schema-valid route.', "
                    "stop_condition='Human route is posted.'"
                    ")))"
                ),
            ],
            "timeout_s": 10,
        },
    )
    return {
        "agents_root": agents_root,
        "command_spec_root": command_spec_root,
        "receipt_dir": receipt_dir,
        "start": start_path,
    }


def valid_handoff() -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "local-sanity"},
        "goal": {
            "goal_id": "goal-real-world-sanity",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": "coder",
        "context": {
            "summary": "Real-world sanity runner created a bounded handoff.",
            "artifacts": [],
        },
        "result": {
            "status": "COMPLETED",
            "summary": "The route is ready for the next local agent.",
            "evidence": [],
        },
        "rationale": "The next local agent should inspect the receipt.",
        "next_agent": {
            "name": "reviewer",
            "executor": "either",
            "reason": "Reviewer should inspect evidence before routing onward.",
        },
        "required_evidence": ["schema-valid handoff response"],
        "stop_condition": "Next agent posts a schema-valid route.",
    }


def command_spec_probe(repo: Path) -> str:
    roles = ("planner", "orchestrator", "coder", "reviewer")
    return (
        "import json; from pathlib import Path; "
        f"root=Path({str(repo / 'experiments/goal-locked-subagents/agent-command-specs')!r}); "
        f"roles={roles!r}; missing=[]; specs=[]; "
        "\nfor role in roles:\n"
        "    path=root / role / 'tau-dispatch-command.json'\n"
        "    if not path.exists(): missing.append(str(path)); continue\n"
        "    payload=json.loads(path.read_text(encoding='utf-8'))\n"
        "    if not isinstance(payload.get('command'), list): missing.append(str(path)+': command missing')\n"
        "    specs.append({'role': role, 'path': str(path), 'command': payload.get('command')})\n"
        "receipt={'schema':'tau.real_world_command_spec_catalog_check.v1','ok':not missing,"
        "'status':'PASS' if not missing else 'BLOCKED','mocked':False,'live':False,"
        "'checked_roles':roles,'specs':specs,'errors':missing}\n"
        "print(json.dumps(receipt, sort_keys=True))\n"
        "raise SystemExit(0 if receipt['ok'] else 1)\n"
    )


def run_check(check: Check, *, repo: Path, run_dir: Path) -> dict[str, Any]:
    started = utc_stamp()
    attempt_records: list[dict[str, Any]] = []
    payload: dict[str, Any] | None = None
    errors: list[str] = []
    exit_code = 1
    for attempt in range(1, check.attempts + 1):
        attempt_record = run_check_attempt(
            check=check,
            repo=repo,
            run_dir=run_dir,
            attempt=attempt,
        )
        attempt_records.append(attempt_record)
        payload = (
            attempt_record["payload"] if isinstance(attempt_record.get("payload"), dict) else None
        )
        errors = list(attempt_record["errors"])
        exit_code = int(attempt_record["exit_code"])
        if not errors:
            break

    output_receipt_path = check.output_receipt
    last_attempt = attempt_records[-1]
    cleanup_record: dict[str, Any] | None = None
    if not errors and check.post_cleanup_mode != "off":
        cleanup_record = run_post_check_cleanup(
            check=check,
            repo=repo,
            run_dir=run_dir,
            payload=payload,
        )
        if cleanup_record["status"] != "PASS":
            errors.extend(cleanup_record["errors"])
    status = "PASS" if not errors else "BLOCKED"
    record = {
        "schema": CHECK_SCHEMA,
        "check_id": check.check_id,
        "level": check.level,
        "purpose": check.purpose,
        "status": status,
        "ok": status == "PASS",
        "mocked": False,
        "live": receipt_live_value(payload),
        "provider_live": receipt_provider_live_value(check, payload),
        "started_at": started,
        "completed_at": utc_stamp(),
        "timeout_seconds": check.timeout_seconds,
        "attempt_count": len(attempt_records),
        "max_attempts": check.attempts,
        "command": check.command,
        "exit_code": exit_code,
        "expected_exit_codes": list(check.expected_exit_codes),
        "expected_status": check.expected_status,
        "expected_verdict": check.expected_verdict,
        "expected_min_provider_session_states": check.expected_min_provider_session_states,
        "stdout_path": last_attempt["stdout_path"],
        "stderr_path": last_attempt["stderr_path"],
        "output_receipt_path": str(output_receipt_path) if output_receipt_path else None,
        "receipt_summary": summarize_receipt(payload),
        "post_cleanup": cleanup_record,
        "attempts": [
            {
                key: value
                for key, value in attempt.items()
                if key not in {"payload", "stdout", "stderr"}
            }
            for attempt in attempt_records
        ],
        "errors": errors,
    }
    write_json(run_dir / "checks" / f"{check.check_id}.json", record)
    return record


def run_post_check_cleanup(
    *,
    check: Check,
    repo: Path,
    run_dir: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "status": "BLOCKED",
            "ok": False,
            "mode": check.post_cleanup_mode,
            "errors": ["cannot run cleanup without a JSON receipt payload"],
        }
    provider_run_dir_value = payload.get("run_dir")
    if not isinstance(provider_run_dir_value, str) or not provider_run_dir_value:
        return {
            "status": "BLOCKED",
            "ok": False,
            "mode": check.post_cleanup_mode,
            "errors": ["cannot run cleanup because receipt has no run_dir"],
        }
    provider_run_dir = Path(provider_run_dir_value).expanduser()
    if not provider_run_dir.is_absolute():
        provider_run_dir = repo / provider_run_dir
    cleanup_stdout = run_dir / "logs" / f"{check.check_id}.cleanup.stdout.txt"
    cleanup_stderr = run_dir / "logs" / f"{check.check_id}.cleanup.stderr.txt"
    workspace_lease = None
    if check.post_cleanup_mode == "apply":
        workspace_lease = write_post_cleanup_workspace_lease(provider_run_dir, cleanup_mode="apply")
    command = [
        check.post_cleanup_uv_bin,
        "run",
        "--project",
        str(repo),
        "tau",
        "herdr-cleanup",
        check.post_cleanup_mode,
        "--run-dir",
        str(provider_run_dir),
        "--herdr-bin",
        check.post_cleanup_herdr_bin,
    ]
    if workspace_lease is not None:
        command.extend(["--workspace-lease", str(workspace_lease)])
    completed = subprocess.run(
        command,
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    write_text(cleanup_stdout, completed.stdout)
    write_text(cleanup_stderr, completed.stderr)
    parsed = parse_json_payload(completed.stdout)
    errors: list[str] = []
    if completed.returncode != 0:
        errors.append(f"cleanup exit_code {completed.returncode}")
    if not isinstance(parsed, dict):
        errors.append("cleanup did not emit a JSON receipt")
    elif parsed.get("status") != "PASS":
        errors.append(f"cleanup status {parsed.get('status')!r}, expected 'PASS'")
    return {
        "schema": "tau.real_world_sanity_post_cleanup.v1",
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": parsed.get("live") if isinstance(parsed, dict) else False,
        "mode": check.post_cleanup_mode,
        "herdr_surface": parsed.get("herdr_surface") if isinstance(parsed, dict) else None,
        "command": command,
        "exit_code": completed.returncode,
        "run_dir": str(provider_run_dir),
        "stdout_path": str(cleanup_stdout),
        "stderr_path": str(cleanup_stderr),
        "receipt_summary": summarize_receipt(parsed),
        "receipt_path": str(provider_run_dir / "herdr-cleanup-receipt.json"),
        "errors": errors,
    }


def write_post_cleanup_workspace_lease(provider_run_dir: Path, *, cleanup_mode: str) -> Path:
    manifest_path = provider_run_dir / "runtime-manifest.json"
    manifest = read_json(manifest_path)
    workspace_ids = sorted(_cleanup_workspace_ids(manifest))
    now = datetime.now(UTC).replace(microsecond=0)
    lease = {
        "schema": "tau.herdr_workspace_lease.v1",
        "run_id": manifest.get("run_id"),
        "dag_id": manifest.get("label") or manifest.get("run_id"),
        "owner": "tau-real-world-sanity",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "cleanup_policy": cleanup_mode,
        "workspace_ids": workspace_ids,
        "source_runtime_manifest": str(manifest_path),
    }
    lease_path = provider_run_dir / "real-world-sanity-herdr-workspace-lease.json"
    write_json(lease_path, lease)
    return lease_path


def _cleanup_workspace_ids(manifest: dict[str, Any]) -> set[str]:
    workspace_ids: set[str] = set()
    for records_key in ("provider_sessions", "visible_subagents"):
        records = manifest.get(records_key)
        if not isinstance(records, dict):
            continue
        for record in records.values():
            if isinstance(record, dict) and record.get("workspace_id"):
                workspace_ids.add(str(record["workspace_id"]))
    for path_text in manifest.get("provider_session_states", []):
        if not isinstance(path_text, str):
            continue
        try:
            record = read_json(Path(path_text))
        except OSError:
            continue
        except json.JSONDecodeError:
            continue
        if record.get("workspace_id"):
            workspace_ids.add(str(record["workspace_id"]))
    return workspace_ids


def run_check_attempt(
    *,
    check: Check,
    repo: Path,
    run_dir: Path,
    attempt: int,
) -> dict[str, Any]:
    timed_out = False
    try:
        completed = subprocess.run(
            check.command,
            cwd=repo,
            text=True,
            capture_output=True,
            timeout=check.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = 124
    else:
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode

    parsed = parse_json_payload(stdout)
    output_receipt_payload = None
    if check.output_receipt and check.output_receipt.exists():
        output_receipt_payload = read_json(check.output_receipt)
    payload = output_receipt_payload or parsed
    errors = check_payload_errors(
        check=check,
        payload=payload,
        exit_code=exit_code,
        timed_out=timed_out,
    )
    suffix = f".attempt-{attempt:02d}" if check.attempts > 1 else ""
    stdout_path = write_text(run_dir / "logs" / f"{check.check_id}{suffix}.stdout.txt", stdout)
    stderr_path = write_text(run_dir / "logs" / f"{check.check_id}{suffix}.stderr.txt", stderr)
    return {
        "attempt": attempt,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "payload": payload,
        "receipt_summary": summarize_receipt(payload),
        "errors": errors,
    }


def check_payload_errors(
    *,
    check: Check,
    payload: dict[str, Any] | None,
    exit_code: int,
    timed_out: bool,
) -> list[str]:
    errors: list[str] = []
    if timed_out:
        errors.append(f"timed out after {check.timeout_seconds}s")
    if exit_code not in check.expected_exit_codes:
        errors.append(f"exit_code {exit_code} not in expected {list(check.expected_exit_codes)}")
    if check.require_json_receipt and not isinstance(payload, dict):
        errors.append("no JSON receipt found in stdout or expected output receipt path")
    if isinstance(payload, dict):
        mocked = payload.get("mocked")
        if check.require_mocked_false and mocked is not False:
            errors.append(f"receipt mocked field is {mocked!r}, expected false")
        if check.expected_status is not None and payload.get("status") != check.expected_status:
            errors.append(
                f"receipt status {payload.get('status')!r}, expected {check.expected_status!r}"
            )
        if check.expected_verdict is not None and payload.get("verdict") != check.expected_verdict:
            errors.append(
                f"receipt verdict {payload.get('verdict')!r}, expected {check.expected_verdict!r}"
            )
        if (
            check.expected_provider_live is not None
            and payload.get("provider_live") is not check.expected_provider_live
        ):
            errors.append(
                "receipt provider_live "
                f"{payload.get('provider_live')!r}, expected {check.expected_provider_live!r}"
            )
        if check.expected_min_provider_session_states:
            states = payload.get("provider_session_states")
            state_count = len(states) if isinstance(states, list) else 0
            if state_count < check.expected_min_provider_session_states:
                errors.append(
                    "provider_session_states count "
                    f"{state_count}, expected at least "
                    f"{check.expected_min_provider_session_states}"
                )
        if check.expected_min_resumed_nodes:
            nodes = payload.get("nodes")
            resumed_count = (
                len(
                    [
                        node
                        for node in nodes
                        if isinstance(node, dict) and node.get("resumed") is True
                    ]
                )
                if isinstance(nodes, list)
                else 0
            )
            if resumed_count < check.expected_min_resumed_nodes:
                errors.append(
                    f"resumed node count {resumed_count}, expected at least "
                    f"{check.expected_min_resumed_nodes}"
                )
    return errors


def write_suite_receipt(
    *,
    repo: Path,
    run_dir: Path,
    run_id: str,
    records: list[dict[str, Any]],
    selected_levels: list[str],
    complete: bool,
) -> dict[str, Any]:
    level_counts: dict[str, dict[str, int]] = {}
    for record in records:
        level = str(record["level"])
        status = str(record["status"])
        level_counts.setdefault(level, {})
        level_counts[level][status] = level_counts[level].get(status, 0) + 1
    failed = [record for record in records if record.get("ok") is not True]
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "ok": complete and not failed,
        "status": "PASS" if complete and not failed else "BLOCKED" if failed else "RUNNING",
        "mocked": False,
        "live": "mixed",
        "provider_live": any(record.get("provider_live") is True for record in records),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "repo": str(repo),
        "selected_levels": selected_levels,
        "check_count": len(records),
        "failed_check_count": len(failed),
        "level_counts": level_counts,
        "checks": records,
        "completed_at": utc_stamp() if complete else None,
        "proof_scope": {
            "proves": [
                "Tau CLI and command-spec surfaces execute from the local checkout",
                "Tau can run a real local handoff command loop",
                "Tau planner, generic DAG runner, and deterministic scheduler stress surfaces emit non-mocked receipts",
                "Tau can allocate visible provider panes and run live provider DAG checks when advanced checks pass",
                "Tau can run Surf-backed browser proof checks when advanced browser checks pass",
                "Tau fail-closed negative controls preserve typed BLOCKED receipts",
            ],
            "does_not_prove": [
                "GitHub ticket closure",
                "remote Tailscale monitoring",
                "production browser/chat UI rendering",
                "production repository mutation",
            ],
        },
        "timestamp": utc_stamp(),
    }
    write_json(run_dir / "real-world-sanity-receipt.json", receipt)
    return receipt


def parse_json_payload(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    for start in (stripped.find("{"), stripped.find("[")):
        if start < 0:
            continue
        candidate = stripped[start:]
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def summarize_receipt(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    keys = (
        "schema",
        "ok",
        "status",
        "mocked",
        "live",
        "provider_live",
        "verdict",
        "spec_path",
        "resume_requested",
        "resume_source",
        "attempt_count",
        "node_count",
        "completed_node_count",
        "rung_count",
        "suite_count",
        "total_rungs",
        "feature_counts",
        "item_count",
        "manifest_sha256",
        "required_lock_count",
        "provided_lock_count",
        "provider_session_state_count",
        "scheduler",
        "max_concurrency",
        "max_observed_concurrency",
        "execution_seconds",
        "herdr_bin",
        "herdr_surface",
        "workspace_count",
        "resource_count",
        "candidate_count",
        "runtime_manifest",
        "runtime_manifest_sha256",
        "requested_action",
        "approved",
        "approval_packet_sha256",
        "packet_summary",
        "normalized_state",
        "ready",
        "selected_agents",
        "observed_edges",
        "node_attempts",
        "reviewer_verdicts",
        "memory_sync",
        "sync_status",
        "projected_document_count",
        "source_packet_sha256",
        "source_type",
        "method",
        "classification",
        "source_count",
        "arxiv_source_count",
        "review_required",
        "actions",
        "requirements",
        "preflight_ready",
        "dry_run",
        "applied",
        "target",
        "commands",
        "command_results",
        "preflight_results",
        "indexed_receipt_count",
        "output_path",
        "output_sha256",
        "error_count",
        "warning_count",
        "schema_counts",
        "status_counts",
        "dag_error",
        "errors",
    )
    summary = {key: payload.get(key) for key in keys if key in payload}
    alerts = payload.get("alerts")
    if isinstance(alerts, list):
        summary["alert_count"] = len(alerts)
        summary["alert_codes"] = [
            item.get("code") for item in alerts if isinstance(item, dict) and item.get("code")
        ]
    course_corrections = payload.get("course_correction_artifacts")
    if isinstance(course_corrections, list):
        summary["course_correction_artifact_count"] = len(course_corrections)
        summary["course_correction_artifacts"] = [
            str(item) for item in course_corrections if isinstance(item, str)
        ]
    applied_actions = payload.get("applied_actions")
    if isinstance(applied_actions, list):
        summary["applied_action_count"] = len(applied_actions)
        summary["post_verified_absent_count"] = _post_verified_absent_count(applied_actions)
    states = payload.get("provider_session_states")
    if isinstance(states, list):
        summary["provider_session_state_count"] = len(states)
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        summary["node_count"] = len(nodes)
        summary["resumed_node_count"] = len(
            [node for node in nodes if isinstance(node, dict) and node.get("resumed") is True]
        )
        summary["dispatched_node_count"] = len(
            [
                node
                for node in nodes
                if isinstance(node, dict) and int(node.get("attempt_count") or 0) > 0
            ]
        )
        summary["blocked_node_count"] = len(
            [
                node
                for node in nodes
                if isinstance(node, dict) and str(node.get("status") or "").upper() == "BLOCKED"
            ]
        )
        summary["node_attempt_counts"] = {
            str(node.get("node_id")): node.get("attempt_count")
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("node_id"), str)
            and isinstance(node.get("attempt_count"), int)
        }
        summary["node_statuses"] = {
            str(node.get("node_id")): node.get("status")
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("node_id"), str)
            and isinstance(node.get("status"), str)
        }
        summary["node_verdicts"] = {
            str(node.get("node_id")): node.get("verdict")
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("node_id"), str)
            and isinstance(node.get("verdict"), str)
        }
        summary["node_error_counts"] = {
            str(node.get("node_id")): len(node.get("errors"))
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("node_id"), str)
            and isinstance(node.get("errors"), list)
        }
        node_durations = {
            str(node.get("node_id")): node.get("duration_seconds")
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("node_id"), str)
            and isinstance(node.get("duration_seconds"), int | float)
        }
        if node_durations:
            durations = [float(value) for value in node_durations.values()]
            summary["timed_node_count"] = len(node_durations)
            summary["node_duration_seconds_total"] = round(sum(durations), 3)
            summary["node_duration_seconds_max"] = round(max(durations), 3)
            summary["node_durations_seconds"] = node_durations
    cleanup = payload.get("herdr_cleanup")
    if isinstance(cleanup, dict):
        summary["herdr_cleanup"] = {
            key: cleanup.get(key)
            for key in (
                "mode",
                "status",
                "ok",
                "mocked",
                "live",
                "herdr_surface",
                "runtime_manifest",
                "runtime_manifest_sha256",
                "resource_count",
                "candidate_count",
                "applied_action_count",
                "post_verified_absent_count",
                "receipt_path",
            )
            if key in cleanup
        }
    return summary


def _post_verified_absent_count(value: list[Any]) -> int:
    return sum(
        1 for item in value if isinstance(item, dict) and item.get("post_verified_absent") is True
    )


def receipt_live_value(payload: dict[str, Any] | None) -> Any:
    if isinstance(payload, dict) and "live" in payload:
        return payload["live"]
    return "command"


def receipt_provider_live_value(check: Check, payload: dict[str, Any] | None) -> Any:
    if isinstance(payload, dict) and "provider_live" in payload:
        return payload["provider_live"]
    if check.check_id.startswith("advanced.provider_"):
        return True
    return False


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_gate_receipt(
    path: Path,
    *,
    schema: str,
    goal_hash: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "schema": schema,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "goal_hash": goal_hash,
        "receipt_path": str(path),
    }
    if extra:
        payload.update(extra)
    return write_json(path, payload)


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("work_order_sha256", None)
    data = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def compact_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def slug(value: str) -> str:
    chars = [ch.lower() if ch.isalnum() else "-" for ch in value.strip()]
    return "-".join(part for part in "".join(chars).split("-") if part)[:80] or "sanity"


if __name__ == "__main__":
    raise SystemExit(main())
