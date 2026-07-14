"""Contract tests for Tau's Herdr runtime backend."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.runtime_backends import (
    HerdrRuntimeBackend,
    RuntimeBackendRegistry,
    RuntimeRequirement,
    herdr_cleanup_authorization,
    herdr_runtime_scope_request,
    herdr_runtime_spawn_request,
    herdr_runtime_work_order,
)
from tau_coding.runtime_backends.event_bridge import RuntimeEventBridge
from tau_coding.runtime_backends.herdr import _owned_label
from tau_coding.runtime_backends.herdr_native_events import HerdrNativeEventTransport


class FakeHerdr:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.workspace_count = 0
        self.pane_count = 0
        self.closed_panes: set[str] = set()
        self.pane_agents: dict[str, str] = {}
        self.pane_status = "idle"
        self.visible_text = "ready"
        self.processes: list[dict[str, Any]] = [
            {"pid": 123, "name": "bash", "argv": ["bash"], "cwd": "/tmp"}
        ]
        self.process_info_fails = False
        self.pane_get_error_code: str | None = None
        self.pane_get_malformed = False
        self.process_info_malformed = False
        self.closed_pane_error_code = "pane_not_found"
        self.close_missing_returns_not_found = False
        self.raise_oserror = False
        self.start_missing_terminal_id = False
        self.start_missing_agent_name = False
        self.start_missing_workspace_id = False
        self.start_missing_pane_id = False
        self.start_wrap_agent_in_list = False
        self.start_prepend_unrelated_agent = False
        self.start_unrelated_pane_id = False
        self.start_pane_id_override: str | None = None
        self.start_workspace_id_override: str | None = None
        self.pane_get_agent_override: str | None = None
        self.timeouts: list[float | None] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if self.raise_oserror:
            raise OSError("Herdr executable unavailable")
        self.timeouts.append(kwargs.get("timeout"))
        self.calls.append(list(argv))
        command = argv[3:]
        if command[:2] == ["workspace", "create"]:
            self.workspace_count += 1
            return self._ok(
                argv,
                {
                    "result": {
                        "workspace": {"workspace_id": f"w{self.workspace_count}"}
                    }
                },
            )
        if command[:2] == ["tab", "create"]:
            workspace_id = command[command.index("--workspace") + 1]
            return self._ok(
                argv,
                {"result": {"tab": {"tab_id": f"{workspace_id}:t1"}}},
            )
        if command[:2] == ["agent", "start"]:
            self.pane_count += 1
            name = command[2]
            workspace_id = command[command.index("--workspace") + 1]
            pane_id = self.start_pane_id_override or f"{workspace_id}:p{self.pane_count}"
            self.pane_agents[pane_id] = name
            agent = {
                "agent": name,
                "workspace_id": workspace_id,
                "pane_id": pane_id,
                "terminal_id": f"term-{self.pane_count}",
            }
            if self.start_missing_terminal_id:
                agent.pop("terminal_id")
            if self.start_missing_agent_name:
                agent.pop("agent")
            if self.start_missing_workspace_id:
                agent.pop("workspace_id")
            elif self.start_workspace_id_override is not None:
                agent["workspace_id"] = self.start_workspace_id_override
            if self.start_missing_pane_id:
                agent.pop("pane_id")
            if self.start_wrap_agent_in_list:
                result: dict[str, Any] = {"agents": [{"agent": agent}]}
            elif self.start_prepend_unrelated_agent:
                result = {
                    "agent": {"agent": "unrelated-agent"},
                    "nested": {"agent": agent},
                }
            else:
                result = {"agent": agent}
            if self.start_unrelated_pane_id:
                result["unrelated"] = {"pane_id": "w-unowned:p99"}
            return self._ok(argv, {"result": result})
        if command[:2] == ["pane", "send-text"]:
            return self._ok(argv, {"result": {"type": "text_sent"}})
        if command[:2] == ["pane", "read"]:
            return subprocess.CompletedProcess(argv, 0, self.visible_text + "\n", "")
        if command[:2] == ["pane", "get"]:
            pane_id = command[2]
            if self.pane_get_malformed:
                return subprocess.CompletedProcess(argv, 0, "not-json\n", "")
            if self.pane_get_error_code is not None:
                return self._error(argv, self.pane_get_error_code)
            if pane_id in self.closed_panes:
                return self._error(argv, self.closed_pane_error_code)
            workspace_id = pane_id.split(":p", 1)[0]
            return self._ok(
                argv,
                {
                    "result": {
                        "pane": {
                            "agent": self.pane_get_agent_override
                            or self.pane_agents.get(pane_id),
                            "workspace_id": workspace_id,
                            "pane_id": pane_id,
                            "agent_status": self.pane_status,
                        }
                    }
                },
            )
        if command[:2] == ["pane", "process-info"]:
            if self.process_info_malformed:
                return subprocess.CompletedProcess(argv, 0, "not-json\n", "")
            if self.process_info_fails:
                return self._error(argv, "process_info_unavailable")
            return self._ok(
                argv,
                {
                    "result": {
                        "process_info": {"foreground_processes": self.processes}
                    }
                },
            )
        if command[:2] == ["pane", "close"]:
            if self.close_missing_returns_not_found and command[2] in self.closed_panes:
                return self._error(argv, "pane_not_found")
            self.closed_panes.add(command[2])
            return self._ok(argv, {"result": {"type": "pane_closed"}})
        if command[:2] == ["workspace", "close"]:
            return self._ok(argv, {"result": {"type": "workspace_closed"}})
        raise AssertionError(f"unexpected Herdr command: {argv}")

    @staticmethod
    def _ok(argv: list[str], payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload) + "\n", "")

    @staticmethod
    def _error(argv: list[str], code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            1,
            json.dumps({"error": {"code": code}}) + "\n",
            "",
        )


def test_herdr_backend_registers_interactive_capabilities() -> None:
    backend = HerdrRuntimeBackend(session="default", command_runner=FakeHerdr())
    registry = RuntimeBackendRegistry()
    registry.register(backend)

    decision = registry.decide(
        RuntimeRequirement(
            backend="herdr",
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
    assert backend.capabilities().native_events is False
    assert backend.capabilities().structured_composer_state is False


def test_herdr_backend_requires_explicit_session() -> None:
    with pytest.raises(ValueError, match="explicit session"):
        HerdrRuntimeBackend(session="")


def test_owned_label_preserves_attempt_identity_suffix_when_label_is_long() -> None:
    label = "a" * 200

    first = _owned_label(label, "attempt-1")
    second = _owned_label(label, "attempt-2")

    assert len(first) <= 80
    assert len(second) <= 80
    assert first != second
    assert first.endswith(canonical_sha256("attempt-1").removeprefix("sha256:")[:12])
    assert second.endswith(canonical_sha256("attempt-2").removeprefix("sha256:")[:12])


def test_duplicate_labels_create_distinct_exact_id_scopes(tmp_path: Path) -> None:
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)

    first = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="duplicate-label"
        )
    ).to_value()
    second = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-2", owner="tau", cwd=tmp_path, label="duplicate-label"
        )
    ).to_value()

    assert first["workspace_id"] == "w1"
    assert second["workspace_id"] == "w2"
    assert first["label"] != second["label"]
    assert all(call[:3] == ["herdr", "--session", "default"] for call in fake.calls)


def test_scope_reuse_requires_same_owner_and_cwd(tmp_path: Path) -> None:
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    request = herdr_runtime_scope_request(
        run_id="run-1", owner="tau", cwd=tmp_path, label="scope"
    )

    first = backend.ensure_scope(request)
    second = backend.ensure_scope(request)

    assert first == second
    assert sum(call[3:5] == ["workspace", "create"] for call in fake.calls) == 1
    with pytest.raises(RuntimeError, match="scope_binding_mismatch"):
        backend.ensure_scope(
            herdr_runtime_scope_request(
                run_id="run-1", owner="other", cwd=tmp_path, label="scope"
            )
        )


def test_spawn_binds_exact_workspace_pane_terminal_and_session(tmp_path: Path) -> None:
    backend, _, lease, _ = _spawned_backend(tmp_path)

    backend_ids = lease.backend_ids.to_value()
    assert lease.backend == "herdr"
    assert lease.backend_session_id == "default"
    assert lease.scope_id == "w1"
    assert lease.endpoint_id == "w1:p1"
    assert backend_ids["workspace_id"] == "w1"
    assert backend_ids["pane_id"] == "w1:p1"
    assert backend_ids["terminal_id"] == "term-1"
    assert backend.list_owned("run-1") == [lease]


@pytest.mark.parametrize("response_shape", ["list", "multiple"])
def test_spawn_selects_exact_agent_from_nested_response(
    tmp_path: Path,
    response_shape: str,
) -> None:
    fake = FakeHerdr()
    fake.start_wrap_agent_in_list = response_shape == "list"
    fake.start_prepend_unrelated_agent = response_shape == "multiple"
    backend, _, lease, _ = _spawned_backend(tmp_path, command_runner=fake)

    assert lease.endpoint_id == "w1:p1"
    assert lease.backend_ids.to_value()["agent_name"].startswith("tau-")
    assert backend.list_owned("run-1") == [lease]
    assert fake.closed_panes == set()


def test_duplicate_endpoint_labels_create_distinct_attempt_bound_ids(
    tmp_path: Path,
) -> None:
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    work_hash = canonical_sha256({"work": "bounded"})

    leases = [
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id=f"attempt-{attempt}",
                attempt_number=attempt,
                execution_token=f"token-{attempt}",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=work_hash,
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
                label="duplicate-task",
            )
        )
        for attempt in (1, 2)
    ]

    assert leases[0].endpoint_id != leases[1].endpoint_id
    names = [lease.backend_ids.to_value()["agent_name"] for lease in leases]
    assert names[0] != names[1]
    assert backend.list_owned("run-1") == leases


def test_spawn_does_not_close_existing_endpoint_on_duplicate_response_id(
    tmp_path: Path,
) -> None:
    backend, fake, first, _ = _spawned_backend(tmp_path)
    fake.start_pane_id_override = first.endpoint_id
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()

    with pytest.raises(RuntimeError, match="endpoint_already_exists"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-2",
                attempt_number=2,
                execution_token="token-2",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=canonical_sha256({"work": 2}),
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )

    assert fake.closed_panes == set()
    assert backend.list_owned("run-1") == [first]


def test_spawn_rejects_duplicate_attempt_before_starting_second_agent(
    tmp_path: Path,
) -> None:
    backend, fake, _, _ = _spawned_backend(tmp_path)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-2", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    request = herdr_runtime_spawn_request(
        run_id="run-2",
        plan_revision=canonical_sha256({"plan": 2}),
        dag_id="dag-2",
        node_id="worker",
        attempt_id="attempt-1",
        attempt_number=1,
        execution_token="token-1",
        scope_id=scope["scope_id"],
        command=("bash",),
        cwd=tmp_path,
        work_order_sha256=canonical_sha256({"work": 2}),
        goal_hash=canonical_sha256({"goal": 2}),
        owner="tau",
    )

    backend.spawn(request)
    start_count = sum(call[3:5] == ["agent", "start"] for call in fake.calls)
    with pytest.raises(RuntimeError, match="attempt_already_spawned"):
        backend.spawn(request)

    assert sum(call[3:5] == ["agent", "start"] for call in fake.calls) == start_count


def test_spawn_reclaims_pane_when_start_response_is_incomplete(tmp_path: Path) -> None:
    fake = FakeHerdr()
    fake.start_missing_terminal_id = True
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    request = herdr_runtime_spawn_request(
        run_id="run-1",
        plan_revision=canonical_sha256({"plan": 1}),
        dag_id="dag-1",
        node_id="worker",
        attempt_id="attempt-1",
        attempt_number=1,
        execution_token="token-1",
        scope_id=scope["scope_id"],
        command=("bash",),
        cwd=tmp_path,
        work_order_sha256=canonical_sha256({"work": 1}),
        goal_hash=canonical_sha256({"goal": 1}),
        owner="tau",
    )

    with pytest.raises(ValueError, match="terminal_id"):
        backend.spawn(request)

    assert fake.closed_panes == {"w1:p1"}
    assert [call[3:5] for call in fake.calls[-3:]] == [
        ["pane", "get"],
        ["pane", "close"],
        ["pane", "get"],
    ]


def test_spawn_reclaims_pane_when_start_response_omits_agent_name(tmp_path: Path) -> None:
    fake = FakeHerdr()
    fake.start_missing_agent_name = True
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()

    with pytest.raises(RuntimeError, match="spawn_agent_mismatch"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="token-1",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=canonical_sha256({"work": 1}),
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )

    assert fake.closed_panes == {"w1:p1"}


def test_spawn_without_recoverable_pane_id_reserves_attempt(tmp_path: Path) -> None:
    fake = FakeHerdr()
    fake.start_missing_pane_id = True
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    request = herdr_runtime_spawn_request(
        run_id="run-1",
        plan_revision=canonical_sha256({"plan": 1}),
        dag_id="dag-1",
        node_id="worker",
        attempt_id="attempt-1",
        attempt_number=1,
        execution_token="token-1",
        scope_id=scope["scope_id"],
        command=("bash",),
        cwd=tmp_path,
        work_order_sha256=canonical_sha256({"work": 1}),
        goal_hash=canonical_sha256({"goal": 1}),
        owner="tau",
    )

    with pytest.raises(ValueError, match="pane_id"):
        backend.spawn(request)
    with pytest.raises(RuntimeError, match="attempt_already_spawned"):
        backend.spawn(request)

    assert sum(call[3:5] == ["agent", "start"] for call in fake.calls) == 1
    assert fake.closed_panes == set()


def test_spawn_does_not_reclaim_unrelated_nested_pane_id(tmp_path: Path) -> None:
    fake = FakeHerdr()
    fake.start_missing_pane_id = True
    fake.start_unrelated_pane_id = True
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()

    with pytest.raises(ValueError, match="pane_id"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="token-1",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=canonical_sha256({"work": 1}),
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )

    assert fake.closed_panes == set()


def test_failed_spawn_cleanup_requires_exact_pane_not_found(tmp_path: Path) -> None:
    fake = FakeHerdr()
    fake.start_missing_terminal_id = True
    fake.closed_pane_error_code = "resource_not_found"
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()

    with pytest.raises(RuntimeError, match="failed_spawn_cleanup_not_verified"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="token-1",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=canonical_sha256({"work": 1}),
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )

    assert fake.closed_panes == {"w1:p1"}


def test_failed_spawn_cleanup_recovers_when_start_omits_workspace_id(
    tmp_path: Path,
) -> None:
    fake = FakeHerdr()
    fake.start_missing_workspace_id = True
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()

    with pytest.raises(ValueError, match="workspace_id"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="token-1",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=canonical_sha256({"work": 1}),
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )

    assert fake.closed_panes == {"w1:p1"}
    assert [call[3:6] for call in fake.calls if call[3:5] == ["pane", "get"]] == [
        ["pane", "get", "w1:p1"],
        ["pane", "get", "w1:p1"],
    ]


def test_failed_spawn_cleanup_recovers_when_start_misreports_workspace_id(
    tmp_path: Path,
) -> None:
    fake = FakeHerdr()
    fake.start_workspace_id_override = "w-unowned"
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()

    with pytest.raises(RuntimeError, match="spawn_workspace_mismatch"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="token-1",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=canonical_sha256({"work": 1}),
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )

    assert fake.closed_panes == {"w1:p1"}


def test_failed_spawn_cleanup_requires_independent_agent_identity(tmp_path: Path) -> None:
    fake = FakeHerdr()
    fake.start_missing_terminal_id = True
    fake.pane_get_agent_override = "unrelated-agent"
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()

    with pytest.raises(RuntimeError, match="ownership_not_verified"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="token-1",
                scope_id=scope["scope_id"],
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=canonical_sha256({"work": 1}),
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )

    assert fake.closed_panes == set()


def test_spawn_rejects_attempted_adoption_of_unowned_scope(tmp_path: Path) -> None:
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    work_hash = canonical_sha256({"work": "bounded"})

    with pytest.raises(RuntimeError, match="scope_unknown"):
        backend.spawn(
            herdr_runtime_spawn_request(
                run_id="run-1",
                plan_revision=canonical_sha256({"plan": 1}),
                dag_id="dag-1",
                node_id="worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token="token-1",
                scope_id="workspace-created-outside-tau",
                command=("bash",),
                cwd=tmp_path,
                work_order_sha256=work_hash,
                goal_hash=canonical_sha256({"goal": 1}),
                owner="tau",
            )
        )
    assert not any(call[3:5] == ["agent", "start"] for call in fake.calls)


def test_submit_is_at_most_once_and_records_only_delivery(tmp_path: Path) -> None:
    backend, fake, lease, work_hash = _spawned_backend(tmp_path)
    work_order = herdr_runtime_work_order(
        work_order_sha256=work_hash,
        text="printf 'PASS\\n'",
    )

    first = backend.submit(lease, work_order)
    second = backend.submit(lease, work_order)

    assert first == second
    assert first.delivery_status == "CONFIRMED"
    assert first.provider_execution_status == "NOT_OBSERVED"
    assert first.composer_state_before == "UNKNOWN"
    assert first.composer_state_after == "UNKNOWN"
    assert sum(call[3:5] == ["pane", "send-text"] for call in fake.calls) == 1


def test_visible_pass_text_is_diagnostic_not_completion(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.visible_text = "PASS task complete"
    fake.pane_status = "idle"

    capture = backend.capture(lease, 20).to_value()
    event = backend.observe(lease)

    assert capture["text"] == "PASS task complete\n"
    assert capture["diagnostic_only"] is True
    assert event.state == "READY"
    assert event.event_type == "RUNTIME_OBSERVATION_RECORDED"
    assert event.observation.to_value()["visible_text_diagnostic_only"] is True


@pytest.mark.parametrize(
    ("status", "processes", "visible", "expected_state", "expected_liveness"),
    [
        ("working", [{"pid": 1}], "working", "RUNNING", "ALIVE"),
        ("blocked", [{"pid": 1}], "blocked", "BLOCKED", "ALIVE"),
        ("idle", [{"pid": 1}], "login required", "READY", "ALIVE"),
        ("idle", [{"pid": 1}], "Hooks need review", "READY", "ALIVE"),
        ("idle", [], "shell exited", "UNKNOWN", "UNKNOWN"),
    ],
)
def test_observation_maps_native_process_and_diagnostic_states(
    tmp_path: Path,
    status: str,
    processes: list[dict[str, Any]],
    visible: str,
    expected_state: str,
    expected_liveness: str,
) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.pane_status = status
    fake.processes = processes
    fake.visible_text = visible

    event = backend.observe(lease)

    assert event.state == expected_state
    assert event.liveness == expected_liveness


def test_visible_auth_and_interstitial_text_remains_diagnostic_only(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.pane_status = "working"
    fake.visible_text = "Documentation: sign in later; update available next week"

    event = backend.observe(lease)
    observation = event.observation.to_value()

    assert event.state == "RUNNING"
    assert event.liveness == "ALIVE"
    assert observation["visible_text_diagnostic_only"] is True
    assert observation["visible_auth_marker"] is True
    assert observation["visible_interstitial_marker"] is True


def test_observation_preserves_unknown_when_process_info_fails(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.process_info_fails = True

    event = backend.observe(lease)

    assert event.state == "UNKNOWN"
    assert event.liveness == "UNKNOWN"
    assert event.confidence == "UNKNOWN"


def test_observation_does_not_treat_transport_failure_as_confirmed_death(
    tmp_path: Path,
) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.pane_get_error_code = "server_unavailable"

    event = backend.observe(lease)

    assert event.state == "UNKNOWN"
    assert event.liveness == "UNKNOWN"
    assert event.confidence == "UNKNOWN"
    assert event.observation.to_value()["error_code"] == "server_unavailable"


def test_observation_preserves_unknown_when_herdr_launch_fails(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.raise_oserror = True

    event = backend.observe(lease)

    assert event.state == "UNKNOWN"
    assert event.liveness == "UNKNOWN"
    assert event.confidence == "UNKNOWN"
    assert event.observation.to_value()["pane_present"] is False
    assert event.observation.to_value()["error_code"] is None


def test_observation_preserves_unknown_for_malformed_pane_response(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.pane_get_malformed = True

    event = backend.observe(lease)

    assert event.state == "UNKNOWN"
    assert event.liveness == "UNKNOWN"
    assert event.confidence == "UNKNOWN"
    assert event.observation.to_value()["error_code"] == "malformed_pane_response"


def test_observation_preserves_unknown_for_malformed_process_response(
    tmp_path: Path,
) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.process_info_malformed = True

    event = backend.observe(lease)

    assert event.state == "UNKNOWN"
    assert event.liveness == "UNKNOWN"
    assert event.confidence == "UNKNOWN"
    assert (
        event.observation.to_value()["process_info_error_code"]
        == "malformed_process_info_response"
    )


def test_wait_event_returns_none_when_projection_does_not_change(tmp_path: Path) -> None:
    backend, _, lease, _ = _spawned_backend(tmp_path)
    initial = backend.observe(lease)

    event = backend.wait_event(
        lease,
        initial.event_id,
        datetime.now(UTC) + timedelta(milliseconds=10),
    )

    assert event is None


def test_wait_event_does_not_observe_after_deadline(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    call_count = len(fake.calls)

    event = backend.wait_event(
        lease,
        cursor=None,
        deadline=datetime.now(UTC) - timedelta(milliseconds=1),
    )

    assert event is None
    assert len(fake.calls) == call_count


def test_wait_event_caps_herdr_command_timeouts_to_remaining_deadline(
    tmp_path: Path,
) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.timeouts.clear()

    event = backend.wait_event(
        lease,
        cursor=None,
        deadline=datetime.now(UTC) + timedelta(milliseconds=50),
    )

    assert event is not None
    assert fake.timeouts
    assert all(timeout is not None and 0 < timeout <= 0.05 for timeout in fake.timeouts)


def test_native_event_requirement_blocks_while_bounded_polling_remains_available(
    tmp_path: Path,
) -> None:
    backend, _, lease, _ = _spawned_backend(tmp_path)
    registry = RuntimeBackendRegistry()
    registry.register(backend)

    decision = registry.decide(
        RuntimeRequirement(
            backend="herdr",
            interaction_mode="interactive",
            required_capabilities=("interactive", "native_events"),
            session_scope="persistent_subagent",
            observation_requirements=("NATIVE",),
        )
    )
    event = backend.wait_event(
        lease,
        cursor=None,
        deadline=datetime.now(UTC) + timedelta(milliseconds=10),
    )

    assert decision.status == "BLOCKED"
    assert "runtime_capability_unsupported:native_events" in decision.errors
    assert "runtime_requirement_declared_unsupported:native_events" in decision.errors
    assert event is not None
    assert event.source == "herdr"


def test_verified_native_transport_enables_native_event_capability(tmp_path: Path) -> None:
    transport = HerdrNativeEventTransport(
        session="default",
        socket_path=tmp_path / "herdr.sock",
        server_version="0.7.1",
        protocol=14,
        socket_device=1,
        socket_inode=1,
        socket_ctime_ns=1,
    )
    backend = HerdrRuntimeBackend(
        session="default",
        command_runner=FakeHerdr(),
        native_event_transport=transport,
    )

    capabilities = backend.capabilities()

    assert capabilities.native_events is True
    assert "native_events" not in capabilities.unsupported_requirements
    assert "native-0.7.1-protocol-14-binding-" in capabilities.version


def test_native_capability_hash_binds_exact_socket(tmp_path: Path) -> None:
    first = HerdrRuntimeBackend(
        session="default",
        command_runner=FakeHerdr(),
        native_event_transport=HerdrNativeEventTransport(
            session="default",
            socket_path=tmp_path / "first.sock",
            server_version="0.7.1",
            protocol=14,
            socket_device=1,
            socket_inode=1,
            socket_ctime_ns=1,
        ),
    )
    second = HerdrRuntimeBackend(
        session="default",
        command_runner=FakeHerdr(),
        native_event_transport=HerdrNativeEventTransport(
            session="default",
            socket_path=tmp_path / "second.sock",
            server_version="0.7.1",
            protocol=14,
            socket_device=1,
            socket_inode=2,
            socket_ctime_ns=2,
        ),
    )

    assert first.capabilities().sha256 != second.capabilities().sha256


def test_native_stream_failure_falls_back_to_bounded_polling(tmp_path: Path) -> None:
    transport = HerdrNativeEventTransport(
        session="default",
        socket_path=tmp_path / "missing.sock",
        server_version="0.7.1",
        protocol=14,
        socket_device=0,
        socket_inode=0,
        socket_ctime_ns=0,
    )
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(
        session="default",
        command_runner=fake,
        native_event_transport=transport,
        poll_interval_seconds=0.001,
    )
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    lease = _spawn_native_test_endpoint(backend, scope_id=scope["scope_id"], cwd=tmp_path)

    event = backend.wait_event(
        lease,
        cursor=None,
        deadline=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert event is not None
    assert event.observation.to_value()["native_event_fallback"] == {
        "code": "herdr_native_stream_failed",
        "polling_used": True,
    }


def test_native_stream_close_after_ack_falls_back_to_bounded_polling(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "herdr.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    server_errors: list[BaseException] = []

    def serve_ack_then_close() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                request = json.loads(connection.recv(8192).split(b"\n", 1)[0])
                connection.sendall(
                    json.dumps(
                        {
                            "id": request["id"],
                            "result": {"type": "subscription_started"},
                        }
                    ).encode()
                    + b"\n"
                )
        except BaseException as exc:
            server_errors.append(exc)

    thread = threading.Thread(target=serve_ack_then_close, daemon=True)
    thread.start()
    transport = HerdrNativeEventTransport(
        session="default",
        socket_path=socket_path,
        server_version="0.7.1",
        protocol=14,
        socket_device=socket_path.stat().st_dev,
        socket_inode=socket_path.stat().st_ino,
        socket_ctime_ns=socket_path.stat().st_ctime_ns,
    )
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(
        session="default",
        command_runner=fake,
        native_event_transport=transport,
        poll_interval_seconds=0.001,
    )
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    lease = _spawn_native_test_endpoint(backend, scope_id=scope["scope_id"], cwd=tmp_path)

    event = backend.wait_event(
        lease,
        cursor=None,
        deadline=datetime.now(UTC) + timedelta(seconds=1),
    )
    thread.join(timeout=2)
    listener.close()

    assert not thread.is_alive()
    assert not server_errors
    assert event is not None
    assert event.observation.to_value()["native_event_fallback"] == {
        "code": "herdr_native_stream_closed",
        "polling_used": True,
    }


def test_runtime_event_bridge_appends_real_herdr_polling_observation(
    tmp_path: Path,
) -> None:
    backend, _, endpoint, _ = _spawned_backend(tmp_path)
    with SqliteDagRunStore(tmp_path / "bridge.sqlite3") as store:
        lease = store.acquire_run(
            plan=_runtime_bridge_plan(tmp_path),
            run_id="run-1",
            owner_id="owner-a",
        )

        result = RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=backend,
            endpoint=endpoint,
            cursor=None,
            deadline=datetime.now(UTC) + timedelta(milliseconds=50),
        )

        assert result is not None and result.appended is True
        event = store.load_runtime_events("run-1", endpoint.sha256)[0][1]
        assert event.source == "herdr"
        assert event.observation.to_value()["transport"]["mode"] == "poll"
        assert store.run_outcome("run-1") == ("RUNNING", None)


def test_runtime_event_bridge_preserves_herdr_unknown_observation_failure(
    tmp_path: Path,
) -> None:
    backend, fake, endpoint, _ = _spawned_backend(tmp_path)
    fake.process_info_fails = True
    with SqliteDagRunStore(tmp_path / "bridge-unknown.sqlite3") as store:
        lease = store.acquire_run(
            plan=_runtime_bridge_plan(tmp_path),
            run_id="run-1",
            owner_id="owner-a",
        )

        result = RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=backend,
            endpoint=endpoint,
            cursor=None,
            deadline=datetime.now(UTC) + timedelta(milliseconds=50),
        )

        assert result is not None
        assert result.projection.state == "UNKNOWN"
        assert result.projection.liveness == "UNKNOWN"
        assert store.run_outcome("run-1") == ("RUNNING", None)


def test_wrong_session_and_stale_lease_are_rejected(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    wrong_session = HerdrRuntimeBackend(session="other", command_runner=fake)

    with pytest.raises(RuntimeError, match="endpoint_session_mismatch"):
        wrong_session.observe(lease)
    with pytest.raises(RuntimeError, match="endpoint_unknown"):
        backend.observe(replace(lease, endpoint_id="w1:p-stale"))


def test_terminate_requires_exact_authorization_and_post_verifies_absence(
    tmp_path: Path,
) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    unauthorized = herdr_cleanup_authorization(lease).to_value()
    unauthorized["owner"] = "other"

    with pytest.raises(RuntimeError, match="cleanup_unauthorized:owner"):
        backend.terminate(lease, FrozenJson.from_value(unauthorized))
    assert not any(call[3:5] == ["pane", "close"] for call in fake.calls)

    receipt = backend.terminate(lease, herdr_cleanup_authorization(lease)).to_value()

    assert receipt["status"] == "PASS"
    assert receipt["post_verified_absent"] is True
    assert backend.list_owned("run-1") == []
    close_calls = [call for call in fake.calls if call[3:5] == ["pane", "close"]]
    assert close_calls == [["herdr", "--session", "default", "pane", "close", "w1:p1"]]


def test_terminate_blocks_when_post_verify_failure_is_not_not_found(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.closed_pane_error_code = "server_unavailable"

    receipt = backend.terminate(lease, herdr_cleanup_authorization(lease)).to_value()

    assert receipt["status"] == "BLOCKED"
    assert receipt["post_verified_absent"] is False
    assert receipt["post_verify_error_code"] == "server_unavailable"
    assert backend.list_owned("run-1") == [lease]


def test_submit_timeout_is_indeterminate_and_not_retried(tmp_path: Path) -> None:
    fake = FakeHerdr()

    def timeout_on_send(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[3:5] == ["pane", "send-text"]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return fake(argv, **kwargs)

    backend, _, lease, work_hash = _spawned_backend(
        tmp_path, command_runner=timeout_on_send
    )
    work_order = herdr_runtime_work_order(
        work_order_sha256=work_hash,
        text="echo uncertain",
    )

    first = backend.submit(lease, work_order)
    second = backend.submit(lease, work_order)

    assert first == second
    assert first.delivery_status == "INDETERMINATE"
    assert first.text_delivery_count == 0
    assert first.errors == ("herdr_runtime_input_delivery_unverified",)


def test_spawn_validates_complete_request_before_agent_start(tmp_path: Path) -> None:
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(session="default", command_runner=fake)
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    request = herdr_runtime_spawn_request(
        run_id="run-1",
        plan_revision=canonical_sha256({"plan": 1}),
        dag_id="dag-1",
        node_id="worker",
        attempt_id="attempt-1",
        attempt_number=1,
        execution_token="token-1",
        scope_id=scope["scope_id"],
        command=("bash",),
        cwd=tmp_path,
        work_order_sha256=canonical_sha256({"work": 1}),
        goal_hash=canonical_sha256({"goal": 1}),
        owner="tau",
    ).to_value()
    request["goal_hash"] = "invalid"

    with pytest.raises(ValueError, match="complete sha256"):
        backend.spawn(FrozenJson.from_value(request))

    assert not any(call[3:5] == ["agent", "start"] for call in fake.calls)


def test_terminate_accepts_pane_already_verified_absent(tmp_path: Path) -> None:
    backend, fake, lease, _ = _spawned_backend(tmp_path)
    fake.closed_panes.add(lease.endpoint_id)
    fake.close_missing_returns_not_found = True

    receipt = backend.terminate(lease, herdr_cleanup_authorization(lease)).to_value()

    assert receipt["status"] == "PASS"
    assert receipt["action"] == "already_absent"
    assert receipt["close_returncode"] == 1
    assert receipt["post_verify_error_code"] == "pane_not_found"
    assert receipt["post_verified_absent"] is True
    assert backend.list_owned("run-1") == []


def _spawned_backend(
    tmp_path: Path,
    *,
    command_runner: Any | None = None,
) -> tuple[HerdrRuntimeBackend, FakeHerdr, Any, str]:
    fake = FakeHerdr()
    backend = HerdrRuntimeBackend(
        session="default",
        command_runner=command_runner or fake,
        poll_interval_seconds=0.001,
    )
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(
            run_id="run-1", owner="tau", cwd=tmp_path, label="runtime"
        )
    ).to_value()
    work_hash = canonical_sha256({"work": "bounded"})
    lease = backend.spawn(
        herdr_runtime_spawn_request(
            run_id="run-1",
            plan_revision=canonical_sha256({"plan": 1}),
            dag_id="dag-1",
            node_id="worker",
            attempt_id="attempt-1",
            attempt_number=1,
            execution_token="token-1",
            scope_id=scope["scope_id"],
            command=("bash",),
            cwd=tmp_path,
            work_order_sha256=work_hash,
            goal_hash=canonical_sha256({"goal": 1}),
            owner="tau",
            label="worker",
        )
    )
    return backend, fake, lease, work_hash


def _spawn_native_test_endpoint(
    backend: HerdrRuntimeBackend,
    *,
    scope_id: str,
    cwd: Path,
) -> Any:
    return backend.spawn(
        herdr_runtime_spawn_request(
            run_id="run-1",
            plan_revision=canonical_sha256({"plan": 1}),
            dag_id="dag-1",
            node_id="worker",
            attempt_id="attempt-1",
            attempt_number=1,
            execution_token="token-1",
            scope_id=scope_id,
            command=("bash",),
            cwd=cwd,
            work_order_sha256=canonical_sha256({"work": 1}),
            goal_hash=canonical_sha256({"goal": 1}),
            owner="tau",
        )
    )


def _runtime_bridge_plan(tmp_path: Path):
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "runtime-bridge",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "worker.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "bridge-dag.json",
    )
