"""Battle Red/Blue live Tau handoff bridge through Scillm.

This module is intentionally narrow. Battle owns Docker execution and scoring;
Tau owns the Red/Blue handoff-to-subagent-receipt boundary and the Scillm call
used to replace Battle's deterministic local provider for one bounded proof
rung.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from tau_coding.subagent_receipt import validate_subagent_receipt

SCHEMA = "tau.battle_live_handoff_proof.v1"
SCILLM_CALL_SCHEMA = "tau.battle_scillm_call_receipt.v1"


def write_battle_live_handoff_proof(
    *,
    out_dir: Path,
    battle_id: str,
    run_id: str,
    scenario_id: str,
    red_persona: str,
    blue_persona: str,
    model: str = "gpt-5.5",
    scillm_base_url: str = "http://localhost:4001",
    timeout_s: float = 90.0,
    api_key: str | None = None,
    battle_context_json: Path | None = None,
) -> dict[str, Any]:
    """Write Red/Blue Tau handoffs, Scillm call receipts, and Tau subagent receipts."""

    out_dir.mkdir(parents=True, exist_ok=True)
    goal = _goal_payload(battle_id=battle_id, scenario_id=scenario_id)
    auth = _resolve_api_key(api_key)
    battle_context = _load_battle_context_bundle(battle_context_json)

    team_results = asyncio.run(
        _write_team_handoffs_concurrently(
            out_dir=out_dir,
            goal=goal,
            battle_id=battle_id,
            run_id=run_id,
            scenario_id=scenario_id,
            teams=(("red", red_persona), ("blue", blue_persona)),
            model=model,
            scillm_base_url=scillm_base_url,
            timeout_s=timeout_s,
            api_key=auth["api_key"],
            api_key_source=auth["source"],
            battle_context=battle_context,
        )
    )

    status = (
        "PASS"
        if all(item["status"] == "PASS" and item["validation_ok"] for item in team_results)
        else "BLOCKED"
    )
    manifest = {
        "schema": SCHEMA,
        "battle_id": battle_id,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "status": status,
        "mocked": False,
        "live": True,
        "model": model,
        "surface": "scillm.chat_completions",
        "scillm_base_url": scillm_base_url,
        "battle_context": battle_context["manifest_summary"] if battle_context else None,
        "scheduling": {
            "mode": "asyncio.as_completed",
            "team_count": len(team_results),
            "completion_order": [
                {
                    "team": item["team"],
                    "persona": item["persona"],
                    "completed_at_seconds": item["completed_at_seconds"],
                }
                for item in team_results
            ],
        },
        "teams": team_results,
        "claims": {
            "proves": [
                "Tau consumed one Battle Red handoff and one Battle Blue handoff.",
                "Tau attempted Scillm chat-completions calls for both Battle personas.",
                "Tau wrote tau.subagent_receipt.v1 artifacts for both teams.",
            ],
            "does_not_prove": [
                "Battle Docker scorekeeper PASS unless Battle consumes this manifest.",
                "Unbounded Battle swarm execution.",
                "Scillm delegate/batch/tool execution; this rung uses chat completions.",
            ],
        },
    }
    _write_json(out_dir / "manifest.json", manifest)
    return manifest


async def _write_team_handoffs_concurrently(
    *,
    out_dir: Path,
    goal: dict[str, Any],
    battle_id: str,
    run_id: str,
    scenario_id: str,
    teams: tuple[tuple[str, str], ...],
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    api_key: str | None,
    api_key_source: str,
    battle_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    started = time.monotonic()
    tasks = [
        asyncio.create_task(
            _write_one_team_handoff(
                out_dir=out_dir,
                goal=goal,
                battle_id=battle_id,
                run_id=run_id,
                scenario_id=scenario_id,
                team=team,
                persona=persona,
                model=model,
                scillm_base_url=scillm_base_url,
                timeout_s=timeout_s,
                api_key=api_key,
                api_key_source=api_key_source,
                battle_context=battle_context,
                batch_started=started,
            )
        )
        for team, persona in teams
    ]
    results: list[dict[str, Any]] = []
    for task in asyncio.as_completed(tasks):
        results.append(await task)
    return results


async def _write_one_team_handoff(
    *,
    out_dir: Path,
    goal: dict[str, Any],
    battle_id: str,
    run_id: str,
    scenario_id: str,
    team: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    api_key: str | None,
    api_key_source: str,
    battle_context: dict[str, Any] | None,
    batch_started: float,
) -> dict[str, Any]:
    team_dir = out_dir / team
    team_dir.mkdir(parents=True, exist_ok=True)
    handoff = _handoff_payload(
        goal=goal,
        battle_id=battle_id,
        run_id=run_id,
        scenario_id=scenario_id,
        team=team,
        persona=persona,
        battle_context=battle_context,
    )
    handoff_path = _write_json(team_dir / "handoff.json", handoff)
    events_path = team_dir / "scillm-events.jsonl"
    scillm_call = await _call_scillm_async(
        handoff=handoff,
        team=team,
        persona=persona,
        model=model,
        scillm_base_url=scillm_base_url,
        timeout_s=timeout_s,
        api_key=api_key,
        api_key_source=api_key_source,
        events_path=events_path,
    )
    scillm_path = _write_json(team_dir / "scillm-call-receipt.json", scillm_call)
    receipt = build_subagent_receipt(
        goal=goal,
        run_id=run_id,
        battle_id=battle_id,
        scenario_id=scenario_id,
        team=team,
        persona=persona,
        scillm_call=scillm_call,
        artifacts=[str(handoff_path), str(scillm_path)],
    )
    receipt_path = _write_json(team_dir / "tau-subagent-receipt.json", receipt)
    validation = validate_subagent_receipt(receipt, active_goal_hash=goal["goal_hash"])
    validation_payload = {
        "schema": "tau.subagent_receipt_validation.v1",
        "ok": validation.ok,
        "next_subagent": validation.next_subagent,
        "errors": list(validation.errors),
    }
    validation_path = _write_json(team_dir / "validation.json", validation_payload)
    return {
        "team": team,
        "persona": persona,
        "status": receipt["result"]["status"],
        "handoff": str(handoff_path),
        "scillm_call": str(scillm_path),
        "subagent_receipt": str(receipt_path),
        "validation": str(validation_path),
        "validation_ok": validation.ok,
        "model": scillm_call.get("model"),
        "surface": "scillm.chat_completions",
        "http_status": scillm_call.get("http_status"),
        "error": scillm_call.get("error"),
        "events": str(events_path),
        "started_at_seconds": scillm_call.get("started_at_seconds"),
        "completed_at_seconds": round(time.monotonic() - batch_started, 6),
        "duration_seconds": scillm_call.get("duration_seconds"),
    }


def build_subagent_receipt(
    *,
    goal: dict[str, Any],
    run_id: str,
    battle_id: str,
    scenario_id: str,
    team: str,
    persona: str,
    scillm_call: dict[str, Any],
    artifacts: list[str],
) -> dict[str, Any]:
    """Build one Tau subagent receipt from a Battle Scillm call receipt."""

    passed = scillm_call.get("status") == "PASS"
    status = "PASS" if passed else "BLOCKED"
    next_subagent = "battle-scorekeeper" if passed else "human"
    summary = (
        f"Battle {team} persona {persona} produced a live Scillm response."
        if passed
        else f"Battle {team} persona {persona} could not obtain a live Scillm response."
    )
    return {
        "schema": "tau.subagent_receipt.v1",
        "goal": {**goal, "immutable_goal_preserved": True},
        "context": {
            "run_id": run_id,
            "subagent": f"battle-{team}",
            "actor_type": "tau",
            "artifacts_read": artifacts,
            "assumptions": [
                "Battle owns Docker execution and scorekeeping; Tau owns this handoff receipt."
            ],
            "unknowns": [] if passed else ["Scillm live response unavailable for this team."],
            "battle": {
                "battle_id": battle_id,
                "scenario_id": scenario_id,
                "team": team,
                "persona": persona,
            },
        },
        "result": {
            "status": status,
            "summary": summary,
            "mocked": False,
            "live": True,
            "artifacts": artifacts,
            "commands_run": [],
            "model": scillm_call.get("model"),
            "surface": "scillm.chat_completions",
        },
        "rationale": (
            "Battle requested one bounded Tau/Scillm handoff receipt before Docker scorekeeping."
        ),
        "evidence": artifacts,
        "next": {
            "subagent": next_subagent,
            "reason": "Scillm handoff receipt is ready for Battle scorekeeper."
            if passed
            else "Scillm live handoff is blocked and needs operator repair.",
            "executor": "local" if passed else "human",
        },
        "stop_condition": "Battle scorekeeper consumes Red/Blue receipts."
        if passed
        else "Repair Scillm reachability/auth and rerun the Battle Tau live proof.",
    }


async def _call_scillm_async(
    *,
    handoff: dict[str, Any],
    team: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    api_key: str | None,
    api_key_source: str,
    events_path: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    payload = {
        "model": model,
        "stream": True,
        "stream_heartbeat_s": 15,
        "stream_progress_events": True,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a bounded Battle subagent. Return one concise action "
                    "summary for the supplied Tau handoff. Do not claim Docker proof."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(handoff, sort_keys=True),
            },
        ],
        "temperature": 0,
        "scillm_metadata": {
            "caller": "tau",
            "proof": "battle-live-handoff",
            "team": team,
            "persona": persona,
        },
    }
    receipt: dict[str, Any] = {
        "schema": SCILLM_CALL_SCHEMA,
        "team": team,
        "persona": persona,
        "model": model,
        "scillm_base_url": scillm_base_url,
        "api_key_source": api_key_source,
        "request": {**payload, "messages": "<redacted-request-messages>"},
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "stream": True,
        "events_path": str(events_path),
        "started_at_seconds": 0.0,
    }
    if not api_key:
        receipt["error"] = "scillm_api_key_unavailable"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    try:
        async with httpx.AsyncClient(
            base_url=scillm_base_url.rstrip("/"),
            timeout=timeout_s,
        ) as client:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-Caller-Skill": "tau",
                    "Accept": "text/event-stream",
                },
                json=payload,
            ) as response:
                receipt["http_status"] = response.status_code
                if response.status_code != 200:
                    body = await response.aread()
                    receipt["error"] = f"scillm_http_status_{response.status_code}"
                    receipt["response_text"] = body.decode("utf-8", errors="replace")[:1000]
                    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
                    return receipt
                stream_result = await _collect_scillm_sse_async(response.aiter_lines(), events_path)
    except httpx.HTTPError as exc:
        receipt["error"] = f"scillm_http_error: {exc}"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
    receipt["stream_event_count"] = stream_result["event_count"]
    receipt["stream_heartbeat_count"] = stream_result["heartbeat_count"]
    receipt["stream_done_seen"] = stream_result["done_seen"]
    receipt["stream_last_event_type"] = stream_result["last_event_type"]
    receipt["response"] = _redact_response(stream_result["last_payload"])
    content = stream_result["content"]
    receipt["response_content"] = content
    receipt["status"] = "PASS" if content.strip() else "BLOCKED"
    if not content.strip():
        receipt["error"] = "scillm_empty_response_content"
    return receipt


def _goal_payload(*, battle_id: str, scenario_id: str) -> dict[str, Any]:
    goal_id = f"goal-battle-{battle_id}-tau-live"
    seed = f"{goal_id}:{scenario_id}".encode("utf-8")
    import hashlib

    return {
        "goal_id": goal_id,
        "goal_version": 1,
        "goal_hash": f"sha256:{hashlib.sha256(seed).hexdigest()}",
    }


def _load_battle_context_bundle(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    payload = _read_json(resolved)
    if not isinstance(payload, dict):
        raise ValueError(f"Battle context bundle must be a JSON object: {resolved}")
    artifacts = _extract_context_artifact_paths(payload)
    artifacts.insert(0, str(resolved))
    artifacts = _unique_strings(artifacts)
    return {
        "path": str(resolved),
        "payload": payload,
        "artifacts": artifacts,
        "manifest_summary": _summarize_context_manifest(payload, artifacts),
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_context_artifact_paths(payload: dict[str, Any]) -> list[str]:
    raw_artifacts = payload.get("artifacts")
    if isinstance(raw_artifacts, list):
        return [str(item) for item in raw_artifacts if isinstance(item, str) and item]
    if isinstance(raw_artifacts, dict):
        return [
            str(value)
            for value in raw_artifacts.values()
            if isinstance(value, str) and value
        ]
    raw_artifact_paths = payload.get("artifact_paths")
    if isinstance(raw_artifact_paths, list):
        return [str(item) for item in raw_artifact_paths if isinstance(item, str) and item]
    if isinstance(raw_artifact_paths, dict):
        return [
            str(value)
            for value in raw_artifact_paths.values()
            if isinstance(value, str) and value
        ]
    return []


def _unique_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _summarize_context_manifest(payload: dict[str, Any], artifacts: list[str]) -> dict[str, Any]:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    return {
        "schema": payload.get("schema"),
        "bundle_path": payload.get("bundle_path"),
        "artifact_count": len(artifacts),
        "run_receipt_status": _nested(summary, "run_receipt", "status"),
        "tau_live_manifest_status": _nested(summary, "tau_live_manifest", "status"),
        "research_broker_status": _nested(summary, "research_broker", "status"),
        "research_broker_passed_lane_count": _nested(
            summary, "research_broker", "passed_lane_count"
        ),
        "warm_pond_status": _nested(summary, "warm_pond", "status"),
        "warm_pond_research_weighted_candidate_count": _nested(
            summary,
            "warm_pond",
            "research_weighted_candidate_count",
        ),
        "teams": summary.get("teams") if isinstance(summary.get("teams"), dict) else {},
    }


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _battle_context_artifacts(battle_context: dict[str, Any] | None) -> list[str]:
    if not battle_context:
        return []
    return list(battle_context.get("artifacts") or [])


def _team_battle_context_summary(
    battle_context: dict[str, Any] | None,
    team: str,
) -> dict[str, Any] | None:
    if not battle_context:
        return None
    manifest_summary = dict(battle_context.get("manifest_summary") or {})
    teams = manifest_summary.get("teams")
    team_summary = teams.get(team) if isinstance(teams, dict) else None
    return {
        "bundle_path": battle_context.get("path"),
        "artifact_count": manifest_summary.get("artifact_count"),
        "run_receipt_status": manifest_summary.get("run_receipt_status"),
        "tau_live_manifest_status": manifest_summary.get("tau_live_manifest_status"),
        "research_broker_status": manifest_summary.get("research_broker_status"),
        "research_broker_passed_lane_count": manifest_summary.get(
            "research_broker_passed_lane_count"
        ),
        "warm_pond_status": manifest_summary.get("warm_pond_status"),
        "warm_pond_research_weighted_candidate_count": manifest_summary.get(
            "warm_pond_research_weighted_candidate_count"
        ),
        "team": team,
        "team_summary": team_summary if isinstance(team_summary, dict) else {},
    }


def _handoff_payload(
    *,
    goal: dict[str, Any],
    battle_id: str,
    run_id: str,
    scenario_id: str,
    team: str,
    persona: str,
    battle_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context_artifacts = _battle_context_artifacts(battle_context)
    context_summary = f"Battle {team} persona handoff for {battle_id}."
    battle_context_summary = _team_battle_context_summary(battle_context, team)
    if battle_context_summary:
        context_summary = (
            f"{context_summary} Battle artifact context is attached with "
            f"{len(context_artifacts)} artifact reference(s)."
        )
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "issue#22"},
        "goal": goal,
        "previous_subagent": "battle-orchestrator",
        "context": {
            "summary": context_summary,
            "artifacts": context_artifacts,
            "battle": {
                "battle_id": battle_id,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "team": team,
                "persona": persona,
            },
            "battle_context": battle_context_summary,
        },
        "result": {
            "status": "READY",
            "summary": "Battle requested one bounded Tau/Scillm action-selection turn.",
            "evidence": [],
        },
        "rationale": "Battle needs Red and Blue Tau receipts before scorekeeper proof.",
        "next_agent": {
            "name": f"battle-{team}",
            "executor": "local",
            "reason": "Run one bounded Battle persona action through Tau/Scillm.",
        },
        "required_evidence": [f"{team}/tau-subagent-receipt.json"],
        "stop_condition": "Tau writes a tau.subagent_receipt.v1 or a structured BLOCKED receipt.",
    }


def _resolve_api_key(explicit: str | None) -> dict[str, str | None]:
    if explicit is not None:
        return {"api_key": explicit, "source": "argument"}
    for name in ("SCILLM_API_KEY", "SCILLM_MASTER_KEY"):
        value = os.environ.get(name)
        if value:
            return {"api_key": value, "source": f"env:{name}"}
    docker_key = _scillm_key_from_docker()
    if docker_key:
        return {"api_key": docker_key, "source": "docker:scillm-proxy:SCILLM_MASTER_KEY"}
    return {"api_key": "sk-dev-proxy-123", "source": "default-dev-proxy-key"}


def _scillm_key_from_docker() -> str | None:
    try:
        ps = subprocess.run(
            ["docker", "ps", "--filter", "name=scillm-proxy", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if ps.returncode != 0:
        return None
    names = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not names:
        return None
    try:
        env = subprocess.run(
            ["docker", "exec", names[0], "printenv", "SCILLM_MASTER_KEY"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if env.returncode != 0:
        return None
    value = env.stdout.strip()
    return value or None


def _extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


async def _collect_scillm_sse_async(lines: Any, events_path: Path) -> dict[str, Any]:
    content_parts: list[str] = []
    event_count = 0
    heartbeat_count = 0
    done_seen = False
    current_event = "message"
    last_event_type = ""
    last_payload: dict[str, Any] = {}
    events_path.parent.mkdir(parents=True, exist_ok=True)
    async for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        if not line:
            continue
        if line.startswith(":"):
            heartbeat_count += 1
            last_event_type = "heartbeat"
            _append_jsonl(
                events_path,
                {
                    "type": "heartbeat",
                    "created_at": _now_iso(),
                    "raw": line[:1000],
                },
            )
            continue
        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip() or "message"
            continue
        if not line.startswith("data:"):
            continue
        data_text = line.removeprefix("data:").strip()
        if data_text == "[DONE]":
            done_seen = True
            last_event_type = "done"
            _append_jsonl(events_path, {"type": "done", "created_at": _now_iso()})
            continue
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            payload = {"raw": data_text}
        event_count += 1
        last_payload = payload if isinstance(payload, dict) else {"payload": payload}
        event_type = current_event
        if isinstance(payload, dict) and isinstance(payload.get("type"), str):
            event_type = str(payload["type"])
        elif isinstance(payload, dict) and isinstance(payload.get("choices"), list):
            event_type = "chunk"
        last_event_type = event_type
        _append_jsonl(
            events_path,
            {
                "type": event_type,
                "created_at": _now_iso(),
                "data": payload,
            },
        )
        if isinstance(payload, dict):
            choices = payload.get("choices")
            for choice in choices if isinstance(choices, list) else []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    content_parts.append(delta["content"])
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    content_parts.append(message["content"])
        current_event = "message"
    return {
        "content": "".join(content_parts),
        "event_count": event_count,
        "heartbeat_count": heartbeat_count,
        "done_seen": done_seen,
        "last_event_type": last_event_type,
        "last_payload": last_payload,
    }


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _redact_response(data: Any) -> Any:
    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            if "key" in key.lower() or "token" in key.lower() or "authorization" in key.lower():
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_response(value)
        return redacted
    if isinstance(data, list):
        return [_redact_response(item) for item in data]
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--battle-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--red-persona", required=True)
    parser.add_argument("--blue-persona", required=True)
    parser.add_argument("--model", default=os.environ.get("TAU_BATTLE_SCILLM_MODEL", "gpt-5.5"))
    parser.add_argument("--scillm-base-url", default=os.environ.get("SCILLM_BASE_URL", "http://localhost:4001"))
    parser.add_argument("--timeout-s", type=float, default=90.0)
    parser.add_argument(
        "--battle-context-json",
        "--context-artifact",
        dest="battle_context_json",
        type=Path,
        help="Artifact-backed Battle context bundle to include in Red/Blue Tau handoffs.",
    )
    args = parser.parse_args(argv)
    manifest = write_battle_live_handoff_proof(
        out_dir=args.out_dir,
        battle_id=args.battle_id,
        run_id=args.run_id,
        scenario_id=args.scenario_id,
        red_persona=args.red_persona,
        blue_persona=args.blue_persona,
        model=args.model,
        scillm_base_url=args.scillm_base_url,
        timeout_s=args.timeout_s,
        battle_context_json=args.battle_context_json,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
