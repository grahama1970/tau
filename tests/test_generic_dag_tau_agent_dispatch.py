"""tau#340: `tau run` must dispatch tau_agent nodes through the native adapter.

Before the fix, generic_dag validated the ``tau_agent`` key, left ``command=()``,
and ``_run_node`` fell into the legacy command runner, which failed with
``local runtime command must contain non-empty arguments`` without any
provider or tool execution. These tests pin the CLI path, the preflight
rejections that must never reach a provider, and cancellation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_runtime import native_agent_dispatch as dispatch
from tau_coding.dag_runtime.agent_events import load_agent_events
from tau_coding.dag_runtime.run_store import SqliteDagRunStore

DISCOVERY = {
    "profiles": [
        {
            "schema": "scillm.transport_profile.v1",
            "id": "fixture-model-turn",
            "provider": "fixture",
            "model": "fixture-model",
            "mode": "model_turn",
            "capabilities": ["tool_calling", "structured_events", "streaming", "cancellation"],
            "tags": ["review"],
        }
    ],
    "readiness": {"fixture-model-turn": "transport_live_ready"},
}


def _requirement(
    preferences: list[str] | None = None, caps: list[str] | None = None
) -> dict[str, Any]:
    return {
        "schema": "tau.agent_requirement.v1",
        "role": "review",
        "harness": "tau_native_agent_loop",
        "profile_preferences": preferences or ["fixture-model-turn"],
        "required_transport_capabilities": caps
        if caps is not None
        else ["tool_calling", "structured_events"],
        "workspace": {"mode": "read_only", "allowed_paths": ["notes/**"]},
        "required_evidence": ["tool_effect_receipt"],
        "fallback_policy": {"allowed": True, "prohibit_capability_downgrade": True},
    }


def _spec(tmp_path: Path, **agent_overrides: Any) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "notes" / "nonce.txt").write_text("nonce-fixture-4242\n", encoding="utf-8")
    tau_agent: dict[str, Any] = {
        "prompt": "Read notes/nonce.txt with the read tool and reply with the nonce.",
        "role": "reviewer",
        "agent_requirement": _requirement(),
        "allowed_tools": ["read"],
        "allowed_paths": ["notes/**"],
        "cwd": str(workspace),
        "max_turns": 3,
        "max_tool_calls": 2,
        "required_evidence": ["tool_effect_receipt"],
    }
    tau_agent.update(agent_overrides)
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "tau340-dispatch",
        "run_dir": str(tmp_path / "run"),
        "nodes": [
            {
                "node_id": "reviewer",
                "role": "reviewer",
                "tau_agent": tau_agent,
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(tmp_path / "receipts" / "reviewer.json"),
                "timeout_seconds": 30,
                "max_attempts": 1,
            }
        ],
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return path


class _FixtureProviderFactory:
    """Stands in for SciLLM transport construction; counts provider builds."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, selection: dict[str, Any], correlation: dict[str, Any]) -> Any:
        from tau_agent import AssistantMessage, ToolCall
        from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent

        self.calls += 1
        tool_turn = [
            ProviderResponseStartEvent(model="fixture-model"),
            ProviderResponseEndEvent(
                message=AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="read", arguments={"path": "notes/nonce.txt"})
                    ],
                ),
                finish_reason="tool_calls",
            ),
        ]
        text_turn = [
            ProviderResponseStartEvent(model="fixture-model"),
            ProviderResponseEndEvent(
                message=AssistantMessage(content="The nonce is nonce-fixture-4242."),
                finish_reason="stop",
            ),
        ]
        return FakeProvider([tool_turn, text_turn])


@pytest.fixture
def fixture_transport(monkeypatch: pytest.MonkeyPatch) -> _FixtureProviderFactory:
    factory = _FixtureProviderFactory()
    monkeypatch.setattr(dispatch, "discover_transport_profiles", lambda requirement=None: DISCOVERY)
    monkeypatch.setattr(dispatch, "build_transport_provider", factory)
    return factory


def _run_cli(spec_path: Path) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(app, ["run", str(spec_path)])
    receipt_path = spec_path.parent / "run" / "run-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
    return result.exit_code, receipt


def _node(receipt: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in receipt["nodes"] if item["node_id"] == "reviewer")


def test_discovery_scopes_live_readiness_to_requirement_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _Client:
        calls: list[dict[str, Any]] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def get(self, url: str, **kwargs: Any) -> _Response:
            self.calls.append({"url": url, "params": kwargs.get("params")})
            if url.endswith("/profiles"):
                return _Response(
                    {
                        "profiles": [
                            {
                                **DISCOVERY["profiles"][0],
                                "id": "preferred-model-turn",
                                "fallbacks": ["fallback-model-turn"],
                            },
                            {**DISCOVERY["profiles"][0], "id": "fallback-model-turn"},
                            {**DISCOVERY["profiles"][0], "id": "unrelated-vlm"},
                        ]
                    }
                )
            profile = kwargs["params"]["profile"]
            return _Response(
                {
                    "schema": "scillm.transport_readiness.v1",
                    "live": True,
                    "readiness": [{"profile": profile, "state": "transport_live_ready"}],
                }
            )

    _Client.calls = []
    monkeypatch.setattr(dispatch, "scillm_api_key", lambda: "test-key")
    monkeypatch.setattr(dispatch, "scillm_base_url", lambda: "http://scillm.test")
    monkeypatch.setattr("httpx.Client", _Client)

    discovery = dispatch.discover_transport_profiles(
        requirement=_requirement(preferences=["preferred-model-turn"])
    )

    readiness_params = [item["params"] for item in _Client.calls if item["params"]]
    assert readiness_params == [
        {"live": "true", "profile": "preferred-model-turn"},
        {"live": "true", "profile": "fallback-model-turn"},
    ]
    assert discovery["readiness"] == {
        "preferred-model-turn": "transport_live_ready",
        "fallback-model-turn": "transport_live_ready",
    }
    assert "unrelated-vlm" not in discovery["readiness"]


def test_tau_run_dispatches_tau_agent_node_through_native_adapter(
    tmp_path: Path, fixture_transport: _FixtureProviderFactory
) -> None:
    spec_path = _spec(tmp_path)
    exit_code, receipt = _run_cli(spec_path)

    node = _node(receipt)
    assert node["verdict"] != "ADAPTER_EXECUTION_FAILED", node
    assert exit_code == 0, receipt
    assert node["status"] == "PASS"
    assert fixture_transport.calls == 1
    settlement = node["accepted_output"]["settlement"]
    assert settlement["schema"] == "tau.agent_node_settlement.v1"
    assert settlement["state"] == "completed"
    assert settlement["tool_effect_receipt_sha256s"]
    assert node["transport_profile"]["profile_id"] == "fixture-model-turn"
    assert "nonce-fixture-4242" in node["accepted_output"]["final_text"]
    node_receipt = json.loads((tmp_path / "receipts" / "reviewer.json").read_text(encoding="utf-8"))
    assert node_receipt["settlement"]["sha256"] == settlement["sha256"]
    reader = SqliteDagRunStore(tmp_path / "run" / "dag-run.sqlite3")
    try:
        events = load_agent_events(reader, receipt["scheduler_run_id"], node_id="reviewer")
    finally:
        reader.close()
    kinds = {event["agent_event"]["event_type"] for event in events}
    assert "tool_effect_recorded" in kinds, kinds


@pytest.mark.parametrize(
    ("override", "expected_verdict"),
    [
        (
            {"agent_requirement": _requirement(preferences=["no-such-profile"])},
            "TRANSPORT_PROFILE_UNAVAILABLE",
        ),
        ({"agent_requirement": _requirement(caps=["files"])}, "TRANSPORT_PROFILE_UNAVAILABLE"),
        ({"allowed_tools": ["bash"]}, "NATIVE_TOOL_UNSUPPORTED"),
        ({"allowed_paths": ["../**"]}, "NATIVE_PATH_POLICY_INVALID"),
        ({"allowed_paths": []}, "NATIVE_PATH_POLICY_INVALID"),
        ({"agent_requirement": {"schema": "bogus"}}, "NATIVE_NODE_INVALID"),
        ({"agent_requirement": None}, "NATIVE_NODE_INVALID"),
        ({"max_turns": "many"}, "NATIVE_NODE_INVALID"),
    ],
)
def test_tau_run_preflight_rejects_before_any_provider_or_tool_execution(
    tmp_path: Path,
    fixture_transport: _FixtureProviderFactory,
    override: dict[str, Any],
    expected_verdict: str,
) -> None:
    spec_path = _spec(tmp_path, **override)
    exit_code, receipt = _run_cli(spec_path)

    node = _node(receipt)
    assert exit_code != 0
    assert node["status"] == "BLOCKED"
    assert node["verdict"] == expected_verdict, node
    assert fixture_transport.calls == 0
    assert node.get("accepted_output") is None
    reader = SqliteDagRunStore(tmp_path / "run" / "dag-run.sqlite3")
    try:
        events = load_agent_events(reader, receipt["scheduler_run_id"], node_id="reviewer")
    finally:
        reader.close()
    assert not [e for e in events if e["agent_event"]["event_type"] == "tool_effect_recorded"]


def test_tau_run_cancellation_settles_cancelled_without_tool_effects(
    tmp_path: Path, fixture_transport: _FixtureProviderFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch, "_precancelled", lambda execution: True)
    spec_path = _spec(tmp_path)
    exit_code, receipt = _run_cli(spec_path)

    node = _node(receipt)
    assert exit_code != 0
    assert node["status"] == "BLOCKED"
    assert node["verdict"] == "CANCELLED"
    assert fixture_transport.calls == 0


def test_command_node_cli_path_still_dispatches(tmp_path: Path) -> None:
    from test_generic_dag import _node as command_node

    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "tau340-command",
        "run_dir": str(tmp_path / "run"),
        "nodes": [command_node(tmp_path, "echo")],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    exit_code, receipt = _run_cli(spec_path)
    assert exit_code == 0, receipt
    assert receipt["nodes"][0]["status"] == "PASS"
