"""Tests for the minimal Tau text-reasoning node (persona-dream phases 13/14)."""

from __future__ import annotations

import hashlib
import json
import os

import httpx
import pytest

from tau_coding import persona_dream_text_reasoning_agent as agent


def _sha(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def test_run_records_prompt_hash_contract_hash_and_parsed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = "Return ONLY {\"candidates\": []}."
    contract = {"type": "object", "required": ["candidates"]}

    def fake_resolve() -> dict[str, str]:
        return {"api_key": "sk-test", "source": "env:SCILLM_API_KEY"}

    def fake_post(self, url, headers=None, json=None):  # noqa: A002 - httpx signature
        assert headers["X-Caller-Skill"] == "tau-persona-dream-text-reasoning"
        assert headers["Authorization"] == "Bearer sk-test"
        assert json["response_format"] == {"type": "json_object"}
        assert json["messages"][0]["content"] == prompt
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"candidates": [{"id": "x"}]}'}}]},
            request=httpx.Request("POST", "http://127.0.0.1:4001" + url),
        )

    monkeypatch.setattr(agent, "_resolve_scillm_api_key", fake_resolve)
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    receipt = agent.run_text_reasoning(
        {"prompt": prompt, "role": "unit", "output_contract": contract}
    )

    assert receipt["status"] == "PASS"
    assert receipt["schema"] == agent.RECEIPT_SCHEMA
    assert receipt["live_call_performed"] is True
    assert receipt["mocked"] is False
    assert receipt["prompt_sha256"] == _sha(prompt)
    assert receipt["output_contract_sha256"] == _sha(
        json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )
    assert receipt["api_key_source"] == "env:SCILLM_API_KEY"
    assert receipt["model"] == "gpt-5.5"
    assert receipt["response_content"] == '{"candidates": [{"id": "x"}]}'
    assert receipt["parsed_json"] == {"candidates": [{"id": "x"}]}
    # The raw prompt is never leaked into the receipt request echo.
    assert receipt["request"]["messages"] == "<redacted-prompt>"


def test_blocked_when_api_key_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent, "_resolve_scillm_api_key", lambda: {"api_key": None, "source": "none"}
    )
    receipt = agent.run_text_reasoning({"prompt": "hi"})
    assert receipt["status"] == "BLOCKED"
    assert receipt["live_call_performed"] is False
    assert receipt["error"] == "scillm_api_key_unavailable"


def test_blocked_on_empty_prompt() -> None:
    receipt = agent.run_text_reasoning({"prompt": "   "})
    assert receipt["status"] == "BLOCKED"
    assert receipt["error"] == "empty_prompt"
    assert receipt["live_call_performed"] is False


def test_blocked_when_response_not_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent, "_resolve_scillm_api_key", lambda: {"api_key": "k", "source": "env:X"}
    )

    def fake_post(self, url, headers=None, json=None):  # noqa: A002
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "no json here"}}]},
            request=httpx.Request("POST", "http://127.0.0.1:4001" + url),
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    receipt = agent.run_text_reasoning({"prompt": "go"})
    assert receipt["status"] == "BLOCKED"
    assert receipt["error"] == "response_not_parseable_json_object"
    assert receipt["live_call_performed"] is True


def test_extract_json_object_handles_code_fence() -> None:
    assert agent._extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert agent._extract_json_object('prose {"a": 1} trailing') == {"a": 1}
    assert agent._extract_json_object("no object") is None


@pytest.mark.skipif(
    os.environ.get("TAU_TEXT_REASONING_LIVE") != "1",
    reason="live scillm call; set TAU_TEXT_REASONING_LIVE=1 to run",
)
def test_live_text_reasoning_through_scillm() -> None:
    receipt = agent.run_text_reasoning(
        {
            "prompt": 'Return ONLY a JSON object of the form {"ok": true}. No prose.',
            "role": "live-smoke",
        }
    )
    assert receipt["live_call_performed"] is True
    assert receipt["http_status"] == 200
    assert receipt["status"] == "PASS"
    assert isinstance(receipt["parsed_json"], dict)
    assert receipt["api_key_source"]
