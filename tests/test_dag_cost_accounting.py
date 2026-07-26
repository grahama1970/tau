import json
import sys
from pathlib import Path

from tau_coding.dag_viewer.projection import build_dag_live_snapshot, load_dag_replay
from tau_coding.generic_dag import (
    GENERIC_DAG_NODE_RECEIPT_SCHEMA,
    GENERIC_DAG_SPEC_SCHEMA,
    run_generic_dag,
)


def test_generic_dag_reports_per_node_run_and_viewer_cost_accounting(
    tmp_path: Path,
) -> None:
    spec_path = _write_costed_spec(
        tmp_path,
        [
            _node(tmp_path, "writer", _usage(100, 40, 10, 5, 0.012)),
            _node(tmp_path, "reviewer", _usage(30, 8, 2, 1, 0.004)),
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path, resume=False)

    assert receipt["status"] == "PASS"
    assert receipt["cost_accounting"]["source"] == "provider_reported_estimate"
    assert receipt["cost_accounting"]["input_tokens"] == 130
    assert receipt["cost_accounting"]["output_tokens"] == 48
    assert receipt["cost_accounting"]["cache_read_tokens"] == 12
    assert receipt["cost_accounting"]["cache_write_tokens"] == 6
    assert receipt["cost_accounting"]["total_tokens"] == 196
    assert receipt["cost_accounting"]["estimated_cost_usd"] == 0.016
    assert receipt["cost_accounting"]["estimated_cost_is_billing_truth"] is False
    assert sum(node["cost_accounting"]["input_tokens"] for node in receipt["nodes"]) == 130
    assert sum(node["cost_accounting"]["output_tokens"] for node in receipt["nodes"]) == 48
    assert sum(node["cost_accounting"]["cache_read_tokens"] for node in receipt["nodes"]) == 12
    assert sum(node["cost_accounting"]["cache_write_tokens"] for node in receipt["nodes"]) == 6
    assert (
        round(sum(node["cost_accounting"]["estimated_cost_usd"] for node in receipt["nodes"]), 12)
        == receipt["cost_accounting"]["estimated_cost_usd"]
    )

    replay, events = load_dag_replay(run_dir=tmp_path)
    snapshot = build_dag_live_snapshot(replay=replay, recent_events=events)
    assert snapshot["run_summary"]["cost_accounting"]["estimated_cost_usd"] == 0.016
    costs_by_node = {
        node["node_id"]: node["result"]["cost_accounting"] for node in snapshot["nodes"]
    }
    assert costs_by_node["writer"]["input_tokens"] == 100
    assert costs_by_node["reviewer"]["estimated_cost_usd"] == 0.004


def test_generic_dag_budget_exceeded_blocks_and_resumes_after_raise(
    tmp_path: Path,
) -> None:
    spec_path = _write_costed_spec(
        tmp_path,
        [
            _node(tmp_path, "writer", _usage(100, 40, 0, 0, 0.008)),
            _node(tmp_path, "reviewer", _usage(30, 8, 0, 0, 0.007), depends_on=["writer"]),
        ],
        budget=0.01,
    )

    blocked = run_generic_dag(spec_path=spec_path, resume=False)

    assert blocked["status"] == "BLOCKED"
    assert blocked["verdict"] == "BUDGET_EXCEEDED"
    assert blocked["completed_node_count"] == 1
    reviewer = blocked["nodes"][1]
    assert reviewer["node_id"] == "reviewer"
    assert reviewer["status"] == "BLOCKED"
    assert reviewer["verdict"] == "BUDGET_EXCEEDED"
    assert reviewer["errors"] == ["budget_exceeded"]
    assert reviewer["budget_blocker"]["code"] == "budget_exceeded"
    assert reviewer["budget_blocker"]["allowed_estimated_cost_usd"] == 0.01
    assert reviewer["budget_blocker"]["consumed_estimated_cost_usd"] == 0.015

    (tmp_path / "budget-override.json").write_text(
        json.dumps({"estimated_cost_usd": 0.02}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    resumed = run_generic_dag(spec_path=spec_path, resume=True)

    assert resumed["status"] == "PASS"
    assert resumed["cost_accounting"]["budget"]["state"] == "WITHIN_BUDGET"
    assert resumed["cost_accounting"]["estimated_cost_usd"] == 0.015
    assert resumed["replayed_event_count"] > 0
    assert [node["resumed"] for node in resumed["nodes"]] == [True, True]


def _write_costed_spec(
    tmp_path: Path,
    nodes: list[dict[str, object]],
    *,
    budget: float | None = None,
) -> Path:
    spec: dict[str, object] = {
        "schema": GENERIC_DAG_SPEC_SCHEMA,
        "run_id": "run-cost-accounting-test",
        "run_dir": str(tmp_path),
        "events_jsonl": str(tmp_path / "events.jsonl"),
        "nodes": nodes,
    }
    if budget is not None:
        spec["budget"] = {"estimated_cost_usd": budget}
    spec_path = tmp_path / "dag-spec.json"
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    return spec_path


def _node(
    tmp_path: Path,
    node_id: str,
    usage: dict[str, object],
    *,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    receipt_path = tmp_path / "receipts" / f"{node_id}.json"
    return {
        "node_id": node_id,
        "role": node_id,
        "depends_on": depends_on or [],
        "receipt_path": str(receipt_path),
        "timeout_seconds": 20,
        "max_attempts": 1,
        "command": [
            sys.executable,
            "-c",
            _receipt_writer_code(receipt_path, node_id=node_id, usage=usage),
        ],
    }


def _usage(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    estimated_cost_usd: float,
) -> dict[str, object]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "estimated_cost_usd": estimated_cost_usd,
    }


def _receipt_writer_code(
    receipt_path: Path,
    *,
    node_id: str,
    usage: dict[str, object],
) -> str:
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "usage": usage,
        "artifacts": [],
        "commands_run": ["python fixture receipt writer"],
        "handoff_summary": f"{node_id} passed",
        "errors": [],
        "policy_exceptions": [],
    }
    return (
        "import json; "
        "from pathlib import Path; "
        f"path = Path({str(receipt_path)!r}); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        f"path.write_text(json.dumps({payload!r}, sort_keys=True), encoding='utf-8')"
    )
