"""Embry-OS / Sparta Explorer synthetic airgap demo integration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from tau_coding.demo_airgap_itar import run_demo_airgap_itar_basic

EMBRY_SERVICE_READINESS_SCHEMA = "tau.embry_os_service_readiness_receipt.v1"
EMBRY_SPARTA_DEMO_RECEIPT_SCHEMA = "tau.embry_sparta_airgap_demo_receipt.v1"


def write_embry_os_service_readiness_receipt(
    *,
    memory_url: str,
    scillm_url: str,
    out: Path,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Check configured local Embry-OS service endpoints."""

    resolved_out = out.expanduser().resolve()
    services = {
        "memory": _service_probe(memory_url, timeout_s=timeout_s),
        "scillm": _service_probe(scillm_url, timeout_s=timeout_s),
    }
    errors = [
        f"{name}_unreachable" for name, service in services.items() if service["reachable"] is False
    ]
    ok = not errors
    receipt = {
        "schema": EMBRY_SERVICE_READINESS_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": services["scillm"]["reachable"],
        "services": services,
        "errors": errors,
        "checked_at": _utc_stamp(),
        "proof_scope": {
            "proves": [
                "Tau checked configured local Embry-OS service endpoints.",
            ],
            "does_not_prove": [
                "Service semantic correctness.",
                "Production readiness.",
                "ATO or SCIF certification.",
            ],
        },
    }
    resolved_out.parent.mkdir(parents=True, exist_ok=True)
    resolved_out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def run_demo_embry_sparta_airgap(
    *,
    out: Path,
    memory_url: str,
    scillm_url: str,
    model: str,
    sparta_contract_out: Path | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Run the Embry/Sparta integration demo when local services are reachable."""

    run_dir = out.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    readiness = write_embry_os_service_readiness_receipt(
        memory_url=memory_url,
        scillm_url=scillm_url,
        out=run_dir / "embry-os-service-readiness-receipt.json",
        timeout_s=timeout_s,
    )
    if readiness["ok"] is not True:
        receipt = _demo_receipt(run_dir=run_dir, readiness=readiness, demo_receipt=None)
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt

    demo_receipt = run_demo_airgap_itar_basic(
        out=run_dir,
        provider_url=scillm_url,
        model=model,
        live_provider=True,
        live_airgap_probe=False,
    )
    # Re-write service readiness after the inner demo recreates the run directory contents.
    _write_json(run_dir / "embry-os-service-readiness-receipt.json", readiness)
    posture_path = run_dir / "sparta-posture-contract.json"
    if sparta_contract_out is not None:
        target = sparta_contract_out.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(posture_path.read_text(encoding="utf-8"), encoding="utf-8")
    receipt = _demo_receipt(run_dir=run_dir, readiness=readiness, demo_receipt=demo_receipt)
    _write_json(run_dir / "embry-sparta-demo-receipt.json", receipt)
    return receipt


def _service_probe(url: str, *, timeout_s: float) -> dict[str, Any]:
    health_url = urljoin(url.rstrip("/") + "/", "health")
    try:
        response = httpx.get(health_url, timeout=timeout_s)
    except httpx.HTTPError as exc:
        return {
            "url": url,
            "health_url": health_url,
            "reachable": False,
            "status_code": None,
            "error": str(exc),
        }
    return {
        "url": url,
        "health_url": health_url,
        "reachable": 200 <= response.status_code < 500,
        "status_code": response.status_code,
        "error": None,
    }


def _demo_receipt(
    *,
    run_dir: Path,
    readiness: dict[str, Any],
    demo_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    ok = readiness.get("ok") is True and bool(demo_receipt and demo_receipt.get("ok") is True)
    return {
        "schema": EMBRY_SPARTA_DEMO_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": readiness.get("provider_live"),
        "run_dir": str(run_dir),
        "embry_os_service_readiness": str(run_dir / "embry-os-service-readiness-receipt.json"),
        "sparta_posture_contract": (
            str(run_dir / "sparta-posture-contract.json") if demo_receipt else None
        ),
        "demo_verdict": demo_receipt.get("demo_verdict") if demo_receipt else None,
        "gate": demo_receipt.get("gate") if demo_receipt else "local_service_readiness_failed",
        "errors": readiness.get("errors", []),
        "non_claims": [
            "Synthetic data only.",
            "Does not prove ITAR compliance.",
            "Does not prove service semantic correctness.",
            "Does not prove production readiness.",
            "Does not prove ATO or SCIF certification.",
        ],
        "created_at": _utc_stamp(),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
