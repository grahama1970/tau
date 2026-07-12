#!/usr/bin/env python3
"""Run a provider-live Battle sequence transaction canary."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageOps

SPRITE_ATLAS = Path("/home/graham/workspace/experiments/agent-skills/skills/sprite-atlas/run.sh")
BATTLE_PROFILE = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/battle/profiles/"
    "pixijs-runtime-atlas-64.v1.json"
)


def run_battle_canary(
    *,
    output_dir: Path,
    reference: Path,
    model: str,
    states: tuple[str, ...],
    retry_research_receipt: Path | None = None,
) -> dict[str, Any]:
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
        "--sequence-states",
        ",".join(states),
        "--sequence-frame-counts",
        ",".join(str(_frame_count(state)) for state in states),
        "--approve-synthetic-continuation",
    ]
    if retry_research_receipt is not None:
        command.extend(["--retry-research-receipt", str(retry_research_receipt.resolve())])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"generic live canary failed: {result.stderr[-2000:]}")
    generic_receipt = _read_json(output_dir / "canary-receipt.json")
    final_run = _read_json(output_dir / "run-receipt-after-approval.json")
    nodes = final_run["nodes"]
    accepted_hashes = [node["artifacts"][0]["sha256"] for node in nodes]
    if len(set(accepted_hashes)) != len(accepted_hashes):
        raise RuntimeError("Battle accepted sequence artifacts are not distinct")

    validations = []
    for state, node in zip(states, nodes, strict=True):
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
        "sequence_order": list(states),
        "accepted_sequence_sha256s": {
            state: node["artifacts"][0]["sha256"] for state, node in zip(states, nodes, strict=True)
        },
        "sprite_atlas_validations": validations,
        "claims": {
            "proves": [
                "Tau accepted distinct provider-produced sequence-sheet artifacts.",
                "Later transactions consumed only the selected accepted anchor projection.",
                "Both accepted sequence sources produced complete Battle-profile frame trees "
                "that passed the sprite-atlas validator.",
            ],
            "does_not_prove": [
                "Battle artistic or animation quality beyond this canary.",
                "Provider or reviewer semantic quality for future runs.",
                "That extracted panels have ideal motion semantics.",
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
    columns = min(frame_count, 4)
    rows = (frame_count + columns - 1) // columns
    frame_hashes: list[str] = []
    with Image.open(accepted_artifact) as source:
        source_rgba = source.convert("RGBA")
        cell_width = source_rgba.width // columns
        cell_height = source_rgba.height // rows
        for index in range(frame_count):
            column = index % columns
            row = index // columns
            panel = source_rgba.crop(
                (
                    column * cell_width,
                    row * cell_height,
                    (column + 1) * cell_width,
                    (row + 1) * cell_height,
                )
            )
            panel = _remove_uniform_border_background(panel)
            panel = ImageOps.contain(panel, (58, 58), method=Image.Resampling.LANCZOS)
            frame = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            frame.alpha_composite(panel, ((64 - panel.width) // 2, 63 - panel.height))
            frame_path = frames_dir / f"{index:03d}.png"
            frame.save(frame_path)
            frame_hashes.append(_sha256(frame_path))
    if len(set(frame_hashes)) < max(2, frame_count // 2):
        raise RuntimeError(f"provider sequence sheet lacks distinct panels for {state}")
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
        "distinct_frame_hash_count": len(set(frame_hashes)),
        "validation_path": str(validation_path),
        "validation_sha256": _sha256(validation_path),
        "passed": True,
    }


def _frame_count(state: str) -> int:
    profile = _read_json(BATTLE_PROFILE)
    return int(next(item for item in profile["animations"] if item["name"] == state)["frames"])


def _remove_uniform_border_background(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")

    def pixel_at(point: tuple[int, int]) -> tuple[int, int, int, int]:
        return cast(tuple[int, int, int, int], rgba.getpixel(point))

    border_colors = {
        pixel_at(point)[:3]
        for point in (
            *((x, 0) for x in range(rgba.width)),
            *((x, rgba.height - 1) for x in range(rgba.width)),
            *((0, y) for y in range(rgba.height)),
            *((rgba.width - 1, y) for y in range(rgba.height)),
        )
    }

    def is_background(pixel: tuple[int, int, int, int]) -> bool:
        red, green, blue, alpha = pixel
        if alpha == 0:
            return True
        saturation = max(red, green, blue) - min(red, green, blue)
        if min(red, green, blue) >= 175 and saturation <= 38:
            return True
        return any(
            abs(red - bg_red) + abs(green - bg_green) + abs(blue - bg_blue) <= 30
            for bg_red, bg_green, bg_blue in border_colors
        )

    frontier: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()
    for x in range(rgba.width):
        frontier.extend(((x, 0), (x, rgba.height - 1)))
    for y in range(rgba.height):
        frontier.extend(((0, y), (rgba.width - 1, y)))
    while frontier:
        x, y = frontier.popleft()
        if (x, y) in visited or not is_background(pixel_at((x, y))):
            continue
        visited.add((x, y))
        rgba.putpixel((x, y), (*pixel_at((x, y))[:3], 0))
        for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= neighbor[0] < rgba.width and 0 <= neighbor[1] < rgba.height:
                frontier.append(neighbor)
    bbox = rgba.getbbox()
    return rgba.crop(bbox) if bbox else rgba


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
    parser.add_argument("--states", default="blocked,killed")
    parser.add_argument("--retry-research-receipt", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run_battle_canary(
                output_dir=args.out,
                reference=args.reference,
                model=args.model,
                states=tuple(item.strip() for item in args.states.split(",") if item.strip()),
                retry_research_receipt=args.retry_research_receipt,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
