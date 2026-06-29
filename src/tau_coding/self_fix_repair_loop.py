"""Live coder-reviewer repair loop proof for Tau self-fix.

This module owns one narrow proof rung: Memory-first intake, a live Scillm
coder call, a real file mutation, a live Scillm reviewer call, deterministic
verification, and rollback on failed proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any

import httpx

from tau_coding.generated_ticket import project_agent_handoff
from tau_coding.subagent_receipt import validate_subagent_receipt

SCHEMA = "tau.self_fix_coder_reviewer_loop_receipt.v1"
SCILLM_CALL_SCHEMA = "tau.self_fix_scillm_call_receipt.v1"


def write_coder_reviewer_repair_loop(
    *,
    repo_root: Path,
    out_dir: Path,
    request: str,
    target_file: Path,
    find_text: str,
    replace_text: str,
    verification_commands: list[str],
    memory_base_url: str = "http://127.0.0.1:8601",
    scillm_base_url: str = "http://127.0.0.1:4001",
    model: str = "gpt-5.5",
    max_review_cycles: int = 3,
    github_repo: str = "grahama1970/tau",
    github_target: str = "local-proof",
    active_goal_hash: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Run one bounded live coder-reviewer repair loop and write receipts."""

    resolved_repo = repo_root.expanduser().resolve()
    resolved_out = out_dir.expanduser().resolve()
    resolved_out.mkdir(parents=True, exist_ok=True)
    resolved_target = (resolved_repo / target_file).resolve()
    run_id = resolved_out.name
    goal_hash = active_goal_hash or _goal_hash(request, str(target_file), find_text, replace_text)
    goal = {
        "goal_id": f"goal-tau-coder-reviewer-loop-{run_id}",
        "goal_version": 1,
        "goal_hash": goal_hash,
    }
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "repo_root": str(resolved_repo),
        "target_file": str(resolved_target),
        "model": model,
        "memory_first": None,
        "checkpoint": None,
        "cycles": [],
        "artifacts": {},
        "errors": [],
        "claims": {
            "proves": [],
            "does_not_prove": [
                "Unbounded autonomous repair.",
                "GitHub issue monitoring or label mutation.",
                "Reviewer semantic correctness beyond the recorded live Scillm call and deterministic checks.",
                "Rollback for unrelated dirty working-tree files.",
            ],
        },
    }

    try:
        _require_safe_target(resolved_repo, resolved_target)
        if not find_text:
            raise RuntimeError("find_text must be non-empty")
        if not verification_commands:
            raise RuntimeError("at least one verification command is required")
        if max_review_cycles < 1:
            raise RuntimeError("max_review_cycles must be at least 1")

        before_text = resolved_target.read_text(encoding="utf-8")
        if find_text not in before_text:
            raise RuntimeError("target file does not contain find_text before repair")
        if replace_text in before_text:
            raise RuntimeError("target file already contains replace_text before repair")

        checkpoint = _git_checkpoint(resolved_repo, resolved_target)
        receipt["checkpoint"] = checkpoint
        memory_first = _memory_preflight(
            memory_base_url=memory_base_url,
            request=request,
            out_dir=resolved_out,
        )
        receipt["memory_first"] = memory_first
        if memory_first.get("ok") is not True:
            raise RuntimeError("Memory-first preflight failed")

        start_handoff = _handoff(
            github_repo=github_repo,
            github_target=github_target,
            goal=goal,
            previous_subagent="human",
            context_summary=request,
            context_artifacts=[
                str(resolved_out / "memory-intent.json"),
                str(resolved_out / "memory-recall.json"),
            ],
            result_status="REQUESTED",
            result_summary="Human requested a live Tau coder-reviewer repair loop.",
            evidence=[str(resolved_out / "memory-intent.json"), str(resolved_out / "memory-recall.json")],
            rationale="Memory-first intake produced context for a bounded coder turn.",
            next_name="coder",
            next_executor="local",
            next_reason="Coder applies the smallest real Tau code repair.",
            required_evidence=[
                "Coder writes a schema-valid receipt.",
                "Target file changes from pre-run content.",
            ],
            stop_condition="Coder hands off to reviewer or Tau fails closed.",
        )
        _write_json(resolved_out / "start-handoff.json", start_handoff)
        _validate_handoff_or_raise(start_handoff, active_goal_hash=goal_hash)

        auth = _resolve_api_key(api_key)
        current_handoff = start_handoff
        final_status = "BLOCKED"
        restored = False

        for cycle in range(1, max_review_cycles + 1):
            cycle_dir = resolved_out / f"cycle-{cycle:03d}"
            coder_dir = cycle_dir / "coder"
            reviewer_dir = cycle_dir / "reviewer"
            coder_dir.mkdir(parents=True, exist_ok=True)
            reviewer_dir.mkdir(parents=True, exist_ok=True)

            coder_call = _call_scillm(
                role="coder",
                model=model,
                scillm_base_url=scillm_base_url,
                timeout_s=90.0,
                api_key=auth["api_key"],
                api_key_source=auth["source"],
                payload={
                    "request": request,
                    "target_file": str(target_file),
                    "find_text": find_text,
                    "replace_text": replace_text,
                    "handoff": current_handoff,
                },
            )
            _write_json(coder_dir / "scillm-call-receipt.json", coder_call)
            if coder_call.get("status") != "PASS":
                raise RuntimeError("coder Scillm call failed")

            patched = _apply_patch_text(resolved_target, find_text=find_text, replace_text=replace_text)
            coder_handoff = _handoff(
                github_repo=github_repo,
                github_target=github_target,
                goal=goal,
                previous_subagent="coder",
                context_summary=f"Coder cycle {cycle} applied the requested target-file repair.",
                context_artifacts=[str(coder_dir / "scillm-call-receipt.json")],
                result_status="COMPLETED",
                result_summary=(
                    f"Coder changed {target_file} by replacing the configured text."
                    if patched
                    else f"Coder found {target_file} already had the replacement text."
                ),
                evidence=[str(coder_dir / "scillm-call-receipt.json"), str(resolved_target)],
                rationale="The target file now needs independent reviewer verification.",
                next_name="reviewer",
                next_executor="local",
                next_reason="Reviewer must inspect the code change and run deterministic checks.",
                required_evidence=[
                    "Reviewer Scillm call receipt.",
                    "Verification command results.",
                ],
                stop_condition="Reviewer returns PASS, NEEDS_CHANGES, or BLOCKED.",
            )
            _write_json(coder_dir / "handoff.json", coder_handoff)
            _validate_handoff_or_raise(coder_handoff, active_goal_hash=goal_hash)
            coder_receipt = _subagent_receipt(
                goal=goal,
                run_id=run_id,
                subagent="coder",
                status="COMPLETED",
                summary=str(coder_handoff["result"]["summary"]),
                artifacts=[str(coder_dir / "scillm-call-receipt.json"), str(coder_dir / "handoff.json")],
                next_subagent="reviewer",
                next_executor="local",
                next_reason="Reviewer validates the coder mutation.",
                stop_condition="Reviewer posts PASS or a fail-closed blocker.",
            )
            _write_json(coder_dir / "tau-subagent-receipt.json", coder_receipt)
            _validate_subagent_receipt_or_raise(coder_receipt, goal_hash)

            verification = _run_verification_commands(
                resolved_repo,
                verification_commands,
                out_dir=reviewer_dir,
            )
            reviewer_call = _call_scillm(
                role="reviewer",
                model=model,
                scillm_base_url=scillm_base_url,
                timeout_s=90.0,
                api_key=auth["api_key"],
                api_key_source=auth["source"],
                payload={
                    "request": request,
                    "target_file": str(target_file),
                    "git_diff": _git_diff(resolved_repo, target_file),
                    "verification": verification,
                    "handoff": coder_handoff,
                },
            )
            _write_json(reviewer_dir / "scillm-call-receipt.json", reviewer_call)
            checks_passed = all(item["exit_code"] == 0 for item in verification)
            reviewer_passed = checks_passed and reviewer_call.get("status") == "PASS"
            reviewer_status = "PASS" if reviewer_passed else "NEEDS_CHANGES"
            next_name = "human" if reviewer_passed else "coder"
            next_executor = "human" if reviewer_passed else "local"
            reviewer_handoff = _handoff(
                github_repo=github_repo,
                github_target=github_target,
                goal=goal,
                previous_subagent="reviewer",
                context_summary=f"Reviewer cycle {cycle} inspected the coder mutation.",
                context_artifacts=[
                    str(reviewer_dir / "scillm-call-receipt.json"),
                    str(reviewer_dir / "verification-results.json"),
                ],
                result_status=reviewer_status,
                result_summary=(
                    "Reviewer accepted the coder change after deterministic checks passed."
                    if reviewer_passed
                    else "Reviewer could not accept the coder change."
                ),
                evidence=[
                    str(reviewer_dir / "scillm-call-receipt.json"),
                    str(reviewer_dir / "verification-results.json"),
                ],
                rationale=(
                    "Live reviewer call and deterministic checks both passed."
                    if reviewer_passed
                    else "The repair needs another coder cycle or operator intervention."
                ),
                next_name=next_name,
                next_executor=next_executor,
                next_reason=(
                    "Human can inspect the proof and commit/push decision."
                    if reviewer_passed
                    else "Coder should address reviewer feedback in the next bounded cycle."
                ),
                required_evidence=[
                    "Final receipt names changed file, checks, and rollback status."
                ],
                stop_condition=(
                    "Human accepts proof and commits/pushes the repair."
                    if reviewer_passed
                    else "Coder emits a revised patch or Tau hits max review cycles."
                ),
            )
            _write_json(reviewer_dir / "handoff.json", reviewer_handoff)
            _validate_handoff_or_raise(reviewer_handoff, active_goal_hash=goal_hash)
            reviewer_receipt = _subagent_receipt(
                goal=goal,
                run_id=run_id,
                subagent="reviewer",
                status=reviewer_status,
                summary=str(reviewer_handoff["result"]["summary"]),
                artifacts=[
                    str(reviewer_dir / "scillm-call-receipt.json"),
                    str(reviewer_dir / "verification-results.json"),
                    str(reviewer_dir / "handoff.json"),
                ],
                next_subagent=next_name,
                next_executor=next_executor,
                next_reason=str(reviewer_handoff["next_agent"]["reason"]),
                stop_condition=str(reviewer_handoff["stop_condition"]),
            )
            _write_json(reviewer_dir / "tau-subagent-receipt.json", reviewer_receipt)
            _validate_subagent_receipt_or_raise(reviewer_receipt, goal_hash)

            cycle_payload = {
                "cycle": cycle,
                "coder": {
                    "scillm_call": str(coder_dir / "scillm-call-receipt.json"),
                    "handoff": str(coder_dir / "handoff.json"),
                    "subagent_receipt": str(coder_dir / "tau-subagent-receipt.json"),
                    "patched": patched,
                },
                "reviewer": {
                    "scillm_call": str(reviewer_dir / "scillm-call-receipt.json"),
                    "handoff": str(reviewer_dir / "handoff.json"),
                    "subagent_receipt": str(reviewer_dir / "tau-subagent-receipt.json"),
                    "verification": str(reviewer_dir / "verification-results.json"),
                    "passed": reviewer_passed,
                },
            }
            receipt["cycles"].append(cycle_payload)
            current_handoff = reviewer_handoff
            if reviewer_passed:
                final_status = "PASS"
                break

        if final_status != "PASS":
            resolved_target.write_text(before_text, encoding="utf-8")
            restored = True
            receipt["rollback"] = {"attempted": True, "restored": True}
            raise RuntimeError("reviewer did not pass within max_review_cycles")

        final_diff = _git_diff(resolved_repo, target_file)
        receipt.update(
            {
                "ok": True,
                "status": "PASS",
                "target_changed": before_text != resolved_target.read_text(encoding="utf-8"),
                "rollback": {"attempted": False, "restored": restored},
                "git": {
                    "checkpoint_head": checkpoint.get("head"),
                    "target_diff": final_diff,
                    "changed_files": _git_changed_files(resolved_repo),
                },
            }
        )
        receipt["claims"]["proves"] = [
            "Tau ran Memory /intent and /recall before the repair loop.",
            "Tau called Scillm for a bounded coder turn.",
            "Coder changed a real Tau repository file.",
            "Tau called Scillm for a bounded reviewer turn.",
            "Reviewer PASS required deterministic verification commands to exit 0.",
            "Tau wrote per-turn handoff and subagent receipt artifacts.",
        ]
    except Exception as exc:
        if "before_text" in locals() and resolved_target.exists():
            try:
                if resolved_target.read_text(encoding="utf-8") != before_text:
                    resolved_target.write_text(before_text, encoding="utf-8")
                    receipt["rollback"] = {"attempted": True, "restored": True}
            except OSError as rollback_exc:
                receipt["rollback"] = {
                    "attempted": True,
                    "restored": False,
                    "error": str(rollback_exc),
                }
        receipt.setdefault("errors", []).append(str(exc))

    receipt["artifacts"] = _artifact_map(resolved_out)
    _write_json(resolved_out / "self-fix-coder-reviewer-loop-receipt.json", receipt)
    return receipt


def _goal_hash(request: str, target_file: str, find_text: str, replace_text: str) -> str:
    material = "\n".join([request, target_file, find_text, replace_text])
    return f"sha256:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _require_safe_target(repo_root: Path, target: Path) -> None:
    try:
        target.relative_to(repo_root)
    except ValueError as exc:
        raise RuntimeError(f"target file must be inside repo root: {target}") from exc
    if not target.exists():
        raise RuntimeError(f"target file does not exist: {target}")
    if not target.is_file():
        raise RuntimeError(f"target path is not a file: {target}")


def _git_checkpoint(repo_root: Path, target: Path) -> dict[str, Any]:
    head = _run_git(repo_root, ["rev-parse", "HEAD"])
    target_rel = str(target.relative_to(repo_root))
    diff_check = subprocess.run(
        ["git", "diff", "--quiet", "--", target_rel],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "head": head["stdout"].strip(),
        "target_file": target_rel,
        "target_clean_at_start": diff_check.returncode == 0,
        "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "rollback_strategy": "restore target file preimage on failed proof",
    }


def _run_git(repo_root: Path, args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return {
        "command": ["git", *args],
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _memory_preflight(*, memory_base_url: str, request: str, out_dir: Path) -> dict[str, Any]:
    with httpx.Client(base_url=memory_base_url, timeout=15.0) as client:
        intent_payload, intent_call = _memory_post(
            client,
            "/intent",
            {"q": request, "scope": "tau", "app": "tau", "fast": True},
        )
        recall_payload, recall_call = _memory_post(
            client,
            "/recall",
            {"q": request, "scope": "tau", "k": 5},
        )
    _write_json(out_dir / "memory-intent.json", intent_payload)
    _write_json(out_dir / "memory-recall.json", recall_payload)
    recall_items = recall_payload.get("items")
    if not isinstance(recall_items, list):
        recall_items = recall_payload.get("results")
    return {
        "ok": bool(intent_call["ok"] and recall_call["ok"]),
        "mocked": False,
        "live": True,
        "memory_base_url": memory_base_url,
        "intent_call": intent_call,
        "recall_call": recall_call,
        "recall_count": len(recall_items) if isinstance(recall_items, list) else 0,
        "artifacts": {
            "intent": str(out_dir / "memory-intent.json"),
            "recall": str(out_dir / "memory-recall.json"),
        },
    }


def _memory_post(
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    try:
        response = client.post(path, json=payload)
    except httpx.HTTPError as exc:
        return {}, {
            "ok": False,
            "path": path,
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    call = {
        "ok": response.status_code < 400,
        "path": path,
        "status_code": response.status_code,
        "duration_seconds": round(time.monotonic() - started, 6),
    }
    try:
        body = response.json()
    except json.JSONDecodeError:
        return {"raw": response.text}, {**call, "ok": False, "error": "non_json_response"}
    return body if isinstance(body, dict) else {"value": body}, call


def _resolve_api_key(explicit: str | None) -> dict[str, str | None]:
    if explicit:
        return {"api_key": explicit, "source": "explicit"}
    for env_name in ("SCILLM_API_KEY", "SCILLM_MASTER_KEY"):
        value = os.environ.get(env_name)
        if value:
            return {"api_key": value, "source": f"env:{env_name}"}
    docker_key = _read_scillm_key_from_docker()
    if docker_key:
        return {"api_key": docker_key, "source": "docker:SCILLM_MASTER_KEY"}
    return {"api_key": None, "source": "unavailable"}


def _read_scillm_key_from_docker() -> str | None:
    if which("docker") is None:
        return None
    ps = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if ps.returncode != 0:
        return None
    for name in ps.stdout.splitlines():
        if "scillm-proxy" not in name:
            continue
        key = subprocess.run(
            ["docker", "exec", name, "printenv", "SCILLM_MASTER_KEY"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if key.returncode == 0 and key.stdout.strip():
            return key.stdout.strip()
    return None


def _call_scillm(
    *,
    role: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    api_key: str | None,
    api_key_source: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    request = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are Tau's bounded {role} subagent. Return concise review notes. "
                    "Do not claim proof unless deterministic artifacts are supplied."
                ),
            },
            {"role": "user", "content": json.dumps(payload, sort_keys=True)},
        ],
        "temperature": 0,
        "scillm_metadata": {
            "caller": "tau",
            "proof": "self-fix-coder-reviewer-loop",
            "role": role,
        },
    }
    receipt: dict[str, Any] = {
        "schema": SCILLM_CALL_SCHEMA,
        "role": role,
        "model": model,
        "scillm_base_url": scillm_base_url,
        "api_key_source": api_key_source,
        "request": {**request, "messages": "<redacted-request-messages>"},
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
    }
    if not api_key:
        receipt["error"] = "scillm_api_key_unavailable"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt
    try:
        response = httpx.post(
            f"{scillm_base_url.rstrip('/')}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Caller-Skill": "tau",
            },
            json=request,
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        receipt["error"] = str(exc)
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt
    receipt["http_status"] = response.status_code
    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
    try:
        body = response.json()
    except json.JSONDecodeError:
        receipt["error"] = "non_json_response"
        receipt["response_excerpt"] = response.text[:1000]
        return receipt
    receipt["response"] = _redact_scillm_response(body)
    if response.status_code >= 400:
        receipt["error"] = f"http_{response.status_code}"
        return receipt
    content = _extract_message_content(body)
    if not content:
        receipt["error"] = "missing_message_content"
        return receipt
    receipt["status"] = "PASS"
    receipt["content_excerpt"] = content[:1200]
    return receipt


def _redact_scillm_response(body: object) -> object:
    if not isinstance(body, dict):
        return body
    redacted = dict(body)
    if "usage" in redacted:
        redacted["usage"] = body.get("usage")
    return redacted


def _extract_message_content(body: object) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
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


def _apply_patch_text(target: Path, *, find_text: str, replace_text: str) -> bool:
    text = target.read_text(encoding="utf-8")
    if replace_text in text and find_text not in text:
        return False
    if find_text not in text:
        raise RuntimeError("find_text missing during coder patch application")
    target.write_text(text.replace(find_text, replace_text, 1), encoding="utf-8")
    return True


def _run_verification_commands(
    repo_root: Path,
    commands: list[str],
    *,
    out_dir: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=repo_root,
            shell=True,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        result = {
            "command": command,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        _write_text(out_dir / f"verification-{index:02d}.stdout.txt", completed.stdout)
        _write_text(out_dir / f"verification-{index:02d}.stderr.txt", completed.stderr)
        results.append(result)
    _write_json(out_dir / "verification-results.json", results)
    return results


def _git_diff(repo_root: Path, target_file: Path) -> str:
    completed = subprocess.run(
        ["git", "diff", "--", str(target_file)],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return completed.stdout


def _git_changed_files(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _handoff(
    *,
    github_repo: str,
    github_target: str,
    goal: dict[str, Any],
    previous_subagent: str,
    context_summary: str,
    context_artifacts: list[str],
    result_status: str,
    result_summary: str,
    evidence: list[str],
    rationale: str,
    next_name: str,
    next_executor: str,
    next_reason: str,
    required_evidence: list[str],
    stop_condition: str,
) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": github_repo, "target": github_target},
        "goal": goal,
        "previous_subagent": previous_subagent,
        "context": {"summary": context_summary, "artifacts": context_artifacts},
        "result": {"status": result_status, "summary": result_summary, "evidence": evidence},
        "rationale": rationale,
        "next_agent": {"name": next_name, "executor": next_executor, "reason": next_reason},
        "required_evidence": required_evidence,
        "stop_condition": stop_condition,
    }


def _validate_handoff_or_raise(payload: dict[str, Any], *, active_goal_hash: str) -> None:
    projection = project_agent_handoff(payload, active_goal_hash=active_goal_hash)
    if not projection.ok:
        raise RuntimeError(f"handoff validation failed: {list(projection.errors)}")


def _subagent_receipt(
    *,
    goal: dict[str, Any],
    run_id: str,
    subagent: str,
    status: str,
    summary: str,
    artifacts: list[str],
    next_subagent: str,
    next_executor: str,
    next_reason: str,
    stop_condition: str,
) -> dict[str, Any]:
    return {
        "schema": "tau.subagent_receipt.v1",
        "goal": {**goal, "immutable_goal_preserved": True},
        "context": {
            "run_id": run_id,
            "subagent": subagent,
            "actor_type": "tau",
            "artifacts_read": artifacts,
            "assumptions": ["Scillm output is advisory; deterministic checks are proof."],
            "unknowns": [],
        },
        "result": {
            "status": status,
            "summary": summary,
            "mocked": False,
            "live": True,
            "artifacts": artifacts,
        },
        "rationale": "This receipt records one bounded Tau self-fix subagent turn.",
        "evidence": artifacts,
        "next": {
            "subagent": next_subagent,
            "executor": next_executor,
            "reason": next_reason,
        },
        "stop_condition": stop_condition,
    }


def _validate_subagent_receipt_or_raise(payload: dict[str, Any], active_goal_hash: str) -> None:
    validation = validate_subagent_receipt(payload, active_goal_hash=active_goal_hash)
    if not validation.ok:
        raise RuntimeError(f"subagent receipt validation failed: {list(validation.errors)}")


def _artifact_map(root: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            artifacts[str(path.relative_to(root))] = str(path)
    return artifacts


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_text(path: Path, payload: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path
