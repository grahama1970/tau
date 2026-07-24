"""Minimal Textual app for Tau coding sessions."""

import asyncio
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from inspect import isawaitable
from io import StringIO
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar, Literal, Protocol, cast

from rich.console import Console, Group
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key, Resize
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)
from textual.worker import Worker

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
    RetryEvent,
    ThinkingDeltaEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_agent.messages import AgentMessage
from tau_agent.tools import AgentTool
from tau_ai import ProviderErrorEvent, ProviderEvent
from tau_ai.provider import CancellationToken
from tau_coding.commands import CommandRegistry, CommandResult, create_default_command_registry
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.oauth import OAuthAuthInfo, OAuthPrompt, login_openai_codex
from tau_coding.provider_catalog import (
    BUILTIN_PROVIDER_CATALOG,
    ProviderCatalogEntry,
    builtin_provider_entry,
)
from tau_coding.provider_config import (
    ProviderConfig,
    ProviderSelection,
    load_provider_settings,
    provider_config_from_catalog_entry,
    provider_has_usable_credentials,
    resolve_provider_selection,
    upsert_saved_provider,
)
from tau_coding.provider_runtime import create_model_provider
from tau_coding.session import (
    CodingSession,
    CodingSessionConfig,
    ModelChoice,
    SessionTreeBranchResult,
    SessionTreeChoice,
    jsonl_session_storage,
    parse_terminal_command,
)
from tau_coding.session_manager import CodingSessionRecord, SessionManager
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.thinking import DEFAULT_THINKING_LEVEL
from tau_coding.tui.adapter import TuiEventAdapter
from tau_coding.tui.autocomplete import (
    CompletionItem,
    CompletionOption,
    CompletionState,
    build_completion_state,
)
from tau_coding.tui.config import (
    BUILTIN_TUI_THEME_NAMES,
    TAU_DARK_THEME,
    TuiKeybindings,
    TuiSettings,
    TuiTheme,
    TuiThemeName,
    load_tui_settings,
    save_tui_settings,
)
from tau_coding.tui.pty_proof import pty_input_received_line, pty_ready_line
from tau_coding.tui.state import TuiState, format_terminal_command_result_block
from tau_coding.tui.widgets import (
    CompactSessionInfo,
    SessionSidebar,
    TranscriptView,
    render_completion_suggestions,
)
from tau_coding.trust import ProjectTrustOption, ProjectTrustState, ProjectTrustStoreEntry

type BindingEntry = Binding | tuple[str, str] | tuple[str, str, str]
type SessionPickerSortMode = Literal["threaded", "recent", "relevance", "name"]
SIDEBAR_MIN_WIDTH = 96
SIDEBAR_MIN_HEIGHT = 24
ACTIVITY_TICK_SECONDS = 0.15
ACTIVITY_COLOR_FADE_STEPS = 24
ACTIVITY_INDICATOR_HEIGHT = 3
COMPLETION_MAX_VISIBLE_LINES = 16
NO_STORED_CREDENTIALS_MESSAGE = (
    "No stored credentials to remove. /logout only removes credentials saved by /login; "
    "environment variables and providers.json config are unchanged."
)


class LoginRequiredProvider:
    """Placeholder provider used so the TUI can open before login."""

    def __init__(self, message: str) -> None:
        self.message = message

    async def aclose(self) -> None:
        """Close provider resources."""

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Surface a login-needed provider error."""
        del model, system, messages, tools, signal

        async def iterator() -> AsyncIterator[ProviderEvent]:
            yield ProviderErrorEvent(message=self.message)

        return iterator()


class CompletionActionTarget(Protocol):
    """App actions used by the prompt input completion bindings."""

    def action_accept_completion(self) -> None: ...

    def action_cancel(self) -> None: ...

    def action_completion_next(self) -> None: ...

    def action_completion_previous(self) -> None: ...

    def action_open_command_palette(self) -> None: ...

    def action_open_session_picker(self) -> None: ...

    def action_new_session(self) -> None: ...

    def action_open_tree_picker(self) -> None: ...

    def action_open_fork_picker(self) -> None: ...

    def action_cycle_thinking(self) -> None: ...

    def action_cycle_model(self) -> None: ...

    def action_cycle_model_previous(self) -> None: ...

    def action_open_model_picker(self) -> None: ...

    def action_toggle_tool_results(self) -> None: ...

    def action_toggle_thinking(self) -> None: ...

    def action_open_external_editor(self) -> None: ...

    def action_paste_clipboard(self) -> None: ...

    def action_dequeue_messages(self) -> None: ...

    def action_copy_last_message(self) -> None: ...

    def action_edit_queued_follow_up(self) -> bool: ...

    async def action_submit_prompt(self) -> None: ...

    async def action_submit_follow_up(self) -> None: ...


class SessionCompletionRecord(Protocol):
    """Session metadata needed to render resume picker completions."""

    id: str
    title: str | None
    model: str
    cwd: Path
    updated_at: float
    parent_session_id: str | None


class PromptInput(TextArea):
    """Multiline prompt input with completion key bindings."""

    BINDINGS: ClassVar[list[BindingEntry]] = []
    shell_mode_style: str = ""

    def __init__(
        self,
        *,
        tui_keybindings: TuiKeybindings | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.tui_keybindings = tui_keybindings or TuiKeybindings()
        self._base_bindings = self._bindings.copy()
        self._footer_mode: Literal["normal", "completion", "running"] = "normal"
        self._apply_prompt_bindings()

    def set_footer_mode(self, mode: Literal["normal", "completion", "running"]) -> None:
        """Switch the prompt bindings shown by Textual's built-in footer."""
        if mode == self._footer_mode:
            return
        self._footer_mode = mode
        self._apply_prompt_bindings()
        self.refresh_bindings()

    def _apply_prompt_bindings(self) -> None:
        self._bindings = BindingsMap.merge(
            [
                self._base_bindings,
                BindingsMap(_prompt_bindings(self.tui_keybindings, mode=self._footer_mode)),
            ]
        )

    @property
    def value(self) -> str:
        """Compatibility alias for tests and code that previously used Input.value."""
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.text = text

    @property
    def cursor_position(self) -> int:
        """Return a flat cursor offset for Input compatibility."""
        row, column = self.cursor_location
        lines = self.text.split("\n")
        return sum(len(line) + 1 for line in lines[:row]) + column

    @cursor_position.setter
    def cursor_position(self, offset: int) -> None:
        text = self.text
        bounded = max(0, min(offset, len(text)))
        before = text[:bounded]
        self.move_cursor((before.count("\n"), len(before.rsplit("\n", 1)[-1])))

    def action_accept_completion(self) -> None:
        """Accept the selected app-level completion."""
        self._completion_target().action_accept_completion()

    def action_completion_next(self) -> None:
        """Select the next app-level completion or move down in the prompt."""
        if self._has_completion_options():
            self._completion_target().action_completion_next()
        else:
            self.action_cursor_down()

    def action_completion_previous(self) -> None:
        """Select the previous app-level completion or move up in the prompt."""
        if self._has_completion_options():
            self._completion_target().action_completion_previous()
        elif self._completion_target().action_edit_queued_follow_up():
            return
        else:
            self.action_cursor_up()

    def action_cancel(self) -> None:
        """Run the app-level cancel action."""
        self._completion_target().action_cancel()

    def action_open_command_palette(self) -> None:
        """Open the app-level command palette."""
        self._completion_target().action_open_command_palette()

    def action_open_session_picker(self) -> None:
        """Open the app-level session picker."""
        self._completion_target().action_open_session_picker()

    def action_new_session(self) -> None:
        """Start a new app-level session."""
        self._completion_target().action_new_session()

    def action_open_tree_picker(self) -> None:
        """Open the app-level session tree picker."""
        self._completion_target().action_open_tree_picker()

    def action_open_fork_picker(self) -> None:
        """Open the app-level fork picker."""
        self._completion_target().action_open_fork_picker()

    def action_cycle_thinking(self) -> None:
        """Cycle the app-level thinking mode."""
        self._completion_target().action_cycle_thinking()

    def action_cycle_model(self) -> None:
        """Cycle the app-level scoped model."""
        self._completion_target().action_cycle_model()

    def action_cycle_model_previous(self) -> None:
        """Cycle the app-level scoped model backwards."""
        self._completion_target().action_cycle_model_previous()

    def action_open_model_picker(self) -> None:
        """Open the app-level model picker."""
        self._completion_target().action_open_model_picker()

    def action_toggle_tool_results(self) -> None:
        """Toggle app-level tool result display."""
        self._completion_target().action_toggle_tool_results()

    def action_toggle_thinking(self) -> None:
        """Toggle app-level thinking-token display."""
        self._completion_target().action_toggle_thinking()

    def action_open_external_editor(self) -> None:
        """Open the current prompt in the configured external editor."""
        self._completion_target().action_open_external_editor()

    def action_paste_clipboard(self) -> None:
        """Paste system clipboard text into the prompt."""
        self._completion_target().action_paste_clipboard()

    def action_dequeue_messages(self) -> None:
        """Restore queued messages into the prompt."""
        self._completion_target().action_dequeue_messages()

    def action_copy_last_message(self) -> None:
        """Copy the last assistant message."""
        self._completion_target().action_copy_last_message()

    def action_clear_prompt(self) -> None:
        """Clear the current prompt."""
        if self.selected_text:
            return
        if self.text:
            self.text = ""
            self.move_cursor((0, 0))

    def get_line(self, line_index: int) -> Text:
        """Retrieve one prompt line with shell prefixes highlighted."""
        line = super().get_line(line_index)
        if line_index != 0 or not self.shell_mode_style:
            return line
        span = _terminal_command_prefix_span(self.text)
        if span is None:
            return line
        start, end = span
        line.stylize(self.shell_mode_style, start, end)
        return line

    async def action_submit_follow_up(self) -> None:
        """Submit the prompt as an app-level follow-up."""
        await self._completion_target().action_submit_follow_up()

    async def action_submit_prompt(self) -> None:
        """Submit the prompt through the app-level action."""
        await self._completion_target().action_submit_prompt()

    def action_insert_newline(self) -> None:
        """Insert a newline in the prompt."""
        self.insert("\n")

    async def action_quit(self) -> None:
        """Quit the app through the app-level action."""
        await self.app.action_quit()

    def action_scroll_down(self) -> None:
        """Use down arrow for completion selection while focused."""
        self.action_completion_next()

    def action_scroll_up(self) -> None:
        """Use up arrow for completion selection while focused."""
        self.action_completion_previous()

    async def on_key(self, event: Key) -> None:
        """Route completion and submission keys before default input handling."""
        keybindings = self.tui_keybindings
        if event.key == keybindings.queue_follow_up:
            event.stop()
            event.prevent_default()
            await self._completion_target().action_submit_follow_up()
        elif event.key == keybindings.dequeue_messages:
            event.stop()
            event.prevent_default()
            self._completion_target().action_dequeue_messages()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            await self._completion_target().action_submit_prompt()
        elif event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
        elif event.key == keybindings.accept_completion:
            event.stop()
            self._completion_target().action_accept_completion()
        elif event.key == keybindings.cancel:
            event.stop()
            self._completion_target().action_cancel()
        elif event.key == keybindings.command_palette:
            event.stop()
            self._completion_target().action_open_command_palette()
        elif event.key == keybindings.session_picker:
            event.stop()
            self._completion_target().action_open_session_picker()
        elif keybindings.session_new and event.key == keybindings.session_new:
            event.stop()
            self._completion_target().action_new_session()
        elif keybindings.session_tree and event.key == keybindings.session_tree:
            event.stop()
            self._completion_target().action_open_tree_picker()
        elif keybindings.session_fork and event.key == keybindings.session_fork:
            event.stop()
            self._completion_target().action_open_fork_picker()
        elif keybindings.session_resume and event.key == keybindings.session_resume:
            event.stop()
            self._completion_target().action_open_session_picker()
        elif _is_thinking_cycle_key(event.key, keybindings.thinking_cycle):
            event.stop()
            self._completion_target().action_cycle_thinking()
        elif event.key == keybindings.model_cycle:
            event.stop()
            self._completion_target().action_cycle_model()
        elif event.key == keybindings.model_cycle_previous:
            event.stop()
            self._completion_target().action_cycle_model_previous()
        elif event.key == keybindings.model_picker:
            event.stop()
            self._completion_target().action_open_model_picker()
        elif event.key == keybindings.toggle_tool_results:
            event.stop()
            self._completion_target().action_toggle_tool_results()
        elif event.key == keybindings.toggle_thinking:
            event.stop()
            self._completion_target().action_toggle_thinking()
        elif event.key == keybindings.external_editor:
            event.stop()
            self._completion_target().action_open_external_editor()
        elif event.key == keybindings.paste_clipboard:
            event.stop()
            event.prevent_default()
            self._completion_target().action_paste_clipboard()
        elif event.key == keybindings.copy_last_message:
            event.stop()
            event.prevent_default()
            self._completion_target().action_copy_last_message()
        elif event.key == keybindings.copy_message:
            if self.selected_text:
                return
            event.stop()
            event.prevent_default()
            if self.text:
                self.text = ""
                self.move_cursor((0, 0))
        elif event.key == keybindings.completion_next:
            event.stop()
            if self._has_completion_options():
                self._completion_target().action_completion_next()
            else:
                self.action_cursor_down()
        elif event.key == keybindings.completion_previous:
            event.stop()
            self.action_completion_previous()
        elif event.key == keybindings.quit:
            event.stop()
            await self.action_quit()

    def _has_completion_options(self) -> bool:
        completion_state = getattr(self.app, "_completion_state", None)
        return bool(getattr(completion_state, "items", ()))

    def _completion_target(self) -> CompletionActionTarget:
        return cast(CompletionActionTarget, self.app)


class SessionPickerScreen(ModalScreen[str | None]):
    """Searchable modal picker for indexed sessions."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab", "toggle_scope", "Scope", priority=True),
        Binding("f2", "start_rename", "Rename"),
        Binding("ctrl+r", "start_rename", "Rename", priority=True),
        Binding("ctrl+e", "start_rename", "Rename", show=False),
        Binding("ctrl+d", "delete_session", "Delete"),
        Binding("ctrl+backspace", "delete_session_noninvasive", "Delete", show=False),
        Binding("ctrl+n", "toggle_named_filter", "Named"),
        Binding("ctrl+p", "toggle_path", "Path"),
        Binding("ctrl+s", "toggle_sort", "Sort"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(
        self,
        records: Sequence[SessionCompletionRecord],
        *,
        theme: TuiTheme,
        all_records: Sequence[SessionCompletionRecord] | None = None,
        current_session_id: str | None = None,
        rename_session: Callable[[str, str], SessionCompletionRecord | None] | None = None,
        delete_session: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__()
        self.current_records = tuple(records)
        self.all_records = tuple(all_records if all_records is not None else records)
        self.filtered_records = self.current_records
        self.theme = theme
        self.current_session_id = current_session_id
        self.search_value = ""
        self.scope: Literal["current", "all"] = "current"
        self.mode: Literal["list", "rename"] = "list"
        self.rename_target_id: str | None = None
        self.rename_previous_query = ""
        self.rename_session = rename_session
        self.delete_confirm_target_id: str | None = None
        self.delete_session = delete_session
        self.named_only = False
        self.show_path = False
        self.sort_mode: SessionPickerSortMode = "threaded"

    def compose(self) -> ComposeResult:
        """Compose the session picker."""
        with Vertical(id="session-picker"):
            yield Static("Sessions", id="session-picker-title")
            yield Input(placeholder="Search sessions", id="session-picker-search")
            yield ListView(
                *[
                    ListItem(
                        Label(
                            _session_picker_label(
                                record,
                                show_path=self.show_path,
                                current_session_id=self.current_session_id,
                            ),
                            markup=False,
                        )
                    )
                    for record in self.filtered_records
                ],
                id="session-picker-list",
            )
            yield Static("", id="session-picker-help")

    def on_mount(self) -> None:
        """Focus search input for Pi-style picker filtering."""
        self._refresh_session_list()
        self.query_one("#session-picker-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter sessions as the search value changes."""
        if event.input.id != "session-picker-search":
            return
        if self.mode == "rename":
            return
        self.search_value = event.value
        self._refresh_session_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Select the highlighted filtered session from the search input."""
        if event.input.id != "session-picker-search":
            return
        event.stop()
        if self.mode == "rename":
            self._confirm_rename(event.value)
            return
        self.action_select_cursor()

    def _refresh_session_list(self) -> None:
        """Rebuild the visible session rows from the current search query."""
        if self.mode == "rename":
            return
        records = self.current_records if self.scope == "current" else self.all_records
        self.filtered_records = _filter_session_picker_records(
            records,
            self.search_value,
            named_only=self.named_only,
            sort_mode=self.sort_mode,
        )
        tree_depths = (
            _session_picker_thread_depths(self.filtered_records)
            if self.sort_mode == "threaded"
            else {}
        )
        session_list = self.query_one("#session-picker-list", ListView)
        session_list.clear()
        session_list.extend(
            ListItem(
                Label(
                    _session_picker_label(
                        record,
                        show_path=self.show_path,
                        current_session_id=self.current_session_id,
                        thread_depth=tree_depths.get(record.id, 0),
                    ),
                    markup=False,
                )
            )
            for record in self.filtered_records
        )
        session_list.index = 0 if self.filtered_records else None
        scope_state = f"scope:{self.scope}"
        named_state = "named:on" if self.named_only else "named:off"
        path_state = "path:on" if self.show_path else "path:off"
        sort_state = f"sort:{_session_picker_sort_label(self.sort_mode)}"
        if self.filtered_records:
            help_text = (
                f'Type to search, re:<pattern> regex, or "phrase" exact - '
                f"Tab {scope_state} - Ctrl+N {named_state} - Ctrl+P {path_state} - "
                f"Ctrl+S {sort_state} - "
                "Ctrl+R/F2 rename - Ctrl+D delete - "
                "Enter selects - Escape closes"
            )
        else:
            help_text = (
                f'No matching sessions - re:<pattern> regex, "phrase" exact - '
                f"Tab {scope_state} - Ctrl+N {named_state} - Ctrl+P {path_state} - "
                f"Ctrl+S {sort_state} - Escape closes"
            )
        self.query_one("#session-picker-help", Static).update(help_text)

    def on_key(self, event: Key) -> None:
        """Route session picker keys to the list."""
        if self.mode == "rename":
            return
        if event.key == "up":
            event.stop()
            self.delete_confirm_target_id = None
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.delete_confirm_target_id = None
            self.action_cursor_down()
        elif event.key == "pageup":
            event.stop()
            self.delete_confirm_target_id = None
            self.action_page_up()
        elif event.key == "pagedown":
            event.stop()
            self.delete_confirm_target_id = None
            self.action_page_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()
        elif event.key in {"tab", "ctrl+i"}:
            event.stop()
            self.action_toggle_scope()
        elif event.key == "ctrl+d":
            event.stop()
            self.action_delete_session()
        elif event.key == "ctrl+r":
            event.stop()
            self.action_start_rename()
        elif event.key == "ctrl+backspace" and not self.search_value.strip():
            event.stop()
            self.action_delete_session_noninvasive()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected session id."""
        if self.mode != "list":
            return
        if event.index < 0 or event.index >= len(self.filtered_records):
            return
        self.dismiss(self.filtered_records[event.index].id)

    def action_cursor_up(self) -> None:
        """Move to the previous session."""
        self.query_one("#session-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next session."""
        self.query_one("#session-picker-list", ListView).action_cursor_down()

    def action_page_up(self) -> None:
        """Move up by a page of sessions."""
        self._move_session_page(-1)

    def action_page_down(self) -> None:
        """Move down by a page of sessions."""
        self._move_session_page(1)

    def _move_session_page(self, direction: Literal[-1, 1]) -> None:
        """Move the selected session by a viewport-sized page."""
        if not self.filtered_records:
            return
        session_list = self.query_one("#session-picker-list", ListView)
        current_index = session_list.index if session_list.index is not None else 0
        page_size = max(1, session_list.size.height - 1)
        if page_size <= 1:
            page_size = 10
        next_index = current_index + (direction * page_size)
        session_list.index = max(0, min(len(self.filtered_records) - 1, next_index))

    def action_select_cursor(self) -> None:
        """Select the highlighted session."""
        if self.mode != "list":
            return
        if not self.filtered_records:
            return
        self.query_one("#session-picker-list", ListView).action_select_cursor()

    def action_start_rename(self) -> None:
        """Rename the highlighted session."""
        if self.rename_session is None or self.mode != "list":
            return
        self.delete_confirm_target_id = None
        selected = self._selected_session_record()
        if selected is None:
            return
        self.mode = "rename"
        self.rename_target_id = selected.id
        self.rename_previous_query = self.search_value
        title = _named_session_title(selected.title) or ""
        search = self.query_one("#session-picker-search", Input)
        search.placeholder = "Rename session"
        search.value = title
        search.focus()
        self.query_one("#session-picker-title", Static).update("Rename Session")
        self.query_one("#session-picker-help", Static).update(
            "Enter saves - Escape cancels rename"
        )

    def _confirm_rename(self, value: str) -> None:
        """Save the active rename target and return to list mode."""
        name = value.strip()
        if not name:
            self.query_one("#session-picker-help", Static).update("Session name is required.")
            return
        if any(char in name for char in "\r\n\t"):
            self.query_one("#session-picker-help", Static).update(
                "Session name must be a single line."
            )
            return
        target_id = self.rename_target_id
        rename_session = self.rename_session
        if target_id is None or rename_session is None:
            self._exit_rename_mode()
            return
        updated = rename_session(target_id, name)
        if updated is None:
            self.query_one("#session-picker-help", Static).update(
                f"Unknown session: {target_id}"
            )
            return
        self.current_records = _replace_session_picker_record(self.current_records, updated)
        self.all_records = _replace_session_picker_record(self.all_records, updated)
        self._exit_rename_mode()

    def _exit_rename_mode(self) -> None:
        """Return from rename mode to list mode."""
        self.mode = "list"
        self.rename_target_id = None
        self.search_value = self.rename_previous_query
        search = self.query_one("#session-picker-search", Input)
        search.placeholder = "Search sessions"
        search.value = self.search_value
        self.query_one("#session-picker-title", Static).update("Sessions")
        self._refresh_session_list()

    def _selected_session_record(self) -> SessionCompletionRecord | None:
        """Return the currently highlighted filtered session."""
        if not self.filtered_records:
            return None
        index = self.query_one("#session-picker-list", ListView).index
        if index is None or index < 0 or index >= len(self.filtered_records):
            return None
        return self.filtered_records[index]

    def action_delete_session(self) -> None:
        """Delete the highlighted session after a second delete press confirms it."""
        if self.delete_session is None or self.mode != "list":
            return
        selected = self._selected_session_record()
        if selected is None:
            return
        if selected.id == self.current_session_id:
            self.delete_confirm_target_id = None
            self.query_one("#session-picker-help", Static).update(
                "Cannot delete the active session."
            )
            return
        if self.delete_confirm_target_id != selected.id:
            self.delete_confirm_target_id = selected.id
            self.query_one("#session-picker-help", Static).update(
                "Press Ctrl+D again to delete this session - Escape cancels"
            )
            return
        self.delete_confirm_target_id = None
        if not self.delete_session(selected.id):
            self.query_one("#session-picker-help", Static).update(
                f"Failed to delete session: {selected.id}"
            )
            return
        self.current_records = _remove_session_picker_record(self.current_records, selected.id)
        self.all_records = _remove_session_picker_record(self.all_records, selected.id)
        self._refresh_session_list()

    def action_delete_session_noninvasive(self) -> None:
        """Delete only when the picker query is empty."""
        if self.search_value.strip():
            return
        self.action_delete_session()

    def action_toggle_scope(self) -> None:
        """Toggle between current-project sessions and all indexed sessions."""
        self.delete_confirm_target_id = None
        self.scope = "all" if self.scope == "current" else "current"
        self._refresh_session_list()

    def action_toggle_named_filter(self) -> None:
        """Toggle whether the picker shows only named sessions."""
        self.delete_confirm_target_id = None
        self.named_only = not self.named_only
        self._refresh_session_list()

    def action_toggle_path(self) -> None:
        """Toggle session path visibility."""
        self.delete_confirm_target_id = None
        self.show_path = not self.show_path
        self._refresh_session_list()

    def action_toggle_sort(self) -> None:
        """Toggle session sort order."""
        self.delete_confirm_target_id = None
        sort_order: tuple[SessionPickerSortMode, ...] = (
            "threaded",
            "recent",
            "relevance",
        )
        self.sort_mode = sort_order[(sort_order.index(self.sort_mode) + 1) % len(sort_order)]
        self._refresh_session_list()

    def action_cancel(self) -> None:
        """Close the picker without selecting a session."""
        if self.mode == "rename":
            self._exit_rename_mode()
            return
        if self.delete_confirm_target_id is not None:
            self.delete_confirm_target_id = None
            self._refresh_session_list()
            return
        self.dismiss(None)


@dataclass(frozen=True, slots=True)
class TreePickerResult:
    """Tree-picker branch selection."""

    entry_id: str
    summarize: bool = False
    custom_instructions: str | None = None


SettingsPickerKey = Literal[
    "theme",
    "auto_copy_selection",
    "double_escape_action",
    "tree_filter_mode",
]


@dataclass(frozen=True, slots=True)
class SettingsPickerItem:
    """One durable TUI setting row."""

    key: SettingsPickerKey
    label: str
    value: str


class SettingsPickerScreen(ModalScreen[None]):
    """Modal picker for durable TUI settings."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_cursor", "Change", show=False, priority=True),
    ]

    def __init__(
        self,
        settings: TuiSettings,
        *,
        apply_settings: Callable[[TuiSettings], None],
    ) -> None:
        super().__init__()
        self.settings = settings
        self.apply_settings = apply_settings
        self.search_value = ""
        self.filtered_items = _settings_picker_items(settings)

    def compose(self) -> ComposeResult:
        """Compose the settings picker."""
        with Vertical(id="settings-picker"):
            yield Static("Settings", id="settings-picker-title")
            yield Input(placeholder="Search settings", id="settings-picker-search")
            yield ListView(
                *self._list_items(),
                id="settings-picker-list",
            )
            yield Static(self._help_text(), id="settings-picker-help")

    def on_mount(self) -> None:
        """Focus the settings search input."""
        self._refresh_settings_list(0)
        self.query_one("#settings-picker-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter settings as the search value changes."""
        if event.input.id != "settings-picker-search":
            return
        self.search_value = event.value
        self._refresh_settings_list(0)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Change the highlighted filtered setting from the search input."""
        if event.input.id != "settings-picker-search":
            return
        event.stop()
        self.action_select_cursor()

    def on_key(self, event: Key) -> None:
        """Route settings picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()
        elif event.key == "escape":
            event.stop()
            self.action_cancel()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Change the selected setting and keep the picker open."""
        if event.index >= len(self.filtered_items):
            return
        self._change_setting(event.index)

    def action_cursor_up(self) -> None:
        """Move to the previous setting."""
        self.query_one("#settings-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next setting."""
        self.query_one("#settings-picker-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Change the highlighted setting."""
        settings_list = self.query_one("#settings-picker-list", ListView)
        if settings_list.index is None:
            return
        self._change_setting(settings_list.index)

    def action_cancel(self) -> None:
        """Close the settings picker."""
        self.dismiss(None)

    def _change_setting(self, index: int) -> None:
        if index >= len(self.filtered_items):
            return
        selected_key = self.filtered_items[index].key
        self.settings = _next_tui_settings(self.settings, selected_key)
        self.apply_settings(self.settings)
        self._refresh_settings_list(index)

    def _refresh_settings_list(self, index: int) -> None:
        self.filtered_items = _filter_settings_picker_items(
            _settings_picker_items(self.settings),
            self.search_value,
        )
        settings_list = self.query_one("#settings-picker-list", ListView)
        settings_list.clear()
        settings_list.extend(self._list_items())
        settings_list.index = (
            min(index, len(settings_list.children) - 1) if self.filtered_items else None
        )
        self.query_one("#settings-picker-help", Static).update(self._help_text())

    def _list_items(self) -> list[ListItem]:
        return [
            ListItem(Label(_settings_picker_label(item), markup=False))
            for item in self.filtered_items
        ]

    def _help_text(self) -> str:
        if self.filtered_items:
            return "Type to search - Enter changes - Escape closes"
        return "No matching settings - Escape closes"


class TrustPickerScreen(ModalScreen[ProjectTrustOption | None]):
    """Modal picker for project trust decisions."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_cursor", "Save", show=False, priority=True),
    ]

    def __init__(self, state: ProjectTrustState) -> None:
        super().__init__()
        self.trust_state = state

    def compose(self) -> ComposeResult:
        """Compose the trust picker."""
        with Vertical(id="trust-picker"):
            yield Static("Project trust", id="trust-picker-title")
            yield Static(str(self.trust_state.cwd), id="trust-picker-cwd", markup=False)
            yield Static(
                f"Saved decision: {_project_trust_decision_label(self.trust_state.saved_decision)}",
                id="trust-picker-current",
                markup=False,
            )
            yield ListView(
                *self._list_items(),
                id="trust-picker-list",
            )
            yield Static("Enter saves - Escape closes", id="trust-picker-help")

    def on_mount(self) -> None:
        """Focus the trust list."""
        trust_list = self.query_one("#trust-picker-list", ListView)
        trust_list.index = 0
        trust_list.focus()

    def on_key(self, event: Key) -> None:
        """Route trust picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()
        elif event.key == "escape":
            event.stop()
            self.action_cancel()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected trust option."""
        if event.index >= len(self.trust_state.options):
            return
        self.dismiss(self.trust_state.options[event.index])

    def action_cursor_up(self) -> None:
        """Move to the previous trust option."""
        self.query_one("#trust-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next trust option."""
        self.query_one("#trust-picker-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Choose the highlighted trust option."""
        trust_list = self.query_one("#trust-picker-list", ListView)
        if trust_list.index is None:
            return
        if trust_list.index >= len(self.trust_state.options):
            return
        self.dismiss(self.trust_state.options[trust_list.index])

    def action_cancel(self) -> None:
        """Cancel the trust picker."""
        self.dismiss(None)

    def _list_items(self) -> list[ListItem]:
        return [
            ListItem(
                Label(
                    _project_trust_option_label(item, self.trust_state.saved_decision),
                    markup=False,
                )
            )
            for item in self.trust_state.options
        ]


class UserMessagePickerScreen(ModalScreen[str | None]):
    """Modal picker for forking from a previous user message."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("enter", "select_cursor", "Fork", show=False),
    ]

    def __init__(self, choices: Sequence[SessionTreeChoice]) -> None:
        super().__init__()
        self.choices = tuple(choices)

    def compose(self) -> ComposeResult:
        """Compose the user-message picker."""
        with Vertical(id="user-message-picker"):
            yield Static("Fork from Message", id="user-message-picker-title")
            yield Static(
                "Select a user message to copy the active path up to that point.",
                id="user-message-picker-description",
            )
            yield ListView(
                *self._list_items(),
                id="user-message-picker-list",
            )
            yield Static(
                "Enter forks - Escape cancels",
                id="user-message-picker-help",
            )

    def on_mount(self) -> None:
        """Focus the user-message list for keyboard navigation."""
        message_list = self.query_one("#user-message-picker-list", ListView)
        message_list.index = len(self.choices) - 1 if self.choices else None
        message_list.focus()

    def on_key(self, event: Key) -> None:
        """Route picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "pageup":
            event.stop()
            self.action_page_up()
        elif event.key == "pagedown":
            event.stop()
            self.action_page_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()
        elif event.key == "escape":
            event.stop()
            self.action_cancel()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected entry id."""
        if event.index >= len(self.choices):
            return
        self.dismiss(self.choices[event.index].entry_id)

    def action_cursor_up(self) -> None:
        """Move to the previous user message."""
        self._move(-1)

    def action_cursor_down(self) -> None:
        """Move to the next user message."""
        self._move(1)

    def action_page_up(self) -> None:
        """Move up by a page of user messages."""
        self._move_page(-1)

    def action_page_down(self) -> None:
        """Move down by a page of user messages."""
        self._move_page(1)

    def _move(self, direction: Literal[-1, 1]) -> None:
        if not self.choices:
            return
        message_list = self.query_one("#user-message-picker-list", ListView)
        current_index = message_list.index if message_list.index is not None else 0
        message_list.index = (current_index + direction) % len(self.choices)

    def _move_page(self, direction: Literal[-1, 1]) -> None:
        if not self.choices:
            return
        message_list = self.query_one("#user-message-picker-list", ListView)
        current_index = message_list.index if message_list.index is not None else 0
        page_size = max(1, message_list.size.height - 1)
        if page_size <= 1:
            page_size = 10
        next_index = current_index + (direction * page_size)
        message_list.index = max(0, min(len(self.choices) - 1, next_index))

    def action_select_cursor(self) -> None:
        """Fork from the highlighted user message."""
        self.query_one("#user-message-picker-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close the picker without forking."""
        self.dismiss(None)

    def _list_items(self) -> list[ListItem]:
        total = len(self.choices)
        return [
            ListItem(Label(_user_message_picker_label(choice, index, total), markup=False))
            for index, choice in enumerate(self.choices)
        ]


TreeFilterMode = Literal["default", "no-tools", "user-only", "labeled-only", "all"]
TREE_FILTER_MODES: tuple[TreeFilterMode, ...] = (
    "default",
    "no-tools",
    "user-only",
    "labeled-only",
    "all",
)


class TreePickerScreen(ModalScreen[TreePickerResult | None]):
    """Modal picker for branching from a previous session entry."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("enter", "select_cursor", "Branch", show=False),
        Binding("s", "select_with_summary", "Summarize", show=False),
        Binding("c", "select_with_custom_summary", "Custom summary", show=False),
        Binding("ctrl+t", "toggle_tool_calls", "Tool calls", show=False),
        Binding("ctrl+d", "set_default_tree_filter", "Default filter", show=False),
        Binding("ctrl+u", "toggle_user_tree_filter", "User filter", show=False),
        Binding("ctrl+l", "toggle_labeled_tree_filter", "Labeled filter", show=False),
        Binding("ctrl+a", "toggle_all_tree_filter", "All filter", show=False),
        Binding("ctrl+o", "cycle_tree_filter", "Cycle filter", show=False),
        Binding("shift+ctrl+o", "cycle_tree_filter_backward", "Cycle filter backward", show=False),
        Binding("ctrl+f", "cycle_tree_filter", "Filter", show=False),
        Binding("ctrl+x", "copy_selected_tree_entry", "Copy", show=False),
        Binding("ctrl+left", "fold_tree_branch", "Fold", show=False),
        Binding("alt+left", "fold_tree_branch", "Fold", show=False),
        Binding("ctrl+right", "unfold_tree_branch", "Unfold", show=False),
        Binding("alt+right", "unfold_tree_branch", "Unfold", show=False),
        Binding("shift+l", "edit_tree_label", "Label", show=False),
        Binding("shift+t", "toggle_tree_label_timestamps", "Label time", show=False),
    ]

    def __init__(
        self,
        choices: Sequence[SessionTreeChoice],
        *,
        theme: TuiTheme,
        initial_filter_mode: TreeFilterMode = "default",
        set_entry_label: Callable[[str, str | None], object] | None = None,
    ) -> None:
        super().__init__()
        self.choices = tuple(choices)
        self.theme = theme
        self.set_entry_label = set_entry_label
        self.show_tool_calls = True
        self.show_label_timestamps = False
        self.filter_mode = initial_filter_mode
        self.show_tool_calls = self.filter_mode != "no-tools"
        self.folded_entry_ids: set[str] = set()
        self.search_value = ""

    def compose(self) -> ComposeResult:
        """Compose the tree picker."""
        with Vertical(id="tree-picker"):
            yield Static("Session Tree", id="tree-picker-title")
            yield Static(self._search_text(), id="tree-picker-search")
            yield ListView(
                *self._list_items(),
                id="tree-picker-list",
            )
            yield Static(
                self._help_text(),
                id="tree-picker-help",
            )

    def on_mount(self) -> None:
        """Focus the tree list for keyboard navigation."""
        tree_list = self.query_one("#tree-picker-list", ListView)
        tree_list.index = _active_tree_choice_index(self.choices)
        tree_list.focus()

    def on_key(self, event: Key) -> None:
        """Route tree picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "pageup":
            event.stop()
            self.action_page_up()
        elif event.key == "pagedown":
            event.stop()
            self.action_page_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()
        elif event.key == "s":
            event.stop()
            self.action_select_with_summary()
        elif event.key == "c":
            event.stop()
            self.action_select_with_custom_summary()
        elif event.key == "ctrl+t":
            event.stop()
            self.action_toggle_tool_calls()
        elif event.key == "ctrl+d":
            event.stop()
            self.action_set_default_tree_filter()
        elif event.key == "ctrl+u":
            event.stop()
            self.action_toggle_user_tree_filter()
        elif event.key == "ctrl+l":
            event.stop()
            self.action_toggle_labeled_tree_filter()
        elif event.key == "ctrl+a":
            event.stop()
            self.action_toggle_all_tree_filter()
        elif event.key in {"ctrl+o", "ctrl+f"}:
            event.stop()
            self.action_cycle_tree_filter()
        elif event.key in {"shift+ctrl+o", "ctrl+shift+o"}:
            event.stop()
            self.action_cycle_tree_filter_backward()
        elif event.key == "ctrl+x":
            event.stop()
            self.action_copy_selected_tree_entry()
        elif event.key in {"ctrl+left", "alt+left"}:
            event.stop()
            self.action_fold_tree_branch()
        elif event.key in {"ctrl+right", "alt+right"}:
            event.stop()
            self.action_unfold_tree_branch()
        elif event.key in {"shift+l", "L"}:
            event.stop()
            self.action_edit_tree_label()
        elif event.key in {"shift+t", "T"}:
            event.stop()
            self.action_toggle_tree_label_timestamps()
        elif event.key == "backspace":
            if self.search_value:
                event.stop()
                self.run_worker(self._set_tree_search(self.search_value[:-1]))
        elif search_character := _tree_search_character(event):
            event.stop()
            self.run_worker(self._set_tree_search(self.search_value + search_character))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected entry id."""
        self.dismiss(TreePickerResult(entry_id=self._visible_choices()[event.index].entry_id))

    def action_cursor_up(self) -> None:
        """Move to the previous tree entry."""
        self._move_tree_cursor(-1)

    def action_cursor_down(self) -> None:
        """Move to the next tree entry."""
        self._move_tree_cursor(1)

    def _move_tree_cursor(self, direction: Literal[-1, 1]) -> None:
        """Move selected tree row, wrapping at list boundaries like Pi."""
        visible_choices = self._visible_choices()
        if not visible_choices:
            return
        tree_list = self.query_one("#tree-picker-list", ListView)
        current_index = tree_list.index if tree_list.index is not None else 0
        tree_list.index = (current_index + direction) % len(visible_choices)

    def action_page_up(self) -> None:
        """Move up by a page of tree entries."""
        self._move_tree_page(-1)

    def action_page_down(self) -> None:
        """Move down by a page of tree entries."""
        self._move_tree_page(1)

    def _move_tree_page(self, direction: Literal[-1, 1]) -> None:
        """Move the selected tree entry by a viewport-sized page."""
        visible_choices = self._visible_choices()
        if not visible_choices:
            return
        tree_list = self.query_one("#tree-picker-list", ListView)
        current_index = tree_list.index if tree_list.index is not None else 0
        page_size = max(1, tree_list.size.height - 1)
        if page_size <= 1:
            page_size = 10
        next_index = current_index + (direction * page_size)
        tree_list.index = max(0, min(len(visible_choices) - 1, next_index))

    def action_select_cursor(self) -> None:
        """Branch from the highlighted entry without a summary."""
        self.query_one("#tree-picker-list", ListView).action_select_cursor()

    def action_select_with_summary(self) -> None:
        """Branch from the highlighted entry with a branch summary."""
        tree_list = self.query_one("#tree-picker-list", ListView)
        index = tree_list.index
        if index is None:
            return
        self.dismiss(
            TreePickerResult(entry_id=self._visible_choices()[index].entry_id, summarize=True)
        )

    def action_select_with_custom_summary(self) -> None:
        """Branch from the highlighted entry with custom summary instructions."""
        tree_list = self.query_one("#tree-picker-list", ListView)
        index = tree_list.index
        if index is None:
            return
        self.app.push_screen(
            BranchSummaryInstructionsScreen(theme=self.theme),
            callback=lambda instructions: self._dismiss_with_custom_summary(index, instructions),
        )

    def _dismiss_with_custom_summary(self, index: int, instructions: str | None) -> None:
        if instructions is None:
            return
        visible_choices = self._visible_choices()
        if index >= len(visible_choices):
            return
        self.dismiss(
            TreePickerResult(
                entry_id=visible_choices[index].entry_id,
                summarize=True,
                custom_instructions=instructions,
            )
        )

    def action_copy_selected_tree_entry(self) -> None:
        """Copy the selected tree entry's full text when available."""
        choice = self._selected_choice()
        app = cast(Any, self.app)
        if choice is None or not choice.copy_text:
            app._notify("Selected tree entry has no text to copy.", severity="warning")
            return
        app.copy_to_clipboard(choice.copy_text)
        app._notify("Copied selected tree entry to clipboard.")

    def action_fold_tree_branch(self) -> None:
        """Fold the selected tree branch when it has visible children."""
        self.run_worker(self._fold_tree_branch())

    async def _fold_tree_branch(self) -> None:
        choice = self._selected_choice()
        filtered_choices = self._filtered_choices()
        if choice is None:
            return
        if not _tree_choice_is_branch_foldable(choice, filtered_choices):
            self._move_tree_branch_segment("up")
            return
        if choice.entry_id in self.folded_entry_ids:
            self._move_tree_branch_segment("up")
            return
        self.folded_entry_ids.add(choice.entry_id)
        await self._refresh_tree_choices(selected_entry_id=choice.entry_id)

    def action_unfold_tree_branch(self) -> None:
        """Unfold the selected tree branch."""
        self.run_worker(self._unfold_tree_branch())

    async def _unfold_tree_branch(self) -> None:
        choice = self._selected_choice()
        if choice is None:
            return
        if choice.entry_id in self.folded_entry_ids:
            self.folded_entry_ids.remove(choice.entry_id)
            await self._refresh_tree_choices(selected_entry_id=choice.entry_id)
            return
        self._move_tree_branch_segment("down")

    def _move_tree_branch_segment(self, direction: Literal["up", "down"]) -> None:
        visible_choices = self._visible_choices()
        if not visible_choices:
            return
        tree_list = self.query_one("#tree-picker-list", ListView)
        selected_index = tree_list.index
        if selected_index is None or selected_index >= len(visible_choices):
            return
        tree_list.index = _tree_branch_segment_index(
            visible_choices,
            selected_index,
            direction=direction,
        )

    def action_edit_tree_label(self) -> None:
        """Edit the selected tree entry label."""
        choice = self._selected_choice()
        app = cast(Any, self.app)
        if choice is None:
            return
        if self.set_entry_label is None:
            app._notify("Tree labels are not available.", severity="warning")
            return
        app.push_screen(
            TreeLabelInputScreen(
                entry_id=choice.entry_id,
                current_label=choice.tree_label,
                theme=self.theme,
            ),
            callback=self._handle_tree_label_input,
        )

    def _handle_tree_label_input(self, result: tuple[str, str | None] | None) -> None:
        if result is None:
            return
        entry_id, label = result
        self.run_worker(self._apply_tree_label(entry_id, label))

    async def _apply_tree_label(self, entry_id: str, label: str | None) -> None:
        if self.set_entry_label is None:
            return
        app = cast(Any, self.app)
        try:
            result = self.set_entry_label(entry_id, label)
            if isawaitable(result):
                result = await result
            if result is not None:
                self.choices = tuple(cast(Sequence[SessionTreeChoice], result))
            await self._refresh_tree_choices(selected_entry_id=entry_id)
        except Exception as exc:  # noqa: BLE001 - surface label failures in the TUI
            app._notify(f"Error: {exc}", severity="error")

    def action_toggle_tree_label_timestamps(self) -> None:
        """Toggle Pi-style label timestamp display."""
        self.show_label_timestamps = not self.show_label_timestamps
        self.run_worker(self._refresh_tree_choices(selected_entry_id=self._selected_entry_id()))

    def action_toggle_tool_calls(self) -> None:
        """Toggle Pi's no-tools tree filter."""
        self.run_worker(self._set_tree_filter("no-tools", toggle=True))

    def action_set_default_tree_filter(self) -> None:
        """Set the tree picker to its default filter mode."""
        self.run_worker(self._set_tree_filter("default"))

    def action_toggle_user_tree_filter(self) -> None:
        """Toggle Pi's user-only tree filter."""
        self.run_worker(self._set_tree_filter("user-only", toggle=True))

    def action_toggle_labeled_tree_filter(self) -> None:
        """Toggle Pi's labeled-only tree filter."""
        self.run_worker(self._set_tree_filter("labeled-only", toggle=True))

    def action_toggle_all_tree_filter(self) -> None:
        """Toggle Pi's all-entries tree filter."""
        self.run_worker(self._set_tree_filter("all", toggle=True))

    def action_cycle_tree_filter(self) -> None:
        """Cycle Pi-style tree filter modes supported by Tau's tree choice model."""
        self.run_worker(self._cycle_tree_filter())

    def action_cycle_tree_filter_backward(self) -> None:
        """Cycle Pi-style tree filter modes backward."""
        self.run_worker(self._cycle_tree_filter(direction=-1))

    async def _cycle_tree_filter(self, *, direction: Literal[-1, 1] = 1) -> None:
        selected_entry_id = self._selected_entry_id()
        current_index = TREE_FILTER_MODES.index(self.filter_mode)
        self.filter_mode = TREE_FILTER_MODES[
            (current_index + direction) % len(TREE_FILTER_MODES)
        ]
        if self.filter_mode == "no-tools":
            self.show_tool_calls = False
        elif self.filter_mode == "all":
            self.show_tool_calls = True
        await self._refresh_tree_choices(selected_entry_id=selected_entry_id)

    async def _set_tree_filter(
        self,
        filter_mode: TreeFilterMode,
        *,
        toggle: bool = False,
    ) -> None:
        selected_entry_id = self._selected_entry_id()
        self.filter_mode = (
            "default" if toggle and self.filter_mode == filter_mode else filter_mode
        )
        self.show_tool_calls = self.filter_mode != "no-tools"
        self.folded_entry_ids.clear()
        await self._refresh_tree_choices(selected_entry_id=selected_entry_id)

    async def _refresh_tree_choices(self, *, selected_entry_id: str | None = None) -> None:
        tree_list = self.query_one("#tree-picker-list", ListView)
        await tree_list.clear()
        await tree_list.extend(self._list_items())
        visible_choices = self._visible_choices()
        tree_list.index = (
            _tree_choice_index(visible_choices, selected_entry_id) if visible_choices else None
        )
        self.query_one("#tree-picker-search", Static).update(self._search_text())
        self.query_one("#tree-picker-help", Static).update(self._help_text())

    async def _set_tree_search(self, value: str) -> None:
        selected_entry_id = self._selected_entry_id()
        self.search_value = value
        self.folded_entry_ids.clear()
        await self._refresh_tree_choices(selected_entry_id=selected_entry_id)

    def _selected_entry_id(self) -> str | None:
        choice = self._selected_choice()
        return choice.entry_id if choice is not None else None

    def _selected_choice(self) -> SessionTreeChoice | None:
        tree_list = self.query_one("#tree-picker-list", ListView)
        index = tree_list.index
        visible_choices = self._visible_choices()
        if index is None or index >= len(visible_choices):
            return None
        return visible_choices[index]

    def _visible_choices(self) -> tuple[SessionTreeChoice, ...]:
        filtered = self._filtered_choices()
        if not self.folded_entry_ids:
            return filtered
        choices_by_id = {choice.entry_id: choice for choice in filtered}
        return tuple(
            choice
            for choice in filtered
            if not _tree_choice_has_folded_ancestor(
                choice,
                choices_by_id=choices_by_id,
                folded_entry_ids=self.folded_entry_ids,
            )
        )

    def _filtered_choices(self) -> tuple[SessionTreeChoice, ...]:
        return tuple(
            choice
            for choice in self.choices
            if _tree_choice_matches_filter(
                choice,
                filter_mode=self.filter_mode,
                show_tool_calls=self.show_tool_calls,
            )
            and _tree_choice_matches_search(choice, self.search_value)
        )

    def _list_items(self) -> list[ListItem]:
        return [
            ListItem(
                Label(
                    _tree_picker_label(
                        choice,
                        theme=self.theme,
                        show_label_timestamps=self.show_label_timestamps,
                    ),
                    markup=False,
                )
            )
            for choice in self._visible_choices()
        ]

    def _help_text(self) -> str:
        tool_call_state = "shown" if self.show_tool_calls else "hidden"
        filter_label = "default" if self.filter_mode == "default" else self.filter_mode
        label_time_state = "on" if self.show_label_timestamps else "off"
        return (
            "Type filters - Enter branches - S summarizes - C custom summary - "
            f"Ctrl+X copy - Shift+L label - Shift+T label time ({label_time_state}) - "
            "Ctrl+Left/Right fold - "
            f"Ctrl+T no-tools ({tool_call_state}) - Ctrl+O/Shift+Ctrl+O filter {filter_label} - "
            "Escape clears/closes"
        )

    def _search_text(self) -> str:
        if self.search_value:
            return f"Search: {self.search_value}"
        return "Search: type to filter"

    def action_cancel(self) -> None:
        """Close the picker without selecting an entry."""
        if self.search_value:
            self.run_worker(self._set_tree_search(""))
            return
        self.dismiss(None)


class BranchSummaryInstructionsScreen(ModalScreen[str | None]):
    """Prompt for custom branch-summary instructions."""

    BINDINGS: ClassVar[list[BindingEntry]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, theme: TuiTheme) -> None:
        super().__init__()
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the custom-instructions prompt."""
        with Vertical(id="branch-summary-instructions"):
            yield Static(
                "Custom summarization instructions",
                id="branch-summary-instructions-title",
            )
            yield TextArea(id="branch-summary-instructions-input")
            yield Static(
                "Ctrl+Enter submits - Escape returns to tree",
                id="branch-summary-instructions-help",
            )

    def on_mount(self) -> None:
        """Focus the instruction editor."""
        self.query_one("#branch-summary-instructions-input", TextArea).focus()

    def on_key(self, event: Key) -> None:
        """Submit on Ctrl+Enter and cancel on Escape."""
        if event.key == "ctrl+enter":
            event.stop()
            self.action_submit()
        elif event.key == "escape":
            event.stop()
            self.action_cancel()

    def action_submit(self) -> None:
        """Submit custom instructions."""
        value = self.query_one("#branch-summary-instructions-input", TextArea).text.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        """Cancel custom instructions."""
        self.dismiss(None)


class TreeLabelInputScreen(ModalScreen[tuple[str, str | None] | None]):
    """Prompt for a tree entry label."""

    BINDINGS: ClassVar[list[BindingEntry]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        entry_id: str,
        current_label: str | None,
        theme: TuiTheme,
    ) -> None:
        super().__init__()
        self.entry_id = entry_id
        self.current_label = current_label or ""
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the tree-label editor."""
        with Vertical(id="tree-label-input"):
            yield Static("Tree entry label", id="tree-label-title")
            yield Input(value=self.current_label, id="tree-label-value")
            yield Static("Enter saves - empty clears - Escape returns to tree", id="tree-label-help")

    def on_mount(self) -> None:
        """Focus the label input."""
        label_input = self.query_one("#tree-label-value", Input)
        label_input.cursor_position = len(self.current_label)
        label_input.focus()

    def on_key(self, event: Key) -> None:
        """Submit or cancel the label edit."""
        if event.key == "enter":
            event.stop()
            self.action_submit()
        elif event.key == "escape":
            event.stop()
            self.action_cancel()

    def action_submit(self) -> None:
        """Submit the label edit."""
        value = self.query_one("#tree-label-value", Input).value.strip()
        self.dismiss((self.entry_id, value or None))

    def action_cancel(self) -> None:
        """Cancel the label edit."""
        self.dismiss(None)


class CommandOutputScroll(VerticalScroll):
    """Scrollable command output area with deterministic arrow-key scrolling."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("up", "scroll_up", "Scroll up", show=False, priority=True),
        Binding("down", "scroll_down", "Scroll down", show=False, priority=True),
    ]

    def action_scroll_up(self) -> None:
        """Scroll command output up."""
        self.scroll_y = max(0, self.scroll_y - 1)

    def action_scroll_down(self) -> None:
        """Scroll command output down."""
        self.scroll_y = min(self.max_scroll_y, self.scroll_y + 1)


class CommandOutputScreen(ModalScreen[None]):
    """Dismissible modal for slash-command output."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
        Binding("up", "scroll_up", "Scroll up", show=False, priority=True),
        Binding("down", "scroll_down", "Scroll down", show=False, priority=True),
    ]

    def __init__(self, title: str, message: str, *, theme: TuiTheme) -> None:
        super().__init__()
        self.title_text = title
        self.message = message
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose command output."""
        with Vertical(id="command-output"):
            yield Static(self.title_text, id="command-output-title")
            with CommandOutputScroll(id="command-output-scroll"):
                yield Static(self.message, id="command-output-body", markup=False)
            yield Static("Enter or Escape closes", id="command-output-help")

    def on_mount(self) -> None:
        """Focus the scroll area so arrow keys navigate long output."""
        self.query_one("#command-output-scroll", VerticalScroll).focus()

    def on_key(self, event: Key) -> None:
        """Route arrow keys to the command output scroll area."""
        if event.key == "up":
            event.stop()
            self.action_scroll_up()
        elif event.key == "down":
            event.stop()
            self.action_scroll_down()

    def action_close(self) -> None:
        """Close the command output modal."""
        self.dismiss(None)

    def action_scroll_up(self) -> None:
        """Scroll command output up."""
        self.query_one("#command-output-scroll", CommandOutputScroll).action_scroll_up()

    def action_scroll_down(self) -> None:
        """Scroll command output down."""
        self.query_one("#command-output-scroll", CommandOutputScroll).action_scroll_down()


class LoginProviderPickerScreen(ModalScreen[str | None]):
    """Provider picker for the TUI login flow."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(
        self,
        providers: Sequence[ProviderCatalogEntry],
        *,
        theme: TuiTheme,
        title: str = "Login",
    ) -> None:
        super().__init__()
        self.providers = tuple(providers)
        self.theme = theme
        self.title_text = title

    def compose(self) -> ComposeResult:
        """Compose the provider picker."""
        with Vertical(id="login-provider-picker"):
            yield Static(self.title_text, id="login-provider-title")
            yield ListView(
                *[
                    ListItem(Label(_login_provider_label(provider), markup=False))
                    for provider in self.providers
                ],
                id="login-provider-list",
            )
            yield Static("Enter selects - Escape closes", id="login-provider-help")

    def on_mount(self) -> None:
        """Focus the provider list."""
        provider_list = self.query_one("#login-provider-list", ListView)
        provider_list.index = 0
        provider_list.focus()

    def on_key(self, event: Key) -> None:
        """Route provider picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected provider name."""
        self.dismiss(self.providers[event.index].name)

    def action_cursor_up(self) -> None:
        """Move to the previous provider."""
        self.query_one("#login-provider-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next provider."""
        self.query_one("#login-provider-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted provider."""
        self.query_one("#login-provider-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close without selecting a provider."""
        self.dismiss(None)


class LoginMethodPickerScreen(ModalScreen[str | None]):
    """Login method picker for the TUI login flow."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_cursor", "Select", show=False, priority=True),
    ]

    def __init__(self, *, theme: TuiTheme) -> None:
        super().__init__()
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the login method picker."""
        with Vertical(id="login-method-picker"):
            yield Static("Login", id="login-method-title")
            yield Static("Choose how to authenticate.", id="login-method-intro")
            yield LoginMethodListView(
                ListItem(
                    Label("Subscription\n  Sign in with an OAuth account.", markup=False),
                    id="login-method-subscription",
                ),
                ListItem(
                    Label("API key\n  Save a provider API key.", markup=False),
                    id="login-method-api-key",
                ),
                id="login-method-list",
            )
            yield Static("Enter selects - Escape closes", id="login-method-help")

    def on_mount(self) -> None:
        """Focus the default subscription method."""
        method_list = self.query_one("#login-method-list", ListView)
        method_list.index = 0
        method_list.focus()

    def on_key(self, event: Key) -> None:
        """Route arrow keys between login method buttons."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the selected login method."""
        if event.button.id == "login-method-subscription":
            self.dismiss("subscription")
        elif event.button.id == "login-method-api-key":
            self.dismiss("api-key")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected login method."""
        if event.item.id == "login-method-subscription":
            self.dismiss("subscription")
        elif event.item.id == "login-method-api-key":
            self.dismiss("api-key")

    def action_cancel(self) -> None:
        """Close without selecting a login method."""
        self.dismiss(None)

    def action_cursor_up(self) -> None:
        """Focus the previous login method."""
        self._move_method_cursor(offset=-1)

    def action_cursor_down(self) -> None:
        """Focus the next login method."""
        self._move_method_cursor(offset=1)

    def action_select_cursor(self) -> None:
        """Select the currently focused login method."""
        self.query_one("#login-method-list", ListView).action_select_cursor()

    def _move_method_cursor(self, *, offset: int) -> None:
        method_list = self.query_one("#login-method-list", ListView)
        item_count = len(method_list.children)
        if item_count == 0:
            method_list.index = None
            return
        current_index = method_list.index if method_list.index is not None else 0
        method_list.index = (current_index + offset) % item_count


class LoginMethodListView(ListView):
    """List view with wrapping arrow navigation for the login method picker."""

    def action_cursor_up(self) -> None:
        """Move to the previous login method."""
        self._move_cursor(offset=-1)

    def action_cursor_down(self) -> None:
        """Move to the next login method."""
        self._move_cursor(offset=1)

    def _move_cursor(self, *, offset: int) -> None:
        item_count = len(self.children)
        if item_count == 0:
            self.index = None
            return
        current_index = self.index if self.index is not None else 0
        self.index = (current_index + offset) % item_count


class ThemePickerScreen(ModalScreen[TuiThemeName | None]):
    """Theme picker for the built-in TUI themes."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_cursor", "Select", show=False, priority=True),
    ]

    def __init__(self, *, current_theme: TuiThemeName, theme: TuiTheme) -> None:
        super().__init__()
        self.current_theme = current_theme
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the theme picker."""
        with Vertical(id="theme-picker"):
            yield Static("Theme", id="theme-picker-title")
            yield ListView(
                *[
                    ListItem(
                        Label(
                            _theme_picker_label(theme_name, current_theme=self.current_theme),
                            markup=False,
                        )
                    )
                    for theme_name in BUILTIN_TUI_THEME_NAMES
                ],
                id="theme-picker-list",
            )
            yield Static("Enter selects - Escape closes", id="theme-picker-help")

    def on_mount(self) -> None:
        """Select the current theme."""
        theme_list = self.query_one("#theme-picker-list", ListView)
        try:
            theme_list.index = BUILTIN_TUI_THEME_NAMES.index(self.current_theme)
        except ValueError:
            theme_list.index = 0
        theme_list.focus()

    def on_key(self, event: Key) -> None:
        """Route theme picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected theme name."""
        self.dismiss(BUILTIN_TUI_THEME_NAMES[event.index])

    def action_cursor_up(self) -> None:
        """Move to the previous theme."""
        self.query_one("#theme-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next theme."""
        self.query_one("#theme-picker-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted theme."""
        self.query_one("#theme-picker-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close without selecting a theme."""
        self.dismiss(None)


class ModelPickerSearchInput(Input):
    """Search input that keeps model-picker control keys local to the picker."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("tab", "toggle_mode", "Mode", show=False, priority=True),
        Binding("ctrl+i", "toggle_mode", "Mode", show=False, priority=True),
        Binding("ctrl+a", "enable_all_scoped", "Enable all", show=False, priority=True),
        Binding("ctrl+x", "clear_scoped", "Clear", show=False, priority=True),
        Binding("ctrl+p", "toggle_scoped_provider", "Provider", show=False, priority=True),
        Binding("alt+up", "reorder_scoped_up", "Move up", show=False, priority=True),
        Binding("alt+down", "reorder_scoped_down", "Move down", show=False, priority=True),
        Binding("ctrl+c", "clear_or_cancel", "Clear", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
    ]

    def _picker(self) -> ModelPickerScreen:
        return cast(ModelPickerScreen, self.screen)

    def on_key(self, event: Key) -> None:
        """Route picker control keys before the input edits its text."""
        if event.key == "up":
            event.stop()
            event.prevent_default()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            event.prevent_default()
            self.action_cursor_down()
        elif event.key in {"tab", "ctrl+i"}:
            event.stop()
            event.prevent_default()
            self.action_toggle_mode()
        elif event.key == "ctrl+a":
            event.stop()
            event.prevent_default()
            self.action_enable_all_scoped()
        elif event.key == "ctrl+x":
            event.stop()
            event.prevent_default()
            self.action_clear_scoped()
        elif event.key == "ctrl+p":
            event.stop()
            event.prevent_default()
            self.action_toggle_scoped_provider()
        elif event.key == "alt+up":
            event.stop()
            event.prevent_default()
            self.action_reorder_scoped_up()
        elif event.key == "alt+down":
            event.stop()
            event.prevent_default()
            self.action_reorder_scoped_down()
        elif event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            self.action_clear_or_cancel()
        elif event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()

    def action_cursor_up(self) -> None:
        """Move the model picker selection up."""
        self._picker().action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move the model picker selection down."""
        self._picker().action_cursor_down()

    def action_toggle_mode(self) -> None:
        """Toggle between all and scoped picker modes."""
        self._picker().action_toggle_mode()

    def action_enable_all_scoped(self) -> None:
        """Enable all visible scoped model choices."""
        self._picker().action_enable_all_scoped()

    def action_clear_scoped(self) -> None:
        """Clear all visible scoped model choices."""
        self._picker().action_clear_scoped()

    def action_toggle_scoped_provider(self) -> None:
        """Toggle scoped models for the highlighted provider."""
        self._picker().action_toggle_scoped_provider()

    def action_reorder_scoped_up(self) -> None:
        """Move the highlighted scoped model earlier in cycle order."""
        self._picker().action_reorder_scoped_up()

    def action_reorder_scoped_down(self) -> None:
        """Move the highlighted scoped model later in cycle order."""
        self._picker().action_reorder_scoped_down()

    def action_clear_or_cancel(self) -> None:
        """Clear the model search, or close the picker if search is already empty."""
        self._picker().action_clear_search_or_cancel()

    def action_cancel(self) -> None:
        """Close the model picker."""
        self._picker().action_cancel()


class ModelPickerScreen(ModalScreen[ModelChoice | None]):
    """Model picker for the active TUI provider."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab", "toggle_mode", "Mode", show=False, priority=True),
        Binding("ctrl+i", "toggle_mode", "Mode", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("enter", "accept_model", "Select", show=False),
        Binding("ctrl+a", "enable_all_scoped", "Enable all", show=False),
        Binding("ctrl+x", "clear_scoped", "Clear", show=False),
        Binding("ctrl+p", "toggle_scoped_provider", "Provider", show=False),
        Binding("alt+up", "reorder_scoped_up", "Move up", show=False),
        Binding("alt+down", "reorder_scoped_down", "Move down", show=False),
        Binding("ctrl+c", "clear_search_or_cancel", "Clear", show=False),
    ]

    def __init__(
        self,
        choices: Sequence[ModelChoice],
        *,
        scoped_choices: Sequence[ModelChoice],
        current_model: str,
        provider_name: str,
        theme: TuiTheme,
        on_toggle_scoped: Callable[[ModelChoice], Sequence[ModelChoice]] | None = None,
        on_set_scoped: Callable[[Sequence[ModelChoice]], Sequence[ModelChoice]] | None = None,
        picker_kind: Literal["model", "scoped"] = "model",
    ) -> None:
        super().__init__()
        self.choices = tuple(dict.fromkeys(choices))
        self.scoped_choices = tuple(dict.fromkeys(scoped_choices))
        self.visible_choices = self.choices
        self.current_model = current_model
        self.provider_name = provider_name
        self.theme = theme
        self.on_toggle_scoped = on_toggle_scoped
        self.on_set_scoped = on_set_scoped
        self.picker_kind = picker_kind
        self.mode: Literal["all", "scoped"] = (
            "scoped" if picker_kind == "model" and self.scoped_choices else "all"
        )
        self.search_value = ""

    def compose(self) -> ComposeResult:
        """Compose the model picker."""
        with Vertical(id="model-picker"):
            title = (
                f"Model: {self.provider_name}" if self.picker_kind == "model" else "Scoped models"
            )
            yield Static(title, id="model-picker-title")
            yield Static("", id="model-picker-tabs")
            yield ModelPickerSearchInput(placeholder="Search models", id="model-picker-search")
            yield ListView(
                *[
                    ListItem(
                        Label(
                            _model_picker_label(
                                choice,
                                current_model=self.current_model,
                                current_provider=self.provider_name,
                                scoped=choice in self.scoped_choices,
                            ),
                            markup=False,
                        )
                    )
                    for choice in self.choices
                ],
                id="model-picker-list",
            )
            yield Static("", id="model-picker-help")

    def on_mount(self) -> None:
        """Focus the search field."""
        search = self.query_one("#model-picker-search", Input)
        search.focus()
        self._refresh_model_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter model choices as the search value changes."""
        if event.input.id != "model-picker-search":
            return
        event.stop()
        self.search_value = event.value
        self._refresh_model_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Select the highlighted model from the search field."""
        if event.input.id != "model-picker-search":
            return
        event.stop()
        self._select_visible_choice()

    def _reset_model_list_index(self) -> None:
        """Move selection to the current model or first visible row."""
        model_list = self.query_one("#model-picker-list", ListView)
        if not self.visible_choices:
            model_list.index = None
            return
        try:
            model_list.index = self.visible_choices.index(
                ModelChoice(provider_name=self.provider_name, model=self.current_model)
            )
        except ValueError:
            model_list.index = 0

    def on_key(self, event: Key) -> None:
        """Route model picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "pageup":
            event.stop()
            self.action_page_up()
        elif event.key == "pagedown":
            event.stop()
            self.action_page_down()
        elif event.key == "enter":
            event.stop()
            self.action_accept_model()
        elif event.key == "ctrl+a":
            event.stop()
            self.action_enable_all_scoped()
        elif event.key == "ctrl+x":
            event.stop()
            self.action_clear_scoped()
        elif event.key == "ctrl+p":
            event.stop()
            self.action_toggle_scoped_provider()
        elif event.key == "alt+up":
            event.stop()
            self.action_reorder_scoped_up()
        elif event.key == "alt+down":
            event.stop()
            self.action_reorder_scoped_down()
        elif event.key == "ctrl+c":
            event.stop()
            self.action_clear_search_or_cancel()
        elif event.key in {"tab", "ctrl+i"}:
            event.stop()
            self.action_toggle_mode()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle the selected row."""
        event.stop()
        self._select_visible_choice()

    def action_cursor_up(self) -> None:
        """Move to the previous model."""
        self._move_model_cursor(-1)

    def action_cursor_down(self) -> None:
        """Move to the next model."""
        self._move_model_cursor(1)

    def _move_model_cursor(self, direction: Literal[-1, 1]) -> None:
        """Move selected model, wrapping at list boundaries like Pi."""
        if not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        current_index = model_list.index if model_list.index is not None else 0
        model_list.index = (current_index + direction) % len(self.visible_choices)

    def action_page_up(self) -> None:
        """Move up by a page of models."""
        self._move_model_page(-1)

    def action_page_down(self) -> None:
        """Move down by a page of models."""
        self._move_model_page(1)

    def _move_model_page(self, direction: Literal[-1, 1]) -> None:
        """Move the selected model by a viewport-sized page."""
        if not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        current_index = model_list.index if model_list.index is not None else 0
        page_size = max(1, model_list.size.height - 1)
        if page_size <= 1:
            page_size = 10
        next_index = current_index + (direction * page_size)
        model_list.index = max(0, min(len(self.visible_choices) - 1, next_index))

    def action_accept_model(self) -> None:
        """Select the highlighted model."""
        self._select_visible_choice()

    def action_toggle_mode(self) -> None:
        """Toggle between all models and scoped models."""
        if self.picker_kind != "model":
            return
        self.mode = "scoped" if self.mode == "all" else "all"
        self._refresh_model_list()

    def action_toggle_scoped(self) -> None:
        """Add or remove the highlighted model from scoped models."""
        if self.on_toggle_scoped is None or not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        index = model_list.index
        if index is None:
            return
        choice = self.visible_choices[index]
        self.scoped_choices = tuple(dict.fromkeys(self.on_toggle_scoped(choice)))
        self._refresh_model_list()

    def action_enable_all_scoped(self) -> None:
        """Add all visible target models to scoped models."""
        if self.picker_kind != "scoped" or self.on_set_scoped is None:
            return
        target_choices = self.visible_choices if self.search_value else self.choices
        self.scoped_choices = tuple(
            dict.fromkeys(self.on_set_scoped((*self.scoped_choices, *target_choices)))
        )
        self._refresh_model_list()

    def action_clear_scoped(self) -> None:
        """Remove all visible target models from scoped models."""
        if self.picker_kind != "scoped" or self.on_set_scoped is None:
            return
        target_choices = set(self.visible_choices if self.search_value else self.choices)
        self.scoped_choices = tuple(
            dict.fromkeys(
                self.on_set_scoped(
                    tuple(choice for choice in self.scoped_choices if choice not in target_choices)
                )
            )
        )
        self._refresh_model_list()

    def action_toggle_scoped_provider(self) -> None:
        """Toggle all scoped models for the highlighted provider."""
        if self.picker_kind != "scoped" or self.on_set_scoped is None or not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        index = model_list.index
        if index is None:
            return
        provider_name = self.visible_choices[index].provider_name
        provider_choices = tuple(
            choice for choice in self.choices if choice.provider_name == provider_name
        )
        scoped = list(self.scoped_choices)
        if provider_choices and all(choice in scoped for choice in provider_choices):
            scoped = [choice for choice in scoped if choice.provider_name != provider_name]
        else:
            scoped = list(dict.fromkeys((*scoped, *provider_choices)))
        self.scoped_choices = tuple(dict.fromkeys(self.on_set_scoped(tuple(scoped))))
        self._refresh_model_list()

    def action_reorder_scoped_up(self) -> None:
        """Move the highlighted scoped model earlier in cycle order."""
        self._reorder_highlighted_scoped_choice(-1)

    def action_reorder_scoped_down(self) -> None:
        """Move the highlighted scoped model later in cycle order."""
        self._reorder_highlighted_scoped_choice(1)

    def _reorder_highlighted_scoped_choice(self, delta: Literal[-1, 1]) -> None:
        """Move the highlighted scoped model in the persisted scoped order."""
        if self.picker_kind != "scoped" or self.on_set_scoped is None or not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        index = model_list.index
        if index is None:
            return
        choice = self.visible_choices[index]
        scoped = list(self.scoped_choices)
        try:
            scoped_index = scoped.index(choice)
        except ValueError:
            return
        next_index = scoped_index + delta
        if next_index < 0 or next_index >= len(scoped):
            return
        scoped[scoped_index], scoped[next_index] = scoped[next_index], scoped[scoped_index]
        self.scoped_choices = tuple(dict.fromkeys(self.on_set_scoped(tuple(scoped))))
        self._refresh_model_list()
        if choice in self.visible_choices:
            model_list.index = self.visible_choices.index(choice)

    def action_cancel(self) -> None:
        """Close without selecting a model."""
        self.dismiss(None)

    def action_clear_search_or_cancel(self) -> None:
        """Clear search text, or cancel when the picker search is already empty."""
        if self.search_value:
            self.search_value = ""
            search = self.query_one("#model-picker-search", Input)
            search.value = ""
            self._refresh_model_list()
            return
        self.action_cancel()

    def _select_visible_choice(self) -> None:
        if not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        index = model_list.index
        if index is None:
            return
        choice = self.visible_choices[index]
        if self.picker_kind == "scoped":
            self.action_toggle_scoped()
            return
        self.dismiss(choice)

    def _refresh_model_list(self) -> None:
        base_choices = self.scoped_choices if self.mode == "scoped" else self.choices
        self.visible_choices = _filter_model_choices(base_choices, self.search_value)
        model_list = self.query_one("#model-picker-list", ListView)
        model_list.clear()
        model_list.extend(
            [
                ListItem(
                    Label(
                        _model_picker_label(
                            choice,
                            current_model=self.current_model,
                            current_provider=self.provider_name,
                            scoped=choice in self.scoped_choices,
                        ),
                        markup=False,
                    )
                )
                for choice in self.visible_choices
            ]
        )
        self._reset_model_list_index()
        scope_count = len(self.scoped_choices)
        tabs = self.query_one("#model-picker-tabs", Static)
        if self.picker_kind == "scoped":
            tabs.update("Scoped models setup — Enter toggles membership; active model is unchanged")
            help_text = (
                "No matching models - Ctrl+A/Ctrl+X apply to matching models"
                if not self.visible_choices
                else (
                    "Enter toggles - Ctrl+A all - Ctrl+X clear - "
                    f"Ctrl+P provider - Alt+Up/Down reorder - {scope_count} scoped"
                )
            )
        elif self.mode == "all":
            tabs.update("Tabs: ● All models  ○ Scoped models")
            help_text = (
                "all models: no matching models - Tab switches to scoped models"
                if not self.visible_choices
                else (
                    "All models - Enter selects active model - Tab switches tabs - "
                    f"{scope_count} scoped"
                )
            )
        else:
            tabs.update("Tabs: ○ All models  ● Scoped models")
            help_text = (
                "scoped models: no matching models - Tab switches to all models"
                if not self.visible_choices
                else "Scoped models - Enter selects active model - Tab switches tabs"
            )
        self.query_one("#model-picker-help", Static).update(help_text)


class LoginScreen(ModalScreen[str | None]):
    """Password prompt for saving a provider API key."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, provider: ProviderCatalogEntry, *, theme: TuiTheme) -> None:
        super().__init__()
        self.provider = provider
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the provider login prompt."""
        with Vertical(id="login-screen"):
            yield Static(f"Login: {self.provider.display_name}", id="login-title")
            yield Static("Paste this provider's API key.", id="login-help")
            yield Input(placeholder="Paste API key", password=True, id="login-api-key")
            yield Static("Enter saves - Escape closes", id="login-footer")

    def on_mount(self) -> None:
        """Focus the API key field."""
        self.query_one("#login-api-key", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dismiss with the submitted API key."""
        if event.input.id != "login-api-key":
            return
        event.stop()
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        """Close without saving."""
        self.dismiss(None)


class OAuthLoginScreen(ModalScreen[OAuthCredential | None]):
    """OAuth login flow for providers backed by subscription auth."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, provider: ProviderCatalogEntry, *, theme: TuiTheme) -> None:
        super().__init__()
        self.provider = provider
        self.theme = theme
        self._manual_code_future: asyncio.Future[str] | None = None
        self._manual_code_value: str | None = None

    def compose(self) -> ComposeResult:
        """Compose the OAuth login prompt."""
        with Vertical(id="login-screen"):
            yield Static(f"Login: {self.provider.display_name}", id="login-title")
            yield Static("Complete the browser login, or paste the redirect URL.", id="login-help")
            yield Static("", id="login-oauth-url")
            yield Input(
                placeholder="Paste redirect URL or authorization code",
                id="login-oauth-code",
            )
            yield Static("Enter submits - Escape closes", id="login-footer")

    def on_mount(self) -> None:
        """Focus the manual-code field and start OAuth."""
        self.query_one("#login-oauth-code", Input).focus()
        self.run_worker(self._run_login(), exclusive=True)

    async def _run_login(self) -> None:
        try:
            credential = await login_openai_codex(
                on_auth=self._show_auth,
                on_prompt=self._prompt_for_code,
                on_manual_code_input=self._manual_code_input,
            )
        except Exception as exc:  # noqa: BLE001 - surface OAuth failures in the TUI
            self.query_one("#login-help", Static).update(f"OAuth failed: {exc}")
            return
        self.dismiss(credential)

    def _show_auth(self, info: OAuthAuthInfo) -> None:
        self.query_one("#login-oauth-url", Static).update(info.url)
        if info.instructions:
            self.query_one("#login-help", Static).update(info.instructions)

    async def _prompt_for_code(self, prompt: OAuthPrompt) -> str:
        self.query_one("#login-help", Static).update(prompt.message)
        return await self._manual_code_input()

    async def _manual_code_input(self) -> str:
        if self._manual_code_value is not None:
            return self._manual_code_value
        loop = asyncio.get_running_loop()
        self._manual_code_future = loop.create_future()
        try:
            return await self._manual_code_future
        finally:
            self._manual_code_future = None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Resolve the manual OAuth code fallback."""
        if event.input.id != "login-oauth-code":
            return
        event.stop()
        value = event.value.strip()
        if not value:
            return
        self._manual_code_value = value
        if self._manual_code_future is not None and not self._manual_code_future.done():
            self._manual_code_future.set_result(value)

    def action_cancel(self) -> None:
        """Close without saving OAuth credentials."""
        if self._manual_code_future is not None and not self._manual_code_future.done():
            self._manual_code_future.cancel()
        self.dismiss(None)


class TauTuiApp(App[None]):
    """Interactive Textual frontend for a ``CodingSession``."""

    TITLE = "Tau"
    CSS = """
    Screen {
        layout: vertical;
        background: $tau-screen-background;
        color: $tau-screen-text;
    }

    Header {
        background: $tau-chrome-background;
        color: $tau-muted-text;
        dock: top;
    }

    Footer {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
    }

    Footer FooterKey {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
    }

    Footer FooterKey .footer-key--key {
        background: $tau-chrome-background;
        color: $tau-accent;
    }

    Footer FooterKey .footer-key--description,
    Footer FooterLabel {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
    }

    Toast {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
    }

    Toast .toast--title {
        color: $tau-accent;
    }

    #workspace {
        height: 1fr;
    }

    #sidebar {
        width: 32;
        min-width: 28;
        height: 1fr;
        padding: 1 1 0 0;
        background: $tau-sidebar-background;
        border-right: tall $tau-border;
    }

    TauTuiApp.-hide-sidebar #sidebar {
        display: none;
    }

    TauTuiApp.-hide-sidebar #main-pane {
        padding-left: 1;
    }

    #main-pane {
        width: 1fr;
        padding: 1 1 0 1;
    }

    #transcript {
        height: 1fr;
        border: none;
        background: $tau-transcript-background;
        padding: 0 0 0 2;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }

    #queued-messages {
        height: auto;
        max-height: 8;
        margin: 0 1 1 1;
        padding: 0 1;
        background: $tau-screen-background;
        color: $tau-muted-text;
    }

    #prompt-row {
        height: auto;
        margin: 0 1 1 1;
    }

    #prompt-prefix {
        width: 2;
        height: 3;
        padding: 0 0 0 0;
        margin: 0;
        content-align: center middle;
        color: $tau-accent;
        text-style: bold;
    }

    #prompt {
        width: 1fr;
        height: auto;
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall transparent;
        margin: 0;
        padding: 0 1;
        max-height: 8;
    }

    #prompt:focus {
        border: tall $tau-prompt-border;
    }

    #prompt.-shell-mode {
        border: tall $tau-accent;
    }

    #compact-session-info {
        height: auto;
        max-height: 3;
        margin: 0 1 1 1;
        padding: 0 1;
        color: $tau-muted-text;
    }

    #autocomplete {
        height: auto;
        max-height: 18;
        margin: 0 1 1 1;
        padding: 0 1;
        background: $tau-autocomplete-background;
        color: $tau-screen-text;
        border: tall $tau-border;
        overflow-y: auto;
    }

    SessionPickerScreen,
    SettingsPickerScreen,
    TreePickerScreen,
    UserMessagePickerScreen,
    TreeLabelInputScreen,
    CommandOutputScreen {
        align: center middle;
    }

    #session-picker,
    #settings-picker,
    #tree-picker,
    #user-message-picker,
    #tree-label-input {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $tau-chrome-background;
        border: tall $tau-border;
    }

    #session-picker-title,
    #settings-picker-title,
    #tree-picker-title,
    #user-message-picker-title,
    #tree-label-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #user-message-picker-description {
        height: auto;
        max-height: 2;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #tree-picker-search {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #session-picker-list,
    #settings-picker-list,
    #tree-picker-list,
    #user-message-picker-list {
        height: auto;
        max-height: 16;
        background: $tau-transcript-background;
        border: tall $tau-border;
    }

    ListView > ListItem.--highlight {
        background: $tau-highlight-background;
        color: $tau-highlight-text;
    }

    ListView > ListItem.--highlight Label {
        background: $tau-highlight-background;
        color: $tau-highlight-text;
    }

    #session-picker-help,
    #settings-picker-help,
    #tree-picker-help,
    #user-message-picker-help,
    #tree-label-help {
        height: 1;
        margin-top: 1;
        color: $tau-muted-text;
    }

    #session-picker-search,
    #tree-label-value {
        height: 3;
        margin-bottom: 1;
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall $tau-prompt-border;
    }

    #command-output {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $tau-chrome-background;
        color: $tau-chrome-text;
        border: tall $tau-border;
    }

    #command-output-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #command-output-scroll {
        height: auto;
        max-height: 18;
        background: $tau-transcript-background;
        border: tall $tau-border;
    }

    #command-output-body {
        color: $tau-screen-text;
        padding: 1;
    }

    #command-output-help {
        height: 1;
        margin-top: 1;
        color: $tau-muted-text;
    }

    LoginMethodPickerScreen,
    LoginProviderPickerScreen,
    ThemePickerScreen,
    ModelPickerScreen {
        align: center middle;
    }

    #login-method-picker,
    #login-provider-picker,
    #theme-picker,
    #model-picker {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $tau-chrome-background;
        color: $tau-chrome-text;
        border: tall $tau-border;
    }

    #login-method-title,
    #login-provider-title,
    #theme-picker-title,
    #model-picker-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #model-picker-tabs {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-method-list,
    #login-provider-list,
    #theme-picker-list,
    #model-picker-list {
        height: auto;
        max-height: 12;
        background: $tau-transcript-background;
        color: $tau-screen-text;
        border: tall $tau-border;
    }

    #login-method-list ListItem Label,
    #login-provider-list ListItem Label,
    #theme-picker-list ListItem Label,
    #model-picker-list ListItem Label {
        color: $tau-screen-text;
    }

    #login-method-intro {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-method-list {
        max-height: 6;
    }

    #model-picker-search {
        height: 3;
        margin-bottom: 1;
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall $tau-prompt-border;
    }

    #login-method-help,
    #login-provider-help,
    #theme-picker-help,
    #model-picker-help {
        height: 1;
        margin-top: 1;
        color: $tau-muted-text;
    }

    LoginScreen,
    OAuthLoginScreen {
        align: center middle;
    }

    #login-screen {
        width: 72;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        background: $tau-chrome-background;
        border: tall $tau-border;
    }

    #login-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #login-help {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-api-key,
    #login-oauth-code {
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall $tau-prompt-border;
        margin-bottom: 1;
    }

    #login-oauth-url {
        min-height: 1;
        max-height: 4;
        color: $tau-chrome-text;
        margin-bottom: 1;
    }

    #login-footer {
        height: 1;
        color: $tau-muted-text;
    }
    """
    BINDINGS: ClassVar[list[BindingEntry]] = []

    def __init__(
        self,
        session: CodingSession,
        *,
        tui_settings: TuiSettings | None = None,
        startup_message: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        self.tui_settings = tui_settings or TuiSettings()
        self.startup_message = startup_message
        self.initial_prompt = initial_prompt
        super().__init__()
        self._bindings = BindingsMap(_app_bindings(self.tui_settings.keybindings))
        self.session = session
        self.state = TuiState(skills=session.skills)
        self.state.load_messages(session.messages)
        self.adapter = TuiEventAdapter(self.state)
        self._prompt_worker: Worker[None] | None = None
        self._compaction_worker: Worker[None] | None = None
        self._terminal_worker: Worker[None] | None = None
        self._prompt_run_id = 0
        self._completion_state = CompletionState()
        self._activity_frame = 0
        self._activity_timer: Timer | None = None
        self._active_notification_keys: set[tuple[str, str]] = set()
        self._supports_pyperclip: bool | None = None
        self._last_empty_escape_at: float | None = None
        self._pty_proof_enabled = os.environ.get("TAU_TUI_PTY_PROOF") == "1"
        self._pty_proof_run_id = os.environ.get("TAU_TUI_PTY_RUN_ID", "tau-real-tui")

    def copy_to_clipboard(self, text: str) -> None:
        """Copy text using pyperclip when available, then Textual's fallback."""
        if self._supports_pyperclip is None:
            try:
                import pyperclip  # type: ignore[import-untyped]
            except ImportError:
                self._supports_pyperclip = False
            else:
                self._supports_pyperclip = True
        if self._supports_pyperclip:
            import pyperclip

            with suppress(Exception):
                pyperclip.copy(text)
        super().copy_to_clipboard(text)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Return Tau-specific CSS variables for the selected TUI theme."""
        variables = super().get_theme_variable_defaults()
        return {**variables, **_theme_css_variables(self.tui_settings.resolved_theme)}

    def compose(self) -> ComposeResult:
        """Compose the TUI widgets."""
        yield Header()
        with Horizontal(id="workspace"):
            yield SessionSidebar(id="sidebar")
            with Vertical(id="main-pane"):
                if self._pty_proof_enabled:
                    yield Static(pty_ready_line(self._pty_proof_run_id), id="pty-proof-ready")
                yield TranscriptView(
                    id="transcript",
                    min_width=1,
                    wrap=True,
                    highlight=True,
                    markup=False,
                )
                yield Static("", id="queued-messages")
                with Horizontal(id="prompt-row"):
                    yield Static("τ", id="prompt-prefix")
                    yield PromptInput(
                        placeholder="Ask Tau…  Enter submits, Shift+Enter inserts a newline",
                        id="prompt",
                        tui_keybindings=self.tui_settings.keybindings,
                    )
                yield CompactSessionInfo(id="compact-session-info")
                yield Static("", id="autocomplete")
        yield Footer()

    async def on_mount(self) -> None:
        """Focus the prompt when the app starts."""
        prompt = self.query_one(PromptInput)
        prompt.shell_mode_style = self.tui_settings.resolved_theme.accent
        self._sync_prompt_shell_mode(prompt.text)
        prompt.focus()
        self._update_responsive_layout(self.size.width, self.size.height)
        self._refresh()
        self._refresh_completions()
        if self.startup_message:
            self._notify(self.startup_message, severity="warning")
        if self.initial_prompt and self.initial_prompt.strip():
            self._submit_prompt(self.initial_prompt.strip())

    def on_unmount(self) -> None:
        """Stop the activity timer when the app is torn down."""
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None

    def on_resize(self, event: Resize) -> None:
        """Update responsive chrome when the terminal changes size."""
        self._update_responsive_layout(event.size.width, event.size.height)

    def on_click(self, event: events.Click) -> None:
        """Return keyboard focus to the prompt after clicks in the main TUI."""
        if event.button != 1:
            return
        with suppress(NoMatches):
            self.screen.query_one("#prompt", PromptInput).focus()

    @on(events.TextSelected)
    async def on_text_selected(self) -> None:
        """Optionally copy selected transcript text automatically."""
        if not self.tui_settings.auto_copy_selection:
            return
        selection = self.screen.get_selected_text()
        if selection:
            self.copy_to_clipboard(selection)
            self._notify("Copied selection to clipboard.")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Update prompt autocomplete when the prompt text changes."""
        if event.text_area.id != "prompt":
            return
        if self._pty_proof_enabled and "TAU_TUI_PTY_BROWSER_INPUT" in event.text_area.text:
            with suppress(NoMatches):
                self.query_one("#pty-proof-ready", Static).update(
                    pty_input_received_line(self._pty_proof_run_id, event.text_area.text)
                )
        self._sync_prompt_shell_mode(event.text_area.text)
        self._completion_state = self._build_completion_state(event.text_area.text)
        self._refresh_completions()

    async def action_submit_prompt(self) -> None:
        """Submit the current prompt text or slash command."""
        await self._submit_prompt_from_editor(streaming_behavior="steer")

    async def action_submit_follow_up(self) -> None:
        """Submit the current prompt as a queued follow-up while running."""
        await self._submit_prompt_from_editor(streaming_behavior="follow_up")

    async def _submit_prompt_from_editor(
        self,
        *,
        streaming_behavior: Literal["steer", "follow_up"],
    ) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        raw_text = prompt.text
        applied_completion = self._apply_selected_completion(raw_text)
        if applied_completion is not None and applied_completion != raw_text:
            prompt.text = applied_completion
            prompt.move_cursor(_text_end_location(applied_completion))
            self._completion_state = self._build_completion_state(applied_completion)
            self._refresh_completions()
            return

        text = raw_text.strip()
        if not text:
            prompt.text = ""
            self._completion_state = CompletionState()
            self._refresh_completions()
            return

        if self._is_compaction_active():
            if text.startswith("/compact"):
                self._notify("A compaction is already running.", severity="warning")
            else:
                prompt.text = raw_text
                prompt.move_cursor(_text_end_location(raw_text))
                self._notify(
                    "Compaction is still running. You can keep editing, but wait to submit.",
                    severity="warning",
                )
            return

        if self._is_terminal_command_active():
            prompt.text = raw_text
            prompt.move_cursor(_text_end_location(raw_text))
            self._notify(
                "A terminal command is already running. Press Escape to cancel it first.",
                severity="warning",
            )
            return

        prompt.text = ""
        self._completion_state = CompletionState()
        self._refresh_completions()

        terminal_command = parse_terminal_command(text)
        if terminal_command is not None:
            self._terminal_worker = self.run_worker(
                self._run_terminal_command(
                    terminal_command.command,
                    add_to_context=terminal_command.add_to_context,
                ),
                exclusive=True,
            )
            return

        command = self.session.handle_command(text)
        if command.handled:
            if command.clear_requested:
                self.state.clear()
            if command.new_session_requested:
                await self._new_session()
            if command.clone_session_requested:
                await self._clone_session()
            if command.compact_summary is not None:
                if self._is_compaction_active():
                    self._notify("A compaction is already running.", severity="warning")
                elif self._is_agent_or_queue_active():
                    prompt.text = raw_text
                    prompt.move_cursor(_text_end_location(raw_text))
                    self._notify(
                        "Wait for the current agent turn and queued messages to finish before compacting.",
                        severity="warning",
                    )
                    return
                else:
                    self._compaction_worker = self.run_worker(
                        self._run_compaction(command.compact_summary),
                        exclusive=False,
                    )
            if command.copy_last_message_requested:
                self.action_copy_last_message()
            if command.export_requested:
                try:
                    exported_path = await self.session.export(
                        command.export_destination,
                        format=command.export_format,
                    )
                    self._notify(f"Exported session to {exported_path}")
                except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
                    self._notify(f"Could not export session: {exc}", severity="error")
            if command.import_requested and command.import_path is not None:
                await self._import_session(command.import_path)
            if command.share_requested:
                await self._share_session()
            if command.resume_session_id is not None:
                await self._resume_session(command.resume_session_id)
            if command.resume_picker_requested:
                self.action_open_session_picker()
            if command.tree_picker_requested:
                await self._open_tree_picker()
            if command.fork_picker_requested:
                await self._open_fork_picker()
            if command.login_picker_requested:
                self._open_login_picker()
            if command.login_provider is not None:
                self._open_login(command.login_provider)
            if command.logout_picker_requested:
                self._open_logout_picker()
            if command.logout_provider is not None:
                self._logout(command.logout_provider)
            if command.model_picker_requested:
                self._open_model_picker()
            if command.scoped_models_picker_requested:
                self._open_scoped_models_picker()
            if command.settings_picker_requested:
                self._open_settings_picker()
            if command.trust_picker_requested:
                self._open_trust_picker()
            if command.theme_picker_requested:
                self._open_theme_picker()
            if command.thinking_level is not None:
                await self._set_thinking_level(command.thinking_level)
            if command.theme is not None:
                self._set_tui_theme(cast(TuiThemeName, command.theme))
            self.state.set_skills(self.session.skills)
            if command.message:
                if _command_message_uses_notification(text, command.message):
                    self._notify(command.message)
                elif _command_message_uses_transcript(text):
                    self._append_command_message(text, command.message)
                else:
                    self._show_command_message(text, command.message)
            self._refresh()
            if command.exit_requested:
                self.exit()
            return

        if self.state.running:
            await self._queue_prompt(text, streaming_behavior=streaming_behavior)
            return

        self._submit_prompt(text)

    def _is_compaction_active(self) -> bool:
        """Return whether a manual compaction worker is still running."""
        worker = self._compaction_worker
        return worker is not None and not worker.is_finished and not worker.is_cancelled

    def _is_terminal_command_active(self) -> bool:
        """Return whether an input-bar terminal command is still running."""
        worker = self._terminal_worker
        return worker is not None and not worker.is_finished and not worker.is_cancelled

    def _is_agent_or_queue_active(self) -> bool:
        """Return whether compaction would race an active or queued agent turn."""
        self._sync_queue_state()
        worker = self._prompt_worker
        is_worker_active = worker is not None and not worker.is_finished and not worker.is_cancelled
        is_session_running = bool(getattr(self.session, "is_running", False))
        return self.state.running or is_session_running or is_worker_active or self.state.queued_message_count > 0

    async def _run_compaction(self, summary: str) -> None:
        """Run manual compaction without disabling prompt editing."""
        self.state.clear()
        self.state.add_item("status", "Compacting session…")
        self._refresh()
        try:
            compact_message = await self.session.compact(summary)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
            return
        finally:
            self._compaction_worker = None
        self.state.clear()
        self.state.set_skills(self.session.skills)
        self.state.load_messages(self.session.messages)
        self._notify(compact_message)
        self._refresh()

    def _submit_prompt(self, text: str) -> None:
        """Add a prompt to the transcript and start the agent worker."""
        self._prompt_run_id += 1
        run_id = self._prompt_run_id
        self._follow_transcript_output()
        self._refresh()
        self._prompt_worker = self.run_worker(self._run_prompt(text, run_id), exclusive=True)

    def _follow_transcript_output(self) -> None:
        """Put the transcript back in follow mode for explicit user actions."""
        if not self.screen_stack:
            return
        with suppress(NoMatches):
            self.query_one("#transcript", TranscriptView).follow_output()

    async def _run_terminal_command(self, command: str, *, add_to_context: bool) -> None:
        run_terminal_command = getattr(self.session, "run_terminal_command", None)
        if not callable(run_terminal_command):
            self._notify("Terminal commands are not available.", severity="error")
            self._terminal_worker = None
            return

        item_index = len(self.state.items)
        self.state.add_item(
            "tool",
            f"$ {command.strip()}",
            always_show_tool_result=True,
        )
        self._follow_transcript_output()
        self._refresh()

        try:
            result = await run_terminal_command(command, add_to_context=add_to_context)
        except asyncio.CancelledError:
            self._terminal_worker = None
            self._refresh()
            return
        except Exception as exc:  # noqa: BLE001 - surface command execution failures in the TUI
            if item_index < len(self.state.items):
                item = self.state.items[item_index]
                item.tool_result_text = format_terminal_command_result_block(
                    ok=False,
                    added_to_context=add_to_context,
                    output=str(exc),
                )
            self._notify(f"Could not run command: {exc}", severity="error")
            self._refresh()
            self._terminal_worker = None
            return

        if item_index >= len(self.state.items):
            self._terminal_worker = None
            return
        item = self.state.items[item_index]
        item.text = f"$ {result.command}"
        item.tool_result_text = format_terminal_command_result_block(
            ok=result.ok,
            added_to_context=result.added_to_context,
            output=result.output,
        )
        self._follow_transcript_output()
        self._refresh()
        self._terminal_worker = None

    def _set_tui_theme(self, theme: TuiThemeName) -> None:
        self._set_tui_settings(
            TuiSettings(
                keybindings=self.tui_settings.keybindings,
                theme=theme,
                auto_copy_selection=self.tui_settings.auto_copy_selection,
                double_escape_action=self.tui_settings.double_escape_action,
                tree_filter_mode=self.tui_settings.tree_filter_mode,
            )
        )

    def _set_tui_settings(self, settings: TuiSettings) -> None:
        self.tui_settings = settings
        save_tui_settings(self.tui_settings)
        self._bindings = BindingsMap(_app_bindings(self.tui_settings.keybindings))
        with suppress(NoMatches):
            prompt = self.query_one("#prompt", PromptInput)
            prompt.tui_keybindings = self.tui_settings.keybindings
            prompt.shell_mode_style = self.tui_settings.resolved_theme.accent
            prompt._apply_prompt_bindings()
            prompt.refresh_bindings()
        self.refresh_bindings()
        self.refresh_css(animate=False)
        self._refresh()

    def _settings_picker_settings_changed(self, settings: TuiSettings) -> None:
        self._set_tui_settings(settings)

    def _open_settings_picker(self) -> None:
        self.push_screen(
            SettingsPickerScreen(
                self.tui_settings,
                apply_settings=self._settings_picker_settings_changed,
            )
        )

    def _open_trust_picker(self) -> None:
        project_trust_state = getattr(self.session, "project_trust_state", None)
        if project_trust_state is None:
            self._notify("Project trust is not available.", severity="warning")
            return
        try:
            state = project_trust_state()
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
            return
        self.push_screen(TrustPickerScreen(state), callback=self._handle_trust_picker_result)

    def _handle_trust_picker_result(self, option: ProjectTrustOption | None) -> None:
        if option is None:
            return
        save_project_trust = getattr(self.session, "save_project_trust", None)
        if save_project_trust is None:
            self._notify("Project trust is not available.", severity="warning")
            return
        try:
            result = save_project_trust(option)
            if isawaitable(result):
                self.run_worker(self._notify_awaited_trust_result(result), exclusive=False)
                return
            self._notify(str(result))
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")

    async def _notify_awaited_trust_result(self, result: object) -> None:
        try:
            message = await result
            self._notify(str(message))
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")

    async def _queue_prompt(
        self,
        text: str,
        *,
        streaming_behavior: Literal["steer", "follow_up"],
    ) -> None:
        """Queue a prompt for the active agent worker."""
        try:
            async for event in self.session.prompt(text, streaming_behavior=streaming_behavior):
                self.adapter.apply(event)
        except Exception as exc:  # noqa: BLE001 - surface queueing failures in the TUI
            self._notify(f"Could not queue message: {exc}", severity="error")
            return
        self._refresh()

    async def _run_prompt(self, text: str, run_id: int | None = None) -> None:
        """Run one prompt and stream session events into the TUI state."""
        active_run_id = self._prompt_run_id if run_id is None else run_id
        try:
            async for event in self.session.prompt(text):
                if active_run_id != self._prompt_run_id:
                    return
                self.adapter.apply(event)
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    _attach_diagnostic_log_path_to_error(self.state, self.session)
                await self._apply_streaming_transcript_event(event)
        except Exception as exc:  # noqa: BLE001 - surface unexpected worker errors in the TUI
            if active_run_id != self._prompt_run_id:
                return
            message = _format_prompt_error(exc, self.session)
            self.state.error = message
            self.state.add_item("error", message)
            self.state.running = False
            self._refresh()
        finally:
            if active_run_id == self._prompt_run_id:
                self._prompt_worker = None

    async def _apply_streaming_transcript_event(self, event: AgentEvent) -> None:
        """Apply an agent event to mounted transcript widgets without full redraws."""
        if not self.screen_stack:
            self._refresh()
            return
        theme = self.tui_settings.resolved_theme
        try:
            transcript = self.query_one("#transcript", TranscriptView)
        except NoMatches:
            self._refresh()
            return
        if isinstance(event, AgentStartEvent):
            self._refresh_chrome()
            return
        if isinstance(event, AgentEndEvent):
            await transcript.finish_assistant_message()
            self._refresh_chrome()
            return
        if isinstance(event, MessageStartEvent):
            return
        if isinstance(event, MessageDeltaEvent):
            await transcript.append_assistant_delta(event.delta, theme=theme)
            self._sync_activity_indicator()
            return
        if isinstance(event, ThinkingDeltaEvent):
            await transcript.append_thinking_delta(
                event.delta,
                theme=theme,
                show_thinking=self.state.show_thinking,
                placeholder_text=self.state.thinking_placeholder_text,
            )
            self._sync_activity_indicator()
            return
        if isinstance(event, MessageEndEvent):
            if event.message.role == "user":
                self._refresh()
                return
            if event.message.role == "assistant":
                await transcript.finish_assistant_message(event.message.content)
                self._refresh_chrome()
                return
            return
        if isinstance(event, ToolExecutionStartEvent):
            await transcript.finish_assistant_message()
            await transcript.append_item(
                self.state.items[-1],
                theme=theme,
                show_tool_results=self.state.show_tool_results,
            )
            self._refresh_chrome()
            return
        if isinstance(event, ToolExecutionUpdateEvent) and _tool_update_has_pipeline_stage(event):
            await transcript.finish_assistant_message()
            self._refresh()
            return
        if isinstance(event, ToolExecutionUpdateEvent | RetryEvent | ErrorEvent):
            await transcript.finish_assistant_message()
            if self.state.items:
                await transcript.append_item(
                    self.state.items[-1],
                    theme=theme,
                    show_tool_results=self.state.show_tool_results,
                )
            self._refresh_chrome()
            return
        if isinstance(event, ToolExecutionEndEvent):
            self._refresh()
            return
        if isinstance(event, QueueUpdateEvent):
            self._refresh_chrome()
            return
        self._refresh_chrome()

    def action_cancel(self) -> None:
        """Cancel the active compaction or agent turn."""
        if self._cancel_active_compaction(notify=True):
            self._last_empty_escape_at = None
            return
        if self._cancel_active_terminal_command(notify=True):
            self._last_empty_escape_at = None
            return
        if self._cancel_active_prompt(notify=True):
            self._last_empty_escape_at = None
            return
        self._handle_empty_prompt_escape()

    def _cancel_active_compaction(self, *, notify: bool) -> bool:
        """Cancel the active manual compaction worker and restore visible session state."""
        worker = self._compaction_worker
        if worker is None or worker.is_finished or worker.is_cancelled:
            return False

        worker.cancel()
        self._compaction_worker = None
        self.state.clear()
        self.state.set_skills(self.session.skills)
        self.state.load_messages(self.session.messages)
        self._refresh()
        if notify:
            self._notify("Cancelled compaction.")
        return True

    def _cancel_active_terminal_command(self, *, notify: bool) -> bool:
        """Cancel the active input-bar terminal command."""
        worker = self._terminal_worker
        if worker is None or worker.is_finished or worker.is_cancelled:
            return False

        cancel_terminal = getattr(self.session, "cancel_terminal_command", None)
        if callable(cancel_terminal):
            cancel_terminal()
        worker.cancel()
        self._terminal_worker = None
        self._refresh()
        if notify:
            self._notify("Cancelled terminal command.")
        return True

    def _cancel_active_prompt(self, *, notify: bool, interrupt: bool = False) -> bool:
        """Cancel the active prompt worker and ignore any late events from it."""
        del interrupt
        worker = self._prompt_worker
        is_worker_active = worker is not None and not worker.is_cancelled
        is_session_running = bool(getattr(self.session, "is_running", False))
        if not (self.state.running or is_session_running or is_worker_active):
            return False

        self._prompt_run_id += 1
        cancel = getattr(self.session, "cancel", None)
        if callable(cancel):
            cancel()
        if worker is not None and not worker.is_cancelled:
            worker.cancel()
        self._prompt_worker = None
        self.state.running = False
        self.state.assistant_buffer = ""
        self._refresh()
        if notify:
            self._notify("Interrupted current operation.")
        return True

    def _handle_empty_prompt_escape(self) -> bool:
        action = self.tui_settings.double_escape_action
        if action == "none":
            self._last_empty_escape_at = None
            return False
        try:
            prompt = self.query_one("#prompt", PromptInput)
        except NoMatches:
            self._last_empty_escape_at = None
            return False
        if prompt.text.strip():
            self._last_empty_escape_at = None
            return False

        now = monotonic()
        last_escape_at = self._last_empty_escape_at
        if last_escape_at is not None and now - last_escape_at <= 0.5:
            self._last_empty_escape_at = None
            if action == "tree":
                self.run_worker(self._open_tree_picker(), exclusive=False)
            elif action == "fork":
                self.run_worker(self._open_fork_picker(), exclusive=False)
            return True

        self._last_empty_escape_at = now
        return False

    def action_accept_completion(self) -> None:
        """Accept the currently selected prompt completion."""
        if isinstance(self.screen, ModelPickerScreen):
            self.screen.action_toggle_mode()
            return
        if isinstance(self.screen, SessionPickerScreen):
            self.screen.action_toggle_scope()
            return
        if isinstance(
            self.screen,
            TreePickerScreen
            | UserMessagePickerScreen
            | SettingsPickerScreen
            | LoginMethodPickerScreen
            | LoginProviderPickerScreen
            | ThemePickerScreen,
        ):
            self.screen.action_select_cursor()
            return
        prompt = self.query_one("#prompt", PromptInput)
        applied = self._apply_selected_completion(prompt.text)
        if applied is None:
            return
        prompt.text = applied
        prompt.move_cursor(_text_end_location(applied))
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()

    async def action_quit(self) -> None:
        """Quit the app, or use picker-local quit bindings when a modal owns them."""
        if isinstance(self.screen, SessionPickerScreen):
            self.screen.action_delete_session()
            return
        result = super().action_quit()
        if isawaitable(result):
            await result

    def action_completion_next(self) -> None:
        """Select the next prompt completion or move down in the prompt."""
        if isinstance(self.screen, CommandOutputScreen):
            self.screen.action_scroll_down()
            return
        if isinstance(
            self.screen,
            SessionPickerScreen
            | SettingsPickerScreen
            | TreePickerScreen
            | UserMessagePickerScreen
            | LoginMethodPickerScreen
            | LoginProviderPickerScreen
            | ThemePickerScreen
            | ModelPickerScreen,
        ):
            self.screen.action_cursor_down()
            return
        if not self._completion_state.items:
            self.query_one("#prompt", PromptInput).action_cursor_down()
            return
        self._completion_state = self._completion_state.select_next()
        self._refresh_completions()

    def action_completion_previous(self) -> None:
        """Select the previous prompt completion or move up in the prompt."""
        if isinstance(self.screen, CommandOutputScreen):
            self.screen.action_scroll_up()
            return
        if isinstance(
            self.screen,
            SessionPickerScreen
            | SettingsPickerScreen
            | TreePickerScreen
            | UserMessagePickerScreen
            | LoginMethodPickerScreen
            | LoginProviderPickerScreen
            | ThemePickerScreen
            | ModelPickerScreen,
        ):
            self.screen.action_cursor_up()
            return
        if not self._completion_state.items:
            if self.action_edit_queued_follow_up():
                return
            self.query_one("#prompt", PromptInput).action_cursor_up()
            return
        self._completion_state = self._completion_state.select_previous()
        self._refresh_completions()

    def action_edit_queued_follow_up(self) -> bool:
        """Move the latest queued follow-up back into the prompt for editing."""
        if not self.state.running:
            return False
        prompt = self.query_one("#prompt", PromptInput)
        if prompt.text.strip():
            return False
        pop_follow_up = getattr(self.session, "pop_latest_follow_up_message", None)
        if not callable(pop_follow_up):
            return False
        message = pop_follow_up()
        if not message:
            return False
        prompt.text = message
        prompt.move_cursor(_text_end_location(message))
        self._sync_queue_state()
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh()
        return True

    def action_open_command_palette(self) -> None:
        """Open the slash-command palette in the prompt."""
        prompt = self.query_one("#prompt", PromptInput)
        prompt.focus()
        prompt.text = "/"
        prompt.move_cursor((0, 1))
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()

    def action_open_session_picker(self) -> None:
        """Open the indexed session picker."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        current_records = _session_records(self.session, scope="current")
        all_records = _session_records(self.session, scope="all")
        if not current_records and not all_records:
            self._notify("No sessions found.")
            return
        self.push_screen(
            SessionPickerScreen(
                current_records,
                theme=self.tui_settings.resolved_theme,
                all_records=all_records,
                current_session_id=getattr(self.session, "session_id", None),
                rename_session=self._rename_picker_session,
                delete_session=self._delete_picker_session,
            ),
            callback=self._handle_session_picker_result,
        )

    def action_new_session(self) -> None:
        """Start a new session from a configured app keybinding."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._new_session(), exclusive=False)

    def action_open_tree_picker(self) -> None:
        """Open the session tree from a configured app keybinding."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._open_tree_picker(), exclusive=False)

    def action_open_fork_picker(self) -> None:
        """Open the session fork picker from a configured app keybinding."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._open_fork_picker(), exclusive=False)

    def _rename_picker_session(
        self,
        session_id: str,
        name: str,
    ) -> SessionCompletionRecord | None:
        """Rename an indexed session from the session picker."""
        manager = getattr(self.session, "session_manager", None)
        if manager is None:
            return None
        return manager.touch_session(
            session_id,
            model=self.session.model,
            provider_name=self.session.provider_name,
            title=name,
        )

    def _delete_picker_session(self, session_id: str) -> bool:
        """Delete an indexed session from the session picker."""
        manager = getattr(self.session, "session_manager", None)
        delete_session = getattr(manager, "delete_session", None)
        if not callable(delete_session):
            return False
        return delete_session(session_id) is not None

    def action_cycle_thinking(self) -> None:
        """Cycle the active thinking mode."""
        self.run_worker(self._cycle_thinking_level(), exclusive=False)

    def action_cycle_model(self) -> None:
        """Cycle through scoped models."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._cycle_scoped_model(), exclusive=False)

    def action_cycle_model_previous(self) -> None:
        """Cycle backwards through scoped models."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._cycle_scoped_model(direction="previous"), exclusive=False)

    def action_open_model_picker(self) -> None:
        """Open the interactive model picker."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self._open_model_picker()

    def action_toggle_tool_results(self) -> None:
        """Toggle inline tool result details in the transcript."""
        expanded = self.state.toggle_tool_results()
        self._refresh()
        self._notify("Tool results expanded." if expanded else "Tool results collapsed.")

    def action_toggle_thinking(self) -> None:
        """Toggle thinking-token display in the transcript."""
        self.state.toggle_thinking()
        self._refresh()

    def action_open_external_editor(self) -> None:
        """Open the prompt text in the configured external editor."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._open_external_editor(), exclusive=False)

    def action_paste_clipboard(self) -> None:
        """Paste plain text from the system clipboard into the prompt."""
        self.run_worker(self._paste_clipboard(), exclusive=False)

    def action_dequeue_messages(self) -> None:
        """Restore all queued messages into the prompt for editing."""
        restored = self._restore_queued_messages_to_prompt()
        if restored == 0:
            self._notify("No queued messages to restore.")
        else:
            suffix = "s" if restored != 1 else ""
            self._notify(f"Restored {restored} queued message{suffix}.")

    def action_copy_last_message(self) -> None:
        """Copy the most recent assistant message to the system clipboard."""
        text = _last_assistant_text(self.state)
        if not text:
            self._notify("No assistant messages to copy.", severity="warning")
            return
        self.copy_to_clipboard(text)
        self._notify("Copied last assistant message to clipboard.")

    async def _open_external_editor(self) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        original_text = prompt.text
        editor_command = _external_editor_command()
        if editor_command is None:
            self._notify("Set VISUAL or EDITOR to use the external editor.", severity="warning")
            return
        try:
            with self.suspend():
                result = await _edit_text_with_external_editor(editor_command, original_text)
        except Exception as exc:  # noqa: BLE001 - surface editor launch failures in the TUI
            self._notify(f"External editor failed: {exc}", severity="error")
            return
        if result is None:
            self._notify("External editor exited without changes.", severity="warning")
            return
        prompt.text = result
        prompt.move_cursor(_text_end_location(result))
        prompt.focus()
        self._sync_prompt_shell_mode(prompt.text)
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()
        self._refresh()

    async def _paste_clipboard(self) -> None:
        try:
            image = await _read_clipboard_image()
            if image is not None:
                self._insert_prompt_text(str(_write_clipboard_image_to_temp(image)))
                return
            text = await _read_clipboard_text()
        except Exception:  # noqa: BLE001 - clipboard access is best effort
            return
        if not text:
            return
        self._insert_prompt_text(text)

    def _insert_prompt_text(self, text: str) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        prompt.insert(text)
        prompt.focus()
        self._sync_prompt_shell_mode(prompt.text)
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()
        self._refresh()

    def _restore_queued_messages_to_prompt(self) -> int:
        clear_queued = getattr(self.session, "clear_queued_messages", None)
        if not callable(clear_queued):
            return 0
        queued = clear_queued()
        messages = [
            *(message.content for message in queued.steering),
            *(message.content for message in queued.follow_up),
        ]
        if not messages:
            self._sync_queue_state()
            self._refresh()
            return 0
        prompt = self.query_one("#prompt", PromptInput)
        queued_text = "\n\n".join(messages)
        combined_text = "\n\n".join(
            part for part in (queued_text, prompt.text) if part.strip()
        )
        prompt.text = combined_text
        prompt.move_cursor(_text_end_location(combined_text))
        prompt.focus()
        self._sync_prompt_shell_mode(prompt.text)
        self._sync_queue_state()
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()
        self._refresh()
        return len(messages)

    def _handle_session_picker_result(self, session_id: str | None) -> None:
        if session_id is None:
            return
        self.run_worker(self._resume_session(session_id), exclusive=False)

    async def _resume_session(self, session_id: str) -> None:
        try:
            resume_message = await self.session.resume(session_id)
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self.state.load_messages(self.session.messages)
            self._notify(resume_message)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _import_session(self, path: Path) -> None:
        import_session = getattr(self.session, "import_session", None)
        if import_session is None:
            self._notify("Session import is not available.", severity="warning")
            return
        try:
            message = await import_session(path)
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self.state.load_messages(self.session.messages)
            self._notify(message)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _share_session(self) -> None:
        share = getattr(self.session, "share", None)
        if share is None:
            self._notify("Session sharing is not available.", severity="warning")
            return
        try:
            message = await share()
            self._notify(message)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _open_tree_picker(self) -> None:
        tree_choices = getattr(self.session, "tree_choices", None)
        if tree_choices is None:
            self._notify("Session tree is not available.", severity="warning")
            return
        try:
            choices = tuple(await tree_choices())
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
            return
        if not choices:
            self._notify("No session entries are available for branching.", severity="warning")
            return
        self.push_screen(
            TreePickerScreen(
                choices,
                theme=self.tui_settings.resolved_theme,
                initial_filter_mode=cast(TreeFilterMode, self.tui_settings.tree_filter_mode),
                set_entry_label=self._set_tree_entry_label_from_picker,
            ),
            callback=self._handle_tree_picker_result,
        )

    async def _open_fork_picker(self) -> None:
        tree_choices = getattr(self.session, "tree_choices", None)
        if tree_choices is None:
            self._notify("Session tree is not available.", severity="warning")
            return
        try:
            choices = tuple(
                choice
                for choice in await tree_choices()
                if _tree_choice_matches_filter(
                    choice,
                    filter_mode="user-only",
                    show_tool_calls=True,
                )
            )
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
            return
        if not choices:
            self._notify("No messages to fork from.", severity="warning")
            return
        self.push_screen(
            UserMessagePickerScreen(choices),
            callback=self._handle_fork_picker_result,
        )

    def _handle_fork_picker_result(self, entry_id: str | None) -> None:
        if entry_id is None:
            return
        self.run_worker(
            self._branch_to_tree_entry(entry_id, summarize=False),
            exclusive=False,
        )

    async def _set_tree_entry_label_from_picker(
        self,
        entry_id: str,
        label: str | None,
    ) -> tuple[SessionTreeChoice, ...]:
        set_tree_entry_label = getattr(self.session, "set_tree_entry_label", None)
        tree_choices = getattr(self.session, "tree_choices", None)
        if set_tree_entry_label is None or tree_choices is None:
            raise RuntimeError("Session tree labels are not available.")
        result = set_tree_entry_label(entry_id, label)
        if isawaitable(result):
            result = await result
        if result:
            self._notify(str(result))
        return tuple(await tree_choices())

    def _handle_tree_picker_result(self, result: TreePickerResult | None) -> None:
        if result is None:
            return
        self.run_worker(
            self._branch_to_tree_entry(
                result.entry_id,
                summarize=result.summarize,
                custom_instructions=result.custom_instructions,
            ),
            exclusive=False,
        )

    async def _branch_to_tree_entry(
        self,
        entry_id: str,
        *,
        summarize: bool,
        custom_instructions: str | None = None,
    ) -> None:
        branch_to_entry = getattr(self.session, "branch_to_entry", None)
        if branch_to_entry is None:
            self._notify("Session tree is not available.", severity="warning")
            return
        try:
            if summarize:
                self.state.clear()
                self.state.add_item("status", "Summarizing branch…")
                self._refresh()

            result = branch_to_entry(
                entry_id,
                summarize=summarize,
                custom_instructions=custom_instructions,
            )
            if isawaitable(result):
                result = await result
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self.state.load_messages(self.session.messages)
            if isinstance(result, SessionTreeBranchResult):
                if result.input_prefill is not None:
                    prompt = self.query_one("#prompt", PromptInput)
                    prompt.value = result.input_prefill
                    prompt.move_cursor(_text_end_location(result.input_prefill))
                    prompt.focus()
                self._notify(result.message)
            elif isinstance(result, str):
                self._notify(result)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _new_session(self) -> None:
        self._cancel_active_prompt(notify=False, interrupt=True)
        new_session = getattr(self.session, "new_session", None)
        if new_session is None:
            self._notify("Session manager is not available.")
            return
        try:
            await new_session()
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self.state.load_messages(self.session.messages)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _clone_session(self) -> None:
        clone_current_session = getattr(self.session, "clone_current_session", None)
        if clone_current_session is None:
            self._notify("Session manager is not available.", severity="warning")
            return
        try:
            message = await clone_current_session()
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self.state.load_messages(self.session.messages)
            self._notify(message)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    def _apply_selected_completion(self, value: str) -> str | None:
        item = self._completion_state.selected
        if item is None:
            return None
        return item.apply(value)

    def _append_command_message(self, command_text: str, message: str) -> None:
        """Append non-persistent command output to the visible transcript."""
        self.state.add_item("status", f"{_command_output_title(command_text)}\n{message}")

    def _show_command_message(self, command_text: str, message: str) -> None:
        self.push_screen(
            CommandOutputScreen(
                _command_output_title(command_text),
                message,
                theme=self.tui_settings.resolved_theme,
            )
        )

    def _open_login_picker(self) -> None:
        self.push_screen(
            LoginMethodPickerScreen(theme=self.tui_settings.resolved_theme),
            callback=self._handle_login_method_result,
        )

    def _handle_login_method_result(self, method: str | None) -> None:
        if method is None:
            return
        if method == "subscription":
            providers = _subscription_login_providers(BUILTIN_PROVIDER_CATALOG)
        elif method == "api-key":
            providers = _api_key_login_providers(BUILTIN_PROVIDER_CATALOG)
        else:
            self._notify(f"Unknown login method: {method}", severity="error")
            return
        if not providers:
            self._notify("No login providers are available for that method.", severity="warning")
            return
        self.push_screen(
            LoginProviderPickerScreen(
                providers,
                theme=self.tui_settings.resolved_theme,
            ),
            callback=self._handle_login_provider_result,
        )

    def _handle_login_provider_result(self, provider_name: str | None) -> None:
        if provider_name is None:
            return
        self._open_login(provider_name)

    def _open_login(self, provider_name: str) -> None:
        entry = builtin_provider_entry(provider_name)
        if entry is None:
            self._notify(f"Unknown provider: {provider_name}", severity="error")
            return
        if entry.kind == "openai-codex":
            self.push_screen(
                OAuthLoginScreen(entry, theme=self.tui_settings.resolved_theme),
                callback=lambda credential: self._handle_oauth_login_result(entry, credential),
            )
            return
        self.push_screen(
            LoginScreen(entry, theme=self.tui_settings.resolved_theme),
            callback=lambda api_key: self._handle_login_result(entry, api_key),
        )

    def _handle_login_result(self, entry: ProviderCatalogEntry, api_key: str | None) -> None:
        if api_key is None:
            return
        try:
            FileCredentialStore().set(entry.credential_name, api_key)
            provider = provider_config_from_catalog_entry(entry.name)
            upsert_saved_provider(provider, set_default=False)
            self.session.reload_provider_settings()
            try:
                self.session.set_provider(entry.name, persist_default=False)
            except TypeError:
                self.session.set_provider(entry.name)
        except Exception as exc:  # noqa: BLE001 - surface login failures in the TUI
            self._notify(f"Could not save login: {exc}", severity="error")
            return
        self._notify(f"Saved login for {entry.display_name}.")
        self._refresh()

    def _handle_oauth_login_result(
        self,
        entry: ProviderCatalogEntry,
        credential: OAuthCredential | None,
    ) -> None:
        if credential is None:
            return
        try:
            FileCredentialStore().set_oauth(entry.credential_name, credential)
            provider = provider_config_from_catalog_entry(entry.name)
            upsert_saved_provider(provider, set_default=False)
            self.session.reload_provider_settings()
            try:
                self.session.set_provider(entry.name, persist_default=False)
            except TypeError:
                self.session.set_provider(entry.name)
        except Exception as exc:  # noqa: BLE001 - surface login failures in the TUI
            self._notify(f"Could not save login: {exc}", severity="error")
            return
        self._notify(f"Saved login for {entry.display_name}.")
        self._refresh()

    def _open_logout_picker(self) -> None:
        providers = _stored_credential_providers(BUILTIN_PROVIDER_CATALOG)
        if not providers:
            self._notify(NO_STORED_CREDENTIALS_MESSAGE, severity="warning")
            return
        self.push_screen(
            LoginProviderPickerScreen(
                providers,
                theme=self.tui_settings.resolved_theme,
                title="Logout",
            ),
            callback=self._handle_logout_provider_result,
        )

    def _handle_logout_provider_result(self, provider_name: str | None) -> None:
        if provider_name is None:
            return
        self._logout(provider_name)

    def _logout(self, provider_name: str) -> None:
        entry = builtin_provider_entry(provider_name)
        if entry is None:
            self._notify(f"Unknown provider: {provider_name}", severity="error")
            return

        credential_store = FileCredentialStore()
        if not _credential_store_has_entry(credential_store, entry.credential_name):
            self._notify(NO_STORED_CREDENTIALS_MESSAGE, severity="warning")
            return

        try:
            credential_store.delete(entry.credential_name)
            self.session.reload_provider_settings()
        except Exception as exc:  # noqa: BLE001 - surface logout failures in the TUI
            self._notify(f"Could not log out: {exc}", severity="error")
            return

        if entry.kind == "openai-codex":
            self._notify(f"Logged out of {entry.display_name}.")
        else:
            self._notify(
                f"Removed stored API key for {entry.display_name}. "
                "Environment variables and providers.json config are unchanged."
            )
        self._refresh()

    def _available_model_choices(self) -> tuple[ModelChoice, ...]:
        fallback_choices = (
            ModelChoice(provider_name=self.session.provider_name, model=model)
            for model in self.session.available_models
        )
        return tuple(
            getattr(
                self.session,
                "available_model_choices",
                fallback_choices,
            )
        )

    def _open_model_picker(self) -> None:
        choices = self._available_model_choices()
        if not choices:
            self._notify(
                "No configured providers are usable. Run /login to set up a provider.",
                severity="warning",
            )
            return
        self.push_screen(
            ModelPickerScreen(
                choices,
                scoped_choices=tuple(getattr(self.session, "scoped_model_choices", ())),
                current_model=self.session.model,
                provider_name=self.session.provider_name,
                theme=self.tui_settings.resolved_theme,
                on_toggle_scoped=None,
                picker_kind="model",
            ),
            callback=self._handle_model_picker_result,
        )

    def _open_scoped_models_picker(self) -> None:
        choices = self._available_model_choices()
        if not choices:
            self._notify(
                "No configured providers are usable. Run /login to set up a provider.",
                severity="warning",
            )
            return
        self.push_screen(
            ModelPickerScreen(
                choices,
                scoped_choices=tuple(getattr(self.session, "scoped_model_choices", ())),
                current_model=self.session.model,
                provider_name=self.session.provider_name,
                theme=self.tui_settings.resolved_theme,
                on_toggle_scoped=self._toggle_scoped_model,
                on_set_scoped=self._set_scoped_models,
                picker_kind="scoped",
            ),
            callback=self._handle_scoped_models_picker_result,
        )

    def _toggle_scoped_model(self, choice: ModelChoice) -> Sequence[ModelChoice]:
        toggle_scoped_model = getattr(self.session, "toggle_scoped_model", None)
        if toggle_scoped_model is None:
            self._notify("Scoped model controls are not available.", severity="warning")
            return tuple(getattr(self.session, "scoped_model_choices", ()))
        try:
            return tuple(toggle_scoped_model(choice))
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not update scoped models: {exc}", severity="error")
            return tuple(getattr(self.session, "scoped_model_choices", ()))

    def _set_scoped_models(self, choices: Sequence[ModelChoice]) -> Sequence[ModelChoice]:
        set_scoped_models = getattr(self.session, "set_scoped_models", None)
        if set_scoped_models is None:
            self._notify("Scoped model controls are not available.", severity="warning")
            return tuple(getattr(self.session, "scoped_model_choices", ()))
        try:
            return tuple(set_scoped_models(choices))
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not update scoped models: {exc}", severity="error")
            return tuple(getattr(self.session, "scoped_model_choices", ()))

    def _handle_scoped_models_picker_result(self, choice: ModelChoice | None) -> None:
        del choice
        self._refresh()

    def _handle_model_picker_result(self, choice: ModelChoice | None) -> None:
        if choice is None:
            return
        try:
            set_model_choice = getattr(self.session, "set_model_choice", None)
            if set_model_choice is None:
                if choice.provider_name != self.session.provider_name:
                    self.session.set_provider(choice.provider_name)
                self.session.set_model(choice.model)
            else:
                set_model_choice(choice)
        except Exception as exc:  # noqa: BLE001 - surface model switch failures in the TUI
            self._notify(f"Could not switch model: {exc}", severity="error")
            return
        self._refresh()

    def _open_theme_picker(self) -> None:
        self.push_screen(
            ThemePickerScreen(
                current_theme=self.tui_settings.theme,
                theme=self.tui_settings.resolved_theme,
            ),
            callback=self._handle_theme_picker_result,
        )

    def _handle_theme_picker_result(self, theme: TuiThemeName | None) -> None:
        if theme is None:
            return
        self._set_tui_theme(theme)

    async def _set_thinking_level(self, level: str) -> None:
        setter = getattr(self.session, "set_thinking_level", None)
        if setter is None:
            self._notify("Thinking controls are not available.", severity="warning")
            return
        try:
            result = setter(level)
            if isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not change thinking mode: {exc}", severity="error")
            return
        self._refresh()

    async def _cycle_thinking_level(self) -> None:
        cycler = getattr(self.session, "cycle_thinking_level", None)
        if cycler is None:
            self._notify("Thinking controls are not available.", severity="warning")
            return
        try:
            result = cycler()
            if isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not change thinking mode: {exc}", severity="error")
            return
        self._refresh()

    async def _cycle_scoped_model(
        self,
        *,
        direction: Literal["next", "previous"] = "next",
    ) -> None:
        if direction == "previous":
            self._cycle_scoped_model_previous()
            return
        cycler = getattr(self.session, "cycle_scoped_model", None)
        if cycler is None:
            self._notify("Scoped model controls are not available.", severity="warning")
            return
        try:
            result = cycler()
            if isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not switch scoped model: {exc}", severity="error")
            return
        self._refresh()

    def _cycle_scoped_model_previous(self) -> None:
        choices = tuple(getattr(self.session, "scoped_model_choices", ()))
        if not choices:
            self._notify("No scoped models configured.", severity="warning")
            return
        current = ModelChoice(provider_name=self.session.provider_name, model=self.session.model)
        try:
            index = choices.index(current)
        except ValueError:
            index = 0
        self._handle_model_picker_result(choices[(index - 1) % len(choices)])

    def _notify(
        self,
        message: str,
        *,
        severity: Literal["information", "warning", "error"] = "information",
    ) -> None:
        key = (message, severity)
        if key in self._active_notification_keys:
            return
        self._active_notification_keys.add(key)
        self.set_timer(
            self.NOTIFICATION_TIMEOUT,
            lambda: self._active_notification_keys.discard(key),
            name=f"notification-dedupe-{hash(key)}",
        )
        self.notify(message, severity=severity, markup=False)

    def _refresh(self) -> None:
        theme = self.tui_settings.resolved_theme
        self._refresh_chrome(theme=theme)
        transcript = self.query_one("#transcript", TranscriptView)
        transcript.update_from_state(self.state, theme=theme)

    def _refresh_chrome(self, *, theme: TuiTheme | None = None) -> None:
        """Refresh non-transcript chrome without remounting transcript blocks."""
        theme = theme or self.tui_settings.resolved_theme
        self._sync_queue_state()
        sidebar = self.query_one("#sidebar", SessionSidebar)
        sidebar.update_from_session(self.session, theme=theme)
        compact_info = self.query_one("#compact-session-info", CompactSessionInfo)
        compact_info.update_from_session(self.session, theme=theme)
        queued_messages = self.query_one("#queued-messages", Static)
        queued_messages.display = self.state.queued_message_count > 0
        queued_messages.update(_render_queued_messages(self.state, theme=theme))
        self._sync_activity_indicator()
        self._refresh_footer_bindings()

    def _sync_queue_state(self) -> None:
        queue_event = getattr(self.session, "queue_update_event", None)
        if not callable(queue_event):
            return
        self.adapter.apply(queue_event())

    def _sync_activity_indicator(self) -> None:
        if self.state.running:
            if self._activity_timer is None:
                self._activity_timer = self.set_interval(
                    ACTIVITY_TICK_SECONDS,
                    self._tick_activity,
                    name="activity-indicator",
                )
            else:
                self._activity_timer.resume()
            self._apply_activity_indicator()
            return
        self._activity_frame = 0
        if self._activity_timer is not None:
            self._activity_timer.pause()
        self._apply_activity_indicator()

    def _tick_activity(self) -> None:
        if not self.state.running:
            return
        self._activity_frame += 1
        self._apply_activity_indicator()

    def _apply_activity_indicator(self) -> None:
        theme = self.tui_settings.resolved_theme
        try:
            prompt = self.query_one("#prompt", PromptInput)
            prompt_prefix = self.query_one("#prompt-prefix", Static)
        except NoMatches:
            return
        prompt.styles.border = (
            "tall",
            _activity_prompt_border_color(
                theme,
                frame=self._activity_frame,
                running=self.state.running,
                shell_mode=_is_terminal_command_prompt(prompt.text),
            ),
        )
        prompt_prefix.update(
            _render_activity_indicator(
                theme,
                frame=self._activity_frame,
                running=self.state.running,
            )
        )

    def _refresh_completions(self) -> None:
        suggestions = self.query_one("#autocomplete", Static)
        suggestions.display = bool(self._completion_state.items)
        suggestions.update(
            render_completion_suggestions(
                _visible_completion_state(
                    self._completion_state,
                    max_lines=COMPLETION_MAX_VISIBLE_LINES,
                    width=max(suggestions.content_size.width or suggestions.size.width, 1),
                ),
                theme=self.tui_settings.resolved_theme,
            )
        )
        self._refresh_footer_bindings()

    def _update_responsive_layout(self, width: int, height: int) -> None:
        show_sidebar = width >= SIDEBAR_MIN_WIDTH and height >= SIDEBAR_MIN_HEIGHT
        self.set_class(not show_sidebar, "-hide-sidebar")

    def _build_completion_state(self, text: str) -> CompletionState:
        registry = _session_command_registry(self.session)
        return build_completion_state(
            text,
            command_registry=registry,
            skills=self.session.skills,
            prompt_templates=self.session.prompt_templates,
            model_names=self.session.available_models,
            provider_names=self.session.available_providers,
            thinking_levels=getattr(self.session, "available_thinking_levels", ()),
            theme_names=BUILTIN_TUI_THEME_NAMES,
            session_options=_session_options(self.session),
            cwd=self.session.cwd,
        )

    def _refresh_footer_bindings(self) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        prompt.set_footer_mode(_prompt_footer_mode(self.state, self._completion_state))

    def _sync_prompt_shell_mode(self, text: str) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        prompt.shell_mode_style = self.tui_settings.resolved_theme.accent
        prompt.set_class(_is_terminal_command_prompt(text), "-shell-mode")
        prompt.refresh()
        self._apply_activity_indicator()


def _activity_prompt_border_color(
    theme: TuiTheme,
    *,
    frame: int,
    running: bool,
    shell_mode: bool,
) -> str:
    """Return the prompt border color for the current activity animation frame."""
    del frame, running
    if shell_mode:
        return theme.accent
    return theme.prompt_border


def _render_activity_indicator(theme: TuiTheme, *, frame: int, running: bool) -> Text:
    """Render the prompt prefix, turning Tau into a moving square while running."""
    if not running:
        return Text("τ", style=f"bold {theme.accent}")

    cycle_length = (ACTIVITY_INDICATOR_HEIGHT - 1) * 2
    cycle_position = frame % cycle_length
    active_row = (
        cycle_position
        if cycle_position < ACTIVITY_INDICATOR_HEIGHT
        else cycle_length - cycle_position
    )
    direction = 1 if cycle_position < ACTIVITY_INDICATOR_HEIGHT else -1
    trail_rows = {
        active_row: theme.accent,
        active_row - direction: _blend_hex_colors(
            theme.accent,
            theme.screen_background,
            fraction=0.35,
        ),
        active_row - (direction * 2): _blend_hex_colors(
            theme.accent,
            theme.screen_background,
            fraction=0.65,
        ),
    }

    rendered = Text()
    for row in range(ACTIVITY_INDICATOR_HEIGHT):
        color = trail_rows.get(row)
        if color is None:
            rendered.append(" ")
        else:
            rendered.append("■", style=color)
        if row < ACTIVITY_INDICATOR_HEIGHT - 1:
            rendered.append("\n")
    return rendered


def _is_terminal_command_prompt(text: str) -> bool:
    """Return whether the prompt is currently in terminal-command mode."""
    return _terminal_command_prefix_span(text) is not None


def _terminal_command_prefix_span(text: str) -> tuple[int, int] | None:
    """Return the input span for a leading ! or !! terminal-command prefix."""
    leading_whitespace = len(text) - len(text.lstrip())
    stripped = text[leading_whitespace:]
    if stripped.startswith("!!"):
        return (leading_whitespace, leading_whitespace + 2)
    if stripped.startswith("!"):
        return (leading_whitespace, leading_whitespace + 1)
    return None


def _blend_hex_colors(start: str, end: str, *, fraction: float) -> str:
    """Blend two ``#rrggbb`` colors by ``fraction``."""
    start_rgb = _hex_to_rgb(start)
    end_rgb = _hex_to_rgb(end)
    blended = tuple(
        round(start_channel + (end_channel - start_channel) * fraction)
        for start_channel, end_channel in zip(start_rgb, end_rgb, strict=True)
    )
    return f"#{blended[0]:02x}{blended[1]:02x}{blended[2]:02x}"


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"Expected #rrggbb color, got {color!r}")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _visible_completion_state(
    state: CompletionState,
    *,
    max_lines: int,
    width: int | None = None,
) -> CompletionState:
    """Return a completion-state window with the selected item visible."""
    if not state.items or max_lines <= 0:
        return CompletionState()

    selected_line_limit = max(max_lines - 1, 1)
    start = 0
    while start < state.selected_index:
        candidate = CompletionState(
            items=state.items[start:],
            selected_index=state.selected_index - start,
        )
        if _completion_selected_render_line(candidate, width=width) < selected_line_limit:
            break
        start += 1

    end = len(state.items)
    while end > state.selected_index + 1:
        candidate = CompletionState(
            items=state.items[start:end],
            selected_index=state.selected_index - start,
        )
        if _completion_render_line_count(candidate, width=width) <= max_lines:
            break
        end -= 1

    while start < state.selected_index:
        candidate = CompletionState(
            items=state.items[start:end],
            selected_index=state.selected_index - start,
        )
        if _completion_render_line_count(candidate, width=width) <= max_lines:
            break
        start += 1

    return CompletionState(
        items=state.items[start:end],
        selected_index=state.selected_index - start,
    )


def _completion_selected_render_line(state: CompletionState, *, width: int | None = None) -> int:
    """Return the rendered line number for the selected completion item."""
    line = 0
    has_rendered_text = False
    previous_category: str | None = None
    for index, item in enumerate(state.items):
        if item.category != previous_category:
            if has_rendered_text:
                line += 1
            if item.category:
                line += 1
                has_rendered_text = True
            previous_category = item.category
        elif has_rendered_text:
            line += 1
        if index == state.selected_index:
            return line
        line += _completion_item_extra_wrapped_lines(item, width=width)
        has_rendered_text = True
    return line


def _completion_render_line_count(state: CompletionState, *, width: int | None = None) -> int:
    """Return how many lines the completion state renders into."""
    if not state.items:
        return 0
    line_count = 0
    previous_category: str | None = None
    for index, item in enumerate(state.items):
        if item.category != previous_category:
            if index:
                line_count += 1
            if item.category:
                line_count += 1
            previous_category = item.category
        line_count += 1 + _completion_item_extra_wrapped_lines(item, width=width)
    return line_count


def _completion_item_extra_wrapped_lines(
    item: CompletionItem,
    *,
    width: int | None,
) -> int:
    """Return extra rendered lines used when a completion description wraps."""
    if width is None or width <= 0 or not item.description:
        return 0
    output = StringIO()
    console = Console(
        file=output,
        width=width,
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
    )
    console.print(
        render_completion_suggestions(
            CompletionState(items=(item,), selected_index=0),
            theme=TAU_DARK_THEME,
        ),
        end="",
    )
    line_count = len(output.getvalue().splitlines())
    return max(line_count - 1, 0)


def _session_command_registry(session: CodingSession) -> CommandRegistry:
    registry = getattr(session, "command_registry", None)
    if isinstance(registry, CommandRegistry):
        return registry
    return create_default_command_registry()


def _session_options(session: CodingSession) -> tuple[CompletionOption, ...]:
    return tuple(_session_option(record) for record in _session_records(session))


def _session_records(
    session: CodingSession,
    *,
    scope: Literal["current", "all"] = "current",
) -> tuple[SessionCompletionRecord, ...]:
    manager = getattr(session, "session_manager", None)
    if manager is None:
        return ()
    if scope == "all":
        try:
            return tuple(manager.list_sessions())
        except TypeError:
            return tuple(manager.list_sessions(session.cwd))
    try:
        records = manager.list_sessions(session.cwd)
    except TypeError:
        records = manager.list_sessions()
    return tuple(records)


def _session_option(record: SessionCompletionRecord) -> CompletionOption:
    description_parts = [record.title if record.title else "Untitled session"]
    if record.model:
        description_parts.append(record.model)
    description_parts.append(_short_path(record.cwd))
    return CompletionOption(value=record.id, description=" - ".join(description_parts))


def _short_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _session_picker_label(
    record: SessionCompletionRecord,
    *,
    show_path: bool = False,
    current_session_id: str | None = None,
    thread_depth: int = 0,
) -> str:
    marker = "* " if current_session_id is not None and record.id == current_session_id else ""
    indent = "  " * max(0, thread_depth)
    parts = [f"{marker}{indent}{_session_updated_at_label(record.updated_at)}"]
    if record.model:
        parts.append(record.model)
    title = _named_session_title(record.title)
    if title is not None:
        parts.append(title)
    if show_path:
        parts.append(_short_path(record.cwd))
    return " - ".join(parts)


def _session_picker_sort_label(sort_mode: SessionPickerSortMode) -> str:
    """Return the Pi-facing label for a Tau session picker sort mode."""

    if sort_mode == "relevance":
        return "fuzzy"
    return sort_mode


def _filter_session_picker_records(
    records: Sequence[SessionCompletionRecord],
    query: str,
    *,
    named_only: bool = False,
    sort_mode: SessionPickerSortMode = "threaded",
) -> tuple[SessionCompletionRecord, ...]:
    normalized = " ".join(query.casefold().split())
    candidates = tuple(
        record for record in records if _named_session_title(record.title) is not None
    )
    if not named_only:
        candidates = tuple(records)
    if not normalized:
        return _sort_session_picker_records(candidates, sort_mode=sort_mode)
    if sort_mode == "relevance":
        scored = tuple(
            (record, score)
            for record in candidates
            if (score := _session_picker_record_match_score(record, query)) is not None
        )
        return tuple(
            record
            for record, _score in sorted(
                scored,
                key=lambda item: (item[1], -item[0].updated_at),
            )
        )
    filtered = tuple(
        record for record in candidates if _session_picker_record_matches_query(record, query)
    )
    return _sort_session_picker_records(filtered, sort_mode=sort_mode)


def _session_picker_record_match_score(
    record: SessionCompletionRecord,
    query: str,
) -> float | None:
    text = _session_picker_search_text(record)
    trimmed = query.strip()
    if trimmed.startswith("re:"):
        pattern = trimmed[3:].strip()
        if not pattern:
            return None
        try:
            match = re.search(pattern, text, flags=re.IGNORECASE)
        except re.error:
            return None
        if match is None:
            return None
        return match.start() * 0.1

    score = 0.0
    for kind, value in _session_picker_query_tokens(trimmed):
        token_score = _session_picker_query_token_score(text, kind=kind, value=value)
        if token_score is None:
            return None
        score += token_score
    return score


def _session_picker_record_matches_query(
    record: SessionCompletionRecord,
    query: str,
) -> bool:
    return _session_picker_record_match_score(record, query) is not None


def _session_picker_query_token_score(
    text: str,
    *,
    kind: Literal["token", "phrase"],
    value: str,
) -> float | None:
    normalized_value = " ".join(value.casefold().split())
    if not normalized_value:
        return 0.0
    index = text.find(normalized_value)
    if index < 0:
        return None
    return index * 0.1


def _session_picker_query_tokens(query: str) -> tuple[tuple[Literal["token", "phrase"], str], ...]:
    tokens: list[tuple[Literal["token", "phrase"], str]] = []
    buffer: list[str] = []
    in_quote = False
    had_unclosed_quote = False

    def flush(kind: Literal["token", "phrase"]) -> None:
        value = "".join(buffer).strip()
        buffer.clear()
        if value:
            tokens.append((kind, value))

    for char in query:
        if char == '"':
            if in_quote:
                flush("phrase")
                in_quote = False
            else:
                flush("token")
                in_quote = True
            continue
        if not in_quote and char.isspace():
            flush("token")
            continue
        buffer.append(char)

    if in_quote:
        had_unclosed_quote = True

    if had_unclosed_quote:
        return tuple(
            ("token", token)
            for token in query.split()
            if token.strip()
        )

    flush("phrase" if in_quote else "token")
    return tuple(tokens)


def _sort_session_picker_records(
    records: Sequence[SessionCompletionRecord],
    *,
    sort_mode: SessionPickerSortMode,
) -> tuple[SessionCompletionRecord, ...]:
    if sort_mode == "threaded":
        return _sort_threaded_session_picker_records(records)
    if sort_mode == "name":
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    (_named_session_title(record.title) or "").casefold(),
                    record.model.casefold(),
                    _short_path(record.cwd).casefold(),
                    record.id.casefold(),
                ),
            )
        )
    return tuple(records)


def _sort_threaded_session_picker_records(
    records: Sequence[SessionCompletionRecord],
) -> tuple[SessionCompletionRecord, ...]:
    by_id = {record.id: record for record in records}
    children: dict[str | None, list[SessionCompletionRecord]] = {None: []}
    for record in records:
        parent_id = getattr(record, "parent_session_id", None)
        if parent_id not in by_id:
            parent_id = None
        children.setdefault(parent_id, []).append(record)

    latest_by_id: dict[str, float] = {}

    def latest_subtree_update(
        record: SessionCompletionRecord,
        seen: frozenset[str] = frozenset(),
    ) -> float:
        if record.id in latest_by_id:
            return latest_by_id[record.id]
        if record.id in seen:
            return record.updated_at
        latest = max(
            [record.updated_at]
            + [
                latest_subtree_update(child, seen | {record.id})
                for child in children.get(record.id, [])
                if child.id != record.id
            ]
        )
        latest_by_id[record.id] = latest
        return latest

    def sort_key(record: SessionCompletionRecord) -> tuple[float, float, str]:
        return (-latest_subtree_update(record), -record.updated_at, record.id.casefold())

    ordered: list[SessionCompletionRecord] = []
    emitted: set[str] = set()

    def append_tree(parent_id: str | None, seen: frozenset[str] = frozenset()) -> None:
        for record in sorted(children.get(parent_id, []), key=sort_key):
            if record.id in emitted:
                continue
            ordered.append(record)
            emitted.add(record.id)
            if record.id in seen:
                continue
            append_tree(record.id, seen | {record.id})

    append_tree(None)
    if len(ordered) != len(records):
        for record in sorted(records, key=sort_key):
            if record.id not in emitted:
                ordered.append(record)
                emitted.add(record.id)
                append_tree(record.id, frozenset({record.id}))
    return tuple(ordered)


def _session_picker_thread_depths(
    records: Sequence[SessionCompletionRecord],
) -> dict[str, int]:
    by_id = {record.id: record for record in records}
    depths: dict[str, int] = {}

    def depth_for(record: SessionCompletionRecord, seen: frozenset[str] = frozenset()) -> int:
        cached = depths.get(record.id)
        if cached is not None:
            return cached
        parent_id = getattr(record, "parent_session_id", None)
        if parent_id is None or parent_id not in by_id or parent_id in seen:
            depths[record.id] = 0
            return 0
        depth = depth_for(by_id[parent_id], seen | {record.id}) + 1
        depths[record.id] = depth
        return depth

    for record in records:
        depth_for(record)
    return depths


def _replace_session_picker_record(
    records: Sequence[SessionCompletionRecord],
    updated: SessionCompletionRecord,
) -> tuple[SessionCompletionRecord, ...]:
    """Return records with a matching session id replaced by an updated record."""
    replaced = False
    next_records: list[SessionCompletionRecord] = []
    for record in records:
        if record.id == updated.id:
            next_records.append(updated)
            replaced = True
        else:
            next_records.append(record)
    if not replaced:
        next_records.append(updated)
    return tuple(next_records)


def _remove_session_picker_record(
    records: Sequence[SessionCompletionRecord],
    session_id: str,
) -> tuple[SessionCompletionRecord, ...]:
    """Return records without a matching session id."""
    return tuple(record for record in records if record.id != session_id)


def _session_picker_search_text(record: SessionCompletionRecord) -> str:
    fields = [
        record.id,
        record.title or "",
        record.model,
        str(record.cwd),
        _short_path(record.cwd),
        _session_picker_label(record),
    ]
    return " ".join(" ".join(field.casefold().split()) for field in fields)


def _tree_choice_matches_filter(
    choice: SessionTreeChoice,
    *,
    filter_mode: TreeFilterMode,
    show_tool_calls: bool,
) -> bool:
    normalized_label = " ".join(choice.label.casefold().split())
    if choice.is_tool_call and (filter_mode == "no-tools" or not show_tool_calls):
        return False
    if filter_mode == "user-only":
        return normalized_label.startswith("user:")
    if filter_mode == "labeled-only":
        return choice.tree_label is not None
    return True


def _tree_choice_matches_search(choice: SessionTreeChoice, query: str) -> bool:
    tokens = query.casefold().split()
    if not tokens:
        return True
    searchable = _tree_choice_search_text(choice)
    return all(token in searchable for token in tokens)


def _tree_choice_search_text(choice: SessionTreeChoice) -> str:
    fields = [
        choice.entry_id,
        choice.label,
        choice.tree_label or "",
        choice.copy_text or "",
    ]
    return " ".join(" ".join(field.casefold().split()) for field in fields)


def _tree_search_character(event: Key) -> str | None:
    character = event.character
    if character is None or len(character) != 1:
        return None
    if character.casefold() in {"s", "c"}:
        return None
    if ord(character) < 32 or ord(character) == 127:
        return None
    return character


def _tree_choice_has_children(
    choice: SessionTreeChoice,
    choices: Sequence[SessionTreeChoice],
) -> bool:
    return any(candidate.parent_entry_id == choice.entry_id for candidate in choices)


def _tree_choice_is_branch_foldable(
    choice: SessionTreeChoice,
    choices: Sequence[SessionTreeChoice],
) -> bool:
    children_by_parent, parent_by_id = _visible_tree_relationships(choices)
    if not children_by_parent.get(choice.entry_id):
        return False
    parent_id = parent_by_id.get(choice.entry_id)
    if parent_id is None:
        return True
    return len(children_by_parent.get(parent_id, ())) > 1


def _tree_branch_segment_index(
    choices: Sequence[SessionTreeChoice],
    selected_index: int,
    *,
    direction: Literal["up", "down"],
) -> int:
    if selected_index < 0 or selected_index >= len(choices):
        return 0
    children_by_parent, parent_by_id = _visible_tree_relationships(choices)
    index_by_id = {choice.entry_id: index for index, choice in enumerate(choices)}
    current_id = choices[selected_index].entry_id

    if direction == "down":
        while True:
            children = children_by_parent.get(current_id, ())
            if not children:
                return index_by_id[current_id]
            if len(children) > 1:
                return index_by_id[children[0]]
            current_id = children[0]

    while True:
        parent_id = parent_by_id.get(current_id)
        if parent_id is None:
            return index_by_id[current_id]
        siblings = children_by_parent.get(parent_id, ())
        current_index = index_by_id[current_id]
        if len(siblings) > 1 and current_index < selected_index:
            return current_index
        current_id = parent_id


def _visible_tree_relationships(
    choices: Sequence[SessionTreeChoice],
) -> tuple[dict[str | None, tuple[str, ...]], dict[str, str | None]]:
    visible_ids = {choice.entry_id for choice in choices}
    source_parent_by_id = {choice.entry_id: choice.parent_entry_id for choice in choices}
    children_by_parent_lists: dict[str | None, list[str]] = {None: []}
    parent_by_id: dict[str, str | None] = {}

    for choice in choices:
        parent_id = choice.parent_entry_id
        seen: set[str] = set()
        while parent_id is not None and parent_id not in visible_ids and parent_id not in seen:
            seen.add(parent_id)
            parent_id = source_parent_by_id.get(parent_id)
        if parent_id not in visible_ids:
            parent_id = None
        parent_by_id[choice.entry_id] = parent_id
        children_by_parent_lists.setdefault(parent_id, []).append(choice.entry_id)

    return (
        {
            parent_id: tuple(child_ids)
            for parent_id, child_ids in children_by_parent_lists.items()
        },
        parent_by_id,
    )


def _tree_choice_has_folded_ancestor(
    choice: SessionTreeChoice,
    *,
    choices_by_id: dict[str, SessionTreeChoice],
    folded_entry_ids: set[str],
) -> bool:
    parent_id = choice.parent_entry_id
    seen: set[str] = set()
    while parent_id is not None and parent_id not in seen:
        if parent_id in folded_entry_ids:
            return True
        seen.add(parent_id)
        parent = choices_by_id.get(parent_id)
        if parent is None:
            return False
        parent_id = parent.parent_entry_id
    return False


def _tree_picker_label(
    choice: SessionTreeChoice,
    *,
    theme: TuiTheme,
    show_label_timestamps: bool = False,
) -> Text:
    marker = "* " if choice.active else "  "
    label = choice.label
    indent_width = len(label) - len(label.lstrip(" "))
    indent = label[:indent_width]
    body = label[indent_width:]
    author, separator, rest = body.partition(":")
    text = Text(f"{marker}{indent}")
    if choice.tree_label:
        text.append(f"[{choice.tree_label}] ", style=theme.accent)
        if show_label_timestamps and choice.tree_label_timestamp is not None:
            text.append(f"{_session_updated_at_label(choice.tree_label_timestamp)} ")
    if separator:
        text.append(author, style=theme.accent)
        text.append(f"{separator}{rest}")
    else:
        text.append(body)
    return text


def _user_message_picker_label(choice: SessionTreeChoice, index: int, total: int) -> str:
    preview = " ".join((choice.copy_text or choice.label).split())
    if preview.startswith("user:"):
        preview = preview.partition(":")[2].strip()
    if len(preview) > 72:
        preview = f"{preview[:69]}..."
    return f"Message {index + 1} of {total}: {preview}"


def _active_tree_choice_index(choices: Sequence[SessionTreeChoice]) -> int:
    return _tree_choice_index(choices, None)


def _tree_choice_index(choices: Sequence[SessionTreeChoice], entry_id: str | None) -> int:
    if entry_id is not None:
        for index, choice in enumerate(choices):
            if choice.entry_id == entry_id:
                return index
    for index, choice in enumerate(choices):
        if choice.active:
            return index
    return 0


def _session_updated_at_label(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _named_session_title(title: str | None) -> str | None:
    if title is None:
        return None
    stripped = title.strip()
    if not stripped or stripped.lower() == "untitled session":
        return None
    return stripped


def _login_provider_label(provider: ProviderCatalogEntry) -> str:
    return f"{provider.display_name}\n  {provider.name}"


def _subscription_login_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    return tuple(provider for provider in providers if provider.kind == "openai-codex")


def _api_key_login_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    return tuple(provider for provider in providers if provider.kind != "openai-codex")


def _stored_credential_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    credential_store = FileCredentialStore()
    return tuple(
        provider
        for provider in providers
        if _credential_store_has_entry(credential_store, provider.credential_name)
    )


def _credential_store_has_entry(
    credential_store: FileCredentialStore,
    credential_name: str,
) -> bool:
    return (
        credential_store.get(credential_name) is not None
        or credential_store.get_oauth(credential_name) is not None
    )


def _theme_picker_label(theme_name: TuiThemeName, *, current_theme: TuiThemeName) -> str:
    marker = "✓" if theme_name == current_theme else " "
    return f"{marker} {theme_name}"


def _settings_picker_items(settings: TuiSettings) -> tuple[SettingsPickerItem, ...]:
    return (
        SettingsPickerItem(
            key="theme",
            label="Theme",
            value=settings.theme,
        ),
        SettingsPickerItem(
            key="auto_copy_selection",
            label="Auto-copy selection",
            value="on" if settings.auto_copy_selection else "off",
        ),
        SettingsPickerItem(
            key="double_escape_action",
            label="Double Escape",
            value=settings.double_escape_action,
        ),
        SettingsPickerItem(
            key="tree_filter_mode",
            label="Tree filter mode",
            value=settings.tree_filter_mode,
        ),
    )


def _settings_picker_label(item: SettingsPickerItem) -> str:
    return f"{item.label}: {item.value}"


def _filter_settings_picker_items(
    items: Sequence[SettingsPickerItem],
    query: str,
) -> tuple[SettingsPickerItem, ...]:
    normalized = " ".join(query.casefold().split())
    if not normalized:
        return tuple(items)
    return tuple(
        item
        for item in items
        if normalized in f"{item.label} {item.value} {item.key}".casefold()
    )


def _project_trust_decision_label(decision: ProjectTrustStoreEntry | None) -> str:
    if decision is None:
        return "none"
    label = "trusted" if decision.decision else "untrusted"
    return f"{label} ({decision.path})"


def _project_trust_option_label(
    option: ProjectTrustOption,
    saved_decision: ProjectTrustStoreEntry | None,
) -> str:
    marker = (
        "✓ "
        if option.saved_path is not None
        and saved_decision is not None
        and option.saved_path == saved_decision.path
        and option.trusted == saved_decision.decision
        else "  "
    )
    return f"{marker}{option.label}"


def _next_tui_settings(settings: TuiSettings, key: SettingsPickerKey) -> TuiSettings:
    if key == "theme":
        try:
            current_index = BUILTIN_TUI_THEME_NAMES.index(settings.theme)
        except ValueError:
            current_index = -1
        next_theme = BUILTIN_TUI_THEME_NAMES[
            (current_index + 1) % len(BUILTIN_TUI_THEME_NAMES)
        ]
        return TuiSettings(
            keybindings=settings.keybindings,
            theme=next_theme,
            auto_copy_selection=settings.auto_copy_selection,
            double_escape_action=settings.double_escape_action,
            tree_filter_mode=settings.tree_filter_mode,
        )
    if key == "auto_copy_selection":
        return TuiSettings(
            keybindings=settings.keybindings,
            theme=settings.theme,
            auto_copy_selection=not settings.auto_copy_selection,
            double_escape_action=settings.double_escape_action,
            tree_filter_mode=settings.tree_filter_mode,
        )
    if key == "double_escape_action":
        double_escape_actions: tuple[Literal["tree", "fork", "none"], ...] = (
            "tree",
            "fork",
            "none",
        )
        try:
            current_index = double_escape_actions.index(settings.double_escape_action)
        except ValueError:
            current_index = -1
        return TuiSettings(
            keybindings=settings.keybindings,
            theme=settings.theme,
            auto_copy_selection=settings.auto_copy_selection,
            double_escape_action=double_escape_actions[
                (current_index + 1) % len(double_escape_actions)
            ],
            tree_filter_mode=settings.tree_filter_mode,
        )
    try:
        current_index = TREE_FILTER_MODES.index(cast(TreeFilterMode, settings.tree_filter_mode))
    except ValueError:
        current_index = -1
    return TuiSettings(
        keybindings=settings.keybindings,
        theme=settings.theme,
        auto_copy_selection=settings.auto_copy_selection,
        double_escape_action=settings.double_escape_action,
        tree_filter_mode=TREE_FILTER_MODES[(current_index + 1) % len(TREE_FILTER_MODES)],
    )


def _model_picker_label(
    choice: ModelChoice,
    *,
    current_model: str,
    current_provider: str,
    scoped: bool = False,
) -> str:
    marker = (
        "* "
        if (choice.provider_name == current_provider and choice.model == current_model)
        else "  "
    )
    suffix = " [scoped]" if scoped else ""
    return f"{marker}{choice.provider_name}:{choice.model}{suffix}"


def _filter_model_choices(choices: Sequence[ModelChoice], query: str) -> tuple[ModelChoice, ...]:
    normalized = query.strip().lower()
    if not normalized:
        return tuple(choices)
    return tuple(
        choice
        for choice in choices
        if normalized in choice.provider_name.lower() or normalized in choice.model.lower()
    )


def _command_message_uses_transcript(command_text: str) -> bool:
    """Return whether slash-command output should appear inline in the transcript."""
    command_name = command_text.split(maxsplit=1)[0].casefold()
    return command_name == "/reload"


def _command_message_uses_notification(command_text: str, message: str) -> bool:
    """Return whether slash-command output should appear as a notification."""
    command_name = command_text.split(maxsplit=1)[0].casefold()
    return command_name == "/name" and message.startswith("Session renamed: ")


def _command_output_title(command_text: str) -> str:
    command_name = command_text.split(maxsplit=1)[0].removeprefix("/")
    return f"/{command_name or 'help'}"


def _is_thinking_cycle_key(key: str, configured_key: str) -> bool:
    if key == configured_key:
        return True
    return configured_key == "shift+tab" and key == "backtab"


def _tool_update_has_pipeline_stage(event: ToolExecutionUpdateEvent) -> bool:
    if event.data is None:
        return False
    return any(key in event.data for key in ("memory_stage", "pipeline_stage", "stage"))


_CLIPBOARD_TEXT_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("wl-paste", "--no-newline"),
    ("xclip", "-selection", "clipboard", "-o"),
    ("xsel", "--clipboard", "--output"),
)
_SUPPORTED_CLIPBOARD_IMAGE_MIME_TYPES: tuple[str, ...] = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
)
_CLIPBOARD_IMAGE_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


@dataclass(frozen=True, slots=True)
class _ClipboardImage:
    bytes: bytes
    mime_type: str


async def _read_clipboard_text(timeout_s: float = 0.75) -> str | None:
    """Return plain text from the system clipboard when a supported backend is available."""
    for args in _CLIPBOARD_TEXT_COMMANDS:
        stdout = await _run_clipboard_command(args, timeout_s=timeout_s)
        if stdout is None:
            continue
        text = stdout.decode("utf-8", errors="replace")
        if text:
            return text
    return None


async def _read_clipboard_image() -> _ClipboardImage | None:
    """Return image bytes from Wayland or X11 clipboards when available."""
    wayland = await _read_clipboard_image_via_wl_paste()
    if wayland is not None:
        return wayland
    return await _read_clipboard_image_via_xclip()


async def _read_clipboard_image_via_wl_paste() -> _ClipboardImage | None:
    targets = await _run_clipboard_command(("wl-paste", "--list-types"), timeout_s=1.0)
    if targets is None:
        return None
    mime_type = _select_preferred_image_mime_type(
        targets.decode("utf-8", errors="replace").splitlines()
    )
    if mime_type is None:
        return None
    data = await _run_clipboard_command(
        ("wl-paste", "--type", mime_type, "--no-newline"),
        timeout_s=3.0,
    )
    if not data:
        return None
    return _ClipboardImage(bytes=data, mime_type=_base_mime_type(mime_type))


async def _read_clipboard_image_via_xclip() -> _ClipboardImage | None:
    targets = await _run_clipboard_command(
        ("xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"),
        timeout_s=1.0,
    )
    candidate_mime_types: list[str] = []
    if targets is not None:
        preferred = _select_preferred_image_mime_type(
            targets.decode("utf-8", errors="replace").splitlines()
        )
        if preferred is not None:
            candidate_mime_types.append(preferred)
    candidate_mime_types.extend(_SUPPORTED_CLIPBOARD_IMAGE_MIME_TYPES)
    for mime_type in dict.fromkeys(candidate_mime_types):
        data = await _run_clipboard_command(
            ("xclip", "-selection", "clipboard", "-t", mime_type, "-o"),
            timeout_s=3.0,
        )
        if data:
            return _ClipboardImage(bytes=data, mime_type=_base_mime_type(mime_type))
    return None


async def _run_clipboard_command(args: tuple[str, ...], *, timeout_s: float) -> bytes | None:
    if shutil.which(args[0]) is None:
        return None
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except (OSError, TimeoutError):
        if process is not None and process.returncode is None:
            process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
        return None
    if process.returncode != 0:
        return None
    return stdout


def _write_clipboard_image_to_temp(image: _ClipboardImage) -> Path:
    ext = _CLIPBOARD_IMAGE_EXTENSIONS.get(_base_mime_type(image.mime_type), "png")
    with tempfile.NamedTemporaryFile(
        prefix="tau-clipboard-",
        suffix=f".{ext}",
        delete=False,
    ) as image_file:
        image_file.write(image.bytes)
        return Path(image_file.name)


def _select_preferred_image_mime_type(mime_types: Sequence[str]) -> str | None:
    normalized = [
        (raw.strip(), _base_mime_type(raw))
        for raw in mime_types
        if raw.strip()
    ]
    for preferred in _SUPPORTED_CLIPBOARD_IMAGE_MIME_TYPES:
        for raw, base in normalized:
            if base == preferred:
                return raw
    return None


def _base_mime_type(mime_type: str) -> str:
    return mime_type.split(";", maxsplit=1)[0].strip().lower()


def _external_editor_command() -> str | None:
    """Return the configured external editor command, if any."""
    command = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if command is None or not command.strip():
        return None
    return command.strip()


async def _edit_text_with_external_editor(command: str, content: str) -> str | None:
    """Edit text in an external editor and return updated content on success."""
    with tempfile.TemporaryDirectory(prefix="tau-editor-") as temp_dir:
        prompt_path = Path(temp_dir) / "prompt.md"
        prompt_path.write_text(content, encoding="utf-8")
        args = [*shlex.split(command), str(prompt_path)]
        if len(args) == 1:
            return None
        process = await asyncio.create_subprocess_exec(*args)
        exit_code = await process.wait()
        if exit_code != 0:
            return None
        return prompt_path.read_text(encoding="utf-8").removesuffix("\n")


def _theme_css_variables(theme: TuiTheme) -> dict[str, str]:
    return {
        "tau-screen-background": theme.screen_background,
        "tau-screen-text": theme.screen_text,
        "tau-chrome-background": theme.chrome_background,
        "tau-chrome-text": theme.chrome_text,
        "tau-muted-text": theme.muted_text,
        "tau-sidebar-background": theme.sidebar_background,
        "tau-border": theme.border,
        "tau-transcript-background": theme.transcript_background,
        "tau-prompt-background": theme.prompt_background,
        "tau-prompt-text": theme.prompt_text,
        "tau-prompt-border": theme.prompt_border,
        "tau-autocomplete-background": theme.autocomplete_background,
        "tau-accent": theme.accent,
        "tau-highlight-background": theme.highlight_background,
        "tau-highlight-text": theme.highlight_text,
        "tau-markdown-highlight": theme.markdown_heading,
        "tau-markdown-table-header": theme.markdown_table_header,
        "tau-markdown-table-border": theme.markdown_table_border,
        "tau-markdown-inline-code": theme.markdown_inline_code,
        "tau-markdown-code-block-background": theme.markdown_code_block_background,
        "tau-markdown-link": theme.markdown_link,
        "tau-markdown-bullet": theme.markdown_bullet,
        "footer-background": theme.chrome_background,
        "footer-foreground": theme.chrome_text,
        "footer-description-background": theme.chrome_background,
        "footer-description-foreground": theme.chrome_text,
        "footer-key-background": theme.chrome_background,
        "footer-key-foreground": theme.accent,
        "footer-item-background": theme.chrome_background,
    }


def _render_queued_messages(state: TuiState, *, theme: TuiTheme) -> Group:
    """Render queued prompts stacked above the prompt input."""
    rows: list[Text] = []
    for message in state.queued_steering:
        row = Text("↪ steering · queued: ", style=theme.muted_text)
        row.append(_queued_message_preview(message), style=theme.prompt_text)
        rows.append(row)
    for message in state.queued_follow_up:
        row = Text("↳ follow-up · queued: ", style=theme.muted_text)
        row.append(_queued_message_preview(message), style=theme.prompt_text)
        rows.append(row)
    return Group(*rows)


def _queued_message_preview(message: str) -> str:
    """Return the single-line preview shown above the prompt."""
    lines = message.splitlines()
    return lines[0] if lines else ""


def _last_assistant_text(state: TuiState) -> str | None:
    """Return the most recent assistant transcript text, if any."""
    for item in reversed(state.items):
        if item.role == "assistant" and item.text:
            return item.text
    return None


def _prompt_footer_mode(
    state: TuiState,
    completion_state: CompletionState,
) -> Literal["normal", "completion", "running"]:
    if completion_state.items:
        return "completion"
    if state.running:
        return "running"
    return "normal"


def _key_hint(key: str) -> str:
    return "+".join(part.capitalize() for part in key.split("+"))


def _app_bindings(keybindings: TuiKeybindings) -> list[Binding]:
    bindings = [
        Binding(keybindings.cancel, "cancel", "Cancel"),
        Binding(keybindings.command_palette, "open_command_palette", "Commands"),
        Binding(keybindings.session_picker, "open_session_picker", "Sessions"),
        *_optional_bindings(
            (
                (keybindings.session_new, "new_session", "New session"),
                (keybindings.session_tree, "open_tree_picker", "Tree"),
                (keybindings.session_fork, "open_fork_picker", "Fork"),
                (keybindings.session_resume, "open_session_picker", "Resume"),
            )
        ),
        Binding(keybindings.thinking_cycle, "cycle_thinking", "Thinking"),
        Binding(keybindings.model_cycle, "cycle_model", "Model"),
        Binding(keybindings.model_picker, "open_model_picker", "Model picker"),
        Binding(
            keybindings.accept_completion,
            "accept_completion",
            "Complete",
            priority=True,
        ),
        Binding(
            keybindings.queue_follow_up,
            "submit_follow_up",
            "Follow-up",
            priority=True,
        ),
        Binding(
            keybindings.completion_next,
            "completion_next",
            "Next completion",
            priority=True,
        ),
        Binding(
            keybindings.completion_previous,
            "completion_previous",
            "Previous completion",
            priority=True,
        ),
        Binding(keybindings.toggle_tool_results, "toggle_tool_results", "Tool results"),
        Binding(keybindings.toggle_thinking, "toggle_thinking", "Thinking tokens"),
        Binding(keybindings.external_editor, "open_external_editor", "Editor"),
        Binding(keybindings.paste_clipboard, "paste_clipboard", "Paste"),
        Binding(keybindings.dequeue_messages, "dequeue_messages", "Restore queued"),
        Binding(keybindings.copy_last_message, "copy_last_message", "Copy last message"),
        Binding(keybindings.copy_message, "clear_prompt", "Clear input"),
        Binding(keybindings.suspend, "suspend_process", "Suspend"),
        Binding(keybindings.quit, "quit", "Quit"),
    ]
    return bindings


def _prompt_bindings(
    keybindings: TuiKeybindings,
    *,
    mode: Literal["normal", "completion", "running"],
) -> list[Binding]:
    if mode == "completion":
        bindings = [
            Binding(
                keybindings.accept_completion,
                "accept_completion",
                "Complete",
                key_display=f"{_key_hint(keybindings.accept_completion)}/Enter",
                priority=True,
            ),
            Binding(
                keybindings.completion_next,
                "completion_next",
                "Choose",
                key_display=(
                    f"{_key_hint(keybindings.completion_previous)}/"
                    f"{_key_hint(keybindings.completion_next)}"
                ),
                priority=True,
            ),
            Binding(keybindings.cancel, "cancel", "Close", priority=True),
        ]
        return bindings + _hidden_prompt_bindings(keybindings, visible_bindings=bindings)
    if mode == "running":
        bindings = [
            Binding("enter", "submit_prompt", "Steer", priority=True),
            Binding(keybindings.queue_follow_up, "submit_follow_up", "Follow-up", priority=True),
            Binding(keybindings.dequeue_messages, "dequeue_messages", "Restore", priority=True),
            Binding(keybindings.cancel, "cancel", "Cancel", priority=True),
            Binding(
                keybindings.toggle_thinking,
                "toggle_thinking",
                "Thinking",
                priority=True,
            ),
            Binding(
                keybindings.toggle_tool_results,
                "toggle_tool_results",
                "Tools",
                priority=True,
            ),
        ]
        return bindings + _hidden_prompt_bindings(keybindings, visible_bindings=bindings)
    bindings = [
        Binding("enter", "submit_prompt", "Submit", priority=True),
        Binding("shift+enter", "insert_newline", "Newline", priority=True),
        Binding(keybindings.command_palette, "open_command_palette", "Commands", priority=True),
        Binding(keybindings.session_picker, "open_session_picker", "Sessions", priority=True),
        *_optional_bindings(
            (
                (keybindings.session_new, "new_session", "New",),
                (keybindings.session_tree, "open_tree_picker", "Tree"),
                (keybindings.session_fork, "open_fork_picker", "Fork"),
                (keybindings.session_resume, "open_session_picker", "Resume"),
            ),
            priority=True,
        ),
        Binding(keybindings.external_editor, "open_external_editor", "Editor", priority=True),
        Binding(keybindings.paste_clipboard, "paste_clipboard", "Paste", priority=True),
        Binding(keybindings.thinking_cycle, "cycle_thinking", "Thinking", priority=True),
        Binding(keybindings.model_cycle, "cycle_model", "Model", priority=True),
        Binding(keybindings.model_picker, "open_model_picker", "Models", priority=True),
        Binding(
            keybindings.copy_message,
            "clear_prompt",
            "Clear",
            priority=True,
        ),
        Binding(keybindings.suspend, "suspend_process", "Suspend", priority=True),
        Binding(keybindings.quit, "quit", "Quit", priority=True),
    ]
    return bindings + _hidden_prompt_bindings(keybindings, visible_bindings=bindings)


def _hidden_prompt_bindings(
    keybindings: TuiKeybindings,
    *,
    visible_bindings: Sequence[Binding],
) -> list[Binding]:
    visible_keys = {key for binding in visible_bindings for key in binding.key.split(",")}
    candidates = (
        (keybindings.command_palette, "open_command_palette"),
        (keybindings.session_picker, "open_session_picker"),
        (keybindings.session_new, "new_session"),
        (keybindings.session_tree, "open_tree_picker"),
        (keybindings.session_fork, "open_fork_picker"),
        (keybindings.session_resume, "open_session_picker"),
        (keybindings.queue_follow_up, "submit_follow_up"),
        (keybindings.dequeue_messages, "dequeue_messages"),
        (keybindings.thinking_cycle, "cycle_thinking"),
        (keybindings.model_cycle, "cycle_model"),
        (keybindings.model_cycle_previous, "cycle_model_previous"),
        (keybindings.model_picker, "open_model_picker"),
        (keybindings.external_editor, "open_external_editor"),
        (keybindings.paste_clipboard, "paste_clipboard"),
        (keybindings.copy_last_message, "copy_last_message"),
        (keybindings.toggle_tool_results, "toggle_tool_results"),
        (keybindings.toggle_thinking, "toggle_thinking"),
        (keybindings.copy_message, "clear_prompt"),
        (keybindings.suspend, "suspend_process"),
        (keybindings.accept_completion, "accept_completion"),
        (keybindings.completion_next, "completion_next"),
        (keybindings.completion_previous, "completion_previous"),
        (keybindings.quit, "quit"),
    )
    return [
        Binding(key, action, show=False, priority=True)
        for key, action in candidates
        if key and key not in visible_keys
    ]


def _optional_bindings(
    entries: Sequence[tuple[str, str, str]],
    *,
    priority: bool = False,
) -> list[Binding]:
    return [
        Binding(key, action, description, priority=priority)
        for key, action, description in entries
        if key
    ]


def _text_end_location(text: str) -> tuple[int, int]:
    """Return the TextArea cursor location at the end of text."""
    line, _, column_text = text.rpartition("\n")
    return (line.count("\n") + 1 if line else 0, len(column_text))


def _format_prompt_error(exc: BaseException, session: CodingSession) -> str:
    detail = str(exc) or type(exc).__name__
    message = f"Error: {detail}"
    log_path = getattr(session, "last_diagnostic_log_path", None)
    if isinstance(log_path, Path):
        return f"{message}\nLog: {log_path}"
    return message


def _attach_diagnostic_log_path_to_error(state: TuiState, session: CodingSession) -> None:
    log_path = getattr(session, "last_diagnostic_log_path", None)
    if not isinstance(log_path, Path) or state.error is None:
        return
    message = f"Error: {state.error}\nLog: {log_path}"
    state.error = message
    for item in reversed(state.items):
        if item.role == "error":
            item.text = message
            return
    state.add_item("error", message)


def _explicit_resume_record(
    manager: SessionManager,
    *,
    session_id: str | None,
) -> CodingSessionRecord | None:
    if session_id is None:
        return None
    record = manager.get_session(session_id)
    if record is None:
        raise RuntimeError(f"Unknown session: {session_id}")
    return record


def _create_startup_session_record(
    manager: SessionManager,
    *,
    cwd: Path,
    selection: ProviderSelection,
) -> CodingSessionRecord:
    try:
        return manager.create_session(
            cwd=cwd,
            model=selection.model,
            provider_name=selection.provider.name,
        )
    except TypeError:
        return manager.create_session(cwd=cwd, model=selection.model)


def _resolve_tui_startup_selection(
    settings: Any,
    *,
    record: Any | None,
    provider_name: str | None,
    model: str | None,
    explicit_resume: bool,
) -> ProviderSelection:
    if provider_name is not None or model is not None:
        return resolve_provider_selection(settings, provider_name=provider_name, model=model)

    if explicit_resume:
        record_selection = _selection_from_session_record(settings, record)
        if record_selection is not None:
            return record_selection

    default_selection = resolve_provider_selection(settings)
    if provider_has_usable_credentials(
        default_selection.provider,
        credential_reader=FileCredentialStore(),
    ):
        return default_selection

    fallback_selection = _first_usable_startup_selection(settings)
    return fallback_selection or default_selection


def _first_usable_startup_selection(settings: Any) -> ProviderSelection | None:
    credential_store = FileCredentialStore()
    for provider in settings.providers:
        if provider_has_usable_credentials(provider, credential_reader=credential_store):
            return ProviderSelection(provider=provider, model=provider.default_model)
    return None


def _selection_from_session_record(settings: Any, record: Any | None) -> ProviderSelection | None:
    if record is None:
        return None
    record_model = getattr(record, "model", None)
    if not isinstance(record_model, str) or not record_model:
        return None

    record_provider = getattr(record, "provider_name", None)
    if isinstance(record_provider, str) and record_provider:
        try:
            return resolve_provider_selection(
                settings,
                provider_name=record_provider,
                model=record_model,
            )
        except Exception:
            return None

    for choice in _usable_scoped_startup_choices(settings):
        if choice.model == record_model:
            return resolve_provider_selection(
                settings,
                provider_name=choice.provider_name,
                model=choice.model,
            )

    for provider in settings.providers:
        if record_model in provider.models:
            return ProviderSelection(provider=provider, model=record_model)
    return None


def _usable_scoped_startup_choices(settings: Any) -> tuple[ModelChoice, ...]:
    credential_store = FileCredentialStore()
    choices: list[ModelChoice] = []
    for item in settings.scoped_models:
        try:
            provider = settings.get_provider(item.provider)
        except Exception:
            continue
        if item.model not in provider.models:
            continue
        if not provider_has_usable_credentials(provider, credential_reader=credential_store):
            continue
        choices.append(ModelChoice(provider_name=item.provider, model=item.model))
    return tuple(choices)


async def run_tui_app(
    *,
    model: str | None,
    cwd: Path,
    session_id: str | None = None,
    new_session: bool = False,
    provider_name: str | None = None,
    auto_compact_token_threshold: int | None = None,
    initial_prompt: str | None = None,
    session_manager: SessionManager | None = None,
) -> None:
    """Create the default provider/session and run the Textual app."""
    if new_session and session_id is not None:
        raise RuntimeError("--resume and --new-session cannot be used together")

    provider_settings = load_provider_settings()
    manager = session_manager or SessionManager()
    record = _explicit_resume_record(
        manager,
        session_id=session_id,
    )
    selection = _resolve_tui_startup_selection(
        provider_settings,
        record=record,
        provider_name=provider_name,
        model=model,
        explicit_resume=session_id is not None,
    )
    startup_message: str | None = None
    runtime_provider_config: ProviderConfig | None = selection.provider
    try:
        provider = create_model_provider(
            selection.provider,
            model=selection.model,
            thinking_level=DEFAULT_THINKING_LEVEL,
        )
    except RuntimeError:
        startup_message = (
            "Login required. Run /login to choose a provider, "
            f"or /login {selection.provider.name} to continue with the current provider."
        )
        provider = LoginRequiredProvider(startup_message)
        runtime_provider_config = None
    session: CodingSession | None = None
    try:
        if record is None:
            record = _create_startup_session_record(
                manager,
                cwd=cwd,
                selection=selection,
            )

        session = await CodingSession.load(
            CodingSessionConfig(
                provider=provider,
                model=record.model or selection.model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                session_id=record.id,
                session_manager=manager,
                provider_name=selection.provider.name,
                provider_settings=provider_settings,
                runtime_provider_config=runtime_provider_config,
                auto_compact_token_threshold=auto_compact_token_threshold,
            )
        )
        app = TauTuiApp(
            session,
            tui_settings=load_tui_settings(),
            startup_message=startup_message,
            initial_prompt=initial_prompt,
        )
        await app.run_async()
    finally:
        if session is not None:
            close_session = getattr(session, "aclose", None)
            if close_session is not None:
                await close_session()
        await provider.aclose()


class _PtyProofRealAppSessionState:
    thinking_level = "medium"
    loop_monitor_status = None


class _PtyProofRealAppSession:
    """Minimal session for PTY proof mode that still runs ``TauTuiApp``."""

    def __init__(self, *, cwd: Path) -> None:
        self.cwd = cwd
        self.provider_name = "pty-proof"
        self.model = "real-tau-tui-app"
        self.available_models = ("real-tau-tui-app",)
        self.available_model_choices = (
            ModelChoice(provider_name="pty-proof", model="real-tau-tui-app"),
        )
        self.scoped_model_choices: tuple[ModelChoice, ...] = ()
        self.available_providers = ("pty-proof",)
        self.tools: tuple[AgentTool, ...] = ()
        self.skills: tuple[object, ...] = ()
        self.prompt_templates: tuple[object, ...] = ()
        self.context_files = (
            ProjectContextFile(path=str(cwd / "AGENTS.md"), content="PTY proof mode."),
        )
        self.context_token_estimate = 0
        self.auto_compact_token_threshold = 200000
        self.context_window_tokens = 216384
        self.thinking_level = "medium"
        self.available_thinking_levels = ("off", "minimal", "low", "medium", "high")
        self.resource_diagnostics: tuple[object, ...] = ()
        self.session_manager = None
        self.state = _PtyProofRealAppSessionState()
        self.messages: tuple[AgentMessage, ...] = ()

    def handle_command(self, text: str) -> CommandResult:
        del text
        return CommandResult(handled=False)

    def queue_update_event(self) -> QueueUpdateEvent:
        return QueueUpdateEvent(steering=(), follow_up=())

    async def prompt(
        self,
        text: str,
        *,
        streaming_behavior: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        del text, streaming_behavior
        if False:
            yield AgentStartEvent()


def run_pty_proof_real_app() -> None:
    """Run the real ``TauTuiApp`` class in deterministic PTY proof mode."""

    os.environ.setdefault("TAU_TUI_PTY_PROOF", "1")
    cwd = Path.cwd()
    app = TauTuiApp(cast(CodingSession, _PtyProofRealAppSession(cwd=cwd)))
    app.run()


def main() -> None:
    """Run module entrypoints for ``python -m tau_coding.tui.app``."""

    if "--pty-proof-real-app" in sys.argv[1:]:
        run_pty_proof_real_app()
        return
    raise SystemExit("Usage: python -m tau_coding.tui.app --pty-proof-real-app")


if __name__ == "__main__":
    main()
