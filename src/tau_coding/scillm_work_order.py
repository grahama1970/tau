"""Strict boundary for ``tau.executor.scillm_worker.v1`` work orders (tau#341).

Before this module, ``tau scillm-worker-launch`` accepted a work order with
missing, mistyped, or undeclared fields and returned PASS on a dry run; the
gate alerts only checked paths, substrate, and route metadata. This model is
the single typed contract applied before any request payload is constructed
or any HTTP call is made. ``extra="forbid"`` makes undeclared fields a hard
rejection; identity (``dag_id``, ``node_id``, ``agent``, ``goal_hash``,
``attempt``, ``task``) is mandatory in dry-run and apply alike.

Validation errors are surfaced as one alert with code
``invalid_work_order`` whose ``errors`` list carries pydantic ``loc``/``type``
diagnostics, so a caller can repair the exact field without reading prose.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCILLM_WORK_ORDER_SCHEMA = "tau.executor.scillm_worker.v1"
SCILLM_OPENCODE_SERVE_ENDPOINT = "/v1/scillm/opencode/runs"
GOAL_HASH_PREFIX = "sha256:"
OPENCODE_SERVE_TRANSPORT = "opencode.serve"


class ScillmModelProviderRoute(BaseModel):
    """``model_provider_route`` for the OpenCode-serve surface."""

    model_config = ConfigDict(extra="forbid", strict=True)

    surface: Literal["opencode_serve"]
    endpoint: Literal["/v1/scillm/opencode/runs"] = SCILLM_OPENCODE_SERVE_ENDPOINT
    agent: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1)
    skills: list[str] | None = None

    @field_validator("agent")
    @classmethod
    def agent_is_profile_not_chat_model(cls, value: str) -> str:
        if value.startswith("opencode-go/"):
            raise ValueError(
                "agent must be an OpenCode agent profile, not an opencode-go/* chat model"
            )
        return value

    @field_validator("skills")
    @classmethod
    def skills_non_empty_strings(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not item for item in value):
            raise ValueError("skills entries must be non-empty strings")
        return value


class OpenCodeWorktreeBinding(BaseModel):
    """Workspace binding for the ``opencode.serve`` authoring transport (tau#355).

    This is the second, named workspace-capable authoring lane beside
    ``codex.exec``. It binds an OpenCode serve session to a Git-worktree lease
    managed by ``runtime_backends.worktrees.GitWorktreeLeaseManager``: the
    session runs with ``cwd=worktree_root`` under a pinned authoring agent
    profile, and the work order is not complete until the lease admission
    receipt exists and a cleanup-authorized release has run. Fail closed at
    every step; there is no authoring without the lease receipts.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    transport: Literal["opencode.serve"]
    worktree_state_root: str = Field(min_length=1)
    # Optional expected cross-checks: the launch allocates the lease and
    # derives these paths deterministically; a caller who declares them gets a
    # fail-closed mismatch alert instead of silent drift.
    worktree_root: str | None = None
    lease_receipt_path: str | None = None
    admission_receipt_path: str | None = None
    release_required: Literal[True] = True
    agent_profile: str = Field(min_length=1)

    @field_validator("worktree_state_root")
    @classmethod
    def state_root_not_disposable(cls, value: str) -> str:
        # The worktree audit forbids /tmp worktrees: an authoring lane whose
        # leased workspace can vanish with a reboot is not a repair route.
        # Applies to the declared state root and any declared cross-check.
        if value.startswith("/tmp/") or value == "/tmp":
            raise ValueError("worktree paths must not live under /tmp")
        return value

    @field_validator("worktree_root", "lease_receipt_path", "admission_receipt_path")
    @classmethod
    def receipt_paths_not_disposable(cls, value: str | None) -> str | None:
        if value is not None and (value.startswith("/tmp/") or value == "/tmp"):
            raise ValueError("worktree paths must not live under /tmp")
        return value

    @field_validator("agent_profile")
    @classmethod
    def agent_profile_is_not_chat_model(cls, value: str) -> str:
        if value.startswith("opencode-go/"):
            raise ValueError(
                "agent_profile must be an OpenCode authoring agent profile, not an opencode-go/* chat model"
            )
        return value


class ScillmWorkerWorkOrder(BaseModel):
    """Typed ``tau.executor.scillm_worker.v1`` work order.

    ``strict=True`` rejects coercion (``"1"`` is not ``1``; ``123`` is not a
    string). Path/substrate/policy semantics that need filesystem or receipt
    read-back stay in ``coding_worker_adapters._append_work_order_gate_alerts``;
    this model owns shape, type, and presence.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_: Literal["tau.executor.scillm_worker.v1"] = Field(alias="schema")
    dag_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    goal_hash: str = Field(min_length=len(GOAL_HASH_PREFIX) + 1)
    attempt: int = Field(ge=1)
    task: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)
    result_path: str = Field(min_length=1)
    receipt_path: str = Field(min_length=1)
    # Required by the launch surface (checked there with its own codes); a
    # validate-only work order may omit it, so shape is enforced only when present.
    model_provider_route: ScillmModelProviderRoute | None = None
    workspace: OpenCodeWorktreeBinding | None = None
    forbidden_paths: list[str] | None = None
    required_artifacts: list[str] | None = None
    timeout_s: int | None = Field(default=None, ge=1)
    high_stakes: bool | None = None
    zero_trust: bool | None = None
    execution_substrate: str | None = Field(default=None, min_length=1)
    substrate: str | None = Field(default=None, min_length=1)
    sandbox_receipt_path: str | None = Field(default=None, min_length=1)
    herdr_receipt_path: str | None = Field(default=None, min_length=1)
    herdr_binding: dict[str, Any] | None = None
    policy_profile: dict[str, Any] | None = None
    data_boundary: dict[str, Any] | None = None
    target: str | None = Field(default=None, min_length=1)
    expected_worker_result_path: str | None = Field(default=None, min_length=1)
    # Injected by the adapter after reading the file; never author-supplied.
    work_order_path: str | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def empty_route_means_absent(cls, data: Any) -> Any:
        # Validate-only work orders historically carried ``model_provider_route: {}``;
        # the launch surface still rejects that with ``invalid_scillm_surface``.
        if isinstance(data, dict) and data.get("model_provider_route") == {}:
            data = dict(data)
            data["model_provider_route"] = None
        return data

    @model_validator(mode="after")
    def workspace_requires_opencode_serve(self) -> "ScillmWorkerWorkOrder":
        # tau#355: workspace authoring is allowed on exactly two transports —
        # codex.exec (ask seam) and opencode.serve (this boundary). A workspace
        # on any other surface is a capability-invariant violation, not a
        # routing preference, so it fails closed here at the typed boundary.
        if self.workspace is not None:
            route = self.model_provider_route
            if route is None or route.surface != "opencode_serve":
                raise ValueError(
                    "workspace requires model_provider_route.surface='opencode_serve' "
                    "(the opencode.serve authoring transport); chat/review surfaces stay workspace-less"
                )
            if self.workspace.agent_profile != route.agent:
                raise ValueError(
                    "workspace.agent_profile must equal model_provider_route.agent"
                )
        return self

    @field_validator("goal_hash")
    @classmethod
    def goal_hash_shape(cls, value: str) -> str:
        if not value.startswith(GOAL_HASH_PREFIX):
            raise ValueError("goal_hash must start with sha256:")
        return value

    @field_validator("allowed_paths", "forbidden_paths", "required_artifacts")
    @classmethod
    def path_lists_non_empty_strings(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not item for item in value):
            raise ValueError("path entries must be non-empty strings")
        return value


def work_order_validation_errors(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return pydantic diagnostics for a raw work order, or [] when valid."""
    try:
        ScillmWorkerWorkOrder.model_validate(dict(payload))
    except ValidationError as exc:
        return [
            {
                "loc": ".".join(str(part) for part in error["loc"]) or "$",
                "type": error["type"],
                "msg": error["msg"],
            }
            for error in exc.errors(include_url=False, include_input=False)
        ]
    return []


def format_work_order_errors(errors: list[dict[str, Any]]) -> list[str]:
    return [f"{item['loc']}: {item['type']}: {item['msg']}" for item in errors]


# Legacy per-field alert codes retained for callers that key on them (tau#341
# keeps them alongside the typed ``invalid_work_order`` alert).
LEGACY_ALERT_CODES: dict[str, str] = {
    "schema": "invalid_work_order_schema",
    "allowed_paths": "invalid_allowed_paths",
    "forbidden_paths": "invalid_forbidden_paths",
    "timeout_s": "invalid_scillm_worker_timeout",
    "model_provider_route.surface": "invalid_scillm_surface",
    "model_provider_route.endpoint": "invalid_scillm_endpoint",
    "model_provider_route.agent": "missing_scillm_agent_profile",
    "workspace": "invalid_workspace_binding",
    "workspace.transport": "invalid_workspace_transport",
    "workspace.worktree_root": "invalid_worktree_root",
    "workspace.agent_profile": "chat_model_used_as_agent",
    "workspace.lease_receipt_path": "missing_worktree_lease_receipt",
    "workspace.admission_receipt_path": "missing_worktree_admission_receipt",
}


def legacy_alert_codes(errors: list[dict[str, Any]]) -> list[str]:
    codes: list[str] = []
    for item in errors:
        loc = item["loc"]
        if loc == "model_provider_route.agent" and "opencode-go/" in item["msg"]:
            code = "chat_model_used_as_agent"
        else:
            code = LEGACY_ALERT_CODES.get(loc, "")
        if code and code not in codes:
            codes.append(code)
    return codes


__all__ = [
    "OPENCODE_SERVE_TRANSPORT",
    "SCILLM_WORK_ORDER_SCHEMA",
    "OpenCodeWorktreeBinding",
    "ScillmModelProviderRoute",
    "ScillmWorkerWorkOrder",
    "format_work_order_errors",
    "legacy_alert_codes",
    "work_order_validation_errors",
]
