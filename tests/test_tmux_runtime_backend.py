from __future__ import annotations

import fcntl
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends import (
    RuntimeBackendRegistry,
    RuntimeRequirement,
    TmuxRuntimeBackend,
    tmux_cleanup_authorization,
    tmux_runtime_scope_request,
    tmux_runtime_spawn_request,
    tmux_runtime_work_order,
)
from tau_coding.runtime_backends.tmux import _owned_name


class FakeTmux:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.timeouts: list[float | None] = []
        self.sessions: dict[str, str] = {}
        self.session_tokens: dict[str, str] = {}
        self.panes: dict[str, dict[str, str]] = {}
        self.buffers: dict[str, str] = {}
        self.session_count = 0
        self.window_count = 0
        self.pane_count = 0
        self.inventory_fails = False
        self.inventory_malformed = False
        self.paste_times_out_after_delivery = False
        self.new_window_reported_pane_override: str | None = None
        self.new_session_times_out_after_creation = False
        self.new_window_times_out_after_creation = False
        self.new_window_times_out_before_creation = False
        self.inject_unowned_session_on_new_session = False
        self.race_matching_unowned_session = False
        self.guarded_mutation_fails = False
        self.scope_configuration_fails = False
        self.move_pane_after_paste = False
        self.server_pid = "1001"
        self.server_start_time = "2002"
        self.server_version = "3.4"

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        self.timeouts.append(kwargs.get("timeout"))
        command = argv[3:]
        if command[:1] == ["-N"]:
            command = command[1:]
        elif command[:1] == ["-f"]:
            command = command[2:]
        if command[:1] == ["list-sessions"]:
            if "-F" in command and self.sessions:
                format_value = command[command.index("-F") + 1]
                if "#{socket_path}" in format_value:
                    record = (
                        "/tmp/tmux-1000/tau-test\t"
                        f"{self.server_pid}\t{self.server_start_time}\t{self.server_version}"
                    )
                    records = [record for _ in self.sessions]
                else:
                    records = [
                        f"{session_id}\t{session_name}"
                        for session_id, session_name in self.sessions.items()
                    ]
                return self._ok(
                    argv, "\n".join(records) + "\n"
                )
            return self._ok(
                argv,
                returncode=0 if self.sessions else 1,
                stderr="" if self.sessions else "no server running\n",
            )
        if command[:1] == ["new-session"]:
            self.session_count += 1
            self.window_count += 1
            self.pane_count += 1
            session_id = f"${self.session_count}"
            session_name = command[command.index("-s") + 1]
            owner_token = command[command.index("-e") + 1]
            if session_name in self.sessions.values():
                return self._ok(argv, returncode=1, stderr="duplicate session")
            window_id = f"@{self.window_count}"
            pane_id = f"%{self.pane_count}"
            if self.race_matching_unowned_session:
                self.sessions[session_id] = session_name
                self.panes[pane_id] = self._pane(
                    session_id, window_id, pane_id, "bash", "tau-control"
                )
                return self._ok(argv, returncode=1, stderr="duplicate session")
            self.sessions[session_id] = session_name
            self.session_tokens[session_id] = owner_token
            if self.inject_unowned_session_on_new_session:
                self.sessions["$99"] = "unowned"
            self.panes[pane_id] = self._pane(
                session_id, window_id, pane_id, "bash", "tau-control"
            )
            completed = self._ok(
                argv, f"{session_id}\t{session_name}\t{window_id}\t{pane_id}\n"
            )
            if self.new_session_times_out_after_creation:
                raise subprocess.TimeoutExpired(
                    argv, kwargs["timeout"], output=completed.stdout, stderr=""
                )
            return completed
        if command[:1] == ["set-window-option"]:
            return self._ok(argv, returncode=1 if self.scope_configuration_fails else 0)
        if command[:1] == ["show-environment"]:
            session_id = command[command.index("-t") + 1]
            token = self.session_tokens.get(session_id)
            if token is None:
                return self._ok(argv, returncode=1, stderr="unknown variable")
            return self._ok(argv, token + "\n")
        if command[:1] == ["display-message"]:
            target = command[command.index("-t") + 1]
            pane = self.panes.get(target)
            if pane is None:
                pane = next(
                    (item for item in self.panes.values() if item["session_id"] == target),
                    None,
                )
            if pane is None:
                return self._ok(argv, returncode=1)
            if command[-1] == "#{pane_start_command}":
                return self._ok(argv, pane["pane_start_command"] + "\n")
            return self._ok(
                argv,
                "/tmp/tmux-1000/tau-test\t"
                f"{self.server_pid}\t{self.server_start_time}\t{self.server_version}\n",
            )
        if command[:1] == ["new-window"]:
            if self.new_window_times_out_before_creation:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            self.window_count += 1
            self.pane_count += 1
            session_id = command[command.index("-t") + 1]
            window_id = f"@{self.window_count}"
            pane_id = f"%{self.pane_count}"
            window_name = command[command.index("-n") + 1]
            self.panes[pane_id] = self._pane(
                session_id, window_id, pane_id, "bash", window_name
            )
            self.panes[pane_id]["pane_start_command"] = command[-1]
            reported_pane_id = self.new_window_reported_pane_override or pane_id
            completed = self._ok(
                argv,
                f"{session_id}\t{self.sessions[session_id]}\t{window_id}\t"
                f"{reported_pane_id}\n",
            )
            if self.new_window_times_out_after_creation:
                raise subprocess.TimeoutExpired(
                    argv, kwargs["timeout"], output=completed.stdout, stderr=""
                )
            return completed
        if command[:1] == ["load-buffer"]:
            buffer_name = command[command.index("-b") + 1]
            self.buffers[buffer_name] = str(kwargs.get("input") or "")
            if ";" not in command:
                return self._ok(argv)
            if self.guarded_mutation_fails:
                return self._ok(argv, returncode=86, stderr="ownership guard failed")
            pane_id = command[command.index("-t") + 1]
            self.panes[pane_id]["text"] += self.buffers[buffer_name] + "\n"
            if self.move_pane_after_paste:
                self.panes[pane_id]["window_id"] = "@moved"
            self.buffers.pop(buffer_name, None)
            if self.paste_times_out_after_delivery:
                raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])
            return self._ok(argv)
        if command[:1] == ["paste-buffer"]:
            buffer_name = command[command.index("-b") + 1]
            pane_id = command[command.index("-t") + 1]
            self.panes[pane_id]["text"] += self.buffers[buffer_name]
            if "-d" in command:
                self.buffers.pop(buffer_name, None)
            if self.paste_times_out_after_delivery:
                raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])
            return self._ok(argv)
        if command[:1] == ["delete-buffer"]:
            self.buffers.pop(command[command.index("-b") + 1], None)
            return self._ok(argv)
        if command[:1] == ["capture-pane"]:
            pane_id = command[command.index("-t") + 1]
            pane = self.panes.get(pane_id)
            return self._ok(argv, pane["text"] if pane is not None else "", 0 if pane else 1)
        if command[:1] == ["list-panes"]:
            if self.inventory_fails:
                return self._ok(argv, "server unavailable\n", 1)
            if self.inventory_malformed:
                return self._ok(argv, "malformed\n")
            format_value = command[command.index("-F") + 1]
            if "#{window_name}" in format_value:
                lines = [
                    "\t".join(
                        (
                            pane["session_id"],
                            self.sessions[pane["session_id"]],
                            pane["window_id"],
                            pane["window_name"],
                            pane_id,
                            pane["pane_start_command"],
                        )
                    )
                    for pane_id, pane in sorted(self.panes.items())
                ]
            elif "#{session_name}" in format_value:
                lines = [
                    "\t".join(
                        (
                            pane["session_id"],
                            self.sessions[pane["session_id"]],
                            pane["window_id"],
                            pane_id,
                        )
                    )
                    for pane_id, pane in sorted(self.panes.items())
                ]
            else:
                lines = [
                "\t".join(
                    (
                        pane["session_id"],
                        pane["window_id"],
                        pane_id,
                        pane["pane_dead"],
                        pane["pane_pid"],
                        pane["pane_current_command"],
                        pane["pane_dead_status"],
                        pane["pane_dead_signal"],
                    )
                )
                for pane_id, pane in sorted(self.panes.items())
                ]
            return self._ok(argv, "\n".join(lines) + ("\n" if lines else ""))
        if command[:1] == ["kill-pane"]:
            pane_id = command[command.index("-t") + 1]
            existed = self.panes.pop(pane_id, None) is not None
            return self._ok(argv, returncode=0 if existed else 1)
        if command[:1] == ["if-shell"]:
            if self.guarded_mutation_fails:
                return self._ok(argv, returncode=86, stderr="ownership guard failed")
            true_command = command[-2].split()
            if true_command[:1] == ["kill-pane"]:
                pane_id = true_command[true_command.index("-t") + 1]
                existed = self.panes.pop(pane_id, None) is not None
                return self._ok(argv, returncode=0 if existed else 1)
            if true_command[:1] == ["kill-session"]:
                session_id = true_command[true_command.index("-t") + 1]
                self.sessions.pop(session_id, None)
                self.session_tokens.pop(session_id, None)
                self.panes = {
                    pane_id: pane
                    for pane_id, pane in self.panes.items()
                    if pane["session_id"] != session_id
                }
                return self._ok(argv)
            raise AssertionError(f"unexpected guarded tmux command: {true_command}")
        if command[:1] == ["kill-window"]:
            window_id = command[command.index("-t") + 1]
            self.panes = {
                pane_id: pane
                for pane_id, pane in self.panes.items()
                if pane["window_id"] != window_id
            }
            return self._ok(argv)
        if command[:1] == ["kill-session"]:
            session_id = command[command.index("-t") + 1]
            self.sessions.pop(session_id, None)
            self.session_tokens.pop(session_id, None)
            self.panes = {
                pane_id: pane
                for pane_id, pane in self.panes.items()
                if pane["session_id"] != session_id
            }
            return self._ok(argv)
        raise AssertionError(f"unexpected tmux command: {argv}")

    @staticmethod
    def _pane(
        session_id: str,
        window_id: str,
        pane_id: str,
        command: str,
        window_name: str,
    ) -> dict[str, str]:
        return {
            "session_id": session_id,
            "window_id": window_id,
            "pane_id": pane_id,
            "pane_dead": "0",
            "pane_pid": "123",
            "pane_current_command": command,
            "window_name": window_name,
            "pane_start_command": command,
            "pane_dead_status": "",
            "pane_dead_signal": "",
            "text": "",
        }

    @staticmethod
    def _ok(
        argv: list[str],
        stdout: str = "",
        returncode: int = 0,
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def test_tmux_backend_registers_interactive_capabilities() -> None:
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=FakeTmux())
    registry = RuntimeBackendRegistry()
    registry.register(backend)

    decision = registry.decide(
        RuntimeRequirement(
            backend="tmux",
            interaction_mode="interactive",
            required_capabilities=(
                "interactive",
                "stable_endpoint_id",
                "supports_owned_inventory",
                "supports_terminate",
            ),
            session_scope="persistent_subagent",
            observation_requirements=("PROCESS",),
        )
    )

    assert decision.status == "PASS"
    assert backend.capabilities().native_agent_state is False


def test_tmux_backend_requires_explicit_server() -> None:
    with pytest.raises(ValueError, match="explicit server"):
        TmuxRuntimeBackend(server_name="")
    with pytest.raises(ValueError, match="unsupported characters"):
        TmuxRuntimeBackend(server_name="unsafe/name")


def test_preexisting_server_is_not_adopted(tmp_path: Path) -> None:
    fake = FakeTmux()
    fake.sessions["$99"] = "unowned"
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)

    with pytest.raises(RuntimeError, match="server_exists_unowned"):
        backend.ensure_scope(
            tmux_runtime_scope_request(
                run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
            )
        )

    assert not any("new-session" in call for call in fake.calls)


def test_concurrent_server_creator_cannot_cross_atomic_guard(tmp_path: Path) -> None:
    fake = FakeTmux()
    backend = TmuxRuntimeBackend(
        server_name="tau-test", command_runner=fake, socket_root=tmp_path
    )
    lock_path = tmp_path / ".tau-tmux-tau-test.creation.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(RuntimeError, match="server_creation_in_progress"):
            backend.ensure_scope(
                tmux_runtime_scope_request(
                    run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
                )
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert fake.calls == []


def test_scope_creation_timeout_reconciles_created_session(tmp_path: Path) -> None:
    fake = FakeTmux()
    fake.new_session_times_out_after_creation = True
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)

    scope = backend.ensure_scope(
        tmux_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
        )
    ).to_value()

    assert scope["session_id"] == "$1"
    assert len(fake.sessions) == 1


def test_scope_configuration_failure_reclaims_owned_session(tmp_path: Path) -> None:
    fake = FakeTmux()
    fake.scope_configuration_fails = True
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)

    with pytest.raises(RuntimeError, match="scope_configuration_uncertain"):
        backend.ensure_scope(
            tmux_runtime_scope_request(
                run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
            )
        )

    assert fake.sessions == {}
    assert fake.panes == {}
    fake.scope_configuration_fails = False
    scope = backend.ensure_scope(
        tmux_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
        )
    ).to_value()
    assert scope["session_id"]


def test_noncooperating_server_race_is_detected_after_creation(
    tmp_path: Path,
) -> None:
    fake = FakeTmux()
    fake.inject_unowned_session_on_new_session = True
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)

    with pytest.raises(RuntimeError, match="server_ownership_uncertain"):
        backend.ensure_scope(
            tmux_runtime_scope_request(
                run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
            )
        )

    assert fake.sessions == {"$99": "unowned"}


def test_matching_unowned_session_race_fails_owner_token_check(
    tmp_path: Path,
) -> None:
    fake = FakeTmux()
    fake.race_matching_unowned_session = True
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)

    with pytest.raises(RuntimeError, match="scope_owner_token_mismatch"):
        backend.ensure_scope(
            tmux_runtime_scope_request(
                run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
            )
        )


def test_later_scope_does_not_adopt_matching_unowned_session(tmp_path: Path) -> None:
    fake = FakeTmux()
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)
    backend.ensure_scope(
        tmux_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
        )
    )
    future_name = _owned_name("scope", "run-2")
    fake.sessions["$99"] = future_name
    fake.panes["%99"] = fake._pane("$99", "@99", "%99", "bash", "tau-control")

    with pytest.raises(RuntimeError, match="scope_owner_token_mismatch"):
        backend.ensure_scope(
            tmux_runtime_scope_request(
                run_id="run-2", owner="tau", cwd=tmp_path, label="scope"
            )
        )


def test_duplicate_labels_create_distinct_exact_scope_and_pane_ids(tmp_path: Path) -> None:
    fake = FakeTmux()
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)

    first_scope = backend.ensure_scope(
        tmux_runtime_scope_request(run_id="run-1", owner="tau", cwd=tmp_path, label="same")
    ).to_value()
    second_scope = backend.ensure_scope(
        tmux_runtime_scope_request(run_id="run-2", owner="tau", cwd=tmp_path, label="same")
    ).to_value()
    first = _spawn(backend, tmp_path, first_scope, run_id="run-1", attempt_id="attempt-1")
    second = _spawn(backend, tmp_path, second_scope, run_id="run-2", attempt_id="attempt-1")

    assert first.scope_id != second.scope_id
    assert first.endpoint_id != second.endpoint_id
    assert all(call[:3] == ["tmux", "-L", "tau-test"] for call in fake.calls)
    assert all(
        ("-f" in call if "new-session" in call and call.index("new-session") == 5 else "-N" in call)
        for call in fake.calls
    )
    assert any(
        "set-window-option" in call and "-g" in call and "remain-on-exit" in call
        for call in fake.calls
    )


def test_duplicate_attempt_spawn_is_blocked(tmp_path: Path) -> None:
    backend, _fake, scope, _lease = _spawned_backend(tmp_path)
    request = _spawn_request(tmp_path, scope, run_id="run-1", attempt_id="attempt-2")

    backend.spawn(request)
    with pytest.raises(RuntimeError, match="attempt_already_spawned"):
        backend.spawn(request)


def test_spawn_timeout_reconciles_created_endpoint(tmp_path: Path) -> None:
    fake = FakeTmux()
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)
    scope = backend.ensure_scope(
        tmux_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
        )
    ).to_value()
    fake.new_window_times_out_after_creation = True

    lease = _spawn(
        backend, tmp_path, scope, run_id="run-1", attempt_id="attempt-1"
    )

    assert lease.endpoint_id in fake.panes
    assert backend.list_owned("run-1") == [lease]


def test_spawn_reconciliation_rejects_preexisting_matching_window(
    tmp_path: Path,
) -> None:
    fake = FakeTmux()
    backend = TmuxRuntimeBackend(server_name="tau-test", command_runner=fake)
    scope = backend.ensure_scope(
        tmux_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
        )
    ).to_value()
    window_name = _owned_name("same-worker", "run-1-attempt-1")
    fake.panes["%99"] = fake._pane(
        str(scope["session_id"]), "@99", "%99", "bash", window_name
    )
    fake.new_window_times_out_before_creation = True

    with pytest.raises(RuntimeError, match="spawn_orphan_uncertain"):
        _spawn(
            backend, tmp_path, scope, run_id="run-1", attempt_id="attempt-1"
        )


def test_duplicate_spawn_response_reconciles_tokenized_new_endpoint(tmp_path: Path) -> None:
    backend, fake, scope, existing = _spawned_backend(tmp_path)
    fake.new_window_reported_pane_override = existing.endpoint_id
    calls_before = len(fake.calls)

    reconciled = backend.spawn(
        _spawn_request(
            tmp_path, scope, run_id="run-1", attempt_id="attempt-2"
        )
    )

    assert existing.endpoint_id in fake.panes
    assert reconciled.endpoint_id != existing.endpoint_id
    assert reconciled.endpoint_id in fake.panes
    assert not any("kill-pane" in call for call in fake.calls[calls_before:])


def test_uncertain_submit_is_cached_and_text_is_not_duplicated(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.paste_times_out_after_delivery = True
    work_order = tmux_runtime_work_order(
        work_order_sha256=lease.work_order_sha256, text="printf marker"
    )

    first = backend.submit(lease, work_order)
    second = backend.submit(lease, work_order)

    assert first == second
    assert first.delivery_status == "INDETERMINATE"
    assert fake.panes[lease.endpoint_id]["text"].count("printf marker") == 1
    assert sum(
        any("paste-buffer" in argument for argument in call) for call in fake.calls
    ) == 1


def test_atomic_submit_guard_blocks_replaced_server_target(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.guarded_mutation_fails = True

    receipt = backend.submit(
        lease,
        tmux_runtime_work_order(
            work_order_sha256=lease.work_order_sha256, text="printf marker"
        ),
    )

    assert receipt.delivery_status == "INDETERMINATE"
    assert fake.panes[lease.endpoint_id]["text"] == ""


def test_post_submit_binding_error_is_cached_as_indeterminate(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.move_pane_after_paste = True
    work_order = tmux_runtime_work_order(
        work_order_sha256=lease.work_order_sha256, text="printf marker"
    )

    first = backend.submit(lease, work_order)
    fake.panes[lease.endpoint_id]["window_id"] = lease.backend_ids.to_value()[
        "window_id"
    ]
    second = backend.submit(lease, work_order)

    assert first == second
    assert first.delivery_status == "INDETERMINATE"
    assert fake.panes[lease.endpoint_id]["text"].count("printf marker") == 1


def test_uncertain_preflight_is_retryable_before_mutation(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    work_order = tmux_runtime_work_order(
        work_order_sha256=lease.work_order_sha256, text="printf marker"
    )
    fake.inventory_fails = True

    with pytest.raises(RuntimeError, match="submit_preflight_uncertain"):
        backend.submit(lease, work_order)

    assert not any("load-buffer" in call for call in fake.calls)
    fake.inventory_fails = False
    receipt = backend.submit(lease, work_order)
    assert receipt.delivery_status == "CONFIRMED"
    assert fake.panes[lease.endpoint_id]["text"].count("printf marker") == 1


def test_named_buffers_are_isolated_by_endpoint_even_for_same_work_hash(
    tmp_path: Path,
) -> None:
    backend, fake, scope, first = _spawned_backend(tmp_path)
    second = backend.spawn(
        _spawn_request(
            tmp_path,
            scope,
            run_id="run-1",
            attempt_id="attempt-2",
            work_order_sha256=first.work_order_sha256,
        )
    )
    first_receipt = backend.submit(
        first,
        tmux_runtime_work_order(
            work_order_sha256=first.work_order_sha256, text="first-token"
        ),
    )
    second_receipt = backend.submit(
        second,
        tmux_runtime_work_order(
            work_order_sha256=second.work_order_sha256, text="second-token"
        ),
    )

    first_ack = first_receipt.backend_acknowledgement.to_value()
    second_ack = second_receipt.backend_acknowledgement.to_value()
    assert first_ack["buffer_name"] != second_ack["buffer_name"]
    assert fake.panes[first.endpoint_id]["text"] == "first-token\n"
    assert fake.panes[second.endpoint_id]["text"] == "second-token\n"


@pytest.mark.parametrize("failure", ["failed", "malformed"])
def test_observation_ambiguity_is_unknown(tmp_path: Path, failure: str) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.inventory_fails = failure == "failed"
    fake.inventory_malformed = failure == "malformed"

    event = backend.observe(lease)

    assert event.state == "UNKNOWN"
    assert event.liveness == "UNKNOWN"


def test_bare_shell_is_alive_but_not_claimed_ready(tmp_path: Path) -> None:
    backend, _fake, _scope, lease = _spawned_backend(tmp_path)

    event = backend.observe(lease)

    assert event.state == "UNKNOWN"
    assert event.liveness == "ALIVE"


def test_successful_inventory_without_exact_pane_is_dead(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.panes.pop(lease.endpoint_id)

    event = backend.observe(lease)

    assert event.state == "EXITED"
    assert event.liveness == "DEAD"


def test_moved_pane_binding_is_rejected_by_observation_and_inventory(
    tmp_path: Path,
) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.panes[lease.endpoint_id]["window_id"] = "@moved"

    with pytest.raises(RuntimeError, match="endpoint_binding_mismatch"):
        backend.observe(lease)
    with pytest.raises(RuntimeError, match="endpoint_binding_mismatch"):
        backend.list_owned("run-1")


def test_dead_pane_exit_status_distinguishes_exit_from_crash(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.panes[lease.endpoint_id]["pane_dead"] = "1"
    fake.panes[lease.endpoint_id]["pane_dead_status"] = "9"

    event = backend.observe(lease)

    assert event.state == "CRASHED"
    assert event.liveness == "DEAD"
    assert event.confidence == "NATIVE"


def test_owned_inventory_failure_does_not_claim_no_endpoints(tmp_path: Path) -> None:
    backend, fake, _scope, _lease = _spawned_backend(tmp_path)
    fake.inventory_fails = True

    with pytest.raises(RuntimeError, match="inventory_uncertain"):
        backend.list_owned("run-1")


def test_capture_inventory_failure_is_uncertain_not_absent(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.inventory_malformed = True

    receipt = backend.capture(lease, 10).to_value()

    assert receipt["pane_present"] == "UNKNOWN"
    assert receipt["errors"] == ["tmux_runtime_capture_inventory_uncertain"]


def test_multiline_submit_is_rejected_before_tmux_mutation(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)

    with pytest.raises(ValueError, match="one printable line"):
        backend.submit(
            lease,
            tmux_runtime_work_order(
                work_order_sha256=lease.work_order_sha256,
                text="first line\nsecond line",
            ),
        )

    assert not any("load-buffer" in call for call in fake.calls)


def test_tab_submit_is_rejected_before_tmux_mutation(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)

    with pytest.raises(ValueError, match="one printable line"):
        backend.submit(
            lease,
            tmux_runtime_work_order(
                work_order_sha256=lease.work_order_sha256,
                text="printf\tmarker",
            ),
        )

    assert not any("load-buffer" in call for call in fake.calls)


def test_server_reincarnation_blocks_old_lease(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.server_pid = "9999"

    with pytest.raises(RuntimeError, match="server_incarnation_mismatch"):
        backend.observe(lease)


def test_server_reincarnation_blocks_capture_and_termination_before_mutation(
    tmp_path: Path,
) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.server_pid = "9999"
    calls_before = len(fake.calls)

    with pytest.raises(RuntimeError, match="server_incarnation_mismatch"):
        backend.capture(lease, 10)
    with pytest.raises(RuntimeError, match="server_incarnation_mismatch"):
        backend.terminate(lease, tmux_cleanup_authorization(lease))

    new_calls = fake.calls[calls_before:]
    assert not any("capture-pane" in call for call in new_calls)
    assert not any("kill-pane" in call for call in new_calls)
    assert lease.endpoint_id in fake.panes


def test_capture_has_deterministic_byte_limit(tmp_path: Path) -> None:
    fake = FakeTmux()
    backend = TmuxRuntimeBackend(
        server_name="tau-test", command_runner=fake, max_capture_bytes=8
    )
    scope = backend.ensure_scope(
        tmux_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
        )
    ).to_value()
    lease = _spawn(backend, tmp_path, scope, run_id="run-1", attempt_id="attempt-1")
    fake.panes[lease.endpoint_id]["text"] = "abcdefghijklmnop"

    capture = backend.capture(lease, 80).to_value()

    assert capture["text"] == "abcdefgh"
    assert capture["returned_bytes"] == 8
    assert capture["truncated"] is True


def test_capture_enforces_requested_line_limit_locally(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.panes[lease.endpoint_id]["text"] = "one\ntwo\nthree\nfour\n"

    capture = backend.capture(lease, 2).to_value()
    empty = backend.capture(lease, 0).to_value()

    assert capture["text"] == "three\nfour\n"
    assert capture["returned_lines"] == 2
    assert capture["line_truncated"] is True
    assert empty["text"] == ""
    assert empty["returned_lines"] == 0


def test_missing_scope_session_is_dead_when_same_server_inventory_is_available(
    tmp_path: Path,
) -> None:
    backend, fake, scope, lease = _spawned_backend(tmp_path)
    backend.ensure_scope(
        tmux_runtime_scope_request(
            run_id="run-2", owner="tau", cwd=tmp_path, label="other-scope"
        )
    )
    fake.sessions.pop(str(scope["session_id"]))
    fake.panes = {
        pane_id: pane
        for pane_id, pane in fake.panes.items()
        if pane["session_id"] != scope["session_id"]
    }

    event = backend.observe(lease)

    assert event.state == "EXITED"
    assert event.liveness == "DEAD"


def test_cleanup_requires_exact_authorization_and_preserves_unrelated_pane(
    tmp_path: Path,
) -> None:
    backend, fake, scope, lease = _spawned_backend(tmp_path)
    unrelated = _spawn(backend, tmp_path, scope, run_id="run-1", attempt_id="attempt-2")
    bad = tmux_cleanup_authorization(lease).to_value()
    bad["owner"] = "other"

    with pytest.raises(RuntimeError, match="cleanup_unauthorized"):
        backend.terminate(lease, FrozenJson.from_value(bad))
    result = backend.terminate(lease, tmux_cleanup_authorization(lease)).to_value()

    assert result["post_verified_absent"] is True
    assert lease.endpoint_id not in fake.panes
    assert unrelated.endpoint_id in fake.panes
    assert backend.list_owned("run-1") == [unrelated]


def test_atomic_termination_guard_does_not_kill_replacement(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)
    fake.guarded_mutation_fails = True

    receipt = backend.terminate(
        lease, tmux_cleanup_authorization(lease)
    ).to_value()

    assert receipt["status"] == "BLOCKED"
    assert receipt["post_verified_absent"] is False
    assert lease.endpoint_id in fake.panes


def test_wait_event_caps_tmux_timeout_by_deadline(tmp_path: Path) -> None:
    backend, fake, _scope, lease = _spawned_backend(tmp_path)

    backend.wait_event(
        lease,
        cursor="not-the-current-event",
        deadline=datetime.now(UTC) + timedelta(seconds=0.05),
    )

    assert fake.timeouts[-1] is not None
    assert 0 < float(fake.timeouts[-1]) <= 0.05


def _spawned_backend(
    tmp_path: Path,
) -> tuple[TmuxRuntimeBackend, FakeTmux, dict[str, Any], Any]:
    fake = FakeTmux()
    backend = TmuxRuntimeBackend(
        server_name="tau-test",
        command_runner=fake,
        poll_interval_seconds=0.01,
    )
    scope = backend.ensure_scope(
        tmux_runtime_scope_request(run_id="run-1", owner="tau", cwd=tmp_path, label="scope")
    ).to_value()
    lease = _spawn(backend, tmp_path, scope, run_id="run-1", attempt_id="attempt-1")
    return backend, fake, scope, lease


def _spawn(
    backend: TmuxRuntimeBackend,
    tmp_path: Path,
    scope: dict[str, Any],
    *,
    run_id: str,
    attempt_id: str,
):
    return backend.spawn(_spawn_request(tmp_path, scope, run_id=run_id, attempt_id=attempt_id))


def _spawn_request(
    tmp_path: Path,
    scope: dict[str, Any],
    *,
    run_id: str,
    attempt_id: str,
    work_order_sha256: str | None = None,
) -> FrozenJson:
    return tmux_runtime_spawn_request(
        run_id=run_id,
        plan_revision=canonical_sha256({"plan": run_id}),
        dag_id="dag-1",
        node_id="worker",
        attempt_id=attempt_id,
        attempt_number=int(attempt_id.rsplit("-", 1)[1]),
        execution_token=canonical_sha256({"execution": f"{run_id}:{attempt_id}"}),
        scope_id=str(scope["scope_id"]),
        command=("bash", "--noprofile", "--norc"),
        cwd=tmp_path,
        work_order_sha256=(
            work_order_sha256
            or canonical_sha256({"work": f"{run_id}:{attempt_id}"})
        ),
        goal_hash=canonical_sha256({"goal": "tmux runtime"}),
        owner="tau",
        label="same-worker",
    )
