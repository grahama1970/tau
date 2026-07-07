"""Bounded external coding worker adapter receipts."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

OMP_WORK_ORDER_SCHEMA = "tau.executor.omp.v1"
OMP_WORKER_RESULT_SCHEMA = "tau.omp_worker_result.v1"
OMP_WORKER_RECEIPT_SCHEMA = "tau.omp_worker_receipt.v1"
OMP_WORKER_LAUNCH_RECEIPT_SCHEMA = "tau.omp_worker_launch_receipt.v1"
OMP_RPC_COMMAND = ["omp", "--mode", "rpc", "--no-session"]
SCILLM_WORK_ORDER_SCHEMA = "tau.executor.scillm_worker.v1"
SCILLM_WORKER_RESULT_SCHEMA = "tau.scillm_worker_result.v1"
SCILLM_WORKER_RECEIPT_SCHEMA = "tau.scillm_worker_receipt.v1"
SCILLM_WORKER_LAUNCH_RECEIPT_SCHEMA = "tau.scillm_worker_launch_receipt.v1"
SCILLM_OPENCODE_SERVE_ENDPOINT = "/v1/scillm/opencode/runs"
SANDBOX_RUN_RECEIPT_SCHEMA = "tau.sandbox_run_receipt.v1"
HERDR_OBSERVATION_GATE_RECEIPT_SCHEMA = "tau.herdr_observation_gate_receipt.v1"

ALLOWED_STATUSES = {"PASS", "BLOCKED", "NEEDS_REVIEW"}
ALLOWED_SUBSTRATES = {
    "docker",
    "docker-sandbox",
    "herdr",
    "herdr-visible",
    "bubblewrap",
    "local-low-risk",
}


def write_omp_worker_receipt(
    *,
    work_order_path: Path,
    result_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    return _write_worker_receipt(
        work_order_path=work_order_path,
        result_path=result_path,
        output_path=output_path,
        expected_work_order_schema=OMP_WORK_ORDER_SCHEMA,
        expected_result_schema=OMP_WORKER_RESULT_SCHEMA,
        receipt_schema=OMP_WORKER_RECEIPT_SCHEMA,
        worker_kind="omp",
    )


def write_scillm_worker_receipt(
    *,
    work_order_path: Path,
    result_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    return _write_worker_receipt(
        work_order_path=work_order_path,
        result_path=result_path,
        output_path=output_path,
        expected_work_order_schema=SCILLM_WORK_ORDER_SCHEMA,
        expected_result_schema=SCILLM_WORKER_RESULT_SCHEMA,
        receipt_schema=SCILLM_WORKER_RECEIPT_SCHEMA,
        worker_kind="scillm",
    )


def write_omp_worker_launch_receipt(
    *,
    work_order_path: Path,
    output_path: Path,
    caller_skill: str = "tau",
    apply: bool = False,
    omp_bin: str = "omp",
    timeout_s: int = 600,
) -> dict[str, Any]:
    """Write an OMP RPC launch request receipt."""

    resolved_work_order = work_order_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    alerts: list[dict[str, Any]] = []
    work_order = _read_json_object(resolved_work_order, alerts, "work_order")
    if work_order.get("schema") != OMP_WORK_ORDER_SCHEMA:
        alerts.append(
            _alert("invalid_work_order_schema", f"schema must be {OMP_WORK_ORDER_SCHEMA}")
        )
    _append_work_order_gate_alerts(work_order, alerts)
    route = _model_provider_route(work_order, {})
    route_surface = route.get("surface")
    if route_surface is not None and route_surface != "omp_rpc":
        alerts.append(_alert("invalid_omp_surface", "OMP worker launch must use omp_rpc"))

    request_payload = _omp_rpc_request_payload(work_order)
    command = [omp_bin, *OMP_RPC_COMMAND[1:]]
    launch_result = _maybe_run_omp_rpc_launch(
        apply=apply,
        command=command,
        stdin_payload=request_payload,
        output_path=resolved_output,
        work_order=work_order,
        alerts=alerts,
        timeout_s=timeout_s,
    )
    ok = not alerts
    payload = {
        "schema": OMP_WORKER_LAUNCH_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": launch_result["process_executed"],
        "provider_live": False,
        "dry_run": not apply,
        "apply_requested": apply,
        "process_executed": launch_result["process_executed"],
        "launch_skipped": launch_result["launch_skipped"],
        "exit_code": launch_result["exit_code"],
        "timed_out": launch_result["timed_out"],
        "timeout_s": timeout_s,
        "worker_kind": "omp",
        "work_order_path": str(resolved_work_order),
        "work_order_schema": work_order.get("schema"),
        "dag_id": work_order.get("dag_id"),
        "node_id": work_order.get("node_id"),
        "agent": work_order.get("agent"),
        "attempt": work_order.get("attempt"),
        "goal_hash": work_order.get("goal_hash"),
        **_substrate_metadata(work_order),
        "command": command,
        "stdin_jsonl": [request_payload],
        "stdout_path": launch_result["stdout_path"],
        "stdout_sha256": _artifact_sha256_uri(launch_result["stdout_path"]),
        "stdout_bytes": _artifact_size(launch_result["stdout_path"]),
        "stderr_path": launch_result["stderr_path"],
        "stderr_sha256": _artifact_sha256_uri(launch_result["stderr_path"]),
        "stderr_bytes": _artifact_size(launch_result["stderr_path"]),
        "log_artifacts": _artifact_descriptors(
            ("stdout", launch_result["stdout_path"]),
            ("stderr", launch_result["stderr_path"]),
        ),
        "caller_skill": caller_skill,
        "model_provider_route": route,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau built a bounded OMP RPC launch request from a work order.",
                "Tau checked work-order gates before any external OMP process launch.",
                "When apply=true and gates pass, Tau invoked the configured OMP command "
                "and captured stdout/stderr artifacts.",
            ],
            "does_not_prove": [
                "OMP accepted or ran the request.",
                "A real oh-my-pi binary was used unless independently identified.",
                "A worker result artifact is valid without omp-worker-validate.",
                "The worker is trustworthy.",
                "The code is semantically correct.",
                "Provider/model semantic quality.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(resolved_output)
    _write_json(output_path, payload)
    return payload


def write_scillm_worker_launch_receipt(
    *,
    work_order_path: Path,
    output_path: Path,
    scillm_base_url: str = "http://localhost:4001",
    caller_skill: str = "tau",
    apply: bool = False,
    auth_token: str | None = None,
    request_timeout_s: int = 650,
) -> dict[str, Any]:
    """Write a SciLLM OpenCode-serve launch request receipt."""

    resolved_work_order = work_order_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    alerts: list[dict[str, Any]] = []
    work_order = _read_json_object(resolved_work_order, alerts, "work_order")
    if work_order.get("schema") != SCILLM_WORK_ORDER_SCHEMA:
        alerts.append(
            _alert("invalid_work_order_schema", f"schema must be {SCILLM_WORK_ORDER_SCHEMA}")
        )
    _append_work_order_gate_alerts(work_order, alerts)
    route = _model_provider_route(work_order, {})
    if route.get("surface") != "opencode_serve":
        alerts.append(_alert("invalid_scillm_surface", "SciLLM worker must use opencode_serve"))
    if route.get("endpoint") != SCILLM_OPENCODE_SERVE_ENDPOINT:
        alerts.append(
            _alert(
                "invalid_scillm_endpoint",
                f"SciLLM worker endpoint must be {SCILLM_OPENCODE_SERVE_ENDPOINT}",
            )
        )
    agent = route.get("agent")
    if not isinstance(agent, str) or not agent:
        alerts.append(_alert("missing_scillm_agent_profile", "OpenCode serve agent is required"))
    elif agent.startswith("opencode-go/"):
        alerts.append(
            _alert("chat_model_used_as_agent", "OpenCode serve agent must be an agent profile")
        )
    auth_source = "explicit" if auth_token else "missing"
    effective_auth_token = auth_token
    if not effective_auth_token and _is_local_scillm_url(scillm_base_url):
        effective_auth_token, auth_source = _local_scillm_auth_token()
    if apply and not effective_auth_token:
        alerts.append(
            _alert("missing_scillm_auth_token", "apply requires a SciLLM bearer auth token")
        )

    request_payload = _scillm_opencode_request_payload(work_order, route)
    url = f"{scillm_base_url.rstrip('/')}{SCILLM_OPENCODE_SERVE_ENDPOINT}"
    launch_result = _maybe_post_scillm_opencode_run(
        apply=apply,
        url=url,
        request_payload=request_payload,
        output_path=resolved_output,
        caller_skill=caller_skill,
        auth_token=effective_auth_token,
        alerts=alerts,
        request_timeout_s=request_timeout_s,
    )
    ok = not alerts
    payload = {
        "schema": SCILLM_WORKER_LAUNCH_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": launch_result["http_executed"],
        "provider_live": False,
        "dry_run": not apply,
        "apply_requested": apply,
        "http_executed": launch_result["http_executed"],
        "launch_skipped": launch_result["launch_skipped"],
        "http_status": launch_result["http_status"],
        "timed_out": launch_result["timed_out"],
        "request_timeout_s": request_timeout_s,
        "worker_kind": "scillm",
        "work_order_path": str(resolved_work_order),
        "work_order_schema": work_order.get("schema"),
        "dag_id": work_order.get("dag_id"),
        "node_id": work_order.get("node_id"),
        "agent": work_order.get("agent"),
        "attempt": work_order.get("attempt"),
        "goal_hash": work_order.get("goal_hash"),
        **_substrate_metadata(work_order),
        "scillm_base_url": scillm_base_url.rstrip("/"),
        "endpoint": SCILLM_OPENCODE_SERVE_ENDPOINT,
        "url": url,
        "headers": {
            "authorization": (
                "REDACTED" if effective_auth_token else "REDACTED_REQUIRED"
            ),
            "authorization_source": auth_source,
            "x_caller_skill": caller_skill,
            "content_type": "application/json",
        },
        "model_provider_route": route,
        "request_payload": request_payload,
        "response_path": launch_result["response_path"],
        "response_sha256": _artifact_sha256_uri(launch_result["response_path"]),
        "response_bytes": _artifact_size(launch_result["response_path"]),
        "error_path": launch_result["error_path"],
        "error_sha256": _artifact_sha256_uri(launch_result["error_path"]),
        "error_bytes": _artifact_size(launch_result["error_path"]),
        "http_artifacts": _artifact_descriptors(
            ("response", launch_result["response_path"]),
            ("error", launch_result["error_path"]),
        ),
        "response_schema": launch_result["response_schema"],
        "run_id": launch_result["run_id"],
        "session_id": launch_result["session_id"],
        "scillm_run_status": launch_result["scillm_run_status"],
        "response_artifacts": launch_result["response_artifacts"],
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau built a bounded SciLLM OpenCode-serve launch request from a work order.",
                "Tau checked route metadata before any external SciLLM call.",
                "When apply=true and gates pass, Tau posted the request to the configured "
                "SciLLM OpenCode-serve endpoint and captured the response artifact.",
            ],
            "does_not_prove": [
                "The OpenCode worker result is truthful or sufficient for closure.",
                "The worker result artifact is valid without scillm-worker-validate.",
                "The worker is trustworthy.",
                "The code is semantically correct.",
                "Provider/model semantic quality.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(resolved_output)
    _write_json(output_path, payload)
    return payload


def _write_worker_receipt(
    *,
    work_order_path: Path,
    result_path: Path,
    output_path: Path,
    expected_work_order_schema: str,
    expected_result_schema: str,
    receipt_schema: str,
    worker_kind: str,
) -> dict[str, Any]:
    resolved_work_order = work_order_path.expanduser().resolve()
    resolved_result = result_path.expanduser().resolve()
    alerts: list[dict[str, Any]] = []
    work_order = _read_json_object(resolved_work_order, alerts, "work_order")
    result = _read_json_object(resolved_result, alerts, "worker_result")
    if work_order.get("schema") != expected_work_order_schema:
        alerts.append(
            _alert("invalid_work_order_schema", f"schema must be {expected_work_order_schema}")
        )
    if result.get("schema") != expected_result_schema:
        alerts.append(_alert("invalid_result_schema", f"schema must be {expected_result_schema}"))

    goal_hash = _string(work_order.get("goal_hash"))
    result_goal_hash = _string(result.get("goal_hash"))
    if not goal_hash:
        alerts.append(_alert("missing_goal_hash", "work order goal_hash is required"))
    elif result_goal_hash != goal_hash:
        alerts.append(_alert("goal_hash_mismatch", "worker result goal_hash mismatches work order"))

    repo = _repo_root(work_order)
    _append_work_order_gate_alerts(work_order, alerts)

    allowed_paths = _string_list(work_order.get("allowed_paths"))
    forbidden_paths = _string_list(work_order.get("forbidden_paths"))
    changed_files = _string_list(result.get("changed_files"))
    normalized_changed_files = _repo_relative_worker_paths(changed_files, repo)
    outside_changed_files = _paths_outside_repo(changed_files, repo)
    disallowed = [
        path
        for path in normalized_changed_files
        if not _path_allowed(path, allowed_paths) or _path_forbidden(path, forbidden_paths)
    ]
    if outside_changed_files:
        alerts.append(
            _alert(
                "changed_file_outside_repo",
                f"worker changed files must stay under repo: {outside_changed_files}",
            )
        )
    if disallowed:
        alerts.append(
            _alert(
                "disallowed_changed_file",
                f"worker changed files outside allowed paths: {disallowed}",
            )
        )

    required_artifacts = _string_list(work_order.get("required_artifacts"))
    result_artifacts = _string_list(result.get("artifacts"))
    missing_required_artifacts = _missing_required_artifacts(
        required_artifacts,
        result_artifacts,
        repo,
    )
    if missing_required_artifacts:
        alerts.append(
            _alert(
                "missing_required_artifact",
                f"worker result missing required artifacts: {missing_required_artifacts}",
            )
        )
    outside_required_artifacts = _artifacts_outside_repo(
        [artifact for artifact in result_artifacts if artifact in set(required_artifacts)],
        repo,
    )
    if outside_required_artifacts:
        alerts.append(
            _alert(
                "artifact_outside_repo",
                f"worker result artifacts must stay under repo: {outside_required_artifacts}",
            )
        )

    if _worker_result_is_prose_only(result):
        alerts.append(_alert("prose_only_result", "worker result must include structured evidence"))

    if result.get("status") not in ALLOWED_STATUSES:
        alerts.append(
            _alert("invalid_worker_status", "worker status must be PASS/BLOCKED/NEEDS_REVIEW")
        )

    if _tests_claim_pass_without_logs(result, repo):
        alerts.append(
            _alert("tests_passed_without_logs", "tests_run PASS entries require log paths")
        )
    outside_test_logs = _test_logs_outside_repo(result, repo)
    if outside_test_logs:
        alerts.append(
            _alert(
                "test_log_outside_repo",
                f"worker test log artifacts must stay under repo: {outside_test_logs}",
            )
        )

    if _requested_public_github_mutation(result):
        alerts.append(
            _alert(
                "github_mutation_requires_policy",
                "public GitHub mutation requires apply policy receipt",
            )
        )

    if _external_research_without_receipt(result):
        alerts.append(
            _alert(
                "external_research_requires_receipt",
                "external research requires a research-query or source receipt",
            )
        )

    ok = not alerts
    payload = {
        "schema": receipt_schema,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "worker_kind": worker_kind,
        "work_order_path": str(resolved_work_order),
        "work_order_sha256": _artifact_sha256_uri(resolved_work_order),
        "work_order_bytes": _artifact_size(resolved_work_order),
        "result_path": str(resolved_result),
        "result_sha256": _artifact_sha256_uri(resolved_result),
        "result_bytes": _artifact_size(resolved_result),
        "validated_artifacts": _artifact_descriptors(
            ("work_order", resolved_work_order),
            ("worker_result", resolved_result),
        ),
        "work_order_schema": work_order.get("schema"),
        "result_schema": result.get("schema"),
        "dag_id": work_order.get("dag_id"),
        "node_id": work_order.get("node_id"),
        "agent": work_order.get("agent"),
        "attempt": work_order.get("attempt"),
        "goal_hash": goal_hash,
        **_substrate_metadata(work_order),
        "model_provider_route": _model_provider_route(work_order, result),
        "changed_files": changed_files,
        "normalized_changed_files": normalized_changed_files,
        "required_artifacts": required_artifacts,
        "result_artifacts": result_artifacts,
        "required_artifact_descriptors": _required_artifact_descriptors(
            required_artifacts,
            result_artifacts,
            repo,
        ),
        "test_log_artifacts": _test_log_artifact_descriptors(result, repo),
        "next_recommended_route": result.get("next_recommended_route"),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau inspected an external coding worker result before accepting it.",
                "Tau recorded hashes for the validated work order and worker result.",
                "Tau checked goal hash, changed paths, required artifacts, test logs, "
                "mutation claims, and research claims.",
            ],
            "does_not_prove": [
                "The worker is trustworthy.",
                "The code is semantically correct.",
                "Tests passed unless durable logs are present.",
                "Provider/model semantic quality.",
                "The worker was launched by Tau.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def _read_json_object(path: Path, alerts: list[dict[str, Any]], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        alerts.append(_alert(f"{label}_missing", f"{label} file is missing"))
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(_alert(f"{label}_unreadable", f"{label} file is unreadable: {exc}"))
        return {}
    if not isinstance(payload, dict):
        alerts.append(_alert(f"{label}_not_object", f"{label} root must be a JSON object"))
        return {}
    return payload


def _maybe_run_omp_rpc_launch(
    *,
    apply: bool,
    command: list[str],
    stdin_payload: Mapping[str, Any],
    output_path: Path,
    work_order: Mapping[str, Any],
    alerts: list[dict[str, Any]],
    timeout_s: int,
) -> dict[str, Any]:
    stdout_path = output_path.with_suffix(output_path.suffix + ".stdout.jsonl")
    stderr_path = output_path.with_suffix(output_path.suffix + ".stderr.txt")
    result: dict[str, Any] = {
        "process_executed": False,
        "launch_skipped": not apply,
        "exit_code": None,
        "timed_out": False,
        "stdout_path": None,
        "stderr_path": None,
    }
    if not apply:
        return result
    if alerts:
        result["launch_skipped"] = True
        return result
    if timeout_s <= 0:
        alerts.append(_alert("invalid_timeout", "timeout_s must be positive"))
        result["launch_skipped"] = True
        return result

    stdin_jsonl = json.dumps(stdin_payload, sort_keys=True) + "\n"
    cwd = _repo_root(work_order)
    try:
        completed = subprocess.run(
            command,
            input=stdin_jsonl,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
        )
    except FileNotFoundError as exc:
        alerts.append(_alert("omp_command_missing", f"OMP command is missing: {exc}"))
        result["launch_skipped"] = True
        return result
    except subprocess.TimeoutExpired as exc:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text(exc.stderr or "", encoding="utf-8")
        alerts.append(_alert("omp_launch_timeout", f"OMP launch timed out after {timeout_s}s"))
        result.update(
            {
                "process_executed": True,
                "launch_skipped": False,
                "exit_code": None,
                "timed_out": True,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
        )
        return result

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        alerts.append(
            _alert("omp_launch_nonzero_exit", f"OMP launch exited {completed.returncode}")
        )
    result.update(
        {
            "process_executed": True,
            "launch_skipped": False,
            "exit_code": completed.returncode,
            "timed_out": False,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
    )
    return result


def _maybe_post_scillm_opencode_run(
    *,
    apply: bool,
    url: str,
    request_payload: Mapping[str, Any],
    output_path: Path,
    caller_skill: str,
    auth_token: str | None,
    alerts: list[dict[str, Any]],
    request_timeout_s: int,
) -> dict[str, Any]:
    response_path = output_path.with_suffix(output_path.suffix + ".response.json")
    error_path = output_path.with_suffix(output_path.suffix + ".error.txt")
    result: dict[str, Any] = {
        "http_executed": False,
        "launch_skipped": not apply,
        "http_status": None,
        "timed_out": False,
        "response_path": None,
        "error_path": None,
        "response_schema": None,
        "run_id": None,
        "session_id": None,
        "scillm_run_status": None,
        "response_artifacts": [],
    }
    if not apply:
        return result
    if alerts:
        result["launch_skipped"] = True
        return result
    if request_timeout_s <= 0:
        alerts.append(_alert("invalid_timeout", "request_timeout_s must be positive"))
        result["launch_skipped"] = True
        return result

    body = json.dumps(request_payload, sort_keys=True).encode("utf-8")
    headers = {
        "X-Caller-Skill": caller_skill,
        "Content-Type": "application/json",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=request_timeout_s) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            http_status = response.status
    except TimeoutError:
        alerts.append(
            _alert(
                "scillm_launch_timeout",
                f"SciLLM OpenCode serve request timed out after {request_timeout_s}s",
            )
        )
        result.update({"http_executed": True, "launch_skipped": False, "timed_out": True})
        return result
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.write_text(response_body, encoding="utf-8")
        alerts.append(_alert("scillm_http_error", f"SciLLM returned HTTP {exc.code}"))
        result.update(
            {
                "http_executed": True,
                "launch_skipped": False,
                "http_status": exc.code,
                "error_path": str(error_path),
            }
        )
        return result
    except urllib.error.URLError as exc:
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.write_text(str(exc), encoding="utf-8")
        alerts.append(_alert("scillm_connection_error", f"SciLLM request failed: {exc}"))
        result.update(
            {
                "http_executed": True,
                "launch_skipped": False,
                "error_path": str(error_path),
            }
        )
        return result

    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(response_body, encoding="utf-8")
    try:
        response_payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        alerts.append(_alert("scillm_response_not_json", f"SciLLM response is not JSON: {exc}"))
        response_payload = {}
    if not isinstance(response_payload, Mapping):
        alerts.append(_alert("scillm_response_not_object", "SciLLM response root must be object"))
        response_payload = {}

    run_status = _string(response_payload.get("status"))
    run_id = response_payload.get("run_id")
    session_id = response_payload.get("session_id")
    if not run_status:
        alerts.append(_alert("missing_scillm_run_status", "SciLLM response status is required"))
    elif run_status != "completed":
        alerts.append(_alert("scillm_run_not_completed", f"SciLLM run status is {run_status}"))
    if not (_string(run_id) or _string(session_id)):
        alerts.append(
            _alert(
                "missing_scillm_run_identifier",
                "SciLLM response requires run_id or session_id",
            )
        )
    artifacts = response_payload.get("artifacts")
    result.update(
        {
            "http_executed": True,
            "launch_skipped": False,
            "http_status": http_status,
            "timed_out": False,
            "response_path": str(response_path),
            "response_schema": response_payload.get("schema"),
            "run_id": run_id,
            "session_id": session_id,
            "scillm_run_status": run_status or None,
            "response_artifacts": artifacts if isinstance(artifacts, list) else [],
        }
    )
    return result


def _repo_root(work_order: Mapping[str, Any]) -> Path | None:
    repo = _string(work_order.get("repo"))
    return Path(repo).expanduser().resolve() if repo else None


def _is_local_scillm_url(base_url: str) -> bool:
    parsed = urllib.parse.urlparse(base_url)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _local_scillm_auth_token() -> tuple[str | None, str]:
    for key in ("SCILLM_MASTER_KEY", "SCILLM_API_KEY", "SCILLM_AUTH_TOKEN"):
        value = os.environ.get(key)
        if value:
            return value, f"env:{key}"
    env_path_override = os.environ.get("SCILLM_ENV_PATH")
    if env_path_override:
        env_paths = tuple(Path(item) for item in env_path_override.split(os.pathsep) if item)
    else:
        cwd = Path.cwd()
        env_paths = (
            cwd / ".env",
            cwd.parent / "scillm" / ".env",
            Path.home() / "workspace" / "experiments" / "scillm" / ".env",
        )
    for path in env_paths:
        value = _read_env_token(path)
        if value:
            return value, f"env_file:{path}"
    return None, "missing"


def _read_env_token(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() not in {"SCILLM_MASTER_KEY", "SCILLM_API_KEY", "SCILLM_AUTH_TOKEN"}:
            continue
        token = value.strip().strip("'\"")
        if token:
            return token
    return None


def _append_work_order_gate_alerts(
    work_order: Mapping[str, Any],
    alerts: list[dict[str, Any]],
) -> None:
    repo = _repo_root(work_order)
    substrate = _string(work_order.get("execution_substrate") or work_order.get("substrate"))
    high_stakes = bool(work_order.get("high_stakes") or work_order.get("zero_trust"))
    if high_stakes and substrate not in ALLOWED_SUBSTRATES:
        alerts.append(_alert("substrate_required", "high-stakes worker requires Herdr or sandbox"))
    if high_stakes and substrate == "local-low-risk":
        alerts.append(
            _alert("invalid_high_stakes_substrate", "high-stakes worker cannot use local-low-risk")
        )
    if high_stakes and substrate in {"docker", "docker-sandbox", "bubblewrap"}:
        sandbox_receipt_path = _string(work_order.get("sandbox_receipt_path"))
        if not sandbox_receipt_path:
            alerts.append(
                _alert(
                    "sandbox_receipt_required",
                    "high-stakes sandbox worker requires sandbox_receipt_path",
                )
            )
        else:
            sandbox_receipt = _load_referenced_receipt(
                sandbox_receipt_path,
                repo,
                alerts,
                missing_code="sandbox_receipt_missing",
                unreadable_code="sandbox_receipt_unreadable",
                not_object_code="sandbox_receipt_not_object",
                missing_message=(
                    "high-stakes sandbox worker sandbox_receipt_path does not exist"
                ),
            )
            _append_referenced_receipt_status_alerts(
                sandbox_receipt,
                alerts,
                expected_schema=SANDBOX_RUN_RECEIPT_SCHEMA,
                invalid_schema_code="sandbox_receipt_invalid_schema",
                not_pass_code="sandbox_receipt_not_pass",
                label="sandbox receipt",
            )
    if high_stakes and substrate in {"herdr", "herdr-visible"}:
        herdr_binding = isinstance(work_order.get("herdr_binding"), Mapping)
        herdr_receipt_path = _string(work_order.get("herdr_receipt_path"))
        if not (herdr_binding or herdr_receipt_path):
            alerts.append(
                _alert(
                    "herdr_binding_required",
                    "high-stakes Herdr worker requires herdr_binding or herdr_receipt_path",
                )
            )
        elif herdr_receipt_path:
            herdr_receipt = _load_referenced_receipt(
                herdr_receipt_path,
                repo,
                alerts,
                missing_code="herdr_receipt_missing",
                unreadable_code="herdr_receipt_unreadable",
                not_object_code="herdr_receipt_not_object",
                missing_message="high-stakes Herdr worker herdr_receipt_path does not exist",
            )
            _append_referenced_receipt_status_alerts(
                herdr_receipt,
                alerts,
                expected_schema=HERDR_OBSERVATION_GATE_RECEIPT_SCHEMA,
                invalid_schema_code="herdr_receipt_invalid_schema",
                not_pass_code="herdr_receipt_not_pass",
                label="Herdr observation receipt",
            )
    policy_profile = work_order.get("policy_profile")
    data_boundary = work_order.get("data_boundary")
    if high_stakes and not policy_profile:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust coding worker requires policy_profile")
        )
    elif high_stakes and (
        not isinstance(policy_profile, Mapping)
        or policy_profile.get("schema") != POLICY_PROFILE_SCHEMA
    ):
        alerts.append(
            _alert(
                "invalid_policy_profile_schema",
                f"zero-trust coding worker policy_profile.schema must be {POLICY_PROFILE_SCHEMA}",
            )
        )
    if high_stakes and not data_boundary:
        alerts.append(
            _alert("missing_data_boundary", "zero-trust coding worker requires data_boundary")
        )
    elif high_stakes and (
        not isinstance(data_boundary, Mapping)
        or data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA
    ):
        alerts.append(
            _alert(
                "invalid_data_boundary_schema",
                f"zero-trust coding worker data_boundary.schema must be {DATA_BOUNDARY_SCHEMA}",
            )
        )


def _substrate_metadata(work_order: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "execution_substrate": _string(
            work_order.get("execution_substrate") or work_order.get("substrate")
        ),
        "sandbox_receipt_path": work_order.get("sandbox_receipt_path"),
        "herdr_binding": work_order.get("herdr_binding"),
        "herdr_receipt_path": work_order.get("herdr_receipt_path"),
        "substrate_receipts": _referenced_substrate_receipts(work_order),
        "high_stakes": bool(work_order.get("high_stakes") or work_order.get("zero_trust")),
        "policy_profile": work_order.get("policy_profile"),
        "data_boundary": work_order.get("data_boundary"),
    }


def _referenced_substrate_receipts(work_order: Mapping[str, Any]) -> list[dict[str, Any]]:
    repo = _repo_root(work_order)
    return [
        artifact
        for artifact in (
            _referenced_substrate_receipt_artifact(
                "sandbox_receipt",
                work_order.get("sandbox_receipt_path"),
                repo,
            ),
            _referenced_substrate_receipt_artifact(
                "herdr_receipt",
                work_order.get("herdr_receipt_path"),
                repo,
            ),
        )
        if artifact is not None
    ]


def _referenced_receipt_artifact(
    label: str,
    raw_path: object,
    repo: Path | None,
) -> dict[str, Any] | None:
    path_value = _string(raw_path)
    if not path_value:
        return None
    path = _resolve_repo_artifact_path(path_value, repo)
    if path is None:
        return None
    if not path.exists() or not path.is_file():
        return None
    return {
        "label": label,
        "path": str(path),
        "exists": True,
        "sha256": _artifact_sha256_uri(path),
        "bytes": _artifact_size(path),
    }


def _referenced_substrate_receipt_artifact(
    label: str,
    raw_path: object,
    repo: Path | None,
) -> dict[str, Any] | None:
    descriptor = _referenced_receipt_artifact(label, raw_path, repo)
    if descriptor is None:
        return None
    try:
        payload = json.loads(Path(str(descriptor["path"])).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return descriptor
    if not isinstance(payload, Mapping):
        return descriptor
    descriptor.update(
        {
            "schema": payload.get("schema"),
            "status": payload.get("status"),
            "ok": payload.get("ok"),
            "mocked": payload.get("mocked"),
            "live": payload.get("live"),
            "provider_live": payload.get("provider_live"),
        }
    )
    return descriptor


def _load_referenced_receipt(
    path_value: str,
    repo: Path | None,
    alerts: list[dict[str, Any]],
    *,
    missing_code: str,
    unreadable_code: str,
    not_object_code: str,
    missing_message: str,
) -> Mapping[str, Any] | None:
    path = Path(path_value)
    if not path.is_absolute() and repo is not None:
        path = repo / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        alerts.append(_alert(missing_code, missing_message))
        return None
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(_alert(unreadable_code, f"referenced receipt is unreadable: {exc}"))
        return None
    if not isinstance(payload, Mapping):
        alerts.append(_alert(not_object_code, "referenced receipt root must be a JSON object"))
        return None
    return payload


def _append_referenced_receipt_status_alerts(
    receipt: Mapping[str, Any] | None,
    alerts: list[dict[str, Any]],
    *,
    expected_schema: str,
    invalid_schema_code: str,
    not_pass_code: str,
    label: str,
) -> None:
    if receipt is None:
        return
    if receipt.get("schema") != expected_schema:
        alerts.append(_alert(invalid_schema_code, f"{label} schema must be {expected_schema}"))
    if receipt.get("ok") is not True or receipt.get("status") != "PASS":
        alerts.append(_alert(not_pass_code, f"{label} must be PASS before worker acceptance"))


def _scillm_opencode_request_payload(
    work_order: Mapping[str, Any],
    route: Mapping[str, Any],
) -> dict[str, Any]:
    skills = route.get("skills")
    if not isinstance(skills, list) or not all(isinstance(item, str) for item in skills):
        skills = []
    return {
        "prompt": _scillm_worker_prompt(work_order),
        "agent": route.get("agent"),
        "skills": skills,
        "timeout_s": work_order.get("timeout_s", 600),
        "cleanup_session": True,
        "cwd": work_order.get("repo"),
        "scillm_metadata": {
            "schema": SCILLM_WORK_ORDER_SCHEMA,
            "dag_id": work_order.get("dag_id"),
            "node_id": work_order.get("node_id"),
            "attempt": work_order.get("attempt"),
            "goal_hash": work_order.get("goal_hash"),
            "result_path": work_order.get("result_path"),
            "receipt_path": work_order.get("receipt_path"),
        },
    }


def _omp_rpc_request_payload(work_order: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": f"tau-{_string(work_order.get('dag_id'))}-{_string(work_order.get('node_id'))}",
        "type": "prompt",
        "message": _omp_worker_prompt(work_order),
        "metadata": {
            "schema": OMP_WORK_ORDER_SCHEMA,
            "dag_id": work_order.get("dag_id"),
            "node_id": work_order.get("node_id"),
            "attempt": work_order.get("attempt"),
            "goal_hash": work_order.get("goal_hash"),
            "result_path": work_order.get("result_path"),
            "receipt_path": work_order.get("receipt_path"),
        },
    }


def _omp_worker_prompt(work_order: Mapping[str, Any]) -> str:
    allowed_paths = ", ".join(_string_list(work_order.get("allowed_paths"))) or "(none)"
    forbidden_paths = ", ".join(_string_list(work_order.get("forbidden_paths"))) or "(none)"
    required_artifacts = ", ".join(_string_list(work_order.get("required_artifacts"))) or "(none)"
    return "\n".join(
        [
            "You are an untrusted oh-my-pi coding worker running under Tau.",
            f"Task: {_string(work_order.get('task')) or ''}",
            f"Goal hash: {_string(work_order.get('goal_hash')) or ''}",
            f"Allowed paths: {allowed_paths}",
            f"Forbidden paths: {forbidden_paths}",
            f"Required artifacts: {required_artifacts}",
            "Return a tau.omp_worker_result.v1 JSON artifact at the requested result_path.",
            "Do not claim tests passed without durable logs.",
            "Do not mutate paths outside the allowlist.",
        ]
    )


def _scillm_worker_prompt(work_order: Mapping[str, Any]) -> str:
    allowed_paths = ", ".join(_string_list(work_order.get("allowed_paths"))) or "(none)"
    forbidden_paths = ", ".join(_string_list(work_order.get("forbidden_paths"))) or "(none)"
    required_artifacts = ", ".join(_string_list(work_order.get("required_artifacts"))) or "(none)"
    return "\n".join(
        [
            "You are an untrusted coding worker running under Tau.",
            f"Task: {_string(work_order.get('task')) or ''}",
            f"Goal hash: {_string(work_order.get('goal_hash')) or ''}",
            f"Allowed paths: {allowed_paths}",
            f"Forbidden paths: {forbidden_paths}",
            f"Required artifacts: {required_artifacts}",
            "Return structured evidence for Tau validation; do not claim closure from prose.",
        ]
    )


def _missing_required_artifacts(
    required_artifacts: list[str],
    result_artifacts: list[str],
    repo: Path | None,
) -> list[str]:
    missing: list[str] = []
    result_names = set(result_artifacts)
    for artifact in required_artifacts:
        if artifact not in result_names or not _path_exists(artifact, repo):
            missing.append(artifact)
    return missing


def _required_artifact_descriptors(
    required_artifacts: list[str],
    result_artifacts: list[str],
    repo: Path | None,
) -> list[dict[str, Any]]:
    result_names = set(result_artifacts)
    descriptors: list[dict[str, Any]] = []
    for artifact in required_artifacts:
        if artifact not in result_names:
            continue
        descriptor = _referenced_receipt_artifact("required_artifact", artifact, repo)
        if descriptor is None:
            continue
        descriptor["artifact"] = artifact
        descriptors.append(descriptor)
    return descriptors


def _worker_result_is_prose_only(result: Mapping[str, Any]) -> bool:
    evidence_fields = ("changed_files", "artifacts", "tests_run", "findings")
    has_evidence = any(bool(result.get(field)) for field in evidence_fields)
    return bool(result.get("assistant_text")) and not has_evidence


def _tests_claim_pass_without_logs(result: Mapping[str, Any], repo: Path | None) -> bool:
    tests = result.get("tests_run")
    if not isinstance(tests, list):
        return False
    for item in tests:
        if not isinstance(item, Mapping) or item.get("status") != "PASS":
            continue
        log_path = _string(item.get("log_path") or item.get("stdout_path"))
        if not log_path:
            return True
        candidate = _resolve_repo_artifact_path(log_path, repo)
        if candidate is None:
            return True
        if not candidate.exists():
            return True
    return False


def _test_log_artifact_descriptors(
    result: Mapping[str, Any],
    repo: Path | None,
) -> list[dict[str, Any]]:
    tests = result.get("tests_run")
    if not isinstance(tests, list):
        return []
    descriptors: list[dict[str, Any]] = []
    for index, item in enumerate(tests):
        if not isinstance(item, Mapping):
            continue
        log_path = _string(item.get("log_path") or item.get("stdout_path"))
        if not log_path:
            continue
        descriptor = _referenced_receipt_artifact("test_log", log_path, repo)
        if descriptor is None:
            continue
        descriptor["test_index"] = index
        descriptor["test_name"] = _string(item.get("name"))
        descriptor["test_status"] = _string(item.get("status"))
        descriptor["artifact"] = log_path
        descriptors.append(descriptor)
    return descriptors


def _path_exists(path: str, repo: Path | None) -> bool:
    candidate = _resolve_repo_artifact_path(path, repo)
    if candidate is None:
        return False
    return candidate.exists()


def _repo_relative_worker_paths(paths: list[str], repo: Path | None) -> list[str]:
    return [_repo_relative_worker_path(path, repo) for path in paths]


def _repo_relative_worker_path(path: str, repo: Path | None) -> str:
    if repo is None:
        return path
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return path
    try:
        return candidate.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path


def _paths_outside_repo(paths: list[str], repo: Path | None) -> list[str]:
    return [path for path in paths if _path_outside_repo(path, repo)]


def _artifacts_outside_repo(artifacts: list[str], repo: Path | None) -> list[str]:
    return [artifact for artifact in artifacts if _path_outside_repo(artifact, repo)]


def _test_logs_outside_repo(result: Mapping[str, Any], repo: Path | None) -> list[str]:
    tests = result.get("tests_run")
    if not isinstance(tests, list):
        return []
    outside: list[str] = []
    for item in tests:
        if not isinstance(item, Mapping):
            continue
        log_path = _string(item.get("log_path") or item.get("stdout_path"))
        if log_path and _path_outside_repo(log_path, repo):
            outside.append(log_path)
    return outside


def _path_outside_repo(path: str, repo: Path | None) -> bool:
    if repo is None:
        return False
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return False
    try:
        candidate.resolve().relative_to(repo.resolve())
    except ValueError:
        return True
    return False


def _resolve_repo_artifact_path(path: str, repo: Path | None) -> Path | None:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and repo is not None:
        candidate = repo / candidate
    resolved = candidate.resolve()
    if repo is not None:
        try:
            resolved.relative_to(repo.resolve())
        except ValueError:
            return None
    return resolved


def _requested_public_github_mutation(result: Mapping[str, Any]) -> bool:
    mutations = result.get("requested_mutations")
    if not isinstance(mutations, list):
        return False
    for mutation in mutations:
        if not isinstance(mutation, Mapping):
            continue
        target = str(mutation.get("target") or "")
        if target.startswith("github:") and not mutation.get("github_apply_policy_receipt"):
            return True
    return False


def _external_research_without_receipt(result: Mapping[str, Any]) -> bool:
    if result.get("external_research_used") is not True:
        return False
    return not (
        result.get("research_query_safety_receipt") or result.get("research_source_receipt")
    )


def _model_provider_route(
    work_order: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    route = work_order.get("model_provider_route")
    if isinstance(route, Mapping):
        return dict(route)
    route = result.get("model_provider_route")
    return dict(route) if isinstance(route, Mapping) else {}


def _path_allowed(path: str, patterns: list[str]) -> bool:
    return bool(patterns) and any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _path_forbidden(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact_descriptors(*items: tuple[str, object]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for label, raw_path in items:
        path = _artifact_path(raw_path)
        if path is None:
            continue
        artifacts.append(
            {
                "label": label,
                "path": str(path),
                "exists": True,
                "sha256": _artifact_sha256_uri(path),
                "bytes": path.stat().st_size,
            }
        )
    return artifacts


def _artifact_sha256_uri(raw_path: object) -> str | None:
    path = _artifact_path(raw_path)
    if path is None:
        return None
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _artifact_size(raw_path: object) -> int | None:
    path = _artifact_path(raw_path)
    return path.stat().st_size if path is not None else None


def _artifact_path(raw_path: object) -> Path | None:
    if not isinstance(raw_path, str | Path) or not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    return path if path.exists() else None


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
