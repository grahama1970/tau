"""Small Textual widgets for Tau's interactive TUI."""

import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from subprocess import TimeoutExpired, run
from typing import Any, ClassVar, Literal, Protocol
from urllib.parse import unquote, urlparse

from pygments.lexers import get_lexer_by_name  # type: ignore[import-untyped]
from pygments.util import ClassNotFound  # type: ignore[import-untyped]
from rich.align import Align
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.markdown import CodeBlock, Heading, Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Style as TextualStyle
from textual.events import Resize
from textual.geometry import Offset, Spacing
from textual.selection import Selection
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets import Static
from textual.widgets.markdown import MarkdownBlock, MarkdownStream

from tau_agent.tools import AgentTool
from tau_coding.graph_artifacts import render_markdown_graph_artifacts
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.session_stats import SessionStats
from tau_coding.skills import Skill
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tui.autocomplete import CompletionState
from tau_coding.tui.config import TAU_DARK_THEME, TuiRoleStyle, TuiTheme
from tau_coding.tui.state import (
    DEFAULT_THINKING_PLACEHOLDER_TEXT,
    TOOL_RESULT_PREVIEW_LINES,
    ChatItem,
    LoopMonitorStatus,
    ToolImagePayload,
    TuiState,
)
from tau_coding.tui.terminal_image import TerminalImage, TerminalImageOptions

TAU_SIDEBAR_LOGO = "τ = 2π"
TOOL_RESULT_VISUAL_PREVIEW_LINES = TOOL_RESULT_PREVIEW_LINES + 1
SIDEBAR_BULLET_LIST_LIMIT = 5
VISIBLE_TAB_REPLACEMENT = "   "
OSC133_ZONE_START = "\x1b]133;A\x07"
OSC133_ZONE_END = "\x1b]133;B\x07"
OSC133_ZONE_FINAL = "\x1b]133;C\x07"
MARKDOWN_IMAGE_PATTERN = re.compile(
    r"!\[[^\]\n]*\]\((?P<target><[^>\n]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)"
)
MARKDOWN_IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
MAX_MARKDOWN_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    """Plain transcript line used by compatibility inspection helpers."""

    text: str


@dataclass(frozen=True, slots=True)
class VisualPreviewText:
    """Width-aware text preview that limits wrapped terminal rows."""

    text: str
    max_visual_lines: int
    style: str
    tail: bool = False

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        width = max(1, options.max_width)
        wrapped_lines = Text(
            _normalize_terminal_display_text(self.text),
            style=self.style,
            overflow="fold",
            no_wrap=False,
        ).wrap(console, width, overflow="fold", no_wrap=False)
        if len(wrapped_lines) <= self.max_visual_lines:
            yield Text("\n").join(wrapped_lines)
            return

        if self.tail:
            visible_lines = wrapped_lines[-self.max_visual_lines :]
            skipped = len(wrapped_lines) - len(visible_lines)
            parts: list[Text] = [
                Text(
                    "[Preview only: "
                    f"{skipped} earlier wrapped line{'s' if skipped != 1 else ''} "
                    "hidden from the TUI.]",
                    style=self.style,
                ),
                Text(""),
                *visible_lines,
            ]
        else:
            visible_lines = wrapped_lines[: self.max_visual_lines]
            skipped = len(wrapped_lines) - len(visible_lines)
            parts = [
                *visible_lines,
                Text(""),
                Text(
                    "[Preview only: "
                    f"{skipped} more wrapped line{'s' if skipped != 1 else ''} "
                    "hidden from the TUI.]",
                    style=self.style,
                ),
            ]
        yield Text("\n").join(parts)


@dataclass(frozen=True, slots=True)
class Osc133Zone:
    """Wrap a renderable in shell-integration prompt/response zone markers."""

    renderable: RenderableType

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        lines = console.render_lines(self.renderable, options, pad=False)
        if not lines:
            return
        lines[0].insert(0, Segment(OSC133_ZONE_START))
        lines[-1].insert(0, Segment(OSC133_ZONE_END + OSC133_ZONE_FINAL))
        for line in lines:
            yield from line
            yield Segment.line()


def _normalize_terminal_display_text(text: str) -> str:
    """Normalize text for terminal display without changing transcript content."""
    if "\t" not in text and "\r" not in text:
        return text

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\t" not in normalized:
        return normalized

    parts: list[str] = []
    index = 0
    while index < len(normalized):
        if normalized[index] == "\x1b":
            end = _terminal_escape_sequence_end(normalized, index)
            parts.append(normalized[index:end])
            index = end
            continue
        parts.append(VISIBLE_TAB_REPLACEMENT if normalized[index] == "\t" else normalized[index])
        index += 1
    return "".join(parts)


def _terminal_escape_sequence_end(text: str, start: int) -> int:
    if start + 1 >= len(text):
        return start + 1

    introducer = text[start + 1]
    if introducer in {"]", "P", "^", "_"}:
        bel = text.find("\x07", start + 2)
        st = text.find("\x1b\\", start + 2)
        candidates = [end for end in (bel + 1, st + 2) if end > 1]
        return min(candidates) if candidates else len(text)

    if introducer == "[":
        index = start + 2
        while index < len(text):
            codepoint = ord(text[index])
            if 0x40 <= codepoint <= 0x7E:
                return index + 1
            index += 1
        return len(text)

    if introducer in {"(", ")", "*", "+", "-", ".", "/", "#", "%"}:
        return min(start + 3, len(text))
    return start + 2


class SessionSummarySource(Protocol):
    """Session attributes displayed by the sidebar."""

    @property
    def cwd(self) -> Path: ...

    @property
    def model(self) -> str: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def available_models(self) -> Sequence[str]: ...

    @property
    def available_providers(self) -> Sequence[str]: ...

    @property
    def tools(self) -> Sequence[AgentTool]: ...

    @property
    def skills(self) -> Sequence[Skill]: ...

    @property
    def prompt_templates(self) -> Sequence[PromptTemplate]: ...

    @property
    def context_files(self) -> Sequence[ProjectContextFile]: ...

    @property
    def messages(self) -> Sequence[Any]: ...

    @property
    def session_stats(self) -> SessionStats: ...

    @property
    def context_token_estimate(self) -> int: ...

    @property
    def auto_compact_token_threshold(self) -> int | None: ...

    @property
    def context_window_tokens(self) -> int: ...

    @property
    def context_usage(self) -> Any: ...

    @property
    def thinking_level(self) -> str: ...

    @property
    def session_title(self) -> str | None: ...


class SessionSidebar(Static):
    """Compact sidebar with current session metadata."""

    def update_from_session(
        self,
        session: SessionSummarySource,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw the sidebar from current session metadata."""
        self.update(render_session_sidebar(session, theme=theme))


class CompactSessionInfo(Static):
    """Single-line session metadata for narrow TUI layouts."""

    def update_from_session(
        self,
        session: SessionSummarySource,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
    ) -> None:
        """Redraw compact session metadata."""
        self.update(render_compact_session_info(session, theme=theme))


class NonSelectableStatic(Static):
    """Static display text that should not participate in mouse selection."""

    ALLOW_SELECT = False


class TauMarkdownBlock(MarkdownBlock):
    """Markdown block that applies Tau's themed inline link color."""

    def _token_to_content(self, token: Any) -> Any:
        content = super()._token_to_content(token)
        markdown = self._markdown
        if not isinstance(markdown, ThemedMarkdownWidget):
            return content
        link_style = TextualStyle.parse(markdown.tau_link_style)
        spans = []
        for span in content.spans:
            style = span.style
            if isinstance(style, TextualStyle) and "@click" in style.meta:
                style = link_style + style
            spans.append(type(span)(span.start, span.end, style))
        return type(content)(content.plain, spans=spans)


class ThemedMarkdownWidget(TextualMarkdown):
    """Textual Markdown widget reserved for Tau transcript streaming."""

    BLOCKS = {**TextualMarkdown.BLOCKS, "paragraph_open": TauMarkdownBlock}

    DEFAULT_CSS = """
    ThemedMarkdownWidget MarkdownH1,
    ThemedMarkdownWidget MarkdownH2,
    ThemedMarkdownWidget MarkdownH3,
    ThemedMarkdownWidget MarkdownH4,
    ThemedMarkdownWidget MarkdownH5,
    ThemedMarkdownWidget MarkdownH6 {
        color: $tau-markdown-highlight;
        content-align: left middle;
        text-style: bold;
    }

    ThemedMarkdownWidget MarkdownBlock > .code_inline {
        color: $tau-markdown-inline-code !important;
        background: transparent !important;
    }

    ThemedMarkdownWidget MarkdownBullet {
        color: $tau-markdown-bullet;
    }

    ThemedMarkdownWidget MarkdownFence {
        background: $tau-markdown-code-block-background;
    }

    ThemedMarkdownWidget MarkdownFence:light {
        background: $tau-markdown-code-block-background;
    }

    ThemedMarkdownWidget MarkdownTableContent {
        keyline: thin $tau-markdown-table-border;
    }

    ThemedMarkdownWidget MarkdownTableContent > .header {
        color: $tau-markdown-table-header;
        text-style: bold;
    }
    """

    def __init__(
        self,
        markdown: str | None = None,
        *,
        theme: TuiTheme,
        classes: str | None = None,
    ) -> None:
        self.tau_link_style = theme.markdown_link
        super().__init__(
            None if markdown is None else _normalize_terminal_display_text(markdown),
            classes=classes,
        )


class TranscriptMessageWidget(Horizontal):
    """One selectable transcript message with a non-selectable visual gutter."""

    DEFAULT_CSS = """
    TranscriptMessageWidget {
        width: 1fr;
        height: auto;
        margin: 1 1 2 0;
    }

    TranscriptMessageWidget > .transcript-message-gutter {
        width: 1;
        height: auto;
    }

    TranscriptMessageWidget > .transcript-message-body {
        width: 1fr;
        height: auto;
        padding: 0 1 0 1;
    }

    TranscriptMessageWidget > .transcript-message-body-stack {
        width: 1fr;
        height: auto;
    }

    TranscriptMessageWidget .transcript-markdown-body > MarkdownParagraph {
        margin: 0 0 1 0;
    }

    TranscriptMessageWidget .transcript-markdown-image {
        width: 1fr;
        height: auto;
        margin: 1 0 0 0;
    }

    """

    def __init__(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme,
        show_tool_results: bool,
        show_images: bool = True,
        image_width_cells: int | None = None,
        output_padding_x: int = 1,
        tool_results_key_hint: str = "Ctrl+O",
    ) -> None:
        self.item = item
        self.selection_text = transcript_item_selection_text(
            item,
            show_tool_results=show_tool_results,
            tool_results_key_hint=tool_results_key_hint,
        )
        self._markdown_text = _transcript_item_markdown(
            item,
            show_tool_results=show_tool_results,
            tool_results_key_hint=tool_results_key_hint,
        )
        self._theme = theme
        self._show_images = show_images
        self._image_width_cells = image_width_cells
        self._role_style = _chat_item_role_style(item, theme)
        self._output_padding_x = output_padding_x
        super().__init__(classes="transcript-message")

    def compose(self) -> Any:
        gutter = NonSelectableStatic("▌", classes="transcript-message-gutter")
        gutter.styles.color = self._role_style.border
        body = self._body_widget()
        yield gutter
        yield body

    def _body_widget(self) -> Static | ThemedMarkdownWidget | Vertical:
        if _use_plain_transcript_body(self.item):
            body = Static(
                _transcript_plain_body_text(
                    self.item,
                    text=self.selection_text,
                    body_style=self._role_style.body,
                    theme=self._theme,
                    show_images=self._show_images,
                    image_width_cells=self._image_width_cells,
                ),
                expand=True,
                shrink=True,
                markup=False,
                classes="transcript-message-body transcript-plain-body",
            )
        else:
            body = ThemedMarkdownWidget(
                self._markdown_text,
                theme=self._theme,
                classes="transcript-message-body transcript-markdown-body",
            )
            foreground, background = _split_rich_style_colors(self._role_style.body)
            if foreground:
                body.styles.color = foreground
            if background:
                body.styles.background = background
            image_payloads = _markdown_visual_payloads(self._markdown_text)
            if image_payloads:
                body.styles.padding = Spacing.unpack((0, 0))
                image_widgets = [
                    Static(
                        _render_tool_image(
                            payload,
                            show_images=self._show_images,
                            image_width_cells=self._image_width_cells,
                        ),
                        expand=True,
                        shrink=True,
                        markup=False,
                        classes="transcript-markdown-image",
                    )
                    for payload in image_payloads
                ]
                stack = Vertical(
                    body,
                    *image_widgets,
                    classes="transcript-message-body transcript-message-body-stack",
                )
                stack.styles.padding = Spacing.unpack((0, self._output_padding_x))
                return stack
        body.styles.padding = Spacing.unpack((0, self._output_padding_x))
        return body

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return selected plain text from this message, not rendered Markdown markup."""
        selected_text = _extract_text_selection(self.selection_text, selection)
        if not selected_text:
            return None
        return selected_text, "\n"


class StreamingTranscriptMessageWidget(ThemedMarkdownWidget):
    """One assistant or thinking Markdown block that accepts streamed fragments."""

    DEFAULT_CSS = """
    StreamingTranscriptMessageWidget {
        width: 1fr;
        height: auto;
        margin: 1 1 2 1;
        padding: 0 1 0 0;
    }

    StreamingTranscriptMessageWidget > MarkdownParagraph {
        margin: 0 0 1 0;
    }
    """

    def __init__(self, item: ChatItem, *, theme: TuiTheme, output_padding_x: int = 1) -> None:
        if item.role not in {"assistant", "thinking"}:
            raise ValueError("Streaming transcript widgets only support assistant/thinking items")
        self.item = item
        self.selection_text = item.text
        self._stream: MarkdownStream | None = None
        super().__init__(_normalize_terminal_display_text(item.text), theme=theme)
        self.add_class("transcript-message")
        self.styles.padding = Spacing.unpack((0, output_padding_x))

    @property
    def stream(self) -> MarkdownStream:
        if self._stream is None:
            self._stream = self.get_stream(self)
        return self._stream

    async def append_fragment(self, fragment: str) -> None:
        """Append streamed markdown using the same renderer as finalized messages."""
        if not fragment:
            return
        self.item.text += fragment
        self.selection_text += fragment
        self._stream = None
        await self.update(_normalize_terminal_display_text(self.item.text))

    async def replace_text(self, text: str) -> None:
        """Replace the current markdown text, usually with the final provider message."""
        self.item.text = text
        self.selection_text = text
        self._stream = None
        await self.update(_normalize_terminal_display_text(text))

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return selected text from this streamed message block."""
        selected_text = _extract_text_selection(self.selection_text, selection)
        if not selected_text:
            return None
        return selected_text, "\n"


class TranscriptView(VerticalScroll):
    """Scrollable transcript view backed by individual selectable message widgets."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        for legacy_option in ("wrap", "highlight", "markup"):
            kwargs.pop(legacy_option, None)
        min_width = kwargs.pop("min_width", None)
        self.clear_on_shrink = kwargs.pop("clear_on_shrink", False)
        self.output_padding_x = kwargs.pop("output_padding_x", 1)
        super().__init__(*args, **kwargs)
        self.min_width = min_width
        if min_width is not None:
            self.styles.min_width = min_width
        self._render_state: TuiState | None = None
        self._render_theme: TuiTheme = TAU_DARK_THEME
        self._show_images = True
        self._image_width_cells: int | None = None
        self._last_render_width = 0
        self._active_assistant_widget: StreamingTranscriptMessageWidget | None = None
        self._active_thinking_widget: StreamingTranscriptMessageWidget | None = None
        self._hidden_thinking_placeholder_visible = False
        self.tool_results_key_hint = "Ctrl+O"

    def on_mount(self) -> None:
        """Follow new transcript content until the user scrolls away."""
        self.anchor()

    def follow_output(self) -> None:
        """Return to follow mode for a user-driven turn or explicit jump to bottom."""
        self.anchor()

    @property
    def _should_follow_output(self) -> bool:
        """Return whether new content should keep the viewport pinned to the bottom."""
        return self.is_vertical_scroll_end or (self.is_anchored and not self._anchor_released)

    def update_from_state(
        self,
        state: TuiState,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_images: bool = True,
        image_width_cells: int | None = None,
        clear_on_shrink: bool | None = None,
        output_padding_x: int | None = None,
        tool_results_key_hint: str | None = None,
    ) -> None:
        """Redraw the transcript from display state."""
        self._render_state = state
        self._render_theme = theme
        self._show_images = show_images
        self._image_width_cells = image_width_cells
        if clear_on_shrink is not None:
            self.clear_on_shrink = clear_on_shrink
        if output_padding_x is not None:
            self.output_padding_x = output_padding_x
        if tool_results_key_hint is not None:
            self.tool_results_key_hint = tool_results_key_hint
        self._redraw(scroll_end=self._should_follow_output)

    def on_resize(self, event: Resize) -> None:
        """Re-render transcript entries when the terminal width changes."""
        del event
        if self._render_state is None:
            return
        width = self.scrollable_content_region.width
        if width <= 0 or width == self._last_render_width:
            return
        was_at_end = self.is_vertical_scroll_end
        self._redraw(scroll_end=was_at_end)
        self.scroll_to(x=0, animate=False, immediate=True)

    def _redraw(self, *, scroll_end: bool) -> None:
        state = self._render_state
        if state is None:
            return
        theme = self._render_theme
        previous_child_count = len(
            [
                child
                for child in self.children
                if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
            ]
        )
        self._last_render_width = self.scrollable_content_region.width
        self.remove_children(
            [
                child
                for child in self.children
                if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
            ]
        )
        self._active_assistant_widget = None
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = False
        hidden_thinking_placeholder = False
        for item in state.items:
            if item.role == "thinking" and not state.show_thinking:
                if not hidden_thinking_placeholder:
                    self.mount(
                        TranscriptMessageWidget(
                            ChatItem(
                                role="thinking",
                                text=state.thinking_placeholder_text,
                            ),
                            theme=theme,
                            show_tool_results=state.show_tool_results,
                            show_images=self._show_images,
                            image_width_cells=self._image_width_cells,
                            output_padding_x=self.output_padding_x,
                            tool_results_key_hint=self.tool_results_key_hint,
                        )
                    )
                    hidden_thinking_placeholder = True
                continue
            hidden_thinking_placeholder = False
            self.mount(
                TranscriptMessageWidget(
                    item,
                    theme=theme,
                    show_tool_results=state.show_tool_results or item.always_show_tool_result,
                    show_images=self._show_images,
                    image_width_cells=self._image_width_cells,
                    output_padding_x=self.output_padding_x,
                    tool_results_key_hint=self.tool_results_key_hint,
                )
            )
        if state.assistant_buffer:
            self.mount(
                TranscriptMessageWidget(
                    ChatItem(role="assistant", text=state.assistant_buffer),
                    theme=theme,
                    show_tool_results=state.show_tool_results,
                    show_images=self._show_images,
                    image_width_cells=self._image_width_cells,
                    output_padding_x=self.output_padding_x,
                    tool_results_key_hint=self.tool_results_key_hint,
                )
            )
        self.refresh(layout=True)
        rendered_child_count = len(state.items) + (1 if state.assistant_buffer else 0)
        if self.clear_on_shrink and rendered_child_count < previous_child_count:
            self.app.refresh(layout=True)
        if scroll_end:
            self.scroll_end(animate=False)

    async def append_item(
        self,
        item: ChatItem,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_tool_results: bool = False,
        show_images: bool = True,
        image_width_cells: int | None = None,
        output_padding_x: int | None = None,
        tool_results_key_hint: str | None = None,
        scroll_end: bool = False,
    ) -> TranscriptMessageWidget | StreamingTranscriptMessageWidget:
        """Append one transcript item without rebuilding previous blocks."""
        self._render_theme = theme
        if output_padding_x is not None:
            self.output_padding_x = output_padding_x
        if tool_results_key_hint is not None:
            self.tool_results_key_hint = tool_results_key_hint
        widget = _transcript_widget(
            item,
            theme=theme,
            show_tool_results=show_tool_results,
            show_images=show_images,
            image_width_cells=image_width_cells,
            output_padding_x=self.output_padding_x,
            tool_results_key_hint=self.tool_results_key_hint,
        )
        await self.mount(widget)
        self._active_assistant_widget = None
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = False
        self._last_render_width = self.scrollable_content_region.width
        self.refresh(layout=True)
        if scroll_end:
            self.scroll_end(animate=False)
        return widget

    async def start_assistant_message(
        self,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        output_padding_x: int | None = None,
        scroll_end: bool = False,
    ) -> StreamingTranscriptMessageWidget:
        """Create the active assistant message widget if needed."""
        if output_padding_x is not None:
            self.output_padding_x = output_padding_x
        if self._active_assistant_widget is not None:
            return self._active_assistant_widget
        widget = StreamingTranscriptMessageWidget(
            ChatItem(role="assistant", text=""),
            theme=theme,
            output_padding_x=self.output_padding_x,
        )
        self._render_theme = theme
        await self.mount(widget)
        self._active_assistant_widget = widget
        self._last_render_width = self.scrollable_content_region.width
        if scroll_end:
            self.scroll_end(animate=False)
        return widget

    async def append_assistant_delta(
        self,
        delta: str,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        output_padding_x: int | None = None,
        scroll_end: bool = False,
    ) -> None:
        """Append streamed assistant text to the active message widget."""
        self._active_thinking_widget = None
        self._hidden_thinking_placeholder_visible = False
        widget = await self.start_assistant_message(
            theme=theme,
            output_padding_x=output_padding_x,
            scroll_end=scroll_end,
        )
        await widget.append_fragment(delta)
        if scroll_end:
            self.scroll_end(animate=False)

    async def append_thinking_delta(
        self,
        delta: str,
        *,
        theme: TuiTheme = TAU_DARK_THEME,
        show_thinking: bool,
        placeholder_text: str = DEFAULT_THINKING_PLACEHOLDER_TEXT,
        output_padding_x: int | None = None,
        scroll_end: bool = False,
    ) -> None:
        """Append streamed thinking text or one hidden-thinking placeholder."""
        if output_padding_x is not None:
            self.output_padding_x = output_padding_x
        if not show_thinking:
            if self._hidden_thinking_placeholder_visible:
                return
            await self.append_item(
                ChatItem(
                    role="thinking",
                    text=placeholder_text,
                ),
                theme=theme,
                output_padding_x=self.output_padding_x,
                scroll_end=scroll_end,
            )
            self._hidden_thinking_placeholder_visible = True
            return
        self._hidden_thinking_placeholder_visible = False
        if self._active_thinking_widget is None:
            self._active_thinking_widget = StreamingTranscriptMessageWidget(
                ChatItem(role="thinking", text=""),
                theme=theme,
                output_padding_x=self.output_padding_x,
            )
            await self.mount(self._active_thinking_widget)
        await self._active_thinking_widget.append_fragment(delta)
        if scroll_end:
            self.scroll_end(animate=False)

    async def finish_assistant_message(self, text: str | None = None) -> None:
        """Finalize the active assistant widget after the provider sends the full message."""
        widget = self._active_assistant_widget
        if widget is None:
            if text:
                await self.append_item(
                    ChatItem(role="assistant", text=text),
                    theme=self._render_theme,
                    output_padding_x=self.output_padding_x,
                )
            return
        if text is not None:
            await widget.replace_text(text)
        self._active_assistant_widget = None

    @property
    def lines(self) -> tuple[TranscriptLine, ...]:
        """Compatibility text view for tests and lightweight transcript inspection."""
        messages = [
            child
            for child in self.children
            if isinstance(child, TranscriptMessageWidget | StreamingTranscriptMessageWidget)
        ]
        return tuple(
            TranscriptLine(line)
            for message in messages
            for line in message.selection_text.splitlines()
        )


def _transcript_widget(
    item: ChatItem,
    *,
    theme: TuiTheme,
    show_tool_results: bool,
    show_images: bool = True,
    image_width_cells: int | None = None,
    output_padding_x: int = 1,
    tool_results_key_hint: str = "Ctrl+O",
) -> TranscriptMessageWidget | StreamingTranscriptMessageWidget:
    if item.role in {"assistant", "thinking"}:
        return StreamingTranscriptMessageWidget(
            item,
            theme=theme,
            output_padding_x=output_padding_x,
        )
    return TranscriptMessageWidget(
        item,
        theme=theme,
        show_tool_results=show_tool_results,
        show_images=show_images,
        image_width_cells=image_width_cells,
        output_padding_x=output_padding_x,
        tool_results_key_hint=tool_results_key_hint,
    )


def transcript_item_selection_text(
    item: ChatItem,
    *,
    show_tool_results: bool = False,
    tool_results_key_hint: str = "Ctrl+O",
) -> str:
    """Return the plain text represented by a selectable transcript item."""
    return _visible_chat_text(
        item,
        show_tool_results=show_tool_results,
        tool_results_key_hint=tool_results_key_hint,
    )


def _split_rich_style_colors(style: str) -> tuple[str | None, str | None]:
    """Split the foreground/background colors from a simple Rich style string."""
    text_style = Style.parse(style)
    foreground = text_style.color.name if text_style.color is not None else None
    background = text_style.bgcolor.name if text_style.bgcolor is not None else None
    return foreground, background


def _use_plain_transcript_body(item: ChatItem) -> bool:
    """Return whether a transcript item can use fast selectable plain text."""
    return item.role in {"user", "tool", "skill", "error"}


def _transcript_plain_body_text(
    item: ChatItem,
    *,
    text: str,
    body_style: str,
    theme: TuiTheme,
    show_images: bool = True,
    image_width_cells: int | None = None,
) -> RenderableType:
    """Return styled transcript text for selectable plain rows."""
    if item.role != "tool":
        return Text(
            _normalize_terminal_display_text(text),
            style=body_style,
            overflow="fold",
            no_wrap=False,
        )

    invocation, separator, result_text = text.partition("\n\n")
    invocation_text = _render_transcript_tool_invocation(
        invocation,
        body_style=body_style,
        accent_style=_tool_accent_style(item, theme=theme),
    )
    if not separator:
        return invocation_text

    patch_body = _render_patch_body(
        result_text,
        body_style=body_style,
        syntax_theme=theme.syntax_theme,
        code_block_background=theme.markdown_code_block_background,
    )
    if patch_body is not None:
        return Group(invocation_text, Text(""), patch_body)

    rendered_result = VisualPreviewText(
        result_text,
        max_visual_lines=TOOL_RESULT_VISUAL_PREVIEW_LINES,
        style=body_style,
    )
    tool_images = _tool_images_for_item(item)
    if tool_images:
        return Group(
            invocation_text,
            Text(""),
            rendered_result,
            Text(""),
            *_render_tool_images(
                tool_images,
                show_images=show_images,
                image_width_cells=image_width_cells,
            ),
        )
    return Group(invocation_text, Text(""), rendered_result)


def _render_transcript_tool_invocation(
    text: str,
    *,
    body_style: str,
    accent_style: str | None,
) -> Text:
    """Render a selectable tool invocation with status color after the prefix."""
    rendered = Text(style=body_style, overflow="fold", no_wrap=False)
    accent_style = accent_style or body_style
    prefix, name, remainder = _split_tool_invocation(text)
    rendered.append(prefix, style=body_style)
    rendered.append(name, style=accent_style)
    rendered.append(remainder, style=accent_style)
    return rendered


def _transcript_item_markdown(
    item: ChatItem,
    *,
    show_tool_results: bool,
    tool_results_key_hint: str = "Ctrl+O",
) -> str:
    """Return Markdown for a transcript item using native Textual Markdown blocks."""
    visible_text = _visible_chat_text(
        item,
        show_tool_results=show_tool_results,
        tool_results_key_hint=tool_results_key_hint,
    )
    if item.role in {
        "assistant",
        "thinking",
        "status",
        "custom",
        "branch_summary",
        "compaction_summary",
    }:
        return visible_text
    return _plain_markdown(visible_text)


def _plain_markdown(text: str) -> str:
    """Represent arbitrary plain text as wrapping Markdown paragraphs."""
    if not text:
        return ""
    return "\n".join(_escape_plain_markdown_line(line) for line in text.splitlines())


def _escape_plain_markdown_line(line: str) -> str:
    """Escape Markdown syntax while preserving plain, wrapping text."""
    escaped = line.replace("\\", "\\\\")
    for character in "`*_{}[]()#+-.!|>":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _extract_text_selection(text: str, selection: Selection) -> str:
    clipped_selection = _clip_selection_to_text(selection, text)
    return clipped_selection.extract(text)


def _clip_selection_to_text(selection: Selection, text: str) -> Selection:
    lines = text.splitlines()
    if not lines:
        return Selection(Offset(0, 0), Offset(0, 0))
    return Selection(
        _clip_selection_offset(selection.start, lines),
        _clip_selection_offset(selection.end, lines),
    )


def _clip_selection_offset(offset: Offset | None, lines: list[str]) -> Offset | None:
    if offset is None:
        return None
    line_index = min(max(offset.y, 0), len(lines) - 1)
    column = min(max(offset.x, 0), len(lines[line_index]))
    return Offset(column, line_index)


def render_session_sidebar(
    session: SessionSummarySource,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> RenderableType:
    """Render a dark, minimalist summary of the active coding session."""
    metadata = Table.grid(padding=(0, 1))
    metadata.add_column(style=theme.completion_description, no_wrap=True)
    metadata.add_column(style=theme.prompt_text)
    title = _named_session_title(getattr(session, "session_title", None))
    metadata.add_row("cwd", _short_path(session.cwd))
    if title is not None:
        metadata.add_row("name", title)
    metadata.add_row("context", _context_usage(session))
    metadata.add_row("provider", session.provider_name)
    metadata.add_row("model", session.model)
    metadata.add_row("auth", _provider_readiness_label(session))
    metadata.add_row("thinking", _thinking_level(session))
    metadata.add_row("queue", _queue_status_label(session))
    metadata.add_row("memory", _memory_first_label(session))
    metadata.add_row("dag", "canonical: /workflows")
    metadata.add_row("scillm", _scillm_surface_label(session))
    metadata.add_row("tools", str(len(session.tools)))
    metadata.add_row("skills", str(len(session.skills)))

    tools = _limited_bullet_list(
        [tool.name for tool in session.tools],
        empty="No tools",
        theme=theme,
    )
    skills = _limited_bullet_list(
        [skill.name for skill in session.skills],
        empty="No skills loaded yet",
        theme=theme,
    )
    prompts = _limited_bullet_list(
        [template.name for template in session.prompt_templates],
        empty="No prompt templates",
        theme=theme,
    )
    context = _limited_bullet_list(
        _context_file_labels(session.context_files, cwd=session.cwd),
        empty="No context files",
        theme=theme,
    )
    loop_monitor = _loop_monitor_status(session)
    loop_monitor_section = (
        (
            _sidebar_separator(theme=theme),
            _sidebar_section(
                "loop2 monitor",
                _render_loop_monitor_status(loop_monitor, theme=theme),
                theme=theme,
            ),
        )
        if loop_monitor is not None
        else ()
    )
    equation = Text(TAU_SIDEBAR_LOGO, style=f"bold {theme.prompt_text}")

    return Group(
        Padding(Align.center(equation), (0, 0, 1, 0)),
        _sidebar_section("session", metadata, theme=theme),
        *loop_monitor_section,
        _sidebar_separator(theme=theme),
        _sidebar_section("context", context, theme=theme),
        _sidebar_separator(theme=theme),
        _sidebar_section("tools", tools, theme=theme),
        _sidebar_separator(theme=theme),
        _sidebar_section("skills", skills, theme=theme),
        _sidebar_separator(theme=theme),
        _sidebar_section("prompts", prompts, theme=theme),
    )


def _sidebar_section(
    title: str,
    body: RenderableType,
    *,
    theme: TuiTheme,
) -> RenderableType:
    """Render one sidebar section without a surrounding border."""
    header = Text(title, style=f"bold {theme.accent}")
    return Group(Padding(header, (0, 0, 0, 1)), Padding(body, (0, 0, 1, 1)))


def _sidebar_separator(*, theme: TuiTheme) -> RenderableType:
    """Render a subtle divider between sidebar sections."""
    return Padding(Rule(style=theme.border), (0, 0, 1, 0))


def render_compact_session_info(
    session: SessionSummarySource,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> RenderableType:
    """Render the session facts below the prompt."""
    path_label = _short_path(session.cwd)
    git_branch = _git_branch(session.cwd)
    if git_branch:
        path_label = f"{path_label} ({git_branch})"
    title = _named_session_title(getattr(session, "session_title", None))
    if title is not None:
        path_label = f"{path_label} • {title}"
    left = Text(
        path_label,
        style=theme.prompt_text,
        overflow="fold",
        no_wrap=False,
    )
    identity = Text(style=theme.muted_text, overflow="fold", no_wrap=False, justify="right")
    identity.append(session.provider_name, style=theme.completion_description)
    identity.append(f":{session.model}", style=theme.prompt_text)
    identity.append(" ")
    identity.append(f"({_thinking_level(session)})", style=theme.completion_description)

    readiness = Text(style=theme.muted_text, overflow="fold", no_wrap=False)
    for index, (label, value) in enumerate(_compact_readiness_segments(session)):
        if index:
            readiness.append("  ")
        readiness.append(f"{label}:", style=theme.completion_description)
        readiness.append(value, style=theme.prompt_text)

    metrics = Text(style=theme.muted_text, overflow="fold", no_wrap=False, justify="right")
    stats = _session_stats_summary(session)
    if stats is not None:
        metrics.append(stats, style=theme.completion_description)
        metrics.append("  ")
    metrics.append(_context_usage(session), style=theme.completion_description)
    loop_monitor = _loop_monitor_status(session)
    if loop_monitor is not None:
        metrics.append("  ")
        metrics.append("loop2:", style=theme.completion_description)
        metrics.append(loop_monitor.label, style=theme.accent)

    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1, justify="right")
    table.add_row(left, identity)
    return Group(table, readiness, metrics)


def render_chat_item(
    item: ChatItem,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
    show_tool_results: bool = False,
    show_images: bool = True,
    image_width_cells: int | None = None,
    tool_results_key_hint: str = "Ctrl+O",
) -> RenderableType:
    """Render a chat item as a standalone Toad-inspired transcript block."""
    role_style = _chat_item_role_style(item, theme)
    visible_text = _visible_chat_text(
        item,
        show_tool_results=show_tool_results,
        tool_results_key_hint=tool_results_key_hint,
    )
    body = (
        _render_tool_chat_body(
            item,
            body_style=theme.role_styles["tool"].body,
            accent_style=_tool_accent_style(item, theme=theme),
            show_tool_results=show_tool_results,
            show_images=show_images,
            image_width_cells=image_width_cells,
            syntax_theme=theme.syntax_theme,
            theme=theme,
            tool_results_key_hint=tool_results_key_hint,
        )
        if item.role == "tool"
        else _render_chat_body(
            visible_text,
            role=item.role,
            body_style=role_style.body,
            syntax_theme=theme.syntax_theme,
            theme=theme,
        )
    )
    markdown_images = (
        _markdown_visual_payloads(visible_text)
        if item.role
        in {"assistant", "thinking", "custom", "status", "branch_summary", "compaction_summary"}
        else ()
    )
    if markdown_images:
        body = Group(
            body,
            Text(""),
            *_render_tool_images(
                markdown_images,
                show_images=show_images,
                image_width_cells=image_width_cells,
            ),
        )
    table = Table.grid(expand=True)
    table.add_column(width=1, style=role_style.border)
    table.add_column(ratio=1, style=role_style.body)
    table.add_row(
        Align.left(Text("▌", style=role_style.border)),
        Padding(body, (0, 1, 0, 1), style=role_style.body),
    )
    renderable: RenderableType = table
    if item.role in {"user", "assistant"}:
        renderable = Osc133Zone(renderable)
    return Padding(renderable, (1, 1, 1, 0), style=role_style.body)


def _chat_item_role_style(item: ChatItem, theme: TuiTheme) -> TuiRoleStyle:
    if item.role == "tool" and item.tool_result_text:
        if item.tool_result_text.startswith("✓"):
            return TuiRoleStyle(
                border=_tool_success_color(theme),
                body=theme.role_styles["tool"].body,
            )
        if item.tool_result_text.startswith("✗"):
            return TuiRoleStyle(
                border=theme.error,
                body=theme.role_styles["tool"].body,
            )
    return theme.role_styles[item.role]


def _tool_accent_style(item: ChatItem, *, theme: TuiTheme) -> str | None:
    if item.role != "tool" or not item.tool_result_text:
        return None
    if item.tool_result_text.startswith("✓"):
        return _tool_success_style(theme)
    if item.tool_result_text.startswith("✗"):
        return _tool_error_style(theme)
    return None


def _tool_success_color(theme: TuiTheme) -> str:
    return theme.success


def _tool_success_style(theme: TuiTheme) -> str:
    return theme.tool_success_text


def _tool_error_style(theme: TuiTheme) -> str:
    return theme.tool_error_text


def _render_tool_chat_body(
    item: ChatItem,
    *,
    body_style: str,
    accent_style: str | None,
    show_tool_results: bool,
    show_images: bool,
    image_width_cells: int | None,
    syntax_theme: str,
    theme: TuiTheme,
    tool_results_key_hint: str,
) -> RenderableType:
    invocation_text = _with_tool_results_key_hint(item.text, tool_results_key_hint)
    text = _render_tool_invocation(
        invocation_text,
        body_style=body_style,
        accent_style=accent_style,
    )
    if not show_tool_results or not item.tool_result_text:
        return text

    result_body = _render_chat_body(
        item.tool_result_text,
        role=item.role,
        body_style=body_style,
        syntax_theme=syntax_theme,
        theme=theme,
    )
    if _render_patch_body(
        item.tool_result_text,
        body_style=body_style,
        syntax_theme=syntax_theme,
        code_block_background=theme.markdown_code_block_background,
    ) is None:
        result_body = VisualPreviewText(
            item.tool_result_text,
            max_visual_lines=TOOL_RESULT_VISUAL_PREVIEW_LINES,
            style=body_style,
        )
    tool_images = _tool_images_for_item(item)
    if tool_images:
        return Group(
            text,
            Text(""),
            result_body,
            Text(""),
            *_render_tool_images(
                tool_images,
                show_images=show_images,
                image_width_cells=image_width_cells,
            ),
        )
    return Group(text, Text(""), result_body)


def _tool_images_for_item(item: ChatItem) -> tuple[ToolImagePayload, ...]:
    if item.tool_images:
        return item.tool_images
    if item.tool_image is not None:
        return (item.tool_image,)
    return ()


def markdown_visual_payloads(
    markdown: str,
    *,
    base_path: Path | None = None,
) -> tuple[ToolImagePayload, ...]:
    """Return renderable visual payloads referenced by Markdown text."""
    return (
        *_markdown_image_payloads(markdown, base_path=base_path),
        *_markdown_graph_payloads(markdown),
    )


def _markdown_image_payloads(
    markdown: str,
    *,
    base_path: Path | None = None,
) -> tuple[ToolImagePayload, ...]:
    payloads: list[ToolImagePayload] = []
    seen: set[Path] = set()
    for match in MARKDOWN_IMAGE_PATTERN.finditer(markdown):
        path = _markdown_image_path(match.group("target"), base_path=base_path)
        if path is None or path in seen:
            continue
        payload = _tool_image_payload_from_file(path)
        if payload is None:
            continue
        payloads.append(payload)
        seen.add(path)
    return tuple(payloads)


def _markdown_visual_payloads(markdown: str) -> tuple[ToolImagePayload, ...]:
    """Return local image links plus rendered graph-source artifacts."""
    return markdown_visual_payloads(markdown)


def _markdown_graph_payloads(markdown: str) -> tuple[ToolImagePayload, ...]:
    payloads: list[ToolImagePayload] = []
    for artifact in render_markdown_graph_artifacts(markdown):
        payloads.append(
            ToolImagePayload(
                path=artifact.filename,
                mime_type=artifact.mime_type,
                bytes=artifact.bytes,
                image_base64=artifact.image_base64,
            )
        )
    return tuple(payloads)


def _markdown_image_path(target: str, *, base_path: Path | None = None) -> Path | None:
    cleaned = target.strip()
    if cleaned.startswith("<") and cleaned.endswith(">"):
        cleaned = cleaned[1:-1].strip()
    parsed = urlparse(cleaned)
    if parsed.scheme in {"http", "https", "data"}:
        return None
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
    elif parsed.scheme:
        return None
    else:
        raw_path = unquote(cleaned.split("#", 1)[0].split("?", 1)[0])
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (base_path or Path.cwd()) / path
    return path.resolve()


def _tool_image_payload_from_file(path: Path) -> ToolImagePayload | None:
    mime_type = MARKDOWN_IMAGE_MIME_TYPES.get(path.suffix.lower())
    if mime_type is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_MARKDOWN_IMAGE_BYTES:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return ToolImagePayload(
        path=str(path),
        mime_type=mime_type,
        bytes=len(data),
        image_base64=base64.b64encode(data).decode("ascii"),
    )


def _render_tool_images(
    payloads: Sequence[ToolImagePayload],
    *,
    show_images: bool,
    image_width_cells: int | None,
) -> tuple[RenderableType, ...]:
    renderables: list[RenderableType] = []
    for index, payload in enumerate(payloads):
        if index > 0:
            renderables.append(Text(""))
        renderables.append(
            _render_tool_image(
                payload,
                show_images=show_images,
                image_width_cells=image_width_cells,
            )
        )
    return tuple(renderables)


def _render_tool_image(
    payload: ToolImagePayload,
    *,
    show_images: bool,
    image_width_cells: int | None,
) -> TerminalImage:
    """Return a terminal-image renderable for image tool results."""
    return TerminalImage(
        payload.image_base64,
        payload.mime_type,
        TerminalImageOptions(
            filename=Path(payload.path).name,
            max_width_cells=image_width_cells,
            show=show_images,
        ),
    )


def _render_tool_invocation(text: str, *, body_style: str, accent_style: str | None) -> Text:
    rendered = Text(style=body_style, overflow="fold", no_wrap=False)
    accent_style = accent_style or body_style
    prefix, name, remainder = _split_tool_invocation(text)
    rendered.append(prefix, style=body_style)
    rendered.append(name, style=body_style)
    rendered.append(remainder, style=accent_style)
    return rendered


def _split_tool_invocation(text: str) -> tuple[str, str, str]:
    if text.startswith("→ "):
        rest = text[2:]
        name, separator, remainder = rest.partition(" ")
        return "→ ", name, f"{separator}{remainder}" if separator else ""
    if text.startswith("$ "):
        return "$", "", text[1:]
    name, separator, remainder = text.partition(" ")
    return "", name, f"{separator}{remainder}" if separator else ""


def _visible_chat_text(
    item: ChatItem,
    *,
    show_tool_results: bool,
    tool_results_key_hint: str = "Ctrl+O",
) -> str:
    if item.role == "branch_summary":
        if show_tool_results and item.tool_result_text:
            return f"**Branch Summary**\n\n{item.tool_result_text}"
        return _with_tool_results_key_hint(item.text, tool_results_key_hint)
    if item.role == "compaction_summary":
        if show_tool_results and item.tool_result_text:
            return f"**Compaction Summary**\n\n{item.tool_result_text}"
        return _with_tool_results_key_hint(item.text, tool_results_key_hint)
    if item.role == "skill":
        if show_tool_results and item.tool_result_text:
            return f"[skill]\n\n{item.tool_result_text}"
        return _with_tool_results_key_hint(
            _compact_skill_item_text(item.text),
            tool_results_key_hint,
        )
    if item.role not in {"tool", "skill"} or not show_tool_results or not item.tool_result_text:
        return _with_tool_results_key_hint(item.text, tool_results_key_hint)
    return f"{item.text}\n\n{item.tool_result_text}"


def _compact_skill_item_text(text: str) -> str:
    """Return Pi-style compact text for recognized Tau skill transcript rows."""
    prefix = "Loading skill: "
    if text.startswith(prefix):
        name = text.removeprefix(prefix).strip()
        if name:
            return f"[skill] {name} (Ctrl+O to expand)"
    return text


def _with_tool_results_key_hint(text: str, key_hint: str) -> str:
    key_hint = key_hint.strip() or "Ctrl+O"
    if key_hint == "Ctrl+O":
        return text
    return text.replace("(Ctrl+O to expand)", f"({key_hint} to expand)")


def _render_chat_body(
    text: str,
    *,
    role: str,
    body_style: str,
    syntax_theme: str,
    theme: TuiTheme,
) -> RenderableType:
    display_text = _normalize_terminal_display_text(text)
    patch_body = _render_patch_body(
        display_text,
        body_style=body_style,
        syntax_theme=syntax_theme,
        code_block_background=theme.markdown_code_block_background,
    )
    if patch_body is not None:
        return patch_body
    if role in {"status", "custom"}:
        return _plain_text(display_text, body_style=body_style)
    if role in {"assistant", "thinking"}:
        if _has_unclosed_fence(display_text):
            return _plain_text(display_text, body_style=body_style)
        return ThemedMarkdown(
            display_text,
            style=body_style,
            code_theme=syntax_theme,
            inline_code_theme=syntax_theme,
            heading_style=_markdown_highlight_style(theme),
            inline_code_style=_markdown_inline_code_style(theme),
            link_style=theme.markdown_link,
            bullet_style=theme.markdown_bullet,
            table_border_style=theme.markdown_table_border,
            code_block_background=theme.markdown_code_block_background,
        )
    fenced_body = _render_fenced_body(
        display_text,
        body_style=body_style,
        syntax_theme=syntax_theme,
        code_block_background=theme.markdown_code_block_background,
    )
    if fenced_body is not None:
        return fenced_body
    if "```" in display_text:
        return _plain_text(display_text, body_style=body_style)
    return _plain_text(display_text, body_style=body_style)


def _render_patch_body(
    text: str,
    *,
    body_style: str,
    syntax_theme: str,
    code_block_background: str,
) -> RenderableType | None:
    del syntax_theme
    marker = "\nPatch:\n"
    if marker in text:
        before_patch, patch = text.split(marker, 1)
        if not patch.strip():
            return None
        return Group(
            _plain_text(f"{before_patch}{marker.rstrip()}", body_style=body_style),
            _render_diff_text(
                patch.rstrip("\n"),
                body_style=body_style,
                code_block_background=code_block_background,
            ),
        )

    embedded_diff = _split_embedded_unified_diff(text)
    if embedded_diff is None:
        return None
    before_patch, patch = embedded_diff
    return Group(
        _plain_text(before_patch.rstrip("\n"), body_style=body_style),
        _render_diff_text(
            patch.rstrip("\n"),
            body_style=body_style,
            code_block_background=code_block_background,
        ),
    )


def _split_embedded_unified_diff(text: str) -> tuple[str, str] | None:
    lines = text.splitlines()
    if len(lines) < 4:
        return None

    for index, _line in enumerate(lines):
        if not _looks_like_unified_diff_start(lines, index):
            continue
        diff_lines = lines[index:]
        if not any(candidate.startswith("@@") for candidate in diff_lines):
            continue
        if not (
            any(candidate.startswith("--- ") for candidate in diff_lines)
            and any(candidate.startswith("+++ ") for candidate in diff_lines)
        ):
            continue
        before = "\n".join(lines[:index])
        if not before.strip():
            return None
        return before, "\n".join(diff_lines)
    return None


def _looks_like_unified_diff_start(lines: Sequence[str], index: int) -> bool:
    line = lines[index]
    if line.startswith("diff --git "):
        return True
    return (
        line.startswith("--- ")
        and index + 1 < len(lines)
        and lines[index + 1].startswith("+++ ")
    )


def _render_diff_text(
    patch: str,
    *,
    body_style: str,
    code_block_background: str,
) -> Text:
    rendered = Text(style=body_style, overflow="fold", no_wrap=False)
    lines = patch.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_removed_diff_line(line):
            removed_lines: list[str] = []
            while index < len(lines) and _is_removed_diff_line(lines[index]):
                removed_lines.append(lines[index][1:])
                index += 1
            added_lines: list[str] = []
            while index < len(lines) and _is_added_diff_line(lines[index]):
                added_lines.append(lines[index][1:])
                index += 1
            if len(removed_lines) == 1 and len(added_lines) == 1:
                _append_intraline_diff_pair(
                    rendered,
                    removed_lines[0],
                    added_lines[0],
                    code_block_background=code_block_background,
                )
            else:
                for removed in removed_lines:
                    _append_diff_line(
                        rendered,
                        f"-{removed}",
                        style=f"bright_red on {code_block_background}",
                    )
                for added in added_lines:
                    _append_diff_line(
                        rendered,
                        f"+{added}",
                        style=f"bright_green on {code_block_background}",
                    )
            continue
        if _is_added_diff_line(line):
            _append_diff_line(rendered, line, style=f"bright_green on {code_block_background}")
        else:
            _append_diff_line(rendered, line, style=f"bright_black on {code_block_background}")
        index += 1
    return rendered


def _is_removed_diff_line(line: str) -> bool:
    return line.startswith("-") and not line.startswith("---")


def _is_added_diff_line(line: str) -> bool:
    return line.startswith("+") and not line.startswith("+++")


def _append_intraline_diff_pair(
    rendered: Text,
    removed: str,
    added: str,
    *,
    code_block_background: str,
) -> None:
    removed_style = f"bright_red on {code_block_background}"
    added_style = f"bright_green on {code_block_background}"
    removed_changed = _changed_token_indexes(removed, added, side="removed")
    added_changed = _changed_token_indexes(removed, added, side="added")
    rendered.append("-", style=removed_style)
    _append_diff_tokens(rendered, removed, removed_changed, base_style=removed_style)
    rendered.append("\n")
    rendered.append("+", style=added_style)
    _append_diff_tokens(rendered, added, added_changed, base_style=added_style)
    rendered.append("\n")


def _changed_token_indexes(
    removed: str,
    added: str,
    *,
    side: Literal["removed", "added"],
) -> set[int]:
    removed_tokens = _diff_tokens(removed)
    added_tokens = _diff_tokens(added)
    changed: set[int] = set()
    matcher = SequenceMatcher(a=removed_tokens, b=added_tokens, autojunk=False)
    for tag, removed_start, removed_end, added_start, added_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if side == "removed":
            changed.update(range(removed_start, removed_end))
        else:
            changed.update(range(added_start, added_end))
    return changed


def _diff_tokens(text: str) -> list[str]:
    return re.findall(r"\s+|\w+|[^\w\s]+", text.replace("\t", VISIBLE_TAB_REPLACEMENT))


def _append_diff_tokens(
    rendered: Text,
    text: str,
    changed_indexes: set[int],
    *,
    base_style: str,
) -> None:
    base = Style.parse(base_style)
    for index, token in enumerate(_diff_tokens(text)):
        style = base
        if index in changed_indexes and token.strip():
            style += Style(reverse=True)
        rendered.append(token, style=style)


def _append_diff_line(rendered: Text, line: str, *, style: str) -> None:
    rendered.append(line.replace("\t", VISIBLE_TAB_REPLACEMENT), style=style)
    rendered.append("\n")


class ThemedCodeBlock(CodeBlock):
    """Rich Markdown code block with Tau's themed background color."""

    @classmethod
    def create(cls, markdown: Markdown, token: Any) -> ThemedCodeBlock:
        node_info = token.info or ""
        lexer_name = node_info.partition(" ")[0]
        code_block_background = getattr(markdown, "code_block_background", "default")
        return cls(lexer_name or "text", markdown.code_theme, code_block_background)

    def __init__(self, lexer_name: str, theme: str, code_block_background: str) -> None:
        super().__init__(lexer_name, theme)
        self.code_block_background = code_block_background

    def __rich_console__(self, console: Console, options: Any) -> Any:
        code = str(self.text).rstrip()
        yield Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=1,
            background_color=self.code_block_background,
        )


class LeftAlignedMarkdownHeading(Heading):
    """Rich Markdown heading that keeps all heading levels left-aligned."""

    LEVEL_ALIGN: ClassVar[dict[str, str]] = {
        "h1": "left",
        "h2": "left",
        "h3": "left",
        "h4": "left",
        "h5": "left",
        "h6": "left",
    }


class ThemedMarkdown(Markdown):
    """Markdown renderer with Tau's softer heading/accent colors."""

    elements = {
        **Markdown.elements,
        "heading_open": LeftAlignedMarkdownHeading,
        "fence": ThemedCodeBlock,
        "code_block": ThemedCodeBlock,
    }

    def __init__(
        self,
        markup: str,
        *,
        heading_style: str,
        inline_code_style: str,
        link_style: str,
        bullet_style: str,
        table_border_style: str,
        code_block_background: str,
        code_theme: str,
        inline_code_theme: str,
        style: str = "none",
    ) -> None:
        super().__init__(
            markup,
            style=style,
            code_theme=code_theme,
            inline_code_theme=inline_code_theme,
        )
        self.heading_style = heading_style
        self.inline_code_style = inline_code_style
        self.link_style = link_style
        self.bullet_style = bullet_style
        self.table_border_style = table_border_style
        self.code_block_background = code_block_background

    def __rich_console__(self, console: Console, options: Any) -> Any:
        with console.use_theme(
            _markdown_theme(
                self.heading_style,
                self.inline_code_style,
                self.link_style,
                self.bullet_style,
                self.table_border_style,
                self.code_block_background,
            )
        ):
            yield from super().__rich_console__(console, options)


def _markdown_highlight_style(theme: TuiTheme) -> str:
    return theme.markdown_heading


def _markdown_inline_code_style(theme: TuiTheme) -> str:
    return theme.markdown_inline_code


def _markdown_theme(
    heading_style: str,
    inline_code_style: str,
    link_style: str,
    bullet_style: str,
    table_border_style: str,
    code_block_background: str,
) -> Theme:
    highlight = Style.parse(heading_style)
    inline_code = Style.parse(inline_code_style)
    link = Style.parse(link_style)
    bullet = Style.parse(bullet_style)
    table_border = Style.parse(table_border_style)
    code_block = Style(bgcolor=code_block_background)
    return Theme(
        {
            "markdown.h1": highlight + Style(bold=True),
            "markdown.h2": highlight + Style(bold=True),
            "markdown.h3": highlight + Style(bold=True),
            "markdown.h4": highlight + Style(bold=True),
            "markdown.h5": highlight + Style(bold=True),
            "markdown.h6": highlight + Style(bold=True),
            "markdown.item.bullet": bullet,
            "markdown.item.number": bullet,
            "markdown.block_quote": highlight,
            "markdown.link": link,
            "markdown.link_url": link,
            "markdown.table.header": highlight + Style(bold=True),
            "markdown.table.border": table_border,
            "markdown.code": inline_code,
            "markdown.code_block": code_block,
        }
    )


def _render_fenced_body(
    text: str,
    *,
    body_style: str,
    syntax_theme: str,
    code_block_background: str,
) -> RenderableType | None:
    if "```" not in text:
        return None

    renderables: list[RenderableType] = []
    cursor = 0
    while cursor < len(text):
        fence_start = text.find("```", cursor)
        if fence_start == -1:
            _append_plain(renderables, text[cursor:], body_style=body_style)
            break

        line_start = text.rfind("\n", 0, fence_start) + 1
        if line_start != fence_start:
            return None

        fence_line_end = text.find("\n", fence_start)
        if fence_line_end == -1:
            return None
        closing_start = text.find("\n```", fence_line_end + 1)
        if closing_start == -1:
            return None

        _append_plain(renderables, text[cursor:fence_start], body_style=body_style)
        language = _syntax_language(text[fence_start + 3 : fence_line_end])
        code = text[fence_line_end + 1 : closing_start]
        renderables.append(
            Syntax(
                code.rstrip("\n"),
                language,
                theme=syntax_theme,
                word_wrap=True,
                background_color=code_block_background,
            )
        )
        closing_line_end = text.find("\n", closing_start + 1)
        cursor = len(text) if closing_line_end == -1 else closing_line_end + 1

    return Group(*renderables) if renderables else None


def _append_plain(
    renderables: list[RenderableType],
    text: str,
    *,
    body_style: str,
) -> None:
    if text:
        renderables.append(_plain_text(text.rstrip("\n"), body_style=body_style))


def _plain_text(text: str, *, body_style: str) -> Text:
    return Text(
        _normalize_terminal_display_text(text),
        style=body_style,
        overflow="fold",
        no_wrap=False,
    )


def _context_usage(session: SessionSummarySource) -> str:
    threshold = session.auto_compact_token_threshold
    budget = threshold if threshold is not None and threshold > 0 else session.context_window_tokens
    if threshold is None or threshold <= 0:
        label = (
            f"{_compact_token_count(session.context_token_estimate)}"
            f"/{_compact_token_count(session.context_window_tokens)} context"
        )
    else:
        label = (
            f"{_compact_token_count(session.context_token_estimate)}"
            f"/{_compact_token_count(threshold)} context (auto)"
        )
    percent = _context_percent(session.context_token_estimate, budget)
    breakdown = _context_breakdown(getattr(session, "context_usage", None))
    details = [detail for detail in (percent, breakdown) if detail]
    return f"{label} {' '.join(details)}" if details else label


def _session_stats_summary(session: SessionSummarySource) -> str | None:
    stats = _session_stats(session)
    if (
        stats.turn_count == 0
        and stats.tool_call_count == 0
        and stats.input_tokens == 0
        and stats.output_tokens == 0
        and stats.cache_read_tokens == 0
        and stats.cache_write_tokens == 0
        and stats.estimated_cost is None
    ):
        return None
    parts = [
        f"{stats.turn_count} {_plural(stats.turn_count, 'turn')}, "
        f"{stats.tool_call_count} tool {_plural(stats.tool_call_count, 'call')}",
    ]
    if stats.input_tokens:
        parts.append(f"↑{_compact_usage_count(stats.input_tokens)}")
    if stats.output_tokens:
        parts.append(f"↓{_compact_usage_count(stats.output_tokens)}")
    if stats.cache_read_tokens:
        parts.append(f"R{_compact_usage_count(stats.cache_read_tokens)}")
    if stats.cache_write_tokens:
        parts.append(f"W{_compact_usage_count(stats.cache_write_tokens)}")
    if (
        (stats.cache_read_tokens or stats.cache_write_tokens)
        and stats.latest_cache_hit_rate is not None
    ):
        parts.append(f"CH{stats.latest_cache_hit_rate:.1f}%")
    if stats.estimated_cost is not None:
        parts.append(f"${stats.estimated_cost:.3f}")
    return " ".join(parts)


def _session_stats(session: SessionSummarySource) -> SessionStats:
    stats = getattr(session, "session_stats", None)
    if isinstance(stats, SessionStats):
        return stats
    return _message_session_stats(getattr(session, "messages", ()))


def _compact_readiness_segments(session: SessionSummarySource) -> tuple[tuple[str, str], ...]:
    """Return first-screen readiness facts for narrow or sidebar-hidden layouts."""
    return (
        ("auth", _compact_provider_readiness_label(session)),
        ("mem", _compact_memory_first_label(session)),
        ("dag", "/workflows"),
        ("llm", _compact_scillm_surface_label(session)),
        ("q", _queue_status_label(session)),
    )


def _compact_provider_readiness_label(session: SessionSummarySource) -> str:
    provider_name = session.provider_name.strip()
    available_providers = tuple(
        provider
        for provider in (str(item).strip() for item in getattr(session, "available_providers", ()))
        if provider
    )
    available_models = tuple(
        model
        for model in (str(item).strip() for item in getattr(session, "available_models", ()))
        if model
    )
    if provider_name and provider_name in available_providers and available_models:
        return f"ready({len(available_providers)})"
    if available_providers:
        return f"/model({len(available_providers)})"
    if provider_name:
        return f"/login {provider_name}"
    return "/login"


def _compact_memory_first_label(session: SessionSummarySource) -> str:
    return "loaded" if _memory_first_label(session) == "loaded" else "/skills memory"


def _compact_scillm_surface_label(session: SessionSummarySource) -> str:
    label = _scillm_surface_label(session)
    if label.startswith("active"):
        return "active"
    if label.startswith("switch"):
        return "/model scillm"
    return "/scillm"


def _message_session_stats(messages: object) -> SessionStats:
    if not isinstance(messages, Sequence):
        return SessionStats()
    turn_count = 0
    tool_call_count = 0
    for message in messages:
        if getattr(message, "role", None) == "user":
            turn_count += 1
        tool_calls = getattr(message, "tool_calls", ())
        if isinstance(tool_calls, Sequence):
            tool_call_count += len(tool_calls)
    return SessionStats(turn_count=turn_count, tool_call_count=tool_call_count)


def _provider_readiness_label(session: SessionSummarySource) -> str:
    provider_name = session.provider_name.strip()
    available_providers = tuple(
        provider
        for provider in (str(item).strip() for item in getattr(session, "available_providers", ()))
        if provider
    )
    available_models = tuple(
        model
        for model in (str(item).strip() for item in getattr(session, "available_models", ()))
        if model
    )
    if provider_name and provider_name in available_providers and available_models:
        count = len(available_providers)
        return f"ready ({count} {_plural(count, 'provider')})"
    if available_providers:
        count = len(available_providers)
        return f"switch with /model ({count} usable {_plural(count, 'provider')})"
    if provider_name:
        return f"login required: /login {provider_name}"
    return "login required: /login"


def _queue_status_label(session: SessionSummarySource) -> str:
    steering = _sequence_len(getattr(session, "queued_steering_messages", ()))
    follow_up = _sequence_len(getattr(session, "queued_follow_up_messages", ()))
    total = steering + follow_up
    if total <= 0:
        return "idle"
    parts = []
    if steering:
        parts.append(f"{steering} steering")
    if follow_up:
        parts.append(f"{follow_up} follow-up")
    return ", ".join(parts)


def _memory_first_label(session: SessionSummarySource) -> str:
    skill_names = {
        str(getattr(skill, "name", "")).casefold()
        for skill in getattr(session, "skills", ())
        if str(getattr(skill, "name", "")).strip()
    }
    if "memory" in skill_names:
        return "loaded"
    return "available: /skills memory"


def _scillm_surface_label(session: SessionSummarySource) -> str:
    provider_name = session.provider_name.strip().casefold()
    available_providers = {
        str(provider).strip().casefold()
        for provider in getattr(session, "available_providers", ())
        if str(provider).strip()
    }
    if provider_name == "scillm":
        return "active: /scillm"
    if "scillm" in available_providers:
        return "switch: /model scillm"
    return "/scillm"


def _sequence_len(value: object) -> int:
    return len(value) if isinstance(value, Sequence) else 0


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else f"{singular}s"


def _compact_usage_count(value: int) -> str:
    if value < 1000:
        return str(value)
    if value < 10_000:
        return f"{value / 1000:.1f}k"
    if value < 1_000_000:
        return f"{round(value / 1000)}k"
    if value < 10_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{round(value / 1_000_000)}M"


def _context_percent(used_tokens: int, budget_tokens: int) -> str | None:
    if budget_tokens <= 0 or used_tokens < 0:
        return None
    return f"{round((used_tokens / budget_tokens) * 100)}%"


def _context_breakdown(context_usage: Any) -> str | None:
    if context_usage is None:
        return None
    token_fields = (
        ("sys", "system_tokens"),
        ("msg", "message_tokens"),
        ("tools", "tool_tokens"),
    )
    parts: list[str] = []
    for label, field_name in token_fields:
        value = getattr(context_usage, field_name, None)
        if not isinstance(value, int):
            return None
        parts.append(f"{label} {_compact_token_count(value)}")
    return "(" + " ".join(parts) + ")"


def _compact_token_count(value: int) -> str:
    if value <= 0:
        return "0k"
    if value < 1000:
        return "<1k"
    return f"{(value + 500) // 1000}k"


def _named_session_title(title: str | None) -> str | None:
    if title is None:
        return None
    stripped = title.strip()
    if not stripped or stripped.lower() == "untitled session":
        return None
    return stripped


def _context_file_labels(
    context_files: Sequence[ProjectContextFile],
    *,
    cwd: Path,
) -> list[str]:
    return [_context_file_label(Path(context_file.path), cwd=cwd) for context_file in context_files]


def _context_file_label(path: Path, *, cwd: Path) -> str:
    expanded_path = path.expanduser()
    if not expanded_path.is_absolute():
        expanded_path = cwd / expanded_path
    try:
        return str(expanded_path.resolve().relative_to(cwd.expanduser().resolve()))
    except (OSError, ValueError):
        try:
            absolute_path = expanded_path.resolve()
        except OSError:
            absolute_path = expanded_path.absolute()
        return _short_path(absolute_path)


def _thinking_level(session: SessionSummarySource) -> str:
    available = getattr(session, "available_thinking_levels", None)
    if available == ():
        return "unavailable"
    explicit_level = getattr(session, "thinking_level", None)
    if explicit_level:
        return str(explicit_level)
    state = getattr(session, "state", None)
    thinking_level = getattr(state, "thinking_level", None)
    return str(thinking_level) if thinking_level else "--"


def _loop_monitor_status(session: SessionSummarySource) -> LoopMonitorStatus | None:
    status = getattr(session, "loop_monitor_status", None)
    if isinstance(status, LoopMonitorStatus):
        return status
    state = getattr(session, "state", None)
    status = getattr(state, "loop_monitor_status", None)
    return status if isinstance(status, LoopMonitorStatus) else None


def _render_loop_monitor_status(
    status: LoopMonitorStatus,
    *,
    theme: TuiTheme,
) -> RenderableType:
    table = Table.grid(padding=(0, 1))
    table.add_column(style=theme.completion_description, no_wrap=True)
    table.add_column(style=theme.prompt_text)
    table.add_row("status", status.label)
    if status.run_id:
        table.add_row("run", status.run_id)
    if status.event_count is not None:
        table.add_row("events", str(status.event_count))
    if status.last_event_type:
        table.add_row("last", status.last_event_type)
    if status.receipt_status:
        table.add_row("receipt", status.receipt_status)
    evidence = _loop_monitor_evidence_label(status)
    if evidence:
        table.add_row("evidence", evidence)
    if status.proof_scope:
        table.add_row("scope", status.proof_scope)
    if status.does_not_prove:
        table.add_row("does not prove", "; ".join(status.does_not_prove))
    if status.source:
        table.add_row("source", status.source)
    return table


def _loop_monitor_evidence_label(status: LoopMonitorStatus) -> str:
    parts: list[str] = []
    if status.mocked is not None:
        parts.append(f"mocked:{str(status.mocked).lower()}")
    if status.live is not None:
        parts.append(f"live:{str(status.live).lower()}")
    return " ".join(parts)


def _git_branch(cwd: Path) -> str | None:
    try:
        result = run(
            ["git", "-C", str(cwd), "branch", "--show-current"],
            capture_output=True,
            check=False,
            text=True,
            timeout=0.5,
        )
    except OSError:
        return None
    except TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if branch:
        return branch
    return None


def _has_unclosed_fence(text: str) -> bool:
    fence_count = sum(1 for line in text.splitlines() if line.startswith("```"))
    return fence_count % 2 == 1


def _fence_language(raw: str) -> str:
    language = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
    return language or "text"


def _syntax_language(raw: str) -> str:
    language = _fence_language(raw)
    if language == "text":
        return language
    try:
        get_lexer_by_name(language)
    except ClassNotFound:
        return "text"
    return language


def render_completion_suggestions(
    state: CompletionState,
    *,
    theme: TuiTheme = TAU_DARK_THEME,
) -> RenderableType:
    """Render prompt completion suggestions in aligned command/description columns."""
    table = Table.grid(expand=True)
    table.add_column(no_wrap=True)
    table.add_column(ratio=1)

    previous_category: str | None = None
    for index, item in enumerate(state.items):
        if item.category != previous_category:
            if index:
                table.add_row(Text(""), Text(""))
            if item.category:
                table.add_row(Text(item.category, style=theme.completion_description), Text(""))
            previous_category = item.category

        selected = index == state.selected_index
        prefix = "› " if selected else "  "
        style = theme.completion_selected if selected else theme.prompt_text
        description_style = (
            theme.completion_selected_description if selected else theme.completion_description
        )
        command = Text(prefix, style=style)
        command.append(item.display, style=style)
        if item.argument_hint:
            command.append(f" {item.argument_hint}", style=description_style)
        command.append("  ", style=style)
        table.add_row(command, Text(item.description or "", style=description_style))
    return table


def _bullet_list(
    items: Sequence[str],
    *,
    empty: str,
    theme: TuiTheme,
) -> Text:
    text = Text()
    if not items:
        text.append(empty, style=theme.completion_description)
        return text

    for index, item in enumerate(items):
        if index:
            text.append("\n")
        text.append("• ", style=theme.completion_description)
        text.append(item, style=theme.prompt_text)
    return text


def _limited_bullet_list(
    items: Sequence[str],
    *,
    empty: str,
    theme: TuiTheme,
) -> Text:
    text = _bullet_list(
        items[:SIDEBAR_BULLET_LIST_LIMIT],
        empty=empty,
        theme=theme,
    )
    hidden_count = len(items) - SIDEBAR_BULLET_LIST_LIMIT
    if hidden_count > 0:
        text.append(f"\n...({hidden_count} more)", style=theme.completion_description)
    return text


def _short_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)
