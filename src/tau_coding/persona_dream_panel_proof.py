"""Local one-panel persona-dream proof command for Tau handoff loops."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tau_coding.handoff_dispatch import write_agent_handoff_command_loop_receipt
from tau_coding.persona_dream_panel_agent import (
    DEFAULT_FIXTURE_ROOT,
    DEFAULT_IMAGE,
    DEFAULT_VISUAL_REVIEW,
)


PERSONA_DREAM_PANEL_PROOF_SCHEMA = "tau.persona_dream_panel_proof.v1"
DEFAULT_AGENT_REGISTRY_ROOT = Path("/home/graham/workspace/experiments/agent-skills/agents")
DEFAULT_COMMAND_SPEC_ROOT = Path("experiments/goal-locked-subagents/agent-command-specs")
DEFAULT_GOAL_HASH = "sha256:0000000000000000000000000000000000000000000000000000000000000027"


def write_persona_dream_panel_proof(
    out_dir: Path,
    *,
    agents_root: Path = DEFAULT_AGENT_REGISTRY_ROOT,
    command_spec_root: Path = DEFAULT_COMMAND_SPEC_ROOT,
    active_goal_hash: str = DEFAULT_GOAL_HASH,
    github_target: str = "issue#27",
    panel_evidence: Path | None = None,
) -> dict[str, Any]:
    """Run the local persona-dream panel command loop and write a proof manifest."""

    proof_dir = out_dir.expanduser().resolve()
    proof_dir.mkdir(parents=True, exist_ok=True)
    panel_context = _panel_context(panel_evidence)
    start_payload = _start_handoff(
        active_goal_hash=active_goal_hash,
        github_target=github_target,
        panel_context=panel_context,
        panel_evidence=panel_evidence,
    )
    start_path = proof_dir / "start-handoff.json"
    _write_json(start_path, start_payload)

    loop = write_agent_handoff_command_loop_receipt(
        start_payload,
        proof_dir / "command-loop",
        agent_registry_root=agents_root,
        command_spec_root=command_spec_root,
        active_goal_hash=active_goal_hash,
        max_steps=4,
    )
    loop_payload = loop.as_dict()
    response_payloads = _command_response_payloads(loop_payload)
    first_blocker = _first_blocker(response_payloads)
    dry_run_kling_request = _find_artifact_suffix(loop_payload, "one_scene_kling_request.json")
    selected_agents = [
        str(dispatch.get("selected_agent"))
        for dispatch in loop_payload.get("dispatches", [])
        if dispatch.get("selected_agent")
    ]
    command_exits = [
        result.get("exit_code")
        for dispatch in loop_payload.get("dispatches", [])
        for result in dispatch.get("command_results", [])
        if isinstance(result, dict)
    ]
    manifest = {
        "schema": PERSONA_DREAM_PANEL_PROOF_SCHEMA,
        "created_at": _now_iso(),
        "mocked": False,
        "live": True,
        "proof_scope": (
            "Local Tau command-loop proof for one persona-dream panel. It runs real "
            "Tau command specs and stops at either a dry-run one-scene Kling request "
            "artifact or the first explicit creator/reviewer/repair-gate blocker."
        ),
        "panel_evidence": str(panel_evidence.expanduser().resolve()) if panel_evidence else None,
        "panel_context": panel_context,
        "start_handoff": str(start_path),
        "command_loop_receipt": str(proof_dir / "command-loop" / "command-loop-receipt.json"),
        "selected_agents": selected_agents,
        "command_exit_codes": command_exits,
        "terminal_agent": loop_payload.get("terminal_agent"),
        "stop_reason": loop_payload.get("stop_reason"),
        "status": loop_payload.get("status"),
        "ok": loop_payload.get("ok"),
        "first_blocker": first_blocker,
        "dry_run_one_scene_kling_request": dry_run_kling_request,
        "claims": {
            "proves": [
                "Tau can run the local persona-dream panel creator/reviewer/repair-gate loop through command specs.",
                "The loop writes concrete per-role receipts and stops at a human-visible first blocker.",
                "The command path preserves mocked=false/live=true for the Tau command-loop runner.",
            ],
            "does_not_prove": [
                "No new image generation was performed.",
                "No Kling or paid provider call was performed.",
                "No public asset upload was performed.",
                "No provider-ready persona-dream panel packet is claimed.",
            ],
        },
    }
    _write_json(proof_dir / "manifest.json", manifest)
    return manifest


def _start_handoff(
    *,
    active_goal_hash: str,
    github_target: str,
    panel_context: dict[str, str],
    panel_evidence: Path | None,
) -> dict[str, Any]:
    context_artifacts = [panel_context["run_root"]]
    if panel_evidence is not None:
        context_artifacts.append(str(panel_evidence.expanduser().resolve()))
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": github_target},
        "goal": {
            "goal_id": "goal-tau-issue-27-persona-dream-panel-proof",
            "goal_version": 1,
            "goal_hash": active_goal_hash,
        },
        "previous_subagent": "human",
        "context": {
            "summary": "Run one bounded persona-dream panel proof through Tau command-loop.",
            "artifacts": context_artifacts,
            "persona_dream_panel": panel_context,
        },
        "result": {
            "status": "COMPLETED",
            "summary": "Human requested the local one-panel persona-dream proof command.",
            "evidence": context_artifacts,
        },
        "rationale": "The first bounded step is a local panel creator command with receipt output.",
        "next_agent": {
            "name": "panel-creator",
            "executor": "local",
            "reason": "Panel Creator must write a local creator receipt before review.",
        },
        "required_evidence": [
            "Panel creator, reviewer, and repair-gate receipts or the first failed receipt."
        ],
        "stop_condition": "The local proof command writes manifest.json with first_blocker or dry-run Kling request.",
    }


def _panel_context(panel_evidence: Path | None) -> dict[str, str]:
    if panel_evidence is None:
        return {
            "panel_id": "panel_001",
            "run_root": str(DEFAULT_FIXTURE_ROOT.resolve()),
            "image_path": str(DEFAULT_IMAGE.resolve()),
            "visual_review_receipt": str(DEFAULT_VISUAL_REVIEW.resolve()),
        }

    evidence_path = panel_evidence.expanduser().resolve()
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"panel evidence root must be an object: {evidence_path}")
    panel = payload.get("persona_dream_panel", payload)
    if not isinstance(panel, dict):
        raise RuntimeError(f"panel evidence persona_dream_panel must be an object: {evidence_path}")

    base = evidence_path.parent
    run_root = _required_path_text(panel, "run_root", base=base)
    return {
        "panel_id": _required_text(panel, "panel_id"),
        "run_root": run_root,
        "image_path": _required_path_text(panel, "image_path", base=Path(run_root)),
        "visual_review_receipt": _required_path_text(
            panel,
            "visual_review_receipt",
            base=Path(run_root),
        ),
    }


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"panel evidence requires non-empty string field: {key}")
    return value


def _required_path_text(payload: dict[str, Any], key: str, *, base: Path) -> str:
    value = _required_text(payload, key)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def _command_response_payloads(loop_payload: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for dispatch in loop_payload.get("dispatches", []):
        if not isinstance(dispatch, dict):
            continue
        for result in dispatch.get("command_results", []):
            if not isinstance(result, dict):
                continue
            stdout = result.get("stdout")
            if not isinstance(stdout, str) or not stdout.strip():
                continue
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def _first_blocker(response_payloads: list[dict[str, Any]]) -> dict[str, Any] | None:
    for payload in response_payloads:
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "")
        if status.upper() not in {"COMPLETED", "PASS"}:
            return {
                "previous_subagent": payload.get("previous_subagent"),
                "status": status,
                "summary": result.get("summary"),
                "evidence": result.get("evidence") if isinstance(result.get("evidence"), list) else [],
            }
    return None


def _find_artifact_suffix(loop_payload: dict[str, Any], suffix: str) -> str | None:
    for artifact in loop_payload.get("artifacts", []):
        if isinstance(artifact, str) and artifact.endswith(suffix):
            return artifact
    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
