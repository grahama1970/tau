"""Battle worker handoff spec extraction for Tau live handoffs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_handoff_specs(
    *,
    teams: tuple[tuple[str, str], ...],
    battle_context: dict[str, Any] | None,
    handoff_granularity: str,
) -> list[dict[str, Any]]:
    """Build team-level or worker-level Tau handoff specs from Battle receipts."""

    if handoff_granularity == "team":
        return [
            {"team": team, "persona": persona, "worker_context": None}
            for team, persona in teams
        ]
    if handoff_granularity != "worker":
        raise ValueError("handoff_granularity must be 'team' or 'worker'")
    if battle_context is None:
        raise ValueError("worker handoff granularity requires --battle-context-json")

    run_root = _battle_run_root(battle_context)
    specs: list[dict[str, Any]] = []
    for team, fallback_persona in teams:
        team_receipt_path = run_root / team / "team-receipt.json"
        team_receipt = _read_json(team_receipt_path) if team_receipt_path.exists() else {}
        worker_refs = team_receipt.get("worker_receipts") if isinstance(team_receipt, dict) else None
        if not isinstance(worker_refs, list) or not worker_refs:
            raise ValueError(f"worker handoff granularity requires {team} worker_receipts")
        for worker_ref in worker_refs:
            if not isinstance(worker_ref, str) or not worker_ref:
                continue
            worker_path = _resolve_battle_artifact(run_root, worker_ref)
            worker_receipt = _read_json(worker_path)
            if not isinstance(worker_receipt, dict):
                raise ValueError(f"worker receipt must be an object: {worker_path}")
            specs.append(
                {
                    "team": team,
                    "persona": str(worker_receipt.get("persona") or fallback_persona),
                    "worker_context": _worker_context(
                        worker_receipt=worker_receipt,
                        worker_path=worker_path,
                        team_receipt_path=team_receipt_path,
                        run_root=run_root,
                    ),
                }
            )
    return specs


def _battle_run_root(battle_context: dict[str, Any]) -> Path:
    path = battle_context.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("battle context has no source path")
    context_path = Path(path)
    if context_path.parent.name == "context":
        return context_path.parent.parent
    return context_path.parent


def _resolve_battle_artifact(run_root: Path, artifact_ref: str) -> Path:
    path = Path(artifact_ref)
    return path if path.is_absolute() else run_root / path


def _worker_context(
    *,
    worker_receipt: dict[str, Any],
    worker_path: Path,
    team_receipt_path: Path,
    run_root: Path,
) -> dict[str, Any]:
    combination_id = worker_receipt.get("combination_id")
    attempt_path = None
    if isinstance(combination_id, str) and combination_id:
        candidate = run_root / "scorekeeper" / "replays" / combination_id / "attempt-receipt.json"
        if candidate.exists():
            attempt_path = candidate
    artifacts = [str(worker_path), str(team_receipt_path)]
    if attempt_path is not None:
        artifacts.append(str(attempt_path))
    return {
        "worker_id": worker_receipt.get("worker_id"),
        "combination_id": combination_id,
        "team": worker_receipt.get("team"),
        "persona": worker_receipt.get("persona"),
        "research_dispatch": worker_receipt.get("research_dispatch"),
        "model": worker_receipt.get("model"),
        "surface": worker_receipt.get("surface"),
        "status": worker_receipt.get("status"),
        "source_worker_receipt": str(worker_path),
        "source_team_receipt": str(team_receipt_path),
        "source_attempt_receipt": str(attempt_path) if attempt_path is not None else None,
        "source_artifacts": _unique_strings(artifacts),
        "outcome": worker_receipt.get("outcome"),
        "exploit": worker_receipt.get("exploit"),
        "defense": worker_receipt.get("defense"),
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _unique_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique
