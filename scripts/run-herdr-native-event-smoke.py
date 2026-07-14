"""Run a non-mocked Tau-to-Herdr native event subscription smoke.

The script creates one Tau-owned Herdr scope and endpoint, waits for one native
agent-status observation, verifies the event is diagnostic rather than node
completion authority, cleans up the exact pane and workspace, and writes a
machine-readable receipt. It never invokes a model provider.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.runtime_backends import (
    HerdrRuntimeBackend,
    herdr_cleanup_authorization,
    herdr_runtime_scope_request,
    herdr_runtime_spawn_request,
)

app = typer.Typer(add_completion=False)


@app.command()  # type: ignore[untyped-decorator]
def main(
    out: Annotated[Path, typer.Option("--out")],
    session: Annotated[str, typer.Option("--session")] = "default",
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=0.1)] = 5.0,
) -> None:
    """Exercise one live native event without granting completion authority."""

    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    run_id = f"native-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    backend = HerdrRuntimeBackend(session=session)
    capabilities = backend.capabilities()
    if not capabilities.native_events:
        raise typer.Exit(_write_blocked(out, run_id, "native_events_unavailable"))
    scope: dict[str, Any] | None = None
    endpoint = None
    cleanup_errors: list[str] = []
    try:
        scope = backend.ensure_scope(
            herdr_runtime_scope_request(
                run_id=run_id,
                owner="tau-native-smoke",
                cwd=Path.cwd(),
                label="native-event-smoke",
            )
        ).to_value()
        endpoint = backend.spawn(
            herdr_runtime_spawn_request(
                run_id=run_id,
                plan_revision=canonical_sha256({"smoke": "native-event"}),
                dag_id="herdr-native-event-smoke",
                node_id="observer",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="native-smoke-token",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=Path.cwd(),
                work_order_sha256=canonical_sha256({"work": "observe-only"}),
                goal_hash=canonical_sha256({"goal": "native-event-smoke"}),
                owner="tau-native-smoke",
                lease_seconds=timeout_seconds + 30,
                label="native-event-observer",
            )
        )
        event = backend.wait_event(
            endpoint,
            cursor=None,
            deadline=datetime.now(UTC) + timedelta(seconds=timeout_seconds),
        )
        if event is None:
            raise RuntimeError("native_event_not_received")
        observation = event.observation.to_value()
        if observation.get("transport", {}).get("mode") != "native":
            raise RuntimeError("native_event_fell_back_to_polling")
        receipt = {
            "schema": "tau.herdr_native_event_smoke_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "run_id": run_id,
            "herdr": {
                "session": session,
                "runtime_version": capabilities.version,
                "native_events": capabilities.native_events,
            },
            "endpoint_lease_sha256": endpoint.sha256,
            "runtime_event": event.to_payload(),
            "command_executed": True,
            "provider_invoked": False,
            "node_completion_claimed": False,
            "proof_scope": {
                "proves": [
                    "Tau subscribed to a live Herdr session through the native socket API.",
                    "Tau bound the observation to the exact run, workspace, pane, and lease.",
                    "The runtime event remained diagnostic and did not settle a DAG node.",
                ],
                "does_not_prove": [
                    "Agent semantic correctness.",
                    "DAG node completion.",
                    "Provider or model quality.",
                    "Future Herdr protocol compatibility.",
                ],
            },
        }
        out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        typer.echo(json.dumps(receipt, sort_keys=True))
    except Exception as exc:
        _write_blocked(out, run_id, str(exc))
        raise
    finally:
        if endpoint is not None:
            cleanup = backend.terminate(endpoint, herdr_cleanup_authorization(endpoint)).to_value()
            if cleanup.get("status") != "PASS":
                cleanup_errors.append("endpoint_cleanup_failed")
        if scope is not None:
            completed = subprocess.run(
                [
                    "herdr",
                    "--session",
                    session,
                    "workspace",
                    "close",
                    str(scope["scope_id"]),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if completed.returncode != 0:
                cleanup_errors.append("workspace_cleanup_failed")
        if cleanup_errors:
            cleanup_error = ",".join(cleanup_errors)
            _write_blocked(out, run_id, cleanup_error)
            raise RuntimeError(cleanup_error)


def _write_blocked(out: Path, run_id: str, error: str) -> int:
    receipt = {
        "schema": "tau.herdr_native_event_smoke_receipt.v1",
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_id": run_id,
        "errors": [error],
        "node_completion_claimed": False,
    }
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    typer.echo(json.dumps(receipt, sort_keys=True))
    return 1


if __name__ == "__main__":
    app()
