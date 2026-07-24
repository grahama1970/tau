from pathlib import Path

from tau_coding.tui.file_drop import normalize_dropped_paths


def test_normalize_dropped_paths_accepts_existing_absolute_path(tmp_path: Path) -> None:
    dropped = tmp_path / "notes.txt"
    dropped.write_text("hello", encoding="utf-8")

    assert normalize_dropped_paths(str(dropped)) == str(dropped)


def test_normalize_dropped_paths_quotes_paths_with_spaces(tmp_path: Path) -> None:
    dropped = tmp_path / "notes with spaces.txt"
    dropped.write_text("hello", encoding="utf-8")

    assert normalize_dropped_paths(str(dropped)) == f'"{dropped}"'


def test_normalize_dropped_paths_accepts_shell_escaped_multiple_paths(tmp_path: Path) -> None:
    first = tmp_path / "first file.txt"
    second = tmp_path / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    assert normalize_dropped_paths(f"{first!s}".replace(" ", "\\ ") + f" {second}") == (
        f'"{first}" {second}'
    )


def test_normalize_dropped_paths_accepts_local_file_uri(tmp_path: Path) -> None:
    dropped = tmp_path / "uri file.txt"
    dropped.write_text("hello", encoding="utf-8")

    assert normalize_dropped_paths(f"file://{str(dropped).replace(' ', '%20')}") == f'"{dropped}"'


def test_normalize_dropped_paths_rejects_non_drop_text(tmp_path: Path) -> None:
    dropped = tmp_path / "notes.txt"
    dropped.write_text("hello", encoding="utf-8")

    assert normalize_dropped_paths(f"inspect {dropped}") is None
    assert normalize_dropped_paths("relative.txt") is None
    assert normalize_dropped_paths("file://example.invalid/tmp/a.txt") is None
