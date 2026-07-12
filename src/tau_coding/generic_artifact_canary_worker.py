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
) -> None:
    context_path = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    context = _read_json(context_path)
    context_sha256 = _sha256(context_path)
    if context_sha256 != os.environ["TAU_GENERIC_DAG_CONTEXT_SHA256"]:
        raise RuntimeError("attempt context hash mismatch")
    attempt = int(context["attempt"])
    artifact_root.mkdir(parents=True, exist_ok=True)
    output = artifact_root / f"{stage}-attempt-{attempt}.png"
    if stage == "stage-1" and attempt == 1:
        with Image.open(reference) as source:
            Image.new("RGB", source.size, color=(0, 0, 0)).save(output)
    else:
        source_path = reference
        if stage == "stage-2":
            accepted_inputs = context.get("accepted_inputs")
            if not isinstance(accepted_inputs, list) or len(accepted_inputs) != 1:
                raise RuntimeError("stage-2 requires exactly one accepted input")
            artifacts = accepted_inputs[0].get("artifacts")
            if not isinstance(artifacts, list) or len(artifacts) != 1:
                raise RuntimeError("stage-2 accepted input must contain one artifact")
            source_path = Path(str(artifacts[0]["path"]))
        with Image.open(source_path) as source:
            source.convert("RGB").save(output)
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


def review(*, model: str, base_url: str, timeout_seconds: float) -> None:
    context_path = Path(os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT"])
    context = _read_json(context_path)
    context_sha256 = _sha256(context_path)
    if context_sha256 != os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256"]:
        raise RuntimeError("review context hash mismatch")
    artifact = context["validated_artifacts"][0]
    image_path = Path(str(artifact["path"]))
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = (
        "You are an independent image admissibility reviewer. Return JSON only. "
        "If the image is entirely or effectively blank/solid black, verdict must be REVISE "
        "with one finding whose artifact_ids is ['primary'] and whose revision_instruction "
        "requests a visible non-blank image. Otherwise verdict must be PASS with no BLOCK "
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
        )
    elif args.command == "review":
        review(model=args.model, base_url=args.base_url, timeout_seconds=args.timeout_s)
    else:
        continue_locally(marker=args.marker)


if __name__ == "__main__":
    main()
