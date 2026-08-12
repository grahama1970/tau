#!/usr/bin/env python3
"""Run the live DAG smoke while Chromium verifies the rendered application."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _node_path() -> str:
    candidates = [
        os.environ.get("NODE_PATH"),
        subprocess.run(
            ["npm", "root", "-g"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "/home/graham/.npm/_npx/0f94ee7615faf582/node_modules",
    ]
    for candidate in candidates:
        if candidate and (
            (Path(candidate) / "puppeteer").is_dir()
            or (Path(candidate) / "puppeteer-core").is_dir()
        ):
            return candidate
    raise RuntimeError("dag_viewer_browser_proof_missing_puppeteer")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--step-delay-seconds", type=float, default=0.65)
    args = parser.parse_args()
    output = args.out.expanduser().resolve()
    screenshot = args.screenshot.expanduser().resolve()
    run_root = (args.run_root or output.with_suffix("")).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tau-dag-browser-proof-") as temporary:
        url_path = Path(temporary) / "viewer-url.txt"
        smoke_receipt = Path(temporary) / "smoke.json"
        smoke = subprocess.Popen(
            [
                sys.executable,
                "scripts/run-dag-viewer-live-smoke.py",
                "--run-root",
                str(run_root),
                "--out",
                str(smoke_receipt),
                "--step-delay-seconds",
                str(args.step_delay_seconds),
                "--viewer-url-out",
                str(url_path),
                "--serve-after-seconds",
                "45",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 15
        while not url_path.is_file() and smoke.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not url_path.is_file():
            stderr = smoke.stderr.read() if smoke.stderr else ""
            smoke.terminate()
            raise RuntimeError(f"dag_viewer_browser_server_unavailable:{stderr}")
        url = url_path.read_text(encoding="utf-8").strip()
        env = dict(os.environ)
        env["NODE_PATH"] = _node_path()
        env.setdefault("CHROME_BIN", "/snap/bin/chromium")
        browser = subprocess.run(
            [
                "node",
                "scripts/dag-viewer-browser-proof.mjs",
                url,
                str(screenshot),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        try:
            smoke_stdout, smoke_stderr = smoke.communicate(timeout=70)
        except subprocess.TimeoutExpired:
            smoke.terminate()
            smoke_stdout, smoke_stderr = smoke.communicate(timeout=5)
        if smoke.returncode != 0:
            raise RuntimeError(
                f"dag_viewer_live_smoke_failed:{smoke_stderr or smoke_stdout}"
            )
        if browser.returncode != 0:
            raise RuntimeError(
                f"dag_viewer_browser_proof_failed:{browser.stderr or browser.stdout}"
            )
        print(browser.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
