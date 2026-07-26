import pytest

from tau_coding.tui.terminal_notification import (
    TerminalNotificationController,
    desktop_notification_protocol,
    desktop_notification_sequence,
    osc9_notification_sequence,
    osc99_notification_sequence,
    terminal_notification_supported,
)


class FakeStream:
    def __init__(self, *, is_tty: bool = True) -> None:
        self.is_tty = is_tty

    def isatty(self) -> bool:
        return self.is_tty


def test_terminal_notification_support_requires_tty_and_non_ci_terminal() -> None:
    assert terminal_notification_supported(environ={"TERM": "xterm"}, stream=FakeStream())
    assert not terminal_notification_supported(environ={"TERM": "dumb"}, stream=FakeStream())
    assert not terminal_notification_supported(
        environ={"TERM": "xterm", "CI": "1"},
        stream=FakeStream(),
    )
    assert not terminal_notification_supported(
        environ={"TERM": "xterm"},
        stream=FakeStream(is_tty=False),
    )


def test_desktop_notification_protocol_detects_supported_terminals() -> None:
    assert desktop_notification_protocol(environ={"TERM_PROGRAM": "ghostty"}) == "osc9"
    assert desktop_notification_protocol(environ={"TERM_PROGRAM": "iTerm.app"}) == "osc9"
    assert desktop_notification_protocol(environ={"MINTTY_SHORTCUT": "1"}) == "osc9"
    assert desktop_notification_protocol(environ={"TERM": "xterm-kitty"}) == "osc99"
    assert desktop_notification_protocol(environ={"TERM_PROGRAM": "unknown"}) is None


def test_desktop_notification_sequences_sanitize_message() -> None:
    assert osc9_notification_sequence("Tau\nfinished\a") == "\x1b]9;Taufinished\x07"
    assert osc99_notification_sequence("Tau\nfinished\a") == "\x1b]99;;Taufinished\x1b\\"
    assert desktop_notification_sequence(
        "Tau finished",
        environ={"TERM_PROGRAM": "ghostty"},
    ) == "\x1b]9;Tau finished\x07"


def test_terminal_notification_controller_writes_selected_mode() -> None:
    writes: list[str] = []
    controller = TerminalNotificationController(
        "desktop",
        enabled=True,
        writer=writes.append,
        environ={"TERM_PROGRAM": "ghostty"},
    )

    controller.notify_turn_finished()

    assert writes == ["\x1b]9;Tau turn finished\x07"]


def test_terminal_notification_controller_writes_pending_decision_message() -> None:
    writes: list[str] = []
    controller = TerminalNotificationController(
        "desktop",
        enabled=True,
        writer=writes.append,
        environ={"TERM_PROGRAM": "ghostty"},
    )

    controller.notify_pending_decision("Tau approval required: release")

    assert writes == ["\x1b]9;Tau approval required: release\x07"]


def test_terminal_notification_controller_honors_off_and_unknown_desktop_protocol() -> None:
    writes: list[str] = []
    TerminalNotificationController("off", enabled=True, writer=writes.append).notify_turn_finished()
    TerminalNotificationController(
        "desktop",
        enabled=True,
        writer=writes.append,
        environ={"TERM_PROGRAM": "unknown"},
    ).notify_turn_finished()

    assert writes == []


def test_terminal_notification_controller_disables_after_write_error() -> None:
    def failing_writer(sequence: str) -> None:
        del sequence
        raise OSError("closed")

    controller = TerminalNotificationController("bell", enabled=True, writer=failing_writer)

    controller.notify_turn_finished()

    assert controller.enabled is False


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("bell", "\a"), ("desktop", "\x1b]9;Tau turn finished\x07")],
)
def test_terminal_notification_controller_modes(mode: str, expected: str) -> None:
    writes: list[str] = []
    controller = TerminalNotificationController(
        mode,  # type: ignore[arg-type]
        enabled=True,
        writer=writes.append,
        environ={"TERM_PROGRAM": "ghostty"},
    )

    controller.notify_turn_finished()

    assert writes == [expected]
