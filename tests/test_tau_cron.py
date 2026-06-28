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


def test_tau_cron_once_runs_one_bounded_tick_with_valid_start(tmp_path: Path) -> None:
    receipt_root = tmp_path / "receipts"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    start = tmp_path / "start-handoff.json"
    start.write_text('{"schema":"tau.agent_handoff.v1"}\n', encoding="utf-8")
    command_log = tmp_path / "tau-command.json"
    fake_tau = bin_dir / "tau"
    fake_tau.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python",
                "import json, os, sys",
                f"open({str(command_log)!r}, 'w', encoding='utf-8').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_tau.chmod(0o755)
    env = _cron_env(receipt_root)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "TAU_ORCHESTRATOR_START": str(start),
            "TAU_ORCHESTRATOR_ONCE": "1",
            "TAU_AGENTS_ROOT": "/agents",
            "TAU_COMMAND_SPEC_ROOT": "/specs",
            "TAU_ACTIVE_GOAL_HASH": "sha256:active-goal",
            "TAU_GOAL_GUARDIAN_TICKET_SOURCE": "/tickets/source.json",
        }
    )

    result = subprocess.run(
        ["bash", str(TAU_CRON)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    invocation = json.loads(command_log.read_text())
    argv = invocation["argv"]

    assert result.returncode == 0
    assert "handoff-command-loop" in result.stdout
    assert "TAU_ORCHESTRATOR_ONCE=1; exiting after one bounded tick" in result.stdout
    assert argv[:3] == ["handoff-command-loop", "--start", str(start)]
    assert "--receipt-dir" in argv
    assert "--agents-root" in argv
    assert argv[argv.index("--agents-root") + 1] == "/agents"
    assert "--command-spec-root" in argv
    assert argv[argv.index("--command-spec-root") + 1] == "/specs"
    assert "--active-goal-hash" in argv
    assert argv[argv.index("--active-goal-hash") + 1] == "sha256:active-goal"
    assert "--goal-guardian-ticket-source" in argv
    assert argv[argv.index("--goal-guardian-ticket-source") + 1] == "/tickets/source.json"
    assert len(list(receipt_root.iterdir())) == 1
    assert command_log.exists()


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
