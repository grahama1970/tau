from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

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
