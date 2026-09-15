"""Deterministic fixture workspace builder for the project-memory-dream workflow.

Creates local projects with transcripts, worktrees, and recorded memory heads.
Everything is byte-stable: the same scenario produces the same digests, so the
canonical scheduler replay and the terminal receipts are deterministic.

The fake source/model adapters are the workflow's ``fixture`` backend itself;
the workspace only provides deterministic data for them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dream_nodes import _manifest_digest  # noqa: E402

RUN_CONFIG_SCHEMA = "tau.project_memory_dream_run_config.v1"


def build_project(root: Path, pid: str, *, turns: int = 2, faults: dict[str, Any] | None = None) -> None:
    project_dir = root / "projects" / pid
    (project_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    (project_dir / "worktree").mkdir(parents=True, exist_ok=True)
    project = {
        "project_id": pid,
        "git_head": f"head-{pid}-0001",
        "state_summary": {"module": pid, "maturity": "seed"},
        "faults": faults or {},
    }
    (project_dir / "project.json").write_text(
        json.dumps(project, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = []
    for turn in range(1, turns + 1):
        lines.append(
            json.dumps(
                {
                    "schema": "session.turn.v1",
                    "session_id": f"sess-{pid}-{turn}",
                    "turn": turn,
                    "text": f"{pid} working note {turn}",
                },
                sort_keys=True,
            )
        )
    (project_dir / "transcripts" / "bundle-0001.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (project_dir / "worktree" / "notes.md").write_text(f"# {pid}\n", encoding="utf-8")
    record_head(root, pid)


def corrupt_transcripts(root: Path, pid: str) -> None:
    bundle = root / "projects" / pid / "transcripts" / "bundle-0001.jsonl"
    bundle.write_text("{not json at all\n", encoding="utf-8")


def record_head(root: Path, pid: str, *, digest: str | None = None, generation: int = 1) -> str:
    """Record (or refresh) the project's active memory head.

    With ``digest=None`` the head records the project's current manifest digest,
    which makes the project look unchanged on the next dispatch decision.
    """

    head_path = root / "memory" / pid
    head_path.mkdir(parents=True, exist_ok=True)
    observed = digest or _manifest_digest(root / "projects" / pid)
    head = {
        "schema": "graph_memory.project_head.v1",
        "generation": generation,
        "digest": observed,
        "candidate_digest": None,
    }
    (head_path / "head.json").write_text(
        json.dumps(head, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return observed


def write_approval(
    root: Path,
    pid: str,
    candidate_digest: str,
    *,
    expires_at: str,
    base_head_generation: int | None = None,
) -> None:
    approval = {
        "schema": "tau.project_dream_approval.v1",
        "decision": "approved",
        "project_id": pid,
        "candidate_digest": candidate_digest,
        "expires_at": expires_at,
    }
    if base_head_generation is not None:
        approval["base_head_generation"] = base_head_generation
    (root / "projects" / pid / "approval.json").write_text(
        json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_config(
    root: Path,
    *,
    promotion_mode: str = "shadow",
    model_id: str = "fixture/deterministic",
    synthesis_backend: str = "fixture",
    projects: list[str] | None = None,
    force_reconciliation: bool = False,
    extra: dict[str, Any] | None = None,
) -> Path:
    workspace_root = str(root)
    config: dict[str, Any] = {
        "schema": RUN_CONFIG_SCHEMA,
        "run_id": "project-memory-dream-fixture",
        "workspace_root": workspace_root,
        "promotion_mode": promotion_mode,
        "model_id": model_id,
        "synthesis_backend": synthesis_backend,
        "max_projects_per_run": 8,
        "max_parallel_projects": 2,
        "max_input_chars_per_project": 20000,
        "max_topic_mutations": 3,
        "max_project_mutation_ratio": 0.5,
        "timeouts": {"node_seconds": 30, "synthesis_seconds": 120},
        "retries": {"read_only_max_attempts": 2, "synthesis_max_attempts": 1},
        "cost_budget_usd": 5.0,
        "force_reconciliation": force_reconciliation,
        "policy_profile_path": str(
            Path(__file__).resolve().parent / "policy-profile.json"
        ),
    }
    if projects is not None:
        config["projects"] = projects
    config.update(extra or {})
    path = root / "run-config.json"
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def default_workspace(root: Path, *, unchanged_pid: str = "gamma") -> list[str]:
    """Two eligible projects plus one unchanged project. Returns the project ids.

    Eligibility comes from a changed manifest versus the recorded head: alpha and
    beta get new worktree content after their heads are recorded, gamma does not.
    """

    build_project(root, "alpha")
    build_project(root, "beta")
    build_project(root, unchanged_pid)
    for pid in ("alpha", "beta"):
        notes = root / "projects" / pid / "worktree" / "notes.md"
        notes.write_text(notes.read_text(encoding="utf-8") + "\nchanged after head record\n",
                         encoding="utf-8")
    return ["alpha", "beta", unchanged_pid]
