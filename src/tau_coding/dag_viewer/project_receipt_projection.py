"""Read-only DAG viewer projection for project-DAG receipt directories."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_viewer.receipt_index import IndexedReceipt, ReceiptIndex
from tau_coding.dag_viewer.redaction import redact_for_viewer
from tau_coding.run_ledger import read_ledger, verify_ledger

PROJECT_RECEIPT_PROOF_SCOPE = {
    "proves": [
        "Tau projected an authoritative project-DAG receipt and progress artifact.",
        "Visible node, edge, evidence, blocker, and terminal states are source-backed.",
        "The source DAG contract is exposed read-only and is not editable from the viewer.",
    ],
    "does_not_prove": [
        "Provider/model semantic quality.",
        "Future route correctness.",
        "GitHub mutation or ticket closure.",
        "Human acceptance of the full immutable Tau goal.",
    ],
}


@dataclass(frozen=True, slots=True)
class ProjectReceiptProjection:
    run_dir: Path
    contract_path: Path
    receipt_path: Path
    progress_path: Path
    contract: dict[str, Any]
    receipt: dict[str, Any]
    progress: dict[str, Any]
    receipt_index: ReceiptIndex
    run_id: str
    plan_sha256: str

    @classmethod
    def load(cls, run_dir: Path) -> "ProjectReceiptProjection":
        root = run_dir.expanduser().resolve()
        receipt_path = root / "dag-receipt.json"
        progress_path = root / "dag-progress.json"
        receipt = _read_object(receipt_path, code="dag_viewer_project_receipt_invalid")
        if receipt.get("schema") != "tau.dag_receipt.v1":
            raise RuntimeError("dag_viewer_project_receipt_invalid")
        progress = _read_object(progress_path, code="dag_viewer_project_progress_invalid")
        if progress.get("schema") != "tau.dag_progress.v1":
            raise RuntimeError("dag_viewer_project_progress_invalid")
        contract_path = _contract_path(root, receipt)
        contract = _read_object(contract_path, code="dag_viewer_project_contract_invalid")
        if contract.get("schema") != "tau.dag_contract.v1":
            raise RuntimeError("dag_viewer_project_contract_invalid")
        if contract.get("dag_id") != receipt.get("dag_id"):
            raise RuntimeError("dag_viewer_project_contract_receipt_mismatch")
        plan_sha256 = canonical_sha256(contract)
        run_id = _run_id(root, receipt)
        return cls(
            run_dir=root,
            contract_path=contract_path,
            receipt_path=receipt_path,
            progress_path=progress_path,
            contract=contract,
            receipt=receipt,
            progress=progress,
            receipt_index=_build_project_receipt_index(root=root, receipt=receipt),
            run_id=run_id,
            plan_sha256=plan_sha256,
        )

    def manifest(self) -> dict[str, Any]:
        graph = {
            "nodes": [_plan_node(node) for node in _contract_nodes(self.contract)],
            "edges": [_plan_edge(edge, index) for index, edge in enumerate(_contract_edges(self.contract))],
            "terminals": [
                {"terminal_id": terminal, "kind": "human", "origin": "project_dag_contract"}
                for terminal in _terminal_ids(self.contract)
            ],
            "routes": [],
            "joins": [],
        }
        payload = {
            "schema": "tau.dag_view_manifest.v1",
            "run_id": self.run_id,
            "plan_id": str(self.contract.get("dag_id") or self.run_id),
            "plan_sha256": self.plan_sha256,
            "source_schema": "tau.dag_contract.v1",
            "source_sha256": self.plan_sha256,
            "source_available": True,
            "source_redacted": False,
            "source_dag": self.contract,
            "source_status": "AVAILABLE",
            "dag_plan": _dag_plan_payload(self.contract, graph, self.plan_sha256),
            "goal": _goal_projection(self.contract),
            "workflow": None,
            "graph": graph,
            "receipt_index": self.receipt_index.public_entries(),
            "ledger_summary": _ledger_summary(self.run_dir),
            "proof_scope": PROJECT_RECEIPT_PROOF_SCOPE,
        }
        return _redacted(payload)

    def snapshot(self, *, at_sequence: int | None = None) -> dict[str, Any]:
        events = self.events()
        selected_events = (
            tuple(event for event in events if int(event["seq"]) <= at_sequence)
            if at_sequence is not None
            else events
        )
        sequence = at_sequence if at_sequence is not None else len(events)
        nodes = self._live_nodes()
        edges = self._live_edges()
        terminals = self._live_terminals(edges)
        attention_items = self._attention_items(sequence)
        payload: dict[str, Any] = {
            "schema": "tau.dag_view_snapshot.v2",
            "run_id": self.run_id,
            "plan_sha256": self.plan_sha256,
            "journal_sequence": sequence,
            "view": {
                "mode": "HISTORICAL" if at_sequence is not None else "LIVE",
                "sequence": sequence,
                "sequence_created_at": selected_events[-1]["payload"].get("ts")
                if selected_events
                else None,
            },
            "run_status": str(self.receipt.get("status") or "UNKNOWN"),
            "run_verdict": self.receipt.get("verdict")
            if isinstance(self.receipt.get("verdict"), str)
            else None,
            "projection_state": "PROJECT_RECEIPT",
            "nodes": nodes,
            "edges": edges,
            "terminals": terminals,
            "routes": self._routes(edges),
            "joins": [],
            "corrections": [],
            "attention_items": attention_items,
            "highest_priority_attention_id": (
                attention_items[0]["attention_id"] if attention_items else None
            ),
            "run_summary": {
                "active_node_ids": [
                    node["node_id"]
                    for node in nodes
                    if node["scheduler"]["state"] in {"ready", "running"}
                ],
                "accepted_node_ids": [
                    node["node_id"] for node in nodes if node["admission"]["accepted"] is True
                ],
                "highest_priority_blocker": _highest_priority_blocker(nodes),
                "final_result": _final_result(self.receipt),
                "ledger": _ledger_summary(self.run_dir),
            },
            "recent_events": list(selected_events[-100:]),
            "proof_scope": PROJECT_RECEIPT_PROOF_SCOPE,
        }
        payload["snapshot_sha256"] = f"sha256:{canonical_sha256(payload)}"
        return _redacted(payload)

    def events(self) -> tuple[dict[str, Any], ...]:
        raw_events = self.progress.get("events")
        source = raw_events if isinstance(raw_events, list) else self.receipt.get("scheduler_events")
        events: list[dict[str, Any]] = []
        for index, raw in enumerate(source if isinstance(source, list) else [], start=1):
            if not isinstance(raw, dict):
                continue
            node_id = raw.get("node_id") or raw.get("selected_agent") or raw.get("join_node_id")
            events.append(
                {
                    "seq": index,
                    "event_type": str(raw.get("event") or raw.get("event_type") or "project_dag_event"),
                    "entity_type": "node" if isinstance(node_id, str) and node_id else "run",
                    "entity_id": str(node_id or self.run_id),
                    "attempt_id": None,
                    "payload": raw,
                }
            )
        if not events:
            events.append(
                {
                    "seq": 1,
                    "event_type": "project_dag_receipt_loaded",
                    "entity_type": "run",
                    "entity_id": self.run_id,
                    "attempt_id": None,
                    "payload": {
                        "status": self.receipt.get("status"),
                        "verdict": self.receipt.get("verdict"),
                    },
                }
            )
        return tuple(events)

    def explanation(
        self, kind: str, subject_id: str, *, at_sequence: int | None = None
    ) -> dict[str, Any]:
        sequence = at_sequence if at_sequence is not None else len(self.events())
        projected_state = str(self.receipt.get("status") or "UNKNOWN")
        reason_code = "project_receipt_projection"
        references: list[dict[str, Any]] = [
            {
                "reference_id": "dag-receipt",
                "kind": "RECEIPT",
                "path": str(self.receipt_path),
                "schema": self.receipt.get("schema"),
            },
            {
                "reference_id": "dag-progress",
                "kind": "PROGRESS",
                "path": str(self.progress_path),
                "schema": self.progress.get("schema"),
            },
        ]
        alerts = self.receipt.get("alerts")
        if isinstance(alerts, list) and alerts:
            reason_code = str(alerts[0].get("code") or reason_code)
            projected_state = "BLOCKED"
        return {
            "schema": "tau.dag_causal_explanation.v1",
            "explanation_id": f"project-receipt:{kind.lower()}:{subject_id}",
            "run_id": self.run_id,
            "as_of_sequence": sequence,
            "subject": {"kind": kind, "id": subject_id},
            "projected_state": projected_state,
            "reason_code": reason_code,
            "summary_code": reason_code,
            "trigger_sequence": sequence,
            "references": references,
            "chain": [
                {"step": 1, "relation": "loaded", "reference_id": "dag-receipt"},
                {"step": 2, "relation": "projected", "reference_id": "dag-progress"},
            ],
            "proof_scope": PROJECT_RECEIPT_PROOF_SCOPE,
        }

    def _live_nodes(self) -> list[dict[str, Any]]:
        progress_by_node = {
            str(item.get("node_id")): item
            for item in self.progress.get("node_progress", [])
            if isinstance(item, dict) and isinstance(item.get("node_id"), str)
        }
        node_attempts = self.receipt.get("node_attempts")
        attempts = node_attempts if isinstance(node_attempts, dict) else {}
        alerts = self.receipt.get("alerts")
        alert_items = alerts if isinstance(alerts, list) else []
        alert_codes = [
            str(item.get("code"))
            for item in alert_items
            if isinstance(item, dict) and item.get("code")
        ]
        nodes: list[dict[str, Any]] = []
        for node in _contract_nodes(self.contract):
            node_id = str(node["id"])
            progress = progress_by_node.get(node_id, {})
            scheduler_state = _scheduler_state(progress.get("status"), self.receipt.get("status"))
            accepted = scheduler_state == "settled" and not alert_codes
            nodes.append(
                {
                    "node_id": node_id,
                    "node_kind": str(node.get("executor") or "local"),
                    "scheduler": {
                        "state": scheduler_state,
                        "attempt": int(attempts.get(node_id, progress.get("attempt") or 0) or 0),
                        "max_attempts": int(node.get("max_attempts") or 1),
                    },
                    "runtime": {
                        "state": "COMPLETED" if scheduler_state == "settled" else scheduler_state.upper(),
                        "liveness": "COMPLETE" if scheduler_state == "settled" else "UNKNOWN",
                        "confidence": "SOURCE_BACKED",
                        "last_event_id": str(progress.get("last_event_at"))
                        if progress.get("last_event_at")
                        else None,
                    },
                    "admission": {
                        "state": "accepted" if accepted else "blocked" if alert_codes else "pending",
                        "accepted": accepted,
                        "receipt_refs": _node_receipt_refs(self.receipt_index, node_id),
                    },
                    "result": {
                        "summary": _node_summary(self.receipt, node_id),
                        "accepted_output": _node_output(self.receipt, node_id),
                        "blocker_codes": alert_codes if not accepted else [],
                        "started_at": progress.get("last_event_at")
                        if isinstance(progress.get("last_event_at"), str)
                        else None,
                        "finished_at": progress.get("last_event_at")
                        if isinstance(progress.get("last_event_at"), str)
                        else None,
                        "duration_seconds": None,
                        "cost_accounting": _empty_cost_accounting(),
                        "budget_blocker": None,
                    },
                    "transaction": _transaction(self.receipt, node_id, attempts),
                    "correction": None,
                    "causal_explanation_id": f"project-receipt:node:{node_id}",
                    "updated_sequence": len(self.events()),
                }
            )
        return nodes

    def _live_edges(self) -> list[dict[str, Any]]:
        observed = {
            (str(item.get("from_node") or item.get("from_agent")), str(item.get("to_node") or item.get("to_agent")))
            for item in self.receipt.get("observed_edges", [])
            if isinstance(item, dict)
        }
        return [
            {
                "edge_id": _edge_id(edge, index),
                "state": "success"
                if (str(edge.get("from")), str(edge.get("to"))) in observed
                else "pending",
                "causal_explanation_id": f"project-receipt:edge:{_edge_id(edge, index)}",
            }
            for index, edge in enumerate(_contract_edges(self.contract))
        ]

    def _live_terminals(self, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        del edges
        success_targets = {
            str(edge.get("to_node") or edge.get("to_agent"))
            for edge in self.receipt.get("observed_edges", [])
            if isinstance(edge, dict)
        }
        edge_targets = {
            str(edge.get("to"))
            for edge in _contract_edges(self.contract)
            if isinstance(edge.get("to"), str)
        }
        return [
            {
                "terminal_id": terminal_id,
                "state": "success" if terminal_id in success_targets else "pending",
                "causal_explanation_id": f"project-receipt:terminal:{terminal_id}",
            }
            for terminal_id in _terminal_ids(self.contract)
            if terminal_id in edge_targets or terminal_id in success_targets
        ]

    def _routes(self, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "schema": "tau.dag_route_projection.v1",
                "route_id": edge["edge_id"],
                "source_node_id": edge["edge_id"].split("->", 1)[0],
                "state": edge["state"],
                "reason_code": "observed_project_dag_edge"
                if edge["state"] == "success"
                else "edge_not_observed",
                "decision_sequence": len(self.events()) if edge["state"] == "success" else None,
                "decision_receipt_id": None,
                "selected_edge_ids": [edge["edge_id"]] if edge["state"] == "success" else [],
                "skipped_edge_ids": [] if edge["state"] == "success" else [edge["edge_id"]],
                "causal_explanation_id": f"project-receipt:route:{edge['edge_id']}",
            }
            for edge in edges
        ]

    def _attention_items(self, sequence: int) -> list[dict[str, Any]]:
        alerts = self.receipt.get("alerts")
        if not isinstance(alerts, list):
            return []
        items: list[dict[str, Any]] = []
        for index, alert in enumerate(alerts, start=1):
            if not isinstance(alert, dict):
                continue
            code = str(alert.get("code") or f"alert-{index}")
            items.append(
                {
                    "schema": "tau.dag_attention_item.v1",
                    "attention_id": f"project-receipt-alert-{index}",
                    "severity": "BLOCKER",
                    "state": "OPEN",
                    "reason_code": code,
                    "subject": {"kind": "RUN", "id": self.run_id},
                    "opened_sequence": max(sequence, 1),
                    "resolved_sequence": None,
                    "required_action_code": code,
                    "causal_explanation_id": f"project-receipt:attention:{index}",
                }
            )
        return items


def project_receipt_projection_available(run_dir: Path) -> bool:
    try:
        ProjectReceiptProjection.load(run_dir)
    except RuntimeError:
        return False
    return True


def _read_object(path: Path, *, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(code) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(code)
    return payload


def _contract_path(run_dir: Path, receipt: dict[str, Any]) -> Path:
    raw = receipt.get("contract_path")
    candidates = [Path(raw).expanduser() for raw in [raw] if isinstance(raw, str)]
    candidates.extend([run_dir.parent / "dag-contract.json", run_dir / "source-dag.json"])
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise RuntimeError("dag_viewer_project_contract_missing")


def _run_id(run_dir: Path, receipt: dict[str, Any]) -> str:
    raw = receipt.get("run_id") or receipt.get("dag_id") or run_dir.parent.name
    return str(raw)


def _contract_nodes(contract: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = contract.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("dag_viewer_project_contract_invalid")
    result = [node for node in nodes if isinstance(node, dict) and isinstance(node.get("id"), str)]
    if not result:
        raise RuntimeError("dag_viewer_project_contract_invalid")
    return result


def _contract_edges(contract: dict[str, Any]) -> list[dict[str, Any]]:
    edges = contract.get("edges")
    return [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []


def _terminal_ids(contract: dict[str, Any]) -> list[str]:
    terminals = contract.get("terminal_nodes")
    return [str(item) for item in terminals] if isinstance(terminals, list) else []


def _plan_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(node["id"]),
        "role": str(node.get("agent") or node["id"]),
        "adapter": {
            "kind": str(node.get("executor") or "local"),
            "config": {
                "command_spec": str(node.get("command_spec") or ""),
                "required_evidence": list(node.get("required_evidence") or []),
            },
        },
        "retry_policy": {"max_attempts": int(node.get("max_attempts") or 1)},
    }


def _plan_edge(edge: dict[str, Any], index: int) -> dict[str, Any]:
    target = str(edge.get("to") or "")
    return {
        "edge_id": _edge_id(edge, index),
        "source_node_id": str(edge.get("from") or ""),
        "target": {"id": target, "kind": "terminal" if target == "human" else "node"},
    }


def _edge_id(edge: dict[str, Any], index: int) -> str:
    base = f"{edge.get('from')}->{edge.get('to')}"
    condition = edge.get("condition")
    return f"{base}:{condition}" if condition else base if index == 0 else f"{base}:{index}"


def _dag_plan_payload(contract: dict[str, Any], graph: dict[str, Any], plan_sha256: str) -> dict[str, Any]:
    return {
        "schema": "tau.dag_plan.v1",
        "plan_id": str(contract.get("dag_id") or ""),
        "plan_sha256": plan_sha256,
        "source_schema": contract.get("schema"),
        "goal": contract.get("goal"),
        "graph": graph,
        "limits": contract.get("limits"),
        "fail_closed_on": contract.get("fail_closed_on"),
    }


def _goal_projection(contract: dict[str, Any]) -> dict[str, Any]:
    raw = contract.get("goal")
    goal = dict(raw) if isinstance(raw, dict) else {}
    goal_id = str(goal.get("goal_id") or contract.get("dag_id") or "unknown-goal")
    goal_hash = str(goal.get("goal_hash") or "goal_hash_unavailable")
    goal["kind"] = "full" if goal_hash != "goal_hash_unavailable" else "hash_only"
    goal["summary"] = f"{goal_id} · {goal_hash}"
    return goal


def _scheduler_state(progress_status: Any, receipt_status: Any) -> str:
    status = str(progress_status or receipt_status or "PENDING").upper()
    if status in {"COMPLETED", "PASS"}:
        return "settled"
    if status in {"RUNNING"}:
        return "running"
    if status in {"WAITING"}:
        return "ready"
    if status in {"BLOCKED", "FAIL", "FAILED"}:
        return "blocked"
    return "pending"


def _node_receipt_refs(index: ReceiptIndex, node_id: str) -> list[str]:
    return [
        entry.receipt_id
        for entry in index.entries
        if node_id in entry.path_display or node_id in entry.receipt_id
    ]


def _node_summary(receipt: dict[str, Any], node_id: str) -> str | None:
    if node_id == str(receipt.get("entry_node") or ""):
        return "accepted evidence preserved the active goal"
    verdicts = receipt.get("reviewer_verdicts")
    if isinstance(verdicts, list):
        for verdict in verdicts:
            if isinstance(verdict, dict) and verdict.get("reviewed_node_id") == node_id:
                return f"reviewed: {verdict.get('verdict')}"
            if isinstance(verdict, dict) and node_id == "reviewer":
                return f"reviewer verdict: {verdict.get('verdict')}"
    return None


def _node_output(receipt: dict[str, Any], node_id: str) -> dict[str, Any]:
    return {
        "dag_id": receipt.get("dag_id"),
        "node_id": node_id,
        "receipt_status": receipt.get("status"),
        "receipt_verdict": receipt.get("verdict"),
        "node_attempts": receipt.get("node_attempts"),
        "reviewer_verdicts": receipt.get("reviewer_verdicts"),
        "route_decision_receipts": receipt.get("route_decision_receipts"),
        "terminal_contribution_receipts": receipt.get("terminal_contribution_receipts"),
        "join_decision_receipts": receipt.get("join_decision_receipts"),
    }


def _transaction(receipt: dict[str, Any], node_id: str, attempts: dict[str, Any]) -> dict[str, Any] | None:
    attempt_count = int(attempts.get(node_id, 0) or 0)
    if attempt_count <= 1:
        return None
    verdicts = receipt.get("reviewer_verdicts")
    verdict_items = verdicts if isinstance(verdicts, list) else []
    reviewer_by_attempt = {
        int(item.get("creator_attempt") or item.get("attempt") or 0): str(item.get("verdict"))
        for item in verdict_items
        if isinstance(item, dict)
    }
    return {
        "transaction_id": f"project-dag:{node_id}",
        "current_attempt": attempt_count,
        "max_attempts": attempt_count,
        "state": str(receipt.get("status") or "UNKNOWN"),
        "attempts": [
            {
                "attempt": attempt,
                "producer_state": "PASS" if receipt.get("status") == "PASS" else "BLOCKED",
                "validator_status": str(receipt.get("status") or "UNKNOWN"),
                "reviewer_verdict": reviewer_by_attempt.get(attempt),
                "revision_instruction": "retry recorded by project DAG receipt"
                if attempt > 1
                else None,
            }
            for attempt in range(1, attempt_count + 1)
        ],
    }


def _highest_priority_blocker(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for node in nodes:
        codes = node.get("result", {}).get("blocker_codes")
        if isinstance(codes, list) and codes:
            return {"node_id": node["node_id"], "codes": codes}
    return None


def _final_result(receipt: dict[str, Any]) -> dict[str, Any] | None:
    if receipt.get("status") != "PASS":
        return None
    return {
        "dag_id": receipt.get("dag_id"),
        "verdict": receipt.get("verdict"),
        "reviewer_verdicts": receipt.get("reviewer_verdicts"),
        "selected_agents": receipt.get("selected_agents"),
        "observed_edges": receipt.get("observed_edges"),
    }


def _empty_cost_accounting() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
    }


def _ledger_summary(root: Path) -> dict[str, Any] | None:
    ledger_path = root / "run-ledger.json"
    if not ledger_path.is_file():
        return None
    try:
        ledger = read_ledger(ledger_path)
        verification = verify_ledger(ledger)
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "schema": "tau.dag_ledger_summary.v1",
            "available": False,
            "path": str(ledger_path),
            "verify_ok": False,
            "verify_reason": "ledger_unreadable",
        }
    trace = ledger.get("trace") if isinstance(ledger.get("trace"), dict) else {}
    return {
        "schema": "tau.dag_ledger_summary.v1",
        "available": True,
        "path": str(ledger_path),
        "verify_ok": verification.get("ok") is True,
        "verify_reason": verification.get("reason"),
        "entry_count": ledger.get("entry_count"),
        "head_hash": ledger.get("head_hash"),
        "artifact_count": trace.get("artifact_count"),
        "agentic_eval_count": trace.get("agentic_eval_count"),
        "entry_kind_counts": trace.get("entry_kind_counts"),
    }


def _build_project_receipt_index(*, root: Path, receipt: dict[str, Any]) -> ReceiptIndex:
    paths = [root / "dag-receipt.json", root / "dag-progress.json"]
    artifacts = receipt.get("artifacts")
    if isinstance(artifacts, list):
        paths.extend(Path(path) for path in artifacts if isinstance(path, str))
    entries: list[IndexedReceipt] = []
    seen: set[Path] = set()
    ids: set[str] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen or not resolved.is_file() or not _is_beneath(resolved, root):
            continue
        seen.add(resolved)
        data = resolved.read_bytes()
        try:
            payload = json.loads(data)
        except (UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        schema = payload.get("schema")
        if not isinstance(schema, str) or not schema.startswith("tau."):
            continue
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        display = resolved.relative_to(root).as_posix()
        identity = hashlib.sha256(f"{display}\0{digest}".encode()).hexdigest()
        receipt_id = f"sha256-{identity[:24]}"
        if receipt_id in ids:
            raise RuntimeError("dag_viewer_receipt_id_collision")
        ids.add(receipt_id)
        entries.append(
            IndexedReceipt(
                receipt_id=receipt_id,
                schema=schema,
                path=resolved,
                path_display=display,
                sha256=digest,
            )
        )
    return ReceiptIndex(root, tuple(entries))


def _redacted(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
