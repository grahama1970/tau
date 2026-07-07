import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.debug_session_receipt import DEBUG_SESSION_RECEIPT_SCHEMA
from tau_coding.debugger_skill_adapter import (
    DEBUGGER_SKILL_ADAPTER_RECEIPT_SCHEMA,
    write_debugger_skill_adapter_receipt,
)


def test_debugger_adapter_accepts_debugger_proof(tmp_path: Path) -> None:
    proof = _write_debugger_proof(tmp_path)

    receipt = write_debugger_skill_adapter_receipt(
        proof_path=proof,
        output_path=tmp_path / "adapter-receipt.json",
        debug_session_output_path=tmp_path / "debug-session-receipt.json",
        repo_root=tmp_path,
        expected_goal_hash="sha256:goal",
    )

    debug_receipt = json.loads((tmp_path / "debug-session-receipt.json").read_text())
    assert receipt["schema"] == DEBUGGER_SKILL_ADAPTER_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["debug_session_status"] == "PASS"
    assert debug_receipt["schema"] == DEBUG_SESSION_RECEIPT_SCHEMA
    assert debug_receipt["target"] == "python -m pytest tests/test_example.py"


def test_debugger_adapter_blocks_missing_goal_hash(tmp_path: Path) -> None:
    proof = _write_debugger_proof(tmp_path, goal_hash=None)

    receipt = write_debugger_skill_adapter_receipt(
        proof_path=proof,
        output_path=tmp_path / "adapter-receipt.json",
        debug_session_output_path=tmp_path / "debug-session-receipt.json",
        repo_root=tmp_path,
        zero_trust=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "goal_hash is required in zero-trust mode" in receipt["errors"]
    assert receipt["course_correction"]["trigger"] == "debugger_evidence_required"


def test_debugger_adapter_blocks_stdout_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-debug-stdout.txt"
    outside.write_text("outside\n", encoding="utf-8")
    proof = _write_debugger_proof(tmp_path, stdout_path=str(outside))

    receipt = write_debugger_skill_adapter_receipt(
        proof_path=proof,
        output_path=tmp_path / "adapter-receipt.json",
        debug_session_output_path=tmp_path / "debug-session-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert any("stdout_path escapes repo root" in error for error in receipt["errors"])


def test_debugger_adapter_redacts_sensitive_variable_values(tmp_path: Path) -> None:
    proof = _write_debugger_proof(
        tmp_path,
        variables=[
            {"name": "api_token", "value": "secret-token"},
            {"name": "value", "value": "41"},
        ],
    )

    receipt = write_debugger_skill_adapter_receipt(
        proof_path=proof,
        output_path=tmp_path / "adapter-receipt.json",
        debug_session_output_path=tmp_path / "debug-session-receipt.json",
        repo_root=tmp_path,
    )

    debug_receipt_text = (tmp_path / "debug-session-receipt.json").read_text()
    debug_receipt = json.loads(debug_receipt_text)
    assert receipt["status"] == "PASS"
    assert debug_receipt["variable_redaction_count"] == 1
    assert debug_receipt["variables"][0]["value"] == "[REDACTED]"
    assert "secret-token" not in debug_receipt_text


def test_debugger_adapter_course_corrects_missing_proof(tmp_path: Path) -> None:
    proof = tmp_path / "missing-debugger-proof.json"

    receipt = write_debugger_skill_adapter_receipt(
        proof_path=proof,
        output_path=tmp_path / "adapter-receipt.json",
        debug_session_output_path=tmp_path / "debug-session-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["course_correction"]["required_next_action"] == "debug_or_route_reviewer"


def test_cli_debugger_skill_adapter_writes_adapter_and_debug_receipts(
    tmp_path: Path,
) -> None:
    proof = _write_debugger_proof(tmp_path)
    out = tmp_path / "adapter-receipt.json"
    debug_out = tmp_path / "debug-session-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "debugger-skill-adapter",
            "--proof",
            str(proof),
            "--out",
            str(out),
            "--debug-session-out",
            str(debug_out),
            "--repo-root",
            str(tmp_path),
            "--goal-hash",
            "sha256:goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == DEBUGGER_SKILL_ADAPTER_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    debug_payload = json.loads(debug_out.read_text(encoding="utf-8"))
    assert debug_payload["schema"] == DEBUG_SESSION_RECEIPT_SCHEMA


def _write_debugger_proof(
    tmp_path: Path,
    *,
    goal_hash: str | None = "sha256:goal",
    stdout_path: str = "debug-stdout.txt",
    variables: list[dict[str, str]] | None = None,
) -> Path:
    (tmp_path / "debug-stdout.txt").write_text("stopped at breakpoint\n", encoding="utf-8")
    (tmp_path / "debug-stderr.txt").write_text("", encoding="utf-8")
    payload = {
        "schema": "debugger.proof.v1",
        "goal_hash": goal_hash,
        "target_command": "python -m pytest tests/test_example.py",
        "adapter_label": "debugpy",
        "adapter_available": True,
        "allowed_paths": ["tests/test_example.py"],
        "forbidden_paths": [],
        "breakpoints": [{"path": "tests/test_example.py", "line": 12}],
        "stopped_frame": {
            "path": "tests/test_example.py",
            "line": 12,
            "function": "answer",
        },
        "variables": variables or [{"name": "value", "value": "41"}],
        "commands": ["next", "continue"],
        "stdout_path": stdout_path,
        "stderr_path": "debug-stderr.txt",
        "conclusion": "Stopped at expected frame.",
    }
    proof = tmp_path / "debugger-proof.json"
    proof.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return proof
