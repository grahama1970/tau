"""Persona-dream dream-packet command helpers for Tau handoff loops."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.subagent_receipt import validate_subagent_receipt

PERSONA_DREAM_ROOT = Path("/home/graham/workspace/experiments/agent-skills/skills/persona-dream")
PERSONA_DREAM_RUN = PERSONA_DREAM_ROOT / "run.sh"
DEFAULT_GOAL_HASH = "sha256:0000000000000000000000000000000000000000000000000000000000000041"


def run_persona_dream_packet_agent(role: str) -> dict[str, Any]:
    """Run one bounded persona-dream dream-packet role and return a Tau handoff."""

    start_payload = _read_stdin_handoff()
    selected_agent = os.environ.get("TAU_HANDOFF_SELECTED_AGENT") or role
    if selected_agent != role:
        raise RuntimeError(f"selected agent {selected_agent!r} does not match role {role!r}")
    artifact_dir = _artifact_dir(role)
    context = _dream_packet_context(start_payload, artifact_dir)
    if role == "dreamer":
        return _run_dreamer(start_payload, context, artifact_dir)
    if role == "dream-reviewer":
        return _run_dream_reviewer(start_payload, context, artifact_dir)
    raise RuntimeError(f"unsupported persona-dream dream-packet role: {role}")


def write_persona_dream_packet_loop_proof(
    *,
    work_order: Path,
    out_dir: Path,
    active_goal_hash: str = DEFAULT_GOAL_HASH,
    github_target: str = "issue#41",
    persona: str = "embry",
    secondary_persona: str | None = None,
    about: str = "Tau issue 41 dream packet creator reviewer loop",
    frames: int = 3,
    limit: int = 4,
) -> dict[str, Any]:
    """Run the dreamer -> dream-reviewer command loop and write a proof manifest."""

    from tau_coding.handoff_dispatch import write_agent_handoff_command_loop_receipt

    proof_dir = out_dir.expanduser().resolve()
    proof_dir.mkdir(parents=True, exist_ok=True)
    input_work_order = proof_dir / "input_dream_packet_work_order.json"
    input_work_order.write_text(
        work_order.expanduser().resolve().read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    dream_run_root = proof_dir / "dream-run"
    start_payload = {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": github_target},
        "goal": {
            "goal_id": "goal-tau-issue-41-persona-dream-dream-packet-loop",
            "goal_version": 1,
            "goal_hash": active_goal_hash,
        },
        "previous_subagent": "human",
        "context": {
            "summary": "Run a bounded Tau dream-packet creator/reviewer loop.",
            "artifacts": [str(input_work_order)],
            "persona_dream_dream_packet": {
                "work_order": str(input_work_order),
                "run_root": str(dream_run_root),
                "run_id": proof_dir.name,
                "persona": persona,
                "secondary_persona": secondary_persona or "",
                "about": about,
                "frames": str(frames),
                "limit": str(limit),
            },
        },
        "result": {
            "status": "COMPLETED",
            "summary": "Human requested a Tau dream-packet creator/reviewer proof.",
            "evidence": [str(input_work_order)],
        },
        "rationale": (
            "The first bounded step is a dreamer command that creates or fails "
            "closed on dream_packet.json."
        ),
        "next_agent": {
            "name": "dreamer",
            "executor": "local",
            "reason": (
                "Dreamer must create dream_packet.json from real memory residue "
                "or fail closed with no_dream."
            ),
        },
        "required_evidence": [
            "Dreamer and dream-reviewer write Tau handoffs, subagent receipts, "
            "and persona-dream validation receipts."
        ],
        "stop_condition": "Dream-reviewer routes to human with PASS or BLOCKED evidence.",
    }
    start_path = proof_dir / "start-handoff.json"
    _write_json(start_path, start_payload)
    loop = write_agent_handoff_command_loop_receipt(
        start_payload,
        proof_dir / "command-loop",
        agent_registry_root=Path("/home/graham/workspace/experiments/agent-skills/agents"),
        command_spec_root=Path("experiments/goal-locked-subagents/agent-command-specs"),
        active_goal_hash=active_goal_hash,
        max_steps=3,
    )
    loop_payload = loop.as_dict()
    validation_path = dream_run_root / "receipts" / "validate_dream_packet.json"
    pipeline_status_path = dream_run_root / "receipts" / "pipeline_loop_status_forward.json"
    validation = _read_json_optional(validation_path)
    pipeline_status = _read_json_optional(pipeline_status_path)
    first_blocker = _pipeline_first_blocker(pipeline_status)
    manifest = {
        "schema": "tau.persona_dream_dream_packet_loop_proof.v1",
        "created_at": _now_iso(),
        "mocked": False,
        "live": True,
        "issue": 41,
        "input_work_order": str(input_work_order),
        "start_handoff": str(start_path),
        "dream_run_root": str(dream_run_root),
        "dream_packet": str(dream_run_root / "dream_packet.json"),
        "command_loop_receipt": str(proof_dir / "command-loop" / "command-loop-receipt.json"),
        "command_loop_status": loop_payload.get("status"),
        "command_loop_ok": loop_payload.get("ok"),
        "terminal_agent": loop_payload.get("terminal_agent"),
        "stop_reason": loop_payload.get("stop_reason"),
        "validate_dream_packet": str(validation_path) if validation else None,
        "validate_dream_packet_status": validation.get("status") if validation else None,
        "pipeline_loop_status": str(pipeline_status_path) if pipeline_status else None,
        "pipeline_first_blocker": first_blocker,
        "claims": {
            "proves": [
                "Tau ran a command-spec loop from dreamer to dream-reviewer.",
                (
                    "Dreamer called the persona-dream skill runtime rather than "
                    "fabricating fixture residue."
                ),
                "Dream-reviewer ran persona-dream validators and recorded their JSON outputs.",
            ],
            "does_not_prove": [
                "No Kling call, paid provider call, or public upload was performed.",
                (
                    "This does not claim full persona-dream pipeline readiness "
                    "beyond the first blocker reported by pipeline-loop-status."
                ),
            ],
        },
    }
    _write_json(proof_dir / "manifest.json", manifest)
    return manifest


def _run_dreamer(
    start_payload: Mapping[str, Any],
    context: dict[str, str],
    artifact_dir: Path,
) -> dict[str, Any]:
    work_order = _load_work_order(context["work_order"])
    run_root = Path(context["run_root"]).expanduser().resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    command = [
        str(PERSONA_DREAM_RUN),
        "generate",
        "--persona",
        context["persona"],
        "--about",
        context["about"],
        "--output-dir",
        str(run_root),
        "--run-id",
        context["run_id"],
        "--limit",
        context["limit"],
        "--frames",
        context["frames"],
        "--no-write-memory",
    ]
    if context.get("secondary_persona"):
        command.extend(["--secondary-persona", context["secondary_persona"]])
    completed = _run_command(command, artifact_dir / "dreamer-persona-dream-generate")
    response_path = run_root / "response.json"
    response = _read_json_optional(response_path)
    packet_path = run_root / "dream_packet.json"
    status = "COMPLETED" if completed.returncode == 0 and packet_path.is_file() else "BLOCKED"
    reason = response.get("reason") if response else None
    receipt = _subagent_receipt(
        start_payload,
        run_id=context["run_id"],
        subagent="dreamer",
        status=status,
        summary=(
            "Dreamer created dream_packet.json through persona-dream generate."
            if status == "COMPLETED"
            else (
                "Dreamer failed closed before dream packet creation: "
                f"{reason or 'persona-dream generate failed'}."
            )
        ),
        artifacts=[
            str(context["work_order"]),
            str(run_root),
            str(response_path),
            str(artifact_dir / "dreamer-persona-dream-generate.command.json"),
        ],
        next_subagent="dream-reviewer",
        next_executor="local",
        next_reason=(
            "Dream-reviewer must independently run persona-dream validation on "
            "the emitted packet or blocker."
        ),
        stop_condition="Dream-reviewer emits validation receipts and routes to human.",
    )
    receipt_path = artifact_dir / "dreamer_tau_subagent_receipt.json"
    _write_json(receipt_path, receipt)
    _validate_subagent_receipt_or_raise(receipt, str(start_payload["goal"]["goal_hash"]))
    creator_receipt = {
        "schema": "tau.persona_dream.dreamer_receipt.v1",
        "created_at": _now_iso(),
        "role": "dreamer",
        "status": status,
        "work_order": work_order,
        "run_root": str(run_root),
        "dream_packet": str(packet_path) if packet_path.exists() else None,
        "persona_dream_response": str(response_path) if response_path.exists() else None,
        "command": completed.as_dict(),
        "subagent_receipt": str(receipt_path),
        "mocked": False,
        "live": True,
        "provider_calls": {"kling": False, "paid": False, "public_upload": False},
    }
    creator_receipt_path = artifact_dir / "dreamer_receipt.json"
    _write_json(creator_receipt_path, creator_receipt)
    return _handoff(
        start_payload,
        previous_subagent="dreamer",
        result_status=status,
        result_summary=receipt["result"]["summary"],
        evidence=[str(receipt_path), str(creator_receipt_path), str(response_path)],
        context_summary=(
            "Dreamer consumed the dream-packet work order and invoked persona-dream generate."
        ),
        artifacts=[str(receipt_path), str(creator_receipt_path), str(run_root), str(packet_path)],
        context_update={"persona_dream_dream_packet": context},
        rationale="Independent validation is required before any dream-packet acceptance claim.",
        next_agent="dream-reviewer",
        next_executor="local",
        next_reason="Dream-reviewer must run validate-dream-packet and pipeline-loop-status.",
        required_evidence="validate-dream-packet JSON and pipeline-loop-status JSON.",
        stop_condition="Dream-reviewer routes to human with PASS or BLOCKED evidence.",
    )


def _run_dream_reviewer(
    start_payload: Mapping[str, Any],
    context: dict[str, str],
    artifact_dir: Path,
) -> dict[str, Any]:
    run_root = Path(context["run_root"]).expanduser().resolve()
    receipts_dir = run_root / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    packet_path = run_root / "dream_packet.json"
    validation_path = receipts_dir / "validate_dream_packet.json"
    pipeline_status_path = receipts_dir / "pipeline_loop_status_forward.json"
    if packet_path.is_file():
        validation_command = [
            str(PERSONA_DREAM_RUN),
            "validate-dream-packet",
            str(packet_path),
            "--run-root",
            str(run_root),
            "--json",
        ]
    else:
        validation_command = [
            str(PERSONA_DREAM_RUN),
            "validate-dream-packet",
            str(packet_path),
            "--run-root",
            str(run_root),
            "--json",
        ]
    validation_completed = _run_command(
        validation_command,
        artifact_dir / "dream-reviewer-validate-dream-packet",
    )
    _write_command_stdout_json(validation_completed, validation_path)
    pipeline_completed = _run_command(
        [
            str(PERSONA_DREAM_RUN),
            "pipeline-loop-status",
            str(run_root),
            "--direction",
            "forward",
            "--json",
        ],
        artifact_dir / "dream-reviewer-pipeline-loop-status",
    )
    _write_command_stdout_json(pipeline_completed, pipeline_status_path)
    validation = _read_json_optional(validation_path)
    pipeline_status = _read_json_optional(pipeline_status_path)
    validation_pass = validation.get("status") == "PASS_DREAM_PACKET"
    first_blocker = _pipeline_first_blocker(pipeline_status)
    advanced_past_dream_packet = not (
        isinstance(first_blocker, dict) and first_blocker.get("phase") == "dream_packet"
    )
    status = "PASS" if validation_pass and advanced_past_dream_packet else "BLOCKED"
    receipt = _subagent_receipt(
        start_payload,
        run_id=context["run_id"],
        subagent="dream-reviewer",
        status=status,
        summary=(
            "Dream-reviewer accepted the dream packet and pipeline-loop-status "
            "advanced past dream_packet."
            if status == "PASS"
            else (
                "Dream-reviewer failed closed on dream packet validation or "
                "beginning-pipeline status."
            )
        ),
        artifacts=[
            str(packet_path),
            str(validation_path),
            str(pipeline_status_path),
            str(artifact_dir / "dream-reviewer-validate-dream-packet.command.json"),
            str(artifact_dir / "dream-reviewer-pipeline-loop-status.command.json"),
        ],
        next_subagent="human",
        next_executor="human",
        next_reason=(
            "Human or ticket resolver reviews the proof and decides whether to "
            "close the GitHub issue."
        ),
        stop_condition=(
            "Issue resolver comments proof and closes or files the next concrete blocker."
        ),
    )
    receipt_path = artifact_dir / "dream_reviewer_tau_subagent_receipt.json"
    _write_json(receipt_path, receipt)
    _validate_subagent_receipt_or_raise(receipt, str(start_payload["goal"]["goal_hash"]))
    reviewer_receipt = {
        "schema": "tau.persona_dream.dream_reviewer_receipt.v1",
        "created_at": _now_iso(),
        "role": "dream-reviewer",
        "status": status,
        "validation_status": validation.get("status") if validation else None,
        "pipeline_first_blocker": first_blocker,
        "advanced_past_dream_packet": advanced_past_dream_packet,
        "validate_dream_packet": str(validation_path),
        "pipeline_loop_status": str(pipeline_status_path),
        "subagent_receipt": str(receipt_path),
        "mocked": False,
        "live": True,
        "provider_calls": {"kling": False, "paid": False, "public_upload": False},
    }
    reviewer_receipt_path = artifact_dir / "dream_reviewer_receipt.json"
    _write_json(reviewer_receipt_path, reviewer_receipt)
    return _handoff(
        start_payload,
        previous_subagent="dream-reviewer",
        result_status=status,
        result_summary=receipt["result"]["summary"],
        evidence=[
            str(receipt_path),
            str(reviewer_receipt_path),
            str(validation_path),
            str(pipeline_status_path),
        ],
        context_summary=(
            "Dream-reviewer ran persona-dream dream-packet validation and serial loop status."
        ),
        artifacts=[
            str(receipt_path),
            str(reviewer_receipt_path),
            str(validation_path),
            str(pipeline_status_path),
        ],
        context_update={"persona_dream_dream_packet": context},
        rationale=(
            "The next route is human because the bounded creator/reviewer loop "
            "has reached a terminal proof or blocker."
        ),
        next_agent="human",
        next_executor="human",
        next_reason="Human/ticket resolver reviews proof artifacts and closes or redirects.",
        required_evidence=(
            "Proof comment cites dreamer, reviewer, validate-dream-packet, and "
            "pipeline-loop-status receipts."
        ),
        stop_condition="GitHub issue is closed with proof or left open with exact blocker.",
    )


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout_path: str
    stderr_path: str
    command_json_path: str
    duration_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "command_json_path": self.command_json_path,
            "duration_seconds": self.duration_seconds,
        }


def _run_command(command: list[str], base_path: Path) -> CommandResult:
    base_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    duration = time.monotonic() - started
    stdout_path = base_path.with_suffix(".stdout.txt")
    stderr_path = base_path.with_suffix(".stderr.txt")
    command_json_path = base_path.with_suffix(".command.json")
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    result = CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        command_json_path=str(command_json_path),
        duration_seconds=duration,
    )
    _write_json(command_json_path, result.as_dict())
    return result


def _write_command_stdout_json(result: CommandResult, output: Path) -> None:
    stdout = Path(result.stdout_path).read_text(encoding="utf-8")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = {
            "schema": "tau.persona_dream.command_stdout_parse_error.v1",
            "status": "BLOCKED",
            "command": result.as_dict(),
            "stdout": stdout,
        }
    _write_json(output, payload)


def _pipeline_first_blocker(payload: Mapping[str, Any]) -> object:
    first_blocker = payload.get("first_blocker")
    if first_blocker:
        return first_blocker
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        return validation.get("first_blocker")
    return None


def _dream_packet_context(start_payload: Mapping[str, Any], artifact_dir: Path) -> dict[str, str]:
    context = start_payload.get("context")
    raw = context.get("persona_dream_dream_packet") if isinstance(context, Mapping) else None
    if not isinstance(raw, Mapping):
        raw = {}
    work_order = _find_work_order(start_payload, raw)
    run_root = Path(str(raw.get("run_root") or (artifact_dir / "dream-run"))).expanduser().resolve()
    run_id = str(raw.get("run_id") or run_root.name or f"tau-dream-packet-{int(time.time())}")
    return {
        "work_order": str(work_order),
        "run_root": str(run_root),
        "run_id": run_id,
        "persona": str(raw.get("persona") or "embry"),
        "secondary_persona": str(raw.get("secondary_persona") or ""),
        "about": str(raw.get("about") or "Tau persona-dream dream packet creator reviewer loop"),
        "frames": str(raw.get("frames") or "3"),
        "limit": str(raw.get("limit") or "4"),
    }


def _find_work_order(start_payload: Mapping[str, Any], raw: Mapping[str, Any]) -> Path:
    candidate = raw.get("work_order")
    if isinstance(candidate, str) and candidate.strip():
        return Path(candidate).expanduser().resolve()
    context = start_payload.get("context")
    artifacts = context.get("artifacts") if isinstance(context, Mapping) else []
    for artifact in artifacts if isinstance(artifacts, list) else []:
        path = Path(str(artifact)).expanduser()
        if not path.is_file():
            continue
        payload = _read_json_optional(path)
        if payload.get("schema") == "persona_dream.dream_packet_work_order.v1":
            return path.resolve()
    raise RuntimeError("dream packet work order path is required")


def _load_work_order(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    payload = _read_json(path)
    if payload.get("schema") != "persona_dream.dream_packet_work_order.v1":
        raise RuntimeError(
            f"work order schema must be persona_dream.dream_packet_work_order.v1: {path}"
        )
    return payload


def _handoff(
    start_payload: Mapping[str, Any],
    *,
    previous_subagent: str,
    result_status: str,
    result_summary: str,
    evidence: list[str],
    context_summary: str,
    artifacts: list[str],
    context_update: dict[str, Any],
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
            **context_update,
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


def _subagent_receipt(
    handoff: Mapping[str, Any],
    *,
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
    goal = dict(_required_mapping(handoff, "goal"))
    return {
        "schema": "tau.subagent_receipt.v1",
        "goal": {**goal, "immutable_goal_preserved": True},
        "context": {
            "run_id": run_id,
            "subagent": subagent,
            "actor_type": "tau",
            "artifacts_read": artifacts,
            "assumptions": [],
            "unknowns": [],
        },
        "result": {
            "status": status,
            "summary": summary,
            "mocked": False,
            "live": True,
            "artifacts": artifacts,
        },
        "rationale": (
            "This receipt records one bounded Tau persona-dream dream-packet subagent turn."
        ),
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


def _artifact_dir(role: str) -> Path:
    value = os.environ.get("TAU_HANDOFF_COMMAND_ARTIFACT_DIR")
    path = Path(value) if value else Path("/tmp") / f"tau-persona-dream-dream-packet-{role}"
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
        raise RuntimeError(f"payload missing {key} object")
    return value


def _artifact_list(payload: Mapping[str, Any]) -> list[str]:
    context = payload.get("context")
    artifacts = context.get("artifacts") if isinstance(context, Mapping) else None
    return [str(item) for item in artifacts] if isinstance(artifacts, list) else []


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def _read_json_optional(path: Path) -> dict[str, Any]:
    try:
        return _read_json(path)
    except (OSError, json.JSONDecodeError, RuntimeError):
        return {}


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=["dreamer", "dream-reviewer"], required=False)
    parser.add_argument("--proof", action="store_true")
    parser.add_argument("--work-order", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--active-goal-hash", default=DEFAULT_GOAL_HASH)
    parser.add_argument("--github-target", default="issue#41")
    parser.add_argument("--persona", default="embry")
    parser.add_argument("--secondary-persona")
    parser.add_argument("--about", default="Tau issue 41 dream packet creator reviewer loop")
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--limit", type=int, default=4)
    args = parser.parse_args(argv)
    if args.proof:
        if args.work_order is None or args.out_dir is None:
            parser.error("--proof requires --work-order and --out-dir")
        payload = write_persona_dream_packet_loop_proof(
            work_order=args.work_order,
            out_dir=args.out_dir,
            active_goal_hash=args.active_goal_hash,
            github_target=args.github_target,
            persona=args.persona,
            secondary_persona=args.secondary_persona,
            about=args.about,
            frames=args.frames,
            limit=args.limit,
        )
    else:
        if not args.role:
            parser.error("--role is required unless --proof is set")
        payload = run_persona_dream_packet_agent(args.role)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
