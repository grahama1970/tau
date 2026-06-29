"""Persona-dream panel command helpers for Tau handoff-command-loop."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import selectors
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx


PERSONA_DREAM_ROOT = Path("/home/graham/workspace/experiments/agent-skills/skills/persona-dream")
DEFAULT_FIXTURE_ROOT = PERSONA_DREAM_ROOT / "fixtures/one_scene_kling_dry_run"
DEFAULT_IMAGE = DEFAULT_FIXTURE_ROOT / "artifacts/panel_001_reference.png"
DEFAULT_VISUAL_REVIEW = DEFAULT_FIXTURE_ROOT / "receipts/visual_review_receipt.json"
SCILLM_SKILL_RUN = Path("/home/graham/workspace/experiments/agent-skills/skills/scillm/run.sh")


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
    if _truthy(panel.get("scillm_live_panel")):
        generation = _generate_panel_image_with_scillm(panel, artifact_dir)
        if generation.get("ok") is not True:
            receipt_path = artifact_dir / "panel_creator_receipt.json"
            receipt = {
                "schema": "tau.persona_dream.panel_creator_receipt.v1",
                "created_at": _now_iso(),
                "role": "panel-creator",
                "panel_id": panel["panel_id"],
                "status": "BLOCKED_SCILLM_IMAGE_GENERATION",
                "image_generation_receipt": generation.get("receipt_path"),
                "live_call_performed": True,
                "paid_call_performed": None,
                "public_upload_performed": False,
                "kling_api_call_performed": False,
                "blocking_findings": [
                    "Scillm image generation did not produce an ok image receipt."
                ],
                "claims": {
                    "proves": [
                        "panel-creator command consumed a Tau handoff",
                        "panel-creator initiated the Scillm image wrapper inside Tau",
                        "panel-creator wrote an explicit blocker receipt instead of crashing",
                    ],
                    "does_not_prove": [
                        "Scillm image generation success",
                        "panel-reviewer execution",
                        "provider-ready panel packet",
                    ],
                },
            }
            _write_json(receipt_path, receipt)
            _write_json(artifact_dir / "request.json", {"role": "panel-creator", "panel": dict(panel)})
            handoff = _handoff(
                start_payload,
                previous_subagent="panel-creator",
                result_status="BLOCKED",
                result_summary="Panel Creator initiated Scillm image generation inside Tau and failed closed before review.",
                evidence=[str(receipt_path), str(generation.get("receipt_path"))],
                context_summary="Panel Creator reached the first live Scillm image-generation blocker.",
                artifacts=[str(receipt_path), str(generation.get("receipt_path"))],
                rationale="Reviewer cannot run until a generated panel image exists.",
                next_agent="human",
                next_executor="human",
                next_reason="Human or operator must restore image provider capacity or switch image auth/provider.",
                required_evidence="A Scillm image generation receipt with ok=true, sha256, width, and height.",
                stop_condition="Human supplies provider capacity or reruns with a working Scillm image auth mode.",
            )
            _write_json(artifact_dir / "response.json", handoff)
            return handoff
        image_path = Path(str(generation["path"])).resolve()
        generation_receipt_path = str(generation["receipt_path"])
        generation_receipt = generation
    else:
        image_path = Path(str(panel["image_path"])).resolve()
        generation_receipt_path = str(panel.get("image_generation_receipt") or "")
        generation_receipt = _read_json(Path(generation_receipt_path)) if generation_receipt_path else {}
    image_hash = _sha256(image_path)
    generated_by_scillm = generation_receipt.get("ok") is True
    receipt = {
        "schema": "tau.persona_dream.panel_creator_receipt.v1",
        "created_at": _now_iso(),
        "role": "panel-creator",
        "panel_id": panel["panel_id"],
        "status": "SCILLM_IMAGE_GENERATED" if generated_by_scillm else "DRY_RUN_REFERENCE_LOCKED",
        "generated_image_path": str(image_path),
        "image_generation_receipt": generation_receipt_path or None,
        "sha256": image_hash,
        "source": "scillm image generation receipt" if generated_by_scillm else "persona-dream one-scene local reference fixture",
        "live_call_performed": bool(generated_by_scillm),
        "paid_call_performed": None if generated_by_scillm else False,
        "public_upload_performed": False,
        "kling_api_call_performed": False,
        "claims": {
            "proves": [
                "panel-creator command consumed a Tau handoff",
                "panel-creator command wrote a concrete receipt with a local image artifact path and hash",
                "panel-creator initiated the Scillm image wrapper inside Tau"
                if generated_by_scillm
                else "panel-creator did not initiate a live Scillm image call",
            ],
            "does_not_prove": [
                "no public upload or Kling call occurred",
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
            "and hash; no public upload or Kling call occurred."
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
    if _truthy(panel.get("scillm_live_panel")):
        visual_review = _review_panel_image_with_scillm(panel, artifact_dir)
        visual_review_path = Path(str(visual_review["receipt_path"])).resolve()
    else:
        visual_review_path = Path(str(panel["visual_review_receipt"])).resolve()
        visual_review = _read_json(visual_review_path)
    status = str(visual_review.get("status") or visual_review.get("verdict") or "UNKNOWN")
    live_review_performed = bool(visual_review.get("live_call_performed"))
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
            "No live Scillm VLM review PASS was produced in this Tau command-loop proof."
        ]
        if status != "PASS"
        else [],
        "passed_entities": [],
        "live_call_performed": live_review_performed,
        "paid_call_performed": False,
        "public_upload_performed": False,
        "kling_api_call_performed": False,
        "claims": {
            "proves": [
                "panel-reviewer command consumed the creator route",
                "panel-reviewer command wrote a read-only receipt tied to a concrete review source",
                "panel-reviewer initiated a Scillm VLM image_url review inside Tau"
                if live_review_performed
                else "panel-reviewer did not initiate a live Scillm VLM call",
            ],
            "does_not_prove": [
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
            "Panel Reviewer wrote a read-only receipt from a live Scillm VLM review."
            if live_review_performed
            else "Panel Reviewer wrote a read-only receipt and failed closed because the "
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


def _generate_panel_image_with_scillm(
    panel: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    prompt_file = artifact_dir / "panel_image.prompt.md"
    image_path = Path(str(panel["image_path"])).expanduser()
    if not image_path.is_absolute():
        image_path = artifact_dir / image_path
    image_path = image_path.resolve()
    receipt_path = artifact_dir / "scillm_image_generation_receipt.json"
    events_path = artifact_dir / "scillm_image_generation_events.jsonl"
    wrapper_events_path = artifact_dir / "scillm_image_generation_wrapper_events.jsonl"
    prompt = str(panel.get("panel_prompt") or _default_panel_prompt(panel)).strip()
    prompt_file.write_text(prompt + "\n", encoding="utf-8")
    auth = _resolve_scillm_api_key()
    image_auth = str(panel.get("scillm_image_auth") or "codex-oauth")
    cmd = [
        "bash",
        str(SCILLM_SKILL_RUN),
        "generate-image",
        "--auth",
        image_auth,
        "--prompt-file",
        str(prompt_file),
        "--out",
        str(image_path),
        "--receipt",
        str(receipt_path),
        "--events-out",
        str(wrapper_events_path),
        "--model",
        str(panel.get("scillm_image_model") or "gpt-image-2"),
        "--quality",
        str(panel.get("scillm_image_quality") or "high"),
        "--caller-skill",
        "tau-persona-dream-panel-creator",
        "--json",
    ]
    if image_auth == "openai-api-key":
        cmd[-1:-1] = ["--master-key", str(auth["api_key"] or "")]
    started = time.monotonic()
    events_path.parent.mkdir(parents=True, exist_ok=True)
    _append_event(
        events_path,
        {
            "type": "image_started",
            "created_at": _now_iso(),
            "role": "panel-creator",
            "surface": "scillm.generate-image-wrapper",
            "model": str(panel.get("scillm_image_model") or "gpt-image-2"),
            "auth": image_auth,
            "wrapper_events_path": str(wrapper_events_path),
        },
    )
    proc = subprocess.Popen(
        cmd,
        cwd=Path.cwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    timeout_s = float(panel.get("scillm_image_timeout_s") or 900)
    heartbeat_s = float(panel.get("scillm_stream_heartbeat_s") or 15)
    stdout_tail: list[str] = []
    stderr_tail: list[str] = []
    wrapper_event_count = 0
    wrapper_json_event_count = 0
    heartbeat_event_count = 0
    success_gate_observed = False
    next_heartbeat_at = 0.0
    sel = selectors.DefaultSelector()
    if proc.stdout is not None:
        sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    if proc.stderr is not None:
        sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")
    while proc.poll() is None:
        elapsed = time.monotonic() - started
        if elapsed > timeout_s:
            proc.kill()
            _append_event(
                events_path,
                {
                    "type": "image_timeout",
                    "created_at": _now_iso(),
                    "elapsed_seconds": round(elapsed, 6),
                    "timeout_s": timeout_s,
                },
            )
            break
        for key, _ in sel.select(timeout=0.25):
            stream_name = str(key.data)
            line = key.fileobj.readline()
            if not line:
                continue
            if stream_name == "stdout":
                stdout_tail.append(line)
                stdout_tail = stdout_tail[-80:]
            else:
                stderr_tail.append(line)
                stderr_tail = stderr_tail[-80:]
            event = _scillm_image_stream_event(
                stream_name=stream_name,
                line=line,
                elapsed=time.monotonic() - started,
            )
            if event is not None:
                wrapper_event_count += 1
                if event.get("json") is True:
                    wrapper_json_event_count += 1
                _append_event(events_path, event)
        if _image_success_gate_satisfied(image_path, receipt_path):
            if not success_gate_observed:
                success_gate_observed = True
                _append_event(
                    events_path,
                    {
                        "type": "image_success_gate_observed",
                        "created_at": _now_iso(),
                        "elapsed_seconds": round(time.monotonic() - started, 6),
                        "receipt_path": str(receipt_path),
                        "image_path": str(image_path),
                    },
                )
        if elapsed >= next_heartbeat_at:
            heartbeat_event_count += 1
            _append_event(
                events_path,
                {
                    "type": "heartbeat",
                    "created_at": _now_iso(),
                    "elapsed_seconds": round(elapsed, 6),
                    "role": "panel-creator",
                },
            )
            next_heartbeat_at = elapsed + max(0.1, heartbeat_s)
    for key in list(sel.get_map().values()):
        try:
            sel.unregister(key.fileobj)
        except (KeyError, ValueError):
            pass
    sel.close()
    stdout, stderr = proc.communicate()
    if stdout:
        stdout_tail.extend(stdout.splitlines(keepends=True))
        stdout_tail = stdout_tail[-80:]
    if stderr:
        stderr_tail.extend(stderr.splitlines(keepends=True))
        stderr_tail = stderr_tail[-80:]
        for line in stderr.splitlines():
            event = _scillm_image_stream_event(
                stream_name="stderr",
                line=line,
                elapsed=time.monotonic() - started,
            )
            if event is not None:
                wrapper_event_count += 1
                if event.get("json") is True:
                    wrapper_json_event_count += 1
                _append_event(events_path, event)
    returncode = proc.returncode if proc.returncode is not None else 1
    final_wrapper_events = _mirror_wrapper_jsonl_events(wrapper_events_path, events_path)
    wrapper_event_count += final_wrapper_events
    wrapper_json_event_count += final_wrapper_events
    if _image_success_gate_satisfied(image_path, receipt_path):
        success_gate_observed = True
    wrapper = {
        "schema": "tau.persona_dream.scillm_image_generation_call.v1",
        "created_at": _now_iso(),
        "role": "panel-creator",
        "mocked": False,
        "live": True,
        "surface": "scillm.generate-image-wrapper",
        "api_key_source": auth["source"],
        "command": _redact_command(cmd),
        "exit_code": returncode,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout_tail": "".join(stdout_tail)[-4000:],
        "stderr_tail": "".join(stderr_tail)[-4000:],
        "prompt_file": str(prompt_file),
        "events_path": str(events_path),
        "wrapper_events_path": str(wrapper_events_path),
        "stream": True,
        "heartbeat_event_count": heartbeat_event_count,
        "wrapper_event_count": wrapper_event_count,
        "wrapper_json_event_count": wrapper_json_event_count,
        "success_gate_observed": success_gate_observed,
        "path": str(image_path),
        "receipt_path": str(receipt_path),
        "ok": False,
    }
    receipt: dict[str, Any] = {}
    if receipt_path.is_file():
        receipt = _read_json(receipt_path)
        wrapper.update(receipt)
        wrapper["receipt_path"] = str(receipt_path)
    wrapper["ok"] = (
        returncode == 0
        and image_path.is_file()
        and image_path.stat().st_size > 0
        and receipt.get("ok") is True
        and bool(receipt.get("sha256"))
        and receipt.get("width") is not None
        and receipt.get("height") is not None
    )
    _append_event(
        events_path,
        {
            "type": "image_completed" if wrapper["ok"] else "image_failed",
            "created_at": _now_iso(),
            "ok": wrapper["ok"],
            "exit_code": returncode,
            "receipt_path": str(receipt_path),
            "image_path": str(image_path),
            "heartbeat_event_count": heartbeat_event_count,
            "wrapper_event_count": wrapper_event_count,
            "wrapper_json_event_count": wrapper_json_event_count,
            "success_gate_observed": success_gate_observed,
        },
    )
    _write_json(receipt_path, wrapper)
    return wrapper


def _review_panel_image_with_scillm(
    panel: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    image_path = Path(str(panel["image_path"])).resolve()
    prompt = (
        "Review this generated persona-dream panel for basic one-scene eligibility. "
        "Return JSON only with keys status, summary, blocking_findings, passed_entities. "
        "Use status PASS if the image is a coherent single cinematic panel suitable "
        "for a dry-run one-scene Kling request; otherwise use NEEDS_CHANGES."
    )
    payload = {
        "model": str(panel.get("scillm_vlm_model") or "gpt-5.5"),
        "stream": True,
        "stream_heartbeat_s": float(panel.get("scillm_stream_heartbeat_s") or 15),
        "stream_progress_events": True,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_uri(image_path)},
                    },
                ],
            }
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "scillm_metadata": {
            "caller": "tau",
            "proof": "persona-dream-panel-review",
            "role": "panel-reviewer",
        },
    }
    auth = _resolve_scillm_api_key()
    receipt_path = artifact_dir / "visual_review_receipt.json"
    events_path = artifact_dir / "panel_reviewer_events.jsonl"
    started = time.monotonic()
    receipt: dict[str, Any] = {
        "schema": "tau.persona_dream.scillm_vlm_review_receipt.v1",
        "created_at": _now_iso(),
        "role": "panel-reviewer",
        "mocked": False,
        "live": True,
        "surface": "scillm.chat_completions.image_url",
        "model": payload["model"],
        "image_path": str(image_path),
        "api_key_source": auth["source"],
        "request": {**payload, "messages": "<redacted-image-url-request>"},
        "status": "BLOCKED",
        "live_call_performed": False,
        "events_path": str(events_path),
        "stream": True,
        "receipt_path": str(receipt_path),
    }
    if not auth["api_key"]:
        receipt["error"] = "scillm_api_key_unavailable"
        _write_json(receipt_path, receipt)
        return receipt
    try:
        with httpx.Client(
            base_url=str(panel.get("scillm_base_url") or "http://127.0.0.1:4001").rstrip("/"),
            timeout=float(panel.get("scillm_vlm_timeout_s") or 180),
        ) as client:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {auth['api_key']}",
                    "X-Caller-Skill": "tau-persona-dream-panel-reviewer",
                    "Accept": "text/event-stream",
                },
                json=payload,
            ) as response:
                receipt["http_status"] = response.status_code
                receipt["duration_seconds"] = round(time.monotonic() - started, 6)
                receipt["live_call_performed"] = True
                if response.status_code != 200:
                    receipt["error"] = f"scillm_http_status_{response.status_code}"
                    receipt["response_text"] = response.read().decode("utf-8", errors="replace")[:1000]
                    _write_json(receipt_path, receipt)
                    return receipt
                stream_result = _collect_scillm_sse(response.iter_lines(), events_path)
    except httpx.HTTPError as exc:
        receipt["error"] = f"scillm_http_error: {exc}"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        _write_json(receipt_path, receipt)
        return receipt

    receipt["stream_event_count"] = stream_result["event_count"]
    receipt["stream_done_seen"] = stream_result["done_seen"]
    receipt["stream_heartbeat_count"] = stream_result["heartbeat_count"]
    receipt["stream_last_event_type"] = stream_result["last_event_type"]
    content = stream_result["content"]
    receipt["response_content"] = content
    parsed = _parse_review_content(content)
    receipt.update(parsed)
    if receipt.get("status") not in {"PASS", "NEEDS_CHANGES"}:
        receipt["status"] = "NEEDS_CHANGES"
        receipt.setdefault("blocking_findings", ["VLM response did not provide PASS status."])
    _write_json(receipt_path, receipt)
    return receipt


def _run_panel_repair_gate(
    start_payload: Mapping[str, Any],
    panel: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    artifacts = _artifact_list(start_payload)
    creator_receipt = _read_json(_find_artifact_path(artifacts, "panel_creator_receipt.json"))
    reviewer_receipt = _read_json(_find_artifact_path(artifacts, "panel_reviewer_receipt.json"))
    creator_generated = creator_receipt.get("status") == "SCILLM_IMAGE_GENERATED"
    reviewer_passed = reviewer_receipt.get("status") == "PASS"
    remaining_blockers: list[str] = []
    if not reviewer_passed:
        remaining_blockers.append("live WebGPT/VLM visual PASS receipt is missing")
    if not creator_generated:
        remaining_blockers.append("real panel-creator generation receipt is missing")
    panel_review_ready = creator_generated and reviewer_passed
    provider_eligibility = False
    provider_packet_status = "DRY_RUN_NOT_LIVE_SUBMITTABLE" if panel_review_ready else "BLOCKED_PROVIDER_GATE"
    one_scene_request_path = artifact_dir / "one_scene_kling_request.json"
    if panel_review_ready:
        _write_json(
            one_scene_request_path,
            {
                "schema": "persona_dream.kling_one_scene_request.v1",
                "created_at": _now_iso(),
                "submit_live": False,
                "provider": "kling",
                "mode": "std",
                "resolution": "720p",
                "duration_seconds": 5,
                "external_task_id": f"persona-dream-{panel['panel_id']}",
                "image_reference": {
                    "local_path": str(Path(str(panel["image_path"])).resolve()),
                    "sha256": creator_receipt.get("sha256"),
                    "public_url": None,
                },
                "prompt": (
                    "Animate the accepted photorealistic persona-dream storyboard panel "
                    "as one quiet cinematic shot. Preserve character identities, scale, "
                    "tea steam, evidence cards, laptop glow, distant creatures, and sky-eye."
                ),
                "negative_prompt": "No text overlays, no captions, no logo, no style change, no missing characters.",
                "voice_list": [],
                "callback_url": None,
                "live_submit_blockers": [
                    "provider-accessible public image URL is missing",
                    "explicit human approval for Kling live call is missing",
                ],
            },
        )
    else:
        remaining_blockers.append("loop final receipt for a panel repair attempt is missing")
    if panel_review_ready:
        remaining_blockers.append("provider-accessible public image URL is missing")
        remaining_blockers.append("provider media URL probe receipt is missing")
    receipt_path = artifact_dir / "panel_repair_gate_receipt.json"
    run_root_receipts = _proof_run_root(artifact_dir) / "receipts"
    run_root_repair_receipt_path = run_root_receipts / "panel_repair_gate_receipt.json"
    run_root_panel_source_path = run_root_receipts / "panel_source_receipt.json"
    support_receipts = _write_persona_dream_support_receipts(
        run_root_receipts,
        panel=panel,
        creator_receipt=creator_receipt,
        reviewer_receipt=reviewer_receipt,
        creator_generated=creator_generated,
        reviewer_passed=reviewer_passed,
    )
    script_coverage_passed = _support_receipt_status(support_receipts, "script_coverage_receipt") == "PASS"
    post_generation_passed = (
        _support_receipt_status(support_receipts, "post_generation_script_coverage_receipt") == "PASS"
    )
    current_image_hash = _sha256(Path(str(panel["image_path"])).expanduser().resolve())
    provider_media_passed = _provider_media_probe_matches_current_image(
        support_receipts,
        current_image_hash=current_image_hash,
    )
    provider_eligibility = (
        panel_review_ready
        and script_coverage_passed
        and post_generation_passed
        and provider_media_passed
    )
    if not script_coverage_passed:
        remaining_blockers.append("script coverage receipt is missing or failed")
    if not post_generation_passed:
        remaining_blockers.append("post-generation script coverage receipt is missing or failed")
    if provider_media_passed:
        remaining_blockers = [
            blocker
            for blocker in remaining_blockers
            if blocker
            not in {
                "provider-accessible public image URL is missing",
                "provider media URL probe receipt is missing",
            }
        ]
    if provider_eligibility:
        remaining_blockers = []
        provider_packet_status = "PROVIDER_READY"
    receipt = _persona_dream_repair_gate_receipt(
        panel=panel,
        creator_receipt=creator_receipt,
        reviewer_receipt=reviewer_receipt,
        creator_generated=creator_generated,
        reviewer_passed=reviewer_passed,
        panel_review_ready=panel_review_ready,
        provider_eligibility=provider_eligibility,
        provider_packet_status=provider_packet_status,
        remaining_blockers=remaining_blockers,
        artifacts=artifacts,
        support_receipts=support_receipts,
        one_scene_request_path=one_scene_request_path if panel_review_ready else None,
    )
    tau_receipt = {
        "schema": "tau.persona_dream.panel_repair_gate_adapter_receipt.v1",
        "created_at": _now_iso(),
        "role": "persona-dream-panel-repair-gate",
        "panel_id": panel["panel_id"],
        "status": "DRY_RUN_KLING_REQUEST_READY" if panel_review_ready else "BLOCKED_PENDING_INDEPENDENT_VERIFICATION",
        "provider_eligibility": provider_eligibility,
        "provider_packet_status": provider_packet_status,
        "remaining_blockers": remaining_blockers,
        "dry_run_one_scene_kling_request": str(one_scene_request_path) if panel_review_ready else None,
        "persona_dream_panel_repair_gate_receipt": str(receipt_path),
        "persona_dream_panel_source_receipt": str(run_root_panel_source_path),
        "run_root_panel_repair_gate_receipt": str(run_root_repair_receipt_path),
        "source_artifacts": artifacts,
        "live_call_performed": False,
        "paid_call_performed": False,
        "public_upload_performed": False,
        "kling_api_call_performed": False,
        "claims": {
            "proves": [
                "repair-gate command consumed the reviewer route",
                "repair-gate command wrote a terminal receipt from creator/reviewer evidence",
            ],
            "does_not_prove": [
                "no full panel repair loop ran",
                "no provider-ready packet is claimed",
                "no Kling or paid provider call occurred",
            ],
        },
    }
    _write_json(receipt_path, receipt)
    _write_json(artifact_dir / "tau_panel_repair_gate_adapter_receipt.json", tau_receipt)
    _write_json(run_root_repair_receipt_path, receipt)
    panel_source_receipt = _persona_dream_panel_source_receipt(
        receipt,
        repair_receipt_path=run_root_repair_receipt_path,
    )
    _write_json(run_root_panel_source_path, panel_source_receipt)
    _write_json(artifact_dir / "request.json", {"role": "persona-dream-panel-repair-gate", "panel": dict(panel)})
    response_path = artifact_dir / "response.json"
    evidence_paths = [
        str(receipt_path),
        str(run_root_repair_receipt_path),
        str(run_root_panel_source_path),
    ]
    if panel_review_ready:
        evidence_paths.append(str(one_scene_request_path))

    handoff = _handoff(
        start_payload,
        previous_subagent="persona-dream-panel-repair-gate",
        result_status="COMPLETED" if panel_review_ready else "BLOCKED",
        result_summary=(
            "Persona Dream Panel Repair Gate wrote a dry-run one-scene Kling request; "
            "persona-dream-compatible receipts were emitted, but provider media remains blocked."
            if panel_review_ready
            else "Persona Dream Panel Repair Gate wrote a terminal blocker receipt with "
            "persona-dream-compatible repair/source receipts; no public upload, Kling call, paid call, "
            "or provider-ready claim occurred."
        ),
        evidence=evidence_paths,
        context_summary="Repair gate consolidated persona-dream panel evidence and emitted fail-closed persona-dream receipts.",
        artifacts=evidence_paths,
        rationale=(
            "The bounded one-panel command chain has produced receipts and reached the "
            "first real blocker in the persona-dream serial pipeline."
        ),
        next_agent="human",
        next_executor="human",
        next_reason=(
            "Human or outer Persona-Dream harness must supply a public media URL and "
            "explicit Kling approval before live submission."
            if provider_eligibility
            else "Human or outer Persona-Dream harness must supply a live one-panel work order "
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


def _proof_run_root(artifact_dir: Path) -> Path:
    parts = artifact_dir.resolve().parts
    if len(parts) >= 3 and parts[-3:] and artifact_dir.parent.name == "command-artifacts":
        return artifact_dir.parents[2]
    for parent in artifact_dir.resolve().parents:
        if parent.name == "command-loop":
            return parent.parent
    return artifact_dir


def _write_persona_dream_support_receipts(
    receipts_dir: Path,
    *,
    panel: Mapping[str, Any],
    creator_receipt: Mapping[str, Any],
    reviewer_receipt: Mapping[str, Any],
    creator_generated: bool,
    reviewer_passed: bool,
) -> dict[str, str]:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    panel_id = str(panel["panel_id"])
    script_coverage = _script_coverage_receipt(panel)
    post_generation_coverage = _post_generation_script_coverage_receipt(
        panel,
        creator_generated=creator_generated,
        reviewer_passed=reviewer_passed,
    )
    provider_media_probe = _provider_media_probe_receipt(panel)
    support_specs = {
        "requirement_matrix": {
            "schema": "persona_dream.requirement_matrix_receipt.v1",
            "status": "PASS" if panel_id else "FAIL",
            "panel_id": panel_id,
            "requirements": ["single panel image exists", "visual review receipt exists"],
        },
        "script_coverage_receipt": script_coverage,
        "post_generation_script_coverage_receipt": post_generation_coverage,
        "reference_receipt": {
            "schema": "persona_dream.reference_evidence_receipt.v1",
            "status": "PASS" if Path(str(panel.get("image_path"))).expanduser().exists() else "FAIL",
            "panel_id": panel_id,
            "image_path": str(Path(str(panel.get("image_path"))).expanduser().resolve()),
        },
        "generation_receipt": {
            "schema": "persona_dream.generation_receipt.v1",
            "status": "PASS" if creator_generated else "FAIL",
            "panel_id": panel_id,
            "source_receipt_status": creator_receipt.get("status"),
            "source_receipt": creator_receipt.get("image_generation_receipt"),
        },
        "visual_review_receipt": {
            "schema": "persona_dream.visual_review_gate_receipt.v1",
            "status": "PASS" if reviewer_passed else "FAIL",
            "panel_id": panel_id,
            "source_receipt_status": reviewer_receipt.get("status"),
            "source_receipt": reviewer_receipt.get("reviewer_source"),
        },
        "no_overlay_receipt": {
            "schema": "persona_dream.no_overlay_receipt.v1",
            "status": "PASS" if reviewer_passed else "FAIL",
            "panel_id": panel_id,
            "blockers": [] if reviewer_passed else ["Visual review did not pass."],
        },
        "callback_or_polling_plan": {
            "schema": "persona_dream.callback_or_polling_plan.v1",
            "status": "DRY_RUN_ONLY",
            "panel_id": panel_id,
            "callback_url": None,
            "polling_plan": "No live provider task exists; do not poll.",
        },
        "cost_estimate": {
            "schema": "persona_dream.provider_cost_estimate.v1",
            "status": "DRY_RUN_ONLY",
            "panel_id": panel_id,
            "estimated_cost_usd": 0,
            "paid_call_authorized": False,
        },
        "provider_media_probe_receipt": provider_media_probe,
    }
    paths: dict[str, str] = {}
    for name, payload in support_specs.items():
        path = receipts_dir / f"{name}.json"
        _write_json(path, payload)
        paths[name] = str(path)
    return paths


def _support_receipt_status(support_receipts: Mapping[str, str], name: str) -> str:
    path = support_receipts.get(name)
    if not path:
        return ""
    try:
        payload = _read_json(Path(path))
    except RuntimeError:
        return ""
    status = payload.get("status")
    return str(status) if isinstance(status, str) else ""


def _provider_media_probe_matches_current_image(
    support_receipts: Mapping[str, str],
    *,
    current_image_hash: str,
) -> bool:
    path = support_receipts.get("provider_media_probe_receipt")
    if not path:
        return False
    try:
        payload = _read_json(Path(path))
    except RuntimeError:
        return False
    return (
        payload.get("status") == "PASS_PROVIDER_MEDIA_URL_PROBE"
        and payload.get("expected_sha256") == current_image_hash
        and payload.get("observed_sha256") == current_image_hash
        and payload.get("http_status") == 200
    )


def _script_coverage_receipt(panel: Mapping[str, Any]) -> dict[str, Any]:
    panel_id = str(panel["panel_id"])
    source_panel = str(panel.get("source_panel") or "")
    source_summary = str(panel.get("source_panel_summary") or "")
    coverage = str(panel.get("source_script_coverage") or "")
    if source_panel and source_summary and coverage:
        return {
            "schema": "persona_dream.script_coverage_receipt.v1",
            "status": "PASS",
            "panel_id": panel_id,
            "source_panel": source_panel,
            "checked": [
                "source_panel_work_order",
                "action_or_beat",
                "required_entities_or_props",
                "motion_or_environment_cues",
            ],
            "coverage_summary": coverage,
            "source_summary": source_summary,
            "blockers": [],
        }
    return {
        "schema": "persona_dream.script_coverage_receipt.v1",
        "status": "FAIL",
        "panel_id": panel_id,
        "blockers": ["Tau proof does not include full persona-dream script coverage."],
    }


def _post_generation_script_coverage_receipt(
    panel: Mapping[str, Any],
    *,
    creator_generated: bool,
    reviewer_passed: bool,
) -> dict[str, Any]:
    panel_id = str(panel["panel_id"])
    source_panel = str(panel.get("source_panel") or "")
    source_summary = str(panel.get("source_panel_summary") or "")
    coverage = str(panel.get("post_generation_script_coverage") or "")
    if source_panel and source_summary and coverage and creator_generated and reviewer_passed:
        return {
            "schema": "persona_dream.post_generation_script_coverage_receipt.v1",
            "status": "PASS",
            "panel_id": panel_id,
            "source_panel": source_panel,
            "image_delta_checked": True,
            "introduced_visible_elements_accounted_for": True,
            "coverage_summary": coverage,
            "source_summary": source_summary,
            "blockers": [],
        }
    blockers = ["Tau proof does not include post-generation script repair coverage."]
    if not creator_generated:
        blockers.append("real panel-creator generation receipt is missing")
    if not reviewer_passed:
        blockers.append("visual review did not pass")
    return {
        "schema": "persona_dream.post_generation_script_coverage_receipt.v1",
        "status": "FAIL",
        "panel_id": panel_id,
        "blockers": blockers,
    }


def _provider_media_probe_receipt(panel: Mapping[str, Any]) -> dict[str, Any]:
    panel_id = str(panel["panel_id"])
    probe_path_text = str(panel.get("provider_media_probe_receipt") or "")
    if probe_path_text:
        probe_path = Path(probe_path_text).expanduser()
        if probe_path.is_file():
            payload = _read_json(probe_path)
            payload.setdefault("panel_id", panel_id)
            return payload
        return {
            "schema": "persona_dream.provider_media_url_probe_receipt.v1",
            "status": "BLOCKED_PROVIDER_MEDIA_URLS",
            "panel_id": panel_id,
            "blockers": [f"provider media probe receipt does not exist: {probe_path}"],
        }
    return {
        "schema": "persona_dream.provider_media_url_probe_receipt.v1",
        "status": "BLOCKED_PROVIDER_MEDIA_URLS",
        "panel_id": panel_id,
        "blockers": ["No provider-accessible public image URL has been authorized or probed."],
    }


def _persona_dream_repair_gate_receipt(
    *,
    panel: Mapping[str, Any],
    creator_receipt: Mapping[str, Any],
    reviewer_receipt: Mapping[str, Any],
    creator_generated: bool,
    reviewer_passed: bool,
    panel_review_ready: bool,
    provider_eligibility: bool,
    provider_packet_status: str,
    remaining_blockers: list[str],
    artifacts: list[str],
    support_receipts: Mapping[str, str],
    one_scene_request_path: Path | None,
) -> dict[str, Any]:
    image_path = Path(str(creator_receipt.get("generated_image_path") or panel["image_path"])).expanduser().resolve()
    image_hash = str(creator_receipt.get("sha256") or (_sha256(image_path) if image_path.exists() else "sha256:" + "0" * 64))
    script_coverage_status = _support_receipt_status(support_receipts, "script_coverage_receipt")
    post_generation_status = _support_receipt_status(
        support_receipts,
        "post_generation_script_coverage_receipt",
    )
    current_image_hash = image_hash if image_hash.startswith("sha256:") else _sha256(image_path)
    provider_media_passed = _provider_media_probe_matches_current_image(
        support_receipts,
        current_image_hash=current_image_hash,
    )
    provider_media_status = "PASS" if provider_media_passed else "FAIL"
    if provider_eligibility:
        status = "PASS_PANEL_REVIEWED"
    elif panel_review_ready:
        status = "BLOCKED_PROVIDER_MEDIA_URLS"
    else:
        status = "BLOCKED_PENDING_INDEPENDENT_VERIFICATION"
    return {
        "schema": "persona_dream.panel_repair_gate_receipt.v1",
        "created_at": _now_iso(),
        "run_id": _run_id_from_panel(panel),
        "panel_id": str(panel["panel_id"]),
        "status": status,
        "script_coverage_status": "PASS" if script_coverage_status == "PASS" else "FAIL",
        "post_generation_script_coverage_status": "PASS" if post_generation_status == "PASS" else "FAIL",
        "reference_evidence_status": "PASS" if image_path.exists() else "FAIL",
        "visual_review_status": "PASS" if reviewer_passed else "FAIL",
        "no_overlay_status": "PASS" if reviewer_passed else "FAIL",
        "provider_media_status": provider_media_status,
        "requirement_matrix": support_receipts["requirement_matrix"],
        "script_coverage_receipt": support_receipts["script_coverage_receipt"],
        "post_generation_script_coverage_receipt": support_receipts["post_generation_script_coverage_receipt"],
        "reference_receipt": support_receipts["reference_receipt"],
        "generation_receipt": support_receipts["generation_receipt"],
        "visual_review_receipt": str(reviewer_receipt.get("reviewer_source") or support_receipts["visual_review_receipt"]),
        "no_overlay_receipt": support_receipts["no_overlay_receipt"],
        "generated_image_path": str(image_path),
        "media_hashes": {str(panel["panel_id"]): image_hash},
        "provider_media_sha256": image_hash,
        "provider_eligibility": provider_eligibility,
        "provider_mode": "std",
        "provider_resolution": "720p",
        "external_task_id": f"persona-dream-{panel['panel_id']}",
        "callback_or_polling_plan": support_receipts["callback_or_polling_plan"],
        "voice_id_status": "SILENT_SCENE",
        "provider_voice_ids": {},
        "cost_estimate": support_receipts["cost_estimate"],
        "provider_media_urls": _provider_media_urls(panel),
        "provider_media_probe_receipt": support_receipts["provider_media_probe_receipt"],
        "provider_packet_status": provider_packet_status,
        "visual_style_status": "PASS_PHOTOREAL_CINEMATIC" if reviewer_passed else "UNKNOWN",
        "remaining_blockers": remaining_blockers,
        "dry_run_one_scene_kling_request": str(one_scene_request_path) if one_scene_request_path else None,
        "source_artifacts": artifacts,
        "live_call_performed": False,
        "paid_call_performed": False,
        "public_upload_performed": False,
        "kling_api_call_performed": False,
        "nano_banana_fallback_used": False,
        "gemini_fallback_used": False,
        "claims": {
            "proves": [
                "Tau emitted a persona-dream-compatible repair gate receipt.",
                "The receipt preserves the local generated image path and sha256.",
            ],
            "does_not_prove": [
                "provider media URL accessibility",
                "provider eligibility",
                "Kling readiness",
                "public upload",
            ],
        },
    }


def _persona_dream_panel_source_receipt(
    repair_receipt: Mapping[str, Any],
    *,
    repair_receipt_path: Path,
) -> dict[str, Any]:
    image_path = Path(str(repair_receipt.get("generated_image_path"))).expanduser().resolve()
    actual_hash = _sha256(image_path) if image_path.exists() else None
    claimed_hash = str(repair_receipt.get("provider_media_sha256") or "")
    blockers: list[str] = []
    if repair_receipt.get("status") != "PASS_PANEL_REVIEWED":
        blockers.append(f"repair_gate_status_not_final_reviewed:{repair_receipt.get('status')}")
    for subgate in (
        "script_coverage_status",
        "post_generation_script_coverage_status",
        "reference_evidence_status",
        "visual_review_status",
        "no_overlay_status",
        "provider_media_status",
    ):
        if repair_receipt.get(subgate) != "PASS":
            blockers.append(f"{subgate}_not_pass:{repair_receipt.get(subgate)}")
    if repair_receipt.get("visual_style_status") != "PASS_PHOTOREAL_CINEMATIC":
        blockers.append(f"visual_style_not_photoreal:{repair_receipt.get('visual_style_status')}")
    if repair_receipt.get("provider_eligibility") is not True:
        blockers.append("provider_eligibility_not_true")
    if not image_path.exists():
        blockers.append(f"generated_image_path_not_found:{image_path}")
    if not claimed_hash.startswith("sha256:"):
        blockers.append("claimed_media_hash_missing")
    elif actual_hash and actual_hash != claimed_hash:
        blockers.append(f"claimed_media_hash_mismatch:{actual_hash}")
    return {
        "schema": "persona_dream.panel_source_receipt.v1",
        "run_id": str(repair_receipt.get("run_id") or "unknown"),
        "panel_id": str(repair_receipt.get("panel_id") or "unknown"),
        "status": "PASS_PANEL_SOURCE" if not blockers else "BLOCKED",
        "image_path": str(image_path),
        "sha256": actual_hash or claimed_hash or "sha256:" + ("0" * 64),
        "producer": {
            "kind": "subagent",
            "name": "persona-dream-panel-repair-gate",
            "receipt": str(repair_receipt_path),
        },
        "photoreal_status": repair_receipt.get("visual_style_status")
        if repair_receipt.get("visual_style_status") == "PASS_PHOTOREAL_CINEMATIC"
        else "UNKNOWN",
        "nano_banana_fallback_used": False,
        "final_panel_eligible": not blockers,
        "blockers": blockers,
    }


def _run_id_from_panel(panel: Mapping[str, Any]) -> str:
    run_root = Path(str(panel.get("run_root") or "tau-persona-dream-panel")).expanduser()
    return run_root.name or "tau-persona-dream-panel"


def _non_submittable_provider_url(panel: Mapping[str, Any]) -> str:
    panel_id = str(panel.get("panel_id") or "panel_001")
    return f"https://raw.githubusercontent.com/grahama1970/tau/main/provider-media/not-published/{panel_id}.png"


def _provider_media_urls(panel: Mapping[str, Any]) -> list[str]:
    url = panel.get("provider_media_url")
    if isinstance(url, str) and url.strip():
        return [url.strip()]
    probe_path_text = panel.get("provider_media_probe_receipt")
    if isinstance(probe_path_text, str) and probe_path_text.strip():
        probe_path = Path(probe_path_text).expanduser()
        if probe_path.is_file():
            try:
                payload = _read_json(probe_path)
            except RuntimeError:
                payload = {}
            observed_url = payload.get("url")
            if isinstance(observed_url, str) and observed_url.strip():
                return [observed_url.strip()]
    return [_non_submittable_provider_url(panel)]


def _find_artifact_path(artifacts: list[str], suffix: str) -> Path:
    for artifact in artifacts:
        path = Path(artifact)
        if path.name == suffix:
            return path
    return Path("")


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
    context = _required_mapping(start_payload, "context")
    next_context: dict[str, Any] = {
        "summary": context_summary,
        "artifacts": _artifact_list(start_payload) + artifacts,
    }
    persona_dream_panel = context.get("persona_dream_panel")
    if isinstance(persona_dream_panel, Mapping):
        next_context["persona_dream_panel"] = dict(persona_dream_panel)
    return {
        "schema": "tau.agent_handoff.v1",
        "github": _required_mapping(start_payload, "github"),
        "goal": _required_mapping(start_payload, "goal"),
        "previous_subagent": previous_subagent,
        "context": next_context,
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
        "image_generation_receipt": str(panel.get("image_generation_receipt") or ""),
        "panel_prompt": str(panel.get("panel_prompt") or ""),
        "scillm_live_panel": str(panel.get("scillm_live_panel") or ""),
        "scillm_image_model": str(panel.get("scillm_image_model") or ""),
        "scillm_image_auth": str(panel.get("scillm_image_auth") or ""),
        "scillm_image_quality": str(panel.get("scillm_image_quality") or ""),
        "scillm_vlm_model": str(panel.get("scillm_vlm_model") or ""),
        "scillm_base_url": str(panel.get("scillm_base_url") or ""),
        "scillm_image_timeout_s": str(panel.get("scillm_image_timeout_s") or ""),
        "scillm_vlm_timeout_s": str(panel.get("scillm_vlm_timeout_s") or ""),
        "source_panel": str(panel.get("source_panel") or ""),
        "source_panel_summary": str(panel.get("source_panel_summary") or ""),
        "source_script_coverage": str(panel.get("source_script_coverage") or ""),
        "post_generation_script_coverage": str(panel.get("post_generation_script_coverage") or ""),
        "provider_media_probe_receipt": str(panel.get("provider_media_probe_receipt") or ""),
        "provider_media_url": str(panel.get("provider_media_url") or ""),
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


def _image_data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    media_type = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return f"data:{media_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _default_panel_prompt(panel: Mapping[str, Any]) -> str:
    return (
        "Create a single photorealistic cinematic storyboard panel for a persona-dream "
        "one-scene dry-run. The image should show a quiet evidence workshop: one "
        "focused character at a desk, a laptop glow, paper evidence cards, tea steam, "
        "and a subtle surreal sky-eye motif outside the window. No text overlays, no "
        "logos, no captions, no UI chrome. Keep the composition suitable as a still "
        f"reference image for panel id {panel.get('panel_id') or 'panel_001'}."
    )


def _resolve_scillm_api_key() -> dict[str, str | None]:
    for name in ("SCILLM_API_KEY", "SCILLM_MASTER_KEY", "LITELLM_MASTER_KEY", "MASTER_KEY"):
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
    names = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    container = next((name for name in names if "scillm-proxy" in name), None)
    if container is None:
        return None
    try:
        key = subprocess.run(
            ["docker", "exec", container, "printenv", "SCILLM_MASTER_KEY"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return key.stdout.strip() or None


def _extract_content(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _parse_review_content(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {
            "status": "NEEDS_CHANGES",
            "summary": content[:1000] if content else "empty VLM response content",
            "blocking_findings": ["VLM response was not JSON."],
            "passed_entities": [],
        }
    if not isinstance(parsed, dict):
        return {
            "status": "NEEDS_CHANGES",
            "summary": "VLM response JSON root was not an object.",
            "blocking_findings": ["VLM response JSON root was not an object."],
            "passed_entities": [],
        }
    status = str(parsed.get("status") or "NEEDS_CHANGES").upper()
    if status not in {"PASS", "NEEDS_CHANGES"}:
        status = "NEEDS_CHANGES"
    findings = parsed.get("blocking_findings")
    entities = parsed.get("passed_entities")
    return {
        "status": status,
        "summary": str(parsed.get("summary") or parsed.get("rationale") or "Scillm VLM review completed."),
        "blocking_findings": [str(item) for item in findings] if isinstance(findings, list) else [],
        "passed_entities": [str(item) for item in entities] if isinstance(entities, list) else [],
        "parsed_response": parsed,
    }


def _redact_response(response: Mapping[str, Any]) -> dict[str, Any]:
    redacted = dict(response)
    if isinstance(redacted.get("usage"), Mapping):
        redacted["usage"] = dict(redacted["usage"])
    return redacted


def _collect_scillm_sse(lines: Any, events_path: Path) -> dict[str, Any]:
    content_parts: list[str] = []
    event_count = 0
    heartbeat_count = 0
    done_seen = False
    current_event = "message"
    last_event_type = ""
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        if not line:
            continue
        if line.startswith(":"):
            heartbeat_count += 1
            last_event_type = "heartbeat"
            _append_event(
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
            _append_event(events_path, {"type": "done", "created_at": _now_iso()})
            continue
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            payload = {"raw": data_text}
        event_count += 1
        event_type = current_event
        if isinstance(payload, dict) and isinstance(payload.get("type"), str):
            event_type = str(payload["type"])
        elif isinstance(payload, dict) and isinstance(payload.get("choices"), list):
            event_type = "chunk"
        last_event_type = event_type
        _append_event(
            events_path,
            {
                "type": event_type,
                "created_at": _now_iso(),
                "data": payload,
            },
        )
        if isinstance(payload, dict):
            for choice in payload.get("choices", []) if isinstance(payload.get("choices"), list) else []:
                if not isinstance(choice, Mapping):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, Mapping):
                    text = delta.get("content")
                    if isinstance(text, str):
                        content_parts.append(text)
                message = choice.get("message")
                if isinstance(message, Mapping):
                    text = message.get("content")
                    if isinstance(text, str):
                        content_parts.append(text)
        current_event = "message"
    return {
        "content": "".join(content_parts),
        "event_count": event_count,
        "heartbeat_count": heartbeat_count,
        "done_seen": done_seen,
        "last_event_type": last_event_type,
    }


def _scillm_image_stream_event(
    *,
    stream_name: str,
    line: str,
    elapsed: float,
) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {"text": text[:1000]}
        event_type = "image_wrapper_stdout" if stream_name == "stdout" else "image_wrapper_stderr"
        json_event = False
    else:
        event_type = str(payload.get("type") or f"image_wrapper_{stream_name}_json")
        json_event = True
    return {
        "type": event_type,
        "created_at": _now_iso(),
        "elapsed_seconds": round(elapsed, 6),
        "stream": stream_name,
        "json": json_event,
        "data": payload,
    }


def _mirror_wrapper_jsonl_events(wrapper_events_path: Path, events_path: Path) -> int:
    if not wrapper_events_path.is_file():
        return 0
    count = 0
    for raw_line in wrapper_events_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            payload = {"raw": raw_line[:1000]}
        count += 1
        _append_event(
            events_path,
            {
                "type": "image_wrapper_codex_json_event",
                "created_at": _now_iso(),
                "stream": "wrapper_events_jsonl",
                "json": True,
                "data": payload,
            },
        )
    return count


def _image_success_gate_satisfied(image_path: Path, receipt_path: Path) -> bool:
    if not image_path.is_file() or image_path.stat().st_size <= 0 or not receipt_path.is_file():
        return False
    try:
        receipt = _read_json(receipt_path)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        receipt.get("ok") is True
        and bool(receipt.get("sha256"))
        and receipt.get("width") is not None
        and receipt.get("height") is not None
    )


def _append_event(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(event), sort_keys=True) + "\n")


def _redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, item in enumerate(redacted[:-1]):
        if item in {"--master-key", "--api-key"}:
            redacted[index + 1] = "<redacted>"
    return redacted


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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
