import json
from pathlib import Path

from tau_coding.ticket_closure_evidence import (
    BLOCKED_E2E_REQUIRED,
    TICKET_CLOSURE_EVIDENCE_SCHEMA,
    validate_code_ticket_closure_evidence,
)


def test_code_ticket_closure_rejects_unit_only_evidence() -> None:
    result = validate_code_ticket_closure_evidence(
        {
            "schema": TICKET_CLOSURE_EVIDENCE_SCHEMA,
            "issue": 183,
            "unit": {"command": "uv run pytest -q", "exit_code": 0, "passed": 1},
        }
    )

    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.blocker == BLOCKED_E2E_REQUIRED
    assert "e2e must be an object" in result.errors


def test_code_ticket_closure_accepts_live_e2e_readback(tmp_path: Path) -> None:
    artifact = tmp_path / "live-artifact.json"
    artifact.write_text(
        json.dumps({"schema": "example.live.v1", "mocked": False, "live": True}) + "\n",
        encoding="utf-8",
    )
    agentic_report = _write_agentic_eval_report(tmp_path)

    result = validate_code_ticket_closure_evidence(
        {
            "schema": TICKET_CLOSURE_EVIDENCE_SCHEMA,
            "issue": 183,
            "unit": {"command": "uv run pytest -q", "exit_code": 0, "passed": 1},
            "e2e": {
                "command": "tau ticket-subagent-closure-proof --allow-live-filesystem",
                "exit_code": 0,
                "mocked": False,
                "live": True,
                "artifact": str(artifact),
            },
            "agentic_evals": {
                "command": "agentic-evals run evals/tau_core_agentic_eval.json",
                "exit_code": 0,
                "report": str(agentic_report),
            },
        }
    )

    assert result.ok is True
    assert result.status == "ACCEPTED"
    assert result.artifact_payload == {"schema": "example.live.v1", "mocked": False, "live": True}


def test_code_ticket_closure_rejects_missing_agentic_evals(tmp_path: Path) -> None:
    artifact = tmp_path / "live-artifact.json"
    artifact.write_text(json.dumps({"mocked": False, "live": True}) + "\n", encoding="utf-8")

    result = validate_code_ticket_closure_evidence(
        {
            "schema": TICKET_CLOSURE_EVIDENCE_SCHEMA,
            "issue": 183,
            "unit": {"command": "uv run pytest -q", "exit_code": 0, "passed": 1},
            "e2e": {
                "command": "tau ticket-subagent-closure-proof --allow-live-filesystem",
                "exit_code": 0,
                "mocked": False,
                "live": True,
                "artifact": str(artifact),
            },
        }
    )

    assert result.ok is False
    assert result.blocker == BLOCKED_E2E_REQUIRED
    assert "agentic_evals must be an object" in result.errors


def test_code_ticket_closure_rejects_unready_agentic_evals_report(tmp_path: Path) -> None:
    artifact = tmp_path / "live-artifact.json"
    artifact.write_text(json.dumps({"mocked": False, "live": True}) + "\n", encoding="utf-8")
    agentic_report = _write_agentic_eval_report(tmp_path, readiness="FAILED")

    result = validate_code_ticket_closure_evidence(
        {
            "schema": TICKET_CLOSURE_EVIDENCE_SCHEMA,
            "issue": 183,
            "unit": {"command": "uv run pytest -q", "exit_code": 0, "passed": 1},
            "e2e": {
                "command": "tau ticket-subagent-closure-proof --allow-live-filesystem",
                "exit_code": 0,
                "mocked": False,
                "live": True,
                "artifact": str(artifact),
            },
            "agentic_evals": {
                "command": "agentic-evals run evals/tau_core_agentic_eval.json",
                "exit_code": 0,
                "report": str(agentic_report),
            },
        }
    )

    assert result.ok is False
    assert result.blocker == BLOCKED_E2E_REQUIRED
    assert "agentic_evals.report.readiness must be READY" in result.errors


def test_code_ticket_closure_rejects_deterministic_runner_as_e2e(tmp_path: Path) -> None:
    artifact = tmp_path / "live-artifact.json"
    artifact.write_text(json.dumps({"mocked": False, "live": True}) + "\n", encoding="utf-8")
    agentic_report = _write_agentic_eval_report(tmp_path)

    result = validate_code_ticket_closure_evidence(
        {
            "schema": TICKET_CLOSURE_EVIDENCE_SCHEMA,
            "issue": 183,
            "unit": {"command": "uv run pytest -q", "exit_code": 0, "passed": 1},
            "e2e": {
                "command": "uv run pytest -q tests/test_ticket_closure_evidence.py",
                "exit_code": 0,
                "mocked": False,
                "live": True,
                "artifact": str(artifact),
            },
            "agentic_evals": {
                "command": "agentic-evals run evals/tau_core_agentic_eval.json",
                "exit_code": 0,
                "report": str(agentic_report),
            },
        }
    )

    assert result.ok is False
    assert result.blocker == BLOCKED_E2E_REQUIRED
    assert "e2e.command must not be a deterministic test runner" in result.errors


def _write_agentic_eval_report(tmp_path: Path, *, readiness: str = "READY") -> Path:
    path = tmp_path / "agentic-evals-report.json"
    path.write_text(
        json.dumps(
            {
                "schema": "agentic_evals.report.v2",
                "readiness": readiness,
                "mocked": False,
                "live": True,
                "case_count": 1,
                "trial_count": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path
