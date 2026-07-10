"""Deterministic security-capability compilation for secure Tau DAG nodes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CAPABILITY_REQUEST_SCHEMA = "tau.capability_request.v1"
CAPABILITY_GRANT_SCHEMA = "tau.capability_grant.v1"
CAPABILITY_DECISION_RECEIPT_SCHEMA = "tau.capability_decision_receipt.v1"

SECURITY_CAPABILITIES = frozenset(
    {
        "approval.request",
        "artifact.create",
        "artifact.promote",
        "artifact.validate",
        "browser.inspect",
        "browser.interact",
        "collaboration.request_review",
        "collaboration.send",
        "collaboration.subscribe",
        "filesystem.create",
        "filesystem.delete",
        "filesystem.read",
        "filesystem.write",
        "github.comment",
        "github.label",
        "github.merge",
        "github.push",
        "github.read",
        "memory.promote",
        "memory.read",
        "memory.write",
        "network.connect",
        "network.listen",
        "process.execute",
        "process.spawn",
        "provider.invoke",
        "provider.read_credentials",
        "research.import",
        "research.query",
    }
)

_MUTATING_CAPABILITIES = frozenset(
    {
        "artifact.create",
        "artifact.promote",
        "browser.interact",
        "filesystem.create",
        "filesystem.delete",
        "filesystem.write",
        "github.comment",
        "github.label",
        "github.merge",
        "github.push",
        "memory.promote",
        "memory.write",
    }
)
_NETWORK_CAPABILITIES = frozenset(
    {
        "collaboration.send",
        "collaboration.subscribe",
        "github.comment",
        "github.label",
        "github.merge",
        "github.push",
        "github.read",
        "network.connect",
        "network.listen",
        "provider.invoke",
        "provider.read_credentials",
        "research.import",
        "research.query",
    }
)


def validate_capability_declaration(value: object, *, label: str) -> list[str]:
    """Validate one concise DAG-node capability declaration."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return [f"{label} must be an object"]
    capability = value.get("capability")
    if capability not in SECURITY_CAPABILITIES:
        errors.append(f"{label}.capability must name a supported security capability")
    if not _non_empty_string(value.get("target")):
        errors.append(f"{label}.target must be a non-empty string")
    resource_scope = value.get("resource_scope")
    if not _non_empty_string_list(resource_scope):
        errors.append(f"{label}.resource_scope must be a non-empty string list")
    maximum_effect = value.get("maximum_effect")
    if not isinstance(maximum_effect, Mapping) or not maximum_effect:
        errors.append(f"{label}.maximum_effect must be a non-empty object")
    return errors


def compile_capability_decision(
    *,
    dag_id: str,
    run_id: str,
    goal_hash: str,
    security_context: Mapping[str, Any],
    command_policy: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    receipt_dir: Path,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    """Compile secure node declarations into all-or-nothing capability grants."""

    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
    now = (issued_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    policy_errors = _validate_capability_policy(command_policy)
    policy_rules = _policy_rules(command_policy)
    ttl_seconds = _positive_int(command_policy.get("capability_grant_ttl_seconds")) or 300
    security_context_sha256 = _required_hash(
        security_context.get("security_context_sha256"),
        field="security_context.security_context_sha256",
    )
    policy_ref = security_context.get("policy_profile")
    boundary_ref = security_context.get("data_boundary")
    actor_ref = security_context.get("actor")
    policy_sha256 = _reference_hash(policy_ref)
    boundary_sha256 = _reference_hash(boundary_ref)
    actor_id = _reference_string(actor_ref, "actor_id")

    alerts: list[dict[str, Any]] = [
        _alert("invalid_capability_policy", error) for error in policy_errors
    ]
    requests: list[dict[str, Any]] = []
    grants: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for node in nodes:
        node_id = str(node.get("node_id") or "")
        executor = str(node.get("executor") or "local")
        attempt = _positive_int(node.get("attempt")) or 1
        declarations = node.get("requested_capabilities")
        declaration_list = (
            list(declarations)
            if isinstance(declarations, Sequence)
            and not isinstance(declarations, (str, bytes))
            else []
        )
        node_errors: list[str] = []
        if _node_requires_execution_capability(executor) and not declaration_list:
            node_errors.append("secure executable node requires requested_capabilities")
        declared_names = {
            str(item.get("capability"))
            for item in declaration_list
            if isinstance(item, Mapping)
        }
        if (
            _node_requires_execution_capability(executor)
            and "process.execute" not in declared_names
        ):
            node_errors.append("secure executable node must request process.execute")

        node_grants: list[str] = []
        for index, declaration in enumerate(declaration_list):
            label = f"nodes[{node_id}].requested_capabilities[{index}]"
            declaration_errors = validate_capability_declaration(declaration, label=label)
            node_errors.extend(declaration_errors)
            if declaration_errors or not isinstance(declaration, Mapping):
                continue
            request = _build_request(
                declaration=declaration,
                request_index=index,
                run_id=run_id,
                dag_id=dag_id,
                node_id=node_id,
                attempt=attempt,
                goal_hash=goal_hash,
                security_context_sha256=security_context_sha256,
                policy_sha256=policy_sha256,
                boundary_sha256=boundary_sha256,
                actor_id=actor_id,
                requested_at=now,
            )
            requests.append(request)
            rule = policy_rules.get(str(declaration["capability"]))
            denial = _request_denial(request, rule, command_policy=command_policy)
            if denial is not None:
                node_errors.append(denial)
                continue
            grant = _build_grant(
                request=request,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            grant_path = (
                resolved_receipt_dir
                / "capability-grants"
                / node_id
                / f"{index:03d}-{str(declaration['capability']).replace('.', '-')}.json"
            )
            grant["grant_path"] = str(grant_path)
            grants.append(grant)
            node_grants.append(str(grant["grant_sha256"]))

        if node_errors:
            alerts.extend(
                _alert(
                    "capability_request_denied",
                    error,
                    {"node_id": node_id, "executor": executor},
                )
                for error in node_errors
            )
        decisions.append(
            {
                "node_id": node_id,
                "executor": executor,
                "status": "BLOCKED" if node_errors else "PASS",
                "requested_count": len(declaration_list),
                "grant_sha256s": node_grants,
                "errors": node_errors,
            }
        )

    request_dir = resolved_receipt_dir / "capability-requests"
    for index, request in enumerate(requests):
        request_path = request_dir / str(request["node_id"]) / f"{index:03d}.json"
        _write_json(request_path, request)
        request["request_path"] = str(request_path)

    if alerts:
        grants = []
        for decision in decisions:
            decision["grant_sha256s"] = []
    else:
        for grant in grants:
            grant_path = Path(str(grant["grant_path"]))
            _write_json(grant_path, grant)

    status = "PASS" if not alerts else "BLOCKED"
    receipt_path = resolved_receipt_dir / "capability-decision-receipt.json"
    receipt = {
        "schema": CAPABILITY_DECISION_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_id": run_id,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "security_context_sha256": security_context_sha256,
        "policy_profile_sha256": policy_sha256,
        "data_boundary_sha256": boundary_sha256,
        "actor_id": actor_id,
        "request_count": len(requests),
        "grant_count": len(grants),
        "decisions": decisions,
        "requests": requests,
        "grants": grants,
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "command_executed": False,
        "provider_invoked": False,
        "filesystem_mutation_performed": False,
        "receipt_path": str(receipt_path),
        "proof_scope": {
            "proves": [
                "Tau compiled declared node capability requests against the resolved "
                "command policy.",
                "Tau bound generated grants to the goal, security context, policy, boundary, "
                "node, and attempt.",
                "Tau denied the complete pre-dispatch decision when any requested capability "
                "was not allowed.",
            ],
            "does_not_prove": [
                "Runtime capability enforcement.",
                "Sandbox isolation.",
                "Secret isolation.",
                "Network isolation.",
                "Provider/model semantic quality.",
            ],
        },
        "checked_at": _stamp(now),
    }
    _write_json(receipt_path, receipt)
    return receipt


def _build_request(
    *,
    declaration: Mapping[str, Any],
    request_index: int,
    run_id: str,
    dag_id: str,
    node_id: str,
    attempt: int,
    goal_hash: str,
    security_context_sha256: str,
    policy_sha256: str,
    boundary_sha256: str,
    actor_id: str,
    requested_at: datetime,
) -> dict[str, Any]:
    request = {
        "schema": CAPABILITY_REQUEST_SCHEMA,
        "request_id": (
            f"{dag_id}:{node_id}:{attempt}:{request_index}:{declaration['capability']}"
        ),
        "run_id": run_id,
        "dag_id": dag_id,
        "node_id": node_id,
        "attempt": attempt,
        "actor_id": actor_id,
        "goal_hash": goal_hash,
        "security_context_sha256": security_context_sha256,
        "policy_profile_sha256": policy_sha256,
        "data_boundary_sha256": boundary_sha256,
        "capability": declaration["capability"],
        "target": declaration["target"],
        "resource_scope": list(declaration["resource_scope"]),
        "maximum_effect": dict(declaration["maximum_effect"]),
        "requested_at": _stamp(requested_at),
    }
    return {**request, "request_sha256": f"sha256:{_canonical_sha256(request)}"}


def _build_grant(*, request: Mapping[str, Any], expires_at: datetime) -> dict[str, Any]:
    grant = {
        "schema": CAPABILITY_GRANT_SCHEMA,
        "grant_id": f"grant:{request['request_id']}",
        "request_sha256": request["request_sha256"],
        "run_id": request["run_id"],
        "dag_id": request["dag_id"],
        "node_id": request["node_id"],
        "attempt": request["attempt"],
        "actor_id": request["actor_id"],
        "goal_hash": request["goal_hash"],
        "security_context_sha256": request["security_context_sha256"],
        "policy_profile_sha256": request["policy_profile_sha256"],
        "data_boundary_sha256": request["data_boundary_sha256"],
        "capability": request["capability"],
        "target": request["target"],
        "resource_scope": request["resource_scope"],
        "maximum_effect": request["maximum_effect"],
        "issued_at": request["requested_at"],
        "expires_at": _stamp(expires_at),
        "granting_authority": "tau.command_spec_policy.v1",
    }
    return {**grant, "grant_sha256": f"sha256:{_canonical_sha256(grant)}"}


def _request_denial(
    request: Mapping[str, Any],
    rule: Mapping[str, Any] | None,
    *,
    command_policy: Mapping[str, Any],
) -> str | None:
    capability = str(request["capability"])
    if rule is None:
        return f"capability {capability} has no command-policy rule"
    targets = rule.get("targets")
    target_list = list(targets) if isinstance(targets, list) else []
    if not _non_empty_string_list(target_list) or request["target"] not in target_list:
        return f"capability {capability} target is not allowed by command policy"
    allowed_scope = rule.get("resource_scope")
    allowed_scope_list = list(allowed_scope) if isinstance(allowed_scope, list) else []
    if not _non_empty_string_list(allowed_scope_list) or not set(
        request["resource_scope"]
    ).issubset(set(allowed_scope_list)):
        return f"capability {capability} resource scope exceeds command policy"
    if dict(request["maximum_effect"]) != rule.get("maximum_effect"):
        return f"capability {capability} maximum effect does not match command policy"
    if capability in _NETWORK_CAPABILITIES and command_policy.get("allows_network") is not True:
        return f"capability {capability} requires command policy allows_network=true"
    if capability in _MUTATING_CAPABILITIES and command_policy.get("allows_mutation") is not True:
        return f"capability {capability} requires command policy allows_mutation=true"
    return None


def _validate_capability_policy(policy: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if policy.get("schema") != "tau.command_spec_policy.v1":
        errors.append("command policy schema must be tau.command_spec_policy.v1")
    rules = policy.get("capability_rules")
    if not isinstance(rules, list):
        return ["command policy capability_rules must be a list"]
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        label = f"capability_rules[{index}]"
        if not isinstance(rule, Mapping):
            errors.append(f"{label} must be an object")
            continue
        capability = rule.get("capability")
        if capability not in SECURITY_CAPABILITIES:
            errors.append(f"{label}.capability must name a supported security capability")
            continue
        if capability in seen:
            errors.append(f"duplicate capability rule: {capability}")
        seen.add(str(capability))
        if not _non_empty_string_list(rule.get("targets")):
            errors.append(f"{label}.targets must be a non-empty string list")
        if not _non_empty_string_list(rule.get("resource_scope")):
            errors.append(f"{label}.resource_scope must be a non-empty string list")
        if not isinstance(rule.get("maximum_effect"), Mapping) or not rule.get(
            "maximum_effect"
        ):
            errors.append(f"{label}.maximum_effect must be a non-empty object")
    ttl = policy.get("capability_grant_ttl_seconds")
    if ttl is not None and _positive_int(ttl) is None:
        errors.append("capability_grant_ttl_seconds must be a positive integer")
    return errors


def _policy_rules(policy: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rules = policy.get("capability_rules")
    if not isinstance(rules, list):
        return {}
    return {
        str(rule["capability"]): rule
        for rule in rules
        if isinstance(rule, Mapping) and rule.get("capability") in SECURITY_CAPABILITIES
    }


def _node_requires_execution_capability(executor: str) -> bool:
    return executor not in {"human", "scheduler", "virtual"}


def _reference_hash(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("security context reference is missing")
    return _required_hash(value.get("sha256"), field="security context reference sha256")


def _reference_string(value: object, key: str) -> str:
    if not isinstance(value, Mapping) or not _non_empty_string(value.get(key)):
        raise ValueError(f"security context reference {key} is missing")
    return str(value[key])


def _required_hash(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field} must be a sha256 value")
    return value


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_non_empty_string(item) for item in value)
    )


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _alert(code: str, message: str, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "severity": "BLOCK",
        "code": code,
        "message": message,
        "evidence": dict(evidence or {}),
    }


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
