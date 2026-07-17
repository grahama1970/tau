#!/usr/bin/env python3
"""Prove approval, resume, and publication in one live Tau viewer session."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag
from tau_coding.workflows.catalog import get_workflow
from tau_coding.workflows.materialize import materialize_approved_release_bundle
from tau_coding.workflows.runner import approve_approved_release_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--desktop-screenshot", type=Path, required=True)
    parser.add_argument("--mobile-screenshot", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    desktop = args.desktop_screenshot.resolve()
    mobile = args.mobile_screenshot.resolve()
    for path in (output, desktop, mobile):
        path.parent.mkdir(parents=True, exist_ok=True)
    node_root = subprocess.run(
        ["npm", "root", "-g"], check=True, capture_output=True, text=True
    ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="tau-approved-release-browser-") as temporary:
        root = Path(temporary)
        repo = _git_repo(root / "repo")
        run_dir = root / "run"
        publish_path = root / "published"
        materialized = materialize_approved_release_bundle(
            definition=get_workflow("approved-release-bundle"),
            repo_path=repo,
            human_goal="Publish an approved release bundle.",
            publish_path=publish_path,
            run_dir=run_dir,
            step_delay_seconds=0.8,
        )
        handshake = root / "handshake"
        handshake.mkdir()
        url_path = handshake / "url"
        ready_path = handshake / "ready"
        approval_seen_path = handshake / "approval-seen"
        browser = subprocess.Popen(
            [
                "node",
                "scripts/approved-release-browser-proof.mjs",
                str(url_path),
                str(ready_path),
                str(approval_seen_path),
                str(desktop),
                str(mobile),
                str(output),
            ],
            cwd=repo_root,
            env={**os.environ, "NODE_PATH": node_root},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_file(ready_path, browser, 15)
        first, first_error, first_thread = _worker(
            lambda: run_generic_dag(spec_path=materialized.source_dag_path)
        )
        server = _wait_server(run_dir, first_thread, first_error)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        url_path.write_text(server.url + "\n", encoding="utf-8")
        first_thread.join(timeout=45)
        if first_thread.is_alive() or first_error:
            raise RuntimeError(f"initial_workflow_failed:{first_error}")
        _wait_file(approval_seen_path, browser, 20)
        approve_approved_release_bundle(run_dir=run_dir)
        resumed, resume_error, resume_thread = _worker(
            lambda: run_generic_dag(spec_path=materialized.source_dag_path, resume=True)
        )
        resume_thread.join(timeout=45)
        stdout, stderr = browser.communicate(timeout=45)
        server.shutdown()
        server_thread.join(timeout=5)
        if resume_thread.is_alive() or resume_error:
            raise RuntimeError(f"resume_failed:{resume_error}")
        if browser.returncode:
            raise RuntimeError(f"browser_failed:{stderr}\n{stdout}")
        receipt = _json(output)
        if first.get("verdict") != "APPROVAL_REQUIRED":
            raise RuntimeError("initial_approval_boundary_not_proven")
        if resumed.get("ok") is not True or receipt.get("status") != "PASS":
            raise RuntimeError("approved_release_browser_proof_blocked")
        if not (publish_path / "approved-release-bundle.json").is_file():
            raise RuntimeError("approved_publication_missing")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


def _worker(
    operation: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], list[BaseException], threading.Thread]:
    outcome: dict[str, Any] = {}
    errors: list[BaseException] = []

    def run() -> None:
        try:
            outcome.update(operation())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return outcome, errors, thread


def _wait_server(
    run_dir: Path, worker: threading.Thread, errors: list[BaseException]
) -> Any:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if errors:
            raise RuntimeError(f"workflow_failed:{errors[0]}")
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if not worker.is_alive():
                break
            time.sleep(0.03)
    raise RuntimeError(f"viewer_unavailable:{last_error}")


def _wait_file(path: Path, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"browser_exited_early:{stderr}\n{stdout}")
        time.sleep(0.02)
    raise RuntimeError(f"browser_handshake_timeout:{path.name}")


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# Release browser fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Tau",
            "-c",
            "user.email=tau@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return path


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object expected: {path}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
