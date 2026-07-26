"""Native Tau DAG template registry and compiler.

The registry expands named workflow patterns into explicit
`tau.dag_contract.v1` nodes, edges, evidence gates, retry limits, and stop
conditions. It does not call providers or hide workflow structure inside prompt
text. Missing required parameters produce an interview packet instead of a
partial DAG.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.project_dag import validate_dag_contract

DAG_TEMPLATE_REGISTRY_SCHEMA = "tau.dag_template_registry.v1"
DAG_TEMPLATE_COMPILE_RECEIPT_SCHEMA = "tau.dag_template_compile_receipt.v1"
DAG_TEMPLATE_MISSING_FIELDS_SCHEMA = "tau.dag_template_missing_fields.v1"

DEFAULT_FAIL_CLOSED_ON = [
    "goal_hash_mismatch",
    "target_changed",
    "unexpected_node",
    "unexpected_edge",
    "missing_required_evidence",
    "max_attempts_exceeded",
    "malformed_handoff",
]

TemplateExpansion = tuple[list[dict[str, Any]], list[dict[str, str]], str, list[str]]


@dataclass(frozen=True, slots=True)
class DagTemplate:
    """Metadata for one native Tau DAG template."""

    name: str
    summary: str
    required_fields: tuple[str, ...]
    topology: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "required_fields": list(self.required_fields),
            "topology": self.topology,
        }


TEMPLATES: dict[str, DagTemplate] = {
    "single-call": DagTemplate(
        name="single-call",
        summary="One local handler node routes directly to human.",
        required_fields=("dag_id", "goal", "target", "handler"),
        topology="LINEAR_SINGLE",
    ),
    "prompt-chain": DagTemplate(
        name="prompt-chain",
        summary="A sequential chain of two or more explicit local step nodes.",
        required_fields=("dag_id", "goal", "target", "steps"),
        topology="LINEAR_CHAIN",
    ),
    "reflection-loop": DagTemplate(
        name="reflection-loop",
        summary="Creator node followed by reviewer/reflection node and human handoff.",
        required_fields=("dag_id", "goal", "target", "creator", "reviewer"),
        topology="CREATOR_REVIEWER",
    ),
    "roundtable": DagTemplate(
        name="roundtable",
        summary="Two or more parallel handlers join into a synthesis node.",
        required_fields=("dag_id", "goal", "target", "handlers", "join"),
        topology="FAN_OUT_JOIN",
    ),
    "compete": DagTemplate(
        name="compete",
        summary="Two or more competitors feed a judge node.",
        required_fields=("dag_id", "goal", "target", "competitors", "judge"),
        topology="COMPETITION_JUDGE",
    ),
}


def dag_template_registry_payload() -> dict[str, Any]:
    """Return registry metadata suitable for CLI/UI display."""

    return {
        "schema": DAG_TEMPLATE_REGISTRY_SCHEMA,
        "template_count": len(TEMPLATES),
        "templates": [template.to_json() for template in TEMPLATES.values()],
    }


def write_dag_template_compile_receipt(
    *,
    template_name: str,
    params_path: Path,
    out_path: Path,
    receipt_path: Path,
    missing_out_path: Path | None = None,
) -> dict[str, Any]:
    """Compile one template params file to a DAG contract or missing-field packet."""

    resolved_params = params_path.expanduser().resolve()
    resolved_out = out_path.expanduser().resolve()
    resolved_receipt = receipt_path.expanduser().resolve()
    resolved_missing = (
        missing_out_path.expanduser().resolve()
        if missing_out_path is not None
        else resolved_receipt.with_name(f"{resolved_receipt.stem}.missing-fields.json")
    )
    params = _read_json_object(resolved_params)
    template = TEMPLATES.get(template_name)
    alerts: list[dict[str, Any]] = []
    dag_contract: dict[str, Any] | None = None
    missing_packet: dict[str, Any] | None = None
    if template is None:
        alerts.append(_alert("unknown_template", f"unknown DAG template: {template_name}"))
    else:
        missing = _missing_fields(template, params)
        if missing:
            missing_packet = _missing_fields_packet(
                template=template,
                params_path=resolved_params,
                missing_fields=missing,
            )
            _write_json(resolved_missing, missing_packet)
            alerts.append(
                _alert(
                    "template_interview_required",
                    "DAG template parameters are incomplete.",
                    {"missing_fields": missing},
                )
            )
        else:
            dag_contract = compile_dag_template(
                template_name,
                _params_with_resolved_command_specs(params, resolved_params.parent),
            )
            validate_dag_contract(dag_contract)
            _write_json(resolved_out, dag_contract)

    ok = not alerts
    receipt = {
        "schema": DAG_TEMPLATE_COMPILE_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "template": template_name,
        "params_path": str(resolved_params),
        "params_sha256": _sha256_uri(resolved_params),
        "dag_contract_path": str(resolved_out) if ok else None,
        "dag_contract_sha256": _sha256_uri(resolved_out) if ok else None,
        "missing_fields_path": str(resolved_missing) if missing_packet else None,
        "missing_fields_sha256": _sha256_uri(resolved_missing) if missing_packet else None,
        "interview_required": missing_packet is not None,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "template_registry": dag_template_registry_payload(),
        "proof_scope": {
            "proves": [
                "Tau selected a native DAG template by name.",
                "Tau checked typed required fields before emitting a DAG contract.",
                "A successful compile emitted a contract accepted by validate_dag_contract.",
                "No model/provider call or command dispatch occurred during template compile.",
            ],
            "does_not_prove": [
                "Runtime execution success.",
                "Provider/model semantic quality.",
                "That a missing-field interview has been answered by a human.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt, receipt)
    return receipt


def compile_dag_template(template_name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Compile one complete params object into `tau.dag_contract.v1`."""

    if template_name not in TEMPLATES:
        raise RuntimeError(f"unknown DAG template: {template_name}")
    template = TEMPLATES[template_name]
    missing = _missing_fields(template, params)
    if missing:
        raise RuntimeError(f"missing DAG template fields: {', '.join(missing)}")
    if template_name == "single-call":
        nodes, edges, entry, evidence = _single_call(params)
    elif template_name == "prompt-chain":
        nodes, edges, entry, evidence = _prompt_chain(params)
    elif template_name == "reflection-loop":
        nodes, edges, entry, evidence = _reflection_loop(params)
    elif template_name == "roundtable":
        nodes, edges, entry, evidence = _roundtable(params)
    else:
        nodes, edges, entry, evidence = _compete(params)
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": _string(params["dag_id"]),
        "goal": dict(params["goal"]),
        "target": dict(params["target"]),
        "entry_node": entry,
        "terminal_nodes": ["human"],
        "limits": _limits(params, nodes),
        "context": {
            "dag_template": template.to_json(),
            "template_params_sha256": _mapping_sha256(params),
            "stop_condition": "Stop at human after required evidence gates pass.",
        },
        "nodes": nodes,
        "edges": edges,
        "required_evidence": evidence,
        "fail_closed_on": list(params.get("fail_closed_on") or DEFAULT_FAIL_CLOSED_ON),
    }
    return contract


def _single_call(params: Mapping[str, Any]) -> TemplateExpansion:
    handler = _node_from_value(params["handler"], default_evidence="handler_receipt")
    nodes = [_with_command_spec(handler, params)]
    return (
        nodes,
        [{"from": handler["id"], "to": "human"}],
        str(handler["id"]),
        list(handler["required_evidence"]),
    )


def _prompt_chain(params: Mapping[str, Any]) -> TemplateExpansion:
    steps = [
        _with_command_spec(_node_from_value(item, default_evidence="step_receipt"), params)
        for item in params["steps"]
    ]
    edges = [
        {"from": str(steps[index]["id"]), "to": str(steps[index + 1]["id"])}
        for index in range(len(steps) - 1)
    ]
    edges.append({"from": str(steps[-1]["id"]), "to": "human"})
    return steps, edges, str(steps[0]["id"]), _all_required_evidence(steps)


def _reflection_loop(params: Mapping[str, Any]) -> TemplateExpansion:
    creator = _node_from_value(params["creator"], default_evidence="creator_artifact")
    reviewer = _node_from_value(params["reviewer"], default_evidence="reviewer_verdict")
    reviewer["reviewer"] = {
        "reviews_node": creator["id"],
        "requires_goal_hash": True,
    }
    nodes = [_with_command_spec(creator, params), _with_command_spec(reviewer, params)]
    edges = [
        {"from": str(creator["id"]), "to": str(reviewer["id"])},
        {"from": str(reviewer["id"]), "to": "human"},
    ]
    return nodes, edges, str(creator["id"]), _all_required_evidence(nodes)


def _roundtable(params: Mapping[str, Any]) -> TemplateExpansion:
    handlers = [
        _with_command_spec(_node_from_value(item, default_evidence="handler_receipt"), params)
        for item in params["handlers"]
    ]
    join = _with_command_spec(
        _node_from_value(params["join"], default_evidence="roundtable_join_receipt"),
        params,
    )
    nodes = [*handlers, join]
    edges = [{"from": str(handler["id"]), "to": str(join["id"])} for handler in handlers]
    edges.append({"from": str(join["id"]), "to": "human"})
    return nodes, edges, str(handlers[0]["id"]), _all_required_evidence(nodes)


def _compete(params: Mapping[str, Any]) -> TemplateExpansion:
    competitors = [
        _with_command_spec(_node_from_value(item, default_evidence="competitor_receipt"), params)
        for item in params["competitors"]
    ]
    judge = _with_command_spec(
        _node_from_value(params["judge"], default_evidence="competition_judgment_receipt"),
        params,
    )
    judge["reviewer"] = {
        "reviews_node": str(competitors[0]["id"]),
        "requires_goal_hash": True,
    }
    nodes = [*competitors, judge]
    edges = [{"from": str(competitor["id"]), "to": str(judge["id"])} for competitor in competitors]
    edges.append({"from": str(judge["id"]), "to": "human"})
    return nodes, edges, str(competitors[0]["id"]), _all_required_evidence(nodes)


def _node_from_value(value: object, *, default_evidence: str) -> dict[str, Any]:
    if isinstance(value, str):
        node_id = value
        agent = value
        evidence = [default_evidence]
        max_attempts = 1
    elif isinstance(value, Mapping):
        node_id = _string(value.get("id") or value.get("agent"))
        agent = _string(value.get("agent") or node_id)
        evidence = _string_list(value.get("required_evidence")) or [default_evidence]
        max_attempts = int(value.get("max_attempts") or 1)
    else:
        raise RuntimeError("template node value must be a string or object")
    return {
        "id": node_id,
        "agent": agent,
        "executor": "local",
        "max_attempts": max_attempts,
        "required_evidence": evidence,
    }


def _with_command_spec(node: dict[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    specs = params.get("command_specs")
    if isinstance(specs, Mapping):
        value = specs.get(node["id"])
        if isinstance(value, str) and value:
            node = dict(node)
            node["command_spec"] = value
    return node


def _params_with_resolved_command_specs(
    params: Mapping[str, Any],
    base_dir: Path,
) -> dict[str, Any]:
    updated = dict(params)
    specs = params.get("command_specs")
    if not isinstance(specs, Mapping):
        return updated
    resolved_specs: dict[str, str] = {}
    for key, value in specs.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            continue
        path = Path(value)
        resolved_specs[key] = str(path if path.is_absolute() else (base_dir / path).resolve())
    updated["command_specs"] = resolved_specs
    return updated


def _limits(params: Mapping[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    raw = params.get("limits") if isinstance(params.get("limits"), Mapping) else {}
    max_attempts = sum(int(node.get("max_attempts") or 1) for node in nodes)
    return {
        "resume": bool(raw.get("resume", True)),
        "default_timeout_seconds": int(raw.get("default_timeout_seconds") or 120),
        "max_total_attempts": int(raw.get("max_total_attempts") or max(max_attempts, 1)),
    }


def _missing_fields(template: DagTemplate, params: Mapping[str, Any]) -> list[str]:
    missing = [field for field in template.required_fields if _missing_value(params.get(field))]
    if not _missing_value(params.get("goal")):
        goal = params["goal"]
        if not isinstance(goal, Mapping) or _missing_value(goal.get("goal_hash")):
            missing.append("goal.goal_hash")
    if not _missing_value(params.get("target")):
        target = params["target"]
        if not isinstance(target, Mapping) or _missing_value(target.get("repo")):
            missing.append("target.repo")
        if not isinstance(target, Mapping) or _missing_value(target.get("target")):
            missing.append("target.target")
    if (
        template.name in {"prompt-chain"}
        and isinstance(params.get("steps"), list)
        and len(params["steps"]) < 2
    ):
        missing.append("steps[1]")
    if (
        template.name in {"roundtable"}
        and isinstance(params.get("handlers"), list)
        and len(params["handlers"]) < 2
    ):
        missing.append("handlers[1]")
    if (
        template.name in {"compete"}
        and isinstance(params.get("competitors"), list)
        and len(params["competitors"]) < 2
    ):
        missing.append("competitors[1]")
    return sorted(set(missing))


def _missing_fields_packet(
    *,
    template: DagTemplate,
    params_path: Path,
    missing_fields: list[str],
) -> dict[str, Any]:
    return {
        "schema": DAG_TEMPLATE_MISSING_FIELDS_SCHEMA,
        "ok": False,
        "status": "INTERVIEW_REQUIRED",
        "template": template.name,
        "params_path": str(params_path),
        "params_sha256": _sha256_uri(params_path),
        "missing_fields": missing_fields,
        "questions": [
            {
                "field": field,
                "question": f"Provide {field} for DAG template {template.name}.",
                "required": True,
            }
            for field in missing_fields
        ],
        "interview_required": True,
        "next_action": "Ask the human for the missing fields, then rerun dag-template-compile.",
    }


def _all_required_evidence(nodes: list[dict[str, Any]]) -> list[str]:
    evidence: list[str] = []
    for node in nodes:
        for item in node.get("required_evidence", []):
            if isinstance(item, str) and item not in evidence:
                evidence.append(item)
    return evidence


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("DAG template params must be a JSON object")
    return payload


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("expected non-empty string")
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _missing_value(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _mapping_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sha256_uri(path: Path) -> str | None:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}" if path.exists() else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _alert(code: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if data:
        alert["data"] = data
    return alert


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
