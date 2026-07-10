"""Canonical security context resolution for secure Tau DAG runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.itar_boundary import ACTOR_ACCESS_MANIFEST_SCHEMA
from tau_coding.policy_profile import validate_data_boundary, validate_policy_profile
from tau_coding.provenance import build_environment_manifest, validate_environment_manifest

SECURITY_CONTEXT_SCHEMA = "tau.security_context.v1"
SECURITY_CONTEXT_RECEIPT_SCHEMA = "tau.security_context_receipt.v1"
SECURITY_MODES = {"development", "secure"}
CONTROLLED_CLASSIFICATIONS = {"CUI", "EAR", "ITAR"}


@dataclass(frozen=True)
class SecurityContextResult:
    context: dict[str, Any]
    receipt: dict[str, Any]
    alerts: list[dict[str, Any]]
    receipt_path: Path
    environment_manifest_path: Path | None


def resolve_security_context(
    *,
    dag_contract: Mapping[str, Any],
    contract_path: Path,
    receipt_dir: Path,
    requested_mode: str | None = None,
) -> SecurityContextResult:
    """Resolve policy, boundary, actor, and environment into one run context."""

    resolved_contract_path = contract_path.expanduser().resolve()
    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
    alerts: list[dict[str, Any]] = []

    requested_mode = requested_mode or _optional_string(dag_contract.get("security_mode"))
    if requested_mode is not None and requested_mode not in SECURITY_MODES:
        alerts.append(
            _alert(
                "invalid_security_mode",
                "security_mode must be development or secure.",
                {"security_mode": requested_mode},
            )
        )

    policy_source = _resolve_json_source(
        dag_contract.get("policy_profile"),
        contract_path=resolved_contract_path,
        field_name="policy_profile",
        validator=validate_policy_profile,
    )
    boundary_source = _resolve_json_source(
        dag_contract.get("data_boundary"),
        contract_path=resolved_contract_path,
        field_name="data_boundary",
        validator=validate_data_boundary,
    )
    actor_source = _resolve_json_source(
        dag_contract.get("actor_access_manifest"),
        contract_path=resolved_contract_path,
        field_name="actor_access_manifest",
        validator=_validate_actor_access_manifest,
    )
    command_policy_source = _resolve_json_source(
        dag_contract.get("command_policy"),
        contract_path=resolved_contract_path,
        field_name="command_policy",
        validator=None,
    )
    alerts.extend(policy_source.alerts)
    alerts.extend(boundary_source.alerts)
    alerts.extend(actor_source.alerts)
    alerts.extend(command_policy_source.alerts)

    controlled_boundary = _is_controlled_boundary(boundary_source.payload)
    explicit_mode = requested_mode is not None
    effective_mode = requested_mode or "development"
    if controlled_boundary and (not explicit_mode or effective_mode != "secure"):
        alerts.append(
            _alert(
                "controlled_boundary_requires_secure_mode",
                "Controlled data boundaries require explicit secure mode before DAG dispatch.",
                {
                    "requested_mode": requested_mode,
                    "classification": _classification(boundary_source.payload),
                },
            )
        )

    env_source = _resolve_or_generate_environment_manifest(
        dag_contract=dag_contract,
        contract_path=resolved_contract_path,
        receipt_dir=resolved_receipt_dir,
        policy_source=policy_source,
        boundary_source=boundary_source,
    )
    alerts.extend(env_source.alerts)

    goal = dag_contract.get("goal")
    goal_map = goal if isinstance(goal, Mapping) else {}
    goal_hash = _optional_string(goal_map.get("goal_hash"))
    if effective_mode == "secure":
        if policy_source.payload is None:
            alerts.append(_missing_alert("policy_profile"))
        if boundary_source.payload is None:
            alerts.append(_missing_alert("data_boundary"))
        if actor_source.payload is None:
            alerts.append(_missing_alert("actor_access_manifest"))
        if command_policy_source.payload is None:
            alerts.append(_missing_alert("command_policy"))
        if env_source.payload is None:
            alerts.append(_missing_alert("environment_manifest"))
        if not goal_hash:
            alerts.append(_missing_alert("goal_hash"))

    required_gates = _required_gates(
        controlled_boundary=controlled_boundary,
        policy_source=policy_source,
        command_policy_source=command_policy_source,
    )
    context = {
        "schema": SECURITY_CONTEXT_SCHEMA,
        "run_id": str(dag_contract.get("run_id") or dag_contract.get("dag_id") or "unknown"),
        "dag_id": dag_contract.get("dag_id"),
        "security_mode": effective_mode,
        "security_mode_explicit": explicit_mode,
        "security_mode_requested": requested_mode,
        "regulated_data_authorized": effective_mode == "secure" and not alerts,
        "production_authority": False,
        "goal": {
            "goal_id": goal_map.get("goal_id"),
            "goal_version": goal_map.get("goal_version"),
            "goal_hash": goal_hash,
        },
        "policy_profile": policy_source.as_context_reference(),
        "data_boundary": boundary_source.as_context_reference(
            extra={
                "classification": _classification(boundary_source.payload),
                "export_controlled": bool(
                    isinstance(boundary_source.payload, Mapping)
                    and boundary_source.payload.get("export_controlled") is True
                ),
                "controlled_boundary": controlled_boundary,
            }
        ),
        "actor": actor_source.as_context_reference(extra=_actor_summary(actor_source.payload)),
        "environment": env_source.as_context_reference(),
        "command_policy": command_policy_source.as_context_reference(),
        "required_gates": required_gates,
        "resolved_inputs": {
            "policy_profile": policy_source.payload is not None,
            "data_boundary": boundary_source.payload is not None,
            "actor_access_manifest": actor_source.payload is not None,
            "environment_manifest": env_source.payload is not None,
            "command_policy": command_policy_source.payload is not None,
        },
        "created_at": _utc_stamp(),
    }
    context_sha256 = f"sha256:{_canonical_sha256(context)}"
    ok = not alerts
    receipt_path = resolved_receipt_dir / "security-context-receipt.json"
    context_path = resolved_receipt_dir / "security-context.json"
    context_payload = {**context, "security_context_sha256": context_sha256}
    _write_json(context_path, context_payload)
    receipt = {
        "schema": SECURITY_CONTEXT_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "security_mode": effective_mode,
        "security_context": context_payload,
        "security_context_path": str(context_path),
        "security_context_sha256": context_sha256,
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "receipt_path": str(receipt_path),
        "proof_scope": {
            "proves": [
                "Tau resolved DAG policy, boundary, actor, environment, command-policy, "
                "and goal inputs before dispatch.",
                "Tau hash-bound the resolved security context for this run.",
                "Tau identified whether the resolved data boundary is controlled.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Legal identity proof or authoritative U.S.-person status.",
                "Runtime sandbox enforcement.",
                "Provider/model semantic quality.",
                "Secure enforcement outside tau run and tau dag-run.",
            ],
        },
        "checked_at": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    return SecurityContextResult(
        context=context_payload,
        receipt=receipt,
        alerts=alerts,
        receipt_path=receipt_path,
        environment_manifest_path=env_source.path,
    )


@dataclass(frozen=True)
class _ResolvedSource:
    field_name: str
    payload: dict[str, Any] | None
    path: Path | None
    source_kind: str | None
    sha256: str | None
    alerts: list[dict[str, Any]]
    generated: bool = False

    def as_context_reference(
        self,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if self.payload is None:
            return None
        payload: dict[str, Any] = {
            "source_kind": self.source_kind,
            "path": str(self.path) if self.path is not None else None,
            "sha256": self.sha256,
            "schema": self.payload.get("schema"),
            "generated": self.generated,
        }
        if extra:
            payload.update(extra)
        return payload


def _resolve_or_generate_environment_manifest(
    *,
    dag_contract: Mapping[str, Any],
    contract_path: Path,
    receipt_dir: Path,
    policy_source: _ResolvedSource,
    boundary_source: _ResolvedSource,
) -> _ResolvedSource:
    value = dag_contract.get("environment_manifest")
    if value is not None:
        return _resolve_json_source(
            value,
            contract_path=contract_path,
            field_name="environment_manifest",
            validator=validate_environment_manifest,
        )
    generated_path = receipt_dir / "environment-manifest.json"
    network_policy = "deny" if _is_controlled_boundary(boundary_source.payload) else "allowlisted"
    provider_access = (
        "denied"
        if isinstance(boundary_source.payload, Mapping)
        and boundary_source.payload.get("external_provider_allowed") is False
        else "allowed"
    )
    try:
        payload = build_environment_manifest(
            run_id=str(dag_contract.get("run_id") or dag_contract.get("dag_id") or "unknown"),
            network_policy=network_policy,
            provider_access=provider_access,
            mounted_paths=[],
            secrets_visible=[],
            policy_profile=str(policy_source.path) if policy_source.path is not None else None,
            data_boundary=str(boundary_source.path) if boundary_source.path is not None else None,
            output_path=generated_path,
        )
    except Exception as exc:  # pragma: no cover - defensive error packaging
        return _ResolvedSource(
            field_name="environment_manifest",
            payload=None,
            path=generated_path,
            source_kind="generated",
            sha256=None,
            alerts=[
                _alert(
                    "environment_manifest_generation_failed",
                    "Tau could not generate an environment manifest before dispatch.",
                    {"error": str(exc), "path": str(generated_path)},
                )
            ],
            generated=True,
        )
    alerts = [
        _alert(
            "invalid_environment_manifest",
            "Generated environment manifest did not validate.",
            {"errors": list(payload.get("errors", [])), "path": str(generated_path)},
        )
    ] if payload.get("ok") is not True else []
    return _ResolvedSource(
        field_name="environment_manifest",
        payload=payload,
        path=generated_path.resolve(),
        source_kind="generated",
        sha256=f"sha256:{_sha256_file(generated_path)}",
        alerts=alerts,
        generated=True,
    )


def _resolve_json_source(
    value: object,
    *,
    contract_path: Path,
    field_name: str,
    validator: Any,
) -> _ResolvedSource:
    if value is None:
        return _ResolvedSource(field_name, None, None, None, None, [])
    if isinstance(value, Mapping):
        payload = dict(value)
        alerts = _validation_alerts(field_name, payload, validator, None)
        return _ResolvedSource(
            field_name=field_name,
            payload=payload,
            path=None,
            source_kind="embedded",
            sha256=f"sha256:{_canonical_sha256(payload)}",
            alerts=alerts,
        )
    if not isinstance(value, str):
        return _ResolvedSource(
            field_name,
            None,
            None,
            None,
            None,
            [
                _alert(
                    f"invalid_{field_name}",
                    f"{field_name} must be a path string or embedded object.",
                    {"value_type": type(value).__name__},
                )
            ],
        )
    raw_path = Path(value)
    source_kind = "absolute_path" if raw_path.is_absolute() else "relative_path"
    path = raw_path if raw_path.is_absolute() else contract_path.parent / raw_path
    resolved_path = path.expanduser().resolve()
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _ResolvedSource(
            field_name,
            None,
            resolved_path,
            source_kind,
            None,
            [
                _alert(
                    f"invalid_{field_name}",
                    f"{field_name} could not be read.",
                    {"path": str(resolved_path), "errors": [str(exc)]},
                )
            ],
        )
    if not isinstance(payload, dict):
        return _ResolvedSource(
            field_name,
            None,
            resolved_path,
            source_kind,
            f"sha256:{_sha256_file(resolved_path)}",
            [
                _alert(
                    f"invalid_{field_name}",
                    f"{field_name} root must be an object.",
                    {"path": str(resolved_path)},
                )
            ],
        )
    alerts = _validation_alerts(field_name, payload, validator, resolved_path)
    return _ResolvedSource(
        field_name=field_name,
        payload=payload,
        path=resolved_path,
        source_kind=source_kind,
        sha256=f"sha256:{_sha256_file(resolved_path)}",
        alerts=alerts,
    )


def _validation_alerts(
    field_name: str,
    payload: dict[str, Any],
    validator: Any,
    path: Path | None,
) -> list[dict[str, Any]]:
    if validator is None:
        return []
    errors = validator(payload)
    return [
        _alert(
            f"invalid_{field_name}",
            f"{field_name} did not validate.",
            {"path": str(path) if path is not None else None, "errors": errors},
        )
    ] if errors else []


def _validate_actor_access_manifest(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != ACTOR_ACCESS_MANIFEST_SCHEMA:
        errors.append(f"schema must be {ACTOR_ACCESS_MANIFEST_SCHEMA}")
    if not _optional_string(payload.get("actor_id")):
        errors.append("actor_id must be a non-empty string")
    if payload.get("actor_type") not in {"human", "service", "workload", "agent"}:
        errors.append("actor_type must be one of agent, human, service, workload")
    eligibility = payload.get("eligibility")
    if eligibility is not None and not isinstance(eligibility, Mapping):
        errors.append("eligibility must be an object when provided")
    return errors


def _required_gates(
    *,
    controlled_boundary: bool,
    policy_source: _ResolvedSource,
    command_policy_source: _ResolvedSource,
) -> list[str]:
    gates: list[str] = []
    if policy_source.payload is not None:
        gates.append("zero_trust_preflight")
    if controlled_boundary:
        gates.append("itar_access_preflight")
    if command_policy_source.payload is not None:
        gates.append("command_policy")
    return gates


def _is_controlled_boundary(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    classification = _classification(payload)
    return (
        classification in CONTROLLED_CLASSIFICATIONS
        or payload.get("export_controlled") is True
        or payload.get("itar") is True
        or payload.get("technical_data") is True
    )


def _classification(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("classification") or "").upper()


def _actor_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    eligibility = payload.get("eligibility")
    eligibility_map = eligibility if isinstance(eligibility, Mapping) else {}
    approved = eligibility_map.get("approved_for_boundary")
    return {
        "source_schema": payload.get("schema"),
        "actor_id": payload.get("actor_id"),
        "actor_type": payload.get("actor_type"),
        "assurance": "declared",
        "trusted": payload.get("trusted") is True,
        "verified": payload.get("verified") is True,
        "approved_boundaries": approved if isinstance(approved, list) else [],
    }


def _missing_alert(field_name: str) -> dict[str, Any]:
    return _alert(
        f"missing_{field_name}",
        f"Secure mode requires {field_name} before DAG dispatch.",
        {"field": field_name},
    )


def _alert(code: str, message: str, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "severity": "BLOCK",
        "code": code,
        "message": message,
        "evidence": dict(evidence or {}),
    }


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
