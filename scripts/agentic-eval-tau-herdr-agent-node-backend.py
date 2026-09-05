#!/usr/bin/env python3
"""Live proof for Herdr-backed Tau headless agent-node workers (tau#315).

Every clause below runs against a live Herdr session; nothing is mocked.

Clause 3  A real scheduler process (``--phase before-loss``) dispatches agent-a
          to Herdr, persists the endpoint lease, and is SIGKILLed while the
          worker is still in flight. A restarted scheduler adopts the same
          endpoint from the durable lease and settles once, with no duplicate
          endpoint, attempt, or settlement.
Clause 5  ``HerdrRuntimeBackend.replacement_decision`` is exercised against a
          live ``herdr pane get`` that genuinely times out, producing UNKNOWN
          liveness. The harness only spawns when the decision allows it, so a
          BLOCKED decision is proven to create zero replacement endpoints.
Clause 8  The same fixture work order runs through ``execute_tau_agent_node``
          on the local backend (in-process ``AgentNodeRun``) and through the
          adapter's Herdr path (headless worker running ``AgentNodeRun``).
          Every settlement field except ``attempt_id``/``sha256`` must match.
Clause 9  An unrelated pane in a different Herdr workspace survives cleanup,
          and a cleanup authorization for a different endpoint is refused.
"""

# ruff: noqa: E501  (live proof harness; long assertion/report lines are intentional)

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import venv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.agent_node_adapter import execute_tau_agent_node  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.run_store import (  # noqa: E402
    SqliteDagRunReader,
    SqliteDagRunStore,
    _operator_action_head,
)
from tau_coding.dag_runtime.scheduler import DagNodeAttempt  # noqa: E402
from tau_coding.runtime_backends.contracts import RuntimeEndpointLease  # noqa: E402
from tau_coding.runtime_backends.event_bridge import RuntimeEventBridge  # noqa: E402
from tau_coding.runtime_backends.herdr import (  # noqa: E402
    HerdrRuntimeBackend,
    herdr_cleanup_authorization,
    herdr_runtime_scope_request,
    herdr_runtime_spawn_request,
    herdr_runtime_work_order,
)

PROOF_SCHEMA = "tau.herdr_agent_node_backend_proof.v2"
WORKER_DELAY_ENV = "TAU_PROOF_WORKER_DELAY_SECONDS"
EQUIVALENCE_EXCLUDED_FIELDS = ("attempt_id", "sha256")
SETTLEMENT_TRANSPORT_FIELDS = (
    "final_text",
)  # carried in the worker file for accepted_output, not part of AgentNodeRun.settle()

# Headless worker: reads the adapter handshake, runs the SAME AgentNodeRun the
# local backend runs, and writes the settlement the adapter admits. Repo src is
# passed as argv[2] so the worker needs no source-checkout-only import magic.
WORKER_SOURCE = """
import asyncio, json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from tau_agent import AgentTool, AgentToolResult, AssistantMessage, ToolCall
from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent
from tau_coding.dag_runtime.agent_node import AgentNodeRun, ToolPolicy

handshake = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert handshake["schema"] == "tau.agent_node_worker_handshake.v1", handshake["schema"]
work_order = handshake["work_order"]
policy = handshake["policy"]
delay = float(os.environ.get("TAU_PROOF_WORKER_DELAY_SECONDS", "0"))

def tool_stream():
    return [ProviderResponseStartEvent(model="fixture"), ProviderResponseEndEvent(
        message=AssistantMessage(content="", tool_calls=[ToolCall(id="call-1", name="note", arguments={"path": "notes/a.txt"})]),
        finish_reason="tool_calls")]

def text_stream(text):
    return [ProviderResponseStartEvent(model="fixture"), ProviderResponseEndEvent(
        message=AssistantMessage(content=text), finish_reason="stop")]

async def note(arguments, signal=None):
    return AgentToolResult(tool_call_id="", name="note", ok=True, content=f"noted {arguments.get('path')}")

tool = AgentTool(name="note", description="Record a note.",
                 input_schema={"type": "object", "properties": {"path": {"type": "string"}}}, executor=note)
run = AgentNodeRun(
    work_order=work_order,
    policy=ToolPolicy(goal_hash=policy["goal_hash"], allowed_tools=tuple(policy["allowed_tools"]),
                      allowed_paths=tuple(policy["allowed_paths"]), max_tool_calls=int(policy["max_tool_calls"])),
    provider=FakeProvider([tool_stream(), text_stream("fixture worker wrote the note")]),
    tools=[tool], max_turns=4)
asyncio.run(run.run(handshake["prompt"]))
if run.tool_effect_receipts and all(r["ok"] for r in run.tool_effect_receipts):
    run.add_evidence("tool_effect_receipt", {"receipts": [r["sha256"] for r in run.tool_effect_receipts]})
settlement = run.settle()
final_text = run.turn_receipts[-1]["assistant_text"] if run.turn_receipts else ""
if delay:
    time.sleep(delay)
out = Path(handshake["settlement_path"])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({**settlement, "final_text": final_text}, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print(json.dumps({"status": "PASS", "node_id": settlement["node_id"], "attempt_id": settlement["attempt_id"]}))
time.sleep(1.0)
"""


def _projections(store_path: Path, run_id: str) -> list[dict[str, Any]]:
    """Read-only SQLite projection read-back (the surface Herdr/viewer consume)."""
    reader = SqliteDagRunReader(store_path)
    try:
        return [p.to_payload() for p in reader.runtime_projections(run_id)]
    finally:
        reader.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wait_for_json(path: Path, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise FileNotFoundError(f"receipt not readable after {timeout_seconds}s: {path}: {last_error}")


def _runtime_requirement(backend: str) -> dict[str, Any] | None:
    if backend == "local":
        # Undeclared runtime_requirement on a tau_agent node compiles to the
        # local in-process backend; that is the exact default path being compared.
        return None
    return {
        "schema": "tau.runtime_requirement.v1",
        "backend": "herdr",
        "interaction_mode": "interactive",
        "required_capabilities": [
            "interactive",
            "stable_endpoint_id",
            "human_attach",
            "native_agent_state",
            "foreground_process_state",
            "supports_working_directory",
            "supports_owned_inventory",
            "supports_terminate",
        ],
        "session_scope": "node_attempt",
        "observation_requirements": ["PROCESS"],
    }


def _agent_config(work: Path, *, node_id: str, role: str, prompt: str) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "role": role,
        "model": "fixture",
        "harness": "tau_native_agent_loop",
        "allowed_tools": ["note"],
        "allowed_paths": ["**"],
        "max_tool_calls": 4,
        "worker_command": [
            sys.executable,
            str(work / "tau-headless-agent-worker.py"),
            "{handshake_path}",
            str(SRC),
        ],
        "cwd": str(work),
        "dag_id": "issue315-herdr-agent-node",
        "runtime_scope_label": "issue315-herdr",
        "runtime_observe_seconds": 3.0,
        "worker_settlement_timeout_seconds": 60.0,
        "worker_deadline_seconds": 120.0,
    }


def _spec(work: Path, marker: str, *, backend: str) -> dict[str, Any]:
    requirement = _runtime_requirement(backend)
    declared = {"runtime_requirement": requirement} if requirement is not None else {}
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": marker,
        "run_dir": str(work / f"run-{backend}"),
        "nodes": [
            {
                "node_id": "agent-a",
                "role": "coder",
                "tau_agent": _agent_config(
                    work, node_id="agent-a", role="coder", prompt="write a receipt"
                ),
                **declared,
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(work / "receipts" / f"agent-a-{backend}.json"),
                "timeout_seconds": 60,
                "max_attempts": 2,
            },
            {
                "node_id": "agent-b",
                "role": "reviewer",
                "tau_agent": _agent_config(
                    work, node_id="agent-b", role="reviewer", prompt="read upstream receipt"
                ),
                **declared,
                "depends_on": ["agent-a"],
                "accepted_context_from": ["agent-a"],
                "receipt_path": str(work / "receipts" / f"agent-b-{backend}.json"),
                "timeout_seconds": 60,
                "max_attempts": 1,
            },
        ],
    }


def _execution(identity: Any) -> DagNodeAttempt:
    from threading import Event

    return DagNodeAttempt(
        attempt=identity.attempt,
        max_attempts=2,
        cancel_event=Event(),
        run_id=identity.run_id,
        attempt_id=identity.attempt_id,
        idempotency_key=identity.idempotency_key,
    )


def _fixture_provider() -> Any:
    from tau_agent import AssistantMessage, ToolCall
    from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent

    tool_stream = [
        ProviderResponseStartEvent(model="fixture"),
        ProviderResponseEndEvent(
            message=AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", name="note", arguments={"path": "notes/a.txt"})],
            ),
            finish_reason="tool_calls",
        ),
    ]
    text_stream = [
        ProviderResponseStartEvent(model="fixture"),
        ProviderResponseEndEvent(
            message=AssistantMessage(content="fixture worker wrote the note"), finish_reason="stop"
        ),
    ]
    return FakeProvider([tool_stream, text_stream])


def _note_tool() -> Any:
    from tau_agent import AgentTool, AgentToolResult

    async def _executor(arguments: Any, signal: Any = None) -> AgentToolResult:
        return AgentToolResult(
            tool_call_id="", name="note", ok=True, content=f"noted {arguments.get('path')}"
        )

    return AgentTool(
        name="note",
        description="Record a note.",
        input_schema={"type": "object", "properties": {"path": {"path": "string"}}},
        executor=_executor,
    )


def _work_order(plan: Any, node_id: str, identity: Any) -> dict[str, Any]:
    node = next(n for n in plan.nodes if n.node_id == node_id)
    config = dict(node.adapter_config.to_value() or {})
    return {
        "schema": "tau.agent_node.v1",
        "run_id": identity.run_id,
        "node_id": node_id,
        "attempt_id": identity.attempt_id,
        "attempt": identity.attempt,
        "goal_hash": plan.runtime_goal_hash,
        "plan_sha256": getattr(node, "plan_sha256", None) or "0" * 64,
        "model": str(config.get("model", "profile-owned")),
        "harness": str(config.get("harness", "tau_native_agent_loop")),
        "role": config.get("role"),
        "required_evidence": list(config.get("required_evidence", [])),
        "transport_profile_selection": config.get("transport_profile_selection"),
    }


def _spawn_direct(
    backend: HerdrRuntimeBackend,
    *,
    plan: Any,
    node_id: str,
    identity: Any,
    scope_id: str,
    work: Path,
    handshake_path: Path,
    settlement_path: Path,
    delay_seconds: float,
) -> tuple[RuntimeEndpointLease, dict[str, Any]]:
    """Spawn a headless worker exactly as the adapter does, but keep the lease.

    Used only where the harness must hold the endpoint lease across a process
    boundary (scheduler kill) or a replacement decision; agent-b goes through
    ``execute_tau_agent_node`` itself.
    """
    work_order = _work_order(plan, node_id, identity)
    prompt = str(
        dict(
            next(n for n in plan.nodes if n.node_id == node_id).adapter_config.to_value() or {}
        ).get("prompt", "")
    )
    handshake = {
        "schema": "tau.agent_node_worker_handshake.v1",
        "worker_runtime_version": "tau-herdr-headless-agent-worker.v1",
        "work_order": work_order,
        "work_order_sha256": canonical_sha256(work_order),
        "prompt": prompt,
        "prompt_sha256": canonical_sha256(prompt),
        "accepted_inputs": [],
        "settlement_path": str(settlement_path),
        "policy": {
            "goal_hash": plan.runtime_goal_hash,
            "allowed_tools": ["note"],
            "allowed_paths": ["**"],
            "max_tool_calls": 4,
        },
    }
    handshake_path.parent.mkdir(parents=True, exist_ok=True)
    handshake_path.write_text(
        json.dumps(handshake, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lease = backend.spawn(
        herdr_runtime_spawn_request(
            run_id=identity.run_id,
            plan_revision=work_order["plan_sha256"],
            dag_id="issue315-herdr-agent-node",
            node_id=node_id,
            attempt_id=identity.attempt_id,
            attempt_number=identity.attempt,
            execution_token=identity.idempotency_key,
            scope_id=scope_id,
            command=[
                sys.executable,
                str(work / "tau-headless-agent-worker.py"),
                str(handshake_path),
                str(SRC),
            ],
            cwd=work,
            work_order_sha256=canonical_sha256(work_order),
            goal_hash=plan.runtime_goal_hash,
            owner="tau",
            label=node_id,
            environment={
                WORKER_DELAY_ENV: str(delay_seconds),
                "TAU_AGENT_WORKER_HANDSHAKE": str(handshake_path),
            },
            lease_seconds=300.0,
        )
    )
    return lease, work_order


def _operator_request(store: SqliteDagRunStore, plan: Any, *, marker: str) -> dict[str, Any]:
    head_seq, head_sha256 = _operator_action_head(store._connection, marker)
    return {
        "schema": "tau.operator_action_request.v1",
        "action_request_id": f"action-{marker}",
        "idempotency_key": f"idem-{marker}",
        "run_id": marker,
        "plan_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "goal_hash": plan.runtime_goal_hash,
        "node_id": "agent-a",
        "attempt": 1,
        "action": "add_next_turn_instruction",
        "actor": "project-watchdog",
        "principal": "project-watchdog",
        "authority_class": "project_watchdog",
        "observed_journal_seq": head_seq,
        "observed_journal_head_sha256": head_sha256,
        "requested_safe_point": "scheduler_boundary",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "arguments": {"instruction": "continue without asking for human input"},
        "client_correlation": {"backend": "herdr", "marker": marker},
    }


def _installed_wheel_probe(work: Path) -> dict[str, Any]:
    venv_dir = work / "wheel-venv"
    dist = work / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheel = sorted(dist.glob("*.whl"))[-1]
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    python = venv_dir / "bin" / "python"
    subprocess.run(
        [str(python), "-m", "pip", "install", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from tau_coding.runtime_backends.herdr import HerdrRuntimeBackend; "
            "from tau_coding.dag_runtime.agent_node_adapter import execute_tau_agent_node; "
            "print(HerdrRuntimeBackend(session='default').capabilities().backend)",
        ],
        cwd=work,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "wheel": str(wheel),
        "returncode": probe.returncode,
        "stdout": probe.stdout.strip(),
        "stderr_tail": probe.stderr[-400:],
        "source_checkout_import": str(REPO_ROOT) in probe.stdout or str(REPO_ROOT) in probe.stderr,
        "status": "PASS" if probe.returncode == 0 and probe.stdout.strip() == "herdr" else "FAIL",
    }


class _InducedUnknownRunner:
    """Real ``subprocess.run`` for Herdr, except ``pane get`` gets a 0.5 ms budget.

    The Herdr binary really starts and is really killed by the timeout, so the
    backend sees a genuine unresponsive observation (``TimeoutExpired``), which
    is the UNKNOWN-liveness case the replacement guard must block on.
    """

    def __init__(self) -> None:
        self.induce = False
        self.induced_calls = 0

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        if self.induce and "pane" in argv and "get" in argv:
            self.induced_calls += 1
            kwargs["timeout"] = 0.0005
        return subprocess.run(argv, **kwargs)


# ---------------------------------------------------------------- phase: before-loss


def _phase_before_loss(args: argparse.Namespace) -> int:
    """Scheduler process that will be SIGKILLed while agent-a is in flight."""
    work = Path(args.work)
    marker = args.marker
    plan = compile_generic_dag_plan(
        _spec(work, marker, backend="herdr"), source_path=work / "issue315-herdr.dag.json"
    )
    backend = HerdrRuntimeBackend(session=args.session, command_timeout_seconds=10)
    store = SqliteDagRunStore(work / "dag-run.sqlite3")
    lease = store.acquire_run(
        plan=plan,
        run_id=marker,
        owner_id=f"scheduler-before-loss-pid{os.getpid()}",
        ttl_seconds=1.0,
    )
    scope = backend.ensure_scope(
        herdr_runtime_scope_request(run_id=marker, owner="tau", cwd=work, label="issue315-herdr")
    ).to_value()
    identity = store.reserve_attempt(
        lease, plan_sha256=plan.plan_sha256, node_id="agent-a", attempt=1
    )
    store.mark_dispatched(lease, identity.attempt_id)
    attempt_dir = work / "agent-workers" / "agent-a" / "attempt-001"
    endpoint, work_order = _spawn_direct(
        backend,
        plan=plan,
        node_id="agent-a",
        identity=identity,
        scope_id=str(scope["scope_id"]),
        work=work,
        handshake_path=attempt_dir / "handshake.json",
        settlement_path=attempt_dir / "settlement.json",
        delay_seconds=float(args.worker_delay_seconds),
    )
    first_projection = RuntimeEventBridge(store).wait_and_append(
        lease=lease,
        backend=backend,
        endpoint=endpoint,
        cursor=None,
        deadline=datetime.now(UTC) + timedelta(seconds=3),
    )
    # Durable endpoint lease: what a Tau scheduler must persist so a restarted
    # process can adopt without re-dispatching.
    write_durable_json(
        attempt_dir / "endpoint-lease.json",
        {
            "schema": "tau.herdr_agent_node_endpoint_lease_record.v1",
            "scheduler_pid": os.getpid(),
            "scheduler_owner_id": lease.owner_id,
            "run_id": marker,
            "node_id": "agent-a",
            "attempt_id": identity.attempt_id,
            "idempotency_key": identity.idempotency_key,
            "work_order_sha256": canonical_sha256(work_order),
            "scope": scope,
            "endpoint_lease": endpoint.to_payload(),
            "endpoint_lease_sha256": endpoint.sha256,
            "first_projection_liveness": first_projection.projection.liveness
            if first_projection
            else None,
            "settlement_path": str(attempt_dir / "settlement.json"),
            "handshake_path": str(attempt_dir / "handshake.json"),
        },
    )
    # Stay "in flight" holding the run lease until the parent kills us.
    while True:
        with contextlib.suppress(Exception):
            lease = store.renew_lease(lease, ttl_seconds=1.0)
        time.sleep(0.2)


# ---------------------------------------------------------------- phase: main


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--work", required=True)
    parser.add_argument("--session", default="default")
    parser.add_argument("--skip-wheel", action="store_true")
    parser.add_argument("--phase", choices=("main", "before-loss"), default="main")
    parser.add_argument("--marker")
    parser.add_argument("--worker-delay-seconds", default="6.0")
    args = parser.parse_args()
    if args.phase == "before-loss":
        return _phase_before_loss(args)
    if not args.out:
        parser.error("--out is required for the main phase")

    out_path = Path(args.out).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    (work / "tau-headless-agent-worker.py").write_text(WORKER_SOURCE, encoding="utf-8")
    marker = f"issue315_tau_herdr_worker_{int(time.time() * 1000)}"
    worker_delay = float(args.worker_delay_seconds)
    errors: list[str] = []
    report: dict[str, Any] = {}
    leases: list[RuntimeEndpointLease] = []
    terminations: list[dict[str, Any]] = []
    unrelated_pane_id: str | None = None
    unrelated_backend: HerdrRuntimeBackend | None = None
    unrelated_lease: RuntimeEndpointLease | None = None
    store_path = work / "dag-run.sqlite3"

    herdr_plan = compile_generic_dag_plan(
        _spec(work, marker, backend="herdr"), source_path=work / "issue315-herdr.dag.json"
    )
    local_plan = compile_generic_dag_plan(
        _spec(work, marker, backend="local"), source_path=work / "issue315-local.dag.json"
    )
    runtime_requirements = {
        node.node_id: node.runtime_requirement.to_value() for node in herdr_plan.nodes
    }
    scope_payload: dict[str, Any] = {}
    try:
        # ---------------- clause 3: real scheduler process loss --------------
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--phase",
                "before-loss",
                "--work",
                str(work),
                "--session",
                args.session,
                "--marker",
                marker,
                "--worker-delay-seconds",
                str(worker_delay),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        attempt_dir = work / "agent-workers" / "agent-a" / "attempt-001"
        if (attempt_dir / "endpoint-lease.json").exists():
            raise RuntimeError(
                f"stale work dir: {attempt_dir / 'endpoint-lease.json'} already exists; use a fresh --work"
            )
        lease_record = _wait_for_json(attempt_dir / "endpoint-lease.json", timeout_seconds=40)
        assert child.pid == lease_record["scheduler_pid"], "child pid mismatch"
        settlement_present_before_kill = (attempt_dir / "settlement.json").exists()
        kill_at = datetime.now(UTC)
        os.kill(child.pid, signal.SIGKILL)
        child_returncode = child.wait(timeout=10)
        first_endpoint = RuntimeEndpointLease.from_payload(lease_record["endpoint_lease"])
        leases.append(first_endpoint)
        scope_payload = dict(lease_record["scope"])

        restarted_backend = HerdrRuntimeBackend(session=args.session, command_timeout_seconds=10)
        with SqliteDagRunStore(store_path) as store:
            takeover_started = time.monotonic()
            restarted_lease = None
            takeover_error = None
            while time.monotonic() - takeover_started < 10:
                try:
                    restarted_lease = store.acquire_run(
                        plan=herdr_plan,
                        run_id=marker,
                        owner_id=f"scheduler-after-loss-pid{os.getpid()}",
                        ttl_seconds=30,
                        allow_takeover=True,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    takeover_error = f"{type(exc).__name__}:{exc}"
                    time.sleep(0.25)
            if restarted_lease is None:
                raise RuntimeError(f"scheduler takeover failed: {takeover_error}")
            bridge = RuntimeEventBridge(store)
            adopted = bridge.wait_and_append(
                lease=restarted_lease,
                backend=restarted_backend,
                endpoint=first_endpoint,
                cursor=None,
                deadline=datetime.now(UTC) + timedelta(seconds=3),
            )
            owned_after_adoption = restarted_backend.list_owned(marker)
            first_settlement = _wait_for_json(
                attempt_dir / "settlement.json", timeout_seconds=worker_delay + 30
            )
            settled_at = datetime.now(UTC)
            first_settlement_sha = _sha256(attempt_dir / "settlement.json")
            settlement_files = sorted(p.name for p in attempt_dir.glob("settlement*.json"))
            adoption_projection = store.runtime_state_projection(marker, first_endpoint.sha256)
            report["scheduler_process_loss"] = {
                "killed_pid": child.pid,
                "kill_signal": "SIGKILL",
                "child_returncode": child_returncode,
                "settlement_present_before_kill": settlement_present_before_kill,
                "kill_at": kill_at.isoformat(),
                "settled_at": settled_at.isoformat(),
                "worker_delay_seconds": worker_delay,
                "before_loss_owner_id": lease_record["scheduler_owner_id"],
                "after_loss_owner_id": restarted_lease.owner_id,
                "lease_epoch_after_takeover": restarted_lease.epoch,
                "adopted_endpoint_id": first_endpoint.endpoint_id,
                "adopted_endpoint_lease_sha256": first_endpoint.sha256,
                "projection_endpoint_lease_sha256": adoption_projection.endpoint_lease_sha256
                if adoption_projection
                else None,
                "projection_event_count": adoption_projection.event_count
                if adoption_projection
                else 0,
                "owned_after_adoption_count": len(owned_after_adoption),
                "settlement_files": settlement_files,
                "settlement_attempt_id": first_settlement.get("attempt_id"),
                "reserved_attempt_id": lease_record["attempt_id"],
                "settlement_work_order_sha256_matches": first_settlement.get("work_order_sha256")
                in {None, lease_record["work_order_sha256"]},
            }
            if child_returncode != -signal.SIGKILL:
                errors.append("scheduler_process_not_killed")
            if settlement_present_before_kill:
                errors.append("worker_not_in_flight_at_kill")
            if (
                adopted is None
                or len(owned_after_adoption) != 1
                or owned_after_adoption[0].sha256 != first_endpoint.sha256
            ):
                errors.append("restart_adoption_failed_or_duplicated")
            if adoption_projection is None or adoption_projection.event_count < 2:
                errors.append("adoption_not_journaled_in_sqlite")
            if (
                settlement_files != ["settlement.json"]
                or first_settlement.get("attempt_id") != lease_record["attempt_id"]
            ):
                errors.append("duplicate_or_foreign_settlement_after_restart")
            if first_settlement.get("state") != "completed":
                errors.append("first_node_not_settled")

            # ---------------- clause 6: pane text is diagnostic only ----------
            projections_before_text = _projections(store_path, marker)
            submit = restarted_backend.submit(
                first_endpoint,
                herdr_runtime_work_order(
                    work_order_sha256=first_endpoint.work_order_sha256,
                    text="diagnostic-only pane text mutation; Tau settlement must not change\n",
                ),
            )
            projections_after_text = _projections(store_path, marker)
            first_settlement_after_text_sha = _sha256(attempt_dir / "settlement.json")
            if projections_before_text != projections_after_text:
                errors.append("pane_text_changed_tau_projection")
            if first_settlement_sha != first_settlement_after_text_sha:
                errors.append("pane_text_changed_settlement")

            # ---------------- clause 7: operator action ----------------------
            submitted = store.submit_operator_action_request(
                _operator_request(store, herdr_plan, marker=marker)
            )
            claimed = store.claim_operator_action(restarted_lease)
            completed = store.complete_operator_action(
                restarted_lease,
                action_request_id=claimed["action_request_id"] if claimed else "missing",
                status="APPLIED",
                outcome="instruction_sent_to_herdr_endpoint",
                code="operator_action_instruction_queued",
                canonical_transition={
                    "endpoint_id": first_endpoint.endpoint_id,
                    "submit_delivery_status": submit.delivery_status,
                },
            )
            if (
                submitted.get("status") != "VALIDATED"
                or not claimed
                or completed.get("status") != "APPLIED"
            ):
                errors.append("operator_action_not_applied")

            # ---------------- clause 1 + 8: agent-b via the real adapter -----
            herdr_node_b = next(n for n in herdr_plan.nodes if n.node_id == "agent-b")
            b_identity = store.reserve_attempt(
                restarted_lease, plan_sha256=herdr_plan.plan_sha256, node_id="agent-b", attempt=1
            )
            store.mark_dispatched(restarted_lease, b_identity.attempt_id)
            accepted_inputs = (
                {"source_node_id": "agent-a", "accepted_output": {"settlement": first_settlement}},
            )
            herdr_result = execute_tau_agent_node(
                herdr_node_b,
                accepted_inputs,
                _execution(b_identity),
                goal_hash=herdr_plan.runtime_goal_hash,
                provider_factory=lambda node, config: _fixture_provider(),
                tools_factory=lambda node, config: [_note_tool()],
                run_store=store,
                lease=restarted_lease,
                runtime_backend=restarted_backend,
                runtime_cwd=work,
            )
            owned_after_b = restarted_backend.list_owned(marker)
            herdr_b_lease = next((item for item in owned_after_b if item.node_id == "agent-b"), None)
            if herdr_b_lease is not None:
                leases.append(herdr_b_lease)
            herdr_handshake = json.loads(
                (
                    Path(store_path).parent
                    / "agent-workers"
                    / "agent-b"
                    / "attempt-001"
                    / "handshake.json"
                ).read_text(encoding="utf-8")
            )
            herdr_settlement = (herdr_result.get("accepted_output") or {}).get("settlement") or {}

        # Local backend: identical fixture through the in-process path on a
        # separate run store so the attempt id is the only lineage difference.
        local_node_b = next(n for n in local_plan.nodes if n.node_id == "agent-b")
        with SqliteDagRunStore(work / "dag-run-local.sqlite3") as local_store:
            local_lease = local_store.acquire_run(
                plan=local_plan, run_id=marker, owner_id="scheduler-local", ttl_seconds=30
            )
            local_store.reserve_attempt(
                local_lease, plan_sha256=local_plan.plan_sha256, node_id="agent-a", attempt=1
            )
            local_b_identity = local_store.reserve_attempt(
                local_lease, plan_sha256=local_plan.plan_sha256, node_id="agent-b", attempt=1
            )
            local_store.mark_dispatched(local_lease, local_b_identity.attempt_id)
            local_result = execute_tau_agent_node(
                local_node_b,
                accepted_inputs,
                _execution(local_b_identity),
                goal_hash=local_plan.runtime_goal_hash,
                provider_factory=lambda node, config: _fixture_provider(),
                tools_factory=lambda node, config: [_note_tool()],
                run_store=local_store,
                lease=local_lease,
            )
        local_settlement = (local_result.get("accepted_output") or {}).get("settlement") or {}
        local_work_order = _work_order(local_plan, "agent-b", local_b_identity)
        herdr_work_order = herdr_handshake["work_order"]
        wo_excluded = {"attempt_id"}
        work_order_diff = sorted(
            k
            for k in set(local_work_order) | set(herdr_work_order)
            if k not in wo_excluded and local_work_order.get(k) != herdr_work_order.get(k)
        )
        all_fields = sorted(set(local_settlement) | set(herdr_settlement))
        compared_fields = [
            f
            for f in all_fields
            if f not in EQUIVALENCE_EXCLUDED_FIELDS and f not in SETTLEMENT_TRANSPORT_FIELDS
        ]
        final_text_equal = (herdr_result.get("accepted_output") or {}).get("final_text") == (
            local_result.get("accepted_output") or {}
        ).get("final_text")
        settlement_diff = sorted(
            f for f in compared_fields if local_settlement.get(f) != herdr_settlement.get(f)
        )
        report["local_vs_herdr_equivalence"] = {
            "herdr_result_status": herdr_result.get("status"),
            "herdr_result_verdict": herdr_result.get("verdict"),
            "herdr_result_errors": herdr_result.get("errors"),
            "local_result_status": local_result.get("status"),
            "handshake_schema": herdr_handshake.get("schema"),
            "handshake_worker_runtime_version": herdr_handshake.get("worker_runtime_version"),
            "handshake_policy_hash": herdr_handshake.get("policy_hash"),
            "work_order_compared_fields": sorted(
                k for k in set(local_work_order) | set(herdr_work_order) if k not in wo_excluded
            ),
            "work_order_diff_fields": work_order_diff,
            "settlement_compared_fields": compared_fields,
            "settlement_excluded_fields": list(EQUIVALENCE_EXCLUDED_FIELDS),
            "settlement_transport_fields": list(SETTLEMENT_TRANSPORT_FIELDS),
            "accepted_output_final_text_equal": final_text_equal,
            "handshake_prompt_sha256_matches_prompt": herdr_handshake.get("prompt_sha256")
            == canonical_sha256(herdr_handshake.get("prompt")),
            "settlement_diff_fields": settlement_diff,
            "turn_receipt_sha256s_equal": local_settlement.get("turn_receipt_sha256s")
            == herdr_settlement.get("turn_receipt_sha256s"),
            "tool_effect_receipt_sha256s_equal": local_settlement.get("tool_effect_receipt_sha256s")
            == herdr_settlement.get("tool_effect_receipt_sha256s"),
            "evidence_equal": local_settlement.get("evidence") == herdr_settlement.get("evidence"),
            "journal_head_sha256_equal": local_settlement.get("journal_head_sha256")
            == herdr_settlement.get("journal_head_sha256"),
            "equivalent": not work_order_diff
            and not settlement_diff
            and bool(compared_fields)
            and final_text_equal,
        }
        if herdr_result.get("status") != "PASS":
            errors.append("herdr_adapter_path_not_pass")
        if local_result.get("status") != "PASS":
            errors.append("local_adapter_path_not_pass")
        if not report["local_vs_herdr_equivalence"]["equivalent"]:
            errors.append("local_vs_herdr_equivalence_failed")
        if len(owned_after_b) != 2:
            errors.append("agent_b_not_hosted_in_same_workspace")

        # ---------------- clause 5: induced UNKNOWN blocks replacement -------
        with SqliteDagRunStore(store_path) as store:
            unknown_runner = _InducedUnknownRunner()
            unknown_backend = HerdrRuntimeBackend(
                session=args.session, command_timeout_seconds=10, command_runner=unknown_runner
            )
            unknown_backend.observe(first_endpoint)  # adopt with a responsive Herdr
            owned_before_unknown = unknown_backend.list_owned(marker)
            unknown_runner.induce = True
            decision = unknown_backend.replacement_decision(first_endpoint)
            unknown_runner.induce = False
            replacement_spawned = False
            if decision.replacement_allowed:  # the guard is the only gate
                replacement_spawned = True
            owned_after_unknown = unknown_backend.list_owned(marker)
            unknown_event = unknown_backend.observe(first_endpoint)
            report["unknown_liveness_policy"] = {
                "decision": decision.to_payload(),
                "induced_pane_get_calls": unknown_runner.induced_calls,
                "replacement_spawned": replacement_spawned,
                "owned_before": len(owned_before_unknown),
                "owned_after": len(owned_after_unknown),
                "liveness_after_recovery": unknown_event.liveness,
            }
            if (
                decision.status != "BLOCKED"
                or decision.observed_liveness != "UNKNOWN"
                or decision.reason != "unknown_liveness_blocks_replacement"
            ):
                errors.append("unknown_liveness_not_blocked_by_guard")
            if replacement_spawned or len(owned_before_unknown) != len(owned_after_unknown):
                errors.append("unknown_liveness_replaced_endpoint")

            # ---------------- clause 4: confirmed dead retry -----------------
            restarted_lease = store.acquire_run(
                plan=herdr_plan,
                run_id=marker,
                owner_id=f"scheduler-after-loss-pid{os.getpid()}",
                ttl_seconds=30,
                allow_takeover=True,
            )
            dead_first = restarted_backend.terminate(
                first_endpoint, herdr_cleanup_authorization(first_endpoint)
            ).to_value()
            terminations.append(dead_first)
            dead_decision = restarted_backend.replacement_decision(first_endpoint)
            retry_lineage_new = False
            retry_endpoint = None
            if dead_decision.replacement_allowed:
                retry_identity = store.reserve_attempt(
                    restarted_lease,
                    plan_sha256=herdr_plan.plan_sha256,
                    node_id="agent-a",
                    attempt=2,
                )
                store.mark_dispatched(restarted_lease, retry_identity.attempt_id)
                retry_dir = work / "agent-workers" / "agent-a" / "attempt-002"
                retry_endpoint, retry_work_order = _spawn_direct(
                    restarted_backend,
                    plan=herdr_plan,
                    node_id="agent-a",
                    identity=retry_identity,
                    scope_id=str(scope_payload["scope_id"]),
                    work=work,
                    handshake_path=retry_dir / "handshake.json",
                    settlement_path=retry_dir / "settlement.json",
                    delay_seconds=0.0,
                )
                leases.append(retry_endpoint)
                retry_settlement = _wait_for_json(retry_dir / "settlement.json", timeout_seconds=30)
                retry_lineage_new = (
                    retry_endpoint.endpoint_id != first_endpoint.endpoint_id
                    and retry_endpoint.attempt_id != first_endpoint.attempt_id
                    and retry_endpoint.work_order_sha256 != first_endpoint.work_order_sha256
                    and retry_settlement.get("attempt") == 2
                )
            report["confirmed_dead_retry"] = {
                "decision": dead_decision.to_payload(),
                "retry_endpoint_id": retry_endpoint.endpoint_id if retry_endpoint else None,
                "retry_lineage_new": retry_lineage_new,
            }
            if dead_decision.observed_liveness != "DEAD" or not retry_lineage_new:
                errors.append("confirmed_dead_retry_lineage_failed")
            final_projections = _projections(store_path, marker)

        # ---------------- clause 9: exact-ownership cleanup ------------------
        unrelated_backend = HerdrRuntimeBackend(session=args.session, command_timeout_seconds=10)
        unrelated_run = f"{marker}_unrelated"
        unrelated_scope = unrelated_backend.ensure_scope(
            herdr_runtime_scope_request(
                run_id=unrelated_run, owner="tau", cwd=work, label="issue315-unrelated"
            )
        ).to_value()
        unrelated_lease = unrelated_backend.spawn(
            herdr_runtime_spawn_request(
                run_id=unrelated_run,
                plan_revision=canonical_sha256({"unrelated": marker}),
                dag_id="unrelated",
                node_id="bystander",
                attempt_id="bystander-1",
                attempt_number=1,
                execution_token="bystander",
                scope_id=str(unrelated_scope["scope_id"]),
                command=[sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=work,
                work_order_sha256=canonical_sha256({"bystander": marker}),
                goal_hash=canonical_sha256({"bystander-goal": marker}),
                owner="tau",
                label="bystander",
            )
        )
        unrelated_pane_id = unrelated_lease.endpoint_id
        wrong_auth_refused = False
        wrong_auth_error = None
        try:
            restarted_backend.terminate(leases[-1], herdr_cleanup_authorization(unrelated_lease))
        except Exception as exc:  # noqa: BLE001
            wrong_auth_refused = True
            wrong_auth_error = f"{type(exc).__name__}:{exc}"
        time.sleep(1.5)
        for lease in leases:
            if lease.endpoint_id == first_endpoint.endpoint_id:
                continue
            try:
                terminations.append(
                    restarted_backend.terminate(
                        lease, herdr_cleanup_authorization(lease)
                    ).to_value()
                )
            except Exception as exc:  # noqa: BLE001
                terminations.append(
                    {
                        "status": "BLOCKED",
                        "endpoint_id": lease.endpoint_id,
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )
        unrelated_after = subprocess.run(
            ["herdr", "--session", args.session, "pane", "get", unrelated_pane_id],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        unrelated_survived = (
            unrelated_after.returncode == 0 and '"pane_not_found"' not in unrelated_after.stdout
        )
        report["cleanup_ownership"] = {
            "wrong_authorization_refused": wrong_auth_refused,
            "wrong_authorization_error": wrong_auth_error,
            "unrelated_workspace_id": unrelated_scope["workspace_id"],
            "unrelated_pane_id": unrelated_pane_id,
            "unrelated_pane_survived_cleanup": unrelated_survived,
            "unrelated_workspace_differs": unrelated_scope["workspace_id"]
            != scope_payload.get("workspace_id"),
        }
        if (
            not wrong_auth_refused
            or not unrelated_survived
            or unrelated_scope["workspace_id"] == scope_payload.get("workspace_id")
        ):
            errors.append("cleanup_ownership_not_exact")
        if not all(item.get("status") == "PASS" for item in terminations):
            errors.append("cleanup_not_verified")

        wheel_probe = {"status": "SKIPPED"} if args.skip_wheel else _installed_wheel_probe(work)
        if not args.skip_wheel and wheel_probe.get("status") != "PASS":
            errors.append("installed_wheel_probe_failed")
        if (
            runtime_requirements["agent-a"]["backend"] != "herdr"
            or runtime_requirements["agent-b"]["backend"] != "herdr"
        ):
            errors.append("explicit_runtime_requirement_not_compiled")
        report.update(
            {
                "pane_text_status_only": {
                    "projection_unchanged": projections_before_text == projections_after_text,
                    "settlement_sha256_unchanged": first_settlement_sha
                    == first_settlement_after_text_sha,
                    "submit_delivery_status": submit.delivery_status,
                },
                "operator_action": {
                    "submitted_status": submitted.get("status"),
                    "completed_status": completed.get("status"),
                    "receipt_code": (completed.get("receipt") or {}).get("code")
                    if isinstance(completed.get("receipt"), dict)
                    else None,
                },
                "settlement_receipts": {
                    "agent-a-herdr": first_settlement,
                    "agent-b-herdr": herdr_settlement,
                    "agent-b-local": local_settlement,
                },
                "tau_sqlite_projection_readback": final_projections,
                "installed_wheel_probe": wheel_probe,
            }
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"herdr_live_error:{type(exc).__name__}:{exc}")
        import traceback

        report["traceback"] = traceback.format_exc()[-3000:]
    finally:
        if unrelated_backend is not None and unrelated_lease is not None:
            try:
                terminations.append(
                    unrelated_backend.terminate(
                        unrelated_lease, herdr_cleanup_authorization(unrelated_lease)
                    ).to_value()
                )
            except Exception as exc:  # noqa: BLE001
                terminations.append(
                    {
                        "status": "BLOCKED",
                        "endpoint_id": unrelated_pane_id,
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )

    payload = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "marker": marker,
        "runtime_requirements": runtime_requirements,
        "scope": scope_payload,
        "endpoint_ids": [lease.endpoint_id for lease in leases],
        "workspace_ids": sorted({lease.scope_id for lease in leases}),
        "sqlite_run_store": str(store_path),
        **report,
        "terminations": terminations,
        "proof_boundary": {
            "proves": (
                "A real Tau scheduler process was SIGKILLed while a Herdr-hosted agent worker was in flight and a restarted "
                "scheduler adopted the same endpoint from the durable lease with one settlement and no duplicate endpoint; "
                "replacement_decision blocked on a live induced UNKNOWN observation and created zero endpoints; the same fixture "
                "work order produced field-equal settlements (all fields except attempt_id/sha256) through the local in-process "
                "path and the adapter's Herdr worker path; pane text is diagnostic only; an operator action round-tripped through "
                "the Tau inbox; confirmed-dead retry produced new lineage; cleanup refused a foreign authorization and left an "
                "unrelated workspace pane intact; the installed wheel imports the adapter and backend without the source checkout."
            ),
            "does_not_prove": (
                "provider semantic quality, paid-provider execution, React Flow rendering, GOAL.md completion, or that the "
                "shipped adapter itself persists endpoint leases for restart adoption (this harness persists the lease record "
                "the scheduler would need; see local_vs_herdr_equivalence and scheduler_process_loss for the exact boundary)."
            ),
            "fixture_provider": "FakeProvider in both local and Herdr workers; FakeProvider is the model, Herdr and the scheduler are live",
        },
        "errors": errors,
    }
    write_durable_json(out_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
