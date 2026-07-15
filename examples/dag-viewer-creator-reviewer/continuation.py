from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--step-delay-seconds", type=float, default=0.0)
    args = parser.parse_args()
    time.sleep(max(0.0, args.step_delay_seconds))
    context_path = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if not context.get("accepted_inputs"):
        raise RuntimeError("accepted_input_missing")
    args.marker.write_text("released after Tau acceptance\n", encoding="utf-8")
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
                "commands_run": ["deterministic continuation"],
                "errors": [],
                "policy_exceptions": [],
                "handoff_summary": "accepted transaction output consumed",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
