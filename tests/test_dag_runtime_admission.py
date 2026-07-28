"""Crash-boundary tests for the admission-grade durable write primitive."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tau_coding.dag_runtime.admission import (
    AdmissionWriteError,
    read_back_durable_json,
    write_durable_json,
)


def test_write_produces_bound_digest_and_readback_verifies(tmp_path: Path) -> None:
    result = write_durable_json(tmp_path / "receipts" / "node.json", {"a": 1})

    raw = result.path.read_bytes()
    assert result.size_bytes == len(raw)
    assert read_back_durable_json(result) == {"a": 1}


def test_rewrite_replaces_atomically_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "node.json"
    first = write_durable_json(target, {"attempt": 1})
    second = write_durable_json(target, {"attempt": 2})

    assert first.sha256 != second.sha256
    assert json.loads(target.read_text()) == {"attempt": 2}
    leftovers = [p for p in tmp_path.iterdir() if p.name != "node.json"]
    assert leftovers == []


def test_readback_detects_post_write_mutation(tmp_path: Path) -> None:
    target = tmp_path / "node.json"
    result = write_durable_json(target, {"a": 1})
    target.write_text("{tampered")

    with pytest.raises(AdmissionWriteError, match="digest mismatch"):
        read_back_durable_json(result)


def test_write_failure_leaves_prior_content_intact(tmp_path: Path) -> None:
    target = tmp_path / "node.json"
    write_durable_json(target, {"attempt": 1})

    # Non-serializable payload fails before S2 completes.
    with pytest.raises(TypeError):
        write_durable_json(target, {"bad": object()})  # type: ignore[dict-item]

    assert json.loads(target.read_text()) == {"attempt": 1}


_KILL_HARNESS = """
import os, signal, sys
sys.path.insert(0, {src!r})
from pathlib import Path
from unittest.mock import patch
import tau_coding.dag_runtime.admission as adm

target = Path({target!r})
boundary = {boundary!r}
_original_replace = os.replace

def die(*_a, **_k):
    os.kill(os.getpid(), signal.SIGKILL)

if boundary == "during_temp_write":
    with patch.object(adm.os, "fsync", side_effect=die):
        adm.write_durable_json(target, {{"attempt": 99}})
elif boundary == "before_rename":
    with patch.object(adm.os, "replace", side_effect=die):
        adm.write_durable_json(target, {{"attempt": 99}})
elif boundary == "after_rename_before_dirsync":
    def replace_then_die(src_p, dst_p):
        _original_replace(src_p, dst_p)
        die()
    with patch.object(adm.os, "replace", side_effect=replace_then_die):
        adm.write_durable_json(target, {{"attempt": 99}})
"""


@pytest.mark.parametrize(
    "boundary", ["during_temp_write", "before_rename", "after_rename_before_dirsync"]
)
def test_sigkill_at_each_boundary_never_leaves_a_torn_final_file(
    tmp_path: Path, boundary: str
) -> None:
    """A process killed inside the primitive leaves the final path either
    absent/previous (before S4) or complete and valid (at/after S4)."""

    src = str(Path(__file__).resolve().parents[1] / "src")
    target = tmp_path / "node.json"
    write_durable_json(target, {"attempt": 1})
    before = target.read_bytes()

    code = _KILL_HARNESS.format(src=src, target=str(target), boundary=boundary)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert proc.returncode == -9, proc.stderr.decode()

    after = target.read_bytes()
    payload = json.loads(after)
    if boundary == "after_rename_before_dirsync":
        assert payload == {"attempt": 99}
    else:
        assert after == before
    # Whatever happened, the file parses and is one of the two full payloads.
    assert payload in ({"attempt": 1}, {"attempt": 99})
