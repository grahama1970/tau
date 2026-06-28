import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAU_CRON = ROOT / "docker" / "tau-cron.sh"


def test_tau_cron_fails_closed_without_start_handoff(tmp_path: Path) -> None:
    receipt_root = tmp_path / "receipts"
    env = _cron_env(receipt_root)
    env.pop("TAU_ORCHESTRATOR_START", None)

    result = subprocess.run(
        ["bash", str(TAU_CRON)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    receipts = sorted(receipt_root.glob("tau-cron-preflight-*.json"))

    assert result.returncode == 64
    assert "TAU_ORCHESTRATOR_START is required" in result.stderr
    assert "handoff-command-loop" not in result.stdout
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["schema"] == "tau.cron_preflight_receipt.v1"
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "missing_start"
    assert receipt["command_executed"] is False
    assert receipt["mocked"] is False
    assert receipt["live"] is True


def test_tau_cron_fails_closed_when_start_path_is_not_file(tmp_path: Path) -> None:
    receipt_root = tmp_path / "receipts"
    env = _cron_env(receipt_root)
    env["TAU_ORCHESTRATOR_START"] = str(tmp_path / "missing-start-handoff.json")

    result = subprocess.run(
        ["bash", str(TAU_CRON)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    receipts = sorted(receipt_root.glob("tau-cron-preflight-*.json"))

    assert result.returncode == 66
    assert "does not point to a readable file" in result.stderr
    assert "handoff-command-loop" not in result.stdout
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["schema"] == "tau.cron_preflight_receipt.v1"
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "start_not_file"
    assert receipt["start"] == env["TAU_ORCHESTRATOR_START"]
    assert receipt["command_executed"] is False


def _cron_env(receipt_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "TAU_RECEIPT_DIR": str(receipt_root),
            "TAU_ORCHESTRATOR_INTERVAL_SECONDS": "1",
            "TAU_ORCHESTRATOR_MAX_STEPS": "1",
        }
    )
    return env
