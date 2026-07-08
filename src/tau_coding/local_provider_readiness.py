"""Local provider readiness receipts for air-gapped Tau demos."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

LOCAL_PROVIDER_READINESS_SCHEMA = "tau.local_provider_readiness_receipt.v1"


def local_provider_readiness_receipt(
    *,
    provider_url: str,
    model: str,
    model_weight_sha256: str | None = None,
    tokenizer_sha256: str | None = None,
    inference_engine: str | None = None,
    timeout_s: float = 5.0,
    airgap_mode: bool = False,
    allow_unavailable_demo: bool = False,
) -> dict[str, Any]:
    """Check local provider reachability and return a fail-closed receipt."""

    checks = [
        _probe(provider_url, "/health", timeout_s=timeout_s),
        _probe(provider_url, "/v1/models", timeout_s=timeout_s),
    ]
    reachable = any(check["ok"] is True for check in checks)
    ok = reachable or allow_unavailable_demo
    errors: list[str] = []
    if not reachable and not allow_unavailable_demo:
        errors.append("local_provider_unreachable")

    return {
        "schema": LOCAL_PROVIDER_READINESS_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": reachable,
        "provider_live": reachable,
        "checked_at": _utc_stamp(),
        "provider_url": provider_url,
        "model": model,
        "airgap_mode": airgap_mode,
        "model_weight_sha256": model_weight_sha256,
        "tokenizer_sha256": tokenizer_sha256,
        "inference_engine": inference_engine,
        "allow_unavailable_demo": allow_unavailable_demo,
        "checks": checks,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau checked a configured local provider endpoint.",
                "Tau recorded the selected local model identity fields.",
            ],
            "does_not_prove": [
                "Model approval for ITAR data.",
                "Model semantic correctness.",
                "Absence of all network egress.",
                "SCIF or ATO certification.",
                "Future provider availability.",
            ],
        },
    }


def write_local_provider_readiness_receipt(
    *,
    provider_url: str,
    model: str,
    out: Path | None = None,
    model_weight_sha256: str | None = None,
    tokenizer_sha256: str | None = None,
    inference_engine: str | None = None,
    timeout_s: float = 5.0,
    airgap_mode: bool = False,
    allow_unavailable_demo: bool = False,
) -> dict[str, Any]:
    """Write a local provider readiness receipt when an output path is supplied."""

    receipt = local_provider_readiness_receipt(
        provider_url=provider_url,
        model=model,
        model_weight_sha256=model_weight_sha256,
        tokenizer_sha256=tokenizer_sha256,
        inference_engine=inference_engine,
        timeout_s=timeout_s,
        airgap_mode=airgap_mode,
        allow_unavailable_demo=allow_unavailable_demo,
    )
    if out is not None:
        resolved_out = out.expanduser().resolve()
        resolved_out.parent.mkdir(parents=True, exist_ok=True)
        receipt["receipt_path"] = str(resolved_out)
        resolved_out.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return receipt


def _probe(provider_url: str, path: str, *, timeout_s: float) -> dict[str, Any]:
    url = urljoin(provider_url.rstrip("/") + "/", path.lstrip("/"))
    try:
        response = httpx.get(url, timeout=timeout_s)
    except httpx.HTTPError as exc:
        return {
            "code": f"provider_http_{path.strip('/').replace('/', '_') or 'root'}",
            "ok": False,
            "url": url,
            "status_code": None,
            "error": str(exc),
        }
    return {
        "code": f"provider_http_{path.strip('/').replace('/', '_') or 'root'}",
        "ok": 200 <= response.status_code < 500,
        "url": url,
        "status_code": response.status_code,
        "error": None,
    }


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
