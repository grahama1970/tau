"""Framing, torn-tail, and identity tests for the write-intent sidecar."""

from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.dag_runtime.write_intent import (
    WriteIntentError,
    append_intent,
    read_sidecar,
)


def _append(path: Path, stage: str = "S1", node: str = "n1") -> dict[str, object]:
    return append_intent(
        path,
        run_id="run-1",
        node_id=node,
        attempt_id="attempt-abc",
        receipt_kind="node_receipt",
        stage=stage,
        target_path="/tmp/receipts/n1/attempt-1.json",
    )


def test_roundtrip_preserves_records_in_order(tmp_path: Path) -> None:
    sidecar = tmp_path / "intents.twi"
    first = _append(sidecar, stage="S1")
    second = _append(sidecar, stage="S5")

    result = read_sidecar(sidecar)
    assert result.torn_tail_reason is None
    assert list(result.records) == [first, second]


def test_missing_sidecar_reads_as_empty(tmp_path: Path) -> None:
    result = read_sidecar(tmp_path / "absent.twi")
    assert result.records == ()
    assert result.torn_tail_reason is None


@pytest.mark.parametrize("cut", [1, 5, 11, 20])
def test_torn_final_append_is_ignored_not_fatal(tmp_path: Path, cut: int) -> None:
    sidecar = tmp_path / "intents.twi"
    kept = _append(sidecar)
    intact = sidecar.read_bytes()
    _append(sidecar, stage="S5")
    torn = sidecar.read_bytes()
    sidecar.write_bytes(torn[: len(intact) + cut])

    result = read_sidecar(sidecar)
    assert list(result.records) == [kept]
    assert result.torn_tail_bytes == cut
    assert result.torn_tail_reason in {"truncated_header", "truncated_body"}


def test_corrupted_final_body_is_reported_as_crc_tail(tmp_path: Path) -> None:
    sidecar = tmp_path / "intents.twi"
    kept = _append(sidecar)
    boundary = sidecar.stat().st_size
    _append(sidecar, stage="S5")
    blob = bytearray(sidecar.read_bytes())
    blob[-1] ^= 0xFF
    sidecar.write_bytes(bytes(blob))

    result = read_sidecar(sidecar)
    assert list(result.records) == [kept]
    assert result.torn_tail_reason == "crc_mismatch"
    assert result.torn_tail_bytes == len(blob) - boundary


def test_mid_file_corruption_raises_rather_than_skipping_history(tmp_path: Path) -> None:
    sidecar = tmp_path / "intents.twi"
    first_len = len(_append(sidecar)) and sidecar.stat().st_size
    _append(sidecar, stage="S5")
    blob = bytearray(sidecar.read_bytes())
    blob[first_len - 3] ^= 0xFF  # damage inside the FIRST record's body

    sidecar.write_bytes(bytes(blob))
    with pytest.raises(WriteIntentError, match="mid-file CRC mismatch"):
        read_sidecar(sidecar)


def test_identity_fields_are_required(tmp_path: Path) -> None:
    with pytest.raises(WriteIntentError, match="attempt_id"):
        append_intent(
            tmp_path / "x.twi",
            run_id="r",
            node_id="n",
            attempt_id="",
            receipt_kind="node_receipt",
            stage="S1",
        )
