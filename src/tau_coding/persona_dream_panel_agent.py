"""Persona-dream panel command helpers for Tau handoff-command-loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PERSONA_DREAM_ROOT = Path("/home/graham/workspace/experiments/agent-skills/skills/persona-dream")
DEFAULT_FIXTURE_ROOT = PERSONA_DREAM_ROOT / "fixtures/one_scene_kling_dry_run"
DEFAULT_IMAGE = DEFAULT_FIXTURE_ROOT / "artifacts/panel_001_reference.png"
DEFAULT_VISUAL_REVIEW = DEFAULT_FIXTURE_ROOT / "receipts/visual_review_receipt.json"


def run_persona_dream_panel_agent(role: str) -> dict[str, Any]:
    """Run one bounded persona-dream panel role and return a Tau handoff."""

    start_payload = _read_stdin_handoff()
    selected_agent = os.environ.get("TAU_HANDOFF_SELECTED_AGENT") or role
    if selected_agent != role:
        raise RuntimeError(f"selected agent {selected_agent!r} does not match role {role!r}")
    artifact_dir = _artifact_dir(role)
    panel = _panel_context(start_payload)

    if role == "panel-creator":
        return _run_panel_creator(start_payload, panel, artifact_dir)
    if role == "panel-reviewer":
        return _run_panel_reviewer(start_payload, panel, artifact_dir)
    if role == "persona-dream-panel-repair-gate":
        return _run_panel_repair_gate(start_payload, panel, artifact_dir)
    raise RuntimeError(f"unsupported persona-dream panel role: {role}")


def _run_panel_creator(
    start_payload: Mapping[str, Any],
    panel: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    image_path = Path(str(panel["image_path"])).resolve()
    image_hash = _sha256(image_path)
    receipt = {
        "schema": "tau.persona_dream.panel_creator_receipt.v1",
        "created_at": _now_iso(),
        "role": "panel-creator",
        "panel_id": panel["panel_id"],
        "status": "DRY_RUN_REFERENCE_LOCKED",
        "generated_image_path": str(image_path),
        "sha256": image_hash,
        "source": "persona-dream one-scene local reference fixture",
        "live_call_performed": False,
        "paid_call_performed": False,
        "public_upload_performed": False,
        "kling_api_call_performed": False,
        "claims": {
            "proves": [
                "panel-creator command consumed a Tau handoff",
                "panel-creator command wrote a concrete receipt with a local image artifact path and hash",
            ],
            "does_not_prove": [
                "no new image generation was performed",
                "no provider-ready panel is claimed",
            ],
        },
    }
    receipt_path = artifact_dir / "panel_creator_receipt.json"
    _write_json(receipt_path, receipt)
    _write_json(artifact_dir / "request.json", {"role": "panel-creator", "panel": dict(panel)})
    response_path = artifact_dir / "response.json"

    handoff = _handoff(
        start_payload,
        previous_subagent="panel-creator",
        result_status="COMPLETED",
        result_summary=(
            "Panel Creator wrote a local panel_creator_receipt with image artifact path "
            "and hash; no public upload, Kling call, paid call, or new generation occurred."
        ),
        evidence=[
            str(receipt_path),
            f"{image_path} sha256:{image_hash.removeprefix('sha256:')}",
        ],
        context_summary="Panel Creator locked a one-panel local reference artifact for review.",
        artifacts=[str(receipt_path), str(image_path)],
        rationale=(
            "A concrete local panel artifact and creator receipt now exist, so the next "
            "bounded step is independent panel review."
        ),
        next_agent="panel-reviewer",
        next_executor="local",
        next_reason="Panel Reviewer must inspect or fail closed on the creator receipt.",
        required_evidence=(
            "Panel Reviewer writes panel_reviewer_receipt.json or visual_review_receipt evidence."
        ),
        stop_condition="Panel Reviewer emits a Tau handoff with receipt paths.",
    )
    _write_json(response_path, handoff)
    return handoff


def _run_panel_reviewer(
    start_payload: Mapping[str, Any],
    panel: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    visual_review_path = Path(str(panel["visual_review_receipt"])).resolve()
    visual_review = _read_json(visual_review_path)
    status = str(visual_review.get("status") or visual_review.get("verdict") or "UNKNOWN")
    receipt = {
        "schema": "tau.persona_dream.panel_reviewer_receipt.v1",
        "created_at": _now_iso(),
        "role": "panel-reviewer",
        "panel_id": panel["panel_id"],
        "status": "INSUFFICIENT_EVIDENCE" if status != "PASS" else "PASS",
        "reviewer_source": str(visual_review_path),
        "reviewer_source_status": status,
        "reviewer_readonly": True,
        "blocking_findings": [
            "No live WebGPT/VLM review was performed in this Tau command-loop proof."
        ]
        if status != "PASS"
        else [],
        "passed_entities": [],
        "live_call_performed": False,
        "paid_call_performed": False,
        "public_upload_performed": False,
        "kling_api_call_performed": False,
        "claims": {
            "proves": [
                "panel-reviewer command consumed the creator route",
                "panel-reviewer command wrote a read-only receipt tied to a concrete review source",
            ],
            "does_not_prove": [
                "no new WebGPT or VLM visual review was performed",
                "no visual PASS is claimed when source status is not PASS",
            ],
        },
    }
    receipt_path = artifact_dir / "panel_reviewer_receipt.json"
    _write_json(receipt_path, receipt)
    _write_json(artifact_dir / "request.json", {"role": "panel-reviewer", "panel": dict(panel)})
    response_path = artifact_dir / "response.json"

    handoff = _handoff(
        start_payload,
        previous_subagent="panel-reviewer",
        result_status=receipt["status"],
        result_summary=(
            "Panel Reviewer wrote a read-only receipt and failed closed because the "
            "available visual review source is not a live PASS verdict."
        ),
        evidence=[str(receipt_path), str(visual_review_path)],
        context_summary="Panel Reviewer consumed the creator artifact and review source.",
        artifacts=[str(receipt_path), str(visual_review_path)],
        rationale=(
            "The repair gate owns terminal panel eligibility and must consume this "
            "reviewer evidence before any provider readiness claim."
        ),
        next_agent="persona-dream-panel-repair-gate",
        next_executor="local",
        next_reason="Repair gate must consolidate creator/reviewer evidence and fail closed.",
        required_evidence="Repair gate writes panel_repair_gate_receipt.json with provider_eligibility=false unless every subgate passes.",
        stop_condition="Repair gate emits a terminal Tau handoff with receipt paths.",
    )
    _write_json(response_path, handoff)
    return handoff


def _run_panel_repair_gate(
    start_payload: Mapping[str, Any],
    panel: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    receipt = {
        "schema": "tau.persona_dream.panel_repair_gate_receipt.v1",
        "created_at": _now_iso(),
        "role": "persona-dream-panel-repair-gate",
        "panel_id": panel["panel_id"],
        "status": "BLOCKED_PENDING_INDEPENDENT_VERIFICATION",
        "provider_eligibility": False,
        "provider_packet_status": "DRY_RUN_NOT_LIVE_SUBMITTABLE",
        "remaining_blockers": [
            "live WebGPT/VLM visual PASS receipt is missing",
            "real panel-creator generation receipt is missing",
            "loop final receipt for a panel repair attempt is missing",
        ],
        "source_artifacts": _artifact_list(start_payload),
        "live_call_performed": False,
        "paid_call_performed": False,
        "public_upload_performed": False,
        "kling_api_call_performed": False,
        "claims": {
            "proves": [
                "repair-gate command consumed the reviewer route",
                "repair-gate command wrote a terminal receipt with provider_eligibility=false",
            ],
            "does_not_prove": [
                "no full panel repair loop ran",
                "no provider-ready packet is claimed",
                "no Kling or paid provider call occurred",
            ],
        },
    }
    receipt_path = artifact_dir / "panel_repair_gate_receipt.json"
    _write_json(receipt_path, receipt)
    _write_json(artifact_dir / "request.json", {"role": "persona-dream-panel-repair-gate", "panel": dict(panel)})
    response_path = artifact_dir / "response.json"

    handoff = _handoff(
        start_payload,
        previous_subagent="persona-dream-panel-repair-gate",
        result_status="BLOCKED",
        result_summary=(
            "Persona Dream Panel Repair Gate wrote a terminal blocker receipt with "
            "provider_eligibility=false; no public upload, Kling call, paid call, or "
            "provider-ready claim occurred."
        ),
        evidence=[str(receipt_path)],
        context_summary="Repair gate consolidated persona-dream panel evidence and blocked provider readiness.",
        artifacts=[str(receipt_path)],
        rationale=(
            "The bounded one-panel command chain has produced receipts and reached the "
            "first real blocker: independent live visual/generation evidence is absent."
        ),
        next_agent="human",
        next_executor="human",
        next_reason=(
            "Human or outer Persona-Dream harness must supply a live one-panel work order "
            "with creator, reviewer, and loop receipts before further automation."
        ),
        required_evidence=(
            "Concrete one-panel work order plus real creator, visual reviewer, no-overlay, "
            "and loop final receipts."
        ),
        stop_condition="Human provides missing one-panel evidence or routes to a live repair attempt.",
    )
    _write_json(response_path, handoff)
    return handoff


def _handoff(
    start_payload: Mapping[str, Any],
    *,
    previous_subagent: str,
    result_status: str,
    result_summary: str,
    evidence: list[str],
    context_summary: str,
    artifacts: list[str],
    rationale: str,
    next_agent: str,
    next_executor: str,
    next_reason: str,
    required_evidence: str,
    stop_condition: str,
) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": _required_mapping(start_payload, "github"),
        "goal": _required_mapping(start_payload, "goal"),
        "previous_subagent": previous_subagent,
        "context": {
            "summary": context_summary,
            "artifacts": _artifact_list(start_payload) + artifacts,
        },
        "result": {
            "status": result_status,
            "summary": result_summary,
            "evidence": evidence,
        },
        "rationale": rationale,
        "next_agent": {
            "name": next_agent,
            "executor": next_executor,
            "reason": next_reason,
        },
        "required_evidence": [required_evidence],
        "stop_condition": stop_condition,
    }


def _panel_context(start_payload: Mapping[str, Any]) -> dict[str, str]:
    context = start_payload.get("context")
    panel = context.get("persona_dream_panel") if isinstance(context, Mapping) else None
    if not isinstance(panel, Mapping):
        panel = {}
    return {
        "panel_id": str(panel.get("panel_id") or "panel_001"),
        "run_root": str(panel.get("run_root") or DEFAULT_FIXTURE_ROOT),
        "image_path": str(panel.get("image_path") or DEFAULT_IMAGE),
        "visual_review_receipt": str(panel.get("visual_review_receipt") or DEFAULT_VISUAL_REVIEW),
    }


def _artifact_list(payload: Mapping[str, Any]) -> list[str]:
    context = payload.get("context")
    artifacts = context.get("artifacts") if isinstance(context, Mapping) else None
    return [str(item) for item in artifacts] if isinstance(artifacts, list) else []


def _artifact_dir(role: str) -> Path:
    value = os.environ.get("TAU_HANDOFF_COMMAND_ARTIFACT_DIR")
    if value:
        path = Path(value)
    else:
        path = Path("/tmp") / f"tau-persona-dream-panel-{role}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _read_stdin_handoff() -> dict[str, Any]:
    try:
        payload = json.loads(input())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"stdin handoff JSON is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stdin handoff JSON root must be an object")
    return payload


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"stdin handoff missing {key} object")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing JSON artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON artifact: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON artifact must be an object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing panel image artifact: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    payload = run_persona_dream_panel_agent(args.role)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
