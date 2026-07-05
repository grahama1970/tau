import json
from pathlib import Path

import pytest

from tau_coding.receipt_signing import (
    SIGNED_RECEIPT_SCHEMA,
    SIGNED_RECEIPT_VERIFICATION_SCHEMA,
    sign_receipt,
    verify_signed_receipt,
)


def test_sign_receipt_wraps_receipt_hash_and_manifests(tmp_path: Path) -> None:
    receipt, key, actor_manifest, environment_manifest = _write_signing_inputs(tmp_path)
    out = tmp_path / "signed-receipt.json"

    envelope = sign_receipt(
        receipt_path=receipt,
        key_path=key,
        actor_manifest_path=actor_manifest,
        environment_manifest_path=environment_manifest,
        output_path=out,
    )

    assert envelope["schema"] == SIGNED_RECEIPT_SCHEMA
    assert envelope["ok"] is True
    assert envelope["algorithm"] == "HMAC-SHA256"
    assert envelope["signed_payload"]["receipt"]["schema"] == "tau.test_receipt.v1"
    assert envelope["signature"].startswith("hmac-sha256:")
    assert out.exists()
    assert "Public-key non-repudiation." in envelope["proof_scope"]["does_not_prove"]


def test_verify_signed_receipt_accepts_unchanged_inputs(tmp_path: Path) -> None:
    receipt, key, actor_manifest, environment_manifest = _write_signing_inputs(tmp_path)
    signed = tmp_path / "signed-receipt.json"
    sign_receipt(
        receipt_path=receipt,
        key_path=key,
        actor_manifest_path=actor_manifest,
        environment_manifest_path=environment_manifest,
        output_path=signed,
    )

    verification = verify_signed_receipt(signed_receipt_path=signed, key_path=key)

    assert verification["schema"] == SIGNED_RECEIPT_VERIFICATION_SCHEMA
    assert verification["ok"] is True
    assert verification["status"] == "PASS"
    assert verification["errors"] == []


def test_verify_signed_receipt_blocks_tampered_receipt_file(tmp_path: Path) -> None:
    receipt, key, actor_manifest, environment_manifest = _write_signing_inputs(tmp_path)
    signed = tmp_path / "signed-receipt.json"
    sign_receipt(
        receipt_path=receipt,
        key_path=key,
        actor_manifest_path=actor_manifest,
        environment_manifest_path=environment_manifest,
        output_path=signed,
    )
    receipt.write_text(
        json.dumps({"schema": "tau.test_receipt.v1", "status": "BLOCKED"}),
        encoding="utf-8",
    )

    verification = verify_signed_receipt(signed_receipt_path=signed, key_path=key)

    assert verification["ok"] is False
    assert verification["status"] == "BLOCKED"
    assert "receipt sha256 mismatch" in verification["errors"]


def test_sign_receipt_blocks_empty_key(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    key = tmp_path / "key.txt"
    receipt.write_text(json.dumps({"schema": "tau.test_receipt.v1"}), encoding="utf-8")
    key.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="key path is empty"):
        sign_receipt(receipt_path=receipt, key_path=key)


def _write_signing_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    receipt = tmp_path / "receipt.json"
    key = tmp_path / "key.txt"
    actor_manifest = tmp_path / "actor-manifest.json"
    environment_manifest = tmp_path / "environment-manifest.json"
    receipt.write_text(
        json.dumps({"schema": "tau.test_receipt.v1", "ok": True, "status": "PASS"}),
        encoding="utf-8",
    )
    key.write_text("local-test-key", encoding="utf-8")
    actor_manifest.write_text(
        json.dumps({"schema": "tau.actor_manifest.v1", "run_id": "run-1", "actors": []}),
        encoding="utf-8",
    )
    environment_manifest.write_text(
        json.dumps(
            {
                "schema": "tau.environment_manifest.v1",
                "run_id": "run-1",
                "network_policy": "deny",
            }
        ),
        encoding="utf-8",
    )
    return receipt, key, actor_manifest, environment_manifest
