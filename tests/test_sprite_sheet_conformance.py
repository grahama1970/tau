from __future__ import annotations

import json
from pathlib import Path

from tau_coding import sprite_sheet_conformance as sprite


def test_frame_lineage_writes_expected_pngs_and_hashes(tmp_path: Path) -> None:
    receipt = sprite._write_frame_lineage(
        frames_dir=tmp_path / "frames",
        receipt_path=tmp_path / "frame-lineage-receipt.json",
    )

    assert receipt["schema"] == sprite.SPRITE_FRAME_LINEAGE_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["frame_count"] == 14
    assert receipt["accepted_counts"] == {"blocked": 6, "killed": 8}
    assert sprite._accepted_count(receipt, "blocked") == 6
    assert sprite._accepted_count(receipt, "killed") == 8
    assert sprite._lineage_hashes_present(receipt) is True
    first_frame = Path(receipt["frames"][0]["path"])
    assert first_frame.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert Path(receipt["receipt_path"]).is_file()


def test_playback_proof_passes_and_blocks_from_manifest_counts(tmp_path: Path) -> None:
    manifest_path = tmp_path / "atlas.candidate.json"
    manifest = {
        "animations": {
            "blocked": [f"battle_blocked_{index}" for index in range(6)],
            "killed": [f"battle_killed_{index}" for index in range(8)],
        },
        "frames": {
            **{
                f"battle_blocked_{index}": {"frame": {"x": index, "y": 0, "w": 64, "h": 64}}
                for index in range(6)
            },
            **{
                f"battle_killed_{index}": {"frame": {"x": index, "y": 64, "w": 64, "h": 64}}
                for index in range(8)
            },
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    passed = sprite._write_playback_proof(
        manifest_path=manifest_path,
        receipt_path=tmp_path / "playback-proof.json",
    )

    assert passed["schema"] == sprite.SPRITE_PLAYBACK_PROOF_SCHEMA
    assert passed["status"] == "PASS"
    assert passed["checks"] == {"blocked_frame_count": True, "killed_frame_count": True}
    assert passed["sequences"]["blocked"]["frame_count"] == 6
    assert passed["sequences"]["killed"]["frame_count"] == 8

    manifest["animations"]["killed"].pop()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    blocked = sprite._write_playback_proof(
        manifest_path=manifest_path,
        receipt_path=tmp_path / "playback-proof-blocked.json",
    )

    assert blocked["status"] == "BLOCKED"
    assert blocked["checks"]["killed_frame_count"] is False


def test_release_boundary_is_human_gated_and_non_promoting(tmp_path: Path) -> None:
    atlas = tmp_path / "atlas.candidate.png"
    manifest = tmp_path / "atlas.candidate.json"
    atlas.write_bytes(b"atlas")
    manifest.write_text("{}", encoding="utf-8")

    receipt = sprite._write_release_boundary(
        atlas_path=atlas,
        manifest_path=manifest,
        receipt_path=tmp_path / "release-boundary-receipt.json",
    )

    assert receipt["schema"] == sprite.SPRITE_RELEASE_BOUNDARY_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["release_state"] == "HUMAN_GATED"
    assert receipt["promotion_performed"] is False
    assert receipt["human_required"] is True


def test_dag_spec_has_canonical_sprite_conformance_order(tmp_path: Path) -> None:
    spec = sprite._dag_spec(run_dir=tmp_path / "run", artifacts_dir=tmp_path / "artifacts")

    nodes = {node["node_id"]: node for node in spec["nodes"]}
    assert list(nodes) == [
        "frame-lineage",
        "sequence-validation",
        "atlas-validation",
        "playback-proof",
        "release-boundary",
    ]
    assert nodes["frame-lineage"]["depends_on"] == []
    assert nodes["sequence-validation"]["depends_on"] == ["frame-lineage"]
    assert nodes["atlas-validation"]["depends_on"] == ["sequence-validation"]
    assert nodes["playback-proof"]["depends_on"] == ["atlas-validation"]
    assert nodes["release-boundary"]["depends_on"] == ["playback-proof"]


def test_validation_counts_only_passed_expected_states() -> None:
    validation = {
        "checks": [
            {"path": "blocked/000.png", "passed": True},
            {"path": "blocked/001.png", "passed": False},
            {"path": "killed/000.png", "passed": True},
            {"path": "idle/000.png", "passed": True},
            {"path": "killed/001.png", "passed": True},
        ]
    }

    assert sprite._accepted_counts_from_validation(validation) == {
        "blocked": 1,
        "killed": 2,
    }
