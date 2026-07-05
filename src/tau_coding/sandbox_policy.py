"""Zero-trust sandbox policy checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tau_coding.policy_profile import validate_data_boundary, validate_policy_profile

SANDBOX_RUN_RECEIPT_SCHEMA = "tau.sandbox_run_receipt.v1"


def sandbox_policy_alerts(
    *,
    policy_profile: Mapping[str, Any],
    data_boundary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return fail-closed alerts for a zero-trust sandbox run request."""

    alerts: list[dict[str, Any]] = []
    policy_errors = validate_policy_profile(policy_profile)
    if policy_errors:
        alerts.append(_alert("invalid_policy_profile", "Policy profile is invalid.", policy_errors))
    boundary_errors = validate_data_boundary(data_boundary)
    if boundary_errors:
        alerts.append(_alert("invalid_data_boundary", "Data boundary is invalid.", boundary_errors))

    if _section_default(policy_profile, "network") != "deny":
        alerts.append(
            _alert(
                "network_not_default_deny",
                "Sandbox runs require policy_profile.network.default to be deny.",
            )
        )
    if _section_value(policy_profile, "providers", "cloud_llm") != "deny":
        alerts.append(
            _alert(
                "cloud_provider_not_denied",
                "Sandbox runs require policy_profile.providers.cloud_llm to be deny.",
            )
        )
    if _section_value(policy_profile, "research", "external_search") != "deny":
        alerts.append(
            _alert(
                "external_research_not_denied",
                "Sandbox runs require policy_profile.research.external_search to be deny.",
            )
        )
    if _section_value(policy_profile, "github", "public_mutation") != "deny":
        alerts.append(
            _alert(
                "public_github_mutation_not_denied",
                "Sandbox runs require policy_profile.github.public_mutation to be deny.",
            )
        )
    if data_boundary.get("external_provider_allowed") is True:
        alerts.append(
            _alert(
                "data_boundary_allows_external_provider",
                "Sandbox runs require data_boundary.external_provider_allowed to be false.",
            )
        )
    if data_boundary.get("external_research_allowed") is True:
        alerts.append(
            _alert(
                "data_boundary_allows_external_research",
                "Sandbox runs require data_boundary.external_research_allowed to be false.",
            )
        )
    if data_boundary.get("public_repo_allowed") is True:
        alerts.append(
            _alert(
                "data_boundary_allows_public_repo",
                "Sandbox runs require data_boundary.public_repo_allowed to be false.",
            )
        )
    return alerts


def _section_default(payload: Mapping[str, Any], section: str) -> str | None:
    value = payload.get(section)
    if not isinstance(value, Mapping):
        return None
    default = value.get("default")
    return default if isinstance(default, str) else None


def _section_value(payload: Mapping[str, Any], section: str, key: str) -> str | None:
    value = payload.get(section)
    if not isinstance(value, Mapping):
        return None
    decision = value.get(key)
    return decision if isinstance(decision, str) else None


def _alert(code: str, message: str, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "code": code,
        "severity": "BLOCK",
        "message": message,
    }
    if errors:
        alert["errors"] = errors
    return alert
