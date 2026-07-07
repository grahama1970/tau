"""Local self-hosted Tau API surface."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from tau_coding.compliance_package import build_compliance_evidence_package
from tau_coding.memory_evidence_gate import (
    evaluate_memory_evidence_gate,
    write_memory_evidence_gate_receipts,
)
from tau_coding.policy_profile import write_zero_trust_preflight_receipt
from tau_coding.project_dag import run_project_dag_contract
from tau_coding.run_status import build_run_status

SERVER_ROUTE_RECEIPT_SCHEMA = "tau.server_route_receipt.v1"

DoctorHandler = Callable[[], dict[str, Any]]


def route_request(
    *,
    method: str,
    target: str,
    body: bytes = b"",
    doctor_handler: DoctorHandler | None = None,
) -> tuple[int, dict[str, Any]]:
    """Route one local API request and return an HTTP status plus JSON payload."""

    parsed = urlparse(target)
    path = parsed.path.rstrip("/") or "/"
    try:
        payload = _decode_body(body)
        if method == "GET" and path == "/health":
            return 200, _health_payload()
        if method == "POST" and path == "/doctor":
            doctor_payload = doctor_handler() if doctor_handler is not None else _health_payload()
            return _status_for_payload(doctor_payload), doctor_payload
        if method == "POST" and path == "/zero-trust/preflight":
            return _zero_trust_preflight(payload)
        if method == "POST" and path == "/memory-evidence/preflight":
            return _memory_evidence_preflight(payload)
        if method == "POST" and path == "/dag/run":
            return _dag_run(payload)
        run_dir, suffix = _parse_run_path(path)
        if method == "GET" and run_dir is not None and suffix == "":
            return _run_summary(run_dir)
        if method == "GET" and run_dir is not None and suffix == "status":
            return _run_status(run_dir)
        if method == "GET" and run_dir is not None and suffix == "receipts":
            return _run_receipts(run_dir)
        if method == "POST" and run_dir is not None and suffix == "compliance-package":
            return _run_compliance_package(run_dir, payload)
        return 404, _error_payload("not_found", f"unknown route: {method} {path}")
    except Exception as exc:  # pragma: no cover - defensive API boundary
        return 500, _error_payload("internal_error", str(exc))


def serve_tau_api(
    *,
    host: str,
    port: int,
    doctor_handler: DoctorHandler | None = None,
) -> None:
    """Serve the local Tau API until interrupted."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
            self._handle()

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
            self._handle()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            request_body = self.rfile.read(length) if length else b""
            status, response = route_request(
                method=self.command,
                target=self.path,
                body=request_body,
                doctor_handler=doctor_handler,
            )
            encoded = json.dumps(response, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()


def _zero_trust_preflight(payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    policy_profile = _required_path(payload, "policy_profile")
    receipt = write_zero_trust_preflight_receipt(
        policy_profile_path=policy_profile,
        data_boundary_path=_optional_path(payload, "data_boundary"),
        dag_contract_path=_optional_path(payload, "dag_contract"),
        receipt_path=_optional_path(payload, "receipt"),
    )
    return _status_for_payload(receipt), receipt


def _memory_evidence_preflight(payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    intent_receipt, evidence_receipt = evaluate_memory_evidence_gate(
        policy_profile=_mapping_or_none(payload.get("policy_profile")),
        data_boundary=_mapping_or_none(payload.get("data_boundary")),
        memory_intent=_mapping_or_none(payload.get("memory_intent")),
        evidence_case=_mapping_or_none(payload.get("evidence_case")),
    )
    receipt_dir = _optional_path(payload, "receipt_dir")
    if receipt_dir is not None:
        intent_receipt, evidence_receipt = write_memory_evidence_gate_receipts(
            receipt_dir=receipt_dir,
            intent_receipt=intent_receipt,
            evidence_receipt=evidence_receipt,
        )
    ok = intent_receipt.get("ok") is True and evidence_receipt.get("ok") is True
    response = {
        "schema": SERVER_ROUTE_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "route": "/memory-evidence/preflight",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "memory_intent_gate_receipt": intent_receipt,
        "evidence_case_gate_receipt": evidence_receipt,
        "proof_scope": {
            "proves": [
                "Tau evaluated memory intent and evidence-case gate inputs through the local API."
            ],
            "does_not_prove": [
                "Memory facts are true.",
                "The evidence case is sufficient for closure.",
                "ITAR compliance.",
                "Provider/model semantic quality.",
            ],
        },
    }
    return _status_for_payload(response), response


def _dag_run(payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    contract_path = _required_path(payload, "contract_path")
    receipt_dir = _optional_path(payload, "receipt_dir")
    agents_root = _optional_path(payload, "agents_root") or Path("agents")
    command_spec_root = _optional_path(payload, "command_spec_root")
    scheduler = str(payload.get("scheduler") or "handoff-loop")
    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=receipt_dir,
        agents_root=agents_root,
        command_spec_root=command_spec_root,
        scheduler=scheduler,
    )
    return _status_for_payload(receipt), receipt


def _run_summary(run_dir: Path) -> tuple[int, dict[str, Any]]:
    status = build_run_status(run_dir)
    summary = {
        "schema": SERVER_ROUTE_RECEIPT_SCHEMA,
        "ok": status.get("ok") is True,
        "status": status.get("status"),
        "route": "/runs/{id}",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "run_dir": str(run_dir.expanduser().resolve()),
        "detected_type": status.get("detected_type"),
        "artifact_count": len(status.get("artifacts", {})),
        "coding_evidence_count": status.get("coding_evidence", {}).get("receipt_count", 0),
        "missing_required_artifacts": status.get("missing_required_artifacts", []),
        "proof_scope": {
            "proves": ["Tau summarized one local run directory through the local API."],
            "does_not_prove": [
                "New provider execution.",
                "Production deployment readiness.",
                "Provider/model semantic quality.",
            ],
        },
    }
    return _status_for_payload(summary), summary


def _run_status(run_dir: Path) -> tuple[int, dict[str, Any]]:
    status = build_run_status(run_dir)
    return _status_for_payload(status), status


def _run_receipts(run_dir: Path) -> tuple[int, dict[str, Any]]:
    resolved = run_dir.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        return 404, _error_payload("missing_run_dir", f"run directory not found: {resolved}")
    receipts = [
        {
            "path": str(path),
            "relative_path": str(path.relative_to(resolved)),
            "bytes": path.stat().st_size,
        }
        for path in sorted(resolved.rglob("*.json"))
        if _looks_like_receipt(path)
    ]
    payload = {
        "schema": SERVER_ROUTE_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "route": "/runs/{id}/receipts",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "run_dir": str(resolved),
        "receipt_count": len(receipts),
        "receipts": receipts,
    }
    return 200, payload


def _run_compliance_package(
    run_dir: Path,
    payload: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    out_dir = _required_path(payload, "out")
    receipt = build_compliance_evidence_package(
        run_dir=run_dir,
        out_dir=out_dir,
        force=payload.get("force") is True,
    )
    return _status_for_payload(receipt), receipt


def _parse_run_path(path: str) -> tuple[Path | None, str]:
    prefix = "/runs/"
    if not path.startswith(prefix):
        return None, ""
    remainder = path[len(prefix) :]
    if not remainder:
        return None, ""
    if "/" in remainder:
        encoded_run, suffix = remainder.split("/", 1)
    else:
        encoded_run, suffix = remainder, ""
    return Path(unquote(encoded_run)), suffix.rstrip("/")


def _health_payload() -> dict[str, Any]:
    return {
        "schema": "tau.server_health.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "proof_scope": {
            "proves": ["The local Tau API route handler is reachable."],
            "does_not_prove": [
                "Production deployment readiness.",
                "Provider/model semantic quality.",
                "Sandbox enforcement.",
            ],
        },
    }


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {
        "schema": SERVER_ROUTE_RECEIPT_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "error": {"code": code, "message": message},
    }


def _decode_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("request body must be a JSON object")
    return payload


def _status_for_payload(payload: Mapping[str, Any]) -> int:
    return 200 if payload.get("ok") is True else 400


def _required_path(payload: Mapping[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{key} is required")
    return Path(value).expanduser().resolve()


def _optional_path(payload: Mapping[str, Any], key: str) -> Path | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{key} must be a non-empty string when provided")
    return Path(value).expanduser().resolve()


def _mapping_or_none(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _looks_like_receipt(path: Path) -> bool:
    if "receipt" in path.name:
        return True
    payload = _read_optional_json(path)
    return isinstance(payload.get("schema"), str) and "receipt" in str(payload["schema"])


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
