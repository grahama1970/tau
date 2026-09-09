from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-doc-status.py"


def test_unmarked_future_claim_fails(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("# Demo\n\nThis future supported feature is ready.\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(doc)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["unclassified_count"] == 1


def test_marked_roadmap_future_claim_passes(tmp_path: Path) -> None:
    doc = tmp_path / "roadmap.md"
    doc.write_text(
        "# Demo\n<!-- tau-doc-status: ROADMAP -->\n\nThis future supported feature is planned.\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(doc)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["claims"][0]["classification"] == "ROADMAP"
