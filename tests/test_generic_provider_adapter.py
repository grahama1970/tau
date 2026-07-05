import hashlib
from pathlib import Path

from tau_coding.cli import _parse_generic_provider_dag_node_cli_args
from tau_coding.generic_dag import GENERIC_DAG_NODE_RECEIPT_SCHEMA
from tau_coding.generic_provider_adapter import (
    PROVIDER_DAG_WORK_ORDER_SCHEMA,
    build_generic_provider_node_receipt,
    run_generic_provider_dag_node,
)


def test_build_generic_provider_node_receipt_pass() -> None:
    receipt = build_generic_provider_node_receipt(
        node_id="provider-task",
        provider_receipt={
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "live": True,
            "run_dir": "/tmp/provider-run",
            "events_jsonl": "/tmp/provider-run/events.jsonl",
        },
    )

    assert receipt["schema"] == GENERIC_DAG_NODE_RECEIPT_SCHEMA
    assert receipt["node_id"] == "provider-task"
    assert receipt["status"] == "PASS"
    assert receipt["verdict"] == "PASS"
    assert receipt["provider_live"] is True
    assert receipt["errors"] == []
    assert {artifact["kind"] for artifact in receipt["artifacts"]} == {
        "run_dir",
        "events_jsonl",
    }


def test_build_generic_provider_node_receipt_carries_work_order_hash(tmp_path: Path) -> None:
    work_order = tmp_path / "work-order.json"
    work_order.write_text('{"task":"provider work"}\n', encoding="utf-8")

    receipt = build_generic_provider_node_receipt(
        node_id="provider-task",
        provider_receipt={
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "live": True,
        },
        work_order_path=work_order,
    )

    expected = hashlib.sha256(work_order.read_bytes()).hexdigest()
    assert receipt["work_order_path"] == str(work_order.resolve())
    assert receipt["work_order_sha256"] == expected
    assert receipt["provider_binding"]["status"] == "LEGACY_UNBOUND"


def test_build_generic_provider_node_receipt_binds_canonical_work_order(
    tmp_path: Path,
) -> None:
    visible_log = tmp_path / "coder.visible.txt"
    visible_log.write_text("visible provider output\n", encoding="utf-8")
    work_order = tmp_path / "work-order.json"
    work_order.write_text(
        """{
  "schema": "tau.provider_dag_work_order.v1",
  "dag_id": "dag-001",
  "goal": {
    "goal_id": "goal",
    "goal_version": 1,
    "goal_hash": "sha256:goal"
  },
  "node": {
    "node_id": "provider-task",
    "agent": "coder",
    "attempt": 1,
    "max_attempts": 2
  },
  "target": {
    "repo": "grahama1970/tau",
    "allowed_paths": [],
    "scratch_worktree": "/tmp/tau-scratch"
  },
  "herdr": {
    "workspace_id": "w1",
    "pane_id": "w1:p3",
    "terminal_id": "term-coder"
  },
  "required_evidence": [],
  "forbidden_actions": [],
  "receipt_path": "/tmp/provider-task-receipt.json"
}
""",
        encoding="utf-8",
    )

    receipt = build_generic_provider_node_receipt(
        node_id="provider-task",
        provider_receipt={
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "live": True,
            "provider_sessions": {
                "codex": {
                    "workspace_id": "w1",
                    "pane_id": "w1:p3",
                    "terminal_id": "term-coder",
                    "visible_log_path": str(visible_log),
                }
            },
        },
        work_order_path=work_order,
    )

    expected_work_order_sha = hashlib.sha256(work_order.read_bytes()).hexdigest()
    expected_visible_log_sha = hashlib.sha256(visible_log.read_bytes()).hexdigest()
    assert receipt["status"] == "PASS"
    assert receipt["provider_binding"] == {
        "schema": "tau.provider_dag_node_binding.v1",
        "status": "PASS",
        "work_order_schema": PROVIDER_DAG_WORK_ORDER_SCHEMA,
        "work_order_path": str(work_order.resolve()),
        "work_order_sha256": expected_work_order_sha,
        "dag_id": "dag-001",
        "goal_hash": "sha256:goal",
        "node_id": "provider-task",
        "attempt": 1,
        "workspace_id": "w1",
        "pane_id": "w1:p3",
        "terminal_id": "term-coder",
        "visible_log_path": str(visible_log),
        "visible_log_sha256": expected_visible_log_sha,
        "errors": [],
    }
    assert receipt["dag_id"] == "dag-001"
    assert receipt["goal_hash"] == "sha256:goal"
    assert receipt["attempt"] == 1
    assert receipt["workspace_id"] == "w1"
    assert receipt["visible_log_sha256"] == expected_visible_log_sha


def test_build_generic_provider_node_receipt_blocks_work_order_node_mismatch(
    tmp_path: Path,
) -> None:
    visible_log = tmp_path / "coder.visible.txt"
    visible_log.write_text("visible provider output\n", encoding="utf-8")
    work_order = tmp_path / "work-order.json"
    work_order.write_text(
        """{
  "schema": "tau.provider_dag_work_order.v1",
  "dag_id": "dag-001",
  "goal": {
    "goal_hash": "sha256:goal"
  },
  "node": {
    "node_id": "other-node",
    "attempt": 1
  },
  "herdr": {
    "workspace_id": "w1",
    "pane_id": "w1:p3",
    "terminal_id": "term-coder"
  }
}
""",
        encoding="utf-8",
    )

    receipt = build_generic_provider_node_receipt(
        node_id="provider-task",
        provider_receipt={
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "live": True,
            "visible_subagents": {
                "coder": {
                    "workspace_id": "w1",
                    "pane_id": "w1:p3",
                    "terminal_id": "term-coder",
                    "visible_log_path": str(visible_log),
                }
            },
        },
        work_order_path=work_order,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "BLOCKED"
    assert receipt["provider_binding"]["status"] == "BLOCKED"
    assert receipt["errors"] == [
        "work_order_node_id_mismatch: expected 'provider-task', got 'other-node'"
    ]


def test_build_generic_provider_node_receipt_blocks_missing_visible_log(
    tmp_path: Path,
) -> None:
    work_order = tmp_path / "work-order.json"
    work_order.write_text(
        """{
  "schema": "tau.provider_dag_work_order.v1",
  "dag_id": "dag-001",
  "goal": {
    "goal_hash": "sha256:goal"
  },
  "node": {
    "node_id": "provider-task",
    "attempt": 1
  },
  "herdr": {
    "workspace_id": "w1",
    "pane_id": "w1:p3",
    "terminal_id": "term-coder"
  }
}
""",
        encoding="utf-8",
    )

    receipt = build_generic_provider_node_receipt(
        node_id="provider-task",
        provider_receipt={
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "live": True,
            "provider_sessions": {
                "codex": {
                    "workspace_id": "w1",
                    "pane_id": "w1:p3",
                    "terminal_id": "term-coder",
                }
            },
        },
        work_order_path=work_order,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["provider_binding"]["status"] == "BLOCKED"
    assert receipt["errors"] == ["provider_receipt_missing_visible_log_path"]


def test_build_generic_provider_node_receipt_blocks_provider_verdicts() -> None:
    receipt = build_generic_provider_node_receipt(
        node_id="provider-task",
        provider_receipt={
            "ok": False,
            "status": "BLOCKED",
            "verdict": "REVIEWER_RECEIPT_INVALID",
            "live": True,
            "errors": ["reviewer receipt missing"],
        },
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "BLOCKED"
    assert receipt["provider_verdict"] == "REVIEWER_RECEIPT_INVALID"
    assert receipt["errors"] == [
        "reviewer receipt missing",
        "provider DAG verdict: REVIEWER_RECEIPT_INVALID",
    ]


def test_run_generic_provider_dag_node_writes_generic_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_provider_dag(**kwargs):
        return {
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "live": True,
            "run_dir": str(kwargs["run_root"] / "provider-run"),
            "events_jsonl": str(kwargs["run_root"] / "provider-run" / "events.jsonl"),
        }

    monkeypatch.setattr(
        "tau_coding.generic_provider_adapter.run_provider_dag_poc",
        fake_provider_dag,
    )
    receipt_path = tmp_path / "node-receipt.json"

    receipt = run_generic_provider_dag_node(
        node_id="provider-task",
        receipt_path=receipt_path,
        provider_run_root=tmp_path / "provider-runs",
        repo=tmp_path,
    )

    assert receipt["status"] == "PASS"
    assert receipt_path.exists()


def test_parse_generic_provider_dag_node_accepts_work_order_path(tmp_path: Path) -> None:
    options = _parse_generic_provider_dag_node_cli_args(
        [
            "--node-id",
            "provider-task",
            "--receipt-path",
            str(tmp_path / "receipt.json"),
            "--work-order-path",
            str(tmp_path / "work-order.json"),
            "--provider-run-root",
            str(tmp_path / "provider-runs"),
        ]
    )

    assert options["work_order_path"] == tmp_path / "work-order.json"
