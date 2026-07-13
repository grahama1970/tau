"""Native skill-provider nodes for the generic Tau DAG runner."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from tau_coding.dag_runtime.subprocess_control import run_cancellable_subprocess

SKILL_DAG_NODE_SCHEMA = "tau.skill_dag_node.v1"
SKILL_ROUND_POLICY_SCHEMA = "tau.bounded_skill_round_policy.v1"
SKILL_ROUND_RECEIPT_SCHEMA = "tau.skill_round_receipt.v1"

SURF_RUN = Path("/home/graham/workspace/experiments/agent-skills/skills/surf/run.sh")
CREATE_ARCHITECTURE_RUN = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/create-architecture/run.sh"
)


@dataclass(frozen=True)
class SkillDagSpec:
    capability: str
    provider: str
    input_path: Path | None
    output_dir: Path
    configuration: dict[str, Any]
    max_rounds: int
    clarification_allowed: bool
    clarification_answer_path: Path | None


def parse_skill_dag_spec(raw: Any, *, base_dir: Path, node_id: str) -> SkillDagSpec:
    if not isinstance(raw, dict):
        raise RuntimeError(f"node {node_id} skill must be an object")
    if raw.get("schema") != SKILL_DAG_NODE_SCHEMA:
        raise RuntimeError(f"node {node_id} skill schema must be {SKILL_DAG_NODE_SCHEMA}")
    capability = _required_string(raw, "capability", node_id=node_id)
    provider = _required_string(raw, "provider", node_id=node_id)
    if (capability, provider) not in {
        ("architecture_review", "webgpt"),
        ("architecture_render", "create-architecture"),
    }:
        raise RuntimeError(
            f"node {node_id} unsupported skill capability/provider: {capability}/{provider}"
        )
    input_raw = raw.get("input_path")
    input_path = _resolve_path(input_raw, base_dir=base_dir) if _non_empty(input_raw) else None
    output_dir = _resolve_path(
        _required_string(raw, "output_dir", node_id=node_id), base_dir=base_dir
    )
    configuration = raw.get("configuration", {})
    if not isinstance(configuration, dict):
        raise RuntimeError(f"node {node_id} skill configuration must be an object")
    round_policy = raw.get("round_policy", {})
    if not isinstance(round_policy, dict):
        raise RuntimeError(f"node {node_id} skill round_policy must be an object")
    if round_policy and round_policy.get("schema") != SKILL_ROUND_POLICY_SCHEMA:
        raise RuntimeError(
            f"node {node_id} skill round_policy schema must be {SKILL_ROUND_POLICY_SCHEMA}"
        )
    max_rounds = int(round_policy.get("max_rounds", 1))
    if max_rounds < 1:
        raise RuntimeError(f"node {node_id} skill max_rounds must be at least 1")
    answer_raw = round_policy.get("clarification_answer_path")
    answer_path = (
        _resolve_path(answer_raw, base_dir=base_dir) if _non_empty(answer_raw) else None
    )
    return SkillDagSpec(
        capability=capability,
        provider=provider,
        input_path=input_path,
        output_dir=output_dir,
        configuration=configuration,
        max_rounds=max_rounds,
        clarification_allowed=round_policy.get("clarification_allowed") is True,
        clarification_answer_path=answer_path,
    )


def execute_skill_dag_node(
    *,
    spec: SkillDagSpec,
    run_id: str,
    node_id: str,
    goal_hash: str | None,
    work_order_sha256: str | None,
    accepted_inputs: list[dict[str, Any]],
    cancel_event: Event | None = None,
) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    if spec.provider == "webgpt":
        return _execute_webgpt(
            spec=spec,
            run_id=run_id,
            node_id=node_id,
            goal_hash=goal_hash,
            work_order_sha256=work_order_sha256,
            cancel_event=cancel_event,
        )
    return _execute_create_architecture(
        spec=spec,
        run_id=run_id,
        node_id=node_id,
        goal_hash=goal_hash,
        work_order_sha256=work_order_sha256,
        accepted_inputs=accepted_inputs,
        cancel_event=cancel_event,
    )


def _execute_webgpt(
    *,
    spec: SkillDagSpec,
    run_id: str,
    node_id: str,
    goal_hash: str | None,
    work_order_sha256: str | None,
    cancel_event: Event | None,
) -> dict[str, Any]:
    errors = _webgpt_configuration_errors(spec)
    state_path = spec.output_dir / "round-state.json"
    state = _read_optional_json(state_path)
    rounds = state.get("rounds", []) if isinstance(state.get("rounds"), list) else []
    round_number = len(rounds) + 1
    prior = rounds[-1] if rounds else None
    if prior and prior.get("action") == "CLARIFY":
        answer_path = spec.clarification_answer_path
        if answer_path is None or not answer_path.is_file():
            errors.append("clarification_answer_required")
    if round_number > spec.max_rounds:
        errors.append("skill_round_budget_exhausted")
    if spec.input_path is None or not spec.input_path.is_file():
        errors.append("skill_input_missing")
    if errors:
        return _node_receipt(
            node_id=node_id,
            capability=spec.capability,
            provider=spec.provider,
            status="BLOCKED",
            verdict="BLOCKED",
            errors=errors,
            round_number=round_number,
            max_rounds=spec.max_rounds,
        )
    assert spec.input_path is not None

    request_path = spec.output_dir / f"round-{round_number:03d}-request.md"
    response_path = spec.output_dir / f"round-{round_number:03d}-response.md"
    raw_path = spec.output_dir / f"round-{round_number:03d}-response.raw.md"
    meta_path = spec.output_dir / f"round-{round_number:03d}-response.meta.json"
    transport_receipt_path = spec.output_dir / f"round-{round_number:03d}-transport.json"
    request_path.write_text(
        _round_request(
            source=spec.input_path,
            round_number=round_number,
            max_rounds=spec.max_rounds,
            prior=prior,
            answer_path=spec.clarification_answer_path,
        ),
        encoding="utf-8",
    )
    recovery_sentinel = spec.configuration.get("recovery_sentinel")
    if _non_empty(recovery_sentinel):
        command = [
            str(SURF_RUN),
            "webgpt.extract",
            "--tab-id",
            str(spec.configuration["tab_id"]),
            "--sentinel",
            str(recovery_sentinel),
            "--output",
            str(response_path),
            "--raw-output",
            str(raw_path),
            "--meta-output",
            str(meta_path),
            "--timeout",
            str(int(spec.configuration.get("timeout_seconds", 300))),
            "--wait",
            "--stable-polls",
            "3",
        ]
    else:
        command = [
            str(SURF_RUN),
            "webgpt.submit",
            "--input",
            str(request_path),
            "--output",
            str(response_path),
            "--raw-output",
            str(raw_path),
            "--meta-output",
            str(meta_path),
            "--receipt-output",
            str(transport_receipt_path),
            "--tab-id",
            str(spec.configuration["tab_id"]),
            "--expect-url",
            str(spec.configuration["expected_url"]),
            "--no-activate",
            "--no-remember",
            "--timeout",
            str(int(spec.configuration.get("timeout_seconds", 900))),
        ]
    commands_run = [command]
    if _non_empty(recovery_sentinel):
        preflight_command = [
            str(SURF_RUN),
            "webgpt.preflight",
            "--tab-id",
            str(spec.configuration["tab_id"]),
            "--expect-url",
            str(spec.configuration["expected_url"]),
            "--no-activate",
            "--json",
        ]
        preflight = run_cancellable_subprocess(
            preflight_command,
            timeout_seconds=30,
            cancel_event=cancel_event,
        )
        commands_run.insert(0, preflight_command)
        if preflight.returncode != 0:
            return _node_receipt(
                node_id=node_id,
                capability=spec.capability,
                provider=spec.provider,
                status="BLOCKED",
                verdict="BLOCKED",
                errors=[f"webgpt_recovery_preflight_failed:{preflight.returncode}"],
                round_number=round_number,
                max_rounds=spec.max_rounds,
                commands_run=commands_run,
            )
    completed = run_cancellable_subprocess(
        command,
        timeout_seconds=float(spec.configuration.get("timeout_seconds", 900)) + 30,
        cancel_event=cancel_event,
    )
    if completed.returncode != 0:
        diagnostic_artifacts = _write_surf_doctor_request(
            spec=spec,
            run_id=run_id,
            node_id=node_id,
            goal_hash=goal_hash,
            work_order_sha256=work_order_sha256,
            round_number=round_number,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            request_path=request_path,
            response_path=response_path,
            raw_path=raw_path,
            meta_path=meta_path,
            transport_receipt_path=transport_receipt_path,
        )
        return _node_receipt(
            node_id=node_id,
            capability=spec.capability,
            provider=spec.provider,
            status="BLOCKED",
            verdict="BLOCKED",
            errors=[f"webgpt_transport_failed:{completed.returncode}"],
            round_number=round_number,
            max_rounds=spec.max_rounds,
            commands_run=commands_run,
            artifacts=diagnostic_artifacts,
            handoff_summary="WebGPT transport blocked; Surf Doctor incident request emitted",
        )
    meta = _read_optional_json(meta_path)
    contract = _extract_round_contract(response_path.read_text(encoding="utf-8"))
    validation_errors = _webgpt_proof_errors(
        meta=meta,
        contract=contract,
        expected_tab_id=str(spec.configuration["tab_id"]),
        allow_degraded_focus=spec.configuration.get("allow_degraded_focus") is True,
        recovery=bool(_non_empty(recovery_sentinel)),
    )
    action = str(contract.get("action") or "BLOCKED").upper()
    questions = contract.get("clarifying_questions", [])
    question_hash = _json_sha256(questions) if questions else None
    if action == "CLARIFY":
        if not spec.clarification_allowed:
            validation_errors.append("clarification_not_allowed")
        if not isinstance(questions, list) or not questions:
            validation_errors.append("clarification_questions_required")
        prior_hashes = {
            item.get("clarification_question_sha256")
            for item in rounds
            if isinstance(item, dict)
        }
        if question_hash in prior_hashes:
            validation_errors.append("duplicate_clarification_question")
    if validation_errors:
        action = "BLOCKED"
    round_record = {
        "schema": SKILL_ROUND_RECEIPT_SCHEMA,
        "run_id": run_id,
        "node_id": node_id,
        "round_number": round_number,
        "max_rounds": spec.max_rounds,
        "goal_hash": goal_hash,
        "work_order_sha256": work_order_sha256,
        "prior_response_sha256": prior.get("response_sha256") if prior else None,
        "request_path": str(request_path),
        "request_sha256": _file_sha256(request_path),
        "response_path": str(response_path),
        "response_sha256": _file_sha256(response_path),
        "meta_path": str(meta_path),
        "meta_sha256": _file_sha256(meta_path),
        "transport_degraded": meta.get("transport_degraded") is True,
        "focus_invariant_ok": meta.get("focus_invariant_ok"),
        "action": action,
        "clarifying_questions": questions if isinstance(questions, list) else [],
        "clarification_question_sha256": question_hash,
        "clarification_answer_sha256": (
            _file_sha256(spec.clarification_answer_path)
            if spec.clarification_answer_path and spec.clarification_answer_path.is_file()
            else None
        ),
        "errors": validation_errors,
    }
    rounds.append(round_record)
    _write_json(state_path, {"schema": "tau.skill_round_state.v1", "rounds": rounds})
    _write_json(spec.output_dir / f"round-{round_number:03d}-receipt.json", round_record)
    if action == "CLARIFY":
        _write_json(
            spec.output_dir / "clarification-request.json",
            {
                "schema": "tau.skill_clarification_request.v1",
                "status": "BLOCKED",
                "round_number": round_number,
                "questions": questions,
                "questions_sha256": question_hash,
                "answer_path": str(spec.clarification_answer_path)
                if spec.clarification_answer_path
                else None,
            },
        )
    status = "PASS" if action == "PASS" and not validation_errors else "BLOCKED"
    return _node_receipt(
        node_id=node_id,
        capability=spec.capability,
        provider=spec.provider,
        status=status,
        verdict="PASS" if status == "PASS" else "BLOCKED",
        errors=validation_errors or ([] if status == "PASS" else [f"skill_action:{action}"]),
        round_number=round_number,
        max_rounds=spec.max_rounds,
        commands_run=commands_run,
        artifacts=[
            _artifact(response_path, "webgpt.response.md"),
            _artifact(raw_path, "webgpt.response.raw.md"),
            _artifact(meta_path, "surf.webgpt.meta.v1"),
            _artifact(
                spec.output_dir / f"round-{round_number:03d}-receipt.json",
                SKILL_ROUND_RECEIPT_SCHEMA,
            ),
        ],
        provider_live=False,
        handoff_summary=f"WebGPT round {round_number}/{spec.max_rounds}: {action}",
    )


def _execute_create_architecture(
    *,
    spec: SkillDagSpec,
    run_id: str,
    node_id: str,
    goal_hash: str | None,
    work_order_sha256: str | None,
    accepted_inputs: list[dict[str, Any]],
    cancel_event: Event | None,
) -> dict[str, Any]:
    input_path = spec.input_path or _first_accepted_artifact(accepted_inputs)
    if input_path is None or not input_path.is_file():
        return _node_receipt(
            node_id=node_id,
            capability=spec.capability,
            provider=spec.provider,
            status="BLOCKED",
            verdict="BLOCKED",
            errors=["architecture_input_missing"],
        )
    command = [str(CREATE_ARCHITECTURE_RUN), "create", "--input", str(input_path)]
    completed = run_cancellable_subprocess(
        command,
        timeout_seconds=float(spec.configuration.get("timeout_seconds", 900)),
        cancel_event=cancel_event,
    )
    match = re.search(r"select\s+([A-Za-z0-9_-]+)", completed.stdout, flags=re.DOTALL)
    if completed.returncode != 0 or match is None:
        return _node_receipt(
            node_id=node_id,
            capability=spec.capability,
            provider=spec.provider,
            status="BLOCKED",
            verdict="BLOCKED",
            errors=[f"create_architecture_failed:{completed.returncode}"],
            commands_run=[command],
        )
    architecture_id = match.group(1)
    artifact_receipt = spec.output_dir / "architecture-artifact.json"
    _write_json(
        artifact_receipt,
        {
            "schema": "tau.architecture_artifact_receipt.v1",
            "status": "PASS",
            "run_id": run_id,
            "node_id": node_id,
            "goal_hash": goal_hash,
            "work_order_sha256": work_order_sha256,
            "input_path": str(input_path),
            "input_sha256": _file_sha256(input_path),
            "architecture_id": architecture_id,
            "view_url": "http://localhost:3002/#architecture",
            "does_not_prove": ["The rendered architecture is implemented or correct."],
        },
    )
    return _node_receipt(
        node_id=node_id,
        capability=spec.capability,
        provider=spec.provider,
        status="PASS",
        verdict="PASS",
        errors=[],
        commands_run=[command],
        artifacts=[_artifact(artifact_receipt, "tau.architecture_artifact_receipt.v1")],
        handoff_summary=f"Created architecture {architecture_id}",
    )


def _webgpt_configuration_errors(spec: SkillDagSpec) -> list[str]:
    errors = []
    if not _non_empty(spec.configuration.get("tab_id")):
        errors.append("webgpt_tab_id_required")
    if not _non_empty(spec.configuration.get("expected_url")):
        errors.append("webgpt_expected_url_required")
    return errors


def _write_surf_doctor_request(
    *,
    spec: SkillDagSpec,
    run_id: str,
    node_id: str,
    goal_hash: str | None,
    work_order_sha256: str | None,
    round_number: int,
    command: list[str],
    returncode: int,
    stdout: str,
    stderr: str,
    request_path: Path,
    response_path: Path,
    raw_path: Path,
    meta_path: Path,
    transport_receipt_path: Path,
) -> list[dict[str, Any]]:
    stdout_path = spec.output_dir / f"round-{round_number:03d}-transport.stdout.txt"
    stderr_path = spec.output_dir / f"round-{round_number:03d}-transport.stderr.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    meta = _read_optional_json(meta_path)
    transport = _read_optional_json(transport_receipt_path)
    source_paths = [
        request_path,
        response_path,
        raw_path,
        meta_path,
        transport_receipt_path,
        stdout_path,
        stderr_path,
    ]
    existing_sources = [path for path in source_paths if path.is_file()]
    incident_path = spec.output_dir / "surf-doctor-request.json"
    _write_json(
        incident_path,
        {
            "schema": "surf_doctor.incident_request.v1",
            "status": "PENDING_DIAGNOSIS",
            "agent_id": "surf-doctor",
            "run_id": run_id,
            "node_id": node_id,
            "round_number": round_number,
            "goal_hash": goal_hash,
            "work_order_sha256": work_order_sha256,
            "failure_class": meta.get("failure") or f"webgpt_transport_exit_{returncode}",
            "proof_status": meta.get("proof_status"),
            "returncode": returncode,
            "requested_tab_id": meta.get("requested_tab_id")
            or str(spec.configuration.get("tab_id")),
            "controlled_tab_id": meta.get("controlled_tab_id"),
            "expected_url": str(spec.configuration.get("expected_url")),
            "sentinel": meta.get("sentinel") or transport.get("sentinel"),
            "submitted_to_chatgpt": meta.get("submitted_to_chatgpt")
            if "submitted_to_chatgpt" in meta
            else transport.get("submitted_to_chatgpt"),
            "browser_mutation_authorized": False,
            "allowed_actions": [
                "preserve_incident",
                "classify_failure",
                "read_only_reproduction",
                "prepare_repair_packet",
            ],
            "forbidden_actions": [
                "webgpt_resubmit",
                "tab_activate",
                "tab_close",
                "tab_navigate",
                "page_refresh",
                "extension_reload",
            ],
            "command": command,
            "source_artifacts": [
                _artifact(path, "surf.incident.source.v1") for path in existing_sources
            ],
            "dag_spec": {
                "schema": "subagent_dag.v1",
                "mode": "bounded_dag",
                "nodes": [
                    "preserve_incident",
                    "classify_failure",
                    "reproduce_or_bound",
                    "prepare_repair",
                ],
                "max_attempts": 3,
                "stop_conditions": [
                    "diagnosis_and_repair_packet_written",
                    "same_failure_repeated_twice",
                    "architectural_change_required",
                    "browser_mutation_required",
                ],
            },
        },
    )
    return [
        *[_artifact(path, "surf.incident.source.v1") for path in existing_sources],
        _artifact(incident_path, "surf_doctor.incident_request.v1"),
    ]


def _webgpt_proof_errors(
    *,
    meta: dict[str, Any],
    contract: dict[str, Any],
    expected_tab_id: str,
    allow_degraded_focus: bool,
    recovery: bool,
) -> list[str]:
    errors = []
    if recovery:
        if meta.get("status") != "completed" or meta.get("raw_contains_sentinel") is not True:
            errors.append("webgpt_recovery_not_proven")
    else:
        allowed_proof_statuses = {"response_proven"}
        if allow_degraded_focus:
            allowed_proof_statuses.add("degraded_focus")
        if meta.get("proof_status") not in allowed_proof_statuses:
            errors.append("webgpt_response_not_proven")
    if str(meta.get("requested_tab_id")) != expected_tab_id:
        errors.append("webgpt_requested_tab_mismatch")
    if str(meta.get("controlled_tab_id")) != expected_tab_id:
        errors.append("webgpt_controlled_tab_mismatch")
    if not recovery and meta.get("controlled_tab_id_mismatch") is not False:
        errors.append("webgpt_controlled_tab_mismatch_flag")
    if not recovery and meta.get("tab_was_created") is not False:
        errors.append("webgpt_unapproved_tab_created")
    if contract.get("schema") != "tau.skill_round_response.v1":
        errors.append("skill_round_response_contract_missing")
    if str(contract.get("action") or "").upper() not in {
        "PASS",
        "CLARIFY",
        "REVISE",
        "BLOCKED",
    }:
        errors.append("skill_round_action_invalid")
    return errors


def _extract_round_contract(text: str) -> dict[str, Any]:
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schema") == "tau.skill_round_response.v1":
            return payload
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schema") == "tau.skill_round_response.v1":
            return payload
    return {}


def _round_request(
    *,
    source: Path,
    round_number: int,
    max_rounds: int,
    prior: dict[str, Any] | None,
    answer_path: Path | None,
) -> str:
    text = source.read_text(encoding="utf-8")
    answer = (
        answer_path.read_text(encoding="utf-8")
        if answer_path and answer_path.is_file()
        else ""
    )
    return (
        f"{text}\n\nTau bounded skill round {round_number} of {max_rounds}.\n"
        f"Prior response sha256: {prior.get('response_sha256') if prior else 'none'}.\n"
        f"Human clarification answer:\n{answer or 'none'}\n\n"
        "End with exactly one fenced JSON object using this contract:\n"
        "```json\n"
        '{"schema":"tau.skill_round_response.v1","action":"PASS|CLARIFY|REVISE|BLOCKED",'
        '"clarifying_questions":[],"accepted_artifact_path":null,"summary":"..."}\n'
        "```\n"
        "Use CLARIFY only for questions that require human intent or authority. "
        "You cannot increase max_rounds."
    )


def _node_receipt(
    *,
    node_id: str,
    capability: str,
    provider: str,
    status: str,
    verdict: str,
    errors: list[str],
    round_number: int | None = None,
    max_rounds: int | None = None,
    commands_run: list[list[str]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    provider_live: bool = False,
    handoff_summary: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "tau.generic_dag_node_receipt.v1",
        "node_id": node_id,
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": bool(commands_run),
        "provider_live": provider_live,
        "skill_provider": provider,
        "capability": capability,
        "round_number": round_number,
        "max_rounds": max_rounds,
        "artifacts": artifacts or [],
        "commands_run": commands_run or [],
        "errors": errors,
        "policy_exceptions": [],
        "handoff_summary": handoff_summary or f"{provider} skill node {status.lower()}",
    }


def _artifact(path: Path, schema: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "schema": schema,
        "sha256": _file_sha256(path),
        "bytes": path.stat().st_size,
    }


def _first_accepted_artifact(accepted_inputs: list[dict[str, Any]]) -> Path | None:
    for projection in accepted_inputs:
        artifacts = projection.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, dict) and _non_empty(artifact.get("path")):
                    return Path(str(artifact["path"])).expanduser().resolve()
    return None


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _required_string(payload: dict[str, Any], key: str, *, node_id: str) -> str:
    value = payload.get(key)
    if not _non_empty(value):
        raise RuntimeError(f"node {node_id} skill {key} must be a non-empty string")
    return str(value)


def _resolve_path(value: Any, *, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    return (path if path.is_absolute() else base_dir / path).resolve()


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
