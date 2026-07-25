import asyncio
import base64
import shutil
import subprocess
from pathlib import Path
from time import monotonic

import pytest

from tau_coding import (
    create_bash_tool,
    create_coding_tools,
    create_edit_tool,
    create_edit_tool_definition,
    create_ls_tool,
    create_read_tool,
    create_read_tool_definition,
    create_write_tool,
)


class FakeCancellationToken:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled


def _require_magick() -> str:
    magick = shutil.which("magick")
    if magick is None:
        pytest.skip("ImageMagick magick command is required for image resize proof")
    return magick


def _write_magick_image(path: Path, size: str) -> None:
    subprocess.run(
        [_require_magick(), "-size", size, "gradient:#ff0000-#0000ff", str(path)],
        check=True,
        capture_output=True,
    )


def _magick_dimensions(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [_require_magick(), "identify", "-format", "%w %h", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = result.stdout.split()
    return int(width), int(height)


@pytest.mark.anyio
async def test_create_coding_tools_returns_initial_tool_set(tmp_path: Path) -> None:
    tools = create_coding_tools(cwd=tmp_path)

    assert [tool.name for tool in tools] == ["read", "ls", "write", "edit", "bash"]
    edit_tool = next(tool for tool in tools if tool.name == "edit")
    assert edit_tool.prompt_snippet is not None
    assert "Use edit for precise changes" in edit_tool.prompt_guidelines[0]


@pytest.mark.anyio
async def test_ls_tool_lists_directory_entries_with_directory_suffix(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    tool = create_ls_tool(cwd=tmp_path)

    result = await tool.execute({"path": ".", "limit": 3})
    limited = await tool.execute({"path": ".", "limit": 2})

    assert result.ok is True
    assert result.name == "ls"
    assert result.content.splitlines() == [".env", "README.md", "src/"]
    assert result.data is not None
    assert result.data["entry_count"] == 3
    assert result.data["returned_entries"] == 3
    assert limited.content.splitlines() == [
        ".env",
        "README.md",
        "",
        "[2 entries limit reached. Use limit=4 for more]",
    ]
    assert limited.data is not None
    assert limited.data["entry_limit_reached"] == 2


def test_tool_definitions_expose_pi_style_prompt_metadata(tmp_path: Path) -> None:
    definition = create_edit_tool_definition(cwd=tmp_path)

    assert definition.prompt_snippet.startswith("Make precise file edits")
    assert len(definition.prompt_guidelines) == 4


def test_read_tool_schema_defines_line_controls_as_integers(tmp_path: Path) -> None:
    definition = create_read_tool_definition(cwd=tmp_path)
    properties = definition.input_schema["properties"]

    assert isinstance(properties, dict)
    assert properties["offset"]["type"] == "integer"
    assert properties["limit"]["type"] == "integer"


@pytest.mark.anyio
async def test_read_tool_reads_file_with_offset_and_limit(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute({"path": "notes.txt", "offset": 2, "limit": 1})

    assert result.ok is True
    assert result.name == "read"
    assert result.content == "two\n\n[2 more lines in file. Use offset=3 to continue.]"
    assert result.data is not None
    assert result.data["path"] == str(path)
    assert isinstance(result.data["truncation"], dict)


@pytest.mark.anyio
async def test_read_tool_treats_zero_offset_as_start_of_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute({"path": "notes.txt", "offset": 0, "limit": 1})

    assert result.ok is True
    assert result.content == "one\n\n[3 more lines in file. Use offset=2 to continue.]"


@pytest.mark.anyio
async def test_read_tool_auto_resizes_large_image_with_magick(tmp_path: Path) -> None:
    path = tmp_path / "large.png"
    _write_magick_image(path, "2400x1200")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute({"path": "large.png"})

    assert result.ok is True
    assert result.content == "Read image file [image/jpeg] (auto-resized from image/png)"
    assert result.data is not None
    assert result.data["mime_type"] == "image/jpeg"
    assert result.data["original_mime_type"] == "image/png"
    assert result.data["original_bytes"] == path.stat().st_size
    assert result.data["auto_resized"] is True
    image_base64 = result.data["image_base64"]
    assert isinstance(image_base64, str)
    resized_path = tmp_path / "resized.jpg"
    resized_path.write_bytes(base64.b64decode(image_base64))
    width, height = _magick_dimensions(resized_path)
    assert width <= 2000
    assert height <= 2000


@pytest.mark.anyio
async def test_read_tool_can_disable_auto_resize_for_large_images(tmp_path: Path) -> None:
    path = tmp_path / "large.png"
    _write_magick_image(path, "2400x1200")
    tool = create_read_tool(cwd=tmp_path, auto_resize_images=False)

    result = await tool.execute({"path": "large.png"})

    assert result.ok is True
    assert result.content == "Read image file [image/png]"
    assert result.data is not None
    assert result.data["mime_type"] == "image/png"
    assert result.data["bytes"] == path.stat().st_size
    assert result.data["auto_resized"] is False
    assert result.data["image_base64"] == base64.b64encode(path.read_bytes()).decode("ascii")


@pytest.mark.anyio
async def test_write_tool_creates_parent_directories(tmp_path: Path) -> None:
    tool = create_write_tool(cwd=tmp_path)

    result = await tool.execute({"path": "nested/file.txt", "content": "hello"})

    assert result.ok is True
    assert (tmp_path / "nested" / "file.txt").read_text() == "hello"


@pytest.mark.anyio
async def test_edit_tool_applies_multiple_exact_replacements(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("alpha\nbeta\ngamma\n")
    tool = create_edit_tool(cwd=tmp_path)

    result = await tool.execute(
        {
            "path": "file.txt",
            "edits": [
                {"oldText": "alpha", "newText": "one"},
                {"oldText": "gamma", "newText": "three"},
            ],
        }
    )

    assert result.ok is True
    assert path.read_text() == "one\nbeta\nthree\n"


@pytest.mark.anyio
async def test_edit_tool_rolls_back_when_any_edit_fails(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    original = "alpha\nbeta\ngamma\n"
    path.write_text(original)
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Could not find edits\\[1\\]"):
        await tool.execute(
            {
                "path": "file.txt",
                "edits": [
                    {"oldText": "alpha", "newText": "one"},
                    {"oldText": "missing", "newText": "nope"},
                ],
            }
        )

    assert path.read_text() == original


@pytest.mark.anyio
async def test_edit_tool_requires_unique_matches(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("repeat\nrepeat\n")
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Found 2 occurrences"):
        await tool.execute(
            {
                "path": "file.txt",
                "edits": [{"oldText": "repeat", "newText": "once"}],
            }
        )


@pytest.mark.anyio
async def test_bash_tool_captures_stdout_and_exit_code(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute({"command": "printf hello"})

    assert result.ok is True
    assert result.content == "hello"
    assert result.data is not None
    assert result.data["exit_code"] == 0
    assert result.data["timed_out"] is False


@pytest.mark.anyio
async def test_bash_tool_applies_environment_provider_and_unsets_stale_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAU_SESSION_ID", "stale-parent")
    tool = create_bash_tool(
        cwd=tmp_path,
        environment=lambda: {
            "TAU_SESSION_ID": None,
            "TAU_PROVIDER": "openai",
        },
    )

    result = await tool.execute(
        {"command": 'printf "%s/%s" "${TAU_SESSION_ID-unset}" "$TAU_PROVIDER"'}
    )

    assert result.ok is True
    assert result.content == "unset/openai"


@pytest.mark.anyio
async def test_bash_tool_emits_live_output_updates_before_process_exits(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    updates: list[tuple[str, dict[str, object] | None]] = []

    def on_update(message: str, data: object | None = None) -> None:
        updates.append((message, data if isinstance(data, dict) else None))

    task = asyncio.create_task(
        tool.execute(
            {"command": "printf 'one\\n'; sleep 0.2; printf 'two\\n'"},
            on_update=on_update,
        )
    )
    for _ in range(100):
        if updates:
            break
        await asyncio.sleep(0.01)

    assert updates
    assert task.done() is False

    result = await task

    assert result.ok is True
    assert result.content == "one\ntwo\n"
    assert "".join(message for message, _data in updates) == result.content
    assert updates[0][1] is not None
    assert updates[0][1]["tool"] == "bash"
    assert updates[0][1]["stream"] == "combined_output"


@pytest.mark.anyio
async def test_bash_tool_uses_custom_shell_path(tmp_path: Path) -> None:
    shell = tmp_path / "custom-shell"
    shell.write_text(
        "#!/bin/sh\n"
        "printf 'custom-shell:'\n"
        "exec /bin/sh \"$@\"\n",
        encoding="utf-8",
    )
    shell.chmod(0o755)
    tool = create_bash_tool(cwd=tmp_path, shell_path=shell)

    result = await tool.execute({"command": "printf hello"})

    assert result.ok is True
    assert result.content == "custom-shell:hello"


@pytest.mark.anyio
async def test_create_coding_tools_passes_custom_shell_path_to_bash(tmp_path: Path) -> None:
    shell = tmp_path / "custom-shell"
    shell.write_text(
        "#!/bin/sh\n"
        "printf 'tool-shell:'\n"
        "exec /bin/sh \"$@\"\n",
        encoding="utf-8",
    )
    shell.chmod(0o755)
    tools = create_coding_tools(cwd=tmp_path, shell_path=shell)
    bash = tools[-1]

    result = await bash.execute({"command": "printf hello"})

    assert result.ok is True
    assert result.content == "tool-shell:hello"


@pytest.mark.anyio
async def test_bash_tool_reports_timeout(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute({"command": "sleep 1", "timeout": 0.01})

    assert result.ok is False
    assert result.data is not None
    assert result.data["timed_out"] is True
    assert "timed out" in result.content


@pytest.mark.anyio
async def test_bash_tool_timeout_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    marker = tmp_path / "marker"

    start = monotonic()
    result = await tool.execute(
        {"command": "(sleep 0.25; touch marker) & wait", "timeout": 0.01}
    )
    duration = monotonic() - start
    await asyncio.sleep(0.35)

    assert result.ok is False
    assert result.data is not None
    assert result.data["timed_out"] is True
    assert duration < 0.5
    assert not marker.exists()


@pytest.mark.anyio
async def test_bash_tool_cancellation_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    token = FakeCancellationToken()

    task = asyncio.create_task(tool.execute({"command": "sleep 1 & wait"}, signal=token))
    await asyncio.sleep(0.05)
    token.cancel()
    start = monotonic()
    result = await task
    duration = monotonic() - start

    assert result.ok is False
    assert result.data is not None
    assert result.data["cancelled"] is True
    assert "cancelled" in result.content
    assert duration < 0.5
