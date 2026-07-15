from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_live_creator_reviewer_smoke_observes_admission_boundary(tmp_path: Path) -> None:
    output = tmp_path / "smoke.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run-dag-viewer-live-smoke.py",
            "--step-delay-seconds",
            "0.03",
            "--run-root",
            str(tmp_path / "smoke-run"),
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert all(receipt["checks"].values())
