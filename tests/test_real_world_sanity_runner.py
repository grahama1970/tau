import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def test_summarize_receipt_includes_provider_dag_cleanup_summary() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.dag_run_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "verdict": "PASS",
            "herdr_cleanup": {
                "mode": "apply",
                "status": "PASS",
                "ok": True,
                "mocked": False,
                "live": True,
                "herdr_surface": "real",
                "runtime_manifest": "/tmp/run/runtime-manifest.json",
                "runtime_manifest_sha256": "manifest-sha-test",
                "resource_count": 1,
                "candidate_count": 1,
                "applied_action_count": 1,
                "post_verified_absent_count": 1,
                "receipt_path": "/tmp/run/herdr-cleanup-receipt.json",
                "resources": [{"workspace_id": "w1"}],
            },
        }
    )

    assert summary["herdr_cleanup"] == {
        "mode": "apply",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "herdr_surface": "real",
        "runtime_manifest": "/tmp/run/runtime-manifest.json",
        "runtime_manifest_sha256": "manifest-sha-test",
        "resource_count": 1,
        "candidate_count": 1,
        "applied_action_count": 1,
        "post_verified_absent_count": 1,
        "receipt_path": "/tmp/run/herdr-cleanup-receipt.json",
    }


def test_summarize_receipt_includes_cleanup_apply_absence_counts() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.herdr_cleanup_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "runtime_manifest": "/tmp/run/runtime-manifest.json",
            "runtime_manifest_sha256": "manifest-sha-test",
            "resource_count": 1,
            "candidate_count": 1,
            "applied_actions": [
                {
                    "workspace_id": "w1",
                    "applied": True,
                    "post_verified_absent": True,
                }
            ],
        }
    )

    assert summary == {
        "schema": "tau.herdr_cleanup_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "runtime_manifest": "/tmp/run/runtime-manifest.json",
        "runtime_manifest_sha256": "manifest-sha-test",
        "resource_count": 1,
        "candidate_count": 1,
        "applied_action_count": 1,
        "post_verified_absent_count": 1,
    }


def test_summarize_receipt_includes_herdr_gc_surface_boundary() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.herdr_gc_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": False,
            "mode": "apply",
            "herdr_bin": "/tmp/fake-herdr",
            "herdr_surface": "fixture",
            "workspace_count": 1,
            "candidate_count": 1,
            "applied_actions": [
                {
                    "workspace_id": "w1",
                    "applied": True,
                    "post_verified_absent": True,
                }
            ],
        }
    )

    assert summary == {
        "schema": "tau.herdr_gc_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "herdr_bin": "/tmp/fake-herdr",
        "herdr_surface": "fixture",
        "workspace_count": 1,
        "candidate_count": 1,
        "applied_action_count": 1,
        "post_verified_absent_count": 1,
    }


def test_summarize_receipt_includes_generic_dag_node_timing() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.generic_dag_run_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": True,
            "spec_path": "/tmp/dag-spec.json",
            "resume_requested": True,
            "resume_source": {
                "mode": "run_metadata",
                "run_dir": "/tmp/run",
                "metadata_path": "/tmp/run/current-state.json",
                "spec_path": "/tmp/dag-spec.json",
            },
            "nodes": [
                {
                    "node_id": "provider_task",
                    "status": "PASS",
                    "verdict": "PASS",
                    "attempt_count": 1,
                    "resumed": False,
                    "duration_seconds": 12.3456,
                    "errors": [],
                },
                {
                    "node_id": "review",
                    "status": "BLOCKED",
                    "verdict": "SUBAGENT_TIMEOUT",
                    "attempt_count": 2,
                    "resumed": True,
                    "duration_seconds": 0.25,
                    "errors": ["timed out"],
                },
            ],
        }
    )

    assert summary["node_count"] == 2
    assert summary["spec_path"] == "/tmp/dag-spec.json"
    assert summary["resume_requested"] is True
    assert summary["resume_source"] == {
        "mode": "run_metadata",
        "run_dir": "/tmp/run",
        "metadata_path": "/tmp/run/current-state.json",
        "spec_path": "/tmp/dag-spec.json",
    }
    assert summary["resumed_node_count"] == 1
    assert summary["dispatched_node_count"] == 2
    assert summary["blocked_node_count"] == 1
    assert summary["node_attempt_counts"] == {
        "provider_task": 1,
        "review": 2,
    }
    assert summary["node_statuses"] == {
        "provider_task": "PASS",
        "review": "BLOCKED",
    }
    assert summary["node_verdicts"] == {
        "provider_task": "PASS",
        "review": "SUBAGENT_TIMEOUT",
    }
    assert summary["node_error_counts"] == {
        "provider_task": 0,
        "review": 1,
    }
    assert summary["timed_node_count"] == 2
    assert summary["node_duration_seconds_total"] == 12.596
    assert summary["node_duration_seconds_max"] == 12.346
    assert summary["node_durations_seconds"] == {
        "provider_task": 12.3456,
        "review": 0.25,
    }


def test_summarize_receipt_includes_github_apply_policy_fields() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.github_apply_policy_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": False,
            "provider_live": False,
            "actions": ["comment", "label"],
            "requirements": {
                "approval_packet": True,
                "preflight": True,
                "redaction": True,
            },
            "preflight_ready": True,
            "errors": [],
        }
    )

    assert summary == {
        "schema": "tau.github_apply_policy_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "actions": ["comment", "label"],
        "requirements": {
            "approval_packet": True,
            "preflight": True,
            "redaction": True,
        },
        "preflight_ready": True,
        "errors": [],
    }


def test_summarize_receipt_includes_github_transport_fields() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.github_handoff_transport_receipt.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dry_run": False,
            "applied": False,
            "target": {"repo": "grahama1970/tau", "target": "issue#47"},
            "commands": [],
            "command_results": [],
            "preflight_results": [],
            "errors": [
                "GitHub --apply requires --github-apply-policy-receipt with a PASS receipt."
            ],
        }
    )

    assert summary == {
        "schema": "tau.github_handoff_transport_receipt.v1",
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "dry_run": False,
        "applied": False,
        "target": {"repo": "grahama1970/tau", "target": "issue#47"},
        "commands": [],
        "command_results": [],
        "preflight_results": [],
        "errors": [
            "GitHub --apply requires --github-apply-policy-receipt with a PASS receipt."
        ],
    }


def test_summarize_receipt_includes_branch_lock_fields() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.dag_branch_lock_validation_receipt.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "verdict": "MISSING_WORKSPACE_LEASE",
            "required_lock_count": 2,
            "provided_lock_count": 2,
            "alerts": [
                {
                    "severity": "BLOCK",
                    "code": "missing_workspace_lease",
                    "message": "Provider branch locks require a Herdr workspace lease reference.",
                }
            ],
        }
    )

    assert summary == {
        "schema": "tau.dag_branch_lock_validation_receipt.v1",
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "verdict": "MISSING_WORKSPACE_LEASE",
        "required_lock_count": 2,
        "provided_lock_count": 2,
        "alert_count": 1,
        "alert_codes": ["missing_workspace_lease"],
    }


def test_summarize_receipt_includes_route_memory_sync_fields() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.dag_route_memory_sync_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "memory_sync": False,
            "sync_status": "DRY_RUN",
            "projected_document_count": 2,
            "errors": [],
        }
    )

    assert summary == {
        "schema": "tau.dag_route_memory_sync_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "memory_sync": False,
        "sync_status": "DRY_RUN",
        "projected_document_count": 2,
        "errors": [],
    }


def test_summarize_receipt_includes_evidence_validation_fields() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.evidence_validation_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "item_count": 1,
            "manifest_sha256": "sha256:" + ("1" * 64),
            "errors": [],
        }
    )

    assert summary == {
        "schema": "tau.evidence_validation_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "item_count": 1,
        "manifest_sha256": "sha256:" + ("1" * 64),
        "errors": [],
    }


def test_summarize_receipt_includes_project_dag_fields() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.dag_receipt.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "verdict": "INVALID_COMMAND_JSON",
            "selected_agents": ["coder", "reviewer"],
            "observed_edges": [
                {
                    "from_node": "coder",
                    "to_node": "reviewer",
                }
            ],
            "node_attempts": {"coder": 1, "reviewer": 1},
            "reviewer_verdicts": [
                {
                    "kind": "reviewer_verdict",
                    "reviewed_node_id": "coder",
                    "goal_hash": "sha256:active-goal",
                    "verdict": "PASS",
                }
            ],
            "dag_error": {
                "schema": "tau.dag_error.v1",
                "failure_code": "invalid_command_json",
                "recommended_action": {
                    "type": "repair_then_retry_or_reroute",
                    "next_agent": "goal-guardian",
                    "reason": "Repair the node command or subagent response contract before retrying.",
                },
            },
            "alerts": [{"code": "invalid_command_json"}],
            "errors": ["command stdout was not JSON"],
        }
    )

    assert summary == {
        "schema": "tau.dag_receipt.v1",
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "verdict": "INVALID_COMMAND_JSON",
        "selected_agents": ["coder", "reviewer"],
        "observed_edges": [
            {
                "from_node": "coder",
                "to_node": "reviewer",
            }
        ],
        "node_attempts": {"coder": 1, "reviewer": 1},
        "reviewer_verdicts": [
            {
                "kind": "reviewer_verdict",
                "reviewed_node_id": "coder",
                "goal_hash": "sha256:active-goal",
                "verdict": "PASS",
            }
        ],
        "dag_error": {
            "schema": "tau.dag_error.v1",
            "failure_code": "invalid_command_json",
            "recommended_action": {
                "type": "repair_then_retry_or_reroute",
                "next_agent": "goal-guardian",
                "reason": "Repair the node command or subagent response contract before retrying.",
            },
        },
        "errors": ["command stdout was not JSON"],
        "alert_count": 1,
        "alert_codes": ["invalid_command_json"],
    }


def test_summarize_receipt_includes_approval_packet_summary() -> None:
    module = _load_runner_module()

    packet_summary = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "action": "working_tree_mutation",
        "human_id": "human:graham",
        "target_id": "scratch-working-tree",
        "evidence_count": 1,
        "expires_at": "2000-01-01T00:00:00Z",
    }
    summary = module.summarize_receipt(
        {
            "schema": "tau.approval_gate_receipt.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": False,
            "requested_action": "working_tree_mutation",
            "approved": False,
            "approval_packet_sha256": "sha256-test",
            "packet_summary": packet_summary,
            "errors": ["approval packet expired"],
        }
    )

    assert summary == {
        "schema": "tau.approval_gate_receipt.v1",
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": False,
        "requested_action": "working_tree_mutation",
        "approved": False,
        "approval_packet_sha256": "sha256-test",
        "packet_summary": packet_summary,
        "errors": ["approval packet expired"],
    }


def test_summarize_receipt_includes_provider_lifecycle_probe_state() -> None:
    module = _load_runner_module()

    summary = module.summarize_receipt(
        {
            "schema": "tau.provider_lifecycle_probe_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": False,
            "normalized_state": "crashed",
            "ready": False,
            "errors": [],
        }
    )

    assert summary == {
        "schema": "tau.provider_lifecycle_probe_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "normalized_state": "crashed",
        "ready": False,
        "errors": [],
    }


def test_build_checks_registers_browser_cdp_proof(tmp_path: Path) -> None:
    module = _load_runner_module()

    checks = module.build_checks(
        repo=Path(__file__).resolve().parents[1],
        run_dir=tmp_path,
        uv_bin="uv",
        herdr_bin="herdr",
        receipt_timeout_seconds=120,
        provider_cleanup_mode="off",
    )

    check = next(item for item in checks if item.check_id == "advanced.browser_cdp_proof")
    assert check.level == "advanced"
    assert check.expected_status == "PASS"
    assert check.expected_verdict == "PASS"
    assert check.expected_provider_live is False
    assert "browser-cdp-proof" in check.command
    assert "--out-dir" in check.command
    assert str(tmp_path / "browser-cdp-proof") in check.command

    receipt = module.write_suite_receipt(
        repo=Path(__file__).resolve().parents[1],
        run_dir=tmp_path,
        run_id="browser-proof-suite",
        records=[],
        selected_levels=["advanced"],
        complete=True,
    )
    assert "Surf-backed browser proof checks" in " ".join(receipt["proof_scope"]["proves"])
    assert "production browser/chat UI rendering" in receipt["proof_scope"]["does_not_prove"]


def _load_runner_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "run-real-world-sanity.py"
    spec = importlib.util.spec_from_file_location("run_real_world_sanity", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
