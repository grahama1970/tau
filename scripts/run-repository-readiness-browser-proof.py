#!/usr/bin/env python3
"""Prove the live positive and dirty-repository workflow viewer paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag
from tau_coding.workflows.catalog import get_workflow
from tau_coding.workflows.materialize import materialize_repository_readiness

HUMAN_GOAL = "Determine whether this checkout is ready for focused work."


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _git_fixture(path: Path, *, dirty: bool) -> None:
    path.mkdir(parents=True)
    _run(["git", "init", "--initial-branch=main"], cwd=path)
    _run(["git", "config", "user.name", "Tau Proof"], cwd=path)
    _run(["git", "config", "user.email", "tau-proof@example.invalid"], cwd=path)
    (path / "README.md").write_text("# Repository readiness fixture\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=path)
    _run(["git", "commit", "-m", "fixture"], cwd=path)
    if dirty:
        (path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")


def _wait_for_server(run_dir: Path, worker: threading.Thread) -> Any:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while worker.is_alive() and time.monotonic() < deadline:
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError, sqlite3.OperationalError) as exc:
            last_error = exc
            time.sleep(0.02)
    raise RuntimeError(f"repository_readiness_viewer_unavailable:{last_error}")


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _result_artifacts(run_dir: Path) -> list[dict[str, object]]:
    artifacts = []
    for name, kind in (
        ("repository-readiness.json", "repository_readiness_json"),
        ("repository-readiness.md", "repository_readiness_markdown"),
    ):
        path = run_dir / "results" / name
        if path.is_file():
            artifacts.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return artifacts


def _proof_scenario(
    *,
    repo_root: Path,
    fixture: Path,
    run_dir: Path,
    scenario: str,
    output: Path,
    desktop_screenshot: Path,
    mobile_screenshot: Path,
    node_root: str,
    source_ref: str,
) -> dict[str, object]:
    materialized = materialize_repository_readiness(
        definition=get_workflow("repository-readiness"),
        repo_path=fixture,
        human_goal=HUMAN_GOAL,
        require_clean=True,
        run_dir=run_dir,
        step_delay_seconds=0.6,
    )
    result: dict[str, object] = {}
    failure: BaseException | None = None
    handshake_dir = run_dir.parent / f".{scenario}-browser-handshake"
    handshake_dir.mkdir(parents=True, exist_ok=False)
    url_path = handshake_dir / "viewer-url.txt"
    ready_path = handshake_dir / "browser-ready.txt"
    env = {**os.environ, "NODE_PATH": node_root}
    browser = subprocess.Popen(
        [
            "node",
            "scripts/repository-readiness-browser-proof.mjs",
            str(url_path),
            str(ready_path),
            scenario,
            str(desktop_screenshot),
            str(mobile_screenshot),
            str(output),
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    ready_deadline = time.monotonic() + 15
    while not ready_path.is_file() and browser.poll() is None and time.monotonic() < ready_deadline:
        time.sleep(0.01)
    if not ready_path.is_file():
        browser_stdout, browser_stderr = browser.communicate(timeout=5)
        raise RuntimeError(
            f"repository_readiness_browser_prewarm_failed:{browser_stderr}\n{browser_stdout}"
        )

    def execute() -> None:
        nonlocal result, failure
        try:
            result = run_generic_dag(spec_path=materialized.source_dag_path, resume=False)
        except BaseException as exc:  # Preserve worker failure across the thread boundary.
            failure = exc

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    server = _wait_for_server(materialized.run_dir, worker)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url_path.write_text(server.url + "\n", encoding="utf-8")
    try:
        browser_stdout, browser_stderr = browser.communicate(timeout=45)
        worker.join(timeout=45)
    finally:
        if browser.poll() is None:
            browser.terminate()
            browser.wait(timeout=5)
        server.shutdown()
        server_thread.join(timeout=2)
    if worker.is_alive():
        raise RuntimeError(f"repository_readiness_workflow_timeout:{scenario}")
    if failure is not None:
        raise RuntimeError(f"repository_readiness_workflow_failed:{scenario}:{failure}")
    if browser.returncode != 0:
        raise RuntimeError(
            f"repository_readiness_browser_failed:{scenario}:{browser_stderr}\n{browser_stdout}"
        )

    receipt = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise RuntimeError("repository_readiness_browser_receipt_invalid")
    artifacts = _result_artifacts(run_dir)
    expected_artifact_count = 2 if scenario == "positive" else 0
    if len(artifacts) != expected_artifact_count:
        raise RuntimeError(f"repository_readiness_result_artifact_count:{scenario}")
    receipt.update(
        {
            "fixture_repo": str(fixture),
            "run_dir": str(run_dir),
            "source_dag_path": str(materialized.source_dag_path),
            "source_ref": source_ref,
            "workflow_status": result.get("status"),
            "workflow_verdict": result.get("verdict"),
            "result_artifacts": artifacts,
        }
    )
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positive-out", type=Path, required=True)
    parser.add_argument("--positive-desktop-screenshot", type=Path, required=True)
    parser.add_argument("--positive-mobile-screenshot", type=Path, required=True)
    parser.add_argument("--negative-out", type=Path, required=True)
    parser.add_argument("--negative-desktop-screenshot", type=Path, required=True)
    parser.add_argument("--negative-mobile-screenshot", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    outputs = [
        args.positive_out.expanduser().resolve(),
        args.positive_desktop_screenshot.expanduser().resolve(),
        args.positive_mobile_screenshot.expanduser().resolve(),
        args.negative_out.expanduser().resolve(),
        args.negative_desktop_screenshot.expanduser().resolve(),
        args.negative_mobile_screenshot.expanduser().resolve(),
    ]
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    node_root = _run(["npm", "root", "-g"], cwd=repo_root).stdout.strip()
    source_ref = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="tau-repository-readiness-proof-") as temporary:
        root = Path(temporary)
        positive_fixture = root / "positive-repo"
        negative_fixture = root / "negative-repo"
        _git_fixture(positive_fixture, dirty=False)
        _git_fixture(negative_fixture, dirty=True)
        positive = _proof_scenario(
            repo_root=repo_root,
            fixture=positive_fixture,
            run_dir=root / "positive-run",
            scenario="positive",
            output=outputs[0],
            desktop_screenshot=outputs[1],
            mobile_screenshot=outputs[2],
            node_root=node_root,
            source_ref=source_ref,
        )
        negative = _proof_scenario(
            repo_root=repo_root,
            fixture=negative_fixture,
            run_dir=root / "negative-run",
            scenario="negative",
            output=outputs[3],
            desktop_screenshot=outputs[4],
            mobile_screenshot=outputs[5],
            node_root=node_root,
            source_ref=source_ref,
        )
    summary = {
        "schema": "tau.repository_readiness_browser_proof_summary.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "positive_receipt": str(outputs[0]),
        "positive_desktop_screenshot": str(outputs[1]),
        "positive_desktop_screenshot_sha256": positive["desktop_screenshot_sha256"],
        "positive_mobile_screenshot": str(outputs[2]),
        "positive_mobile_screenshot_sha256": positive["mobile_screenshot_sha256"],
        "negative_receipt": str(outputs[3]),
        "negative_desktop_screenshot": str(outputs[4]),
        "negative_desktop_screenshot_sha256": negative["desktop_screenshot_sha256"],
        "negative_mobile_screenshot": str(outputs[5]),
        "negative_mobile_screenshot_sha256": negative["mobile_screenshot_sha256"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
