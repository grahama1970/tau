#!/usr/bin/env python3
"""Prove Tau preserves ops-discord live failure details in repair receipts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--missing-channel", default="tau-missing-channel-for-failure-proof")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    out = _resolve_out(repo, args.out)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-discord-failure-path-"))
    )
    run_root.mkdir(parents=True, exist_ok=True)
    proof = run_root / "discord-failure-source.json"
    logs = run_root / "logs"
    logs.mkdir(exist_ok=True)
    uv = shutil.which(args.uv_bin) or args.uv_bin
    proc = subprocess.run(
        [
            uv,
            "run",
            "--project",
            str(repo),
            "python",
            str(repo / "scripts" / "agentic-eval-tau-discord-unblock.py"),
            "--repo",
            str(repo),
            "--out",
            str(proof),
            "--run-root",
            str(run_root / "discord-run"),
            "--discord-bot",
            "--channel-name",
            args.missing_channel,
            "--live-notify",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=args.timeout_seconds,
        check=False,
    )
    (logs / "source.stdout.json").write_text(proc.stdout, encoding="utf-8")
    (logs / "source.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    source = _read_json(proof)
    notification = (
        source.get("ops_discord_notification_receipt")
        if isinstance(source.get("ops_discord_notification_receipt"), dict)
        else {}
    )
    discord_state = (
        source.get("discord_question_state")
        if isinstance(source.get("discord_question_state"), dict)
        else {}
    )
    errors: list[str] = []
    if proc.returncode == 0:
        errors.append("failure_probe_unexpectedly_succeeded")
    if notification.get("schema") != "ops_discord.notification_receipt.v1":
        errors.append("notification_receipt_missing")
    if notification.get("status") in {"SENT", "DRY_RUN", "DEDUPED"}:
        errors.append("notification_status_not_failure")
    if notification.get("ok") is not False:
        errors.append("notification_ok_not_false")
    if not notification.get("status") and not notification.get("error_code"):
        errors.append("failure_code_missing")
    if discord_state.get("ops_discord_notification_ok") is not False:
        errors.append("projection_did_not_expose_notification_failure")
    receipt = {
        "schema": "tau.discord_failure_path_agentic_eval_proof.v1",
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "source_exit_code": proc.returncode,
        "missing_channel": args.missing_channel,
        "notification_status": notification.get("status"),
        "notification_error_code": notification.get("error_code"),
        "notification_http_status": notification.get("last_http_status")
        or notification.get("http_status"),
        "notification_receipt": notification,
        "discord_question_state": discord_state,
        "errors": errors,
        "proof_boundary": (
            "Live Discord bot failure path with an intentionally missing channel; proves failure "
            "details are preserved, not that delivery succeeded."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


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
