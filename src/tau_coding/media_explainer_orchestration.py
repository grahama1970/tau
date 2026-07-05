"""Async linked-asset media explanation orchestration proof for Tau."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MEDIA_EXPLAINER_WORK_ITEM_SCHEMA = "tau.media_explainer_work_item.v1"
MEDIA_EXPLAINER_ASSET_RECEIPT_SCHEMA = "tau.media_explainer_asset_receipt.v1"
MEDIA_EXPLAINER_RUN_RECEIPT_SCHEMA = "tau.media_explainer_run_receipt.v1"
MEDIA_EXPLAINER_INSPECT_SCHEMA = "tau.media_explainer_inspect.v1"

_TOOL_ROUTES = {
    "image": {
        "subagent": "image-media-explainer",
        "tool_path": "vlm_description",
        "backend": "deterministic_vlm_contract_stub",
    },
    "video": {
        "subagent": "video-media-explainer",
        "tool_path": "watch_keyframe_description",
        "backend": "deterministic_watch_contract_stub",
    },
    "audio": {
        "subagent": "audio-media-explainer",
        "tool_path": "audio_caption_service",
        "backend": "deterministic_audio_caption_contract_stub",
    },
    "text": {
        "subagent": "text-media-explainer",
        "tool_path": "text_summarizer",
        "backend": "deterministic_text_summary_contract_stub",
    },
}


def run_media_explainer_smoke(
    *,
    run_root: Path,
    label: str = "tau-media-explainer-smoke",
    work_item: Path | None = None,
) -> dict[str, Any]:
    """Run a deterministic async mixed-media orchestration smoke."""

    run_root = run_root.expanduser().resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{_compact_stamp()}-{_slug(label)}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir = run_dir / "asset-receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"
    resolved_work_item_path = run_dir / "work-item.json"
    source_work_item = _load_work_item(work_item) if work_item else _default_work_item(run_id)
    work_item_payload = _normalize_work_item(source_work_item, run_id=run_id)
    _write_json(resolved_work_item_path, work_item_payload)
    _append_event(
        events_path,
        "work_item_accepted",
        {
            "run_id": run_id,
            "asset_count": len(work_item_payload["assets"]),
            "phase": work_item_payload["phase"],
        },
    )

    asset_receipts = asyncio.run(
        _run_assets(
            run_id=run_id,
            assets=work_item_payload["assets"],
            receipts_dir=receipts_dir,
            events_path=events_path,
        )
    )
    completion_order = [receipt["asset_id"] for receipt in asset_receipts]
    by_asset_id = {receipt["asset_id"]: receipt for receipt in asset_receipts}
    manifest_order = [asset["asset_id"] for asset in work_item_payload["assets"]]
    step02_gate = _step02_gate(work_item_payload["assets"], by_asset_id)
    emitted_receipts = sorted(str(path) for path in receipts_dir.glob("*.json"))
    receipt = {
        "schema": MEDIA_EXPLAINER_RUN_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": True,
        "live": False,
        "provider_live": False,
        "execution": "local_deterministic_asyncio_as_completed_contract",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "work_item": str(resolved_work_item_path),
        "events_jsonl": str(events_path),
        "asset_count": len(asset_receipts),
        "manifest_order": manifest_order,
        "completion_order": completion_order,
        "completion_order_differs_from_manifest": completion_order != manifest_order,
        "asset_receipts": asset_receipts,
        "asset_receipt_paths": emitted_receipts,
        "status_counts": _status_counts(asset_receipts),
        "step02_gate": step02_gate,
        "memory_policy": {
            "mocked_descriptions_persisted_as_live_truth": False,
            "live_memory_write_enabled": False,
            "persistence_mode": "placeholder_receipts_only",
        },
        "proof_scope": {
            "proves": [
                "Tau accepts a media-explainer work item with linked mixed assets",
                "Tau routes image, video, audio, and text assets to distinct tool paths",
                "Tau schedules per-asset explanation work concurrently and records "
                "as_completed order",
                "Tau emits one receipt per asset with ready or failed status",
                "Tau isolates optional asset failures without blocking Step 02",
                "Tau computes a Step 02 Story gate from required linked asset readiness",
                "Tau does not persist mocked media descriptions as live memory truth",
            ],
            "does_not_prove": [
                "live VLM/video/audio/text model quality",
                "live Memory service writes",
                "Dream UI integration",
                "persona memory schema migration",
                "media-explainer internals",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "run-receipt.json", receipt)
    return receipt


def inspect_media_explainer_run(run_dir: Path) -> dict[str, Any]:
    """Return a compact summary for a media-explainer smoke run."""

    resolved = run_dir.expanduser().resolve()
    receipt = _read_json_object(resolved / "run-receipt.json", label="run receipt")
    events_path = Path(str(receipt["events_jsonl"]))
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "schema": MEDIA_EXPLAINER_INSPECT_SCHEMA,
        "ok": receipt.get("ok") is True,
        "status": receipt.get("status"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "provider_live": receipt.get("provider_live"),
        "execution": receipt.get("execution"),
        "run_id": receipt.get("run_id"),
        "run_dir": str(resolved),
        "asset_count": receipt.get("asset_count"),
        "completion_order": receipt.get("completion_order"),
        "manifest_order": receipt.get("manifest_order"),
        "completion_order_differs_from_manifest": receipt.get(
            "completion_order_differs_from_manifest"
        ),
        "status_counts": receipt.get("status_counts"),
        "step02_gate": receipt.get("step02_gate"),
        "memory_policy": receipt.get("memory_policy"),
        "events_count": len(events),
        "proof_scope": receipt.get("proof_scope"),
    }


async def _run_assets(
    *,
    run_id: str,
    assets: list[dict[str, Any]],
    receipts_dir: Path,
    events_path: Path,
) -> list[dict[str, Any]]:
    tasks = [
        asyncio.create_task(
            _dispatch_asset(
                run_id=run_id,
                asset=asset,
                receipt_path=receipts_dir / f"{asset['asset_id']}.json",
                events_path=events_path,
            )
        )
        for asset in assets
    ]
    receipts: list[dict[str, Any]] = []
    for completed in asyncio.as_completed(tasks):
        receipt = await completed
        receipts.append(receipt)
        _append_event(
            events_path,
            "asset_completed",
            {
                "run_id": run_id,
                "asset_id": receipt["asset_id"],
                "media_type": receipt["media_type"],
                "status": receipt["status"],
            },
        )
    return receipts


async def _dispatch_asset(
    *,
    run_id: str,
    asset: dict[str, Any],
    receipt_path: Path,
    events_path: Path,
) -> dict[str, Any]:
    media_type = str(asset["media_type"])
    route = _TOOL_ROUTES[media_type]
    delay_ms = int(asset.get("delay_ms", 0))
    _append_event(
        events_path,
        "asset_dispatched",
        {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "media_type": media_type,
            "subagent": route["subagent"],
            "tool_path": route["tool_path"],
        },
    )
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)
    should_fail = bool(asset.get("simulate_failure", False))
    status = "FAILED" if should_fail else "READY"
    description = None if should_fail else _description(asset)
    errors = [str(asset.get("failure_reason") or "simulated asset failure")] if should_fail else []
    receipt = {
        "schema": MEDIA_EXPLAINER_ASSET_RECEIPT_SCHEMA,
        "run_id": run_id,
        "asset_id": asset["asset_id"],
        "media_type": media_type,
        "source_uri": asset.get("source_uri"),
        "filepath": asset.get("filepath"),
        "required": bool(asset.get("required", True)),
        "status": status,
        "description": description,
        "tags": [] if should_fail else [media_type, "prompt-ready", "deterministic-contract"],
        "subagent": route["subagent"],
        "tool_path": route["tool_path"],
        "model_backend": route["backend"],
        "mocked": True,
        "live": False,
        "memory_persistence": {
            "status": "SKIPPED_PLACEHOLDER",
            "receipt": None,
            "mocked_description_not_persisted_as_live_truth": True,
        },
        "evidence": {
            "receipt_path": str(receipt_path),
            "delay_ms": delay_ms,
        },
        "errors": errors,
        "timestamp": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    return receipt


def _normalize_work_item(payload: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    if payload.get("schema") != MEDIA_EXPLAINER_WORK_ITEM_SCHEMA:
        raise RuntimeError(f"work item schema must be {MEDIA_EXPLAINER_WORK_ITEM_SCHEMA}")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("work item assets must be a non-empty list")
    normalized_assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, asset in enumerate(assets, start=1):
        if not isinstance(asset, dict):
            raise RuntimeError("each asset must be an object")
        asset_id = str(asset.get("asset_id") or f"asset-{index:02d}")
        if asset_id in seen:
            raise RuntimeError(f"duplicate asset_id: {asset_id}")
        seen.add(asset_id)
        media_type = str(asset.get("media_type") or "")
        if media_type not in _TOOL_ROUTES:
            raise RuntimeError(f"unsupported media_type for {asset_id}: {media_type}")
        if not asset.get("source_uri") and not asset.get("filepath"):
            raise RuntimeError(f"asset {asset_id} must include source_uri or filepath")
        normalized = {
            "asset_id": asset_id,
            "media_type": media_type,
            "source_uri": asset.get("source_uri"),
            "filepath": asset.get("filepath"),
            "required": bool(asset.get("required", True)),
            "delay_ms": int(asset.get("delay_ms", 0)),
            "simulate_failure": bool(asset.get("simulate_failure", False)),
            "failure_reason": asset.get("failure_reason"),
        }
        normalized_assets.append(normalized)
    return {
        "schema": MEDIA_EXPLAINER_WORK_ITEM_SCHEMA,
        "run_id": str(payload.get("run_id") or run_id),
        "persona_id": str(payload.get("persona_id") or "persona-smoke"),
        "phase": str(payload.get("phase") or "01_idea"),
        "idea": str(payload.get("idea") or "Linked media explanation smoke"),
        "context": str(payload.get("context") or ""),
        "assets": normalized_assets,
    }


def _default_work_item(run_id: str) -> dict[str, Any]:
    return {
        "schema": MEDIA_EXPLAINER_WORK_ITEM_SCHEMA,
        "run_id": run_id,
        "persona_id": "persona-smoke",
        "phase": "01_idea",
        "idea": "Explain linked media before Step 02 Story generation.",
        "context": "Deterministic mixed-asset smoke for Tau issue #46.",
        "assets": [
            {
                "asset_id": "image-hero",
                "media_type": "image",
                "source_uri": "file:///fixtures/hero.png",
                "required": True,
                "delay_ms": 40,
            },
            {
                "asset_id": "video-loop",
                "media_type": "video",
                "source_uri": "file:///fixtures/loop.mp4",
                "required": True,
                "delay_ms": 30,
            },
            {
                "asset_id": "audio-note",
                "media_type": "audio",
                "source_uri": "file:///fixtures/note.wav",
                "required": True,
                "delay_ms": 20,
            },
            {
                "asset_id": "text-brief",
                "media_type": "text",
                "source_uri": "file:///fixtures/brief.txt",
                "required": True,
                "delay_ms": 10,
            },
            {
                "asset_id": "optional-audio-broken",
                "media_type": "audio",
                "source_uri": "file:///fixtures/missing.wav",
                "required": False,
                "delay_ms": 5,
                "simulate_failure": True,
                "failure_reason": "optional fixture intentionally unavailable",
            },
        ],
    }


def _step02_gate(
    assets: list[dict[str, Any]], receipts_by_asset_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    required_ids = [asset["asset_id"] for asset in assets if asset.get("required", True)]
    failed_required_ids = [
        asset_id
        for asset_id in required_ids
        if receipts_by_asset_id.get(asset_id, {}).get("status") != "READY"
    ]
    optional_failed_ids = [
        asset["asset_id"]
        for asset in assets
        if not asset.get("required", True)
        and receipts_by_asset_id.get(asset["asset_id"], {}).get("status") == "FAILED"
    ]
    return {
        "schema": "tau.media_explainer_step02_gate.v1",
        "phase": "02_story",
        "status": "READY" if not failed_required_ids else "BLOCKED",
        "required_asset_ids": required_ids,
        "ready_required_asset_ids": [
            asset_id
            for asset_id in required_ids
            if receipts_by_asset_id.get(asset_id, {}).get("status") == "READY"
        ],
        "failed_required_asset_ids": failed_required_ids,
        "optional_failed_asset_ids": optional_failed_ids,
        "required_assets_ready": not failed_required_ids,
        "waived_asset_ids": [],
    }


def _description(asset: dict[str, Any]) -> str:
    source = asset.get("source_uri") or asset.get("filepath")
    return (
        f"Prompt-ready {asset['media_type']} description for {asset['asset_id']} "
        f"from {source}."
    )


def _status_counts(receipts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for receipt in receipts:
        status = str(receipt.get("status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _load_work_item(path: Path) -> dict[str, Any]:
    return _read_json_object(path.expanduser().resolve(), label="work item")


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_event(path: Path, kind: str, payload: dict[str, Any]) -> None:
    event = {
        "schema": "tau.media_explainer_event.v1",
        "kind": kind,
        "timestamp": _utc_stamp(),
        **payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compact_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    cleaned = []
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
        elif char in {"-", "_", " "}:
            cleaned.append("-")
    return "-".join("".join(cleaned).split("-")) or "run"
