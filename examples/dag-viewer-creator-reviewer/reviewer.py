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
    context_path = Path(os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    attempt = int(context["attempt"])
    verdict = "REVISE" if attempt == 1 else "PASS"
    findings = (
        [
            {
                "finding_id": "revise-1",
                "code": "CONTENT_REVISION_REQUIRED",
                "severity": "ERROR",
                "message": "The first candidate must be revised.",
                "artifact_ids": ["primary"],
                "revision_instruction": "Produce a distinct second candidate.",
            }
        ]
        if verdict == "REVISE"
        else []
    )
    output = Path(context["output_contract"]["review_feedback_path"])
    output.write_text(
        json.dumps(
            {
                "schema": "tau.generic_artifact_review.v1",
                "transaction_id": context["transaction_id"],
                "node_id": context["node_id"],
                "attempt": attempt,
                "producer_id": context["producer_id"],
                "reviewer_id": context["reviewer_id"],
                "review_context_sha256": os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256"],
                "candidate_manifest_sha256": context["candidate_manifest_sha256"],
                "verdict": verdict,
                "mocked": False,
                "live": True,
                "provider_live": False,
                "summary": "Revise the first candidate." if verdict == "REVISE" else "PASS claim",
                "findings": findings,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
