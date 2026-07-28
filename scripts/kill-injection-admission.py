#!/usr/bin/env python3
"""Live kill-injection harness for the admission write primitive (#197).

Runs each crash boundary as a real SIGKILLed subprocess against a real run
directory and emits a receipt naming every observation. Exit 0 only if no
boundary ever produced a torn final file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402

HARNESS = """
import os, signal, sys
sys.path.insert(0, {src!r})
from pathlib import Path
from unittest.mock import patch
import tau_coding.dag_runtime.admission as adm
target = Path({target!r})
boundary = {boundary!r}
_orig = os.replace
def die(*_a, **_k): os.kill(os.getpid(), signal.SIGKILL)
if boundary == "during_temp_write":
    with patch.object(adm.os, "fsync", side_effect=die):
        adm.write_durable_json(target, {{"attempt": 99}})
elif boundary == "before_rename":
    with patch.object(adm.os, "replace", side_effect=die):
        adm.write_durable_json(target, {{"attempt": 99}})
else:
    def rd(a, b):
        _orig(a, b); die()
    with patch.object(adm.os, "replace", side_effect=rd):
        adm.write_durable_json(target, {{"attempt": 99}})
"""


def main() -> int:
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "kill-injection-run").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    observations = []
    ok = True
    for boundary in ("during_temp_write", "before_rename", "after_rename_before_dirsync"):
        target = run_dir / f"{boundary}.json"
        write_durable_json(target, {"attempt": 1})
        before = target.read_bytes()
        code = HARNESS.format(src=str(SRC), target=str(target), boundary=boundary)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
        after = target.read_bytes()
        try:
            payload = json.loads(after)
            torn = payload not in ({"attempt": 1}, {"attempt": 99})
        except json.JSONDecodeError:
            payload, torn = None, True
        expected_final = boundary == "after_rename_before_dirsync"
        correct = (not torn) and (
            payload == {"attempt": 99} if expected_final else after == before
        )
        ok = ok and proc.returncode == -9 and correct
        observations.append(
            {
                "boundary": boundary,
                "subprocess_returncode": proc.returncode,
                "final_payload": payload,
                "torn": torn,
                "correct": correct,
            }
        )
    receipt = {
        "schema": "tau.admission_kill_injection_receipt.v1",
        "mocked": False,
        "live": True,
        "ok": ok,
        "observations": observations,
    }
    receipt_path = run_dir / "kill-injection-receipt.json"
    write_durable_json(receipt_path, receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
