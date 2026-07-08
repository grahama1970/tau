from pathlib import Path

import httpx

from tau_coding.local_provider_readiness import (
    LOCAL_PROVIDER_READINESS_SCHEMA,
    write_local_provider_readiness_receipt,
)


def test_local_provider_receipt_passes_with_fake_httpx_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        assert timeout == 5.0
        if url.endswith("/health"):
            return httpx.Response(200)
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "get", fake_get)

    out = tmp_path / "receipt.json"
    receipt = write_local_provider_readiness_receipt(
        provider_url="http://127.0.0.1:4001",
        model="local-kimi-k2.6",
        out=out,
        airgap_mode=True,
    )

    assert receipt["schema"] == LOCAL_PROVIDER_READINESS_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["live"] is True
    assert receipt["provider_live"] is True
    assert receipt["provider_url"] == "http://127.0.0.1:4001"
    assert receipt["model"] == "local-kimi-k2.6"
    assert receipt["airgap_mode"] is True
    assert out.exists()


def test_local_provider_receipt_blocks_unreachable_provider(monkeypatch) -> None:
    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)

    receipt = write_local_provider_readiness_receipt(
        provider_url="http://127.0.0.1:4999",
        model="local-kimi-k2.6",
        airgap_mode=True,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["live"] is False
    assert receipt["provider_live"] is False
    assert receipt["errors"] == ["local_provider_unreachable"]


def test_local_provider_receipt_records_model_hash_fields(monkeypatch) -> None:
    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "get", fake_get)

    receipt = write_local_provider_readiness_receipt(
        provider_url="http://127.0.0.1:4001",
        model="local-kimi-k2.6",
        model_weight_sha256="sha256:" + "a" * 64,
        tokenizer_sha256="sha256:" + "b" * 64,
        inference_engine="vllm",
    )

    assert receipt["model_weight_sha256"] == "sha256:" + "a" * 64
    assert receipt["tokenizer_sha256"] == "sha256:" + "b" * 64
    assert receipt["inference_engine"] == "vllm"


def test_local_provider_receipt_has_non_claims(monkeypatch) -> None:
    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "get", fake_get)

    receipt = write_local_provider_readiness_receipt(
        provider_url="http://127.0.0.1:4001",
        model="local-kimi-k2.6",
    )

    non_claims = receipt["proof_scope"]["does_not_prove"]
    assert "Model approval for ITAR data." in non_claims
    assert "Model semantic correctness." in non_claims
    assert "Absence of all network egress." in non_claims
    assert "SCIF or ATO certification." in non_claims
