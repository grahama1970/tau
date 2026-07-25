import base64
import shutil
from pathlib import Path

import pytest

from tau_agent import (
    AssistantMessage,
    CompactionEntry,
    LeafEntry,
    MessageEntry,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_coding.session_export import export_session_html, render_session_html

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_render_session_html_preserves_branch_tree() -> None:
    entries = [
        MessageEntry(id="root", message=UserMessage(content="Start <session>")),
        MessageEntry(
            id="left",
            parent_id="root",
            message=AssistantMessage(content="Left branch"),
        ),
        MessageEntry(
            id="right",
            parent_id="root",
            message=AssistantMessage(
                content="Right branch",
                tool_calls=[ToolCall(id="call-1", name="read", arguments={"path": "README.md"})],
            ),
        ),
        MessageEntry(
            id="tool",
            parent_id="right",
            message=ToolResultMessage(
                tool_call_id="call-1",
                name="read",
                content="File contents",
                ok=True,
                data={"bytes": 13},
            ),
        ),
        CompactionEntry(
            id="compact",
            parent_id="tool",
            summary="The right branch was compacted.",
            replaces_entry_ids=["root", "right", "tool"],
        ),
        LeafEntry(id="leaf", parent_id="compact", entry_id="compact"),
    ]

    html = render_session_html(entries, title="Test Export", source="/tmp/session.jsonl")

    assert "<title>Test Export</title>" in html
    assert "Source: <code>/tmp/session.jsonl</code>" in html
    assert 'id="entry-root"' in html
    assert 'id="entry-left"' in html
    assert 'id="entry-right"' in html
    assert 'id="entry-compact"' in html
    assert "Start &lt;session&gt;" in html
    assert "Right branch [read]" in html
    assert "active-path" in html
    assert "active-leaf" in html
    assert "Replaces entries" in html


def test_export_session_html_writes_file(tmp_path: Path) -> None:
    entries = [MessageEntry(id="root", message=UserMessage(content="Hello"))]
    output_path = tmp_path / "session.html"

    result = export_session_html(entries, output_path, title="Session")

    assert result == output_path
    assert output_path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_render_session_html_renders_assistant_markdown_and_local_images(tmp_path: Path) -> None:
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(base64.b64decode(PNG_1X1_BASE64))
    entries = [
        MessageEntry(
            id="root",
            message=AssistantMessage(
                content=(
                    "# Figure\n\n"
                    "| Name | Value |\n"
                    "| --- | --- |\n"
                    "| chart | ready |\n\n"
                    f"![chart]({image_path})\n\n"
                    "<script>alert('no')</script>"
                )
            ),
        )
    ]

    exported = render_session_html(entries, title="Session")

    assert '<div class="markdown-rendered">' in exported
    assert '<table class="markdown-table">' in exported
    assert "<th>Name</th>" in exported
    assert '<div class="export-images">' in exported
    assert 'src="data:image/png;base64,' in exported
    assert 'href="data:image/png;base64,' in exported
    assert 'target="_blank"' in exported
    assert "open full-size" in exported
    assert str(image_path) in exported
    assert "<script>alert" not in exported
    assert "&lt;script&gt;alert('no')&lt;/script&gt;" in exported
    assert "<summary>Raw Markdown</summary>" in exported


@pytest.mark.skipif(shutil.which("dot") is None, reason="Graphviz dot is not installed")
def test_render_session_html_embeds_graphviz_fence_as_openable_graph() -> None:
    entries = [
        MessageEntry(
            id="root",
            message=AssistantMessage(
                content="# Graph\n\n```dot\ndigraph G { human -> tau -> evidence }\n```"
            ),
        )
    ]

    exported = render_session_html(entries, title="Session")

    assert '<div class="export-graphs">' in exported
    assert 'src="data:image/svg+xml;base64,' in exported
    assert 'href="data:image/svg+xml;base64,' in exported
    assert "graphviz graph 1" in exported
    assert "rendered by dot" in exported
    assert "open full-size" in exported
