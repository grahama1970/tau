import hashlib
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


def test_debug_receipt_records_log_artifact_hashes(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    stdout = tmp_path / "debug-stdout.txt"
    stderr = tmp_path / "debug-stderr.txt"
    assert receipt["stdout_sha256"] == f"sha256:{_sha256(stdout)}"
    assert receipt["stdout_bytes"] == len("stopped at breakpoint\n".encode("utf-8"))
    assert receipt["stderr_sha256"] == f"sha256:{_sha256(stderr)}"
    assert receipt["stderr_bytes"] == 0
    assert receipt["log_artifacts"] == [
        {
            "label": "stdout",
            "path": str(stdout.resolve()),
            "sha256": f"sha256:{_sha256(stdout)}",
            "bytes": stdout.stat().st_size,
        },
        {
            "label": "stderr",
            "path": str(stderr.resolve()),
            "sha256": f"sha256:{_sha256(stderr)}",
            "bytes": stderr.stat().st_size,
        },
    ]


def test_debug_receipt_never_claims_fix_correctness(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert "The bug is fixed." in receipt["proof_scope"]["does_not_prove"]
    assert "The code is correct." in receipt["proof_scope"]["does_not_prove"]


def test_debug_receipt_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_policy_profile" in receipt["alert_codes"]
    assert "missing_data_boundary" in receipt["alert_codes"]


def test_debug_receipt_zero_trust_accepts_policy_boundary(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile={"schema": "tau.policy_profile.v1", "profile_id": "test"},
        data_boundary={"schema": "tau.data_boundary.v1", "classification": "public"},
    )

    assert receipt["status"] == "PASS"
    assert receipt["zero_trust"] is True
    assert receipt["policy_profile"]["profile_id"] == "test"
    assert receipt["data_boundary"]["classification"] == "public"


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


def test_cli_debug_session_zero_trust_missing_boundary_exits_blocked(
    tmp_path: Path,
) -> None:
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
            "--zero-trust",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
