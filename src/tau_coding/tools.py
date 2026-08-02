"""Built-in filesystem and shell tools for Tau coding sessions.

The module exposes factory functions that create provider-neutral `AgentTool`
objects plus richer `ToolDefinition` objects for callers that need prompt
metadata and JSON schemas. The tools operate relative to a configurable working
directory, return structured `AgentToolResult` values, and keep local
filesystem/shell behavior outside the reusable `tau_agent` package.
"""

import asyncio
import contextlib
import difflib
import json
import mimetypes
import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from time import monotonic

from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolExecutor,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024
DEFAULT_MAX_OUTPUT_LINES = 2_000
DEFAULT_LS_ENTRY_LIMIT = 500
DEFAULT_GREP_MATCH_LIMIT = 100
DEFAULT_FIND_RESULT_LIMIT = 1000
GREP_MAX_LINE_LENGTH = 500
DEFAULT_IMAGE_MAX_WIDTH_PX = 2000
DEFAULT_IMAGE_MAX_HEIGHT_PX = 2000
DEFAULT_INLINE_IMAGE_MAX_BASE64_BYTES = int(4.5 * 1024 * 1024)
BUILTIN_CODING_TOOL_NAMES = ("read", "ls", "grep", "find", "write", "edit", "bash")
MAX_BASH_TIMEOUT_SECONDS = 2_147_483_647 / 1000
INLINE_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SUPPORTED_IMAGE_MIME_TYPES = INLINE_IMAGE_MIME_TYPES | {"image/bmp"}
UTF8_BOM = "\ufeff"
type BashEnvironment = Mapping[str, str | None] | Callable[[], Mapping[str, str | None]]


class ToolInputError(ValueError):
    """Raised when a tool receives invalid structured arguments."""


@dataclass(frozen=True, slots=True)
class TruncationResult:
    """Metadata describing how a tool output was shortened.

    `content` contains the returned slice. The remaining fields record whether
    truncation happened, whether the line or byte limit was responsible, the
    total size of the original output, the size of the returned output, and
    edge cases such as partial-line output or a first line that is too large to
    display safely.
    """

    content: str
    truncated: bool
    truncated_by: str | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int

    def to_json(self) -> dict[str, JSONValue]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReadImageResult:
    """Image payload returned by the read tool after optional provider-size resizing."""

    mime_type: str
    bytes: int
    image_base64: str | None
    message: str
    omitted: bool = False
    resized: bool = False
    original_mime_type: str | None = None
    original_bytes: int | None = None

    def to_json(self, *, path: Path) -> dict[str, JSONValue]:
        payload: dict[str, JSONValue] = {
            "path": str(path),
            "mime_type": self.mime_type,
            "bytes": self.bytes,
            "auto_resized": self.resized,
        }
        if self.original_mime_type is not None:
            payload["original_mime_type"] = self.original_mime_type
        if self.original_bytes is not None:
            payload["original_bytes"] = self.original_bytes
        if self.omitted:
            payload["image_omitted"] = True
        elif self.image_base64 is not None:
            payload["image_base64"] = self.image_base64
        return payload


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Complete definition for a coding tool before provider conversion.

    A definition contains the tool name, user-facing description, prompt
    metadata, JSON input schema, and async executor. `to_agent_tool()` converts
    it into the smaller `AgentTool` type consumed by the provider-neutral agent
    loop while preserving prompt metadata for clients that render tool guidance.
    """

    name: str
    description: str
    prompt_snippet: str
    prompt_guidelines: tuple[str, ...]
    input_schema: Mapping[str, JSONValue]
    executor: ToolExecutor

    def to_agent_tool(self) -> AgentTool:
        return AgentTool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            executor=self.executor,
            prompt_snippet=self.prompt_snippet,
            prompt_guidelines=self.prompt_guidelines,
        )


_file_locks: dict[Path, asyncio.Lock] = {}


def create_coding_tools(
    *,
    cwd: str | Path | None = None,
    shell_path: str | Path | None = None,
    bash_environment: BashEnvironment | None = None,
    auto_resize_images: bool = True,
) -> list[AgentTool]:
    """Create the default coding-tool set for a local project.

    The returned tools are ordered as `read`, `ls`, `grep`, `find`, `write`, `edit`, and `bash`.
    Relative paths used with those tools are resolved against `cwd`; when `cwd`
    is omitted, the process current working directory at factory-call time is
    used. The tools share per-path write/edit locks within this process so
    concurrent mutations of the same file do not interleave.
    """
    root = Path.cwd() if cwd is None else Path(cwd)
    return [
        create_read_tool(cwd=root, auto_resize_images=auto_resize_images),
        create_ls_tool(cwd=root),
        create_grep_tool(cwd=root),
        create_find_tool(cwd=root),
        create_write_tool(cwd=root),
        create_edit_tool(cwd=root),
        create_bash_tool(cwd=root, shell_path=shell_path, environment=bash_environment),
    ]


def create_read_tool_definition(
    *,
    cwd: str | Path | None = None,
    auto_resize_images: bool = True,
) -> ToolDefinition:
    """Create a definition for the `read` tool.

    The tool reads a file resolved relative to `cwd` unless an absolute path is
    supplied. Text files are decoded as UTF-8 and may be sliced with optional
    1-indexed `offset` and positive integer `limit` arguments. Returned text is
    truncated to `DEFAULT_MAX_OUTPUT_LINES` lines or `DEFAULT_MAX_OUTPUT_BYTES`
    bytes, whichever comes first, and continuation hints are appended when more
    lines remain. Supported image paths (`jpg`, `png`, `gif`, and `webp`) are
    detected by MIME type and returned as base64 metadata instead of text.

    The executor raises `ToolInputError` for invalid arguments, missing files,
    directories, and offsets beyond the end of the file. Successful results
    include the resolved path and truncation metadata in `data`.
    """
    root = Path.cwd() if cwd is None else Path(cwd)

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        del signal
        raw_path = _str_arg(arguments, "path")
        path = _path_arg(arguments, "path", cwd=root)
        offset = _optional_int_arg(arguments, "offset")
        limit = _optional_int_arg(arguments, "limit")

        if offset is not None and offset < 0:
            raise ToolInputError("offset must be at least 0")
        if limit is not None and limit < 1:
            raise ToolInputError("limit must be at least 1")
        if not path.exists():
            raise ToolInputError(f"File not found: {path}")
        if path.is_dir():
            raise ToolInputError(f"Path is a directory: {path}")

        mime_type = _detect_supported_image_mime_type(path)
        if mime_type is not None:
            processed_image = _process_image_for_read(
                path,
                mime_type,
                auto_resize_images=auto_resize_images,
            )
            if processed_image.omitted:
                return AgentToolResult(
                    tool_call_id="",
                    name="read",
                    ok=True,
                    content=processed_image.message,
                    data=processed_image.to_json(path=path),
                )
            return AgentToolResult(
                tool_call_id="",
                name="read",
                ok=True,
                content=processed_image.message,
                data=processed_image.to_json(path=path),
            )

        text = path.read_text(encoding="utf-8")
        all_lines = text.split("\n")
        start_line = 0 if offset is None or offset == 0 else offset - 1
        if start_line >= len(all_lines):
            raise ToolInputError(
                f"Offset {offset} is beyond end of file ({len(all_lines)} lines total)"
            )

        user_limited_lines: int | None = None
        if limit is not None:
            end_line = min(start_line + limit, len(all_lines))
            selected = "\n".join(all_lines[start_line:end_line])
            user_limited_lines = end_line - start_line
        else:
            selected = "\n".join(all_lines[start_line:])

        truncation = truncate_head(selected)
        start_display = start_line + 1
        details: dict[str, JSONValue] = {"path": str(path), "truncation": truncation.to_json()}

        if truncation.first_line_exceeds_limit:
            first_line_size = format_size(len(all_lines[start_line].encode()))
            output = (
                f"[Line {start_display} is {first_line_size}, exceeds "
                f"{format_size(DEFAULT_MAX_OUTPUT_BYTES)} limit. Use bash: sed -n "
                f"'{start_display}p' {raw_path} | head -c {DEFAULT_MAX_OUTPUT_BYTES}]"
            )
        elif truncation.truncated:
            end_display = start_display + truncation.output_lines - 1
            next_offset = end_display + 1
            output = truncation.content
            if truncation.truncated_by == "lines":
                output += (
                    f"\n\n[Showing lines {start_display}-{end_display} of {len(all_lines)}. "
                    f"Use offset={next_offset} to continue.]"
                )
            else:
                output += (
                    f"\n\n[Showing lines {start_display}-{end_display} of {len(all_lines)} "
                    f"({format_size(DEFAULT_MAX_OUTPUT_BYTES)} limit). "
                    f"Use offset={next_offset} to continue.]"
                )
        elif user_limited_lines is not None and start_line + user_limited_lines < len(all_lines):
            remaining = len(all_lines) - (start_line + user_limited_lines)
            next_offset = start_line + user_limited_lines + 1
            output = (
                f"{truncation.content}\n\n[{remaining} more lines in file. "
                f"Use offset={next_offset} to continue.]"
            )
        else:
            output = truncation.content

        return AgentToolResult(
            tool_call_id="",
            name="read",
            ok=True,
            content=output,
            data=details,
        )

    return ToolDefinition(
        name="read",
        description=(
            "Read the contents of a file. Supports text files and images (jpg, png, gif, webp). "
            "Images are returned as base64 metadata. For text files, output is truncated to "
            f"{DEFAULT_MAX_OUTPUT_LINES} lines or {DEFAULT_MAX_OUTPUT_BYTES // 1024}KB "
            "(whichever is hit first). Use offset/limit for large files. When you need the "
            "full file, continue with offset until complete."
        ),
        prompt_snippet="Read file contents",
        prompt_guidelines=("Use read to examine files instead of cat or sed.",),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read"},
                "offset": {"type": "integer", "description": "Line number to start reading from"},
                "limit": {"type": "integer", "description": "Maximum number of lines to read"},
            },
            "required": ["path"],
        },
        executor=execute,
    )


def create_read_tool(
    *,
    cwd: str | Path | None = None,
    auto_resize_images: bool = True,
) -> AgentTool:
    """Create an `AgentTool` for reading UTF-8 text files and supported images."""
    return create_read_tool_definition(
        cwd=cwd,
        auto_resize_images=auto_resize_images,
    ).to_agent_tool()


def create_ls_tool_definition(*, cwd: str | Path | None = None) -> ToolDefinition:
    """Create a definition for the `ls` tool.

    The tool lists directory entries resolved relative to `cwd`, includes
    dotfiles, sorts case-insensitively, appends `/` to directories, and bounds
    output by entry count and Tau's normal text truncation limit. It performs no
    mutations and does not shell out.
    """
    root = Path.cwd() if cwd is None else Path(cwd)

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        del signal
        raw_path = arguments.get("path", ".")
        if not isinstance(raw_path, str):
            raise ToolInputError("path must be a string")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = root / path
        limit = _optional_int_arg(arguments, "limit")
        effective_limit = DEFAULT_LS_ENTRY_LIMIT if limit is None else limit
        if effective_limit < 1:
            raise ToolInputError("limit must be at least 1")
        if not path.exists():
            raise ToolInputError(f"Path not found: {path}")
        if not path.is_dir():
            raise ToolInputError(f"Path is not a directory: {path}")

        entries = sorted(path.iterdir(), key=lambda entry: entry.name.casefold())
        lines: list[str] = []
        entry_limit_reached = False
        for entry in entries:
            if len(lines) >= effective_limit:
                entry_limit_reached = True
                break
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{suffix}")

        content = "\n".join(lines) if lines else "(empty directory)"
        truncation = truncate_head(content, max_lines=max(effective_limit, 1))
        output = truncation.content
        notices: list[str] = []
        if entry_limit_reached:
            notices.append(
                f"{effective_limit} entries limit reached. Use limit={effective_limit * 2} for more"
            )
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_OUTPUT_BYTES)} limit reached")
        if notices:
            output = append_status_block(output, f"[{'. '.join(notices)}]")

        details: dict[str, JSONValue] = {
            "path": str(path),
            "entry_count": len(entries),
            "returned_entries": len(lines),
            "truncation": truncation.to_json(),
        }
        if entry_limit_reached:
            details["entry_limit_reached"] = effective_limit

        return AgentToolResult(
            tool_call_id="",
            name="ls",
            ok=True,
            content=output,
            data=details,
        )

    return ToolDefinition(
        name="ls",
        description=(
            "List directory contents. Returns entries sorted alphabetically, appends '/' "
            "to directories, includes dotfiles, and truncates large directories by entry "
            f"limit ({DEFAULT_LS_ENTRY_LIMIT} default) or {DEFAULT_MAX_OUTPUT_BYTES // 1024}KB."
        ),
        prompt_snippet="List directory contents",
        prompt_guidelines=("Use ls to inspect directories before reading files.",),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list; defaults to current directory",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return",
                },
            },
            "required": [],
        },
        executor=execute,
    )


def create_ls_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create an `AgentTool` for listing directory entries."""
    return create_ls_tool_definition(cwd=cwd).to_agent_tool()


def create_grep_tool_definition(*, cwd: str | Path | None = None) -> ToolDefinition:
    """Create a definition for the `grep` tool.

    The tool searches text with ripgrep, respecting `.gitignore`, and formats
    match rows as `path:line: text`. It supports literal matching,
    case-insensitive matching, glob filters, context lines, and bounded output.
    """
    root = Path.cwd() if cwd is None else Path(cwd)

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        pattern = _str_arg(arguments, "pattern")
        raw_path = arguments.get("path", ".")
        if not isinstance(raw_path, str):
            raise ToolInputError("path must be a string")
        search_path = Path(raw_path).expanduser()
        if not search_path.is_absolute():
            search_path = root / search_path
        if not search_path.exists():
            raise ToolInputError(f"Path not found: {search_path}")
        glob = arguments.get("glob")
        if glob is not None and not isinstance(glob, str):
            raise ToolInputError("glob must be a string")
        ignore_case = _optional_bool_arg(arguments, "ignore_case")
        literal = _optional_bool_arg(arguments, "literal")
        context = _optional_int_arg(arguments, "context")
        if context is not None and context < 0:
            raise ToolInputError("context must be at least 0")
        context_value = context or 0
        limit = _optional_int_arg(arguments, "limit")
        effective_limit = DEFAULT_GREP_MATCH_LIMIT if limit is None else limit
        if effective_limit < 1:
            raise ToolInputError("limit must be at least 1")
        rg = shutil.which("rg")
        if rg is None:
            raise ToolInputError("ripgrep (rg) is not available")

        args = [rg, "--json", "--line-number", "--color=never", "--hidden"]
        if ignore_case:
            args.append("--ignore-case")
        if literal:
            args.append("--fixed-strings")
        if glob:
            args.extend(["--glob", glob])
        args.extend(["--", pattern, str(search_path)])

        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr, _timed_out, cancelled = await _communicate_with_cancellation(
            process,
            timeout=None,
            signal=signal,
        )
        if cancelled:
            raise ToolInputError("Search cancelled")
        if process.returncode not in (0, 1):
            message = stderr.decode(errors="replace").strip() or (
                f"ripgrep exited with code {process.returncode}"
            )
            raise ToolInputError(message)

        is_directory = search_path.is_dir()
        matches: list[tuple[Path, int, str]] = []
        total_matches = 0
        for raw_line in stdout.decode(errors="replace").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            line_number = data.get("line_number")
            path_data = data.get("path")
            line_data = data.get("lines")
            if not isinstance(line_number, int):
                continue
            if not isinstance(path_data, dict) or not isinstance(path_data.get("text"), str):
                continue
            if not isinstance(line_data, dict) or not isinstance(line_data.get("text"), str):
                continue
            total_matches += 1
            if len(matches) < effective_limit:
                line_text = line_data["text"].removesuffix("\n").replace("\r", "")
                matches.append((Path(path_data["text"]), line_number, line_text))

        if not matches:
            return AgentToolResult(
                tool_call_id="",
                name="grep",
                ok=True,
                content="No matches found",
                data={
                    "path": str(search_path),
                    "match_count": 0,
                    "returned_matches": 0,
                },
            )

        output_lines: list[str] = []
        lines_truncated = False
        file_line_cache: dict[Path, list[str]] = {}
        for file_path, line_number, line_text in matches:
            display_path = _grep_display_path(
                file_path,
                search_path=search_path,
                is_directory=is_directory,
            )
            if context_value <= 0:
                truncated_line, was_truncated = _truncate_grep_line(line_text)
                lines_truncated = lines_truncated or was_truncated
                output_lines.append(
                    f"{display_path}:{line_number}: {truncated_line}"
                )
                continue
            context_lines = await _grep_context_lines(file_path, cache=file_line_cache)
            start = max(1, line_number - context_value)
            end = min(len(context_lines), line_number + context_value)
            for current_line_number in range(start, end + 1):
                context_line = context_lines[current_line_number - 1] if context_lines else ""
                truncated_line, was_truncated = _truncate_grep_line(context_line)
                lines_truncated = lines_truncated or was_truncated
                separator = ":" if current_line_number == line_number else "-"
                output_lines.append(
                    f"{display_path}{separator}{current_line_number}{separator} {truncated_line}"
                )

        raw_output = "\n".join(output_lines)
        truncation = truncate_head(raw_output, max_lines=max(len(output_lines), 1))
        output = truncation.content
        match_limit_reached = total_matches > len(matches)
        notices: list[str] = []
        if match_limit_reached:
            notices.append(
                f"{effective_limit} matches limit reached. Use limit={effective_limit * 2} "
                "for more, or refine pattern"
            )
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_OUTPUT_BYTES)} limit reached")
        if lines_truncated:
            notices.append(
                f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. "
                "Use read tool to see full lines"
            )
        if notices:
            output = append_status_block(output, f"[{'. '.join(notices)}]")

        details: dict[str, JSONValue] = {
            "path": str(search_path),
            "match_count": total_matches,
            "returned_matches": len(matches),
            "context": context_value,
            "truncation": truncation.to_json(),
        }
        if match_limit_reached:
            details["match_limit_reached"] = effective_limit
        if lines_truncated:
            details["lines_truncated"] = True

        return AgentToolResult(
            tool_call_id="",
            name="grep",
            ok=True,
            content=output,
            data=details,
        )

    return ToolDefinition(
        name="grep",
        description=(
            "Search file contents for a pattern with ripgrep. Returns matching lines with "
            "file paths and line numbers, respects .gitignore, and truncates output to "
            f"{DEFAULT_GREP_MATCH_LIMIT} matches or {DEFAULT_MAX_OUTPUT_BYTES // 1024}KB."
        ),
        prompt_snippet="Search file contents for patterns",
        prompt_guidelines=("Use grep to find relevant files before reading or editing them.",),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern"},
                "path": {
                    "type": "string",
                    "description": "Directory or file to search; defaults to current directory",
                },
                "glob": {"type": "string", "description": "Optional glob filter, such as *.py"},
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search",
                },
                "literal": {
                    "type": "boolean",
                    "description": "Treat pattern as a literal string instead of a regex",
                },
                "context": {
                    "type": "integer",
                    "description": "Number of context lines before and after each match",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matches to return",
                },
            },
            "required": ["pattern"],
        },
        executor=execute,
    )


def create_grep_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create an `AgentTool` for searching file contents with ripgrep."""
    return create_grep_tool_definition(cwd=cwd).to_agent_tool()


def create_find_tool_definition(*, cwd: str | Path | None = None) -> ToolDefinition:
    """Create a definition for the `find` tool.

    The tool searches for files by glob pattern with `fd`, formats results
    relative to the search root, and bounds output by result count and Tau's
    normal text truncation limit.
    """
    root = Path.cwd() if cwd is None else Path(cwd)

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        pattern = _str_arg(arguments, "pattern")
        raw_path = arguments.get("path", ".")
        if not isinstance(raw_path, str):
            raise ToolInputError("path must be a string")
        search_path = Path(raw_path).expanduser()
        if not search_path.is_absolute():
            search_path = root / search_path
        if not search_path.exists():
            raise ToolInputError(f"Path not found: {search_path}")
        limit = _optional_int_arg(arguments, "limit")
        effective_limit = DEFAULT_FIND_RESULT_LIMIT if limit is None else limit
        if effective_limit < 1:
            raise ToolInputError("limit must be at least 1")
        fd = shutil.which("fd") or shutil.which("fdfind")
        if fd is None:
            raise ToolInputError("fd is not available")

        args = [fd, "--glob", "--color=never", "--hidden"]
        if not _inside_git_repo(search_path):
            args.append("--no-require-git")
        effective_pattern = pattern
        if "/" in pattern:
            args.append("--full-path")
            if not pattern.startswith("/") and not pattern.startswith("**/") and pattern != "**":
                effective_pattern = f"**/{pattern}"
        args.extend(["--", effective_pattern, str(search_path)])

        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr, _timed_out, cancelled = await _communicate_with_cancellation(
            process,
            timeout=None,
            signal=signal,
        )
        if cancelled:
            raise ToolInputError("Search cancelled")
        output_text = stdout.decode(errors="replace")
        if process.returncode not in (0, None) and not output_text.strip():
            message = stderr.decode(errors="replace").strip() or (
                f"fd exited with code {process.returncode}"
            )
            raise ToolInputError(message)

        matching_lines = sorted(
            _find_display_path(raw_line, search_path=search_path)
            for raw_line in output_text.splitlines()
            if raw_line.strip()
        )
        lines = matching_lines[:effective_limit]
        if not lines:
            return AgentToolResult(
                tool_call_id="",
                name="find",
                ok=True,
                content="No files found matching pattern",
                data={
                    "path": str(search_path),
                    "result_count": 0,
                    "returned_results": 0,
                },
            )

        raw_output = "\n".join(lines)
        truncation = truncate_head(raw_output, max_lines=max(len(lines), 1))
        output = truncation.content
        result_limit_reached = len(matching_lines) > effective_limit
        notices: list[str] = []
        if result_limit_reached:
            notices.append(
                f"{effective_limit} results limit reached. Use limit={effective_limit * 2} "
                "for more, or refine pattern"
            )
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_OUTPUT_BYTES)} limit reached")
        if notices:
            output = append_status_block(output, f"[{'. '.join(notices)}]")

        details: dict[str, JSONValue] = {
            "path": str(search_path),
            "result_count": len(lines),
            "returned_results": len(lines),
            "truncation": truncation.to_json(),
        }
        if result_limit_reached:
            details["result_limit_reached"] = effective_limit

        return AgentToolResult(
            tool_call_id="",
            name="find",
            ok=True,
            content=output,
            data=details,
        )

    return ToolDefinition(
        name="find",
        description=(
            "Search for files by glob pattern with fd. Returns matching file paths relative "
            "to the search directory, respects .gitignore, and truncates output to "
            f"{DEFAULT_FIND_RESULT_LIMIT} results or {DEFAULT_MAX_OUTPUT_BYTES // 1024}KB."
        ),
        prompt_snippet="Find files by glob pattern",
        prompt_guidelines=("Use find to locate files by name before reading or editing them.",),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern such as *.py or src/**/*.py",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search; defaults to current directory",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                },
            },
            "required": ["pattern"],
        },
        executor=execute,
    )


def create_find_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create an `AgentTool` for finding files by glob pattern."""
    return create_find_tool_definition(cwd=cwd).to_agent_tool()


def create_write_tool_definition(*, cwd: str | Path | None = None) -> ToolDefinition:
    """Create a definition for the `write` tool.

    The tool writes the supplied string `content` to `path`, resolving relative
    paths against `cwd`. Parent directories are created automatically and any
    existing file is overwritten. Writes use UTF-8 text encoding and are guarded
    by a per-path async lock so multiple writes/edits to the same resolved file
    are serialized within this process.

    The executor raises `ToolInputError` when `path` or `content` has the wrong
    type. Successful results include the resolved path and number of characters
    written in `data`.
    """
    root = Path.cwd() if cwd is None else Path(cwd)

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        del signal
        path = _path_arg(arguments, "path", cwd=root)
        content = _str_arg(arguments, "content")

        async with _file_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return AgentToolResult(
            tool_call_id="",
            name="write",
            ok=True,
            content=f"Successfully wrote to {path}.",
            data={"path": str(path), "characters": len(content)},
        )

    return ToolDefinition(
        name="write",
        description=(
            "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. "
            "Automatically creates parent directories."
        ),
        prompt_snippet="Create or overwrite files",
        prompt_guidelines=("Use write only for new files or complete rewrites.",),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to write"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["path", "content"],
        },
        executor=execute,
    )


def create_write_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create an `AgentTool` for creating or overwriting UTF-8 text files."""
    return create_write_tool_definition(cwd=cwd).to_agent_tool()


def create_edit_tool_definition(*, cwd: str | Path | None = None) -> ToolDefinition:
    """Create a definition for the `edit` tool.

    The tool applies one or more exact text replacements to a single UTF-8 file
    resolved relative to `cwd`. Each edit item contains `oldText` and `newText`.
    Every `oldText` must be non-empty, must occur exactly once in the original
    file, and must not overlap another edit span. All replacements are validated
    before writing, so the file is left unchanged if any edit fails.

    File content and edit text are normalized to LF for matching, then the
    original file's dominant line ending is restored after replacement. UTF-8
    byte-order marks are preserved. The executor also accepts legacy top-level
    `oldText`/`newText` arguments and JSON-string `edits` values by normalizing
    them into the canonical edits list.

    Successful results include the resolved path, edit count, an ndiff-style
    diff, a unified patch, and the first changed line in `data`.
    """
    root = Path.cwd() if cwd is None else Path(cwd)

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        del signal
        prepared = _prepare_edit_arguments(arguments)
        path = _path_arg(prepared, "path", cwd=root)
        edits = _edits_arg(prepared)

        if not path.exists():
            raise ToolInputError(f"Could not edit file: {path}. File not found.")
        if path.is_dir():
            raise ToolInputError(f"Could not edit file: {path}. Path is a directory.")

        async with _file_lock(path):
            raw_content = path.read_text(encoding="utf-8")
            bom, content = _strip_bom(raw_content)
            original_ending = detect_line_ending(content)
            normalized = normalize_to_lf(content)
            base_content, new_content = apply_edits_to_normalized_content(
                normalized, edits, str(path)
            )
            final_content = bom + restore_line_endings(new_content, original_ending)
            path.write_text(final_content, encoding="utf-8")

        diff_text, first_changed_line = generate_diff_string(base_content, new_content)
        patch = generate_unified_patch(str(path), base_content, new_content)
        return AgentToolResult(
            tool_call_id="",
            name="edit",
            ok=True,
            content=f"Successfully replaced {len(edits)} block(s) in {path}.",
            data={
                "path": str(path),
                "edits": len(edits),
                "diff": diff_text,
                "patch": patch,
                "first_changed_line": first_changed_line,
            },
        )

    return ToolDefinition(
        name="edit",
        description=(
            "Edit a single file using exact text replacement. Every edits[].oldText must match "
            "a unique, non-overlapping region of the original file. If two changes affect the "
            "same block or nearby lines, merge them into one edit instead of emitting overlapping "
            "edits. Do not include large unchanged regions just to connect distant changes."
        ),
        prompt_snippet=(
            "Make precise file edits with exact text replacement, including multiple disjoint "
            "edits in one call"
        ),
        prompt_guidelines=(
            "Use edit for precise changes (edits[].oldText must match exactly)",
            "When changing multiple separate locations in one file, use one edit call with "
            "multiple entries in edits[] instead of multiple edit calls",
            "Each edits[].oldText is matched against the original file, not after earlier "
            "edits are applied. Do not emit overlapping or nested edits. Merge nearby "
            "changes into one edit.",
            "Keep edits[].oldText as small as possible while still being unique in the file. "
            "Do not pad with large unchanged regions.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit"},
                "edits": {
                    "type": "array",
                    "description": "One or more targeted replacements.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string"},
                            "newText": {"type": "string"},
                        },
                        "required": ["oldText", "newText"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["path", "edits"],
            "additionalProperties": False,
        },
        executor=execute,
    )


def create_edit_tool(*, cwd: str | Path | None = None) -> AgentTool:
    """Create an `AgentTool` for exact, validated text replacement in one file."""
    return create_edit_tool_definition(cwd=cwd).to_agent_tool()


def create_bash_tool_definition(
    *,
    cwd: str | Path | None = None,
    shell_path: str | Path | None = None,
    environment: BashEnvironment | None = None,
    on_output_chunk: Callable[[str], None] | None = None,
) -> ToolDefinition:
    """Create a definition for the `bash` tool.

    The tool runs a shell command with `cwd` as the subprocess working
    directory and combines stdout and stderr into one UTF-8 decoded output
    stream. The optional `timeout` argument must be positive when supplied. On
    timeout, POSIX commands are started in a new session and the entire process
    group is killed so shell children from pipelines or compound commands do
    not continue running; non-POSIX platforms fall back to killing the direct
    subprocess.

    Output is tail-truncated to `DEFAULT_MAX_OUTPUT_LINES` lines or
    `DEFAULT_MAX_OUTPUT_BYTES` bytes. When truncation occurs, the full output is
    written to a temporary log file and that path is reported in `data`.
    Successful and failed command results both include exit code, timeout state,
    duration, truncation metadata, and full-output path metadata.
    """
    root = Path.cwd() if cwd is None else Path(cwd)
    shell_executable = None if shell_path is None else str(shell_path)

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        command = _str_arg(arguments, "command")
        timeout = _optional_float_arg(arguments, "timeout")
        timeout = _validate_bash_timeout(timeout)
        if signal is not None and signal.is_cancelled():
            raise ToolInputError("Command cancelled")

        def emit_output_chunk(chunk: str) -> None:
            if on_output_chunk is not None:
                on_output_chunk(chunk)
            if on_update is not None:
                on_update(
                    chunk,
                    {
                        "tool": "bash",
                        "stream": "combined_output",
                        "command": command,
                    },
                )

        start = monotonic()
        process_env = _resolve_bash_process_environment(environment)
        if os.name == "posix":
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=root,
                env=process_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                executable=shell_executable,
                start_new_session=True,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=root,
                env=process_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                executable=shell_executable,
            )
        output_bytes, _stderr, timed_out, cancelled = await _communicate_with_cancellation(
            process,
            timeout=timeout,
            signal=signal,
            on_output_chunk=emit_output_chunk
            if on_output_chunk is not None or on_update is not None
            else None,
        )

        output = output_bytes.decode(errors="replace")
        truncation = truncate_tail(output)
        full_output_path: str | None = None
        output_text = truncation.content or "(no output)"
        if truncation.truncated:
            full_output_path = _write_temp_output(output)
            start_line = truncation.total_lines - truncation.output_lines + 1
            end_line = truncation.total_lines
            if truncation.last_line_partial:
                output_text += (
                    f"\n\n[Showing last {format_size(truncation.output_bytes)} of line {end_line}. "
                    f"Full output: {full_output_path}]"
                )
            elif truncation.truncated_by == "lines":
                output_text += (
                    f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines}. "
                    f"Full output: {full_output_path}]"
                )
            else:
                output_text += (
                    f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines} "
                    f"({format_size(DEFAULT_MAX_OUTPUT_BYTES)} limit). "
                    f"Full output: {full_output_path}]"
                )

        exit_code = process.returncode
        status: str | None = None
        if timed_out:
            status = (
                f"Command timed out after {timeout:g} seconds" if timeout else "Command timed out"
            )
        elif cancelled:
            status = "Command cancelled"
        elif exit_code not in (0, None):
            status = f"Command exited with code {exit_code}"
        if status:
            output_text = append_status_block(output_text, status)

        ok = exit_code == 0 and not timed_out and not cancelled
        return AgentToolResult(
            tool_call_id="",
            name="bash",
            ok=ok,
            content=output_text,
            error=None if ok else status,
            data={
                "command": command,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "cancelled": cancelled,
                "duration_seconds": round(monotonic() - start, 3),
                "truncation": truncation.to_json(),
                "full_output_path": full_output_path,
            },
        )

    return ToolDefinition(
        name="bash",
        description=(
            "Execute a bash command in the current working directory. Returns stdout and stderr. "
            f"Output is truncated to last {DEFAULT_MAX_OUTPUT_LINES} lines or "
            f"{DEFAULT_MAX_OUTPUT_BYTES // 1024}KB (whichever is hit first). If truncated, "
            "full output is saved to a temp file. Optionally provide a timeout in seconds."
        ),
        prompt_snippet="Execute bash commands (ls, grep, find, etc.)",
        prompt_guidelines=(
            ("Inspect TAU_* environment variables for current model and session details.",)
            if environment is not None
            else ()
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute"},
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (optional, no default timeout)",
                },
            },
            "required": ["command"],
        },
        executor=execute,
    )


def create_bash_tool(
    *,
    cwd: str | Path | None = None,
    shell_path: str | Path | None = None,
    environment: BashEnvironment | None = None,
    on_output_chunk: Callable[[str], None] | None = None,
) -> AgentTool:
    """Create an `AgentTool` for executing shell commands with captured output."""
    return create_bash_tool_definition(
        cwd=cwd,
        shell_path=shell_path,
        environment=environment,
        on_output_chunk=on_output_chunk,
    ).to_agent_tool()


def format_size(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count}B"
    if bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f}KB"
    return f"{bytes_count / (1024 * 1024):.1f}MB"


def append_status_block(text: str, status: str) -> str:
    """Append command status text after a blank line when output already exists."""
    return f"{text}\n\n{status}" if text else status


def _validate_bash_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if not isfinite(timeout):
        raise ToolInputError("timeout must be a finite number of seconds")
    if timeout <= 0:
        raise ToolInputError("timeout must be greater than 0")
    if timeout > MAX_BASH_TIMEOUT_SECONDS:
        raise ToolInputError(f"timeout must be at most {MAX_BASH_TIMEOUT_SECONDS:g} seconds")
    return timeout


def _resolve_bash_process_environment(environment: BashEnvironment | None) -> dict[str, str] | None:
    if environment is None:
        return None
    overrides = environment() if callable(environment) else environment
    process_env = os.environ.copy()
    for key, value in overrides.items():
        if value is None:
            process_env.pop(key, None)
        else:
            process_env[key] = value
    return process_env


async def _communicate_with_cancellation(
    process: asyncio.subprocess.Process,
    *,
    timeout: float | None,
    signal: ToolCancellationToken | None,
    on_output_chunk: Callable[[str], None] | None = None,
) -> tuple[bytes, bytes | None, bool, bool]:
    if on_output_chunk is not None:
        return await _stream_with_cancellation(
            process,
            timeout=timeout,
            signal=signal,
            on_output_chunk=on_output_chunk,
        )

    communicate = asyncio.create_task(process.communicate())
    cancel_watch: asyncio.Task[None] | None = None
    try:
        wait_for = {communicate}
        if signal is not None:
            cancel_watch = asyncio.create_task(_wait_for_cancel(signal))
            wait_for.add(cancel_watch)

        done, _pending = await asyncio.wait(
            wait_for,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicate in done:
            output_bytes, stderr = communicate.result()
            return output_bytes, stderr, False, False

        cancelled = cancel_watch is not None and cancel_watch in done
        _kill_process_tree(process)
        try:
            output_bytes, stderr = await communicate
        except asyncio.CancelledError:
            output_bytes, stderr = b"", None
        return output_bytes, stderr, not cancelled, cancelled
    except asyncio.CancelledError:
        _kill_process_tree(process)
        if not communicate.done():
            communicate.cancel()
        raise
    finally:
        if cancel_watch is not None:
            cancel_watch.cancel()


async def _stream_with_cancellation(
    process: asyncio.subprocess.Process,
    *,
    timeout: float | None,
    signal: ToolCancellationToken | None,
    on_output_chunk: Callable[[str], None],
) -> tuple[bytes, bytes | None, bool, bool]:
    output_task = asyncio.create_task(_read_process_output(process, on_output_chunk))
    wait_task = asyncio.create_task(process.wait())
    cancel_watch: asyncio.Task[None] | None = None
    try:
        wait_for = {wait_task}
        if signal is not None:
            cancel_watch = asyncio.create_task(_wait_for_cancel(signal))
            wait_for.add(cancel_watch)

        done, _pending = await asyncio.wait(
            wait_for,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if wait_task in done:
            output_bytes = await output_task
            return output_bytes, None, False, False

        cancelled = cancel_watch is not None and cancel_watch in done
        _kill_process_tree(process)
        output_bytes = await output_task
        return output_bytes, None, not cancelled, cancelled
    except asyncio.CancelledError:
        _kill_process_tree(process)
        if not output_task.done():
            output_task.cancel()
        if not wait_task.done():
            wait_task.cancel()
        raise
    finally:
        if cancel_watch is not None:
            cancel_watch.cancel()


async def _read_process_output(
    process: asyncio.subprocess.Process,
    on_output_chunk: Callable[[str], None],
) -> bytes:
    if process.stdout is None:
        return b""
    chunks: list[bytes] = []
    while True:
        chunk = await process.stdout.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        with contextlib.suppress(Exception):
            on_output_chunk(chunk.decode(errors="replace"))
    return b"".join(chunks)


async def _wait_for_cancel(signal: ToolCancellationToken) -> None:
    while not signal.is_cancelled():
        await asyncio.sleep(0.05)


def truncate_head(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_OUTPUT_LINES,
    max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> TruncationResult:
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)
    total_bytes = len(content.encode())
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _truncation_result(
            content, False, None, total_lines, total_bytes, total_lines, total_bytes
        )

    first_line_bytes = len(lines[0].encode()) if lines else 0
    if first_line_bytes > max_bytes:
        return _truncation_result(
            "", True, "bytes", total_lines, total_bytes, 0, 0, first_line=True
        )

    output_lines: list[str] = []
    output_bytes = 0
    truncated_by = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = len(line.encode()) + (1 if index > 0 else 0)
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        output_lines.append(line)
        output_bytes += line_bytes

    output = "\n".join(output_lines)
    return _truncation_result(
        output,
        True,
        truncated_by,
        total_lines,
        total_bytes,
        len(output_lines),
        len(output.encode()),
    )


def truncate_tail(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_OUTPUT_LINES,
    max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> TruncationResult:
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)
    total_bytes = len(content.encode())
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _truncation_result(
            content, False, None, total_lines, total_bytes, total_lines, total_bytes
        )

    output_lines: list[str] = []
    output_bytes = 0
    truncated_by = "lines"
    last_line_partial = False
    for line in reversed(lines):
        line_bytes = len(line.encode()) + (1 if output_lines else 0)
        if len(output_lines) >= max_lines:
            truncated_by = "lines"
            break
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not output_lines:
                clipped = _truncate_string_to_bytes_from_end(line, max_bytes)
                output_lines.insert(0, clipped)
                output_bytes = len(clipped.encode())
                last_line_partial = True
            break
        output_lines.insert(0, line)
        output_bytes += line_bytes

    output = "\n".join(output_lines)
    return _truncation_result(
        output,
        True,
        truncated_by,
        total_lines,
        total_bytes,
        len(output_lines),
        len(output.encode()),
        last_line_partial=last_line_partial,
    )


def detect_line_ending(content: str) -> str:
    crlf_index = content.find("\r\n")
    lf_index = content.find("\n")
    if lf_index == -1 or crlf_index == -1:
        return "\n"
    return "\r\n" if crlf_index < lf_index else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def apply_edits_to_normalized_content(
    normalized_content: str,
    edits: list[dict[str, str]],
    path: str,
) -> tuple[str, str]:
    normalized_edits = [
        {"oldText": normalize_to_lf(edit["oldText"]), "newText": normalize_to_lf(edit["newText"])}
        for edit in edits
    ]
    for index, edit in enumerate(normalized_edits):
        if not edit["oldText"]:
            raise ToolInputError(_empty_old_text_error(path, index, len(normalized_edits)))

    matches: list[tuple[int, int, str]] = []
    for index, edit in enumerate(normalized_edits):
        old_text = edit["oldText"]
        occurrences = _count_occurrences(normalized_content, old_text)
        if occurrences == 0:
            raise ToolInputError(_not_found_error(path, index, len(normalized_edits)))
        if occurrences > 1:
            raise ToolInputError(_duplicate_error(path, index, len(normalized_edits), occurrences))
        start = normalized_content.index(old_text)
        matches.append((start, start + len(old_text), edit["newText"]))

    _validate_non_overlapping(matches)
    new_content = normalized_content
    for start, end, new_text in sorted(matches, reverse=True):
        new_content = f"{new_content[:start]}{new_text}{new_content[end:]}"
    if new_content == normalized_content:
        raise ToolInputError(_no_change_error(path, len(normalized_edits)))
    return normalized_content, new_content


def generate_diff_string(old: str, new: str) -> tuple[str, int | None]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = "\n".join(difflib.ndiff(old_lines, new_lines))
    first_changed_line: int | None = None
    new_line_number = 0
    for line in difflib.ndiff(old_lines, new_lines):
        if line.startswith("  "):
            new_line_number += 1
        elif line.startswith("+"):
            new_line_number += 1
            if first_changed_line is None:
                first_changed_line = new_line_number
        elif line.startswith("-") and first_changed_line is None:
            first_changed_line = max(new_line_number + 1, 1)
    return diff, first_changed_line


def generate_unified_patch(path: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )


def _truncation_result(
    content: str,
    truncated: bool,
    truncated_by: str | None,
    total_lines: int,
    total_bytes: int,
    output_lines: int,
    output_bytes: int,
    *,
    last_line_partial: bool = False,
    first_line: bool = False,
) -> TruncationResult:
    return TruncationResult(
        content=content,
        truncated=truncated,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=output_lines,
        output_bytes=output_bytes,
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=first_line,
        max_lines=DEFAULT_MAX_OUTPUT_LINES,
        max_bytes=DEFAULT_MAX_OUTPUT_BYTES,
    )


def _split_lines_for_counting(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def _truncate_string_to_bytes_from_end(text: str, max_bytes: int) -> str:
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    clipped = encoded[-max_bytes:]
    return clipped.decode(errors="ignore")


def _str_arg(arguments: Mapping[str, JSONValue], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ToolInputError(f"{name} must be a string")
    return value


def _path_arg(arguments: Mapping[str, JSONValue], name: str, *, cwd: Path) -> Path:
    value = _str_arg(arguments, name)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _optional_int_arg(arguments: Mapping[str, JSONValue], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ToolInputError(f"{name} must be an integer")
    return value


def _optional_bool_arg(arguments: Mapping[str, JSONValue], name: str) -> bool:
    value = arguments.get(name)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ToolInputError(f"{name} must be a boolean")
    return value


def _optional_float_arg(arguments: Mapping[str, JSONValue], name: str) -> float | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise ToolInputError(f"{name} must be a number")
    return float(value)


def _grep_display_path(file_path: Path, *, search_path: Path, is_directory: bool) -> str:
    if is_directory:
        with contextlib.suppress(ValueError):
            relative = file_path.relative_to(search_path)
            return relative.as_posix() or file_path.name
    return file_path.name


def _find_display_path(raw_line: str, *, search_path: Path) -> str:
    stripped = raw_line.replace("\r", "").strip()
    had_trailing_slash = stripped.endswith(("/", "\\"))
    path = Path(stripped)
    with contextlib.suppress(ValueError):
        path = path.relative_to(search_path)
    if path.is_absolute():
        with contextlib.suppress(ValueError):
            path = path.relative_to(search_path.resolve())
    display = path.as_posix()
    if had_trailing_slash and not display.endswith("/"):
        display += "/"
    return display


def _inside_git_repo(path: Path) -> bool:
    current = path if path.is_dir() else path.parent
    return any((candidate / ".git").exists() for candidate in (current, *current.parents))


async def _grep_context_lines(file_path: Path, *, cache: dict[Path, list[str]]) -> list[str]:
    cached = cache.get(file_path)
    if cached is not None:
        return cached

    def read_lines() -> list[str]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            return normalize_to_lf(content).split("\n")
        except OSError:
            return []

    lines = await asyncio.to_thread(read_lines)
    cache[file_path] = lines
    return lines


def _truncate_grep_line(text: str) -> tuple[str, bool]:
    if len(text) <= GREP_MAX_LINE_LENGTH:
        return text, False
    return f"{text[:GREP_MAX_LINE_LENGTH]}...", True


def _prepare_edit_arguments(arguments: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
    prepared = dict(arguments)
    edits_value = prepared.get("edits")
    if isinstance(edits_value, str):
        try:
            parsed = json.loads(edits_value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            prepared["edits"] = parsed

    old_text = prepared.get("oldText")
    new_text = prepared.get("newText")
    if isinstance(old_text, str) and isinstance(new_text, str):
        edits = prepared.get("edits")
        edit_list = edits if isinstance(edits, list) else []
        prepared["edits"] = [*edit_list, {"oldText": old_text, "newText": new_text}]
        prepared.pop("oldText", None)
        prepared.pop("newText", None)
    return prepared


def _edits_arg(arguments: Mapping[str, JSONValue]) -> list[dict[str, str]]:
    value = arguments.get("edits")
    if not isinstance(value, list) or not value:
        raise ToolInputError(
            "Edit tool input is invalid. edits must contain at least one replacement."
        )

    edits: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ToolInputError(f"edits[{index}] must be an object")
        old_text = item.get("oldText")
        new_text = item.get("newText")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ToolInputError(
                f"edits[{index}].oldText and edits[{index}].newText must be strings"
            )
        edits.append({"oldText": old_text, "newText": new_text})
    return edits


def _validate_non_overlapping(spans: list[tuple[int, int, str]]) -> None:
    previous_end = -1
    for start, end, _new_text in sorted(spans):
        if start < previous_end:
            raise ToolInputError("Edits must not overlap")
        previous_end = end


def _count_occurrences(content: str, text: str) -> int:
    count = 0
    start = 0
    while True:
        index = content.find(text, start)
        if index == -1:
            return count
        count += 1
        start = index + len(text)


def _strip_bom(content: str) -> tuple[str, str]:
    return (UTF8_BOM, content[1:]) if content.startswith(UTF8_BOM) else ("", content)


def _not_found_error(path: str, edit_index: int, total_edits: int) -> str:
    if total_edits == 1:
        return (
            f"Could not find the exact text in {path}. The old text must match exactly "
            "including all whitespace and newlines."
        )
    return (
        f"Could not find edits[{edit_index}] in {path}. The oldText must match exactly "
        "including all whitespace and newlines."
    )


def _duplicate_error(path: str, edit_index: int, total_edits: int, occurrences: int) -> str:
    if total_edits == 1:
        return (
            f"Found {occurrences} occurrences of the text in {path}. The text must be unique. "
            "Please provide more context to make it unique."
        )
    return (
        f"Found {occurrences} occurrences of edits[{edit_index}] in {path}. "
        "Each oldText must be unique. Please provide more context to make it unique."
    )


def _empty_old_text_error(path: str, edit_index: int, total_edits: int) -> str:
    if total_edits == 1:
        return f"oldText must not be empty in {path}."
    return f"edits[{edit_index}].oldText must not be empty in {path}."


def _no_change_error(path: str, total_edits: int) -> str:
    if total_edits == 1:
        return (
            f"No changes made to {path}. The replacement produced identical content. "
            "This might indicate an issue with special characters or the text not existing "
            "as expected."
        )
    return f"No changes made to {path}. The replacements produced identical content."


def _detect_supported_image_mime_type(path: Path) -> str | None:
    mime_type, _encoding = mimetypes.guess_type(path)
    if mime_type in SUPPORTED_IMAGE_MIME_TYPES:
        return mime_type
    with contextlib.suppress(OSError):
        header = path.read_bytes()[:30]
        if _is_bmp_header(header):
            return "image/bmp"
    return None


def _process_image_for_read(
    path: Path,
    mime_type: str,
    *,
    auto_resize_images: bool,
) -> ReadImageResult:
    data = path.read_bytes()
    if mime_type not in INLINE_IMAGE_MIME_TYPES:
        converted = _resize_image_with_magick(path)
        if converted is None:
            return ReadImageResult(
                mime_type=mime_type,
                bytes=len(data),
                image_base64=None,
                message=(
                    "[Image omitted: could not be converted to a supported inline image format.]"
                ),
                omitted=True,
            )
        converted_mime_type, converted_data = converted
        return ReadImageResult(
            mime_type=converted_mime_type,
            bytes=len(converted_data),
            image_base64=_base64_text(converted_data),
            message=f"Read image file [{converted_mime_type}] (converted from {mime_type})",
            resized=True,
            original_mime_type=mime_type,
            original_bytes=len(data),
        )

    if not auto_resize_images or _image_fits_inline(path, data):
        return ReadImageResult(
            mime_type=mime_type,
            bytes=len(data),
            image_base64=_base64_text(data),
            message=f"Read image file [{mime_type}]",
        )

    resized = _resize_image_with_magick(path)
    if resized is None:
        return ReadImageResult(
            mime_type=mime_type,
            bytes=len(data),
            image_base64=None,
            message="[Image omitted: could not be resized below the inline image size limit.]",
            omitted=True,
        )
    resized_mime_type, resized_data = resized
    return ReadImageResult(
        mime_type=resized_mime_type,
        bytes=len(resized_data),
        image_base64=_base64_text(resized_data),
        message=f"Read image file [{resized_mime_type}] (auto-resized from {mime_type})",
        resized=True,
        original_mime_type=mime_type,
        original_bytes=len(data),
    )


def _image_fits_inline(path: Path, data: bytes) -> bool:
    if _base64_encoded_size(data) >= DEFAULT_INLINE_IMAGE_MAX_BASE64_BYTES:
        return False
    dimensions = _identify_image_dimensions(path)
    if dimensions is None:
        return True
    width_px, height_px = dimensions
    return width_px <= DEFAULT_IMAGE_MAX_WIDTH_PX and height_px <= DEFAULT_IMAGE_MAX_HEIGHT_PX


def _is_bmp_header(header: bytes) -> bool:
    if len(header) < 30 or not header.startswith(b"BM"):
        return False
    declared_file_size = int.from_bytes(header[2:6], "little")
    pixel_data_offset = int.from_bytes(header[10:14], "little")
    dib_header_size = int.from_bytes(header[14:18], "little")
    if declared_file_size != 0 and declared_file_size < 26:
        return False
    if pixel_data_offset < 14 + dib_header_size:
        return False
    if declared_file_size != 0 and pixel_data_offset >= declared_file_size:
        return False
    if dib_header_size == 12:
        color_planes = int.from_bytes(header[22:24], "little")
        bits_per_pixel = int.from_bytes(header[24:26], "little")
    elif 40 <= dib_header_size <= 124:
        color_planes = int.from_bytes(header[26:28], "little")
        bits_per_pixel = int.from_bytes(header[28:30], "little")
    else:
        return False
    return color_planes == 1 and bits_per_pixel in {1, 4, 8, 16, 24, 32}


def _resize_image_with_magick(path: Path) -> tuple[str, bytes] | None:
    magick = shutil.which("magick") or shutil.which("convert")
    if magick is None:
        return None

    first_frame_path = f"{path}[0]"
    for scale_percent in (100, 75, 55, 40, 25, 15, 8):
        for quality in (85, 70, 55, 40):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as output_file:
                output_path = Path(output_file.name)
            try:
                args = [
                    magick,
                    first_frame_path,
                    "-auto-orient",
                    "-resize",
                    f"{DEFAULT_IMAGE_MAX_WIDTH_PX}x{DEFAULT_IMAGE_MAX_HEIGHT_PX}>",
                    "-resize",
                    f"{scale_percent}%",
                    "-strip",
                    "-quality",
                    str(quality),
                    f"jpg:{output_path}",
                ]
                result = subprocess.run(
                    args,
                    check=False,
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode != 0 or not output_path.exists():
                    continue
                data = output_path.read_bytes()
                if data and _base64_encoded_size(data) < DEFAULT_INLINE_IMAGE_MAX_BASE64_BYTES:
                    return "image/jpeg", data
            except (OSError, subprocess.SubprocessError):
                return None
            finally:
                with contextlib.suppress(OSError):
                    output_path.unlink()
    return None


def _identify_image_dimensions(path: Path) -> tuple[int, int] | None:
    magick = shutil.which("magick") or shutil.which("identify")
    if magick is None:
        return None
    if Path(magick).name == "magick":
        args = [magick, "identify", "-format", "%w %h", f"{path}[0]"]
    else:
        args = [magick, "-format", "%w %h", f"{path}[0]"]
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    width, separator, height = result.stdout.strip().partition(" ")
    if not separator or not width.isdigit() or not height.isdigit():
        return None
    return int(width), int(height)


def _base64_encoded_size(data: bytes) -> int:
    return ((len(data) + 2) // 3) * 4


def _base64_text(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        try:
            process.kill()
        except ProcessLookupError:
            return


def _write_temp_output(output: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="tau-bash-",
        suffix=".log",
        delete=False,
    ) as handle:
        handle.write(output)
        return handle.name


class _FileLockContext:
    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock: asyncio.Lock | None = None

    async def __aenter__(self) -> None:
        lock = _file_locks.setdefault(self._path, asyncio.Lock())
        self._lock = lock
        await lock.acquire()

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self._lock is not None:
            self._lock.release()


def _file_lock(path: Path) -> _FileLockContext:
    return _FileLockContext(path)
