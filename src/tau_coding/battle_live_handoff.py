"""Run bounded Tau handoffs for Battle Red and Blue teams.

The command writes Tau handoff, Scillm-call, materialization, validation, and
subagent receipts. It supports one or more bounded Red and Blue workers. Each
worker is a separate Tau subagent receipt. Worker 0 also writes the legacy
team-level paths used by the current visibility checks.

The harness exits successfully when it recorded receipts. BLOCKED materialization
does not become proof. Downstream Battle Judge must require materialized
executable artifact paths before claiming PASS.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .battle_scillm import (
    call_battle_json_task,
    call_battle_subagent,
    parse_json_object,
    preflight_battle_scillm_auth,
)

# ADAPTIVE_LINEAGE_GATE_V2


def main() -> None:
    """CLI entry point for `python -m tau_coding.battle_live_handoff`."""
    args = _parse_args()
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    scillm_auth_preflight = _run_auth_preflight(args)
    if scillm_auth_preflight.get("ok") is not True:
        _write_auth_blocked_manifest(
            out_dir=out_dir,
            args=args,
            started=started,
            scillm_auth_preflight=scillm_auth_preflight,
            spawn_child=args.spawn_red_child,
            parent_spawn_decision=args.parent_spawn_decision,
        )
        return
    scillm_auth_preflight_path = out_dir / "scillm-auth-preflight.json"
    _write_json(scillm_auth_preflight_path, scillm_auth_preflight)

    if args.parent_spawn_decision:
        context = _read_context(args.battle_context_json)
        parent_receipt = _read_json(args.parent_subagent_receipt)
        parent_receipt["receipt_path"] = str(args.parent_subagent_receipt.expanduser().resolve())
        pressure_receipt = _read_json(args.pressure_receipt)
        pressure_receipt["receipt_path"] = str(args.pressure_receipt.expanduser().resolve())
        decision = _run_parent_spawn_decision(
            out_dir=out_dir,
            battle_id=args.battle_id,
            run_id=args.run_id,
            scenario_id=args.scenario_id,
            persona=args.red_persona,
            model=args.model,
            scillm_base_url=args.scillm_base_url,
            timeout_s=args.timeout_s,
            context=context,
            parent_receipt=parent_receipt,
            pressure_receipt=pressure_receipt,
        )
        _write_json(out_dir / "spawn-decision-receipt.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return

    if args.spawn_red_child:
        context = _read_context(args.battle_context_json)
        parent_receipt = _read_json(args.parent_subagent_receipt)
        parent_receipt["receipt_path"] = str(args.parent_subagent_receipt.expanduser().resolve())

        pressure_receipt: dict[str, Any] | None = None
        spawn_decision_receipt: dict[str, Any] | None = None
        if args.pressure_receipt is not None:
            pressure_receipt = _read_json(args.pressure_receipt)
            pressure_receipt["receipt_path"] = str(args.pressure_receipt.expanduser().resolve())
        if args.spawn_decision_receipt is not None:
            spawn_decision_receipt = _read_json(args.spawn_decision_receipt)
            spawn_decision_receipt["receipt_path"] = str(
                args.spawn_decision_receipt.expanduser().resolve()
            )
        if (pressure_receipt is None) != (spawn_decision_receipt is None):
            raise ValueError(
                "--pressure-receipt and --spawn-decision-receipt must be supplied together"
            )
        if pressure_receipt is not None and spawn_decision_receipt is not None:
            errors = _prekill_spawn_input_errors(
                pressure_receipt=pressure_receipt,
                decision_receipt=spawn_decision_receipt,
            )
            if errors:
                manifest = {
                    "schema": "tau.battle_spawn_child_proof.v1",
                    "battle_id": args.battle_id,
                    "run_id": args.run_id,
                    "scenario_id": args.scenario_id,
                    "status": "BLOCKED",
                    "reason": errors[0],
                    "errors": errors,
                    "mocked": False,
                    "live": True,
                    "duration_seconds": round(time.time() - started, 6),
                    "teams": [],
                    "lineage_spawns": [],
                    "scillm_auth_preflight": str(scillm_auth_preflight_path),
                }
                _write_json(out_dir / "spawn-manifest.json", manifest)
                print(json.dumps(manifest, indent=2, sort_keys=True))
                return

        child = _run_spawned_red_child(
            out_dir=out_dir,
            battle_id=args.battle_id,
            run_id=args.run_id,
            scenario_id=args.scenario_id,
            persona=_persona_for_worker(
                args.red_persona,
                args.child_worker_index,
            ),
            model=args.model,
            scillm_base_url=args.scillm_base_url,
            timeout_s=args.timeout_s,
            context=context,
            parent_receipt=parent_receipt,
            worker_id=args.child_worker_id,
            worker_index=args.child_worker_index,
            lane_id=args.child_lane_id,
            pressure_receipt=pressure_receipt,
            spawn_decision_receipt=spawn_decision_receipt,
        )
        spawn_receipt = _write_spawn_receipt(
            out_dir=out_dir,
            parent_receipt=parent_receipt,
            child=child,
            battle_id=args.battle_id,
            run_id=args.run_id,
        )
        manifest = {
            "schema": "tau.battle_spawn_child_proof.v1",
            "battle_id": args.battle_id,
            "run_id": args.run_id,
            "scenario_id": args.scenario_id,
            "status": child.get("status"),
            "mocked": False,
            "live": True,
            "duration_seconds": round(time.time() - started, 6),
            "teams": [child],
            "scillm_auth_preflight": str(scillm_auth_preflight_path),
            "lineage_spawns": [spawn_receipt["spawn"]],
            "spawn_receipt_path": spawn_receipt["path"],
            "claims": {
                "proves": [
                    "Tau spawned a receipt-backed Red child lane from an explicit "
                    "parent subagent receipt.",
                ],
                "does_not_prove": [
                    "Blue kill, fastest crash, or promotion.",
                    "Judge replay of the child lane.",
                ],
            },
        }
        _write_json(out_dir / "spawn-manifest.json", manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return

    context = _read_context(args.battle_context_json)

    teams: list[dict[str, Any]] = []
    for index in range(args.red_workers):
        teams.append(
            _run_team(
                out_dir=out_dir,
                battle_id=args.battle_id,
                run_id=args.run_id,
                scenario_id=args.scenario_id,
                team="red",
                persona=_persona_for_worker(args.red_persona, index),
                model=args.model,
                scillm_base_url=args.scillm_base_url,
                timeout_s=args.timeout_s,
                context=context,
                worker_index=index,
            )
        )
    for index in range(args.blue_workers):
        teams.append(
            _run_team(
                out_dir=out_dir,
                battle_id=args.battle_id,
                run_id=args.run_id,
                scenario_id=args.scenario_id,
                team="blue",
                persona=_persona_for_worker(args.blue_persona, index),
                model=args.model,
                scillm_base_url=args.scillm_base_url,
                timeout_s=args.timeout_s,
                context=context,
                worker_index=index,
            )
        )

    red_pass_count = _count_pass(teams, "red")
    blue_pass_count = _count_pass(teams, "blue")
    status = "PASS" if red_pass_count > 0 and blue_pass_count > 0 else "BLOCKED"
    manifest = {
        "schema": "tau.battle_live_handoff_proof.v1",
        "battle_id": args.battle_id,
        "run_id": args.run_id,
        "scenario_id": args.scenario_id,
        "status": status,
        "mocked": False,
        "live": True,
        "surface": "scillm.chat_completions",
        "model": args.model,
        "scillm_base_url": args.scillm_base_url,
        "duration_seconds": round(time.time() - started, 6),
        "handoff_topology": "bounded_worker_matrix",
        "requested_workers": {
            "red": args.red_workers,
            "blue": args.blue_workers,
        },
        "materialized_counts": {
            "red": red_pass_count,
            "blue": blue_pass_count,
        },
        "scillm_auth_preflight": str(scillm_auth_preflight_path),
        "claims": {
            "proves": [
                "Tau consumed bounded Battle Red and Blue handoffs.",
                "Tau attempted Scillm chat-completions calls for each requested worker.",
                "Tau wrote tau.subagent_receipt.v1 artifacts for each requested worker.",
                "Each worker has its own materialization receipt.",
            ],
            "does_not_prove": [
                "Battle Docker scorekeeper PASS unless Battle consumes this manifest "
                "and Judge receipts.",
                "Unbounded Battle swarm execution.",
                "Child-spawn lineage; this rung uses bounded independent workers.",
                "Blue kill, fastest crash, or promotion.",
            ],
        },
        "teams": teams,
    }
    _write_json(out_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded Battle Tau live handoffs.")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--battle-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--red-persona", required=True)
    parser.add_argument("--blue-persona", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--scillm-base-url", required=True)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--battle-context-json", type=Path, default=None)
    parser.add_argument("--red-workers", type=int, default=1)
    parser.add_argument("--blue-workers", type=int, default=1)
    parser.add_argument(
        "--parent-spawn-decision",
        action="store_true",
        help="Run one live parent Red pressure-backed spawn decision.",
    )
    parser.add_argument(
        "--spawn-red-child",
        action="store_true",
        help="Run one spawned Red child from a parent subagent receipt.",
    )
    parser.add_argument("--parent-subagent-receipt", type=Path, default=None)
    parser.add_argument("--pressure-receipt", type=Path, default=None)
    parser.add_argument("--spawn-decision-receipt", type=Path, default=None)
    parser.add_argument("--child-worker-id", default="red-1")
    parser.add_argument("--child-worker-index", type=int, default=1)
    parser.add_argument("--child-lane-id", default="payload-857-red-1")
    args = parser.parse_args()
    if args.parent_spawn_decision and args.spawn_red_child:
        raise ValueError("--parent-spawn-decision and --spawn-red-child are mutually exclusive")
    if args.parent_spawn_decision:
        if args.parent_subagent_receipt is None:
            raise ValueError("--parent-subagent-receipt is required with --parent-spawn-decision")
        if args.pressure_receipt is None:
            raise ValueError("--pressure-receipt is required with --parent-spawn-decision")
    if args.spawn_red_child and args.parent_subagent_receipt is None:
        raise ValueError("--parent-subagent-receipt is required with --spawn-red-child")
    if args.red_workers < 1:
        raise ValueError("--red-workers must be >= 1")
    if args.blue_workers < 1 and not args.spawn_red_child and not args.parent_spawn_decision:
        raise ValueError("--blue-workers must be >= 1 unless a decision or child-spawn mode is set")
    return args


def _run_auth_preflight(args: argparse.Namespace) -> dict[str, Any]:
    return preflight_battle_scillm_auth(
        scillm_base_url=args.scillm_base_url,
        model=args.model,
    )


def _write_auth_blocked_manifest(
    *,
    out_dir: Path,
    args: argparse.Namespace,
    started: float,
    scillm_auth_preflight: dict[str, Any],
    spawn_child: bool,
    parent_spawn_decision: bool = False,
) -> None:
    preflight_path = out_dir / "scillm-auth-preflight.json"
    _write_json(preflight_path, scillm_auth_preflight)
    if parent_spawn_decision:
        receipt = {
            "schema": "tau.battle_parent_spawn_decision_receipt.v1",
            "status": "BLOCKED",
            "receipt_id": "tau-parent-spawn-decision:red-0",
            "battle_id": args.battle_id,
            "run_id": args.run_id,
            "scenario_id": args.scenario_id,
            "author": {
                "team": "red",
                "worker_id": "red-0",
                "lane_id": "payload-857-receipt",
                "persona": args.red_persona,
                "authority": "tau_parent",
            },
            "decision": None,
            "reason_codes": [],
            "pressure_receipt": None,
            "inherited_evidence_refs": [],
            "scillm_call_receipt": None,
            "errors": [str(scillm_auth_preflight.get("reason") or "scillm_auth_preflight_failed")],
            "mocked": False,
            "live": True,
            "duration_seconds": round(time.time() - started, 6),
        }
        _write_json(out_dir / "spawn-decision-receipt.json", receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return
    if spawn_child:
        manifest = {
            "schema": "tau.battle_spawn_child_proof.v1",
            "battle_id": args.battle_id,
            "run_id": args.run_id,
            "scenario_id": args.scenario_id,
            "status": "BLOCKED",
            "reason": (scillm_auth_preflight.get("reason") or "scillm_auth_preflight_failed"),
            "mocked": False,
            "live": True,
            "duration_seconds": round(time.time() - started, 6),
            "teams": [],
            "lineage_spawns": [],
            "scillm_auth_preflight": str(preflight_path),
            "claims": {
                "proves": [
                    "Tau checked Scillm auth before Battle spawned-child materialization.",
                    "Tau did not dispatch a worker after auth preflight failed.",
                ],
                "does_not_prove": [
                    "Worker materialization.",
                    "Battle Docker scorekeeper PASS.",
                ],
            },
        }
        _write_json(out_dir / "spawn-manifest.json", manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return

    manifest = {
        "schema": "tau.battle_live_handoff_proof.v1",
        "battle_id": args.battle_id,
        "run_id": args.run_id,
        "scenario_id": args.scenario_id,
        "status": "BLOCKED",
        "reason": (scillm_auth_preflight.get("reason") or "scillm_auth_preflight_failed"),
        "mocked": False,
        "live": True,
        "surface": "scillm.chat_completions",
        "model": args.model,
        "scillm_base_url": args.scillm_base_url,
        "duration_seconds": round(time.time() - started, 6),
        "handoff_topology": "bounded_worker_matrix",
        "requested_workers": {
            "red": args.red_workers,
            "blue": args.blue_workers,
        },
        "materialized_counts": {
            "red": 0,
            "blue": 0,
        },
        "scillm_auth_preflight": str(preflight_path),
        "claims": {
            "proves": [
                "Tau checked Scillm auth before Battle worker materialization.",
                "Tau did not dispatch Battle workers after auth preflight failed.",
            ],
            "does_not_prove": [
                "Worker materialization.",
                "Battle Docker scorekeeper PASS.",
            ],
        },
        "teams": [],
    }
    _write_json(out_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


def _run_team(
    *,
    out_dir: Path,
    battle_id: str,
    run_id: str,
    scenario_id: str,
    team: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    context: dict[str, Any],
    worker_index: int,
) -> dict[str, Any]:
    worker_id = f"{team}-{worker_index}"
    lane_id = (
        "payload-857-receipt" if team == "red" and worker_index == 0 else f"payload-857-{worker_id}"
    )
    team_dir = _worker_dir(
        out_dir=out_dir, team=team, worker_index=worker_index, worker_id=worker_id
    )
    team_dir.mkdir(parents=True, exist_ok=True)

    handoff = _handoff(
        battle_id=battle_id,
        run_id=run_id,
        scenario_id=scenario_id,
        team=team,
        persona=persona,
        context=_context_for_team(context, team),
        worker_id=worker_id,
        worker_index=worker_index,
        lane_id=lane_id,
    )
    handoff_path = team_dir / "handoff.json"
    _write_json(handoff_path, handoff)

    scillm_call = call_battle_subagent(
        handoff=handoff,
        team=team,
        persona=persona,
        model=model,
        scillm_base_url=scillm_base_url,
        timeout_s=timeout_s,
    )
    scillm_call_path = team_dir / "scillm-call-receipt.json"
    _write_json(scillm_call_path, scillm_call)

    materialized = _materialize_team_artifact(team_dir=team_dir, team=team, scillm_call=scillm_call)
    materialized["scillm_call_receipt_sha256"] = _file_sha256(scillm_call_path)
    materialized_path = team_dir / "materialized-artifact-receipt.json"
    _write_json(materialized_path, materialized)

    subagent_receipt = _subagent_receipt(
        battle_id=battle_id,
        run_id=run_id,
        scenario_id=scenario_id,
        team=team,
        persona=persona,
        model=model,
        handoff_path=handoff_path,
        scillm_call_path=scillm_call_path,
        materialized_path=materialized_path,
        materialized=materialized,
        worker_id=worker_id,
        lane_id=lane_id,
    )
    subagent_receipt_path = team_dir / "tau-subagent-receipt.json"
    _write_json(subagent_receipt_path, subagent_receipt)

    validation = _validation_receipt(
        battle_id=battle_id,
        run_id=run_id,
        team=team,
        worker_id=worker_id,
        handoff_path=handoff_path,
        scillm_call_path=scillm_call_path,
        subagent_receipt_path=subagent_receipt_path,
        materialized=materialized,
    )
    validation_path = team_dir / "validation.json"
    _write_json(validation_path, validation)

    return {
        "team": team,
        "worker_id": worker_id,
        "worker_index": worker_index,
        "lane_id": lane_id if team == "red" else None,
        "persona": persona,
        "status": materialized["status"],
        "error": None if materialized["status"] == "PASS" else materialized.get("reason"),
        "model": model,
        "surface": "scillm.chat_completions",
        "http_status": scillm_call.get("http_status"),
        "handoff": str(handoff_path),
        "scillm_call": str(scillm_call_path),
        "subagent_receipt": str(subagent_receipt_path),
        "validation": str(validation_path),
        "validation_ok": validation["status"] == "PASS",
        "materialized": materialized,
        "materialized_artifact": materialized,
    }


def _context_for_team(context: dict[str, Any], team: str) -> dict[str, Any]:
    """Return common Battle context plus only the requested team's private projection."""
    projected = copy.deepcopy(
        {key: value for key, value in context.items() if key != "team_contexts"}
    )
    summary = projected.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("teams"), dict):
        own_summary = summary["teams"].get(team)
        if not isinstance(own_summary, dict):
            raise ValueError(f"summary.teams missing object for {team}")
        summary["teams"] = {team: own_summary}
    team_contexts = context.get("team_contexts")
    if team_contexts is None:
        return projected
    if not isinstance(team_contexts, dict):
        raise ValueError("team_contexts must be an object")
    team_context = team_contexts.get(team)
    if not isinstance(team_context, dict):
        raise ValueError(f"team_contexts missing object for {team}")
    projected["team_context"] = team_context
    return projected


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


PREKILL_SURVIVAL_SPAWN_DECISIONS = frozenset(
    {"strategic_pre_kill", "panic_spawn", "parallel_pivot"}
)


def _run_parent_spawn_decision(
    *,
    out_dir: Path,
    battle_id: str,
    run_id: str,
    scenario_id: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    context: dict[str, Any],
    parent_receipt: dict[str, Any],
    pressure_receipt: dict[str, Any],
) -> dict[str, Any]:
    parent_battle = (
        parent_receipt.get("context", {}).get("battle", {})
        if isinstance(parent_receipt.get("context"), dict)
        and isinstance(parent_receipt.get("context", {}).get("battle"), dict)
        else {}
    )
    parent_worker_id = _first_str(
        parent_receipt.get("worker_id"),
        parent_battle.get("worker_id"),
        "red-0",
    )
    parent_lane_id = _first_str(
        parent_receipt.get("lane_id"),
        parent_battle.get("lane_id"),
        "payload-857-receipt",
    )
    pressure_path = Path(str(pressure_receipt.get("receipt_path") or ""))
    parent_path = Path(str(parent_receipt.get("receipt_path") or ""))

    inherited_evidence_refs = [
        _receipt_ref(
            kind="parent_pressure",
            receipt_id=_first_str(
                pressure_receipt.get("receipt_id"),
                "prekill-pressure",
            ),
            path=pressure_path,
        ),
        _receipt_ref(
            kind="parent_tau_subagent",
            receipt_id=f"tau-parent-subagent:{parent_worker_id}",
            path=parent_path,
        ),
    ]
    result_artifacts = (
        parent_receipt.get("result", {}).get("artifacts", [])
        if isinstance(parent_receipt.get("result"), dict)
        and isinstance(parent_receipt.get("result", {}).get("artifacts"), list)
        else []
    )
    parent_materialized_ref_found = False
    for value in result_artifacts:
        if not isinstance(value, str) or "materialized-artifact-receipt" not in value:
            continue
        artifact_path = Path(value)
        if not artifact_path.is_absolute():
            artifact_path = parent_path.parent / artifact_path
        if artifact_path.exists():
            inherited_evidence_refs.append(
                _receipt_ref(
                    kind="parent_materialized_artifact",
                    receipt_id="tau-parent-materialized-artifact",
                    path=artifact_path,
                )
            )
            parent_materialized_ref_found = True
            break

    required_ids = [
        str(ref["receipt_id"])
        for ref in inherited_evidence_refs
        if isinstance(ref, dict) and ref.get("receipt_id")
    ]
    task = {
        "schema": "tau.battle_parent_spawn_decision_task.v1",
        "battle_id": battle_id,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "parent": {
            "team": "red",
            "worker_id": parent_worker_id,
            "lane_id": parent_lane_id,
        },
        "pressure": pressure_receipt,
        "public_context": _context_for_team(context, "red"),
        "allowed_decisions": sorted(PREKILL_SURVIVAL_SPAWN_DECISIONS),
        "required_inherited_evidence_receipt_ids": required_ids,
        "required_output": {
            "decision": "one allowed_decisions value",
            "reason_codes": ["one or more short strings"],
            "inherited_evidence_receipt_ids": required_ids,
        },
    }
    call = call_battle_json_task(
        task=task,
        system_prompt=(
            "You are the live parent Red Tau subagent. Defender pressure has been "
            "observed but the parent has not yet received a terminal receipt. "
            "Choose exactly one allowed pre-kill child-spawn decision. Return only "
            "a JSON object with decision, reason_codes, and "
            "inherited_evidence_receipt_ids. Copy every required inherited evidence "
            "receipt id exactly; do not claim the parent is killed or terminal."
        ),
        team="red",
        persona=persona,
        model=model,
        scillm_base_url=scillm_base_url,
        timeout_s=timeout_s,
    )
    call_path = out_dir / "parent-spawn-decision-scillm-call-receipt.json"
    _write_json(call_path, call)
    parsed = call.get("parsed_json") if isinstance(call.get("parsed_json"), dict) else {}

    errors: list[str] = []
    if not parent_materialized_ref_found:
        errors.append("parent materialized-artifact receipt is missing")
    decision = str(parsed.get("decision") or "")
    cited_ids = (
        [str(value) for value in parsed.get("inherited_evidence_receipt_ids", [])]
        if isinstance(parsed.get("inherited_evidence_receipt_ids"), list)
        else []
    )
    reason_codes = (
        [str(value) for value in parsed.get("reason_codes", [])]
        if isinstance(parsed.get("reason_codes"), list)
        else []
    )
    if call.get("status") != "PASS":
        errors.append("parent spawn-decision Scillm call did not PASS")
    if decision not in PREKILL_SURVIVAL_SPAWN_DECISIONS:
        errors.append("parent did not choose an allowed pre-kill spawn decision")
    if sorted(cited_ids) != sorted(required_ids):
        errors.append("parent decision did not cite every required inherited receipt id")

    return {
        "schema": "tau.battle_parent_spawn_decision_receipt.v1",
        "status": "PASS" if not errors else "BLOCKED",
        "receipt_id": f"tau-parent-spawn-decision:{parent_worker_id}",
        "battle_id": battle_id,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "author": {
            "team": "red",
            "worker_id": parent_worker_id,
            "lane_id": parent_lane_id,
            "persona": persona,
            "authority": "tau_parent",
        },
        "decision": decision,
        "reason_codes": reason_codes,
        "pressure_receipt": {
            "receipt_id": pressure_receipt.get("receipt_id"),
            "path": str(pressure_path),
            "sha256": _file_sha256(pressure_path) if pressure_path.exists() else None,
        },
        "inherited_evidence_refs": inherited_evidence_refs,
        "scillm_call_receipt": str(call_path),
        "scillm_call_receipt_sha256": _file_sha256(call_path),
        "model_output": parsed,
        "errors": errors,
        "mocked": False,
        "live": True,
    }


def _receipt_ref(*, kind: str, receipt_id: str, path: Path) -> dict[str, Any]:
    return {
        "kind": kind,
        "receipt_id": receipt_id,
        "path": str(path),
        "sha256": _file_sha256(path) if path.exists() else None,
    }


def _prekill_spawn_input_errors(
    *,
    pressure_receipt: dict[str, Any],
    decision_receipt: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    author = (
        decision_receipt.get("author") if isinstance(decision_receipt.get("author"), dict) else {}
    )
    decision_pressure = (
        decision_receipt.get("pressure_receipt")
        if isinstance(decision_receipt.get("pressure_receipt"), dict)
        else {}
    )
    pressure_path = Path(str(pressure_receipt.get("receipt_path") or ""))
    if decision_receipt.get("status") != "PASS":
        errors.append("spawn-decision receipt status is not PASS")
    if str(decision_receipt.get("decision")) not in PREKILL_SURVIVAL_SPAWN_DECISIONS:
        errors.append("spawn-decision receipt does not contain an allowed decision")
    if author.get("team") != "red" or author.get("worker_id") != "red-0":
        errors.append("spawn-decision receipt is not authored by red-0")
    if decision_pressure.get("receipt_id") != pressure_receipt.get("receipt_id"):
        errors.append("spawn-decision receipt cites the wrong pressure receipt")
    if pressure_path.exists() and decision_pressure.get("sha256") != _file_sha256(pressure_path):
        errors.append("spawn-decision receipt cites the wrong pressure hash")
    inherited_refs = decision_receipt.get("inherited_evidence_refs")
    if not isinstance(inherited_refs, list) or not inherited_refs:
        errors.append("spawn-decision receipt has no inherited evidence refs")
    else:
        kinds = {str(ref.get("kind")) for ref in inherited_refs if isinstance(ref, dict)}
        required_kinds = {
            "parent_pressure",
            "parent_tau_subagent",
            "parent_materialized_artifact",
        }
        if not required_kinds <= kinds:
            errors.append("spawn-decision receipt is missing required inherited evidence kinds")
        if any(
            not isinstance(ref, dict) or not ref.get("receipt_id") or not ref.get("sha256")
            for ref in inherited_refs
        ):
            errors.append(
                "spawn-decision inherited evidence refs must contain receipt_id and sha256"
            )
    return errors


def _run_spawned_red_child(
    *,
    out_dir: Path,
    battle_id: str,
    run_id: str,
    scenario_id: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    context: dict[str, Any],
    parent_receipt: dict[str, Any],
    worker_id: str,
    worker_index: int,
    lane_id: str,
    pressure_receipt: dict[str, Any] | None = None,
    spawn_decision_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parent_battle = (
        parent_receipt.get("context", {}).get("battle", {})
        if isinstance(parent_receipt.get("context"), dict)
        and isinstance(parent_receipt.get("context", {}).get("battle"), dict)
        else {}
    )
    parent_worker_id = _first_str(
        parent_receipt.get("worker_id"),
        parent_battle.get("worker_id"),
        parent_receipt.get("subagent_id"),
        "red-0",
    )
    parent_lane_id = _first_str(
        parent_receipt.get("lane_id"),
        parent_battle.get("lane_id"),
        "payload-857-receipt",
    )
    prekill = isinstance(pressure_receipt, dict) and isinstance(spawn_decision_receipt, dict)
    decision = str(spawn_decision_receipt.get("decision")) if prekill else "post_block_handoff"
    inherited_evidence_refs = (
        list(spawn_decision_receipt.get("inherited_evidence_refs") or []) if prekill else []
    )
    inherited_context = (
        [
            f"receipt_id={ref.get('receipt_id')};sha256={ref.get('sha256')}"
            for ref in inherited_evidence_refs
            if isinstance(ref, dict)
        ]
        if prekill
        else [
            f"parent_worker={parent_worker_id}",
            f"parent_lane={parent_lane_id}",
            "ZIP_SLIP_CONFIRMED",
        ]
    )
    leased_task = (
        "Use the inherited parent pressure and exploit receipts to continue the "
        "authorized Zip Slip proof before the parent terminal confirmation."
        if prekill
        else "Continue Zip Slip exploit from parent useful signal after Blue block/handoff."
    )

    spawn_context = dict(context)
    spawn_context["spawn"] = {
        "parent_worker_id": parent_worker_id,
        "parent_lane_id": parent_lane_id,
        "parent_subagent_receipt": str(
            parent_receipt.get("receipt_path") or parent_receipt.get("path") or ""
        ),
        "spawn_type": decision,
        "pressure_receipt_id": (pressure_receipt.get("receipt_id") if prekill else None),
        "spawn_decision_receipt_id": (
            spawn_decision_receipt.get("receipt_id") if prekill else None
        ),
        "inherited_evidence_refs": inherited_evidence_refs,
        "inherited_context": inherited_context,
        "leased_task": leased_task,
    }
    team_dir = _worker_dir(
        out_dir=out_dir,
        team="red",
        worker_index=worker_index,
        worker_id=worker_id,
    )
    handoff = _handoff(
        battle_id=battle_id,
        run_id=run_id,
        scenario_id=scenario_id,
        team="red",
        persona=persona,
        context=spawn_context,
        worker_id=worker_id,
        worker_index=worker_index,
        lane_id=lane_id,
    )
    handoff["schema"] = "tau.battle_spawn_handoff.v1"
    handoff["spawn"] = spawn_context["spawn"]
    handoff_path = team_dir / "handoff.json"
    _write_json(handoff_path, handoff)

    scillm_call = call_battle_subagent(
        handoff=handoff,
        team="red",
        persona=persona,
        model=model,
        scillm_base_url=scillm_base_url,
        timeout_s=timeout_s,
    )
    scillm_call_path = team_dir / "scillm-call-receipt.json"
    _write_json(scillm_call_path, scillm_call)

    materialized = _materialize_team_artifact(
        team_dir=team_dir,
        team="red",
        scillm_call=scillm_call,
    )
    materialized["scillm_call_receipt_sha256"] = _file_sha256(scillm_call_path)
    materialized_path = team_dir / "materialized-artifact-receipt.json"
    _write_json(materialized_path, materialized)

    subagent_receipt = _subagent_receipt(
        battle_id=battle_id,
        run_id=run_id,
        scenario_id=scenario_id,
        team="red",
        persona=persona,
        model=model,
        handoff_path=handoff_path,
        scillm_call_path=scillm_call_path,
        materialized_path=materialized_path,
        materialized=materialized,
        worker_id=worker_id,
        lane_id=lane_id,
    )
    subagent_receipt["parent_worker_id"] = parent_worker_id
    subagent_receipt["parent_lane_id"] = parent_lane_id
    subagent_receipt["spawned_from_receipt"] = True
    subagent_receipt["spawn_type"] = decision
    subagent_receipt["pressure_receipt_id"] = (
        pressure_receipt.get("receipt_id") if prekill else None
    )
    subagent_receipt["spawn_decision_receipt_id"] = (
        spawn_decision_receipt.get("receipt_id") if prekill else None
    )
    subagent_receipt["inherited_evidence_refs"] = inherited_evidence_refs
    subagent_receipt_path = team_dir / "tau-subagent-receipt.json"
    _write_json(subagent_receipt_path, subagent_receipt)

    validation = _validation_receipt(
        battle_id=battle_id,
        run_id=run_id,
        team="red",
        worker_id=worker_id,
        handoff_path=handoff_path,
        scillm_call_path=scillm_call_path,
        subagent_receipt_path=subagent_receipt_path,
        materialized=materialized,
    )
    validation_path = team_dir / "validation.json"
    _write_json(validation_path, validation)

    return {
        "team": "red",
        "worker_id": worker_id,
        "worker_index": worker_index,
        "lane_id": lane_id,
        "parent_worker_id": parent_worker_id,
        "parent_lane_id": parent_lane_id,
        "persona": persona,
        "status": materialized["status"],
        "error": (None if materialized["status"] == "PASS" else materialized.get("reason")),
        "model": model,
        "surface": "scillm.chat_completions",
        "http_status": scillm_call.get("http_status"),
        "handoff": str(handoff_path),
        "scillm_call": str(scillm_call_path),
        "subagent_receipt": str(subagent_receipt_path),
        "validation": str(validation_path),
        "validation_ok": validation["status"] == "PASS",
        "materialized": materialized,
        "materialized_artifact": materialized,
        "spawn_type": decision,
        "pressure_receipt_id": (pressure_receipt.get("receipt_id") if prekill else None),
        "spawn_decision_receipt_id": (
            spawn_decision_receipt.get("receipt_id") if prekill else None
        ),
        "inherited_evidence_refs": inherited_evidence_refs,
        "inherited_context": inherited_context,
    }


def _write_spawn_receipt(
    *,
    out_dir: Path,
    parent_receipt: dict[str, Any],
    child: dict[str, Any],
    battle_id: str,
    run_id: str,
) -> dict[str, Any]:
    lineage_dir = out_dir / "lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    parent_battle = (
        parent_receipt.get("context", {}).get("battle", {})
        if isinstance(parent_receipt.get("context"), dict)
        and isinstance(parent_receipt.get("context", {}).get("battle"), dict)
        else {}
    )
    parent_lane_id = _first_str(
        parent_receipt.get("lane_id"),
        parent_battle.get("lane_id"),
        child.get("parent_lane_id"),
        "payload-857-receipt",
    )
    child_lane_id = _first_str(child.get("lane_id"), "payload-857-red-1")
    receipt_id = f"lineage-spawn-{parent_lane_id}-to-{child_lane_id}"
    rel_path = f"lineage/{receipt_id}.json"
    spawn_type = _first_str(child.get("spawn_type"), "post_block_handoff")
    generation = 1 if spawn_type in PREKILL_SURVIVAL_SPAWN_DECISIONS else 2
    spawn = {
        "schema": "battle.lineage_spawn_receipt.v1",
        "status": "PASS" if child.get("status") == "PASS" else "BLOCKED",
        "battle_id": battle_id,
        "run_id": run_id,
        "receipt_id": receipt_id,
        "parent_lane_id": parent_lane_id,
        "child_lane_id": child_lane_id,
        "parent_worker_id": _first_str(
            parent_receipt.get("worker_id"),
            parent_battle.get("worker_id"),
            "red-0",
        ),
        "child_worker_id": _first_str(child.get("worker_id"), "red-1"),
        "parent_tau_subagent_id": _first_str(
            parent_receipt.get("subagent_id"),
            parent_receipt.get("worker_id"),
            parent_battle.get("worker_id"),
            "red-0",
        ),
        "child_tau_subagent_id": _first_str(
            child.get("worker_id"),
            "red-1",
        ),
        "child_payload_id": child_lane_id,
        "spawn_type": spawn_type,
        "pressure_receipt_id": child.get("pressure_receipt_id"),
        "spawn_decision_receipt_id": child.get("spawn_decision_receipt_id"),
        "inherited_evidence_refs": child.get("inherited_evidence_refs") or [],
        "inherited_context": child.get("inherited_context") or [],
        "leased_task": (
            "Continue the authorized Zip Slip proof using inherited receipt-backed parent evidence."
        ),
        "goal": "Materialize one receipt-backed Red child from the parent decision.",
        "generation": generation,
        "receipt_path": rel_path,
        "parent_subagent_receipt": str(
            parent_receipt.get("receipt_path") or parent_receipt.get("path") or ""
        ),
        "child_subagent_receipt": str(child.get("subagent_receipt") or ""),
    }
    path = out_dir / rel_path
    _write_json(path, spawn)
    return {"path": rel_path, "spawn": spawn}


def _first_str(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return ""


def _worker_dir(*, out_dir: Path, team: str, worker_index: int, worker_id: str) -> Path:
    if worker_index == 0:
        return out_dir / team
    return out_dir / team / "workers" / worker_id


def _handoff(
    *,
    battle_id: str,
    run_id: str,
    scenario_id: str,
    team: str,
    persona: str,
    context: dict[str, Any],
    worker_id: str,
    worker_index: int,
    lane_id: str,
) -> dict[str, Any]:
    return {
        "schema": "tau.battle_team_handoff.v1",
        "battle_id": battle_id,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "team": team,
        "worker_id": worker_id,
        "worker_index": worker_index,
        "lane_id": lane_id,
        "persona": persona,
        "visibility_model": "team_public_only",
        "public_context": context,
        "instructions": {
            "red": (
                "Return a JSON object with artifact_type=red_exploit, exploit_py, and "
                "strategy_genome. "
                "strategy_genome must be an object with selected_methods, rejected_methods, "
                "parameters, mutation_origin, and expected_observation. "
                "The exploit_py script is executed by Battle Judge as: "
                "python red_exploit_submission.py --expect-vulnerable inside a local Docker "
                "workspace containing app.py. "
                "Use Python standard library only. Do not import requests, httpx, flask, "
                "fastapi, urllib, socket, or any network/HTTP package. "
                "Do not assume a web server or localhost:8000. "
                "The script must import import_zip from local app.py, create a temporary zip "
                "file with a path traversal entry such as ../arena_escape.txt, "
                "call import_zip(zip_path, destination) directly, and verify that the escape "
                "file was written outside the destination. "
                "When --expect-vulnerable is passed and the vulnerable write succeeds, print "
                "RED_EXPLOIT_CONFIRMED and exit 0. "
                "When --expect-vulnerable is passed and the write does not occur, exit nonzero. "
                "The script must contain RED_EXPLOIT_CONFIRMED."
            ),
            "blue": (
                "Return a JSON object with artifact_type=blue_patch, app_py, and strategy_genome. "
                "strategy_genome must be an object with selected_methods, rejected_methods, "
                "parameters, mutation_origin, and expected_observation. "
                "The app_py value must be a complete replacement application file."
            ),
        }[team],
    }


def _materialize_team_artifact(
    *, team_dir: Path, team: str, scillm_call: dict[str, Any]
) -> dict[str, Any]:
    parsed = scillm_call.get("parsed_json")
    if not isinstance(parsed, dict):
        parsed = parse_json_object(str(scillm_call.get("response_content") or ""))
    if not isinstance(parsed, dict):
        return {
            "schema": "tau.battle_materialized_artifact_receipt.v1",
            "status": "BLOCKED",
            "reason": "scillm_response_not_json_object",
            "path": None,
            "parsed_keys": [],
        }

    if team == "red":
        return _materialize_red(team_dir=team_dir, parsed=parsed)
    if team == "blue":
        return _materialize_blue(team_dir=team_dir, parsed=parsed)
    return {
        "schema": "tau.battle_materialized_artifact_receipt.v1",
        "status": "BLOCKED",
        "reason": f"unsupported_team:{team}",
        "path": None,
        "parsed_keys": sorted(parsed),
    }


def _materialize_red(*, team_dir: Path, parsed: dict[str, Any]) -> dict[str, Any]:
    script = parsed.get("exploit_py")
    if not isinstance(script, str) or not script.strip():
        return _blocked_materialization("red_exploit_py_missing", parsed)
    if "```" in script:
        return _blocked_materialization("red_exploit_py_contains_markdown_fence", parsed)
    if "RED_EXPLOIT_CONFIRMED" not in script:
        return _blocked_materialization("red_exploit_py_missing_success_marker", parsed)
    contract_error = _red_contract_error(script)
    if contract_error:
        return _blocked_materialization(contract_error, parsed)

    path = team_dir / "red_exploit_submission.py"
    path.write_text(script, encoding="utf-8")
    return {
        "schema": "tau.battle_materialized_artifact_receipt.v1",
        "status": "PASS",
        "artifact_type": "red_exploit",
        "path": str(path),
        "artifact_sha256": _file_sha256(path),
        "artifact_bytes": path.stat().st_size,
        "strategy_genome_sha256": _json_sha256(parsed.get("strategy_genome")),
        "parsed_keys": sorted(parsed),
        "rationale": parsed.get("rationale"),
    }


def _red_contract_error(script: str) -> str | None:
    """Return a fail-closed materialization reason for non-Judge-compatible Red code."""
    banned_imports = {
        "requests",
        "httpx",
        "flask",
        "fastapi",
        "urllib",
        "socket",
        "aiohttp",
    }
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return "red_exploit_py_syntax_invalid"

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])

    if imports & banned_imports:
        return "red_artifact_not_local_stdlib_exploit"
    if "from app import import_zip" not in script and "import app" not in script:
        return "red_artifact_missing_local_app_import"
    if "import_zip(" not in script:
        return "red_artifact_does_not_call_import_zip"
    if "--expect-vulnerable" not in script:
        return "red_artifact_missing_expect_vulnerable_arg"
    return None


def _materialize_blue(*, team_dir: Path, parsed: dict[str, Any]) -> dict[str, Any]:
    app_py = parsed.get("app_py")
    if not isinstance(app_py, str) or not app_py.strip():
        return _blocked_materialization("blue_app_py_missing", parsed)
    if "```" in app_py:
        return _blocked_materialization("blue_app_py_contains_markdown_fence", parsed)

    path = team_dir / "app.py"
    path.write_text(app_py, encoding="utf-8")
    try:
        ast.parse(app_py)
    except SyntaxError as exc:
        return {
            **_blocked_materialization("blue_app_py_syntax_invalid", parsed),
            "diagnostic_path": str(path),
            "syntax_error": str(exc),
        }

    return {
        "schema": "tau.battle_materialized_artifact_receipt.v1",
        "status": "PASS",
        "artifact_type": "blue_patch",
        "path": str(path),
        "artifact_sha256": _file_sha256(path),
        "artifact_bytes": path.stat().st_size,
        "strategy_genome_sha256": _json_sha256(parsed.get("strategy_genome")),
        "parsed_keys": sorted(parsed),
        "rationale": parsed.get("rationale"),
    }


def _blocked_materialization(reason: str, parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "tau.battle_materialized_artifact_receipt.v1",
        "status": "BLOCKED",
        "reason": reason,
        "path": None,
        "parsed_keys": sorted(parsed),
    }


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _subagent_receipt(
    *,
    battle_id: str,
    run_id: str,
    scenario_id: str,
    team: str,
    persona: str,
    model: str,
    handoff_path: Path,
    scillm_call_path: Path,
    materialized_path: Path,
    materialized: dict[str, Any],
    worker_id: str,
    lane_id: str,
) -> dict[str, Any]:
    return {
        "schema": "tau.subagent_receipt.v1",
        "context": {
            "actor_type": "tau",
            "battle": {
                "battle_id": battle_id,
                "team": team,
                "worker_id": worker_id,
                "lane_id": lane_id,
                "persona": persona,
                "scenario_id": scenario_id,
            },
            "run_id": run_id,
            "subagent": f"battle-{team}-{worker_id}",
            "artifacts_read": [str(handoff_path)],
            "assumptions": [
                "Battle owns Docker execution and scorekeeping; Tau owns this handoff and "
                "artifact materialization receipt."
            ],
            "unknowns": [],
        },
        "goal": {
            "goal_id": f"goal-battle-{battle_id}-tau-public-only",
            "goal_version": 1,
            "immutable_goal_preserved": True,
        },
        "rationale": (
            "Battle requested one bounded Tau/Scillm public-only handoff receipt before "
            "Judge replay."
        ),
        "result": {
            "status": materialized["status"],
            "live": True,
            "mocked": False,
            "model": model,
            "surface": "scillm.chat_completions",
            "summary": _result_summary(team, materialized),
            "artifacts": [str(scillm_call_path), str(materialized_path)],
            "commands_run": [],
        },
        "evidence": [str(handoff_path), str(scillm_call_path), str(materialized_path)],
        "next": {
            "subagent": "battle-scorekeeper",
            "executor": "local",
            "reason": (
                "Battle Judge may consume materialized paths only when materialization "
                "status is PASS."
            ),
        },
        "stop_condition": (
            "Battle Judge consumes Red/Blue materialized artifacts or records "
            "INSUFFICIENT_EVIDENCE."
        ),
    }


def _validation_receipt(
    *,
    battle_id: str,
    run_id: str,
    team: str,
    worker_id: str,
    handoff_path: Path,
    scillm_call_path: Path,
    subagent_receipt_path: Path,
    materialized: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "tau.battle_handoff_validation.v1",
        "battle_id": battle_id,
        "run_id": run_id,
        "team": team,
        "worker_id": worker_id,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "checks": {
            "handoff_written": handoff_path.exists(),
            "scillm_call_receipt_written": scillm_call_path.exists(),
            "subagent_receipt_written": subagent_receipt_path.exists(),
            "materialization_status": materialized["status"],
        },
    }


def _result_summary(team: str, materialized: dict[str, Any]) -> str:
    if materialized["status"] == "PASS":
        return f"Battle {team} produced a materialized executable artifact."
    return (
        f"Battle {team} Scillm response was recorded but artifact materialization is "
        f"BLOCKED: {materialized.get('reason')}"
    )


def _persona_for_worker(base: str, index: int) -> str:
    if index == 0:
        return base
    return f"{base}-variant-{index}"


def _count_pass(teams: list[dict[str, Any]], team: str) -> int:
    return sum(
        1
        for item in teams
        if item.get("team") == team and item.get("materialized", {}).get("status") == "PASS"
    )


def _read_context(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"battle context JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"battle context JSON must be an object: {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
