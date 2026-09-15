"""Config validation, command-spec gate, and tau.generic_dag_spec.v1 generation.

Pure module: no side effects. The packaged workflow entrypoint
(``workflow.py``) and the deterministic tests both use these functions.

The generated spec compiles through ``tau_coding.dag_runtime.compiler`` and is
executed by the canonical scheduler in ``tau_coding.generic_dag``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

RUN_CONFIG_SCHEMA = "tau.project_memory_dream_run_config.v1"
POLICY_PROFILE_SCHEMA = "tau.policy_profile.v1"
DATA_BOUNDARY_SCHEMA = "tau.data_boundary.v1"
COMMAND_SPEC_SCHEMA = "tau.opencode_command_spec.v1"
NODE_RUNNER = Path(__file__).resolve().parent / "dream_nodes.py"

PREFLIGHT_UNDECLARED = "undeclared_capabilities"
PREFLIGHT_FORBIDDEN = "forbidden_model_capabilities"
PREFLIGHT_MODEL_ID = "model_id_unresolved"

_FORBIDDEN_MODEL_CAPABILITIES = (
    "memory.direct",
    "memory.write_direct",
    "arangodb.write",
    "qdrant.write",
    "database.write",
    "generic_upsert",
)
_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")

_CONFIG_KEYS = {
    "schema",
    "run_id",
    "workspace_root",
    "promotion_mode",
    "model_id",
    "synthesis_backend",
    "max_projects_per_run",
    "max_parallel_projects",
    "max_input_chars_per_project",
    "max_topic_mutations",
    "max_project_mutation_ratio",
    "timeouts",
    "retries",
    "cost_budget_usd",
    "projects",
    "force_reconciliation",
    "policy_profile_path",
    "synthesis_uses_capabilities_extra",
}
_PROMOTION_MODES = {"shadow", "human_approval", "policy_limited_auto"}


def canonical_sha256(payload: object) -> str:
    """Canonical JSON digest matching tau_coding.dag_runtime.model.canonical_json."""

    text = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def load_policy_profile(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("schema") != POLICY_PROFILE_SCHEMA:
        raise RuntimeError(f"policy profile schema must be {POLICY_PROFILE_SCHEMA}: {path}")
    return payload


def validate_run_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if config.get("schema") != RUN_CONFIG_SCHEMA:
        errors.append(f"schema must be {RUN_CONFIG_SCHEMA}")
    unknown = sorted(set(config) - _CONFIG_KEYS)
    if unknown:
        errors.append(f"unknown config keys: {unknown}")
    root = config.get("workspace_root")
    if not isinstance(root, str) or not root or not Path(root).is_dir():
        errors.append(f"workspace_root must be an existing directory: {root!r}")
    mode = config.get("promotion_mode", "shadow")
    if mode not in _PROMOTION_MODES:
        errors.append(f"promotion_mode must be one of {sorted(_PROMOTION_MODES)}")
    for key in (
        "max_projects_per_run",
        "max_parallel_projects",
        "max_input_chars_per_project",
        "max_topic_mutations",
    ):
        value = config.get(key)
        if type(value) is not int or value < 1:
            errors.append(f"{key} must be a positive integer")
    ratio = config.get("max_project_mutation_ratio")
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 < ratio <= 1:
        errors.append("max_project_mutation_ratio must be within (0, 1]")
    timeouts = config.get("timeouts")
    if not isinstance(timeouts, dict) or not isinstance(
        timeouts.get("node_seconds"), (int, float)
    ):
        errors.append("timeouts.node_seconds must be a number")
    retries = config.get("retries")
    if not isinstance(retries, dict) or type(retries.get("read_only_max_attempts")) is not int:
        errors.append("retries.read_only_max_attempts must be an integer")
    if not isinstance(retries, dict) or type(retries.get("synthesis_max_attempts")) is not int:
        errors.append("retries.synthesis_max_attempts must be an integer")
    return errors


def discover_projects(config: dict[str, Any]) -> list[str]:
    """Project discovery: explicit config list, else workspace subdirectories."""

    explicit = config.get("projects")
    if isinstance(explicit, list) and explicit:
        projects = [str(item) for item in explicit]
    else:
        root = Path(str(config["workspace_root"]))
        projects = sorted(
            item.name for item in (root / "projects").iterdir() if (item / "project.json").is_file()
        )
    limit = int(config.get("max_projects_per_run", 8))
    if len(projects) > limit:
        raise RuntimeError(f"project count {len(projects)} exceeds max_projects_per_run {limit}")
    return projects


def build_command_spec(
    *,
    project_id: str,
    config: dict[str, Any],
    packet_dir: Path,
    raw_output_path: Path,
) -> dict[str, Any]:
    """One bounded OpenCode/fixture command spec: packet dir in, one file out."""

    backend = str(config.get("synthesis_backend", "fixture"))
    model_id = str(config.get("model_id", ""))
    uses = [
        f"filesystem.read:{packet_dir}",
        f"filesystem.write:{raw_output_path}",
    ]
    declared = list(uses)
    if backend == "opencode":
        uses.append("process.opencode.run")
        declared.append("process.opencode.run")
    uses.extend(str(item) for item in config.get("synthesis_uses_capabilities_extra") or [])
    return {
        "schema": COMMAND_SPEC_SCHEMA,
        "command_spec_id": f"synthesize-{project_id}",
        "provider": backend,
        "model_id": model_id,
        "declared_capabilities": declared,
        "uses_capabilities": uses,
        "argv": [
            "opencode",
            "run",
            "--model",
            model_id,
            "--dir",
            str(packet_dir),
            "Read evidence-packet.json in this directory. Write a JSON candidate file to "
            f"{raw_output_path} with keys schema (tau.project_dream_candidate.v1), project_id, "
            "claim_type, topics. Change nothing else.",
        ]
        if backend == "opencode"
        else [],
        "cwd": str(packet_dir),
        "excluded_from_learning": True,
    }


def validate_command_spec(spec: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Fail-closed gate: declared covers used; forbidden capabilities never pass."""

    errors: list[str] = []
    if spec.get("schema") != COMMAND_SPEC_SCHEMA:
        errors.append(f"command spec schema must be {COMMAND_SPEC_SCHEMA}")
    declared = {item for item in spec.get("declared_capabilities") or [] if isinstance(item, str)}
    used = {item for item in spec.get("uses_capabilities") or [] if isinstance(item, str)}
    undeclared = sorted(used - declared)
    if undeclared:
        errors.append(f"{PREFLIGHT_UNDECLARED}: {undeclared}")
    forbidden_allowlist = set(policy.get("forbidden_model_capabilities") or ()) | set(
        _FORBIDDEN_MODEL_CAPABILITIES
    )
    forbidden = sorted((declared | used) & forbidden_allowlist)
    if forbidden:
        errors.append(f"{PREFLIGHT_FORBIDDEN}: {forbidden}")
    model_id = str(spec.get("model_id") or "")
    if not model_id or _MODEL_ID_RE.fullmatch(model_id) is None:
        errors.append(f"{PREFLIGHT_MODEL_ID}: {model_id!r}")
    if spec.get("excluded_from_learning") is not True:
        errors.append("worker sessions must be excluded from future learning")
    return errors


def build_goal(run_id: str) -> dict[str, Any]:
    goal: dict[str, Any] = {
        "goal_id": f"project-memory-dream:{run_id}",
        "goal_version": 1,
        "summary": (
            "Nightly project-memory consolidation: normalize transcripts, collect project "
            "deltas, build evidence packets, synthesize bounded candidates, stage under "
            "shadow policy, and emit tau.project_dream_receipt.v1 terminal evidence."
        ),
        "completion_criteria": [
            "every selected project has a terminal row and no suppressed sibling",
            "shadow mode changes no active head",
            "terminal receipts validate under tau.project_dream_receipt.v1",
        ],
    }
    goal["goal_hash"] = canonical_sha256({k: v for k, v in goal.items()})
    return goal


def build_data_boundary(config: dict[str, Any], projects: list[str], run_dir: Path) -> dict[str, Any]:
    workspace = Path(str(config["workspace_root"])).resolve()
    return {
        "schema": DATA_BOUNDARY_SCHEMA,
        "allowed_projects": list(projects),
        "allowed_paths": [str(workspace), str(run_dir.resolve())],
        "home_transcript_access": False,
        "network": {"model_dispatch_only": True},
    }


def build_spec_with_orders(*, config_path: Path, run_dir: Path) -> tuple[dict[str, Any], dict[Path, dict[str, Any]]]:
    """Generate the canonical tau.generic_dag_spec.v1 for one consolidation run."""

    config = load_json(config_path)
    errors = validate_run_config(config)
    if errors:
        raise RuntimeError(f"invalid run config: {errors}")
    run_id = str(config.get("run_id") or "project-memory-dream")
    projects = discover_projects(config)
    if not projects:
        raise RuntimeError("no projects discovered under workspace_root/projects")
    workspace = Path(str(config["workspace_root"])).resolve()
    run_dir = run_dir.resolve()
    goal = build_goal(run_id)
    boundary = build_data_boundary(config, projects, run_dir)
    timeouts = config.get("timeouts") or {}
    retries = config.get("retries") or {}
    node_timeout = float(timeouts.get("node_seconds", 30))
    synth_timeout = float(timeouts.get("synthesis_seconds", 120))
    read_attempts = int(retries.get("read_only_max_attempts", 2))
    synth_attempts = int(retries.get("synthesis_max_attempts", 1))

    def node(node_id: str, mode: str, depends: list[str], *, project: str | None = None,
             timeout: float = node_timeout, attempts: int = 1) -> dict[str, Any]:
        work_order: dict[str, Any] = {
            "schema": "tau.project_memory_dream_work_order.v1",
            "node_id": node_id,
            "mode": mode,
            "run_id": run_id,
            "attempt_id": "attempt-001",
            "goal_hash": goal["goal_hash"],
            "config_path": str(config_path.resolve()),
            "run_dir": str(run_dir),
        }
        if project is not None:
            work_order["project_id"] = project
        order_path = run_dir / "work-orders" / f"{node_id}.json"
        entry: dict[str, Any] = {
            "node_id": node_id,
            "role": mode,
            "command": [
                "python",
                str(NODE_RUNNER),
                "--mode",
                mode,
                "--work-order",
                str(order_path),
            ],
            "depends_on": list(depends),
            "accepted_context_from": list(depends),
            "timeout_seconds": timeout,
            "max_attempts": attempts,
            "receipt_path": f"node-receipts/{node_id}.json",
            "work_order_path": str(order_path),
        }
        return entry, work_order

    nodes: list[dict[str, Any]] = []
    orders: dict[Path, dict[str, Any]] = {}

    def add(entry_tuple: tuple[dict[str, Any], dict[str, Any]]) -> str:
        entry, order = entry_tuple
        nodes.append(entry)
        orders[run_dir / "work-orders" / f"{entry['node_id']}.json"] = order
        return entry["node_id"]

    preflight = add(node("preflight", "preflight", []))
    for pid in projects:
        transcripts = add(node(f"transcripts.{pid}", "discover-and-normalize-transcripts", [preflight], project=pid, attempts=read_attempts))
        archive = add(node(f"archive.{pid}", "archive-and-compact-sessions", [transcripts], project=pid))
        delta = add(node(f"delta.{pid}", "discover-project-deltas", [preflight], project=pid, attempts=read_attempts))
        state = add(node(f"state.{pid}", "project-state", [delta], project=pid, attempts=read_attempts))
        ingest = add(node(f"ingest.{pid}", "ingest-code-incremental", [delta], project=pid, attempts=read_attempts))
        join = add(node(f"join.{pid}", "join", [archive, state, ingest], project=pid))
        packet = add(node(f"packet.{pid}", "build-evidence-packet", [join], project=pid))
        synth = add(node(f"synthesize.{pid}", "opencode-synthesize", [packet], project=pid, timeout=synth_timeout, attempts=synth_attempts))
        validate = add(node(f"validate.{pid}", "validate-candidate", [synth], project=pid))
        stage = add(node(f"stage.{pid}", "stage-candidate", [validate], project=pid))
        add(node(f"policy.{pid}", "promotion-policy", [stage], project=pid))
    policy_nodes = [f"policy.{pid}" for pid in projects]
    aggregate = add(node("aggregate", "aggregate", policy_nodes))

    spec: dict[str, Any] = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "goal": goal,
        "max_concurrency": int(config.get("max_parallel_projects", 2)),
        "data_boundary": boundary,
        "workflow": {
            "workflow_id": "project-memory-dream",
            "result_node_id": aggregate,
        },
        "nodes": nodes,
    }
    return spec, orders


def write_work_orders(work_orders: dict[Path, dict[str, Any]]) -> None:
    """Persist per-node work orders referenced by the spec (hash-bound by the scheduler)."""

    for path, order in work_orders.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(order, indent=2, sort_keys=True) + "\n", encoding="utf-8")
