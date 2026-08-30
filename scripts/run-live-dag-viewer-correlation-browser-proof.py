#!/usr/bin/env python3
"""Prove live React Flow DAG transitions correlate to Tau event and ledger IDs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.run_ledger import build_run_ledger_from_run_dir, verify_ledger


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object expected: {path}")
    return payload


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _node_path() -> str:
    candidates = [
        os.environ.get("NODE_PATH"),
        subprocess.run(["npm", "root", "-g"], check=True, capture_output=True, text=True).stdout.strip(),
        "/home/graham/.npm/_npx/0f94ee7615faf582/node_modules",
    ]
    for candidate in candidates:
        if candidate and (
            (Path(candidate) / "puppeteer").is_dir()
            or (Path(candidate) / "puppeteer-core").is_dir()
        ):
            return candidate
    raise RuntimeError("live_dag_viewer_correlation_missing_puppeteer")


def _chrome_bin() -> str:
    for candidate in (
        os.environ.get("CHROME_BIN"),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return "/usr/bin/google-chrome"


def _wait_file(path: Path, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"process_exited_before_{path.name}:{stderr}\n{stdout}")
        time.sleep(0.05)
    raise RuntimeError(f"proof_handshake_timeout:{path}")


def _wait_server(run_dir: Path, process: subprocess.Popen[str]) -> Any:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while process.poll() is None and time.monotonic() < deadline:
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError, sqlite3.OperationalError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"live_correlation_viewer_unavailable:{last_error}")


def _write_progress_artifact(run_dir: Path) -> Path:
    current_state = _json(run_dir / "current-state.json")
    events = _jsonl(run_dir / "events.jsonl")
    progress = {
        "schema": "tau.dag_progress.v1",
        "source": "tau.generic_dag_current_state",
        "run_id": current_state.get("run_id"),
        "status": current_state.get("status"),
        "verdict": current_state.get("verdict"),
        "completed_nodes": current_state.get("completed_nodes"),
        "blocked_nodes": current_state.get("blocked_nodes"),
        "ready_nodes": current_state.get("ready_nodes"),
        "event_count": len(events),
        "last_event": events[-1] if events else None,
        "current_state_path": str(run_dir / "current-state.json"),
        "events_jsonl": str(run_dir / "events.jsonl"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "proof_scope": {
            "proves": [
                "This generic DAG run has a retained progress snapshot for the viewer correlation proof.",
                "The progress snapshot is derived from current-state.json and events.jsonl.",
            ],
            "does_not_prove": [
                "Provider or model semantic quality.",
                "A project_dag-specific progress writer ran for this generic DAG.",
            ],
        },
    }
    progress_path = run_dir / "dag-progress.json"
    progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path = run_dir / "run-receipt.json"
    receipt = _json(receipt_path)
    receipt["progress_path"] = str(progress_path)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return progress_path


def _copy_source_artifacts(run_dir: Path, output_dir: Path) -> dict[str, str]:
    source_dir = output_dir / "source-artifacts"
    source_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}
    for name in (
        "events.jsonl",
        "dag-progress.json",
        "run-ledger.json",
        "source-dag.json",
        "run-receipt.json",
        "current-state.json",
    ):
        source = run_dir / name
        if not source.is_file():
            continue
        target = source_dir / name
        shutil.copy2(source, target)
        artifacts[name] = str(target)
    return artifacts


def _stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--step-delay-seconds", type=float, default=1.2)
    args = parser.parse_args()
    if args.step_delay_seconds <= 0:
        raise SystemExit("--step-delay-seconds must be positive")

    repo_root = Path(__file__).resolve().parents[1]
    base_output_dir = args.output_dir.expanduser().resolve()
    output_dir = base_output_dir / f"proof-{_stamp()}-{os.getpid()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root is not None
        else output_dir / f"run-root-{_stamp()}-{os.getpid()}"
    )
    if run_root.exists():
        raise RuntimeError(f"proof run root already exists: {run_root}")
    run_dir = run_root / "run"
    handshake = output_dir / "handshake"
    handshake.mkdir(parents=True, exist_ok=True)
    ready_path = handshake / "browser-ready"
    blocked_seen_path = handshake / "blocked-seen"
    final_seen_path = handshake / "final-seen"
    ledger_ready_path = handshake / "ledger-ready"

    base = [
        sys.executable,
        os.fspath(repo_root / "examples" / "canonical-dags" / "run.py"),
        "--dag",
        "5",
        "--run-root",
        os.fspath(run_root),
        "--step-delay-seconds",
        str(args.step_delay_seconds),
        "--approve",
    ]
    first = subprocess.Popen(
        base,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    server = _wait_server(run_dir, first)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = subprocess.Popen(
        [
            "node",
            "scripts/live-dag-viewer-correlation-browser-proof.mjs",
            server.url,
            os.fspath(ready_path),
            os.fspath(blocked_seen_path),
            os.fspath(final_seen_path),
            os.fspath(ledger_ready_path),
            os.fspath(output_dir),
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "NODE_PATH": _node_path(), "CHROME_BIN": _chrome_bin()},
    )
    try:
        _wait_file(ready_path, browser, 20)
        first_stdout, first_stderr = first.communicate(timeout=60)
        if first.returncode != 2:
            raise RuntimeError(f"initial_dag_did_not_block:{first.returncode}:{first_stderr}\n{first_stdout}")
        first_result = json.loads(first_stdout)
        if first_result.get("status") != "BLOCKED" or first_result.get("completed_node_count") != 4:
            raise RuntimeError("initial_block_boundary_not_observed")
        _wait_file(blocked_seen_path, browser, 20)
        blocked_marker = blocked_seen_path.read_text(encoding="utf-8").strip()
        if blocked_marker != "blocked":
            raise RuntimeError("browser_did_not_observe_blocked_state")
        resumed = subprocess.run(
            [*base, "--repair", "--resume"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=70,
        )
        if resumed.returncode != 0:
            raise RuntimeError(f"resume_failed:{resumed.stderr}\n{resumed.stdout}")
        resumed_result = json.loads(resumed.stdout)
        if resumed_result.get("status") != "PASS" or resumed_result.get("resumed_node_count") != 4:
            raise RuntimeError("resume_did_not_preserve_and_complete_expected_nodes")
        _wait_file(final_seen_path, browser, 30)
        _write_progress_artifact(run_dir)
        ledger_path = run_dir / "run-ledger.json"
        ledger = build_run_ledger_from_run_dir(run_dir, output_path=ledger_path)
        verification = verify_ledger(ledger)
        if verification.get("ok") is not True:
            raise RuntimeError(f"ledger_verification_failed:{verification}")
        ledger_ready_path.write_text("ready\n", encoding="utf-8")
        browser_stdout, browser_stderr = browser.communicate(timeout=45)
        if browser.returncode != 0:
            raise RuntimeError(f"browser_correlation_failed:{browser_stderr}\n{browser_stdout}")
        proof_path = output_dir / "browser-proof.json"
        proof = _json(proof_path)
        source_artifacts = _copy_source_artifacts(run_dir, output_dir)
        proof["source_artifacts"] = source_artifacts
        proof["proof_dir"] = str(output_dir)
        proof["run_root"] = str(run_root)
        proof["run_dir"] = str(run_dir)
        proof["proof_scope"] = {
            "proves": [
                "Live React Flow DOM transitions were observed while canonical DAG 5 ran, blocked, resumed, and completed.",
                "Every recorded visible transition is correlated to Tau event sequences and run-ledger entry hashes.",
                "The viewer requests made by the browser proof were GET-only.",
            ],
            "does_not_prove": [
                "Provider or model semantic quality.",
                "Production deployment readiness.",
                "A human independently accepted the final Tau product goal.",
            ],
        }
        proof_path.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        latest_path = base_output_dir / "latest-proof.json"
        latest_path.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(proof, indent=2, sort_keys=True))
        return 0 if proof.get("status") == "PASS" else 1
    finally:
        if browser.poll() is None:
            browser.terminate()
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait(timeout=5)
        if first.poll() is None:
            first.terminate()
            try:
                first.wait(timeout=5)
            except subprocess.TimeoutExpired:
                first.kill()
                first.wait(timeout=5)
        server.shutdown()
        server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
