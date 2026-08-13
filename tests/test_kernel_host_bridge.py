from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.attempt_result import admit_dag_attempt_result
from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends.contracts import RuntimeEndpointLease
from tau_coding.runtime_backends.kernel import create_python_workspace
from tau_coding.runtime_backends.kernel_contracts import (
    PYTHON_KERNEL_FEATURES,
    PythonWorkspaceRequest,
)
from tau_coding.runtime_backends.kernel_host_bridge import (
    HOST_CALL_KINDS,
    HostBridgeContext,
    KernelHostBridge,
    PythonHostCallIntent,
)

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


def test_source_read_and_code_search_are_bounded_host_operations(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src/example.py"
    source.write_text("class RuntimeEndpointLease:\n    pass\n", encoding="utf-8")
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-1"))

    read = bridge.dispatch(_intent("source.read", execution_id="exec-1", path="src/example.py"))
    search = bridge.dispatch(
        _intent("code.search", execution_id="exec-1", query="RuntimeEndpointLease", roots=["src"])
    )

    assert read.admission["admitted"] is True
    assert read.effect["handler_executed"] is True
    assert read.result["value"]["path"] == "src/example.py"
    assert read.result["tau_admission_status"] == "not_admitted"
    assert search.result["value"]["count"] == 1
    assert search.result["value"]["results"][0]["path"] == "src/example.py"


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.txt"])
def test_source_read_rejects_absolute_and_parent_paths(tmp_path: Path, path: str) -> None:
    (tmp_path / "src").mkdir()
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-path"))

    bundle = bridge.dispatch(_intent("source.read", execution_id="exec-path", path=path))

    assert bundle.admission["admitted"] is True
    assert bundle.result["status"] == "BLOCKED"
    assert bundle.effect["artifacts"] == []
    assert "path_outside_grant" in bundle.result["errors"]


def test_source_read_rejects_symlink_escape(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "src/link.txt").symlink_to(outside)
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-symlink"))

    bundle = bridge.dispatch(
        _intent("source.read", execution_id="exec-symlink", path="src/link.txt")
    )

    assert bundle.effect["artifacts"] == []
    assert bundle.result["errors"] == ["symlink_escape_denied"]


def test_stale_generation_and_cancelled_execution_reject_before_handler(tmp_path: Path) -> None:
    bridge = KernelHostBridge(
        _context(
            tmp_path,
            execution_id="exec-current",
            cancelled_executions=frozenset({"exec-current"}),
        )
    )

    stale = bridge.dispatch(
        _intent(
            "artifact.put",
            generation_id="old-generation",
            execution_id="exec-current",
            name="x.json",
            value={},
        )
    )
    cancelled = bridge.dispatch(
        _intent("artifact.put", execution_id="exec-current", name="x.json", value={})
    )

    assert stale.request is None
    assert stale.effect["handler_executed"] is False
    assert "binding_mismatch:generation_id" in stale.admission["errors"]
    assert cancelled.request is None
    assert cancelled.effect["handler_executed"] is False
    assert "execution_cancelled" in cancelled.admission["errors"]
    assert not list((tmp_path / "artifacts").glob("**/*"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("endpoint_lease_sha256", "sha256:" + "1" * 64),
        ("execution_token", "wrong-token"),
        ("goal_hash", "sha256:" + "2" * 64),
        ("policy_sha256", "sha256:" + "3" * 64),
        ("data_boundary_sha256", "sha256:" + "4" * 64),
        ("worktree_sha256", "sha256:" + "5" * 64),
    ],
)
def test_binding_mutations_reject_before_handler(tmp_path: Path, field: str, value: str) -> None:
    intent = _intent("progress.emit", execution_id="exec-bind", message="hi")
    payload = intent.to_payload()
    payload["binding"][field] = value

    bundle = KernelHostBridge(_context(tmp_path, execution_id="exec-bind")).dispatch(payload)

    assert bundle.request is None
    assert bundle.effect["handler_executed"] is False
    assert f"binding_mismatch:{field}" in bundle.admission["errors"]


def test_graph_query_allows_only_named_governed_profiles_and_budgets(tmp_path: Path) -> None:
    calls = []

    def memory_query(
        profile: str, params: dict[str, Any], budget: dict[str, int]
    ) -> dict[str, Any]:
        calls.append((profile, params, budget))
        return {
            "profile": profile,
            "service_status": "OK",
            "items": [{"symbol": params["symbol_id"]}],
        }

    bridge = KernelHostBridge(
        _context(tmp_path, execution_id="exec-graph"), memory_query=memory_query
    )

    ok = bridge.dispatch(
        _intent(
            "graph.query",
            execution_id="exec-graph",
            profile="symbol-neighborhood",
            params={"symbol_id": "RuntimeEndpointLease", "depth": 2, "limit": 3},
        )
    )
    undeclared = bridge.dispatch(
        _intent(
            "graph.query",
            execution_id="exec-graph",
            profile="raw-aql",
            params={"aql": "FOR x IN y"},
        )
    )
    excessive = bridge.dispatch(
        _intent(
            "graph.query",
            execution_id="exec-graph",
            profile="symbol-neighborhood",
            params={"symbol_id": "x", "depth": 99},
        )
    )

    assert ok.result["status"] == "OK"
    assert calls[0][0] == "symbol-neighborhood"
    assert undeclared.effect["handler_executed"] is True
    assert undeclared.result["status"] == "BLOCKED"
    assert "graph_profile_not_allowed" in undeclared.result["errors"]
    assert excessive.effect["handler_executed"] is False
    assert excessive.result["errors"] == ["depth_outside_budget"]


def test_oversize_source_projection_is_explicit_and_artifact_is_complete(tmp_path: Path) -> None:
    source = tmp_path / "large.py"
    source.write_text(
        "\n".join([f"print({index!r}, {'x' * 120!r})" for index in range(600)]),
        encoding="utf-8",
    )
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-large"))

    bundle = bridge.dispatch(
        _intent("source.read", execution_id="exec-large", path="large.py", end_line=501)
    )

    assert bundle.result["value"]["truncated"] is True
    artifact = bundle.result["value"]["artifact"]
    assert Path(artifact["path"]).read_text(encoding="utf-8").startswith("print(0,")


def test_duplicate_artifact_put_uses_one_content_identity(tmp_path: Path) -> None:
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-artifact"))
    intent = _intent(
        "artifact.put", execution_id="exec-artifact", name="analysis.json", value={"ok": True}
    )

    first = bridge.dispatch(intent)
    second = bridge.dispatch(intent)

    assert (
        first.result["value"]["artifact"]["sha256"] == second.result["value"]["artifact"]["sha256"]
    )
    assert first.result["value"]["artifact"]["path"] == second.result["value"]["artifact"]["path"]


def test_evidence_emit_is_candidate_only_even_with_pass_and_high_confidence(tmp_path: Path) -> None:
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-evidence"))

    bundle = bridge.dispatch(
        _intent(
            "evidence.emit",
            execution_id="exec-evidence",
            claim="PASS because the kernel printed PASS",
            support_refs=[],
            confidence=0.99,
        )
    )

    value = bundle.result["value"]
    assert value["candidate_only"] is True
    assert value["accepted"] is False
    assert value["confidence"] == 0.49
    assert "pass_claim_downgraded_to_candidate" in value["warnings"]
    assert bundle.result["tau_admission_status"] == "not_admitted"


def test_undeclared_ambient_authority_kinds_are_rejected_before_handler(tmp_path: Path) -> None:
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-deny"))

    for kind in (
        "network.fetch",
        "github.issue",
        "provider.call",
        "memory.raw_socket",
        "database.aql",
    ):
        bundle = bridge.dispatch(_intent(kind, execution_id="exec-deny", target="http://127.0.0.1"))
        assert bundle.request is None
        assert bundle.effect["handler_executed"] is False
        assert f"undeclared_request_kind:{kind}" in bundle.admission["errors"]


def test_late_request_from_previous_execution_cannot_attach_to_next(tmp_path: Path) -> None:
    bridge = KernelHostBridge(_context(tmp_path, execution_id="exec-next"))

    bundle = bridge.dispatch(_intent("progress.emit", execution_id="exec-previous", message="late"))

    assert bundle.request is None
    assert "binding_mismatch:execution_id" in bundle.admission["errors"]


def test_live_kernel_host_bridge_canary_and_independent_attempt_admission(tmp_path: Path) -> None:
    workspace_request = PythonWorkspaceRequest(
        run_id="host-bridge-canary",
        plan_revision=canonical_sha256({"plan": "host-bridge"}),
        dag_id="host-bridge",
        node_id="python-host",
        attempt_id="attempt-host",
        attempt_number=1,
        worktree=str(tmp_path),
        goal_hash=GOAL_HASH,
        policy_sha256=POLICY_HASH,
        data_boundary_sha256=DATA_HASH,
        required_features=tuple(sorted(PYTHON_KERNEL_FEATURES)),
        startup_timeout_seconds=20,
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/example.py").write_text("RuntimeEndpointLease = 'symbol'\n", encoding="utf-8")
    workspace, workspace_receipt = create_python_workspace(
        workspace_request, state_dir=tmp_path / "state"
    )
    endpoint = RuntimeEndpointLease.from_payload(workspace_receipt.endpoint_lease.to_value())
    try:
        bundles = []
        for index, call in enumerate(
            [
                ("source.read", {"path": "src/example.py", "start_line": 1, "end_line": 1}),
                (
                    "code.search",
                    {"query": "RuntimeEndpointLease", "roots": ["src"], "max_results": 5},
                ),
                (
                    "graph.query",
                    {
                        "profile": "symbol-neighborhood",
                        "params": {"symbol_id": "RuntimeEndpointLease", "depth": 1},
                    },
                ),
                ("artifact.put", {"name": "analysis.json", "value": {"answer": 42}}),
                ("evidence.emit", {"claim": "candidate observation", "support_refs": []}),
                ("progress.emit", {"message": "halfway"}),
            ]
        ):
            execution_id = f"exec-live-{index}"
            request_path = tmp_path / f"intent-{index}.json"
            code = _client_code(
                request_path=request_path,
                endpoint=endpoint,
                generation_id=workspace.generation_id,
                execution_id=execution_id,
                kind=call[0],
                params=call[1],
            )
            execution = workspace.execute(code, execution_id=execution_id)
            assert execution.status == "OK"
            bridge = KernelHostBridge(
                _context(
                    tmp_path,
                    endpoint=endpoint,
                    execution_id=execution_id,
                    generation_id=workspace.generation_id,
                )
            )
            bundle = bridge.dispatch(json.loads(request_path.read_text(encoding="utf-8")))
            bundles.append(bundle)
        cancelled_context = _context(
            tmp_path,
            endpoint=endpoint,
            execution_id="exec-cancelled",
            generation_id=workspace.generation_id,
            cancelled_executions=frozenset({"exec-cancelled"}),
        )
        cancelled = KernelHostBridge(cancelled_context).dispatch(
            _intent(
                "artifact.put",
                execution_id="exec-cancelled",
                generation_id=workspace.generation_id,
                name="blocked.json",
                value={"no": True},
                endpoint=endpoint,
            )
        )
    finally:
        workspace.stop()

    assert all(
        bundle.admission["schema"] == "tau.python_host_admission_receipt.v1" for bundle in bundles
    )
    assert all(
        bundle.effect["handler_executed"] is True
        for bundle in bundles
        if bundle.result["status"] != "BLOCKED"
    )
    assert bundles[0].result["value"]["path"] == "src/example.py"
    assert bundles[1].result["value"]["count"] == 1
    assert bundles[2].result["value"]["service_status"] in {"OK", "DEGRADED"}
    assert bundles[3].result["value"]["artifact"]["media_type"] == "application/json"
    assert bundles[4].result["value"]["candidate_only"] is True
    assert bundles[5].result["value"]["authoritative"] is False
    assert cancelled.effect["handler_executed"] is False
    assert "execution_cancelled" in cancelled.admission["errors"]
    admission = admit_dag_attempt_result(
        plan_sha256=canonical_sha256({"plan": "host-bridge"}),
        identity=_Identity(),
        node_id="python-host",
        result={
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "validated_artifact": bundles[3].result["value"]["artifact"],
                "host_call_success_is_not_settlement": True,
            },
        },
    )
    assert admission.validation["status"] == "PASS"


GOAL_HASH = canonical_sha256({"goal": "host-bridge"})
POLICY_HASH = canonical_sha256({"policy": "host-bridge"})
DATA_HASH = canonical_sha256({"data": "local"})
WORKTREE_HASH = canonical_sha256({"worktree": "test"})


@dataclass(frozen=True, slots=True)
class _Identity:
    run_id: str = "host-bridge-canary"
    attempt_id: str = "attempt-host"
    attempt: int = 1


def _context(
    worktree: Path,
    *,
    execution_id: str,
    endpoint: RuntimeEndpointLease | None = None,
    generation_id: str = "generation-test",
    cancelled_executions: frozenset[str] = frozenset(),
) -> HostBridgeContext:
    lease = endpoint or _endpoint(execution_id=execution_id)
    return HostBridgeContext(
        run_id=lease.run_id,
        dag_id=lease.dag_id,
        plan_revision=lease.plan_revision,
        node_id=lease.node_id,
        attempt_id=lease.attempt_id,
        work_order_sha256=lease.work_order_sha256,
        endpoint_lease=lease,
        generation_id=generation_id,
        active_execution_id=execution_id,
        goal_hash=lease.goal_hash,
        policy_sha256=POLICY_HASH,
        data_boundary_sha256=DATA_HASH,
        worktree=worktree,
        worktree_sha256=WORKTREE_HASH,
        artifact_dir=worktree / "artifacts",
        allowed_roots=(worktree,),
        allowed_graph_profiles={"symbol-neighborhood": {"max_depth": 2, "max_results": 5}},
        cancelled_executions=cancelled_executions,
    )


def _endpoint(*, execution_id: str) -> RuntimeEndpointLease:
    return RuntimeEndpointLease(
        run_id="host-bridge-canary",
        plan_revision=canonical_sha256({"plan": "host-bridge"}),
        dag_id="host-bridge",
        node_id="python-host",
        attempt_id="attempt-host",
        attempt_number=1,
        execution_token=f"token-{execution_id}",
        backend="python-kernel",
        backend_session_id="kernel",
        scope_id="scope",
        endpoint_id="endpoint",
        work_order_sha256=canonical_sha256({"work": "host-bridge"}),
        goal_hash=GOAL_HASH,
        owner="tau-python-workspace",
        created_at="now",
        expires_at="later",
        heartbeat_policy=FrozenJson.from_value({}),
        cleanup_policy=FrozenJson.from_value({}),
        capabilities_sha256=canonical_sha256({"backend": "python-kernel"}),
        backend_ids=FrozenJson.from_value({}),
    )


def _intent(
    kind: str,
    *,
    execution_id: str,
    generation_id: str = "generation-test",
    endpoint: RuntimeEndpointLease | None = None,
    **params: Any,
) -> PythonHostCallIntent:
    lease = endpoint or _endpoint(execution_id=execution_id)
    return PythonHostCallIntent(
        kind=kind,
        params=FrozenJson.from_value(params),
        binding=FrozenJson.from_value(
            {
                "endpoint_lease_sha256": lease.sha256,
                "execution_token": lease.execution_token,
                "generation_id": generation_id,
                "execution_id": execution_id,
                "goal_hash": lease.goal_hash,
                "policy_sha256": POLICY_HASH,
                "data_boundary_sha256": DATA_HASH,
                "worktree_sha256": WORKTREE_HASH,
            }
        ),
    )


def _client_code(
    *,
    request_path: Path,
    endpoint: RuntimeEndpointLease,
    generation_id: str,
    execution_id: str,
    kind: str,
    params: dict[str, Any],
) -> str:
    namespace, method = kind.split(".")
    return (
        "from tau_coding.runtime_backends.kernel_host_client import TauHostClient\n"
        f"client = TauHostClient({str(request_path)!r}, "
        f"endpoint_lease_sha256={endpoint.sha256!r}, "
        f"execution_token={endpoint.execution_token!r}, "
        f"generation_id={generation_id!r}, "
        f"execution_id={execution_id!r}, "
        f"goal_hash={endpoint.goal_hash!r}, "
        f"policy_sha256={POLICY_HASH!r}, "
        f"data_boundary_sha256={DATA_HASH!r}, "
        f"worktree_sha256={WORKTREE_HASH!r})\n"
        f"client.{namespace}.{method}(**{params!r})\n"
    )


assert HOST_CALL_KINDS
