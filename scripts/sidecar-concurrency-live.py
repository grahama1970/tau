#!/usr/bin/env python3
"""Live two-process concurrency + torn-tail harness for the write-intent
sidecar (#198). Two real processes append interleaved records; the file is
then truncated mid-record. Exit 0 only if every appended record survives
un-interleaved and the torn tail is classified, not fatal."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.write_intent import append_intent, read_sidecar  # noqa: E402

WRITER = """
import sys
sys.path.insert(0, {src!r})
from pathlib import Path
from tau_coding.dag_runtime.write_intent import append_intent
for i in range({count}):
    append_intent(Path({sidecar!r}), run_id="run-live", node_id={node!r},
                  attempt_id=f"attempt-{{i}}", receipt_kind="node_receipt", stage="S1")
"""


def main() -> int:
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "sidecar-live-run").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    sidecar = run_dir / "intents.twi"
    count = 200
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                WRITER.format(src=str(SRC), sidecar=str(sidecar), node=node, count=count),
            ]
        )
        for node in ("proc-a", "proc-b")
    ]
    rcs = [p.wait(timeout=120) for p in procs]
    clean = read_sidecar(sidecar)
    by_node = {
        "proc-a": [r for r in clean.records if r["node_id"] == "proc-a"],
        "proc-b": [r for r in clean.records if r["node_id"] == "proc-b"],
    }
    complete = all(len(v) == count for v in by_node.values())
    ordered = all(
        [r["attempt_id"] for r in v] == [f"attempt-{i}" for i in range(count)]
        for v in by_node.values()
    )
    # Torn tail: append one more record then cut it mid-body.
    append_intent(sidecar, run_id="run-live", node_id="tail", attempt_id="attempt-x",
                  receipt_kind="node_receipt", stage="S1")
    blob = sidecar.read_bytes()
    sidecar.write_bytes(blob[:-7])
    torn = read_sidecar(sidecar)
    tail_ok = (
        len(torn.records) == 2 * count
        and torn.torn_tail_reason in {"truncated_body", "crc_mismatch"}
    )
    ok = all(rc == 0 for rc in rcs) and complete and ordered and tail_ok
    receipt = {
        "schema": "tau.sidecar_concurrency_receipt.v1",
        "mocked": False,
        "live": True,
        "ok": ok,
        "writer_returncodes": rcs,
        "records_per_writer": {k: len(v) for k, v in by_node.items()},
        "per_writer_order_preserved": ordered,
        "torn_tail_reason": torn.torn_tail_reason,
        "records_after_torn_tail": len(torn.records),
    }
    write_durable_json(run_dir / "sidecar-concurrency-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
