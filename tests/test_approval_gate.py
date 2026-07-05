import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tau_coding.approval_gate import evaluate_approval_gate
from tau_coding.cli import _parse_approval_gate_check_cli_args


def test_approval_gate_passes_valid_human_packet(tmp_path: Path) -> None:
    packet = tmp_path / "approval.json"
    _write_packet(packet, action="working_tree_mutation")

    receipt = evaluate_approval_gate(
        approval_packet=packet,
        requested_action="working_tree_mutation",
        run_dir=tmp_path / "run",
    )

    assert receipt["schema"] == "tau.approval_gate_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    assert receipt["approved"] is True
    assert receipt["approval_packet_sha256"] == hashlib.sha256(packet.read_bytes()).hexdigest()
    assert receipt["packet_summary"]["actor_id"] == "human:graham"
    assert receipt["packet_summary"]["actor_auth_method"] == "manual"
    assert receipt["packet_summary"]["human_id"] == "human:graham"
    assert receipt["packet_summary"]["signature_present"] is True
    assert (tmp_path / "run" / "approval-gate-receipt.json").exists()


def test_approval_gate_blocks_missing_packet(tmp_path: Path) -> None:
    receipt = evaluate_approval_gate(
        approval_packet=tmp_path / "missing.json",
        requested_action="github_ticket_closure",
        run_dir=tmp_path / "run",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["approved"] is False
    assert "approval packet not found" in receipt["errors"][0]


def test_approval_gate_blocks_action_mismatch(tmp_path: Path) -> None:
    packet = tmp_path / "approval.json"
    _write_packet(packet, action="working_tree_mutation")

    receipt = evaluate_approval_gate(
        approval_packet=packet,
        requested_action="github_ticket_closure",
        run_dir=tmp_path / "run",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "action must match requested_action github_ticket_closure" in receipt["errors"]


def test_approval_gate_blocks_expired_packet(tmp_path: Path) -> None:
    packet = tmp_path / "approval.json"
    _write_packet(
        packet,
        action="working_tree_mutation",
        expires_at=(datetime.now(UTC) - timedelta(minutes=1))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    )

    receipt = evaluate_approval_gate(
        approval_packet=packet,
        requested_action="working_tree_mutation",
        run_dir=tmp_path / "run",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["approved"] is False
    assert "approval packet expired" in receipt["errors"]
    assert receipt["packet_summary"]["expires_at"].endswith("Z")


def test_approval_gate_blocks_missing_provenance_fields(tmp_path: Path) -> None:
    packet = tmp_path / "approval.json"
    _write_packet(packet, action="herdr_cleanup_apply")
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload.pop("nonce")
    payload.pop("signature")
    payload["actor"].pop("auth_method")
    packet.write_text(json.dumps(payload), encoding="utf-8")

    receipt = evaluate_approval_gate(
        approval_packet=packet,
        requested_action="herdr_cleanup_apply",
        run_dir=tmp_path / "run",
    )

    assert receipt["ok"] is False
    assert "actor.auth_method must be one of" in "\n".join(receipt["errors"])
    assert "nonce must be a non-empty string" in receipt["errors"]
    assert "signature must be a non-empty string" in receipt["errors"]


def test_parse_approval_gate_check_cli_args() -> None:
    options = _parse_approval_gate_check_cli_args(
        [
            "--approval-packet",
            "approval.json",
            "--requested-action",
            "working_tree_mutation",
            "--run-dir",
            "run",
            "--output",
            "receipt.json",
        ]
    )

    assert options == {
        "approval_packet": Path("approval.json"),
        "requested_action": "working_tree_mutation",
        "run_dir": Path("run"),
        "output": Path("receipt.json"),
    }


def _write_packet(path: Path, *, action: str, expires_at: str | None = None) -> None:
    payload = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "action": action,
        "actor": {"id": "human:graham", "auth_method": "manual"},
        "target": {"id": "scratch-run"},
        "reason": "Approve bounded scratch action for proof.",
        "evidence": ["run-receipt.json"],
        "nonce": "approval-nonce-001",
        "signature": "manual-signature-recorded-by-test",
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
