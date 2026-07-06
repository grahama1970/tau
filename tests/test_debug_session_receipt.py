import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.debug_session_receipt import (
    DEBUG_SESSION_RECEIPT_SCHEMA,
    write_debug_session_receipt,
)


def test_debug_receipt_records_adapter_and_target(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["schema"] == DEBUG_SESSION_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["adapter"] == "debugpy"
    assert receipt["target"] == "python -m pytest tests/test_example.py"


def test_debug_receipt_blocks_missing_adapter_when_required(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path, adapter_available=False)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        required=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "debug_adapter_unavailable" in receipt["alert_codes"]


def test_debug_receipt_records_variables_and_frames(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["stopped_frame"]["function"] == "answer"
    assert receipt["variables"] == [{"name": "value", "value": "41"}]


def test_debug_receipt_never_claims_fix_correctness(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert "The bug is fixed." in receipt["proof_scope"]["does_not_prove"]
    assert "The code is correct." in receipt["proof_scope"]["does_not_prove"]


def test_cli_debug_session_receipt_writes_receipt(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    out = tmp_path / "debug-session-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "debug-session-receipt",
            "--session",
            str(session),
            "--out",
            str(out),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == DEBUG_SESSION_RECEIPT_SCHEMA


def _write_debug_session(
    tmp_path: Path,
    *,
    adapter_available: bool = True,
) -> Path:
    stdout = tmp_path / "debug-stdout.txt"
    stderr = tmp_path / "debug-stderr.txt"
    stdout.write_text("stopped at breakpoint\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    payload = {
        "schema": "tau.debug_session_packet.v1",
        "target": "python -m pytest tests/test_example.py",
        "adapter": "debugpy",
        "adapter_available": adapter_available,
        "breakpoints": [{"file": "src/example.py", "line": 2}],
        "stopped_frame": {"file": "src/example.py", "line": 2, "function": "answer"},
        "variables": [{"name": "value", "value": "41"}],
        "commands": ["set_breakpoint", "continue", "locals"],
        "stdout_path": str(stdout),
        "stderr_path": str(stderr),
        "conclusion": "The failing value is still 41 before the patch.",
    }
    path = tmp_path / "debug-session.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
