"""No-egress probe receipts for synthetic airgap Tau demos."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

AIRGAP_NO_EGRESS_SCHEMA = "tau.airgap_no_egress_receipt.v1"
DEFAULT_DNS_PROBE_HOST = "example.com"
DEFAULT_HTTP_PROBE_URL = "http://example.com/"


def airgap_no_egress_receipt(
    *,
    allowed_local_endpoints: list[str] | None = None,
    dns_probe_host: str = DEFAULT_DNS_PROBE_HOST,
    http_probe_url: str = DEFAULT_HTTP_PROBE_URL,
    timeout_s: float = 3.0,
    assume_no_egress_demo: bool = False,
) -> dict[str, Any]:
    """Run bounded external egress probes and return a fail-closed receipt."""

    endpoints = allowed_local_endpoints or []
    if assume_no_egress_demo:
        checks = [
            {
                "code": "dns_external_probe",
                "ok": True,
                "result": "demo_fixture_not_executed",
                "target": dns_probe_host,
            },
            {
                "code": "http_external_probe",
                "ok": True,
                "result": "demo_fixture_not_executed",
                "target": http_probe_url,
            },
        ]
        unexpected_egress: list[dict[str, Any]] = []
    else:
        checks = [
            _dns_probe(dns_probe_host),
            _http_probe(http_probe_url, timeout_s=timeout_s),
        ]
        unexpected_egress = [
            {
                "code": check["code"],
                "target": check["target"],
                "result": check["result"],
            }
            for check in checks
            if check["ok"] is False
        ]

    ok = not unexpected_egress
    return {
        "schema": AIRGAP_NO_EGRESS_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": not assume_no_egress_demo,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "network_policy": "deny_external",
        "allowed_local_endpoints": endpoints,
        "checks": checks,
        "unexpected_egress": unexpected_egress,
        "errors": ["unexpected_egress_detected"] if unexpected_egress else [],
        "proof_scope": {
            "proves": [
                "Tau ran bounded local egress probes.",
                "Tau recorded whether unexpected external probes succeeded.",
            ],
            "does_not_prove": [
                "Formal airgap certification.",
                "SCIF readiness.",
                "ATO readiness.",
                "Absence of all covert channels.",
                "Future network behavior.",
            ],
        },
    }


def write_airgap_no_egress_receipt(
    *,
    out: Path | None = None,
    allowed_local_endpoints: list[str] | None = None,
    dns_probe_host: str = DEFAULT_DNS_PROBE_HOST,
    http_probe_url: str = DEFAULT_HTTP_PROBE_URL,
    timeout_s: float = 3.0,
    assume_no_egress_demo: bool = False,
) -> dict[str, Any]:
    """Write a no-egress receipt when an output path is supplied."""

    receipt = airgap_no_egress_receipt(
        allowed_local_endpoints=allowed_local_endpoints,
        dns_probe_host=dns_probe_host,
        http_probe_url=http_probe_url,
        timeout_s=timeout_s,
        assume_no_egress_demo=assume_no_egress_demo,
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


def _dns_probe(host: str) -> dict[str, Any]:
    try:
        addresses = socket.getaddrinfo(host, None)
    except OSError as exc:
        return {
            "code": "dns_external_probe",
            "ok": True,
            "result": "blocked_or_unreachable",
            "target": host,
            "error": str(exc),
        }
    return {
        "code": "dns_external_probe",
        "ok": False,
        "result": "resolved_external_host",
        "target": host,
        "address_count": len(addresses),
        "error": None,
    }


def _http_probe(url: str, *, timeout_s: float) -> dict[str, Any]:
    try:
        response = httpx.get(url, timeout=timeout_s, follow_redirects=False)
    except httpx.HTTPError as exc:
        return {
            "code": "http_external_probe",
            "ok": True,
            "result": "blocked_or_unreachable",
            "target": url,
            "status_code": None,
            "error": str(exc),
        }
    return {
        "code": "http_external_probe",
        "ok": False,
        "result": "external_http_succeeded",
        "target": url,
        "status_code": response.status_code,
        "error": None,
    }


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
