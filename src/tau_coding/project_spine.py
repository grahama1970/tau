"""Project-spine checks for revision-bound Tau orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.course_correction import build_course_correction_receipt

PROJECT_SPINE_SCHEMA = "tau.project_spine.v1"
PROJECT_SPINE_CHECK_RECEIPT_SCHEMA = "tau.project_spine_check_receipt.v1"


def write_project_spine_check_receipt(
    *,
    spine_path: Path,
    out: Path,
) -> dict[str, Any]:
    resolved_spine = spine_path.expanduser().resolve()
    resolved_out = out.expanduser().resolve()
    errors: list[str] = []
    spine = _read_json_object(resolved_spine, errors=errors)
    receipt = check_project_spine(spine, spine_path=resolved_spine, input_errors=errors)
    resolved_out.parent.mkdir(parents=True, exist_ok=True)
    resolved_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def check_project_spine(
    spine: dict[str, Any],
    *,
    spine_path: Path | None = None,
    input_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Check a project spine without mutating routes, providers, Memory, or DAGs."""

    errors = list(input_errors or [])
    defects: list[dict[str, Any]] = []
    if spine.get("schema") != PROJECT_SPINE_SCHEMA:
        errors.append(f"schema must be {PROJECT_SPINE_SCHEMA}")
    goal = spine.get("goal") if isinstance(spine.get("goal"), dict) else {}
    goal_hash = _text(goal.get("goal_hash"))
    active_revision_id = _text(goal.get("active_revision_id"))
    if not active_revision_id:
        errors.append("goal.active_revision_id must be a non-empty string")
    if not goal_hash:
        errors.append("goal.goal_hash must be a non-empty string")
    project_id = _text(spine.get("project_id"))
    if not project_id:
        errors.append("project_id must be a non-empty string")

    change_events = _items_by_id(spine.get("change_events"), errors, "change_events")
    lineage = _items_by_id(spine.get("artifact_lineage_index"), errors, "artifact_lineage_index")
    work_items = _list(spine.get("active_work_queue"), errors, "active_work_queue")
    leases = _items_by_id(spine.get("work_lease_index"), errors, "work_lease_index")
    accepted_evidence = _list(
        spine.get("accepted_evidence_index"),
        errors,
        "accepted_evidence_index",
    )
    progress = spine.get("local_progress") if isinstance(spine.get("local_progress"), dict) else {}
    side_effects = _list(spine.get("side_effects"), errors, "side_effects")

    _check_work_queue_revision(
        work_items=work_items,
        active_revision_id=active_revision_id,
        defects=defects,
    )
    _check_lineage(
        lineage=lineage,
        accepted_evidence=accepted_evidence,
        change_events=change_events,
        active_revision_id=active_revision_id,
        defects=defects,
    )
    _check_leases(
        work_items=work_items,
        leases=leases,
        active_revision_id=active_revision_id,
        defects=defects,
    )
    _check_progress(
        progress=progress,
        accepted_evidence=accepted_evidence,
        work_items=work_items,
        defects=defects,
    )
    _check_side_effects(side_effects=side_effects, defects=defects)

    corrections = [
        _course_correction_for_defect(
            defect,
            spine=spine,
            goal_hash=goal_hash,
        )
        for defect in defects
    ]
    status = "PASS" if not errors and not defects else "BLOCKED"
    return {
        "schema": PROJECT_SPINE_CHECK_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source_project_spine": str(spine_path) if spine_path else None,
        "source_project_spine_sha256": (
            f"sha256:{_sha256_file(spine_path)}" if spine_path and spine_path.exists() else None
        ),
        "project_id": project_id,
        "goal": {
            "goal_id": _text(goal.get("goal_id")),
            "active_revision_id": active_revision_id,
            "goal_hash": goal_hash,
        },
        "defect_count": len(defects),
        "defects": defects,
        "course_correction_count": len(corrections),
        "course_corrections": corrections,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau inspected a revision-bound project spine artifact.",
                "Tau checked active work, lineage, accepted evidence, progress, leases, "
                "and side-effect gates deterministically.",
                "Tau emitted bounded course-correction records for blocked or drifting "
                "project-spine states.",
                "Tau did not mutate the DAG, goal, route, provider state, Memory, Herdr, "
                "filesystem work products, or side-effect targets.",
            ],
            "does_not_prove": [
                "Semantic correctness of any project artifact.",
                "Provider/model semantic quality.",
                "Human approval.",
                "That the proposed correction has been executed.",
                "Future route correctness.",
            ],
        },
        "timestamp": _utc_stamp(),
    }


def _check_work_queue_revision(
    *,
    work_items: list[dict[str, Any]],
    active_revision_id: str | None,
    defects: list[dict[str, Any]],
) -> None:
    for item in work_items:
        revision_id = _text(item.get("revision_id"))
        if active_revision_id and revision_id and revision_id != active_revision_id:
            work_id = item.get("work_id") or item.get("id")
            defects.append(
                _defect(
                    "stale_lineage",
                    "active_work_uses_stale_revision",
                    (
                        f"work item {work_id} uses {revision_id}, "
                        f"not active {active_revision_id}"
                    ),
                    target=item,
                )
            )


def _check_lineage(
    *,
    lineage: dict[str, dict[str, Any]],
    accepted_evidence: list[dict[str, Any]],
    change_events: dict[str, dict[str, Any]],
    active_revision_id: str | None,
    defects: list[dict[str, Any]],
) -> None:
    for evidence in accepted_evidence:
        artifact_id = _text(evidence.get("artifact_id"))
        lineage_record = lineage.get(artifact_id or "")
        if lineage_record is None:
            defects.append(
                _defect(
                    "stale_lineage",
                    "accepted_evidence_missing_lineage",
                    f"accepted evidence {artifact_id or '<missing>'} has no lineage record",
                    target=evidence,
                )
            )
            continue
        revision_id = _text(lineage_record.get("revision_id"))
        if active_revision_id and revision_id != active_revision_id:
            defects.append(
                _defect(
                    "stale_lineage",
                    "accepted_evidence_stale_revision",
                    (
                        f"accepted evidence {artifact_id} belongs to {revision_id}, "
                        f"not active {active_revision_id}"
                    ),
                    target=lineage_record,
                )
            )
        for change_id in _string_list(lineage_record.get("depends_on_change_events")):
            change = change_events.get(change_id)
            status = _text(change.get("status")) if change else None
            if status not in {"applied", "accepted", "closed"}:
                defects.append(
                    _defect(
                        "stale_lineage",
                        "accepted_evidence_depends_on_open_change",
                        (
                            f"accepted evidence {artifact_id} depends on unresolved "
                            f"change event {change_id}"
                        ),
                        target={"artifact": lineage_record, "change_event": change or change_id},
                    )
                )


def _check_leases(
    *,
    work_items: list[dict[str, Any]],
    leases: dict[str, dict[str, Any]],
    active_revision_id: str | None,
    defects: list[dict[str, Any]],
) -> None:
    for item in work_items:
        if _text(item.get("status")) not in {"running", "mutating", "provider_pending"}:
            continue
        lease_id = _text(item.get("lease_id"))
        lease = leases.get(lease_id or "")
        if lease is None:
            work_id = item.get("work_id") or item.get("id")
            defects.append(
                _defect(
                    "forbidden_side_effect",
                    "active_mutating_work_missing_lease",
                    f"active work item {work_id} requires a matching lease",
                    target=item,
                )
            )
            continue
        if active_revision_id and _text(lease.get("revision_id")) != active_revision_id:
            defects.append(
                _defect(
                    "forbidden_side_effect",
                    "active_work_lease_stale_revision",
                    f"lease {lease_id} is not bound to active revision {active_revision_id}",
                    target={"work_item": item, "lease": lease},
                )
            )


def _check_progress(
    *,
    progress: dict[str, Any],
    accepted_evidence: list[dict[str, Any]],
    work_items: list[dict[str, Any]],
    defects: list[dict[str, Any]],
) -> None:
    reported = progress.get("reported_percent")
    derived = progress.get("derived_percent")
    if (
        isinstance(reported, int | float)
        and isinstance(derived, int | float)
        and reported >= 100
        and derived < 100
    ):
        defects.append(
            _defect(
                "false_progress",
                "reported_complete_exceeds_derived_progress",
                f"reported progress is {reported}, but receipt-derived progress is {derived}",
                target=progress,
            )
        )
    accepted_count = len(accepted_evidence)
    done_count = len([item for item in work_items if _text(item.get("status")) == "done"])
    if isinstance(reported, int | float) and reported >= 100 and accepted_count < done_count:
        defects.append(
            _defect(
                "false_progress",
                "complete_progress_missing_accepted_evidence",
                "reported complete progress without accepted evidence for every done work item",
                target={
                    "reported_percent": reported,
                    "done_work_count": done_count,
                    "accepted_evidence_count": accepted_count,
                },
            )
        )


def _check_side_effects(
    *,
    side_effects: list[dict[str, Any]],
    defects: list[dict[str, Any]],
) -> None:
    for effect in side_effects:
        status = _text(effect.get("status"))
        gate = effect.get("final_gate") if isinstance(effect.get("final_gate"), dict) else {}
        gate_status = _text(gate.get("status"))
        human_accepted = bool(gate.get("human_accepted_exception"))
        if (
            status in {"requested", "executed", "submitted"}
            and gate_status not in {
            "PROVIDER_READY",
            "SIDE_EFFECT_READY",
            }
            and not human_accepted
        ):
            effect_id = effect.get("side_effect_id") or effect.get("id")
            defects.append(
                _defect(
                    "forbidden_side_effect",
                    "side_effect_without_final_gate",
                    (
                        f"side effect {effect_id} is {status} without a passing "
                        "final gate"
                    ),
                    target=effect,
                )
            )


def _course_correction_for_defect(
    defect: dict[str, Any],
    *,
    spine: dict[str, Any],
    goal_hash: str | None,
) -> dict[str, Any]:
    goal = spine.get("goal") if isinstance(spine.get("goal"), dict) else {}
    return build_course_correction_receipt(
        trigger=str(defect["trigger"]),
        run_id=_text(spine.get("run_id")),
        dag_id=_text(spine.get("dag_id")),
        goal_hash=goal_hash,
        target={"project_id": spine.get("project_id")},
        node_id="project-spine",
        agent="project-spine-checker",
        attempt=1,
        observed_state={
            "goal_id": goal.get("goal_id"),
            "active_revision_id": goal.get("active_revision_id"),
            "defect_code": defect["code"],
            "defect_message": defect["message"],
            "defect_target": defect["target"],
        },
        reason=defect["message"],
        live=True,
    )


def _defect(
    trigger: str,
    code: str,
    message: str,
    *,
    target: Any,
) -> dict[str, Any]:
    return {
        "trigger": trigger,
        "code": code,
        "severity": "BLOCK",
        "message": message,
        "target": target,
    }


def _read_json_object(path: Path, *, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"project spine unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("project spine root must be a JSON object")
        return {}
    return payload


def _items_by_id(value: Any, errors: list[str], label: str) -> dict[str, dict[str, Any]]:
    items = _list(value, errors, label)
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        item_id = (
            _text(item.get("id"))
            or _text(item.get("artifact_id"))
            or _text(item.get("lease_id"))
            or _text(item.get("event_id"))
        )
        if not item_id:
            errors.append(f"{label}[{index}] requires id, artifact_id, lease_id, or event_id")
            continue
        result[item_id] = item
    return result


def _list(value: Any, errors: list[str], label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{label} must be a list when present")
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        result.append(item)
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
