"""tau.run_ledger.v1: tamper-evident build/verify + agentic-eval admission (#327)."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

from tau_coding import run_ledger as rl


def _sample_entries():
    return [
        {"schema": "tau.dag_live_event.v1", "node": "start", "status": "scheduled"},
        {"schema": "tau.dag_live_event.v1", "node": "coder", "status": "pass"},
        rl.admit_agentic_eval({
            "skill": "surf", "readiness": "READY", "live": True, "mocked": False, "trial_count": 2,
            "cases": [{"name": "c1", "type": "adversarial", "real_world": True,
                       "trials": [{"outcome": "PASS"}, {"outcome": "PASS"}]}],
        }),
    ]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _init_agentic_eval_repo(root: Path) -> None:
    (root / "evals").mkdir()
    (root / "local" / "agentic-evals").mkdir(parents=True)
    _write_json(
        root / "evals" / "demo_agentic_eval.json",
        {
            "version": 2,
            "skill": "demo",
            "cases": [{"name": "positive", "command": ["true"]}],
        },
    )
    artifact = root / "local" / "agentic-evals" / "demo-proof.json"
    _write_json(artifact, {"schema": "tau.demo_proof.v1", "ok": True})
    _write_json(
        root / "local" / "agentic-evals" / "demo-agentic-evals-report.json",
        {
            "schema": "agentic_evals.report.v2",
            "source": "evals/demo_agentic_eval.json",
            "fixture_sha256": "sha256:fixture",
            "repo": {"sha": "source-sha-a", "ref": "main"},
            "mocked": False,
            "live": True,
            "skill": "demo",
            "readiness": "READY",
            "case_count": 1,
            "trial_count": 2,
            "cases": [
                {
                    "name": "positive",
                    "argv": ["bash", "-lc", "true"],
                    "trials": [
                        {
                            "trial_id": "t0",
                            "artifact_hashes": {
                                "../local/agentic-evals/demo-proof.json": _sha256(artifact)
                            },
                            "outcome": "PASS",
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        root / "evals" / "other_agentic_eval.json",
        {"version": 2, "skill": "other", "cases": []},
    )
    _write_json(
        root / "local" / "agentic-evals" / "other-agentic-evals-report.json",
        {
            "schema": "agentic_evals.report.v2",
            "source": "evals/other_agentic_eval.json",
            "fixture_sha256": "sha256:other",
            "repo": {"sha": "source-sha-b", "ref": "main"},
            "mocked": False,
            "live": True,
            "skill": "other",
            "readiness": "READY",
            "case_count": 1,
            "trial_count": 2,
            "cases": [],
        },
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )


def test_intact_ledger_verifies():
    led = rl.build_ledger(
        _sample_entries(), goal_hash="sha256:goalA", run_id="run-1", dag_id="dag-1"
    )
    v = rl.verify_ledger(led)
    assert v["ok"] is True and v["first_bad_index"] is None
    assert led["entry_count"] == 3 and led["head_hash"].startswith("sha256:")


def test_tampered_entry_fails_and_names_index():
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1")
    tampered = copy.deepcopy(led)
    tampered["entries"][1]["payload"]["status"] = "fail"  # silent edit of entry 1
    v = rl.verify_ledger(tampered)
    assert v["ok"] is False
    assert v["first_bad_index"] == 1
    assert v["reason"] == "entry_hash_mismatch"


def test_deleted_entry_breaks_sequence():
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1")
    tampered = copy.deepcopy(led)
    del tampered["entries"][1]  # drop an entry
    v = rl.verify_ledger(tampered)
    assert v["ok"] is False and v["first_bad_index"] == 1


def test_cross_run_splice_rejected():
    # An entry chain from goalA must not verify under goalB (goal binding).
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1")
    spliced = copy.deepcopy(led)
    spliced["goal_hash"] = "sha256:goalB"
    v = rl.verify_ledger(spliced)
    assert v["ok"] is False


def test_agentic_eval_admitted_with_boundary():
    e = rl.admit_agentic_eval({"skill": "ask", "readiness": "READY", "live": True, "mocked": False,
                               "trial_count": 3,
                               "cases": [{"name": "x", "trials": [{"outcome": "PASS"}]}]})
    assert e["schema"] == rl.AGENTIC_EVAL_RECEIPT_SCHEMA
    assert e["readiness"] == "READY" and e["live"] is True and e["mocked"] is False
    assert e["cases"][0]["passed"] is True


def test_trace_tamper_fails_when_trace_present():
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1")
    tampered = copy.deepcopy(led)
    tampered["trace"]["entry_count"] = 999
    v = rl.verify_ledger(tampered)
    assert v["ok"] is False
    assert v["reason"] == "trace_mismatch"


def test_run_dir_ledger_includes_artifact_digests_and_trace(tmp_path):
    progress_path = tmp_path / "dag-progress.json"
    progress_path.write_text(
        json.dumps({"schema": "tau.dag_progress.v1", "ok": True}), encoding="utf-8"
    )
    source_path = tmp_path / "source-dag.json"
    source_path.write_text(
        json.dumps({"schema": "tau.dag_contract.v1", "dag_id": "dag-1"}), encoding="utf-8"
    )
    receipt_path = tmp_path / "dag-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_receipt.v1",
                "dag_id": "dag-1",
                "active_goal_hash": "sha256:goalA",
                "scheduler_events": [
                    {"event": "node_started", "node_id": "coder", "attempt": 1},
                    {"event": "node_completed", "node_id": "coder", "attempt": 1},
                ],
                "dispatches": [
                    {
                        "schema": "tau.agent_handoff_dispatch_receipt.v1",
                        "selected_agent": "coder",
                        "status": "COMPLETED",
                        "mocked": False,
                        "live": True,
                    }
                ],
                "progress_path": str(progress_path),
                "artifacts": [str(source_path)],
            }
        ),
        encoding="utf-8",
    )
    ledger = rl.build_run_ledger_from_run_dir(tmp_path)
    assert rl.verify_ledger(ledger)["ok"] is True
    assert ledger["trace"]["schema"] == rl.RUN_LEDGER_TRACE_SCHEMA
    assert ledger["trace"]["entry_kind_counts"]["artifact_digest"] == 2
    assert ledger["trace"]["artifact_count"] == 2
    paths = {row["path"] for row in ledger["trace"]["artifact_digests"]}
    assert paths == {"dag-progress.json", "source-dag.json"}


def test_generic_run_dir_ledger_includes_events_nodes_and_progress(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema": "tau.generic_dag_event.v1",
                        "kind": "node_dispatch",
                        "run_id": "generic-run",
                        "node_id": "build",
                        "attempt": 1,
                    }
                ),
                json.dumps(
                    {
                        "schema": "tau.generic_dag_event.v1",
                        "kind": "node_receipt_validated",
                        "run_id": "generic-run",
                        "node_id": "build",
                        "attempt": 1,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "source-dag.json").write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "generic-run",
                "goal_hash": "sha256:goalA",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "dag-progress.json").write_text(
        json.dumps({"schema": "tau.dag_progress.v1", "status": "PASS"}),
        encoding="utf-8",
    )
    (tmp_path / "run-receipt.json").write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_run_receipt.v1",
                "run_id": "generic-run",
                "scheduler_run_id": "generic-run",
                "status": "PASS",
                "events_jsonl": str(events_path),
                "nodes": [
                    {
                        "node_id": "build",
                        "attempt": 1,
                        "attempt_id": "attempt-build",
                        "status": "PASS",
                        "verdict": "PASS",
                        "mocked": False,
                        "live": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    ledger = rl.build_run_ledger_from_run_dir(tmp_path)

    assert rl.verify_ledger(ledger)["ok"] is True
    assert ledger["source_receipt_path"].endswith("run-receipt.json")
    assert ledger["trace"]["entry_kind_counts"]["generic_dag_event"] == 2
    assert ledger["trace"]["entry_kind_counts"]["generic_node_receipt"] == 1
    assert {
        row["path"] for row in ledger["trace"]["artifact_digests"]
    } >= {"events.jsonl", "source-dag.json", "dag-progress.json"}
    assert any(
        row["node_id"] == "build"
        and {"node_dispatch", "node_receipt_validated"} <= set(row["events"])
        for row in ledger["trace"]["node_attempts"]
    )


def test_agentic_eval_evidence_index_verifies_clean_retained_reports(tmp_path):
    _init_agentic_eval_repo(tmp_path)
    index_path = tmp_path.parent / "index.json"
    index = rl.build_agentic_eval_ledger_evidence_index(tmp_path, output_path=index_path)

    result = rl.verify_agentic_eval_ledger_evidence_index(
        index_path,
        tmp_path,
        require_clean=True,
        require_current_sha=True,
    )

    assert index["schema"] == rl.AGENTIC_EVAL_EVIDENCE_INDEX_SCHEMA
    assert index["report_count"] == 2
    assert index["artifact_count"] == 1
    assert result["ok"] is True
    assert result["retained_reports_live_readback"] == {
        "mocked": False,
        "all_unmocked": True,
        "live": True,
        "ready": True,
        "count": 2,
    }


def test_agentic_eval_evidence_index_rejects_mutated_report(tmp_path):
    _init_agentic_eval_repo(tmp_path)
    index_path = tmp_path.parent / "index.json"
    rl.build_agentic_eval_ledger_evidence_index(tmp_path, output_path=index_path)
    report = tmp_path / "local" / "agentic-evals" / "demo-agentic-evals-report.json"
    report.write_text(report.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = rl.verify_agentic_eval_ledger_evidence_index(
        index_path,
        tmp_path,
        require_clean=False,
        require_current_sha=False,
    )

    assert result["ok"] is False
    assert "report_digest_mismatch" in result["failure_codes"]


def test_agentic_eval_evidence_index_rejects_substituted_report(tmp_path):
    _init_agentic_eval_repo(tmp_path)
    index_path = tmp_path.parent / "index.json"
    rl.build_agentic_eval_ledger_evidence_index(tmp_path, output_path=index_path)
    report = tmp_path / "local" / "agentic-evals" / "demo-agentic-evals-report.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["repo"]["sha"] = "substituted-from-another-sha"
    _write_json(report, payload)

    result = rl.verify_agentic_eval_ledger_evidence_index(
        index_path,
        tmp_path,
        require_clean=False,
        require_current_sha=False,
    )

    assert result["ok"] is False
    assert "report_digest_mismatch" in result["failure_codes"]
    assert "report_repo_sha_mismatch" in result["failure_codes"]


def test_agentic_eval_evidence_index_rejects_deleted_artifact(tmp_path):
    _init_agentic_eval_repo(tmp_path)
    index_path = tmp_path.parent / "index.json"
    rl.build_agentic_eval_ledger_evidence_index(tmp_path, output_path=index_path)
    (tmp_path / "local" / "agentic-evals" / "demo-proof.json").unlink()

    result = rl.verify_agentic_eval_ledger_evidence_index(
        index_path,
        tmp_path,
        require_clean=False,
        require_current_sha=False,
    )

    assert result["ok"] is False
    assert "artifact_missing" in result["failure_codes"]


def test_agentic_eval_evidence_index_rejects_dirty_tree(tmp_path):
    _init_agentic_eval_repo(tmp_path)
    index_path = tmp_path.parent / "index.json"
    rl.build_agentic_eval_ledger_evidence_index(tmp_path, output_path=index_path)
    report = tmp_path / "local" / "agentic-evals" / "demo-agentic-evals-report.json"
    report.write_text(report.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = rl.verify_agentic_eval_ledger_evidence_index(
        index_path,
        tmp_path,
        require_clean=True,
        require_current_sha=False,
    )

    assert result["ok"] is False
    assert "dirty_tree_mismatch" in result["failure_codes"]
