"""Deterministic command workers for the gs001-closure-audit workflow.

PDF Lab produces evidence (comparison, second-pass backlog, human triage
queue); this workflow decides what counts. Four read-only stages:

  validate  — artifact presence + schema admissibility (fail closed)
  verdict   — recompute the GS001 closure verdict from the artifacts
  project   — dry-run tau.generated_ticket.v1 projections from the backlog
  publish   — hash-bound closure report

Nothing here calls a model, mutates a repository, or touches the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
VALIDATION_SCHEMA = "tau.gs001_artifact_validation.v1"
VERDICT_SCHEMA = "tau.gs001_closure_verdict.v1"
PROJECTION_SCHEMA = "tau.gs001_ticket_projection.v1"
REPORT_SCHEMA = "tau.gs001_closure_report.v1"

_EXPECTED_ARTIFACT_SCHEMAS = {
    "comparison_json": ("pdf-lab.comparison.v2",),
    "backlog_json": ("pdf_lab.second_pass_backlog.v1",),
    "triage_queue_json": ("pdf-lab.human-triage-queue.v1",),
    "expected_contract_json": ("pdf_lab.golden_slice_expected.v3",),
}

_PENDING = "PENDING"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "verdict", "project"):
        sub = subparsers.add_parser(name)
        _common(sub)
        sub.add_argument("--output", type=Path, required=True)
    publish = subparsers.add_parser("publish")
    _common(publish)
    publish.add_argument("--json-output", type=Path, required=True)
    publish.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    request = _read_json(args.request, label="gs001 closure audit request")
    delay = float(args.step_delay_seconds)
    if delay < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    if delay:
        time.sleep(delay)
    if args.command == "validate":
        _validate(request, output=args.output, receipt=args.receipt)
    elif args.command == "verdict":
        _verdict(request, output=args.output, receipt=args.receipt)
    elif args.command == "project":
        _project(request, output=args.output, receipt=args.receipt)
    else:
        _publish(
            request,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            receipt=args.receipt,
        )
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--step-delay-seconds", type=float, default=0.0)


# --- validate -------------------------------------------------------------


def _validate(request: dict[str, Any], *, output: Path, receipt: Path) -> None:
    errors: list[str] = []
    artifacts: list[dict[str, str]] = []
    checked: dict[str, dict[str, Any]] = {}
    for key, allowed_schemas in _EXPECTED_ARTIFACT_SCHEMAS.items():
        raw_path = request.get(key)
        if not raw_path:
            errors.append(f"request missing artifact path: {key}")
            continue
        path = Path(str(raw_path)).expanduser()
        if not path.exists():
            errors.append(f"{key} not found: {path}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{key} unreadable: {exc}")
            continue
        schema = payload.get("schema_version")
        if schema not in allowed_schemas:
            errors.append(
                f"{key} schema_version {schema!r} not in {list(allowed_schemas)}"
            )
            continue
        checked[key] = {"path": str(path), "schema_version": schema}
        artifacts.append(_artifact(key, path))

    goal_path_raw = request.get("goal_md")
    goal_pins: dict[str, str] = {}
    if not goal_path_raw:
        errors.append("request missing artifact path: goal_md")
    else:
        goal_path = Path(str(goal_path_raw)).expanduser()
        if not goal_path.exists():
            errors.append(f"goal_md not found: {goal_path}")
        else:
            goal_pins = _goal_pins(goal_path.read_text(encoding="utf-8"))
            if not goal_pins:
                errors.append("goal_md has no '## Goal-lock pins' section")
            artifacts.append(_artifact("goal_md", goal_path))

    validation = {
        "schema": VALIDATION_SCHEMA,
        "ok": not errors,
        "errors": errors,
        "artifacts_checked": checked,
        "goal_pins": goal_pins,
        "goal_pins_pending": sorted(
            key for key, value in goal_pins.items() if _PENDING in value
        ),
    }
    _write_json(output, validation)
    if errors:
        _write_json(
            receipt,
            _receipt(
                request,
                node_id="validate-artifacts",
                status="BLOCKED",
                artifacts=artifacts,
                accepted_output=None,
                commands_run=["deterministic artifact/schema validation"],
                errors=errors,
                handoff="Blocked: supply the missing/invalid artifacts and re-run.",
            ),
        )
        return
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="validate-artifacts",
            status="PASS",
            artifacts=artifacts,
            accepted_output={
                "schema": VALIDATION_SCHEMA,
                "summary": "GS001 artifact validation completed.",
                "ok": True,
                "errors": [],
                "goal_pins_pending": validation["goal_pins_pending"],
            },
            commands_run=["deterministic artifact/schema validation"],
            errors=[],
            handoff="Artifact validation available for closure verdict.",
        ),
    )


# --- verdict --------------------------------------------------------------


def _verdict(request: dict[str, Any], *, output: Path, receipt: Path) -> None:
    validation = _accepted_input(VALIDATION_SCHEMA)
    comparison = _read_json(
        Path(str(request["comparison_json"])).expanduser(), label="comparison"
    )
    backlog = _read_json(
        Path(str(request["backlog_json"])).expanduser(), label="backlog"
    )
    triage = _read_json(
        Path(str(request["triage_queue_json"])).expanduser(), label="triage queue"
    )
    contract = _read_json(
        Path(str(request["expected_contract_json"])).expanduser(),
        label="expected contract",
    )

    blockers: list[str] = []
    defect_vector = comparison.get("defect_vector") or {}
    if comparison.get("passed") is not True:
        for item in comparison.get("blockers") or ["comparison_not_passed"]:
            blockers.append(f"comparison:{item}")
    task_count = int(triage.get("task_count") or 0)
    if task_count > 0:
        blockers.append(f"human_triage_open:{task_count}")
    agent_resolved = triage.get("agent_resolved_findings")
    resolved_count = len(agent_resolved) if isinstance(agent_resolved, list) else 0
    finding_count = int(backlog.get("finding_count") or 0)
    if finding_count < resolved_count:
        blockers.append(
            f"backlog_untracked_findings:{resolved_count - finding_count}"
        )
    for entry in backlog.get("entries") or []:
        key = str(entry.get("defect_key") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", key):
            blockers.append(f"backlog_entry_bad_defect_key:{entry.get('finding_id')}")
    if contract.get("contract_status") != "locked":
        blockers.append(
            f"expected_contract_not_locked:{contract.get('contract_status')}"
        )
    pending_rows = [
        row.get("id")
        for row in contract.get("expected_elements") or []
        if row.get("pending_recovery")
    ]
    if pending_rows:
        blockers.append(f"expected_contract_pending_rows:{len(pending_rows)}")
    pins_pending = validation.get("goal_pins_pending") or []
    if pins_pending:
        blockers.append(f"goal_not_locked:{','.join(pins_pending)}")

    verdict = {
        "schema": VERDICT_SCHEMA,
        "closed": not blockers,
        "blockers": blockers,
        "defect_vector": defect_vector,
        "human_triage_task_count": task_count,
        "agent_resolved_count": resolved_count,
        "backlog_entry_count": int(backlog.get("backlog_count") or 0),
        "semantic_truth": "NOT_CLAIMED",
    }
    _write_json(output, verdict)
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="verdict-closure",
            status="PASS",
            artifacts=[_artifact("gs001_closure_verdict", output)],
            accepted_output={
                "schema": VERDICT_SCHEMA,
                "summary": "GS001 closure verdict computed.",
                "closed": verdict["closed"],
                "blockers": blockers,
            },
            commands_run=["deterministic closure verdict recomputation"],
            errors=[],
            handoff="Closure verdict available for ticket projection.",
        ),
    )


# --- project --------------------------------------------------------------


def _project(request: dict[str, Any], *, output: Path, receipt: Path) -> None:
    backlog = _read_json(
        Path(str(request["backlog_json"])).expanduser(), label="backlog"
    )
    github = request.get("github") or {}
    owner = str(github.get("owner") or "grahama1970")
    repo = str(github.get("repo") or "pdf_oxide")
    tickets: list[dict[str, Any]] = []
    for entry in backlog.get("entries") or []:
        defect_key = str(entry.get("defect_key") or "")
        title = (
            f"[pdf-lab] {entry.get('kind')} on page {entry.get('page')} "
            f"({defect_key[:19]})"
        )
        body_lines = [
            "## Discrepancy",
            f"- defect_key: `{defect_key}`",
            f"- observation_id: `{entry.get('observation_id')}`",
            f"- page: {entry.get('page')}",
            f"- kind: {entry.get('kind')}",
            f"- classification: {entry.get('classification')}",
            f"- target: {entry.get('target_id')}",
            f"- proposed owner layer: {entry.get('proposed_owner_layer')}",
            "",
            "## Evidence",
            f"{entry.get('reason')}",
            "",
            "## Recommended engine fix",
            f"{entry.get('recommended_engine_fix') or 'NONE PROVIDED — repair agent must first propose guidance.'}",
            "",
            "## Closure requirement",
            "Deterministic proof only: pdf-lab regression-check PASS with this",
            "defect class strictly decreased and no blocking class increased.",
        ]
        tickets.append(
            {
                "schema": "tau.generated_ticket.v1",
                "github": {"owner": owner, "repo": repo},
                "goal": request.get("goal"),
                "kind": "issue",
                "title": title[:120],
                "body": "\n".join(body_lines),
                "labels": ["type:bug", "agent-work", "next:coder"],
                "requested_work": "bounded extraction repair under GS001 goal",
                "apply": False,
                "dedup": {
                    "defect_key": defect_key,
                    "strategy": "update_existing_issue_on_repeat_observation",
                },
            }
        )
    projection = {
        "schema": PROJECTION_SCHEMA,
        "dry_run": True,
        "ticket_count": len(tickets),
        "tickets": tickets,
    }
    _write_json(output, projection)
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="project-tickets",
            status="PASS",
            artifacts=[_artifact("gs001_ticket_projection", output)],
            accepted_output={
                "schema": PROJECTION_SCHEMA,
                "summary": f"Projected {len(tickets)} dry-run tickets from the backlog.",
                "ticket_count": len(tickets),
                "dry_run": True,
            },
            commands_run=["deterministic dry-run ticket projection"],
            errors=[],
            handoff="Dry-run ticket projections available for closure publication.",
        ),
    )


# --- publish --------------------------------------------------------------


def _publish(
    request: dict[str, Any],
    *,
    json_output: Path,
    markdown_output: Path,
    receipt: Path,
) -> None:
    verdict = _accepted_input(VERDICT_SCHEMA)
    projection = _accepted_input(PROJECTION_SCHEMA)
    report = {
        "schema": REPORT_SCHEMA,
        "closed": verdict.get("closed") is True,
        "blockers": verdict.get("blockers") or [],
        "ticket_count": projection.get("ticket_count", 0),
        "dry_run_tickets_only": True,
        "goal_hash": _goal_hash(request),
        "semantic_truth": "NOT_CLAIMED",
    }
    _write_json(json_output, report)
    lines = [
        "# GS001 Closure Audit",
        "",
        f"- closed: **{report['closed']}**",
        f"- goal_hash: `{report['goal_hash']}`",
        f"- dry-run tickets projected: {report['ticket_count']}",
        "",
        "## Blockers" if report["blockers"] else "## No blockers",
    ]
    for blocker in report["blockers"]:
        lines.append(f"- {blocker}")
    markdown_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="publish-closure",
            status="PASS",
            artifacts=[
                _artifact("gs001_closure_report", json_output),
                _artifact("gs001_closure_report_md", markdown_output),
            ],
            accepted_output={
                "schema": REPORT_SCHEMA,
                "summary": "GS001 closure report published.",
                "closed": report["closed"],
                "blocker_count": len(report["blockers"]),
                "artifacts": [_artifact("gs001_closure_report", json_output)],
            },
            commands_run=["deterministic closure report publication"],
            errors=[],
            handoff="Closure report published; human review decides acceptance.",
        ),
    )


# --- helpers --------------------------------------------------------------


_GOAL_PIN_PATTERN = re.compile(r"^\s*-\s*`(?P<key>[a-z0-9_]+)`\s*:\s*(?P<value>.+?)\s*$")


def _goal_pins(goal_text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    in_pins = False
    for line in goal_text.splitlines():
        if line.strip().startswith("## Goal-lock pins"):
            in_pins = True
            continue
        if in_pins and line.startswith("## "):
            break
        if in_pins:
            match = _GOAL_PIN_PATTERN.match(line)
            if match:
                pins[match.group("key")] = match.group("value").strip("`")
    return pins


def _accepted_input(schema: str) -> dict[str, Any]:
    context_value = os.environ.get("TAU_GENERIC_DAG_CONTEXT")
    if not context_value:
        raise RuntimeError("TAU_GENERIC_DAG_CONTEXT is required")
    context = _read_json(Path(context_value), label="generic DAG context")
    inputs = context.get("accepted_inputs")
    if not isinstance(inputs, list):
        raise RuntimeError("generic DAG accepted_inputs is missing")
    for item in inputs:
        if not isinstance(item, dict):
            continue
        if item.get("schema") == schema:
            return item
        accepted = item.get("accepted_output")
        if isinstance(accepted, dict) and accepted.get("schema") == schema:
            return accepted
    raise RuntimeError(f"accepted input missing schema {schema}")


def _receipt(
    request: dict[str, Any],
    *,
    node_id: str,
    status: str,
    artifacts: list[dict[str, str]],
    accepted_output: dict[str, Any] | None,
    commands_run: list[str],
    errors: list[str],
    handoff: str,
) -> dict[str, Any]:
    return {
        "schema": NODE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "status": status,
        "verdict": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": _goal_hash(request),
        "artifacts": artifacts,
        "accepted_output": accepted_output,
        "commands_run": commands_run,
        "errors": errors,
        "policy_exceptions": [],
        "handoff_summary": handoff,
    }


def _goal_hash(request: dict[str, Any]) -> str:
    goal = request.get("goal")
    if not isinstance(goal, dict) or not isinstance(goal.get("goal_hash"), str):
        raise RuntimeError("gs001 closure audit request goal_hash is missing")
    return str(goal["goal_hash"])


def _artifact(kind: str, path: Path) -> dict[str, str]:
    return {"kind": kind, "path": str(path.resolve()), "sha256": _file_sha256(path)}


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
