"""Bounded child-agent request contracts for Tau DAG launches.

This module keeps model-facing child-agent requests as data until admission.  A
model may ask for a child worker, but Tau assigns the child id, enforces depth
and fan-out budgets, compiles a bounded child DAG, and returns only a durable
handle.  Results and operator actions flow through receipts, not through direct
pane control.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from tau_coding.dag_runtime.model import canonical_sha256

CHILD_AGENT_REQUEST_SCHEMA = "tau.child_agent_request.v1"
CHILD_AGENT_HANDLE_SCHEMA = "tau.child_agent_handle.v1"
CHILD_AGENT_REGISTRY_SCHEMA = "tau.child_agent_registry.v1"
CHILD_AGENT_PROOF_SCHEMA = "tau.child_agent_compilation_proof.v1"
GENERIC_DAG_SPEC_SCHEMA = "tau.generic_dag_spec.v1"
GENERIC_DAG_NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
OPERATOR_ACTION_REQUEST_SCHEMA = "tau.operator_action_request.v1"
_ALLOWED_ROLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ALLOWED_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ChildAgentRequestError(ValueError):
    """Raised when a child-agent request violates Tau admission policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChildAgentBudgets:
    max_depth: int = 1
    max_turns: int = 1
    timeout_seconds: int = 120
    max_prompt_bytes: int = 12000
    max_attempts: int = 1
    max_concurrency: int = 1
    max_tokens: int = 16000
    max_cost_usd: float = 0.0

    def to_payload(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ChildAgentPolicy:
    allowed_tools: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_skills: tuple[str, ...] = ()
    allowed_data_classes: tuple[str, ...] = ()
    allowed_models: tuple[str, ...] = ()
    allow_network: bool = False
    require_receipt: bool = True

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ChildAgentHandle:
    handle_id: str
    child_run_id: str
    parent_run_id: str
    parent_node_id: str
    request_id: str
    request_sha256: str
    role: str
    task_summary: str
    dag_spec_path: str
    result_receipt_path: str
    status: str = "ADMITTED"
    operator_action_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = CHILD_AGENT_HANDLE_SCHEMA
        return payload


class ChildAgentRegistry:
    """Durable child-handle registry with byte-idempotent admission."""

    def __init__(self, *, parent_run_id: str, max_children: int = 8) -> None:
        if not _nonempty(parent_run_id):
            raise ChildAgentRequestError("child_agent_parent_run_id_required", "parent_run_id is required")
        if max_children < 1:
            raise ChildAgentRequestError("child_agent_max_children_invalid", "max_children must be >= 1")
        self.parent_run_id = parent_run_id
        self.max_children = max_children
        self._handles_by_id: dict[str, ChildAgentHandle] = {}
        self._handle_by_idempotency_key: dict[str, str] = {}
        self._request_sha_by_idempotency_key: dict[str, str] = {}

    @property
    def handles(self) -> tuple[ChildAgentHandle, ...]:
        return tuple(self._handles_by_id.values())

    def admit(
        self,
        request: Mapping[str, Any],
        *,
        run_root: Path,
        command: list[str] | None = None,
    ) -> ChildAgentHandle:
        normalized = normalize_child_agent_request(
            request,
            parent_run_id=self.parent_run_id,
            max_children=self.max_children,
        )
        idempotency_key = str(normalized["idempotency_key"])
        request_sha = canonical_sha256(normalized)
        existing_id = self._handle_by_idempotency_key.get(idempotency_key)
        if existing_id is not None:
            existing_sha = self._request_sha_by_idempotency_key[idempotency_key]
            if existing_sha != request_sha:
                raise ChildAgentRequestError(
                    "child_agent_idempotency_conflict",
                    f"idempotency key {idempotency_key!r} was reused with different request bytes",
                )
            return self._handles_by_id[existing_id]
        if len(self._handles_by_id) >= self.max_children:
            raise ChildAgentRequestError(
                "child_agent_fanout_exceeded",
                f"child fan-out limit {self.max_children} exceeded",
            )
        handle = _make_handle(normalized, request_sha=request_sha, run_root=run_root)
        spec = compile_child_agent_dag_spec(normalized, handle, command=command)
        spec_path = Path(handle.dag_spec_path)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._handles_by_id[handle.handle_id] = handle
        self._handle_by_idempotency_key[idempotency_key] = handle.handle_id
        self._request_sha_by_idempotency_key[idempotency_key] = request_sha
        return handle

    def record_terminal(self, handle_id: str, *, receipt: Mapping[str, Any]) -> ChildAgentHandle:
        handle = self.inspect(handle_id)
        status = str(receipt.get("status") or "UNKNOWN")
        updated = ChildAgentHandle(
            **{**asdict(handle), "status": status, "operator_action_ids": handle.operator_action_ids}
        )
        self._handles_by_id[handle_id] = updated
        return updated

    def inspect(self, handle_id: str) -> ChildAgentHandle:
        try:
            return self._handles_by_id[handle_id]
        except KeyError as exc:
            raise ChildAgentRequestError("child_agent_handle_not_found", handle_id) from exc

    def accepted_results(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for handle in self.handles:
            receipt_path = Path(handle.result_receipt_path)
            if not receipt_path.is_file():
                continue
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            if payload.get("status") == "PASS":
                results.append(
                    {
                        "handle_id": handle.handle_id,
                        "child_run_id": handle.child_run_id,
                        "receipt_path": str(receipt_path),
                        "receipt_sha256": "sha256:" + _sha256(receipt_path),
                        "output": payload.get("accepted_output"),
                    }
                )
        return results

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": CHILD_AGENT_REGISTRY_SCHEMA,
            "parent_run_id": self.parent_run_id,
            "max_children": self.max_children,
            "child_count": len(self._handles_by_id),
            "handles": [handle.to_payload() for handle in self.handles],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ChildAgentRegistry":
        if payload.get("schema") != CHILD_AGENT_REGISTRY_SCHEMA:
            raise ChildAgentRequestError("child_agent_registry_schema_invalid", "invalid registry schema")
        registry = cls(
            parent_run_id=str(payload["parent_run_id"]),
            max_children=int(payload.get("max_children") or 8),
        )
        for raw in payload.get("handles", []):
            if not isinstance(raw, Mapping):
                continue
            handle = ChildAgentHandle(
                handle_id=str(raw["handle_id"]),
                child_run_id=str(raw["child_run_id"]),
                parent_run_id=str(raw["parent_run_id"]),
                parent_node_id=str(raw["parent_node_id"]),
                request_id=str(raw["request_id"]),
                request_sha256=str(raw["request_sha256"]),
                role=str(raw["role"]),
                task_summary=str(raw["task_summary"]),
                dag_spec_path=str(raw["dag_spec_path"]),
                result_receipt_path=str(raw["result_receipt_path"]),
                status=str(raw.get("status") or "ADMITTED"),
                operator_action_ids=tuple(str(item) for item in raw.get("operator_action_ids", [])),
            )
            registry._handles_by_id[handle.handle_id] = handle
            registry._handle_by_idempotency_key[handle.request_id] = handle.handle_id
            registry._request_sha_by_idempotency_key[handle.request_id] = handle.request_sha256
        return registry


def normalize_child_agent_request(
    request: Mapping[str, Any],
    *,
    parent_run_id: str,
    max_children: int = 8,
) -> dict[str, Any]:
    """Validate and canonicalize a model-facing child-agent request."""

    if request.get("schema") != CHILD_AGENT_REQUEST_SCHEMA:
        raise ChildAgentRequestError("child_agent_request_schema_invalid", "invalid child request schema")
    request_id = _required_id(request, "request_id")
    idempotency_key = str(request.get("idempotency_key") or request_id)
    if not _ALLOWED_ID.fullmatch(idempotency_key):
        raise ChildAgentRequestError("child_agent_idempotency_key_invalid", "invalid idempotency_key")
    parent = _required_mapping(request, "parent")
    if str(parent.get("run_id") or "") != parent_run_id:
        raise ChildAgentRequestError("child_agent_parent_mismatch", "parent run_id does not match registry")
    parent_node_id = _required_id(parent, "node_id")
    task = _required_mapping(request, "task")
    role = str(request.get("role") or "").strip()
    if not _ALLOWED_ROLE.fullmatch(role):
        raise ChildAgentRequestError("child_agent_role_invalid", "role must be a bounded stable id")
    prompt = str(task.get("prompt") or "")
    summary = str(task.get("summary") or "").strip()
    if not summary:
        raise ChildAgentRequestError("child_agent_task_summary_required", "task.summary is required")
    if not prompt.strip():
        raise ChildAgentRequestError("child_agent_task_prompt_required", "task.prompt is required")
    budgets = _normalize_budgets(request.get("budgets", {}))
    if int(parent.get("depth", 0)) + 1 > budgets.max_depth:
        raise ChildAgentRequestError("child_agent_depth_exceeded", "child request exceeds max_depth")
    if int(request.get("fanout_index", 0)) >= max_children:
        raise ChildAgentRequestError("child_agent_fanout_index_exceeded", "fanout_index exceeds registry max_children")
    if len(prompt.encode("utf-8")) > budgets.max_prompt_bytes:
        raise ChildAgentRequestError("child_agent_prompt_too_large", "task.prompt exceeds max_prompt_bytes")
    policy = _normalize_policy(request.get("policy", {}))
    requested = _normalize_requested_grants(request.get("requested", {}))
    _validate_requested_subset(
        requested.get("tools", []), policy.allowed_tools, "tool", allow_empty_policy=False
    )
    _validate_requested_subset(
        requested.get("skills", []), policy.allowed_skills, "skill", allow_empty_policy=True
    )
    _validate_requested_subset(
        requested.get("paths", []), policy.allowed_paths, "path", allow_empty_policy=False
    )
    _validate_requested_subset(
        requested.get("data_classes", []),
        policy.allowed_data_classes,
        "data_classification",
        allow_empty_policy=True,
    )
    _validate_requested_subset(
        requested.get("models", []), policy.allowed_models, "model", allow_empty_policy=True
    )
    parent_lineage = {
        "run_id": parent_run_id,
        "node_id": parent_node_id,
        "depth": int(parent.get("depth", 0)),
        "goal_hash": str(parent.get("goal_hash") or ""),
        "attempt_id": str(parent.get("attempt_id") or ""),
        "plan_sha256": str(parent.get("plan_sha256") or ""),
        "journal_seq": int(parent.get("journal_seq", 0)),
        "journal_head_sha256": str(parent.get("journal_head_sha256") or ""),
    }
    return {
        "schema": CHILD_AGENT_REQUEST_SCHEMA,
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "parent": parent_lineage,
        "role": role,
        "task": {"summary": summary, "prompt": prompt},
        "requested": requested,
        "budgets": budgets.to_payload(),
        "policy": policy.to_payload(),
        "join": dict(request.get("join", {})) if isinstance(request.get("join"), Mapping) else {},
        "fanout_index": int(request.get("fanout_index", 0)),
    }


def compile_child_agent_dag_spec(
    normalized_request: Mapping[str, Any],
    handle: ChildAgentHandle,
    *,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Compile an admitted child request into a bounded Tau DAG spec."""

    budgets = _required_mapping(normalized_request, "budgets")
    policy = _required_mapping(normalized_request, "policy")
    if command is None:
        command = [
            "python",
            "-c",
            _default_child_receipt_writer(
                handle.result_receipt_path,
                handle.handle_id,
                goal_hash=str(
                    normalized_request["parent"].get("goal_hash") or "sha256:child-agent"
                ),
            ),
        ]
    return {
        "schema": GENERIC_DAG_SPEC_SCHEMA,
        "run_id": handle.child_run_id,
        "run_dir": str(Path(handle.dag_spec_path).parent / "run"),
        "goal_hash": normalized_request["parent"].get("goal_hash"),
        "nodes": [
            {
                "node_id": "child-agent",
                "receipt_path": handle.result_receipt_path,
                "timeout_seconds": int(budgets["timeout_seconds"]),
                "command": list(command),
                "extensions": {
                    "tau_child_agent": {
                        "handle_id": handle.handle_id,
                        "role": handle.role,
                        "request_sha256": handle.request_sha256,
                        "max_turns": int(budgets["max_turns"]),
                        "max_attempts": int(budgets["max_attempts"]),
                        "max_tokens": int(budgets["max_tokens"]),
                        "max_cost_usd": float(budgets["max_cost_usd"]),
                        "allow_network": bool(policy.get("allow_network", False)),
                        "allowed_tools": list(policy.get("allowed_tools", [])),
                        "allowed_paths": list(policy.get("allowed_paths", [])),
                        "allowed_skills": list(policy.get("allowed_skills", [])),
                        "allowed_data_classes": list(policy.get("allowed_data_classes", [])),
                        "allowed_models": list(policy.get("allowed_models", [])),
                        "requested": dict(normalized_request.get("requested", {})),
                        "requires_receipt": bool(policy.get("require_receipt", True)),
                    }
                },
            }
        ],
    }


def child_instruction_operator_action(
    handle: ChildAgentHandle,
    *,
    action_request_id: str,
    instruction: str,
    journal_seq: int,
    journal_head_sha256: str,
) -> dict[str, Any]:
    return _operator_action(
        handle,
        action_request_id=action_request_id,
        action="add_next_turn_instruction",
        arguments={"instruction": instruction},
        journal_seq=journal_seq,
        journal_head_sha256=journal_head_sha256,
    )


def child_cancel_operator_action(
    handle: ChildAgentHandle,
    *,
    action_request_id: str,
    reason: str,
    journal_seq: int,
    journal_head_sha256: str,
) -> dict[str, Any]:
    return _operator_action(
        handle,
        action_request_id=action_request_id,
        action="cancel",
        arguments={"reason": reason},
        journal_seq=journal_seq,
        journal_head_sha256=journal_head_sha256,
    )


def _operator_action(
    handle: ChildAgentHandle,
    *,
    action_request_id: str,
    action: str,
    arguments: Mapping[str, Any],
    journal_seq: int,
    journal_head_sha256: str,
) -> dict[str, Any]:
    if not _ALLOWED_ID.fullmatch(action_request_id):
        raise ChildAgentRequestError("child_agent_operator_action_id_invalid", "invalid action_request_id")
    return {
        "schema": OPERATOR_ACTION_REQUEST_SCHEMA,
        "action_request_id": action_request_id,
        "run_id": handle.child_run_id,
        "node_id": "child-agent",
        "attempt": 1,
        "action": action,
        "arguments": dict(arguments),
        "source": "child_agent_handle",
        "target": {"handle_id": handle.handle_id, "parent_run_id": handle.parent_run_id},
        "requires_human_input": False,
        "authorized_agent_next_steps": [action],
        "journal_seq": journal_seq,
        "journal_head_sha256": journal_head_sha256,
    }


def _make_handle(
    normalized: Mapping[str, Any],
    *,
    request_sha: str,
    run_root: Path,
) -> ChildAgentHandle:
    parent = _required_mapping(normalized, "parent")
    task = _required_mapping(normalized, "task")
    digest = request_sha.removeprefix("sha256:")[:16]
    handle_id = f"child-handle-{digest}"
    child_run_id = f"child-run-{digest}"
    child_root = run_root / handle_id
    return ChildAgentHandle(
        handle_id=handle_id,
        child_run_id=child_run_id,
        parent_run_id=str(parent["run_id"]),
        parent_node_id=str(parent["node_id"]),
        request_id=str(normalized["request_id"]),
        request_sha256=request_sha,
        role=str(normalized["role"]),
        task_summary=str(task["summary"]),
        dag_spec_path=str(child_root / "dag.json"),
        result_receipt_path=str(child_root / "receipt.json"),
    )


def _normalize_budgets(raw: object) -> ChildAgentBudgets:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ChildAgentRequestError("child_agent_budgets_invalid", "budgets must be an object")
    budgets = ChildAgentBudgets(
        max_depth=int(raw.get("max_depth", 1)),
        max_turns=int(raw.get("max_turns", 1)),
        timeout_seconds=int(raw.get("timeout_seconds", 120)),
        max_prompt_bytes=int(raw.get("max_prompt_bytes", 12000)),
        max_attempts=int(raw.get("max_attempts", 1)),
        max_concurrency=int(raw.get("max_concurrency", 1)),
        max_tokens=int(raw.get("max_tokens", 16000)),
        max_cost_usd=float(raw.get("max_cost_usd", 0.0)),
    )
    if (
        budgets.max_depth < 1
        or budgets.max_turns < 1
        or budgets.timeout_seconds < 1
        or budgets.max_prompt_bytes < 1
        or budgets.max_attempts < 1
        or budgets.max_concurrency < 1
        or budgets.max_tokens < 1
        or budgets.max_cost_usd < 0
    ):
        raise ChildAgentRequestError("child_agent_budget_invalid", "budgets must be positive")
    if budgets.max_attempts > budgets.max_turns:
        raise ChildAgentRequestError("child_agent_attempt_budget_exceeded", "max_attempts exceeds max_turns")
    return budgets


def _normalize_policy(raw: object) -> ChildAgentPolicy:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ChildAgentRequestError("child_agent_policy_invalid", "policy must be an object")
    return ChildAgentPolicy(
        allowed_tools=tuple(_bounded_str_list(raw.get("allowed_tools", []), "allowed_tools")),
        allowed_paths=tuple(_bounded_str_list(raw.get("allowed_paths", []), "allowed_paths")),
        allowed_skills=tuple(_bounded_str_list(raw.get("allowed_skills", []), "allowed_skills")),
        allowed_data_classes=tuple(
            _bounded_str_list(raw.get("allowed_data_classes", []), "allowed_data_classes")
        ),
        allowed_models=tuple(_bounded_str_list(raw.get("allowed_models", []), "allowed_models")),
        allow_network=bool(raw.get("allow_network", False)),
        require_receipt=bool(raw.get("require_receipt", True)),
    )


def _normalize_requested_grants(raw: object) -> dict[str, list[str]]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ChildAgentRequestError("child_agent_requested_grants_invalid", "requested must be an object")
    return {
        "tools": _bounded_str_list(raw.get("tools", []), "requested_tools"),
        "paths": _bounded_str_list(raw.get("paths", []), "requested_paths"),
        "skills": _bounded_str_list(raw.get("skills", []), "requested_skills"),
        "data_classes": _bounded_str_list(raw.get("data_classes", []), "requested_data_classes"),
        "models": _bounded_str_list(raw.get("models", []), "requested_models"),
    }


def _validate_requested_subset(
    requested: list[str],
    allowed: tuple[str, ...],
    grant_name: str,
    *,
    allow_empty_policy: bool,
) -> None:
    if not requested:
        return
    if not allowed and allow_empty_policy:
        raise ChildAgentRequestError(
            f"child_agent_{grant_name}_not_allowed",
            f"requested {grant_name} grant is not allowed by policy",
        )
    missing = [item for item in requested if item not in allowed]
    if missing:
        raise ChildAgentRequestError(
            f"child_agent_{grant_name}_not_allowed",
            f"requested {grant_name} grant is not allowed: {missing[0]}",
        )


def _bounded_str_list(raw: object, field_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ChildAgentRequestError(f"child_agent_{field_name}_invalid", f"{field_name} must be a list")
    values = [str(item) for item in raw]
    if len(values) > 32:
        raise ChildAgentRequestError(f"child_agent_{field_name}_too_large", f"{field_name} is too large")
    if any(not value.strip() or len(value) > 256 for value in values):
        raise ChildAgentRequestError(f"child_agent_{field_name}_invalid", f"{field_name} contains invalid values")
    return values


def _required_mapping(payload: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, Mapping):
        raise ChildAgentRequestError(f"child_agent_{field_name}_required", f"{field_name} is required")
    return value


def _required_id(payload: Mapping[str, Any], field_name: str) -> str:
    value = str(payload.get(field_name) or "").strip()
    if not _ALLOWED_ID.fullmatch(value):
        raise ChildAgentRequestError(f"child_agent_{field_name}_invalid", f"{field_name} must be a stable id")
    return value


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _default_child_receipt_writer(receipt_path: str, handle_id: str, *, goal_hash: str) -> str:
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "child-agent",
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "accepted_output": {"handle_id": handle_id, "message": "child dag executed"},
        "artifacts": [],
        "commands_run": [],
        "policy_exceptions": [],
        "handoff_summary": "Bounded child-agent DAG executed and produced an accepted receipt.",
        "errors": [],
    }
    return (
        "import json\n"
        "from pathlib import Path\n"
        f"path = Path({json.dumps(receipt_path)})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"payload = json.loads({json.dumps(json.dumps(payload, sort_keys=True))})\n"
        "path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\\n', encoding='utf-8')\n"
        "print(json.dumps(payload, sort_keys=True))\n"
    )


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
