"""Deterministic tests for tau#312 watched store-backed scheduler runs."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.watched_run import run_dag_plan_watched


def _spec(tmp_path: Path) -> dict[str, Any]:
    def node(node_id: str, depends_on: list[str]) -> dict[str, Any]:
        return {
            "node_id": node_id,
            "role": node_id,
            "command": ["true"],
            "depends_on": depends_on,
            "accepted_context_from": depends_on,
            "receipt_path": str(tmp_path / "receipts" / f"{node_id}.json"),
            "timeout_seconds": 10,
            "max_attempts": 1,
        }

    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "watched-run-test",
        "run_dir": str(tmp_path / "run"),
        "nodes": [node("first", []), node("second", ["first"])],
    }


def test_watched_run_serves_viewer_before_first_node_settles(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(_spec(tmp_path), source_path=tmp_path / "dag.json")
    run_dir = tmp_path / "watched"
    seen: dict[str, Any] = {}

    def on_url(url: str) -> None:
        seen["url_at_start"] = url

    def execute(plan_node: Any, accepted_inputs: Any, execution: Any) -> dict[str, Any]:
        if plan_node.node_id == "first":
            # The viewer must already be serving while the first node runs.
            with urllib.request.urlopen(seen["url_at_start"], timeout=5) as response:
                seen["status_during_run"] = response.status
                seen["body_head"] = response.read(2048).decode("utf-8", "replace")
            with urllib.request.urlopen(
                seen["url_at_start"] + "api/v1/manifest", timeout=5
            ) as response:
                seen["manifest"] = json.loads(response.read().decode())
        return {
            "node_id": plan_node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": plan_node.node_id},
        }

    watched = run_dag_plan_watched(
        plan, execute_node=execute, run_dir=run_dir, on_viewer_url=on_url
    )
    assert watched.result.status == "PASS"
    assert seen["status_during_run"] == 200
    assert (run_dir / "dag-run.sqlite3").is_file()
    receipt = watched.receipt
    assert receipt["schema"] == "tau.watched_dag_run_receipt.v1"
    assert receipt["durable"] is True
    assert receipt["viewer"]["url"] == seen["url_at_start"]
    assert receipt["viewer"]["served_from_run_start"] is True
    assert receipt["dag_viewer_link"]["schema"] == "tau.dag_viewer_link.v1"
    assert watched.viewer is None  # shut down by default after the run


def test_watch_disabled_still_writes_durable_store(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(_spec(tmp_path), source_path=tmp_path / "dag.json")

    def execute(plan_node: Any, accepted_inputs: Any, execution: Any) -> dict[str, Any]:
        return {
            "node_id": plan_node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": plan_node.node_id},
        }

    watched = run_dag_plan_watched(
        plan, execute_node=execute, run_dir=tmp_path / "unwatched", watch=False
    )
    assert watched.result.status == "PASS"
    assert watched.receipt["viewer"] is None
    assert watched.viewer_url is None
    assert (tmp_path / "unwatched" / "dag-run.sqlite3").is_file()


def test_keep_viewer_leaves_server_running(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(_spec(tmp_path), source_path=tmp_path / "dag.json")

    def execute(plan_node: Any, accepted_inputs: Any, execution: Any) -> dict[str, Any]:
        return {
            "node_id": plan_node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": plan_node.node_id},
        }

    watched = run_dag_plan_watched(
        plan, execute_node=execute, run_dir=tmp_path / "kept", keep_viewer=True
    )
    try:
        assert watched.viewer_url is not None
        with urllib.request.urlopen(watched.viewer_url, timeout=5) as response:
            assert response.status == 200
    finally:
        watched.shutdown_viewer()
    assert watched.viewer is None
