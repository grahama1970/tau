#!/usr/bin/env python3
"""Run the live Tau TUI voice stop/restart proof for issue #223.

This script intentionally exercises the real Chatterbox container boundary.
It stops the named container, proves Tau degrades without blocking, restarts
the same container, and proves a later fresh Tau turn succeeds.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.tui.voice import VoiceRunSnapshot, VoiceSurface


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8018")
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", default=180.0, type=float)
    parser.add_argument("--health-timeout-seconds", default=120.0, type=float)
    args = parser.parse_args()

    started_at = _now()
    surface = VoiceSurface(
        chatterbox_url=args.url,
        stt_url=None,
        timeout_seconds=args.timeout_seconds,
    )
    before_snapshot = VoiceRunSnapshot(
        workflow="Issue 223 restart proof",
        run_id=f"tau-223-restart-{started_at}",
        state="RUNNING",
        active_node="before-stop",
        attempt_id="attempt-before",
        scheduler_journal_sequence=1,
        state_digest="sha256:before-stop",
        state_transition="READY->RUNNING",
        goal_hash="sha256:issue-223",
    )
    down_snapshot = VoiceRunSnapshot(
        workflow="Issue 223 restart proof",
        run_id=before_snapshot.run_id,
        state="BLOCKED",
        active_node="service-down",
        blocker="chatterbox container stopped for restart proof",
        attempt_id="attempt-down",
        scheduler_journal_sequence=2,
        state_digest="sha256:service-down",
        state_transition="RUNNING->DEGRADED",
        goal_hash="sha256:issue-223",
    )
    after_snapshot = VoiceRunSnapshot(
        workflow="Issue 223 restart proof",
        run_id=before_snapshot.run_id,
        state="COMPLETED",
        active_node="after-restart",
        attempt_id="attempt-after",
        scheduler_journal_sequence=3,
        state_digest="sha256:after-restart",
        state_transition="DEGRADED->RUNNING",
        goal_hash="sha256:issue-223",
    )

    proof: dict[str, Any] = {
        "schema": "tau.issue_223.tui_voice_restart_proof.v1",
        "issue": "grahama1970/tau#223",
        "started_at_utc": started_at,
        "chatterbox_url": args.url,
        "container": args.container,
        "mocked": False,
        "live": True,
        "shared_service_stop_start": True,
        "status": "FAIL",
        "commands": [],
        "receipts": {},
        "checks": {},
        "claims": {
            "proves": [
                "Tau TUI voice renders through a live Chatterbox service before restart.",
                (
                    "When Chatterbox is stopped, Tau records an explicit degraded receipt "
                    "and does not block workflow progress."
                ),
                (
                    "After Chatterbox restarts, a later fresh Tau turn succeeds through "
                    "/tau/voice-render."
                ),
                (
                    "Tau does not replay the down-time turn after restart; only the fresh "
                    "post-restart turn is rendered."
                ),
            ],
            "does_not_prove": [
                "Perceptual quality of requested voice delivery profiles.",
                "Voice authentication or voice approval authority.",
                "Arbitrary host audio-device compatibility.",
            ],
        },
    }

    try:
        proof["health_before"] = _wait_for_health(args.url, args.health_timeout_seconds)
        proof["receipts"]["before_stop"] = surface.announce_state_change(
            before_snapshot,
            conversation_id="tau-issue-223-restart",
            turn_id="tau-223-before-stop",
        )

        proof["commands"].append(_run(["docker", "stop", args.container]))
        proof["health_while_stopped"] = _health(args.url)
        proof["receipts"]["while_stopped"] = surface.announce_state_change(
            down_snapshot,
            conversation_id="tau-issue-223-restart",
            turn_id="tau-223-while-stopped",
        )

        proof["commands"].append(_run(["docker", "start", args.container]))
        proof["health_after_restart"] = _wait_for_health(
            args.url,
            args.health_timeout_seconds,
        )
        proof["receipts"]["after_restart"] = surface.announce_state_change(
            after_snapshot,
            conversation_id="tau-issue-223-restart",
            turn_id="tau-223-after-restart",
        )
        proof["receipts"]["lineage"] = surface.turn_lineage_receipt()

        proof["checks"] = _checks(proof)
        proof["status"] = "PASS" if all(proof["checks"].values()) else "FAIL"
    finally:
        if not _container_running(args.container):
            proof.setdefault("commands", []).append(_run(["docker", "start", args.container]))
            proof["health_after_final_restore"] = _wait_for_health(
                args.url,
                args.health_timeout_seconds,
            )
        proof["finished_at_utc"] = _now()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")

    print(f"status {proof['status']}")
    for key, value in proof["checks"].items():
        print(f"{key} {value}")
    print(f"output {args.output}")
    return 0 if proof["status"] == "PASS" else 1


def _checks(proof: dict[str, Any]) -> dict[str, bool]:
    before = proof["receipts"].get("before_stop", {})
    down = proof["receipts"].get("while_stopped", {})
    after = proof["receipts"].get("after_restart", {})
    lineage = proof["receipts"].get("lineage", {})
    rendered_turn_ids = {
        item.get("turn_id")
        for item in lineage.get("turns", [])
        if item.get("status") == "PASS"
    }
    return {
        "health_before_live": _is_live_health(proof.get("health_before")),
        "before_render_live_pass": _is_live_render(before),
        "docker_stop_succeeded": _command_ok(proof, ["docker", "stop"]),
        "health_down_unavailable": proof.get("health_while_stopped", {}).get("ok") is False,
        "down_receipt_degraded": down.get("status") == "DEGRADED"
        and down.get("degraded_reasons") == ["voice_render_request_failed"],
        "docker_start_succeeded": _command_ok(proof, ["docker", "start"]),
        "health_after_restart_live": _is_live_health(proof.get("health_after_restart")),
        "after_render_live_pass": _is_live_render(after),
        "down_turn_not_rendered": "tau-223-while-stopped" not in rendered_turn_ids,
        "after_turn_rendered": "tau-223-after-restart" in rendered_turn_ids,
        "after_turn_did_not_reuse_old_turn_id": after.get("turn_id")
        == "tau-223-after-restart",
    }


def _is_live_health(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("live") is True
        and payload.get("mocked") is False
        and payload.get("model_loaded") is True
    )


def _is_live_render(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("status") == "PASS"
        and payload.get("service_ok") is True
        and payload.get("service_live") is True
        and payload.get("service_mocked") is False
        and bool(payload.get("audio_identity", {}).get("finished_response_audio"))
    )


def _command_ok(proof: dict[str, Any], prefix: list[str]) -> bool:
    for command in proof.get("commands", []):
        argv = command.get("argv", [])
        if argv[: len(prefix)] == prefix and command.get("returncode") == 0:
            return True
    return False


def _wait_for_health(url: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _health(url)
        if _is_live_health(last):
            return last
        time.sleep(2)
    return last


def _health(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=2) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return {"ok": False, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "health response is not a JSON object"}
    return payload


def _run(argv: list[str]) -> dict[str, Any]:
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _container_running(container: str) -> bool:
    completed = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    sys.exit(main())
