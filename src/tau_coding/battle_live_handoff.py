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
) -> dict[str, Any]:
    """Write Red/Blue Tau handoffs, Scillm call receipts, and Tau subagent receipts."""

    out_dir.mkdir(parents=True, exist_ok=True)
    goal = _goal_payload(battle_id=battle_id, scenario_id=scenario_id)
    auth = _resolve_api_key(api_key)

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
    )
    handoff_path = _write_json(team_dir / "handoff.json", handoff)
    scillm_call = await _call_scillm_async(
        handoff=handoff,
        team=team,
        persona=persona,
        model=model,
        scillm_base_url=scillm_base_url,
        timeout_s=timeout_s,
        api_key=api_key,
        api_key_source=api_key_source,
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


def _call_scillm(
    *,
    handoff: dict[str, Any],
    team: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    api_key: str | None,
    api_key_source: str,
) -> dict[str, Any]:
    started = time.monotonic()
    payload = {
        "model": model,
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
    }
    if not api_key:
        receipt["error"] = "scillm_api_key_unavailable"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    try:
        with httpx.Client(base_url=scillm_base_url.rstrip("/"), timeout=timeout_s) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-Caller-Skill": "tau",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        receipt["error"] = f"scillm_http_error: {exc}"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    receipt["http_status"] = response.status_code
    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
    try:
        data = response.json()
    except ValueError as exc:
        receipt["error"] = f"scillm_invalid_json: {exc}"
        receipt["response_text"] = response.text[:1000]
        return receipt
    receipt["response"] = _redact_response(data)
    if response.status_code != 200:
        receipt["error"] = f"scillm_http_status_{response.status_code}"
        return receipt
    content = _extract_content(data)
    receipt["response_content"] = content
    receipt["status"] = "PASS" if content.strip() else "BLOCKED"
    if not content.strip():
        receipt["error"] = "scillm_empty_response_content"
    return receipt


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
) -> dict[str, Any]:
    started = time.monotonic()
    payload = {
        "model": model,
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
            response = await client.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-Caller-Skill": "tau",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        receipt["error"] = f"scillm_http_error: {exc}"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    receipt["http_status"] = response.status_code
    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
    try:
        data = response.json()
    except ValueError as exc:
        receipt["error"] = f"scillm_invalid_json: {exc}"
        receipt["response_text"] = response.text[:1000]
        return receipt
    receipt["response"] = _redact_response(data)
    if response.status_code != 200:
        receipt["error"] = f"scillm_http_status_{response.status_code}"
        return receipt
    content = _extract_content(data)
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


def _handoff_payload(
    *,
    goal: dict[str, Any],
    battle_id: str,
    run_id: str,
    scenario_id: str,
    team: str,
    persona: str,
) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "issue#22"},
        "goal": goal,
        "previous_subagent": "battle-orchestrator",
        "context": {
            "summary": f"Battle {team} persona handoff for {battle_id}.",
            "artifacts": [],
            "battle": {
                "battle_id": battle_id,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "team": team,
                "persona": persona,
            },
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
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
