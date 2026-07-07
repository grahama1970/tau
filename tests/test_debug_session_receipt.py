import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.debug_session_receipt import (
    DEBUG_SESSION_RECEIPT_SCHEMA,
    SUPPORTED_ADAPTERS,
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
    assert receipt["supported_adapters"] == ["debugpy", "dlv", "lldb-dap", "node"]
    assert receipt["target"] == "python -m pytest tests/test_example.py"
    assert receipt["goal_hash"] == "sha256:debug-goal"
    assert receipt["session_sha256"] == f"sha256:{_sha256(session)}"
    assert receipt["session_bytes"] == session.stat().st_size
    assert receipt["session_artifact"] == {
        "label": "debug_session_packet",
        "path": str(session.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256(session)}",
        "bytes": session.stat().st_size,
    }


def test_debug_receipt_accepts_required_common_adapters(tmp_path: Path) -> None:
    assert SUPPORTED_ADAPTERS == {"debugpy", "lldb-dap", "dlv", "node"}
    for adapter in sorted(SUPPORTED_ADAPTERS):
        session = _write_debug_session(tmp_path / adapter, adapter=adapter)

        receipt = write_debug_session_receipt(
            session_path=session,
            output_path=tmp_path / adapter / "debug-session-receipt.json",
            required=True,
        )

        assert receipt["status"] == "PASS"
        assert receipt["adapter"] == adapter
        assert receipt["supported_adapters"] == ["debugpy", "dlv", "lldb-dap", "node"]
        assert receipt["adapter_available"] is True


def test_debug_receipt_blocks_missing_adapter_when_required(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path, adapter_available=False)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        required=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "debug_adapter_unavailable" in receipt["alert_codes"]


def test_debug_receipt_blocks_missing_target(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload.pop("target")
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_debug_target" in receipt["alert_codes"]


def test_debug_receipt_records_hash_for_unreadable_session_packet(tmp_path: Path) -> None:
    session = tmp_path / "debug-session.json"
    session.write_text("{not-json", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "debug_session_unreadable" in receipt["alert_codes"]
    assert receipt["session_sha256"] == f"sha256:{_sha256(session)}"
    assert receipt["session_bytes"] == session.stat().st_size


def test_debug_receipt_records_null_hash_for_missing_session_packet(
    tmp_path: Path,
) -> None:
    session = tmp_path / "missing-debug-session.json"

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "debug_session_missing" in receipt["alert_codes"]
    assert receipt["session_path"] == str(session.resolve())
    assert receipt["session_sha256"] is None
    assert receipt["session_bytes"] is None
    assert receipt["session_artifact"] == {
        "label": "debug_session_packet",
        "path": str(session.resolve()),
        "exists": False,
        "sha256": None,
        "bytes": None,
    }


def test_debug_receipt_blocks_malformed_evidence_shapes(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["breakpoints"] = "not-list"
    payload["stopped_frame"] = []
    payload["variables"] = "not-list"
    payload["commands"] = "not-list"
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "invalid_breakpoints" in receipt["alert_codes"]
    assert "invalid_stopped_frame" in receipt["alert_codes"]
    assert "invalid_variables" in receipt["alert_codes"]
    assert "invalid_commands" in receipt["alert_codes"]


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
            "exists": True,
            "sha256": f"sha256:{_sha256(stdout)}",
            "bytes": stdout.stat().st_size,
        },
        {
            "label": "stderr",
            "path": str(stderr.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(stderr)}",
            "bytes": stderr.stat().st_size,
        },
    ]


def test_debug_receipt_blocks_log_paths_outside_session_directory(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path / "session")
    outside = tmp_path / "outside-debug-stdout.txt"
    outside.write_text("outside log\n", encoding="utf-8")
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["stdout_path"] = str(outside)
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "stdout_path_outside_session_dir" in receipt["alert_codes"]
    assert receipt["stdout_path"] is None
    assert receipt["stdout_sha256"] is None


def test_debug_receipt_blocks_missing_log_path(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["stdout_path"] = "missing-debug-stdout.txt"
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "stdout_path_missing" in receipt["alert_codes"]
    assert receipt["stdout_path"] is None


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


def test_debug_receipt_zero_trust_blocks_missing_goal_hash(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path, goal_hash=None)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_goal_hash" in receipt["alert_codes"]


def test_debug_receipt_zero_trust_blocks_invalid_policy_boundary_schema(
    tmp_path: Path,
) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile={"schema": "not.tau.policy", "profile_id": "test"},
        data_boundary={"schema": "not.tau.boundary", "classification": "public"},
    )

    assert receipt["status"] == "BLOCKED"
    assert "invalid_policy_profile_schema" in receipt["alert_codes"]
    assert "invalid_data_boundary_schema" in receipt["alert_codes"]


def test_debug_receipt_blocks_expected_goal_hash_mismatch(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        expected_goal_hash="sha256:other-goal",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["goal_hash"] == "sha256:debug-goal"
    assert receipt["expected_goal_hash"] == "sha256:other-goal"
    assert "goal_hash_mismatch" in receipt["alert_codes"]


def test_debug_receipt_zero_trust_accepts_policy_boundary(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "PASS"
    assert receipt["zero_trust"] is True
    assert receipt["goal_hash"] == "sha256:debug-goal"
    assert receipt["policy_profile"]["profile_id"] == "test"
    assert receipt["data_boundary"]["classification"] == "public"


def test_debug_receipt_zero_trust_blocks_shell_control_target(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["target"] = "python -m pytest tests/test_example.py; curl https://example.invalid"
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["target"] == payload["target"]
    assert "unsafe_debug_target" in receipt["alert_codes"]


def test_debug_receipt_legacy_allows_shell_control_target(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["target"] = "python -m pytest tests/test_example.py; echo legacy"
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["target"] == payload["target"]
    assert "unsafe_debug_target" not in receipt["alert_codes"]


def test_debug_receipt_zero_trust_blocks_invalid_data_boundary(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    boundary = _data_boundary()
    boundary["classification"] = "classified-not-allowed"
    boundary.pop("foreign_person_access")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile=_policy_profile(),
        data_boundary=boundary,
    )

    assert receipt["status"] == "BLOCKED"
    assert "invalid_data_boundary" in receipt["alert_codes"]
    assert "classified_not_allowed" in receipt["alert_codes"]
    assert "foreign_person_access must be one of" in receipt["alerts"][0]["errors"][0]


def test_debug_receipt_zero_trust_honors_log_read_denylist(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile=_policy_profile(read_denylist=["debug-stdout.txt"]),
        data_boundary=_data_boundary(),
    )

    stderr = tmp_path / "debug-stderr.txt"
    assert receipt["status"] == "BLOCKED"
    assert "policy_read_denied" in receipt["alert_codes"]
    assert receipt["stdout_path"] is None
    assert receipt["stdout_sha256"] is None
    assert receipt["stdout_bytes"] is None
    assert receipt["log_artifacts"] == [
        {
            "label": "stderr",
            "path": str(stderr.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(stderr)}",
            "bytes": stderr.stat().st_size,
        }
    ]


def test_debug_receipt_blocks_malformed_policy_read_denylist(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
        zero_trust=True,
        policy_profile=_policy_profile(read_denylist="debug-stdout.txt"),
        data_boundary=_data_boundary(),
    )

    assert receipt["status"] == "BLOCKED"
    assert "invalid_policy_read_denylist" in receipt["alert_codes"]


def test_debug_receipt_blocks_evidence_outside_allowed_paths(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["allowed_paths"] = ["tests/**"]
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["allowed_paths"] == ["tests/**"]
    assert "debug_evidence_path_disallowed" in receipt["alert_codes"]


def test_debug_receipt_blocks_evidence_matching_forbidden_paths(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["allowed_paths"] = ["src/**", "tests/**"]
    payload["forbidden_paths"] = ["src/example.py"]
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["forbidden_paths"] == ["src/example.py"]
    assert "debug_evidence_path_forbidden" in receipt["alert_codes"]


def test_debug_receipt_blocks_absolute_or_escaping_evidence_path(tmp_path: Path) -> None:
    session = _write_debug_session(tmp_path)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["allowed_paths"] = ["src/**", "tests/**"]
    payload["breakpoints"] = [{"file": "../outside.py", "line": 2}]
    payload["stopped_frame"] = {"file": "/tmp/outside.py", "line": 2, "function": "answer"}
    session.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_debug_session_receipt(
        session_path=session,
        output_path=tmp_path / "debug-session-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["alert_codes"].count("debug_evidence_path_escape") == 2


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
            "--goal-hash",
            "sha256:debug-goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == DEBUG_SESSION_RECEIPT_SCHEMA
    assert payload["expected_goal_hash"] == "sha256:debug-goal"


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
    adapter: str = "debugpy",
    adapter_available: bool = True,
    goal_hash: str | None = "sha256:debug-goal",
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    stdout = tmp_path / "debug-stdout.txt"
    stderr = tmp_path / "debug-stderr.txt"
    stdout.write_text("stopped at breakpoint\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    payload = {
        "schema": "tau.debug_session_packet.v1",
        "goal_hash": goal_hash,
        "target": "python -m pytest tests/test_example.py",
        "adapter": adapter,
        "adapter_available": adapter_available,
        "breakpoints": [{"file": "src/example.py", "line": 2}],
        "stopped_frame": {"file": "src/example.py", "line": 2, "function": "answer"},
        "variables": [{"name": "value", "value": "41"}],
        "commands": ["set_breakpoint", "continue", "locals"],
        "stdout_path": str(stdout),
        "stderr_path": str(stderr),
        "conclusion": "The failing value is still 41 before the patch.",
    }
    if goal_hash is None:
        payload.pop("goal_hash")
    path = tmp_path / "debug-session.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy_profile(read_denylist: object | None = None) -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "test",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {
            "external_search": "deny",
            "manual_sanitized_receipt": "allow_with_review",
        },
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {
            "write_allowlist": ["src/**", "tests/**"],
            "read_denylist": [] if read_denylist is None else read_denylist,
        },
    }


def _data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }
