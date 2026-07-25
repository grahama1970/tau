from pathlib import Path

import pytest

from tau_coding.extensions.api import ExtensionCommandContext


class _BranchResult:
    message = "Forked."
    input_prefill = "draft"


class _ReplacementSession:
    def __init__(self) -> None:
        self.cwd = Path("/workspace/project")
        self.session_id = "initial"
        self.session_path = self.cwd / "initial.jsonl"
        self.new_session_count = 0
        self.fork_calls: list[tuple[str, str | None]] = []
        self.resume_calls: list[str] = []

    async def new_session(self) -> str:
        self.new_session_count += 1
        self.session_id = "new-session"
        self.session_path = self.cwd / "new-session.jsonl"
        return "Started new session."

    async def fork_from_entry(self, entry_id: str, *, position: str | None = None) -> _BranchResult:
        self.fork_calls.append((entry_id, position))
        self.session_id = "fork-session"
        self.session_path = self.cwd / "fork-session.jsonl"
        return _BranchResult()

    async def resume(self, session_id: str) -> str:
        self.resume_calls.append(session_id)
        self.session_id = session_id
        self.session_path = self.cwd / f"{session_id}.jsonl"
        return f"Resumed {session_id}."


def _context(session: _ReplacementSession) -> ExtensionCommandContext:
    return ExtensionCommandContext(
        session=session,
        registry=None,
        text="/demo",
        name="demo",
        args="",
        extension_name="demo-ext",
    )


@pytest.mark.anyio
async def test_extension_new_session_runs_with_session_after_replacement() -> None:
    session = _ReplacementSession()
    seen: list[tuple[str, str, Path]] = []

    async def with_session(context: ExtensionCommandContext) -> None:
        seen.append((context.extension_name, context.session.session_id, context.cwd))

    result = await _context(session).newSession({"withSession": with_session})

    assert result["sessionId"] == "new-session"
    assert result["sessionPath"] == str(session.cwd / "new-session.jsonl")
    assert seen == [("demo-ext", "new-session", session.cwd)]


@pytest.mark.anyio
async def test_extension_fork_runs_with_session_after_replacement() -> None:
    session = _ReplacementSession()
    seen: list[str] = []

    def with_session(context: ExtensionCommandContext) -> None:
        seen.append(context.session.session_id)

    result = await _context(session).fork(
        "entry-1",
        {"position": "at", "with_session": with_session},
    )

    assert result["sessionId"] == "fork-session"
    assert result["inputPrefill"] == "draft"
    assert session.fork_calls == [("entry-1", "at")]
    assert seen == ["fork-session"]


@pytest.mark.anyio
async def test_extension_switch_session_runs_with_session_after_replacement() -> None:
    session = _ReplacementSession()
    seen: list[str] = []

    async def with_session(context: ExtensionCommandContext) -> None:
        seen.append(context.session.session_id)

    result = await _context(session).switchSession("target-session", {"withSession": with_session})

    assert result["sessionId"] == "target-session"
    assert result["message"] == "Resumed target-session."
    assert session.resume_calls == ["target-session"]
    assert seen == ["target-session"]
