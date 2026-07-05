import hashlib
import json
import subprocess
from pathlib import Path

from tau_coding.cli import _parse_provider_dag_poc_cli_args
from tau_coding.provider_dag_poc import (
    _coder_work_order,
    _reviewer_work_order,
    _run_provider_dag_cleanup,
    _send_pane_prompt,
    _validate_node_receipt,
    _wait_for_node_receipt,
    inspect_provider_dag_run,
    plan_provider_dag_poc,
    run_provider_dag_orchestrator,
)
from tau_coding.provider_pane_poc import (
    ProviderPane,
    _compact_readiness_samples,
    _default_provider_panes,
    _pane_record,
    _provider_agent_name,
    _provider_initializing_visible,
    inspect_provider_pane_run,
    inspect_provider_readiness_run,
)


def test_inspect_provider_pane_run_summarizes_artifacts(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({"schema": "tau.provider_pane_event.v1", "kind": "provider_spec_created"})
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_pane_runtime_manifest.v1",
                "run_id": "run-1",
                "events_jsonl": str(events),
                "workstation_manifest": "/tmp/workstation.json",
                "inspect_path": "/tmp/inspect.json",
                "providers": [
                    {
                        "provider_id": "codex",
                        "role": "codex",
                        "pane_id": "w1:p1",
                        "terminal_id": "term_codex",
                        "work_order_path": "/tmp/codex.json",
                        "ready_prompt_observed": True,
                        "readiness_actions": ["codex_update_prompt_skipped"],
                        "visible_log": "/tmp/codex.log",
                        "read_returncode": 0,
                    },
                    {
                        "provider_id": "opencode",
                        "role": "opencode",
                        "pane_id": "w1:p2",
                        "terminal_id": "term_opencode",
                        "work_order_path": "/tmp/opencode.json",
                        "ready_prompt_observed": True,
                        "readiness_actions": [],
                        "visible_log": "/tmp/opencode.log",
                        "read_returncode": 0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "run-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_pane_run_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "proof_scope": {
                    "proves": ["provider panes launched"],
                    "does_not_prove": ["ticket closure"],
                },
            }
        ),
        encoding="utf-8",
    )

    summary = inspect_provider_pane_run(tmp_path)

    assert summary["schema"] == "tau.provider_pane_inspect.v1"
    assert summary["ok"] is True
    assert summary["mocked"] is False
    assert summary["live"] is True
    assert summary["events_count"] == 1
    assert summary["providers"] == [
        {
            "provider_id": "codex",
            "role": "codex",
            "agent_name": None,
            "dag": None,
            "pane_id": "w1:p1",
            "terminal_id": "term_codex",
            "work_order_path": "/tmp/codex.json",
            "ready_prompt_observed": True,
            "readiness_actions": ["codex_update_prompt_skipped"],
            "visible_log": "/tmp/codex.log",
            "read_returncode": 0,
        },
        {
            "provider_id": "opencode",
            "role": "opencode",
            "agent_name": None,
            "dag": None,
            "pane_id": "w1:p2",
            "terminal_id": "term_opencode",
            "work_order_path": "/tmp/opencode.json",
            "ready_prompt_observed": True,
            "readiness_actions": [],
            "visible_log": "/tmp/opencode.log",
            "read_returncode": 0,
        },
    ]


def test_provider_agent_name_keeps_run_provider_fallback() -> None:
    provider = ProviderPane(
        provider_id="codex",
        role="codex",
        command=("codex",),
    )

    assert _provider_agent_name("run-001", provider) == "run-001-codex"


def test_provider_agent_name_uses_dag_node_context() -> None:
    provider = ProviderPane(
        provider_id="codex",
        role="codex",
        command=("codex",),
        dag_id="tau-issue-47-script-contract",
        node_id="script-writer",
        agent="coder",
    )

    assert (
        _provider_agent_name("run-001", provider)
        == "tau-issue-47-script-contract-script-writer-coder-codex"
    )


def test_default_provider_panes_accept_dag_node_context(tmp_path: Path) -> None:
    providers = _default_provider_panes(
        tmp_path,
        provider_node_context={
            "codex": {
                "dag_id": "tau-provider-dag",
                "node_id": "coder",
                "agent": "coder",
            },
            "opencode": {
                "dag_id": "tau-provider-dag",
                "node_id": "reviewer",
                "agent": "reviewer",
            },
        },
    )

    names = {
        provider.provider_id: _provider_agent_name("readiness-run", provider)
        for provider in providers
    }
    assert names == {
        "codex": "tau-provider-dag-coder-codex",
        "opencode": "tau-provider-dag-reviewer-opencode",
    }


def test_pane_record_preserves_agent_name_and_dag_context(tmp_path: Path) -> None:
    provider = ProviderPane(
        provider_id="codex",
        role="codex",
        command=("codex",),
        dag_id="tau-provider-dag",
        node_id="coder",
        agent="coder",
    )
    record = _pane_record(
        provider,
        tmp_path / "codex.json",
        "tau-provider-dag-coder-codex",
        {
            "agents": {
                "tau-provider-dag-coder-codex": {
                    "last_start_result": {
                        "returncode": 0,
                        "parsed": {
                            "result": {
                                "agent": {
                                    "pane_id": "pane-1",
                                    "terminal_id": "term-1",
                                    "workspace_id": "ws-1",
                                }
                            }
                        },
                    }
                }
            }
        },
    )

    assert record["agent_name"] == "tau-provider-dag-coder-codex"
    assert record["dag"] == {
        "dag_id": "tau-provider-dag",
        "node_id": "coder",
        "agent": "coder",
    }


def test_inspect_provider_readiness_run_summarizes_structured_records(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    readiness_dir = tmp_path / "readiness"
    codex_readiness = readiness_dir / "codex.readiness.json"
    opencode_readiness = readiness_dir / "opencode.readiness.json"
    codex_session_state = readiness_dir / "codex.session-state.json"
    opencode_session_state = readiness_dir / "opencode.session-state.json"
    readiness_dir.mkdir()
    events.write_text(
        json.dumps(
            {
                "schema": "tau.provider_pane_event.v1",
                "kind": "provider_structured_readiness_observed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    codex_readiness.write_text(
        json.dumps(
            {
                "schema": "tau.provider_readiness.v1",
                "provider_id": "codex",
                "state": "ready",
                "ready": True,
                "source": "herdr_process_info",
                "pane_id": "w1:p1",
                "terminal_id": "term_codex",
                "evidence": {
                    "provider_readiness_path": str(codex_readiness),
                    "provider_session_state_path": str(codex_session_state),
                },
                "provider_session_state": {
                    "schema": "tau.provider_session_state.v1",
                    "provider_id": "codex",
                    "state": "ready",
                    "ready": True,
                },
                "diagnostics": {
                    "visible_prompt_observed": True,
                    "visible_prompt_is_gate": False,
                },
            }
        ),
        encoding="utf-8",
    )
    opencode_readiness.write_text(
        json.dumps(
            {
                "schema": "tau.provider_readiness.v1",
                "provider_id": "opencode",
                "state": "ready",
                "ready": True,
                "source": "herdr_process_info",
                "pane_id": "w1:p2",
                "terminal_id": "term_opencode",
                "evidence": {
                    "provider_readiness_path": str(opencode_readiness),
                    "provider_session_state_path": str(opencode_session_state),
                },
                "provider_session_state": {
                    "schema": "tau.provider_session_state.v1",
                    "provider_id": "opencode",
                    "state": "ready",
                    "ready": True,
                },
                "diagnostics": {
                    "visible_prompt_observed": False,
                    "visible_prompt_is_gate": False,
                },
            }
        ),
        encoding="utf-8",
    )
    codex_session_state.write_text(
        json.dumps(
            {
                "schema": "tau.provider_session_state.v1",
                "provider_id": "codex",
                "workspace_id": "w1",
                "pane_id": "w1:p1",
                "terminal_id": "term_codex",
                "state": "ready",
                "ready": True,
                "source": "herdr_process_info",
                "process": {"alive": True, "command": "codex"},
                "evidence": {
                    "visible_log_path": "/tmp/codex.visible.txt",
                    "provider_readiness_path": str(codex_readiness),
                },
            }
        ),
        encoding="utf-8",
    )
    opencode_session_state.write_text(
        json.dumps(
            {
                "schema": "tau.provider_session_state.v1",
                "provider_id": "opencode",
                "workspace_id": "w1",
                "pane_id": "w1:p2",
                "terminal_id": "term_opencode",
                "state": "ready",
                "ready": True,
                "source": "herdr_process_info",
                "process": {"alive": True, "command": "opencode"},
                "evidence": {
                    "visible_log_path": "/tmp/opencode.visible.txt",
                    "provider_readiness_path": str(opencode_readiness),
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_readiness_runtime_manifest.v1",
                "run_id": "run-1",
                "events_jsonl": str(events),
                "workstation_manifest": "/tmp/workstation.json",
                "inspect_path": "/tmp/inspect.json",
                "readiness_records": [str(codex_readiness), str(opencode_readiness)],
                "provider_session_states": [
                    str(codex_session_state),
                    str(opencode_session_state),
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "run-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_readiness_run_receipt.v1",
                "ok": True,
                "status": "PASS",
                "mocked": False,
                "live": True,
                "all_provider_structured_ready": True,
                "proof_scope": {
                    "proves": ["structured readiness"],
                    "does_not_prove": ["semantic task completion"],
                },
            }
        ),
        encoding="utf-8",
    )

    summary = inspect_provider_readiness_run(tmp_path)

    assert summary["schema"] == "tau.provider_readiness_inspect.v1"
    assert summary["ok"] is True
    assert summary["mocked"] is False
    assert summary["live"] is True
    assert summary["events_count"] == 1
    assert summary["all_provider_structured_ready"] is True
    assert summary["readiness"] == [
        {
            "provider_id": "codex",
            "state": "ready",
            "ready": True,
            "source": "herdr_process_info",
            "pane_id": "w1:p1",
            "terminal_id": "term_codex",
            "visible_prompt_observed": True,
            "visible_prompt_is_gate": False,
            "provider_readiness_path": str(codex_readiness),
            "provider_session_state_path": str(codex_session_state),
            "provider_session_state": {
                "schema": "tau.provider_session_state.v1",
                "provider_id": "codex",
                "state": "ready",
                "ready": True,
            },
        },
        {
            "provider_id": "opencode",
            "state": "ready",
            "ready": True,
            "source": "herdr_process_info",
            "pane_id": "w1:p2",
            "terminal_id": "term_opencode",
            "visible_prompt_observed": False,
            "visible_prompt_is_gate": False,
            "provider_readiness_path": str(opencode_readiness),
            "provider_session_state_path": str(opencode_session_state),
            "provider_session_state": {
                "schema": "tau.provider_session_state.v1",
                "provider_id": "opencode",
                "state": "ready",
                "ready": True,
            },
        },
    ]
    assert summary["provider_session_states"] == [
        {
            "schema": "tau.provider_session_state.v1",
            "provider_id": "codex",
            "workspace_id": "w1",
            "pane_id": "w1:p1",
            "terminal_id": "term_codex",
            "state": "ready",
            "ready": True,
            "source": "herdr_process_info",
            "observed_at": None,
            "process_alive": True,
            "foreground_command": "codex",
            "auth_status": None,
            "interstitial_present": None,
            "interstitial_kind": None,
            "provider_api_available": None,
            "visible_log_path": "/tmp/codex.visible.txt",
            "provider_readiness_path": str(codex_readiness),
            "provider_event_log_path": None,
        },
        {
            "schema": "tau.provider_session_state.v1",
            "provider_id": "opencode",
            "workspace_id": "w1",
            "pane_id": "w1:p2",
            "terminal_id": "term_opencode",
            "state": "ready",
            "ready": True,
            "source": "herdr_process_info",
            "observed_at": None,
            "process_alive": True,
            "foreground_command": "opencode",
            "auth_status": None,
            "interstitial_present": None,
            "interstitial_kind": None,
            "provider_api_available": None,
            "visible_log_path": "/tmp/opencode.visible.txt",
            "provider_readiness_path": str(opencode_readiness),
            "provider_event_log_path": None,
        },
    ]


def test_compact_readiness_samples_preserves_probe_attempts() -> None:
    compact = _compact_readiness_samples(
        [
            {
                "attempt": 1,
                "state": "unknown",
                "ready": False,
                "process_alive": False,
                "command_matches": False,
                "foreground_process": {},
                "pane_get_returncode": 0,
                "process_info_returncode": 0,
            },
            {
                "attempt": 2,
                "state": "ready",
                "ready": True,
                "process_alive": True,
                "command_matches": True,
                "foreground_process": {"argv": ["opencode", "/tmp/repo"]},
                "pane_get_returncode": 0,
                "process_info_returncode": 0,
            },
        ]
    )

    assert compact == [
        {
            "attempt": 1,
            "state": "unknown",
            "ready": False,
            "process_alive": False,
            "command_matches": False,
            "foreground_command": "",
            "pane_get_returncode": 0,
            "process_info_returncode": 0,
        },
        {
            "attempt": 2,
            "state": "ready",
            "ready": True,
            "process_alive": True,
            "command_matches": True,
            "foreground_command": "opencode",
            "pane_get_returncode": 0,
            "process_info_returncode": 0,
        },
    ]


def test_codex_model_loading_visible_is_provider_initializing() -> None:
    assert (
        _provider_initializing_visible(
            "codex",
            "OpenAI Codex\nmodel:       loading   /model to change\n\n› Explain this codebase",
        )
        is True
    )
    assert _provider_initializing_visible("opencode", "model: loading") is False


def test_inspect_provider_dag_run_summarizes_attempts(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({"schema": "tau.provider_dag_event.v1", "kind": "coder_dispatch"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_runtime_manifest.v1",
                "run_id": "run-1",
                "events_jsonl": str(events),
                "scratch_worktree": str(tmp_path / "scratch-worktree"),
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
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "run-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.dag_run_receipt.v1",
                "ok": True,
                "status": "PASS",
                "verdict": "PASS",
                "mocked": False,
                "live": True,
                "run_id": "run-1",
                "scratch_worktree": str(tmp_path / "scratch-worktree"),
                "attempt_count": 1,
                "max_attempts": 2,
                "provider_sessions": {
                    "codex": {
                        "role": "coder",
                        "provider_id": "codex",
                        "workspace_id": "w1",
                        "pane_id": "w1:p1",
                        "terminal_id": "term-codex",
                        "visible": True,
                    },
                    "opencode": {
                        "role": "reviewer",
                        "provider_id": "opencode",
                        "workspace_id": "w1",
                        "pane_id": "w1:p2",
                        "terminal_id": "term-opencode",
                        "visible": True,
                    },
                },
                "visible_subagents": {
                    "planner": {
                        "role": "planner",
                        "workspace_id": "w1",
                        "pane_id": "w1:p3",
                        "terminal_id": "term-planner",
                        "visible": True,
                    },
                    "orchestrator": {
                        "role": "orchestrator",
                        "workspace_id": "w1",
                        "pane_id": "w1:p4",
                        "terminal_id": "term-orchestrator",
                        "visible": True,
                    },
                    "coder": {
                        "role": "coder",
                        "provider_id": "codex",
                        "workspace_id": "w1",
                        "pane_id": "w1:p1",
                        "terminal_id": "term-codex",
                        "visible": True,
                    },
                    "reviewer": {
                        "role": "reviewer",
                        "provider_id": "opencode",
                        "workspace_id": "w1",
                        "pane_id": "w1:p2",
                        "terminal_id": "term-opencode",
                        "visible": True,
                    },
                },
                "herdr_cleanup_receipt": str(tmp_path / "herdr-cleanup-receipt.json"),
                "herdr_cleanup": {
                    "mode": "dry-run",
                    "status": "PASS",
                    "candidate_count": 1,
                    "applied_action_count": 1,
                    "post_verified_absent_count": 1,
                    "mocked": False,
                    "live": False,
                },
                "orchestration_evidence_receipt": str(
                    tmp_path / "orchestration-evidence-receipt.json"
                ),
                "orchestration_evidence": {
                    "status": "PASS",
                    "mocked": False,
                    "live": True,
                    "provider_live": True,
                    "feature_counts": {"agent_lineage": 4},
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
                "proof_scope": {
                    "proves": ["bounded provider DAG"],
                    "does_not_prove": ["ticket closure"],
                },
            }
        ),
        encoding="utf-8",
    )

    summary = inspect_provider_dag_run(tmp_path)

    assert summary["schema"] == "tau.provider_dag_inspect.v1"
    assert summary["ok"] is True
    assert summary["mocked"] is False
    assert summary["live"] is True
    assert summary["events_count"] == 1
    assert summary["attempt_count"] == 1
    assert summary["max_attempts"] == 2
    assert set(summary["visible_subagents"]) == {"planner", "orchestrator", "coder", "reviewer"}
    assert summary["visible_subagents"]["planner"]["visible"] is True
    assert summary["visible_subagents"]["orchestrator"]["visible"] is True
    assert summary["provider_sessions"]["codex"]["pane_id"] == "w1:p1"
    assert summary["provider_sessions"]["opencode"]["pane_id"] == "w1:p2"
    assert summary["herdr_cleanup_receipt"] == str(tmp_path / "herdr-cleanup-receipt.json")
    assert summary["herdr_cleanup"]["mode"] == "dry-run"
    assert summary["herdr_cleanup"]["applied_action_count"] == 1
    assert summary["herdr_cleanup"]["post_verified_absent_count"] == 1
    assert summary["orchestration_evidence_receipt"] == str(
        tmp_path / "orchestration-evidence-receipt.json"
    )
    assert summary["orchestration_evidence"]["feature_counts"]["agent_lineage"] == 4
    assert summary["attempts"] == [
        {
            "attempt": 1,
            "coder_status": "PASS",
            "coder_verdict": "PASS",
            "reviewer_status": "PASS",
            "reviewer_verdict": "PASS",
            "errors": [],
        }
    ]


def test_plan_provider_dag_poc_writes_spec_and_planner_receipt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    receipt = plan_provider_dag_poc(
        repo=repo,
        run_root=tmp_path / "runs",
        label="planner-proof",
        max_attempts=2,
    )

    assert receipt["schema"] == "tau.dag_planner_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    spec_path = Path(str(receipt["dag_spec"]))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec["schema"] == "tau.dag_run_spec.v1"
    assert spec["goal"]["goal_hash"].startswith("sha256:")
    assert spec["goal"]["goal_version"] == 1
    assert spec["target"]["repo"] == str(repo.resolve())
    assert spec["target"]["scratch_worktree"] == receipt["scratch_worktree"]
    assert spec["target"]["allowed_paths"] == [receipt["target_file"]]
    assert spec["proof_controls"] == {
        "force_reviewer_revise_attempts": [],
        "allow_final_forced_revise": False,
        "reviewer_model": None,
        "coder_mode": "codex",
    }
    assert spec["planner"]["subagent"] == "planner"
    assert spec["orchestrator"]["subagent"] == "orchestrator"
    assert spec["nodes"] == [
        {
            "depends_on": [],
            "node_id": "coder",
            "provider_id": "codex",
            "receipt_schema": "tau.provider_dag_node_receipt.v1",
            "role": "coder",
        },
        {
            "depends_on": ["coder"],
            "node_id": "reviewer",
            "provider_id": "opencode",
            "receipt_schema": "tau.provider_dag_node_receipt.v1",
            "role": "reviewer",
        },
    ]
    assert spec["policy"]["require_structured_readiness"] is True
    assert spec["policy"]["allow_visible_text_readiness_gate"] is False


def test_provider_dag_work_orders_are_canonical_and_hash_bound(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch-worktree"
    receipt_dir = tmp_path / "receipts"
    repo.mkdir()
    scratch.mkdir()
    receipt_dir.mkdir()
    target_file = scratch / "message.txt"
    coder_receipt = receipt_dir / "coder.json"
    reviewer_receipt = receipt_dir / "reviewer.json"
    visible_log = tmp_path / "codex.visible.txt"
    visible_log.write_text("codex visible log\n", encoding="utf-8")
    provider_record = {
        "workspace_id": "w1",
        "pane_id": "w1:p3",
        "terminal_id": "term-coder",
        "visible_log_path": str(visible_log),
    }

    coder = _coder_work_order(
        run_id="run-001",
        dag_id="dag-001",
        goal_hash="sha256:goal",
        attempt=1,
        max_attempts=2,
        repo=repo,
        scratch_dir=scratch,
        target_file=target_file,
        receipt_path=coder_receipt,
        reviewer_feedback="",
        provider_record=provider_record,
    )
    reviewer = _reviewer_work_order(
        run_id="run-001",
        dag_id="dag-001",
        goal_hash="sha256:goal",
        attempt=1,
        max_attempts=2,
        repo=repo,
        scratch_dir=scratch,
        target_file=target_file,
        receipt_path=reviewer_receipt,
        coder_receipt_path=coder_receipt,
        force_revise=False,
        provider_record={**provider_record, "terminal_id": "term-reviewer"},
    )

    assert coder["schema"] == "tau.provider_dag_work_order.v1"
    assert coder["dag_id"] == "dag-001"
    assert coder["goal"]["goal_hash"] == "sha256:goal"
    assert coder["node"] == {
        "node_id": "coder",
        "agent": "coder",
        "attempt": 1,
        "max_attempts": 2,
    }
    assert coder["target"] == {
        "repo": str(repo),
        "allowed_paths": [str(target_file)],
        "scratch_worktree": str(scratch),
    }
    assert coder["herdr"] == provider_record
    assert coder["work_order_sha256"] == _canonical_work_order_sha256(coder)
    assert coder["target_file"] == str(target_file)
    assert coder["receipt_path"] == str(coder_receipt)

    assert reviewer["node"]["node_id"] == "reviewer"
    assert reviewer["target"]["allowed_paths"] == [str(target_file), str(coder_receipt)]
    assert reviewer["required_evidence"] == ["coder_receipt_reviewed", "target_file_reviewed"]
    assert reviewer["herdr"]["terminal_id"] == "term-reviewer"
    assert reviewer["work_order_sha256"] == _canonical_work_order_sha256(reviewer)


def test_provider_node_receipt_validator_requires_work_order_and_herdr_binding(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch-worktree"
    receipt_dir = tmp_path / "receipts"
    repo.mkdir()
    scratch.mkdir()
    receipt_dir.mkdir()
    target_file = scratch / "message.txt"
    target_file.write_text("completed\n", encoding="utf-8")
    visible_log = tmp_path / "codex.visible.txt"
    visible_log.write_text("codex visible log\n", encoding="utf-8")
    receipt_path = receipt_dir / "coder.json"
    work_order = _coder_work_order(
        run_id="run-001",
        dag_id="dag-001",
        goal_hash="sha256:goal",
        attempt=1,
        max_attempts=2,
        repo=repo,
        scratch_dir=scratch,
        target_file=target_file,
        receipt_path=receipt_path,
        reviewer_feedback="",
        provider_record={
            "workspace_id": "w1",
            "pane_id": "w1:p5",
            "terminal_id": "term-codex",
            "visible_log_path": str(visible_log),
        },
    )
    work_order_path = tmp_path / "work-order.json"
    work_order_path.write_text(json.dumps(work_order), encoding="utf-8")
    receipt = {
        "schema": "tau.provider_dag_node_receipt.v1",
        "dag_id": "dag-001",
        "goal_hash": "sha256:goal",
        "node_id": "coder",
        "provider_id": "codex",
        "attempt": 1,
        "workspace_id": "w1",
        "pane_id": "w1:p5",
        "terminal_id": "term-codex",
        "visible_log_path": str(visible_log),
        "visible_log_sha256": hashlib.sha256(visible_log.read_bytes()).hexdigest(),
        "work_order_path": str(work_order_path),
        "work_order_sha256": work_order["work_order_sha256"],
        "status": "PASS",
        "verdict": "PASS",
        "changed_files": [str(target_file)],
        "commands_run": ["test"],
        "artifacts": [str(target_file)],
        "handoff_summary": "Coder updated the target file.",
        "errors": [],
        "policy_exceptions": [],
    }

    errors = _validate_node_receipt(
        receipt,
        expected_node_id="coder",
        expected_provider_id="codex",
        expected_attempt=1,
        work_order_path=work_order_path,
        work_order_sha256=str(work_order["work_order_sha256"]),
        expected_herdr=work_order["herdr"],
        expected_goal_hash="sha256:goal",
        expected_dag_id="dag-001",
    )

    assert errors == []

    stale_receipt = dict(receipt)
    stale_receipt["work_order_sha256"] = "stale"
    stale_receipt["pane_id"] = "wrong-pane"
    stale_receipt["visible_log_path"] = "wrong-log"
    stale_receipt["visible_log_sha256"] = "stale-log-sha"
    stale_errors = _validate_node_receipt(
        stale_receipt,
        expected_node_id="coder",
        expected_provider_id="codex",
        expected_attempt=1,
        work_order_path=work_order_path,
        work_order_sha256=str(work_order["work_order_sha256"]),
        expected_herdr=work_order["herdr"],
        expected_goal_hash="sha256:goal",
        expected_dag_id="dag-001",
    )

    assert "work_order_sha256 must match the dispatched work order" in stale_errors
    assert "pane_id must be w1:p5" in stale_errors
    assert f"visible_log_path must be {visible_log}" in stale_errors
    assert "visible_log_sha256 must match visible_log_path contents" in stale_errors


def test_provider_node_receipt_timeout_reports_delivery_diagnostics(tmp_path: Path) -> None:
    visible_log = tmp_path / "codex.visible.txt"
    visible_log.write_text("OpenAI Codex\n› Find and fix a bug in @filename\n", encoding="utf-8")
    work_order_path = tmp_path / "work-orders" / "attempt-01-coder.json"
    work_order_path.parent.mkdir()
    work_order_path.write_text('{"schema":"tau.provider_dag_work_order.v1"}\n', encoding="utf-8")
    missing_receipt = tmp_path / "receipts" / "attempt-01-coder.json"

    receipt, errors = _wait_for_node_receipt(
        missing_receipt,
        expected_node_id="coder",
        expected_provider_id="codex",
        expected_attempt=1,
        work_order_path=work_order_path,
        work_order_sha256="sha256:work-order",
        expected_herdr={
            "workspace_id": "w1",
            "pane_id": "w1:p5",
            "terminal_id": "term-codex",
            "visible_log_path": str(visible_log),
        },
        expected_goal_hash="sha256:goal",
        expected_dag_id="dag-001",
        timeout_seconds=0.01,
    )

    assert receipt == {}
    assert any(error.startswith("node_receipt_timeout: coder") for error in errors)
    assert f"node_receipt_missing: {missing_receipt}" in errors
    assert f"visible_log_path: {visible_log}" in errors
    assert any(
        error.startswith("work_order_delivery_not_observed: visible log does not contain")
        for error in errors
    )


def test_provider_node_receipt_timeout_reports_delivered_work_order(tmp_path: Path) -> None:
    work_order_path = tmp_path / "work-orders" / "attempt-01-coder.json"
    work_order_path.parent.mkdir()
    work_order_path.write_text('{"schema":"tau.provider_dag_work_order.v1"}\n', encoding="utf-8")
    visible_log = tmp_path / "codex.visible.txt"
    visible_log.write_text(
        "› provider-runs/run/work-orders/attempt-01-coder.json\n"
        "Receipt JSON shape:\n"
        '{"schema":"tau.provider_dag_node_receipt.v1"}\n',
        encoding="utf-8",
    )
    missing_receipt = tmp_path / "receipts" / "attempt-01-coder.json"

    receipt, errors = _wait_for_node_receipt(
        missing_receipt,
        expected_node_id="coder",
        expected_provider_id="codex",
        expected_attempt=1,
        work_order_path=work_order_path,
        work_order_sha256="sha256:work-order",
        expected_herdr={
            "workspace_id": "w1",
            "pane_id": "w1:p5",
            "terminal_id": "term-codex",
            "visible_log_path": str(visible_log),
        },
        expected_goal_hash="sha256:goal",
        expected_dag_id="dag-001",
        timeout_seconds=0.01,
    )

    assert receipt == {}
    assert any(
        error.startswith("work_order_delivered_but_receipt_missing: visible log contains")
        for error in errors
    )
    assert not any(error.startswith("work_order_delivery_not_observed") for error in errors)


def test_provider_prompt_send_uses_herdr_pane_run(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_pane_command(argv, *, cwd, timeout_seconds):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "tau_coding.provider_dag_poc._run_pane_command",
        fake_run_pane_command,
    )

    results = _send_pane_prompt(
        herdr_bin="herdr",
        pane_id="w1:p5",
        text="Read work order /tmp/work-order.json",
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert len(results) == 1
    assert calls == [
        [
            "herdr",
            "pane",
            "run",
            "w1:p5",
            "Read work order /tmp/work-order.json\n",
        ]
    ]


def test_plan_provider_dag_poc_records_forced_reviewer_revise_attempts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    receipt = plan_provider_dag_poc(
        repo=repo,
        run_root=tmp_path / "runs",
        label="forced-retry",
        max_attempts=2,
        force_reviewer_revise_attempts=(1,),
    )

    assert receipt["proof_controls"] == {
        "force_reviewer_revise_attempts": [1],
        "allow_final_forced_revise": False,
        "reviewer_model": None,
        "coder_mode": "codex",
    }
    spec = json.loads(Path(str(receipt["dag_spec"])).read_text(encoding="utf-8"))
    assert spec["proof_controls"] == {
        "force_reviewer_revise_attempts": [1],
        "allow_final_forced_revise": False,
        "reviewer_model": None,
        "coder_mode": "codex",
    }


def test_parse_provider_dag_poc_force_reviewer_revise_flags() -> None:
    options = _parse_provider_dag_poc_cli_args(
        ["--max-attempts", "3", "--force-reviewer-revise-attempts", "1,2"]
    )

    assert options["max_attempts"] == 3
    assert options["force_reviewer_revise_attempts"] == (1, 2)

    first_options = _parse_provider_dag_poc_cli_args(["--force-reviewer-revise-first"])

    assert first_options["force_reviewer_revise_attempts"] == (1,)

    exhaustion_options = _parse_provider_dag_poc_cli_args(
        ["--force-reviewer-revise-attempts=1,2", "--allow-final-forced-revise"]
    )

    assert exhaustion_options["force_reviewer_revise_attempts"] == (1, 2)
    assert exhaustion_options["allow_final_forced_revise"] is True

    model_options = _parse_provider_dag_poc_cli_args(["--reviewer-model", "openai/test-model"])

    assert model_options["reviewer_model"] == "openai/test-model"

    coder_mode_options = _parse_provider_dag_poc_cli_args(
        ["--coder-mode", "deterministic-visible"]
    )

    assert coder_mode_options["coder_mode"] == "deterministic-visible"

    cleanup_options = _parse_provider_dag_poc_cli_args(["--cleanup-mode", "off"])

    assert cleanup_options["cleanup_mode"] == "off"


def test_plan_provider_dag_poc_can_intentionally_force_final_revise(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    receipt = plan_provider_dag_poc(
        repo=repo,
        run_root=tmp_path / "runs",
        label="max-attempt-exhaustion",
        max_attempts=2,
        force_reviewer_revise_attempts=(1, 2),
        allow_final_forced_revise=True,
    )

    assert receipt["proof_controls"] == {
        "force_reviewer_revise_attempts": [1, 2],
        "allow_final_forced_revise": True,
        "reviewer_model": None,
        "coder_mode": "codex",
    }


def test_plan_provider_dag_poc_records_deterministic_visible_coder_mode(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    receipt = plan_provider_dag_poc(
        repo=repo,
        run_root=tmp_path / "runs",
        label="deterministic-coder",
        max_attempts=1,
        coder_mode="deterministic-visible",
    )

    spec = json.loads(Path(str(receipt["dag_spec"])).read_text(encoding="utf-8"))
    assert receipt["proof_controls"]["coder_mode"] == "deterministic-visible"
    assert spec["proof_controls"]["coder_mode"] == "deterministic-visible"
    assert spec["nodes"][0]["provider_id"] == "tau-deterministic-visible"


def test_plan_provider_dag_poc_records_reviewer_model(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    receipt = plan_provider_dag_poc(
        repo=repo,
        run_root=tmp_path / "runs",
        label="reviewer-model-proof",
        max_attempts=1,
        reviewer_model="openai/not-a-real-model",
    )

    assert receipt["proof_controls"]["reviewer_model"] == "openai/not-a-real-model"
    spec = json.loads(Path(str(receipt["dag_spec"])).read_text(encoding="utf-8"))
    assert spec["proof_controls"]["reviewer_model"] == "openai/not-a-real-model"


def test_plan_provider_dag_poc_rejects_final_forced_revise_by_default(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    try:
        plan_provider_dag_poc(
            repo=repo,
            run_root=tmp_path / "runs",
            label="invalid-final-revise",
            max_attempts=2,
            force_reviewer_revise_attempts=(1, 2),
        )
    except RuntimeError as exc:
        assert "must leave a later attempt for PASS" in str(exc)
    else:
        raise AssertionError("final forced revise should require explicit proof flag")


def test_provider_dag_orchestrator_rejects_invalid_spec(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = tmp_path / "bad-dag.json"
    spec.write_text(json.dumps({"schema": "not.tau.dag"}), encoding="utf-8")

    try:
        run_provider_dag_orchestrator(dag_spec=spec, repo=repo)
    except RuntimeError as exc:
        assert "DAG spec schema must be tau.dag_run_spec.v1" in str(exc)
    else:
        raise AssertionError("invalid DAG spec should fail closed")


def test_provider_dag_cleanup_writes_dry_run_receipt(tmp_path: Path) -> None:
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_runtime_manifest.v1",
                "provider_sessions": {
                    "codex": {
                        "workspace_id": "w-clean",
                        "pane_id": "w-clean:p5",
                        "terminal_id": "term-coder",
                    }
                },
                "visible_subagents": {
                    "planner": {
                        "workspace_id": "w-clean",
                        "pane_id": "w-clean:p7",
                        "terminal_id": "term-planner",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    cleanup = _run_provider_dag_cleanup(run_dir=tmp_path, mode="dry-run", herdr_bin="herdr")

    assert cleanup["mode"] == "dry-run"
    assert cleanup["receipt_path"] == str(tmp_path / "herdr-cleanup-receipt.json")
    assert cleanup["status"] == "PASS"
    assert cleanup["ok"] is True
    assert cleanup["mocked"] is False
    assert cleanup["live"] is False
    assert cleanup["candidate_count"] == 1
    assert cleanup["resource_count"] == 1
    assert cleanup["workspace_lease"] == str(tmp_path / "herdr-workspace-lease.json")
    assert isinstance(cleanup["workspace_lease_sha256"], str)
    assert cleanup["applied_action_count"] == 0
    assert cleanup["post_verified_absent_count"] == 0
    receipt = json.loads((tmp_path / "herdr-cleanup-receipt.json").read_text(encoding="utf-8"))
    assert receipt["candidates"][0]["workspace_id"] == "w-clean"


def test_provider_dag_cleanup_apply_summarizes_verified_absence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_runtime_manifest.v1",
                "provider_sessions": {
                    "codex": {
                        "workspace_id": "w-clean",
                        "pane_id": "w-clean:p5",
                        "terminal_id": "term-coder",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = tmp_path / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"argv\":[' >> \"$HERDR_CALLS\"\n"
        "first=1\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$first\" = 0 ]; then printf ',' >> \"$HERDR_CALLS\"; fi\n"
        "  first=0\n"
        "  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]), end=\"\")' \"$arg\" >> \"$HERDR_CALLS\"\n"
        "done\n"
        "printf ']}\\n' >> \"$HERDR_CALLS\"\n"
        "if [ \"$1 $2 $3\" = \"workspace get w-clean\" ]; then\n"
        "  printf '{\"error\":{\"code\":\"workspace_not_found\"}}\\n'\n"
        "  exit 1\n"
        "fi\n"
        "printf '{\"result\":{\"type\":\"ok\"}}\\n'\n",
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    cleanup = _run_provider_dag_cleanup(run_dir=tmp_path, mode="apply", herdr_bin=str(fake_herdr))

    assert cleanup["status"] == "PASS"
    assert cleanup["live"] is True
    assert cleanup["applied_action_count"] == 1
    assert cleanup["post_verified_absent_count"] == 1
    receipt = json.loads((tmp_path / "herdr-cleanup-receipt.json").read_text(encoding="utf-8"))
    assert receipt["applied_actions"][0]["post_verified_absent"] is True


def test_provider_dag_cleanup_can_be_disabled(tmp_path: Path) -> None:
    cleanup = _run_provider_dag_cleanup(run_dir=tmp_path, mode="off", herdr_bin="herdr")

    assert cleanup == {
        "mode": "off",
        "receipt_path": None,
        "status": "SKIPPED",
        "mocked": False,
        "live": False,
    }


def test_tau_dag_subagent_contracts_comply_with_required_sections() -> None:
    root = Path(__file__).resolve().parents[1]
    required_sections = [
        "schema: oc_subagent.persona.v1",
        "role:",
        "does_not_own:",
        "dag_spec:",
        "primary_skills:",
        "tool_policy:",
        "memory_policy:",
        "delegated_access_skills:",
        "help_policy:",
        "turn_contract:",
        "status_reporting:",
        "retry_policy:",
        "output_contract:",
        "artifact_contract:",
        "proof_tasks:",
    ]
    for agent_id in ("planner", "orchestrator", "coder", "reviewer"):
        agent_dir = root / "agents" / agent_id
        agents_md = agent_dir / "AGENTS.md"
        persona = agent_dir / "persona.yaml"
        assert agents_md.exists()
        assert persona.exists()
        text = persona.read_text(encoding="utf-8")
        for section in required_sections:
            assert section in text, f"{agent_id} missing {section}"
        assert f"id: {agent_id}" in text
        assert "require_dag_spec_before_work: true" in text
        assert "reject_prose_only_work_orders: true" in text
        assert "recipient: project_agent" in text
        assert "unlimited_retries: denied" in text or "absolute_max_attempts:" in text
        assert "memory.store" in text
        assert "memory.upsert" in text


def test_tau_dag_command_specs_reference_agent_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    for agent_id in ("planner", "orchestrator", "coder", "reviewer"):
        spec_path = (
            root
            / "experiments"
            / "goal-locked-subagents"
            / "agent-command-specs"
            / agent_id
            / "tau-dispatch-command.json"
        )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        assert spec["agent_contract"] == f"agents/{agent_id}/AGENTS.md"
        assert spec["persona_contract"] == f"agents/{agent_id}/persona.yaml"
        assert (root / spec["agent_contract"]).exists()
        assert (root / spec["persona_contract"]).exists()


def _canonical_work_order_sha256(payload: dict[str, object]) -> str:
    import hashlib

    canonical = dict(payload)
    canonical.pop("work_order_sha256", None)
    data = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
