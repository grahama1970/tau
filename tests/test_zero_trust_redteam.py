import json
import subprocess
import sys
from pathlib import Path

from tau_coding.zero_trust_redteam import (
    ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA,
    run_zero_trust_redteam,
)


def test_zero_trust_redteam_blocks_all_expected_paths(tmp_path: Path) -> None:
    receipt = run_zero_trust_redteam(output_dir=tmp_path)

    assert receipt["schema"] == ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["attempt_count"] == 9
    assert receipt["passed_attempt_count"] == 9
    names = {attempt["name"] for attempt in receipt["attempts"]}
    assert "skip_memory_intent" in names
    assert "tampered_signed_receipt" in names
    assert (tmp_path / "zero-trust-redteam-receipt.json").exists()


def test_zero_trust_redteam_script_writes_receipt(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run-zero-trust-redteam.py",
            "--out-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["schema"] == ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert (tmp_path / "zero-trust-redteam-receipt.json").exists()
