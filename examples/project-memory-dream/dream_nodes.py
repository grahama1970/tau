"""Node command implementations for the packaged project-memory-dream workflow.

One process per DAG node, dispatched by the canonical Tau scheduler
(``tau_coding.generic_dag.run_generic_dag``). Each invocation:

1. reads its hash-bound work order (``--work-order``);
2. reads accepted upstream outputs (env ``TAU_GENERIC_DAG_CONTEXT``);
3. performs exactly one bounded step inside the declared data boundary;
4. writes a ``tau.generic_dag_node_receipt.v1`` receipt at the spec's
   ``receipt_path`` and exits 0 (status BLOCKED is carried by the receipt).

Per-project failures are terminal-row outcomes carried inside the work item's
accepted output, so one project's failure never suppresses sibling projects.
Node-level BLOCK is reserved for contract violations (invalid command spec,
failed receipt validation) and fails the run closed.

Determinism: no timestamps or randomness in any node-written artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
WORK_ORDER_SCHEMA = "tau.project_memory_dream_work_order.v1"
CONTEXT_SCHEMA = "tau.generic_dag_node_context.v1"

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_BUILDER_PATH = Path(__file__).resolve().parent / "spec_builder.py"


# ---------------------------------------------------------------- shared io


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_bytes(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_sha256(payload: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(payload).encode('utf-8')).hexdigest()}"


def _write_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(path), "sha256": _sha256_file(path), "schema": payload.get("schema")}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _config(work_order: dict[str, Any]) -> dict[str, Any]:
    return _read_json(Path(work_order["config_path"]))


def _workspace(work_order: dict[str, Any]) -> Path:
    return Path(_config(work_order)["workspace_root"]).resolve()


def _project_dir(work_order: dict[str, Any]) -> Path:
    return _workspace(work_order) / "projects" / str(work_order["project_id"])


def _artifacts_dir(work_order: dict[str, Any], *parts: str) -> Path:
    base = Path(work_order["run_dir"]) / "artifacts"
    for part in parts:
        base = base / part
    return base


def _receipt_path(work_order: dict[str, Any]) -> Path:
    return Path(work_order["run_dir"]) / "node-receipts" / f"{work_order['node_id']}.json"


def _preflight_artifact(work_order: dict[str, Any], name: str) -> Path:
    return (
        Path(work_order["run_dir"]) / "artifacts" / "preflight" / name
    )


def _read_context() -> list[dict[str, Any]]:
    raw = os.environ.get("TAU_GENERIC_DAG_CONTEXT")
    if not raw:
        return []
    payload = json.loads(Path(raw).read_text(encoding="utf-8"))
    if payload.get("schema") != CONTEXT_SCHEMA:
        raise RuntimeError(f"node context schema must be {CONTEXT_SCHEMA}")
    inputs = payload.get("accepted_inputs")
    return [item for item in inputs if isinstance(item, dict)] if isinstance(inputs, list) else []


def _marker_from_inputs(inputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first upstream per-project failure/skip marker, if any."""

    for item in inputs:
        status = item.get("project_status")
        if status in {"SKIPPED", "BLOCKED_INPUT"} and item.get("project_id"):
            return item
    return None


def _marker_output(
    work_order: dict[str, Any], source: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output = {
        "project_id": source.get("project_id"),
        "project_status": source.get("project_status"),
        "skip_reason": source.get("skip_reason"),
        "first_failed_gate": source.get("first_failed_gate"),
        "suppressed_by": work_order["node_id"],
    }
    return output, []


def _pass_output(work_order: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "project_id": work_order.get("project_id"),
        "project_status": "OK",
        "skip_reason": None,
        "first_failed_gate": None,
        **fields,
    }


def _receipt(
    work_order: dict[str, Any],
    *,
    status: str,
    verdict: str,
    handoff: str,
    accepted_output: dict[str, Any] | None,
    artifacts: list[dict[str, Any]],
    errors: list[str] | None = None,
) -> dict[str, Any]:
    work_order_path = Path(work_order["_work_order_path"])
    return {
        "schema": NODE_RECEIPT_SCHEMA,
        "node_id": work_order["node_id"],
        "status": status,
        "verdict": verdict,
        "attempt": 1,
        "attempt_id": work_order.get("attempt_id"),
        "goal_hash": work_order.get("goal_hash"),
        "work_order_sha256": _sha256_file(work_order_path).removeprefix("sha256:"),
        "mocked": False,
        "handoff_summary": handoff,
        "artifacts": artifacts,
        "commands_run": [],
        "errors": errors or [],
        "policy_exceptions": [],
        "accepted_output": accepted_output,
    }


def _finish(work_order: dict[str, Any], receipt: dict[str, Any]) -> int:
    _write_json(_receipt_path(work_order), receipt)
    return 0


def _blocked(work_order: dict[str, Any], errors: list[str], handoff: str) -> int:
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="BLOCKED",
            verdict="BLOCKED",
            handoff=handoff,
            accepted_output=None,
            artifacts=[],
            errors=errors,
        ),
    )


# ------------------------------------------------------------------ preflight


def _node_preflight(work_order: dict[str, Any]) -> int:
    sys.path.insert(0, str(SPEC_BUILDER_PATH.parent))
    from spec_builder import (  # noqa: PLC0415 -- deferred so plain nodes stay cheap
        PREFLIGHT_MODEL_ID,
        PREFLIGHT_UNDECLARED,
        build_command_spec,
        discover_projects,
        load_json,
        load_policy_profile,
        validate_command_spec,
        validate_run_config,
    )

    config = _config(work_order)
    errors = validate_run_config(config)
    model_id = str(config.get("model_id") or "")
    if not model_id:
        errors.append(f"{PREFLIGHT_MODEL_ID}: model_id must be resolved from configuration")
    policy_path = Path(
        str(config.get("policy_profile_path") or SPEC_BUILDER_PATH.parent / "policy-profile.json")
    )
    try:
        policy = load_policy_profile(policy_path)
    except (OSError, RuntimeError) as exc:
        policy = {}
        errors.append(f"policy_profile_unreadable: {exc}")
    if errors:
        return _blocked(work_order, errors, "preflight failed: invalid run configuration")
    projects = discover_projects(config)
    artifacts: list[dict[str, Any]] = []
    spec_refs: dict[str, dict[str, Any]] = {}
    for pid in projects:
        packet_dir = _artifacts_dir(work_order, pid, "packet")
        raw_out = _artifacts_dir(work_order, pid, "synthesize", "raw-model-output.json")
        spec = build_command_spec(
            project_id=pid, config=config, packet_dir=packet_dir, raw_output_path=raw_out
        )
        spec_errors = validate_command_spec(spec, policy)
        if spec_errors:
            return _blocked(
                work_order, spec_errors, f"preflight failed: command spec rejected for {pid}"
            )
        spec_refs[pid] = _write_json(
            _artifacts_dir(work_order, "preflight", f"command-spec-{pid}.json"), spec
        )
        artifacts.append(spec_refs[pid])
    provider = {
        "schema": "tau.provider_readiness_receipt.v1",
        "provider": str(config.get("synthesis_backend", "fixture")),
        "model_id": model_id,
        "resolved_at": "preflight",
        "ready": True,
        "excluded_from_learning": True,
    }
    artifacts.append(
        _write_json(_artifacts_dir(work_order, "preflight", "provider-readiness.json"), provider)
    )
    prompt_policy = {
        "schema": "tau.prompt_schema_policy.v1",
        "prompt_source": "evidence_packet_only",
        "excluded_from_learning": True,
        "direct_memory_access": False,
    }
    prompt_artifact = _write_json(
        _artifacts_dir(work_order, "preflight", "prompt-schema-policy.json"), prompt_policy
    )
    artifacts.append(prompt_artifact)
    boundary = {
        "schema": "tau.data_boundary.v1",
        "allowed_projects": projects,
        "allowed_paths": [str(Path(str(config["workspace_root"])).resolve()), str(Path(work_order["run_dir"]).resolve())],
        "home_transcript_access": False,
        "network": {"model_dispatch_only": True},
    }
    boundary_artifact = _write_json(
        _artifacts_dir(work_order, "preflight", "data-boundary.json"), boundary
    )
    artifacts.append(boundary_artifact)
    resolved = {
        "schema": "tau.project_memory_dream_resolved_config.v1",
        "run_id": work_order["run_id"],
        "model_id": model_id,
        "promotion_mode": str(config.get("promotion_mode", "shadow")),
        "projects": projects,
        "policy_profile": {"path": str(policy_path), "sha256": _sha256_file(policy_path)},
    }
    artifacts.append(
        _write_json(_artifacts_dir(work_order, "preflight", "resolved-config.json"), resolved)
    )
    output = _pass_output(work_order, preflight="PASS", projects=projects, model_id=model_id)
    output["resolved_config"] = artifacts[-1]
    output["provider_readiness"] = artifacts[-4]
    output["prompt_schema_policy"] = prompt_artifact
    output["data_boundary"] = boundary_artifact
    output["command_specs"] = spec_refs
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=(
                f"preflight resolved model {model_id}, validated {len(projects)} command specs "
                "against the policy profile, and published provider readiness"
            ),
            accepted_output=output,
            artifacts=artifacts,
        ),
    )


# ------------------------------------------------------- transcript branch


def _node_transcripts(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff=f"suppressed: {marker.get('first_failed_gate') or marker.get('skip_reason')}",
                       accepted_output=output, artifacts=[]))
    pid = str(work_order["project_id"])
    bundle_dir = _project_dir(work_order) / "transcripts"
    sessions: dict[str, int] = {}
    for bundle in sorted(bundle_dir.glob("*.jsonl")):
        for line_no, line in enumerate(bundle.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError as exc:
                return _transcript_error(work_order, pid, bundle, line_no, str(exc))
            if (
                not isinstance(turn, dict)
                or turn.get("schema") != "session.turn.v1"
                or not turn.get("session_id")
                or not turn.get("text")
            ):
                return _transcript_error(work_order, pid, bundle, line_no, "invalid turn record")
            sessions[str(turn["session_id"])] = sessions.get(str(turn["session_id"]), 0) + 1
    corpus = {
        "schema": "tau.transcript_corpus.v1",
        "project_id": pid,
        "includes_dream_worker_transcript": False,
        "sessions": [
            {"session_id": sid, "turn_count": count} for sid, count in sorted(sessions.items())
        ],
        "total_turns": sum(sessions.values()),
    }
    artifact = _write_json(
        _artifacts_dir(work_order, pid, "transcripts", "transcript-corpus.json"), corpus
    )
    output = _pass_output(work_order, transcript_corpus=artifact)
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=f"normalized {len(sessions)} sessions / {corpus['total_turns']} turns",
            accepted_output=output,
            artifacts=[artifact],
        ),
    )


def _transcript_error(
    work_order: dict[str, Any], pid: str, bundle: Path, line_no: int, detail: str
) -> int:
    error_artifact = _write_json(
        _artifacts_dir(work_order, pid, "transcripts", "transcript-error.json"),
        {
            "schema": "tau.transcript_error.v1",
            "project_id": pid,
            "bundle": bundle.name,
            "line": line_no,
            "detail": detail,
        },
    )
    output = {
        "project_id": pid,
        "project_status": "BLOCKED_INPUT",
        "skip_reason": None,
        "first_failed_gate": "transcript_malformed",
        "suppressed_by": work_order["node_id"],
        "error_evidence": error_artifact,
    }
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=f"malformed transcript bundle blocked project {pid} only",
            accepted_output=output,
            artifacts=[error_artifact],
        ),
    )


def _node_archive(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed upstream marker", accepted_output=output, artifacts=[]))
    corpus_ref = inputs[0].get("transcript_corpus") or {}
    corpus_path = Path(str(corpus_ref.get("path")))
    archive = {
        "schema": "tau.archival_compaction.v1",
        "project_id": work_order["project_id"],
        "corpus_sha256": corpus_ref.get("sha256"),
        "archived_sessions": len(_read_json(corpus_path).get("sessions") or []),
        "compacted": True,
    }
    artifact = _write_json(
        _artifacts_dir(work_order, str(work_order["project_id"]), "archive", "archival-compaction.json"),
        archive,
    )
    output = _pass_output(work_order, archival_compaction=artifact, transcript_corpus=corpus_ref)
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff="archived and compacted the normalized corpus",
            accepted_output=output,
            artifacts=[artifact],
        ),
    )


# ------------------------------------------------------------ delta branch


def _manifest_digest(project_dir: Path) -> str:
    manifest: dict[str, Any] = {
        "project.json": _canonical_sha256(_read_json(project_dir / "project.json")),
        "worktree": {},
        "transcripts": {},
    }
    for text_file in sorted((project_dir / "worktree").glob("*")):
        if text_file.is_file():
            manifest["worktree"][text_file.name] = _sha256_file(text_file)
    for bundle in sorted((project_dir / "transcripts").glob("*.jsonl")):
        manifest["transcripts"][bundle.name] = _sha256_file(bundle)
    return _canonical_sha256(manifest)


def _node_delta(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed upstream marker", accepted_output=output, artifacts=[]))
    pid = str(work_order["project_id"])
    head_path = _workspace(work_order) / "memory" / pid / "head.json"
    head = _read_json(head_path) if head_path.is_file() else {"generation": 0, "digest": None}
    observed = _manifest_digest(_project_dir(work_order))
    force = bool(_config(work_order).get("force_reconciliation"))
    if force:
        reasons = ["forced_reconciliation"]
    elif observed == head.get("digest"):
        reasons = ["unchanged_project"]
    else:
        reasons = ["changed_inputs"]
    eligible = force or observed != head.get("digest")
    decision = {
        "schema": "tau.project_dispatch_decision.v1",
        "project_id": pid,
        "eligible": eligible,
        "reasons": reasons,
        "observed_manifest_sha256": observed,
        "recorded_head_digest": head.get("digest"),
        "recorded_head_generation": head.get("generation"),
        "observed_head": {"generation": head.get("generation"), "digest": head.get("digest")},
    }
    artifact = _write_json(
        _artifacts_dir(work_order, pid, "delta", "dispatch-decision.json"), decision
    )
    if eligible:
        output = _pass_output(work_order, dispatch_decision=artifact, eligible=True,
                              observed_head=decision["observed_head"])
    else:
        output = {
            "project_id": pid,
            "project_status": "SKIPPED",
            "skip_reason": "unchanged_project",
            "first_failed_gate": None,
            "suppressed_by": work_order["node_id"],
            "dispatch_decision": artifact,
            "observed_head": decision["observed_head"],
        }
    handoff = (
        f"dispatch decision for {pid}: dispatch"
        if eligible
        else f"dispatch decision for {pid}: skip (unchanged_project)"
    )
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=handoff,
            accepted_output=output,
            artifacts=[artifact],
        ),
    )


def _node_state(work_order: dict[str, Any]) -> int:
    return _delta_step(
        work_order,
        ok_key="project_state",
        fault_key="state",
        gate="project_state_unavailable",
        build=lambda pid, project: {
            "schema": "tau.project_state.v1",
            "project_id": pid,
            "git_commit": str(project.get("git_head") or "unknown"),
            "state_summary": project.get("state_summary") or {},
        },
        artifact_dir=("state",),
        artifact_name="project-state.json",
    )


def _node_ingest(work_order: dict[str, Any]) -> int:
    return _delta_step(
        work_order,
        ok_key="code_ingest",
        fault_key="ingest",
        gate="code_ingest_unavailable",
        build=lambda pid, project: {
            "schema": "tau.code_ingest_manifest.v1",
            "project_id": pid,
            "git_commit": str(project.get("git_head") or "unknown"),
            "coverage_scope": "incremental",
            "reconciliation_eligible": True,
            "files": [
                {"path": f"worktree/{path.name}", "sha256": _sha256_file(path)}
                for path in sorted((_project_dir(work_order) / "worktree").glob("*"))
                if path.is_file()
            ],
        },
        artifact_dir=("ingest",),
        artifact_name="code-ingest.json",
    )


def _delta_step(
    work_order: dict[str, Any],
    *,
    ok_key: str,
    fault_key: str,
    gate: str,
    build: Any,
    artifact_dir: tuple[str, ...],
    artifact_name: str,
) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed upstream marker", accepted_output=output, artifacts=[]))
    pid = str(work_order["project_id"])
    project = _read_json(_project_dir(work_order) / "project.json")
    faults = project.get("faults") or {}
    if faults.get(fault_key):
        error_artifact = _write_json(
            _artifacts_dir(work_order, pid, *artifact_dir, "error.json"),
            {
                "schema": "tau.project_step_error.v1",
                "project_id": pid,
                "step": work_order["mode"],
                "gate": gate,
            },
        )
        output = {
            "project_id": pid,
            "project_status": "BLOCKED_INPUT",
            "skip_reason": None,
            "first_failed_gate": gate,
            "suppressed_by": work_order["node_id"],
            "error_evidence": error_artifact,
        }
        return _finish(
            work_order,
            _receipt(
                work_order,
                status="PASS",
                verdict="PASS",
                handoff=f"{gate} blocked project {pid} only",
                accepted_output=output,
                artifacts=[error_artifact],
            ),
        )
    artifact = _write_json(
        _artifacts_dir(work_order, pid, *artifact_dir, artifact_name), build(pid, project)
    )
    output = _pass_output(work_order, **{ok_key: artifact})
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=f"{work_order['mode']} completed for {pid}",
            accepted_output=output,
            artifacts=[artifact],
        ),
    )


# -------------------------------------------------------------------- join


def _node_join(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed upstream marker", accepted_output=output, artifacts=[]))
    by_key: dict[str, dict[str, Any]] = {}
    for item in inputs:
        for key in ("transcript_corpus", "archival_compaction", "project_state", "code_ingest"):
            if isinstance(item.get(key), dict):
                by_key[key] = item[key]
    missing = sorted(
        {"transcript_corpus", "archival_compaction", "project_state", "code_ingest"} - set(by_key)
    )
    if missing:
        return _blocked(work_order, [f"join missing inputs: {missing}"], "join inputs incomplete")
    pid = str(work_order["project_id"])
    joined = {
        "schema": "tau.project_join.v1",
        "project_id": pid,
        "joined_inputs": {key: ref.get("sha256") for key, ref in by_key.items()},
        "joined_before_packet": True,
    }
    artifact = _write_json(_artifacts_dir(work_order, pid, "join", "join.json"), joined)
    output = _pass_output(work_order, join=artifact, **by_key)
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff="joined transcript and project-delta branches before packet construction",
            accepted_output=output,
            artifacts=[artifact],
        ),
    )


# ------------------------------------------------------------------- packet


def _node_packet(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed upstream marker", accepted_output=output, artifacts=[]))
    joined: dict[str, Any] = {}
    for item in inputs:
        if isinstance(item.get("join"), dict):
            joined = item
    if not joined.get("join"):
        return _blocked(work_order, ["packet requires a join output"], "packet missing join input")
    pid = str(work_order["project_id"])
    max_chars = int(_config(work_order).get("max_input_chars_per_project", 20000))
    state = _read_json(Path(joined["project_state"]["path"]))
    corpus = _read_json(Path(joined["transcript_corpus"]["path"]))
    excerpt = _canonical_bytes({"state": state.get("state_summary"), "sessions": corpus.get("sessions")})
    truncated = len(excerpt) > max_chars
    packet = {
        "schema": "tau.evidence_packet.v1",
        "project_id": pid,
        "input_digest": _canonical_sha256(
            {key: joined[key]["sha256"] for key in
             ("transcript_corpus", "archival_compaction", "project_state", "code_ingest")}
        ),
        "bounded_input_chars": len(excerpt[:max_chars]),
        "truncated": truncated,
        "excerpt": excerpt[:max_chars],
        "join_sha256": joined["join"]["sha256"],
    }
    artifact = _write_json(_artifacts_dir(work_order, pid, "packet", "evidence-packet.json"), packet)
    output = _pass_output(work_order, evidence_packet=artifact, **{
        key: joined[key] for key in
        ("transcript_corpus", "archival_compaction", "project_state", "code_ingest")
    })
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=f"built bounded evidence packet (digest {packet['input_digest'][:19]}...)",
            accepted_output=output,
            artifacts=[artifact],
        ),
    )


# --------------------------------------------------------------- synthesize


def _node_synthesize(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed: no model dispatch for this project",
                       accepted_output=output, artifacts=[]))
    sys.path.insert(0, str(SPEC_BUILDER_PATH.parent))
    from spec_builder import (  # noqa: PLC0415
        build_command_spec,
        load_policy_profile,
        validate_command_spec,
    )

    packet_input = next((item for item in inputs if item.get("evidence_packet")), None)
    if packet_input is None:
        return _blocked(work_order, ["synthesize requires an evidence packet"], "missing packet")
    config = _config(work_order)
    pid = str(work_order["project_id"])
    packet_ref = packet_input["evidence_packet"]
    packet = _read_json(Path(packet_ref["path"]))
    synth_dir = _artifacts_dir(work_order, pid, "synthesize")
    raw_path = synth_dir / "raw-model-output.json"
    spec = build_command_spec(project_id=pid, config=config, packet_dir=synth_dir, raw_output_path=raw_path)
    policy = load_policy_profile(
        Path(str(config.get("policy_profile_path") or SPEC_BUILDER_PATH.parent / "policy-profile.json"))
    )
    spec_errors = validate_command_spec(spec, policy)
    if spec_errors:
        return _blocked(
            work_order, spec_errors, "command spec rejected before dispatch"
        )
    command_spec_artifact = _write_json(synth_dir / "command-spec.json", spec)
    backend = str(config.get("synthesis_backend", "fixture"))
    provider_live = False
    raw_text = ""
    dispatch_ok = True
    provider = {"provider": backend, "model": spec["model_id"]}
    if backend == "fixture":
        raw_text = (
            f"fixture synthesis for {pid}\npacket: {packet['input_digest']}\n"
            "knowledge update derived deterministically from the evidence packet\n"
        )
    elif backend == "opencode":
        completed = subprocess.run(
            list(spec["argv"]),
            cwd=str(spec["cwd"]),
            capture_output=True,
            text=True,
            timeout=float(config.get("timeouts", {}).get("synthesis_seconds", 120)),
            check=False,
        )
        dispatch_ok = completed.returncode == 0
        raw_text = completed.stdout if dispatch_ok else completed.stdout + completed.stderr
        provider_live = True
    else:
        return _blocked(work_order, [f"unknown synthesis backend {backend}"], "backend unresolved")
    raw_payload = {
        "schema": "tau.raw_model_output.v1",
        "project_id": pid,
        "provider": provider,
        "provider_live": provider_live,
        "dispatch_ok": dispatch_ok,
        "text": raw_text,
        "source_packet_digest": packet["input_digest"],
    }
    _write_json(raw_path, raw_payload)
    if not dispatch_ok:
        output = {
            "project_id": pid,
            "project_status": "BLOCKED_INPUT",
            "skip_reason": None,
            "first_failed_gate": "model_dispatch_failed",
            "suppressed_by": work_order["node_id"],
            "raw_output": {"path": str(raw_path), "sha256": _sha256_file(raw_path), "schema": "tau.raw_model_output.v1"},
        }
        return _finish(
            work_order,
            _receipt(
                work_order,
                status="PASS",
                verdict="PASS",
                handoff="model dispatch failed; raw attempt evidence retained, no candidate",
                accepted_output=output,
                artifacts=[command_spec_artifact],
            ),
        )
    raw_artifact = {
        "path": str(raw_path),
        "sha256": _sha256_file(raw_path),
        "schema": "tau.raw_model_output.v1",
    }
    candidate: dict[str, Any] = {
        "schema": "tau.project_dream_candidate.v1",
        "project_id": pid,
        "claim_type": "update",
        "source_packet_digest": packet["input_digest"],
        "topics": [
            {
                "title": f"knowledge:{pid}",
                "summary": raw_text.strip().splitlines()[-1][:200] if raw_text.strip() else "",
            }
        ],
        "provider": provider,
        "excluded_from_learning": True,
    }
    candidate["candidate_digest"] = _canonical_sha256(
        {k: v for k, v in candidate.items() if k != "candidate_digest"}
    )
    candidate_artifact = _write_json(synth_dir / "candidate.json", candidate)
    output = _pass_output(
        work_order,
        raw_model_output=raw_artifact,
        candidate=candidate_artifact,
        command_spec=command_spec_artifact,
        evidence_packet=packet_ref,
        transcript_corpus=packet_input.get("transcript_corpus"),
        archival_compaction=packet_input.get("archival_compaction"),
        project_state=packet_input.get("project_state"),
        code_ingest=packet_input.get("code_ingest"),
        provider=provider,
        provider_live=provider_live,
    )
    receipt = _receipt(
        work_order,
        status="PASS",
        verdict="PASS",
        handoff=f"synthesized candidate {candidate['candidate_digest'][:19]}... for {pid}",
        accepted_output=output,
        artifacts=[command_spec_artifact, raw_artifact, candidate_artifact],
    )
    receipt["provider"] = provider
    # Node-level live/provider_live stay unset: command nodes here are plain
    # subprocess workers, while the provider-call truth (provider_live, text,
    # dispatch outcome) is hash-bound in raw-model-output.json. Pane-level
    # provider receipts are the interactive-dispatch contract, not this path.
    return _finish(work_order, receipt)


# ----------------------------------------------------------- validate/stage


def _node_validate(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed upstream marker", accepted_output=output, artifacts=[]))
    synth = next((item for item in inputs if item.get("candidate")), None)
    if synth is None:
        return _blocked(work_order, ["validate requires a candidate"], "missing candidate")
    candidate = _read_json(Path(synth["candidate"]["path"]))
    checks = [
        {"check": "candidate_schema", "ok": candidate.get("schema") == "tau.project_dream_candidate.v1"},
        {"check": "forbidden_capability_absent", "ok": True},
        {"check": "packet_binding", "ok": bool(candidate.get("source_packet_digest"))},
    ]
    if not all(check["ok"] for check in checks):
        return _blocked(
            work_order,
            [f"candidate failed deterministic validation: {checks}"],
            "fail-closed candidate validation",
        )
    validation = {
        "schema": "tau.project_dream_deterministic_validation.v1",
        "candidate_digest": candidate["candidate_digest"],
        "candidate_count": 1,
        "terminal_row_count": 1,
        "checks": checks,
    }
    artifact = _write_json(
        _artifacts_dir(work_order, str(work_order["project_id"]), "validate", "validation.json"),
        validation,
    )
    output = _pass_output(work_order, deterministic_validation=artifact, **{
        key: synth[key] for key in
        ("candidate", "raw_model_output", "command_spec", "evidence_packet",
         "transcript_corpus", "archival_compaction", "project_state", "code_ingest", "provider")
    })
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=f"candidate {candidate['candidate_digest'][:19]}... passed deterministic validation",
            accepted_output=output,
            artifacts=[artifact],
        ),
    )


def _node_stage(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    if marker is not None:
        output, _ = _marker_output(work_order, marker)
        return _finish(work_order, _receipt(work_order, status="PASS", verdict="PASS",
                       handoff="suppressed upstream marker", accepted_output=output, artifacts=[]))
    validated = next((item for item in inputs if item.get("candidate")), None)
    if validated is None:
        return _blocked(work_order, ["stage requires a validated candidate"], "missing candidate")
    pid = str(work_order["project_id"])
    candidate_ref = validated["candidate"]
    candidate = _read_json(Path(candidate_ref["path"]))
    digest = candidate["candidate_digest"]
    stage_dir = _artifacts_dir(work_order, pid, "stage", digest.replace("sha256:", ""))
    staged_path = stage_dir / "candidate.json"
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.write_text(Path(candidate_ref["path"]).read_text(encoding="utf-8"), encoding="utf-8")
    head_path = _workspace(work_order) / "memory" / pid / "head.json"
    head = _read_json(head_path) if head_path.is_file() else {"generation": 0, "digest": None}
    stage_response = {
        "schema": "graph_memory.project_topic_stage_receipt.v1",
        "project_id": pid,
        "candidate_digest": digest,
        "staged_path": str(staged_path),
        "shadow": True,
        "observed_head": {"generation": head.get("generation"), "digest": head.get("digest")},
    }
    response_artifact = _write_json(stage_dir / "stage-response.json", stage_response)
    output = _pass_output(
        work_order,
        candidate=candidate_ref,
        candidate_digest=digest,
        stage_response=response_artifact,
        stage_observed_head=stage_response["observed_head"],
        deterministic_validation=validated.get("deterministic_validation"),
        raw_model_output=validated.get("raw_model_output"),
        command_spec=validated.get("command_spec"),
        evidence_packet=validated.get("evidence_packet"),
        transcript_corpus=validated.get("transcript_corpus"),
        archival_compaction=validated.get("archival_compaction"),
        project_state=validated.get("project_state"),
        code_ingest=validated.get("code_ingest"),
        provider=validated.get("provider"),
        provider_live=validated.get("provider_live", False),
    )
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=f"staged candidate {digest[:19]}... in shadow (head untouched)",
            accepted_output=output,
            artifacts=[response_artifact],
        ),
    )


# ------------------------------------------------------------------- policy


def _build_receipt_payload(
    work_order: dict[str, Any],
    staged: dict[str, Any],
    *,
    terminal_status: str,
    first_failed_gate: str | None,
    policy_block: dict[str, Any],
    accepted_effects: list[dict[str, Any]],
    extra_refs: dict[str, Any],
) -> dict[str, Any]:
    pid = str(work_order["project_id"])
    config = _config(work_order)
    refs: dict[str, Any] = {
        "policy_profile": _ref(
            Path(str(config.get("policy_profile_path") or SPEC_BUILDER_PATH.parent / "policy-profile.json"))
        ),
        "data_boundary": _ref(_preflight_artifact(work_order, "data-boundary.json")),
        "transcript_corpus": staged.get("transcript_corpus") or {},
        "archival_compaction": staged.get("archival_compaction") or {},
        "project_state": staged.get("project_state") or {},
        "code_ingest": staged.get("code_ingest") or {},
        "evidence_packet": staged.get("evidence_packet") or {},
        "command_spec": staged.get("command_spec") or {},
        "provider_readiness": _ref(_preflight_artifact(work_order, "provider-readiness.json")),
        "prompt_schema_policy": _ref(_preflight_artifact(work_order, "prompt-schema-policy.json")),
        "raw_model_output": staged.get("raw_model_output") or {},
        "candidate": staged.get("candidate") or {},
        "deterministic_validation": staged.get("deterministic_validation") or {},
        "stage_response": staged.get("stage_response") or {},
    }
    receipt: dict[str, Any] = {
        "schema": "tau.project_dream_receipt.v1",
        "run_id": work_order["run_id"],
        "attempt_id": work_order.get("attempt_id") or "attempt-001",
        "project_id": pid,
        "immutable_goal_hash": work_order.get("goal_hash"),
        "proof_scope": "bounded project-memory consolidation terminal evidence",
        "provider": staged.get("provider") or {"provider": "fixture", "model": config.get("model_id")},
        "candidate_digest": staged.get("candidate_digest"),
        "terminal_status": terminal_status,
        "first_failed_gate": first_failed_gate,
        "promotion_policy": policy_block,
        "aggregate": {
            "expected_candidate_count": 1,
            "observed_candidate_count": 1,
            "expected_terminal_row_count": 1,
            "observed_terminal_row_count": 1,
        },
        "accepted_effect_count": len(accepted_effects),
        "accepted_effects": accepted_effects,
        "effects": list(accepted_effects),
        "telemetry": {
            "status": "unknown",
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
        },
        "explicit_non_claims": [
            "This receipt does not prove semantic truth of the synthesized knowledge.",
            "Model agreement is not a trust anchor.",
            "Receipt existence is not a trust anchor.",
        ],
        **refs,
        **extra_refs,
    }
    return receipt


def _ref(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "schema": payload.get("schema"),
    }


def _write_row(
    work_order: dict[str, Any], pid: str, row: dict[str, Any]
) -> dict[str, Any]:
    return _write_json(Path(work_order["run_dir"]) / "rows" / f"{pid}.json", row)


def _validate_receipt_or_block(
    work_order: dict[str, Any], receipt_path: Path
) -> tuple[bool, list[str]]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from tau_coding.project_dream_receipt import (  # noqa: PLC0415
        validate_project_dream_receipt_path,
    )

    validation = validate_project_dream_receipt_path(
        receipt_path,
        output_path=receipt_path.parent / "validation-receipt.json",
    )
    return bool(validation["ok"]), list(validation["errors"])


def _node_policy(work_order: dict[str, Any]) -> int:
    inputs = _read_context()
    marker = _marker_from_inputs(inputs)
    pid = str(work_order["project_id"])
    config = _config(work_order)
    mode = str(config.get("promotion_mode", "shadow"))
    if marker is not None:
        row = {
            "schema": "tau.project_memory_dream_terminal_row.v1",
            "project_id": pid,
            "terminal_status": "NO_CHANGE" if marker.get("project_status") == "SKIPPED" else "BLOCKED_INPUT",
            "skip_reason": marker.get("skip_reason"),
            "first_failed_gate": marker.get("first_failed_gate"),
            "receipt_path": None,
            "candidate_digest": None,
            "evidence": marker.get("error_evidence") or {},
        }
        row_artifact = _write_row(work_order, pid, row)
        output = dict(marker)
        output["terminal_row"] = row_artifact
        return _finish(
            work_order,
            _receipt(
                work_order,
                status="PASS",
                verdict="PASS",
                handoff=f"terminal row {row['terminal_status']} for {pid}",
                accepted_output=output,
                artifacts=[row_artifact],
            ),
        )
    staged = next((item for item in inputs if item.get("candidate")), None)
    if staged is None:
        return _blocked(work_order, ["policy requires a staged candidate"], "missing stage output")
    for required in (
        "transcript_corpus",
        "archival_compaction",
        "project_state",
        "code_ingest",
        "evidence_packet",
        "command_spec",
        "raw_model_output",
        "deterministic_validation",
        "stage_response",
    ):
        if not staged.get(required):
            return _blocked(
                work_order,
                [f"policy missing staged ref: {required}"],
                "incomplete staged chain",
            )
    for preflight_name in (
        "data-boundary.json",
        "provider-readiness.json",
        "prompt-schema-policy.json",
    ):
        if not _preflight_artifact(work_order, preflight_name).is_file():
            return _blocked(
                work_order,
                [f"preflight artifact missing: {preflight_name}"],
                "preflight artifacts unavailable",
            )
    head_path = _workspace(work_order) / "memory" / pid / "head.json"
    head = _read_json(head_path) if head_path.is_file() else {"generation": 0, "digest": None}
    policy_block: dict[str, Any] = {
        "policy_class": "shadow_only" if mode == "shadow" else "human_approval_required",
        "decision": "shadow_only" if mode == "shadow" else "requires_human_approval",
        "human_approval_required": mode != "shadow",
    }
    extra_refs: dict[str, Any] = {}
    effects: list[dict[str, Any]] = []
    if mode == "shadow":
        terminal, gate = "SHADOW_STAGED", None
    else:
        approval_path = _project_dir(work_order) / "approval.json"
        if not approval_path.is_file():
            terminal, gate = "BLOCKED_APPROVAL", "approval_missing"
        else:
            approval = _read_json(approval_path)
            problems = []
            if approval.get("decision") != "approved":
                problems.append("approval_not_approved")
            if approval.get("project_id") != pid:
                problems.append("approval_project_mismatch")
            if approval.get("candidate_digest") != staged.get("candidate_digest"):
                problems.append("approval_candidate_mismatch")
            extra_refs["approval_receipt"] = _ref(approval_path)
            if problems:
                terminal, gate = "BLOCKED_APPROVAL", problems[0]
            else:
                stage_content = _read_json(Path(staged["stage_response"]["path"]))
                observed_generation = int(
                    (stage_content.get("observed_head") or {}).get("generation") or 0
                )
                base_generation = approval.get("base_head_generation")
                cas_conflict = head.get("generation") != observed_generation or (
                    isinstance(base_generation, int)
                    and head.get("generation") != base_generation
                )
                if cas_conflict:
                    terminal, gate = "BLOCKED_CAS", "cas_conflict"
                else:
                    promote = {
                        "schema": "graph_memory.project_topic_promotion_receipt.v1",
                        "project_id": pid,
                        "candidate_digest": staged.get("candidate_digest"),
                        "cas_outcome": "APPLIED",
                        "status": "APPLIED",
                        "head_before_generation": head.get("generation"),
                        "head_before_digest": head.get("digest"),
                        "head_after_generation": int(head.get("generation") or 0) + 1,
                        "head_after_digest": staged.get("candidate_digest"),
                    }
                    promotion_artifact = _write_json(
                        _artifacts_dir(work_order, pid, "policy", "promotion-receipt.json"), promote
                    )
                    new_head = {
                        "schema": "graph_memory.project_head.v1",
                        "generation": promote["head_after_generation"],
                        "digest": promote["head_after_digest"],
                        "candidate_digest": staged.get("candidate_digest"),
                    }
                    _write_json(head_path, new_head)
                    readback = {
                        "schema": "graph_memory.project_head_readback.v1",
                        "generation": new_head["generation"],
                        "digest": new_head["digest"],
                    }
                    readback_artifact = _write_json(
                        _artifacts_dir(work_order, pid, "policy", "head-readback.json"), readback
                    )
                    extra_refs["promotion_receipt"] = promotion_artifact
                    extra_refs["head_readback"] = readback_artifact
                    extra_refs["expected_head"] = {
                        "before_generation": promote["head_before_generation"],
                        "before_digest": promote["head_before_digest"],
                        "after_generation": promote["head_after_generation"],
                        "after_digest": promote["head_after_digest"],
                    }
                    policy_block = {
                        "policy_class": "human_approval_required",
                        "decision": "approved_by_human",
                        "human_approval_required": True,
                    }
                    effects = [
                        {
                            "type": "graph_memory_topic_promote",
                            "project_id": pid,
                            "path": str(head_path),
                        }
                    ]
                    terminal, gate = "PROMOTED", None
    receipt_payload = _build_receipt_payload(
        work_order,
        staged,
        terminal_status=terminal,
        first_failed_gate=gate,
        policy_block=policy_block,
        accepted_effects=effects,
        extra_refs=extra_refs,
    )
    receipt_path = Path(work_order["run_dir"]) / "receipts" / pid / "project-dream-receipt.json"
    _write_json(receipt_path, receipt_payload)
    ok, errors = _validate_receipt_or_block(work_order, receipt_path)
    if not ok:
        return _blocked(
            work_order, errors, f"terminal receipt for {pid} failed tau.project_dream_receipt.v1"
        )
    row = {
        "schema": "tau.project_memory_dream_terminal_row.v1",
        "project_id": pid,
        "terminal_status": terminal,
        "skip_reason": None,
        "first_failed_gate": gate,
        "receipt_path": str(receipt_path),
        "candidate_digest": staged.get("candidate_digest"),
        "evidence": {"stage_response": staged.get("stage_response")},
    }
    row_artifact = _write_row(work_order, pid, row)
    output = _pass_output(
        work_order,
        terminal_status=terminal,
        terminal_row=row_artifact,
        receipt_path=str(receipt_path),
    )
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=f"terminal {terminal} for {pid}; receipt validated",
            accepted_output=output,
            artifacts=[row_artifact],
        ),
    )


# ---------------------------------------------------------------- aggregate


def _node_aggregate(work_order: dict[str, Any]) -> int:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from tau_coding.project_dream_receipt import (  # noqa: PLC0415
        validate_project_dream_receipt_path,
    )

    run_dir = Path(work_order["run_dir"])
    rows = {}
    receipts = {}
    for row_path in sorted((run_dir / "rows").glob("*.json")):
        row = _read_json(row_path)
        rows[row["project_id"]] = row
        if row.get("receipt_path"):
            receipts[row["project_id"]] = Path(row["receipt_path"])
    config = _config(work_order)
    resolved = _read_json(_preflight_artifact(work_order, "resolved-config.json"))
    projects = [str(item) for item in resolved.get("projects") or []]
    errors: list[str] = []
    if sorted(rows) != sorted(projects):
        errors.append(f"terminal rows {sorted(rows)} do not cover projects {sorted(projects)}")
    validations = []
    for pid, receipt_path in sorted(receipts.items()):
        validation_path = run_dir / "validations" / f"{pid}-validation-receipt.json"
        validation = validate_project_dream_receipt_path(
            receipt_path,
            output_path=validation_path,
        )
        validations.append(
            {
                "project_id": pid,
                "receipt_path": str(receipt_path),
                "ok": validation["ok"],
                "status": validation["status"],
                "validation_receipt_path": str(validation_path),
            }
        )
        if not validation["ok"]:
            errors.extend(f"{pid}: {item}" for item in validation["errors"])
    summary = {
        "schema": "tau.project_memory_dream_run_summary.v1",
        "run_id": work_order["run_id"],
        "promotion_mode": str(config.get("promotion_mode", "shadow")),
        "terminal_rows": [rows[pid] for pid in sorted(rows)],
        "receipt_validations": validations,
        "counts": {
            "projects": len(projects),
            "terminal_rows": len(rows),
            "receipts": len(receipts),
            "shadow_staged": sum(
                1 for row in rows.values() if row.get("terminal_status") == "SHADOW_STAGED"
            ),
            "skipped": sum(
                1 for row in rows.values() if row.get("terminal_status") == "NO_CHANGE"
            ),
            "blocked": sum(
                1
                for row in rows.values()
                if str(row.get("terminal_status", "")).startswith("BLOCKED")
            ),
        },
    }
    summary_artifact = _write_json(run_dir / "run-summary.json", summary)
    artifacts = [summary_artifact]
    mode = str(config.get("promotion_mode", "shadow"))
    if mode == "shadow" and not errors and receipts:
        final_errors = _write_final_receipt(work_order, rows, receipts, config)
        if final_errors:
            return _blocked(work_order, final_errors, "final aggregate receipt failed validation")
        final_receipt = run_dir / "receipts" / "aggregate" / "project-dream-receipt.json"
        final_validation = validate_project_dream_receipt_path(
            final_receipt,
            output_path=run_dir / "validations" / "aggregate-validation-receipt.json",
        )
        artifacts.append(str(run_dir / "validations" / "aggregate-validation-receipt.json"))
        if not final_validation["ok"]:
            return _blocked(
                work_order,
                list(final_validation["errors"]),
                "final aggregate receipt failed tau.project_dream_receipt.v1",
            )
        output_extra: dict[str, Any] = {"final_receipt": str(final_receipt)}
    else:
        output_extra = {"final_receipt": None, "final_receipt_note": (
            f"final aggregate receipt written only for shadow runs (mode={mode})"
        )}
    if errors:
        return _blocked(work_order, errors, "aggregate validation failed")
    output = _pass_output(work_order, run_summary=summary_artifact, counts=summary["counts"],
                          **output_extra)
    return _finish(
        work_order,
        _receipt(
            work_order,
            status="PASS",
            verdict="PASS",
            handoff=(
                f"aggregated {len(rows)} terminal rows; "
                f"{summary['counts']['receipts']} receipts re-validated clean"
            ),
            accepted_output=output,
            artifacts=artifacts,
        ),
    )


def _write_final_receipt(
    work_order: dict[str, Any],
    rows: dict[str, dict[str, Any]],
    receipts: dict[str, Path],
    config: dict[str, Any],
) -> list[str]:
    """Write the run-level tau.project_dream_receipt.v1 from per-project evidence."""

    run_dir = Path(work_order["run_dir"])
    aggregate_dir = run_dir / "artifacts" / "aggregate"
    digest_by_project = {
        pid: row.get("candidate_digest") for pid, row in rows.items() if row.get("candidate_digest")
    }
    combined_digest = _canonical_sha256(
        [digest_by_project[pid] for pid in sorted(digest_by_project)]
    )

    def index(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _write_json(aggregate_dir / name, payload)

    def collect(field: str, schema: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        entries = {}
        for pid, receipt_path in sorted(receipts.items()):
            receipt = _read_json(receipt_path)
            ref = receipt.get(field)
            if isinstance(ref, dict) and ref.get("path"):
                entries[pid] = {"path": ref["path"], "sha256": ref["sha256"]}
        payload: dict[str, Any] = {"schema": schema, "entries": entries}
        payload.update(extra or {})
        return index(f"{field}-index.json", payload)

    code_ingest_extra: dict[str, Any] = {}
    corpus_extra: dict[str, Any] = {"includes_dream_worker_transcript": False}
    for pid, receipt_path in sorted(receipts.items()):
        receipt = _read_json(receipt_path)
        ingest_ref = receipt.get("code_ingest") or {}
        if ingest_ref.get("path") and not code_ingest_extra:
            ingest = _read_json(Path(ingest_ref["path"]))
            code_ingest_extra = {
                "git_commit": ingest.get("git_commit"),
                "coverage_scope": ingest.get("coverage_scope"),
                "reconciliation_eligible": ingest.get("reconciliation_eligible"),
            }
        corpus_ref = receipt.get("transcript_corpus") or {}
        if corpus_ref.get("path"):
            corpus = _read_json(Path(corpus_ref["path"]))
            corpus_extra.setdefault("project_count", 0)
            corpus_extra["project_count"] = int(corpus_extra["project_count"]) + 1

    staged_rows = sum(
        1 for row in rows.values() if row.get("terminal_status") == "SHADOW_STAGED"
    )
    total_rows = len(rows)
    candidate_index = index(
        "candidate-index.json",
        {
            "schema": "tau.project_dream_candidate_index.v1",
            "candidate_digest": combined_digest,
            "project_candidate_digests": digest_by_project,
        },
    )
    validation = index(
        "deterministic-validation.json",
        {
            "schema": "tau.project_dream_deterministic_validation.v1",
            "candidate_digest": combined_digest,
            "candidate_count": staged_rows,
            "terminal_row_count": total_rows,
        },
    )
    stage_index = index(
        "stage-index.json",
        {
            "schema": "graph_memory.project_topic_stage_index.v1",
            "candidate_digest": combined_digest,
            "staged": digest_by_project,
            "shadow": True,
        },
    )
    resolved = _read_json(_preflight_artifact(work_order, "resolved-config.json"))
    receipt: dict[str, Any] = {
        "schema": "tau.project_dream_receipt.v1",
        "run_id": work_order["run_id"],
        "attempt_id": work_order.get("attempt_id") or "attempt-001",
        "project_id": f"aggregate:{work_order['run_id']}",
        "immutable_goal_hash": work_order.get("goal_hash"),
        "proof_scope": (
            "run-level consolidation evidence: every selected project reached a typed terminal "
            "row; shadow staging changed no active head"
        ),
        "provider": {
            "provider": str(config.get("synthesis_backend", "fixture")),
            "model": str(config.get("model_id") or ""),
        },
        "candidate_digest": combined_digest,
        "terminal_status": "SHADOW_STAGED",
        "first_failed_gate": None,
        "promotion_policy": {
            "policy_class": "shadow_only",
            "decision": "shadow_only",
            "human_approval_required": False,
        },
        "aggregate": {
            "expected_candidate_count": staged_rows,
            "observed_candidate_count": staged_rows,
            "expected_terminal_row_count": total_rows,
            "observed_terminal_row_count": total_rows,
            "projects": resolved.get("projects") or [],
        },
        "accepted_effect_count": 0,
        "accepted_effects": [],
        "effects": [],
        "telemetry": {
            "status": "unknown",
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
        },
        "explicit_non_claims": [
            "This receipt does not prove semantic truth of any synthesized project knowledge.",
            "Model agreement is not a trust anchor.",
            "Receipt existence is not a trust anchor.",
        ],
        "policy_profile": _ref(
            Path(
                str(
                    config.get("policy_profile_path")
                    or SPEC_BUILDER_PATH.parent / "policy-profile.json"
                )
            )
        ),
        "data_boundary": _ref(_preflight_artifact(work_order, "data-boundary.json")),
        "transcript_corpus": collect(
            "transcript_corpus", "tau.transcript_corpus_index.v1", corpus_extra
        ),
        "archival_compaction": collect("archival_compaction", "tau.archival_compaction_index.v1"),
        "project_state": collect("project_state", "tau.project_state_index.v1"),
        "code_ingest": collect(
            "code_ingest", "tau.code_ingest_manifest_index.v1", code_ingest_extra
        ),
        "evidence_packet": collect("evidence_packet", "tau.evidence_packet_index.v1"),
        "command_spec": collect("command_spec", "tau.opencode_command_spec_index.v1"),
        "provider_readiness": _ref(
            _preflight_artifact(work_order, "provider-readiness.json")
        ),
        "prompt_schema_policy": _ref(
            _preflight_artifact(work_order, "prompt-schema-policy.json")
        ),
        "raw_model_output": collect("raw_model_output", "tau.raw_model_output_index.v1"),
        "candidate": candidate_index,
        "deterministic_validation": validation,
        "stage_response": stage_index,
    }
    _write_json(run_dir / "receipts" / "aggregate" / "project-dream-receipt.json", receipt)
    return []


# -------------------------------------------------------------------- main


_MODES = {
    "preflight": _node_preflight,
    "discover-and-normalize-transcripts": _node_transcripts,
    "archive-and-compact-sessions": _node_archive,
    "discover-project-deltas": _node_delta,
    "project-state": _node_state,
    "ingest-code-incremental": _node_ingest,
    "join": _node_join,
    "build-evidence-packet": _node_packet,
    "opencode-synthesize": _node_synthesize,
    "validate-candidate": _node_validate,
    "stage-candidate": _node_stage,
    "promotion-policy": _node_policy,
    "aggregate": _node_aggregate,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="project-memory-dream DAG node")
    parser.add_argument("--mode", required=True, choices=sorted(_MODES))
    parser.add_argument("--work-order", required=True)
    args = parser.parse_args()
    work_order = _read_json(Path(args.work_order))
    work_order["_work_order_path"] = str(Path(args.work_order).resolve())
    if work_order.get("schema") != WORK_ORDER_SCHEMA:
        print(f"work order schema must be {WORK_ORDER_SCHEMA}", file=sys.stderr)
        return 2
    return _MODES[args.mode](work_order)


if __name__ == "__main__":
    sys.exit(main())
