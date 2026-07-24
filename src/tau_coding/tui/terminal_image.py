"""Terminal graphics helpers for Pi-style inline image support."""

import base64
import os
import random
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

ImageProtocol = Literal["kitty", "iterm2"] | None

KITTY_PREFIX = "\x1b_G"
ITERM2_PREFIX = "\x1b]1337;File="
DEFAULT_CELL_DIMENSIONS = None
KITTY_CHUNK_SIZE = 4096


@dataclass(frozen=True, slots=True)
class TerminalCapabilities:
    """Terminal rendering capabilities relevant to Tau's TUI."""

    images: ImageProtocol
    true_color: bool
    hyperlinks: bool


@dataclass(frozen=True, slots=True)
class CellDimensions:
    """Pixel dimensions of one terminal cell."""

    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class ImageDimensions:
    """Pixel dimensions of an image."""

    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class ImageCellSize:
    """Terminal cell dimensions used to place an image."""

    columns: int
    rows: int


@dataclass(frozen=True, slots=True)
class ImageRenderResult:
    """Encoded terminal image sequence and occupied row count."""

    sequence: str
    rows: int
    image_id: int | None = None


@dataclass(frozen=True, slots=True)
class TerminalImageOptions:
    """Sizing and placement options for terminal image rendering."""

    max_width_cells: int | None = None
    max_height_cells: int | None = None
    filename: str | None = None
    image_id: int | None = None


class TerminalImage:
    """Pi-style terminal image render helper with a visible fallback string."""

    def __init__(
        self,
        base64_data: str,
        mime_type: str,
        options: TerminalImageOptions | None = None,
        dimensions: ImageDimensions | None = None,
    ) -> None:
        self.base64_data = base64_data
        self.mime_type = mime_type
        self.options = options or TerminalImageOptions()
        self.dimensions = (
            dimensions
            or get_image_dimensions(base64_data, mime_type)
            or ImageDimensions(width_px=800, height_px=600)
        )
        self.image_id = self.options.image_id
        self._cached_width: int | None = None
        self._cached_lines: tuple[str, ...] | None = None

    def invalidate(self) -> None:
        """Clear cached render lines."""
        self._cached_width = None
        self._cached_lines = None

    def render(self, width: int) -> tuple[str, ...]:
        """Return terminal lines for this image at the provided render width."""
        if self._cached_width == width and self._cached_lines is not None:
            return self._cached_lines

        max_width = max(1, min(width - 2, self.options.max_width_cells or 60))
        cell_dimensions = get_cell_dimensions()
        default_max_height = max(
            1,
            _ceil_div_scaled(max_width * cell_dimensions.width_px, cell_dimensions.height_px),
        )
        max_height = self.options.max_height_cells or default_max_height
        capabilities = get_capabilities()

        if capabilities.images is None:
            lines = (
                image_fallback(self.mime_type, self.dimensions, self.options.filename),
            )
            self._cached_width = width
            self._cached_lines = lines
            return lines

        image_id = self.image_id
        if capabilities.images == "kitty" and image_id is None:
            image_id = allocate_image_id()
            self.image_id = image_id

        result = render_image(
            self.base64_data,
            self.dimensions,
            max_width_cells=max_width,
            max_height_cells=max_height,
            image_id=image_id,
            move_cursor=False,
        )
        if result is None:
            lines = (
                image_fallback(self.mime_type, self.dimensions, self.options.filename),
            )
        elif capabilities.images == "kitty":
            lines = (result.sequence, *("" for _ in range(result.rows - 1)))
        else:
            empty_rows = tuple("" for _ in range(max(0, result.rows - 1)))
            row_offset = result.rows - 1
            move_up = f"\x1b[{row_offset}A" if row_offset > 0 else ""
            lines = (*empty_rows, f"{move_up}{result.sequence}")

        self._cached_width = width
        self._cached_lines = lines
        return lines

    def get_image_id(self) -> int | None:
        """Return the Kitty image id allocated for this image, if any."""
        return self.image_id


_cached_capabilities: TerminalCapabilities | None = None
_cell_dimensions = CellDimensions(width_px=9, height_px=18)


def get_cell_dimensions() -> CellDimensions:
    """Return the current terminal cell dimensions."""
    return _cell_dimensions


def set_cell_dimensions(dimensions: CellDimensions) -> None:
    """Override terminal cell dimensions, mainly for tests or terminal probes."""
    global _cell_dimensions
    _cell_dimensions = dimensions


def probe_tmux_hyperlinks() -> bool:
    """Return whether the attached tmux client advertises OSC 8 hyperlink forwarding."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{client_termfeatures}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=0.25,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "hyperlinks" in {part.strip() for part in result.stdout.split(",")}


def detect_capabilities(
    *,
    env: Mapping[str, str] | None = None,
    tmux_forwards_hyperlink: Callable[[], bool] = probe_tmux_hyperlinks,
) -> TerminalCapabilities:
    """Detect conservative terminal image, true-color, and hyperlink capabilities."""
    values = env if env is not None else os.environ
    term_program = values.get("TERM_PROGRAM", "").lower()
    terminal_emulator = values.get("TERMINAL_EMULATOR", "").lower()
    term = values.get("TERM", "").lower()
    color_term = values.get("COLORTERM", "").lower()
    has_true_color_hint = color_term in {"truecolor", "24bit"}

    if values.get("TMUX") or term.startswith("tmux"):
        return TerminalCapabilities(
            images=None,
            true_color=has_true_color_hint,
            hyperlinks=tmux_forwards_hyperlink(),
        )

    if term.startswith("screen"):
        return TerminalCapabilities(images=None, true_color=has_true_color_hint, hyperlinks=False)

    if values.get("KITTY_WINDOW_ID") or term_program == "kitty":
        return TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True)

    if term_program == "ghostty" or "ghostty" in term or values.get("GHOSTTY_RESOURCES_DIR"):
        return TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True)

    if values.get("WEZTERM_PANE") or term_program == "wezterm":
        return TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True)

    if (
        term_program == "warpterminal"
        or values.get("WARP_SESSION_ID")
        or values.get("WARP_TERMINAL_SESSION_UUID")
    ):
        return TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True)

    if values.get("ITERM_SESSION_ID") or term_program == "iterm.app":
        return TerminalCapabilities(images="iterm2", true_color=True, hyperlinks=True)

    if values.get("WT_SESSION"):
        return TerminalCapabilities(images=None, true_color=True, hyperlinks=True)

    if term_program in {"vscode", "alacritty"}:
        return TerminalCapabilities(images=None, true_color=True, hyperlinks=True)

    if terminal_emulator == "jetbrains-jediterm":
        return TerminalCapabilities(images=None, true_color=True, hyperlinks=False)

    return TerminalCapabilities(images=None, true_color=has_true_color_hint, hyperlinks=False)


def get_capabilities() -> TerminalCapabilities:
    """Return cached terminal capabilities."""
    global _cached_capabilities
    if _cached_capabilities is None:
        _cached_capabilities = detect_capabilities()
    return _cached_capabilities


def set_capabilities(capabilities: TerminalCapabilities) -> None:
    """Override cached terminal capabilities."""
    global _cached_capabilities
    _cached_capabilities = capabilities


def reset_capabilities_cache() -> None:
    """Clear cached terminal capabilities."""
    global _cached_capabilities
    _cached_capabilities = None


def is_image_line(line: str) -> bool:
    """Return whether a terminal output line contains a known image escape sequence."""
    return (
        line.startswith((KITTY_PREFIX, ITERM2_PREFIX))
        or KITTY_PREFIX in line
        or ITERM2_PREFIX in line
    )


def allocate_image_id() -> int:
    """Return a Kitty image id with the same range as Pi's TUI."""
    return random.randint(1, 0xFFFFFFFF)


def encode_kitty(
    base64_data: str,
    *,
    columns: int | None = None,
    rows: int | None = None,
    image_id: int | None = None,
    move_cursor: bool = True,
) -> str:
    """Encode base64 image data using Kitty's graphics protocol."""
    params = ["a=T", "f=100", "q=2"]
    if not move_cursor:
        params.append("C=1")
    if columns is not None:
        params.append(f"c={columns}")
    if rows is not None:
        params.append(f"r={rows}")
    if image_id is not None:
        params.append(f"i={image_id}")

    if len(base64_data) <= KITTY_CHUNK_SIZE:
        return f"\x1b_G{','.join(params)};{base64_data}\x1b\\"

    chunks: list[str] = []
    offset = 0
    is_first = True
    while offset < len(base64_data):
        chunk = base64_data[offset : offset + KITTY_CHUNK_SIZE]
        is_last = offset + KITTY_CHUNK_SIZE >= len(base64_data)
        if is_first:
            chunks.append(f"\x1b_G{','.join(params)},m=1;{chunk}\x1b\\")
            is_first = False
        elif is_last:
            chunks.append(f"\x1b_Gm=0;{chunk}\x1b\\")
        else:
            chunks.append(f"\x1b_Gm=1;{chunk}\x1b\\")
        offset += KITTY_CHUNK_SIZE
    return "".join(chunks)


def delete_kitty_image(image_id: int) -> str:
    """Return a Kitty sequence that deletes one image and suppresses replies."""
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


def delete_all_kitty_images() -> str:
    """Return a Kitty sequence that deletes all visible images and suppresses replies."""
    return "\x1b_Ga=d,d=A,q=2\x1b\\"


def encode_iterm2(
    base64_data: str,
    *,
    width: int | str | None = None,
    height: int | str | None = None,
    name: str | None = None,
    preserve_aspect_ratio: bool = True,
    inline: bool = True,
) -> str:
    """Encode base64 image data using iTerm2's inline image protocol."""
    params = [f"inline={1 if inline else 0}"]
    if width is not None:
        params.append(f"width={width}")
    if height is not None:
        params.append(f"height={height}")
    if name:
        params.append(f"name={base64.b64encode(name.encode()).decode('ascii')}")
    if not preserve_aspect_ratio:
        params.append("preserveAspectRatio=0")
    return f"\x1b]1337;File={';'.join(params)}:{base64_data}\x07"


def calculate_image_cell_size(
    image_dimensions: ImageDimensions,
    max_width_cells: int,
    max_height_cells: int | None = None,
    cell_dimensions: CellDimensions = _cell_dimensions,
) -> ImageCellSize:
    """Scale image pixels into bounded terminal cell dimensions."""
    max_width = max(1, int(max_width_cells))
    max_height = None if max_height_cells is None else max(1, int(max_height_cells))
    image_width = max(1, image_dimensions.width_px)
    image_height = max(1, image_dimensions.height_px)

    width_scale = (max_width * cell_dimensions.width_px) / image_width
    height_scale = (
        width_scale
        if max_height is None
        else (max_height * cell_dimensions.height_px) / image_height
    )
    scale = min(width_scale, height_scale)
    scaled_width_px = image_width * scale
    scaled_height_px = image_height * scale
    columns = _ceil_div_scaled(scaled_width_px, cell_dimensions.width_px)
    rows = _ceil_div_scaled(scaled_height_px, cell_dimensions.height_px)
    return ImageCellSize(
        columns=max(1, min(max_width, columns)),
        rows=max(1, rows if max_height is None else min(max_height, rows)),
    )


def calculate_image_rows(
    image_dimensions: ImageDimensions,
    target_width_cells: int,
    cell_dimensions: CellDimensions = _cell_dimensions,
) -> int:
    """Return image row count for a target width."""
    return calculate_image_cell_size(
        image_dimensions,
        target_width_cells,
        cell_dimensions=cell_dimensions,
    ).rows


def get_image_dimensions(base64_data: str, mime_type: str) -> ImageDimensions | None:
    """Read dimensions from supported image headers."""
    try:
        data = base64.b64decode(base64_data, validate=False)
    except ValueError:
        return None
    if mime_type == "image/png":
        return _png_dimensions(data)
    if mime_type == "image/jpeg":
        return _jpeg_dimensions(data)
    if mime_type == "image/gif":
        return _gif_dimensions(data)
    if mime_type == "image/webp":
        return _webp_dimensions(data)
    return None


def render_image(
    base64_data: str,
    image_dimensions: ImageDimensions,
    *,
    max_width_cells: int = 80,
    max_height_cells: int | None = None,
    preserve_aspect_ratio: bool = True,
    image_id: int | None = None,
    move_cursor: bool = True,
) -> ImageRenderResult | None:
    """Render image data for the detected terminal protocol."""
    capabilities = get_capabilities()
    if capabilities.images is None:
        return None

    size = calculate_image_cell_size(
        image_dimensions,
        max_width_cells,
        max_height_cells,
        get_cell_dimensions(),
    )
    if capabilities.images == "kitty":
        sequence = encode_kitty(
            base64_data,
            columns=size.columns,
            rows=size.rows,
            image_id=image_id,
            move_cursor=move_cursor,
        )
        return ImageRenderResult(sequence=sequence, rows=size.rows, image_id=image_id)

    if capabilities.images == "iterm2":
        sequence = encode_iterm2(
            base64_data,
            width=size.columns,
            height="auto",
            preserve_aspect_ratio=preserve_aspect_ratio,
        )
        return ImageRenderResult(sequence=sequence, rows=size.rows)

    return None


def hyperlink(text: str, url: str) -> str:
    """Wrap text in an OSC 8 hyperlink sequence."""
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def image_fallback(
    mime_type: str,
    dimensions: ImageDimensions | None = None,
    filename: str | None = None,
) -> str:
    """Return a visible fallback string for unsupported terminal image rendering."""
    parts: list[str] = []
    if filename:
        parts.append(filename)
    parts.append(f"[{mime_type}]")
    if dimensions is not None:
        parts.append(f"{dimensions.width_px}x{dimensions.height_px}")
    return f"[Image: {' '.join(parts)}]"


def _ceil_div_scaled(value: float, divisor: int) -> int:
    return int(-(-value // divisor))


def _png_dimensions(data: bytes) -> ImageDimensions | None:
    if len(data) < 24 or data[:4] != b"\x89PNG":
        return None
    return ImageDimensions(
        width_px=int.from_bytes(data[16:20], "big"),
        height_px=int.from_bytes(data[20:24], "big"),
    )


def _jpeg_dimensions(data: bytes) -> ImageDimensions | None:
    if len(data) < 2 or data[:2] != b"\xff\xd8":
        return None
    offset = 2
    while offset < len(data) - 9:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if 0xC0 <= marker <= 0xC2:
            return ImageDimensions(
                width_px=int.from_bytes(data[offset + 7 : offset + 9], "big"),
                height_px=int.from_bytes(data[offset + 5 : offset + 7], "big"),
            )
        if offset + 3 >= len(data):
            return None
        length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if length < 2:
            return None
        offset += 2 + length
    return None


def _gif_dimensions(data: bytes) -> ImageDimensions | None:
    if len(data) < 10 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        return None
    return ImageDimensions(
        width_px=int.from_bytes(data[6:8], "little"),
        height_px=int.from_bytes(data[8:10], "little"),
    )


def _webp_dimensions(data: bytes) -> ImageDimensions | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":
        return ImageDimensions(
            width_px=int.from_bytes(data[26:28], "little") & 0x3FFF,
            height_px=int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L":
        if len(data) < 25:
            return None
        bits = int.from_bytes(data[21:25], "little")
        return ImageDimensions(width_px=(bits & 0x3FFF) + 1, height_px=((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8X":
        return ImageDimensions(
            width_px=int.from_bytes(data[24:27] + b"\0", "little") + 1,
            height_px=int.from_bytes(data[27:30] + b"\0", "little") + 1,
        )
    return None
