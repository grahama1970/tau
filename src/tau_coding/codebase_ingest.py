"""Codebase-ingest coordination for Tau."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.external_workspace import agent_skills_root
from tau_coding.runtime_backends.contracts import RuntimeEndpointLease

CODEBASE_INGEST_RECEIPT_SCHEMA = "tau.codebase_ingest_receipt.v2"
DEFAULT_INGEST_CODE_RUNNER = str(agent_skills_root() / "skills/ingest-code/run.sh")


def write_codebase_ingest_receipt(
    *,
    repo_path: Path,
    receipt_path: Path,
    state_path: Path,
    ingest_runner: str = DEFAULT_INGEST_CODE_RUNNER,
    scope: str = "monitor-tau",
    start: bool = False,
    run_id: str | None = None,
    plan_id: str = "codebase-ingest",
    node_id: str = "ingest-code-scan",
    attempt_number: int = 1,
    goal_hash: str | None = None,
    timeout_seconds: float = 300.0,
    projection_authorized: bool = False,
    memory_socket_path: str = "/run/user/1000/embry/memory.sock",
    projection_timeout_seconds: float = 30.0,
    cancel_requested: bool = False,
    restart_worker: dict[str, Any] | None = None,
    restart_liveness: Literal["ALIVE", "DEAD", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """Write a Tau-owned ingest receipt, optionally running an emit-mode scan."""

    repo = repo_path.expanduser().resolve()
    if not repo.is_dir():
        raise RuntimeError(f"repo path must be a directory: {repo}")
    resolved_receipt = receipt_path.expanduser().resolve()
    resolved_state = state_path.expanduser().resolve()
    prior_state = _read_json_object(resolved_state) if resolved_state.exists() else {}
    current_commit = _git_value(repo, ["rev-parse", "HEAD"], default="unknown")
    current_files = _file_manifest(repo)
    changed_files = _changed_files(current_files, prior_state.get("files"))
    effective_run_id = run_id or f"codebase-ingest-{_short_hash(str(repo))}-{int(time.time())}"
    attempt_id = f"{effective_run_id}:{node_id}:attempt-{attempt_number:03d}"
    command = [
        ingest_runner,
        "scan",
        str(repo),
        "--treesitter",
        "--projection-mode",
        "emit",
        "--scope",
        scope,
    ]
    work_order = {
        "schema": "tau.codebase_ingest_work_order.v1",
        "run_id": effective_run_id,
        "plan_id": plan_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "repo_path": str(repo),
        "commit": current_commit,
        "scope": scope,
        "command": command,
        "projection_mode": "emit",
        "changed_files_sha256": canonical_sha256(changed_files),
    }
    effective_goal_hash = goal_hash or canonical_sha256(
        {
            "repo_path": str(repo),
            "commit": current_commit,
            "scope": scope,
            "purpose": "tau-codebase-ingest",
        }
    )
    endpoint_lease = _runtime_endpoint_lease(
        run_id=effective_run_id,
        plan_id=plan_id,
        node_id=node_id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        work_order=work_order,
        goal_hash=effective_goal_hash,
        repo=repo,
    )
    status = "SKIPPED_UNCHANGED" if not changed_files else "READY_FOR_SCAN"
    worker: dict[str, Any] | None = None
    scan_result: dict[str, Any] | None = None
    admission = _empty_admission()
    recovery = _recovery_state(
        restart_worker=restart_worker,
        restart_liveness=restart_liveness,
        attempt_id=attempt_id,
        endpoint_lease=endpoint_lease,
    )
    if changed_files and cancel_requested:
        status = "CANCELLED"
        admission["first_failed_gate"] = "cancel_requested"
        admission["errors"].append("cancelled before ingest-code worker execution")
    elif changed_files and restart_liveness == "ALIVE":
        status = "RECONCILED_LIVE_WORKER"
        worker = restart_worker
    elif changed_files and restart_liveness == "UNKNOWN":
        status = "BLOCKED_UNKNOWN_LIVENESS"
        admission["first_failed_gate"] = "unknown_liveness"
        admission["errors"].append("existing worker liveness is unknown; replacement refused")
    if (
        start
        and changed_files
        and status
        not in {
            "CANCELLED",
            "RECONCILED_LIVE_WORKER",
            "BLOCKED_UNKNOWN_LIVENESS",
        }
    ):
        worker = {
            "schema": "tau.codebase_ingest_worker.v1",
            "ownership": "tau_synchronous_skill_worker",
            "detached_child": False,
            "runtime_endpoint_lease": endpoint_lease.to_payload(),
            "runtime_endpoint_lease_sha256": endpoint_lease.sha256,
        }
        started = time.monotonic()
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=str(repo),
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        scan_result = {
            "schema": "tau.codebase_ingest_scan_process.v1",
            "exit_code": completed.returncode,
            "stdout_sha256": _sha256_text(completed.stdout),
            "stderr_sha256": _sha256_text(completed.stderr),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
        if completed.returncode == 0:
            admission = _admit_emit_scan(repo)
            status = "SCAN_ADMITTED" if admission["ok"] else "BLOCKED"
            if status == "SCAN_ADMITTED" and projection_authorized:
                projection = _apply_projection_request(
                    admission,
                    memory_socket_path=memory_socket_path,
                    timeout_seconds=projection_timeout_seconds,
                )
                status = projection["terminal_status"]
        else:
            admission["first_failed_gate"] = "scan_exit"
            admission["errors"].append(f"scan exited non-zero: {completed.returncode}")
            status = "BLOCKED"
    if status in {
        "SKIPPED_UNCHANGED",
        "SCAN_ADMITTED",
        "PROJECTION_ACCEPTED",
        "SCAN_ADMITTED_PROJECTION_DEGRADED",
    }:
        _write_json(resolved_state, _state_payload(repo, current_commit, current_files))
    receipt = {
        "schema": CODEBASE_INGEST_RECEIPT_SCHEMA,
        "ok": status
        in {
            "SKIPPED_UNCHANGED",
            "READY_FOR_SCAN",
            "CANCELLED",
            "RECONCILED_LIVE_WORKER",
            "SCAN_ADMITTED",
            "PROJECTION_ACCEPTED",
            "SCAN_ADMITTED_PROJECTION_DEGRADED",
        },
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo_path": str(repo),
        "commit": current_commit,
        "state_path": str(resolved_state),
        "receipt_path": str(resolved_receipt),
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "command": command,
        "work_order": work_order,
        "work_order_sha256": canonical_sha256(work_order),
        "run_id": effective_run_id,
        "plan_id": plan_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "goal_hash": effective_goal_hash,
        "nodes": _nodes(status=status, changed=bool(changed_files), admission=admission),
        "started": bool(worker),
        "worker": worker,
        "process": None,
        "scan_result": scan_result,
        "recovery": recovery,
        "admission": admission,
        "projection": _projection_state(admission),
        "accepted_effect_count": _projection_state(admission)["accepted_effect_count"],
        "interactive_blocking": False,
        "resumable": True,
        "memory_writes_performed_by_tau": False,
        "change_marker_advanced": status
        in {
            "SKIPPED_UNCHANGED",
            "SCAN_ADMITTED",
            "PROJECTION_ACCEPTED",
            "SCAN_ADMITTED_PROJECTION_DEGRADED",
        },
        "proof_scope": {
            "proves": [
                "Tau detects changed repository files before scheduling ingest-code.",
                "Unchanged repositories produce a typed skip with no worker or effect.",
                "The scan command uses ingest-code projection-mode emit and carries no "
                "Memory/GMO effect authority.",
                "Tau records run, plan, node, attempt, work-order, goal, and endpoint "
                "lease identities before running the skill.",
                "Tau advances the local change marker only after a typed skip or admitted "
                "terminal scan result.",
                "Cancellation, live-worker restart, dead-worker retry, and unknown-liveness "
                "replacement are represented as typed states without detached ownership.",
            ],
            "does_not_prove": [
                "Memory/GMO generation activation.",
                "Projection effect reconciliation when projection_authorized=true and "
                "Memory/GMO returns generation readback.",
                "Tree-sitter extraction correctness.",
                "Point-in-time Memory recall.",
                "Operating-system process truth beyond the supplied liveness observation.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt, receipt)
    return receipt


def _state_payload(repo: Path, commit: str, files: dict[str, str]) -> dict[str, Any]:
    return {
        "schema": "tau.codebase_ingest_state.v2",
        "repo_path": str(repo),
        "commit": commit,
        "files": files,
        "updated_at": _utc_stamp(),
    }


def _runtime_endpoint_lease(
    *,
    run_id: str,
    plan_id: str,
    node_id: str,
    attempt_id: str,
    attempt_number: int,
    work_order: dict[str, Any],
    goal_hash: str,
    repo: Path,
) -> RuntimeEndpointLease:
    now = _utc_stamp()
    return RuntimeEndpointLease(
        run_id=run_id,
        plan_revision=plan_id,
        dag_id="codebase-ingest",
        node_id=node_id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        execution_token=_short_hash(canonical_sha256(work_order)),
        backend="local-skill-runner",
        backend_session_id=None,
        scope_id=str(repo),
        endpoint_id=f"local-skill:{_short_hash(attempt_id)}",
        work_order_sha256=canonical_sha256(work_order),
        goal_hash=goal_hash,
        owner="tau-codebase-ingest",
        created_at=now,
        expires_at=now,
        heartbeat_policy=FrozenJson.from_value({"mode": "one_shot"}),
        cleanup_policy=FrozenJson.from_value({"mode": "no_detached_child"}),
        capabilities_sha256=canonical_sha256({"backend": "local-skill-runner", "one_shot": True}),
        backend_ids=FrozenJson.from_value(
            {"entrypoint": work_order["command"][0], "cwd": str(repo)}
        ),
    )


def _recovery_state(
    *,
    restart_worker: dict[str, Any] | None,
    restart_liveness: str | None,
    attempt_id: str,
    endpoint_lease: RuntimeEndpointLease,
) -> dict[str, Any]:
    prior_lease = (
        restart_worker.get("runtime_endpoint_lease") if isinstance(restart_worker, dict) else None
    )
    prior_attempt_id = prior_lease.get("attempt_id") if isinstance(prior_lease, dict) else None
    prior_endpoint_id = prior_lease.get("endpoint_id") if isinstance(prior_lease, dict) else None
    if restart_liveness == "ALIVE":
        action = "reconcile_existing_worker"
        replacement_allowed = False
    elif restart_liveness == "DEAD":
        action = "launch_new_attempt"
        replacement_allowed = True
    elif restart_liveness == "UNKNOWN":
        action = "block_replacement"
        replacement_allowed = False
    else:
        action = "normal_start"
        replacement_allowed = True
    return {
        "schema": "tau.codebase_ingest_recovery.v1",
        "restart_liveness": restart_liveness,
        "action": action,
        "replacement_allowed": replacement_allowed,
        "prior_attempt_id": prior_attempt_id,
        "prior_endpoint_id": prior_endpoint_id,
        "new_attempt_id": attempt_id,
        "new_endpoint_id": endpoint_lease.endpoint_id,
        "new_attempt_differs": prior_attempt_id is None or prior_attempt_id != attempt_id,
        "new_endpoint_differs": (
            prior_endpoint_id is None or prior_endpoint_id != endpoint_lease.endpoint_id
        ),
    }


def _empty_admission() -> dict[str, Any]:
    return {
        "schema": "tau.codebase_ingest_admission.v1",
        "ok": False,
        "first_failed_gate": None,
        "errors": [],
        "artifacts": {},
        "environment_manifest_digest": None,
        "bundle_digest": None,
        "checksums_digest": None,
        "projection_request_digest": None,
        "projection_request_idempotency_key": None,
        "projection_mode": None,
        "projection_applied": False,
    }


def _admit_emit_scan(repo: Path) -> dict[str, Any]:
    admission = _empty_admission()
    marker_path = repo / ".ingest-code.json"
    marker = _read_json_object(marker_path)
    if not marker:
        return _blocked(admission, "missing_marker", f"missing ingest-code marker: {marker_path}")
    code_index = marker.get("code_index")
    local_artifacts = marker.get("local_artifacts")
    if not isinstance(code_index, dict):
        return _blocked(admission, "marker_shape", "marker code_index must be an object")
    if not isinstance(local_artifacts, dict):
        return _blocked(admission, "marker_shape", "marker local_artifacts must be an object")
    if code_index.get("projection_mode") != "emit":
        return _blocked(admission, "projection_mode", "scan did not run in emit mode")
    if code_index.get("projection_applied") is not False:
        return _blocked(admission, "scan_effect_authority", "scan stage applied projection")
    graph = local_artifacts.get("code_graph")
    request = local_artifacts.get("code_projection_request")
    environment = local_artifacts.get("environment_manifest")
    if not isinstance(graph, dict) or graph.get("complete") is not True:
        return _blocked(admission, "code_graph", "code graph bundle is missing or incomplete")
    if not isinstance(request, dict) or request.get("status") != "emitted_not_applied":
        return _blocked(admission, "projection_request", "projection request was not emitted")
    if not isinstance(environment, dict) or environment.get("admissible") is not True:
        return _blocked(admission, "environment", "environment manifest was not admissible")
    request_path = Path(str(request.get("path", "")))
    request_payload = _read_json_object(request_path)
    if request_payload.get("schema") != "ingest-code.code_projection_request.v1":
        return _blocked(
            admission, "projection_request_schema", "projection request schema mismatch"
        )
    if request_payload.get("environment_manifest_digest") != environment.get(
        "environment_manifest_digest"
    ):
        return _blocked(
            admission,
            "environment_digest",
            "projection request is not bound to the environment manifest digest",
        )
    admission.update(
        {
            "ok": True,
            "artifacts": {
                "marker": _artifact_ref(marker_path),
                "code_graph_manifest": _artifact_ref(Path(str(graph.get("manifest")))),
                "code_graph_checksums": _artifact_ref(Path(str(graph.get("checksums")))),
                "code_graph_coverage": _artifact_ref(Path(str(graph.get("coverage")))),
                "projection_request": _artifact_ref(request_path),
                "environment_manifest": _artifact_ref(Path(str(environment.get("path")))),
            },
            "environment_manifest_digest": environment.get("environment_manifest_digest"),
            "bundle_digest": request.get("submitted_bundle_digest"),
            "checksums_digest": request.get("checksums_digest"),
            "projection_request_digest": request.get("sha256"),
            "projection_request_idempotency_key": request.get("idempotency_key"),
            "projection_mode": "emit",
            "projection_applied": False,
        }
    )
    return admission


def _blocked(admission: dict[str, Any], gate: str, error: str) -> dict[str, Any]:
    admission["first_failed_gate"] = gate
    admission["errors"].append(error)
    return admission


def _artifact_ref(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "exists": resolved.is_file(),
        "sha256": _sha256(resolved) if resolved.is_file() else None,
        "bytes": resolved.stat().st_size if resolved.is_file() else None,
    }


def _projection_state(admission: dict[str, Any]) -> dict[str, Any]:
    if isinstance(admission.get("projection_state"), dict):
        return admission["projection_state"]
    if not admission.get("ok"):
        state = "blocked_before_projection"
    elif admission.get("projection_applied") is False:
        state = "request_emitted_unapplied"
    else:
        state = "unknown"
    return {
        "schema": "tau.codebase_ingest_projection_state.v1",
        "state": state,
        "policy_authorized": False,
        "accepted_effect_count": 0,
        "generation_id": None,
        "readback": None,
    }


def _apply_projection_request(
    admission: dict[str, Any],
    *,
    memory_socket_path: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    request_ref = admission.get("artifacts", {}).get("projection_request")
    request_path = (
        Path(str(request_ref.get("path", ""))) if isinstance(request_ref, dict) else Path()
    )
    request = _read_json_object(request_path)
    projection_state = {
        "schema": "tau.codebase_ingest_projection_state.v1",
        "state": "degraded_unapplied",
        "policy_authorized": True,
        "accepted_effect_count": 0,
        "generation_id": None,
        "readback": None,
        "errors": [],
        "request_sha256": _sha256(request_path) if request_path.is_file() else None,
    }
    if request.get("schema") != "ingest-code.code_projection_request.v1":
        projection_state["errors"].append("projection_request_schema_mismatch")
        admission["projection_state"] = projection_state
        return {"terminal_status": "SCAN_ADMITTED_PROJECTION_DEGRADED"}
    mutation_errors = _admitted_artifact_mutation_errors(admission)
    if mutation_errors:
        projection_state["state"] = "blocked_stale_artifact"
        projection_state["errors"].extend(mutation_errors)
        admission["projection_state"] = projection_state
        return {"terminal_status": "SCAN_ADMITTED_PROJECTION_DEGRADED"}
    try:
        transport = httpx.HTTPTransport(uds=memory_socket_path)
        with httpx.Client(
            transport=transport,
            base_url="http://localhost",
            timeout=projection_timeout(timeout_seconds),
        ) as client:
            response = client.post("/code/projection/apply", json=request)
    except Exception as exc:
        projection_state["errors"].append(str(exc))
        admission["projection_state"] = projection_state
        return {"terminal_status": "SCAN_ADMITTED_PROJECTION_DEGRADED"}
    if not 200 <= response.status_code < 300:
        projection_state["errors"].append(f"HTTP {response.status_code}: {response.text}")
        admission["projection_state"] = projection_state
        return {"terminal_status": "SCAN_ADMITTED_PROJECTION_DEGRADED"}
    try:
        receipt = response.json()
    except ValueError as exc:
        projection_state["errors"].append(f"invalid_projection_receipt_json: {exc}")
        admission["projection_state"] = projection_state
        return {"terminal_status": "SCAN_ADMITTED_PROJECTION_DEGRADED"}
    generation = receipt.get("generation") if isinstance(receipt, dict) else None
    errors: list[str] = []
    if receipt.get("status") != "applied":
        errors.append("projection_receipt_status_not_applied")
    if receipt.get("submitted_bundle_digest") != admission.get("bundle_digest"):
        errors.append("projection_bundle_digest_mismatch")
    if receipt.get("checksums_digest") != admission.get("checksums_digest"):
        errors.append("projection_checksums_digest_mismatch")
    if not isinstance(generation, dict) or not generation.get("generation_id"):
        errors.append("projection_generation_missing")
    if errors:
        projection_state["errors"].extend(errors)
        projection_state["receipt"] = receipt
        admission["projection_state"] = projection_state
        return {"terminal_status": "SCAN_ADMITTED_PROJECTION_DEGRADED"}
    projection_state.update(
        {
            "state": "accepted_effect_applied",
            "accepted_effect_count": 1,
            "generation_id": generation["generation_id"],
            "readback": {
                "generation": generation,
                "counts": receipt.get("counts"),
                "status": receipt.get("status"),
            },
            "receipt": receipt,
            "receipt_sha256": canonical_sha256(receipt),
        }
    )
    admission["projection_state"] = projection_state
    return {"terminal_status": "PROJECTION_ACCEPTED"}


def projection_timeout(timeout_seconds: float) -> httpx.Timeout:
    return httpx.Timeout(timeout_seconds)


def _admitted_artifact_mutation_errors(admission: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    artifacts = admission.get("artifacts")
    if not isinstance(artifacts, dict):
        return ["admitted_artifacts_missing"]
    for key in (
        "code_graph_manifest",
        "code_graph_checksums",
        "code_graph_coverage",
        "projection_request",
        "environment_manifest",
    ):
        ref = artifacts.get(key)
        if not isinstance(ref, dict):
            errors.append(f"{key}_ref_missing")
            continue
        path = Path(str(ref.get("path", "")))
        expected = ref.get("sha256")
        if not path.is_file():
            errors.append(f"{key}_missing")
        elif expected != _sha256(path):
            errors.append(f"{key}_sha256_mismatch")
    return errors


def _nodes(*, status: str, changed: bool, admission: dict[str, Any]) -> list[dict[str, Any]]:
    scan_status = (
        "SKIPPED"
        if not changed
        else "PASS"
        if status in {"SCAN_ADMITTED", "PROJECTION_ACCEPTED", "SCAN_ADMITTED_PROJECTION_DEGRADED"}
        else "CANCELLED"
        if status == "CANCELLED"
        else "RECONCILED"
        if status == "RECONCILED_LIVE_WORKER"
        else "BLOCKED"
        if status in {"BLOCKED", "BLOCKED_UNKNOWN_LIVENESS"}
        else "READY"
    )
    projection_status = (
        "ACCEPTED"
        if status == "PROJECTION_ACCEPTED"
        else "DEGRADED"
        if status == "SCAN_ADMITTED_PROJECTION_DEGRADED"
        else "NOT_AUTHORIZED"
        if admission.get("ok")
        else "BLOCKED"
    )
    return [
        {
            "id": "preflight",
            "status": "PASS",
            "accepted": True,
        },
        {
            "id": "ingest-code-scan",
            "status": scan_status,
            "accepted": status
            in {
                "SCAN_ADMITTED",
                "PROJECTION_ACCEPTED",
                "SCAN_ADMITTED_PROJECTION_DEGRADED",
            },
            "first_failed_gate": admission.get("first_failed_gate"),
        },
        {
            "id": "projection-effect",
            "status": projection_status,
            "accepted": status == "PROJECTION_ACCEPTED",
        },
    ]


def _file_manifest(repo: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name.endswith((".pyc", ".pyo")):
            continue
        rel = path.relative_to(repo).as_posix()
        if rel in {".batch_state.json", ".ingest-code.json"} or rel.startswith(
            "artifacts/ingest-code/"
        ):
            continue
        files[rel] = _sha256(path)
    return files


def _changed_files(current: dict[str, str], prior: object) -> list[str]:
    if not isinstance(prior, dict):
        return sorted(current)
    changed = [path for path, digest in current.items() if prior.get(path) != digest]
    deleted = [path for path in prior if isinstance(path, str) and path not in current]
    return sorted(changed + deleted)


def _git_value(repo: Path, args: list[str], *, default: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return default
    return result.stdout.strip() or default


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
