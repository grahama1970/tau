import pytest

from tau_ai import AnthropicProvider, OpenAICodexProvider, OpenAICompatibleProvider
from tau_coding import provider_runtime
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.provider_config import (
    AnthropicProviderConfig,
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
)
from tau_coding.provider_runtime import OpenAICodexCredentialResolver, create_model_provider


def test_create_model_provider_returns_openai_codex_provider(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")

    provider = create_model_provider(
        OpenAICodexProviderConfig(),
        credential_store=store,
    )

    assert isinstance(provider, OpenAICodexProvider)


def test_create_model_provider_uses_runtime_api_key_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("TAU_RUNTIME_API_KEY", "runtime-key")
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set("stored", "stored-key")

    provider = create_model_provider(
        OpenAICompatibleProviderConfig(
            name="openai",
            credential_name="stored",
            api_key_env="MISSING_API_KEY",
        ),
        credential_store=store,
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.api_key == "runtime-key"


@pytest.mark.anyio
async def test_codex_credential_resolver_uses_runtime_api_key_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("TAU_RUNTIME_API_KEY", "runtime-codex-token")
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "openai-codex",
        OAuthCredential(
            access="stored-access",
            refresh="stored-refresh",
            account_id="stored-account",
            expires=9999999999,
        ),
    )
    resolver = OpenAICodexCredentialResolver(
        OpenAICodexProviderConfig(),
        credential_store=store,
    )

    credentials = await resolver()

    assert credentials.access_token == "runtime-codex-token"
    assert credentials.account_id is None


def test_create_model_provider_maps_codex_reasoning_effort_like_pi(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    provider_config = OpenAICodexProviderConfig(
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
        thinking_models=("gpt-5.5",),
        thinking_parameter="reasoning.effort",
    )

    off_provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="gpt-5.5",
        thinking_level="off",
    )
    minimal_provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="gpt-5.5",
        thinking_level="minimal",
    )
    xhigh_provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="gpt-5.5",
        thinking_level="xhigh",
    )

    assert isinstance(off_provider, OpenAICodexProvider)
    assert isinstance(minimal_provider, OpenAICodexProvider)
    assert isinstance(xhigh_provider, OpenAICodexProvider)
    assert off_provider._config.reasoning_effort is None
    assert minimal_provider._config.reasoning_effort == "low"
    assert xhigh_provider._config.reasoning_effort == "xhigh"


def test_create_model_provider_maps_anthropic_opus_5_adaptive_thinking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    store = FileCredentialStore(tmp_path / "credentials.json")
    provider_config = AnthropicProviderConfig(
        models=("claude-opus-5",),
        default_model="claude-opus-5",
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
        thinking_models=("claude-opus-5",),
        thinking_parameter="anthropic.thinking",
    )

    provider = create_model_provider(
        provider_config,
        credential_store=store,
        model="claude-opus-5",
        thinking_level="xhigh",
    )

    assert isinstance(provider, AnthropicProvider)
    assert provider._config.thinking_mode == "adaptive"
    assert provider._config.thinking_effort == "max"
    assert provider._config.thinking_budget_tokens is None


@pytest.mark.anyio
async def test_openai_codex_credential_resolver_refreshes_expired_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "openai-codex",
        OAuthCredential(
            access="old-access",
            refresh="old-refresh",
            expires=1,
            account_id="old-account",
        ),
    )

    async def fake_refresh(refresh_token: str) -> OAuthCredential:
        assert refresh_token == "old-refresh"
        return OAuthCredential(
            access="new-access",
            refresh="new-refresh",
            expires=9999999999999,
            account_id="new-account",
        )

    monkeypatch.setattr(provider_runtime, "refresh_openai_codex_token", fake_refresh)

    resolver = OpenAICodexCredentialResolver(
        OpenAICodexProviderConfig(),
        credential_store=store,
    )

    credentials = await resolver()

    assert credentials.access_token == "new-access"
    assert credentials.account_id == "new-account"
    assert store.get_oauth("openai-codex") == OAuthCredential(
        access="new-access",
        refresh="new-refresh",
        expires=9999999999999,
        account_id="new-account",
    )
