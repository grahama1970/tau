"""Local one-panel persona-dream proof command for Tau handoff loops."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

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
    panel_source: Path | None = None,
    panel_repair_work_order: Path | None = None,
    scillm_live_panel: bool = False,
    panel_prompt: str | None = None,
    scillm_image_model: str = "gpt-image-2",
    scillm_image_auth: str = "codex-oauth",
    scillm_image_quality: str = "high",
    scillm_vlm_model: str = "gpt-5.5",
    scillm_base_url: str = "http://127.0.0.1:4001",
) -> dict[str, Any]:
    """Run the local persona-dream panel command loop and write a proof manifest."""

    proof_dir = out_dir.expanduser().resolve()
    proof_dir.mkdir(parents=True, exist_ok=True)
    panel_context = _panel_context(
        panel_evidence,
        proof_dir=proof_dir,
        panel_source=panel_source,
        panel_repair_work_order=panel_repair_work_order,
        scillm_live_panel=scillm_live_panel,
        panel_prompt=panel_prompt,
        scillm_image_model=scillm_image_model,
        scillm_image_auth=scillm_image_auth,
        scillm_image_quality=scillm_image_quality,
        scillm_vlm_model=scillm_vlm_model,
        scillm_base_url=scillm_base_url,
    )
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
    tau_originated_scillm = panel_context.get("scillm_live_panel") == "true"
    consumed_generation_receipt = bool(panel_context.get("image_generation_receipt")) or tau_originated_scillm
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
        "panel_repair_work_order": str(panel_repair_work_order.expanduser().resolve()) if panel_repair_work_order else None,
        "panel_context": panel_context,
        "scillm_originated_inside_tau": tau_originated_scillm,
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
                "Tau panel-creator and panel-reviewer are configured to initiate Scillm calls inside the command loop."
                if tau_originated_scillm
                else "Tau fixture mode does not initiate Scillm calls inside the command loop.",
            ],
            "does_not_prove": [
                (
                    "Tau live Scillm calls completed only if the role receipts show live_call_performed=true."
                    if tau_originated_scillm
                    else "Tau consumed a Scillm image generation receipt, but panel generation was not initiated inside this Tau command."
                    if consumed_generation_receipt
                    else "No new image generation was performed."
                ),
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
    panel_repair_work_order = panel_context.get("panel_repair_work_order")
    if panel_repair_work_order:
        context_artifacts.append(panel_repair_work_order)
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


def _panel_context(
    panel_evidence: Path | None,
    *,
    proof_dir: Path | None = None,
    panel_source: Path | None = None,
    panel_repair_work_order: Path | None = None,
    scillm_live_panel: bool = False,
    panel_prompt: str | None = None,
    scillm_image_model: str = "gpt-image-2",
    scillm_image_auth: str = "codex-oauth",
    scillm_image_quality: str = "high",
    scillm_vlm_model: str = "gpt-5.5",
    scillm_base_url: str = "http://127.0.0.1:4001",
) -> dict[str, str]:
    if panel_repair_work_order is not None:
        if panel_evidence is not None or panel_source is not None:
            raise RuntimeError("--panel-repair-work-order cannot be combined with --panel-evidence or --panel-source")
        if proof_dir is None:
            raise RuntimeError("proof_dir is required for panel repair work-order mode")
        return _panel_context_from_repair_work_order(
            panel_repair_work_order,
            proof_dir=proof_dir,
            scillm_image_model=scillm_image_model,
            scillm_image_auth=scillm_image_auth,
            scillm_image_quality=scillm_image_quality,
            scillm_vlm_model=scillm_vlm_model,
            scillm_base_url=scillm_base_url,
        )
    source_context = _source_panel_context(panel_source)
    if scillm_live_panel:
        if panel_evidence is not None:
            raise RuntimeError("--scillm-live-panel and --panel-evidence are mutually exclusive")
        if proof_dir is None:
            raise RuntimeError("proof_dir is required for scillm live panel mode")
        run_root = proof_dir / "scillm-panel"
        return {
            "panel_id": "panel_001",
            "run_root": str(run_root.resolve()),
            "image_path": str((run_root / "panel_001.png").resolve()),
            "visual_review_receipt": str((run_root / "visual_review_receipt.json").resolve()),
            "panel_prompt": panel_prompt or "",
            "scillm_live_panel": "true",
            "scillm_image_model": scillm_image_model,
            "scillm_image_auth": scillm_image_auth,
            "scillm_image_quality": scillm_image_quality,
            "scillm_vlm_model": scillm_vlm_model,
            "scillm_base_url": scillm_base_url,
        } | source_context
    if panel_evidence is None:
        return {
            "panel_id": "panel_001",
            "run_root": str(DEFAULT_FIXTURE_ROOT.resolve()),
            "image_path": str(DEFAULT_IMAGE.resolve()),
            "visual_review_receipt": str(DEFAULT_VISUAL_REVIEW.resolve()),
        } | source_context

    evidence_path = panel_evidence.expanduser().resolve()
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"panel evidence root must be an object: {evidence_path}")
    panel = payload.get("persona_dream_panel", payload)
    if not isinstance(panel, dict):
        raise RuntimeError(f"panel evidence persona_dream_panel must be an object: {evidence_path}")

    base = evidence_path.parent
    run_root = _required_path_text(panel, "run_root", base=base)
    panel_context = {
        "panel_id": _required_text(panel, "panel_id"),
        "run_root": run_root,
        "image_path": _required_path_text(panel, "image_path", base=Path(run_root)),
        "visual_review_receipt": _required_path_text(
            panel,
            "visual_review_receipt",
            base=Path(run_root),
        ),
    }
    image_generation_receipt = _optional_path_text(
        panel,
        "image_generation_receipt",
        base=Path(run_root),
    )
    if image_generation_receipt:
        panel_context["image_generation_receipt"] = image_generation_receipt
    for key in (
        "panel_prompt",
        "source_panel",
        "source_panel_summary",
        "source_script_coverage",
        "post_generation_script_coverage",
        "provider_media_probe_receipt",
        "provider_media_url",
    ):
        value = panel.get(key)
        if isinstance(value, str) and value.strip():
            panel_context[key] = _resolve_optional_artifact(value, base=Path(run_root)) if key.endswith("_receipt") else value
    return panel_context | source_context


def _panel_context_from_repair_work_order(
    work_order_path: Path,
    *,
    proof_dir: Path,
    scillm_image_model: str,
    scillm_image_auth: str,
    scillm_image_quality: str,
    scillm_vlm_model: str,
    scillm_base_url: str,
) -> dict[str, str]:
    resolved = work_order_path.expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"panel repair work order must be a JSON object: {resolved}")
    if payload.get("schema") != "persona_dream.panel_repair_work_order.v1":
        raise RuntimeError(f"wrong panel repair work order schema: {payload.get('schema')}")
    source_paths = payload.get("source_paths")
    if not isinstance(source_paths, dict):
        raise RuntimeError("panel repair work order missing source_paths")
    run_root = _required_path_text(source_paths, "run_root", base=resolved.parent)
    panel_id = str(payload.get("panel_id") or "panel_01")
    artifacts_dir = Path(run_root) / "artifacts"
    receipts_dir = Path(run_root) / "receipts"
    candidate = payload.get("current_candidate")
    candidate_image = ""
    if isinstance(candidate, dict) and isinstance(candidate.get("image_path"), str):
        candidate_image = _resolve_optional_artifact(candidate["image_path"], base=Path(run_root))
    output_image = artifacts_dir / f"{panel_id}_scillm_panel.png"
    visual_review = receipts_dir / "visual_review_receipt.json"

    source_summary = _repair_work_order_source_summary(payload, run_root=Path(run_root))
    prompt = (
        "Generate one photorealistic cinematic persona-dream panel from this "
        "panel repair work order. This is a final usable panel, not a flat "
        "storyboard contract. Preserve the story beat, required characters, "
        "props, environment, dynamic behaviors, physical interactions, scale, "
        "and continuity ledger. No text overlays, captions, logos, UI chrome, "
        "collage borders, or illustrated/comic styling. Do not use Nano Banana "
        "or Gemini visual style.\n\n"
        f"Panel repair work order: {resolved}\n"
        f"Candidate storyboard/reference image: {candidate_image or 'none'}\n"
        f"{source_summary}"
    )
    return {
        "panel_id": panel_id,
        "run_root": run_root,
        "image_path": str(output_image.resolve()),
        "visual_review_receipt": str(visual_review.resolve()),
        "panel_prompt": prompt,
        "scillm_live_panel": "true",
        "scillm_image_model": scillm_image_model,
        "scillm_image_auth": scillm_image_auth,
        "scillm_image_quality": scillm_image_quality,
        "scillm_vlm_model": scillm_vlm_model,
        "scillm_base_url": scillm_base_url,
        "source_panel": str(resolved),
        "source_panel_summary": source_summary,
        "source_script_coverage": "panel repair work order includes storyboard, continuity, and required subagent repair contract",
        "post_generation_script_coverage": "post-generation script coverage must reconcile generated panel against the work order and continuity ledger",
        "write_receipts_to_panel_run_root": "true",
        "panel_repair_work_order": str(resolved),
    }


def _repair_work_order_source_summary(payload: Mapping[str, Any], *, run_root: Path) -> str:
    lines: list[str] = []
    for key in ("purpose", "acceptance_criteria", "forbidden_actions", "required_default_action", "current_candidate"):
        value = payload.get(key)
        if value is not None:
            lines.append(f"- {key}: {json.dumps(value, sort_keys=True)}")
    source_paths = payload.get("source_paths")
    if isinstance(source_paths, Mapping):
        for key in ("storyboard_panel_receipt", "continuity_ledger", "work_order"):
            value = source_paths.get(key)
            if isinstance(value, str) and value.strip():
                path = Path(value).expanduser()
                if not path.is_absolute():
                    path = run_root / path
                if path.exists():
                    try:
                        loaded = json.loads(path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        loaded = {"path": str(path)}
                    lines.append(f"- {key}: {json.dumps(loaded, sort_keys=True)}")
    return "\n".join(lines)


def _source_panel_context(panel_source: Path | None) -> dict[str, str]:
    if panel_source is None:
        return {}
    source_path = panel_source.expanduser().resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"panel source must be a JSON object: {source_path}")
    panel = payload.get("persona_dream_panel", payload)
    if not isinstance(panel, dict):
        raise RuntimeError(f"panel source persona_dream_panel must be an object: {source_path}")
    panel_id = str(panel.get("panel_id") or panel.get("id") or panel.get("panel") or "panel_001")
    source_summary = _source_summary(panel)
    context = {
        "panel_id": panel_id,
        "source_panel": str(source_path),
        "source_panel_summary": source_summary,
        "source_script_coverage": "source panel includes script/beat/entity requirements",
        "post_generation_script_coverage": "post-generation script coverage must reconcile generated image with source panel requirements",
    }
    prompt = panel.get("panel_prompt")
    if isinstance(prompt, str) and prompt.strip():
        context["panel_prompt"] = prompt.strip()
    elif source_summary:
        context["panel_prompt"] = (
            "Generate one photorealistic cinematic persona-dream storyboard panel from "
            "this source work order. Preserve the named action, required entities, "
            "props, environment, motion cues, and script beat. No text overlays, "
            f"captions, logos, or UI chrome.\n\nSource panel {panel_id}:\n{source_summary}"
        )
    for key in ("provider_media_probe_receipt", "provider_media_url"):
        value = panel.get(key)
        if isinstance(value, str) and value.strip():
            context[key] = _resolve_optional_artifact(value, base=source_path.parent) if key.endswith("_receipt") else value
    return context


def _source_summary(payload: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for key in (
        "title",
        "action",
        "beat",
        "description",
        "script",
        "dialogue",
        "shot",
        "camera",
        "required_visible_entities",
        "required_entities",
        "required_props",
        "required_environment",
        "required_dynamic_behaviors",
        "motion_cues",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value if str(item).strip())
        elif isinstance(value, dict):
            rendered = json.dumps(value, sort_keys=True)
        else:
            rendered = str(value)
        if rendered.strip():
            lines.append(f"- {key}: {rendered.strip()}")
    return "\n".join(lines)


def _resolve_optional_artifact(value: str, *, base: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


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


def _optional_path_text(payload: dict[str, Any], key: str, *, base: Path) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"panel evidence field must be a non-empty string when provided: {key}")
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
