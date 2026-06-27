import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from tau_agent.session import entries_from_json_lines
from tau_ai import FakeProvider, ProviderErrorEvent

EXPERIMENT_DIR = Path(__file__).resolve().parents[1] / "experiments" / "loop2-alignment"


def _load_experiment_module():
    path = EXPERIMENT_DIR / "tau_loop_receipt.py"
    spec = importlib.util.spec_from_file_location("tau_loop_receipt_experiment", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load experiment module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_loop2_alignment_tool_map_indexes_live_proofs() -> None:
    tool_map = json.loads((EXPERIMENT_DIR / "tau_loop2_tools.json").read_text())
    live_evidence = tool_map["live_evidence"]

    assert tool_map["still_missing"] == []
    assert {item["name"] for item in live_evidence} == {
        "live_scillm_opencode_repair",
        "env_scillm_auth_contract_preparation",
        "scillm_auth_preflight",
    }
    for item in live_evidence:
        proof_path = Path(__file__).resolve().parents[1] / item["proof"]
        assert proof_path.exists()
        proof = json.loads(proof_path.read_text())
        assert item["status"] == "proven"
        assert item["mocked"] is False
        assert item["live"] is True
        assert proof["mocked"] is False
        assert proof["live"] is True


@pytest.mark.anyio
async def test_fake_tau_loop_receipt_writes_loop2_shaped_artifacts(tmp_path: Path) -> None:
    module = _load_experiment_module()

    result = await module.run_fake_tau_loop_receipt(run_dir=tmp_path / "run")

    assert result.events_path.exists()
    assert result.current_state_path.exists()
    assert result.final_receipt_path.exists()
    assert result.node_result_path.exists()
    assert result.session_path.exists()

    events = [json.loads(line) for line in result.events_path.read_text().splitlines()]
    assert [row["event"]["type"] for row in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]

    current_state = json.loads(result.current_state_path.read_text())
    assert current_state["schema"] == "tau.loop_receipt.current_state.v1"
    assert current_state["status"] == "PASS"
    assert current_state["event_count"] == len(events)
    assert current_state["mocked"] is True
    assert current_state["live"] is False

    final_receipt = json.loads(result.final_receipt_path.read_text())
    assert final_receipt["schema"] == "tau.loop_receipt.final.v1"
    assert final_receipt["status"] == "PASS"
    assert final_receipt["provider"] == "fake"
    assert final_receipt["session_entry_count"] == 7
    assert "live provider behavior" in final_receipt["claims"]["does_not_prove"]

    node_result = json.loads(result.node_result_path.read_text())
    assert node_result["schema"] == "loop2.node_result.v1"
    assert node_result["status"] == "PASS"
    assert node_result["mocked"] is True
    assert node_result["live"] is False
    assert node_result["events"] == str(result.events_path)
    assert node_result["final_receipt"] == str(result.final_receipt_path)

    session_entries = entries_from_json_lines(result.session_path.read_text().splitlines())
    assert [entry.type for entry in session_entries] == [
        "session_info",
        "model_change",
        "thinking_level_change",
        "message",
        "leaf",
        "message",
        "leaf",
    ]


@pytest.mark.anyio
@pytest.mark.skipif(
    not (os.environ.get("CHUTES_API_TOKEN") or os.environ.get("CHUTES_API_KEY")),
    reason="CHUTES_API_TOKEN or CHUTES_API_KEY is required for live Chutes proof",
)
async def test_live_chutes_tau_loop_receipt_writes_loop2_shaped_artifacts(
    tmp_path: Path,
) -> None:
    module = _load_experiment_module()

    result = await module.run_chutes_tau_loop_receipt(run_dir=tmp_path / "run-live")

    events = [json.loads(line) for line in result.events_path.read_text().splitlines()]
    event_types = [row["event"]["type"] for row in events]
    assert event_types[0] == "agent_start"

    final_receipt = json.loads(result.final_receipt_path.read_text())
    assert final_receipt["schema"] == "tau.loop_receipt.final.v1"
    assert final_receipt["provider"] == "chutes"
    assert final_receipt["model"] == module.CHUTES_DEFAULT_MODEL
    assert final_receipt["mocked"] is False
    assert final_receipt["live"] is True

    node_result = json.loads(result.node_result_path.read_text())
    assert node_result["schema"] == "loop2.node_result.v1"
    assert node_result["status"] == final_receipt["status"]
    assert node_result["mocked"] is False
    assert node_result["live"] is True
    if final_receipt["status"] == "PASS":
        assert "message_delta" in event_types
        assert event_types[-1] == "agent_end"
    else:
        assert final_receipt["status"] == "FAILED"
        assert "error" in event_types


@pytest.mark.anyio
async def test_tau_loop_receipt_marks_provider_error_failed(tmp_path: Path) -> None:
    module = _load_experiment_module()
    provider = FakeProvider([[ProviderErrorEvent(message="provider failed")]])

    result = await module._run_tau_loop_receipt(
        run_dir=tmp_path / "run-error",
        prompt="fail",
        node_id="tau-provider-error-loop",
        provider=provider,
        provider_name="fake",
        model="fake",
        mocked=True,
        live=False,
        close_provider=False,
    )

    events = [json.loads(line) for line in result.events_path.read_text().splitlines()]
    final_receipt = json.loads(result.final_receipt_path.read_text())
    node_result = json.loads(result.node_result_path.read_text())

    assert "error" in [row["event"]["type"] for row in events]
    assert final_receipt["status"] == "FAILED"
    assert node_result["status"] == "FAILED"
