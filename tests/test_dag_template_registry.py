from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_template_registry import (
    compile_dag_template,
    dag_template_registry_payload,
)
from tau_coding.project_dag import (
    DAG_RECEIPT_SCHEMA,
    run_project_dag_contract,
    validate_dag_contract,
)


def test_dag_template_registry_lists_required_patterns() -> None:
    payload = dag_template_registry_payload()

    assert payload["schema"] == "tau.dag_template_registry.v1"
    assert {template["name"] for template in payload["templates"]} == {
        "single-call",
        "prompt-chain",
        "reflection-loop",
        "roundtable",
        "compete",
        "plan-execute-verify",
        "claim-chain-verification",
        "specialist-fanout-join",
        "dry-run-human-approval",
        "memory-recalled-workflow",
    }


def test_dag_template_compile_cli_examples_validate_all_templates(tmp_path: Path) -> None:
    for template_name, params in _template_params(tmp_path).items():
        params_path = tmp_path / f"{template_name}-params.json"
        out_path = tmp_path / f"{template_name}-dag.json"
        receipt_path = tmp_path / f"{template_name}-compile-receipt.json"
        params_path.write_text(
            json.dumps(params, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            app,
            [
                "dag-template-compile",
                "--template",
                template_name,
                "--params",
                str(params_path),
                "--out",
                str(out_path),
                "--receipt",
                str(receipt_path),
            ],
        )

        assert result.exit_code == 0
        receipt = json.loads(result.output)
        contract = json.loads(out_path.read_text(encoding="utf-8"))
        validate_dag_contract(contract)
        assert receipt["schema"] == "tau.dag_template_compile_receipt.v1"
        assert receipt["ok"] is True
        assert receipt["provider_live"] is False
        assert contract["context"]["dag_template"]["name"] == template_name
        assert contract["nodes"]
        assert contract["edges"]


def test_dag_template_compile_missing_fields_writes_interview_packet(tmp_path: Path) -> None:
    params_path = tmp_path / "missing-roundtable.json"
    out_path = tmp_path / "roundtable-dag.json"
    receipt_path = tmp_path / "compile-receipt.json"
    missing_path = tmp_path / "missing-fields.json"
    params_path.write_text(
        json.dumps(
            {
                "dag_id": "missing-roundtable",
                "goal": _goal(),
                "target": _target(),
                "handlers": ["handler-a"],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "dag-template-compile",
            "--template",
            "roundtable",
            "--params",
            str(params_path),
            "--out",
            str(out_path),
            "--receipt",
            str(receipt_path),
            "--missing-out",
            str(missing_path),
        ],
    )

    assert result.exit_code == 1
    receipt = json.loads(result.output)
    packet = json.loads(missing_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "BLOCKED"
    assert receipt["interview_required"] is True
    assert packet["schema"] == "tau.dag_template_missing_fields.v1"
    assert packet["status"] == "INTERVIEW_REQUIRED"
    assert "handlers[1]" in packet["missing_fields"]
    assert "join" in packet["missing_fields"]


def test_compiled_single_call_template_runs_local_fixture(tmp_path: Path) -> None:
    response = _handoff_response(
        previous="handler",
        next_agent="human",
        evidence=[{"kind": "handler_receipt", "path": str(tmp_path / "handler.json")}],
    )
    command_spec = _write_response_spec(tmp_path, "handler", response)
    params = {
        "dag_id": "single-call-local-fixture",
        "goal": _goal(),
        "target": _target(),
        "handler": {"id": "handler", "agent": "handler"},
        "command_specs": {"handler": str(command_spec)},
        "limits": {"default_timeout_seconds": 5, "max_total_attempts": 1},
    }
    contract = compile_dag_template("single-call", params)
    contract_path = tmp_path / "single-call-dag.json"
    contract_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["schema"] == DAG_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["selected_agents"] == ["handler"]
    assert receipt["observed_edges"] == [
        {
            "from_agent": "handler",
            "from_node": "handler",
            "to_agent": "human",
            "to_node": "human",
        }
    ]


def _template_params(tmp_path: Path) -> dict[str, dict[str, object]]:
    return {
        "single-call": {
            "dag_id": "template-single-call",
            "goal": _goal(),
            "target": _target(),
            "handler": "handler",
        },
        "prompt-chain": {
            "dag_id": "template-prompt-chain",
            "goal": _goal(),
            "target": _target(),
            "steps": ["outline", "draft"],
        },
        "reflection-loop": {
            "dag_id": "template-reflection-loop",
            "goal": _goal(),
            "target": _target(),
            "creator": "creator",
            "reviewer": "reviewer",
        },
        "roundtable": {
            "dag_id": "template-roundtable",
            "goal": _goal(),
            "target": _target(),
            "handlers": ["webgpt", "webclaude"],
            "join": "join",
        },
        "compete": {
            "dag_id": "template-compete",
            "goal": _goal(),
            "target": _target(),
            "competitors": ["candidate-a", "candidate-b"],
            "judge": "judge",
        },
        "plan-execute-verify": {
            "dag_id": "template-plan-execute-verify",
            "goal": _goal(),
            "target": _target(),
            "planner": "planner",
            "executor": "executor",
            "verifier": "verifier",
        },
        "claim-chain-verification": {
            "dag_id": "template-claim-chain-verification",
            "goal": _goal(),
            "target": _target(),
            "claim_steps": ["claim-a", "claim-b"],
            "verifier": "verifier",
        },
        "specialist-fanout-join": {
            "dag_id": "template-specialist-fanout-join",
            "goal": _goal(),
            "target": _target(),
            "specialists": ["specialist-a", "specialist-b"],
            "join": "join",
        },
        "dry-run-human-approval": {
            "dag_id": "template-dry-run-human-approval",
            "goal": _goal(),
            "target": _target(),
            "dry_run": "dry-run",
            "approval_packet": "approval-packet",
        },
        "memory-recalled-workflow": {
            "dag_id": "template-memory-recalled-workflow",
            "goal": _goal(),
            "target": _target(),
            "memory_recall": "memory-recall",
            "handler": "handler",
        },
    }


def _goal() -> dict[str, object]:
    return {
        "goal_id": "dag-template-test",
        "goal_version": 1,
        "goal_hash": "sha256:active-goal",
    }


def _target() -> dict[str, str]:
    return {"repo": "grahama1970/tau", "target": "scratch-dag-template"}


def _write_response_spec(tmp_path: Path, agent: str, response: dict[str, object]) -> Path:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"print({json.dumps(json.dumps(response))})"
    spec_path.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "timeout_s": 5}),
        encoding="utf-8",
    )
    return spec_path


def _handoff_response(
    *,
    previous: str,
    next_agent: str,
    evidence: list[object],
) -> dict[str, object]:
    normalized_evidence: list[object] = []
    goal_hash = str(_goal()["goal_hash"])
    for item in evidence:
        if isinstance(item, dict) and item.get("kind") != "dag_contract" and "goal_hash" not in item:
            normalized_evidence.append({**item, "goal_hash": goal_hash})
        else:
            normalized_evidence.append(item)
    return {
        "schema": "tau.agent_handoff.v1",
        "github": _target(),
        "goal": _goal(),
        "previous_subagent": previous,
        "context": {"summary": f"{previous} completed.", "artifacts": []},
        "result": {
            "status": "PASS",
            "summary": f"{previous} completed.",
            "evidence": normalized_evidence,
        },
        "rationale": "The DAG template controls the route.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Continue along the compiled DAG route.",
        },
        "required_evidence": ["handler_receipt"],
        "stop_condition": "Stop at human.",
    }
