#!/usr/bin/env python3
"""Prove live positive and missing-workflow operator-reference viewer paths."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag
from tau_coding.workflows import materialize as workflow_materialize
from tau_coding.workflows.catalog import get_workflow

WORKFLOW_ID = "tau-operator-reference"
POSITIVE_REQUIRED_WORKFLOW = "repository-readiness"
NEGATIVE_REQUIRED_WORKFLOW = "deliberately-absent-workflow"
NODE_IDS = (
    "collect-operator-sources",
    "capture-operator-cli",
    "compose-operator-reference",
    "validate-operator-reference",
)


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _materializer() -> Callable[..., Any]:
    for name in ("materialize_operator_reference", "materialize_tau_operator_reference"):
        candidate = getattr(workflow_materialize, name, None)
        if callable(candidate):
            return candidate
    raise RuntimeError("operator_reference_materializer_missing")


def _materialize(*, repo_path: Path, run_dir: Path, required_workflow: str) -> Any:
    materializer = _materializer()
    parameters = inspect.signature(materializer).parameters
    arguments: dict[str, object] = {
        "definition": get_workflow(WORKFLOW_ID),
        "repo_path": repo_path,
        "run_dir": run_dir,
        "step_delay_seconds": 0.6,
    }
    if "required_workflow_id" in parameters:
        arguments["required_workflow_id"] = required_workflow
    elif "required_workflow" in parameters:
        arguments["required_workflow"] = required_workflow
    else:
        raise RuntimeError("operator_reference_required_workflow_parameter_missing")
    return materializer(**arguments)


def _wait_for_server(
    run_dir: Path,
    worker: threading.Thread,
    failure: list[BaseException],
) -> Any:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if failure:
            raise RuntimeError(f"operator_reference_workflow_failed:{failure[0]}")
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError, sqlite3.OperationalError) as exc:
            last_error = exc
            if not worker.is_alive() and not (run_dir / "dag-run.sqlite3").is_file():
                break
            time.sleep(0.02)
    raise RuntimeError(f"operator_reference_viewer_unavailable:{last_error}")


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}_not_object")
    return payload


def _result_artifacts(run_dir: Path) -> list[dict[str, object]]:
    artifacts = []
    for name, kind in (
        ("tau-operator-reference.json", "tau_operator_reference_json"),
        ("tau-operator-reference.md", "tau_operator_reference_markdown"),
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


def _validate_node_receipts(run_dir: Path, *, scenario: str) -> list[dict[str, object]]:
    receipts = []
    for index, node_id in enumerate(NODE_IDS):
        path = run_dir / "receipts" / f"{node_id}.json"
        payload = _json_object(path, label=f"{node_id}_receipt")
        errors = payload.get("errors")
        expected_status = "BLOCKED" if scenario == "negative" and index == 3 else "PASS"
        if payload.get("node_id") != node_id or payload.get("status") != expected_status:
            raise RuntimeError(f"operator_reference_node_receipt_invalid:{scenario}:{node_id}")
        if expected_status == "PASS" and payload.get("accepted_output") is None:
            raise RuntimeError(f"operator_reference_node_not_accepted:{scenario}:{node_id}")
        if expected_status == "BLOCKED" and errors != ["required_workflow_missing"]:
            raise RuntimeError("operator_reference_negative_error_code_invalid")
        receipts.append(
            {
                "node_id": node_id,
                "status": expected_status,
                "path": str(path),
                "sha256": _sha256(path),
            }
        )
    return receipts


def _proof_scenario(
    *,
    repo_root: Path,
    run_dir: Path,
    required_workflow: str,
    scenario: str,
    output: Path,
    desktop_screenshot: Path,
    mobile_screenshot: Path,
    node_root: str,
    source_ref: str,
) -> dict[str, object]:
    materialized = _materialize(
        repo_path=repo_root,
        run_dir=run_dir,
        required_workflow=required_workflow,
    )
    result: dict[str, object] = {}
    failure: list[BaseException] = []
    handshake_dir = run_dir.parent / f".{scenario}-browser-handshake"
    handshake_dir.mkdir(parents=True, exist_ok=False)
    url_path = handshake_dir / "viewer-url.txt"
    ready_path = handshake_dir / "browser-ready.txt"
    env = {**os.environ, "NODE_PATH": node_root}
    browser = subprocess.Popen(
        [
            "node",
            "scripts/operator-reference-browser-proof.mjs",
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
            f"operator_reference_browser_prewarm_failed:{browser_stderr}\n{browser_stdout}"
        )

    def execute() -> None:
        try:
            result.update(run_generic_dag(spec_path=materialized.source_dag_path, resume=False))
        except BaseException as exc:  # Preserve worker failure across the thread boundary.
            failure.append(exc)

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    server = _wait_for_server(materialized.run_dir, worker, failure)
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
        raise RuntimeError(f"operator_reference_workflow_timeout:{scenario}")
    if failure:
        raise RuntimeError(f"operator_reference_workflow_failed:{scenario}:{failure[0]}")
    if browser.returncode != 0:
        raise RuntimeError(
            f"operator_reference_browser_failed:{scenario}:{browser_stderr}\n{browser_stdout}"
        )

    receipt = _json_object(output, label="operator_reference_browser_receipt")
    artifacts = _result_artifacts(run_dir)
    expected_artifact_count = 2 if scenario == "positive" else 0
    if len(artifacts) != expected_artifact_count:
        raise RuntimeError(f"operator_reference_result_artifact_count:{scenario}")
    results_dir = run_dir / "results"
    if (
        scenario == "negative"
        and results_dir.is_dir()
        and any(path.is_file() for path in results_dir.iterdir())
    ):
        raise RuntimeError("operator_reference_negative_results_present")
    node_receipts = _validate_node_receipts(run_dir, scenario=scenario)
    receipt.update(
        {
            "required_workflow": required_workflow,
            "run_dir": str(run_dir),
            "source_dag_path": str(materialized.source_dag_path),
            "source_ref": source_ref,
            "workflow_status": result.get("status"),
            "workflow_verdict": result.get("verdict"),
            "node_receipts": node_receipts,
            "result_artifacts": artifacts,
            "proof_scope": {
                "proves": [
                    "The local packaged workflow ran against the installed Tau catalog.",
                    "The read-only viewer exposed live persisted transitions without reload.",
                    "The expected positive or deliberately absent-workflow boundary was observed.",
                ],
                "does_not_prove": [
                    "Provider or model execution quality.",
                    "Network-backed documentation freshness.",
                    "Production deployment readiness.",
                ],
            },
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

    with tempfile.TemporaryDirectory(prefix="tau-operator-reference-proof-") as temporary:
        root = Path(temporary)
        positive = _proof_scenario(
            repo_root=repo_root,
            run_dir=root / "positive-run",
            required_workflow=POSITIVE_REQUIRED_WORKFLOW,
            scenario="positive",
            output=outputs[0],
            desktop_screenshot=outputs[1],
            mobile_screenshot=outputs[2],
            node_root=node_root,
            source_ref=source_ref,
        )
        negative = _proof_scenario(
            repo_root=repo_root,
            run_dir=root / "negative-run",
            required_workflow=NEGATIVE_REQUIRED_WORKFLOW,
            scenario="negative",
            output=outputs[3],
            desktop_screenshot=outputs[4],
            mobile_screenshot=outputs[5],
            node_root=node_root,
            source_ref=source_ref,
        )
    summary = {
        "schema": "tau.operator_reference_browser_proof_summary.v1",
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
        "proof_scope": positive["proof_scope"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
