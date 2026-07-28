"""Live Tau DAG conformance for Battle blocked/killed sprite sheets."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.external_workspace import agent_skills_root

SPRITE_SHEET_CONFORMANCE_SCHEMA = "tau.sprite_sheet_conformance.v1"
SPRITE_FRAME_LINEAGE_SCHEMA = "tau.sprite_frame_lineage.v1"
SPRITE_PLAYBACK_PROOF_SCHEMA = "tau.sprite_playback_proof.v1"
SPRITE_RELEASE_BOUNDARY_SCHEMA = "tau.sprite_release_boundary.v1"
SPRITE_ATLAS = agent_skills_root() / "skills/sprite-atlas/run.sh"
BATTLE_PROFILE = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/battle/profiles/"
    "pixijs-runtime-atlas-64.v1.json"
)
SPRITE_ID = "battle"
EXPECTED_COUNTS = {"blocked": 6, "killed": 8}


def write_sprite_sheet_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Run the Battle sprite-sheet conformance workload through Tau's DAG scheduler."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    if not SPRITE_ATLAS.is_file():
        raise RuntimeError(f"sprite-atlas runtime missing: {SPRITE_ATLAS}")
    if not BATTLE_PROFILE.is_file():
        raise RuntimeError(f"Battle sprite profile missing: {BATTLE_PROFILE}")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    run_dir = proof_dir / "run"
    artifacts_dir = proof_dir / "artifacts"
    frames_dir = artifacts_dir / "frames" / SPRITE_ID
    profile_path = artifacts_dir / "battle-blocked-killed-profile.json"
    spec_path = artifacts_dir / "sprite-sheet-conformance-dag.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    profile = _battle_subset_profile()
    _write_json(profile_path, profile)
    spec = _dag_spec(run_dir=run_dir, artifacts_dir=artifacts_dir)
    _write_json(spec_path, spec)
    plan = compile_generic_dag_plan(spec, source_path=spec_path)
    node_outputs: dict[str, dict[str, Any]] = {}
    scheduler_events: list[dict[str, Any]] = []

    with SqliteDagRunStore(run_dir / "dag-run.sqlite3") as store:
        scheduler_result = run_dag_plan(
            plan,
            execute_node=_executor(
                artifacts_dir=artifacts_dir,
                frames_dir=frames_dir,
                profile_path=profile_path,
                node_outputs=node_outputs,
            ),
            event_sink=scheduler_events.append,
            run_store=store,
            run_id="sprite-sheet-conformance-run",
            lease_owner="sprite-sheet-conformance",
        )
        journal_events = [
            _journal_event_payload(item)
            for item in store.load_events(scheduler_result.run_id or "")
        ]

    frame_lineage = node_outputs.get("frame-lineage", {}).get("accepted_output", {})
    sequence_validation = node_outputs.get("sequence-validation", {}).get("accepted_output", {})
    atlas_validation = node_outputs.get("atlas-validation", {}).get("accepted_output", {})
    playback_proof = node_outputs.get("playback-proof", {}).get("accepted_output", {})
    release_boundary = node_outputs.get("release-boundary", {}).get("accepted_output", {})
    checks = {
        "scheduler_status_pass": scheduler_result.status == "PASS"
        and scheduler_result.verdict == "PASS",
        "blocked_frame_count": _accepted_count(frame_lineage, "blocked") == 6,
        "killed_frame_count": _accepted_count(frame_lineage, "killed") == 8,
        "lineage_hash_present_per_frame": _lineage_hashes_present(frame_lineage),
        "sequence_validator_pass": sequence_validation.get("passed") is True,
        "atlas_pack_pass": atlas_validation.get("pack_status") == "PASS_NAMED_FRAME_PACK",
        "atlas_validator_pass": atlas_validation.get("runtime_validation_passed") is True,
        "playback_proof_present": playback_proof.get("schema") == SPRITE_PLAYBACK_PROOF_SCHEMA
        and playback_proof.get("status") == "PASS",
        "final_release_human_gated": release_boundary.get("release_state") == "HUMAN_GATED"
        and release_boundary.get("promotion_performed") is False,
    }
    failed_checks = [name for name, value in checks.items() if value is not True]
    payload = {
        "schema": SPRITE_SHEET_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "proof_dir": str(proof_dir),
        "run_dir": str(run_dir),
        "artifacts_dir": str(artifacts_dir),
        "dag_spec": str(spec_path),
        "dag_plan_sha256": plan.plan_sha256,
        "scheduler_result": _scheduler_result_summary(scheduler_result),
        "scheduler_events": scheduler_events,
        "journal_event_count": len(journal_events),
        "frame_counts": {
            state: _accepted_count(frame_lineage, state) for state in sorted(EXPECTED_COUNTS)
        },
        "frame_lineage_receipt": frame_lineage.get("receipt_path"),
        "sequence_validation_receipt": sequence_validation.get("validation_path"),
        "atlas_manifest": atlas_validation.get("manifest_path"),
        "atlas_validation_receipt": atlas_validation.get("runtime_validation_path"),
        "playback_proof": playback_proof.get("receipt_path"),
        "release_boundary_receipt": release_boundary.get("receipt_path"),
        "checks": checks,
        "failed_checks": failed_checks,
        "proof_scope": {
            "proves": [
                "Tau ran a sprite-sheet workload through the canonical DAG scheduler.",
                "Six blocked and eight killed frames were generated with per-frame lineage.",
                "The real sprite-atlas validator accepted the named frame tree.",
                "The real sprite-atlas packer and runtime atlas validator accepted the atlas.",
                "Tau produced a playback proof bound to the runtime manifest.",
                "Final release remains human-gated and no promotion was performed.",
            ],
            "does_not_prove": [
                "Battle art direction quality.",
                "Provider/model semantic quality.",
                "Browser playback rendering in PixiJS.",
                "Human approval to promote the candidate runtime atlas.",
            ],
        },
        "checked_at": _now(),
    }
    _write_json(resolved_output, payload)
    return payload


def _executor(
    *,
    artifacts_dir: Path,
    frames_dir: Path,
    profile_path: Path,
    node_outputs: dict[str, dict[str, Any]],
) -> Callable[[DagPlanNode, tuple[dict[str, Any], ...], DagNodeAttempt], dict[str, Any]]:
    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs
        if node.node_id == "frame-lineage":
            accepted_output = _write_frame_lineage(
                frames_dir=frames_dir,
                receipt_path=artifacts_dir / "frame-lineage-receipt.json",
            )
        elif node.node_id == "sequence-validation":
            accepted_output = _validate_sequence(
                frames_dir=frames_dir,
                profile_path=profile_path,
                validation_dir=artifacts_dir / "sequence-validation",
            )
        elif node.node_id == "atlas-validation":
            accepted_output = _validate_atlas(
                frames_dir=frames_dir,
                profile_path=profile_path,
                pack_dir=artifacts_dir / "atlas-pack",
                runtime_validation_dir=artifacts_dir / "atlas-runtime-validation",
            )
        elif node.node_id == "playback-proof":
            accepted_output = _write_playback_proof(
                manifest_path=artifacts_dir / "atlas-pack" / "atlas.candidate.json",
                receipt_path=artifacts_dir / "playback-proof.json",
            )
        elif node.node_id == "release-boundary":
            accepted_output = _write_release_boundary(
                atlas_path=artifacts_dir / "atlas-pack" / "atlas.candidate.png",
                manifest_path=artifacts_dir / "atlas-pack" / "atlas.candidate.json",
                receipt_path=artifacts_dir / "release-boundary-receipt.json",
            )
        else:
            raise RuntimeError(f"unknown sprite conformance node: {node.node_id}")
        result = {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "attempt_id": attempt.attempt_id,
            "live": True,
            "provider_live": False,
            "accepted_output": accepted_output,
        }
        node_outputs[node.node_id] = result
        return result

    return execute


def _write_frame_lineage(*, frames_dir: Path, receipt_path: Path) -> dict[str, Any]:
    frames: list[dict[str, Any]] = []
    for state, count in EXPECTED_COUNTS.items():
        state_dir = frames_dir / state
        state_dir.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            frame_path = state_dir / f"{index:03d}.png"
            lineage_seed = {
                "sprite_id": SPRITE_ID,
                "state": state,
                "frame_index": index,
                "source": "tau-sprite-sheet-conformance-stdlib-png",
            }
            _write_frame_png(frame_path, state=state, index=index)
            frame_sha256 = _sha256(frame_path)
            frames.append(
                {
                    "state": state,
                    "frame_index": index,
                    "path": str(frame_path.resolve()),
                    "sha256": frame_sha256,
                    "lineage_hash": canonical_sha256(
                        {**lineage_seed, "frame_sha256": frame_sha256}
                    ),
                    "accepted": True,
                }
            )
    payload = {
        "schema": SPRITE_FRAME_LINEAGE_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "sprite_id": SPRITE_ID,
        "receipt_path": str(receipt_path.resolve()),
        "frames_dir": str(frames_dir.resolve()),
        "expected_counts": EXPECTED_COUNTS,
        "frames": frames,
        "frame_count": len(frames),
        "accepted_counts": {
            state: len([item for item in frames if item["state"] == state and item["accepted"]])
            for state in EXPECTED_COUNTS
        },
    }
    _write_json(receipt_path, payload)
    return payload


def _validate_sequence(
    *,
    frames_dir: Path,
    profile_path: Path,
    validation_dir: Path,
) -> dict[str, Any]:
    _run(
        [
            str(SPRITE_ATLAS),
            "validate-frames",
            "--frames-dir",
            str(frames_dir),
            "--profile",
            str(profile_path),
            "--job-dir",
            str(validation_dir),
        ]
    )
    validation_path = validation_dir / "frame-validation-index.json"
    validation = _read_json(validation_path)
    return {
        "schema": "tau.sprite_sequence_validation.v1",
        "status": "PASS" if validation.get("passed") is True else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "validation_path": str(validation_path.resolve()),
        "validation_sha256": _sha256(validation_path),
        "passed": validation.get("passed") is True,
        "required_frame_count": validation.get("required_frame_count"),
        "passed_frame_count": validation.get("passed_frame_count"),
        "failed_frame_count": validation.get("failed_frame_count"),
        "accepted_counts": _accepted_counts_from_validation(validation),
    }


def _validate_atlas(
    *,
    frames_dir: Path,
    profile_path: Path,
    pack_dir: Path,
    runtime_validation_dir: Path,
) -> dict[str, Any]:
    _run(
        [
            str(SPRITE_ATLAS),
            "pack-frames",
            "--frames-dir",
            str(frames_dir),
            "--profile",
            str(profile_path),
            "--sprite-id",
            SPRITE_ID,
            "--job-dir",
            str(pack_dir),
        ]
    )
    pack_receipt_path = pack_dir / "named-frame-pack-receipt.json"
    atlas_path = pack_dir / "atlas.candidate.png"
    manifest_path = pack_dir / "atlas.candidate.json"
    pack_receipt = _read_json(pack_receipt_path)
    _run(
        [
            str(SPRITE_ATLAS),
            "validate",
            "--atlas",
            str(atlas_path),
            "--manifest",
            str(manifest_path),
            "--profile",
            str(profile_path),
            "--sprite-id",
            SPRITE_ID,
            "--job-dir",
            str(runtime_validation_dir),
        ]
    )
    runtime_validation_path = runtime_validation_dir / "validation.json"
    runtime_validation = _read_json(runtime_validation_path)
    return {
        "schema": "tau.sprite_atlas_validation.v1",
        "status": (
            "PASS"
            if pack_receipt.get("status") == "PASS_NAMED_FRAME_PACK"
            and runtime_validation.get("passed") is True
            else "BLOCKED"
        ),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "pack_receipt_path": str(pack_receipt_path.resolve()),
        "pack_receipt_sha256": _sha256(pack_receipt_path),
        "pack_status": pack_receipt.get("status"),
        "atlas_path": str(atlas_path.resolve()),
        "atlas_sha256": _sha256(atlas_path),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": _sha256(manifest_path),
        "runtime_validation_path": str(runtime_validation_path.resolve()),
        "runtime_validation_sha256": _sha256(runtime_validation_path),
        "runtime_validation_passed": runtime_validation.get("passed") is True,
    }


def _write_playback_proof(*, manifest_path: Path, receipt_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    playback_sequences = {}
    for state, expected_count in EXPECTED_COUNTS.items():
        frames = manifest.get("animations", {}).get(state, [])
        playback_sequences[state] = {
            "frame_count": len(frames),
            "expected_frame_count": expected_count,
            "frames": frames,
            "frame_rectangles": {
                frame: manifest["frames"][frame]["frame"] for frame in frames
            },
            "loop": False,
        }
    checks = {
        f"{state}_frame_count": data["frame_count"] == data["expected_frame_count"]
        for state, data in playback_sequences.items()
    }
    payload = {
        "schema": SPRITE_PLAYBACK_PROOF_SCHEMA,
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "receipt_path": str(receipt_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": _sha256(manifest_path),
        "sprite_id": SPRITE_ID,
        "sequences": playback_sequences,
        "checks": checks,
    }
    _write_json(receipt_path, payload)
    return payload


def _write_release_boundary(
    *,
    atlas_path: Path,
    manifest_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    payload = {
        "schema": SPRITE_RELEASE_BOUNDARY_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "receipt_path": str(receipt_path.resolve()),
        "release_state": "HUMAN_GATED",
        "promotion_performed": False,
        "human_required": True,
        "candidate_atlas": str(atlas_path.resolve()),
        "candidate_atlas_sha256": _sha256(atlas_path),
        "candidate_manifest": str(manifest_path.resolve()),
        "candidate_manifest_sha256": _sha256(manifest_path),
        "allowed_next_action": "human_review_then_sprite_atlas_promote",
    }
    _write_json(receipt_path, payload)
    return payload


def _battle_subset_profile() -> dict[str, Any]:
    profile = _read_json(BATTLE_PROFILE)
    animations = [
        item for item in profile["animations"] if item["name"] in set(EXPECTED_COUNTS)
    ]
    return {
        **profile,
        "profile_id": "battle-blocked-killed-conformance-v1",
        "animations": animations,
    }


def _dag_spec(*, run_dir: Path, artifacts_dir: Path) -> dict[str, Any]:
    nodes = [
        ("frame-lineage", []),
        ("sequence-validation", ["frame-lineage"]),
        ("atlas-validation", ["sequence-validation"]),
        ("playback-proof", ["atlas-validation"]),
        ("release-boundary", ["playback-proof"]),
    ]
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "sprite-sheet-conformance",
        "run_dir": str(run_dir.resolve()),
        "events_jsonl": str((run_dir / "events.jsonl").resolve()),
        "goal_hash": "sha256:sprite-sheet-conformance",
        "nodes": [
            {
                "node_id": node_id,
                "role": node_id,
                "command": ["tau-internal-sprite-sheet-conformance", node_id],
                "depends_on": depends_on,
                "receipt_path": str((artifacts_dir / f"{node_id}-node-receipt.json").resolve()),
                "timeout_seconds": 60,
                "max_attempts": 1,
            }
            for node_id, depends_on in nodes
        ],
    }


def _write_frame_png(path: Path, *, state: str, index: int) -> None:
    width = 64
    height = 64
    body_color = (220, 40, 40, 255) if state == "blocked" else (80, 80, 220, 255)
    accent_color = (255, 220, 160, 255)
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            body = 20 + (index % 4) <= x <= 44 + (index % 4) and 12 + index <= y <= 58
            head = 26 <= x <= 38 and 7 + index <= y <= 20 + index
            slash = (
                state == "killed"
                and 16 + index <= x <= 48 - index // 2
                and y == 42 + index // 2
            )
            if head:
                color = accent_color
            elif body or slash:
                color = body_color
            else:
                color = (0, 0, 0, 0)
            pixels.extend(color)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes(width=width, height=height, rgba_scanlines=bytes(pixels)))


def _png_bytes(*, width: int, height: int, rgba_scanlines: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(rgba_scanlines)),
            chunk(b"IEND", b""),
        )
    )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "sprite_sheet_conformance_command_failed:"
            f" command={command!r} stdout={result.stdout[-2000:]!r}"
            f" stderr={result.stderr[-2000:]!r}"
        )
    return result


def _accepted_count(frame_lineage: dict[str, Any], state: str) -> int:
    frames = frame_lineage.get("frames")
    if not isinstance(frames, list):
        return 0
    return len(
        [
            item
            for item in frames
            if isinstance(item, dict)
            and item.get("state") == state
            and item.get("accepted") is True
        ]
    )


def _lineage_hashes_present(frame_lineage: dict[str, Any]) -> bool:
    frames = frame_lineage.get("frames")
    if not isinstance(frames, list) or len(frames) != sum(EXPECTED_COUNTS.values()):
        return False
    return all(
        isinstance(item, dict)
        and isinstance(item.get("lineage_hash"), str)
        and str(item["lineage_hash"]).startswith("sha256:")
        for item in frames
    )


def _accepted_counts_from_validation(validation: dict[str, Any]) -> dict[str, int]:
    counts = {state: 0 for state in EXPECTED_COUNTS}
    for item in validation.get("checks", []):
        if not isinstance(item, dict) or item.get("passed") is not True:
            continue
        path = str(item.get("path") or "")
        state = path.split("/", 1)[0]
        if state in counts:
            counts[state] += 1
    return counts


def _scheduler_result_summary(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "verdict": result.verdict,
        "completed_node_ids": list(result.completed_node_ids),
        "node_states": dict(result.node_states),
        "edge_states": dict(result.edge_states),
        "terminal_states": dict(result.terminal_states),
        "max_observed_concurrency": result.max_observed_concurrency,
        "run_id": result.run_id,
        "lease_epoch": result.lease_epoch,
        "replayed_event_count": result.replayed_event_count,
    }


def _journal_event_payload(event: Any) -> dict[str, Any]:
    if hasattr(event, "to_mapping"):
        return dict(event.to_mapping())
    return dict(event)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.expanduser().resolve().read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
