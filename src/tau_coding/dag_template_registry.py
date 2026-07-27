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

from tau_coding.dag_runtime.compiler import compile_project_dag_plan
from tau_coding.project_dag import validate_dag_contract

DAG_TEMPLATE_REGISTRY_SCHEMA = "tau.dag_template_registry.v1"
DAG_TEMPLATE_CATALOG_SCHEMA = "tau.dag_template_catalog.v1"
DAG_TEMPLATE_DESCRIPTOR_SCHEMA = "tau.dag_template_descriptor.v1"
DAG_TEMPLATE_COMPILE_RECEIPT_SCHEMA = "tau.dag_template_compile_receipt.v1"
DAG_TEMPLATE_MISSING_FIELDS_SCHEMA = "tau.dag_template_missing_fields.v1"
DAG_TEMPLATE_VALIDATION_RECEIPT_SCHEMA = "tau.dag_template_validation_receipt.v1"
DAG_TEMPLATE_PREVIEW_SCHEMA = "tau.dag_template_preview.v1"
DAG_TEMPLATE_SELECTION_RECEIPT_SCHEMA = "tau.dag_template_selection_receipt.v1"

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
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    node_roles: tuple[str, ...]
    required_evidence_kinds: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "required_fields": list(self.required_fields),
            "topology": self.topology,
        }

    def descriptor_json(self) -> dict[str, Any]:
        return {
            "schema": DAG_TEMPLATE_DESCRIPTOR_SCHEMA,
            "name": self.name,
            "summary": self.summary,
            "topology": self.topology,
            "required_fields": list(self.required_fields),
            "optional_fields": [
                "limits",
                "command_specs",
                "fail_closed_on",
            ],
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "node_roles": list(self.node_roles),
            "evidence_contract": {
                "required_kinds": list(self.required_evidence_kinds),
                "admission_rule": (
                    "Every required evidence kind must be emitted by a routed node "
                    "before Tau admits progress."
                ),
            },
            "resources": {
                "requires_memory": self.name == "memory-recalled-workflow",
                "requires_human_approval": self.name == "dry-run-human-approval",
                "external_services": [],
            },
            "side_effects": {
                "describe_validate_preview_select": "none",
                "compile": "writes only caller-specified DAG, receipt, and missing-field paths",
                "runtime": "depends on compiled node command_specs and executor policy",
            },
            "human_interview": {
                "status_when_incomplete": "INTERVIEW_REQUIRED",
                "questions_source": "missing typed required fields",
            },
            "preview": {
                "available": True,
                "dispatches_commands": False,
                "compiles_to_schema": "tau.dag_contract.v1",
            },
            "authority_boundary": {
                "selector": "deterministic template name and typed params",
                "scheduler": "existing Tau DAG runtime",
                "viewer": "read-only projection of scheduler journal and admitted receipts",
                "provider_calls": "never during describe, validate, preview, or compile",
            },
        }


TEMPLATES: dict[str, DagTemplate] = {
    "single-call": DagTemplate(
        name="single-call",
        summary="One local handler node routes directly to human.",
        required_fields=("dag_id", "goal", "target", "handler"),
        topology="LINEAR_SINGLE",
        use_when=("One bounded worker can produce the requested artifact or answer.",),
        avoid_when=("Independent review, retry, or multi-step decomposition is required.",),
        node_roles=("handler", "human"),
        required_evidence_kinds=("handler_receipt",),
    ),
    "prompt-chain": DagTemplate(
        name="prompt-chain",
        summary="A sequential chain of two or more explicit local step nodes.",
        required_fields=("dag_id", "goal", "target", "steps"),
        topology="LINEAR_CHAIN",
        use_when=("The task has ordered steps where later work depends on earlier artifacts.",),
        avoid_when=("Steps can run concurrently or the route needs a reviewer gate.",),
        node_roles=("step", "human"),
        required_evidence_kinds=("step_receipt",),
    ),
    "reflection-loop": DagTemplate(
        name="reflection-loop",
        summary="Creator node followed by reviewer/reflection node and human handoff.",
        required_fields=("dag_id", "goal", "target", "creator", "reviewer"),
        topology="CREATOR_REVIEWER",
        use_when=("A produced artifact needs a separate review/reflection pass before handoff.",),
        avoid_when=("Two or more independent candidate solutions should compete.",),
        node_roles=("creator", "reviewer", "human"),
        required_evidence_kinds=("creator_artifact", "reviewer_verdict"),
    ),
    "roundtable": DagTemplate(
        name="roundtable",
        summary="Two or more parallel handlers join into a synthesis node.",
        required_fields=("dag_id", "goal", "target", "handlers", "join"),
        topology="FAN_OUT_JOIN",
        use_when=("Multiple independent specialists should work in parallel before synthesis.",),
        avoid_when=("The request needs a winner rather than a synthesis.",),
        node_roles=("handler", "join", "human"),
        required_evidence_kinds=("handler_receipt", "roundtable_join_receipt"),
    ),
    "compete": DagTemplate(
        name="compete",
        summary="Two or more competitors feed a judge node.",
        required_fields=("dag_id", "goal", "target", "competitors", "judge"),
        topology="COMPETITION_JUDGE",
        use_when=("Two or more candidate solutions should be judged against explicit criteria.",),
        avoid_when=("The desired output is a merged synthesis rather than a selected winner.",),
        node_roles=("competitor", "judge", "human"),
        required_evidence_kinds=("competitor_receipt", "competition_judgment_receipt"),
    ),
    "plan-execute-verify": DagTemplate(
        name="plan-execute-verify",
        summary="Planner, executor, and verifier nodes run in sequence before human handoff.",
        required_fields=("dag_id", "goal", "target", "planner", "executor", "verifier"),
        topology="PLAN_EXECUTE_VERIFY",
        use_when=("A task needs an explicit plan, implementation, and verification pass.",),
        avoid_when=("The request is a one-step answer or needs competing candidates.",),
        node_roles=("planner", "executor", "verifier", "human"),
        required_evidence_kinds=("plan_receipt", "execution_receipt", "verification_receipt"),
    ),
    "claim-chain-verification": DagTemplate(
        name="claim-chain-verification",
        summary="One or more claim-producing nodes feed a verifier before human handoff.",
        required_fields=("dag_id", "goal", "target", "claim_steps", "verifier"),
        topology="CLAIM_CHAIN_VERIFICATION",
        use_when=("Claims, citations, or proof steps must be checked before acceptance.",),
        avoid_when=("The output is an implementation without a claim/proof chain.",),
        node_roles=("claim_step", "verifier", "human"),
        required_evidence_kinds=("claim_receipt", "verification_receipt"),
    ),
    "specialist-fanout-join": DagTemplate(
        name="specialist-fanout-join",
        summary="Specialist workers run in parallel and join into one synthesis node.",
        required_fields=("dag_id", "goal", "target", "specialists", "join"),
        topology="SPECIALIST_FAN_OUT_JOIN",
        use_when=("Named specialists should inspect separate aspects before synthesis.",),
        avoid_when=("The request needs a winner instead of a synthesized result.",),
        node_roles=("specialist", "join", "human"),
        required_evidence_kinds=("specialist_receipt", "specialist_join_receipt"),
    ),
    "dry-run-human-approval": DagTemplate(
        name="dry-run-human-approval",
        summary="Dry-run node produces an approval packet before human handoff.",
        required_fields=("dag_id", "goal", "target", "dry_run", "approval_packet"),
        topology="DRY_RUN_HUMAN_APPROVAL",
        use_when=("A mutating action needs a non-mutating plan and explicit human approval.",),
        avoid_when=("The work is read-only or already authorized for execution.",),
        node_roles=("dry_run", "approval_packet", "human"),
        required_evidence_kinds=("dry_run_receipt", "approval_packet"),
    ),
    "memory-recalled-workflow": DagTemplate(
        name="memory-recalled-workflow",
        summary="Memory recall/provenance node precedes a bounded handler node.",
        required_fields=("dag_id", "goal", "target", "memory_recall", "handler"),
        topology="MEMORY_RECALLED_WORKFLOW",
        use_when=("Prior lessons or skill-chain provenance must be recalled before work.",),
        avoid_when=("Memory is unavailable and the task can proceed without provenance.",),
        node_roles=("memory_recall", "handler", "human"),
        required_evidence_kinds=("memory_recall_receipt", "handler_receipt"),
    ),
}


def dag_template_registry_payload() -> dict[str, Any]:
    """Return registry metadata suitable for CLI/UI display."""

    return {
        "schema": DAG_TEMPLATE_REGISTRY_SCHEMA,
        "template_count": len(TEMPLATES),
        "templates": [template.to_json() for template in TEMPLATES.values()],
    }


def dag_template_catalog_payload() -> dict[str, Any]:
    """Return full catalogue metadata for pre-run UX and docs surfaces."""

    return {
        "schema": DAG_TEMPLATE_CATALOG_SCHEMA,
        "template_count": len(TEMPLATES),
        "templates": [template.descriptor_json() for template in TEMPLATES.values()],
        "commands": {
            "list": "tau dag-template-list",
            "catalog": "tau dag-template-catalog",
            "describe": "tau dag-template-describe --template <name>",
            "select": "tau dag-template-select --facts <json>",
            "validate": "tau dag-template-validate --template <name> --params <json>",
            "preview": "tau dag-template-preview --template <name> --params <json>",
            "compile": (
                "tau dag-template-compile --template <name> --params <json> "
                "--out <dag.json> --receipt <receipt.json>"
            ),
        },
        "authority_boundary": {
            "selection_authority": "deterministic closed typed facts only",
            "preview_authority": "compiled Tau DAG contract and DagPlan summary",
            "execution_authority": "existing Tau scheduler",
            "viewer_authority": "read-only scheduler journal projection",
        },
    }


def describe_dag_template(template_name: str) -> dict[str, Any]:
    """Return the machine-readable descriptor for one native Tau template."""

    template = _template_or_raise(template_name)
    return template.descriptor_json()


def validate_dag_template_params(
    template_name: str,
    params_path: Path,
) -> dict[str, Any]:
    """Validate typed template params without compiling or dispatching commands."""

    resolved_params = params_path.expanduser().resolve()
    params = _read_json_object(resolved_params)
    template = _template_or_raise(template_name)
    missing = _missing_fields(template, params)
    ok = not missing
    return {
        "schema": DAG_TEMPLATE_VALIDATION_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "INTERVIEW_REQUIRED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "template": template.name,
        "params_path": str(resolved_params),
        "params_sha256": _sha256_uri(resolved_params),
        "descriptor": template.descriptor_json(),
        "missing_fields": missing,
        "questions": _interview_questions(template=template, missing_fields=missing),
        "next_action": (
            "Proceed to dag-template-preview or dag-template-compile."
            if ok
            else "Ask the human for the missing fields, then rerun validation."
        ),
        "proof_scope": {
            "proves": [
                "Tau checked the template's typed required fields without dispatching commands.",
                "Incomplete params fail closed to INTERVIEW_REQUIRED.",
            ],
            "does_not_prove": [
                "Runtime execution success.",
                "Provider/model semantic quality.",
                "That a compiled DAG will be executed.",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def preview_dag_template(
    template_name: str,
    params_path: Path,
) -> dict[str, Any]:
    """Return a pre-run DAG preview without writing artifacts or dispatching commands."""

    resolved_params = params_path.expanduser().resolve()
    params = _params_with_resolved_command_specs(
        _read_json_object(resolved_params),
        resolved_params.parent,
    )
    template = _template_or_raise(template_name)
    missing = _missing_fields(template, params)
    ok = not missing
    preview: dict[str, Any] | None = None
    if ok:
        contract = compile_dag_template(template.name, params)
        plan_payload = compile_project_dag_plan(contract).to_payload()
        preview = {
            "schema": "tau.dag_contract.preview.v1",
            "dag_id": contract["dag_id"],
            "goal": contract["goal"],
            "target": contract["target"],
            "entry_node": contract["entry_node"],
            "terminal_nodes": contract["terminal_nodes"],
            "node_count": len(contract["nodes"]),
            "edge_count": len(contract["edges"]),
            "nodes": contract["nodes"],
            "edges": contract["edges"],
            "required_evidence": contract["required_evidence"],
            "limits": contract["limits"],
            "fail_closed_on": contract["fail_closed_on"],
            "context": contract["context"],
            "compiled_dag_plan": _dag_plan_preview(plan_payload),
            "source_to_plan_diff": _source_to_plan_diff(contract, plan_payload),
        }
    return {
        "schema": DAG_TEMPLATE_PREVIEW_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "INTERVIEW_REQUIRED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "template": template.name,
        "params_path": str(resolved_params),
        "params_sha256": _sha256_uri(resolved_params),
        "descriptor": template.descriptor_json(),
        "missing_fields": missing,
        "questions": _interview_questions(template=template, missing_fields=missing),
        "preview": preview,
        "dispatches_commands": False,
        "proof_scope": {
            "proves": [
                (
                    "Tau can render the selected native template as an inspectable "
                    "pre-run DAG preview."
                ),
                "Incomplete params fail closed to INTERVIEW_REQUIRED.",
                "Preview does not dispatch workers, providers, or scheduler execution.",
            ],
            "does_not_prove": [
                "Runtime execution success.",
                "Provider/model semantic quality.",
                "That the human accepted the preview.",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def select_dag_template_from_facts(facts_path: Path) -> dict[str, Any]:
    """Select a template from closed typed facts, or fail closed for interview."""

    resolved_facts = facts_path.expanduser().resolve()
    facts = _read_json_object(resolved_facts)
    required_missing = _missing_selection_fields(facts)
    candidates = [] if required_missing else _selection_candidates(facts)
    selected = candidates[0]["template"] if len(candidates) == 1 else None
    status = "PASS" if selected else "INTERVIEW_REQUIRED"
    questions = _selection_questions(required_missing, candidates)
    return {
        "schema": DAG_TEMPLATE_SELECTION_RECEIPT_SCHEMA,
        "ok": selected is not None,
        "status": status,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "facts_path": str(resolved_facts),
        "facts_sha256": _sha256_uri(resolved_facts),
        "required_fact_fields": [
            "request_hash",
            "goal_hash",
            "policy_hash",
            "capability_hash",
            "target.repo",
            "target.target",
        ],
        "missing_fact_fields": required_missing,
        "selector_inputs": _selection_input_summary(facts),
        "eligible_templates": candidates,
        "selected_template": selected,
        "questions": questions,
        "diagnostic_model_confidence_ignored": facts.get("model_confidence") is not None,
        "authority_boundary": {
            "selection_authority": "deterministic closed typed facts only",
            "model_confidence": "ignored",
            "provider_calls": "never during selection",
        },
        "next_action": (
            "Proceed to dag-template-preview with selected_template."
            if selected
            else "Ask the human for the missing or ambiguous selection facts."
        ),
        "proof_scope": {
            "proves": [
                "Tau selected, or refused to select, a template from closed typed facts.",
                "Missing or ambiguous facts fail closed to INTERVIEW_REQUIRED.",
                "Model confidence cannot override deterministic selection.",
            ],
            "does_not_prove": [
                "Runtime execution success.",
                "Provider/model semantic quality.",
                "That the selected template params are complete.",
            ],
        },
        "timestamp": _utc_stamp(),
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

    template = _template_or_raise(template_name)
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
    elif template_name == "compete":
        nodes, edges, entry, evidence = _compete(params)
    elif template_name == "plan-execute-verify":
        nodes, edges, entry, evidence = _plan_execute_verify(params)
    elif template_name == "claim-chain-verification":
        nodes, edges, entry, evidence = _claim_chain_verification(params)
    elif template_name == "specialist-fanout-join":
        nodes, edges, entry, evidence = _specialist_fanout_join(params)
    elif template_name == "dry-run-human-approval":
        nodes, edges, entry, evidence = _dry_run_human_approval(params)
    else:
        nodes, edges, entry, evidence = _memory_recalled_workflow(params)
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


def _plan_execute_verify(params: Mapping[str, Any]) -> TemplateExpansion:
    planner = _node_from_value(params["planner"], default_evidence="plan_receipt")
    executor = _node_from_value(params["executor"], default_evidence="execution_receipt")
    verifier = _node_from_value(params["verifier"], default_evidence="verification_receipt")
    verifier["reviewer"] = {
        "reviews_node": executor["id"],
        "requires_goal_hash": True,
    }
    nodes = [
        _with_command_spec(planner, params),
        _with_command_spec(executor, params),
        _with_command_spec(verifier, params),
    ]
    edges = [
        {"from": str(planner["id"]), "to": str(executor["id"])},
        {"from": str(executor["id"]), "to": str(verifier["id"])},
        {"from": str(verifier["id"]), "to": "human"},
    ]
    return nodes, edges, str(planner["id"]), _all_required_evidence(nodes)


def _claim_chain_verification(params: Mapping[str, Any]) -> TemplateExpansion:
    claim_steps = [
        _with_command_spec(_node_from_value(item, default_evidence="claim_receipt"), params)
        for item in params["claim_steps"]
    ]
    verifier = _with_command_spec(
        _node_from_value(params["verifier"], default_evidence="verification_receipt"),
        params,
    )
    verifier["reviewer"] = {
        "reviews_node": str(claim_steps[-1]["id"]),
        "requires_goal_hash": True,
    }
    nodes = [*claim_steps, verifier]
    edges = [
        {"from": str(claim_steps[index]["id"]), "to": str(claim_steps[index + 1]["id"])}
        for index in range(len(claim_steps) - 1)
    ]
    edges.append({"from": str(claim_steps[-1]["id"]), "to": str(verifier["id"])})
    edges.append({"from": str(verifier["id"]), "to": "human"})
    return nodes, edges, str(claim_steps[0]["id"]), _all_required_evidence(nodes)


def _specialist_fanout_join(params: Mapping[str, Any]) -> TemplateExpansion:
    specialists = [
        _with_command_spec(_node_from_value(item, default_evidence="specialist_receipt"), params)
        for item in params["specialists"]
    ]
    join = _with_command_spec(
        _node_from_value(params["join"], default_evidence="specialist_join_receipt"),
        params,
    )
    nodes = [*specialists, join]
    edges = [{"from": str(specialist["id"]), "to": str(join["id"])} for specialist in specialists]
    edges.append({"from": str(join["id"]), "to": "human"})
    return nodes, edges, str(specialists[0]["id"]), _all_required_evidence(nodes)


def _dry_run_human_approval(params: Mapping[str, Any]) -> TemplateExpansion:
    dry_run = _node_from_value(params["dry_run"], default_evidence="dry_run_receipt")
    approval = _node_from_value(params["approval_packet"], default_evidence="approval_packet")
    nodes = [_with_command_spec(dry_run, params), _with_command_spec(approval, params)]
    edges = [
        {"from": str(dry_run["id"]), "to": str(approval["id"])},
        {"from": str(approval["id"]), "to": "human"},
    ]
    return nodes, edges, str(dry_run["id"]), _all_required_evidence(nodes)


def _memory_recalled_workflow(params: Mapping[str, Any]) -> TemplateExpansion:
    memory_recall = _node_from_value(
        params["memory_recall"],
        default_evidence="memory_recall_receipt",
    )
    handler = _node_from_value(params["handler"], default_evidence="handler_receipt")
    handler["requires_memory_provenance"] = True
    nodes = [_with_command_spec(memory_recall, params), _with_command_spec(handler, params)]
    edges = [
        {"from": str(memory_recall["id"]), "to": str(handler["id"])},
        {"from": str(handler["id"]), "to": "human"},
    ]
    return nodes, edges, str(memory_recall["id"]), _all_required_evidence(nodes)


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
    if (
        template.name in {"claim-chain-verification"}
        and isinstance(params.get("claim_steps"), list)
        and len(params["claim_steps"]) < 1
    ):
        missing.append("claim_steps[0]")
    if (
        template.name in {"specialist-fanout-join"}
        and isinstance(params.get("specialists"), list)
        and len(params["specialists"]) < 2
    ):
        missing.append("specialists[1]")
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
        "questions": _interview_questions(template=template, missing_fields=missing_fields),
        "interview_required": True,
        "next_action": "Ask the human for the missing fields, then rerun dag-template-compile.",
    }


def _interview_questions(
    *,
    template: DagTemplate,
    missing_fields: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "field": field,
            "question": f"Provide {field} for DAG template {template.name}.",
            "required": True,
        }
        for field in missing_fields
    ]


def _missing_selection_fields(facts: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in ["request_hash", "goal_hash", "policy_hash", "capability_hash"]:
        if _missing_value(facts.get(field)):
            missing.append(field)
    target = facts.get("target")
    if not isinstance(target, Mapping) or _missing_value(target.get("repo")):
        missing.append("target.repo")
    if not isinstance(target, Mapping) or _missing_value(target.get("target")):
        missing.append("target.target")
    if not _selection_signal_count(facts):
        missing.append("task_shape")
    return missing


def _selection_input_summary(facts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_shape": facts.get("task_shape"),
        "ordered_steps": facts.get("ordered_steps"),
        "needs_review": facts.get("needs_review"),
        "needs_plan_execute_verify": facts.get("needs_plan_execute_verify"),
        "needs_claim_verification": facts.get("needs_claim_verification"),
        "needs_specialists": facts.get("needs_specialists"),
        "needs_dry_run_approval": facts.get("needs_dry_run_approval"),
        "needs_memory_recall": facts.get("needs_memory_recall"),
        "wants_synthesis": facts.get("wants_synthesis"),
        "wants_winner": facts.get("wants_winner"),
        "handler_count": facts.get("handler_count"),
        "competitor_count": facts.get("competitor_count"),
        "step_count": facts.get("step_count"),
    }


def _selection_signal_count(facts: Mapping[str, Any]) -> int:
    signals = [
        _truthy(facts.get("needs_plan_execute_verify"))
        or facts.get("task_shape") == "plan_execute_verify",
        _truthy(facts.get("needs_claim_verification"))
        or facts.get("task_shape") == "claim_chain_verification",
        _truthy(facts.get("needs_specialists"))
        or facts.get("task_shape") == "specialist_fanout_join",
        _truthy(facts.get("needs_dry_run_approval"))
        or facts.get("task_shape") == "dry_run_human_approval",
        _truthy(facts.get("needs_memory_recall"))
        or facts.get("task_shape") == "memory_recalled_workflow",
        _truthy(facts.get("wants_winner")) or facts.get("task_shape") == "competition",
        _truthy(facts.get("wants_synthesis")) or facts.get("task_shape") == "fanout_join",
        _truthy(facts.get("needs_review")) or facts.get("task_shape") == "creator_reviewer",
        _truthy(facts.get("ordered_steps")) or facts.get("task_shape") == "linear_chain",
        facts.get("task_shape") == "single",
    ]
    return sum(1 for signal in signals if signal)


def _selection_candidates(facts: Mapping[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if (
        _truthy(facts.get("needs_plan_execute_verify"))
        or facts.get("task_shape") == "plan_execute_verify"
    ):
        candidates.append(
            {
                "template": "plan-execute-verify",
                "reason": "closed facts request explicit plan, execution, and verification",
            }
        )
    if (
        _truthy(facts.get("needs_claim_verification"))
        or facts.get("task_shape") == "claim_chain_verification"
    ):
        candidates.append(
            {
                "template": "claim-chain-verification",
                "reason": "closed facts request claim/proof verification",
            }
        )
    if (
        _truthy(facts.get("needs_specialists"))
        or facts.get("task_shape") == "specialist_fanout_join"
    ):
        candidates.append(
            {
                "template": "specialist-fanout-join",
                "reason": "closed facts request named specialist fan-out with join",
            }
        )
    if (
        _truthy(facts.get("needs_dry_run_approval"))
        or facts.get("task_shape") == "dry_run_human_approval"
    ):
        candidates.append(
            {
                "template": "dry-run-human-approval",
                "reason": "closed facts request dry-run evidence before human approval",
            }
        )
    if (
        _truthy(facts.get("needs_memory_recall"))
        or facts.get("task_shape") == "memory_recalled_workflow"
    ):
        candidates.append(
            {
                "template": "memory-recalled-workflow",
                "reason": "closed facts request governed Memory recall before work",
            }
        )
    if _truthy(facts.get("wants_winner")) or facts.get("task_shape") == "competition":
        candidates.append(
            {
                "template": "compete",
                "reason": "closed facts request a winner/judge route",
            }
        )
    if _truthy(facts.get("wants_synthesis")) or facts.get("task_shape") == "fanout_join":
        candidates.append(
            {
                "template": "roundtable",
                "reason": "closed facts request parallel handlers with synthesis",
            }
        )
    if _truthy(facts.get("needs_review")) or facts.get("task_shape") == "creator_reviewer":
        candidates.append(
            {
                "template": "reflection-loop",
                "reason": "closed facts request creator plus reviewer/reflection",
            }
        )
    if _truthy(facts.get("ordered_steps")) or facts.get("task_shape") == "linear_chain":
        candidates.append(
            {
                "template": "prompt-chain",
                "reason": "closed facts request ordered dependent steps",
            }
        )
    if facts.get("task_shape") == "single":
        candidates.append(
            {
                "template": "single-call",
                "reason": "closed facts request one bounded handler",
            }
        )
    return candidates


def _selection_questions(
    missing_fields: list[str],
    candidates: list[dict[str, str]],
) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = [
        {
            "field": field,
            "question": f"Provide closed selector fact {field}.",
            "required": True,
        }
        for field in missing_fields
    ]
    if not missing_fields and len(candidates) != 1:
        questions.append(
            {
                "field": "task_shape",
                "question": "Choose exactly one Tau template shape before selection.",
                "required": True,
                "allowed_values": [
                    "single",
                    "linear_chain",
                    "creator_reviewer",
                    "fanout_join",
                    "competition",
                    "plan_execute_verify",
                    "claim_chain_verification",
                    "specialist_fanout_join",
                    "dry_run_human_approval",
                    "memory_recalled_workflow",
                ],
            }
        )
    return questions


def _truthy(value: object) -> bool:
    return value is True


def _dag_plan_preview(plan_payload: Mapping[str, Any]) -> dict[str, Any]:
    nodes = plan_payload.get("nodes")
    edges = plan_payload.get("control_edges")
    terminals = plan_payload.get("terminal_endpoints")
    return {
        "schema": plan_payload.get("schema"),
        "plan_id": plan_payload.get("plan_id"),
        "plan_sha256": plan_payload.get("plan_sha256"),
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "edge_count": len(edges) if isinstance(edges, list) else 0,
        "entry_node_ids": plan_payload.get("entry_node_ids"),
        "terminal_endpoints": terminals if isinstance(terminals, list) else [],
        "required_evidence": plan_payload.get("required_evidence"),
    }


def _source_to_plan_diff(
    contract: Mapping[str, Any],
    plan_payload: Mapping[str, Any],
) -> dict[str, Any]:
    plan_preview = _dag_plan_preview(plan_payload)
    source_nodes = contract.get("nodes")
    source_edges = contract.get("edges")
    source_required_evidence = contract.get("required_evidence")
    return {
        "source_schema": contract.get("schema"),
        "plan_schema": plan_payload.get("schema"),
        "source_node_count": len(source_nodes) if isinstance(source_nodes, list) else 0,
        "plan_node_count": plan_preview["node_count"],
        "source_edge_count": len(source_edges) if isinstance(source_edges, list) else 0,
        "plan_edge_count": plan_preview["edge_count"],
        "entry_preserved": contract.get("entry_node") in (plan_payload.get("entry_node_ids") or []),
        "required_evidence_preserved": source_required_evidence
        == plan_payload.get("required_evidence"),
    }


def _template_or_raise(template_name: str) -> DagTemplate:
    template = TEMPLATES.get(template_name)
    if template is None:
        raise RuntimeError(f"unknown DAG template: {template_name}")
    return template


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
