"""Bounded external coding worker adapter receipts."""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
) -> dict[str, Any]:
    """Write a dry-run OMP RPC launch request receipt."""

    resolved_work_order = work_order_path.expanduser().resolve()
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
    if apply:
        alerts.append(_alert("apply_not_implemented", "live OMP worker launch is not implemented"))

    request_payload = _omp_rpc_request_payload(work_order)
    ok = not alerts
    payload = {
        "schema": OMP_WORKER_LAUNCH_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "dry_run": True,
        "apply_requested": apply,
        "worker_kind": "omp",
        "work_order_path": str(resolved_work_order),
        "work_order_schema": work_order.get("schema"),
        "dag_id": work_order.get("dag_id"),
        "node_id": work_order.get("node_id"),
        "agent": work_order.get("agent"),
        "attempt": work_order.get("attempt"),
        "goal_hash": work_order.get("goal_hash"),
        "command": OMP_RPC_COMMAND,
        "stdin_jsonl": [request_payload],
        "caller_skill": caller_skill,
        "model_provider_route": route,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau built a bounded OMP RPC launch request from a work order.",
                "Tau checked work-order gates before any external OMP process launch.",
            ],
            "does_not_prove": [
                "Tau launched OMP.",
                "OMP accepted or ran the request.",
                "The worker is trustworthy.",
                "The code is semantically correct.",
                "Provider/model semantic quality.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def write_scillm_worker_launch_receipt(
    *,
    work_order_path: Path,
    output_path: Path,
    scillm_base_url: str = "http://localhost:4001",
    caller_skill: str = "tau",
    apply: bool = False,
) -> dict[str, Any]:
    """Write a dry-run SciLLM OpenCode-serve launch request receipt."""

    resolved_work_order = work_order_path.expanduser().resolve()
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
    if apply:
        alerts.append(
            _alert("apply_not_implemented", "live SciLLM worker launch is not implemented")
        )

    request_payload = _scillm_opencode_request_payload(work_order, route)
    ok = not alerts
    payload = {
        "schema": SCILLM_WORKER_LAUNCH_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "dry_run": True,
        "apply_requested": apply,
        "worker_kind": "scillm",
        "work_order_path": str(resolved_work_order),
        "work_order_schema": work_order.get("schema"),
        "dag_id": work_order.get("dag_id"),
        "node_id": work_order.get("node_id"),
        "agent": work_order.get("agent"),
        "attempt": work_order.get("attempt"),
        "goal_hash": work_order.get("goal_hash"),
        "scillm_base_url": scillm_base_url.rstrip("/"),
        "endpoint": SCILLM_OPENCODE_SERVE_ENDPOINT,
        "url": f"{scillm_base_url.rstrip('/')}{SCILLM_OPENCODE_SERVE_ENDPOINT}",
        "headers": {
            "authorization": "REDACTED_REQUIRED",
            "x_caller_skill": caller_skill,
            "content_type": "application/json",
        },
        "model_provider_route": route,
        "request_payload": request_payload,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau built a bounded SciLLM OpenCode-serve launch request from a work order.",
                "Tau checked route metadata before any external SciLLM call.",
            ],
            "does_not_prove": [
                "Tau called SciLLM.",
                "OpenCode serve accepted or ran the request.",
                "The worker is trustworthy.",
                "The code is semantically correct.",
                "Provider/model semantic quality.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
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
    substrate = _string(work_order.get("execution_substrate") or work_order.get("substrate"))
    high_stakes = bool(work_order.get("high_stakes") or work_order.get("zero_trust"))

    allowed_paths = _string_list(work_order.get("allowed_paths"))
    forbidden_paths = _string_list(work_order.get("forbidden_paths"))
    changed_files = _string_list(result.get("changed_files"))
    disallowed = [
        path
        for path in changed_files
        if not _path_allowed(path, allowed_paths) or _path_forbidden(path, forbidden_paths)
    ]
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
        "result_path": str(resolved_result),
        "work_order_schema": work_order.get("schema"),
        "result_schema": result.get("schema"),
        "dag_id": work_order.get("dag_id"),
        "node_id": work_order.get("node_id"),
        "agent": work_order.get("agent"),
        "attempt": work_order.get("attempt"),
        "goal_hash": goal_hash,
        "execution_substrate": substrate,
        "sandbox_receipt_path": work_order.get("sandbox_receipt_path"),
        "herdr_binding": work_order.get("herdr_binding"),
        "herdr_receipt_path": work_order.get("herdr_receipt_path"),
        "high_stakes": high_stakes,
        "policy_profile": work_order.get("policy_profile"),
        "data_boundary": work_order.get("data_boundary"),
        "model_provider_route": _model_provider_route(work_order, result),
        "changed_files": changed_files,
        "required_artifacts": required_artifacts,
        "result_artifacts": result_artifacts,
        "next_recommended_route": result.get("next_recommended_route"),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau inspected an external coding worker result before accepting it.",
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


def _repo_root(work_order: Mapping[str, Any]) -> Path | None:
    repo = _string(work_order.get("repo"))
    return Path(repo).expanduser().resolve() if repo else None


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
        elif not _path_exists(sandbox_receipt_path, repo):
            alerts.append(
                _alert(
                    "sandbox_receipt_missing",
                    "high-stakes sandbox worker sandbox_receipt_path does not exist",
                )
            )
    if high_stakes and substrate in {"herdr", "herdr-visible"}:
        if not (
            isinstance(work_order.get("herdr_binding"), Mapping)
            or _string(work_order.get("herdr_receipt_path"))
        ):
            alerts.append(
                _alert(
                    "herdr_binding_required",
                    "high-stakes Herdr worker requires herdr_binding or herdr_receipt_path",
                )
            )
    if high_stakes and not work_order.get("policy_profile"):
        alerts.append(
            _alert("missing_policy_profile", "zero-trust coding worker requires policy_profile")
        )
    if high_stakes and not work_order.get("data_boundary"):
        alerts.append(
            _alert("missing_data_boundary", "zero-trust coding worker requires data_boundary")
        )


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
        if artifact in result_names:
            continue
        candidate = Path(artifact).expanduser()
        if not candidate.is_absolute() and repo is not None:
            candidate = repo / candidate
        if not candidate.exists():
            missing.append(artifact)
    return missing


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
        candidate = Path(log_path).expanduser()
        if not candidate.is_absolute() and repo is not None:
            candidate = repo / candidate
        if not candidate.exists():
            return True
    return False


def _path_exists(path: str, repo: Path | None) -> bool:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and repo is not None:
        candidate = repo / candidate
    return candidate.exists()


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


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
