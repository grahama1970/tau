from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step-delay-seconds", type=float, default=0.0)
    args = parser.parse_args()
    time.sleep(max(0.0, args.step_delay_seconds))
    context_path = Path(os.environ["TAU_GENERIC_DAG_VALIDATION_CONTEXT"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    output = Path(context["output_contract"]["validation_receipt_path"])
    output.write_text(
        json.dumps(
            {
                "schema": "tau.generic_artifact_validation.v1",
                "status": "PASS",
                "node_id": context["node_id"],
                "transaction_id": context["transaction_id"],
                "attempt": context["attempt"],
                "validator_id": context["validator_id"],
                "validation_context_sha256": os.environ[
                    "TAU_GENERIC_DAG_VALIDATION_CONTEXT_SHA256"
                ],
                "candidate_manifest_sha256": context["candidate_manifest_sha256"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
