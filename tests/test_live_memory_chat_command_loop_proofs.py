import json
from pathlib import Path

PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / "experiments/goal-locked-subagents/proofs/live-memory-chat-command-loop-20260628T013609Z"
)
RESEARCH_RECEIPT_PRODUCER_PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / "experiments/goal-locked-subagents/proofs/external-research-receipt-producer-20260628T022000Z"
)
LIVE_BRAVE_RECEIPT_PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / "experiments/goal-locked-subagents/proofs/live-brave-research-receipt-20260628T023500Z"
)
LIVE_CLARIFY_DEFLECT_PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / "experiments/goal-locked-subagents/proofs/live-clarify-deflect-memory-routes-20260628T021701Z"
)
LIVE_ROUTE_COVERAGE_PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / "experiments/goal-locked-subagents/proofs/live-memory-route-coverage-20260628T022500Z"
)
SUBAGENT_RECEIPT_COVERAGE_PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / (
        "experiments/goal-locked-subagents/proofs/"
        "subagent-receipt-backed-route-coverage-20260628T023000Z"
    )
)
LIVE_ROUTE_FAILCLOSED_PROOF_DIR = (
    Path(__file__).resolve().parents[1]
    / "experiments/goal-locked-subagents/proofs/live-memory-route-failclosed-20260628T140048Z"
)


def test_live_memory_chat_command_loop_proof_manifest_matches_raw_receipts() -> None:
    manifest_path = PROOF_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema"] == "tau.live_memory_chat_command_loop_proof_manifest.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert [route["route"] for route in manifest["routes"]] == [
        "ANSWER",
        "COMPLIANCE",
        "RESEARCH",
        "RESEARCH_AUTHORIZED_RECEIPT",
    ]
    assert {
        route["route"]: route["response_result_status"] for route in manifest["routes"]
    } == {
        "ANSWER": "COMPLETED",
        "COMPLIANCE": "COMPLETED",
        "RESEARCH": "REFUSED",
        "RESEARCH_AUTHORIZED_RECEIPT": "COMPLETED",
    }

    for route in manifest["routes"]:
        start = json.loads(
            (PROOF_DIR / Path(route["start_handoff"]).name).read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (PROOF_DIR / Path(route["command_loop_receipt"]).name).read_text(encoding="utf-8")
        )
        dispatch = receipt["dispatches"][0]

        assert route["start_next_agent"] == start["next_agent"]["name"]
        assert receipt["schema"] == "tau.agent_handoff_command_loop_receipt.v1"
        assert receipt["ok"] is True
        assert receipt["mocked"] is False
        assert receipt["live"] is True
        assert receipt["step_count"] == route["step_count"]
        assert receipt["terminal_agent"] == "human"
        assert receipt["stop_reason"] == "next_agent_is_human"

        if route["route"] == "RESEARCH_AUTHORIZED_RECEIPT":
            assert route["selected_agents"] == [
                dispatch["selected_agent"],
                receipt["dispatches"][1]["selected_agent"],
            ]
            assert route["command_exits"] == [
                step["command_results"][0]["exit_code"] for step in receipt["dispatches"]
            ]
            assert route["external_research_receipt_live"] is False
            receipt_path = Path(__file__).resolve().parents[1] / route["external_research_receipt"]
            research_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            assert research_receipt["schema"] == "tau.external_research_receipt.v1"
            assert research_receipt["method"] == "brave-search"
        else:
            assert route["selected_agent"] == dispatch["selected_agent"]
            assert route["command_exit"] == dispatch["command_results"][0]["exit_code"]
            assert receipt["step_count"] == 1


def test_external_research_receipt_producer_proof_matches_written_receipt() -> None:
    manifest = json.loads(
        (RESEARCH_RECEIPT_PRODUCER_PROOF_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    stdout_payload = json.loads(
        (RESEARCH_RECEIPT_PRODUCER_PROOF_DIR / "stdout.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (RESEARCH_RECEIPT_PRODUCER_PROOF_DIR / "receipt.json").read_text(encoding="utf-8")
    )

    assert manifest["schema"] == "tau.external_research_receipt_producer_proof.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["external_research_receipt_live"] is False
    assert manifest["exit_code"] == 0
    assert manifest["stdout_matches_receipt"] is True
    assert stdout_payload == receipt
    assert receipt["schema"] == "tau.external_research_receipt.v1"
    assert receipt["method"] == "brave-search"
    assert len(receipt["sources"]) == 1


def test_live_brave_receipt_proof_routes_through_research_auditor() -> None:
    manifest = json.loads((LIVE_BRAVE_RECEIPT_PROOF_DIR / "manifest.json").read_text())
    receipt = json.loads((LIVE_BRAVE_RECEIPT_PROOF_DIR / "receipt.json").read_text())
    command_loop = json.loads(
        (LIVE_BRAVE_RECEIPT_PROOF_DIR / "command-loop/command-loop-receipt.json").read_text()
    )

    assert manifest["schema"] == "tau.live_brave_research_receipt_proof.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["external_research_receipt_live"] is True
    assert manifest["exit_code"] == 0
    assert manifest["receipt_schema"] == "tau.external_research_receipt.v1"
    assert manifest["source_count"] >= 1
    assert manifest["stdout_matches_receipt"] is True
    assert receipt["schema"] == "tau.external_research_receipt.v1"
    assert receipt["method"] == "brave-search"
    assert len(receipt["sources"]) == manifest["source_count"]
    assert command_loop["ok"] is True
    assert command_loop["mocked"] is False
    assert command_loop["live"] is True
    assert command_loop["step_count"] == 2
    assert command_loop["terminal_agent"] == "human"
    assert command_loop["stop_reason"] == "next_agent_is_human"
    assert manifest["command_loop_selected_agents"] == ["research-auditor", "reviewer"]
    assert manifest["command_loop_command_exits"] == [0, 0]


def test_live_clarify_deflect_memory_routes_have_command_loop_proofs() -> None:
    manifest = json.loads((LIVE_CLARIFY_DEFLECT_PROOF_DIR / "manifest.json").read_text())

    assert manifest["schema"] == "tau.live_clarify_deflect_memory_routes_proof.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    routes = {route["route"]: route for route in manifest["routes"]}
    assert set(routes) == {"CLARIFY", "DEFLECT"}

    clarify = routes["CLARIFY"]
    assert clarify["selected_skill"] == "memory.clarify"
    assert clarify["branch_stage"] == "clarify"
    assert clarify["final_stage"] == "clarify"
    assert clarify["stage_trace_stages"] == ["intent", "extract_entities", "recall", "clarify"]

    deflect = routes["DEFLECT"]
    assert deflect["selected_skill"] == "memory.deflect"
    assert deflect["branch_stage"] == "deflect"
    assert "deflect" in deflect["stage_trace_stages"]
    assert deflect["final_stage"] == "personaplex"

    for route in routes.values():
        harness = json.loads(
            (Path(__file__).resolve().parents[1] / route["harness_receipt"]).read_text()
        )
        command_loop = json.loads(
            (Path(__file__).resolve().parents[1] / route["command_loop_receipt"]).read_text()
        )
        assert route["mocked"] is False
        assert route["live"] is True
        assert route["memory_first"] is True
        assert route["branch_status"] == "PASS"
        assert route["fail_closed"] is False
        assert harness["selected_skill"] == route["selected_skill"]
        assert command_loop["ok"] is True
        assert command_loop["mocked"] is False
        assert command_loop["live"] is True
        assert command_loop["step_count"] == 1
        assert command_loop["dispatches"][0]["selected_agent"] == "reviewer"
        assert command_loop["dispatches"][0]["command_results"][0]["exit_code"] == 0
        assert command_loop["terminal_agent"] == "human"
        assert command_loop["stop_reason"] == "next_agent_is_human"


def test_live_memory_route_coverage_manifest_matches_source_manifests() -> None:
    manifest = json.loads((LIVE_ROUTE_COVERAGE_PROOF_DIR / "manifest.json").read_text())

    assert manifest["schema"] == "tau.live_memory_route_coverage_proof.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["required_routes"] == [
        "CLARIFY",
        "DEFLECT",
        "ANSWER",
        "COMPLIANCE",
        "RESEARCH",
        "RESEARCH_LIVE_BRAVE_RECEIPT",
    ]
    routes = {route["route"]: route for route in manifest["routes"]}
    assert set(routes) == set(manifest["required_routes"])

    for name in ("CLARIFY", "DEFLECT", "ANSWER", "COMPLIANCE", "RESEARCH"):
        assert routes[name]["mocked"] is False
        assert routes[name]["live"] is True
        assert routes[name]["command_loop_ok"] is True

    assert routes["CLARIFY"]["selected_skill"] == "memory.clarify"
    assert routes["CLARIFY"]["branch_stage"] == "clarify"
    assert routes["DEFLECT"]["selected_skill"] == "memory.deflect"
    assert routes["DEFLECT"]["branch_stage"] == "deflect"
    assert routes["DEFLECT"]["final_stage"] == "personaplex"
    assert routes["ANSWER"]["response_result_status"] == "COMPLETED"
    assert routes["COMPLIANCE"]["response_result_status"] == "COMPLETED"
    assert routes["RESEARCH"]["response_result_status"] == "REFUSED"
    assert routes["RESEARCH"]["selected_agent"] == "research-auditor"

    live_research = routes["RESEARCH_LIVE_BRAVE_RECEIPT"]
    assert live_research["mocked"] is False
    assert live_research["live"] is True
    assert live_research["external_research_receipt_live"] is True
    assert live_research["source_count"] >= 1
    assert live_research["command_loop_ok"] is True
    assert live_research["command_loop_selected_agents"] == ["research-auditor", "reviewer"]
    assert live_research["command_loop_command_exits"] == [0, 0]

    for route in manifest["routes"]:
        source_manifest = Path(__file__).resolve().parents[1] / route["source_manifest"]
        assert source_manifest.exists()
        source = json.loads(source_manifest.read_text())
        assert source["mocked"] is False
        assert source["live"] is True


def test_subagent_receipt_backed_route_coverage_matches_source_handoffs() -> None:
    manifest = json.loads((SUBAGENT_RECEIPT_COVERAGE_PROOF_DIR / "manifest.json").read_text())

    assert manifest["schema"] == "tau.subagent_receipt_backed_route_coverage_proof.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["receipt_count"] == 7
    routes = {receipt["route"]: receipt for receipt in manifest["receipts"]}
    assert set(routes) == {
        "ANSWER_REVIEWER",
        "COMPLIANCE_REVIEWER",
        "RESEARCH_REFUSAL_AUDITOR",
        "CLARIFY_REVIEWER",
        "DEFLECT_REVIEWER",
        "LIVE_BRAVE_RESEARCH_AUDITOR",
        "LIVE_BRAVE_REVIEWER",
    }

    for row in manifest["receipts"]:
        assert row["converter_exit"] == 0
        assert row["schema"] == "tau.subagent_receipt.v1"
        assert row["mocked"] is False
        assert row["live"] is True
        receipt = json.loads(
            (Path(__file__).resolve().parents[1] / row["subagent_receipt"]).read_text()
        )
        handoff = json.loads((Path(__file__).resolve().parents[1] / row["handoff"]).read_text())
        source_loop = json.loads(
            (Path(__file__).resolve().parents[1] / row["source_command_loop_receipt"]).read_text()
        )
        source_dispatch = source_loop["dispatches"][row["source_dispatch_index"]]
        source_stdout = source_dispatch["command_results"][0]["stdout"]

        assert json.loads(source_stdout) == handoff
        assert receipt["schema"] == "tau.subagent_receipt.v1"
        assert receipt["context"]["subagent"] == row["subagent"]
        assert receipt["context"]["actor_type"] == "tau"
        assert receipt["goal"]["immutable_goal_preserved"] is True
        assert receipt["result"]["status"] == row["result_status"]
        assert receipt["result"]["mocked"] is False
        assert receipt["result"]["live"] is True
        assert receipt["next"]["subagent"] == row["next_subagent"]
        assert receipt["next"]["executor"] == row["next_executor"]

    assert routes["RESEARCH_REFUSAL_AUDITOR"]["result_status"] == "REFUSED"
    assert routes["LIVE_BRAVE_RESEARCH_AUDITOR"]["next_subagent"] == "reviewer"
    assert routes["LIVE_BRAVE_REVIEWER"]["next_subagent"] == "human"


def test_live_memory_route_failclosed_manifest_matches_receipts() -> None:
    manifest = json.loads((LIVE_ROUTE_FAILCLOSED_PROOF_DIR / "manifest.json").read_text())

    assert manifest["schema"] == "tau.live_memory_route_failclosed_proof.v1"
    assert manifest["mocked"] is False
    assert manifest["live"] is True
    assert manifest["ok"] is True
    assert manifest["route_count"] == 5

    routes = {route["route"]: route for route in manifest["routes"]}
    assert set(routes) == {
        "CLARIFY",
        "DEFLECT",
        "ANSWER_SELECTOR_ATTEMPT",
        "ANSWER_DIRECT_PRODUCT",
        "RESEARCH_BRAVE_DISABLED",
    }

    assert routes["CLARIFY"]["selected_skill"] == "memory.clarify"
    assert routes["CLARIFY"]["memory_product_schema"] == "memory.clarify.v1"
    assert routes["CLARIFY"]["validation_errors"] == []
    assert routes["DEFLECT"]["selected_skill"] == "memory.deflect"
    assert routes["DEFLECT"]["memory_product_schema"] == "memory.deflect.v1"
    assert routes["DEFLECT"]["validation_errors"] == []
    assert routes["ANSWER_DIRECT_PRODUCT"]["memory_product_schema"] == "memory.answer.v1"
    assert routes["ANSWER_DIRECT_PRODUCT"]["branch_status"] == "PASS"
    assert routes["ANSWER_DIRECT_PRODUCT"]["fail_closed"] is False
    assert routes["RESEARCH_BRAVE_DISABLED"]["selected_skill"] == "brave-search"
    assert routes["RESEARCH_BRAVE_DISABLED"]["branch_status"] == "FAILED"
    assert routes["RESEARCH_BRAVE_DISABLED"]["fail_closed"] is True

    for route in manifest["routes"]:
        receipt = json.loads((Path(__file__).resolve().parents[1] / route["receipt"]).read_text())
        if route["route"] == "ANSWER_DIRECT_PRODUCT":
            assert receipt["schema"] == "tau.live_memory_answer_direct_product.v1"
            assert receipt["branch"]["payload"]["schema"] == "memory.answer.v1"
            continue
        assert receipt["schema"] == "tau.loop2_memory_skill_selector_harness.v1"
        assert receipt["mocked"] is False
        assert receipt["live"] is True
        assert receipt["selected_skill"] == route["selected_skill"]
        assert receipt["branch_status"] == route["branch_status"]
        assert receipt["fail_closed"] == route["fail_closed"]
