"""Explicit runtime backend registration and fail-closed capability negotiation."""

from __future__ import annotations

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.runtime_backends.base import RuntimeBackend
from tau_coding.runtime_backends.contracts import (
    RuntimeCapabilities,
    RuntimeCapabilityDecision,
    RuntimeRequirement,
)


class RuntimeBackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, tuple[RuntimeBackend, RuntimeCapabilities]] = {}

    def register(self, backend: RuntimeBackend) -> None:
        capabilities = backend.capabilities()
        if capabilities.backend in self._backends:
            raise RuntimeError(f"runtime_backend_already_registered:{capabilities.backend}")
        self._backends[capabilities.backend] = (backend, capabilities)

    def get(self, name: str) -> RuntimeBackend:
        try:
            return self._backends[name][0]
        except KeyError as exc:
            raise RuntimeError(f"runtime_backend_unknown:{name}") from exc

    def decide(self, requirement: RuntimeRequirement) -> RuntimeCapabilityDecision:
        requirement_sha256 = canonical_sha256(requirement.to_payload())
        if (
            requirement.backend == "none"
            and requirement.interaction_mode == "none"
            and not requirement.required_capabilities
            and not requirement.observation_requirements
        ):
            return RuntimeCapabilityDecision(
                status="PASS",
                backend="none",
                requirement_sha256=requirement_sha256,
                capabilities_sha256=None,
                missing_capabilities=(),
                errors=(),
            )
        registered = self._backends.get(requirement.backend)
        if registered is None:
            return RuntimeCapabilityDecision(
                status="BLOCKED",
                backend=requirement.backend,
                requirement_sha256=requirement_sha256,
                capabilities_sha256=None,
                missing_capabilities=(),
                errors=(f"runtime_backend_unknown:{requirement.backend}",),
            )

        _, capabilities = registered
        missing = tuple(
            sorted(
                name
                for name in requirement.required_capabilities
                if getattr(capabilities, name) is not True
            )
        )
        acceptable_observations = set(requirement.observation_requirements)
        supported_observations = set(capabilities.observation_confidence_levels)
        observation_supported = (
            not acceptable_observations
            or bool(acceptable_observations & supported_observations)
        )
        declared_unsupported = tuple(
            sorted(
                ({*requirement.required_capabilities, requirement.session_scope})
                & set(capabilities.unsupported_requirements)
            )
        )
        session_scope_supported = (
            requirement.session_scope in capabilities.supported_session_scopes
        )
        errors = tuple(
            [*(f"runtime_capability_unsupported:{name}" for name in missing)]
            + (
                [
                    "runtime_observation_unsupported:"
                    + "|".join(sorted(acceptable_observations))
                ]
                if not observation_supported
                else []
            )
            + [
                *(
                    f"runtime_requirement_declared_unsupported:{name}"
                    for name in declared_unsupported
                )
            ]
            + (
                [f"runtime_session_scope_unsupported:{requirement.session_scope}"]
                if not session_scope_supported
                else []
            )
        )
        return RuntimeCapabilityDecision(
            status="BLOCKED" if errors else "PASS",
            backend=requirement.backend,
            requirement_sha256=requirement_sha256,
            capabilities_sha256=capabilities.sha256,
            missing_capabilities=missing,
            errors=errors,
        )
