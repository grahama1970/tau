"""Tau-owned SciLLM chat/VLM review executor receipts."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.coding_worker_adapters import (
    _alert,
    _append_scillm_base_url_alerts,
    _artifact_sha256_uri,
    _artifact_size,
    _local_scillm_auth_token,
    _read_json_object,
    _write_json,
)

SCILLM_CHAT_REVIEW_RECEIPT_SCHEMA = "tau.scillm_chat_review_receipt.v1"
PDF_LAB_REVIEW_REQUEST_SCHEMA = "pdf_lab.second_pass.review_request.v1"
PDF_LAB_REVIEW_RESPONSE_SCHEMA = "pdf_lab.second_pass.review_response.v1"
SCILLM_CHAT_COMPLETIONS_ENDPOINT = "/v1/chat/completions"
ALLOWED_PAGE_STATUSES = {"clean", "defect", "unsure", "substrate_blocked"}
TIMEOUT_DIAGNOSIS_MODES = {"off", "live_canary"}


def write_scillm_chat_review_receipt(
    *,
    request_path: Path,
    output_path: Path,
    response_output_path: Path | None = None,
    scillm_base_url: str = "http://localhost:4001",
    caller_skill: str = "tau",
    apply: bool = False,
    auth_token: str | None = None,
    request_timeout_s: int = 120,
    timeout_diagnosis_mode: str = "off",
    timeout_diagnosis_timeout_s: int = 30,
) -> dict[str, Any]:
    """Forward a model-ready chat review request through Tau and write a receipt."""

    started = time.monotonic()
    resolved_request = request_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_response_output = (
        response_output_path.expanduser().resolve()
        if response_output_path is not None
        else resolved_output.with_name("review_response.json")
    )
    raw_response_path = resolved_output.with_name(f"{resolved_output.stem}.raw-response.json")
    error_path = resolved_output.with_name(f"{resolved_output.stem}.error.json")
    alerts: list[dict[str, Any]] = []

    request = _read_json_object(resolved_request, alerts, "review_request")
    if request.get("schema") != PDF_LAB_REVIEW_REQUEST_SCHEMA:
        alerts.append(
            _alert(
                "invalid_review_request_schema",
                f"review request schema must be {PDF_LAB_REVIEW_REQUEST_SCHEMA}",
            )
        )
    payload = _chat_payload_from_review_request(request, alerts)
    _append_scillm_base_url_alerts(scillm_base_url, alerts)
    if request_timeout_s <= 0:
        alerts.append(_alert("invalid_timeout", "request timeout must be a positive integer"))
    if timeout_diagnosis_timeout_s <= 0:
        alerts.append(
            _alert("invalid_timeout", "timeout diagnosis timeout must be a positive integer")
        )
    if timeout_diagnosis_mode not in TIMEOUT_DIAGNOSIS_MODES:
        alerts.append(
            _alert(
                "invalid_timeout_diagnosis_mode",
                f"timeout diagnosis mode must be one of {sorted(TIMEOUT_DIAGNOSIS_MODES)}",
            )
        )

    auth_source = "explicit" if auth_token else "missing"
    effective_auth_token = auth_token
    if not effective_auth_token and _is_local_scillm_base_url(scillm_base_url):
        effective_auth_token, auth_source = _local_scillm_auth_token()
    if apply and not effective_auth_token:
        alerts.append(
            _alert("missing_scillm_auth_token", "apply requires a SciLLM bearer auth token")
        )

    launch_result = _maybe_post_chat_review(
        apply=apply,
        base_url=scillm_base_url,
        payload=payload,
        caller_skill=caller_skill,
        auth_token=effective_auth_token,
        raw_response_path=raw_response_path,
        error_path=error_path,
        alerts=alerts,
        request_timeout_s=request_timeout_s,
        timeout_diagnosis_mode=timeout_diagnosis_mode,
        timeout_diagnosis_timeout_s=timeout_diagnosis_timeout_s,
    )
    parsed_response = launch_result.get("parsed_response")
    if isinstance(parsed_response, dict):
        _write_json(resolved_response_output, parsed_response)
    elif (
        apply
        and launch_result["http_executed"]
        and not launch_result["timed_out"]
        and launch_result.get("body_present")
    ):
        alerts.append(
            _alert(
                "review_response_not_parseable",
                "SciLLM response content did not contain a parseable JSON object",
            )
        )

    validation_errors = (
        _validate_pdf_lab_review_response(parsed_response, request) if isinstance(parsed_response, dict) else []
    )
    for error in validation_errors:
        alerts.append(_alert("invalid_review_response", error))

    ok = not alerts
    payload_redaction = _redacted_payload_summary(payload)
    receipt = {
        "schema": SCILLM_CHAT_REVIEW_RECEIPT_SCHEMA,
        "created_at": _utc_stamp(),
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": launch_result["http_executed"],
        "provider_live": bool(ok and launch_result["http_executed"]),
        "dry_run": not apply,
        "apply_requested": apply,
        "http_executed": launch_result["http_executed"],
        "launch_skipped": launch_result["launch_skipped"],
        "http_status": launch_result["http_status"],
        "timed_out": launch_result["timed_out"],
        "request_timeout_s": request_timeout_s,
        "timeout_diagnosis_mode": timeout_diagnosis_mode,
        "timeout_diagnosis_timeout_s": timeout_diagnosis_timeout_s,
        "caller_skill": caller_skill,
        "surface": "scillm.chat_completions",
        "endpoint": SCILLM_CHAT_COMPLETIONS_ENDPOINT,
        "url": f"{scillm_base_url.rstrip('/')}{SCILLM_CHAT_COMPLETIONS_ENDPOINT}",
        "scillm_base_url": scillm_base_url.rstrip("/"),
        "headers": {
            "authorization": ("REDACTED" if effective_auth_token else "REDACTED_REQUIRED"),
            "authorization_source": auth_source,
            "x_caller_skill": caller_skill,
            "content_type": "application/json",
        },
        "review_request_path": str(resolved_request),
        "review_request_sha256": _artifact_sha256_uri(resolved_request),
        "review_request_bytes": _artifact_size(resolved_request),
        "response_output_path": str(resolved_response_output),
        "response_output_sha256": _artifact_sha256_uri(resolved_response_output),
        "response_output_bytes": _artifact_size(resolved_response_output),
        "raw_response_path": str(raw_response_path) if raw_response_path.exists() else None,
        "raw_response_sha256": _artifact_sha256_uri(raw_response_path),
        "raw_response_bytes": _artifact_size(raw_response_path),
        "error_path": str(error_path) if error_path.exists() else None,
        "error_sha256": _artifact_sha256_uri(error_path),
        "error_bytes": _artifact_size(error_path),
        "model": payload.get("model"),
        "response_format": payload.get("response_format"),
        "scillm_metadata": payload.get("scillm_metadata"),
        "request_payload": payload_redaction,
        "root_cause_code": launch_result.get("root_cause_code"),
        "root_cause_basis": launch_result.get("root_cause_basis"),
        "recommended_next_action": launch_result.get("recommended_next_action"),
        "timeout_diagnosis": launch_result.get("timeout_diagnosis"),
        "parsed_response_schema": (
            parsed_response.get("schema") if isinstance(parsed_response, dict) else None
        ),
        "parsed_page_status": (
            parsed_response.get("page_status") if isinstance(parsed_response, dict) else None
        ),
        "parsed_candidate_finding_count": (
            len(parsed_response.get("candidate_findings", []))
            if isinstance(parsed_response.get("candidate_findings") if isinstance(parsed_response, dict) else None, list)
            else None
        ),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "duration_seconds": round(time.monotonic() - started, 6),
    }
    _write_json(resolved_output, receipt)
    return receipt


def _chat_payload_from_review_request(
    request: Mapping[str, Any], alerts: list[dict[str, Any]]
) -> dict[str, Any]:
    payload = request.get("scillm_payload")
    if not isinstance(payload, Mapping):
        alerts.append(_alert("missing_scillm_payload", "review request must include scillm_payload"))
        return {}
    payload_dict = dict(payload)
    if not isinstance(payload_dict.get("model"), str) or not payload_dict.get("model"):
        model = request.get("model")
        if isinstance(model, str) and model:
            payload_dict["model"] = model
        else:
            alerts.append(_alert("missing_model", "scillm_payload.model is required"))
    if not isinstance(payload_dict.get("messages"), list) or not payload_dict.get("messages"):
        alerts.append(_alert("missing_messages", "scillm_payload.messages must be a non-empty list"))
    if "temperature" not in payload_dict:
        payload_dict["temperature"] = 0
    if "response_format" not in payload_dict and isinstance(request.get("response_format"), Mapping):
        payload_dict["response_format"] = dict(request["response_format"])
    if "scillm_metadata" not in payload_dict and isinstance(request.get("scillm_metadata"), Mapping):
        payload_dict["scillm_metadata"] = dict(request["scillm_metadata"])
    return payload_dict


def _maybe_post_chat_review(
    *,
    apply: bool,
    base_url: str,
    payload: Mapping[str, Any],
    caller_skill: str,
    auth_token: str | None,
    raw_response_path: Path,
    error_path: Path,
    alerts: list[dict[str, Any]],
    request_timeout_s: int,
    timeout_diagnosis_mode: str,
    timeout_diagnosis_timeout_s: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "http_executed": False,
        "launch_skipped": True,
        "http_status": None,
        "timed_out": False,
        "parsed_response": None,
        "body_present": False,
        "root_cause_code": None,
        "root_cause_basis": None,
        "recommended_next_action": None,
        "timeout_diagnosis": None,
    }
    if not apply or alerts:
        return result
    result["http_executed"] = True
    result["launch_skipped"] = False
    first = _post_json(
        url=f"{base_url.rstrip('/')}{SCILLM_CHAT_COMPLETIONS_ENDPOINT}",
        payload=dict(payload),
        caller_skill=caller_skill,
        auth_token=str(auth_token),
        timeout_s=request_timeout_s,
    )
    selected = first
    if (
        payload.get("response_format") is not None
        and first["status"] == "HTTP_ERROR"
        and int(first.get("http_status") or 0) in {400, 404, 422}
    ):
        retry_payload = dict(payload)
        retry_payload.pop("response_format", None)
        selected = _post_json(
            url=f"{base_url.rstrip('/')}{SCILLM_CHAT_COMPLETIONS_ENDPOINT}",
            payload=retry_payload,
            caller_skill=caller_skill,
            auth_token=str(auth_token),
            timeout_s=request_timeout_s,
        )
        selected["response_format_retry_reason"] = _safe_response_excerpt(first)

    result["http_status"] = selected.get("http_status")
    result["timed_out"] = bool(selected.get("timed_out"))
    result["body_present"] = bool(selected.get("body_text"))
    if selected["status"] == "TIMEOUT":
        alerts.append(_alert("scillm_chat_review_timeout", "SciLLM chat review timed out"))
        result["root_cause_code"] = "scillm_chat_review_request_timeout"
        result["root_cause_basis"] = "primary request timed out before a response body was available"
        result["recommended_next_action"] = (
            "rerun with --timeout-diagnosis-mode live_canary before changing the caller payload"
        )
        if timeout_diagnosis_mode == "live_canary":
            diagnosis = _run_timeout_canary(
                base_url=base_url,
                model=str(payload.get("model") or "vlm-free2"),
                caller_skill=caller_skill,
                auth_token=str(auth_token),
                timeout_s=timeout_diagnosis_timeout_s,
            )
            result["timeout_diagnosis"] = diagnosis
            if diagnosis.get("status") == "TIMEOUT":
                result["root_cause_code"] = "scillm_chat_review_service_unresponsive"
                result["root_cause_basis"] = (
                    "primary request and minimal canary request both timed out before a response body was available"
                )
                result["recommended_next_action"] = (
                    "do not retry PDF Lab page payloads; repair or restart the SciLLM/Ollama route "
                    "until a minimal Tau canary returns PASS, then rerun the page request through Tau"
                )
                alerts.append(
                    _alert(
                        "scillm_chat_review_service_unresponsive",
                        "SciLLM chat review service did not answer a minimal canary request within the diagnosis timeout",
                    )
                )
            elif diagnosis.get("status") == "PASS":
                result["root_cause_code"] = "scillm_chat_review_request_exceeded_live_budget"
                result["root_cause_basis"] = (
                    "primary request timed out but minimal canary request returned successfully"
                )
                result["recommended_next_action"] = (
                    "keep model transport in Tau and reduce the page review unit or request budget before retrying"
                )
    elif selected["status"] != "PASS" or int(selected.get("http_status") or 0) >= 400:
        alerts.append(
            _alert(
                "scillm_chat_review_http_error",
                "SciLLM chat review request failed",
                errors=[str(selected.get("error") or selected.get("status"))],
            )
        )

    if selected.get("body_text"):
        _write_text(raw_response_path, str(selected["body_text"]))
    if selected.get("error") or selected["status"] != "PASS":
        _write_json(error_path, _safe_response_excerpt(selected))
    decoded = _loads_object(str(selected.get("body_text") or ""))
    content = _message_content(decoded) if isinstance(decoded, Mapping) else ""
    parsed = _parse_json_object(content)
    result["parsed_response"] = parsed
    result["response_format_retry_reason"] = selected.get("response_format_retry_reason")
    return result


def _run_timeout_canary(
    *,
    base_url: str,
    model: str,
    caller_skill: str,
    auth_token: str,
    timeout_s: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Return this exact JSON object only: "
                    '{"schema":"pdf_lab.second_pass.review_response.v1",'
                    '"page_status":"clean","candidate_findings":['
                    '{"candidate_id":"cand:p9999:0000:text","status":"clean",'
                    '"evidence":"canary","rationale":"canary",'
                    '"suggested_fix_surface":"none"}],"page_rationale":"canary"}'
                ),
            }
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "scillm_metadata": {
            "batch_id": "pdf-lab-second-pass-timeout-canary",
            "case_id": "page_case_9999_p9999",
            "item_id": "page_case_9999_p9999:timeout-canary",
            "request_sha256": "timeout-canary",
        },
    }
    response = _post_json(
        url=f"{base_url.rstrip('/')}{SCILLM_CHAT_COMPLETIONS_ENDPOINT}",
        payload=payload,
        caller_skill=caller_skill,
        auth_token=auth_token,
        timeout_s=timeout_s,
    )
    return _safe_response_excerpt(response)


def _post_json(
    *,
    url: str,
    payload: dict[str, Any],
    caller_skill: str,
    auth_token: str,
    timeout_s: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "X-Caller-Skill": caller_skill,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return {
                "status": "PASS",
                "http_status": response.status,
                "body_text": response.read().decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": "HTTP_ERROR",
            "http_status": exc.code,
            "body_text": exc.read().decode("utf-8", "replace"),
            "error": str(exc),
        }
    except (TimeoutError, socket.timeout) as exc:
        return {
            "status": "TIMEOUT",
            "http_status": None,
            "body_text": "",
            "error": str(exc),
            "timed_out": True,
        }
    except urllib.error.URLError as exc:
        timed_out = isinstance(exc.reason, socket.timeout)
        return {
            "status": "TIMEOUT" if timed_out else "NETWORK_ERROR",
            "http_status": None,
            "body_text": "",
            "error": str(exc),
            "timed_out": timed_out,
        }


def _validate_pdf_lab_review_response(
    parsed: Mapping[str, Any], request: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    if parsed.get("schema") != PDF_LAB_REVIEW_RESPONSE_SCHEMA:
        errors.append(f"response schema must be {PDF_LAB_REVIEW_RESPONSE_SCHEMA}")
    page_status = parsed.get("page_status")
    if page_status not in ALLOWED_PAGE_STATUSES:
        errors.append("page_status must be one allowed status")
    findings = parsed.get("candidate_findings")
    if not isinstance(findings, list):
        return [*errors, "candidate_findings must be a list"]

    expected_ids = _expected_candidate_ids(request)
    actual_ids: list[str] = []
    candidate_statuses: list[str] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            errors.append(f"candidate_findings[{index}] must be an object")
            continue
        candidate_id = finding.get("candidate_id")
        status = finding.get("status")
        if not isinstance(candidate_id, str):
            errors.append(f"candidate_findings[{index}].candidate_id must be a string")
        else:
            actual_ids.append(candidate_id)
        if status not in ALLOWED_PAGE_STATUSES:
            errors.append(f"candidate_findings[{index}].status must be one allowed status")
        elif isinstance(status, str):
            candidate_statuses.append(status)
        for required_field in ("evidence", "rationale"):
            value = finding.get(required_field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"candidate_findings[{index}].{required_field} must be non-empty")

    if expected_ids and actual_ids != expected_ids:
        errors.append("candidate_findings candidate_id values must exactly match request order")
    if page_status == "clean" and any(status != "clean" for status in candidate_statuses):
        errors.append("page_status clean requires every candidate finding status to be clean")
    if page_status == "defect" and "defect" not in candidate_statuses:
        errors.append("page_status defect requires at least one defect finding")
    if page_status == "substrate_blocked":
        if "substrate_blocked" not in candidate_statuses:
            errors.append("page_status substrate_blocked requires at least one substrate_blocked finding")
        if any(status not in {"clean", "substrate_blocked"} for status in candidate_statuses):
            errors.append("page_status substrate_blocked allows only clean or substrate_blocked findings")
    page_rationale = parsed.get("page_rationale")
    if not isinstance(page_rationale, str) or not page_rationale.strip():
        errors.append("page_rationale must be non-empty")
    return errors


def _expected_candidate_ids(request: Mapping[str, Any]) -> list[str]:
    page_case = request.get("page_case")
    if isinstance(page_case, Mapping) and isinstance(page_case.get("candidate_ids"), list):
        return [item for item in page_case["candidate_ids"] if isinstance(item, str)]
    payload = request.get("scillm_metadata")
    if isinstance(payload, Mapping) and isinstance(payload.get("candidate_ids"), list):
        return [item for item in payload["candidate_ids"] if isinstance(item, str)]
    return []


def _message_content(decoded: Mapping[str, Any] | None) -> str:
    if not isinstance(decoded, Mapping):
        return ""
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                _content_part_text(part) for part in content if _content_part_text(part)
            )
    text = choices[0].get("text")
    return text if isinstance(text, str) else ""


def _content_part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, Mapping):
        for key in ("text", "content"):
            value = part.get(key)
            if isinstance(value, str):
                return value
    return ""


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = _strip_fence(text.strip())
    decoded = _loads_object(stripped)
    if isinstance(decoded, dict):
        return decoded
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    decoded = _loads_object(stripped[start : end + 1])
    return decoded if isinstance(decoded, dict) else None


def _strip_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _loads_object(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def _redacted_payload_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": payload.get("model"),
        "message_count": len(payload.get("messages", [])) if isinstance(payload.get("messages"), list) else 0,
        "messages": "<redacted-review-request-messages>",
        "response_format": payload.get("response_format"),
        "scillm_metadata": payload.get("scillm_metadata"),
        "temperature": payload.get("temperature"),
    }


def _safe_response_excerpt(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": response.get("status"),
        "http_status": response.get("http_status"),
        "error": response.get("error"),
        "timed_out": response.get("timed_out", False),
        "body_excerpt": str(response.get("body_text") or "")[:2000],
        "response_format_retry_reason": response.get("response_format_retry_reason"),
    }


def _write_text(path: Path, text: str) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")


def _is_local_scillm_base_url(base_url: str) -> bool:
    return base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
