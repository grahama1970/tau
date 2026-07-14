#!/usr/bin/env python3
"""Run a non-mocked Herdr runtime adapter smoke with fail-closed controls."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.herdr_cleanup import classify_herdr_surface, run_herdr_cleanup
from tau_coding.runtime_backends import (
    HerdrRuntimeBackend,
    herdr_cleanup_authorization,
    herdr_runtime_scope_request,
    herdr_runtime_spawn_request,
    herdr_runtime_work_order,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--session", default="default")
    parser.add_argument("--herdr-bin", default="herdr")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()
    receipt = run_smoke(
        out_dir=args.out_dir,
        session=args.session,
        herdr_bin=args.herdr_bin,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


def run_smoke(
    *, out_dir: Path, session: str, herdr_bin: str, timeout_seconds: float
) -> dict[str, Any]:
    resolved_out = out_dir.expanduser().resolve()
    resolved_out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"tau-herdr-runtime-smoke-{stamp}"
    owner = "tau-runtime-smoke"
    backend = HerdrRuntimeBackend(
        session=session,
        herdr_bin=herdr_bin,
        poll_interval_seconds=0.05,
    )
    scope_records: list[dict[str, Any]] = []
    lease = None
    submit = None
    capture: dict[str, Any] | None = None
    observation = None
    endpoint_cleanup: dict[str, Any] | None = None
    workspace_cleanup: dict[str, Any] | None = None
    wrong_session: dict[str, Any] | None = None
    unauthorized_cleanup_blocked = False
    marker = f"TAU_HERDR_RUNTIME_SMOKE:{stamp}"
    errors: list[str] = []
    herdr_surface = classify_herdr_surface(herdr_bin)
    if herdr_surface != "real":
        errors.append("herdr_binary_provenance_unverified")
    runtime_manifest_path = resolved_out / "runtime-manifest.json"
    workspace_lease_path = resolved_out / "herdr-workspace-lease.json"
    try:
        for suffix in ("primary", "collision"):
            scope = backend.ensure_scope(
                herdr_runtime_scope_request(
                    run_id=f"{run_id}-{suffix}",
                    owner=owner,
                    cwd=resolved_out,
                    label="tau-herdr-runtime-smoke",
                )
            ).to_value()
            scope_records.append(scope)
        if scope_records[0]["workspace_id"] == scope_records[1]["workspace_id"]:
            raise RuntimeError("duplicate labels resolved to the same workspace id")
        _write_cleanup_inputs(
            runtime_manifest_path=runtime_manifest_path,
            workspace_lease_path=workspace_lease_path,
            run_id=run_id,
            owner=owner,
            scope_records=scope_records,
        )
        work_order_sha256 = canonical_sha256({"marker": marker})
        lease = backend.spawn(
            herdr_runtime_spawn_request(
                run_id=f"{run_id}-primary",
                plan_revision=canonical_sha256({"plan": run_id}),
                dag_id="tau-herdr-runtime-smoke",
                node_id="shell-worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token=canonical_sha256({"execution": run_id}),
                scope_id=str(scope_records[0]["scope_id"]),
                command=("bash", "--noprofile", "--norc"),
                cwd=resolved_out,
                work_order_sha256=work_order_sha256,
                goal_hash=canonical_sha256({"goal": "exercise Herdr adapter"}),
                owner=owner,
                label="shell-worker",
                lease_seconds=max(timeout_seconds + 60.0, 120.0),
            )
        )
        _write_json(resolved_out / "runtime-endpoint-lease.json", lease.to_payload())
        backend_ids = lease.backend_ids.to_value()
        _write_cleanup_inputs(
            runtime_manifest_path=runtime_manifest_path,
            workspace_lease_path=workspace_lease_path,
            run_id=run_id,
            owner=owner,
            scope_records=scope_records,
            endpoint=backend_ids,
        )
        wrong_session_name = f"tau-issue-90-missing-{stamp.lower()}"
        wrong_session = _probe_wrong_session(
            herdr_bin=herdr_bin,
            session=wrong_session_name,
            pane_id=lease.endpoint_id,
            timeout_seconds=timeout_seconds,
        )
        if wrong_session["timed_out"] is True:
            errors.append("wrong_session_check_timed_out")
        if wrong_session["returncode"] == 0:
            raise RuntimeError("wrong Herdr session unexpectedly resolved endpoint")
        bad_authorization = herdr_cleanup_authorization(lease).to_value()
        bad_authorization["owner"] = "not-the-owner"
        try:
            backend.terminate(lease, FrozenJson.from_value(bad_authorization))
        except RuntimeError as exc:
            unauthorized_cleanup_blocked = "cleanup_unauthorized" in str(exc)
        if not unauthorized_cleanup_blocked:
            raise RuntimeError("unowned endpoint cleanup was not blocked")
        backend.observe(lease)
        submit = backend.submit(
            lease,
            herdr_runtime_work_order(
                work_order_sha256=work_order_sha256,
                text=f"printf '{marker}\\n'",
            ),
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            capture = backend.capture(lease, 80).to_value()
            if marker in str(capture.get("text") or ""):
                break
            time.sleep(0.1)
        observation = backend.observe(lease)
        if capture is None or marker not in str(capture.get("text") or ""):
            raise RuntimeError("submitted marker was not visible before timeout")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if lease is not None:
            try:
                endpoint_cleanup = backend.terminate(
                    lease, herdr_cleanup_authorization(lease)
                ).to_value()
            except Exception as exc:
                errors.append(f"endpoint_cleanup_failed: {type(exc).__name__}: {exc}")
        if scope_records:
            try:
                _write_cleanup_inputs(
                    runtime_manifest_path=runtime_manifest_path,
                    workspace_lease_path=workspace_lease_path,
                    run_id=run_id,
                    owner=owner,
                    scope_records=scope_records,
                    endpoint=(lease.backend_ids.to_value() if lease is not None else None),
                )
                workspace_cleanup = run_herdr_cleanup(
                    run_dir=resolved_out,
                    mode="apply",
                    herdr_bin=herdr_bin,
                    session=session,
                    workspace_lease_path=workspace_lease_path,
                )
            except Exception as exc:
                errors.append(f"workspace_cleanup_failed: {type(exc).__name__}: {exc}")
    passed = all(
        (
            not errors,
            lease is not None,
            submit is not None and submit.delivery_status == "CONFIRMED",
            capture is not None and marker in str(capture.get("text") or ""),
            observation is not None,
            wrong_session is not None and wrong_session["blocked"] is True,
            unauthorized_cleanup_blocked,
            endpoint_cleanup is not None
            and endpoint_cleanup.get("post_verified_absent") is True,
            workspace_cleanup is not None and workspace_cleanup.get("status") == "PASS",
            workspace_cleanup is not None
            and workspace_cleanup.get("post_verified_absent_count")
            == len(scope_records),
        )
    )
    receipt = {
        "schema": "tau.herdr_runtime_smoke_receipt.v1",
        "ok": passed,
        "status": "PASS" if passed else "BLOCKED",
        "mocked": herdr_surface != "real",
        "live": herdr_surface == "real",
        "provider_live": False,
        "herdr_surface": herdr_surface,
        "run_id": run_id,
        "backend_session_id": session,
        "duplicate_label_control": {
            "workspace_ids": [scope["workspace_id"] for scope in scope_records],
            "distinct_exact_ids": len({scope["workspace_id"] for scope in scope_records})
            == len(scope_records),
        },
        "wrong_session_control": wrong_session,
        "unowned_cleanup_blocked": unauthorized_cleanup_blocked,
        "endpoint_lease": lease.to_payload() if lease is not None else None,
        "submit_receipt": submit.to_payload() if submit is not None else None,
        "capture": capture,
        "runtime_event": observation.to_payload() if observation is not None else None,
        "endpoint_cleanup": endpoint_cleanup,
        "workspace_cleanup_receipt": str(resolved_out / "herdr-cleanup-receipt.json"),
        "workspace_cleanup": workspace_cleanup,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau used the real Herdr CLI with an explicit session.",
                "Duplicate labels produced distinct recorded workspace IDs.",
                "Tau spawned, submitted to, captured, and observed one exact pane ID.",
                "Wrong-session lookup and unowned endpoint cleanup were blocked.",
                "Endpoint and workspace cleanup were post-verified absent.",
            ],
            "does_not_prove": [
                "Pane text or runtime state completed a Tau DAG node.",
                "Provider/model semantic quality.",
                "Crash-safe restart reconciliation.",
                "Secure sandbox isolation or production readiness.",
            ],
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    _write_json(resolved_out / "herdr-runtime-smoke-receipt.json", receipt)
    return receipt


def _probe_wrong_session(
    *, herdr_bin: str, session: str, pane_id: str, timeout_seconds: float
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [herdr_bin, "--session", session, "pane", "get", pane_id],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        return {
            "session": session,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "blocked": result.returncode != 0,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "session": session,
            "returncode": 124,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "blocked": False,
            "timed_out": True,
        }


def _write_cleanup_inputs(
    *,
    runtime_manifest_path: Path,
    workspace_lease_path: Path,
    run_id: str,
    owner: str,
    scope_records: list[dict[str, Any]],
    endpoint: dict[str, Any] | None = None,
) -> None:
    provider_sessions = {}
    for index, scope in enumerate(scope_records):
        record = {
            "workspace_id": scope["workspace_id"],
            "pane_id": None,
            "terminal_id": None,
        }
        if index == 0 and endpoint is not None:
            record["pane_id"] = endpoint.get("pane_id")
            record["terminal_id"] = endpoint.get("terminal_id")
        provider_sessions[f"scope-{index + 1}"] = record
    _write_json(
        runtime_manifest_path,
        {
            "schema": "tau.herdr_runtime_manifest.v1",
            "run_id": run_id,
            "backend_session_id": scope_records[0]["backend_session_id"],
            "provider_sessions": provider_sessions,
        },
    )
    _write_json(
        workspace_lease_path,
        {
            "schema": "tau.herdr_workspace_lease.v1",
            "run_id": run_id,
            "dag_id": "tau-herdr-runtime-smoke",
            "owner": owner,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "cleanup_policy": "apply",
            "workspace_ids": [scope["workspace_id"] for scope in scope_records],
        },
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
