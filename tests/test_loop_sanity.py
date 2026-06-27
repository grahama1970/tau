import json
from pathlib import Path

import pytest

from tau_coding.loop_sanity import run_loop2_sanity

LOOP2_SRC = (
    Path(__file__).resolve().parents[2] / "agent-skills" / "skills" / "loop2" / "src"
)


@pytest.mark.skipif(not LOOP2_SRC.exists(), reason="Loop2 source tree is not available")
def test_run_loop2_sanity_creates_valid_fixture_receipt_run(tmp_path: Path) -> None:
    result = run_loop2_sanity(
        root_dir=tmp_path / "sanity",
        repo=Path.cwd(),
        loop2_src=LOOP2_SRC,
    )

    run_dir = Path(str(result["run_dir"]))

    assert result["schema"] == "tau.loop2_sanity.v1"
    assert result["ok"] is True
    assert result["mocked"] is True
    assert result["live"] is False
    assert run_dir.exists()
    assert result["loop2_contract_validation"] == {
        "ok": True,
        "checked_artifacts": [
            "contract",
            "final_receipt",
            "node_result",
            "events",
            "current_state",
            "transport_dag_evidence",
            "artifact_paths",
            "check_status",
            "mocked_live",
            "node_result_parity",
            "contract_parity",
            "state_status",
        ],
        "errors": [],
    }
    assert result["monitor_check"] == {
        "ok": True,
        "checked_endpoints": [
            "summary",
            "transport-dag-evidence",
            "events",
            "events/stream",
            "peer-message",
        ],
        "errors": [],
    }

    final_receipt = json.loads((run_dir / "final-receipt.json").read_text())
    node_result = json.loads((run_dir / "node-result.json").read_text())
    stdout = (run_dir / "checks" / "sanity.stdout.txt").read_text()

    assert final_receipt["mocked"] is True
    assert final_receipt["live"] is False
    assert final_receipt["status"] == "PASS"
    assert final_receipt["checks"][0]["exit_code"] == 0
    assert "live provider behavior" in final_receipt["claims"]["does_not_prove"]
    assert node_result["schema"] == "loop2.node_result.v1"
    assert stdout == "tau loop2 sanity check\n"
