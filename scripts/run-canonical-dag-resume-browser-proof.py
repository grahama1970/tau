#!/usr/bin/env python3
"""Prove DAG 5 blocking, durable resume, and dynamic React Flow updates."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server


def _wait_for_server(run_dir: Path, process: subprocess.Popen[str]) -> Any:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while process.poll() is None and time.monotonic() < deadline:
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"canonical_resume_viewer_unavailable:{last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--desktop-screenshot", type=Path, required=True)
    parser.add_argument("--mobile-screenshot", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    run_root = args.run_root.expanduser().resolve()
    if run_root.exists():
        raise RuntimeError(f"proof run root already exists: {run_root}")
    base = [
        sys.executable,
        os.fspath(repo_root / "examples" / "canonical-dags" / "run.py"),
        "--dag",
        "5",
        "--run-root",
        os.fspath(run_root),
        "--step-delay-seconds",
        "2",
        "--approve",
    ]
    first = subprocess.Popen(
        base,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    server = _wait_for_server(run_root / "run", first)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.port}/"
    node_root = subprocess.run(
        ["npm", "root", "-g"], check=True, capture_output=True, text=True
    ).stdout.strip()
    env = {**os.environ, "NODE_PATH": node_root}
    browser = subprocess.Popen(
        [
            "node",
            "scripts/canonical-dag-resume-browser-proof.mjs",
            url,
            os.fspath(args.desktop_screenshot.resolve()),
            os.fspath(args.mobile_screenshot.resolve()),
            os.fspath(args.out.resolve()),
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        first_stdout, first_stderr = first.communicate(timeout=45)
        if first.returncode != 2:
            raise RuntimeError(
                f"initial DAG 5 run did not block ({first.returncode}):{first_stderr}"
            )
        first_result = json.loads(first_stdout)
        if first_result.get("completed_node_count") != 4:
            raise RuntimeError("initial DAG 5 run blocked at the wrong boundary")
        time.sleep(2)
        resumed = subprocess.run(
            [*base, "--repair", "--resume"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if resumed.returncode != 0:
            raise RuntimeError(f"resumed DAG 5 run failed:{resumed.stderr}")
        resumed_result = json.loads(resumed.stdout)
        if resumed_result.get("resumed_node_count") != 4:
            raise RuntimeError("resumed DAG 5 run did not preserve accepted work")
        browser_stdout, browser_stderr = browser.communicate(timeout=75)
        if browser.returncode != 0:
            raise RuntimeError(f"browser proof failed:{browser_stderr}\n{browser_stdout}")
        print(browser_stdout, end="")
        return 0
    finally:
        if browser.poll() is None:
            browser.terminate()
            browser.wait(timeout=5)
        server.shutdown()
        server_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
