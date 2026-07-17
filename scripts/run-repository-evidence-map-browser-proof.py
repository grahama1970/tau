#!/usr/bin/env python3
"""Run positive and negative live browser proofs for repository-evidence-map."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag
from tau_coding.workflows.catalog import get_workflow
from tau_coding.workflows.materialize import materialize_repository_evidence_map

GOAL = "Map this repository for focused work."


def main() -> int:
    parser = argparse.ArgumentParser()
    for scenario in ("positive", "negative"):
        parser.add_argument(f"--{scenario}-out", type=Path, required=True)
        parser.add_argument(f"--{scenario}-desktop-screenshot", type=Path, required=True)
        parser.add_argument(f"--{scenario}-mobile-screenshot", type=Path, required=True)
    args = parser.parse_args()
    node_root = _node_root()
    with tempfile.TemporaryDirectory(prefix="tau-evidence-map-browser-") as temporary:
        root = Path(temporary)
        positive = _scenario(
            root / "positive-repo",
            root / "positive-run",
            True,
            "positive",
            args.positive_out.resolve(),
            args.positive_desktop_screenshot.resolve(),
            args.positive_mobile_screenshot.resolve(),
            node_root,
        )
        negative = _scenario(
            root / "negative-repo",
            root / "negative-run",
            False,
            "negative",
            args.negative_out.resolve(),
            args.negative_desktop_screenshot.resolve(),
            args.negative_mobile_screenshot.resolve(),
            node_root,
        )
    summary = {
        "schema": "tau.repository_evidence_map_browser_proof_summary.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "positive_receipt": str(args.positive_out.resolve()),
        "negative_receipt": str(args.negative_out.resolve()),
        "positive_checks": positive["checks"],
        "negative_checks": negative["checks"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _scenario(
    repo: Path,
    run_dir: Path,
    with_tests: bool,
    scenario: str,
    output: Path,
    desktop: Path,
    mobile: Path,
    node_root: str,
) -> dict[str, Any]:
    _git_repo(repo, with_tests=with_tests)
    materialized = materialize_repository_evidence_map(
        definition=get_workflow("repository-evidence-map"),
        repo_path=repo,
        human_goal=GOAL,
        require_tests=True,
        run_dir=run_dir,
        step_delay_seconds=0.8,
    )
    handshake = run_dir.parent / f".{scenario}-handshake"
    handshake.mkdir()
    url_path = handshake / "url"
    ready_path = handshake / "ready"
    output.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    mobile.parent.mkdir(parents=True, exist_ok=True)
    browser = subprocess.Popen(
        [
            "node",
            "scripts/repository-evidence-map-browser-proof.mjs",
            str(url_path),
            str(ready_path),
            scenario,
            str(desktop),
            str(mobile),
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "NODE_PATH": node_root},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_file(ready_path, browser, 15)
    outcome: dict[str, Any] = {}
    failures: list[BaseException] = []

    def run() -> None:
        try:
            outcome.update(run_generic_dag(spec_path=materialized.source_dag_path))
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    server = _wait_server(run_dir, worker, failures)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url_path.write_text(server.url + "\n", encoding="utf-8")
    worker.join(timeout=40)
    stdout, stderr = browser.communicate(timeout=40)
    server.shutdown()
    server_thread.join(timeout=5)
    if worker.is_alive() or failures:
        raise RuntimeError(f"workflow_failed:{failures}")
    if browser.returncode:
        raise RuntimeError(f"browser_failed:{scenario}:{stderr}\n{stdout}")
    receipt = _json(output)
    if receipt.get("status") != "PASS":
        raise RuntimeError(f"browser_receipt_blocked:{scenario}")
    if scenario == "positive":
        if outcome.get("ok") is not True or outcome.get("max_observed_concurrency") != 3:
            raise RuntimeError("positive_concurrency_not_proven")
        if not (run_dir / "results" / "repository-evidence-map.json").is_file():
            raise RuntimeError("positive_result_missing")
    else:
        tests_receipt = _json(run_dir / "receipts" / "analyze-tests.json")
        if tests_receipt.get("errors") != ["test_surface_missing"]:
            raise RuntimeError("negative_blocker_mismatch")
        if (run_dir / "receipts" / "publish-evidence-map.json").exists():
            raise RuntimeError("negative_publish_dispatched")
        if (run_dir / "results").exists():
            raise RuntimeError("negative_results_exist")
    return receipt


def _git_repo(path: Path, *, with_tests: bool) -> None:
    path.mkdir()
    (path / "README.md").write_text("# Browser Fixture\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname = "browser-fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    if with_tests:
        (path / "tests").mkdir()
        (path / "tests" / "test_fixture.py").write_text(
            "def test_fixture():\n    assert True\n", encoding="utf-8"
        )
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
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


def _wait_file(path: Path, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"browser_exited_early:{stderr}\n{stdout}")
        time.sleep(0.02)
    raise RuntimeError("browser_handshake_timeout")


def _wait_server(run_dir: Path, worker: threading.Thread, failures: list[BaseException]) -> Any:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if failures:
            raise RuntimeError(f"workflow_failed:{failures[0]}")
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if not worker.is_alive():
                break
            time.sleep(0.03)
    raise RuntimeError(f"viewer_unavailable:{last_error}")


def _node_root() -> str:
    result = subprocess.run(
        ["npm", "root", "-g"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object expected: {path}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
