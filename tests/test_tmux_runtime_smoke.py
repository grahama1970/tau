from __future__ import annotations

import runpy
import subprocess
from contextlib import suppress
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-tmux-runtime-smoke.py"


def test_ambiguous_paste_runner_executes_real_command_only_once(monkeypatch) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    calls: list[list[str]] = []

    def completed(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", completed)
    runner = namespace["_AmbiguousPasteRunner"]()
    argv = ["tmux", "-L", "server", "paste-buffer", "-t", "%1"]

    with suppress(subprocess.TimeoutExpired):
        runner(argv, timeout=1.0)

    assert calls == [argv]
    assert runner.paste_attempt_count == 1
    assert runner.induced_ambiguity_count == 1


def test_substitute_tmux_binary_is_fixture_evidence(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    substitute = tmp_path / "tmux-wrapper"
    substitute.write_text("#!/bin/sh\nexec tmux \"$@\"\n")
    substitute.chmod(0o755)

    assert namespace["_classify_tmux_surface"](str(substitute)) == "fixture"


def test_cleanup_requires_known_missing_server_diagnostic(monkeypatch) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    results = iter(
        (
            subprocess.CompletedProcess(
                ["tmux"], 0, "/tmp/tmux.sock\t1\t2\t3.4\n", ""
            ),
            subprocess.CompletedProcess(["tmux"], 0, "", ""),
            subprocess.CompletedProcess(["tmux"], 1, "", "permission denied"),
        )
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))

    receipt = namespace["_cleanup_server"](
        tmux_bin="tmux",
        server_name="server",
        timeout_seconds=1.0,
        expected_incarnation={
            "socket_path": "/tmp/tmux.sock",
            "server_pid": 1,
            "server_start_time": 2,
            "server_version": "3.4",
        },
    )

    assert receipt["post_verified_absent"] is False


def test_cleanup_rechecks_transient_server_exit_diagnostic(monkeypatch) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    results = iter(
        (
            subprocess.CompletedProcess(
                ["tmux"], 0, "/tmp/tmux.sock\t1\t2\t3.4\n", ""
            ),
            subprocess.CompletedProcess(["tmux"], 0, "", ""),
            subprocess.CompletedProcess(
                ["tmux"], 1, "", "server exited unexpectedly"
            ),
            subprocess.CompletedProcess(
                ["tmux"], 1, "", "no server running on /tmp/tmux.sock"
            ),
        )
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(namespace["time"], "sleep", lambda _seconds: None)

    receipt = namespace["_cleanup_server"](
        tmux_bin="tmux",
        server_name="server",
        timeout_seconds=1.0,
        expected_incarnation={
            "socket_path": "/tmp/tmux.sock",
            "server_pid": 1,
            "server_start_time": 2,
            "server_version": "3.4",
        },
    )

    assert receipt["post_verified_absent"] is True


def test_smoke_does_not_cleanup_server_without_acquired_scope(
    monkeypatch, tmp_path: Path
) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    cleanup_calls: list[dict[str, object]] = []

    class RejectingBackend:
        def __init__(self, **_kwargs):
            pass

        def ensure_scope(self, _request):
            raise RuntimeError("tmux_runtime_server_exists_unowned")

    monkeypatch.setitem(
        namespace["run_smoke"].__globals__, "TmuxRuntimeBackend", RejectingBackend
    )
    monkeypatch.setitem(
        namespace["run_smoke"].__globals__, "_classify_tmux_surface", lambda _bin: "real"
    )
    monkeypatch.setitem(
        namespace["run_smoke"].__globals__,
        "_cleanup_server",
        lambda **kwargs: cleanup_calls.append(kwargs),
    )

    receipt = namespace["run_smoke"](
        out_dir=tmp_path, tmux_bin="tmux", timeout_seconds=1.0
    )

    assert receipt["status"] == "BLOCKED"
    assert cleanup_calls == []


def test_cleanup_timeout_is_encoded_as_unverified_absence(monkeypatch) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    call_count = 0

    def timeout(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subprocess.CompletedProcess(
                ["tmux"], 0, "/tmp/tmux.sock\t1\t2\t3.4\n", ""
            )
        raise subprocess.TimeoutExpired(["tmux"], 1.0)

    monkeypatch.setattr(subprocess, "run", timeout)

    receipt = namespace["_cleanup_server"](
        tmux_bin="tmux",
        server_name="server",
        timeout_seconds=1.0,
        expected_incarnation={
            "socket_path": "/tmp/tmux.sock",
            "server_pid": 1,
            "server_start_time": 2,
            "server_version": "3.4",
        },
    )

    assert receipt["kill_returncode"] == 124
    assert receipt["verify_returncode"] == 124
    assert receipt["post_verified_absent"] is False
