"""Render graph source blocks into local visual artifacts for Tau transcripts.

The helpers in this module are deliberately fail-closed: they render bounded
DOT/Graphviz fenced Markdown blocks with Graphviz, and they attempt Mermaid only
when the local Mermaid CLI and its browser runtime can launch normally. Missing
tools, invalid graph source, oversized input, and renderer errors produce no
artifact instead of optimistic UI output.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

GraphKind = Literal["graphviz", "mermaid"]

GRAPH_FENCE_PATTERN = re.compile(
    r"```[ \t]*(?P<language>dot|graphviz|mermaid|mmd)(?:[ \t][^\n`]*)?\n"
    r"(?P<source>.*?)(?:\n```|$)",
    re.IGNORECASE | re.DOTALL,
)
GRAPH_ARTIFACT_MIME_TYPE = "image/svg+xml"
MAX_GRAPH_SOURCE_BYTES = 128 * 1024
MAX_GRAPH_ARTIFACT_BYTES = 5 * 1024 * 1024
GRAPH_RENDER_TIMEOUT_SECONDS = 8


@dataclass(frozen=True, slots=True)
class RenderedGraphArtifact:
    """One rendered graph artifact extracted from Markdown."""

    index: int
    kind: GraphKind
    filename: str
    mime_type: str
    bytes: int
    image_base64: str
    renderer: str


def render_markdown_graph_artifacts(
    markdown: str,
    *,
    max_graphs: int = 3,
) -> tuple[RenderedGraphArtifact, ...]:
    """Return SVG artifacts for fenced DOT/Graphviz and Mermaid blocks."""
    if not markdown or max_graphs <= 0:
        return ()
    artifacts: list[RenderedGraphArtifact] = []
    seen: set[tuple[GraphKind, str]] = set()
    for match in GRAPH_FENCE_PATTERN.finditer(markdown):
        language = match.group("language")
        source = match.group("source").strip()
        kind = _graph_kind(language)
        if kind is None or not source:
            continue
        key = (kind, source)
        if key in seen:
            continue
        seen.add(key)
        rendered = _render_graph_source(kind, source)
        if rendered is None:
            continue
        renderer, svg = rendered
        artifacts.append(
            RenderedGraphArtifact(
                index=len(artifacts) + 1,
                kind=kind,
                filename=_graph_filename(kind, source, len(artifacts) + 1),
                mime_type=GRAPH_ARTIFACT_MIME_TYPE,
                bytes=len(svg),
                image_base64=base64.b64encode(svg).decode("ascii"),
                renderer=renderer,
            )
        )
        if len(artifacts) >= max_graphs:
            break
    return tuple(artifacts)


def _graph_kind(language: str) -> GraphKind | None:
    normalized = language.strip().lower()
    if normalized in {"dot", "graphviz"}:
        return "graphviz"
    if normalized in {"mermaid", "mmd"}:
        return "mermaid"
    return None


def _graph_filename(kind: GraphKind, source: str, index: int) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    return f"{kind}-{index}-{digest}.svg"


@lru_cache(maxsize=128)
def _render_graph_source(kind: GraphKind, source: str) -> tuple[str, bytes] | None:
    source_bytes = source.encode("utf-8")
    if len(source_bytes) > MAX_GRAPH_SOURCE_BYTES:
        return None
    if kind == "graphviz":
        return _render_graphviz(source_bytes)
    return _render_mermaid(source)


def _render_graphviz(source_bytes: bytes) -> tuple[str, bytes] | None:
    dot = shutil.which("dot")
    if dot is None:
        return None
    try:
        result = subprocess.run(
            [dot, "-Tsvg"],
            input=source_bytes,
            capture_output=True,
            check=False,
            timeout=GRAPH_RENDER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _accepted_svg("dot", result.stdout)


def _render_mermaid(source: str) -> tuple[str, bytes] | None:
    mmdc = shutil.which("mmdc")
    if mmdc is None:
        return None
    input_path: Path | None = None
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mmd",
            encoding="utf-8",
            delete=False,
        ) as input_file:
            input_file.write(source)
            input_path = Path(input_file.name)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as output_file:
            output_path = Path(output_file.name)
        result = subprocess.run(
            [mmdc, "-i", str(input_path), "-o", str(output_path), "-b", "transparent"],
            capture_output=True,
            check=False,
            timeout=GRAPH_RENDER_TIMEOUT_SECONDS,
        )
        if result.returncode != 0 or not output_path.exists():
            return None
        return _accepted_svg("mmdc", output_path.read_bytes())
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for path in (input_path, output_path):
            if path is not None:
                with contextlib.suppress(OSError):
                    path.unlink()


def _accepted_svg(renderer: str, svg: bytes) -> tuple[str, bytes] | None:
    if not svg or len(svg) > MAX_GRAPH_ARTIFACT_BYTES:
        return None
    prefix = svg[:2048].lower()
    if b"<svg" not in prefix:
        return None
    return renderer, svg
