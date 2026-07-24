import base64

from tau_coding.tui.terminal_image import (
    CellDimensions,
    ImageDimensions,
    TerminalCapabilities,
    TerminalImage,
    TerminalImageOptions,
    calculate_image_cell_size,
    delete_all_kitty_images,
    delete_kitty_image,
    detect_capabilities,
    encode_iterm2,
    encode_kitty,
    get_image_dimensions,
    hyperlink,
    image_fallback,
    is_image_line,
    render_image,
    reset_capabilities_cache,
    set_capabilities,
    set_cell_dimensions,
)


def test_is_image_line_detects_kitty_and_iterm_sequences_anywhere() -> None:
    assert is_image_line("\x1b]1337;File=inline=1:base64==\x07") is True
    assert is_image_line("prefix \x1b]1337;File=inline=1:base64==\x07 suffix") is True
    assert is_image_line("\x1b_Ga=T,f=100;data\x1b\\") is True
    assert is_image_line("prefix \x1b_Ga=T,f=100;data\x1b\\ suffix") is True
    assert is_image_line("\x1b[31mred text\x1b[0m") is False
    assert is_image_line("/path/to/File_1337_backup/image.jpg") is False


def test_detect_capabilities_matches_pi_terminal_rules() -> None:
    assert detect_capabilities(env={}) == TerminalCapabilities(
        images=None,
        true_color=False,
        hyperlinks=False,
    )
    assert detect_capabilities(env={"COLORTERM": "truecolor"}) == TerminalCapabilities(
        images=None,
        true_color=True,
        hyperlinks=False,
    )
    assert detect_capabilities(env={"KITTY_WINDOW_ID": "1"}) == TerminalCapabilities(
        images="kitty",
        true_color=True,
        hyperlinks=True,
    )
    assert detect_capabilities(env={"TERM_PROGRAM": "iterm.app"}) == TerminalCapabilities(
        images="iterm2",
        true_color=True,
        hyperlinks=True,
    )
    assert detect_capabilities(env={"TERM": "screen-256color"}) == TerminalCapabilities(
        images=None,
        true_color=False,
        hyperlinks=False,
    )


def test_detect_capabilities_disables_images_inside_tmux() -> None:
    env = {
        "TERM_PROGRAM": "WarpTerminal",
        "TMUX": "/tmp/tmux-1000/default,1234,0",
        "TERM": "tmux-256color",
    }

    capabilities = detect_capabilities(env=env, tmux_forwards_hyperlink=lambda: True)
    assert capabilities == TerminalCapabilities(
        images=None,
        true_color=False,
        hyperlinks=True,
    )
    assert detect_capabilities(
        env={**env, "COLORTERM": "24bit"},
        tmux_forwards_hyperlink=lambda: False,
    ) == TerminalCapabilities(images=None, true_color=True, hyperlinks=False)


def test_encode_kitty_supports_no_cursor_movement_chunking_and_deletion() -> None:
    sequence = encode_kitty("AAAA", columns=2, rows=2, move_cursor=False)

    assert sequence == "\x1b_Ga=T,f=100,q=2,C=1,c=2,r=2;AAAA\x1b\\"
    assert delete_kitty_image(42) == "\x1b_Ga=d,d=I,i=42,q=2\x1b\\"
    assert delete_all_kitty_images() == "\x1b_Ga=d,d=A,q=2\x1b\\"

    chunked = encode_kitty("A" * 4100)
    assert chunked.startswith("\x1b_Ga=T,f=100,q=2,m=1;")
    assert "\x1b_Gm=0;" in chunked


def test_encode_iterm2_supports_dimensions_name_and_aspect_ratio() -> None:
    sequence = encode_iterm2(
        "AAAA",
        width=10,
        height="auto",
        name="image.png",
        preserve_aspect_ratio=False,
    )

    assert sequence.startswith("\x1b]1337;File=inline=1;width=10;height=auto;")
    assert "name=aW1hZ2UucG5n" in sequence
    assert "preserveAspectRatio=0" in sequence
    assert sequence.endswith(":AAAA\x07")


def test_calculate_image_cell_size_preserves_aspect_and_caps_height() -> None:
    size = calculate_image_cell_size(
        ImageDimensions(width_px=10, height_px=100),
        max_width_cells=10,
        max_height_cells=5,
        cell_dimensions=CellDimensions(width_px=10, height_px=10),
    )

    assert size.columns == 1
    assert size.rows == 5


def test_get_image_dimensions_reads_png_gif_webp_headers() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (3).to_bytes(4, "big") + (5).to_bytes(4, "big")
    gif = b"GIF89a" + (7).to_bytes(2, "little") + (9).to_bytes(2, "little")
    webp_vp8x = (
        b"RIFF"
        + b"\0\0\0\0"
        + b"WEBP"
        + b"VP8X"
        + b"\0" * 8
        + (10).to_bytes(3, "little")
        + (12).to_bytes(3, "little")
    )

    assert get_image_dimensions(_b64(png), "image/png") == ImageDimensions(3, 5)
    assert get_image_dimensions(_b64(gif), "image/gif") == ImageDimensions(7, 9)
    assert get_image_dimensions(_b64(webp_vp8x), "image/webp") == ImageDimensions(11, 13)
    assert get_image_dimensions("not base64", "image/png") is None


def test_render_image_uses_configured_protocol_and_cell_size() -> None:
    set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
    set_cell_dimensions(CellDimensions(width_px=10, height_px=10))
    try:
        result = render_image(
            "AAAA",
            ImageDimensions(width_px=20, height_px=20),
            max_width_cells=2,
            move_cursor=False,
        )
        assert result is not None
        assert result.rows == 2
        assert result.sequence.startswith("\x1b_Ga=T,f=100,q=2,C=1,c=2,r=2;")

        set_capabilities(TerminalCapabilities(images="iterm2", true_color=True, hyperlinks=True))
        iterm = render_image("AAAA", ImageDimensions(width_px=20, height_px=20), max_width_cells=2)
        assert iterm is not None
        assert iterm.rows == 2
        assert iterm.sequence == "\x1b]1337;File=inline=1;width=2;height=auto:AAAA\x07"

        set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=True))
        assert render_image("AAAA", ImageDimensions(width_px=20, height_px=20)) is None
    finally:
        reset_capabilities_cache()
        set_cell_dimensions(CellDimensions(width_px=9, height_px=18))


def test_terminal_image_falls_back_when_terminal_has_no_image_protocol() -> None:
    set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=True))
    try:
        image = TerminalImage(
            "AAAA",
            "image/png",
            TerminalImageOptions(filename="figure.png"),
            ImageDimensions(width_px=20, height_px=10),
        )

        assert image.render(80) == ("[Image: figure.png [image/png] 20x10]",)
        assert image.get_image_id() is None
    finally:
        reset_capabilities_cache()


def test_terminal_image_places_kitty_sequence_on_first_line_with_padding_rows() -> None:
    set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
    set_cell_dimensions(CellDimensions(width_px=10, height_px=10))
    try:
        image = TerminalImage(
            "AAAA",
            "image/png",
            TerminalImageOptions(max_width_cells=2),
            ImageDimensions(width_px=20, height_px=20),
        )

        lines = image.render(4)
        image_id = image.get_image_id()

        assert isinstance(image_id, int)
        assert len(lines) == 2
        assert lines[0].startswith("\x1b_G")
        assert ",C=1," in lines[0]
        assert f",i={image_id}" in lines[0]
        assert lines[1] == ""
        assert image.render(4) is lines
        image.invalidate()
        assert image.render(4) is not lines
    finally:
        reset_capabilities_cache()
        set_cell_dimensions(CellDimensions(width_px=9, height_px=18))


def test_terminal_image_places_iterm2_sequence_on_last_reserved_line() -> None:
    set_capabilities(TerminalCapabilities(images="iterm2", true_color=True, hyperlinks=True))
    set_cell_dimensions(CellDimensions(width_px=10, height_px=10))
    try:
        image = TerminalImage(
            "AAAA",
            "image/png",
            TerminalImageOptions(max_width_cells=2),
            ImageDimensions(width_px=20, height_px=20),
        )

        lines = image.render(4)

        assert lines[0] == ""
        assert lines[1].startswith("\x1b[1A\x1b]1337;File=inline=1;width=2;height=auto:")
    finally:
        reset_capabilities_cache()
        set_cell_dimensions(CellDimensions(width_px=9, height_px=18))


def test_hyperlink_and_image_fallback_are_visible_text_contracts() -> None:
    assert hyperlink("README.md", "file:///tmp/README.md") == (
        "\x1b]8;;file:///tmp/README.md\x1b\\README.md\x1b]8;;\x1b\\"
    )
    assert image_fallback(
        "image/png",
        ImageDimensions(width_px=8, height_px=6),
        "figure.png",
    ) == "[Image: figure.png [image/png] 8x6]"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
