"""Minimal generated-ticket validation and GitHub projection for Tau."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAU_AGENT_COMMON_SCHEMA = "tau.agent_common.v1"
TAU_AGENT_HANDOFF_SCHEMA = "tau.agent_handoff.v1"
TAU_GENERATED_TICKET_SCHEMA = "tau.generated_ticket.v1"

ROUTABLE_AGENTS = frozenset(
    {
        "human",
        "goal-guardian",
        "webgpt-ticket-author",
        "coder",
        "reviewer",
        "releaser",
        "dba-auditor-v2",
        "dewey",
        "prompt-health-auditor",
        "petey",
        "qra-auditor",
        "qbert",
        "research-auditor",
        "ryan",
        "monitor-sparta-supervisor",
        "dreamer",
        "dream-reviewer",
        "story-writer",
        "story-reviewer",
        "panel-creator",
        "panel-reviewer",
        "persona-dream-panel-repair-gate",
        "battle-scorekeeper",
    }
)
TICKET_CREATORS = frozenset({"chatgpt-pro", "chatgpt_pro", "webgpt", "webgpt-ticket-author"})
EXECUTORS = frozenset(
    {"github-actions", "local", "either", "human", "codex", "opencode", "webgpt", "scillm"}
)
TICKET_KINDS = frozenset({"issue", "pull_request"})

REQUIRED_GENERATED_TICKET_FIELDS = (
    "schema",
    "github",
    "goal",
    "previous_subagent",
    "context",
    "ticket",
    "requested_work",
    "rationale",
    "next_agent",
    "required_evidence",
    "stop_condition",
    "goal_amendment_proposal",
)
REQUIRED_HANDOFF_FIELDS = (
    "schema",
    "github",
    "goal",
    "previous_subagent",
    "context",
    "result",
    "rationale",
    "next_agent",
    "required_evidence",
    "stop_condition",
)
REQUIRED_GOAL_FIELDS = ("goal_id", "goal_version", "goal_hash")
REQUIRED_CONTEXT_FIELDS = ("summary", "artifacts")
REQUIRED_TICKET_FIELDS = ("kind", "title", "body")
REQUIRED_NEXT_FIELDS = ("name", "reason")


@dataclass(frozen=True, slots=True)
class GithubProjection:
    """Deterministic GitHub issue projection derived by Tau."""

    kind: str
    title: str
    body: str
    labels: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable projection."""

        return {
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "labels": list(self.labels),
        }


@dataclass(frozen=True, slots=True)
class GeneratedTicketValidationResult:
    """Validation result for one minimal generated-ticket draft."""

    ok: bool
    next_agent: str | None = None
    github_create: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentHandoffProjectionResult:
    """Non-mutating GitHub projection for one validated agent handoff."""

    ok: bool
    dry_run: bool = True
    next_agent: str | None = None
    target: dict[str, Any] | None = None
    labels: dict[str, list[str]] | None = None
    comment: dict[str, str] | None = None
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable projection receipt payload."""

        return {
            "schema": "tau.agent_handoff_projection_receipt.v1",
            "ok": self.ok,
            "dry_run": self.dry_run,
            "next_agent": self.next_agent,
            "target": self.target,
            "labels": self.labels,
            "comment": self.comment,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class AgentHandoffChainResult:
    """Non-mutating dry-run receipt for a sequence of routed handoffs."""

    ok: bool
    dry_run: bool = True
    handoff_count: int = 0
    projections: tuple[dict[str, Any], ...] = ()
    receipt_dir: str | None = None
    artifacts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable chain receipt payload."""

        return {
            "schema": "tau.agent_handoff_chain_receipt.v1",
            "ok": self.ok,
            "dry_run": self.dry_run,
            "handoff_count": self.handoff_count,
            "projections": list(self.projections),
            "receipt_dir": self.receipt_dir,
            "artifacts": list(self.artifacts),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class AgentHandoffLoopResult:
    """Non-mutating dry-run receipt for a routed handoff loop."""

    ok: bool
    status: str
    dry_run: bool = True
    step_count: int = 0
    terminal_agent: str | None = None
    stop_reason: str | None = None
    projections: tuple[dict[str, Any], ...] = ()
    receipt_dir: str | None = None
    artifacts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable loop receipt payload."""

        return {
            "schema": "tau.agent_handoff_loop_receipt.v1",
            "ok": self.ok,
            "status": self.status,
            "dry_run": self.dry_run,
            "step_count": self.step_count,
            "terminal_agent": self.terminal_agent,
            "stop_reason": self.stop_reason,
            "projections": list(self.projections),
            "receipt_dir": self.receipt_dir,
            "artifacts": list(self.artifacts),
            "errors": list(self.errors),
        }


def derived_labels(next_agent: str, executor: str | None = None) -> tuple[str, ...]:
    """Return the canonical labels Tau derives from a next-agent route."""

    resolved_executor = executor or "either"
    return ("agent-work", f"next:{next_agent}", f"executor:{resolved_executor}")


def load_agent_registry_ids(root: Path) -> frozenset[str]:
    """Load active agent ids from an agent-skills-style agents directory."""

    resolved = root.expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"agent registry root does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"agent registry root is not a directory: {resolved}")

    agent_ids: set[str] = set()
    for child in sorted(resolved.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        agents_md = child / "AGENTS.md"
        if not agents_md.exists():
            continue
        metadata = _read_agents_frontmatter(agents_md)
        if str(metadata.get("active", "true")).strip().lower() == "false":
            continue
        agent_ids.add(child.name)
        frontmatter_id = metadata.get("id")
        if isinstance(frontmatter_id, str) and frontmatter_id.strip():
            agent_ids.add(frontmatter_id.strip())
    return frozenset(agent_ids)


def load_generated_ticket(path: Path) -> dict[str, Any]:
    """Load one generated-ticket JSON file."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("generated ticket root must be a JSON object")
    return payload


def validate_generated_ticket_file(
    path: Path,
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> GeneratedTicketValidationResult:
    """Validate one generated-ticket JSON file."""

    try:
        payload = load_generated_ticket(path)
    except Exception as exc:
        return GeneratedTicketValidationResult(ok=False, errors=(f"invalid json: {exc}",))
    return validate_generated_ticket(
        payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )


def validate_generated_ticket(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> GeneratedTicketValidationResult:
    """Validate a minimal ChatGPT Pro/WebGPT ticket draft.

    The agent supplies ticket text and the next route. Tau derives labels and
    the GitHub create projection after validation.
    """

    errors: list[str] = []
    routable_agents = _routable_agents(agent_registry_root, errors)
    _require_fields(payload, REQUIRED_GENERATED_TICKET_FIELDS, "generated_ticket", errors)
    if payload.get("schema") != TAU_GENERATED_TICKET_SCHEMA:
        errors.append(
            f"generated_ticket.schema must be {TAU_GENERATED_TICKET_SCHEMA!r}; "
            f"got {payload.get('schema')!r}"
        )

    github = _mapping_field(payload, "github", "generated_ticket", errors)
    goal = _mapping_field(payload, "goal", "generated_ticket", errors)
    context = _mapping_field(payload, "context", "generated_ticket", errors)
    ticket = _mapping_field(payload, "ticket", "generated_ticket", errors)
    next_agent_payload = _mapping_field(payload, "next_agent", "generated_ticket", errors)

    _require_fields(github, ("repo",), "github", errors)
    _require_fields(goal, REQUIRED_GOAL_FIELDS, "goal", errors)
    _require_fields(context, REQUIRED_CONTEXT_FIELDS, "context", errors)
    _require_fields(ticket, REQUIRED_TICKET_FIELDS, "ticket", errors)
    _require_fields(next_agent_payload, REQUIRED_NEXT_FIELDS, "next_agent", errors)

    _non_empty_string(github, "repo", "github", errors)
    previous_agent = _non_empty_string(payload, "previous_subagent", "generated_ticket", errors)
    if previous_agent and previous_agent not in TICKET_CREATORS:
        errors.append(f"previous_subagent may not create tickets: {previous_agent}")

    goal_hash = _non_empty_string(goal, "goal_hash", "goal", errors)
    _non_empty_string(goal, "goal_id", "goal", errors)
    if not isinstance(goal.get("goal_version"), int):
        errors.append("goal.goal_version must be an integer")
    if active_goal_hash and goal_hash and goal_hash != active_goal_hash:
        errors.append("generated ticket may not change goal.goal_hash")

    _non_empty_string(context, "summary", "context", errors)
    if not isinstance(context.get("artifacts"), list):
        errors.append("context.artifacts must be a list")

    ticket_kind = _non_empty_string(ticket, "kind", "ticket", errors)
    ticket_title = _non_empty_string(ticket, "title", "ticket", errors)
    ticket_body = _non_empty_string(ticket, "body", "ticket", errors)
    if ticket_kind and ticket_kind not in TICKET_KINDS:
        errors.append(f"ticket.kind must be one of {sorted(TICKET_KINDS)}")

    next_name = _non_empty_string(next_agent_payload, "name", "next_agent", errors)
    next_executor = _optional_string(next_agent_payload, "executor", "next_agent", errors)
    _non_empty_string(next_agent_payload, "reason", "next_agent", errors)
    if next_name and next_name not in routable_agents:
        errors.append(f"next_agent.name must be one of {sorted(routable_agents)}")
    if next_executor and next_executor not in EXECUTORS:
        errors.append(f"next_agent.executor must be one of {sorted(EXECUTORS)}")

    _non_empty_string(payload, "requested_work", "generated_ticket", errors)
    _non_empty_string(payload, "rationale", "generated_ticket", errors)
    _require_string_list(payload, "required_evidence", "generated_ticket", errors)
    _non_empty_string(payload, "stop_condition", "generated_ticket", errors)

    goal_amendment = payload.get("goal_amendment_proposal")
    if goal_amendment is not None and next_name not in {"human", "goal-guardian"}:
        errors.append("goal_amendment_proposal requires next_agent.name human or goal-guardian")

    projection: GithubProjection | None = None
    if not errors and ticket_kind and ticket_title and ticket_body and next_name:
        projection = GithubProjection(
            kind=ticket_kind,
            title=ticket_title,
            body=ticket_body,
            labels=derived_labels(next_name, next_executor),
        )

    return GeneratedTicketValidationResult(
        ok=not errors,
        next_agent=next_name if not errors else None,
        github_create=projection.as_dict() if projection else None,
        errors=tuple(errors),
    )


def validate_agent_handoff(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> GeneratedTicketValidationResult:
    """Validate a minimal handoff used by bounded subagents and comments."""

    errors: list[str] = []
    routable_agents = _routable_agents(agent_registry_root, errors)
    _require_fields(payload, REQUIRED_HANDOFF_FIELDS, "agent_handoff", errors)
    if payload.get("schema") != TAU_AGENT_HANDOFF_SCHEMA:
        errors.append(
            f"agent_handoff.schema must be {TAU_AGENT_HANDOFF_SCHEMA!r}; "
            f"got {payload.get('schema')!r}"
        )
    goal = _mapping_field(payload, "goal", "agent_handoff", errors)
    context = _mapping_field(payload, "context", "agent_handoff", errors)
    result = _mapping_field(payload, "result", "agent_handoff", errors)
    next_agent_payload = _mapping_field(payload, "next_agent", "agent_handoff", errors)
    _require_fields(goal, REQUIRED_GOAL_FIELDS, "goal", errors)
    _require_fields(context, REQUIRED_CONTEXT_FIELDS, "context", errors)
    _require_fields(result, ("status", "summary", "evidence"), "result", errors)
    _require_fields(next_agent_payload, REQUIRED_NEXT_FIELDS, "next_agent", errors)

    goal_hash = _non_empty_string(goal, "goal_hash", "goal", errors)
    if active_goal_hash and goal_hash and goal_hash != active_goal_hash:
        errors.append("agent handoff may not change goal.goal_hash")
    previous_agent = _non_empty_string(payload, "previous_subagent", "agent_handoff", errors)
    if previous_agent and previous_agent not in routable_agents and previous_agent != "chatgpt-pro":
        errors.append(f"previous_subagent is not recognized: {previous_agent}")
    next_name = _non_empty_string(next_agent_payload, "name", "next_agent", errors)
    next_executor = _optional_string(next_agent_payload, "executor", "next_agent", errors)
    if next_name and next_name not in routable_agents:
        errors.append(f"next_agent.name must be one of {sorted(routable_agents)}")
    if next_executor and next_executor not in EXECUTORS:
        errors.append(f"next_agent.executor must be one of {sorted(EXECUTORS)}")
    _non_empty_string(payload, "rationale", "agent_handoff", errors)
    _require_string_list(payload, "required_evidence", "agent_handoff", errors)
    _non_empty_string(payload, "stop_condition", "agent_handoff", errors)
    return GeneratedTicketValidationResult(
        ok=not errors,
        next_agent=next_name if not errors else None,
        errors=tuple(errors),
    )


def project_agent_handoff(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffProjectionResult:
    """Project a handoff into GitHub comment and label changes without mutating GitHub."""

    validation = validate_agent_handoff(
        payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    projection_errors = list(validation.errors)

    github = _mapping_field(payload, "github", "agent_handoff", projection_errors)
    _require_fields(github, ("repo", "target"), "github", projection_errors)
    repo = _non_empty_string(github, "repo", "github", projection_errors)
    target = _non_empty_string(github, "target", "github", projection_errors)

    if projection_errors:
        return AgentHandoffProjectionResult(
            ok=False,
            next_agent=validation.next_agent,
            errors=tuple(projection_errors),
        )

    next_agent_payload = payload["next_agent"]
    next_agent = str(next_agent_payload["name"])
    executor = next_agent_payload.get("executor")
    if not isinstance(executor, str) or not executor.strip():
        executor = "either"

    remove_labels = _stale_state_and_route_labels(github, next_agent, executor)
    labels = {
        "add": list(derived_labels(next_agent, executor)),
        "remove": remove_labels,
    }
    return AgentHandoffProjectionResult(
        ok=True,
        next_agent=next_agent,
        target={"repo": repo, "target": target},
        labels=labels,
        comment={"body": render_agent_handoff_comment(payload)},
    )


def render_agent_handoff_comment(payload: Mapping[str, Any]) -> str:
    """Render the durable GitHub comment body for a handoff projection."""

    result = payload.get("result", {})
    next_agent = payload.get("next_agent", {})
    status = result.get("status", "UNKNOWN") if isinstance(result, Mapping) else "UNKNOWN"
    summary = result.get("summary", "") if isinstance(result, Mapping) else ""
    next_name = next_agent.get("name", "") if isinstance(next_agent, Mapping) else ""
    executor = next_agent.get("executor", "either") if isinstance(next_agent, Mapping) else "either"
    stop_condition = payload.get("stop_condition", "")
    json_block = json.dumps(payload, indent=2, sort_keys=True)
    return (
        "## Tau Agent Handoff\n\n"
        f"- Result: `{status}`\n"
        f"- Summary: {summary}\n"
        f"- Next agent: `{next_name}`\n"
        f"- Executor: `{executor or 'either'}`\n"
        f"- Stop condition: {stop_condition}\n\n"
        "<!-- tau-agent-handoff:v1 -->\n"
        "```json\n"
        f"{json_block}\n"
        "```\n"
    )


def _stale_state_and_route_labels(
    github: Mapping[str, Any],
    next_agent: str,
    executor: str | None,
) -> list[str]:
    """Return stale state/route labels from the caller-supplied current label snapshot."""

    current_labels = github.get("current_labels")
    if not isinstance(current_labels, list):
        return []
    keep = {f"next:{next_agent}", f"executor:{executor or 'either'}"}
    stale: list[str] = []
    for label in current_labels:
        if not isinstance(label, str):
            continue
        if label in {"agent-active", "agent-blocked"} or (
            (label.startswith("next:") or label.startswith("executor:")) and label not in keep
        ):
            stale.append(label)
    return stale


def write_agent_handoff_projection_receipt(
    payload: Mapping[str, Any],
    receipt_path: Path,
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffProjectionResult:
    """Write a dry-run handoff projection receipt for deterministic inspection."""

    projection = project_agent_handoff(
        payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(projection.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return projection


def project_agent_handoff_chain(
    payloads: list[Mapping[str, Any]],
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffChainResult:
    """Project a local sequence of handoffs and fail closed on route discontinuity."""

    if not payloads:
        return AgentHandoffChainResult(
            ok=False,
            errors=("handoff chain requires at least one handoff",),
        )

    errors: list[str] = []
    projection_dicts: list[dict[str, Any]] = []
    prior_next_agent: str | None = None
    first_target: dict[str, Any] | None = None
    first_goal: tuple[Any, Any, Any] | None = None

    for index, payload in enumerate(payloads, start=1):
        projection = project_agent_handoff(
            payload,
            active_goal_hash=active_goal_hash,
            agent_registry_root=agent_registry_root,
        )
        projection_dict = projection.as_dict()
        projection_dict["chain_index"] = index
        projection_dicts.append(projection_dict)
        if not projection.ok:
            errors.extend(f"handoff[{index}]: {error}" for error in projection.errors)
            continue

        previous_subagent = payload.get("previous_subagent")
        if prior_next_agent is not None and previous_subagent != prior_next_agent:
            errors.append(
                f"handoff[{index}].previous_subagent must equal prior next_agent "
                f"{prior_next_agent!r}; got {previous_subagent!r}"
            )
        prior_next_agent = projection.next_agent

        if first_target is None:
            first_target = projection.target
        elif projection.target != first_target:
            errors.append(
                f"handoff[{index}].github target must match first handoff target "
                f"{first_target!r}; got {projection.target!r}"
            )

        goal = payload.get("goal")
        if isinstance(goal, Mapping):
            goal_key = (goal.get("goal_id"), goal.get("goal_version"), goal.get("goal_hash"))
            if first_goal is None:
                first_goal = goal_key
            elif goal_key != first_goal:
                errors.append(
                    f"handoff[{index}].goal must match first handoff goal "
                    f"{first_goal!r}; got {goal_key!r}"
                )

    return AgentHandoffChainResult(
        ok=not errors,
        handoff_count=len(payloads),
        projections=tuple(projection_dicts),
        errors=tuple(errors),
    )


def write_agent_handoff_chain_receipt(
    payloads: list[Mapping[str, Any]],
    receipt_dir: Path,
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffChainResult:
    """Write per-handoff and chain receipts for a dry-run handoff sequence."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    chain = project_agent_handoff_chain(
        payloads,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    artifacts: list[str] = []
    for projection in chain.projections:
        index = int(projection["chain_index"])
        receipt_path = receipt_dir / f"handoff-{index:03d}.receipt.json"
        receipt_path.write_text(
            json.dumps(projection, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifacts.append(str(receipt_path))
    chain_payload = {
        **chain.as_dict(),
        "receipt_dir": str(receipt_dir),
        "artifacts": artifacts,
    }
    (receipt_dir / "chain-receipt.json").write_text(
        json.dumps(chain_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return AgentHandoffChainResult(
        ok=chain.ok,
        handoff_count=chain.handoff_count,
        projections=chain.projections,
        receipt_dir=str(receipt_dir),
        artifacts=tuple(artifacts),
        errors=chain.errors,
    )


def run_agent_handoff_loop(
    start_payload: Mapping[str, Any],
    response_by_agent: Mapping[str, Mapping[str, Any]],
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
    max_steps: int = 5,
) -> AgentHandoffLoopResult:
    """Follow next_agent routes through supplied handoff responses without running agents."""

    if max_steps < 1:
        return AgentHandoffLoopResult(
            ok=False,
            status="BLOCKED",
            stop_reason="invalid_max_steps",
            errors=("max_steps must be at least 1",),
        )

    errors: list[str] = []
    projections: list[dict[str, Any]] = []
    current_payload = start_payload
    prior_next_agent: str | None = None
    first_target: dict[str, Any] | None = None
    first_goal: tuple[Any, Any, Any] | None = None

    for step in range(1, max_steps + 1):
        projection = project_agent_handoff(
            current_payload,
            active_goal_hash=active_goal_hash,
            agent_registry_root=agent_registry_root,
        )
        projection_dict = projection.as_dict()
        projection_dict["loop_step"] = step
        projections.append(projection_dict)
        if not projection.ok:
            errors.extend(f"step[{step}]: {error}" for error in projection.errors)
            return AgentHandoffLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=projection.next_agent,
                stop_reason="invalid_handoff",
                projections=tuple(projections),
                errors=tuple(errors),
            )

        previous_subagent = current_payload.get("previous_subagent")
        if prior_next_agent is not None and previous_subagent != prior_next_agent:
            errors.append(
                f"step[{step}].previous_subagent must equal prior next_agent "
                f"{prior_next_agent!r}; got {previous_subagent!r}"
            )
            return AgentHandoffLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=projection.next_agent,
                stop_reason="route_discontinuity",
                projections=tuple(projections),
                errors=tuple(errors),
            )

        target_error = _same_target_or_error(step, projection.target, first_target)
        if target_error:
            errors.append(target_error)
            return AgentHandoffLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=projection.next_agent,
                stop_reason="target_changed",
                projections=tuple(projections),
                errors=tuple(errors),
            )
        first_target = first_target or projection.target

        goal_error, first_goal = _same_goal_or_error(step, current_payload.get("goal"), first_goal)
        if goal_error:
            errors.append(goal_error)
            return AgentHandoffLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=projection.next_agent,
                stop_reason="goal_changed",
                projections=tuple(projections),
                errors=tuple(errors),
            )

        next_agent = projection.next_agent
        if next_agent == "human":
            return AgentHandoffLoopResult(
                ok=True,
                status="WAITING",
                step_count=step,
                terminal_agent=next_agent,
                stop_reason="next_agent_is_human",
                projections=tuple(projections),
            )
        next_payload = response_by_agent.get(str(next_agent))
        if next_payload is None:
            return AgentHandoffLoopResult(
                ok=True,
                status="WAITING",
                step_count=step,
                terminal_agent=next_agent,
                stop_reason="missing_agent_response",
                projections=tuple(projections),
            )
        prior_next_agent = next_agent
        current_payload = next_payload

    return AgentHandoffLoopResult(
        ok=False,
        status="BLOCKED",
        step_count=max_steps,
        terminal_agent=prior_next_agent,
        stop_reason="max_steps_exhausted",
        projections=tuple(projections),
        errors=(f"handoff loop exceeded max_steps={max_steps}",),
    )


def write_agent_handoff_loop_receipt(
    start_payload: Mapping[str, Any],
    response_by_agent: Mapping[str, Mapping[str, Any]],
    receipt_dir: Path,
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
    max_steps: int = 5,
) -> AgentHandoffLoopResult:
    """Write step and loop receipts for a dry-run handoff loop."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    loop = run_agent_handoff_loop(
        start_payload,
        response_by_agent,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
        max_steps=max_steps,
    )
    artifacts: list[str] = []
    for projection in loop.projections:
        step = int(projection["loop_step"])
        receipt_path = receipt_dir / f"loop-step-{step:03d}.receipt.json"
        receipt_path.write_text(
            json.dumps(projection, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifacts.append(str(receipt_path))
    loop_payload = {
        **loop.as_dict(),
        "receipt_dir": str(receipt_dir),
        "artifacts": artifacts,
    }
    (receipt_dir / "loop-receipt.json").write_text(
        json.dumps(loop_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return AgentHandoffLoopResult(
        ok=loop.ok,
        status=loop.status,
        step_count=loop.step_count,
        terminal_agent=loop.terminal_agent,
        stop_reason=loop.stop_reason,
        projections=loop.projections,
        receipt_dir=str(receipt_dir),
        artifacts=tuple(artifacts),
        errors=loop.errors,
    )


def _same_target_or_error(
    index: int,
    target: dict[str, Any] | None,
    first_target: dict[str, Any] | None,
) -> str | None:
    if first_target is None or target == first_target:
        return None
    return (
        f"step[{index}].github target must match first handoff target "
        f"{first_target!r}; got {target!r}"
    )


def _same_goal_or_error(
    index: int,
    goal: object,
    first_goal: tuple[Any, Any, Any] | None,
) -> tuple[str | None, tuple[Any, Any, Any] | None]:
    if not isinstance(goal, Mapping):
        return None, first_goal
    goal_key = (goal.get("goal_id"), goal.get("goal_version"), goal.get("goal_hash"))
    if first_goal is None or goal_key == first_goal:
        return None, goal_key if first_goal is None else first_goal
    return (
        f"step[{index}].goal must match first handoff goal {first_goal!r}; got {goal_key!r}",
        first_goal,
    )


def _routable_agents(agent_registry_root: Path | None, errors: list[str]) -> frozenset[str]:
    if agent_registry_root is None:
        return ROUTABLE_AGENTS
    try:
        registry_agents = load_agent_registry_ids(agent_registry_root)
    except ValueError as exc:
        errors.append(str(exc))
        return ROUTABLE_AGENTS
    return ROUTABLE_AGENTS | registry_agents


def _read_agents_frontmatter(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        key, separator, value = stripped.partition(":")
        if separator:
            metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def _mapping_field(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        errors.append(f"{label}.{field} must be an object")
        return {}
    return value


def _require_fields(
    payload: Mapping[str, Any],
    fields: tuple[str, ...],
    label: str,
    errors: list[str],
) -> None:
    for field in fields:
        if field not in payload:
            errors.append(f"{label}.{field} is required")


def _non_empty_string(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> str | None:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string")
        return None
    return value


def _optional_string(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string when present")
        return None
    return value


def _require_string_list(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list):
        errors.append(f"{label}.{field} must be a list")
        return []
    strings = [item for item in value if isinstance(item, str) and item.strip()]
    if len(strings) != len(value):
        errors.append(f"{label}.{field} must contain only non-empty strings")
    return strings
