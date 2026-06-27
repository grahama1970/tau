#!/usr/bin/env python3
"""Experiment-local Memory-first / Brave-when-required harness slice."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

MEMORY_BASE_URL = "http://127.0.0.1:8601"
MEMORY_TIMEOUT_S = 60.0
BRAVE_RUN_SH = Path("/home/graham/workspace/experiments/agent-skills/skills/brave-search/run.sh")
CREATE_EVIDENCE_CASE_RUN_SH = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/create-evidence-case/run.sh"
)
PERSONAPLEX_SKILL_PATH = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/personaplex/SKILL.md"
)
PIPELINE_STAGE_LABELS = {
    "intent": "Getting Intent...",
    "extract_entities": "Extracting Entities...",
    "recall": "Accessing Memory...",
    "evidence_case": "Creating Evidence Case...",
    "brave_search": "Searching Web...",
    "answer": "Answering...",
    "clarify": "Clarifying...",
    "deflect": "Deflecting...",
    "personaplex": "Preparing Persona Voice...",
}
SKILL_STAGE = {
    "memory.answer": "answer",
    "memory.clarify": "clarify",
    "memory.deflect": "deflect",
    "brave-search": "brave_search",
    "create-evidence-case": "evidence_case",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def memory_post(
    path: str,
    payload: dict[str, Any],
    *,
    base_url: str = MEMORY_BASE_URL,
) -> dict[str, Any]:
    timeout = httpx.Timeout(MEMORY_TIMEOUT_S, connect=2.0)
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        response = client.post(path, json=payload)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Memory {path} returned non-object JSON")
    return data


def memory_post_result(path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        return memory_post(path, payload), "PASS"
    except httpx.HTTPStatusError as exc:
        return (
            {
                "error": str(exc),
                "status_code": exc.response.status_code,
                "body": exc.response.text[-2000:],
            },
            "FAILED",
        )
    except Exception as exc:
        return {"error": repr(exc)}, "FAILED"


def memory_route(query: str, *, scope: str) -> dict[str, Any]:
    intent = memory_post(
        "/intent",
        {
            "q": query,
            "scope": scope,
            "session_id": "tau-loop2-harness",
            "fast": True,
        },
    )
    extract_entities = call_memory_extract_entities(query, scope=scope)
    entity_packet = entity_packet_from_extract_entities(
        extract_entities,
        fallback_intent=intent,
    )
    recall = memory_post(
        "/recall",
        {
            "q": query,
            "scope": scope,
            "k": 5,
        },
    )
    return {
        "schema": "tau.loop2_memory_route.v1",
        "intent": intent,
        "extract_entities": extract_entities,
        "recall": recall,
        "entity_packet": entity_packet,
    }


def entity_packet_from_intent(intent: dict[str, Any]) -> dict[str, Any]:
    query_plan = intent.get("query_plan") if isinstance(intent.get("query_plan"), dict) else {}
    unresolved_terms = intent.get("unresolved_terms")
    frameworks = intent.get("frameworks")
    return {
        "schema": "tau.loop2_memory_entity_packet.v1",
        "entities": intent.get("entities") if isinstance(intent.get("entities"), list) else [],
        "valid_entities": (
            intent.get("valid_entities") if isinstance(intent.get("valid_entities"), list) else []
        ),
        "unresolved_terms": unresolved_terms if isinstance(unresolved_terms, list) else [],
        "frameworks": frameworks if isinstance(frameworks, list) else [],
        "query_plan_extracted_entities": (
            query_plan.get("extracted_entities")
            if isinstance(query_plan.get("extracted_entities"), list)
            else []
        ),
        "source": "intent_fallback",
    }


def call_memory_extract_entities(query: str, *, scope: str) -> dict[str, Any]:
    payload, call_status = memory_post_result(
        "/extract-entities",
        {
            "text": query,
            "scope": scope,
        },
    )
    return {
        "schema": "tau.loop2_memory_extract_entities_stage.v1",
        "ran": True,
        "endpoint": "/extract-entities",
        "payload": payload,
        "status": call_status,
    }


def entity_packet_from_extract_entities(
    extract_entities: dict[str, Any],
    *,
    fallback_intent: dict[str, Any],
) -> dict[str, Any]:
    payload = (
        extract_entities.get("payload")
        if isinstance(extract_entities.get("payload"), dict)
        else {}
    )
    if extract_entities.get("status") != "PASS":
        packet = entity_packet_from_intent(fallback_intent)
        packet["extract_entities_status"] = extract_entities.get("status")
        packet["source"] = "intent_fallback_after_extract_entities_failure"
        return packet

    query_plan = payload.get("query_plan") if isinstance(payload.get("query_plan"), dict) else {}
    unresolved_terms = payload.get("unresolved_terms")
    frameworks = payload.get("frameworks")
    entities = payload.get("entities")
    valid_entities = payload.get("valid_entities")
    if not isinstance(entities, list):
        entities = payload.get("extracted_entities")
    return {
        "schema": "tau.loop2_memory_entity_packet.v1",
        "entities": entities if isinstance(entities, list) else [],
        "valid_entities": valid_entities if isinstance(valid_entities, list) else [],
        "unresolved_terms": unresolved_terms if isinstance(unresolved_terms, list) else [],
        "frameworks": frameworks if isinstance(frameworks, list) else [],
        "query_plan_extracted_entities": (
            query_plan.get("extracted_entities")
            if isinstance(query_plan.get("extracted_entities"), list)
            else []
        ),
        "source": "extract_entities",
        "extract_entities_status": "PASS",
    }


def has_unresolved_entities(entity_packet: dict[str, Any]) -> bool:
    return bool(
        entity_packet.get("unresolved_terms")
        or entity_packet.get("query_plan_extracted_entities") == []
        and entity_packet.get("entities") == []
    )


def recall_scan_required(recall: dict[str, Any]) -> bool:
    return recall.get("found") is False and recall.get("should_scan") is True


def select_skill(
    memory: dict[str, Any],
    *,
    require_external: bool = False,
) -> dict[str, Any]:
    reasons: list[str] = []
    intent = memory.get("intent") if isinstance(memory.get("intent"), dict) else {}
    recall = memory.get("recall") if isinstance(memory.get("recall"), dict) else {}
    entity_packet = (
        memory.get("entity_packet") if isinstance(memory.get("entity_packet"), dict) else {}
    )
    action = str(intent.get("action") or "").upper()
    confidence = float(intent.get("confidence") or 0.0)
    selected = "memory.answer"

    if confidence and confidence < 0.6:
        selected = "memory.clarify"
        reasons.append("low_intent_confidence")
    elif action == "CLARIFY":
        selected = "memory.clarify"
        reasons.append("memory_intent_clarify")
    elif action in {"OFF_TOPIC", "UNSAFE"}:
        selected = "memory.deflect"
        reasons.append(f"memory_intent_{action.lower()}")
    elif action == "COMPLIANCE":
        selected = "create-evidence-case"
        reasons.append("memory_intent_compliance")
    elif action == "RESEARCH":
        selected = "brave-search"
        reasons.append("memory_intent_research")
    elif require_external:
        selected = "brave-search"
        reasons.append("caller_require_external")
    elif action == "NO_MATCH":
        selected = "memory.deflect"
        reasons.append("memory_intent_no_match")
    elif has_unresolved_entities(entity_packet):
        selected = "memory.clarify"
        reasons.append("unresolved_or_missing_entities")
    elif recall_scan_required(recall):
        selected = "brave-search"
        reasons.append("memory_recall_miss_should_scan")
    else:
        selected = "memory.answer"
        reasons.append("memory_query_answer")

    candidates = [
        "memory.answer",
        "memory.clarify",
        "memory.deflect",
        "brave-search",
        "create-evidence-case",
    ]
    return {
        "schema": "tau.loop2_skill_selection.v1",
        "selected_skill": selected,
        "reasons": reasons,
        "intent_action": action,
        "intent_confidence": confidence,
        "candidate_skills": candidates,
        "rejected_skills": [candidate for candidate in candidates if candidate != selected],
        "external_search_allowed": selected == "brave-search",
        "stop_condition": "selected branch writes branch receipt with returncode/status",
    }


def brave_required(
    memory: dict[str, Any],
    *,
    require_external: bool = False,
) -> tuple[bool, list[str]]:
    selection = select_skill(memory, require_external=require_external)
    if selection["selected_skill"] != "brave-search":
        return False, []
    return True, list(selection["reasons"])


def call_memory_answer(query: str, *, scope: str) -> dict[str, Any]:
    payload, call_status = memory_post_result("/answer", {"q": query, "scope": scope, "k": 5})
    status = "PASS" if payload.get("can_answer") is True else "NEEDS_MORE_EVIDENCE"
    if call_status == "FAILED":
        status = "FAILED"
    return {
        "schema": "tau.loop2_memory_answer_branch.v1",
        "ran": True,
        "endpoint": "/answer",
        "payload": payload,
        "status": status,
    }


def call_memory_clarify(
    query: str,
    *,
    scope: str,
    evidence_case: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"q": query, "scope": scope, "k": 5}
    if evidence_case is not None:
        request["evidence_case"] = evidence_case
    payload, call_status = memory_post_result("/clarify", request)
    return {
        "schema": "tau.loop2_memory_clarify_branch.v1",
        "ran": True,
        "endpoint": "/clarify",
        "payload": payload,
        "status": call_status,
    }


def call_memory_deflect(query: str, *, intent_action: str) -> dict[str, Any]:
    payload, call_status = memory_post_result(
        "/deflect",
        {"q": query, "intent_action": intent_action},
    )
    return {
        "schema": "tau.loop2_memory_deflect_branch.v1",
        "ran": True,
        "endpoint": "/deflect",
        "payload": payload,
        "status": call_status,
    }


def run_create_evidence_case(query: str, *, category: str = "compliance") -> dict[str, Any]:
    process = subprocess.run(
        [
            str(CREATE_EVIDENCE_CASE_RUN_SH),
            "create",
            query,
            "--category",
            category,
            "--test-only",
            "--json",
            "--quiet",
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=180,
    )
    payload: dict[str, Any]
    if process.returncode == 0:
        payload = extract_json_object(process.stdout)
    else:
        payload = {
            "error": process.stderr.strip() or process.stdout.strip(),
            "stdout": process.stdout[-2000:],
        }
    branch = {
        "schema": "tau.loop2_create_evidence_case_branch.v1",
        "ran": True,
        "command": "create-evidence-case create --test-only --json --quiet",
        "returncode": process.returncode,
        "payload": payload,
        "status": "PASS" if process.returncode == 0 else "FAILED",
    }
    if process.returncode == 0 and payload.get("can_answer") is False:
        branch["clarify_handoff_required"] = True
    return branch


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {"parse_error": "no JSON object found", "raw_tail": text[-2000:]}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return {"parse_error": str(exc), "raw_tail": text[-2000:]}
    return parsed if isinstance(parsed, dict) else {"parse_error": "top-level JSON was not object"}


def run_selected_branch(
    query: str,
    *,
    scope: str,
    memory: dict[str, Any],
    selection: dict[str, Any],
    run_brave: bool,
    run_evidence_case: bool,
) -> dict[str, Any]:
    selected = selection["selected_skill"]
    if selected == "memory.answer":
        return call_memory_answer(query, scope=scope)
    if selected == "memory.clarify":
        return call_memory_clarify(query, scope=scope)
    if selected == "memory.deflect":
        return call_memory_deflect(
            query,
            intent_action=str(selection.get("intent_action") or "NO_MATCH"),
        )
    if selected == "brave-search":
        if run_brave:
            return run_brave_web(query)
        return {
            "schema": "tau.loop2_brave_search.v1",
            "ran": False,
            "reason": "required_but_disabled",
            "status": "FAILED",
            "query": query,
            "result_count": 0,
            "payload": {},
        }
    if selected == "create-evidence-case":
        if not run_evidence_case:
            return {
                "schema": "tau.loop2_create_evidence_case_branch.v1",
                "ran": False,
                "reason": "required_but_disabled",
                "status": "SKIPPED",
            }
        evidence = run_create_evidence_case(query)
        if evidence.get("clarify_handoff_required") is True:
            payload = evidence.get("payload")
            evidence_payload = payload if isinstance(payload, dict) else None
            evidence["clarify_handoff"] = call_memory_clarify(
                query,
                scope=scope,
                evidence_case=evidence_payload,
            )
        return evidence
    return {
        "schema": "tau.loop2_unknown_branch.v1",
        "ran": False,
        "status": "FAILED",
        "error": f"unknown selected skill: {selected}",
    }


def branch_failed_closed(branch: dict[str, Any]) -> bool:
    """Return whether the selected branch stopped instead of producing an answer artifact."""

    if branch.get("ran") is False:
        return True
    status = str(branch.get("status") or "").upper()
    return status not in {"PASS"}


def build_stage_trace(
    memory: dict[str, Any],
    selection: dict[str, Any],
    branch: dict[str, Any],
    persona_voice: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build receipt-backed stage metadata for chat/TUI consumers."""

    extract_entities = (
        memory.get("extract_entities") if isinstance(memory.get("extract_entities"), dict) else {}
    )
    recall = memory.get("recall") if isinstance(memory.get("recall"), dict) else {}
    branch_stage = SKILL_STAGE.get(str(selection.get("selected_skill") or ""), "answer")
    trace = [
        _stage_item("intent", "PASS", source="memory.intent"),
        _stage_item(
            "extract_entities",
            str(extract_entities.get("status") or "UNKNOWN"),
            source="memory.extract_entities",
        ),
        _stage_item(
            "recall",
            _memory_payload_status(recall),
            source="memory.recall",
        ),
        _stage_item(
            branch_stage,
            str(branch.get("status") or "UNKNOWN"),
            source=str(selection.get("selected_skill") or "unknown"),
        ),
    ]
    if persona_voice.get("voice_requested") is True:
        trace.append(
            _stage_item(
                "personaplex",
                str(persona_voice.get("voice_status") or "UNKNOWN"),
                source="personaplex",
            )
        )
    return trace


def _stage_item(stage: str, status: str, *, source: str) -> dict[str, Any]:
    return {
        "schema": "tau.loop2_pipeline_stage.v1",
        "stage": stage,
        "label": PIPELINE_STAGE_LABELS[stage],
        "status": status,
        "source": source,
    }


def _memory_payload_status(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return "FAILED"
    return "PASS"


def _legacy_brave_reasons(
    memory: dict[str, Any],
    *,
    require_external: bool = False,
) -> list[str]:
    reasons: list[str] = []
    intent = memory.get("intent") if isinstance(memory.get("intent"), dict) else {}
    recall = memory.get("recall") if isinstance(memory.get("recall"), dict) else {}
    action = str(intent.get("action") or "").upper()

    if require_external:
        reasons.append("caller_require_external")
    if action == "RESEARCH":
        reasons.append("memory_intent_research")
    if action == "NO_MATCH" and recall.get("should_scan") is True:
        reasons.append("memory_no_match_should_scan")
    if recall.get("found") is False and recall.get("should_scan") is True:
        reasons.append("memory_recall_miss_should_scan")

    return reasons


def run_brave_web(query: str, *, count: int = 5) -> dict[str, Any]:
    env = dict(os.environ)
    command = [
        "bash",
        "-lc",
        (
            "source ~/.zshrc >/dev/null 2>&1 || true; "
            f"{BRAVE_RUN_SH} web {json.dumps(query)} --count {count} --json"
        ),
    ]
    process = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=90,
    )
    payload: dict[str, Any]
    if process.returncode == 0:
        payload = json.loads(process.stdout)
    else:
        payload = {
            "query": query,
            "results": [],
            "error": process.stderr.strip() or process.stdout.strip(),
        }
    return {
        "schema": "tau.loop2_brave_search.v1",
        "ran": True,
        "returncode": process.returncode,
        "status": "PASS" if process.returncode == 0 else "FAILED",
        "query": query,
        "result_count": len(payload.get("results") or []),
        "payload": payload,
    }


def build_persona_voice_packet(
    *,
    requested_persona_id: str | None,
    voice_engine: str = "personaplex",
    personaplex_receipt: Path | None = None,
    branch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build fail-closed persona voice metadata for Sparta Chat consumers."""
    branch_payload = branch.get("payload") if isinstance(branch, dict) else None
    branch_persona_id = (
        branch_payload.get("persona_id")
        if isinstance(branch_payload, dict) and isinstance(branch_payload.get("persona_id"), str)
        else None
    )
    persona_id = requested_persona_id or branch_persona_id
    packet: dict[str, Any] = {
        "schema": "tau.sparta_chat_persona_voice.v1",
        "persona_id": persona_id,
        "voice_engine": voice_engine,
        "voice_requested": persona_id is not None,
        "personaplex_skill": str(PERSONAPLEX_SKILL_PATH),
        "personaplex_receipt": str(personaplex_receipt) if personaplex_receipt else None,
        "text_persona_source": "memory_branch_payload" if branch_persona_id else "requested",
        "voice_status": "NOT_REQUESTED",
        "publication_status": "NOT_APPLICABLE",
        "live_full_duplex": False,
        "claims": {
            "proves": [
                "Tau receipt preserves persona and voice routing metadata for TUI consumers"
            ],
            "does_not_prove": [
                "PersonaPlex audio synthesis",
                "published PersonaPlex voice identity",
                "live full-duplex PersonaPlex readiness",
            ],
        },
    }
    if persona_id is None:
        return packet
    packet["voice_status"] = "REQUESTED_NO_PERSONAPLEX_RECEIPT"
    packet["publication_status"] = "UNVERIFIED"
    if personaplex_receipt is None:
        return packet
    try:
        receipt = json.loads(personaplex_receipt.read_text())
    except Exception as exc:
        packet["voice_status"] = "PERSONAPLEX_RECEIPT_UNREADABLE"
        packet["receipt_error"] = repr(exc)
        return packet
    if not isinstance(receipt, dict):
        packet["voice_status"] = "PERSONAPLEX_RECEIPT_INVALID"
        packet["receipt_error"] = "top-level JSON is not an object"
        return packet
    packet["personaplex"] = {
        "schema": receipt.get("schema"),
        "status": receipt.get("status"),
        "persona": receipt.get("persona"),
        "publication_status": receipt.get("publication_status"),
        "human_review_status": receipt.get("human_review_status"),
        "review_html": receipt.get("review_html"),
        "voice_prompt_count": len(receipt.get("generated_voice_prompts") or []),
    }
    packet["voice_status"] = str(receipt.get("status") or "PERSONAPLEX_RECEIPT_UNKNOWN")
    packet["publication_status"] = str(receipt.get("publication_status") or "UNKNOWN")
    return packet


def build_harness_receipt(
    query: str,
    *,
    scope: str = "tau-loop2-harness",
    require_external: bool = False,
    run_brave: bool = True,
    run_evidence_case_branch: bool = True,
    persona_id: str | None = None,
    personaplex_receipt: Path | None = None,
) -> dict[str, Any]:
    memory = memory_route(query, scope=scope)
    selection = select_skill(memory, require_external=require_external)
    branch = run_selected_branch(
        query,
        scope=scope,
        memory=memory,
        selection=selection,
        run_brave=run_brave,
        run_evidence_case=run_evidence_case_branch,
    )
    persona_voice = build_persona_voice_packet(
        requested_persona_id=persona_id,
        personaplex_receipt=personaplex_receipt,
        branch=branch,
    )
    stage_trace = build_stage_trace(memory, selection, branch, persona_voice)
    brave = branch if selection["selected_skill"] == "brave-search" else {
        "schema": "tau.loop2_brave_search.v1",
        "ran": False,
        "reason": "not_selected",
        "status": "NOT_SELECTED",
        "query": query,
        "result_count": 0,
        "payload": {},
    }
    fail_closed = branch_failed_closed(branch)

    return {
        "schema": "tau.loop2_memory_skill_selector_harness.v1",
        "created_utc": utc_now(),
        "query": query,
        "scope": scope,
        "mocked": False,
        "live": True,
        "memory_first": True,
        "memory": memory,
        "selector": selection,
        "selected_skill": selection["selected_skill"],
        "branch": branch,
        "branch_status": str(branch.get("status") or "UNKNOWN"),
        "stage_trace": stage_trace,
        "current_stage": stage_trace[-1],
        "fail_closed": fail_closed,
        "persona_voice": persona_voice,
        "brave_required": selection["selected_skill"] == "brave-search",
        "brave_required_reasons": (
            selection["reasons"] if selection["selected_skill"] == "brave-search" else []
        ),
        "brave": brave,
        "claims": {
            "proves": [
                "Tau experiment harness preserves Memory intent/extract-entities/recall packets",
                "Memory intent drives deterministic skill selection before branch execution",
                "The selected branch writes an explicit branch receipt",
            ],
            "does_not_prove": [
                "full DAG scheduling",
                "semantic quality of downstream Tau repair output",
                "production Tau CLI integration",
                (
                    "full compliance adjudication beyond deterministic "
                    "create-evidence-case integration"
                ),
                "PersonaPlex audio synthesis unless persona_voice includes a real receipt",
            ],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--scope", default="tau-loop2-harness")
    parser.add_argument("--require-external", action="store_true")
    parser.add_argument("--no-brave", action="store_true")
    parser.add_argument("--no-evidence-case", action="store_true")
    parser.add_argument("--persona-id")
    parser.add_argument("--personaplex-receipt", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    receipt = build_harness_receipt(
        args.query,
        scope=args.scope,
        require_external=args.require_external,
        run_brave=not args.no_brave,
        run_evidence_case_branch=not args.no_evidence_case,
        persona_id=args.persona_id,
        personaplex_receipt=args.personaplex_receipt,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "receipt": str(args.out),
                "selected_skill": receipt["selected_skill"],
                "branch_ran": receipt["branch"]["ran"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
