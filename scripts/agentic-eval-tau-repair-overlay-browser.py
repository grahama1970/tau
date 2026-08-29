#!/usr/bin/env python3
"""Live browser proof that Tau repair overlays expose Discord adjudication state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--node-bin", default="node")
    parser.add_argument("--channel-name", default="horus")
    parser.add_argument("--live-notify", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    out = _resolve_out(repo, args.out)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-repair-overlay-browser-"))
    )
    run_root.mkdir(parents=True, exist_ok=True)
    proof_json = run_root / "discord-proof.json"
    browser_json = run_root / "browser-proof.json"
    screenshot = run_root / "repair-overlay.png"
    logs = run_root / "logs"
    logs.mkdir(exist_ok=True)

    uv = shutil.which(args.uv_bin) or args.uv_bin
    discord_cmd = [
        uv,
        "run",
        "--project",
        str(repo),
        "python",
        str(repo / "scripts" / "agentic-eval-tau-discord-unblock.py"),
        "--repo",
        str(repo),
        "--out",
        str(proof_json),
        "--run-root",
        str(run_root / "discord-run"),
        "--discord-bot",
        "--channel-name",
        args.channel_name,
    ]
    if args.live_notify:
        discord_cmd.append("--live-notify")
    discord = _run(
        discord_cmd,
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs / "discord.stdout.json",
        stderr_path=logs / "discord.stderr.txt",
    )
    discord_payload = _read_json(proof_json)
    receipt_dir = Path(str(discord_payload.get("run_root") or "")) / "run"

    tau = [uv, "run", "--project", str(repo), "tau"]
    server = subprocess.Popen(
        [
            *tau,
            "dag-view-serve",
            "--run-dir",
            str(receipt_dir),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--no-open",
        ],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        server_receipt = _read_json_from_stream(server, timeout=20)
        url = str(server_receipt["url"])
        env = os.environ.copy()
        env.setdefault("NODE_PATH", _node_path(args.node_bin, repo))
        browser = _run(
            [
                shutil.which(args.node_bin) or args.node_bin,
                str(repo / "scripts" / "repair-overlay-browser-proof.mjs"),
                url,
                str(screenshot),
                str(browser_json),
            ],
            cwd=repo,
            timeout=args.timeout_seconds,
            stdout_path=logs / "browser.stdout.json",
            stderr_path=logs / "browser.stderr.txt",
            env=env,
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    browser_payload = _read_json(browser_json)
    errors: list[str] = []
    if discord["exit_code"] != 0 or discord_payload.get("status") != "PASS":
        errors.append("discord_repair_eval_failed")
    if args.live_notify and not discord_payload.get("ops_discord_notification_receipt", {}).get(
        "message_url"
    ):
        errors.append("live_discord_message_url_missing")
    if browser["exit_code"] != 0 or browser_payload.get("status") != "PASS":
        errors.append("browser_overlay_proof_failed")
    receipt = {
        "schema": "tau.repair_overlay_browser_agentic_eval_proof.v1",
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "receipt_dir": str(receipt_dir),
        "discord_proof": discord_payload,
        "browser_proof": browser_payload,
        "screenshot": str(screenshot),
        "errors": errors,
        "proof_boundary": (
            "Live local Tau viewer rendered in headless Chrome; Discord delivery is live only "
            "when --live-notify is set."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    stdout_path: Path,
    stderr_path: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    proc = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "command": command,
    }


def _read_json_from_stream(process: subprocess.Popen[str], *, timeout: int) -> dict[str, Any]:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line:
            chunks.append(line)
            text = "".join(chunks)
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        if process.poll() is not None:
            break
        time.sleep(0.05)
    stderr = process.stderr.read() if process.stderr is not None else ""
    raise RuntimeError(f"dag viewer server did not print a JSON receipt: {stderr[-1000:]}")


def _node_path(node_bin: str, cwd: Path) -> str:
    proc = subprocess.run(
        [shutil.which("npm") or "npm", "root", "-g"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _resolve_out(repo: Path, out: Path) -> Path:
    expanded = out.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (repo / expanded).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
