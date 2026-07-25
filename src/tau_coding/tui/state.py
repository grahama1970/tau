"""Display state for Tau's Textual TUI."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from json import dumps
from pathlib import Path

from tau_agent.messages import AgentMessage
from tau_agent.session.entries import CustomEntry
from tau_agent.tools import AgentToolResult, ToolCall
from tau_agent.types import JSONValue
from tau_coding.skills import Skill, parse_skill_invocation
from tau_coding.tui.config import TranscriptRole

ChatItemRole = TranscriptRole
TOOL_RESULT_PREVIEW_LINES = 8
TOOL_PATCH_PREVIEW_LINES = 32
TOOL_RESULT_PREVIEW_CHARS = 2_000
TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES = 120
ASSISTANT_LENGTH_STOP_ERROR_TEXT = (
    "Error: Model stopped because it reached the maximum output token limit. "
    "The response may be incomplete."
)
DEFAULT_THINKING_PLACEHOLDER_TEXT = "Thinking… Press Ctrl+T to show thinking tokens."
COMPACT_RESOURCE_FILE_NAMES = frozenset({"AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"})
MEMORY_PIPELINE_STAGE_LABELS = {
    "intent": "Getting Intent...",
    "get_intent": "Getting Intent...",
    "extract_entities": "Extracting Entities...",
    "entities": "Extracting Entities...",
    "recall": "Accessing Memory...",
    "memory": "Accessing Memory...",
    "access_memory": "Accessing Memory...",
    "evidence_case": "Creating Evidence Case...",
    "create_evidence_case": "Creating Evidence Case...",
    "brave_search": "Searching Web...",
    "search": "Searching Web...",
    "research": "Searching Web...",
    "figure": "Creating Figure...",
    "create_figure": "Creating Figure...",
    "personaplex": "Preparing Persona Voice...",
    "persona_voice": "Preparing Persona Voice...",
    "answer": "Answering...",
    "clarify": "Clarifying...",
    "deflect": "Deflecting...",
}


@dataclass(frozen=True, slots=True)
class LoopMonitorStatus:
    """Visible status for a Loop2/Tau harness monitor envelope."""

    label: str
    run_id: str = ""
    stream_status: str = ""
    event_count: int | None = None
    last_event_type: str = ""
    receipt_status: str = ""
    proof_scope: str = ""
    mocked: bool | None = None
    live: bool | None = None
    does_not_prove: tuple[str, ...] = ()
    source: str = ""


@dataclass(frozen=True, slots=True)
class ToolImagePayload:
    """Image metadata returned by a tool result for TUI transcript rendering."""

    path: str
    mime_type: str
    bytes: int
    image_base64: str


@dataclass(slots=True)
class ChatItem:
    """One rendered item in the TUI transcript."""

    role: ChatItemRole
    text: str
    tool_call_id: str | None = None
    tool_result_text: str | None = None
    tool_image: ToolImagePayload | None = None
    always_show_tool_result: bool = False


@dataclass(slots=True)
class TuiState:
    """Mutable display state for the interactive TUI."""

    items: list[ChatItem] = field(default_factory=list)
    assistant_buffer: str = ""
    running: bool = False
    error: str | None = None
    show_tool_results: bool = False
    show_thinking: bool = False
    thinking_status: str | None = None
    loop_monitor_status: LoopMonitorStatus | None = None
    queued_steering: tuple[str, ...] = ()
    queued_follow_up: tuple[str, ...] = ()
    skills: tuple[Skill, ...] = ()

    def add_item(
        self,
        role: ChatItemRole,
        text: str,
        *,
        tool_call_id: str | None = None,
        tool_result_text: str | None = None,
        tool_image: ToolImagePayload | None = None,
        always_show_tool_result: bool = False,
    ) -> None:
        """Append a transcript item."""
        self.items.append(
            ChatItem(
                role=role,
                text=text,
                tool_call_id=tool_call_id,
                tool_result_text=tool_result_text,
                tool_image=tool_image,
                always_show_tool_result=always_show_tool_result,
            )
        )

    def add_tool_call(self, tool_call: ToolCall) -> None:
        """Append a collapsed tool-call item."""
        skill_name = self._read_skill_name(tool_call)
        if skill_name is not None:
            self.add_item(
                "skill",
                f"Loading skill: {skill_name}",
                tool_call_id=tool_call.id,
            )
            return
        self.add_item(
            "tool",
            format_tool_call_block(tool_call),
            tool_call_id=tool_call.id,
        )

    def add_user_message(self, content: str) -> None:
        """Append a user-authored message, compacting skill and summary messages."""
        branch_summary = _parse_branch_summary_message(content)
        if branch_summary is not None:
            self.add_item(
                "branch_summary",
                "Branch summary (Ctrl+O to expand)",
                tool_result_text=branch_summary,
            )
            return

        compaction_summary = _parse_compaction_summary_message(content)
        if compaction_summary is not None:
            self.add_item(
                "compaction_summary",
                "Compaction summary (Ctrl+O to expand)",
                tool_result_text=compaction_summary,
            )
            return

        skill_invocation = parse_skill_invocation(content)
        if skill_invocation is None:
            self.add_item("user", content)
            return
        self.add_item(
            "skill",
            f"Using skill: {skill_invocation.name} (Ctrl+O to expand)",
            tool_result_text=f"**{skill_invocation.name}**\n\n{skill_invocation.content}",
        )
        if skill_invocation.additional_instructions:
            self.add_item("user", skill_invocation.additional_instructions)

    def add_thinking_delta(self, delta: str) -> None:
        """Append a thinking/reasoning fragment to the current thinking block."""
        if self.items and self.items[-1].role == "thinking":
            self.items[-1].text += delta
            return
        self.add_item("thinking", delta)

    def add_custom_entry(self, entry: CustomEntry) -> None:
        """Append an extension/application-owned session entry to the transcript."""
        payload = dumps(entry.data, indent=2, sort_keys=True)
        self.add_item("status", f"Custom entry: {entry.namespace}\n{payload}")

    def record_tool_result(self, result: AgentToolResult) -> None:
        """Attach a tool result to its matching call, or append an orphan result."""
        result_text = format_tool_result_block(
            name=result.name,
            ok=result.ok,
            content=result.content,
            data=result.data,
        )
        image = _tool_image_payload(result)
        for item in reversed(self.items):
            if item.role in {"tool", "skill"} and item.tool_call_id == result.tool_call_id:
                item.tool_result_text = result_text
                item.tool_image = image
                return
        self.add_item(
            "tool",
            format_tool_result_summary(name=result.name, ok=result.ok),
            tool_call_id=result.tool_call_id,
            tool_result_text=result_text,
            tool_image=image,
        )

    def toggle_tool_results(self) -> bool:
        """Toggle expanded display for tool results and return the new state."""
        self.show_tool_results = not self.show_tool_results
        return self.show_tool_results

    def toggle_thinking(self) -> bool:
        """Toggle thinking-token display and return the new state."""
        self.show_thinking = not self.show_thinking
        return self.show_thinking

    @property
    def thinking_placeholder_text(self) -> str:
        """Return the visible hidden-thinking label for the current run stage."""
        return self.thinking_status or DEFAULT_THINKING_PLACEHOLDER_TEXT

    def set_thinking_status(self, stage: str | None) -> None:
        """Set the hidden-thinking label from a structured pipeline stage."""
        if stage is None:
            self.thinking_status = None
            return
        normalized = stage.strip().lower().replace("-", "_").replace(" ", "_")
        if not normalized:
            self.thinking_status = None
            return
        self.thinking_status = MEMORY_PIPELINE_STAGE_LABELS.get(normalized, stage.strip())

    def set_loop_monitor_status(self, status: LoopMonitorStatus | None) -> None:
        """Replace the visible Loop2/Tau monitor status."""
        self.loop_monitor_status = status

    def set_loop_monitor_status_from_payload(self, payload: JSONValue | None) -> None:
        """Replace loop monitor status from a structured event payload."""
        status = loop_monitor_status_from_payload(payload)
        if status is not None:
            self.loop_monitor_status = status

    def update_queue(self, *, steering: tuple[str, ...], follow_up: tuple[str, ...]) -> None:
        """Replace visible queued-message state."""
        self.queued_steering = steering
        self.queued_follow_up = follow_up

    @property
    def queued_message_count(self) -> int:
        """Return the total number of pending queued messages."""
        return len(self.queued_steering) + len(self.queued_follow_up)

    def clear(self) -> None:
        """Clear visible transcript state without modifying durable session history."""
        self.items.clear()
        self.assistant_buffer = ""
        self.error = None
        self.thinking_status = None
        self.loop_monitor_status = None

    def set_skills(self, skills: Iterable[Skill]) -> None:
        """Replace loaded skill metadata used for presentation-only path matching."""
        self.skills = tuple(skills)

    def load_messages(self, messages: Iterable[AgentMessage]) -> None:
        """Populate the transcript from restored session messages."""
        for message in messages:
            if message.role == "user":
                self.add_user_message(message.content)
            elif message.role == "assistant":
                if message.content:
                    self.add_item("assistant", message.content)
                if message.finish_reason == "length":
                    self.add_item("error", ASSISTANT_LENGTH_STOP_ERROR_TEXT)
                for tool_call in message.tool_calls:
                    self.add_tool_call(tool_call)
            elif message.role == "tool":
                self.record_tool_result(
                    AgentToolResult(
                        tool_call_id=message.tool_call_id,
                        name=message.name,
                        ok=message.ok,
                        content=message.content,
                        data=message.data,
                        details=message.details,
                        error=message.error,
                    )
                )

    def load_custom_entries(self, entries: Iterable[CustomEntry]) -> None:
        """Populate extension/application-owned transcript entries."""
        for entry in entries:
            self.add_custom_entry(entry)

    def _read_skill_name(self, tool_call: ToolCall) -> str | None:
        if tool_call.name != "read":
            return None
        path = _string_argument(tool_call.arguments, "path")
        if path is None:
            return None
        read_path = _normalized_path(path)
        for skill in self.skills:
            if _normalized_path(skill.path) == read_path:
                return skill.name
        return None


def loop_monitor_status_from_payload(payload: JSONValue | None) -> LoopMonitorStatus | None:
    """Parse a structured monitor payload into visible TUI state."""
    if not isinstance(payload, Mapping):
        return None

    label = _string_value(payload.get("label"))
    stream_status = _string_value(payload.get("stream_status"))
    receipt_status = _string_value(payload.get("receipt_status"))
    run_state = _string_value(payload.get("run_state"))
    derived_label = label or stream_status or receipt_status or run_state
    if not derived_label:
        return None

    return LoopMonitorStatus(
        label=derived_label,
        run_id=_string_value(payload.get("run_id")),
        stream_status=stream_status,
        event_count=_int_value(payload.get("event_count")),
        last_event_type=_string_value(payload.get("last_event_type")),
        receipt_status=receipt_status,
        proof_scope=_string_value(payload.get("proof_scope")),
        mocked=_bool_value(payload.get("mocked")),
        live=_bool_value(payload.get("live")),
        does_not_prove=_string_tuple(payload.get("does_not_prove")),
        source=_string_value(payload.get("source")),
    )


def _string_value(value: JSONValue | None) -> str:
    return value if isinstance(value, str) else ""


def _int_value(value: JSONValue | None) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _bool_value(value: JSONValue | None) -> bool | None:
    return value if isinstance(value, bool) else None


def _string_tuple(value: JSONValue | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _parse_branch_summary_message(content: str) -> str | None:
    prefix = (
        "The following is a summary of a branch that this conversation "
        "came back from:\n<summary>\n"
    )
    suffix = "\n</summary>"
    if content.startswith(prefix) and content.endswith(suffix):
        return content.removeprefix(prefix).removesuffix(suffix)
    return None


def _parse_compaction_summary_message(content: str) -> str | None:
    prefix = "Previous conversation summary:\n"
    if content.startswith(prefix):
        return content.removeprefix(prefix)
    return None


def format_tool_call_block(tool_call: ToolCall) -> str:
    """Format a collapsed tool call for live and restored transcript blocks."""
    invocation = format_tool_call_invocation(tool_call)
    if tool_call.name == "bash":
        return invocation
    return f"→ {invocation}"


def format_tool_call_invocation(tool_call: ToolCall) -> str:
    """Format a tool call as a terse human-readable invocation."""
    arguments = tool_call.arguments
    if tool_call.name == "read":
        path = _string_argument(arguments, "path")
        if path is None:
            return _fallback_tool_call_invocation(tool_call)
        classification = _compact_read_classification(path)
        if classification is not None:
            kind, label = classification
            return f"read {kind} {label}{_read_line_suffix(arguments)} (Ctrl+O to expand)"
        return f"read {path}{_read_line_suffix(arguments)}"
    if tool_call.name == "edit":
        path = _string_argument(arguments, "path")
        if path is None:
            return _fallback_tool_call_invocation(tool_call)
        return f"edit {path}"
    if tool_call.name == "write":
        path = _string_argument(arguments, "path")
        if path is None:
            return _fallback_tool_call_invocation(tool_call)
        return f"write {path}"
    if tool_call.name == "bash":
        command = _string_argument(arguments, "command")
        if command is None:
            return _fallback_tool_call_invocation(tool_call)
        timeout = _number_argument(arguments, "timeout")
        suffix = f" (timeout {timeout:g}s)" if timeout is not None else ""
        return f"$ {command}{suffix}"
    return _fallback_tool_call_invocation(tool_call)


def _read_line_suffix(arguments: dict[str, JSONValue]) -> str:
    offset = _int_argument(arguments, "offset")
    limit = _int_argument(arguments, "limit")
    if offset is None and limit is None:
        return ""
    start = 1 if offset is None else max(1, offset)
    if limit is None:
        return f":{start}-"
    return f":{start}-{start + max(1, limit) - 1}"


def _fallback_tool_call_invocation(tool_call: ToolCall) -> str:
    if tool_call.arguments:
        return f"{tool_call.name} {tool_call.arguments}"
    return tool_call.name


def _compact_read_classification(path: str) -> tuple[str, str] | None:
    read_path = _normalized_path(path)
    if read_path.name == "SKILL.md":
        return ("skill", read_path.parent.name or read_path.name)

    docs_label = _tau_docs_label(read_path)
    if docs_label is not None:
        return ("docs", docs_label)

    if read_path.name in COMPACT_RESOURCE_FILE_NAMES:
        return ("resource", _display_path_relative_to_cwd(read_path))

    return None


def _tau_docs_label(read_path: Path) -> str | None:
    try:
        relative = read_path.relative_to(_tau_package_root())
    except ValueError:
        return None
    label = relative.as_posix()
    if label == "README.md" or label.startswith(("docs/", "examples/")):
        return label
    return None


def _tau_package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _display_path_relative_to_cwd(path: Path) -> str:
    cwd = Path.cwd().resolve(strict=False)
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return str(path)


def _string_argument(arguments: dict[str, JSONValue], key: str) -> str | None:
    value = arguments.get(key)
    return value if isinstance(value, str) else None


def _normalized_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _int_argument(arguments: dict[str, JSONValue], key: str) -> int | None:
    value = arguments.get(key)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _number_argument(arguments: dict[str, JSONValue], key: str) -> int | float | None:
    value = arguments.get(key)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int | float) else None


def format_tool_result_summary(*, name: str, ok: bool) -> str:
    """Format a terse tool result line for orphaned results."""
    status = "✓" if ok else "✗"
    return f"{status} {name}"


def format_tool_result_block(
    *,
    name: str,
    ok: bool,
    content: str,
    data: dict[str, JSONValue] | None = None,
) -> str:
    """Format a tool result for live and restored transcript blocks."""
    status = "✓" if ok else "✗"
    lines = [f"{status} {name}"]
    if content:
        lines.append(_preview_text(content, max_lines=TOOL_RESULT_PREVIEW_LINES))
    patch = _result_patch(name=name, ok=ok, data=data)
    if patch:
        lines.extend(["", "Patch:", _preview_text(patch, max_lines=TOOL_PATCH_PREVIEW_LINES)])
    return "\n".join(lines)


def _tool_image_payload(result: AgentToolResult) -> ToolImagePayload | None:
    """Return image metadata for tool results that carry a supported image."""
    data = result.data
    if not result.ok or not isinstance(data, dict):
        return None
    image_base64 = data.get("image_base64")
    mime_type = data.get("mime_type")
    path = data.get("path")
    size = data.get("bytes")
    if (
        isinstance(image_base64, str)
        and isinstance(mime_type, str)
        and isinstance(path, str)
        and isinstance(size, int)
    ):
        return ToolImagePayload(
            path=path,
            mime_type=mime_type,
            bytes=size,
            image_base64=image_base64,
        )
    return None


def format_terminal_command_result_block(
    *,
    ok: bool,
    added_to_context: bool,
    output: str,
) -> str:
    """Format an input-bar terminal command result for visible TUI display."""
    status = "✓" if ok else "✗"
    suffix = " · added to context" if added_to_context else " · not added to context"
    lines = [f"{status} bash{suffix}"]
    if output:
        lines.append(_preview_tail_text(output, max_lines=TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES))
    return "\n".join(lines)


def format_terminal_command_running_block(
    *,
    added_to_context: bool,
    output: str | None = None,
) -> str:
    """Format an input-bar terminal command while it is still running."""
    suffix = " · added to context" if added_to_context else " · not added to context"
    lines = [f"… bash{suffix}", "Running... (Escape to cancel)"]
    if output:
        lines.append(_preview_tail_text(output, max_lines=TERMINAL_COMMAND_OUTPUT_PREVIEW_LINES))
    return "\n".join(lines)


def _result_patch(
    *,
    name: str,
    ok: bool,
    data: dict[str, JSONValue] | None,
) -> str | None:
    if name != "edit" or not ok or data is None:
        return None
    patch = data.get("patch")
    return patch if isinstance(patch, str) and patch.strip() else None


def _preview_text(text: str, *, max_lines: int) -> str:
    lines = text.splitlines()
    if not lines:
        return text[:TOOL_RESULT_PREVIEW_CHARS]

    preview_lines = lines[:max_lines]
    preview = "\n".join(preview_lines)
    hidden_lines = max(0, len(lines) - len(preview_lines))

    truncated_by_chars = len(preview) > TOOL_RESULT_PREVIEW_CHARS
    if truncated_by_chars:
        preview = preview[:TOOL_RESULT_PREVIEW_CHARS].rstrip()

    if hidden_lines or truncated_by_chars:
        details: list[str] = []
        if hidden_lines:
            details.append(f"{hidden_lines} more line{'s' if hidden_lines != 1 else ''}")
        if truncated_by_chars:
            details.append("additional text")
        preview = f"{preview}\n\n[Preview only: {', '.join(details)} hidden from the TUI.]"
    return preview


def _preview_tail_text(text: str, *, max_lines: int) -> str:
    lines = text.splitlines()
    if not lines:
        return text[:TOOL_RESULT_PREVIEW_CHARS]

    preview_lines = lines[-max_lines:]
    preview = "\n".join(preview_lines)
    hidden_lines = max(0, len(lines) - len(preview_lines))

    truncated_by_chars = len(preview) > TOOL_RESULT_PREVIEW_CHARS
    if truncated_by_chars:
        preview = preview[-TOOL_RESULT_PREVIEW_CHARS:].lstrip()

    if hidden_lines or truncated_by_chars:
        details: list[str] = []
        if hidden_lines:
            details.append(
                f"{hidden_lines} earlier line{'s' if hidden_lines != 1 else ''}"
            )
        if truncated_by_chars:
            details.append("earlier text")
        preview = f"[Preview only: {', '.join(details)} hidden from the TUI.]\n\n{preview}"
    return preview
