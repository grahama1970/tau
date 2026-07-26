import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.gs001_closure_publisher import (
    SCHEMA,
    publish_gs001_closure_receipt,
)


GOAL_HASH = "sha256:ca56881acd36f5fdffffafc2a2ee73bbfd806df820f2fe0bd50ec52e794308ca"


def test_gs001_closure_publisher_writes_current_goal_terminal_receipt(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    output = tmp_path / "tau-closure-publisher-receipt.json"

    receipt = publish_gs001_closure_receipt(
        repo_root=repo,
        dag_contract_path=Path(".tau/gs001-execution-dag.json"),
        closure_state_path=Path(
            "artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z"
            "/gs001-closure-state.json"
        ),
        terminal_receipt_path=Path(
            "artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z"
            "/terminal-receipt.json"
        ),
        visual_receipt_path=Path(
            "artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z"
            "/receipts/gs001-closure-page-visual-receipt.json"
        ),
        output_path=output,
        expected_goal_hash=GOAL_HASH,
    )

    assert receipt["schema"] == SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["goal_hash"] == GOAL_HASH
    assert receipt["terminal_status"] == "pending_human"
    assert receipt["source_commit"] == _git(repo, "rev-parse", "HEAD")
    refs = {item["kind"]: item for item in receipt["references"]}
    assert refs["closure_state_json"]["repo_relative_path"].endswith("gs001-closure-state.json")
    assert refs["closure_page_html"]["sha256"].startswith("sha256:")
    assert refs["visual_receipt_json"]["bytes"] > 0
    assert refs["visual_screenshot_png"]["bytes"] > 0
    assert json.loads(output.read_text(encoding="utf-8")) == receipt


def test_cli_gs001_closure_publisher_marks_old_goal_hash_stale(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    output = tmp_path / "stale-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "gs001-closure-publish",
            "--repo-root",
            str(repo),
            "--dag",
            ".tau/gs001-execution-dag.json",
            "--closure-state",
            (
                "artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z"
                "/gs001-closure-state.json"
            ),
            "--terminal-receipt",
            (
                "artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z"
                "/terminal-receipt.json"
            ),
            "--visual-receipt",
            (
                "artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z"
                "/receipts/gs001-closure-page-visual-receipt.json"
            ),
            "--out",
            str(output),
            "--expected-goal-hash",
            "sha256:old-goal",
        ],
    )

    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert result.exit_code == 1
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["terminal_status"] == "stale_goal_hash"
    assert receipt["errors"] == ["stale_goal_hash"]
    assert json.loads(result.output)["errors"] == ["stale_goal_hash"]


def _write_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    run = repo / "artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z"
    receipts = run / "receipts"
    receipts.mkdir(parents=True)
    (repo / ".tau").mkdir()
    _write_json(
        repo / ".tau/gs001-execution-dag.json",
        {
            "schema": "tau.dag_contract.v1",
            "dag_id": "gs001-extraction-repair-v1",
            "goal": {
                "goal_id": "PDF-EXTRACTION-GS001-TAU-V1",
                "goal_version": 1,
                "goal_hash": GOAL_HASH,
            },
            "target": {"repo": "grahama1970/pdf_oxide", "target": "GS001"},
            "entry_node": "closure-audit",
            "terminal_nodes": ["closure-publisher"],
            "limits": {"max_total_attempts": 2},
            "required_evidence": [],
            "fail_closed_on": [
                "anti_overfit_inspection_failed",
                "expected_contract_not_locked",
                "patch_path_violation",
            ],
            "nodes": [
                {"id": "closure-audit", "agent": "reviewer", "executor": "local"},
                {"id": "closure-publisher", "agent": "releaser", "executor": "local"},
            ],
            "edges": [{"from": "closure-audit", "to": "closure-publisher"}],
        },
    )
    _write_json(
        run / "gs001-closure-state.json",
        {
            "schema_version": "pdf_oxide.gs001.closure_state.v1",
            "goal_id": "PDF-EXTRACTION-GS001-TAU-V1",
            "goal_hash": GOAL_HASH,
            "dag_goal_hash": GOAL_HASH,
            "counts": {"criteria_total": 10, "criteria_pass": 8},
            "criteria": [{"criterion": 10, "status": "PENDING_HUMAN"}],
            "blocking_items": [{"criterion": 10, "status": "PENDING_HUMAN"}],
            "terminal_reason": "human_acceptance_pending",
        },
    )
    (run / "gs001-closure-page.html").write_text("<h1>GS001 Closure</h1>\n", encoding="utf-8")
    (receipts / "gs001-closure-page.png").write_bytes(b"png")
    _write_json(
        run / "terminal-receipt.json",
        {
            "schema_version": "pdf_oxide.gs001.terminal_receipt.v1",
            "goal_hash": GOAL_HASH,
            "terminal_status": "pending",
            "terminal_reason": "human_acceptance_pending",
        },
    )
    _write_json(
        receipts / "gs001-closure-page-visual-receipt.json",
        {
            "schema_version": "pdf_oxide.gs001.visual_closure_receipt.v1",
            "ok": True,
            "mocked": False,
            "live": False,
            "screenshot_path": str(receipts / "gs001-closure-page.png"),
        },
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Tau Test",
            "-c",
            "user.email=tau@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return repo


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
