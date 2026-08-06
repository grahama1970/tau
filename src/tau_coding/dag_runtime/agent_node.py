"""Tau-native agent-node execution contract (tau#310).

Binds the provider-neutral ``tau_agent`` loop into versioned Tau contracts:
``tau.agent_node.v1`` work orders, per-turn ``tau.agent_turn_receipt.v1``
receipts, hash-chained ``tau.agent_event.v1`` journal entries,
policy-validated ``tau.tool_request.v1`` / ``tau.tool_effect_receipt.v1``
tool effects, and fail-closed ``tau.agent_node_settlement.v1``.

Authority boundaries (tau#310):
- Tau owns context, tool policy, tool execution, the turn loop, retries,
  evidence validation, and settlement.
- SciLLM (or any provider transport) only moves model turns. A provider or
  transport reporting ``completed`` never settles a Tau node; a model saying
  "done", reasoning-only output, empty terminal text, or an active pending
  tool cannot become Tau success.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tau_agent import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    ErrorEvent,
    SimpleCancellationToken,
    ToolExecutionEndEvent,
    TurnEndEvent,
    UserMessage,
)
from tau_agent.loop import run_agent_loop
from tau_coding.dag_runtime.model import canonical_sha256

AGENT_NODE_SCHEMA = "tau.agent_node.v1"
AGENT_TURN_SCHEMA = "tau.agent_turn.v1"
AGENT_EVENT_SCHEMA = "tau.agent_event.v1"
TOOL_REQUEST_SCHEMA = "tau.tool_request.v1"
TOOL_EFFECT_RECEIPT_SCHEMA = "tau.tool_effect_receipt.v1"
AGENT_TURN_RECEIPT_SCHEMA = "tau.agent_turn_receipt.v1"
AGENT_NODE_SETTLEMENT_SCHEMA = "tau.agent_node_settlement.v1"

TAU_NATIVE_HARNESS_MODE = "tau_native_agent_loop"
OPAQUE_COMPAT_HARNESS_MODE = "opaque_agent_compat"
HARNESS_MODES = (TAU_NATIVE_HARNESS_MODE, OPAQUE_COMPAT_HARNESS_MODE)

TERMINAL_STATES = ("completed", "failed", "cancelled", "blocked")


class AgentNodeError(RuntimeError):
    """Fail-closed agent-node error with a stable verdict code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Tau-owned policy applied to every requested tool effect."""

    goal_hash: str
    allowed_tools: tuple[str, ...]
    allowed_paths: tuple[str, ...] = ("**",)
    max_tool_calls: int = 32
    approvals_granted: tuple[str, ...] = ()
    approval_required_tools: tuple[str, ...] = ()

    def rejection_codes(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        goal_hash: str,
        tool_calls_so_far: int,
        input_schema: Mapping[str, Any] | None,
    ) -> list[str]:
        codes: list[str] = []
        if goal_hash != self.goal_hash:
            codes.append("TOOL_GOAL_HASH_STALE")
        if tool_name not in self.allowed_tools:
            codes.append("TOOL_NOT_ALLOWED")
        if tool_calls_so_far >= self.max_tool_calls:
            codes.append("TOOL_BUDGET_EXHAUSTED")
        if tool_name in self.approval_required_tools and tool_name not in self.approvals_granted:
            codes.append("TOOL_APPROVAL_MISSING")
        path = arguments.get("path")
        if isinstance(path, str) and path:
            escapes = path.startswith("/") or ".." in path.split("/")
            allowed = any(fnmatch.fnmatch(path, pattern) for pattern in self.allowed_paths)
            if escapes or not allowed:
                codes.append("TOOL_PATH_FORBIDDEN")
        codes.extend(_schema_violations(arguments, input_schema))
        return codes


def _schema_violations(
    arguments: Mapping[str, Any], input_schema: Mapping[str, Any] | None
) -> list[str]:
    if not isinstance(arguments, Mapping):
        return ["TOOL_ARGS_MALFORMED"]
    if not input_schema:
        return []
    properties = input_schema.get("properties")
    required = input_schema.get("required", [])
    codes: list[str] = []
    for key in required if isinstance(required, list) else []:
        if key not in arguments:
            codes.append("TOOL_ARGS_MALFORMED")
            break
    if isinstance(properties, Mapping):
        type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
        for key, spec in properties.items():
            if key not in arguments or not isinstance(spec, Mapping):
                continue
            expected = type_map.get(spec.get("type"))
            wrong_type = expected is not None and not isinstance(arguments[key], expected)
            if wrong_type and "TOOL_ARGS_MALFORMED" not in codes:
                codes.append("TOOL_ARGS_MALFORMED")
    return codes


def tool_request_idempotency_sha256(request: Mapping[str, Any]) -> str:
    """Semantic identity of a tool effect — stable across attempts/retries."""
    return canonical_sha256(
        {
            "run_id": request.get("run_id"),
            "node_id": request.get("node_id"),
            "tool_name": request.get("tool_name"),
            "arguments": request.get("arguments"),
        }
    )


@dataclass(slots=True)
class AgentNodeJournal:
    """Hash-chained, sequence-numbered agent-event journal for one attempt.

    ``sink`` (optional) receives every appended entry synchronously — the
    durability hook for the canonical run store (tau#313). A sink failure
    fails the append: an event that cannot be persisted must not be acted on.
    """

    run_id: str
    node_id: str
    attempt: int
    entries: list[dict[str, Any]] = field(default_factory=list)
    sink: Any | None = None

    def append(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        prev_sha = self.entries[-1]["sha256"] if self.entries else ""
        body = {
            "schema": AGENT_EVENT_SCHEMA,
            "seq": len(self.entries) + 1,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt": self.attempt,
            "event_type": event_type,
            "payload": dict(payload),
            "prev_sha256": prev_sha,
        }
        entry = {**body, "sha256": canonical_sha256(body)}
        if self.sink is not None:
            self.sink(entry)
        self.entries.append(entry)
        return entry

    def verify_chain(self) -> None:
        prev_sha = ""
        for index, entry in enumerate(self.entries, start=1):
            body = {key: value for key, value in entry.items() if key != "sha256"}
            if entry["seq"] != index or entry["prev_sha256"] != prev_sha:
                raise AgentNodeError("agent_journal_chain_broken", f"seq={entry['seq']}")
            if canonical_sha256(body) != entry["sha256"]:
                raise AgentNodeError("agent_journal_hash_mismatch", f"seq={entry['seq']}")
            prev_sha = entry["sha256"]


def validate_agent_node_work_order(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ``tau.agent_node.v1`` work order before dispatch."""
    if payload.get("schema") != AGENT_NODE_SCHEMA:
        raise AgentNodeError("agent_node_schema_invalid", str(payload.get("schema")))
    for key in ("run_id", "node_id", "attempt_id", "goal_hash", "plan_sha256", "model"):
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise AgentNodeError("agent_node_field_missing", key)
    harness = payload.get("harness", "tau_native_agent_loop")
    if harness not in HARNESS_MODES:
        raise AgentNodeError("agent_node_harness_invalid", str(harness))
    attempt = payload.get("attempt")
    if type(attempt) is not int or attempt < 1:
        raise AgentNodeError("agent_node_field_missing", "attempt")
    selection = payload.get("transport_profile_selection")
    if selection is not None and not isinstance(selection, Mapping):
        raise AgentNodeError("agent_node_selection_invalid")
    required_evidence = payload.get("required_evidence", [])
    if not isinstance(required_evidence, list):
        raise AgentNodeError("agent_node_required_evidence_invalid")
    return dict(payload)


@dataclass(slots=True)
class AgentNodeRun:
    """Executes one Tau-native agent-node attempt and records receipts."""

    work_order: dict[str, Any]
    policy: ToolPolicy
    provider: Any
    tools: list[AgentTool]
    system: str = "You are a Tau agent node."
    max_turns: int = 8
    event_sink: Any | None = None
    prior_tool_effects: dict[str, dict[str, Any]] = field(default_factory=dict)
    journal: AgentNodeJournal = field(init=False)
    turn_receipts: list[dict[str, Any]] = field(default_factory=list)
    tool_effect_receipts: list[dict[str, Any]] = field(default_factory=list)
    rejected_tool_requests: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    cancellation: SimpleCancellationToken = field(default_factory=SimpleCancellationToken)
    steering_queue: list[str] = field(default_factory=list)
    _tool_calls_admitted: int = 0
    _pending_tool_calls: int = 0

    def __post_init__(self) -> None:
        self.work_order = validate_agent_node_work_order(self.work_order)
        self.journal = AgentNodeJournal(
            run_id=self.work_order["run_id"],
            node_id=self.work_order["node_id"],
            attempt=self.work_order["attempt"],
            sink=self.event_sink,
        )
        if self.policy.goal_hash != self.work_order["goal_hash"]:
            raise AgentNodeError("agent_node_goal_policy_mismatch")

    def add_evidence(self, kind: str, payload: Mapping[str, Any]) -> None:
        receipt = {
            "kind": kind,
            "payload": dict(payload),
            "sha256": canonical_sha256(dict(payload)),
        }
        self.evidence[kind] = receipt
        self.journal.append("evidence_recorded", receipt)

    async def run(self, prompt: str) -> None:
        prompt_sha = canonical_sha256({"p": prompt})
        self.journal.append("agent_node_started", {"prompt_sha256": prompt_sha})
        messages: list[Any] = [UserMessage(content=prompt)]
        wrapped = [self._wrap_tool(tool) for tool in self.tools]
        current_turn: dict[str, Any] = {}

        def _steering() -> list[UserMessage]:
            queued = [UserMessage(content=text) for text in self.steering_queue]
            self.steering_queue.clear()
            return queued

        async for event in run_agent_loop(
            provider=self.provider,
            model=self.work_order["model"],
            system=self.system,
            messages=messages,
            tools=wrapped,
            max_turns=self.max_turns,
            signal=self.cancellation,
            get_steering_messages=_steering,
        ):
            if isinstance(event, TurnEndEvent):
                self._record_turn(event.turn, messages, current_turn)
                current_turn = {}
            elif isinstance(event, ToolExecutionEndEvent):
                current_turn.setdefault("tool_executions", 0)
                current_turn["tool_executions"] += 1
            elif isinstance(event, ErrorEvent):
                self.journal.append(
                    "agent_error", {"message": event.message, "recoverable": event.recoverable}
                )
        self.journal.append("agent_loop_finished", {"turns": len(self.turn_receipts)})

    def _wrap_tool(self, tool: AgentTool) -> AgentTool:
        async def _executor(arguments: Mapping[str, Any], signal: Any = None) -> AgentToolResult:
            args = dict(arguments) if isinstance(arguments, Mapping) else {}
            request = {
                "schema": TOOL_REQUEST_SCHEMA,
                "run_id": self.work_order["run_id"],
                "node_id": self.work_order["node_id"],
                "attempt": self.work_order["attempt"],
                "goal_hash": self.work_order["goal_hash"],
                "tool_name": tool.name,
                "request_index": self._tool_calls_admitted + len(self.rejected_tool_requests) + 1,
                "arguments": args,
            }
            codes = self.policy.rejection_codes(
                tool_name=tool.name,
                arguments=args,
                goal_hash=self.work_order["goal_hash"],
                tool_calls_so_far=self._tool_calls_admitted,
                input_schema=tool.input_schema,
            )
            if codes:
                rejection = {**request, "rejection_codes": codes}
                self.rejected_tool_requests.append(rejection)
                self.journal.append("tool_request_rejected", rejection)
                return AgentToolResult(
                    tool_call_id="",
                    name=tool.name,
                    ok=False,
                    content=f"tool request rejected: {','.join(codes)}",
                    error=",".join(codes),
                )
            idempotency = tool_request_idempotency_sha256(request)
            prior = self.prior_tool_effects.get(idempotency)
            if prior is not None:
                # Effect already admitted in a prior attempt/process: replay
                # the receipt, never re-execute the side effect (tau#313).
                self.journal.append(
                    "tool_effect_replayed",
                    {"idempotency_sha256": idempotency, "sha256": prior["sha256"]},
                )
                content = str(
                    prior.get("effect_content")
                    or f"replayed prior tool effect {prior['sha256']}"
                )
                return AgentToolResult(
                    tool_call_id="",
                    name=tool.name,
                    ok=True,
                    content=content,
                    data={"receipt_sha256": prior["sha256"], "replayed": True},
                )
            self._tool_calls_admitted += 1
            self._pending_tool_calls += 1
            self.journal.append("tool_request_admitted", request)
            try:
                result = await tool.execute(args, signal=signal)
            except Exception as error:  # noqa: BLE001 - effect receipts must record failures
                self._pending_tool_calls -= 1
                self._effect_receipt(request, error=str(error))
                return AgentToolResult(
                    tool_call_id="",
                    name=tool.name,
                    ok=False,
                    content=f"tool failed: {error}",
                    error=str(error),
                )
            self._pending_tool_calls -= 1
            if not result.ok:
                self._effect_receipt(request, error=result.error or result.content)
                return result
            receipt = self._effect_receipt(request, content=result.content)
            data = dict(result.data or {})
            data["receipt_sha256"] = receipt["sha256"]
            return result.model_copy(update={"data": data})

        return AgentTool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            executor=_executor,
        )

    def _effect_receipt(
        self, request: Mapping[str, Any], *, content: str | None = None, error: str | None = None
    ) -> dict[str, Any]:
        body = {
            "schema": TOOL_EFFECT_RECEIPT_SCHEMA,
            "tool_request": dict(request),
            "tool_request_sha256": canonical_sha256(dict(request)),
            "idempotency_sha256": tool_request_idempotency_sha256(request),
            "ok": error is None,
            "effect_sha256": canonical_sha256({"content": content, "error": error}),
            # Bounded effect content so a crash-resumed attempt can replay the
            # admitted effect to the model without re-executing it (tau#313).
            "effect_content": (content or "")[:20_000] if error is None else None,
            "error": error,
        }
        receipt = {**body, "sha256": canonical_sha256(body)}
        self.tool_effect_receipts.append(receipt)
        self.journal.append("tool_effect_recorded", receipt)
        return receipt

    def _record_turn(
        self, turn: int, messages: Sequence[Any], turn_state: Mapping[str, Any]
    ) -> None:
        assistant = next(
            (m for m in reversed(messages) if isinstance(m, AssistantMessage)), None
        )
        body = {
            "schema": AGENT_TURN_RECEIPT_SCHEMA,
            "run_id": self.work_order["run_id"],
            "node_id": self.work_order["node_id"],
            "attempt": self.work_order["attempt"],
            "goal_hash": self.work_order["goal_hash"],
            "turn": turn,
            "assistant_text": assistant.content if assistant else "",
            "tool_calls": [
                {"id": call.id, "name": call.name}
                for call in (assistant.tool_calls if assistant else [])
            ],
            "tool_executions": int(turn_state.get("tool_executions", 0)),
            "finish_reason": getattr(assistant, "finish_reason", None),
        }
        receipt = {**body, "sha256": canonical_sha256(body)}
        self.turn_receipts.append(receipt)
        self.journal.append("agent_turn_recorded", {"turn": turn, "sha256": receipt["sha256"]})

    def cancel(self, reason: str) -> None:
        self.cancellation.cancel()
        self.journal.append("agent_cancelled", {"reason": reason})

    def steer(self, instruction: str) -> None:
        self.steering_queue.append(instruction)
        self.journal.append("steering_queued", {"instruction": instruction})

    def settle(self) -> dict[str, Any]:
        """Fail-closed settlement. Provider/model claims of completion are ignored."""
        self.journal.verify_chain()
        required = [
            item
            for item in self.work_order.get("required_evidence", [])
            if isinstance(item, str)
        ]
        missing_evidence = sorted(set(required) - set(self.evidence))
        final_text = self.turn_receipts[-1]["assistant_text"].strip() if self.turn_receipts else ""
        blockers: list[str] = []
        if self.cancellation.is_cancelled():
            state = "cancelled"
            blockers.append("cancelled")
        else:
            if self._pending_tool_calls > 0:
                blockers.append("pending_tool_call")
            if not self.turn_receipts:
                blockers.append("no_accepted_turns")
            if not final_text:
                blockers.append("empty_terminal_output")
            if missing_evidence:
                blockers.append("required_evidence_missing:" + "|".join(missing_evidence))
            if any(
                "TOOL_APPROVAL_MISSING" in item["rejection_codes"]
                for item in self.rejected_tool_requests
            ):
                state = "blocked"
                blockers.append("approval_required")
            elif blockers:
                state = "failed"
            else:
                state = "completed"
        body = {
            "schema": AGENT_NODE_SETTLEMENT_SCHEMA,
            "run_id": self.work_order["run_id"],
            "node_id": self.work_order["node_id"],
            "attempt_id": self.work_order["attempt_id"],
            "attempt": self.work_order["attempt"],
            "goal_hash": self.work_order["goal_hash"],
            "plan_sha256": self.work_order["plan_sha256"],
            "harness": self.work_order.get("harness", "tau_native_agent_loop"),
            "state": state,
            "blockers": blockers,
            "turns": len(self.turn_receipts),
            "turn_receipt_sha256s": [item["sha256"] for item in self.turn_receipts],
            "tool_effect_receipt_sha256s": [item["sha256"] for item in self.tool_effect_receipts],
            "rejected_tool_requests": len(self.rejected_tool_requests),
            "evidence": {kind: item["sha256"] for kind, item in self.evidence.items()},
            "journal_head_sha256": (
                self.journal.entries[-1]["sha256"] if self.journal.entries else ""
            ),
            "grounding": getattr(self.provider, "last_grounding", None),
            "journal_length": len(self.journal.entries),
            "proof_boundary": {
                "provider_completion_is_not_settlement": True,
                "model_done_claim_is_not_settlement": True,
                "settlement_requires_required_evidence": True,
            },
        }
        settlement = {**body, "sha256": canonical_sha256(body)}
        self.journal.append("agent_node_settled", {"state": state, "sha256": settlement["sha256"]})
        return settlement


def reconstruct_context_from_journal(
    journal: AgentNodeJournal, turn_receipts: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Rebuild accepted context for a model switch from the Tau journal.

    Uses only Tau-accepted turn receipts — never an opaque provider transcript.
    """
    journal.verify_chain()
    accepted_turn_hashes = {
        entry["payload"]["sha256"]
        for entry in journal.entries
        if entry["event_type"] == "agent_turn_recorded"
    }
    context: list[dict[str, Any]] = []
    for receipt in turn_receipts:
        if receipt.get("sha256") not in accepted_turn_hashes:
            raise AgentNodeError("context_turn_not_in_journal", str(receipt.get("turn")))
        context.append(
            {"turn": receipt["turn"], "assistant_text": receipt["assistant_text"]}
        )
    return context
