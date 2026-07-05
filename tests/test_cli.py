import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tau_agent import AssistantMessage, UserMessage
from tau_agent.session import JsonlSessionStorage, MessageEntry
from tau_ai import (
    FakeProvider,
    ProviderErrorEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
)
from tau_coding import (
    CodingSessionRecord,
    LoopReceiptConfig,
    LoopReceiptMonitorCheckResult,
    LoopReceiptValidationResult,
    SessionManager,
    cli,
    github_handoff,
)
from tau_coding.cli import app, run_print_mode
from tau_coding.paths import TauPaths
from tau_coding.persona_dream_panel_agent import (
    _collect_scillm_sse,
    _mirror_wrapper_jsonl_events,
    _persona_dream_visual_review_receipt,
    _scillm_image_stream_event,
)
from tau_coding.persona_dream_panel_proof import _panel_context as _persona_panel_context
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderSettings,
    load_provider_settings,
)
from tau_coding.rendering import PrintOutputMode
from tau_coding.resources import TauResourcePaths
from tau_coding.system_prompt import BuildSystemPromptOptions, build_system_prompt
from tau_coding.tools import create_coding_tools

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "experiments" / "goal-locked-subagents" / "fixtures"


async def _passing_scillm_auth_preflight(
    contract: dict[str, object],
) -> dict[str, object]:
    del contract
    return {
        "schema": "tau.scillm_proxy_auth_preflight.v1",
        "ran": True,
        "ok": True,
        "base_url": "http://127.0.0.1:4001",
        "endpoint": "/v1/scillm/loop2/capabilities",
        "caller_skill": "tau",
        "status_code": 200,
        "errors": [],
    }


def _valid_cli_handoff_payload() -> dict[str, object]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/chatgpt-lab",
            "target": "issue#123",
        },
        "goal": {
            "goal_id": "goal-cli-handoff",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": "coder",
        "context": {
            "summary": "Coder produced a bounded implementation.",
            "artifacts": ["/tmp/tau/handoff.json"],
        },
        "result": {
            "status": "COMPLETED",
            "summary": "Implementation is ready for read-only review.",
            "evidence": ["/tmp/tau/tests.out"],
        },
        "rationale": "The implementation path now needs independent validation.",
        "next_agent": {
            "name": "reviewer",
            "executor": "either",
            "reason": "Reviewer should inspect evidence before routing onward.",
        },
        "required_evidence": ["review receipt with PASS, NEEDS_CHANGES, or BLOCKED"],
        "stop_condition": "Reviewer posts a schema-valid handoff.",
    }


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "tau 0.1.0"


def test_doctor_command_reports_read_only_runtime_preflight() -> None:
    result = CliRunner().invoke(app, ["doctor"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.doctor.v1"
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["provider_live"] is False
    assert payload["paths"]["pyproject"]["exists"] is True
    assert payload["paths"]["cli"]["exists"] is True
    assert payload["lanes"]["local_cli"]["ready"] is True
    assert payload["lanes"]["provider_live"]["ready"] is False
    assert isinstance(payload["provider_settings"]["provider_count"], int)
    assert "providers" in payload["provider_settings"]
    assert "Herdr pane readiness." in payload["proof_boundary"]["does_not_prove"]
    assert "Live provider/model semantic quality." in payload["proof_boundary"]["does_not_prove"]


def test_cli_init_zero_trust_creates_starter_files(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "init",
            "--profile",
            "zero-trust",
            "--out",
            str(tmp_path),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.init_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert len(payload["created_files"]) == 5
    assert (tmp_path / ".tau" / "policy-profile.json").exists()
    assert (tmp_path / ".tau" / "data-boundary.json").exists()
    assert (tmp_path / ".tau" / "command-policy.json").exists()
    assert (tmp_path / ".tau" / "dag-template.json").exists()
    assert (tmp_path / ".tau" / "README.md").exists()


def test_cli_init_zero_trust_blocks_existing_files(tmp_path: Path) -> None:
    first = CliRunner().invoke(
        app,
        ["init", "--profile", "zero-trust", "--out", str(tmp_path)],
    )
    second = CliRunner().invoke(
        app,
        ["init", "--profile", "zero-trust", "--out", str(tmp_path)],
    )
    payload = json.loads(second.output)

    assert first.exit_code == 0
    assert second.exit_code == 1
    assert payload["schema"] == "tau.init_receipt.v1"
    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert ".tau/policy-profile.json" in payload["existing_files"]


def test_cli_compliance_package_writes_review_bundle(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    out_dir = tmp_path / "package"
    run_dir.mkdir()
    contract_path = tmp_path / "dag-contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "cli-package-test",
                "goal": {
                    "goal_id": "cli-package-test",
                    "goal_hash": "sha256:cli-package-test",
                },
                "policy_profile": {
                    "schema": "tau.policy_profile.v1",
                    "profile_id": "itar-zero-trust-local-only",
                    "default_decision": "deny",
                },
                "data_boundary": {
                    "schema": "tau.data_boundary.v1",
                    "classification": "public",
                    "export_controlled": False,
                    "itar": False,
                    "technical_data": False,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "dag-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.dag_receipt.v1",
                "ok": True,
                "status": "PASS",
                "contract_path": str(contract_path),
                "zero_trust_preflight_receipt": str(
                    run_dir / "zero-trust-preflight-receipt.json"
                ),
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "zero-trust-preflight-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.zero_trust_preflight_receipt.v1",
                "ok": True,
                "status": "PASS",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["compliance-package", str(run_dir), "--out", str(out_dir)],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.compliance_evidence_package.v1"
    assert payload["ok"] is True
    assert (out_dir / "package-manifest.json").exists()
    assert (out_dir / "dag-receipt.json").exists()
    assert (out_dir / "dag-contract.json").exists()
    assert (out_dir / "non-claims.md").exists()


def test_cli_report_writes_static_html_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    report_path = tmp_path / "report.html"
    run_dir.mkdir()
    contract_path = tmp_path / "dag-contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "cli-report-test",
                "goal": {
                    "goal_id": "cli-report-test",
                    "goal_hash": "sha256:cli-report-test",
                },
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "policy_profile": {
                    "schema": "tau.policy_profile.v1",
                    "profile_id": "itar-zero-trust-local-only",
                    "default_decision": "deny",
                },
                "data_boundary": {
                    "schema": "tau.data_boundary.v1",
                    "classification": "public",
                    "export_controlled": False,
                    "itar": False,
                    "technical_data": False,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "dag-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.dag_receipt.v1",
                "ok": True,
                "status": "PASS",
                "verdict": "PASS",
                "contract_path": str(contract_path),
                "alerts": [],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["report", str(run_dir), "--out", str(report_path)])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.run_report_receipt.v1"
    assert payload["ok"] is True
    assert report_path.exists()
    assert Path(str(payload["receipt_path"])).exists()
    assert "Tau Run Report" in report_path.read_text(encoding="utf-8")


def test_cli_zero_trust_doctor_reports_policy_and_boundary_status(tmp_path: Path) -> None:
    receipt_path = tmp_path / "zero-trust-preflight.json"
    result = CliRunner().invoke(
        app,
        [
            "zero-trust-doctor",
            "--policy-profile",
            str(FIXTURES / "zero-trust-policy.json"),
            "--data-boundary",
            str(FIXTURES / "itar-data-boundary.json"),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload == receipt
    assert payload["schema"] == "tau.zero_trust_preflight_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["mocked"] is False
    assert payload["live"] is False
    assert payload["provider_live"] is False
    assert payload["policy_profile"]["schema"] == "tau.policy_profile.v1"
    assert payload["data_boundary"]["schema"] == "tau.data_boundary.v1"
    assert "ITAR compliance." in payload["proof_scope"]["does_not_prove"]


def test_cli_dag_run_zero_trust_missing_boundary_returns_course_correction_json(
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "zero-trust-missing-boundary-dag.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "zero-trust-missing-boundary",
                "goal": {
                    "goal_id": "zero-trust",
                    "goal_version": 1,
                    "goal_hash": "sha256:active-goal",
                },
                "target": {"repo": "grahama1970/tau", "target": "scratch"},
                "policy_profile": str(FIXTURES / "zero-trust-policy.json"),
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "limits": {"max_total_attempts": 2},
                "nodes": [
                    {
                        "id": "coder",
                        "agent": "coder",
                        "executor": "local",
                        "max_attempts": 1,
                        "command_spec": "coder/tau-dispatch-command.json",
                        "required_evidence": [],
                    }
                ],
                "edges": [{"from": "coder", "to": "human"}],
                "required_evidence": [],
                "fail_closed_on": ["goal_hash_mismatch"],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == "tau.dag_receipt.v1"
    assert payload["status"] == "BLOCKED"
    assert payload["dag_error"]["schema"] == "tau.dag_error.v1"
    assert payload["dag_error"]["failure_code"] == "missing_data_boundary"
    assert payload["dag_error"]["recommended_action"] == {
        "type": "repair_then_retry_or_reroute",
        "next_agent": "goal-guardian",
        "reason": "Repair zero-trust policy/data-boundary gates before DAG dispatch.",
    }


def test_cli_handoff_project_writes_dry_run_receipt(tmp_path: Path) -> None:
    handoff_path = tmp_path / "handoff.json"
    receipt_path = tmp_path / "projection" / "receipt.json"
    handoff_path.write_text(json.dumps(_valid_cli_handoff_payload()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-project",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff_projection_receipt.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["target"] == {"repo": "grahama1970/chatgpt-lab", "target": "issue#123"}
    assert payload["labels"]["add"] == ["agent-work", "next:reviewer", "executor:either"]
    assert "<!-- tau-agent-handoff:v1 -->" in payload["comment"]["body"]
    assert receipt == payload


def test_cli_handoff_project_refuses_stale_goal_hash(tmp_path: Path) -> None:
    handoff = _valid_cli_handoff_payload()
    handoff["goal"]["goal_hash"] = "sha256:stale-goal"
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-project",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["comment"] is None
    assert "agent handoff may not change goal.goal_hash" in payload["errors"]


def test_cli_human_goal_change_bridge_writes_handoff_and_receipt(tmp_path: Path) -> None:
    goal_change_path = FIXTURES / "valid-human-goal-change.json"
    handoff_path = tmp_path / "generated" / "start-handoff.json"
    receipt_path = tmp_path / "receipts" / "bridge-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "human-goal-change-bridge",
            str(goal_change_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--trusted-human",
            "--handoff-out",
            str(handoff_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())
    handoff = json.loads(handoff_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.human_goal_change_bridge_receipt.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["trusted_human"] is True
    assert payload["source"] == str(goal_change_path.resolve())
    assert payload["handoff_path"] == str(handoff_path.resolve())
    assert payload["output_schema"] == "tau.agent_handoff.v1"
    assert payload["next_agent"] == "goal-guardian"
    assert payload["handoff_sha256"].startswith("sha256:")
    assert payload["errors"] == []
    assert receipt == payload
    assert payload["start_handoff"] == handoff
    assert handoff["github"] == {
        "repo": "grahama1970/chatgpt-lab",
        "target": "issue#123",
    }
    assert handoff["goal"]["goal_hash"] == "sha256:active-goal"
    assert handoff["previous_subagent"] == "human"
    assert handoff["result"]["status"] == "GOAL_CHANGE_REQUESTED"
    assert "tau.human_goal_change.v1" in handoff["result"]["evidence"][0]
    assert handoff["next_agent"] == {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Goal changes must be reconciled before further work.",
    }


def test_cli_human_goal_change_bridge_refuses_untrusted_author_with_receipt(
    tmp_path: Path,
) -> None:
    goal_change_path = FIXTURES / "valid-human-goal-change.json"
    handoff_path = tmp_path / "generated" / "start-handoff.json"
    receipt_path = tmp_path / "receipts" / "bridge-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "human-goal-change-bridge",
            str(goal_change_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--handoff-out",
            str(handoff_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 1
    assert payload["schema"] == "tau.human_goal_change_bridge_receipt.v1"
    assert payload["ok"] is False
    assert payload["dry_run"] is True
    assert payload["trusted_human"] is False
    assert payload["output_schema"] is None
    assert payload["start_handoff"] is None
    assert "human goal change requires trusted human author" in payload["errors"]
    assert receipt == payload
    assert not handoff_path.exists()


def test_cli_human_goal_change_bridge_start_handoff_enters_command_loop(
    tmp_path: Path,
) -> None:
    goal_change_path = FIXTURES / "valid-human-goal-change.json"
    handoff_path = tmp_path / "generated" / "start-handoff.json"
    bridge_receipt_path = tmp_path / "receipts" / "bridge-receipt.json"

    bridge = CliRunner().invoke(
        app,
        [
            "human-goal-change-bridge",
            str(goal_change_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--trusted-human",
            "--handoff-out",
            str(handoff_path),
            "--receipt",
            str(bridge_receipt_path),
        ],
    )
    assert bridge.exit_code == 0

    agents_root = tmp_path / "agents"
    command_spec_root = tmp_path / "command-specs"
    guardian_spec_root = command_spec_root / "goal-guardian"
    agents_root.mkdir()
    guardian_spec_root.mkdir(parents=True)
    guardian_script = (
        "import json, sys; "
        "payload=json.load(sys.stdin); "
        "payload['previous_subagent']='goal-guardian'; "
        "payload['context']={"
        "'summary':'Goal guardian smoke consumed the generated start handoff.', "
        "'artifacts':payload.get('context', {}).get('artifacts', [])}; "
        "payload['result']={"
        "'status':'PASS', "
        "'summary':'Goal guardian consumed the generated start handoff.', "
        "'evidence':['smoke command consumed generated start handoff']}; "
        "payload['rationale']='Smoke command proves command-loop can consume bridge output.'; "
        "payload['next_agent']={"
        "'name':'human', "
        "'executor':'human', "
        "'reason':'Human receives the reconciliation result.'}; "
        "payload['required_evidence']=['human reviews the reconciliation result']; "
        "payload['stop_condition']='Human receives the reconciliation handoff.'; "
        "print(json.dumps(payload))"
    )
    (guardian_spec_root / "tau-dispatch-command.json").write_text(
        json.dumps({"command": [sys.executable, "-c", guardian_script], "timeout_s": 5}),
        encoding="utf-8",
    )

    loop_receipt_dir = tmp_path / "command-loop-receipts"
    result = CliRunner().invoke(
        app,
        [
            "handoff-command-loop",
            "--start",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(loop_receipt_dir),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            str(command_spec_root),
            "--max-steps",
            "2",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff_command_loop_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "WAITING"
    assert payload["step_count"] == 1
    assert payload["terminal_agent"] == "human"
    assert payload["stop_reason"] == "next_agent_is_human"
    assert payload["dispatches"][0]["selected_agent"] == "goal-guardian"


def test_cli_handoff_project_accepts_agents_root_route(tmp_path: Path) -> None:
    agents_root = tmp_path / "agents"
    external_agent = agents_root / "external-agent"
    external_agent.mkdir(parents=True)
    (external_agent / "AGENTS.md").write_text(
        "---\nid: external-agent\nkind: worker\n---\n# External agent\n",
        encoding="utf-8",
    )
    handoff = _valid_cli_handoff_payload()
    handoff["next_agent"] = {
        "name": "external-agent",
        "executor": "either",
        "reason": "Registry-backed route.",
    }
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-project",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--agents-root",
            str(agents_root),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["next_agent"] == "external-agent"
    assert payload["labels"]["add"] == ["agent-work", "next:external-agent", "executor:either"]


def test_cli_handoff_github_transport_defaults_to_dry_run(tmp_path: Path) -> None:
    handoff_path = tmp_path / "handoff.json"
    receipt_path = tmp_path / "github-transport" / "receipt.json"
    handoff_path.write_text(json.dumps(_valid_cli_handoff_payload()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-github-transport",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.github_handoff_transport_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["mocked"] is False
    assert payload["live"] is False
    assert payload["provider_live"] is False
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["commands"][0][:3] == ["gh", "issue", "comment"]
    assert payload["commands"][1][:3] == ["gh", "issue", "edit"]
    assert receipt == payload


def test_cli_handoff_github_transport_apply_requires_policy_receipt(tmp_path: Path) -> None:
    handoff_path = tmp_path / "handoff.json"
    receipt_path = tmp_path / "github-transport" / "receipt.json"
    handoff_path.write_text(json.dumps(_valid_cli_handoff_payload()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-github-transport",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt",
            str(receipt_path),
            "--apply",
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 1
    assert payload["schema"] == "tau.github_handoff_transport_receipt.v1"
    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["mocked"] is False
    assert payload["live"] is False
    assert payload["provider_live"] is False
    assert payload["applied"] is False
    assert payload["commands"] == []
    assert payload["command_results"] == []
    assert "--github-apply-policy-receipt" in "\n".join(payload["errors"])
    assert receipt == payload


def test_cli_handoff_github_transport_apply_accepts_policy_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff_path = tmp_path / "handoff.json"
    receipt_path = tmp_path / "github-transport" / "receipt.json"
    policy_receipt_path = tmp_path / "github-policy-receipt.json"
    handoff_path.write_text(json.dumps(_valid_cli_handoff_payload()), encoding="utf-8")
    policy_receipt_path.write_text(
        json.dumps(
            {
                "schema": "tau.github_apply_policy_receipt.v1",
                "ok": True,
                "status": "PASS",
                "target": {
                    "repo": "grahama1970/chatgpt-lab",
                    "target": "issue#123",
                },
                "actions": ["comment", "label"],
                "requirements": {
                    "approval_packet": True,
                    "preflight": True,
                    "redaction": True,
                },
                "failed_checks": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(github_handoff, "_run_gh_command", fake_run)

    result = CliRunner().invoke(
        app,
        [
            "handoff-github-transport",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt",
            str(receipt_path),
            "--apply",
            "--github-apply-policy-receipt",
            str(policy_receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["mocked"] is False
    assert payload["live"] is False
    assert payload["provider_live"] is False
    assert payload["dry_run"] is False
    assert payload["applied"] is True
    assert payload["commands"] == commands
    assert len(payload["command_results"]) == 2
    assert receipt == payload


def test_cli_handoff_github_transport_refuses_invalid_projection(tmp_path: Path) -> None:
    handoff = _valid_cli_handoff_payload()
    handoff["next_agent"]["name"] = "unknown-route"
    handoff_path = tmp_path / "handoff.json"
    receipt_path = tmp_path / "github-transport" / "receipt.json"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-github-transport",
            str(handoff_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["mocked"] is False
    assert payload["live"] is False
    assert payload["provider_live"] is False
    assert payload["applied"] is False
    assert payload["commands"] == []
    assert payload["command_results"] == []
    assert "next_agent.name must be one of" in "\n".join(payload["errors"])
    assert receipt == payload


def test_cli_dag_fail_closed_registry_writes_registry_receipt(tmp_path: Path) -> None:
    output_path = tmp_path / "fail-closed-registry.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-fail-closed-registry",
            "--out",
            str(output_path),
        ],
    )
    payload = json.loads(result.output)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload == written
    assert payload["schema"] == "tau.fail_closed_registry.v1"
    assert payload["ok"] is True
    assert payload["status"] == "ACTIVE"
    assert payload["receipt_path"] == str(output_path.resolve())
    assert payload["invariants"]["goal_hash_mismatch"]["severity"] == "BLOCK"
    assert payload["invariants"]["missing_work_order_sha256"]["implemented_by"] == (
        "tau.validators.provider_work_order.sha256"
    )


def test_cli_github_redact_projection_writes_receipt_and_redacted_artifact(
    tmp_path: Path,
) -> None:
    projection_path = tmp_path / "projection.json"
    redacted_path = tmp_path / "projection.redacted.json"
    receipt_path = tmp_path / "redaction-receipt.json"
    projection = {
        "schema": "tau.agent_handoff_projection_receipt.v1",
        "ok": True,
        "target": {"repo": "grahama1970/tau", "target": "issue#47"},
        "comment": {
            "body": (
                "Inspect /home/graham/workspace/experiments/tau/private.txt "
                "with token github_pat_abcdefghijklmnopqrstuvwxyz"
            )
        },
        "labels": {"add": ["agent-work"], "remove": []},
        "credentials": {"token": "ghp_abcdefghijklmnopqrstuvwxyz"},
    }
    projection_path.write_text(json.dumps(projection), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "github-redact-projection",
            "--projection",
            str(projection_path),
            "--out",
            str(redacted_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    redacted = json.loads(redacted_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload["schema"] == "tau.github_projection_redaction_receipt.v1"
    assert payload["ok"] is True
    assert payload == receipt
    assert payload["redaction_count"] == 2
    assert "<redacted-local-path>" in redacted["comment"]["body"]
    assert "<redacted-token>" in redacted["comment"]["body"]
    assert redacted["credentials"] == "<redacted:credentials>"


def test_cli_github_apply_policy_check_writes_policy_receipt(tmp_path: Path) -> None:
    projection_path = tmp_path / "projection.json"
    redacted_path = tmp_path / "projection.redacted.json"
    policy_path = tmp_path / "github-apply-policy.json"
    redaction_receipt_path = tmp_path / "github-redaction-receipt.json"
    approval_receipt_path = tmp_path / "approval-gate-receipt.json"
    receipt_path = tmp_path / "github-apply-policy-receipt.json"
    projection = {
        "schema": "tau.agent_handoff_projection_receipt.v1",
        "ok": True,
        "target": {"repo": "grahama1970/tau", "target": "issue#47"},
        "comment": {"body": "## Tau Agent Handoff\n"},
        "labels": {"add": ["agent-work"], "remove": ["agent-active"]},
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
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    redacted_path.write_text(json.dumps(projection), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    redaction_receipt_path.write_text(
        json.dumps(
            {
                "schema": "tau.github_projection_redaction_receipt.v1",
                "ok": True,
                "status": "PASS",
                "projection": str(projection_path.resolve()),
                "redacted_projection": str(redacted_path.resolve()),
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    approval_receipt_path.write_text(
        json.dumps(
            {
                "schema": "tau.approval_gate_receipt.v1",
                "ok": True,
                "status": "PASS",
                "approved": True,
                "requested_action": "github_apply",
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "github-apply-policy-check",
            "--projection",
            str(projection_path),
            "--policy",
            str(policy_path),
            "--receipt",
            str(receipt_path),
            "--approval-receipt",
            str(approval_receipt_path),
            "--redaction-receipt",
            str(redaction_receipt_path),
            "--preflight-ready",
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload == receipt
    assert payload["schema"] == "tau.github_apply_policy_receipt.v1"
    assert payload["ok"] is True
    assert payload["actions"] == ["comment", "label"]
    assert payload["requirements"]["approval_packet"] is True
    assert payload["requirements"]["redaction"] is True
    assert payload["requirements"]["preflight"] is True


def test_cli_research_source_receipt_writes_review_required_receipt(tmp_path: Path) -> None:
    source_path = tmp_path / "research-source-packet.json"
    receipt_path = tmp_path / "research-source-receipt.json"
    source_path.write_text(
        json.dumps(
            {
                "schema": "tau.research_source_packet.v1",
                "source_type": "paper",
                "method": "arxiv",
                "query": "adaptive DAG references",
                "retrieved_at": "2026-07-05T13:40:00Z",
                "classification": "design_input",
                "sources": [
                    {
                        "title": "Graph of Thoughts",
                        "url": "https://arxiv.org/abs/2308.09687",
                        "arxiv_id": "2308.09687",
                        "relevance": "HIGH",
                        "claims_supported": ["graph reasoning inspiration"],
                    }
                ],
                "summary": "ArXiv reference packet for Tau adaptive DAG critique.",
                "limitations": ["Design input only."],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "research-source-receipt",
            "--source",
            str(source_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload == receipt
    assert payload["schema"] == "tau.research_source_receipt.v1"
    assert payload["ok"] is True
    assert payload["review_required"] is True
    assert payload["source_count"] == 1
    assert payload["arxiv_source_count"] == 1


def test_cli_generated_ticket_github_create_defaults_to_dry_run(tmp_path: Path) -> None:
    ticket_path = tmp_path / "generated-ticket.json"
    receipt_path = tmp_path / "generated-ticket-transport" / "receipt.json"
    ticket = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())
    ticket["github"]["repo"] = "grahama1970/tau"
    ticket_path.write_text(json.dumps(ticket), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "generated-ticket-github-create",
            str(ticket_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.github_generated_ticket_transport_receipt.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["target"] == {"repo": "grahama1970/tau", "target": "new"}
    assert payload["commands"][0][:3] == ["gh", "issue", "create"]
    assert receipt == payload


def test_cli_generated_ticket_github_create_refuses_invalid_ticket(tmp_path: Path) -> None:
    ticket_path = tmp_path / "generated-ticket.json"
    receipt_path = tmp_path / "generated-ticket-transport" / "receipt.json"
    ticket = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())
    ticket["previous_subagent"] = "coder"
    ticket_path.write_text(json.dumps(ticket), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "generated-ticket-github-create",
            str(ticket_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 1
    assert payload["schema"] == "tau.github_generated_ticket_transport_receipt.v1"
    assert payload["ok"] is False
    assert payload["commands"] == []
    assert "previous_subagent may not create tickets: coder" in payload["errors"]
    assert receipt == payload


def test_cli_handoff_chain_dry_run_writes_receipt_dir(tmp_path: Path) -> None:
    first = _valid_cli_handoff_payload()
    second = _valid_cli_handoff_payload()
    second["previous_subagent"] = "reviewer"
    second["result"] = {
        "status": "PASS",
        "summary": "Reviewer accepted the evidence.",
        "evidence": ["/tmp/tau/review.json"],
    }
    second["next_agent"] = {
        "name": "releaser",
        "executor": "either",
        "reason": "Release gate can inspect the review receipt.",
    }
    first_path = tmp_path / "handoff-001.json"
    second_path = tmp_path / "handoff-002.json"
    receipt_dir = tmp_path / "chain-receipts"
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-chain-dry-run",
            str(first_path),
            str(second_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)
    chain_receipt = json.loads((receipt_dir / "chain-receipt.json").read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff_chain_receipt.v1"
    assert payload["ok"] is True
    assert payload["handoff_count"] == 2
    assert payload["artifacts"] == [
        str(receipt_dir.resolve() / "handoff-001.receipt.json"),
        str(receipt_dir.resolve() / "handoff-002.receipt.json"),
    ]
    assert chain_receipt == payload
    assert (receipt_dir / "handoff-001.receipt.json").exists()
    assert (receipt_dir / "handoff-002.receipt.json").exists()


def test_cli_handoff_chain_dry_run_refuses_discontinuity(tmp_path: Path) -> None:
    first = _valid_cli_handoff_payload()
    second = _valid_cli_handoff_payload()
    second["previous_subagent"] = "coder"
    first_path = tmp_path / "handoff-001.json"
    second_path = tmp_path / "handoff-002.json"
    receipt_dir = tmp_path / "chain-receipts"
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-chain-dry-run",
            str(first_path),
            str(second_path),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert "previous_subagent must equal prior next_agent" in "\n".join(payload["errors"])
    assert (receipt_dir / "chain-receipt.json").exists()


def test_cli_handoff_loop_dry_run_follows_response_dir(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    reviewer = _valid_cli_handoff_payload()
    reviewer["previous_subagent"] = "reviewer"
    reviewer["result"] = {
        "status": "PASS",
        "summary": "Reviewer accepted the local dry-run evidence.",
        "evidence": ["/tmp/tau/review.json"],
    }
    reviewer["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human decides whether to authorize live GitHub mutation.",
    }
    start_path = tmp_path / "start.json"
    responses_dir = tmp_path / "responses"
    receipt_dir = tmp_path / "loop-receipts"
    responses_dir.mkdir()
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (responses_dir / "reviewer.json").write_text(json.dumps(reviewer), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-loop-dry-run",
            "--start",
            str(start_path),
            "--responses-dir",
            str(responses_dir),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
            "--max-steps",
            "3",
        ],
    )
    payload = json.loads(result.output)
    loop_receipt = json.loads((receipt_dir / "loop-receipt.json").read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff_loop_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "WAITING"
    assert payload["step_count"] == 2
    assert payload["terminal_agent"] == "human"
    assert payload["stop_reason"] == "next_agent_is_human"
    assert loop_receipt == payload
    assert (receipt_dir / "loop-step-001.receipt.json").exists()
    assert (receipt_dir / "loop-step-002.receipt.json").exists()


def test_cli_handoff_loop_dry_run_waits_for_missing_response(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    start_path = tmp_path / "start.json"
    responses_dir = tmp_path / "responses"
    receipt_dir = tmp_path / "loop-receipts"
    responses_dir.mkdir()
    start_path.write_text(json.dumps(start), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-loop-dry-run",
            "--start",
            str(start_path),
            "--responses-dir",
            str(responses_dir),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["status"] == "WAITING"
    assert payload["step_count"] == 1
    assert payload["terminal_agent"] == "reviewer"
    assert payload["stop_reason"] == "missing_agent_response"
    assert (receipt_dir / "loop-receipt.json").exists()


def test_cli_handoff_loop_dry_run_refuses_discontinuity(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    reviewer = _valid_cli_handoff_payload()
    reviewer["previous_subagent"] = "coder"
    start_path = tmp_path / "start.json"
    responses_dir = tmp_path / "responses"
    receipt_dir = tmp_path / "loop-receipts"
    responses_dir.mkdir()
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (responses_dir / "reviewer.json").write_text(json.dumps(reviewer), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-loop-dry-run",
            "--start",
            str(start_path),
            "--responses-dir",
            str(responses_dir),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["stop_reason"] == "route_discontinuity"
    assert "previous_subagent must equal prior next_agent" in "\n".join(payload["errors"])
    assert (receipt_dir / "loop-receipt.json").exists()


def test_cli_handoff_dispatch_once_consumes_selected_response(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    reviewer = _valid_cli_handoff_payload()
    reviewer["previous_subagent"] = "reviewer"
    reviewer["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human chooses whether to continue live mutation.",
    }
    start_path = tmp_path / "start.json"
    responses_dir = tmp_path / "responses"
    receipt_dir = tmp_path / "dispatch-receipts"
    responses_dir.mkdir()
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (responses_dir / "reviewer.json").write_text(json.dumps(reviewer), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-once",
            "--start",
            str(start_path),
            "--responses-dir",
            str(responses_dir),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads((receipt_dir / "dispatch-receipt.json").read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff_dispatch_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "COMPLETED"
    assert payload["selected_agent"] == "reviewer"
    assert payload["stop_reason"] == "response_consumed"
    assert payload["mocked"] is True
    assert payload["live"] is False
    assert receipt == payload
    assert (receipt_dir / "start-handoff.receipt.json").exists()
    assert (receipt_dir / "reviewer-response.receipt.json").exists()


def test_cli_handoff_dispatch_once_blocks_invalid_response(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    reviewer = _valid_cli_handoff_payload()
    reviewer["previous_subagent"] = "coder"
    start_path = tmp_path / "start.json"
    responses_dir = tmp_path / "responses"
    receipt_dir = tmp_path / "dispatch-receipts"
    responses_dir.mkdir()
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (responses_dir / "reviewer.json").write_text(json.dumps(reviewer), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-once",
            "--start",
            str(start_path),
            "--responses-dir",
            str(responses_dir),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["stop_reason"] == "invalid_agent_response"
    assert "response.previous_subagent must equal selected_agent" in "\n".join(payload["errors"])
    assert (receipt_dir / "dispatch-receipt.json").exists()


def test_cli_handoff_dispatch_command_consumes_stdout_response(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    reviewer = _valid_cli_handoff_payload()
    reviewer["previous_subagent"] = "reviewer"
    response_path = tmp_path / "reviewer-response.json"
    command_spec = tmp_path / "command-spec.json"
    receipt_dir = tmp_path / "command-dispatch-receipts"
    start_path = tmp_path / "start.json"
    start_path.write_text(json.dumps(start), encoding="utf-8")
    response_path.write_text(json.dumps(reviewer), encoding="utf-8")
    command_spec.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; print(Path({str(response_path)!r}).read_text())",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-command",
            "--start",
            str(start_path),
            "--command-spec",
            str(command_spec),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads((receipt_dir / "dispatch-receipt.json").read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff_dispatch_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "COMPLETED"
    assert payload["selected_agent"] == "reviewer"
    assert payload["runner"] == "command"
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["command_results"][0]["exit_code"] == 0
    assert receipt == payload


def test_cli_handoff_dispatch_command_blocks_malformed_stdout(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    command_spec = tmp_path / "command-spec.json"
    receipt_dir = tmp_path / "command-dispatch-receipts"
    start_path = tmp_path / "start.json"
    start_path.write_text(json.dumps(start), encoding="utf-8")
    command_spec.write_text(
        json.dumps({"command": [sys.executable, "-c", "print('not json')"], "timeout_s": 5}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-command",
            "--start",
            str(start_path),
            "--command-spec",
            str(command_spec),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["stop_reason"] == "invalid_command_json"
    assert payload["command_results"][0]["exit_code"] == 0
    assert (receipt_dir / "dispatch-receipt.json").exists()


def test_cli_handoff_dispatch_agent_command_uses_registry_spec(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    reviewer = _valid_cli_handoff_payload()
    reviewer["previous_subagent"] = "reviewer"
    agents_root = tmp_path / "agents"
    reviewer_dir = agents_root / "reviewer"
    response_path = tmp_path / "reviewer-response.json"
    receipt_dir = tmp_path / "agent-command-dispatch-receipts"
    start_path = tmp_path / "start.json"
    reviewer_dir.mkdir(parents=True)
    (reviewer_dir / "AGENTS.md").write_text("---\nid: reviewer\n---\n", encoding="utf-8")
    start_path.write_text(json.dumps(start), encoding="utf-8")
    response_path.write_text(json.dumps(reviewer), encoding="utf-8")
    (reviewer_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; print(Path({str(response_path)!r}).read_text())",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-agent-command",
            "--start",
            str(start_path),
            "--agents-root",
            str(agents_root),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["status"] == "COMPLETED"
    assert payload["selected_agent"] == "reviewer"
    assert payload["runner"] == "command"
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["command_results"][0]["exit_code"] == 0


def test_cli_handoff_dispatch_agent_command_writes_blocked_receipt_when_spec_missing(
    tmp_path: Path,
) -> None:
    start = _valid_cli_handoff_payload()
    agents_root = tmp_path / "agents"
    reviewer_dir = agents_root / "reviewer"
    receipt_dir = tmp_path / "agent-command-dispatch-receipts"
    start_path = tmp_path / "start.json"
    reviewer_dir.mkdir(parents=True)
    (reviewer_dir / "AGENTS.md").write_text("---\nid: reviewer\n---\n", encoding="utf-8")
    start_path.write_text(json.dumps(start), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-agent-command",
            "--start",
            str(start_path),
            "--agents-root",
            str(agents_root),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads((receipt_dir / "dispatch-receipt.json").read_text())

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["selected_agent"] == "reviewer"
    assert payload["stop_reason"] == "missing_agent_command_spec"
    assert "agent dispatch command spec missing" in "\n".join(payload["errors"])
    assert receipt == payload


def test_cli_handoff_agent_adapter_emits_tau_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    start = _valid_cli_handoff_payload()
    monkeypatch.setenv("TAU_HANDOFF_SELECTED_AGENT", "reviewer")

    result = CliRunner().invoke(
        app,
        [
            "handoff-agent-adapter",
            "--result-summary",
            "Reviewer adapter consumed the start handoff.",
            "--next-agent",
            "human",
            "--next-executor",
            "human",
            "--next-reason",
            "Human should decide the next bounded step.",
        ],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff.v1"
    assert payload["previous_subagent"] == "reviewer"
    assert payload["goal"] == start["goal"]
    assert payload["github"] == start["github"]
    assert payload["next_agent"]["name"] == "human"


def test_cli_subagent_receipt_from_handoff_writes_schema_receipt(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    output_path = tmp_path / "subagent-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "subagent-receipt-from-handoff",
            "--run-id",
            "route-answer-reviewer-001",
            "--subagent",
            "reviewer",
            "--actor-type",
            "tau",
            "--output",
            str(output_path),
        ],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload == written
    assert payload["schema"] == "tau.subagent_receipt.v1"
    assert payload["goal"] == {
        "goal_id": "goal-cli-handoff",
        "goal_version": 1,
        "goal_hash": "sha256:active-goal",
        "immutable_goal_preserved": True,
    }
    assert payload["context"]["run_id"] == "route-answer-reviewer-001"
    assert payload["context"]["subagent"] == "reviewer"
    assert payload["context"]["actor_type"] == "tau"
    assert payload["context"]["ticket"] == "issue#123"
    assert payload["result"]["status"] == "COMPLETED"
    assert payload["result"]["mocked"] is False
    assert payload["result"]["live"] is True
    assert payload["evidence"] == ["/tmp/tau/tests.out"]
    assert payload["next"] == {
        "subagent": "reviewer",
        "executor": "either",
        "reason": "Reviewer should inspect evidence before routing onward.",
    }


def test_cli_subagent_receipt_from_handoff_refuses_unsupported_status(
    tmp_path: Path,
) -> None:
    start = _valid_cli_handoff_payload()
    result_payload = start["result"]
    assert isinstance(result_payload, dict)
    result_payload["status"] = "NEEDS_REVIEW"

    result = CliRunner().invoke(
        app,
        [
            "subagent-receipt-from-handoff",
            "--run-id",
            "route-answer-reviewer-001",
            "--subagent",
            "reviewer",
            "--output",
            str(tmp_path / "subagent-receipt.json"),
        ],
        input=json.dumps(start),
    )

    assert result.exit_code != 0
    assert "cannot become subagent" in result.output
    assert "receipt" in result.output


def test_cli_handoff_research_auditor_adapter_refuses_without_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "research-auditor",
        "executor": "either",
        "reason": "Fresh research is required before Tau may answer.",
    }
    monkeypatch.setenv("TAU_HANDOFF_SELECTED_AGENT", "research-auditor")

    result = CliRunner().invoke(
        app,
        ["handoff-research-auditor-adapter"],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff.v1"
    assert payload["previous_subagent"] == "research-auditor"
    assert payload["result"]["status"] == "REFUSED"
    assert "no Brave/WebGPT call was made" in payload["result"]["summary"]
    assert "context.research_authorization.approved=true" in payload["required_evidence"][0]
    assert payload["next_agent"] == {
        "name": "human",
        "executor": "human",
        "reason": (
            "Human must approve a schema-valid fresh research route before Tau calls "
            "Brave Search, WebGPT, or another external research lane."
        ),
    }


def test_cli_handoff_research_auditor_adapter_accepts_explicit_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "research-auditor",
        "executor": "either",
        "reason": "Fresh research is required before Tau may answer.",
    }
    context = start["context"]
    assert isinstance(context, dict)
    context["research_authorization"] = {
        "approved": True,
        "method": "brave-search",
        "reason": "Human explicitly requested fresh source retrieval.",
    }
    monkeypatch.setenv("TAU_HANDOFF_SELECTED_AGENT", "research-auditor")

    result = CliRunner().invoke(
        app,
        ["handoff-research-auditor-adapter"],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff.v1"
    assert payload["previous_subagent"] == "research-auditor"
    assert payload["result"]["status"] == "NEEDS_AGENT"
    assert "no external research receipt has been produced" in payload["result"]["summary"]
    assert payload["result"]["evidence"] == [
        "context.research_authorization.approved=true",
        "context.research_authorization.method=brave-search",
        "context.research_authorization.receipt_path missing",
    ]
    assert payload["next_agent"]["name"] == "human"
    assert "External research receipt for brave-search" in payload["required_evidence"][0]


def test_cli_external_research_receipt_writes_schema_valid_receipt(tmp_path: Path) -> None:
    output_path = tmp_path / "receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "external-research-receipt",
            "--query",
            "latest Chutes pricing",
            "--method",
            "brave-search",
            "--retrieved-at",
            "2026-06-28T02:20:00Z",
            "--summary",
            "One explicit source was attached.",
            "--source",
            "Chutes pricing|https://chutes.ai/pricing",
            "--output",
            str(output_path),
        ],
    )
    payload = json.loads(result.output)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload == written
    assert payload == {
        "method": "brave-search",
        "query": "latest Chutes pricing",
        "retrieved_at": "2026-06-28T02:20:00Z",
        "schema": "tau.external_research_receipt.v1",
        "sources": [
            {
                "title": "Chutes pricing",
                "url": "https://chutes.ai/pricing",
            }
        ],
        "summary": "One explicit source was attached.",
    }


def test_cli_external_research_receipt_refuses_malformed_source() -> None:
    result = CliRunner().invoke(
        app,
        [
            "external-research-receipt",
            "--query",
            "latest Chutes pricing",
            "--source",
            "https://chutes.ai/pricing",
        ],
    )

    assert result.exit_code != 0
    assert "--source must use title|url format" in result.output


def test_cli_external_research_receipt_can_call_brave_without_key_leak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[object] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        serialized_args = json.dumps(args)
        assert "BRAVE_API_KEY" not in serialized_args
        assert "BRAVE_SEARCH_API_KEY" not in serialized_args
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "query": "latest Chutes pricing",
                    "results": [
                        {
                            "title": "Chutes pricing",
                            "url": "https://chutes.ai/pricing",
                        }
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setenv("BRAVE_API_KEY", "must-not-appear")
    output_path = tmp_path / "brave-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "external-research-receipt",
            "--from-brave",
            "--query",
            "latest Chutes pricing",
            "--count",
            "1",
            "--retrieved-at",
            "2026-06-28T02:35:00Z",
            "--output",
            str(output_path),
        ],
    )
    payload = json.loads(result.output)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert len(calls) == 1
    assert payload == written
    assert payload["schema"] == "tau.external_research_receipt.v1"
    assert payload["method"] == "brave-search"
    assert payload["sources"] == [
        {
            "title": "Chutes pricing",
            "url": "https://chutes.ai/pricing",
        }
    ]
    assert "must-not-appear" not in result.output


def test_cli_external_research_receipt_refuses_failed_brave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args,
            returncode=7,
            stdout="",
            stderr="network unavailable",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = CliRunner().invoke(
        app,
        [
            "external-research-receipt",
            "--from-brave",
            "--query",
            "latest Chutes pricing",
        ],
    )

    assert result.exit_code != 0
    assert "Brave Search failed with exit code 7" in result.output
    assert "network unavailable" in result.output


def test_cli_handoff_research_auditor_adapter_refuses_invalid_research_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "invalid-research-receipt.json"
    receipt_path.write_text(
        json.dumps({"schema": "tau.external_research_receipt.v1", "method": "brave-search"}),
        encoding="utf-8",
    )
    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "research-auditor",
        "executor": "either",
        "reason": "Fresh research is required before Tau may answer.",
    }
    context = start["context"]
    assert isinstance(context, dict)
    context["research_authorization"] = {
        "approved": True,
        "method": "brave-search",
        "receipt_path": str(receipt_path),
    }
    monkeypatch.setenv("TAU_HANDOFF_SELECTED_AGENT", "research-auditor")

    result = CliRunner().invoke(
        app,
        ["handoff-research-auditor-adapter"],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["status"] == "REFUSED"
    assert "receipt was invalid" in payload["context"]["summary"]
    assert any("query must be a non-empty string" in item for item in payload["result"]["evidence"])
    assert payload["next_agent"]["name"] == "human"


def test_cli_handoff_research_auditor_adapter_accepts_cli_produced_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_result = CliRunner().invoke(
        app,
        [
            "external-research-receipt",
            "--query",
            "latest Chutes pricing",
            "--method",
            "brave-search",
            "--retrieved-at",
            "2026-06-28T02:20:00Z",
            "--source",
            "Chutes pricing|https://chutes.ai/pricing",
            "--output",
            str(receipt_path),
        ],
    )
    assert receipt_result.exit_code == 0

    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "research-auditor",
        "executor": "either",
        "reason": "Fresh research is required before Tau may answer.",
    }
    context = start["context"]
    assert isinstance(context, dict)
    context["research_authorization"] = {
        "approved": True,
        "method": "brave-search",
        "receipt_path": str(receipt_path),
    }
    monkeypatch.setenv("TAU_HANDOFF_SELECTED_AGENT", "research-auditor")

    result = CliRunner().invoke(
        app,
        ["handoff-research-auditor-adapter"],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["status"] == "COMPLETED"
    assert payload["next_agent"]["name"] == "reviewer"
    assert (
        f"context.research_authorization.receipt_path={receipt_path}"
        in payload["result"]["evidence"]
    )


def test_cli_handoff_research_auditor_adapter_routes_valid_receipt_to_reviewer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "brave-search-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": "tau.external_research_receipt.v1",
                "method": "brave-search",
                "query": "latest Chutes pricing",
                "retrieved_at": "2026-06-28T01:58:00Z",
                "summary": "Two current source snippets were retrieved.",
                "sources": [
                    {
                        "title": "Chutes pricing",
                        "url": "https://chutes.ai/pricing",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "research-auditor",
        "executor": "either",
        "reason": "Fresh research is required before Tau may answer.",
    }
    context = start["context"]
    assert isinstance(context, dict)
    context["research_authorization"] = {
        "approved": True,
        "method": "brave-search",
        "receipt_path": str(receipt_path),
    }
    monkeypatch.setenv("TAU_HANDOFF_SELECTED_AGENT", "research-auditor")

    result = CliRunner().invoke(
        app,
        ["handoff-research-auditor-adapter"],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["status"] == "COMPLETED"
    assert "schema-valid external research receipt" in payload["result"]["summary"]
    assert (
        f"context.research_authorization.receipt_path={receipt_path}"
        in payload["result"]["evidence"]
    )
    assert "external_research_receipt.sources=1" in payload["result"]["evidence"]
    assert payload["next_agent"] == {
        "name": "reviewer",
        "executor": "either",
        "reason": "Reviewer should inspect the external research receipt before Tau answers.",
    }
    assert str(receipt_path) in payload["context"]["artifacts"]


def test_cli_handoff_goal_guardian_adapter_emits_preserved_goal_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Goal preservation should be checked first.",
    }
    monkeypatch.setenv("TAU_HANDOFF_ACTIVE_GOAL_HASH", "sha256:active-goal")

    result = CliRunner().invoke(
        app,
        [
            "handoff-goal-guardian-adapter",
            "--next-agent",
            "project-or-harness-verifier",
            "--next-executor",
            "local",
        ],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff.v1"
    assert payload["previous_subagent"] == "goal-guardian"
    assert payload["goal"] == start["goal"]
    assert payload["result"]["status"] == "PASS"
    assert payload["next_agent"]["name"] == "project-or-harness-verifier"


def test_cli_handoff_goal_guardian_adapter_reconciles_human_goal_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = json.loads((FIXTURES / "valid-human-goal-change.json").read_text())
    start = _valid_cli_handoff_payload()
    start["github"] = source["github"]
    start["goal"] = source["goal"]
    start["previous_subagent"] = "human"
    start["context"] = {
        "summary": "Trusted human requested immutable goal-change reconciliation.",
        "artifacts": [str(FIXTURES / "valid-human-goal-change.json")],
        "human_goal_change": {
            "schema": source["schema"],
            "source": str(FIXTURES / "valid-human-goal-change.json"),
            "new_goal": source["new_goal"],
            "rationale": source["rationale"],
        },
    }
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Goal changes must be reconciled before further work.",
    }
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("TAU_HANDOFF_ACTIVE_GOAL_HASH", "sha256:active-goal")
    monkeypatch.setenv("TAU_HANDOFF_COMMAND_ARTIFACT_DIR", str(artifact_dir))

    result = CliRunner().invoke(
        app,
        ["handoff-goal-guardian-adapter"],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)
    receipt_path = artifact_dir / "goal-guardian-reconciliation-receipt.json"
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff.v1"
    assert payload["previous_subagent"] == "goal-guardian"
    assert payload["result"]["status"] == "REQUIRES_HUMAN_GOAL_VERSION"
    assert payload["next_agent"] == {
        "name": "human",
        "executor": "human",
        "reason": "Human must create or reject the next immutable goal version.",
    }
    assert str(receipt_path) in payload["context"]["artifacts"]
    assert payload["context"]["goal_guardian_reconciliation"] == receipt
    assert receipt["schema"] == "tau.goal_guardian_reconciliation_receipt.v1"
    assert receipt["decision"] == "REQUIRES_HUMAN_GOAL_VERSION"
    assert receipt["new_goal"] == source["new_goal"]
    assert receipt["next_agent"] == "human"


def test_cli_handoff_goal_guardian_adapter_classifies_ticket_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = json.loads((FIXTURES / "valid-human-goal-change.json").read_text())
    ticket_source = FIXTURES / "goal-guardian-ticket-source.json"
    start = _valid_cli_handoff_payload()
    start["github"] = source["github"]
    start["goal"] = source["goal"]
    start["previous_subagent"] = "human"
    start["context"] = {
        "summary": "Trusted human requested immutable goal-change reconciliation.",
        "artifacts": [str(FIXTURES / "valid-human-goal-change.json")],
        "human_goal_change": {
            "schema": source["schema"],
            "source": str(FIXTURES / "valid-human-goal-change.json"),
            "new_goal": source["new_goal"],
            "rationale": source["rationale"],
        },
    }
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Goal changes must be reconciled before further work.",
    }
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("TAU_HANDOFF_ACTIVE_GOAL_HASH", "sha256:active-goal")
    monkeypatch.setenv("TAU_HANDOFF_COMMAND_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.setenv("TAU_GOAL_GUARDIAN_TICKET_SOURCE", str(tmp_path / "missing-source.json"))

    result = CliRunner().invoke(
        app,
        ["handoff-goal-guardian-adapter", "--ticket-source", str(ticket_source)],
        input=json.dumps(start),
    )
    payload = json.loads(result.output)
    receipt = payload["context"]["goal_guardian_reconciliation"]
    reconciliation = receipt["open_ticket_reconciliation"]

    assert result.exit_code == 0
    assert reconciliation["status"] == "classified"
    assert reconciliation["source"] == str(ticket_source.resolve())
    assert reconciliation["counts"] == {
        "keep": 1,
        "close": 1,
        "migrate": 1,
        "regenerate": 1,
    }
    assert reconciliation["keep"][0]["id"] == "issue#101"
    assert reconciliation["migrate"][0]["id"] == "issue#102"
    assert reconciliation["regenerate"][0]["id"] == "issue#103"
    assert reconciliation["close"][0]["id"] == "issue#104"
    assert receipt["next_agent"] == "human"


def test_cli_handoff_goal_guardian_adapter_refuses_stale_goal_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _valid_cli_handoff_payload()
    monkeypatch.setenv("TAU_HANDOFF_ACTIVE_GOAL_HASH", "sha256:different")

    result = CliRunner().invoke(
        app,
        ["handoff-goal-guardian-adapter"],
        input=json.dumps(start),
    )

    assert result.exit_code != 0
    assert "goal-guardian refused stale or changed goal hash" in result.output


def test_cli_handoff_dispatch_agent_command_accepts_adapter_command(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    agents_root = tmp_path / "agents"
    reviewer_dir = agents_root / "reviewer"
    receipt_dir = tmp_path / "adapter-command-dispatch-receipts"
    start_path = tmp_path / "start.json"
    reviewer_dir.mkdir(parents=True)
    (reviewer_dir / "AGENTS.md").write_text("---\nid: reviewer\n---\n", encoding="utf-8")
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (reviewer_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import json; "
                        "from tau_coding.cli import project_agent_handoff_adapter_command; "
                        "print(json.dumps(project_agent_handoff_adapter_command("
                        "result_status='COMPLETED', "
                        "result_summary='Reviewer adapter consumed the start handoff.', "
                        "next_agent='human', "
                        "next_executor='human', "
                        "next_reason='Human should decide the next bounded step.', "
                        "required_evidence='Human posts the next schema-valid route.', "
                        "stop_condition='Human route is posted.'"
                        ")))"
                    ),
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-agent-command",
            "--start",
            str(start_path),
            "--agents-root",
            str(agents_root),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["status"] == "COMPLETED"
    assert payload["selected_agent"] == "reviewer"
    assert payload["response_projection"]["next_agent"] == "human"
    assert payload["command_results"][0]["exit_code"] == 0


def test_cli_handoff_dispatch_agent_command_uses_command_spec_overlay(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "project-or-harness-verifier",
        "executor": "local",
        "reason": "Use a real registry identity with a Tau-owned command spec.",
    }
    agents_root = tmp_path / "agents"
    command_spec_root = tmp_path / "command-specs"
    agent_dir = agents_root / "project-or-harness-verifier"
    spec_dir = command_spec_root / "project-or-harness-verifier"
    receipt_dir = tmp_path / "overlay-command-dispatch-receipts"
    start_path = tmp_path / "start.json"
    agent_dir.mkdir(parents=True)
    spec_dir.mkdir(parents=True)
    (agent_dir / "AGENTS.md").write_text(
        "---\nid: project-or-harness-verifier\n---\n",
        encoding="utf-8",
    )
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import json; "
                        "from tau_coding.cli import project_agent_handoff_adapter_command; "
                        "print(json.dumps(project_agent_handoff_adapter_command("
                        "result_status='COMPLETED', "
                        "result_summary='Overlay adapter consumed the start handoff.', "
                        "next_agent='human', "
                        "next_executor='human', "
                        "next_reason='Human should decide the next bounded step.', "
                        "required_evidence='Human posts the next schema-valid route.', "
                        "stop_condition='Human route is posted.'"
                        ")))"
                    ),
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-agent-command",
            "--start",
            str(start_path),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            str(command_spec_root),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["selected_agent"] == "project-or-harness-verifier"
    assert payload["response_projection"]["next_agent"] == "human"


def test_cli_handoff_dispatch_agent_command_accepts_builtin_goal_guardian_overlay(
    tmp_path: Path,
) -> None:
    start = _valid_cli_handoff_payload()
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Goal preservation should be checked first.",
    }
    agents_root = tmp_path / "agents"
    command_spec_root = tmp_path / "command-specs"
    verifier_dir = agents_root / "project-or-harness-verifier"
    guardian_spec_dir = command_spec_root / "goal-guardian"
    receipt_dir = tmp_path / "goal-guardian-dispatch-receipts"
    start_path = tmp_path / "start.json"
    verifier_dir.mkdir(parents=True)
    guardian_spec_dir.mkdir(parents=True)
    (verifier_dir / "AGENTS.md").write_text(
        "---\nid: project-or-harness-verifier\n---\n",
        encoding="utf-8",
    )
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (guardian_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
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
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "handoff-dispatch-agent-command",
            "--start",
            str(start_path),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            str(command_spec_root),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["selected_agent"] == "goal-guardian"
    assert payload["response_projection"]["next_agent"] == "project-or-harness-verifier"


def test_cli_handoff_command_loop_reaches_human(tmp_path: Path) -> None:
    start = _valid_cli_handoff_payload()
    start["previous_subagent"] = "human"
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Goal preservation should be checked first.",
    }
    agents_root = tmp_path / "agents"
    command_spec_root = tmp_path / "command-specs"
    verifier_dir = agents_root / "project-or-harness-verifier"
    guardian_spec_dir = command_spec_root / "goal-guardian"
    verifier_spec_dir = command_spec_root / "project-or-harness-verifier"
    receipt_dir = tmp_path / "command-loop-receipts"
    start_path = tmp_path / "start.json"
    verifier_dir.mkdir(parents=True)
    guardian_spec_dir.mkdir(parents=True)
    verifier_spec_dir.mkdir(parents=True)
    (verifier_dir / "AGENTS.md").write_text(
        "---\nid: project-or-harness-verifier\n---\n",
        encoding="utf-8",
    )
    start_path.write_text(json.dumps(start), encoding="utf-8")
    (guardian_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
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
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )
    (verifier_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
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
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "handoff-command-loop",
            "--start",
            str(start_path),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            str(command_spec_root),
            "--active-goal-hash",
            "sha256:active-goal",
            "--receipt-dir",
            str(receipt_dir),
            "--max-steps",
            "4",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == "tau.agent_handoff_command_loop_receipt.v1"
    assert payload["ok"] is True
    assert payload["status"] == "WAITING"
    assert payload["step_count"] == 2
    assert payload["terminal_agent"] == "human"
    assert [dispatch["selected_agent"] for dispatch in payload["dispatches"]] == [
        "goal-guardian",
        "project-or-harness-verifier",
    ]
    assert (receipt_dir / "command-loop-receipt.json").exists()


def test_cli_persona_dream_panel_proof_writes_first_blocker(tmp_path: Path) -> None:
    out_dir = tmp_path / "persona-proof"
    agents_root = tmp_path / "agents"
    agents_root.mkdir()

    result = CliRunner().invoke(
        app,
        [
            "persona-dream-panel-proof",
            "--out-dir",
            str(out_dir),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            "experiments/goal-locked-subagents/agent-command-specs",
            "--active-goal-hash",
            "sha256:test-persona-dream-panel-proof",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    loop = json.loads(
        (out_dir / "command-loop" / "command-loop-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload == manifest
    assert manifest["schema"] == "tau.persona_dream_panel_proof.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["selected_agents"] == [
        "panel-creator",
        "panel-reviewer",
        "persona-dream-panel-repair-gate",
    ]
    assert manifest["first_blocker"]["previous_subagent"] == "panel-reviewer"
    assert manifest["first_blocker"]["status"] == "INSUFFICIENT_EVIDENCE"
    repair_receipt = json.loads(
        (
            out_dir
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-003"
            / "panel_repair_gate_receipt.json"
        ).read_text(encoding="utf-8")
    )
    run_root_repair_receipt = json.loads(
        (out_dir / "receipts" / "panel_repair_gate_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    panel_source_receipt = json.loads(
        (out_dir / "receipts" / "panel_source_receipt.json").read_text(encoding="utf-8")
    )
    assert repair_receipt["schema"] == "persona_dream.panel_repair_gate_receipt.v1"
    assert run_root_repair_receipt == repair_receipt
    assert panel_source_receipt["schema"] == "persona_dream.panel_source_receipt.v1"
    assert panel_source_receipt["status"] == "BLOCKED"
    assert "provider_eligibility_not_true" in panel_source_receipt["blockers"]
    assert manifest["dry_run_one_scene_kling_request"] is None
    assert loop["mocked"] is False
    assert loop["live"] is True
    assert loop["status"] == "WAITING"
    assert loop["terminal_agent"] == "human"


def test_cli_persona_dream_panel_proof_uses_supplied_panel_evidence(tmp_path: Path) -> None:
    out_dir = tmp_path / "persona-proof"
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    run_root = tmp_path / "run-root"
    image = run_root / "artifacts" / "panel_custom.png"
    visual_review = run_root / "receipts" / "visual_review_receipt.json"
    evidence = tmp_path / "panel-evidence.json"
    image.parent.mkdir(parents=True)
    visual_review.parent.mkdir(parents=True)
    image.write_bytes(b"custom panel bytes")
    visual_review.write_text(
        json.dumps(
            {
                "schema": "persona_dream.visual_review_receipt.v1",
                "status": "NEEDS_CHANGES",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence.write_text(
        json.dumps(
            {
                "panel_id": "panel_custom",
                "run_root": str(run_root),
                "image_path": "artifacts/panel_custom.png",
                "visual_review_receipt": "receipts/visual_review_receipt.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "persona-dream-panel-proof",
            "--out-dir",
            str(out_dir),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            "experiments/goal-locked-subagents/agent-command-specs",
            "--active-goal-hash",
            "sha256:test-persona-dream-panel-proof",
            "--panel-evidence",
            str(evidence),
        ],
    )

    assert result.exit_code == 0, result.output
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    creator_receipt = json.loads(
        (
            out_dir
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-001"
            / "panel_creator_receipt.json"
        ).read_text(encoding="utf-8")
    )
    reviewer_receipt = json.loads(
        (
            out_dir
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-002"
            / "panel_reviewer_receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["panel_evidence"] == str(evidence.resolve())
    assert manifest["panel_context"] == {
        "panel_id": "panel_custom",
        "run_root": str(run_root.resolve()),
        "image_path": str(image.resolve()),
        "visual_review_receipt": str(visual_review.resolve()),
    }
    assert creator_receipt["panel_id"] == "panel_custom"
    assert creator_receipt["generated_image_path"] == str(image.resolve())
    assert reviewer_receipt["panel_id"] == "panel_custom"
    assert reviewer_receipt["reviewer_source"] == str(visual_review.resolve())
    repair_receipt = json.loads(
        (out_dir / "receipts" / "panel_repair_gate_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    panel_source_receipt = json.loads(
        (out_dir / "receipts" / "panel_source_receipt.json").read_text(encoding="utf-8")
    )
    assert repair_receipt["schema"] == "persona_dream.panel_repair_gate_receipt.v1"
    assert repair_receipt["generated_image_path"] == str(image.resolve())
    assert repair_receipt["media_hashes"]["panel_custom"].startswith("sha256:")
    assert repair_receipt["provider_eligibility"] is False
    assert panel_source_receipt["producer"]["receipt"] == str(
        out_dir / "receipts" / "panel_repair_gate_receipt.json"
    )
    assert panel_source_receipt["image_path"] == str(image.resolve())
    assert manifest["first_blocker"]["previous_subagent"] == "panel-reviewer"
    assert manifest["first_blocker"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_cli_persona_dream_panel_proof_accepts_source_panel_metadata(tmp_path: Path) -> None:
    out_dir = tmp_path / "persona-proof"
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    run_root = tmp_path / "run-root"
    image = run_root / "artifacts" / "panel_source.png"
    generation_receipt = run_root / "receipts" / "generation_receipt.json"
    visual_review = run_root / "receipts" / "visual_review_receipt.json"
    provider_probe = run_root / "receipts" / "provider_media_probe_receipt.json"
    evidence = tmp_path / "panel-evidence.json"
    source = tmp_path / "panel-source.json"
    image.parent.mkdir(parents=True)
    visual_review.parent.mkdir(parents=True)
    image.write_bytes(b"source-derived panel bytes")
    image_hash = "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest()
    generation_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.persona_dream.scillm_image_generation_call.v1",
                "ok": True,
                "width": 1536,
                "height": 1024,
                "sha256": image_hash,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    visual_review.write_text(
        json.dumps(
            {
                "schema": "tau.persona_dream.scillm_vlm_review_receipt.v1",
                "status": "PASS",
                "live_call_performed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    provider_probe.write_text(
        json.dumps(
            {
                "schema": "persona_dream.provider_media_url_probe_receipt.v1",
                "status": "PASS_PROVIDER_MEDIA_URL_PROBE",
                "url": "https://assets.example.test/persona-dream/panel_source.png",
                "expected_sha256": image_hash,
                "observed_sha256": image_hash,
                "http_status": 200,
                "mocked": "no",
                "live": "yes",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence.write_text(
        json.dumps(
            {
                "panel_id": "panel_source",
                "run_root": str(run_root),
                "image_path": "artifacts/panel_source.png",
                "visual_review_receipt": "receipts/visual_review_receipt.json",
                "image_generation_receipt": "receipts/generation_receipt.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source.write_text(
        json.dumps(
            {
                "panel_id": "panel_source",
                "action": (
                    "Embry examines SPARTA evidence cards while tea steam crosses "
                    "the laptop glow."
                ),
                "required_visible_entities": ["Embry", "SPARTA laptop", "evidence cards"],
                "required_props": ["tea cup", "paper evidence cards"],
                "required_dynamic_behaviors": ["tea steam curls through screen light"],
                "provider_media_probe_receipt": str(provider_probe),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "persona-dream-panel-proof",
            "--out-dir",
            str(out_dir),
            "--agents-root",
            str(agents_root),
            "--command-spec-root",
            "experiments/goal-locked-subagents/agent-command-specs",
            "--active-goal-hash",
            "sha256:test-persona-dream-panel-proof",
            "--panel-evidence",
            str(evidence),
            "--panel-source",
            str(source),
        ],
    )

    assert result.exit_code == 0, result.output
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    prompt = manifest["panel_context"]["panel_prompt"]
    script_coverage = json.loads(
        (out_dir / "receipts" / "script_coverage_receipt.json").read_text(encoding="utf-8")
    )
    post_generation = json.loads(
        (out_dir / "receipts" / "post_generation_script_coverage_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    repair_gate = json.loads(
        (out_dir / "receipts" / "panel_repair_gate_receipt.json").read_text(encoding="utf-8")
    )
    panel_source = json.loads(
        (out_dir / "receipts" / "panel_source_receipt.json").read_text(encoding="utf-8")
    )

    assert manifest["first_blocker"] is None
    assert "Embry examines SPARTA evidence cards" in prompt
    assert "tea steam curls through screen light" in prompt
    assert script_coverage["status"] == "PASS"
    assert script_coverage["source_panel"] == str(source.resolve())
    assert post_generation["status"] == "PASS"
    assert repair_gate["status"] == "PASS_PANEL_REVIEWED"
    assert repair_gate["script_coverage_status"] == "PASS"
    assert repair_gate["post_generation_script_coverage_status"] == "PASS"
    assert repair_gate["provider_media_status"] == "PASS"
    assert repair_gate["provider_eligibility"] is True
    assert repair_gate["provider_media_urls"] == [
        "https://assets.example.test/persona-dream/panel_source.png"
    ]
    assert panel_source["status"] == "PASS_PANEL_SOURCE"
    assert panel_source["final_panel_eligible"] is True


def test_persona_dream_panel_live_context_is_tau_owned(tmp_path: Path) -> None:
    context = _persona_panel_context(
        None,
        proof_dir=tmp_path,
        scillm_live_panel=True,
        panel_prompt="Generate one bounded test panel.",
        scillm_image_model="gpt-image-2",
        scillm_image_auth="codex-oauth",
        scillm_image_quality="medium",
        scillm_vlm_model="gpt-5.5",
        scillm_base_url="http://127.0.0.1:4001",
    )

    assert context["scillm_live_panel"] == "true"
    assert context["panel_prompt"] == "Generate one bounded test panel."
    assert context["image_path"] == str((tmp_path / "scillm-panel" / "panel_001.png").resolve())
    assert context["visual_review_receipt"] == str(
        (tmp_path / "scillm-panel" / "visual_review_receipt.json").resolve()
    )
    assert context["scillm_image_model"] == "gpt-image-2"
    assert context["scillm_image_auth"] == "codex-oauth"
    assert context["scillm_vlm_model"] == "gpt-5.5"


def test_persona_dream_panel_context_accepts_panel_repair_work_order(tmp_path: Path) -> None:
    run_root = tmp_path / "dream-run"
    receipts = run_root / "receipts"
    artifacts = run_root / "artifacts"
    receipts.mkdir(parents=True)
    artifacts.mkdir()
    storyboard_image = artifacts / "panel_001_storyboard_contract.svg"
    storyboard_image.write_text("<svg></svg>\n", encoding="utf-8")
    storyboard_receipt = receipts / "storyboard_panel_receipt.json"
    storyboard_receipt.write_text(
        json.dumps(
            {
                "schema": "persona_dream.storyboard_panel_receipt.v1",
                "panel_id": "panel_01",
                "beat": "Embry reviews source evidence under a void-world sky.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    work_order = receipts / "panel_repair_work_order.json"
    work_order.write_text(
        json.dumps(
            {
                "schema": "persona_dream.panel_repair_work_order.v1",
                "panel_id": "panel_01",
                "purpose": "Run the real panel loop.",
                "source_paths": {
                    "run_root": str(run_root),
                    "storyboard_panel_receipt": str(storyboard_receipt),
                },
                "current_candidate": {
                    "image_path": str(storyboard_image),
                    "remaining_blockers": ["panel_repair_gate_receipt_missing"],
                },
                "forbidden_actions": ["nano_banana_final_panel_generation"],
                "acceptance_criteria": ["write panel_repair_gate_receipt.json"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    context = _persona_panel_context(
        None,
        proof_dir=tmp_path / "proof",
        panel_repair_work_order=work_order,
        scillm_image_model="gpt-image-2",
        scillm_image_auth="codex-oauth",
        scillm_image_quality="high",
        scillm_vlm_model="gpt-5.5",
        scillm_base_url="http://127.0.0.1:4001",
    )

    assert context["panel_id"] == "panel_01"
    assert context["run_root"] == str(run_root.resolve())
    assert context["image_path"] == str((artifacts / "panel_01_scillm_panel.png").resolve())
    assert context["visual_review_receipt"] == str(
        (receipts / "visual_review_receipt.json").resolve()
    )
    assert context["scillm_live_panel"] == "true"
    assert context["write_receipts_to_panel_run_root"] == "true"
    assert context["panel_repair_work_order"] == str(work_order.resolve())
    assert "Nano Banana" in context["panel_prompt"]


def test_persona_dream_visual_review_adapter_adds_run_root_gate_fields(
    tmp_path: Path,
) -> None:
    image = tmp_path / "panel.png"
    image.write_bytes(b"not-a-real-png")
    source = tmp_path / "scillm-review.json"
    source.write_text(
        json.dumps(
            {
                "schema": "tau.persona_dream.scillm_vlm_review_receipt.v1",
                "status": "PASS",
                "blocking_findings": [],
                "passed_entities": ["single coherent cinematic panel"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    receipt = _persona_dream_visual_review_receipt(
        panel={"panel_id": "panel_01", "image_path": str(image)},
        source_path=source,
    )

    assert receipt["schema"] == "persona_dream.visual_review_receipt.v1"
    assert receipt["status"] == "PASS"
    assert receipt["panel_id"] == "panel_01"
    assert receipt["reviewer_source"] == str(source.resolve())
    assert receipt["reviewed_image_path"] == str(image.resolve())
    assert receipt["hash"].startswith("sha256:")
    assert receipt["dimensions"] == {"width": 1, "height": 1}
    assert set(receipt["checks"].values()) == {"PASS"}


def test_persona_dream_panel_sse_collector_records_liveness(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    result = _collect_scillm_sse(
        [
            'event: started',
            'data: {"model":"gpt-5.5","elapsed_ms":1}',
            ': heartbeat {"model":"gpt-5.5"}',
            'data: {"choices":[{"delta":{"content":"{\\"status\\":\\"PASS\\"}"}}]}',
            "data: [DONE]",
        ],
        events_path,
    )

    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert result == {
        "content": '{"status":"PASS"}',
        "event_count": 2,
        "heartbeat_count": 1,
        "done_seen": True,
        "last_event_type": "done",
    }
    assert [event["type"] for event in events] == ["started", "heartbeat", "chunk", "done"]


def test_persona_dream_panel_image_stream_event_parser() -> None:
    json_event = _scillm_image_stream_event(
        stream_name="stderr",
        line='{"type":"scillm.image.started","auth":"codex-oauth"}',
        elapsed=1.23456,
    )
    text_event = _scillm_image_stream_event(
        stream_name="stdout",
        line="[codex +  1.2s] #1 thread.started",
        elapsed=2.0,
    )

    assert json_event is not None
    assert json_event["type"] == "scillm.image.started"
    assert json_event["json"] is True
    assert json_event["data"]["auth"] == "codex-oauth"
    assert text_event is not None
    assert text_event["type"] == "image_wrapper_stdout"
    assert text_event["json"] is False
    assert "thread.started" in text_event["data"]["text"]


def test_persona_dream_panel_mirrors_wrapper_jsonl_events(tmp_path: Path) -> None:
    wrapper_events_path = tmp_path / "wrapper-events.jsonl"
    events_path = tmp_path / "events.jsonl"
    wrapper_events_path.write_text(
        '{"type":"thread.started","thread_id":"abc"}\n'
        '{"type":"item.completed","item":{"type":"image_generation_call"}}\n',
        encoding="utf-8",
    )

    count = _mirror_wrapper_jsonl_events(wrapper_events_path, events_path)
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]

    assert count == 2
    assert [event["type"] for event in events] == [
        "image_wrapper_codex_json_event",
        "image_wrapper_codex_json_event",
    ]
    assert events[0]["data"]["thread_id"] == "abc"


def test_cli_handoff_command_loop_github_transport_dry_run(tmp_path: Path) -> None:
    loop_receipt = {
        "schema": "tau.agent_handoff_command_loop_receipt.v1",
        "ok": True,
        "status": "WAITING",
        "step_count": 2,
        "terminal_agent": "human",
        "stop_reason": "next_agent_is_human",
        "mocked": False,
        "live": True,
        "runner": "agent-registry-command-loop",
        "dispatches": [
            {
                "selected_agent": "goal-guardian",
                "response_projection": {
                    "schema": "tau.agent_handoff_projection_receipt.v1",
                    "ok": True,
                    "dry_run": True,
                    "next_agent": "project-or-harness-verifier",
                    "target": {"repo": "grahama1970/chatgpt-lab", "target": "issue#123"},
                    "labels": {
                        "add": [
                            "agent-work",
                            "next:project-or-harness-verifier",
                            "executor:local",
                        ],
                        "remove": [],
                    },
                    "comment": {"body": "## Goal Guardian\n"},
                    "errors": [],
                },
            },
            {
                "selected_agent": "project-or-harness-verifier",
                "response_projection": {
                    "schema": "tau.agent_handoff_projection_receipt.v1",
                    "ok": True,
                    "dry_run": True,
                    "next_agent": "human",
                    "target": {"repo": "grahama1970/chatgpt-lab", "target": "issue#123"},
                    "labels": {
                        "add": ["agent-work", "next:human", "executor:human"],
                        "remove": [],
                    },
                    "comment": {"body": "## Terminal Handoff\n"},
                    "errors": [],
                },
            },
        ],
        "errors": [],
    }
    loop_receipt_path = tmp_path / "command-loop-receipt.json"
    receipt_path = tmp_path / "github-transport.json"
    loop_receipt_path.write_text(json.dumps(loop_receipt), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-command-loop-github-transport",
            str(loop_receipt_path),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.github_command_loop_terminal_transport_receipt.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["commands"][0][:3] == ["gh", "issue", "comment"]
    assert receipt == payload


def test_cli_handoff_command_loop_github_transport_apply_flag_is_parsed(tmp_path: Path) -> None:
    loop_receipt = {
        "schema": "tau.agent_handoff_command_loop_receipt.v1",
        "ok": False,
        "status": "BLOCKED",
        "step_count": 0,
        "terminal_agent": None,
        "stop_reason": "test",
        "dispatches": [],
        "errors": ["test fixture intentionally invalid"],
    }
    loop_receipt_path = tmp_path / "command-loop-receipt.json"
    loop_receipt_path.write_text(json.dumps(loop_receipt), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-command-loop-github-transport",
            str(loop_receipt_path),
            "--apply",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == "tau.github_command_loop_terminal_transport_receipt.v1"
    assert payload["dry_run"] is False
    assert payload["applied"] is False
    assert "command loop receipt must be ok" in "\n".join(payload["errors"])


def test_cli_goal_guardian_reconciliation_github_transport_writes_dry_run_receipt(
    tmp_path: Path,
) -> None:
    reconciliation_receipt = {
        "schema": "tau.goal_guardian_reconciliation_receipt.v1",
        "ok": True,
        "dry_run": True,
        "github": {"repo": "grahama1970/chatgpt-lab", "target": "issue#123"},
        "goal": {
            "goal_id": "goal-tau-orchestration-001",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "decision": "REQUIRES_HUMAN_GOAL_VERSION",
        "new_goal": {"text": "Build Tau's goal-locked harness one slice at a time."},
        "source_schema": "tau.human_goal_change.v1",
        "source": "valid-human-goal-change.json",
        "source_artifacts": [],
        "open_ticket_reconciliation": {
            "status": "classified",
            "reason": "Classified tickets from authoritative local ticket source.",
            "source": "goal-guardian-ticket-source.json",
            "source_schema": "tau.goal_guardian_ticket_source.v1",
            "counts": {"keep": 1, "close": 1, "migrate": 1, "regenerate": 1},
            "keep": [{"id": "issue#101"}],
            "close": [{"id": "issue#104"}],
            "migrate": [{"id": "issue#102"}],
            "regenerate": [{"id": "issue#103"}],
        },
        "next_agent": "human",
        "errors": [],
    }
    reconciliation_path = tmp_path / "goal-guardian-reconciliation-receipt.json"
    transport_path = tmp_path / "github-transport.json"
    reconciliation_path.write_text(json.dumps(reconciliation_receipt), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "goal-guardian-reconciliation-github-transport",
            str(reconciliation_path),
            "--receipt",
            str(transport_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(transport_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.github_goal_guardian_reconciliation_transport_receipt.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["commands"][0][:3] == ["gh", "issue", "comment"]
    assert payload["commands"][1][:3] == ["gh", "issue", "edit"]
    assert receipt == payload


def test_cli_handoff_command_loop_reconciliation_github_transport_traces_source(
    tmp_path: Path,
) -> None:
    reconciliation_receipt = {
        "schema": "tau.goal_guardian_reconciliation_receipt.v1",
        "ok": True,
        "dry_run": True,
        "github": {"repo": "grahama1970/chatgpt-lab", "target": "issue#123"},
        "goal": {
            "goal_id": "goal-tau-orchestration-001",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "decision": "REQUIRES_HUMAN_GOAL_VERSION",
        "new_goal": {"text": "Build Tau's goal-locked harness one slice at a time."},
        "source_schema": "tau.human_goal_change.v1",
        "source": "valid-human-goal-change.json",
        "source_artifacts": [],
        "open_ticket_reconciliation": {
            "status": "classified",
            "reason": "Classified tickets from authoritative local ticket source.",
            "source": str(tmp_path / "goal-guardian-ticket-source.json"),
            "source_schema": "tau.goal_guardian_ticket_source.v1",
            "counts": {"keep": 1, "close": 0, "migrate": 0, "regenerate": 0},
            "keep": [{"id": "issue#101"}],
            "close": [],
            "migrate": [],
            "regenerate": [],
        },
        "next_agent": "human",
        "errors": [],
    }
    reconciliation_path = tmp_path / "artifacts" / "goal-guardian-reconciliation-receipt.json"
    reconciliation_path.parent.mkdir()
    reconciliation_path.write_text(json.dumps(reconciliation_receipt), encoding="utf-8")
    loop_receipt = {
        "schema": "tau.agent_handoff_command_loop_receipt.v1",
        "ok": True,
        "status": "WAITING",
        "step_count": 1,
        "terminal_agent": "human",
        "stop_reason": "next_agent_is_human",
        "mocked": False,
        "live": True,
        "runner": "agent-registry-command-loop",
        "dispatches": [
            {"selected_agent": "goal-guardian", "artifacts": [str(reconciliation_path)]}
        ],
        "artifacts": [str(reconciliation_path)],
        "errors": [],
    }
    loop_path = tmp_path / "command-loop-receipt.json"
    transport_path = tmp_path / "github-transport.json"
    loop_path.write_text(json.dumps(loop_receipt), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "handoff-command-loop-reconciliation-github-transport",
            str(loop_path),
            "--receipt",
            str(transport_path),
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(transport_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.github_command_loop_reconciliation_transport_receipt.v1"
    assert payload["ok"] is True
    assert payload["source_loop_receipt_path"] == str(loop_path.resolve())
    assert payload["reconciliation_receipt_path"] == str(reconciliation_path.resolve())
    assert payload["ticket_source_path"] == str(tmp_path / "goal-guardian-ticket-source.json")
    assert payload["transport"]["commands"][0][:3] == ["gh", "issue", "comment"]
    assert payload["transport"]["commands"][1][:3] == ["gh", "issue", "edit"]
    assert receipt == payload


def test_cli_goal_guardian_ticket_source_github_fetch_writes_dry_run_receipt(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "goal-guardian-ticket-source.json"
    receipt_path = tmp_path / "github-ticket-source-fetch.json"

    result = CliRunner().invoke(
        app,
        [
            "goal-guardian-ticket-source-github-fetch",
            "grahama1970/chatgpt-lab",
            "--out",
            str(output_path),
            "--receipt",
            str(receipt_path),
            "--state",
            "all",
            "--limit",
            "10",
        ],
    )
    payload = json.loads(result.output)
    receipt = json.loads(receipt_path.read_text())

    assert result.exit_code == 0
    assert payload["schema"] == "tau.github_ticket_source_fetch_receipt.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["executed"] is False
    assert payload["repo"] == "grahama1970/chatgpt-lab"
    assert payload["command"] == [
        "gh",
        "issue",
        "list",
        "--repo",
        "grahama1970/chatgpt-lab",
        "--state",
        "all",
        "--limit",
        "10",
        "--json",
        "number,title,state,url,labels",
    ]
    assert payload["ticket_source"] is None
    assert payload["ticket_source_path"] is None
    assert receipt == payload
    assert not output_path.exists()


def test_cli_loop2_serve_starts_receipt_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"
    run_dir.mkdir()
    calls: list[tuple[Path, str, int]] = []
    closed = False

    class FakeServer:
        server_address = ("127.0.0.1", 43210)

        def serve_forever(self) -> None:
            return

        def server_close(self) -> None:
            nonlocal closed
            closed = True

    def fake_create_loop_receipt_monitor_server(
        selected_run_dir: Path,
        *,
        host: str,
        port: int,
    ) -> FakeServer:
        calls.append((selected_run_dir, host, port))
        return FakeServer()

    monkeypatch.setattr(
        cli,
        "create_loop_receipt_monitor_server",
        fake_create_loop_receipt_monitor_server,
    )

    result = CliRunner().invoke(
        app,
        [
            "--loop2-serve-host",
            "127.0.0.1",
            "--loop2-serve-port",
            "0",
            "loop2-serve",
            str(run_dir),
        ],
    )

    assert result.exit_code == 0
    assert calls == [(run_dir.resolve(), "127.0.0.1", 0)]
    assert closed is True
    assert (
        f"Serving Tau Loop2 receipt run {run_dir.name} at "
        f"http://127.0.0.1:43210/api/loop2/runs/{run_dir.name}"
    ) in result.output


def test_cli_loop2_serve_rejects_missing_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "missing-run"

    result = CliRunner().invoke(app, ["loop2-serve", str(run_dir)])

    assert result.exit_code != 0
    assert "Loop2 receipt run directory does not exist" in result.output


def test_cli_loop2_validate_prints_success_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"
    loop2_src = tmp_path / "loop2-src"
    calls: list[tuple[Path, Path | None]] = []

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        calls.append((selected_run_dir, loop2_src))
        return LoopReceiptValidationResult(
            ok=True,
            checked_artifacts=("contract", "final_receipt", "node_result"),
        )

    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )

    result = CliRunner().invoke(
        app,
        ["--loop2-src", str(loop2_src), "loop2-validate", str(run_dir)],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [(run_dir.resolve(), loop2_src)]
    assert payload == {
        "schema": "tau.loop_receipt.validation.v1",
        "run_dir": str(run_dir.resolve()),
        "ok": True,
        "checked_artifacts": ["contract", "final_receipt", "node_result"],
        "errors": [],
    }


def test_cli_loop2_validate_exits_nonzero_on_contract_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del selected_run_dir, loop2_src
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=("contract",),
            errors=("final_receipt: bad status",),
        )

    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )

    result = CliRunner().invoke(app, ["loop2-validate", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["checked_artifacts"] == ["contract"]
    assert payload["errors"] == ["final_receipt: bad status"]


def test_cli_loop2_validate_contract_prints_success_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    loop2_src = tmp_path / "loop2-src"
    calls: list[tuple[Path, Path | None]] = []

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        calls.append((selected_contract_path, loop2_src))
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src),
            "loop2-validate-contract",
            str(contract_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [(contract_path.resolve(), loop2_src)]
    assert payload == {
        "schema": "tau.loop2_contract.validation.v1",
        "contract": str(contract_path.resolve()),
        "ok": True,
        "checked_artifacts": ["contract"],
        "errors": [],
    }


def test_cli_loop2_validate_contract_exits_nonzero_on_contract_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del selected_contract_path, loop2_src
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=("contract: checks must not be empty",),
        )

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)

    result = CliRunner().invoke(app, ["loop2-validate-contract", str(contract_path)])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["checked_artifacts"] == []
    assert payload["errors"] == ["contract: checks must not be empty"]


def test_cli_loop2_validate_native_prints_success_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "native-loop2-run"
    loop2_src = tmp_path / "loop2-src"
    calls: list[tuple[Path, Path | None]] = []

    def fake_validate_native_loop2_run_with_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        calls.append((selected_run_dir, loop2_src))
        return LoopReceiptValidationResult(
            ok=True,
            checked_artifacts=("contract", "final_receipt", "node_result"),
        )

    monkeypatch.setattr(
        cli,
        "validate_native_loop2_run_with_contracts",
        fake_validate_native_loop2_run_with_contracts,
    )

    result = CliRunner().invoke(
        app,
        ["--loop2-src", str(loop2_src), "loop2-validate-native", str(run_dir)],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [(run_dir.resolve(), loop2_src)]
    assert payload == {
        "schema": "tau.native_loop2_run.validation.v1",
        "run_dir": str(run_dir.resolve()),
        "ok": True,
        "checked_artifacts": ["contract", "final_receipt", "node_result"],
        "errors": [],
    }


def test_cli_loop2_validate_native_exits_nonzero_on_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "native-loop2-run"

    def fake_validate_native_loop2_run_with_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del selected_run_dir, loop2_src
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=("contract",),
            errors=("current_state: schema mismatch",),
        )

    monkeypatch.setattr(
        cli,
        "validate_native_loop2_run_with_contracts",
        fake_validate_native_loop2_run_with_contracts,
    )

    result = CliRunner().invoke(app, ["loop2-validate-native", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["schema"] == "tau.native_loop2_run.validation.v1"
    assert payload["ok"] is False
    assert payload["checked_artifacts"] == ["contract"]
    assert payload["errors"] == ["current_state: schema mismatch"]


def test_cli_loop2_run_passes_loop2_contract_to_print_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    run_root = tmp_path / ".loop2" / "runs"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-one",
                "objective": "Fix the thing.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "required_changed_globs": ["src/**/*.py"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "fixture",
                "run_root": str(run_root),
            }
        )
    )
    calls: list[tuple[str, Path, LoopReceiptConfig | None]] = []

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del loop2_src
        assert selected_contract_path == contract_path.resolve()
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    receipt_validation_calls: list[tuple[Path, Path | None]] = []

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        receipt_validation_calls.append((selected_run_dir, loop2_src))
        return LoopReceiptValidationResult(
            ok=True,
            checked_artifacts=("contract", "final_receipt", "node_result"),
        )

    async def fake_run_fixture_loop2_print_mode(
        *,
        prompt: str,
        model: str,
        cwd: Path,
        output: PrintOutputMode,
        loop_receipt: LoopReceiptConfig,
    ) -> bool:
        del model, output
        calls.append((prompt, cwd, loop_receipt))
        run_dir = run_root / "tau-loop-test"
        run_dir.mkdir(parents=True)
        return True

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )
    monkeypatch.setattr(
        cli,
        "_run_fixture_loop2_print_mode",
        fake_run_fixture_loop2_print_mode,
    )

    result = CliRunner().invoke(app, ["loop2-run", str(contract_path)])

    assert result.exit_code == 0
    assert receipt_validation_calls == [(run_root / "tau-loop-test", None)]
    assert len(calls) == 1
    prompt, cwd, receipt = calls[0]
    assert prompt == "Fix the thing."
    assert cwd == tmp_path
    assert receipt == LoopReceiptConfig(
        root_dir=run_root,
        node_id="repair-one",
        allowed_globs=("src/**",),
        required_changed_globs=("src/**/*.py",),
        checks=("python -m pytest",),
    )
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is True
    assert payload["run_dir"] == str(run_root / "tau-loop-test")
    assert payload["mocked"] is True
    assert payload["live"] is False
    assert payload["receipt_validation"] == {
        "ran": True,
        "ok": True,
        "checked_artifacts": ["contract", "final_receipt", "node_result"],
        "errors": [],
    }


def test_cli_loop2_run_rejects_fixture_run_when_receipt_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    run_root = tmp_path / ".loop2" / "runs"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-one",
                "objective": "Fix the thing.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "fixture",
                "run_root": str(run_root),
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del loop2_src
        assert selected_contract_path == contract_path.resolve()
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    async def fake_run_fixture_loop2_print_mode(
        *,
        prompt: str,
        model: str,
        cwd: Path,
        output: PrintOutputMode,
        loop_receipt: LoopReceiptConfig,
    ) -> bool:
        del prompt, model, cwd, output, loop_receipt
        (run_root / "tau-loop-test").mkdir(parents=True)
        return True

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_run_dir == run_root / "tau-loop-test"
        assert loop2_src is None
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=("contract",),
            errors=("node_result: missing events",),
    )

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(
        cli,
        "_run_fixture_loop2_print_mode",
        fake_run_fixture_loop2_print_mode,
    )
    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )

    result = CliRunner().invoke(app, ["loop2-run", str(contract_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is False
    assert payload["run_dir"] == str(run_root / "tau-loop-test")
    assert payload["receipt_validation"] == {
        "ran": True,
        "ok": False,
        "checked_artifacts": ["contract"],
        "errors": ["node_result: missing events"],
    }
    assert payload["errors"] == ["node_result: missing events"]


def test_cli_loop2_run_fixture_output_is_single_json_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    run_root = tmp_path / ".loop2" / "runs"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-one",
                "objective": "Fix the thing.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": [f"{sys.executable} -c \"print('check ok')\""],
                "max_attempts": 1,
                "backend": "fixture",
                "run_root": str(run_root),
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del loop2_src
        assert selected_contract_path == contract_path.resolve()
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_run_dir.parent == run_root
        assert loop2_src is None
        return LoopReceiptValidationResult(
            ok=True,
            checked_artifacts=("contract", "final_receipt", "node_result"),
        )

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )

    result = CliRunner().invoke(app, ["loop2-run", str(contract_path)])

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is True
    assert "Fixture loop complete." not in result.output


def test_cli_loop2_run_rejects_invalid_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text("{}")

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del selected_contract_path, loop2_src
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=("contract",),
            errors=("node_id missing",),
        )

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)

    result = CliRunner().invoke(app, ["loop2-run", str(contract_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload == {
        "contract": str(contract_path.resolve()),
        "errors": ["node_id missing"],
        "live": False,
        "mocked": True,
        "ok": False,
        "schema": "tau.loop2_contract_run.v1",
    }


def test_cli_loop2_run_rejects_scillm_backend_until_delegation_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "dev-proxy-key-123",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )
    calls = 0

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del loop2_src
        assert selected_contract_path == contract_path.resolve()
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    async def fake_run_openai_print_mode(
        prompt: str,
        model: str | None,
        cwd: Path,
        output: PrintOutputMode,
        provider_name: str | None,
        loop_receipt: LoopReceiptConfig | None,
    ) -> bool:
        nonlocal calls
        del prompt, model, cwd, output, provider_name, loop_receipt
        calls += 1
        return True

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(app, ["--provider", "chutes", "loop2-run", str(contract_path)])

    assert result.exit_code == 1
    assert calls == 0
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is False
    assert payload["node_id"] == "repair-scillm"
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["checks"] == ["python -m pytest"]
    assert payload["run_dir"] == ""
    assert payload["errors"] == [
        "tau loop2-run currently supports backend=fixture only; "
        "backend=scillm requires --loop2-src pointing at the Loop2 runner"
    ]


def test_cli_loop2_run_delegates_scillm_backend_to_loop2_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SCILLM_API_KEY", raising=False)
    contract_path = tmp_path / "contract.json"
    run_dir = tmp_path / ".loop2" / "runs" / "loop2-run-test"
    final_receipt = run_dir / "final-receipt.json"
    doctor_receipt = tmp_path / "scillm-doctor-receipt.json"
    doctor_receipt.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "reason": "",
            }
        )
    )
    loop2_src_path = tmp_path / "loop2" / "src"
    loop2_src_path.mkdir(parents=True)
    runner = loop2_src_path.parent / "run.sh"
    args_path = tmp_path / "runner-args.txt"
    runner.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"printf '%s\\n' \"$@\" > {args_path}",
                f"mkdir -p {run_dir / 'checks'}",
                f"cat > {run_dir / 'contract.json'} <<'CONTRACT'",
                json.dumps(
                    {
                        "schema": "loop2.repair_node_contract.v1",
                        "node_id": "repair-scillm",
                        "objective": "Fix with Loop2 Scillm.",
                        "repo": str(tmp_path),
                        "allowed_globs": ["src/**"],
                        "checks": ["python -m pytest"],
                        "max_attempts": 1,
                        "backend": "scillm",
                        "scillm": {
                            "base_url": "http://127.0.0.1:4001",
                            "api_key": "secret-scillm-key",
                            "agent_id": "",
                            "agent": "build",
                            "mode": "workspace_write",
                            "model": "opencode-go/kimi-k2.6",
                            "timeout_s": 900,
                        },
                    }
                ),
                "CONTRACT",
                f"touch {run_dir / 'current-state.json'}",
                f"touch {run_dir / 'events.jsonl'}",
                f"cat > {final_receipt} <<'FINAL_RECEIPT'",
                json.dumps(
                    {
                        "schema": "loop2.final_receipt.v1",
                        "node_id": "repair-scillm",
                        "status": "PASS",
                        "run_id": "loop2-run-test",
                        "mocked": False,
                        "live": True,
                        "proof_scope": "one bounded loop2 repair node",
                        "changed_files": [
                            "src/fixed.py",
                            "src/__pycache__/fixed.cpython-314.pyc",
                        ],
                        "checks": [
                            {
                                "command": "python -m pytest",
                                "exit_code": 0,
                                "stdout_path": str(run_dir / "checks" / "stdout.txt"),
                                "stderr_path": str(run_dir / "checks" / "stderr.txt"),
                                "elapsed_s": 0.01,
                            }
                        ],
                        "claims": {"proves": [], "does_not_prove": []},
                        "artifacts": {
                            "run_dir": str(run_dir),
                            "events": str(run_dir / "events.jsonl"),
                            "current_state": str(run_dir / "current-state.json"),
                            "transport_dag_evidence": str(
                                run_dir / "transport-dag-evidence.json"
                            ),
                            "node_result": str(run_dir / "node-result.json"),
                        },
                    }
                ),
                "FINAL_RECEIPT",
                f"touch {run_dir / 'transport-dag-evidence.json'}",
                f"cat > {run_dir / 'node-result.json'} <<'NODE_RESULT'",
                json.dumps(
                    {
                        "schema": "loop2.node_result.v1",
                        "node_id": "repair-scillm",
                        "status": "PASS",
                        "run_id": "loop2-run-test",
                        "final_receipt": str(final_receipt),
                        "transport_dag_evidence": str(run_dir / "transport-dag-evidence.json"),
                        "events": str(run_dir / "events.jsonl"),
                        "changed_files": [
                            "src/fixed.py",
                            "src/__pycache__/fixed.cpython-314.pyc",
                        ],
                        "checks": [
                            {
                                "command": "python -m pytest",
                                "exit_code": 0,
                                "stdout_path": str(run_dir / "checks" / "stdout.txt"),
                                "stderr_path": str(run_dir / "checks" / "stderr.txt"),
                                "elapsed_s": 0.01,
                            }
                        ],
                        "mocked": False,
                        "live": True,
                    }
                ),
                "NODE_RESULT",
                f"touch {run_dir / 'checks' / 'stdout.txt'}",
                f"touch {run_dir / 'checks' / 'stderr.txt'}",
                "cat <<'JSON'",
                json.dumps(
                    {
                        "schema": "loop2.node_result.v1",
                        "node_id": "repair-scillm",
                        "status": "PASS",
                        "run_id": "loop2-run-test",
                        "final_receipt": str(final_receipt),
                        "transport_dag_evidence": str(run_dir / "transport-dag-evidence.json"),
                        "events": str(run_dir / "events.jsonl"),
                        "changed_files": ["src/fixed.py"],
                        "checks": [
                            {
                                "command": "python -m pytest",
                                "exit_code": 0,
                                "stdout_path": str(run_dir / "checks" / "stdout.txt"),
                                "stderr_path": str(run_dir / "checks" / "stderr.txt"),
                                "elapsed_s": 0.01,
                            }
                        ],
                        "mocked": False,
                        "live": True,
                    }
                ),
                "JSON",
            ]
        )
    )
    runner.chmod(0o755)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "dev-proxy-key-123",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_contract_path == contract_path.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    native_validation_calls: list[tuple[Path, Path | None]] = []

    def fake_validate_native_loop2_run_with_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        native_validation_calls.append((selected_run_dir, loop2_src))
        return LoopReceiptValidationResult(
            ok=True,
            checked_artifacts=("contract", "final_receipt", "node_result"),
        )

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(cli, "_scillm_materialization_preflight_errors", lambda contract: [])
    monkeypatch.setattr(cli, "_scillm_proxy_auth_preflight", _passing_scillm_auth_preflight)
    monkeypatch.setattr(
        cli,
        "validate_native_loop2_run_with_contracts",
        fake_validate_native_loop2_run_with_contracts,
    )

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-scillm-doctor-receipt",
            str(doctor_receipt),
            "--provider",
            "chutes",
            "loop2-run",
            str(contract_path),
        ],
    )

    assert result.exit_code == 0
    assert native_validation_calls == [(run_dir.resolve(), loop2_src_path)]
    assert args_path.read_text().splitlines() == [
        "run",
        "--contract",
        str(contract_path.resolve()),
        "--json",
    ]
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is True
    assert payload["delegated"] is True
    assert payload["runner"] == str(runner.resolve())
    assert payload["run_dir"] == str(run_dir)
    assert payload["node_id"] == "repair-scillm"
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["node_result"]["schema"] == "loop2.node_result.v1"
    assert payload["node_result"]["changed_files"] == ["src/fixed.py"]
    assert payload["native_validation"] == {
        "ok": True,
        "checked_artifacts": ["contract", "final_receipt", "node_result"],
        "errors": [],
    }
    assert payload["artifact_sanitization"]["schema"] == (
        "tau.loop2_delegated_artifact_sanitization.v1"
    )
    assert payload["artifact_sanitization"]["ran"] is True
    assert payload["artifact_sanitization"]["changed_artifacts"] == [
        "contract.json",
        "final-receipt.json",
        "node-result.json",
    ]
    assert payload["artifact_sanitization"]["redacted_keys"] == ["contract.scillm.api_key"]
    assert payload["artifact_sanitization"]["filtered_changed_files"] == 2
    assert Path(payload["artifact_sanitization"]["artifact"]).exists()
    assert payload["checks"][0]["exit_code"] == 0
    final_receipt_payload = json.loads(final_receipt.read_text())
    assert final_receipt_payload["artifacts"]["tau_sanitization"] == str(
        run_dir / "tau-sanitization.json"
    )
    emitted_contract = json.loads((run_dir / "contract.json").read_text())
    assert emitted_contract["scillm"]["api_key"] == "<redacted-scillm-api-key>"
    assert "secret-scillm-key" not in (run_dir / "contract.json").read_text()


def test_cli_loop2_run_prepares_delegated_scillm_contract_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SCILLM_API_KEY", "active-scillm-proxy-key")
    contract_path = tmp_path / "contract.json"
    observed_contract_path = tmp_path / "observed-contract.json"
    args_path = tmp_path / "runner-args.txt"
    doctor_receipt = tmp_path / "scillm-doctor-receipt.json"
    doctor_receipt.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "reason": "",
            }
        )
    )
    loop2_src_path = tmp_path / "loop2" / "src"
    loop2_src_path.mkdir(parents=True)
    runner = loop2_src_path.parent / "run.sh"
    runner.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"printf '%s\\n' \"$@\" > {args_path}",
                f"cp \"$3\" {observed_contract_path}",
                "cat <<'JSON'",
                json.dumps(
                    {
                        "schema": "loop2.node_result.v1",
                        "node_id": "repair-scillm",
                        "status": "BLOCKED",
                        "run_id": "loop2-run-test",
                        "final_receipt": "",
                        "transport_dag_evidence": "",
                        "events": "",
                        "changed_files": [],
                        "checks": [],
                        "mocked": False,
                        "live": True,
                    }
                ),
                "JSON",
            ]
        )
    )
    runner.chmod(0o755)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "stale-scillm-key",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_contract_path == contract_path.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(cli, "_scillm_materialization_preflight_errors", lambda contract: [])
    monkeypatch.setattr(cli, "_scillm_proxy_auth_preflight", _passing_scillm_auth_preflight)

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-scillm-doctor-receipt",
            str(doctor_receipt),
            "--provider",
            "chutes",
            "loop2-run",
            str(contract_path),
        ],
    )

    payload = json.loads(result.output)
    runner_args = args_path.read_text().splitlines()
    original_contract = json.loads(contract_path.read_text())
    observed_contract = json.loads(observed_contract_path.read_text())

    assert result.exit_code == 1
    assert payload["contract"] == str(contract_path.resolve())
    assert payload["contract_preparation"]["ran"] is True
    assert payload["contract_preparation"]["auth_source"] == "env:SCILLM_API_KEY"
    assert payload["contract_preparation"]["redacted_keys"] == ["contract.scillm.api_key"]
    assert runner_args[:2] == ["run", "--contract"]
    assert runner_args[2] != str(contract_path.resolve())
    assert runner_args[3] == "--json"
    assert original_contract["scillm"]["api_key"] == "stale-scillm-key"
    assert observed_contract["scillm"]["api_key"] == "active-scillm-proxy-key"


def test_cli_loop2_run_blocks_before_runner_when_scillm_auth_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    runner_called = tmp_path / "runner-called"
    doctor_receipt = tmp_path / "scillm-doctor-receipt.json"
    doctor_receipt.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "reason": "",
            }
        )
    )
    loop2_src_path = tmp_path / "loop2" / "src"
    loop2_src_path.mkdir(parents=True)
    runner = loop2_src_path.parent / "run.sh"
    runner.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"touch {runner_called}",
                "cat <<'JSON'",
                json.dumps(
                    {
                        "schema": "loop2.node_result.v1",
                        "node_id": "repair-scillm",
                        "status": "PASS",
                        "run_id": "loop2-run-test",
                        "final_receipt": "",
                        "transport_dag_evidence": "",
                        "events": "",
                        "changed_files": [],
                        "checks": [],
                        "mocked": False,
                        "live": True,
                    }
                ),
                "JSON",
            ]
        )
    )
    runner.chmod(0o755)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "bad-scillm-key",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_contract_path == contract_path.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    async def failing_scillm_auth_preflight(
        contract: dict[str, object],
    ) -> dict[str, object]:
        scillm = contract["scillm"]
        assert isinstance(scillm, dict)
        assert scillm["api_key"] == "bad-scillm-key"
        return {
            "schema": "tau.scillm_proxy_auth_preflight.v1",
            "ran": True,
            "ok": False,
            "base_url": "http://127.0.0.1:4001",
            "endpoint": "/v1/scillm/loop2/capabilities",
            "caller_skill": "tau",
            "status_code": 401,
            "errors": ["Scillm proxy auth preflight failed with HTTP 401"],
        }

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(cli, "_scillm_materialization_preflight_errors", lambda contract: [])
    monkeypatch.setattr(cli, "_scillm_proxy_auth_preflight", failing_scillm_auth_preflight)

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-scillm-doctor-receipt",
            str(doctor_receipt),
            "--provider",
            "chutes",
            "loop2-run",
            str(contract_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert not runner_called.exists()
    assert payload["ok"] is False
    assert payload["run_dir"] == ""
    assert payload["delegated"] is True
    assert payload["scillm_auth_preflight"] == {
        "schema": "tau.scillm_proxy_auth_preflight.v1",
        "ran": True,
        "ok": False,
        "base_url": "http://127.0.0.1:4001",
        "endpoint": "/v1/scillm/loop2/capabilities",
        "caller_skill": "tau",
        "status_code": 401,
        "errors": ["Scillm proxy auth preflight failed with HTTP 401"],
    }
    assert payload["errors"] == ["Scillm proxy auth preflight failed with HTTP 401"]


def test_delegated_loop2_run_sanitizer_filters_generated_changed_files(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "native-run"
    run_dir.mkdir()
    changed_files = [
        "src/buggy_math.py",
        "src/__pycache__/buggy_math.cpython-314.pyc",
        "tests/__pycache__/test_buggy_math.cpython-314-pytest-9.1.1.pyc",
        ".pytest_cache/v/cache/nodeids",
    ]
    for artifact_name in ("final-receipt.json", "node-result.json"):
        (run_dir / artifact_name).write_text(
            json.dumps(
                {
                    "schema": f"test.{artifact_name}",
                    "changed_files": changed_files,
                }
            )
        )
    (run_dir / "contract.json").write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "scillm": {"api_key": "secret-scillm-key"},
            }
        )
    )

    sanitization = cli._sanitize_delegated_loop2_run_artifacts(run_dir)

    for artifact_name in ("final-receipt.json", "node-result.json"):
        payload = json.loads((run_dir / artifact_name).read_text())
        assert payload["changed_files"] == ["src/buggy_math.py"]
    contract = json.loads((run_dir / "contract.json").read_text())
    assert contract["scillm"]["api_key"] == "<redacted-scillm-api-key>"
    assert sanitization["changed_artifacts"] == [
        "contract.json",
        "final-receipt.json",
        "node-result.json",
    ]
    assert sanitization["redacted_keys"] == ["contract.scillm.api_key"]
    assert sanitization["filtered_changed_files"] == 6
    final_receipt_payload = json.loads((run_dir / "final-receipt.json").read_text())
    assert final_receipt_payload["artifacts"]["tau_sanitization"] == str(
        run_dir / "tau-sanitization.json"
    )
    assert json.loads((run_dir / "tau-sanitization.json").read_text()) == sanitization


def test_cli_loop2_run_rejects_tmp_repo_before_scillm_delegation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    doctor_receipt = tmp_path / "scillm-doctor-receipt.json"
    doctor_receipt.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "reason": "",
            }
        )
    )
    loop2_src_path = tmp_path / "loop2" / "src"
    loop2_src_path.mkdir(parents=True)
    runner = loop2_src_path.parent / "run.sh"
    runner_called = tmp_path / "runner-called"
    runner.write_text(f"#!/usr/bin/env bash\ntouch {runner_called}\nexit 99\n")
    runner.chmod(0o755)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "dev-proxy-key-123",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_contract_path == contract_path.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-scillm-doctor-receipt",
            str(doctor_receipt),
            "--provider",
            "chutes",
            "loop2-run",
            str(contract_path),
        ],
    )

    assert result.exit_code == 1
    assert not runner_called.exists()
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is False
    assert payload["delegated"] is True
    assert payload["run_dir"] == ""
    assert payload["mocked"] is False
    assert payload["live"] is True
    assert payload["errors"] == [
        "delegated Scillm loop2 repo is not materializable by the OpenCode "
        f"worker from /tmp: {tmp_path.resolve()}. Move the repair repo under "
        "the project workspace before running live loop2."
    ]


def test_cli_loop2_run_rejects_delegated_result_with_missing_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    run_dir = tmp_path / ".loop2" / "runs" / "loop2-run-test"
    doctor_receipt = tmp_path / "scillm-doctor-receipt.json"
    doctor_receipt.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "reason": "",
            }
        )
    )
    loop2_src_path = tmp_path / "loop2" / "src"
    loop2_src_path.mkdir(parents=True)
    runner = loop2_src_path.parent / "run.sh"
    runner.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "cat <<'JSON'",
                json.dumps(
                    {
                        "schema": "loop2.node_result.v1",
                        "node_id": "repair-scillm",
                        "status": "PASS",
                        "run_id": "loop2-run-test",
                        "final_receipt": str(run_dir / "final-receipt.json"),
                        "transport_dag_evidence": str(run_dir / "transport-dag-evidence.json"),
                        "events": str(run_dir / "events.jsonl"),
                        "changed_files": [],
                        "checks": [
                            {
                                "command": "python -m pytest",
                                "exit_code": 0,
                                "stdout_path": str(run_dir / "checks" / "stdout.txt"),
                                "stderr_path": str(run_dir / "checks" / "stderr.txt"),
                                "elapsed_s": 0.01,
                            }
                        ],
                        "mocked": False,
                        "live": True,
                    }
                ),
                "JSON",
            ]
        )
    )
    runner.chmod(0o755)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "dev-proxy-key-123",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_contract_path == contract_path.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(cli, "_scillm_materialization_preflight_errors", lambda contract: [])
    monkeypatch.setattr(cli, "_scillm_proxy_auth_preflight", _passing_scillm_auth_preflight)

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-scillm-doctor-receipt",
            str(doctor_receipt),
            "--provider",
            "chutes",
            "loop2-run",
            str(contract_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is False
    assert payload["delegated"] is True
    assert payload["run_dir"] == str(run_dir)
    assert payload["errors"][0].startswith("missing delegated Loop2 artifacts:")


def test_cli_loop2_run_rejects_delegated_result_with_native_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    run_dir = tmp_path / ".loop2" / "runs" / "loop2-run-test"
    final_receipt = run_dir / "final-receipt.json"
    doctor_receipt = tmp_path / "scillm-doctor-receipt.json"
    doctor_receipt.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "reason": "",
            }
        )
    )
    loop2_src_path = tmp_path / "loop2" / "src"
    loop2_src_path.mkdir(parents=True)
    runner = loop2_src_path.parent / "run.sh"
    runner.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"mkdir -p {run_dir / 'checks'}",
                f"touch {run_dir / 'contract.json'}",
                f"touch {run_dir / 'current-state.json'}",
                f"touch {run_dir / 'events.jsonl'}",
                f"touch {final_receipt}",
                f"touch {run_dir / 'transport-dag-evidence.json'}",
                f"touch {run_dir / 'node-result.json'}",
                f"touch {run_dir / 'checks' / 'stdout.txt'}",
                f"touch {run_dir / 'checks' / 'stderr.txt'}",
                "cat <<'JSON'",
                json.dumps(
                    {
                        "schema": "loop2.node_result.v1",
                        "node_id": "repair-scillm",
                        "status": "PASS",
                        "run_id": "loop2-run-test",
                        "final_receipt": str(final_receipt),
                        "transport_dag_evidence": str(run_dir / "transport-dag-evidence.json"),
                        "events": str(run_dir / "events.jsonl"),
                        "changed_files": ["src/fixed.py"],
                        "checks": [
                            {
                                "command": "python -m pytest",
                                "exit_code": 0,
                                "stdout_path": str(run_dir / "checks" / "stdout.txt"),
                                "stderr_path": str(run_dir / "checks" / "stderr.txt"),
                                "elapsed_s": 0.01,
                            }
                        ],
                        "mocked": False,
                        "live": True,
                    }
                ),
                "JSON",
            ]
        )
    )
    runner.chmod(0o755)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "dev-proxy-key-123",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_contract_path == contract_path.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    def fake_validate_native_loop2_run_with_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_run_dir == run_dir.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=("contract", "final_receipt"),
            errors=("node_result: status mismatch",),
        )

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)
    monkeypatch.setattr(cli, "_scillm_materialization_preflight_errors", lambda contract: [])
    monkeypatch.setattr(cli, "_scillm_proxy_auth_preflight", _passing_scillm_auth_preflight)
    monkeypatch.setattr(
        cli,
        "validate_native_loop2_run_with_contracts",
        fake_validate_native_loop2_run_with_contracts,
    )

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-scillm-doctor-receipt",
            str(doctor_receipt),
            "--provider",
            "chutes",
            "loop2-run",
            str(contract_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is False
    assert payload["delegated"] is True
    assert payload["native_validation"] == {
        "ok": False,
        "checked_artifacts": ["contract", "final_receipt"],
        "errors": ["node_result: status mismatch"],
    }
    assert payload["errors"] == [
        "native Loop2 validation failed: node_result: status mismatch"
    ]


def test_cli_loop2_run_rejects_blocked_scillm_doctor_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "contract.json"
    doctor_receipt = tmp_path / "scillm-doctor-receipt.json"
    doctor_receipt.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "BLOCKED",
                "mocked": False,
                "live": True,
                "reason": "proxy_auth_preflight_failed",
            }
        )
    )
    loop2_src_path = tmp_path / "loop2" / "src"
    loop2_src_path.mkdir(parents=True)
    runner = loop2_src_path.parent / "run.sh"
    runner.write_text("#!/usr/bin/env bash\nexit 99\n")
    runner.chmod(0o755)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "loop2.repair_node_contract.v1",
                "node_id": "repair-scillm",
                "objective": "Fix with Loop2 Scillm.",
                "repo": str(tmp_path),
                "allowed_globs": ["src/**"],
                "checks": ["python -m pytest"],
                "max_attempts": 1,
                "backend": "scillm",
                "run_root": str(tmp_path / ".loop2" / "runs"),
                "scillm": {
                    "base_url": "http://127.0.0.1:4001",
                    "api_key": "dev-proxy-key-123",
                    "agent_id": "",
                    "agent": "build",
                    "mode": "workspace_write",
                    "model": "opencode-go/kimi-k2.6",
                    "timeout_s": 900,
                },
            }
        )
    )

    def fake_validate_loop2_contract_file(
        selected_contract_path: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_contract_path == contract_path.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))

    monkeypatch.setattr(cli, "validate_loop2_contract_file", fake_validate_loop2_contract_file)

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-scillm-doctor-receipt",
            str(doctor_receipt),
            "--provider",
            "chutes",
            "loop2-run",
            str(contract_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.loop2_contract_run.v1"
    assert payload["ok"] is False
    assert payload["delegated"] is True
    assert payload["run_dir"] == ""
    assert payload["scillm_doctor_receipt"] == str(doctor_receipt.resolve())
    assert payload["errors"] == [
        "Scillm doctor receipt status is 'BLOCKED': proxy_auth_preflight_failed"
    ]


def test_cli_loop2_inspect_prints_summary_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"
    calls: list[Path] = []

    def fake_loop_receipt_summary(selected_run_dir: Path) -> dict[str, object]:
        calls.append(selected_run_dir)
        return {
            "schema": "tau.loop_receipt.summary.v1",
            "found": True,
            "run_id": "tau-loop-run",
            "status": "PASS",
            "mocked": True,
            "live": False,
        }

    monkeypatch.setattr(cli, "loop_receipt_summary", fake_loop_receipt_summary)

    result = CliRunner().invoke(app, ["loop2-inspect", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [run_dir.resolve()]
    assert payload["schema"] == "tau.loop_receipt.summary.v1"
    assert payload["found"] is True
    assert payload["status"] == "PASS"
    assert payload["loop2_contract_validation"] == {
        "ran": False,
        "ok": None,
        "validator": None,
        "checked_artifacts": [],
        "errors": ["not run; pass --loop2-inspect-validate to validate Loop2 contracts"],
    }
    assert payload["tau_delegation"] == {
        "schema": "tau.loop2_delegation.inspect.v1",
        "delegated": False,
        "tau_sanitization_present": False,
        "tau_sanitization_artifact": "",
        "changed_artifacts": [],
        "redacted_keys": [],
        "filtered_changed_files": 0,
        "validation_checked_tau_sanitization": None,
    }


def test_cli_loop2_inspect_exits_nonzero_for_incomplete_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"

    def fake_loop_receipt_summary(selected_run_dir: Path) -> dict[str, object]:
        return {
            "schema": "tau.loop_receipt.summary.v1",
            "found": False,
            "run_id": selected_run_dir.name,
            "missing_artifacts": ["contract", "final_receipt"],
        }

    monkeypatch.setattr(cli, "loop_receipt_summary", fake_loop_receipt_summary)

    result = CliRunner().invoke(app, ["loop2-inspect", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["found"] is False
    assert payload["missing_artifacts"] == ["contract", "final_receipt"]
    assert payload["loop2_contract_validation"]["ran"] is False


def test_cli_loop2_inspect_can_include_contract_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"
    loop2_src = tmp_path / "loop2-src"
    calls: list[tuple[Path, Path | None]] = []

    def fake_loop_receipt_summary(selected_run_dir: Path) -> dict[str, object]:
        return {
            "schema": "tau.loop_receipt.summary.v1",
            "found": True,
            "run_id": selected_run_dir.name,
            "status": "PASS",
        }

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        calls.append((selected_run_dir, loop2_src))
        return LoopReceiptValidationResult(
            ok=True,
            checked_artifacts=("contract", "final_receipt", "node_result"),
        )

    monkeypatch.setattr(cli, "loop_receipt_summary", fake_loop_receipt_summary)
    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src),
            "--loop2-inspect-validate",
            "loop2-inspect",
            str(run_dir),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [(run_dir.resolve(), loop2_src)]
    assert payload["found"] is True
    assert payload["loop2_contract_validation"] == {
        "ran": True,
        "ok": True,
        "validator": "tau_receipt",
        "checked_artifacts": ["contract", "final_receipt", "node_result"],
        "errors": [],
    }


def test_cli_loop2_inspect_projects_tau_delegation_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "native-loop2-run"
    sidecar_path = run_dir / "tau-sanitization.json"
    loop2_src_path = tmp_path / "loop2-src"

    def fake_loop_receipt_summary(selected_run_dir: Path) -> dict[str, object]:
        assert selected_run_dir == run_dir.resolve()
        return {
            "schema": "tau.loop_receipt.summary.v1",
            "found": True,
            "run_id": selected_run_dir.name,
            "status": "PASS",
            "artifacts": {"tau_sanitization": str(sidecar_path)},
            "tau_sanitization": {
                "schema": "tau.loop2_delegated_artifact_sanitization.v1",
                "ran": True,
                "artifact": str(sidecar_path),
                "changed_artifacts": [
                    "contract.json",
                    "final-receipt.json",
                    "node-result.json",
                ],
                "redacted_keys": ["contract.scillm.api_key"],
                "filtered_changed_files": 8,
            },
        }

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        raise AssertionError("delegated native loop2 inspect should not use tau receipt validator")

    def fake_validate_native_loop2_run_with_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        assert selected_run_dir == run_dir.resolve()
        assert loop2_src == loop2_src_path
        return LoopReceiptValidationResult(
            ok=True,
            checked_artifacts=("contract", "final_receipt", "tau_sanitization"),
        )

    monkeypatch.setattr(cli, "loop_receipt_summary", fake_loop_receipt_summary)
    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )
    monkeypatch.setattr(
        cli,
        "validate_native_loop2_run_with_contracts",
        fake_validate_native_loop2_run_with_contracts,
    )

    result = CliRunner().invoke(
        app,
        [
            "--loop2-src",
            str(loop2_src_path),
            "--loop2-inspect-validate",
            "loop2-inspect",
            str(run_dir),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["loop2_contract_validation"] == {
        "ran": True,
        "ok": True,
        "validator": "native_loop2",
        "checked_artifacts": ["contract", "final_receipt", "tau_sanitization"],
        "errors": [],
    }
    assert payload["tau_delegation"] == {
        "schema": "tau.loop2_delegation.inspect.v1",
        "delegated": True,
        "tau_sanitization_present": True,
        "tau_sanitization_artifact": str(sidecar_path),
        "changed_artifacts": [
            "contract.json",
            "final-receipt.json",
            "node-result.json",
        ],
        "redacted_keys": ["contract.scillm.api_key"],
        "filtered_changed_files": 8,
        "validation_checked_tau_sanitization": True,
    }


def test_cli_loop2_inspect_exits_nonzero_when_embedded_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"

    def fake_loop_receipt_summary(selected_run_dir: Path) -> dict[str, object]:
        return {
            "schema": "tau.loop_receipt.summary.v1",
            "found": True,
            "run_id": selected_run_dir.name,
            "status": "PASS",
        }

    def fake_validate_loop_receipt_with_loop2_contracts(
        selected_run_dir: Path,
        *,
        loop2_src: Path | None,
    ) -> LoopReceiptValidationResult:
        del selected_run_dir, loop2_src
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=("contract",),
            errors=("node_result: missing events",),
        )

    monkeypatch.setattr(cli, "loop_receipt_summary", fake_loop_receipt_summary)
    monkeypatch.setattr(
        cli,
        "validate_loop_receipt_with_loop2_contracts",
        fake_validate_loop_receipt_with_loop2_contracts,
    )

    result = CliRunner().invoke(
        app,
        ["--loop2-inspect-validate", "loop2-inspect", str(run_dir)],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["found"] is True
    assert payload["loop2_contract_validation"] == {
        "ran": True,
        "ok": False,
        "validator": "tau_receipt",
        "checked_artifacts": ["contract"],
        "errors": ["node_result: missing events"],
    }


def test_cli_loop2_check_monitor_prints_success_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"
    calls: list[Path] = []

    def fake_check_loop_receipt_monitor_contract(
        selected_run_dir: Path,
    ) -> LoopReceiptMonitorCheckResult:
        calls.append(selected_run_dir)
        return LoopReceiptMonitorCheckResult(
            ok=True,
            checked_endpoints=(
                "summary",
                "transport-dag-evidence",
                "events",
                "events/stream",
            ),
        )

    monkeypatch.setattr(
        cli,
        "check_loop_receipt_monitor_contract",
        fake_check_loop_receipt_monitor_contract,
    )

    result = CliRunner().invoke(app, ["loop2-check-monitor", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [run_dir.resolve()]
    assert payload == {
        "schema": "tau.loop2_monitor_check.v1",
        "run_dir": str(run_dir.resolve()),
        "ok": True,
        "checked_endpoints": [
            "summary",
            "transport-dag-evidence",
            "events",
            "events/stream",
        ],
        "errors": [],
    }


def test_cli_loop2_check_monitor_exits_nonzero_on_endpoint_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"

    def fake_check_loop_receipt_monitor_contract(
        selected_run_dir: Path,
    ) -> LoopReceiptMonitorCheckResult:
        del selected_run_dir
        return LoopReceiptMonitorCheckResult(
            ok=False,
            checked_endpoints=("summary",),
            errors=("events: HTTP 404",),
        )

    monkeypatch.setattr(
        cli,
        "check_loop_receipt_monitor_contract",
        fake_check_loop_receipt_monitor_contract,
    )

    result = CliRunner().invoke(app, ["loop2-check-monitor", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["checked_endpoints"] == ["summary"]
    assert payload["errors"] == ["events: HTTP 404"]


def test_cli_loop2_emit_peer_prints_switchboard_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"
    run_dir.mkdir()
    calls: list[tuple[Path, str, str, str | None]] = []

    class FakeEmitResult:
        ok = True
        switchboard_url = "http://127.0.0.1:7890/emit"
        status_code = 201
        request = {
            "from": "tau",
            "to": "pi-mono",
            "message": "Tau Loop2 receipt is available.",
            "type": "info",
            "priority": "normal",
            "subject": "Tau Loop2 receipt available: run-id",
            "metadata": {
                "schema": "tau.loop_harness_peer_message.v1",
                "run_id": "run-id",
                "claims": {"does_not_prove": ["full DAG scheduling"]},
            },
        }
        response = {"success": True, "id": "msg_123"}
        errors: tuple[str, ...] = ()

    def fake_emit_loop_peer_to_switchboard(
        selected_run_dir: Path,
        *,
        switchboard_url: str,
        target_harness: str,
        monitor_base_url: str | None,
    ) -> FakeEmitResult:
        calls.append((selected_run_dir, switchboard_url, target_harness, monitor_base_url))
        return FakeEmitResult()

    monkeypatch.setattr(cli, "emit_loop_peer_to_switchboard", fake_emit_loop_peer_to_switchboard)

    result = CliRunner().invoke(
        app,
        [
            "--loop2-switchboard-url",
            "http://127.0.0.1:7890",
            "--loop2-peer-target",
            "pi-mono",
            "--loop2-serve-host",
            "127.0.0.1",
            "--loop2-serve-port",
            "4321",
            "loop2-emit-peer",
            str(run_dir),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [
        (
            run_dir.resolve(),
            "http://127.0.0.1:7890",
            "pi-mono",
            "http://127.0.0.1:4321",
        )
    ]
    assert payload["schema"] == "tau.loop_peer_switchboard_emit.v1"
    assert payload["ok"] is True
    assert payload["status_code"] == 201
    assert payload["request"]["to"] == "pi-mono"
    assert payload["request"]["metadata"]["claims"]["does_not_prove"] == ["full DAG scheduling"]


def test_cli_loop2_check_scillm_doctor_accepts_passing_receipt(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "reason": "",
            }
        )
    )

    result = CliRunner().invoke(app, ["loop2-check-scillm-doctor", str(receipt_path)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "schema": "tau.loop2_scillm_doctor_check.v1",
        "receipt": str(receipt_path.resolve()),
        "ok": True,
        "errors": [],
    }


def test_cli_loop2_check_scillm_doctor_rejects_blocked_receipt(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": "scillm.project_agent_sanity.v1",
                "status": "BLOCKED",
                "mocked": False,
                "live": True,
                "reason": "proxy_auth_preflight_failed",
            }
        )
    )

    result = CliRunner().invoke(app, ["loop2-check-scillm-doctor", str(receipt_path)])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload == {
        "schema": "tau.loop2_scillm_doctor_check.v1",
        "receipt": str(receipt_path.resolve()),
        "ok": False,
        "errors": ["Scillm doctor receipt status is 'BLOCKED': proxy_auth_preflight_failed"],
    }


def test_cli_loop2_backfill_artifacts_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"
    calls: list[Path] = []

    def fake_backfill_loop_receipt_artifact_index(selected_run_dir: Path) -> dict[str, object]:
        calls.append(selected_run_dir)
        return {
            "schema": "tau.loop_receipt.artifact_index_backfill.v1",
            "ok": True,
            "run_dir": str(selected_run_dir),
            "changed": True,
            "added_keys": ["contract", "final_receipt"],
            "backup_path": str(
                selected_run_dir / "final-receipt.json.before-artifact-index-backfill"
            ),
            "errors": [],
        }

    monkeypatch.setattr(
        cli,
        "backfill_loop_receipt_artifact_index",
        fake_backfill_loop_receipt_artifact_index,
    )

    result = CliRunner().invoke(app, ["loop2-backfill-artifacts", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [run_dir]
    assert payload["schema"] == "tau.loop_receipt.artifact_index_backfill.v1"
    assert payload["ok"] is True
    assert payload["changed"] is True
    assert payload["added_keys"] == ["contract", "final_receipt"]


def test_cli_loop2_backfill_artifacts_exits_nonzero_on_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "tau-loop-run"

    def fake_backfill_loop_receipt_artifact_index(selected_run_dir: Path) -> dict[str, object]:
        del selected_run_dir
        return {
            "schema": "tau.loop_receipt.artifact_index_backfill.v1",
            "ok": False,
            "changed": False,
            "errors": ["missing final receipt"],
        }

    monkeypatch.setattr(
        cli,
        "backfill_loop_receipt_artifact_index",
        fake_backfill_loop_receipt_artifact_index,
    )

    result = CliRunner().invoke(app, ["loop2-backfill-artifacts", str(run_dir)])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["errors"] == ["missing final receipt"]


def test_cli_loop2_sanity_prints_fixture_proof_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root_dir = tmp_path / "sanity"
    repo = tmp_path / "repo"
    loop2_src = tmp_path / "loop2-src"
    calls: list[tuple[Path, Path, Path | None]] = []

    def fake_run_loop2_sanity(
        *,
        root_dir: Path,
        repo: Path,
        loop2_src: Path | None,
    ) -> dict[str, object]:
        calls.append((root_dir, repo, loop2_src))
        return {
            "schema": "tau.loop2_sanity.v1",
            "ok": True,
            "run_dir": str(root_dir / "run-1"),
            "mocked": True,
            "live": False,
            "loop2_contract_validation": {
                "ok": True,
                "checked_artifacts": ["contract"],
                "errors": [],
            },
            "monitor_check": {
                "ok": True,
                "checked_endpoints": ["summary"],
                "errors": [],
            },
        }

    monkeypatch.setattr(cli, "run_loop2_sanity", fake_run_loop2_sanity)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(repo),
            "--loop2-src",
            str(loop2_src),
            "--loop2-sanity-root",
            str(root_dir),
            "loop2-sanity",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert calls == [(root_dir, repo, loop2_src)]
    assert payload["schema"] == "tau.loop2_sanity.v1"
    assert payload["ok"] is True
    assert payload["mocked"] is True
    assert payload["live"] is False


def test_cli_loop2_sanity_exits_nonzero_on_failed_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_loop2_sanity(
        *,
        root_dir: Path,
        repo: Path,
        loop2_src: Path | None,
    ) -> dict[str, object]:
        del root_dir, repo, loop2_src
        return {
            "schema": "tau.loop2_sanity.v1",
            "ok": False,
            "mocked": True,
            "live": False,
        }

    monkeypatch.setattr(cli, "run_loop2_sanity", fake_run_loop2_sanity)

    result = CliRunner().invoke(app, ["loop2-sanity"])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["schema"] == "tau.loop2_sanity.v1"
    assert payload["ok"] is False


def test_cli_without_prompt_invokes_tui_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str | None, Path, str | None, bool, str | None, int | None, str | None]] = []

    async def fake_run_openai_tui(
        model: str | None,
        cwd: Path,
        session_id: str | None,
        new_session: bool,
        provider_name: str | None,
        auto_compact_token_threshold: int | None,
        initial_prompt: str | None,
    ) -> None:
        calls.append(
            (
                model,
                cwd,
                session_id,
                new_session,
                provider_name,
                auto_compact_token_threshold,
                initial_prompt,
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert calls == [(None, tmp_path, None, False, None, None, None)]


def test_cli_positional_prompt_invokes_tui_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str | None, Path, str | None, bool, str | None, int | None, str | None]] = []

    async def fake_run_openai_tui(
        model: str | None,
        cwd: Path,
        session_id: str | None,
        new_session: bool,
        provider_name: str | None,
        auto_compact_token_threshold: int | None,
        initial_prompt: str | None,
    ) -> None:
        calls.append(
            (
                model,
                cwd,
                session_id,
                new_session,
                provider_name,
                auto_compact_token_threshold,
                initial_prompt,
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(app, ["explain this repo"])

    assert result.exit_code == 0
    assert calls == [(None, tmp_path, None, False, None, None, "explain this repo")]


@pytest.mark.anyio
async def test_run_print_mode_prints_final_assistant_text(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hel"),
                ProviderTextDeltaEvent(delta="lo"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hello")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        resource_paths=TauResourcePaths(root=tmp_path / "resources", agents_root=None),
    )

    captured = capsys.readouterr()
    assert ok is True
    assert captured.out == "Hello\n"
    assert captured.err == ""
    assert provider.calls[0][0] == "fake"
    assert provider.calls[0][1] == build_system_prompt(
        BuildSystemPromptOptions(cwd=tmp_path, tools=create_coding_tools(cwd=tmp_path))
    )
    assert [tool.name for tool in provider.calls[0][3]] == ["read", "write", "edit", "bash"]


@pytest.mark.anyio
async def test_run_print_mode_fails_on_non_recoverable_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderErrorEvent(message="provider failed"),
            ]
        ]
    )

    ok = await run_print_mode(prompt="Say hello", model="fake", cwd=tmp_path, provider=provider)

    captured = capsys.readouterr()
    assert ok is False
    assert captured.out == ""
    assert "Error: provider failed" in captured.err


@pytest.mark.anyio
async def test_run_print_mode_includes_discovered_context(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text("Use the local rules.", encoding="utf-8")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        resource_paths=TauResourcePaths(root=tmp_path / "resources", agents_root=None),
    )

    _captured = capsys.readouterr()
    assert ok is True
    assert "Use the local rules." in provider.calls[0][1]
    assert f'<project_instructions path="{tmp_path / "AGENTS.md"}">' in provider.calls[0][1]


@pytest.mark.anyio
async def test_run_print_mode_persists_session_entries(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "print-session.jsonl")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        storage=storage,
    )

    _captured = capsys.readouterr()
    entries = await storage.read_all()
    messages = [entry.message for entry in entries if isinstance(entry, MessageEntry)]

    assert ok is True
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "Say hello"
    assert messages[1].content == "Done"
    assert any(entry.type == "leaf" for entry in entries)


@pytest.mark.anyio
async def test_run_print_mode_terminal_command_adds_context(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "print-session.jsonl")
    provider = FakeProvider([])

    ok = await run_print_mode(
        prompt="! printf hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        storage=storage,
    )

    captured = capsys.readouterr()
    entries = await storage.read_all()
    messages = [entry.message for entry in entries if isinstance(entry, MessageEntry)]

    assert ok is True
    assert "$ printf hello" in captured.out
    assert "[added to context]" in captured.out
    assert "hello" in captured.out
    assert len(messages) == 1
    assert "Terminal command executed by the user." in messages[0].content
    assert provider.calls == []


@pytest.mark.anyio
async def test_run_print_mode_terminal_command_can_skip_context(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    storage = JsonlSessionStorage(tmp_path / "print-session.jsonl")
    provider = FakeProvider([])

    ok = await run_print_mode(
        prompt="!! printf hidden",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        storage=storage,
    )

    captured = capsys.readouterr()
    entries = await storage.read_all()

    assert ok is True
    assert "$ printf hidden" in captured.out
    assert "[not added to context]" in captured.out
    assert "hidden" in captured.out
    assert not any(isinstance(entry, MessageEntry) for entry in entries)
    assert provider.calls == []


@pytest.mark.anyio
async def test_run_print_mode_expands_skill_commands(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    resource_root = tmp_path / "resources"
    skills_dir = resource_root / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "testing.md").write_text("# Testing\nRun pytest.", encoding="utf-8")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="/skill:testing add tests",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        resource_paths=TauResourcePaths(root=resource_root, agents_root=None),
    )

    _captured = capsys.readouterr()

    assert ok is True
    assert '<skill name="testing" location="' in provider.calls[0][2][0].content
    assert "References are relative to" in provider.calls[0][2][0].content
    assert provider.calls[0][2][0].content.endswith("</skill>\n\nadd tests")


@pytest.mark.anyio
async def test_run_print_mode_can_emit_json_events(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hello"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hello")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        output=PrintOutputMode.json,
    )

    captured = capsys.readouterr()
    assert ok is True
    assert '"type":"agent_start"' in captured.out
    assert '"type":"message_delta"' in captured.out
    assert captured.err == ""


@pytest.mark.anyio
async def test_run_print_mode_can_emit_live_transcript(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hel"),
                ProviderTextDeltaEvent(delta="lo"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hello")),
            ]
        ]
    )

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        output=PrintOutputMode.transcript,
    )

    captured = capsys.readouterr()
    assert ok is True
    assert captured.out == "Hello\n"
    assert captured.err == ""


@pytest.mark.anyio
async def test_run_print_mode_writes_loop2_receipts(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    receipt_root = tmp_path / ".loop2" / "runs"
    check_command = f"{sys.executable} -c \"print('cli check ok')\""

    ok = await run_print_mode(
        prompt="Say hello",
        model="fake",
        cwd=tmp_path,
        provider=provider,
        loop_receipt=LoopReceiptConfig(
            root_dir=receipt_root,
            node_id="cli-print",
            allowed_globs=("src/**",),
            checks=(check_command,),
        ),
    )

    _captured = capsys.readouterr()
    run_dirs = sorted(path for path in receipt_root.iterdir() if path.is_dir())
    assert ok is True
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    contract = json.loads((run_dir / "contract.json").read_text())
    receipt = json.loads((run_dir / "final-receipt.json").read_text())
    node_result = json.loads((run_dir / "node-result.json").read_text())
    evidence = json.loads((run_dir / "transport-dag-evidence.json").read_text())
    rows = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]

    assert contract["schema"] == "loop2.repair_node_contract.v1"
    assert contract["node_id"] == "cli-print"
    assert contract["checks"] == [check_command]
    assert receipt["schema"] == "loop2.final_receipt.v1"
    assert receipt["status"] == "PASS"
    assert receipt["checks"][0]["exit_code"] == 0
    assert Path(receipt["checks"][0]["stdout_path"]).read_text() == "cli check ok\n"
    assert [row["event_type"] for row in rows[-4:]] == [
        "checks_started",
        "check_finished",
        "checks_finished",
        "receipt_written",
    ]
    assert node_result["checks"] == receipt["checks"]
    assert evidence["schema"] == "ux_lab.transport_dag_run_evidence.v1"
    assert evidence["progress_stream"]["last_event_type"] == "receipt_written"


@pytest.mark.anyio
async def test_run_print_mode_writes_loop2_receipts_from_contract_adapter(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Done")),
            ]
        ]
    )
    run_root = tmp_path / ".loop2" / "runs"
    check_command = f"{sys.executable} -c \"print('contract check ok')\""
    contract: dict[str, object] = {
        "schema": "loop2.repair_node_contract.v1",
        "node_id": "contract-node",
        "objective": "Run from a Loop2 contract.",
        "repo": str(tmp_path),
        "allowed_globs": ["**/*"],
        "required_changed_globs": [],
        "checks": [check_command],
        "max_attempts": 1,
        "backend": "fixture",
        "run_root": str(run_root),
    }

    ok = await run_print_mode(
        prompt=str(contract["objective"]),
        model="fake",
        cwd=tmp_path,
        provider=provider,
        loop_receipt=cli._loop_receipt_config_from_contract(contract),
    )

    _captured = capsys.readouterr()
    run_dirs = sorted(path for path in run_root.iterdir() if path.is_dir())
    assert ok is True
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    validation = cli.validate_loop_receipt_with_loop2_contracts(
        run_dir,
        loop2_src=Path(__file__).resolve().parents[2]
        / "agent-skills"
        / "skills"
        / "loop2"
        / "src",
    )
    receipt = json.loads((run_dir / "final-receipt.json").read_text())
    emitted_contract = json.loads((run_dir / "contract.json").read_text())

    assert validation.ok is True
    assert emitted_contract["node_id"] == "contract-node"
    assert emitted_contract["objective"] == "Run from a Loop2 contract."
    assert emitted_contract["run_root"] == str(run_root)
    assert receipt["mocked"] is True
    assert receipt["live"] is False
    assert receipt["checks"][0]["exit_code"] == 0
    assert Path(receipt["checks"][0]["stdout_path"]).read_text() == "contract check ok\n"


def test_cli_exits_nonzero_when_print_mode_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_openai_print_mode(
        prompt: str,
        model: str | None,
        cwd: Path,
        output: PrintOutputMode,
        provider_name: str | None,
        loop_receipt: LoopReceiptConfig | None,
    ) -> bool:
        return False

    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(app, ["-p", "hello"])

    assert result.exit_code == 1


def test_cli_print_mode_passes_loop2_receipt_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[LoopReceiptConfig | None] = []

    async def fake_run_openai_print_mode(
        prompt: str,
        model: str | None,
        cwd: Path,
        output: PrintOutputMode,
        provider_name: str | None,
        loop_receipt: LoopReceiptConfig | None,
    ) -> bool:
        del prompt, model, cwd, output, provider_name
        calls.append(loop_receipt)
        return True

    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "-p",
            "hello",
            "--loop2-receipt-root",
            str(tmp_path / ".loop2" / "runs"),
            "--loop2-node-id",
            "cli-node",
            "--loop2-allowed-glob",
            "src/**",
            "--loop2-required-changed-glob",
            "src/**/*.py",
            "--loop2-check",
            "python -m pytest",
        ],
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0] == LoopReceiptConfig(
        root_dir=tmp_path / ".loop2" / "runs",
        node_id="cli-node",
        allowed_globs=("src/**",),
        required_changed_globs=("src/**/*.py",),
        checks=("python -m pytest",),
    )


def test_cli_print_mode_marks_nonfake_loop2_receipt_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[LoopReceiptConfig | None] = []

    async def fake_run_openai_print_mode(
        prompt: str,
        model: str | None,
        cwd: Path,
        output: PrintOutputMode,
        provider_name: str | None,
        loop_receipt: LoopReceiptConfig | None,
    ) -> bool:
        del prompt, model, cwd, output, provider_name
        calls.append(loop_receipt)
        return True

    monkeypatch.setattr(cli, "run_openai_print_mode", fake_run_openai_print_mode)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--provider",
            "chutes",
            "-p",
            "hello",
            "--loop2-receipt-root",
            str(tmp_path / ".loop2" / "runs"),
            "--loop2-check",
            "python -m pytest",
        ],
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0] is not None
    assert calls[0].mocked is False
    assert calls[0].live is True


def test_cli_print_mode_rejects_loop2_receipt_without_check(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "-p",
            "hello",
            "--loop2-receipt-root",
            str(tmp_path / ".loop2" / "runs"),
        ],
    )

    assert result.exit_code != 0
    assert "--loop2-receipt-root requires at least one --loop2-check" in result.output


def test_cli_print_mode_rejects_loop2_required_changed_glob_without_receipt_root(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "-p",
            "hello",
            "--loop2-required-changed-glob",
            "src/**/*.py",
        ],
    )

    assert result.exit_code != 0
    assert "--loop2-required-changed-glob requires --loop2-receipt-root" in result.output


def test_default_tui_invokes_tui_runner_with_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str | None, Path, str | None, bool, str | None, int | None, str | None]] = []

    async def fake_run_openai_tui(
        model: str | None,
        cwd: Path,
        session_id: str | None,
        new_session: bool,
        provider_name: str | None,
        auto_compact_token_threshold: int | None,
        initial_prompt: str | None,
    ) -> None:
        calls.append(
            (
                model,
                cwd,
                session_id,
                new_session,
                provider_name,
                auto_compact_token_threshold,
                initial_prompt,
            )
        )

    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "fake",
            "--provider",
            "local",
            "--resume",
            "session-1",
            "--auto-compact-threshold",
            "1000",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("fake", tmp_path, "session-1", False, "local", 1000, None)]


def test_default_tui_rejects_resume_with_new_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_openai_tui(
        model: str | None,
        cwd: Path,
        session_id: str | None,
        new_session: bool,
        provider_name: str | None,
        auto_compact_token_threshold: int | None,
        initial_prompt: str | None,
    ) -> None:
        del model, cwd, session_id, new_session, provider_name, auto_compact_token_threshold
        del initial_prompt
        raise RuntimeError("--resume and --new-session cannot be used together")

    monkeypatch.setattr(cli, "run_openai_tui", fake_run_openai_tui)

    result = CliRunner().invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--resume",
            "session-1",
            "--new-session",
        ],
    )

    assert result.exit_code != 0
    assert "--resume and --new-session cannot be used together" in result.output


def test_sessions_command_lists_indexed_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = CodingSessionRecord(
        id="session-1",
        path=tmp_path / "session.jsonl",
        cwd=tmp_path,
        model="fake",
        title="Test session",
        created_at=1.0,
        updated_at=2.0,
    )

    class FakeSessionManager:
        def list_sessions(self) -> list[CodingSessionRecord]:
            return [record]

    monkeypatch.setattr(cli, "SessionManager", FakeSessionManager)

    result = CliRunner().invoke(app, ["sessions"])

    assert result.exit_code == 0
    assert "session-1" in result.stdout
    assert "Test session" in result.stdout


def test_sessions_command_handles_empty_index(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSessionManager:
        def list_sessions(self) -> list[CodingSessionRecord]:
            return []

    monkeypatch.setattr(cli, "SessionManager", FakeSessionManager)

    result = CliRunner().invoke(app, ["sessions"])

    assert result.exit_code == 0
    assert "No sessions found." in result.stdout


@pytest.mark.anyio
async def test_export_session_command_writes_html_for_indexed_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(
        cwd=tmp_path,
        model="fake",
        title="Exported Session",
        session_id="session-1",
    )
    await JsonlSessionStorage(record.path).append(
        MessageEntry(id="root", message=UserMessage(content="Export this"))
    )

    output_path = await cli.export_session_command(
        "session-1",
        tmp_path / "session.html",
        session_manager=manager,
    )

    html = output_path.read_text(encoding="utf-8")
    assert output_path == tmp_path / "session.html"
    assert "<title>Exported Session</title>" in html
    assert "Export this" in html
    assert str(record.path) in html


@pytest.mark.anyio
async def test_export_session_command_writes_html_for_jsonl_path(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    cwd = Path.cwd()
    await JsonlSessionStorage(session_path).append(
        MessageEntry(id="root", message=UserMessage(content="Path export"))
    )

    try:
        import os

        os.chdir(tmp_path)
        output_path = await cli.export_session_command(str(session_path))
    finally:
        os.chdir(cwd)

    html = output_path.read_text(encoding="utf-8")
    assert output_path == tmp_path / "session.html"
    assert "<title>Tau session session</title>" in html
    assert "Path export" in html


@pytest.mark.anyio
async def test_export_session_command_writes_jsonl_format_to_cwd(tmp_path: Path) -> None:
    session_path = tmp_path / ".tau" / "sessions" / "session.jsonl"
    cwd = Path.cwd()
    await JsonlSessionStorage(session_path).append(
        MessageEntry(id="root", message=UserMessage(content="JSONL export"))
    )

    try:
        import os

        os.chdir(tmp_path)
        output_path = await cli.export_session_command(str(session_path), export_format="jsonl")
    finally:
        os.chdir(cwd)

    assert output_path == tmp_path / "session.jsonl"
    assert "JSONL export" in output_path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_export_session_command_treats_suffixless_output_as_directory(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "source" / "session.jsonl"
    await JsonlSessionStorage(session_path).append(
        MessageEntry(id="root", message=UserMessage(content="Directory export"))
    )

    output_path = await cli.export_session_command(str(session_path), tmp_path / "exports")

    assert output_path == tmp_path / "exports" / "session.html"
    assert "Directory export" in output_path.read_text(encoding="utf-8")


def test_export_command_invokes_exporter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, Path | None, str | None]] = []
    output_path = tmp_path / "out.html"

    async def fake_export_session_command(
        session_ref: str,
        requested_output_path: Path | None = None,
        requested_export_format: str | None = None,
    ) -> Path:
        calls.append((session_ref, requested_output_path, requested_export_format))
        return output_path

    monkeypatch.setattr(cli, "export_session_command", fake_export_session_command)

    result = CliRunner().invoke(app, ["export", "session-1", str(output_path)])

    assert result.exit_code == 0
    assert calls == [("session-1", output_path, None)]
    assert f"Exported session to {output_path}" in result.stdout


def test_export_command_accepts_format_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, Path | None, str | None]] = []
    output_path = tmp_path / "out.jsonl"

    async def fake_export_session_command(
        session_ref: str,
        requested_output_path: Path | None = None,
        requested_export_format: str | None = None,
    ) -> Path:
        calls.append((session_ref, requested_output_path, requested_export_format))
        return output_path

    monkeypatch.setattr(cli, "export_session_command", fake_export_session_command)

    result = CliRunner().invoke(app, ["export", "session-1", "--format", "jsonl"])

    assert result.exit_code == 0
    assert calls == [("session-1", None, "jsonl")]


def test_providers_command_lists_default_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(app, ["providers"])

    assert result.exit_code == 0
    assert "*\topenai\topenai-compatible\tgpt-5.5" in result.stdout
    assert " \topenai-codex\topenai-codex\tgpt-5.5" in result.stdout
    assert " \tanthropic\tanthropic\tclaude-sonnet-4-6" in result.stdout
    assert " \topenrouter\topenai-compatible\topenai/gpt-5.5" in result.stdout
    assert " \thuggingface\topenai-compatible\topenai/gpt-oss-120b" in result.stdout
    assert " \tchutes\topenai-compatible\tQwen/Qwen3-32B-TEE" in result.stdout


def test_render_provider_settings_shows_credential_source(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("STORED_API_KEY", raising=False)
    monkeypatch.setenv("ENV_API_KEY", "env-key")
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    settings = ProviderSettings(
        default_provider="stored",
        providers=(
            OpenAICompatibleProviderConfig(
                name="stored",
                api_key_env="STORED_API_KEY",
                credential_name="stored",
            ),
            OpenAICompatibleProviderConfig(
                name="env",
                api_key_env="ENV_API_KEY",
                credential_name=None,
            ),
            OpenAICompatibleProviderConfig(
                name="missing",
                api_key_env="MISSING_API_KEY",
                credential_name="missing",
            ),
        ),
    )

    class FakeCredentials:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "stored" else None

    cli.render_provider_settings(settings, credential_reader=FakeCredentials())

    output = capsys.readouterr().out
    assert "*\tstored\topenai-compatible\tgpt-5.5" in output
    assert "\tSTORED_API_KEY\tstored:stored\t" in output
    assert "\tENV_API_KEY\tenv:ENV_API_KEY\t" in output
    assert "\tMISSING_API_KEY\tmissing\t" in output


def test_setup_command_writes_provider_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")

    result = CliRunner().invoke(
        app,
        [
            "--provider",
            "local",
            "--base-url",
            "http://localhost:11434/v1/",
            "--api-key-env",
            "LOCAL_API_KEY",
            "--timeout-seconds",
            "120",
            "--max-retries",
            "2",
            "--max-retry-delay-seconds",
            "0.5",
            "--model",
            "qwen",
            "setup",
        ],
    )

    settings = load_provider_settings(TauPaths(home=tmp_path / ".tau"))
    provider = settings.get_provider("local")
    assert result.exit_code == 0
    assert "Saved provider 'local'" in result.stdout
    assert settings.default_provider == "local"
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.api_key_env == "LOCAL_API_KEY"
    assert provider.default_model == "qwen"
    assert provider.timeout_seconds == 120
    assert provider.max_retries == 2
    assert provider.max_retry_delay_seconds == 0.5


def test_setup_command_warns_when_api_key_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MISSING_API_KEY", raising=False)

    result = CliRunner().invoke(
        app,
        [
            "--provider",
            "missing",
            "--api-key-env",
            "MISSING_API_KEY",
            "--model",
            "test-model",
            "setup",
        ],
    )

    assert result.exit_code == 0
    assert "Set MISSING_API_KEY before running Tau with this provider." in result.stderr


def test_setup_chutes_command_writes_builtin_chutes_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CHUTES_API_TOKEN", "test-key")

    result = CliRunner().invoke(app, ["setup-chutes"])

    settings = load_provider_settings(TauPaths(home=tmp_path / ".tau"))
    provider = settings.get_provider("chutes")
    assert result.exit_code == 0
    assert "Saved provider 'chutes'" in result.stdout
    assert settings.default_provider == "chutes"
    assert provider.base_url == "https://llm.chutes.ai/v1"
    assert provider.api_key_env == "CHUTES_API_TOKEN"
    assert provider.credential_name == "chutes"
    assert provider.default_model == "Qwen/Qwen3-32B-TEE"
    assert "Qwen/Qwen3-32B-TEE" in provider.models


def test_setup_chutes_command_accepts_model_override_and_warns_without_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CHUTES_API_TOKEN", raising=False)

    result = CliRunner().invoke(
        app,
        ["--model", "Qwen/Qwen3-32B-TEE", "--no-set-default", "setup-chutes"],
    )

    settings = load_provider_settings(TauPaths(home=tmp_path / ".tau"))
    provider = settings.get_provider("chutes")
    assert result.exit_code == 0
    assert settings.default_provider == "openai"
    assert provider.default_model == "Qwen/Qwen3-32B-TEE"
    assert "Set CHUTES_API_TOKEN before running Tau with this provider." in result.stderr
