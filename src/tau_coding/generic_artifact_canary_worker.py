"""Live worker processes for the generic artifact transaction canary.

Producer mode creates real image artifacts from an immutable reference. Reviewer
mode sends the exact candidate bytes to Scillm's VLM endpoint and emits the
structured review contract consumed by Tau. Continuation mode performs only a
local marker write used to prove approval ordering.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from tau_coding.battle_scillm import (
    preflight_battle_scillm_auth,
    resolve_active_scillm_proxy_key,
)


def produce(
    *,
    stage: str,
    reference: Path,
    artifact_root: Path,
    receipt_path: Path,
    work_order_path: Path,
    counter_path: Path,
    sequence_contract: Path | None,
    producer_model: str,
    producer_timeout_seconds: float,
) -> None:
    context_path = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    context = _read_json(context_path)
    context_sha256 = _sha256(context_path)
    if context_sha256 != os.environ["TAU_GENERIC_DAG_CONTEXT_SHA256"]:
        raise RuntimeError("attempt context hash mismatch")
    attempt = int(context["attempt"])
    artifact_root.mkdir(parents=True, exist_ok=True)
    output = artifact_root / f"{stage}-attempt-{attempt}.png"
    provider_receipt: dict[str, Any] | None = None
    if stage == "stage-1" and attempt == 1:
        with Image.open(reference) as source:
            Image.new("RGB", source.size, color=(0, 0, 0)).save(output)
    else:
        accepted_inputs = context.get("accepted_inputs", [])
        if stage == "stage-2" and (
            not isinstance(accepted_inputs, list) or len(accepted_inputs) != 1
        ):
            raise RuntimeError("stage-2 requires exactly one accepted input")
        contract = _read_json(sequence_contract) if sequence_contract else {}
        prompt_path = artifact_root / f"{stage}-attempt-{attempt}.prompt.md"
        provider_receipt_path = artifact_root / f"{stage}-attempt-{attempt}.provider.json"
        provider_events_path = artifact_root / f"{stage}-attempt-{attempt}.events.jsonl"
        prompt_path.write_text(
            _producer_prompt(
                stage=stage,
                reference=reference,
                contract=contract,
                accepted_inputs=accepted_inputs,
                revision=context.get("revision"),
            ),
            encoding="utf-8",
        )
        command = [
            "/home/graham/workspace/experiments/agent-skills/skills/scillm/run.sh",
            "generate-image",
            "--auth",
            "codex-oauth",
            "--prompt-file",
            str(prompt_path),
            "--out",
            str(output),
            "--receipt",
            str(provider_receipt_path),
            "--events-out",
            str(provider_events_path),
            "--model",
            producer_model,
            "--quality",
            "high",
            "--caller-skill",
            "tau-generic-artifact-canary-producer",
            "--timeout-s",
            str(producer_timeout_seconds),
            "--first-event-timeout-s",
            str(min(producer_timeout_seconds, 120.0)),
            "--json",
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError(
                f"Scillm image producer failed ({result.returncode}): "
                f"{result.stderr[-1000:] or result.stdout[-1000:]}"
            )
        provider_receipt = _read_json(provider_receipt_path)
    manifest_path = Path(context["output_contract"]["candidate_manifest_path"])
    _write_json(
        manifest_path,
        {
            "schema": "tau.media_artifact_manifest.v1",
            "transaction_id": context["transaction_id"],
            "node_id": context["node_id"],
            "attempt": attempt,
            "producer_id": context["producer_id"],
            "work_order_sha256": context["work_order"]["sha256"],
            "attempt_context_sha256": context_sha256,
            "artifacts": [
                {
                    "artifact_id": "primary",
                    "kind": "image",
                    "media_type": "image/png",
                    "path": str(output.resolve()),
                    "sha256": _sha256(output),
                    "bytes": output.stat().st_size,
                }
            ],
        },
    )
    _write_json(
        receipt_path,
        {
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": context["node_id"],
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "provider_execution": (
                {
                    "provider_live": True,
                    "provider": "scillm",
                    "model": producer_model,
                    "receipt_path": str(provider_receipt_path),
                    "receipt_sha256": _sha256(provider_receipt_path),
                }
                if provider_receipt is not None
                else None
            ),
            "artifacts": [],
            "commands_run": ["generic artifact canary producer"],
            "errors": [],
            "policy_exceptions": [],
            "handoff_summary": f"{stage} produced attempt {attempt}",
            "work_order_sha256": _sha256(work_order_path),
        },
    )
    count = int(counter_path.read_text()) if counter_path.exists() else 0
    counter_path.write_text(str(count + 1), encoding="utf-8")


def _producer_prompt(
    *,
    stage: str,
    reference: Path,
    contract: dict[str, Any],
    accepted_inputs: Any,
    revision: Any,
) -> str:
    input_hashes = (
        [
            artifact.get("sha256")
            for projection in accepted_inputs
            if isinstance(projection, dict)
            for artifact in projection.get("artifacts", [])
            if isinstance(artifact, dict)
        ]
        if isinstance(accepted_inputs, list)
        else []
    )
    retry_research = contract.get("retry_research")
    retry_guidance: Any = None
    if isinstance(retry_research, dict) and isinstance(retry_research.get("path"), str):
        retry_guidance = _read_json(Path(retry_research["path"]))
    return (
        "Create a clearly visible original pixel-art animation contact sheet.\n"
        f"Stage: {stage}.\n"
        f"Immutable character reference path: {reference}.\n"
        f"Immutable character reference sha256: {_sha256(reference)}.\n"
        f"Sequence contract: {json.dumps(contract, sort_keys=True)}.\n"
        f"Accepted prior-sequence hashes (context only): {json.dumps(input_hashes)}.\n"
        f"Reviewer revision: {json.dumps(revision, sort_keys=True)}.\n"
        f"Retry research guidance: {json.dumps(retry_guidance, sort_keys=True)}.\n"
        "Render exactly the requested frame_count as distinct consecutive motion phases in "
        "the requested grid_columns by grid_rows layout, read left-to-right then top-to-bottom. "
        "Every panel must contain the same right-facing character with consistent identity, "
        "scale, baseline, palette, and crisp pixel-art edges. Use no labels, text, borders, or "
        "decorative background. "
        "The new sequence must be visually distinct from prior accepted sequences while "
        "preserving the same character identity."
    )


def review(*, model: str, base_url: str, timeout_seconds: float) -> None:
    context_path = Path(os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT"])
    context = _read_json(context_path)
    context_sha256 = _sha256(context_path)
    if context_sha256 != os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256"]:
        raise RuntimeError("review context hash mismatch")
    artifact = context["validated_artifacts"][0]
    image_path = Path(str(artifact["path"]))
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    attempt_context = _read_json(Path(context["attempt_context_path"]))
    work_order = _read_json(Path(attempt_context["work_order"]["path"]))
    sequence_contract = _read_json(Path(work_order["sequence_contract"]))
    reference = Path(work_order["immutable_reference"])
    reference_data = base64.b64encode(reference.read_bytes()).decode("ascii")
    prompt = (
        "You are an independent Battle sprite-sequence reviewer. Return JSON only. "
        f"Sequence contract: {json.dumps(sequence_contract, sort_keys=True)}. "
        "If the image is entirely or effectively blank/solid black, verdict must be REVISE "
        "with one finding whose artifact_ids is ['primary'] and whose revision_instruction "
        "requests a visible non-blank image. Otherwise inspect the candidate against the "
        "immutable reference and require the requested number of visibly distinct motion "
        "panels, consistent character identity, right-facing orientation, stable scale and "
        "baseline, no text or panel labels, and state-appropriate motion. Return REVISE with "
        "specific correction instructions for any material defect; otherwise PASS. "
        "finding. Required keys: verdict, summary, findings. Each finding requires "
        "finding_id, code, severity, message, artifact_ids, revision_instruction."
    )
    auth_preflight = preflight_battle_scillm_auth(
        scillm_base_url=base_url,
        model=model,
    )
    if auth_preflight.get("status") != "PASS":
        raise RuntimeError(
            "Scillm auth preflight blocked live review: "
            + "; ".join(str(error) for error in auth_preflight.get("errors", []))
        )
    key, key_source, key_errors = resolve_active_scillm_proxy_key()
    if not key:
        raise RuntimeError(f"Scillm key unavailable: {key_errors}")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{reference_data}"},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_data}"},
                    },
                ],
            }
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "scillm_metadata": {
            "caller": "tau",
            "proof": "generic-artifact-transaction-canary",
            "node_id": context["node_id"],
            "attempt": context["attempt"],
        },
    }
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "X-Caller-Skill": "tau-generic-artifact-canary-reviewer",
        },
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    verdict = str(parsed.get("verdict") or "").upper()
    findings = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
    output_path = Path(context["output_contract"]["review_feedback_path"])
    _write_json(
        output_path,
        {
            "schema": "tau.generic_artifact_review.v1",
            "transaction_id": context["transaction_id"],
            "node_id": context["node_id"],
            "attempt": context["attempt"],
            "producer_id": context["producer_id"],
            "reviewer_id": context["reviewer_id"],
            "review_context_sha256": context_sha256,
            "candidate_manifest_sha256": context["candidate_manifest_sha256"],
            "verdict": verdict,
            "summary": str(parsed.get("summary") or "Scillm VLM review returned no summary"),
            "findings": findings,
            "mocked": False,
            "live": True,
            "provider_live": True,
            "provider": "scillm",
            "model": model,
            "http_status": response.status_code,
            "api_key_source": key_source,
            "provider_response_id": body.get("id"),
            "artifact_sha256": artifact["sha256"],
        },
    )


def continue_locally(*, marker: Path) -> None:
    context = _read_json(Path(os.environ["TAU_GENERIC_DAG_CONTEXT"]))
    marker.write_text(
        json.dumps(
            {
                "continued": True,
                "accepted_manifest_sha256": context["accepted_manifest_sha256"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


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
    subparsers = parser.add_subparsers(dest="command", required=True)
    producer = subparsers.add_parser("produce")
    producer.add_argument("--stage", required=True)
    producer.add_argument("--reference", type=Path, required=True)
    producer.add_argument("--artifact-root", type=Path, required=True)
    producer.add_argument("--receipt", type=Path, required=True)
    producer.add_argument("--work-order", type=Path, required=True)
    producer.add_argument("--counter", type=Path, required=True)
    producer.add_argument("--sequence-contract", type=Path)
    producer.add_argument("--producer-model", default="gpt-2")
    producer.add_argument("--producer-timeout-s", type=float, default=300)
    reviewer = subparsers.add_parser("review")
    reviewer.add_argument("--model", default="gpt-5.5")
    reviewer.add_argument("--base-url", default="http://127.0.0.1:4001")
    reviewer.add_argument("--timeout-s", type=float, default=180)
    continuation = subparsers.add_parser("continue")
    continuation.add_argument("--marker", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "produce":
        produce(
            stage=args.stage,
            reference=args.reference,
            artifact_root=args.artifact_root,
            receipt_path=args.receipt,
            work_order_path=args.work_order,
            counter_path=args.counter,
            sequence_contract=args.sequence_contract,
            producer_model=args.producer_model,
            producer_timeout_seconds=args.producer_timeout_s,
        )
    elif args.command == "review":
        review(model=args.model, base_url=args.base_url, timeout_seconds=args.timeout_s)
    else:
        continue_locally(marker=args.marker)


if __name__ == "__main__":
    main()
