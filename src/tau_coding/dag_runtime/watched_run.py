"""Store-backed, viewer-watched programmatic scheduler runs (tau#312).

``run_dag_plan_watched`` wraps ``run_dag_plan`` so adapter-embedded
programmatic runs (ask tau_harness, team-plan --execute, canaries) get:

1. an opt-in durable run store — ``<run_dir>/dag-run.sqlite3`` — so the run
   journal exists from the first event;
2. a live React Flow viewer started against that store BEFORE the first node
   executes, its URL available to the caller and printed receipt from t0;
3. a ``tau.dag_viewer_link.v1`` payload embedded in the returned run receipt.

The viewer is read-only and loopback-only (enforced by the viewer server).
The caller owns its lifetime: pass ``keep_viewer=True`` to leave it serving
after the run settles (for humans still watching), else it is shut down.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import DagPlan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagSchedulerResult, NodeExecutor, run_dag_plan

WATCHED_RUN_RECEIPT_SCHEMA = "tau.watched_dag_run_receipt.v1"


@dataclass(slots=True)
class WatchedDagRun:
    """Outcome of a watched run: scheduler result, receipt, live viewer handle."""

    result: DagSchedulerResult
    receipt: dict[str, Any]
    viewer: Any | None

    @property
    def viewer_url(self) -> str | None:
        return self.viewer.url if self.viewer is not None else None

    def shutdown_viewer(self) -> None:
        if self.viewer is not None:
            self.viewer.httpd.shutdown()
            self.viewer = None


def run_dag_plan_watched(
    plan: DagPlan,
    *,
    execute_node: NodeExecutor | None = None,
    execute_node_factory: Any | None = None,
    run_dir: Path,
    watch: bool = True,
    keep_viewer: bool = False,
    viewer_host: str = "127.0.0.1",
    viewer_port: int = 0,
    on_viewer_url: Any | None = None,
    **scheduler_kwargs: Any,
) -> WatchedDagRun:
    """Run a DagPlan with a durable store and (optionally) a live viewer.

    ``on_viewer_url`` is called with the URL string before the first node
    executes, so callers can print or forward the link at run start.
    """
    if (execute_node is None) == (execute_node_factory is None):
        raise RuntimeError("pass exactly one of execute_node or execute_node_factory")
    resolved = run_dir.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    store = SqliteDagRunStore(resolved / "dag-run.sqlite3")
    lease_box: dict[str, Any] = {}
    if execute_node_factory is not None:
        # Factory receives the durable store and a getter for the held lease so
        # node executors can persist agent events into the canonical journal.
        execute_node = execute_node_factory(store, lambda: lease_box.get("lease"))
    viewer_box: dict[str, Any] = {"viewer": None}
    caller_on_lease = scheduler_kwargs.pop("on_lease_acquired", None)

    def _on_lease_acquired(lease: Any) -> None:
        lease_box["lease"] = lease
        # The run record exists once the lease is held, and no node has
        # executed yet — start the viewer here so the URL is live from t0.
        if watch and viewer_box["viewer"] is None:
            from tau_coding.dag_viewer.server import create_dag_viewer_server

            viewer = create_dag_viewer_server(
                run_dir=resolved, host=viewer_host, port=viewer_port
            )
            threading.Thread(
                target=viewer.httpd.serve_forever, name="tau-dag-viewer", daemon=True
            ).start()
            viewer_box["viewer"] = viewer
            if on_viewer_url is not None:
                on_viewer_url(viewer.url)
        if caller_on_lease is not None:
            caller_on_lease(lease)

    try:
        result = run_dag_plan(
            plan,
            execute_node=execute_node,
            run_store=store,
            on_lease_acquired=_on_lease_acquired,
            **scheduler_kwargs,
        )
    except BaseException:
        if viewer_box["viewer"] is not None:
            viewer_box["viewer"].httpd.shutdown()
        raise
    finally:
        store.close()
    viewer = viewer_box["viewer"]

    from tau_coding.run_status import build_dag_viewer_link

    viewer_link = build_dag_viewer_link(resolved)
    receipt: dict[str, Any] = {
        "schema": WATCHED_RUN_RECEIPT_SCHEMA,
        "run_id": result.run_id or plan.plan_id,
        "status": result.status,
        "verdict": result.verdict,
        "durable": result.durable,
        "run_dir": str(resolved),
        "run_store_path": str(resolved / "dag-run.sqlite3"),
        "completed_node_ids": list(result.completed_node_ids),
        "dag_viewer_link": viewer_link,
        "viewer": (
            {
                "url": viewer.url,
                "served_from_run_start": True,
                "server_receipt": viewer.receipt(),
            }
            if viewer is not None
            else None
        ),
    }
    if viewer is not None and not keep_viewer:
        viewer.httpd.shutdown()
        viewer = None
    return WatchedDagRun(result=result, receipt=receipt, viewer=viewer)
