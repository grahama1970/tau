from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import tau_coding.docker_sandbox as docker_sandbox
from tau_coding.cli import app
from tau_coding.docker_sandbox import SANDBOX_RUN_RECEIPT_SCHEMA, write_docker_sandbox_receipt

PINNED_IMAGE = (
    "python@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)


def test_docker_sandbox_blocks_unpinned_image(tmp_path: Path) -> None:
    receipt = write_docker_sandbox_receipt(
        image="python:3.12",
        command=["python", "--version"],
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["schema"] == SANDBOX_RUN_RECEIPT_SCHEMA
    assert receipt["ok"] is False
    assert "unpinned_image" in receipt["alert_codes"]
    assert receipt["command_executed"] is False
    assert receipt["docker_command"] == []


def test_docker_sandbox_blocks_docker_socket_mount(tmp_path: Path) -> None:
    receipt = write_docker_sandbox_receipt(
        image=PINNED_IMAGE,
        command=["python", "--version"],
        docker_socket_mounted=True,
        mounts=["type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock"],
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert "docker_socket_mount_requested" in receipt["alert_codes"]


def test_docker_sandbox_blocks_privileged_host_network_and_home_mount(tmp_path: Path) -> None:
    receipt = write_docker_sandbox_receipt(
        image=PINNED_IMAGE,
        command=["python", "--version"],
        privileged=True,
        host_network=True,
        network="host",
        mounts=["type=bind,source=/home/graham,target=/workspace"],
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is False
    assert {
        "privileged_requested",
        "host_network_requested",
        "network_not_none",
        "broad_home_mount_requested",
    }.issubset(set(receipt["alert_codes"]))


def test_docker_sandbox_builds_strict_nonexecuted_command(tmp_path: Path) -> None:
    receipt = write_docker_sandbox_receipt(
        image=PINNED_IMAGE,
        command=["python", "--version"],
        mounts=["type=bind,source=/tmp/scratch,target=/workspace,readonly"],
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["command_executed"] is False
    assert receipt["backend"]["image_digest"] == f"sha256:{'a' * 64}"
    assert "--network" in receipt["docker_command"]
    assert "none" in receipt["docker_command"]
    assert "--read-only" in receipt["docker_command"]
    assert ["--cap-drop", "ALL"] == receipt["docker_command"][8:10]
    assert "no-new-privileges:true" in receipt["docker_command"]


def test_docker_sandbox_executes_when_explicitly_requested(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 7
        cidfile = Path(command[command.index("--cidfile") + 1])
        cidfile.write_text("container-123\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="tau-runtime\n", stderr="")

    monkeypatch.setattr(docker_sandbox.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(docker_sandbox.subprocess, "run", fake_run)
    receipt = write_docker_sandbox_receipt(
        image=PINNED_IMAGE,
        command=["sh", "-c", "echo tau-runtime"],
        receipt_path=tmp_path / "receipt.json",
        execute=True,
        timeout_seconds=7,
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["live"] is True
    assert receipt["command_executed"] is True
    assert receipt["execution"]["exit_code"] == 0
    assert receipt["execution"]["container_id"] == "container-123"
    assert Path(receipt["execution"]["stdout_path"]).read_text(encoding="utf-8") == (
        "tau-runtime\n"
    )
    assert calls and "--cidfile" in calls[0]


def test_docker_sandbox_runtime_timeout_blocks(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="partial")

    monkeypatch.setattr(docker_sandbox.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(docker_sandbox.subprocess, "run", fake_run)
    receipt = write_docker_sandbox_receipt(
        image=PINNED_IMAGE,
        command=["sh", "-c", "sleep 10"],
        receipt_path=tmp_path / "receipt.json",
        execute=True,
        timeout_seconds=1,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["command_executed"] is True
    assert "docker_command_timeout" in receipt["alert_codes"]


def test_cli_docker_sandbox_check_writes_blocked_receipt(tmp_path: Path) -> None:
    receipt_path = tmp_path / "docker-sandbox-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "docker-sandbox-check",
            "--image",
            "python:3.12",
            "--receipt",
            str(receipt_path),
            "--command",
            "python",
            "--version",
        ],
    )
    payload = json.loads(result.output)
    written = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert payload == written
    assert payload["schema"] == SANDBOX_RUN_RECEIPT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert "unpinned_image" in payload["alert_codes"]


def test_cli_docker_sandbox_run_sets_execute(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write(**options):
        captured.update(options)
        receipt = {
            "schema": SANDBOX_RUN_RECEIPT_SCHEMA,
            "ok": True,
            "status": "PASS",
            "command_executed": True,
        }
        options["receipt_path"].write_text(json.dumps(receipt), encoding="utf-8")
        return receipt

    monkeypatch.setattr("tau_coding.cli.write_docker_sandbox_receipt", fake_write)
    receipt_path = tmp_path / "receipt.json"
    result = CliRunner().invoke(
        app,
        [
            "docker-sandbox-run",
            "--image",
            PINNED_IMAGE,
            "--receipt",
            str(receipt_path),
            "--timeout",
            "9",
            "--command",
            "sh",
            "-c",
            "echo tau",
        ],
    )

    assert result.exit_code == 0
    assert captured["execute"] is True
    assert captured["timeout_seconds"] == 9
