#!/usr/bin/env python3
"""Prove crash recovery, targeted repair, approval, and completion in one viewer."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag
from tau_coding.workflows.catalog import get_workflow
from tau_coding.workflows.materialize import materialize_durable_repository_qualification
from tau_coding.workflows.runner import (
    approve_packaged_workflow,
    repair_durable_repository_qualification,
    resume_packaged_workflow,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--desktop-screenshot", type=Path, required=True)
    parser.add_argument("--mobile-screenshot", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    source_ref = _git_source_ref(repo_root)
    output = args.output.resolve()
    desktop = args.desktop_screenshot.resolve()
    mobile = args.mobile_screenshot.resolve()
    for path in (output, desktop, mobile):
        path.parent.mkdir(parents=True, exist_ok=True)
    node_root = subprocess.run(
        ["npm", "root", "-g"], check=True, capture_output=True, text=True
    ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="tau-durable-qualification-browser-") as temporary:
        root = Path(temporary)
        repo = _git_repo(root / "repo")
        run_dir = root / "run"
        publish_path = root / "published"
        materialized = materialize_durable_repository_qualification(
            definition=get_workflow("durable-repository-qualification"),
            repo_path=repo,
            human_goal="Qualify this repository through durable recovery.",
            publish_path=publish_path,
            run_dir=run_dir,
            inject_test_branch_failure=True,
            step_delay_seconds=0.8,
        )
        handshake = root / "handshake"
        handshake.mkdir()
        url_path = handshake / "url"
        ready_path = handshake / "ready"
        repair_seen_path = handshake / "repair-seen"
        approval_seen_path = handshake / "approval-seen"
        browser = subprocess.Popen(
            [
                "node",
                "scripts/durable-qualification-browser-proof.mjs",
                str(url_path),
                str(ready_path),
                str(repair_seen_path),
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

        def crash(point: str, context: Mapping[str, Any]) -> None:
            if point == "after_result_staged" and context.get("node_id") == "qualify-tests":
                raise RuntimeError("diagnostic_injected_crash_after_staged:qualify-tests")

        _, first_error, first_thread = _worker(
            lambda: run_generic_dag(
                spec_path=materialized.source_dag_path,
                diagnostic_fault_injector=crash,
            )
        )
        server = _wait_server(run_dir, first_thread, first_error)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        url_path.write_text(server.url + "\n", encoding="utf-8")
        first_thread.join(timeout=20)
        if first_thread.is_alive() or not first_error:
            raise RuntimeError("diagnostic crash was not observed")
        if "diagnostic_injected_crash_after_staged" not in str(first_error[0]):
            raise RuntimeError(f"unexpected initial error: {first_error[0]}")

        recovered, recovery_error, recovery_thread = _worker(
            lambda: resume_packaged_workflow(run_dir=run_dir)
        )
        _wait_file(repair_seen_path, browser, 35)
        recovery_thread.join(timeout=5)
        if recovery_thread.is_alive() or recovery_error:
            raise RuntimeError(f"recovery failed: {recovery_error}")
        repair_durable_repository_qualification(
            run_dir=run_dir, node_id="qualify-tests"
        )
        repaired, repair_error, repair_thread = _worker(
            lambda: resume_packaged_workflow(run_dir=run_dir)
        )
        _wait_file(approval_seen_path, browser, 25)
        repair_thread.join(timeout=5)
        if repair_thread.is_alive() or repair_error:
            raise RuntimeError(f"targeted repair failed: {repair_error}")
        approve_packaged_workflow(run_dir=run_dir)
        final, final_error, final_thread = _worker(
            lambda: resume_packaged_workflow(run_dir=run_dir)
        )
        final_thread.join(timeout=30)
        stdout, stderr = browser.communicate(timeout=30)
        server.shutdown()
        server_thread.join(timeout=5)
        if final_thread.is_alive() or final_error:
            raise RuntimeError(f"final resume failed: {final_error}")
        if browser.returncode:
            raise RuntimeError(f"browser failed: {stderr}\n{stdout}")
        receipt = _json(output)
        receipt["source_ref"] = source_ref
        ledger = _json(publish_path / "publication-ledger.json")
        receipt["checks"]["publication_effect_count_one"] = ledger["effect_count"] == 1
        receipt["checks"]["journal_recovery_order"] = _journal_recovery_order(run_dir)
        receipt["status"] = (
            "PASS" if all(receipt["checks"].values()) else "BLOCKED"
        )
        output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if recovered.get("status") != "BLOCKED":
            raise RuntimeError("recovery did not settle at targeted repair")
        if repaired.get("status") != "BLOCKED":
            raise RuntimeError("repair did not settle at approval")
        if final.get("status") != "PASS" or receipt["status"] != "PASS":
            raise RuntimeError("durable qualification browser proof blocked")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


def _journal_recovery_order(run_dir: Path) -> bool:
    with sqlite3.connect(run_dir / "dag-run.sqlite3") as connection:
        events = connection.execute(
            """SELECT e.seq, e.event_type
               FROM dag_run_events e
               LEFT JOIN dag_node_attempts a ON a.attempt_id = e.attempt_id
               WHERE e.event_type = 'run_lease_taken_over' OR a.node_id = 'qualify-tests'
               ORDER BY e.seq"""
        ).fetchall()
    staged = next(seq for seq, event in events if event == "attempt_result_staged")
    takeover = next(seq for seq, event in events if event == "run_lease_taken_over")
    validated = next(seq for seq, event in events if event == "attempt_result_validated")
    return bool(staged < takeover < validated)


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
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if errors or not worker.is_alive():
                break
            time.sleep(0.03)
    raise RuntimeError(f"viewer unavailable: {last_error}")


def _wait_file(path: Path, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"browser exited early: {stderr}\n{stdout}")
        time.sleep(0.02)
    raise RuntimeError(f"browser handshake timeout: {path.name}")


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# Durable qualification fixture\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_fixture.py").write_text(
        "def test_fixture():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
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


def _git_source_ref(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
