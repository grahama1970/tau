from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--work-order", type=Path, required=True)
    parser.add_argument("--step-delay-seconds", type=float, default=0.0)
    args = parser.parse_args()
    time.sleep(max(0.0, args.step_delay_seconds))

    context_path = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context_sha256 = hashlib.sha256(context_path.read_bytes()).hexdigest()
    if context_sha256 != os.environ["TAU_GENERIC_DAG_CONTEXT_SHA256"]:
        raise RuntimeError("attempt_context_hash_mismatch")
    attempt = int(context["attempt"])
    if attempt == 2 and context.get("revision", {}).get("source_attempt") != 1:
        raise RuntimeError("revision_not_consumed")

    args.artifact_root.mkdir(parents=True, exist_ok=True)
    artifact = args.artifact_root / f"candidate-{attempt}.txt"
    artifact.write_text(f"candidate attempt {attempt}\n", encoding="utf-8")
    manifest_path = Path(context["output_contract"]["candidate_manifest_path"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
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
                        "kind": "text",
                        "media_type": "text/plain",
                        "path": str(artifact),
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        "bytes": artifact.stat().st_size,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_node_receipt.v1",
                "node_id": context["node_id"],
                "status": "PASS",
                "verdict": "PASS",
                "mocked": False,
                "live": True,
                "provider_live": False,
                "artifacts": [],
                "commands_run": ["deterministic creator"],
                "errors": [],
                "policy_exceptions": [],
                "handoff_summary": f"candidate {attempt} produced",
                "work_order_sha256": hashlib.sha256(args.work_order.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
