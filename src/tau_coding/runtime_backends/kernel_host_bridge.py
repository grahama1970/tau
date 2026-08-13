"""Generation-bound host-call bridge for Tau Python kernel workspaces."""

from __future__ import annotations

import base64
import json
import mimetypes
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends.contracts import RuntimeEndpointLease

PYTHON_HOST_REQUEST_SCHEMA = "tau.python_host_request.v1"
PYTHON_HOST_ADMISSION_RECEIPT_SCHEMA = "tau.python_host_admission_receipt.v1"
PYTHON_HOST_EFFECT_RECEIPT_SCHEMA = "tau.python_host_effect_receipt.v1"
PYTHON_HOST_RESULT_SCHEMA = "tau.python_host_result.v1"
PYTHON_HOST_CALL_INTENT_SCHEMA = "tau.python_host_call_intent.v1"

HOST_CALL_KINDS = frozenset(
    {
        "source.read",
        "code.search",
        "graph.query",
        "artifact.put",
        "evidence.emit",
        "progress.emit",
    }
)

HostCallStatus = Literal["OK", "BLOCKED", "DEGRADED"]


class KernelHostBridgeError(ValueError):
    """Raised when a host-call bridge payload violates Tau's authority boundary."""


@dataclass(frozen=True, slots=True)
class HostBridgeContext:
    run_id: str
    dag_id: str
    plan_revision: str
    node_id: str
    attempt_id: str
    work_order_sha256: str
    endpoint_lease: RuntimeEndpointLease
    generation_id: str
    active_execution_id: str
    goal_hash: str
    policy_sha256: str
    data_boundary_sha256: str
    worktree: Path
    worktree_sha256: str
    artifact_dir: Path
    allowed_roots: tuple[Path, ...]
    allowed_graph_profiles: Mapping[str, Mapping[str, int]]
    cancelled_attempts: frozenset[str] = frozenset()
    cancelled_executions: frozenset[str] = frozenset()
    revoked: bool = False

    def __post_init__(self) -> None:
        if self.endpoint_lease.backend != "python-kernel":
            raise KernelHostBridgeError("host bridge requires a python-kernel endpoint lease")
        if self.endpoint_lease.run_id != self.run_id:
            raise KernelHostBridgeError("run_id does not match endpoint lease")
        if self.endpoint_lease.attempt_id != self.attempt_id:
            raise KernelHostBridgeError("attempt_id does not match endpoint lease")
        if self.endpoint_lease.node_id != self.node_id:
            raise KernelHostBridgeError("node_id does not match endpoint lease")
        if self.endpoint_lease.goal_hash != self.goal_hash:
            raise KernelHostBridgeError("goal_hash does not match endpoint lease")
        if not self.allowed_roots:
            raise KernelHostBridgeError("host bridge requires at least one allowed root")


@dataclass(frozen=True, slots=True)
class PythonHostCallIntent:
    kind: str
    params: FrozenJson
    binding: FrozenJson

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PythonHostCallIntent:
        if payload.get("schema") != PYTHON_HOST_CALL_INTENT_SCHEMA:
            raise KernelHostBridgeError("host-call intent schema is invalid")
        kind = payload.get("kind")
        params = payload.get("params")
        binding = payload.get("binding")
        if not isinstance(kind, str) or not kind:
            raise KernelHostBridgeError("host-call kind must be a non-empty string")
        if not isinstance(params, dict):
            raise KernelHostBridgeError("host-call params must be an object")
        if not isinstance(binding, dict):
            raise KernelHostBridgeError("host-call binding must be an object")
        return cls(
            kind=kind,
            params=FrozenJson.from_value(params),
            binding=FrozenJson.from_value(binding),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_HOST_CALL_INTENT_SCHEMA,
            "kind": self.kind,
            "params": self.params.to_value(),
            "binding": self.binding.to_value(),
        }


@dataclass(frozen=True, slots=True)
class PythonHostRequest:
    request_id: str
    kind: str
    run_id: str
    dag_id: str
    plan_revision: str
    node_id: str
    attempt_id: str
    work_order_sha256: str
    endpoint_lease_sha256: str
    execution_token: str
    generation_id: str
    execution_id: str
    goal_hash: str
    policy_sha256: str
    data_boundary_sha256: str
    worktree_sha256: str
    budget: FrozenJson
    params: FrozenJson

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PYTHON_HOST_REQUEST_SCHEMA,
            "request_id": self.request_id,
            "kind": self.kind,
            "run_id": self.run_id,
            "dag_id": self.dag_id,
            "plan_revision": self.plan_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "work_order_sha256": self.work_order_sha256,
            "endpoint_lease_sha256": self.endpoint_lease_sha256,
            "execution_token": self.execution_token,
            "generation_id": self.generation_id,
            "execution_id": self.execution_id,
            "goal_hash": self.goal_hash,
            "policy_sha256": self.policy_sha256,
            "data_boundary_sha256": self.data_boundary_sha256,
            "worktree_sha256": self.worktree_sha256,
            "budget": self.budget.to_value(),
            "params": self.params.to_value(),
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class HostDispatchBundle:
    request: dict[str, Any] | None
    admission: dict[str, Any]
    effect: dict[str, Any]
    result: dict[str, Any]


MemoryQueryAdapter = Callable[[str, Mapping[str, Any], Mapping[str, int]], dict[str, Any]]


class KernelHostBridge:
    def __init__(
        self,
        context: HostBridgeContext,
        *,
        memory_query: MemoryQueryAdapter | None = None,
    ) -> None:
        self.context = context
        self.memory_query = memory_query or governed_memory_recall_query

    def dispatch(self, intent: PythonHostCallIntent | dict[str, Any]) -> HostDispatchBundle:
        parsed = (
            intent
            if isinstance(intent, PythonHostCallIntent)
            else PythonHostCallIntent.from_payload(intent)
        )
        request_id = f"hostreq-{secrets.token_hex(12)}"
        failures = self._admission_failures(parsed)
        request = None if failures else self._request_from_intent(request_id, parsed)
        admission = _admission_receipt(
            request_id=request_id,
            request=request,
            status="BLOCKED" if failures else "OK",
            errors=tuple(failures),
        )
        if failures or request is None:
            effect = _effect_receipt(
                request_id=request_id,
                request_sha256=None,
                kind=parsed.kind,
                handler_executed=False,
                status="BLOCKED",
                artifacts=(),
                errors=tuple(failures),
            )
            result = _result_receipt(
                request_id=request_id,
                request_sha256=None,
                status="BLOCKED",
                value={"reason": "admission_failed"},
                artifacts=(),
                errors=tuple(failures),
            )
            return HostDispatchBundle(None, admission, effect, result)
        try:
            result_value, artifacts, errors, status = self._execute_handler(request)
            handler_executed = True
        except KernelHostBridgeError as exc:
            result_value = {"reason": str(exc)}
            artifacts = []
            errors = [str(exc)]
            status = "BLOCKED"
            handler_executed = False
        effect = _effect_receipt(
            request_id=request.request_id,
            request_sha256=request.sha256,
            kind=request.kind,
            handler_executed=handler_executed,
            status=status,
            artifacts=tuple(artifacts),
            errors=tuple(errors),
        )
        result = _result_receipt(
            request_id=request.request_id,
            request_sha256=request.sha256,
            status=status,
            value=result_value,
            artifacts=tuple(artifacts),
            errors=tuple(errors),
        )
        return HostDispatchBundle(request.to_payload(), admission, effect, result)

    def _admission_failures(self, intent: PythonHostCallIntent) -> list[str]:
        binding = intent.binding.to_value()
        failures: list[str] = []
        if intent.kind not in HOST_CALL_KINDS:
            failures.append(f"undeclared_request_kind:{intent.kind}")
        expected = {
            "endpoint_lease_sha256": self.context.endpoint_lease.sha256,
            "execution_token": self.context.endpoint_lease.execution_token,
            "generation_id": self.context.generation_id,
            "execution_id": self.context.active_execution_id,
            "goal_hash": self.context.goal_hash,
            "policy_sha256": self.context.policy_sha256,
            "data_boundary_sha256": self.context.data_boundary_sha256,
            "worktree_sha256": self.context.worktree_sha256,
        }
        for key, expected_value in expected.items():
            if binding.get(key) != expected_value:
                failures.append(f"binding_mismatch:{key}")
        if self.context.revoked:
            failures.append("host_bridge_revoked")
        if self.context.attempt_id in self.context.cancelled_attempts:
            failures.append("attempt_cancelled")
        if self.context.active_execution_id in self.context.cancelled_executions:
            failures.append("execution_cancelled")
        return failures

    def _request_from_intent(
        self, request_id: str, intent: PythonHostCallIntent
    ) -> PythonHostRequest:
        return PythonHostRequest(
            request_id=request_id,
            kind=intent.kind,
            run_id=self.context.run_id,
            dag_id=self.context.dag_id,
            plan_revision=self.context.plan_revision,
            node_id=self.context.node_id,
            attempt_id=self.context.attempt_id,
            work_order_sha256=self.context.work_order_sha256,
            endpoint_lease_sha256=self.context.endpoint_lease.sha256,
            execution_token=self.context.endpoint_lease.execution_token,
            generation_id=self.context.generation_id,
            execution_id=self.context.active_execution_id,
            goal_hash=self.context.goal_hash,
            policy_sha256=self.context.policy_sha256,
            data_boundary_sha256=self.context.data_boundary_sha256,
            worktree_sha256=self.context.worktree_sha256,
            budget=FrozenJson.from_value(_budget_for_kind(intent.kind)),
            params=intent.params,
        )

    def _execute_handler(
        self, request: PythonHostRequest
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], HostCallStatus]:
        params = request.params.to_value()
        if request.kind == "source.read":
            return self._source_read(request, params)
        if request.kind == "code.search":
            return self._code_search(request, params)
        if request.kind == "graph.query":
            return self._graph_query(params)
        if request.kind == "artifact.put":
            return self._artifact_put(request, params)
        if request.kind == "evidence.emit":
            return self._evidence_emit(request, params)
        if request.kind == "progress.emit":
            return self._progress_emit(request, params)
        return {"reason": "unsupported"}, [], ["unsupported_request_kind"], "BLOCKED"

    def _source_read(
        self, request: PythonHostRequest, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], HostCallStatus]:
        try:
            path = _resolve_granted_path(
                params.get("path"),
                worktree=self.context.worktree,
                allowed_roots=self.context.allowed_roots,
            )
        except KernelHostBridgeError as exc:
            return {"reason": str(exc)}, [], [str(exc)], "BLOCKED"
        start_line = _int_param(params, "start_line", default=1, minimum=1, maximum=1_000_000)
        end_line = _int_param(
            params,
            "end_line",
            default=start_line + 119,
            minimum=start_line,
            maximum=start_line + 500,
        )
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        selected = "\n".join(lines[start_line - 1 : end_line])
        artifact = self._write_artifact(
            request=request,
            name=f"source-read-{path.name}.txt",
            content=selected.encode("utf-8"),
            media_type="text/plain",
        )
        projection, truncated = _bounded_text(selected, max_bytes=16_000)
        return (
            {
                "path": str(path.relative_to(self.context.worktree)),
                "start_line": start_line,
                "end_line": end_line,
                "source_sha256": canonical_sha256({"text": text}),
                "projection": projection,
                "truncated": truncated,
                "artifact": artifact,
            },
            [artifact],
            [],
            "OK",
        )

    def _code_search(
        self, request: PythonHostRequest, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], HostCallStatus]:
        query = params.get("query")
        if not isinstance(query, str) or not query or len(query) > 200:
            return {"reason": "invalid_query"}, [], ["invalid_query"], "BLOCKED"
        max_results = _int_param(params, "max_results", default=20, minimum=1, maximum=50)
        roots = params.get("roots", ["."])
        if not isinstance(roots, list) or any(not isinstance(root, str) for root in roots):
            return {"reason": "invalid_roots"}, [], ["invalid_roots"], "BLOCKED"
        results: list[dict[str, Any]] = []
        total_bytes = 0
        for root in roots:
            try:
                root_path = _resolve_granted_path(
                    root,
                    worktree=self.context.worktree,
                    allowed_roots=self.context.allowed_roots,
                )
            except KernelHostBridgeError:
                continue
            files = [root_path] if root_path.is_file() else root_path.rglob("*")
            for path in files:
                if (
                    len(results) >= max_results
                    or not path.is_file()
                    or path.stat().st_size > 256_000
                ):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                index = text.find(query)
                if index < 0:
                    continue
                line = text.count("\n", 0, index) + 1
                snippet, truncated = _bounded_text(text[max(0, index - 80) : index + 160])
                result = {
                    "path": str(path.relative_to(self.context.worktree)),
                    "line": line,
                    "span": [index, index + len(query)],
                    "snippet": snippet,
                    "truncated": truncated,
                    "source_sha256": canonical_sha256({"text": text}),
                }
                total_bytes += len(json.dumps(result))
                if total_bytes > 64_000:
                    break
                results.append(result)
        artifact = self._write_artifact(
            request=request,
            name="code-search-results.json",
            content=json.dumps(results, sort_keys=True).encode("utf-8"),
            media_type="application/json",
        )
        return (
            {"results": results, "count": len(results), "artifact": artifact},
            [artifact],
            [],
            "OK",
        )

    def _graph_query(
        self, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], HostCallStatus]:
        profile = params.get("profile")
        if not isinstance(profile, str) or profile not in self.context.allowed_graph_profiles:
            return (
                {"reason": "graph_profile_not_allowed"},
                [],
                ["graph_profile_not_allowed"],
                "BLOCKED",
            )
        profile_budget = self.context.allowed_graph_profiles[profile]
        query_params = params.get("params")
        if not isinstance(query_params, dict):
            return {"reason": "invalid_graph_params"}, [], ["invalid_graph_params"], "BLOCKED"
        requested_depth = _int_param(
            query_params, "depth", default=1, minimum=1, maximum=profile_budget["max_depth"]
        )
        requested_limit = _int_param(
            query_params,
            "limit",
            default=min(5, profile_budget["max_results"]),
            minimum=1,
            maximum=profile_budget["max_results"],
        )
        if "aql" in query_params or "collection" in query_params:
            return {"reason": "raw_graph_access_denied"}, [], ["raw_graph_access_denied"], "BLOCKED"
        value = self.memory_query(
            profile,
            {**query_params, "depth": requested_depth, "limit": requested_limit},
            profile_budget,
        )
        status: HostCallStatus = "DEGRADED" if value.get("service_status") == "DEGRADED" else "OK"
        return value, [], [], status

    def _artifact_put(
        self, request: PythonHostRequest, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], HostCallStatus]:
        name = params.get("name")
        if not isinstance(name, str) or not name or "/" in name or ".." in name:
            return {"reason": "invalid_artifact_name"}, [], ["invalid_artifact_name"], "BLOCKED"
        if "value" in params:
            content = json.dumps(params["value"], sort_keys=True).encode("utf-8")
            media_type = "application/json"
        else:
            raw = params.get("base64")
            if not isinstance(raw, str):
                return (
                    {"reason": "missing_artifact_value"},
                    [],
                    ["missing_artifact_value"],
                    "BLOCKED",
                )
            content = base64.b64decode(raw.encode("ascii"), validate=True)
            media_type = str(params.get("media_type") or "application/octet-stream")
        artifact = self._write_artifact(
            request=request, name=name, content=content, media_type=media_type
        )
        return (
            {"artifact": artifact, "idempotent_content_identity": artifact["sha256"]},
            [artifact],
            [],
            "OK",
        )

    def _evidence_emit(
        self, request: PythonHostRequest, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], HostCallStatus]:
        claim = params.get("claim")
        support_refs = params.get("support_refs", [])
        confidence = params.get("confidence", 0.0)
        warnings: list[str] = []
        if isinstance(claim, str) and "PASS" in claim.upper():
            warnings.append("pass_claim_downgraded_to_candidate")
        if isinstance(confidence, int | float) and confidence > 0.49:
            warnings.append("confidence_capped_for_candidate_evidence")
            confidence = 0.49
        packet = {
            "schema": "tau.python_candidate_evidence.v1",
            "claim": claim,
            "support_refs": support_refs if isinstance(support_refs, list) else [],
            "confidence": confidence,
            "candidate_only": True,
            "accepted": False,
            "warnings": warnings,
        }
        artifact = self._write_artifact(
            request=request,
            name="candidate-evidence.json",
            content=json.dumps(packet, sort_keys=True).encode("utf-8"),
            media_type="application/json",
        )
        return packet | {"artifact": artifact}, [artifact], [], "OK"

    def _progress_emit(
        self, request: PythonHostRequest, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], HostCallStatus]:
        message = params.get("message")
        if not isinstance(message, str) or len(message) > 1000:
            return (
                {"reason": "invalid_progress_message"},
                [],
                ["invalid_progress_message"],
                "BLOCKED",
            )
        progress = {
            "schema": "tau.python_progress_event.v1",
            "message": message,
            "authoritative": False,
            "emitted_at_monotonic": time.monotonic(),
        }
        artifact = self._write_artifact(
            request=request,
            name="progress.json",
            content=json.dumps(progress, sort_keys=True).encode("utf-8"),
            media_type="application/json",
        )
        return progress | {"artifact": artifact}, [artifact], [], "OK"

    def _write_artifact(
        self,
        *,
        request: PythonHostRequest,
        name: str,
        content: bytes,
        media_type: str,
    ) -> dict[str, Any]:
        if len(content) > _budget_for_kind(request.kind)["max_artifact_bytes"]:
            raise KernelHostBridgeError("artifact_budget_exceeded")
        digest = canonical_sha256({"content_base64": base64.b64encode(content).decode("ascii")})
        safe_name = "".join(ch if ch.isalnum() or ch in ".-_" else "-" for ch in name)
        path = self.context.artifact_dir / request.attempt_id / digest.removeprefix("sha256:")
        path.mkdir(parents=True, exist_ok=True)
        artifact_path = path / safe_name
        if not artifact_path.exists():
            artifact_path.write_bytes(content)
        return {
            "schema": "tau.host_artifact_ref.v1",
            "path": str(artifact_path),
            "sha256": digest,
            "media_type": media_type,
            "bytes": len(content),
        }


def governed_memory_recall_query(
    profile: str,
    params: Mapping[str, Any],
    budget: Mapping[str, int],
) -> dict[str, Any]:
    symbol_id = params.get("symbol_id")
    if not isinstance(symbol_id, str) or not symbol_id:
        return {"service_status": "BLOCKED", "errors": ["missing_symbol_id"], "items": []}
    limit = min(int(params.get("limit", 5)), int(budget.get("max_results", 5)))
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:8601",
            timeout=httpx.Timeout(5.0, connect=1.0),
        ) as client:
            response = client.post(
                "/recall",
                json={
                    "q": f"What code symbols are near {symbol_id}?",
                    "k": limit,
                    "collections": ["code_symbols"],
                },
                headers={"X-Caller-Skill": "tau-python-host-bridge"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {
            "profile": profile,
            "service_status": "DEGRADED",
            "errors": [f"memory_recall_failed:{type(exc).__name__}"],
            "items": [],
        }
    return {
        "profile": profile,
        "service_status": "OK",
        "found": bool(data.get("found")),
        "items": data.get("items", [])[:limit],
        "meta": data.get("meta", {}),
        "errors": data.get("errors", []),
    }


def _admission_receipt(
    *,
    request_id: str,
    request: PythonHostRequest | None,
    status: HostCallStatus,
    errors: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": PYTHON_HOST_ADMISSION_RECEIPT_SCHEMA,
        "request_id": request_id,
        "request_sha256": request.sha256 if request is not None else None,
        "status": status,
        "admitted": status == "OK",
        "errors": list(errors),
    }


def _effect_receipt(
    *,
    request_id: str,
    request_sha256: str | None,
    kind: str,
    handler_executed: bool,
    status: HostCallStatus,
    artifacts: tuple[dict[str, Any], ...],
    errors: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": PYTHON_HOST_EFFECT_RECEIPT_SCHEMA,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "kind": kind,
        "handler_executed": handler_executed,
        "status": status,
        "artifacts": list(artifacts),
        "errors": list(errors),
    }


def _result_receipt(
    *,
    request_id: str,
    request_sha256: str | None,
    status: HostCallStatus,
    value: dict[str, Any],
    artifacts: tuple[dict[str, Any], ...],
    errors: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": PYTHON_HOST_RESULT_SCHEMA,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "status": status,
        "value": value,
        "artifacts": list(artifacts),
        "tau_admission_status": "not_admitted",
        "errors": list(errors),
    }


def _budget_for_kind(kind: str) -> dict[str, int]:
    return {
        "deadline_ms": 5000,
        "max_artifact_bytes": 1_000_000,
        "max_projection_bytes": 16_000,
        "max_results": 50 if kind == "code.search" else 20,
    }


def _resolve_granted_path(
    value: object, *, worktree: Path, allowed_roots: tuple[Path, ...]
) -> Path:
    if not isinstance(value, str) or not value:
        raise KernelHostBridgeError("path_missing")
    requested = Path(value)
    if requested.is_absolute() or ".." in requested.parts:
        raise KernelHostBridgeError("path_outside_grant")
    raw_candidate = worktree / requested
    if raw_candidate.is_symlink():
        raise KernelHostBridgeError("symlink_escape_denied")
    candidate = raw_candidate.resolve()
    if not candidate.exists():
        raise KernelHostBridgeError("path_missing")
    if candidate.is_symlink():
        raise KernelHostBridgeError("symlink_escape_denied")
    resolved_roots = tuple(root.resolve() for root in allowed_roots)
    if not any(candidate == root or candidate.is_relative_to(root) for root in resolved_roots):
        raise KernelHostBridgeError("path_outside_grant")
    if not candidate.is_file() and not candidate.is_dir():
        raise KernelHostBridgeError("path_not_regular")
    return candidate


def _int_param(
    params: Mapping[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = params.get(name, default)
    if type(value) is not int or value < minimum or value > maximum:
        raise KernelHostBridgeError(f"{name}_outside_budget")
    return value


def _bounded_text(value: str, *, max_bytes: int = 4096) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def guess_media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
