import socket
from pathlib import Path

import httpx

from tau_coding.airgap_no_egress import (
    AIRGAP_NO_EGRESS_SCHEMA,
    write_airgap_no_egress_receipt,
)


def test_airgap_receipt_passes_when_external_probe_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_getaddrinfo(host: str, port):
        raise socket.gaierror("blocked")

    def fake_get(url: str, *, timeout: float, follow_redirects: bool) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(httpx, "get", fake_get)

    out = tmp_path / "airgap.json"
    receipt = write_airgap_no_egress_receipt(
        out=out,
        allowed_local_endpoints=["127.0.0.1:4001"],
    )

    assert receipt["schema"] == AIRGAP_NO_EGRESS_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["live"] is True
    assert receipt["unexpected_egress"] == []
    assert out.exists()


def test_airgap_receipt_blocks_when_external_probe_succeeds(monkeypatch) -> None:
    def fake_getaddrinfo(host: str, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    def fake_get(url: str, *, timeout: float, follow_redirects: bool) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(httpx, "get", fake_get)

    receipt = write_airgap_no_egress_receipt()

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"] == ["unexpected_egress_detected"]
    assert {item["code"] for item in receipt["unexpected_egress"]} == {
        "dns_external_probe",
        "http_external_probe",
    }


def test_airgap_receipt_records_allowed_local_endpoints(monkeypatch) -> None:
    def fake_getaddrinfo(host: str, port):
        raise socket.gaierror("blocked")

    def fake_get(url: str, *, timeout: float, follow_redirects: bool) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(httpx, "get", fake_get)

    receipt = write_airgap_no_egress_receipt(
        allowed_local_endpoints=["127.0.0.1:4001", "127.0.0.1:8601"],
    )

    assert receipt["allowed_local_endpoints"] == ["127.0.0.1:4001", "127.0.0.1:8601"]


def test_airgap_receipt_demo_fixture_is_explicitly_not_live() -> None:
    receipt = write_airgap_no_egress_receipt(assume_no_egress_demo=True)

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["live"] is False
    assert all(check["result"] == "demo_fixture_not_executed" for check in receipt["checks"])


def test_airgap_receipt_non_claims_present(monkeypatch) -> None:
    def fake_getaddrinfo(host: str, port):
        raise socket.gaierror("blocked")

    def fake_get(url: str, *, timeout: float, follow_redirects: bool) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(httpx, "get", fake_get)

    receipt = write_airgap_no_egress_receipt()

    non_claims = receipt["proof_scope"]["does_not_prove"]
    assert "Formal airgap certification." in non_claims
    assert "SCIF readiness." in non_claims
    assert "ATO readiness." in non_claims
    assert "Absence of all covert channels." in non_claims
