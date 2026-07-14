"""Contract and fail-closed negotiation checks for runtime backends."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import (
    compile_generic_dag_plan,
    compile_project_dag_plan,
)
from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.project_dag import run_project_dag_contract, validate_dag_contract
from tau_coding.runtime_backends.contracts import (
    GitWorktreeLease,
    RuntimeCapabilities,
    RuntimeCapabilityDecision,
    RuntimeEndpointLease,
    RuntimeEvent,
    RuntimeReconciliationReceipt,
    RuntimeRequirement,
    RuntimeStateProjection,
    RuntimeSubmitReceipt,
)
from tau_coding.runtime_backends.registry import RuntimeBackendRegistry

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "experiments" / "goal-locked-subagents" / "schemas"
RUNTIME_SCHEMAS = (
    "tau.runtime_requirement.v1",
    "tau.runtime_backend_capabilities.v1",
    "tau.runtime_endpoint_lease.v1",
    "tau.runtime_submit_receipt.v1",
    "tau.runtime_event.v1",
    "tau.runtime_state_projection.v1",
    "tau.runtime_reconciliation_receipt.v1",
    "tau.git_worktree_lease.v1",
    "tau.runtime_capability_decision.v1",
)
DIGEST = "sha256:" + "a" * 64


class _Backend:
    def __init__(self, capabilities: RuntimeCapabilities) -> None:
        self._capabilities = capabilities

    def capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    def replace_capabilities(self, capabilities: RuntimeCapabilities) -> None:
        self._capabilities = capabilities

    def ensure_scope(self, request: FrozenJson) -> FrozenJson:
        raise NotImplementedError

    def spawn(self, request: FrozenJson) -> RuntimeEndpointLease:
        raise NotImplementedError

    def submit(
        self, endpoint: RuntimeEndpointLease, work_order: FrozenJson
    ) -> RuntimeSubmitReceipt:
        raise NotImplementedError

    def capture(self, endpoint: RuntimeEndpointLease, lines: int) -> FrozenJson:
        raise NotImplementedError

    def observe(self, endpoint: RuntimeEndpointLease) -> RuntimeEvent:
        raise NotImplementedError

    def wait_event(
        self,
        endpoint: RuntimeEndpointLease,
        cursor: str | None,
        deadline: datetime,
    ) -> RuntimeEvent | None:
        raise NotImplementedError

    def list_owned(self, run_id: str) -> list[RuntimeEndpointLease]:
        raise NotImplementedError

    def terminate(self, endpoint: RuntimeEndpointLease, authorization: FrozenJson) -> FrozenJson:
        raise NotImplementedError


def _local_capabilities(**overrides: bool) -> RuntimeCapabilities:
    values = {
        "interactive": False,
        "one_shot": True,
        "native_events": False,
        "native_agent_state": False,
        "foreground_process_state": True,
        "structured_composer_state": False,
        "stable_endpoint_id": True,
        "human_attach": False,
        "supports_working_directory": True,
        "supports_owned_inventory": True,
        "supports_terminate": True,
    }
    values.update(overrides)
    return RuntimeCapabilities(
        backend="local",
        version="test-v1",
        observation_confidence_levels=("PROCESS",),
        supported_session_scopes=("node_attempt",),
        **values,
    )


def _one_shot_requirement() -> RuntimeRequirement:
    return RuntimeRequirement(
        backend="local",
        interaction_mode="one_shot",
        required_capabilities=("one_shot", "supports_working_directory"),
        session_scope="node_attempt",
        observation_requirements=("PROCESS",),
    )


def test_commandless_persistent_subagent_blocks_without_command_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = ROOT / "examples" / "embry-voice-persistent-subagent" / "dag-contract.json"
    receipt_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=receipt_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "RUNTIME_BACKEND_EXECUTION_NOT_IMPLEMENTED"
    assert receipt["command_executed"] is False
    assert receipt["provider_live"] is False
    assert not (receipt_dir / "compiled-command-specs").exists()
    assert not (receipt_dir / "ready-queue").exists()
    assert not (receipt_dir / "dag-run.sqlite3").exists()
    plan = compile_project_dag_plan(
        json.loads(contract_path.read_text(encoding="utf-8")),
        source_path=contract_path,
    )
    node = next(item for item in plan.nodes if item.node_id == "embry-chatterbox")
    assert node.adapter_kind == "project_persistent_declaration"
    assert node.runtime_requirement.to_value()["backend"] == "herdr"


def test_runtime_schema_artifacts_have_matching_ids_and_closed_objects() -> None:
    for schema_id in RUNTIME_SCHEMAS:
        payload = json.loads((SCHEMAS / f"{schema_id}.schema.json").read_text())
        expected_id = (
            "https://tau.local/schemas/tau.runtime_requirement.v1.schema.json"
            if schema_id == "tau.runtime_requirement.v1"
            else schema_id
        )
        assert payload["$id"] == expected_id
        assert payload["additionalProperties"] is False

    projection = json.loads((SCHEMAS / "tau.runtime_state_projection.v1.schema.json").read_text())
    assert projection["properties"]["state"]["enum"] == [
        "STARTING",
        "READY",
        "RUNNING",
        "WAITING_ON_INPUT",
        "WAITING_ON_APPROVAL",
        "AUTH_REQUIRED",
        "INTERSTITIAL",
        "BLOCKED",
        "EXITED",
        "CRASHED",
        "UNKNOWN",
    ]
    endpoint = json.loads((SCHEMAS / "tau.runtime_endpoint_lease.v1.schema.json").read_text())
    assert endpoint["properties"]["work_order_sha256"]["pattern"] == ("^sha256:[0-9a-f]{64}$")
    dag_plan = json.loads((SCHEMAS / "tau.dag_plan.v1.schema.json").read_text())
    node_schema = dag_plan["properties"]["nodes"]["items"]
    assert "runtime_requirement" in node_schema["required"]
    assert node_schema["properties"]["runtime_requirement"]["$ref"] == (
        "https://tau.local/schemas/tau.runtime_requirement.v1.schema.json"
    )


def test_runtime_requirement_and_capabilities_round_trip_and_hash_stably() -> None:
    requirement = _one_shot_requirement()
    capabilities = _local_capabilities()

    restored_requirement = RuntimeRequirement.from_payload(requirement.to_payload())
    restored_capabilities = RuntimeCapabilities.from_payload(capabilities.to_payload())

    assert restored_requirement == requirement
    assert restored_capabilities == capabilities
    assert restored_capabilities.sha256 == capabilities.sha256


def test_runtime_capabilities_reject_non_boolean_values() -> None:
    with pytest.raises(ValueError, match="runtime capability values must be booleans"):
        _local_capabilities(interactive="false")  # type: ignore[arg-type]


def test_runtime_contracts_reject_unknown_observation_values() -> None:
    with pytest.raises(ValueError, match="observation_requirements contains invalid values"):
        RuntimeRequirement(
            backend="local",
            interaction_mode="one_shot",
            required_capabilities=("one_shot",),
            session_scope="node_attempt",
            observation_requirements=("BAD",),  # type: ignore[arg-type]
        )
    with pytest.raises(
        ValueError, match="observation_confidence_levels contains invalid values"
    ):
        replace(
            _local_capabilities(),
            observation_confidence_levels=("BAD",),  # type: ignore[arg-type]
        )


def test_runtime_requirement_rejects_noncanonical_no_runtime_combinations() -> None:
    with pytest.raises(ValueError, match="non-runtime requirement must use backend none"):
        RuntimeRequirement(
            backend="local", interaction_mode="none", required_capabilities=(),
            session_scope="dag_control", observation_requirements=(),
        )
    with pytest.raises(ValueError, match="backend none cannot request runtime interaction"):
        RuntimeRequirement(
            backend="none", interaction_mode="one_shot",
            required_capabilities=("one_shot",), session_scope="node_attempt",
            observation_requirements=("PROCESS",),
        )


def test_runtime_models_reject_non_object_frozen_fields() -> None:
    with pytest.raises(ValueError, match="heartbeat_policy must be an object"):
        RuntimeEndpointLease(
            run_id="run", plan_revision="rev", dag_id="dag", node_id="node",
            attempt_id="attempt", attempt_number=1, execution_token="token",
            backend="local", backend_session_id=None, scope_id="scope",
            endpoint_id="endpoint", work_order_sha256=DIGEST, goal_hash=DIGEST,
            owner="tau", created_at="now", expires_at="later",
            heartbeat_policy=FrozenJson.from_value([]),
            cleanup_policy=FrozenJson.from_value({}), capabilities_sha256=DIGEST,
            backend_ids=FrozenJson.from_value({}),
        )
def test_runtime_contract_models_reject_incomplete_hashes() -> None:
    with pytest.raises(ValueError, match="complete lowercase SHA-256"):
        RuntimeCapabilityDecision(
            status="BLOCKED",
            backend="missing",
            requirement_sha256="sha256:not-a-digest",
            capabilities_sha256=None,
            missing_capabilities=(),
            errors=("runtime_backend_unknown:missing",),
        )

    with pytest.raises(ValueError, match="runtime capability decision status is invalid"):
        RuntimeCapabilityDecision(
            status="UNKNOWN",  # type: ignore[arg-type]
            backend="missing",
            requirement_sha256=DIGEST,
            capabilities_sha256=None,
            missing_capabilities=(),
            errors=(),
        )


def test_persisted_runtime_contract_family_round_trips() -> None:
    lease = RuntimeEndpointLease(
        run_id="run", plan_revision="rev", dag_id="dag", node_id="node",
        attempt_id="attempt", attempt_number=1, execution_token="token",
        backend="local", backend_session_id=None, scope_id="scope",
        endpoint_id="endpoint", work_order_sha256=DIGEST, goal_hash=DIGEST,
        owner="tau", created_at="now", expires_at="later",
        heartbeat_policy=FrozenJson.from_value({}),
        cleanup_policy=FrozenJson.from_value({}), capabilities_sha256=DIGEST,
        backend_ids=FrozenJson.from_value({}),
    )
    submit = RuntimeSubmitReceipt(
        endpoint_lease_sha256=DIGEST, work_order_sha256=DIGEST,
        composer_state_before="READY", text_delivery_count=1,
        submit_attempt_count=1, composer_state_after="SUBMITTED",
        delivery_status="CONFIRMED",
        backend_acknowledgement=FrozenJson.from_value({}),
        provider_execution_status="NOT_CLAIMED", errors=(),
    )
    event = RuntimeEvent(
        event_id="event", run_id="run", endpoint_lease_sha256=DIGEST,
        event_type="RUNTIME_ENDPOINT_CREATED", observed_at="now", state="READY",
        liveness="ALIVE", confidence="PROCESS", source="local",
        observation=FrozenJson.from_value({}),
    )
    projection = RuntimeStateProjection(
        run_id="run", endpoint_lease_sha256=DIGEST, state="READY",
        liveness="ALIVE", confidence="PROCESS", last_event_id="event", event_count=1,
    )
    reconciliation = RuntimeReconciliationReceipt(
        run_id="run", endpoint_lease_sha256=DIGEST, status="PASS",
        action="ADOPT_AND_CONTINUE_WAITING", evidence=FrozenJson.from_value({}), errors=(),
    )
    worktree = GitWorktreeLease(
        run_id="run", plan_revision="rev", node_id="node", attempt_id="attempt",
        repository="repo",
        worktree_path="/tmp/worktree", base_commit="abc", allowed_paths=("src/",),
        owner="tau", created_at="now", expires_at="later",
        cleanup_policy=FrozenJson.from_value({}),
    )
    decision = RuntimeBackendRegistry().decide(_one_shot_requirement())

    for value, model in (
        (lease, RuntimeEndpointLease),
        (submit, RuntimeSubmitReceipt),
        (event, RuntimeEvent),
        (projection, RuntimeStateProjection),
        (reconciliation, RuntimeReconciliationReceipt),
        (worktree, GitWorktreeLease),
        (decision, RuntimeCapabilityDecision),
    ):
        assert model.from_payload(value.to_payload()) == value


def test_runtime_contract_parsers_reject_unknown_properties() -> None:
    payload = _one_shot_requirement().to_payload()
    payload["misspelled_security_field"] = True

    with pytest.raises(ValueError, match="unexpected contract properties"):
        RuntimeRequirement.from_payload(payload)


def test_runtime_models_reject_schema_invalid_counts() -> None:
    with pytest.raises(ValueError, match="attempt_number must be at least 1"):
        RuntimeEndpointLease(
            run_id="run", plan_revision="rev", dag_id="dag", node_id="node",
            attempt_id="attempt", attempt_number=0, execution_token="token",
            backend="local", backend_session_id=None, scope_id="scope",
            endpoint_id="endpoint", work_order_sha256=DIGEST, goal_hash=DIGEST,
            owner="tau", created_at="now", expires_at="later",
            heartbeat_policy=FrozenJson.from_value({}),
            cleanup_policy=FrozenJson.from_value({}), capabilities_sha256=DIGEST,
            backend_ids=FrozenJson.from_value({}),
        )
    with pytest.raises(ValueError, match="runtime submit counts must be non-negative"):
        RuntimeSubmitReceipt(
            endpoint_lease_sha256=DIGEST, work_order_sha256=DIGEST,
            composer_state_before="READY", text_delivery_count=-1,
            submit_attempt_count=0, composer_state_after="READY",
            delivery_status="BLOCKED", backend_acknowledgement=FrozenJson.from_value({}),
            provider_execution_status="NOT_STARTED", errors=(),
        )
    with pytest.raises(ValueError, match="event_count must be non-negative"):
        RuntimeStateProjection(
            run_id="run", endpoint_lease_sha256=DIGEST, state="UNKNOWN",
            liveness="UNKNOWN", confidence="UNKNOWN", last_event_id="none", event_count=-1,
        )


def test_runtime_endpoint_lease_rejects_empty_stable_identity() -> None:
    with pytest.raises(ValueError, match="required runtime identity fields"):
        RuntimeEndpointLease(
            run_id="", plan_revision="rev", dag_id="dag", node_id="node",
            attempt_id="attempt", attempt_number=1, execution_token="token",
            backend="local", backend_session_id=None, scope_id="scope",
            endpoint_id="endpoint", work_order_sha256=DIGEST, goal_hash=DIGEST,
            owner="tau", created_at="now", expires_at="later",
            heartbeat_policy=FrozenJson.from_value({}),
            cleanup_policy=FrozenJson.from_value({}), capabilities_sha256=DIGEST,
            backend_ids=FrozenJson.from_value({}),
        )


def test_runtime_capability_hash_normalizes_set_like_fields() -> None:
    first = replace(
        _local_capabilities(),
        observation_confidence_levels=("PROCESS", "NATIVE"),
        unsupported_requirements=("persistent_subagent", "remote"),
    )
    second = replace(
        _local_capabilities(),
        observation_confidence_levels=("NATIVE", "PROCESS"),
        unsupported_requirements=("remote", "persistent_subagent"),
    )

    assert first == second
    assert first.sha256 == second.sha256


def test_runtime_reconciliation_rejects_contradictory_outcomes() -> None:
    with pytest.raises(ValueError, match="passing reconciliation receipt"):
        RuntimeReconciliationReceipt(
            run_id="run", endpoint_lease_sha256=DIGEST, status="PASS",
            action="ADOPT", evidence=FrozenJson.from_value({}), errors=("failure",),
        )
    with pytest.raises(ValueError, match="blocked reconciliation receipt requires errors"):
        RuntimeReconciliationReceipt(
            run_id="run", endpoint_lease_sha256=DIGEST, status="BLOCKED",
            action="BLOCK", evidence=FrozenJson.from_value({}), errors=(),
        )


def test_runtime_capability_decision_rejects_inconsistent_outcomes() -> None:
    with pytest.raises(ValueError, match="passing capability decision must not contain"):
        RuntimeCapabilityDecision(
            status="PASS", backend="local", requirement_sha256=DIGEST,
            capabilities_sha256=DIGEST, missing_capabilities=("interactive",), errors=(),
        )
    with pytest.raises(ValueError, match="requires capabilities_sha256"):
        RuntimeCapabilityDecision(
            status="PASS", backend="local", requirement_sha256=DIGEST,
            capabilities_sha256=None, missing_capabilities=(), errors=(),
        )
    with pytest.raises(ValueError, match="blocked capability decision requires errors"):
        RuntimeCapabilityDecision(
            status="BLOCKED", backend="local", requirement_sha256=DIGEST,
            capabilities_sha256=DIGEST, missing_capabilities=(), errors=(),
        )


def test_registry_accepts_supported_registered_backend() -> None:
    registry = RuntimeBackendRegistry()
    registry.register(_Backend(_local_capabilities()))

    decision = registry.decide(_one_shot_requirement())

    assert decision.status == "PASS"
    assert decision.errors == ()
    assert decision.capabilities_sha256 == _local_capabilities().sha256
    assert decision.to_payload()["proof_scope"]["does_not_prove"]


def test_registry_blocks_unknown_backend_before_dispatch() -> None:
    registry = RuntimeBackendRegistry()
    requirement = RuntimeRequirement(
        backend="unregistered",
        interaction_mode="interactive",
        required_capabilities=("interactive",),
        session_scope="persistent_subagent",
        observation_requirements=("NATIVE",),
    )

    decision = registry.decide(requirement)

    assert decision.status == "BLOCKED"
    assert decision.capabilities_sha256 is None
    assert decision.errors == ("runtime_backend_unknown:unregistered",)


def test_registry_accepts_canonical_no_runtime_requirement() -> None:
    decision = RuntimeBackendRegistry().decide(
        RuntimeRequirement(
            backend="none", interaction_mode="none", required_capabilities=(),
            session_scope="dag_control", observation_requirements=(),
        )
    )

    assert decision.status == "PASS"
    assert decision.capabilities_sha256 is None
    assert decision.errors == ()


def test_registry_blocks_unsupported_capability_and_observation() -> None:
    registry = RuntimeBackendRegistry()
    registry.register(_Backend(_local_capabilities()))
    requirement = RuntimeRequirement(
        backend="local",
        interaction_mode="interactive",
        required_capabilities=("interactive", "native_events"),
        session_scope="persistent_subagent",
        observation_requirements=("NATIVE",),
    )

    decision = registry.decide(requirement)

    assert decision.status == "BLOCKED"
    assert decision.missing_capabilities == ("interactive", "native_events")
    assert decision.errors == (
        "runtime_capability_unsupported:interactive",
        "runtime_capability_unsupported:native_events",
        "runtime_observation_unsupported:NATIVE",
        "runtime_session_scope_unsupported:persistent_subagent",
    )


def test_registry_accepts_any_declared_observation_alternative() -> None:
    registry = RuntimeBackendRegistry()
    registry.register(_Backend(_local_capabilities()))
    requirement = RuntimeRequirement(
        backend="local",
        interaction_mode="one_shot",
        required_capabilities=("one_shot",),
        session_scope="node_attempt",
        observation_requirements=("NATIVE", "PROCESS"),
    )

    decision = registry.decide(requirement)

    assert decision.status == "PASS"
    assert decision.errors == ()


def test_registry_blocks_explicitly_unsupported_session_scope() -> None:
    capabilities = replace(
        _local_capabilities(interactive=True),
        supported_session_scopes=("node_attempt", "persistent_subagent"),
        unsupported_requirements=("persistent_subagent",),
    )
    registry = RuntimeBackendRegistry()
    registry.register(_Backend(capabilities))
    requirement = RuntimeRequirement(
        backend="local",
        interaction_mode="interactive",
        required_capabilities=("interactive",),
        session_scope="persistent_subagent",
        observation_requirements=("PROCESS",),
    )

    decision = registry.decide(requirement)

    assert decision.status == "BLOCKED"
    assert decision.errors == (
        "runtime_requirement_declared_unsupported:persistent_subagent",
    )


def test_registry_blocks_undeclared_session_scope() -> None:
    registry = RuntimeBackendRegistry()
    registry.register(_Backend(_local_capabilities()))

    decision = registry.decide(
        RuntimeRequirement(
            backend="local",
            interaction_mode="one_shot",
            required_capabilities=("one_shot",),
            session_scope="persistent_subagnt",
            observation_requirements=("PROCESS",),
        )
    )

    assert decision.status == "BLOCKED"
    assert decision.errors == (
        "runtime_session_scope_unsupported:persistent_subagnt",
    )


def test_runtime_capabilities_reject_non_string_requirement_entries() -> None:
    with pytest.raises(ValueError, match="unsupported_requirements must contain strings"):
        replace(
            _local_capabilities(),
            unsupported_requirements=(7,),  # type: ignore[arg-type]
        )


def test_runtime_receipts_reject_non_string_error_entries() -> None:
    with pytest.raises(ValueError, match="errors must contain strings"):
        RuntimeSubmitReceipt(
            endpoint_lease_sha256=DIGEST,
            work_order_sha256=DIGEST,
            composer_state_before="READY",
            text_delivery_count=0,
            submit_attempt_count=0,
            composer_state_after="BLOCKED",
            delivery_status="BLOCKED",
            backend_acknowledgement=FrozenJson.from_value({}),
            provider_execution_status="NOT_STARTED",
            errors=(7,),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="errors must contain strings"):
        RuntimeReconciliationReceipt(
            run_id="run",
            endpoint_lease_sha256=DIGEST,
            status="BLOCKED",
            action="BLOCK",
            evidence=FrozenJson.from_value({}),
            errors=(7,),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="errors must contain strings"):
        RuntimeCapabilityDecision(
            status="BLOCKED",
            backend="local",
            requirement_sha256=DIGEST,
            capabilities_sha256=DIGEST,
            missing_capabilities=(),
            errors=(7,),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="missing_capabilities must contain strings"):
        RuntimeCapabilityDecision(
            status="BLOCKED",
            backend="local",
            requirement_sha256=DIGEST,
            capabilities_sha256=DIGEST,
            missing_capabilities=(7,),  # type: ignore[arg-type]
            errors=("blocked",),
        )
    with pytest.raises(ValueError, match="supported_session_scopes must contain"):
        replace(
            _local_capabilities(),
            supported_session_scopes=(7,),  # type: ignore[arg-type]
        )


def test_registry_refuses_duplicate_backend_registration() -> None:
    registry = RuntimeBackendRegistry()
    registry.register(_Backend(_local_capabilities()))

    with pytest.raises(RuntimeError, match="runtime_backend_already_registered:local"):
        registry.register(_Backend(_local_capabilities()))


def test_registry_freezes_capabilities_at_registration() -> None:
    backend = _Backend(_local_capabilities())
    registry = RuntimeBackendRegistry()
    registry.register(backend)
    registered_hash = _local_capabilities().sha256
    backend.replace_capabilities(_local_capabilities(one_shot=False))

    decision = registry.decide(_one_shot_requirement())

    assert decision.status == "PASS"
    assert decision.capabilities_sha256 == registered_hash


def test_compiled_generic_node_has_explicit_local_runtime_requirement(tmp_path: Path) -> None:
    payload = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "runtime-contract",
        "run_dir": str(tmp_path),
        "nodes": [
            {
                "node_id": "worker",
                "role": "worker",
                "command": ["python3", "-c", "print('ok')"],
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(tmp_path / "worker.json"),
                "timeout_seconds": 5,
                "max_attempts": 1,
            }
        ],
    }

    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")

    assert plan.nodes[0].runtime_requirement.to_value() == _one_shot_requirement().to_payload()
    assert plan.runtime_goal_hash.startswith("sha256:")
    assert len(plan.runtime_goal_hash) == 71


def test_project_legacy_goal_label_has_complete_runtime_goal_hash(tmp_path: Path) -> None:
    command_spec = tmp_path / "command.json"
    command_spec.write_text('{"command":["true"]}', encoding="utf-8")
    payload = {
        "schema": "tau.dag_contract.v1", "dag_id": "legacy-goal",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {"repo": "grahama1970/tau", "target": "runtime-goal"},
        "entry_node": "worker", "terminal_nodes": ["done"],
        "limits": {"max_steps": 1, "max_total_attempts": 1},
        "nodes": [{
            "id": "worker", "agent": "worker", "executor": "local",
            "max_attempts": 1, "command_spec": str(command_spec),
            "required_evidence": [],
        }],
        "edges": [{"from": "worker", "to": "done"}],
        "required_evidence": [], "fail_closed_on": [],
    }

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")
    lease = RuntimeEndpointLease(
        run_id="run", plan_revision=plan.plan_sha256, dag_id="legacy-goal",
        node_id="worker", attempt_id="attempt", attempt_number=1,
        execution_token="token", backend="local", backend_session_id=None,
        scope_id="scope", endpoint_id="endpoint", work_order_sha256=DIGEST,
        goal_hash=plan.runtime_goal_hash, owner="tau", created_at="now",
        expires_at="later", heartbeat_policy=FrozenJson.from_value({}),
        cleanup_policy=FrozenJson.from_value({}), capabilities_sha256=DIGEST,
        backend_ids=FrozenJson.from_value({}),
    )

    assert lease.goal_hash == plan.runtime_goal_hash
    assert len(lease.goal_hash) == 71


def test_compiled_command_backed_persistent_node_uses_bounded_local_tick(
    tmp_path: Path,
) -> None:
    command_spec = tmp_path / "command.json"
    command_spec.write_text(
        json.dumps({"command": ["python3", "-c", "print('ok')"]}), encoding="utf-8"
    )
    payload = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "persistent-runtime-contract",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {
            "repo": "grahama1970/tau",
            "branch": "main",
            "target": "runtime-contract",
        },
        "entry_node": "embry",
        "terminal_nodes": ["done"],
        "limits": {
            "max_steps": 1,
            "max_total_attempts": 1,
            "default_timeout_seconds": 5,
        },
        "nodes": [
            {
                "id": "embry",
                "agent": "embry-chat",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(command_spec),
                "required_evidence": ["persistent_subagent_receipt"],
                "persistent_subagent": {
                    "schema": "tau.persistent_subagent.v1",
                    "surface_id": "embry-voice",
                    "surface_url": "http://localhost:3002/#embry-voice",
                    "session_mode": "persistent",
                    "tau_control": "bounded_receipt_gated_ticks",
                    "dag_parameter": "embry_voice",
                    "required_receipts": ["embry.chatterbox_voice_receipt.v1"],
                    "unbounded_autonomy_allowed": False,
                },
            }
        ],
        "edges": [{"from": "embry", "to": "done"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")
    requirement = plan.nodes[0].runtime_requirement.to_value()

    assert requirement["backend"] == "local"
    assert requirement["interaction_mode"] == "one_shot"
    assert requirement["session_scope"] == "node_attempt"
    assert requirement["required_capabilities"] == [
        "one_shot",
        "supports_working_directory",
    ]


def test_compiler_rejects_non_string_runtime_backend(tmp_path: Path) -> None:
    project_payload = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "invalid-runtime-backend",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {"repo": "grahama1970/tau", "target": "invalid"},
        "entry_node": "worker",
        "terminal_nodes": ["done"],
        "limits": {"max_steps": 1, "max_total_attempts": 1},
        "nodes": [
            {
                "id": "worker",
                "agent": "worker",
                "executor": "local",
                "runtime_backend": 7,
                "max_attempts": 1,
                "command_spec": str(tmp_path / "command.json"),
                "required_evidence": [],
            }
        ],
        "edges": [{"from": "worker", "to": "done"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }
    (tmp_path / "command.json").write_text('{"command":["true"]}')

    with pytest.raises(RuntimeError, match="runtime_backend must be a non-empty string"):
        validate_dag_contract(project_payload)
    with pytest.raises(RuntimeError, match="runtime_backend must be a non-empty string"):
        compile_project_dag_plan(project_payload, source_path=tmp_path / "dag.json")


def test_compiler_classifies_commandless_persistent_subagent_as_declaration(
    tmp_path: Path,
) -> None:
    payload = {
        "schema": "tau.dag_contract.v1", "dag_id": "virtual-persistent",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {"repo": "grahama1970/tau", "target": "invalid"},
        "entry_node": "worker", "terminal_nodes": ["done"],
        "limits": {"max_steps": 1, "max_total_attempts": 1},
        "nodes": [{
            "id": "worker", "agent": "worker", "executor": "local",
            "max_attempts": 1, "required_evidence": ["persistent_subagent_receipt"],
            "persistent_subagent": {
                "schema": "tau.persistent_subagent.v1", "surface_id": "surface",
                "surface_url": "http://localhost:3002/#surface",
                "session_mode": "persistent", "tau_control": "bounded_receipt_gated_ticks",
                "dag_parameter": "surface", "required_receipts": ["surface.receipt.v1"],
                "unbounded_autonomy_allowed": False,
            },
        }],
        "edges": [{"from": "worker", "to": "done"}],
        "required_evidence": [], "fail_closed_on": [],
    }

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")

    assert plan.nodes[0].adapter_kind == "project_persistent_declaration"
    assert plan.nodes[0].runtime_requirement.to_value()["backend"] == "herdr"


def test_compiler_classifies_commandless_provider_persistence_as_interactive(
    tmp_path: Path,
) -> None:
    payload = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "provider-persistent",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {"repo": "grahama1970/tau", "target": "test"},
        "entry_node": "worker",
        "terminal_nodes": ["done"],
        "limits": {"max_steps": 1, "max_total_attempts": 1},
        "nodes": [
            {
                "id": "worker",
                "agent": "worker",
                "executor": "provider",
                "provider": {"provider_id": "scillm"},
                "max_attempts": 1,
                "required_evidence": ["persistent_subagent_receipt"],
                "persistent_subagent": {
                    "schema": "tau.persistent_subagent.v1",
                    "surface_id": "surface",
                    "surface_url": "http://localhost:3002/#surface",
                    "session_mode": "persistent",
                    "tau_control": "bounded_receipt_gated_ticks",
                    "dag_parameter": "surface",
                    "required_receipts": ["surface.receipt.v1"],
                    "unbounded_autonomy_allowed": False,
                },
            }
        ],
        "edges": [{"from": "worker", "to": "done"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")
    node = plan.nodes[0]

    assert node.adapter_kind == "project_persistent_declaration"
    assert node.runtime_requirement.to_value()["interaction_mode"] == "interactive"


def test_command_with_unsupported_runtime_blocks_before_subprocess(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "command-executed"
    command_spec = tmp_path / "command.json"
    command_spec.write_text(
        json.dumps(
            {
                "command": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).touch()",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )
    contract_path = tmp_path / "dag.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "persistent-command",
                "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
                "target": {"repo": "grahama1970/tau", "target": "test"},
                "entry_node": "worker",
                "terminal_nodes": ["done"],
                "limits": {"max_steps": 1, "max_total_attempts": 1},
                "nodes": [
                    {
                        "id": "worker",
                        "agent": "worker",
                        "executor": "local",
                        "runtime_backend": "tmux",
                        "command_spec": str(command_spec),
                        "max_attempts": 1,
                        "required_evidence": [],
                    }
                ],
                "edges": [{"from": "worker", "to": "done"}],
                "required_evidence": [],
                "fail_closed_on": [],
            }
        ),
        encoding="utf-8",
    )
    receipt_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=receipt_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "RUNTIME_REQUIREMENT_UNSUPPORTED"
    assert receipt["command_executed"] is False
    assert marker.exists() is False
    assert not (receipt_dir / "compiled-command-specs").exists()
    assert not (receipt_dir / "ready-queue").exists()
    assert not (receipt_dir / "dag-run.sqlite3").exists()


def test_persistent_preflight_reports_explicit_backend(tmp_path: Path) -> None:
    source = ROOT / "examples" / "embry-voice-persistent-subagent" / "dag-contract.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["nodes"][0]["runtime_backend"] = "tmux"
    contract_path = tmp_path / "dag.json"
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["verdict"] == "RUNTIME_BACKEND_EXECUTION_NOT_IMPLEMENTED"
    assert receipt["alerts"][0]["evidence"]["runtime_backend"] == "tmux"


def test_handoff_loop_runtime_preflight_preserves_bounded_cycle(tmp_path: Path) -> None:
    nodes = []
    for node_id in ("a", "b"):
        spec = tmp_path / f"{node_id}.json"
        spec.write_text(
            json.dumps(
                {
                    "command": ["python3", "-c", "print('{}')"],
                    "timeout_s": 5,
                }
            ),
            encoding="utf-8",
        )
        nodes.append(
            {
                "id": node_id,
                "agent": node_id,
                "executor": "local",
                "command_spec": str(spec),
                "max_attempts": 1,
                "required_evidence": [],
            }
        )
    contract_path = tmp_path / "cycle.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "bounded-handoff-cycle",
                "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
                "target": {"repo": "grahama1970/tau", "target": "test"},
                "entry_node": "a",
                "terminal_nodes": ["done"],
                "limits": {"max_steps": 2, "max_total_attempts": 2},
                "nodes": nodes,
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "a"},
                    {"from": "b", "to": "done"},
                ],
                "required_evidence": [],
                "fail_closed_on": [],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="handoff-loop",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] != "CYCLE_DETECTED"
    assert (tmp_path / "run" / "command-loop" / "command-loop-receipt.json").is_file()


@pytest.mark.parametrize("scheduler", ["handoff-loop", "bounded-ready-queue"])
def test_fallback_command_spec_cannot_bypass_runtime_preflight(
    tmp_path: Path, scheduler: str
) -> None:
    marker = tmp_path / "executed"
    spec_root = tmp_path / "specs"
    spec_path = spec_root / "worker" / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).touch()",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )
    contract_path = tmp_path / "dag.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "fallback-runtime",
                "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
                "target": {"repo": "grahama1970/tau", "target": "test"},
                "entry_node": "worker",
                "terminal_nodes": ["done"],
                "limits": {"max_steps": 1, "max_total_attempts": 1},
                "nodes": [
                    {
                        "id": "worker",
                        "agent": "worker",
                        "executor": "local",
                        "runtime_backend": "tmux",
                        "max_attempts": 1,
                        "required_evidence": [],
                    }
                ],
                "edges": [{"from": "worker", "to": "done"}],
                "required_evidence": [],
                "fail_closed_on": [],
            }
        ),
        encoding="utf-8",
    )
    receipt_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=receipt_dir,
        agents_root=tmp_path / "agents",
        command_spec_root=spec_root,
        scheduler=scheduler,
    )

    assert receipt["verdict"] == "RUNTIME_REQUIREMENT_UNSUPPORTED"
    assert receipt["command_executed"] is False
    assert marker.exists() is False
    assert not (receipt_dir / "compiled-command-specs").exists()
    assert not (receipt_dir / "command-loop").exists()


def test_fallback_plan_keeps_original_contract_source_hash(tmp_path: Path) -> None:
    original = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "source-hash",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {"repo": "grahama1970/tau", "target": "test"},
        "entry_node": "worker",
        "terminal_nodes": ["done"],
        "limits": {"max_steps": 1, "max_total_attempts": 1},
        "nodes": [
            {
                "id": "worker",
                "agent": "worker",
                "executor": "local",
                "max_attempts": 1,
                "required_evidence": [],
            }
        ],
        "edges": [{"from": "worker", "to": "done"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"command": ["true"], "timeout_s": 5}), encoding="utf-8")
    effective = copy.deepcopy(original)
    effective["nodes"][0]["command_spec"] = str(spec)

    plan = compile_project_dag_plan(
        effective,
        source_path=tmp_path / "dag.json",
        source_payload_sha256=canonical_sha256(original),
    )

    assert plan.source_payload_sha256 == canonical_sha256(original)
    assert plan.source_payload_sha256 != canonical_sha256(effective)


def test_human_node_ignores_matching_fallback_command_spec(tmp_path: Path) -> None:
    spec_root = tmp_path / "specs"
    spec = spec_root / "human" / "tau-dispatch-command.json"
    spec.parent.mkdir(parents=True)
    spec.write_text(json.dumps({"command": ["true"], "timeout_s": 5}), encoding="utf-8")
    contract_path = tmp_path / "dag.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "human-fallback",
                "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
                "target": {"repo": "grahama1970/tau", "target": "test"},
                "entry_node": "human",
                "terminal_nodes": ["done"],
                "limits": {"max_steps": 1, "max_total_attempts": 1},
                "nodes": [
                    {
                        "id": "human",
                        "agent": "human",
                        "executor": "human",
                        "max_attempts": 1,
                        "required_evidence": [],
                    }
                ],
                "edges": [{"from": "human", "to": "done"}],
                "required_evidence": [],
                "fail_closed_on": [],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        command_spec_root=spec_root,
        scheduler="bounded-ready-queue",
    )

    assert receipt["verdict"] == "HUMAN_APPROVAL_REQUIRED"
    assert not (tmp_path / "run" / "compiled-command-specs").exists()


def test_compiler_rejects_persistent_human_node(tmp_path: Path) -> None:
    payload = {
        "schema": "tau.dag_contract.v1", "dag_id": "human-persistent",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {"repo": "grahama1970/tau", "target": "invalid"},
        "entry_node": "human", "terminal_nodes": ["done"],
        "limits": {"max_steps": 1, "max_total_attempts": 1},
        "nodes": [{
            "id": "human", "agent": "human", "executor": "human",
            "max_attempts": 1, "required_evidence": ["persistent_subagent_receipt"],
            "persistent_subagent": {
                "schema": "tau.persistent_subagent.v1", "surface_id": "surface",
                "surface_url": "http://localhost:3002/#surface",
                "session_mode": "persistent", "tau_control": "bounded_receipt_gated_ticks",
                "dag_parameter": "surface", "required_receipts": ["surface.receipt.v1"],
                "unbounded_autonomy_allowed": False,
            },
        }],
        "edges": [{"from": "human", "to": "done"}],
        "required_evidence": [], "fail_closed_on": [],
    }

    with pytest.raises(RuntimeError, match="persistent_subagent_requires_executable_node"):
        compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")


def test_runtime_endpoint_lease_parser_rejects_empty_backend_session_id() -> None:
    lease = RuntimeEndpointLease(
        run_id="run", plan_revision="rev", dag_id="dag", node_id="node",
        attempt_id="attempt", attempt_number=1, execution_token="token",
        backend="local", backend_session_id=None, scope_id="scope",
        endpoint_id="endpoint", work_order_sha256=DIGEST, goal_hash=DIGEST,
        owner="tau", created_at="now", expires_at="later",
        heartbeat_policy=FrozenJson.from_value({}), cleanup_policy=FrozenJson.from_value({}),
        capabilities_sha256=DIGEST, backend_ids=FrozenJson.from_value({}),
    )
    payload = lease.to_payload()
    payload["backend_session_id"] = ""

    with pytest.raises(ValueError, match="non-empty string or null"):
        RuntimeEndpointLease.from_payload(payload)
    with pytest.raises(ValueError, match="non-empty string or null"):
        replace(lease, backend_session_id="")


def test_runtime_direct_construction_rejects_boolean_counts() -> None:
    submit = RuntimeSubmitReceipt(
        endpoint_lease_sha256=DIGEST, work_order_sha256=DIGEST,
        composer_state_before="READY", text_delivery_count=1,
        submit_attempt_count=1, composer_state_after="SUBMITTED",
        delivery_status="CONFIRMED", backend_acknowledgement=FrozenJson.from_value({}),
        provider_execution_status="NOT_CLAIMED", errors=(),
    )
    projection = RuntimeStateProjection(
        run_id="run", endpoint_lease_sha256=DIGEST, state="READY",
        liveness="ALIVE", confidence="PROCESS", last_event_id="event", event_count=1,
    )
    lease = RuntimeEndpointLease(
        run_id="run", plan_revision="rev", dag_id="dag", node_id="node",
        attempt_id="attempt", attempt_number=1, execution_token="token",
        backend="local", backend_session_id=None, scope_id="scope",
        endpoint_id="endpoint", work_order_sha256=DIGEST, goal_hash=DIGEST,
        owner="tau", created_at="now", expires_at="later",
        heartbeat_policy=FrozenJson.from_value({}), cleanup_policy=FrozenJson.from_value({}),
        capabilities_sha256=DIGEST, backend_ids=FrozenJson.from_value({}),
    )

    with pytest.raises(ValueError, match="runtime submit counts"):
        replace(submit, text_delivery_count=True)
    with pytest.raises(ValueError, match="event_count"):
        replace(projection, event_count=False)
    with pytest.raises(ValueError, match="attempt_number"):
        replace(lease, attempt_number=True)


def test_runtime_direct_construction_rejects_empty_schema_bound_strings() -> None:
    event = RuntimeEvent(
        event_id="event", run_id="run", endpoint_lease_sha256=DIGEST,
        event_type="RUNTIME_ENDPOINT_CREATED", observed_at="now", state="READY",
        liveness="ALIVE", confidence="PROCESS", source="local",
        observation=FrozenJson.from_value({}),
    )
    reconciliation = RuntimeReconciliationReceipt(
        run_id="run", endpoint_lease_sha256=DIGEST, status="PASS",
        action="ADOPT", evidence=FrozenJson.from_value({}), errors=(),
    )
    worktree = GitWorktreeLease(
        run_id="run", plan_revision="rev", node_id="node", attempt_id="attempt",
        repository="repo", worktree_path="/tmp/worktree", base_commit="abc",
        allowed_paths=("src/",), owner="tau", created_at="now", expires_at="later",
        cleanup_policy=FrozenJson.from_value({}),
    )

    with pytest.raises(ValueError, match="required runtime identity fields"):
        replace(event, event_id="")
    with pytest.raises(ValueError, match="required runtime identity fields"):
        replace(reconciliation, action="")
    with pytest.raises(ValueError, match="allowed_paths"):
        replace(worktree, allowed_paths=("",))
    with pytest.raises(ValueError, match="required runtime identity fields"):
        replace(
            RuntimeCapabilityDecision(
                status="BLOCKED", backend="local", requirement_sha256=DIGEST,
                capabilities_sha256=DIGEST, missing_capabilities=(), errors=("blocked",),
            ),
            backend="",
        )


def test_compiled_human_node_does_not_claim_process_backend(tmp_path: Path) -> None:
    payload = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "human-runtime-contract",
        "goal": {"goal_id": "g", "goal_version": 1, "goal_hash": "sha256:g"},
        "target": {"repo": "grahama1970/tau", "target": "human-review"},
        "entry_node": "human",
        "terminal_nodes": ["done"],
        "limits": {"max_steps": 1, "max_total_attempts": 1},
        "nodes": [
            {
                "id": "human",
                "agent": "human",
                "executor": "human",
                "max_attempts": 1,
                "required_evidence": [],
            }
        ],
        "edges": [{"from": "human", "to": "done"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }

    plan = compile_project_dag_plan(payload, source_path=tmp_path / "dag.json")

    assert (
        plan.nodes[0].runtime_requirement.to_value()
        == RuntimeRequirement(
            backend="none",
            interaction_mode="none",
            required_capabilities=(),
            session_scope="dag_control",
            observation_requirements=(),
        ).to_payload()
    )
