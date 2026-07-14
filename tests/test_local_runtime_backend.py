"""Non-mocked local subprocess runtime adapter checks."""

from __future__ import annotations

import ast
import json
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import tau_coding.runtime_backends.local as local_runtime_module
from tau_coding.dag_runtime.model import FrozenJson
from tau_coding.runtime_backends import (
    LocalRuntimeBackend,
    LocalRuntimeExecutionRequest,
    RuntimeBackendRegistry,
    RuntimeRequirement,
    local_runtime_request,
)

ROOT = Path(__file__).resolve().parents[1]


def _request(
    tmp_path: Path, command: list[str], **overrides: object
) -> LocalRuntimeExecutionRequest:
    values = {
        "command": command,
        "run_id": "local-runtime-test",
        "plan_revision": "plan-v1",
        "dag_id": "local-runtime-test",
        "node_id": "worker",
        "attempt_id": "worker:attempt-001",
        "attempt_number": 1,
        "execution_token": "token-1",
        "work_order": {"command": command},
        "goal": {"goal_id": "local-runtime-test"},
        "cwd": tmp_path,
        "artifact_dir": tmp_path / "runtime",
    }
    values.update(overrides)
    return local_runtime_request(**values)  # type: ignore[arg-type]


def _spawn_payload(request: LocalRuntimeExecutionRequest) -> FrozenJson:
    return FrozenJson.from_value(
        {
            "run_id": request.run_id,
            "plan_revision": request.plan_revision,
            "dag_id": request.dag_id,
            "node_id": request.node_id,
            "attempt_id": request.attempt_id,
            "attempt_number": request.attempt_number,
            "execution_token": request.execution_token,
            "command": list(request.command),
            "cwd": str(request.cwd),
            "timeout_seconds": request.timeout_seconds,
            "work_order_sha256": request.work_order_sha256,
            "goal_hash": request.goal_hash,
            "artifact_dir": str(request.artifact_dir),
        }
    )


def test_local_runtime_executes_real_command_and_writes_normalized_artifacts(
    tmp_path: Path,
) -> None:
    backend = LocalRuntimeBackend()
    result = backend.execute(
        _request(
            tmp_path,
            [
                sys.executable,
                "-c",
                "import sys; print(sys.stdin.read().strip().upper())",
            ],
            stdin_text="bounded input\n",
            timeout_seconds=5.0,
        )
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "BOUNDED INPUT"
    assert result.endpoint_lease.backend == "local"
    assert result.submit_receipt.delivery_status == "CONFIRMED"
    assert result.submit_receipt.text_delivery_count == 1
    assert result.runtime_event.state == "EXITED"
    assert result.runtime_event.liveness == "DEAD"
    assert backend.list_owned("local-runtime-test") == [result.endpoint_lease]
    assert {Path(path).name for path in result.artifact_paths} == {
        "runtime-endpoint-lease.json",
        "runtime-submit-receipt.json",
        "runtime-event.json",
        "runtime-capture.json",
    }
    for path in result.artifact_paths:
        assert json.loads(Path(path).read_text(encoding="utf-8"))


def test_local_runtime_records_nonzero_and_timeout_without_false_pass(
    tmp_path: Path,
) -> None:
    crashed = LocalRuntimeBackend().execute(
        _request(tmp_path, [sys.executable, "-c", "raise SystemExit(7)"])
    )
    timed_out = LocalRuntimeBackend().execute(
        _request(
            tmp_path,
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout_seconds=0.05,
            artifact_dir=tmp_path / "timeout-runtime",
        )
    )

    assert crashed.returncode == 7
    assert crashed.runtime_event.state == "CRASHED"
    assert timed_out.returncode == 124
    assert timed_out.runtime_event.state == "BLOCKED"
    assert timed_out.capture.to_value()["timed_out"] is True


def test_local_runtime_preserves_explicit_124_and_130_exit_causes(tmp_path: Path) -> None:
    for returncode in (124, 130):
        result = LocalRuntimeBackend().execute(
            _request(tmp_path, [sys.executable, "-c", f"raise SystemExit({returncode})"])
        )

        assert result.returncode == returncode
        assert result.termination_cause == "exited"
        assert result.runtime_event.state == "CRASHED"
        assert result.capture.to_value()["timed_out"] is False
        assert result.capture.to_value()["cancelled"] is False


def test_local_runtime_serializes_duplicate_submissions(tmp_path: Path) -> None:
    backend = LocalRuntimeBackend()
    marker = tmp_path / "executions.txt"
    request = _request(
        tmp_path,
        [
            sys.executable,
            "-c",
            (
                "import pathlib,time; "
                f"p=pathlib.Path({str(marker)!r}); "
                "p.write_text((p.read_text() if p.exists() else '') + 'run\\n'); "
                "time.sleep(0.2)"
            ),
        ],
    )
    endpoint = backend._spawn_request(request)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(backend._submit_request(endpoint)))
        for _ in range(2)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert results[0] is results[1]
    assert marker.read_text(encoding="utf-8") == "run\n"


def test_local_runtime_rejects_duplicate_stable_endpoint_spawn(tmp_path: Path) -> None:
    backend = LocalRuntimeBackend()
    payload = _spawn_payload(_request(tmp_path, [sys.executable, "-c", "pass"]))

    backend.spawn(payload)

    try:
        backend.spawn(payload)
    except RuntimeError as exc:
        assert str(exc) == "local_runtime_endpoint_already_exists"
    else:
        raise AssertionError("duplicate stable endpoint spawn was accepted")


def test_protocol_spawn_owns_termination_handle(tmp_path: Path) -> None:
    backend = LocalRuntimeBackend()
    started = tmp_path / "started"
    request = _request(
        tmp_path,
        [
            sys.executable,
            "-c",
            f"import pathlib,time; pathlib.Path({str(started)!r}).touch(); time.sleep(10)",
        ],
    )
    endpoint = backend.spawn(_spawn_payload(request))
    receipts = []
    thread = threading.Thread(
        target=lambda: receipts.append(
            backend.submit(
                endpoint,
                FrozenJson.from_value({"work_order_sha256": endpoint.work_order_sha256}),
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 2
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    termination = backend.terminate(endpoint, FrozenJson.from_value({}))
    thread.join(timeout=2)

    assert termination.to_value()["action"] == "cancellation_requested"
    assert len(receipts) == 1
    capture = backend.capture(endpoint, lines=100).to_value()
    assert capture["cancelled"] is True
    assert capture["timed_out"] is False


def test_termination_before_submit_never_launches_process(tmp_path: Path) -> None:
    backend = LocalRuntimeBackend()
    marker = tmp_path / "forbidden-side-effect"
    request = _request(
        tmp_path,
        [sys.executable, "-c", f"import pathlib; pathlib.Path({str(marker)!r}).touch()"],
    )
    endpoint = backend.spawn(_spawn_payload(request))

    backend.terminate(endpoint, FrozenJson.from_value({}))
    receipt = backend.submit(
        endpoint,
        FrozenJson.from_value({"work_order_sha256": endpoint.work_order_sha256}),
    )

    assert not marker.exists()
    assert receipt.delivery_status == "BLOCKED"
    assert receipt.backend_acknowledgement.to_value()["process_started"] is False
    assert backend.observe(endpoint).state == "BLOCKED"


def test_local_runtime_observes_active_process(tmp_path: Path) -> None:
    backend = LocalRuntimeBackend()
    started = tmp_path / "running"
    request = _request(
        tmp_path,
        [
            sys.executable,
            "-c",
            f"import pathlib,time; pathlib.Path({str(started)!r}).touch(); time.sleep(0.3)",
        ],
    )
    endpoint = backend.spawn(_spawn_payload(request))
    thread = threading.Thread(
        target=lambda: backend.submit(
            endpoint,
            FrozenJson.from_value({"work_order_sha256": endpoint.work_order_sha256}),
        )
    )
    thread.start()
    deadline = time.monotonic() + 2
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    event = backend.observe(endpoint)
    thread.join(timeout=2)

    assert event.state == "RUNNING"
    assert event.liveness == "ALIVE"
    assert event.confidence == "PROCESS"


def test_local_runtime_zero_line_capture_redacts_output(tmp_path: Path) -> None:
    backend = LocalRuntimeBackend()
    result = backend.execute(
        _request(
            tmp_path,
            [sys.executable, "-c", "import sys; print('secret'); print('error', file=sys.stderr)"],
        )
    )

    capture = backend.capture(result.endpoint_lease, lines=0).to_value()

    assert capture["stdout"] == ""
    assert capture["stderr"] == ""


def test_local_runtime_launch_failure_is_terminal_and_receipted(tmp_path: Path) -> None:
    backend = LocalRuntimeBackend()
    result = backend.execute(
        _request(
            tmp_path,
            [str(tmp_path / "missing-executable")],
            artifact_dir=tmp_path / "launch-failure-runtime",
        )
    )

    assert result.returncode == 127
    assert result.termination_cause == "launch_failed"
    assert result.submit_receipt.delivery_status == "BLOCKED"
    assert result.runtime_event.state == "CRASHED"
    assert result.runtime_event.liveness == "DEAD"
    assert backend.observe(result.endpoint_lease).state == "CRASHED"
    assert (
        backend.wait_event(
            result.endpoint_lease,
            cursor=None,
            deadline=datetime.now(UTC),
        )
        == result.runtime_event
    )
    assert all(Path(path).is_file() for path in result.artifact_paths)


def test_local_runtime_finalization_failure_unblocks_duplicate_submitters(
    tmp_path: Path, monkeypatch
) -> None:
    backend = LocalRuntimeBackend()
    request = _request(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(0.1)"],
    )
    endpoint = backend._spawn_request(request)
    monkeypatch.setattr(
        local_runtime_module,
        "_write_runtime_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("persistence failed")),
    )
    errors = []

    def submit() -> None:
        try:
            backend._submit_request(endpoint)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == 2
    event = backend.observe(endpoint)
    assert event.state == "CRASHED"
    assert event.liveness == "DEAD"
    assert (
        backend.wait_event(
            endpoint,
            cursor=None,
            deadline=datetime.now(UTC),
        )
        == event
    )


def test_pre_submit_finalization_failure_unblocks_submitters(tmp_path: Path, monkeypatch) -> None:
    backend = LocalRuntimeBackend()
    endpoint = backend.spawn(_spawn_payload(_request(tmp_path, [sys.executable, "-c", "pass"])))
    monkeypatch.setattr(
        local_runtime_module,
        "_write_runtime_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("persistence failed")),
    )

    try:
        backend.terminate(endpoint, FrozenJson.from_value({}))
    except OSError as exc:
        assert str(exc) == "persistence failed"
    else:
        raise AssertionError("pre-submit persistence failure was hidden")

    errors = []

    def submit() -> None:
        try:
            backend._submit_request(endpoint)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=submit)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(errors) == 1
    event = backend.observe(endpoint)
    assert event.state == "CRASHED"
    assert event.liveness == "DEAD"


def test_timeout_does_not_claim_unconfirmed_stdin_delivery(tmp_path: Path) -> None:
    result = LocalRuntimeBackend().execute(
        _request(
            tmp_path,
            [sys.executable, "-c", "import time; time.sleep(2)"],
            stdin_text="x" * 1_000_000,
            timeout_seconds=0.05,
            artifact_dir=tmp_path / "stdin-timeout-runtime",
        )
    )

    assert result.returncode == 124
    assert result.submit_receipt.delivery_status == "INDETERMINATE"
    assert result.submit_receipt.text_delivery_count == 0
    assert result.submit_receipt.backend_acknowledgement.to_value()["stdin_delivery"] == (
        "indeterminate"
    )
    assert result.submit_receipt.errors == ("local_runtime_stdin_delivery_unverified",)


def test_slow_stdin_consumer_records_completed_delivery(tmp_path: Path) -> None:
    result = LocalRuntimeBackend().execute(
        _request(
            tmp_path,
            [
                sys.executable,
                "-c",
                "import sys,time; time.sleep(0.1); print(sys.stdin.read())",
            ],
            stdin_text="bounded input",
            timeout_seconds=2,
            artifact_dir=tmp_path / "slow-stdin-runtime",
        )
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "bounded input"
    assert result.submit_receipt.delivery_status == "CONFIRMED"
    assert result.submit_receipt.text_delivery_count == 1
    assert result.submit_receipt.backend_acknowledgement.to_value()["stdin_delivery"] == (
        "confirmed"
    )


def test_launch_failure_persistence_error_unblocks_duplicate_submitters(
    tmp_path: Path, monkeypatch
) -> None:
    backend = LocalRuntimeBackend()
    endpoint = backend._spawn_request(_request(tmp_path, [str(tmp_path / "missing-executable")]))
    monkeypatch.setattr(
        local_runtime_module,
        "_write_runtime_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("persistence failed")),
    )
    errors = []

    def submit() -> None:
        try:
            backend._submit_request(endpoint)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == 2
    event = backend.observe(endpoint)
    assert event.state == "CRASHED"
    assert event.liveness == "DEAD"


def test_local_runtime_wait_event_obeys_deadline_and_returns_terminal_event(
    tmp_path: Path,
) -> None:
    backend = LocalRuntimeBackend()
    request = _request(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
    )
    endpoint = backend.spawn(_spawn_payload(request))
    thread = threading.Thread(
        target=lambda: backend.submit(
            endpoint,
            FrozenJson.from_value({"work_order_sha256": endpoint.work_order_sha256}),
        )
    )
    thread.start()

    assert (
        backend.wait_event(
            endpoint,
            cursor=None,
            deadline=datetime.now(UTC) + timedelta(seconds=0.02),
        )
        is None
    )
    event = backend.wait_event(
        endpoint,
        cursor=None,
        deadline=datetime.now(UTC) + timedelta(seconds=2),
    )
    thread.join(timeout=2)

    assert event is not None
    assert event.state == "EXITED"
    assert (
        backend.wait_event(
            endpoint,
            cursor=event.event_id,
            deadline=datetime.now(UTC),
        )
        is None
    )


def test_local_runtime_registers_and_satisfies_compiled_requirement() -> None:
    registry = RuntimeBackendRegistry()
    registry.register(LocalRuntimeBackend())

    decision = registry.decide(
        RuntimeRequirement(
            backend="local",
            interaction_mode="one_shot",
            required_capabilities=("one_shot", "supports_working_directory"),
            session_scope="node_attempt",
            observation_requirements=("PROCESS",),
        )
    )

    assert decision.status == "PASS"
    assert decision.capabilities_sha256 is not None


def test_dag_execution_surfaces_do_not_launch_subprocesses_directly() -> None:
    surfaces = {
        "src/tau_coding/dag_runtime/scheduler.py": {"run_dag_plan"},
        "src/tau_coding/generic_dag.py": {"_run_command"},
        "src/tau_coding/handoff_dispatch.py": {"dispatch_agent_handoff_command_once"},
    }
    for relative_path, function_names in surfaces.items():
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        forbidden = []
        functions: list[ast.stmt] = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in function_names
        ]
        for node in ast.walk(ast.Module(body=functions, type_ignores=[])):
            if isinstance(node, ast.ImportFrom) and node.module == (
                "tau_coding.dag_runtime.subprocess_control"
            ):
                forbidden.append(node.lineno)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in {"Popen", "run"}
            ):
                forbidden.append(node.lineno)
        assert forbidden == [], f"direct subprocess launch in {relative_path}: {forbidden}"
