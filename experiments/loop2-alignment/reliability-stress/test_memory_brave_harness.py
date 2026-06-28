from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest


def load_harness_module():
    path = Path(__file__).with_name("memory_brave_harness.py")
    spec = importlib.util.spec_from_file_location("tau_memory_brave_harness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load harness module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


harness = load_harness_module()


def test_brave_not_required_when_memory_query_is_grounded() -> None:
    memory = {
        "intent": {"action": "QUERY", "confidence": 0.91},
        "recall": {"found": True, "should_scan": False, "confidence": 0.8, "items": [{}]},
    }

    should_search, reasons = harness.brave_required(memory)

    assert should_search is False
    assert reasons == []


def test_brave_required_for_research_intent() -> None:
    memory = {
        "intent": {"action": "RESEARCH", "confidence": 0.84},
        "recall": {"found": False, "should_scan": True, "items": []},
    }

    should_search, reasons = harness.brave_required(memory)

    assert should_search is True
    assert "memory_intent_research" in reasons


def test_brave_required_for_memory_miss_with_should_scan() -> None:
    memory = {
        "intent": {"action": "QUERY", "confidence": 0.7},
        "recall": {"found": False, "should_scan": True, "items": []},
        "entity_packet": {"entities": [{"id": "x"}], "unresolved_terms": []},
    }

    should_search, reasons = harness.brave_required(memory)

    assert should_search is True
    assert "memory_recall_miss_should_scan" in reasons


def test_no_match_selects_deflect_before_brave() -> None:
    memory = {
        "intent": {"action": "NO_MATCH", "confidence": 0.7},
        "recall": {"found": False, "should_scan": True, "items": []},
        "entity_packet": {},
    }

    selection = harness.select_skill(memory)

    assert selection["selected_skill"] == "memory.deflect"
    assert "memory_intent_no_match" in selection["reasons"]


def test_require_external_overrides_plain_no_match_to_brave() -> None:
    memory = {
        "intent": {"action": "NO_MATCH", "confidence": 0.7},
        "recall": {"found": False, "should_scan": True, "items": []},
        "entity_packet": {},
    }

    selection = harness.select_skill(memory, require_external=True)

    assert selection["selected_skill"] == "brave-search"
    assert "caller_require_external" in selection["reasons"]


def test_compliance_selects_create_evidence_case() -> None:
    memory = {
        "intent": {
            "action": "COMPLIANCE",
            "confidence": 0.88,
            "entities": [{"id": "CWE-287"}],
            "frameworks": ["CWE"],
        },
        "recall": {"found": True, "should_scan": False, "items": [{}]},
        "entity_packet": {"entities": [{"id": "CWE-287"}], "frameworks": ["CWE"]},
    }

    selection = harness.select_skill(memory)

    assert selection["selected_skill"] == "create-evidence-case"
    assert selection["external_search_allowed"] is False


def test_grounded_query_selects_answer() -> None:
    memory = {
        "intent": {"action": "QUERY", "confidence": 0.91, "entities": [{"id": "x"}]},
        "recall": {"found": True, "should_scan": False, "items": [{}]},
        "entity_packet": {"entities": [{"id": "x"}], "unresolved_terms": []},
    }

    selection = harness.select_skill(memory)

    assert selection["selected_skill"] == "memory.answer"


def test_unsupported_memory_intent_action_deflects_before_answer() -> None:
    memory = {
        "intent": {"action": "SUMMARIZE_FILE", "confidence": 0.91},
        "recall": {"found": True, "should_scan": False, "items": [{"id": "memory-1"}]},
        "entity_packet": {"entities": [{"id": "x"}], "unresolved_terms": []},
    }

    selection = harness.select_skill(memory)

    assert selection["selected_skill"] == "memory.deflect"
    assert selection["external_search_allowed"] is False
    assert "unsupported_memory_intent_action" in selection["reasons"]


def test_unresolved_entities_selects_clarify() -> None:
    memory = {
        "intent": {"action": "QUERY", "confidence": 0.91},
        "recall": {"found": True, "should_scan": False, "items": [{}]},
        "entity_packet": {"entities": [], "unresolved_terms": ["unclear thing"]},
    }

    selection = harness.select_skill(memory)

    assert selection["selected_skill"] == "memory.clarify"


def test_memory_route_runs_intent_extract_entities_then_recall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        if path == "/intent":
            return {"action": "QUERY", "confidence": 0.9}
        if path == "/extract-entities":
            assert payload["text"] == "What is CWE-287?"
            assert payload["scope"] == "tau"
            return {
                "entities": [{"id": "CWE-287"}],
                "valid_entities": [{"id": "CWE-287"}],
                "frameworks": ["CWE"],
            }
        if path == "/recall":
            return {"found": True, "should_scan": False, "items": [{"id": "memory-1"}]}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    memory = harness.memory_route("What is CWE-287?", scope="tau")

    assert [path for path, _payload in calls] == ["/intent", "/extract-entities", "/recall"]
    assert memory["extract_entities"]["status"] == "PASS"
    assert memory["entity_packet"]["source"] == "extract_entities"
    assert memory["entity_packet"]["entities"] == [{"id": "CWE-287"}]


def test_memory_route_marks_entity_packet_fallback_when_extract_entities_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_memory_post_result(
        path: str,
        payload: dict[str, object],
    ) -> tuple[dict[str, object], str]:
        assert path == "/extract-entities"
        assert payload["text"] == "fallback entity query"
        assert payload["scope"] == "tau"
        return {"error": "extract endpoint unavailable"}, "FAILED"

    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        if path == "/intent":
            return {
                "action": "QUERY",
                "confidence": 0.9,
                "entities": [{"id": "fallback"}],
                "valid_entities": [{"id": "fallback"}],
            }
        if path == "/recall":
            return {"found": True, "should_scan": False, "items": []}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(harness, "memory_post_result", fake_memory_post_result)
    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    memory = harness.memory_route("fallback entity query", scope="tau")

    assert memory["extract_entities"]["status"] == "FAILED"
    assert memory["entity_packet"]["source"] == "intent_fallback_after_extract_entities_failure"
    assert memory["entity_packet"]["extract_entities_status"] == "FAILED"
    assert memory["entity_packet"]["entities"] == [{"id": "fallback"}]


def test_build_harness_receipt_calls_memory_before_selected_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_memory_route(query: str, *, scope: str) -> dict[str, object]:
        calls.append("memory")
        return {
            "schema": "tau.loop2_memory_route.v1",
            "intent": {"action": "RESEARCH", "confidence": 0.9},
            "recall": {"found": False, "should_scan": True, "items": []},
            "entity_packet": {"entities": [], "unresolved_terms": []},
        }

    def fake_brave(query: str, *, count: int = 5) -> dict[str, object]:
        calls.append("brave")
        return {
            "schema": "tau.loop2_brave_search.v1",
            "ran": True,
            "returncode": 0,
            "query": query,
            "result_count": 1,
            "payload": {"results": [{"title": "result"}]},
        }

    monkeypatch.setattr(harness, "memory_route", fake_memory_route)
    monkeypatch.setattr(harness, "run_brave_web", fake_brave)

    receipt = harness.build_harness_receipt("latest docs", run_brave=True)

    assert calls == ["memory", "brave"]
    assert receipt["memory_first"] is True
    assert receipt["schema"] == "tau.loop2_memory_skill_selector_harness.v1"
    assert receipt["selected_skill"] == "brave-search"
    assert receipt["brave_required"] is True
    assert receipt["brave"]["ran"] is True
    assert receipt["branch"]["ran"] is True


def test_answer_branch_calls_memory_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        return {"schema": "memory.answer.v1", "can_answer": True}

    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    result = harness.call_memory_answer("what happened", scope="tau")

    assert calls == [("/answer", {"q": "what happened", "scope": "tau", "k": 5})]
    assert result["status"] == "PASS"


def test_answer_branch_fails_closed_when_memory_cannot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/answer"
        return {
            "schema": "memory.answer.v1",
            "can_answer": False,
            "answer_type": "insufficient_memory_evidence",
        }

    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    result = harness.call_memory_answer("unsupported claim", scope="tau")

    assert result["status"] == "NEEDS_MORE_EVIDENCE"
    assert harness.branch_failed_closed(result) is True


def test_clarify_branch_can_include_evidence_case(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        return {"schema": "memory.clarify.v1", "needs_clarification": True}

    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    result = harness.call_memory_clarify(
        "ambiguous compliance claim",
        scope="tau",
        evidence_case={"failure_codes": ["missing_bridge"]},
    )

    assert calls[0][0] == "/clarify"
    assert calls[0][1]["evidence_case"] == {"failure_codes": ["missing_bridge"]}
    assert result["status"] == "PASS"


def test_clarify_branch_fails_closed_on_memory_error(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8601/clarify")
    response = httpx.Response(503, request=request, text="unavailable")

    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/clarify"
        raise httpx.HTTPStatusError("unavailable", request=request, response=response)

    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    result = harness.call_memory_clarify("ambiguous", scope="tau")

    assert result["status"] == "FAILED"
    assert result["payload"]["status_code"] == 503
    assert harness.branch_failed_closed(result) is True


def test_deflect_branch_calls_memory_deflect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        return {"schema": "memory.deflect.v1", "should_deflect": True}

    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    result = harness.call_memory_deflect("weather", intent_action="NO_MATCH")

    assert calls == [("/deflect", {"q": "weather", "intent_action": "NO_MATCH"})]
    assert result["status"] == "PASS"


def test_deflect_branch_records_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8601/deflect")
    response = httpx.Response(502, request=request, text="bad gateway")

    def fake_memory_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        raise httpx.HTTPStatusError("bad gateway", request=request, response=response)

    monkeypatch.setattr(harness, "memory_post", fake_memory_post)

    result = harness.call_memory_deflect("weather", intent_action="NO_MATCH")

    assert result["status"] == "FAILED"
    assert result["payload"]["status_code"] == 502
    assert harness.branch_failed_closed(result) is True


def test_research_branch_fails_closed_when_brave_required_but_disabled() -> None:
    memory = {
        "schema": "tau.loop2_memory_route.v1",
        "intent": {"action": "RESEARCH", "confidence": 0.92},
        "recall": {"found": False, "should_scan": True, "items": []},
        "entity_packet": {"entities": [], "unresolved_terms": []},
    }
    selection = harness.select_skill(memory)

    result = harness.run_selected_branch(
        "latest Tau docs",
        scope="tau",
        memory=memory,
        selection=selection,
        run_brave=False,
        run_evidence_case=True,
    )

    assert result["schema"] == "tau.loop2_brave_search.v1"
    assert result["ran"] is False
    assert result["status"] == "FAILED"
    assert result["reason"] == "required_but_disabled"
    assert harness.branch_failed_closed(result) is True


def test_build_harness_receipt_exposes_fail_closed_branch_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_memory_route(query: str, *, scope: str) -> dict[str, object]:
        return {
            "schema": "tau.loop2_memory_route.v1",
            "intent": {"action": "QUERY", "confidence": 0.9},
            "recall": {"found": True, "should_scan": False, "items": [{}]},
            "entity_packet": {"entities": [{"id": "x"}], "unresolved_terms": []},
        }

    def fake_answer(query: str, *, scope: str) -> dict[str, object]:
        return {
            "schema": "tau.loop2_memory_answer_branch.v1",
            "ran": True,
            "endpoint": "/answer",
            "payload": {"schema": "memory.answer.v1", "can_answer": False},
            "status": "NEEDS_MORE_EVIDENCE",
        }

    monkeypatch.setattr(harness, "memory_route", fake_memory_route)
    monkeypatch.setattr(harness, "call_memory_answer", fake_answer)

    receipt = harness.build_harness_receipt("unsupported claim")

    assert receipt["selected_skill"] == "memory.answer"
    assert receipt["branch_status"] == "NEEDS_MORE_EVIDENCE"
    assert receipt["fail_closed"] is True


@pytest.mark.parametrize(
    ("selected_skill", "branch_status", "expected_stage", "expected_label"),
    [
        ("memory.answer", "PASS", "answer", "Answering..."),
        ("memory.clarify", "PASS", "clarify", "Clarifying..."),
        ("memory.deflect", "PASS", "deflect", "Deflecting..."),
        ("brave-search", "FAILED", "brave_search", "Searching Web..."),
    ],
)
def test_stage_trace_exposes_route_specific_chat_stage(
    selected_skill: str,
    branch_status: str,
    expected_stage: str,
    expected_label: str,
) -> None:
    memory = {
        "schema": "tau.loop2_memory_route.v1",
        "intent": {"action": "QUERY", "confidence": 0.9},
        "extract_entities": {"status": "PASS"},
        "recall": {"found": True, "should_scan": False, "items": [{}]},
        "entity_packet": {"entities": [{"id": "x"}], "unresolved_terms": []},
    }
    selection = {
        "schema": "tau.loop2_skill_selection.v1",
        "selected_skill": selected_skill,
    }
    branch = {
        "schema": "tau.loop2_branch.v1",
        "ran": branch_status == "PASS",
        "status": branch_status,
    }
    persona_voice = harness.build_persona_voice_packet(requested_persona_id=None)

    trace = harness.build_stage_trace(memory, selection, branch, persona_voice)

    assert [item["stage"] for item in trace] == [
        "intent",
        "extract_entities",
        "recall",
        expected_stage,
    ]
    assert trace[-1]["label"] == expected_label
    assert trace[-1]["status"] == branch_status
    assert trace[-1]["source"] == selected_skill


def test_build_harness_receipt_includes_stage_trace_for_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_memory_route(query: str, *, scope: str) -> dict[str, object]:
        return {
            "schema": "tau.loop2_memory_route.v1",
            "intent": {"action": "CLARIFY", "confidence": 0.9},
            "extract_entities": {"status": "PASS"},
            "recall": {"found": True, "should_scan": False, "items": [{}]},
            "entity_packet": {"entities": [], "unresolved_terms": ["it"]},
        }

    def fake_clarify(query: str, *, scope: str) -> dict[str, object]:
        return {
            "schema": "tau.loop2_memory_clarify_branch.v1",
            "ran": True,
            "endpoint": "/clarify",
            "payload": {"schema": "memory.clarify.v1", "needs_clarification": True},
            "status": "PASS",
        }

    monkeypatch.setattr(harness, "memory_route", fake_memory_route)
    monkeypatch.setattr(harness, "call_memory_clarify", fake_clarify)

    receipt = harness.build_harness_receipt("clarify it")

    assert receipt["stage_trace"][-1] == {
        "schema": "tau.loop2_pipeline_stage.v1",
        "stage": "clarify",
        "label": "Clarifying...",
        "status": "PASS",
        "source": "memory.clarify",
    }
    assert receipt["current_stage"] == receipt["stage_trace"][-1]


def test_create_evidence_case_branch_uses_test_only_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = list(args[0])  # type: ignore[index]
        assert "create" in command
        assert "--test-only" in command
        assert "--json" in command
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"can_answer": False, "failure_codes": ["missing_bridge"]}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = harness.run_create_evidence_case("Does CWE-287 map to SPARTA?")

    assert result["status"] == "PASS"
    assert result["clarify_handoff_required"] is True


def test_run_brave_web_does_not_expose_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "BRAVE_API_KEY" not in json.dumps(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"query": "q", "results": [{"title": "ok"}]}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = harness.run_brave_web("q")

    assert result["ran"] is True
    assert result["status"] == "PASS"
    assert result["result_count"] == 1


def test_persona_voice_packet_is_not_requested_without_persona() -> None:
    packet = harness.build_persona_voice_packet(requested_persona_id=None)

    assert packet["schema"] == "tau.sparta_chat_persona_voice.v1"
    assert packet["voice_requested"] is False
    assert packet["voice_status"] == "NOT_REQUESTED"
    assert packet["live_full_duplex"] is False


def test_persona_voice_packet_fails_closed_without_personaplex_receipt() -> None:
    packet = harness.build_persona_voice_packet(requested_persona_id="embry")

    assert packet["persona_id"] == "embry"
    assert packet["voice_engine"] == "personaplex"
    assert packet["voice_requested"] is True
    assert packet["voice_status"] == "REQUESTED_NO_PERSONAPLEX_RECEIPT"
    assert packet["publication_status"] == "UNVERIFIED"
    assert packet["live_full_duplex"] is False


def test_persona_voice_packet_summarizes_personaplex_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "personaplex-publish-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "personaplex.publish_receipt.v1",
                "status": "CACHE_REPLAY_PASS",
                "publication_status": "NOT_PUBLISHED",
                "human_review_status": "NOT_REVIEWED",
                "persona": "embry",
                "generated_voice_prompts": [{"register": "neutral", "pt": "embry.pt"}],
                "review_html": "/tmp/personaplex/index.html",
            }
        )
        + "\n"
    )

    packet = harness.build_persona_voice_packet(
        requested_persona_id="embry",
        personaplex_receipt=receipt,
    )

    assert packet["voice_status"] == "CACHE_REPLAY_PASS"
    assert packet["publication_status"] == "NOT_PUBLISHED"
    assert packet["personaplex"] == {
        "schema": "personaplex.publish_receipt.v1",
        "status": "CACHE_REPLAY_PASS",
        "persona": "embry",
        "publication_status": "NOT_PUBLISHED",
        "human_review_status": "NOT_REVIEWED",
        "review_html": "/tmp/personaplex/index.html",
        "voice_prompt_count": 1,
    }


def test_build_harness_receipt_includes_persona_voice_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_memory_route(query: str, *, scope: str) -> dict[str, object]:
        return {
            "schema": "tau.loop2_memory_route.v1",
            "intent": {"action": "NO_MATCH", "confidence": 0.9},
            "recall": {"found": False, "should_scan": False, "items": []},
            "entity_packet": {"entities": [], "unresolved_terms": []},
        }

    def fake_deflect(query: str, *, intent_action: str) -> dict[str, object]:
        return {
            "schema": "tau.loop2_memory_deflect_branch.v1",
            "ran": True,
            "endpoint": "/deflect",
            "payload": {"schema": "memory.deflect.v1", "persona_id": "embry"},
            "status": "PASS",
        }

    monkeypatch.setattr(harness, "memory_route", fake_memory_route)
    monkeypatch.setattr(harness, "call_memory_deflect", fake_deflect)

    receipt = harness.build_harness_receipt("weather", persona_id="embry")

    assert receipt["selected_skill"] == "memory.deflect"
    assert receipt["persona_voice"]["persona_id"] == "embry"
    assert receipt["persona_voice"]["text_persona_source"] == "memory_branch_payload"
    assert receipt["persona_voice"]["voice_status"] == "REQUESTED_NO_PERSONAPLEX_RECEIPT"
