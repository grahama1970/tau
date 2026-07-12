#!/usr/bin/env python3
"""Run the Battle blocked-to-killed acceptance canary for Tau issue #71."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

SPRITE_ATLAS = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/sprite-atlas/run.sh"
)
BATTLE_PROFILE = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/battle/profiles/"
    "pixijs-runtime-atlas-64.v1.json"
)


def run_battle_canary(*, output_dir: Path, reference: Path, model: str) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    generic_script = Path(__file__).with_name("run-generic-artifact-transaction-canary.py")
    command = [
        sys.executable,
        str(generic_script),
        "--out",
        str(output_dir),
        "--reference",
        str(reference.expanduser().resolve()),
        "--model",
        model,
        "--sequence-state-1",
        "blocked",
        "--sequence-state-2",
        "killed",
        "--approve-synthetic-continuation",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"generic live canary failed: {result.stderr[-2000:]}")
    generic_receipt = _read_json(output_dir / "canary-receipt.json")
    final_run = _read_json(output_dir / "run-receipt-after-approval.json")
    stage_1, stage_2 = final_run["nodes"]
    if stage_1["artifacts"][0]["sha256"] == stage_2["artifacts"][0]["sha256"]:
        raise RuntimeError("Battle blocked and killed accepted artifacts are identical")

    validations = []
    for state, node in (("blocked", stage_1), ("killed", stage_2)):
        validations.append(
            _validate_sequence(
                state=state,
                accepted_artifact=Path(node["artifacts"][0]["path"]),
                output_dir=output_dir,
            )
        )
    receipt = {
        "schema": "tau.battle_artifact_transaction_live_canary.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": True,
        "generic_canary_receipt": str(output_dir / "canary-receipt.json"),
        "generic_canary_receipt_sha256": _sha256(output_dir / "canary-receipt.json"),
        "immutable_character_reference": {
            "path": str(reference.expanduser().resolve()),
            "sha256": _sha256(reference.expanduser().resolve()),
        },
        "sequence_order": ["blocked", "killed"],
        "accepted_sequence_sha256s": {
            "blocked": stage_1["artifacts"][0]["sha256"],
            "killed": stage_2["artifacts"][0]["sha256"],
        },
        "sprite_atlas_validations": validations,
        "claims": {
            "proves": [
                "Tau accepted distinct provider-produced blocked and killed sequence artifacts.",
                "The killed transaction consumed only the accepted blocked projection as context.",
                "Both accepted sequence sources produced complete Battle-profile frame trees "
                "that passed the sprite-atlas validator.",
            ],
            "does_not_prove": [
                "Battle artistic or animation quality beyond this canary.",
                "Provider or reviewer semantic quality for future runs.",
                "That deterministic frame derivation preserves ideal motion semantics.",
            ],
        },
        "upstream": generic_receipt,
    }
    _write_json(output_dir / "battle-canary-receipt.json", receipt)
    return receipt


def _validate_sequence(*, state: str, accepted_artifact: Path, output_dir: Path) -> dict[str, Any]:
    battle_profile = _read_json(BATTLE_PROFILE)
    animation = next(item for item in battle_profile["animations"] if item["name"] == state)
    frame_count = int(animation["frames"])
    profile = {
        "schema": "sprite_atlas.profile.v1",
        "profile_id": f"battle-{state}-sequence-canary-v1",
        "atlas": {
            "columns": frame_count,
            "rows": 1,
            "frame_width": 64,
            "frame_height": 64,
            "margin": 0,
            "spacing": 0,
        },
        "output": battle_profile["output"],
        "anchor": battle_profile["anchor"],
        "normalization": battle_profile["normalization"],
        "animations": [{**animation, "row": 0}],
    }
    sequence_dir = output_dir / "sprite-atlas" / state
    frames_dir = sequence_dir / "frames" / state
    frames_dir.mkdir(parents=True, exist_ok=True)
    profile_path = sequence_dir / "profile.json"
    _write_json(profile_path, profile)
    with Image.open(accepted_artifact) as source:
        source_rgba = ImageOps.fit(
            source.convert("RGBA"), (54, 54), method=Image.Resampling.LANCZOS
        )
        for index in range(frame_count):
            frame = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            frame.alpha_composite(source_rgba, (1 + index, 5))
            frame.save(frames_dir / f"{index:03d}.png")
    result = subprocess.run(
        [
            str(SPRITE_ATLAS),
            "validate-frames",
            "--frames-dir",
            str(sequence_dir / "frames"),
            "--profile",
            str(profile_path),
            "--job-dir",
            str(sequence_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    validation_path = sequence_dir / "frame-validation-index.json"
    if result.returncode != 0 or not validation_path.is_file():
        raise RuntimeError(f"sprite-atlas validation failed for {state}: {result.stdout}")
    validation = _read_json(validation_path)
    if validation.get("passed") is not True:
        raise RuntimeError(f"sprite-atlas rejected {state}: {validation}")
    return {
        "state": state,
        "source_path": str(accepted_artifact),
        "source_sha256": _sha256(accepted_artifact),
        "profile_path": str(profile_path),
        "profile_sha256": _sha256(profile_path),
        "frame_count": frame_count,
        "validation_path": str(validation_path),
        "validation_sha256": _sha256(validation_path),
        "passed": True,
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path(
            "/home/graham/workspace/experiments/agent-skills/skills/battle/assets/"
            "sprites/pixijs/crimson_hornbreaker.png"
        ),
    )
    parser.add_argument("--model", default="gpt-5.5")
    args = parser.parse_args()
    print(
        json.dumps(
            run_battle_canary(output_dir=args.out, reference=args.reference, model=args.model),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
