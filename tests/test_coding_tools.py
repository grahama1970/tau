from pathlib import Path

import pytest

from tau_coding import (
    create_bash_tool,
    create_coding_tools,
    create_edit_tool,
    create_edit_tool_definition,
    create_read_tool,
    create_write_tool,
)


@pytest.mark.anyio
async def test_create_coding_tools_returns_initial_tool_set(tmp_path: Path) -> None:
    tools = create_coding_tools(cwd=tmp_path)

    assert [tool.name for tool in tools] == ["read", "write", "edit", "bash"]
    edit_tool = tools[2]
    assert edit_tool.prompt_snippet is not None
    assert "Use edit for precise changes" in edit_tool.prompt_guidelines[0]


def test_tool_definitions_expose_pi_style_prompt_metadata(tmp_path: Path) -> None:
    definition = create_edit_tool_definition(cwd=tmp_path)

    assert definition.prompt_snippet.startswith("Make precise file edits")
    assert len(definition.prompt_guidelines) == 4


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
async def test_bash_tool_reports_timeout(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute({"command": "sleep 1", "timeout": 0.01})

    assert result.ok is False
    assert result.data is not None
    assert result.data["timed_out"] is True
    assert "timed out" in result.content
