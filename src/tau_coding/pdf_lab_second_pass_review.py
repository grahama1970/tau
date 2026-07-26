"""Tau-owned PDF Lab second-pass review DAG contract executor.

This module accepts a hash-bound PDF Lab Tau DAG contract, verifies the
referenced page-review artifacts, routes the model-ready request through Tau's
SciLLM chat-review adapter, and writes a terminal receipt that makes the
provider ownership boundary explicit. It fails closed before transport when the
contract or artifact hashes do not match.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.scillm_chat_review import write_scillm_chat_review_receipt

TAU_DAG_CONTRACT_SCHEMA = "tau.dag_contract.v1"
PDF_LAB_SECOND_PASS_CONTEXT_SCHEMA = "pdf_lab.tau_second_pass_context.v1"
PDF_LAB_REVIEW_REQUEST_SCHEMA = "pdf_lab.second_pass.review_request.v1"
PDF_LAB_REVIEW_VALIDATION_SCHEMA = "pdf_lab.second_pass.review_validation.v1"
PDF_LAB_TAU_REVIEW_RECEIPT_SCHEMA = "pdf_lab.tau_second_pass_review_receipt.v1"


def write_pdf_lab_second_pass_review_receipt(
    *,
    contract_path: Path,
    output_path: Path,
    artifact_root: Path | None = None,
    scillm_base_url: str = "http://localhost:4001",
    caller_skill: str = "pdf-lab",
    apply: bool = False,
    auth_token: str | None = None,
    request_timeout_s: int = 900,
    timeout_diagnosis_mode: str = "live_canary",
    timeout_diagnosis_timeout_s: int = 30,
) -> dict[str, Any]:
    """Execute or dry-run a Tau-owned PDF Lab second-pass review contract."""

    started = time.monotonic()
    resolved_contract = contract_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_artifact_root = (
        artifact_root.expanduser().resolve()
        if artifact_root is not None
        else resolved_contract.parent
    )
    alerts: list[dict[str, Any]] = []
    contract = _read_json_object(resolved_contract, alerts, "contract")
    context = contract.get("context") if isinstance(contract.get("context"), Mapping) else {}
    input_artifacts = context.get("input_artifacts") if isinstance(context, Mapping) else {}

    _validate_contract(contract, context, input_artifacts, alerts)
    artifact_manifest = _validate_input_artifacts(
        root=resolved_artifact_root,
        input_artifacts=input_artifacts if isinstance(input_artifacts, Mapping) else {},
        alerts=alerts,
    )
    review_request_path = _artifact_path(
        root=resolved_artifact_root,
        descriptor=(
            input_artifacts.get("review_request_json")
            if isinstance(input_artifacts, Mapping)
            else None
        ),
    )
    review_request = (
        _read_json_object(review_request_path, alerts, "review_request")
        if review_request_path is not None
        else {}
    )
    if review_request and review_request.get("schema") != PDF_LAB_REVIEW_REQUEST_SCHEMA:
        alerts.append(
            _alert(
                "invalid_review_request_schema",
                f"review_request schema must be {PDF_LAB_REVIEW_REQUEST_SCHEMA}",
            )
        )

    run_dir = resolved_output.parent
    scillm_receipt_path = run_dir / "scillm_chat_review_receipt.json"
    response_output_path = run_dir / "review_response.json"
    review_validation_path = run_dir / "review_validation.json"
    if not alerts and review_request_path is not None:
        chat_receipt = write_scillm_chat_review_receipt(
            request_path=review_request_path,
            output_path=scillm_receipt_path,
            response_output_path=response_output_path,
            scillm_base_url=scillm_base_url,
            caller_skill=caller_skill,
            apply=apply,
            auth_token=auth_token,
            request_timeout_s=request_timeout_s,
            timeout_diagnosis_mode=timeout_diagnosis_mode,
            timeout_diagnosis_timeout_s=timeout_diagnosis_timeout_s,
        )
    else:
        chat_receipt = _skipped_chat_receipt(
            apply=apply,
            scillm_base_url=scillm_base_url,
            caller_skill=caller_skill,
        )
    review_validation = _write_review_validation(
        request=review_request,
        response_path=response_output_path,
        output_path=review_validation_path,
    )

    chat_alerts = chat_receipt.get("alerts") if isinstance(chat_receipt, Mapping) else []
    if isinstance(chat_alerts, list):
        alerts.extend(_prefixed_chat_alerts(chat_alerts))
    if review_validation.get("ok") is not True and response_output_path.exists():
        alerts.append(
            _alert(
                "review_validation_failed",
                "review_response.json did not satisfy the exact candidate-id contract",
                errors=[str(error) for error in review_validation.get("errors", [])],
            )
        )

    ok = not alerts and chat_receipt.get("ok") is True and (not apply or review_validation["ok"])
    terminal_result = _terminal_result(
        ok=ok,
        chat_receipt=chat_receipt,
        validation=review_validation,
    )
    receipt = {
        "schema": PDF_LAB_TAU_REVIEW_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "verdict": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": chat_receipt.get("live") is True,
        "provider_live": chat_receipt.get("provider_live") is True,
        "dry_run": not apply,
        "apply_requested": apply,
        "route_owner": "tau",
        "tau_owned_transport": True,
        "model_transport_invoked_by_tau": chat_receipt.get("http_executed") is True,
        "forbidden_transport_owner": "pdf_oxide",
        "dag_id": contract.get("dag_id"),
        "entry_node": contract.get("entry_node"),
        "goal": contract.get("goal"),
        "target": contract.get("target"),
        "page_case": review_request.get("page_case"),
        "tau_work_order_path": str(resolved_contract),
        "tau_work_order_sha256": _sha256_uri(resolved_contract),
        "contract_schema": contract.get("schema"),
        "artifact_root": str(resolved_artifact_root),
        "input_artifacts": artifact_manifest,
        "model_transport_policy": {
            "owner": "tau",
            "surface": "scillm.chat_completions",
            "endpoint": "/v1/chat/completions",
            "request_timeout_s": request_timeout_s,
            "timeout_diagnosis_mode": timeout_diagnosis_mode,
            "timeout_diagnosis_timeout_s": timeout_diagnosis_timeout_s,
            "retry_policy": [
                "retry once without response_format when SciLLM rejects response_format "
                "with 400/404/422",
                "on timeout, optionally run a minimal Tau-owned live canary before page "
                "payload retry",
            ],
            "decomposition": [
                "one Tau work order per PDF Lab page case",
                "one model-ready review_request.json per page case",
                "exactly one finding per expected candidate_id in request order",
            ],
        },
        "model_response_artifact": (
            str(response_output_path) if response_output_path.exists() else None
        ),
        "parseable_review_response_json": review_validation.get("response_parseable") is True,
        "review_validation_artifact": str(review_validation_path),
        "review_validation": review_validation,
        "scillm_chat_review_receipt": (
            str(scillm_receipt_path) if scillm_receipt_path.exists() else None
        ),
        "scillm_chat_review": _chat_receipt_summary(chat_receipt),
        "terminal_result": terminal_result,
        "blocked_reason": None if ok else _blocked_reason(alerts, chat_receipt),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "duration_seconds": round(time.monotonic() - started, 6),
        "created_at": _utc_stamp(),
        "proof_scope": {
            "proves": [
                "Tau accepted and hash-checked a PDF Lab second-pass DAG contract.",
                "Tau, not pdf_oxide, owned the configured SciLLM chat-completions transport.",
                "Tau wrote a terminal PDF Lab receipt naming provider route, retry "
                "policy, timeout policy, and review validation.",
            ],
            "does_not_prove": [
                "The model's semantic judgment is correct.",
                "Any pdf_oxide patch should be applied without downstream project-agent audit.",
                "A dry run invoked live provider transport.",
            ],
        },
    }
    _write_json(resolved_output, receipt)
    return receipt


def _validate_contract(
    contract: Mapping[str, Any],
    context: object,
    input_artifacts: object,
    alerts: list[dict[str, Any]],
) -> None:
    if contract.get("schema") != TAU_DAG_CONTRACT_SCHEMA:
        alerts.append(
            _alert("invalid_contract_schema", f"schema must be {TAU_DAG_CONTRACT_SCHEMA}")
        )
    if contract.get("provider_sensitive") is not True:
        alerts.append(_alert("provider_sensitive_required", "provider_sensitive must be true"))
    if contract.get("requires_provider_route") is not True:
        alerts.append(_alert("provider_route_required", "requires_provider_route must be true"))
    if not isinstance(context, Mapping):
        alerts.append(_alert("missing_pdf_lab_context", "contract.context must be an object"))
    elif context.get("schema") != PDF_LAB_SECOND_PASS_CONTEXT_SCHEMA:
        alerts.append(
            _alert(
                "invalid_pdf_lab_context_schema",
                f"context.schema must be {PDF_LAB_SECOND_PASS_CONTEXT_SCHEMA}",
            )
        )
    if not isinstance(input_artifacts, Mapping):
        alerts.append(
            _alert("missing_input_artifacts", "context.input_artifacts must be an object")
        )
    for key in ("review_request_json", "candidate_presets_json", "page_before_json"):
        if not isinstance(input_artifacts, Mapping) or key not in input_artifacts:
            alerts.append(
                _alert("missing_input_artifact", f"context.input_artifacts.{key} is required")
            )
    boundary = context.get("route_boundary") if isinstance(context, Mapping) else None
    if isinstance(boundary, Mapping):
        if boundary.get("required_owner") != "tau":
            alerts.append(
                _alert("route_owner_not_tau", "route boundary must require Tau ownership")
            )
        if boundary.get("pdf_oxide_direct_model_transport") != "forbidden":
            alerts.append(
                _alert(
                    "pdf_oxide_transport_not_forbidden",
                    "contract must forbid pdf_oxide direct model transport",
                )
            )


def _validate_input_artifacts(
    *,
    root: Path,
    input_artifacts: Mapping[str, Any],
    alerts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for label, descriptor in sorted(input_artifacts.items()):
        path = _artifact_path(root=root, descriptor=descriptor)
        expected = _expected_sha256(descriptor)
        actual = _sha256_uri(path) if path is not None and path.is_file() else None
        item = {
            "label": label,
            "path": str(path) if path is not None else None,
            "expected_sha256": f"sha256:{expected}" if expected else None,
            "actual_sha256": actual,
            "exists": path is not None and path.exists(),
            "bytes": path.stat().st_size if path is not None and path.is_file() else None,
        }
        manifest.append(item)
        if path is None or not path.exists():
            alerts.append(_alert("input_artifact_missing", f"{label} artifact is missing"))
        elif expected and actual != f"sha256:{expected}":
            alerts.append(
                _alert(
                    "input_artifact_hash_mismatch",
                    f"{label} artifact sha256 mismatch",
                    errors=[f"expected sha256:{expected}", f"actual {actual}"],
                )
            )
    return manifest


def _write_review_validation(
    *,
    request: Mapping[str, Any],
    response_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    expected_ids = _expected_candidate_ids(request)
    errors: list[str] = []
    response = _read_json_object(response_path, errors, "review_response")
    seen_ids: list[str] = []
    findings = response.get("candidate_findings") if isinstance(response, Mapping) else None
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, Mapping) and isinstance(finding.get("candidate_id"), str):
                seen_ids.append(str(finding["candidate_id"]))
    elif response_path.exists():
        errors.append("candidate_findings must be a list")
    if response_path.exists() and seen_ids != expected_ids:
        errors.append("seen_candidate_ids must match expected_candidate_ids exactly once in order")
    validation = {
        "schema": PDF_LAB_REVIEW_VALIDATION_SCHEMA,
        "ok": response_path.exists() and not errors,
        "response_parseable": bool(response),
        "candidate_count": len(expected_ids),
        "expected_candidate_ids": expected_ids,
        "seen_candidate_ids": seen_ids,
        "duplicate_seen_candidate_ids": _duplicates(seen_ids),
        "missing_candidate_ids": [
            candidate_id for candidate_id in expected_ids if candidate_id not in seen_ids
        ],
        "unexpected_candidate_ids": [
            candidate_id for candidate_id in seen_ids if candidate_id not in expected_ids
        ],
        "page_status": (
            response.get("page_status") if isinstance(response.get("page_status"), str) else None
        ),
        "page_case": (
            request.get("page_case") if isinstance(request.get("page_case"), Mapping) else None
        ),
        "errors": errors,
    }
    _write_json(output_path, validation)
    return validation


def _expected_candidate_ids(request: Mapping[str, Any]) -> list[str]:
    page_case = request.get("page_case")
    if isinstance(page_case, Mapping) and isinstance(page_case.get("candidate_ids"), list):
        return [item for item in page_case["candidate_ids"] if isinstance(item, str)]
    return []


def _artifact_path(*, root: Path, descriptor: object) -> Path | None:
    if not isinstance(descriptor, Mapping):
        return None
    raw_path = descriptor.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path.expanduser().resolve()
    return (root / path).expanduser().resolve()


def _expected_sha256(descriptor: object) -> str | None:
    if not isinstance(descriptor, Mapping):
        return None
    value = descriptor.get("sha256")
    if not isinstance(value, str) or not value:
        return None
    return value.removeprefix("sha256:")


def _read_json_object(path: Path, errors_or_alerts: list[Any], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors_or_alerts.append(_read_error(label, "file is missing"))
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        errors_or_alerts.append(_read_error(label, f"file is unreadable: {exc}"))
        return {}
    if not isinstance(payload, dict):
        errors_or_alerts.append(_read_error(label, "root must be a JSON object"))
        return {}
    return payload


def _read_error(label: str, message: str) -> Any:
    if label == "review_response":
        return f"{label}_{message}"
    return _alert(f"{label}_unreadable", f"{label} {message}")


def _prefixed_chat_alerts(alerts: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for alert in alerts:
        if not isinstance(alert, Mapping):
            continue
        code = str(alert.get("code") or "scillm_chat_review_alert")
        message = str(alert.get("message") or code)
        result.append(_alert(f"scillm_chat_review:{code}", message))
    return result


def _skipped_chat_receipt(
    *,
    apply: bool,
    scillm_base_url: str,
    caller_skill: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "dry_run": not apply,
        "apply_requested": apply,
        "http_executed": False,
        "scillm_base_url": scillm_base_url.rstrip("/"),
        "caller_skill": caller_skill,
        "alert_codes": ["contract_preflight_failed"],
        "alerts": [
            _alert("contract_preflight_failed", "contract preflight failed before transport")
        ],
    }


def _chat_receipt_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": receipt.get("schema"),
        "ok": receipt.get("ok"),
        "status": receipt.get("status"),
        "live": receipt.get("live"),
        "provider_live": receipt.get("provider_live"),
        "http_executed": receipt.get("http_executed"),
        "http_status": receipt.get("http_status"),
        "timed_out": receipt.get("timed_out"),
        "root_cause_code": receipt.get("root_cause_code"),
        "recommended_next_action": receipt.get("recommended_next_action"),
        "alert_codes": receipt.get("alert_codes"),
    }


def _terminal_result(
    *,
    ok: bool,
    chat_receipt: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> str:
    if not ok:
        root_cause = str(chat_receipt.get("root_cause_code") or "")
        if "auth" in root_cause or "quota" in root_cause or "timeout" in root_cause:
            return "blocked_substrate"
        return "blocked_substrate"
    page_status = validation.get("page_status") or None
    if page_status == "clean":
        return "reviewed_clean"
    return "still_open"


def _blocked_reason(alerts: list[dict[str, Any]], chat_receipt: Mapping[str, Any]) -> str:
    root_cause = chat_receipt.get("root_cause_code")
    if isinstance(root_cause, str) and root_cause:
        return root_cause
    if alerts:
        return str(alerts[0].get("code") or "pdf_lab_review_blocked")
    return "pdf_lab_review_blocked"


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _sha256_uri(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if errors:
        alert["errors"] = errors
    return alert


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
