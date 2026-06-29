import json
from pathlib import Path

from tau_coding.scillm_subagent_gate import validate_scillm_subagent_loop_summary


def test_scillm_subagent_gate_rejects_timeout_with_parsed_acceptance(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "summary.json",
        {
            "schema": "tau.scillm_subagent_while_loop_live_proof.v1",
            "mocked": False,
            "live": True,
            "out_dir": str(tmp_path),
            "final_status": "reviewer_passed",
            "events": [
                {
                    "attempt": 1,
                    "accepted": True,
                    "reviewer": {
                        "accepted": True,
                        "verdict": "pass",
                        "verified": True,
                    },
                }
            ],
        },
    )
    attempt_dir = tmp_path / "attempt_001"
    attempt_dir.mkdir()
    _write_json(
        attempt_dir / "reviewer_tau_subagent_receipt.json",
        {
            "schema": "tau.subagent_receipt.v1",
            "result": {
                "status": "BLOCKED",
                "kind": "blocked_substrate",
                "reason": "scillm_opencode_timeout",
                "parsed": {
                    "accepted": True,
                    "verdict": "pass",
                    "verified": True,
                },
            },
        },
    )

    result = validate_scillm_subagent_loop_summary(tmp_path / "summary.json")

    assert result.ok is False
    assert result.blocked_substrate_receipts == (
        str(attempt_dir / "reviewer_tau_subagent_receipt.json"),
    )
    assert any("accepted=true requires completed reviewer substrate" in error for error in result.errors)
    assert any("final_status" in error for error in result.errors)


def test_scillm_subagent_gate_accepts_completed_reviewer_pass(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "summary.json",
        {
            "schema": "tau.scillm_subagent_while_loop_live_proof.v1",
            "mocked": False,
            "live": True,
            "out_dir": str(tmp_path),
            "final_status": "reviewer_passed",
            "events": [
                {
                    "attempt": 1,
                    "accepted": True,
                    "reviewer": {
                        "accepted": True,
                        "verdict": "pass",
                        "verified": True,
                    },
                }
            ],
        },
    )
    attempt_dir = tmp_path / "attempt_001"
    attempt_dir.mkdir()
    _write_json(
        attempt_dir / "reviewer_tau_subagent_receipt.json",
        {
            "schema": "tau.subagent_receipt.v1",
            "result": {
                "status": "COMPLETED",
                "kind": "delegate_result",
                "reason": "ok",
                "parsed": {
                    "accepted": True,
                    "verdict": "pass",
                    "verified": True,
                },
            },
        },
    )

    result = validate_scillm_subagent_loop_summary(tmp_path / "summary.json")

    assert result.ok is True
    assert result.errors == ()
    assert result.blocked_substrate_receipts == ()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
