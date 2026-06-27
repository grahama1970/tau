"""Validation helpers for Tau Loop2 receipt artifacts."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from tau_coding.loop_receipt import (
    LOOP_RECEIPT_CURRENT_STATE_SCHEMA,
    LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA,
    loop_receipt_loop2_events,
)


@dataclass(frozen=True, slots=True)
class LoopReceiptValidationResult:
    """Validation result for one Tau Loop2 receipt run directory."""

    ok: bool
    checked_artifacts: tuple[str, ...]
    errors: tuple[str, ...] = ()


def validate_loop_receipt_with_loop2_contracts(
    run_dir: Path,
    *,
    loop2_src: Path | None = None,
) -> LoopReceiptValidationResult:
    """Validate Tau receipt artifacts with Loop2's actual Pydantic contracts.

    `loop2_src` should point at the directory containing the `loop2` package,
    for example `/path/to/skills/loop2/src`. If omitted, `loop2` must already be
    importable in the current Python environment.
    """

    resolved = run_dir.resolve()
    artifacts = {
        "contract": resolved / "contract.json",
        "events": resolved / "events.jsonl",
        "current_state": resolved / "current-state.json",
        "transport_dag_evidence": resolved / "transport-dag-evidence.json",
        "final_receipt": resolved / "final-receipt.json",
        "node_result": resolved / "node-result.json",
    }
    missing = [name for name, path in artifacts.items() if not path.exists()]
    if missing:
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=tuple(f"missing artifact: {name}" for name in missing),
        )

    try:
        contracts = _load_loop2_contracts(loop2_src)
    except Exception as exc:  # pragma: no cover - exact import errors depend on environment
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=(f"could not load loop2 contracts: {exc}",),
        )

    validators = (
        ("contract", "RepairNodeContract", artifacts["contract"]),
        ("final_receipt", "FinalReceipt", artifacts["final_receipt"]),
        ("node_result", "NodeResult", artifacts["node_result"]),
    )
    checked: list[str] = []
    errors: list[str] = []
    for artifact_name, model_name, artifact_path in validators:
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            model = getattr(contracts, model_name)
            model.model_validate(payload)
        except Exception as exc:
            errors.append(f"{artifact_name}: {exc}")
        else:
            checked.append(artifact_name)

    projected_events: list[dict[str, object]] = []
    try:
        projected_events = loop_receipt_loop2_events(resolved)
        for event in projected_events:
            contracts.Loop2Event.model_validate(event)
    except Exception as exc:
        errors.append(f"events: {exc}")
    else:
        checked.append("events")

    current_state: object = {}
    try:
        current_state = json.loads(artifacts["current_state"].read_text(encoding="utf-8"))
        _validate_current_state(current_state, run_dir=resolved, event_count=len(projected_events))
    except Exception as exc:
        errors.append(f"current_state: {exc}")
    else:
        checked.append("current_state")

    try:
        transport_dag_evidence = json.loads(
            artifacts["transport_dag_evidence"].read_text(encoding="utf-8")
        )
        _validate_transport_dag_evidence(
            transport_dag_evidence,
            run_dir=resolved,
            event_count=len(projected_events),
        )
    except Exception as exc:
        errors.append(f"transport_dag_evidence: {exc}")
    else:
        checked.append("transport_dag_evidence")

    contract_payload: object = {}
    final_receipt: object = {}
    node_result: object = {}
    try:
        contract_payload = json.loads(artifacts["contract"].read_text(encoding="utf-8"))
        final_receipt = json.loads(artifacts["final_receipt"].read_text(encoding="utf-8"))
        node_result = json.loads(artifacts["node_result"].read_text(encoding="utf-8"))
        _validate_backing_paths(final_receipt, node_result, base_dir=resolved)
    except Exception as exc:
        errors.append(f"artifact_paths: {exc}")
    else:
        checked.append("artifact_paths")

    try:
        _validate_check_status(final_receipt, node_result)
    except Exception as exc:
        errors.append(f"check_status: {exc}")
    else:
        checked.append("check_status")

    try:
        _validate_mocked_live_scope(final_receipt, node_result)
    except Exception as exc:
        errors.append(f"mocked_live: {exc}")
    else:
        checked.append("mocked_live")

    try:
        _validate_node_result_parity(final_receipt, node_result)
    except Exception as exc:
        errors.append(f"node_result_parity: {exc}")
    else:
        checked.append("node_result_parity")

    try:
        _validate_contract_parity(contract_payload, final_receipt)
    except Exception as exc:
        errors.append(f"contract_parity: {exc}")
    else:
        checked.append("contract_parity")

    try:
        _validate_state_status(current_state, final_receipt, node_result, projected_events)
    except Exception as exc:
        errors.append(f"state_status: {exc}")
    else:
        checked.append("state_status")

    return LoopReceiptValidationResult(
        ok=not errors,
        checked_artifacts=tuple(checked),
        errors=tuple(errors),
    )


def validate_loop2_contract_file(
    contract_path: Path,
    *,
    loop2_src: Path | None = None,
) -> LoopReceiptValidationResult:
    """Validate one repair-node contract file with Loop2's actual contract model."""

    resolved = contract_path.resolve()
    if not resolved.exists():
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=(f"missing contract: {resolved}",),
        )

    try:
        contracts = _load_loop2_contracts(loop2_src)
    except Exception as exc:  # pragma: no cover - exact import errors depend on environment
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=(f"could not load loop2 contracts: {exc}",),
        )

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        contracts.RepairNodeContract.model_validate(payload)
    except Exception as exc:
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=(f"contract: {exc}",),
        )
    return LoopReceiptValidationResult(ok=True, checked_artifacts=("contract",))


def validate_native_loop2_run_with_contracts(
    run_dir: Path,
    *,
    loop2_src: Path | None = None,
) -> LoopReceiptValidationResult:
    """Validate artifacts emitted by the native Loop2 runner."""

    resolved = run_dir.resolve()
    artifacts = {
        "contract": resolved / "contract.json",
        "events": resolved / "events.jsonl",
        "current_state": resolved / "current-state.json",
        "transport_dag_evidence": resolved / "transport-dag-evidence.json",
        "final_receipt": resolved / "final-receipt.json",
        "node_result": resolved / "node-result.json",
    }
    missing = [name for name, path in artifacts.items() if not path.exists()]
    if missing:
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=tuple(f"missing artifact: {name}" for name in missing),
        )

    try:
        contracts = _load_loop2_contracts(loop2_src)
    except Exception as exc:  # pragma: no cover - exact import errors depend on environment
        return LoopReceiptValidationResult(
            ok=False,
            checked_artifacts=(),
            errors=(f"could not load loop2 contracts: {exc}",),
        )

    checked: list[str] = []
    errors: list[str] = []
    for artifact_name, model_name, artifact_path in (
        ("contract", "RepairNodeContract", artifacts["contract"]),
        ("final_receipt", "FinalReceipt", artifacts["final_receipt"]),
        ("node_result", "NodeResult", artifacts["node_result"]),
    ):
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            if artifact_name == "contract" and isinstance(payload, dict):
                payload = dict(payload)
                payload.pop("run_id", None)
            model = getattr(contracts, model_name)
            model.model_validate(payload)
        except Exception as exc:
            errors.append(f"{artifact_name}: {exc}")
        else:
            checked.append(artifact_name)

    native_events: list[dict[str, object]] = []
    try:
        native_events = _read_jsonl_objects(artifacts["events"])
        for event in native_events:
            contracts.Loop2Event.model_validate(event)
    except Exception as exc:
        errors.append(f"events: {exc}")
    else:
        checked.append("events")

    current_state: object = {}
    try:
        current_state = json.loads(artifacts["current_state"].read_text(encoding="utf-8"))
        _validate_native_current_state(current_state, event_count=len(native_events))
    except Exception as exc:
        errors.append(f"current_state: {exc}")
    else:
        checked.append("current_state")

    try:
        transport_dag_evidence = json.loads(
            artifacts["transport_dag_evidence"].read_text(encoding="utf-8")
        )
        _validate_transport_dag_evidence(
            transport_dag_evidence,
            run_dir=resolved,
            event_count=len(native_events),
        )
    except Exception as exc:
        errors.append(f"transport_dag_evidence: {exc}")
    else:
        checked.append("transport_dag_evidence")

    contract_payload: object = {}
    final_receipt: object = {}
    node_result: object = {}
    try:
        contract_payload = json.loads(artifacts["contract"].read_text(encoding="utf-8"))
        final_receipt = json.loads(artifacts["final_receipt"].read_text(encoding="utf-8"))
        node_result = json.loads(artifacts["node_result"].read_text(encoding="utf-8"))
        _validate_native_backing_paths(final_receipt, node_result, base_dir=resolved)
    except Exception as exc:
        errors.append(f"artifact_paths: {exc}")
    else:
        checked.append("artifact_paths")

    try:
        _validate_check_status(final_receipt, node_result)
    except Exception as exc:
        errors.append(f"check_status: {exc}")
    else:
        checked.append("check_status")

    try:
        _validate_mocked_live_scope(final_receipt, node_result)
    except Exception as exc:
        errors.append(f"mocked_live: {exc}")
    else:
        checked.append("mocked_live")

    try:
        _validate_node_result_parity(final_receipt, node_result)
    except Exception as exc:
        errors.append(f"node_result_parity: {exc}")
    else:
        checked.append("node_result_parity")

    try:
        _validate_contract_parity(contract_payload, final_receipt)
    except Exception as exc:
        errors.append(f"contract_parity: {exc}")
    else:
        checked.append("contract_parity")

    try:
        _validate_native_secret_redaction(contract_payload)
    except Exception as exc:
        errors.append(f"secret_redaction: {exc}")
    else:
        checked.append("secret_redaction")

    try:
        _validate_tau_sanitization_sidecar(resolved)
    except Exception as exc:
        errors.append(f"tau_sanitization: {exc}")
    else:
        if (resolved / "tau-sanitization.json").exists():
            checked.append("tau_sanitization")

    try:
        _validate_native_state_status(current_state, final_receipt, node_result, native_events)
    except Exception as exc:
        errors.append(f"state_status: {exc}")
    else:
        checked.append("state_status")

    return LoopReceiptValidationResult(
        ok=not errors,
        checked_artifacts=tuple(checked),
        errors=tuple(errors),
    )


def _load_loop2_contracts(loop2_src: Path | None) -> ModuleType:
    with _temporary_sys_path(loop2_src):
        return importlib.import_module("loop2.contracts")


def _validate_current_state(
    current_state: object,
    *,
    run_dir: Path,
    event_count: int,
) -> None:
    if not isinstance(current_state, dict):
        raise ValueError("must be a JSON object")
    if current_state.get("schema") != LOOP_RECEIPT_CURRENT_STATE_SCHEMA:
        raise ValueError("schema mismatch")
    if not isinstance(current_state.get("run_id"), str) or not current_state.get("run_id"):
        raise ValueError("run_id must be a non-empty string")
    if current_state.get("event_count") != event_count:
        raise ValueError(
            f"event_count {current_state.get('event_count')!r} does not match events "
            f"{event_count}"
        )
    state = current_state.get("state")
    if state not in {"running", "ended", "failed"}:
        raise ValueError(f"state must be running, ended, or failed: {state!r}")
    _require_existing_path(
        current_state.get("events_path"),
        label="events_path",
        base_dir=run_dir,
    )


def _validate_transport_dag_evidence(
    evidence: object,
    *,
    run_dir: Path,
    event_count: int,
) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("must be a JSON object")
    if evidence.get("schema") != LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA:
        raise ValueError("schema mismatch")
    if evidence.get("found") is not True:
        raise ValueError("found must be true")
    for key in ("transport_run_id", "graph_id"):
        if not isinstance(evidence.get(key), str) or not evidence.get(key):
            raise ValueError(f"{key} must be a non-empty string")
    for key in ("nodes", "edges", "layers"):
        value = evidence.get(key)
        if not isinstance(value, list) or not value:
            raise ValueError(f"{key} must be a non-empty list")
    _require_existing_path(
        evidence.get("proof_path"),
        label="proof_path",
        base_dir=run_dir,
    )
    progress_stream = evidence.get("progress_stream")
    if not isinstance(progress_stream, dict):
        raise ValueError("progress_stream must be a JSON object")
    if progress_stream.get("event_count") != event_count:
        raise ValueError(
            f"progress_stream.event_count {progress_stream.get('event_count')!r} "
            f"does not match events {event_count}"
        )
    _require_existing_path(
        progress_stream.get("events_path"),
        label="progress_stream.events_path",
        base_dir=run_dir,
    )


def _validate_backing_paths(
    final_receipt: object,
    node_result: object,
    *,
    base_dir: Path,
) -> None:
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if not isinstance(node_result, dict):
        raise ValueError("node result must be a JSON object")

    missing: list[str] = []
    receipt_artifacts = final_receipt.get("artifacts")
    if isinstance(receipt_artifacts, dict):
        for name in (
            "contract",
            "events",
            "current_state",
            "transport_dag_evidence",
            "final_receipt",
            "node_result",
        ):
            _append_missing_path(
                missing,
                f"final_receipt.artifacts.{name}",
                receipt_artifacts.get(name),
                base_dir=base_dir,
            )
    else:
        missing.append("final_receipt.artifacts")

    for name in ("events", "final_receipt", "transport_dag_evidence"):
        _append_missing_path(
            missing,
            f"node_result.{name}",
            node_result.get(name),
            base_dir=base_dir,
        )

    for index, check in enumerate(final_receipt.get("checks", []), start=1):
        if isinstance(check, dict):
            _append_missing_path(
                missing,
                f"final_receipt.checks[{index}].stdout_path",
                check.get("stdout_path"),
                base_dir=base_dir,
            )
            _append_missing_path(
                missing,
                f"final_receipt.checks[{index}].stderr_path",
                check.get("stderr_path"),
                base_dir=base_dir,
            )
    for index, check in enumerate(node_result.get("checks", []), start=1):
        if isinstance(check, dict):
            _append_missing_path(
                missing,
                f"node_result.checks[{index}].stdout_path",
                check.get("stdout_path"),
                base_dir=base_dir,
            )
            _append_missing_path(
                missing,
                f"node_result.checks[{index}].stderr_path",
                check.get("stderr_path"),
                base_dir=base_dir,
            )

    if missing:
        raise ValueError("missing referenced paths: " + ", ".join(missing))


def _validate_native_backing_paths(
    final_receipt: object,
    node_result: object,
    *,
    base_dir: Path,
) -> None:
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if not isinstance(node_result, dict):
        raise ValueError("node result must be a JSON object")

    missing: list[str] = []
    receipt_artifacts = final_receipt.get("artifacts")
    if isinstance(receipt_artifacts, dict):
        for name in (
            "run_dir",
            "events",
            "current_state",
            "transport_dag_evidence",
            "node_result",
        ):
            _append_missing_path(
                missing,
                f"final_receipt.artifacts.{name}",
                receipt_artifacts.get(name),
                base_dir=base_dir,
            )
        for optional_name in ("contract", "final_receipt", "tau_sanitization"):
            if optional_name in receipt_artifacts:
                _append_missing_path(
                    missing,
                    f"final_receipt.artifacts.{optional_name}",
                    receipt_artifacts.get(optional_name),
                    base_dir=base_dir,
                )
    else:
        missing.append("final_receipt.artifacts")

    for name in ("events", "final_receipt", "transport_dag_evidence"):
        _append_missing_path(
            missing,
            f"node_result.{name}",
            node_result.get(name),
            base_dir=base_dir,
        )

    for index, check in enumerate(final_receipt.get("checks", []), start=1):
        if isinstance(check, dict):
            _append_missing_path(
                missing,
                f"final_receipt.checks[{index}].stdout_path",
                check.get("stdout_path"),
                base_dir=base_dir,
            )
            _append_missing_path(
                missing,
                f"final_receipt.checks[{index}].stderr_path",
                check.get("stderr_path"),
                base_dir=base_dir,
            )
    for index, check in enumerate(node_result.get("checks", []), start=1):
        if isinstance(check, dict):
            _append_missing_path(
                missing,
                f"node_result.checks[{index}].stdout_path",
                check.get("stdout_path"),
                base_dir=base_dir,
            )
            _append_missing_path(
                missing,
                f"node_result.checks[{index}].stderr_path",
                check.get("stderr_path"),
                base_dir=base_dir,
            )

    if missing:
        raise ValueError("missing referenced paths: " + ", ".join(missing))


def _validate_check_status(final_receipt: object, node_result: object) -> None:
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if not isinstance(node_result, dict):
        raise ValueError("node result must be a JSON object")
    _validate_check_status_payload(final_receipt, label="final_receipt")
    _validate_check_status_payload(node_result, label="node_result")
    if final_receipt.get("status") != node_result.get("status"):
        raise ValueError(
            "final_receipt.status "
            f"{final_receipt.get('status')!r} does not match node_result.status "
            f"{node_result.get('status')!r}"
        )


def _validate_check_status_payload(payload: dict[str, object], *, label: str) -> None:
    status = payload.get("status")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise ValueError(f"{label}.checks must be a list")
    failing = [
        index
        for index, check in enumerate(checks, start=1)
        if isinstance(check, dict) and int(check.get("exit_code", 1)) != 0
    ]
    if status == "PASS" and failing:
        raise ValueError(f"{label}.status PASS has failing checks: {failing}")


def _validate_mocked_live_scope(final_receipt: object, node_result: object) -> None:
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if not isinstance(node_result, dict):
        raise ValueError("node result must be a JSON object")
    for key in ("mocked", "live"):
        receipt_value = final_receipt.get(key)
        node_value = node_result.get(key)
        if not isinstance(receipt_value, bool):
            raise ValueError(f"final_receipt.{key} must be boolean")
        if not isinstance(node_value, bool):
            raise ValueError(f"node_result.{key} must be boolean")
        if receipt_value != node_value:
            raise ValueError(
                f"final_receipt.{key} {receipt_value!r} does not match "
                f"node_result.{key} {node_value!r}"
            )
    if final_receipt["mocked"] and final_receipt["live"]:
        raise ValueError("mocked and live cannot both be true")


def _validate_node_result_parity(final_receipt: object, node_result: object) -> None:
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if not isinstance(node_result, dict):
        raise ValueError("node result must be a JSON object")
    for key in ("run_id", "node_id", "changed_files", "checks"):
        receipt_value = final_receipt.get(key)
        node_value = node_result.get(key)
        if receipt_value != node_value:
            raise ValueError(
                f"final_receipt.{key} {receipt_value!r} does not match "
                f"node_result.{key} {node_value!r}"
            )


def _validate_contract_parity(contract: object, final_receipt: object) -> None:
    if not isinstance(contract, dict):
        raise ValueError("contract must be a JSON object")
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if contract.get("node_id") != final_receipt.get("node_id"):
        raise ValueError(
            f"contract.node_id {contract.get('node_id')!r} does not match "
            f"final_receipt.node_id {final_receipt.get('node_id')!r}"
        )
    contract_checks = contract.get("checks")
    receipt_checks = final_receipt.get("checks")
    if not isinstance(contract_checks, list):
        raise ValueError("contract.checks must be a list")
    if not isinstance(receipt_checks, list):
        raise ValueError("final_receipt.checks must be a list")
    receipt_commands = [
        check.get("command")
        for check in receipt_checks
        if isinstance(check, dict)
    ]
    if contract_checks != receipt_commands:
        raise ValueError(
            f"contract.checks {contract_checks!r} does not match "
            f"final_receipt check commands {receipt_commands!r}"
        )


def _validate_native_secret_redaction(contract: object) -> None:
    if not isinstance(contract, dict):
        raise ValueError("contract must be a JSON object")
    scillm_config = contract.get("scillm")
    if not isinstance(scillm_config, dict):
        return
    api_key = scillm_config.get("api_key")
    if api_key in (None, ""):
        return
    if not isinstance(api_key, str):
        raise ValueError("contract.scillm.api_key must be a string when present")
    if not api_key.startswith("<redacted"):
        raise ValueError("contract.scillm.api_key must be redacted in persisted run artifacts")


def _validate_tau_sanitization_sidecar(run_dir: Path) -> None:
    sidecar_path = run_dir / "tau-sanitization.json"
    if not sidecar_path.exists():
        return
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("must be a JSON object")
    if payload.get("schema") != "tau.loop2_delegated_artifact_sanitization.v1":
        raise ValueError("schema mismatch")
    if payload.get("ran") is not True:
        raise ValueError("ran must be true")
    if payload.get("artifact") != str(sidecar_path):
        raise ValueError("artifact must point to tau-sanitization.json")
    if payload.get("run_dir") != str(run_dir):
        raise ValueError("run_dir mismatch")
    changed_artifacts = payload.get("changed_artifacts")
    if not isinstance(changed_artifacts, list) or not all(
        item in {"contract.json", "final-receipt.json", "node-result.json"}
        for item in changed_artifacts
    ):
        raise ValueError("changed_artifacts must name sanitized run artifacts")
    redacted_keys = payload.get("redacted_keys")
    if not isinstance(redacted_keys, list) or not all(
        item == "contract.scillm.api_key" for item in redacted_keys
    ):
        raise ValueError("redacted_keys must name redacted secret fields")
    filtered_changed_files = payload.get("filtered_changed_files")
    if not isinstance(filtered_changed_files, int) or filtered_changed_files < 0:
        raise ValueError("filtered_changed_files must be a non-negative integer")


def _validate_state_status(
    current_state: object,
    final_receipt: object,
    node_result: object,
    projected_events: list[dict[str, object]],
) -> None:
    if not isinstance(current_state, dict):
        raise ValueError("current state must be a JSON object")
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if not isinstance(node_result, dict):
        raise ValueError("node result must be a JSON object")
    run_id = current_state.get("run_id")
    if run_id != final_receipt.get("run_id") or run_id != node_result.get("run_id"):
        raise ValueError(
            f"current_state.run_id {run_id!r} does not match receipt/node run ids"
        )
    last_event_type = projected_events[-1].get("event_type") if projected_events else None
    if current_state.get("last_event_type") != last_event_type:
        raise ValueError(
            f"current_state.last_event_type {current_state.get('last_event_type')!r} "
            f"does not match last event {last_event_type!r}"
        )
    if final_receipt.get("status") == "PASS":
        if current_state.get("state") != "ended":
            raise ValueError("PASS receipt requires current_state.state 'ended'")
        if current_state.get("last_event_type") not in {"agent_end", "receipt_written"}:
            raise ValueError(
                "PASS receipt requires last_event_type 'agent_end' or 'receipt_written'"
            )


def _validate_native_current_state(current_state: object, *, event_count: int) -> None:
    if not isinstance(current_state, dict):
        raise ValueError("must be a JSON object")
    if current_state.get("schema") != "loop2.current_state.v1":
        raise ValueError("schema mismatch")
    for key in ("run_id", "node_id"):
        if not isinstance(current_state.get(key), str) or not current_state.get(key):
            raise ValueError(f"{key} must be a non-empty string")
    if current_state.get("event_count") != event_count:
        raise ValueError(
            f"event_count {current_state.get('event_count')!r} does not match events "
            f"{event_count}"
        )
    if not isinstance(current_state.get("last_event_type"), str):
        raise ValueError("last_event_type must be a string")


def _validate_native_state_status(
    current_state: object,
    final_receipt: object,
    node_result: object,
    native_events: list[dict[str, object]],
) -> None:
    if not isinstance(current_state, dict):
        raise ValueError("current state must be a JSON object")
    if not isinstance(final_receipt, dict):
        raise ValueError("final receipt must be a JSON object")
    if not isinstance(node_result, dict):
        raise ValueError("node result must be a JSON object")
    run_id = current_state.get("run_id")
    if run_id != final_receipt.get("run_id") or run_id != node_result.get("run_id"):
        raise ValueError(
            f"current_state.run_id {run_id!r} does not match receipt/node run ids"
        )
    if current_state.get("node_id") != final_receipt.get("node_id"):
        raise ValueError("current_state.node_id does not match receipt node id")
    last_event_type = native_events[-1].get("event_type") if native_events else None
    if current_state.get("last_event_type") != last_event_type:
        raise ValueError(
            f"current_state.last_event_type {current_state.get('last_event_type')!r} "
            f"does not match last event {last_event_type!r}"
        )
    if final_receipt.get("status") == "PASS":
        if current_state.get("status") not in {"completed", "accepted", "pass"}:
            raise ValueError("PASS receipt requires completed current_state.status")
        if current_state.get("last_event_type") != "receipt_written":
            raise ValueError("PASS receipt requires last_event_type 'receipt_written'")


def _read_jsonl_objects(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("JSONL rows must be objects")
        rows.append(row)
    return rows


def _append_missing_path(
    missing: list[str],
    label: str,
    value: object,
    *,
    base_dir: Path,
) -> None:
    if not isinstance(value, str) or not value:
        missing.append(label)
        return
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        missing.append(f"{label}={value}")


def _require_existing_path(value: object, *, label: str, base_dir: Path) -> None:
    missing: list[str] = []
    _append_missing_path(missing, label, value, base_dir=base_dir)
    if missing:
        raise ValueError("missing referenced path: " + ", ".join(missing))


@contextmanager
def _temporary_sys_path(path: Path | None) -> Iterator[None]:
    if path is None:
        yield
        return
    selected = str(path.expanduser().resolve())
    sys.path.insert(0, selected)
    try:
        yield
    finally:
        with suppress(ValueError):
            sys.path.remove(selected)
