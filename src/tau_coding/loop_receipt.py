"""Receipt helpers for recording Tau loop runs.

This module owns the first production slices of Loop2 alignment: a stable run
directory, an append-only `events.jsonl` ledger for Tau `AgentEvent` objects,
and a `current-state.json` snapshot for monitors. Higher-level receipts, checks,
and DAG evidence are added in later slices.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from tau_agent.events import AgentEvent

LOOP_RECEIPT_CONTRACT_SCHEMA = "loop2.repair_node_contract.v1"
LOOP_RECEIPT_EVENTS_SCHEMA = "tau.loop_receipt.events.v1"
LOOP_RECEIPT_CURRENT_STATE_SCHEMA = "tau.loop_receipt.current_state.v1"
LOOP_RECEIPT_FINAL_RECEIPT_SCHEMA = "loop2.final_receipt.v1"
LOOP_RECEIPT_NODE_RESULT_SCHEMA = "loop2.node_result.v1"
LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA = "ux_lab.transport_dag_run_evidence.v1"
LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA = "tau.loop_harness_peer_message.v1"
LOOP2_EVENT_SCHEMA = "loop2.event.v1"


@dataclass(frozen=True, slots=True)
class LoopReceiptRun:
    """Filesystem locations for one recorded Tau loop run."""

    run_id: str
    run_dir: Path
    contract_path: Path
    events_path: Path
    current_state_path: Path
    transport_dag_evidence_path: Path
    final_receipt_path: Path
    node_result_path: Path


@dataclass(frozen=True, slots=True)
class LoopReceiptConfig:
    """Opt-in configuration for recording one Tau run as Loop2 artifacts."""

    root_dir: Path
    node_id: str
    allowed_globs: tuple[str, ...]
    checks: tuple[str, ...]
    max_attempts: int = 1
    backend: str = "fixture"
    backend_config: Mapping[str, object] | None = None
    required_changed_globs: tuple[str, ...] = ()
    mocked: bool = True
    live: bool = False
    changed_files: tuple[str, ...] = ()
    proof_scope: str = "one bounded Tau loop recording"
    proves: tuple[str, ...] = (
        "Tau recorded one prompt run as Loop2-compatible artifacts.",
        "Tau executed configured local checks and captured their stdout/stderr artifacts.",
    )
    does_not_prove: tuple[str, ...] = (
        "Loop2 runner execution",
        "live Scillm/OpenCode transport behavior",
    )


@dataclass(frozen=True, slots=True)
class LoopPeerSwitchboardEmitResult:
    """Result of emitting a Tau peer handoff through pi-mono switchboard."""

    ok: bool
    switchboard_url: str
    status_code: int | None
    response: dict[str, object] | None
    request: dict[str, object]
    errors: tuple[str, ...] = ()


class LoopReceiptRecorder:
    """Append Tau agent events to a JSONL ledger and update current state."""

    def __init__(self, run: LoopReceiptRun) -> None:
        self.run = run
        self._sequence = 0
        self._last_event_type: str | None = None
        self._state = "running"

    @classmethod
    def create(
        cls,
        *,
        root_dir: Path,
        run_id: str | None = None,
    ) -> LoopReceiptRecorder:
        """Create a new run directory and recorder under `root_dir`."""

        selected_run_id = run_id or new_loop_receipt_run_id()
        run_dir = root_dir / selected_run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        contract_path = run_dir / "contract.json"
        events_path = run_dir / "events.jsonl"
        current_state_path = run_dir / "current-state.json"
        transport_dag_evidence_path = run_dir / "transport-dag-evidence.json"
        final_receipt_path = run_dir / "final-receipt.json"
        node_result_path = run_dir / "node-result.json"
        events_path.touch(exist_ok=False)
        recorder = cls(
            LoopReceiptRun(
                run_id=selected_run_id,
                run_dir=run_dir,
                contract_path=contract_path,
                events_path=events_path,
                current_state_path=current_state_path,
                transport_dag_evidence_path=transport_dag_evidence_path,
                final_receipt_path=final_receipt_path,
                node_result_path=node_result_path,
            )
        )
        recorder.write_current_state()
        return recorder

    @property
    def event_count(self) -> int:
        """Return the number of events recorded by this recorder."""

        return self._sequence

    def record(self, event: AgentEvent) -> dict[str, object]:
        """Append one event row and return the serialized row."""

        self._sequence += 1
        self._last_event_type = event.type
        if event.type == "agent_end":
            self._state = "ended"
        elif event.type == "error":
            recoverable = getattr(event, "recoverable", False)
            if not recoverable:
                self._state = "failed"
        row: dict[str, object] = {
            "schema": LOOP_RECEIPT_EVENTS_SCHEMA,
            "run_id": self.run.run_id,
            "sequence": self._sequence,
            "timestamp": _now(),
            "event": event.model_dump(mode="json"),
        }
        with self.run.events_path.open("a", encoding="utf-8") as events_file:
            events_file.write(json.dumps(row, sort_keys=True) + "\n")
        self.write_current_state()
        return row

    def emit_loop2_event(
        self,
        event_type: str,
        *,
        node_id: str,
        status: str | None = None,
        message: str = "",
        attempt: int | None = None,
        data: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Append one native Loop2 event row and return the serialized row."""

        self._sequence += 1
        self._last_event_type = event_type
        if status in {"failed", "blocked", "error"}:
            self._state = "failed"
        elif status == "running":
            self._state = "running"
        elif status in {"completed", "accepted", "pass"}:
            self._state = "ended"
        timestamp = _now()
        row: dict[str, object] = {
            "schema": LOOP2_EVENT_SCHEMA,
            "run_id": self.run.run_id,
            "node_id": node_id,
            "event_id": f"{self.run.run_id}:{self._sequence:04d}:tau-loop2",
            "event_type": event_type,
            "ts": _timestamp(timestamp),
            "iso_time": _iso_z(timestamp),
            "status": status,
            "message": message,
            "data": dict(data or {}),
        }
        if attempt is not None:
            row["attempt"] = attempt
        with self.run.events_path.open("a", encoding="utf-8") as events_file:
            events_file.write(json.dumps(row, sort_keys=True) + "\n")
        self.write_current_state()
        return row

    def current_state(self) -> dict[str, object]:
        """Return the current monitor snapshot for this run."""

        return {
            "schema": LOOP_RECEIPT_CURRENT_STATE_SCHEMA,
            "run_id": self.run.run_id,
            "state": self._state,
            "event_count": self._sequence,
            "last_event_type": self._last_event_type,
            "events_path": str(self.run.events_path),
            "updated_at": _now(),
        }

    def write_current_state(self) -> dict[str, object]:
        """Write and return `current-state.json`."""

        state = self.current_state()
        self.run.current_state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return state

    def contract(
        self,
        *,
        node_id: str,
        objective: str,
        repo: Path,
        allowed_globs: Sequence[str],
        checks: Sequence[str],
        max_attempts: int,
        backend: str,
        backend_config: Mapping[str, object] | None = None,
        required_changed_globs: Sequence[str] = (),
        run_root: Path | None = None,
    ) -> dict[str, object]:
        """Return a normalized Loop2 repair-node contract payload."""

        contract: dict[str, object] = {
            "schema": LOOP_RECEIPT_CONTRACT_SCHEMA,
            "node_id": node_id,
            "objective": objective,
            "repo": str(repo),
            "allowed_globs": list(allowed_globs),
            "required_changed_globs": list(required_changed_globs),
            "checks": list(checks),
            "max_attempts": max_attempts,
            "backend": backend,
            "run_root": str(run_root or self.run.run_dir.parent),
        }
        if backend == "scillm" and backend_config:
            contract["scillm"] = dict(backend_config)
        return contract

    def write_contract(
        self,
        *,
        node_id: str,
        objective: str,
        repo: Path,
        allowed_globs: Sequence[str],
        checks: Sequence[str],
        max_attempts: int,
        backend: str,
        backend_config: Mapping[str, object] | None = None,
        required_changed_globs: Sequence[str] = (),
        run_root: Path | None = None,
    ) -> dict[str, object]:
        """Write and return `contract.json`."""

        contract = self.contract(
            node_id=node_id,
            objective=objective,
            repo=repo,
            allowed_globs=allowed_globs,
            checks=checks,
            max_attempts=max_attempts,
            backend=backend,
            backend_config=backend_config,
            required_changed_globs=required_changed_globs,
            run_root=run_root,
        )
        self.run.contract_path.write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return contract

    def final_receipt(
        self,
        *,
        node_id: str,
        mocked: bool,
        live: bool,
        status: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        checks: Sequence[Mapping[str, object]] = (),
        changed_files: Sequence[str] = (),
        proof_scope: str = "one bounded Tau loop recording",
        proves: Sequence[str] = (),
        does_not_prove: Sequence[str] = (),
        artifacts: Mapping[str, object] | None = None,
        scillm: Mapping[str, object] | None = None,
        error: str = "",
    ) -> dict[str, object]:
        """Return the final receipt payload for this run."""

        resolved_artifacts = {
            "run_dir": str(self.run.run_dir),
            "contract": str(self.run.contract_path),
            "events": str(self.run.events_path),
            "current_state": str(self.run.current_state_path),
            "transport_dag_evidence": str(self.run.transport_dag_evidence_path),
            "final_receipt": str(self.run.final_receipt_path),
            "node_result": str(self.run.node_result_path),
        }
        if artifacts:
            resolved_artifacts.update(dict(artifacts))
        resolved_scillm = dict(scillm or {})
        if provider is not None:
            resolved_scillm["provider"] = provider
        if model is not None:
            resolved_scillm["model"] = model
        receipt: dict[str, object] = {
            "schema": LOOP_RECEIPT_FINAL_RECEIPT_SCHEMA,
            "run_id": self.run.run_id,
            "node_id": node_id,
            "status": _loop2_status(status or self._state),
            "mocked": mocked,
            "live": live,
            "proof_scope": proof_scope,
            "changed_files": list(changed_files),
            "checks": [dict(check) for check in checks],
            "claims": {
                "proves": list(proves),
                "does_not_prove": list(does_not_prove),
            },
            "artifacts": resolved_artifacts,
            "scillm": resolved_scillm,
            "error": error,
        }
        return receipt

    def write_final_receipt(
        self,
        *,
        node_id: str,
        mocked: bool,
        live: bool,
        status: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        checks: Sequence[Mapping[str, object]] = (),
        changed_files: Sequence[str] = (),
        proof_scope: str = "one bounded Tau loop recording",
        proves: Sequence[str] = (),
        does_not_prove: Sequence[str] = (),
        artifacts: Mapping[str, object] | None = None,
        scillm: Mapping[str, object] | None = None,
        error: str = "",
    ) -> dict[str, object]:
        """Write and return `final-receipt.json`."""

        receipt = self.final_receipt(
            node_id=node_id,
            mocked=mocked,
            live=live,
            status=status,
            provider=provider,
            model=model,
            checks=checks,
            changed_files=changed_files,
            proof_scope=proof_scope,
            proves=proves,
            does_not_prove=does_not_prove,
            artifacts=artifacts,
            scillm=scillm,
            error=error,
        )
        self.run.final_receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return receipt

    def node_result(
        self,
        *,
        node_id: str,
        status: str | None = None,
        mocked: bool,
        live: bool,
        final_receipt_path: Path | None = None,
        transport_dag_evidence_path: Path | None = None,
        events_path: Path | None = None,
        checks: Sequence[Mapping[str, object]] = (),
        changed_files: Sequence[str] = (),
    ) -> dict[str, object]:
        """Return the Loop2 harness-facing node result payload for this run."""

        return {
            "schema": LOOP_RECEIPT_NODE_RESULT_SCHEMA,
            "node_id": node_id,
            "status": _loop2_status(status or self._state),
            "run_id": self.run.run_id,
            "final_receipt": str(final_receipt_path or self.run.final_receipt_path),
            "transport_dag_evidence": str(
                transport_dag_evidence_path or self.run.transport_dag_evidence_path
            ),
            "events": str(events_path or self.run.events_path),
            "changed_files": list(changed_files),
            "checks": [dict(check) for check in checks],
            "mocked": mocked,
            "live": live,
        }

    def write_node_result(
        self,
        *,
        node_id: str,
        status: str | None = None,
        mocked: bool,
        live: bool,
        final_receipt_path: Path | None = None,
        transport_dag_evidence_path: Path | None = None,
        events_path: Path | None = None,
        checks: Sequence[Mapping[str, object]] = (),
        changed_files: Sequence[str] = (),
    ) -> dict[str, object]:
        """Write and return `node-result.json`."""

        result = self.node_result(
            node_id=node_id,
            status=status,
            mocked=mocked,
            live=live,
            final_receipt_path=final_receipt_path,
            transport_dag_evidence_path=transport_dag_evidence_path,
            events_path=events_path,
            checks=checks,
            changed_files=changed_files,
        )
        self.run.node_result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return result

    def transport_dag_evidence(self) -> dict[str, object]:
        """Return TransportRoom-compatible DAG evidence for this run."""

        contract = _read_json(self.run.contract_path)
        receipt = _read_json(self.run.final_receipt_path)
        events = _read_event_rows(self.run.events_path)
        node_id = str(contract.get("node_id") or receipt.get("node_id") or self.run.run_id)
        receipt_status = str(receipt.get("status") or _loop2_status(self._state))
        event_types = [_event_type(event) for event in events]

        def timing(*selected_types: str) -> dict[str, str]:
            selected = [event for event in events if _event_type(event) in selected_types]
            if not selected:
                return {}
            return {
                "started_at": _event_time(selected[0]),
                "completed_at": _event_time(selected[-1]),
            }

        worker_status = "failed" if receipt_status in {"FAILED", "BLOCKED"} else "accepted"
        check_rows = receipt.get("checks") if isinstance(receipt.get("checks"), list) else []
        checks_status = "waiting"
        if check_rows:
            mapped_checks = [check for check in check_rows if isinstance(check, Mapping)]
            checks_status = (
                "accepted"
                if all(int(dict(check).get("exit_code", 1)) == 0 for check in mapped_checks)
                else "failed"
            )
        elif receipt:
            checks_status = "accepted" if receipt_status == "PASS" else "failed"
        nodes = [
            {
                "id": "contract",
                "label": "Contract",
                "status": "accepted" if contract else "waiting",
                "semantic_call_type": "contract",
                "skills": ["loop2"],
                "request_summary": node_id,
                "response": str(contract.get("objective") or ""),
            },
            {
                "id": "tau_loop",
                "label": "Tau loop",
                "status": worker_status,
                "semantic_call_type": "tau_loop",
                "skills": ["tau", "loop2"],
                "provider": str((receipt.get("scillm") or {}).get("provider") or ""),
                "model": str((receipt.get("scillm") or {}).get("model") or ""),
                "request_summary": "Tau loop recorded as one bounded Loop2 node",
                "response": receipt_status,
                **timing("agent_start", "message_delta", "agent_end", "error"),
            },
            {
                "id": "checks",
                "label": "Local checks",
                "status": checks_status,
                "semantic_call_type": "verification",
                "skills": ["loop2"],
                "request_summary": ", ".join(str(item) for item in contract.get("checks", [])),
                "response": f"{len(check_rows)} checks",
                **timing("checks_started", "check_finished", "checks_finished"),
            },
            {
                "id": "receipt",
                "label": "Receipt",
                "status": "accepted" if receipt_status == "PASS" else "failed",
                "semantic_call_type": "aggregate",
                "skills": ["loop2"],
                "request_summary": "final receipt and node result",
                "response": receipt_status,
                **timing("receipt_written"),
            },
        ]
        return {
            "schema": LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA,
            "found": True,
            "transport_run_id": self.run.run_id,
            "graph_id": f"loop2:{node_id}",
            "proof_path": str(self.run.final_receipt_path),
            "nodes": nodes,
            "edges": [
                {"from": "contract", "to": "tau_loop"},
                {"from": "tau_loop", "to": "checks"},
                {"from": "checks", "to": "receipt"},
            ],
            "layers": [["contract"], ["tau_loop"], ["checks"], ["receipt"]],
            "not_proven": (receipt.get("claims") or {}).get("does_not_prove", []),
            "progress_stream": {
                "state": "live_or_historical",
                "event_count": len(events),
                "event_types": sorted({event_type for event_type in event_types if event_type}),
                "events_path": str(self.run.events_path),
                "last_event_type": event_types[-1] if event_types else None,
            },
        }

    def write_transport_dag_evidence(self) -> dict[str, object]:
        """Write and return `transport-dag-evidence.json`."""

        evidence = self.transport_dag_evidence()
        self.run.transport_dag_evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return evidence

    def harness_peer_message(
        self,
        *,
        target_harness: str = "*",
        monitor_base_url: str | None = None,
    ) -> dict[str, object]:
        """Return a cross-harness handoff envelope for this Tau run."""

        return build_loop_harness_peer_message(
            self.run.run_dir,
            target_harness=target_harness,
            monitor_base_url=monitor_base_url,
        )

    def write_harness_peer_message(
        self,
        *,
        target_harness: str = "*",
        monitor_base_url: str | None = None,
    ) -> dict[str, object]:
        """Write and return `harness-peer-message.json`."""

        message = self.harness_peer_message(
            target_harness=target_harness,
            monitor_base_url=monitor_base_url,
        )
        (self.run.run_dir / "harness-peer-message.json").write_text(
            json.dumps(message, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return message


def loop_receipt_summary(run_dir: Path) -> dict[str, object]:
    """Return a fail-closed monitor summary for one receipt run directory."""

    required_paths = {
        "contract": run_dir / "contract.json",
        "events": run_dir / "events.jsonl",
        "current_state": run_dir / "current-state.json",
        "transport_dag_evidence": run_dir / "transport-dag-evidence.json",
        "final_receipt": run_dir / "final-receipt.json",
        "node_result": run_dir / "node-result.json",
    }
    missing = [name for name, path in required_paths.items() if not path.exists()]
    if missing:
        return {
            "schema": "tau.loop_receipt.summary.v1",
            "found": False,
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "missing_artifacts": missing,
        }

    contract = _redact_summary_secrets(_read_json(required_paths["contract"]))
    current_state = _read_json(required_paths["current_state"])
    final_receipt = _read_json(required_paths["final_receipt"])
    node_result = _read_json(required_paths["node_result"])
    transport_dag_evidence = _read_json(required_paths["transport_dag_evidence"])
    events = _read_event_rows(required_paths["events"])
    optional_artifacts: dict[str, str] = {}
    tau_sanitization_path = run_dir / "tau-sanitization.json"
    tau_sanitization: dict[str, object] | None = None
    if tau_sanitization_path.exists():
        optional_artifacts["tau_sanitization"] = str(tau_sanitization_path)
        tau_sanitization = _read_json(tau_sanitization_path)
    harness_peer_message_path = run_dir / "harness-peer-message.json"
    harness_peer_message: dict[str, object] | None = None
    if harness_peer_message_path.exists():
        optional_artifacts["harness_peer_message"] = str(harness_peer_message_path)
        harness_peer_message = _read_json(harness_peer_message_path)
    summary = {
        "schema": "tau.loop_receipt.summary.v1",
        "found": True,
        "run_id": str(final_receipt.get("run_id") or node_result.get("run_id") or run_dir.name),
        "run_dir": str(run_dir),
        "node_id": str(
            final_receipt.get("node_id")
            or node_result.get("node_id")
            or contract.get("node_id")
            or ""
        ),
        "status": str(final_receipt.get("status") or node_result.get("status") or ""),
        "mocked": bool(final_receipt.get("mocked", False)),
        "live": bool(final_receipt.get("live", False)),
        "event_count": len(events),
        "last_event_type": _event_type(events[-1]) if events else None,
        "check_count": len(final_receipt.get("checks", []))
        if isinstance(final_receipt.get("checks"), list)
        else 0,
        "artifacts": {
            **{name: str(path) for name, path in required_paths.items()},
            **optional_artifacts,
        },
        "contract": contract,
        "current_state": current_state,
        "final_receipt": final_receipt,
        "node_result": node_result,
        "transport_dag_evidence": transport_dag_evidence,
    }
    if tau_sanitization is not None:
        summary["tau_sanitization"] = tau_sanitization
    if harness_peer_message is not None:
        summary["harness_peer_message"] = harness_peer_message
    return summary


def build_loop_harness_peer_message(
    run_dir: Path,
    *,
    target_harness: str = "*",
    monitor_base_url: str | None = None,
) -> dict[str, object]:
    """Build a fail-closed handoff envelope for another harness."""

    summary = loop_receipt_summary(run_dir)
    if summary.get("found") is not True:
        missing_artifacts = list(summary.get("missing_artifacts", []))
        return {
            "schema": LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA,
            "message_type": "loop2_receipt_unavailable",
            "producer": {
                "harness": "tau",
                "run_id": run_dir.name,
                "run_dir": str(run_dir),
            },
            "target": {"harness": target_harness},
            "status": "MISSING_ARTIFACTS",
            "ready": False,
            "missing_artifacts": missing_artifacts,
            "switchboard": _build_switchboard_peer_envelope(
                message_type="loop2_receipt_unavailable",
                target_harness=target_harness,
                subject=f"Tau Loop2 receipt unavailable: {run_dir.name}",
                message=(
                    "Tau Loop2 receipt artifacts are not ready; consuming harnesses must "
                    "fail closed until required artifacts exist."
                ),
                metadata={
                    "schema": LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA,
                    "ready": False,
                    "run_id": run_dir.name,
                    "run_dir": str(run_dir),
                    "missing_artifacts": missing_artifacts,
                    "claims": {
                        "proves": [],
                        "does_not_prove": [
                            "Tau peer handoff cannot proceed until required artifacts exist."
                        ],
                    },
                },
                priority="high",
                msg_type="alert",
            ),
            "claims": {
                "proves": [],
                "does_not_prove": [
                    "Tau peer handoff cannot proceed until required artifacts exist."
                ],
            },
        }

    run_id = str(summary["run_id"])
    node_id = str(summary["node_id"])
    base = (monitor_base_url or "").rstrip("/")
    endpoints = {
        "summary": f"/api/loop2/runs/{run_id}/summary",
        "transport_dag_evidence": f"/api/loop2/runs/{run_id}/transport-dag-evidence",
        "events": f"/api/loop2/runs/{run_id}/events",
        "events_stream": f"/api/loop2/runs/{run_id}/events/stream",
        "peer_message": f"/api/loop2/runs/{run_id}/peer-message",
    }
    if base:
        endpoints = {key: f"{base}{path}" for key, path in endpoints.items()}
    final_receipt = (
        summary.get("final_receipt") if isinstance(summary.get("final_receipt"), dict) else {}
    )
    claims = final_receipt.get("claims") if isinstance(final_receipt.get("claims"), dict) else {}
    proves = list(claims.get("proves", [])) if isinstance(claims.get("proves"), list) else []
    does_not_prove = (
        list(claims.get("does_not_prove", []))
        if isinstance(claims.get("does_not_prove"), list)
        else []
    )
    return {
        "schema": LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA,
        "message_type": "loop2_receipt_available",
        "created_at": _now(),
        "producer": {
            "harness": "tau",
            "run_id": run_id,
            "node_id": node_id,
            "run_dir": str(run_dir),
        },
        "target": {"harness": target_harness},
        "status": str(summary["status"]),
        "ready": True,
        "mocked": bool(summary["mocked"]),
        "live": bool(summary["live"]),
        "proof_scope": str(final_receipt.get("proof_scope") or ""),
        "schemas": {
            "summary": "loop2.summary.v1",
            "events": "loop2.events.v1",
            "event": LOOP2_EVENT_SCHEMA,
            "transport_dag_evidence": LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA,
            "final_receipt": LOOP_RECEIPT_FINAL_RECEIPT_SCHEMA,
            "node_result": LOOP_RECEIPT_NODE_RESULT_SCHEMA,
        },
        "capabilities": [
            "read_summary",
            "read_transport_dag_evidence",
            "replay_events",
            "stream_events",
            "read_final_receipt",
        ],
        "artifacts": dict(summary["artifacts"]) if isinstance(summary.get("artifacts"), dict) else {},
        "endpoints": endpoints,
        "consumer_checks": [
            "schema == tau.loop_harness_peer_message.v1",
            "ready is true",
            "transport_dag_evidence schema == ux_lab.transport_dag_run_evidence.v1",
            "final_receipt mocked/live flags are displayed without upgrading proof scope",
            "claims.does_not_prove is preserved by the consuming harness",
            "switchboard.metadata.claims.does_not_prove is preserved when relayed",
        ],
        "switchboard": _build_switchboard_peer_envelope(
            message_type="loop2_receipt_available",
            target_harness=target_harness,
            subject=f"Tau Loop2 receipt available: {run_id}",
            message=(
                "Tau Loop2 receipt is available. Consume the peer metadata, read the "
                "transport DAG evidence endpoint, and preserve claims.does_not_prove."
            ),
            metadata={
                "schema": LOOP_RECEIPT_HARNESS_PEER_MESSAGE_SCHEMA,
                "ready": True,
                "run_id": run_id,
                "node_id": node_id,
                "run_dir": str(run_dir),
                "status": str(summary["status"]),
                "mocked": bool(summary["mocked"]),
                "live": bool(summary["live"]),
                "proof_scope": str(final_receipt.get("proof_scope") or ""),
                "schemas": {
                    "transport_dag_evidence": LOOP_RECEIPT_TRANSPORT_DAG_EVIDENCE_SCHEMA,
                    "final_receipt": LOOP_RECEIPT_FINAL_RECEIPT_SCHEMA,
                    "node_result": LOOP_RECEIPT_NODE_RESULT_SCHEMA,
                },
                "endpoints": endpoints,
                "claims": {
                    "proves": proves,
                    "does_not_prove": does_not_prove,
                },
            },
        ),
        "claims": {
            "proves": proves,
            "does_not_prove": does_not_prove,
        },
    }


def build_loop_peer_switchboard_emit_request(
    run_dir: Path,
    *,
    target_harness: str = "pi-mono",
    monitor_base_url: str | None = None,
) -> dict[str, object]:
    """Build the JSON body accepted by pi-mono switchboard `POST /emit`."""
    peer = build_loop_harness_peer_message(
        run_dir,
        target_harness=target_harness,
        monitor_base_url=monitor_base_url,
    )
    switchboard = peer.get("switchboard")
    if not isinstance(switchboard, Mapping):
        raise RuntimeError("Tau peer message did not include a switchboard envelope")
    metadata = switchboard.get("metadata")
    if not isinstance(metadata, Mapping):
        raise RuntimeError("Tau peer switchboard envelope did not include metadata")
    request = {
        "from": _required_string(switchboard, "from"),
        "to": _required_string(switchboard, "to"),
        "message": _required_string(switchboard, "message"),
        "type": _required_string(switchboard, "type"),
        "priority": _required_string(switchboard, "priority"),
        "subject": _required_string(switchboard, "subject"),
        "metadata": dict(metadata),
    }
    return request


def emit_loop_peer_to_switchboard(
    run_dir: Path,
    *,
    switchboard_url: str = "http://127.0.0.1:7890",
    target_harness: str = "pi-mono",
    monitor_base_url: str | None = None,
    timeout_seconds: float = 10.0,
) -> LoopPeerSwitchboardEmitResult:
    """POST a Tau peer handoff to pi-mono switchboard."""
    request_payload = build_loop_peer_switchboard_emit_request(
        run_dir,
        target_harness=target_harness,
        monitor_base_url=monitor_base_url,
    )
    emit_url = f"{switchboard_url.rstrip('/')}/emit"
    request = Request(
        emit_url,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            status_code = int(response.status)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        payload = _json_object_or_none(body)
        return LoopPeerSwitchboardEmitResult(
            ok=False,
            switchboard_url=emit_url,
            status_code=exc.code,
            response=payload,
            request=request_payload,
            errors=(f"HTTP {exc.code}: {body[:240]}",),
        )
    except (OSError, URLError, TimeoutError) as exc:
        return LoopPeerSwitchboardEmitResult(
            ok=False,
            switchboard_url=emit_url,
            status_code=None,
            response=None,
            request=request_payload,
            errors=(str(exc),),
        )

    ok = 200 <= status_code < 300 and isinstance(payload, dict) and payload.get("success") is True
    return LoopPeerSwitchboardEmitResult(
        ok=ok,
        switchboard_url=emit_url,
        status_code=status_code,
        response=payload if isinstance(payload, dict) else None,
        request=request_payload,
        errors=() if ok else (f"unexpected switchboard response: {payload}",),
    )


def _build_switchboard_peer_envelope(
    *,
    message_type: str,
    target_harness: str,
    subject: str,
    message: str,
    metadata: Mapping[str, object],
    priority: str = "normal",
    msg_type: str = "info",
) -> dict[str, object]:
    """Build the pi-mono switchboard message shape for peer handoff."""
    return {
        "id": f"tau-{message_type}-{uuid4().hex}",
        "from": "tau",
        "to": target_harness,
        "type": msg_type,
        "priority": priority,
        "subject": subject,
        "message": message,
        "timestamp": _now(),
        "metadata": dict(metadata),
    }


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Tau peer switchboard envelope missing {key!r}")
    return value


def _json_object_or_none(text: str) -> dict[str, object] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _redact_summary_secrets(payload: dict[str, object]) -> dict[str, object]:
    redacted = dict(payload)
    scillm_config = redacted.get("scillm")
    if isinstance(scillm_config, Mapping):
        redacted_scillm = dict(scillm_config)
        api_key = redacted_scillm.get("api_key")
        if isinstance(api_key, str) and api_key and not api_key.startswith("<redacted"):
            redacted_scillm["api_key"] = "<redacted-scillm-api-key>"
        redacted["scillm"] = redacted_scillm
    return redacted


def backfill_loop_receipt_artifact_index(run_dir: Path) -> dict[str, object]:
    """Backfill missing standard artifact paths in `final-receipt.json`."""

    resolved = run_dir.expanduser().resolve()
    final_receipt_path = resolved / "final-receipt.json"
    if not final_receipt_path.exists():
        return {
            "schema": "tau.loop_receipt.artifact_index_backfill.v1",
            "ok": False,
            "run_dir": str(resolved),
            "changed": False,
            "added_keys": [],
            "backup_path": "",
            "errors": [f"missing final receipt: {final_receipt_path}"],
        }
    receipt = _read_json(final_receipt_path)
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        receipt["artifacts"] = artifacts
    expected = {
        "run_dir": str(resolved),
        "contract": str(resolved / "contract.json"),
        "events": str(resolved / "events.jsonl"),
        "current_state": str(resolved / "current-state.json"),
        "transport_dag_evidence": str(resolved / "transport-dag-evidence.json"),
        "final_receipt": str(final_receipt_path),
        "node_result": str(resolved / "node-result.json"),
    }
    added_keys = [key for key in expected if artifacts.get(key) != expected[key]]
    if not added_keys:
        return {
            "schema": "tau.loop_receipt.artifact_index_backfill.v1",
            "ok": True,
            "run_dir": str(resolved),
            "changed": False,
            "added_keys": [],
            "backup_path": "",
            "errors": [],
        }
    backup_path = final_receipt_path.with_suffix(".json.before-artifact-index-backfill")
    backup_path.write_text(final_receipt_path.read_text(encoding="utf-8"), encoding="utf-8")
    for key in added_keys:
        artifacts[key] = expected[key]
    final_receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema": "tau.loop_receipt.artifact_index_backfill.v1",
        "ok": True,
        "run_dir": str(resolved),
        "changed": True,
        "added_keys": added_keys,
        "backup_path": str(backup_path),
        "errors": [],
    }


def loop_receipt_loop2_events(run_dir: Path) -> list[dict[str, object]]:
    """Project Tau receipt event rows into Loop2 public event rows."""

    contract = _read_json(run_dir / "contract.json")
    final_receipt = _read_json(run_dir / "final-receipt.json")
    node_result = _read_json(run_dir / "node-result.json")
    node_id = str(
        final_receipt.get("node_id") or node_result.get("node_id") or contract.get("node_id") or ""
    )
    run_id = str(final_receipt.get("run_id") or node_result.get("run_id") or run_dir.name)
    rows = _read_event_rows(run_dir / "events.jsonl")
    events: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        if row.get("schema") == LOOP2_EVENT_SCHEMA:
            events.append(dict(row))
            continue
        sequence = _event_sequence(row, index)
        event_type = _event_type(row)
        ts, iso_time = _loop2_event_time(row)
        events.append(
            {
                "schema": LOOP2_EVENT_SCHEMA,
                "run_id": run_id,
                "node_id": node_id,
                "event_id": f"{run_id}:{sequence:04d}:tau",
                "event_type": event_type,
                "ts": ts,
                "iso_time": iso_time,
                "status": _loop2_event_status(event_type),
                "message": _loop2_event_message(row),
                "data": {"tau_event": row},
            }
        )
    return events


def new_loop_receipt_run_id() -> str:
    """Return a unique Tau loop receipt run id."""

    return f"tau-loop-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp(raw: str) -> float:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).timestamp()


def _iso_z(raw: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _loop2_status(status: str) -> str:
    normalized = status.upper()
    if normalized in {"PASS", "BLOCKED", "FAILED"}:
        return normalized
    if status in {"ended", "accepted", "completed"}:
        return "PASS"
    if status in {"failed", "error"}:
        return "FAILED"
    return "BLOCKED"


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _read_event_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _event_type(event: Mapping[str, object]) -> str:
    event_type = event.get("event_type")
    if event_type is not None:
        return str(event_type)
    payload = event.get("event")
    if isinstance(payload, Mapping):
        return str(payload.get("type") or "")
    return ""


def _event_time(event: Mapping[str, object]) -> str:
    return str(event.get("iso_time") or event.get("timestamp") or "")


def _event_sequence(event: Mapping[str, object], fallback: int) -> int:
    value = event.get("sequence")
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _loop2_event_time(event: Mapping[str, object]) -> tuple[float, str]:
    raw = _event_time(event)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0, raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    normalized = parsed.astimezone(UTC)
    return normalized.timestamp(), normalized.isoformat().replace("+00:00", "Z")


def _loop2_event_status(event_type: str) -> str | None:
    if event_type == "agent_end":
        return "completed"
    if event_type == "error":
        return "failed"
    if event_type in {"agent_start", "turn_start"}:
        return "running"
    return None


def _loop2_event_message(event: Mapping[str, object]) -> str:
    payload = event.get("event")
    if not isinstance(payload, Mapping):
        return ""
    for key in ("message", "delta", "content"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""
