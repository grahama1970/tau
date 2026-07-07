import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.research_query_gate import RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA
from tau_coding.research_skill_adapter import (
    RESEARCH_SKILL_ADAPTER_RECEIPT_SCHEMA,
    write_research_skill_adapter_receipt,
)
from tau_coding.research_source_receipt import RESEARCH_SOURCE_RECEIPT_SCHEMA

_QUERY = "adaptive DAG research for Tau"


def test_research_adapter_requires_query_safety_receipt(tmp_path: Path) -> None:
    report = _write_report(tmp_path)

    receipt = write_research_skill_adapter_receipt(
        report_path=report,
        query_safety_receipt_path=tmp_path / "missing-query-safety.json",
        output_path=tmp_path / "research-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "query safety receipt did not pass" in receipt["errors"]
    assert receipt["course_correction"]["required_next_action"] == "run_research_query_gate"


def test_research_adapter_blocks_controlled_query_without_authorization(tmp_path: Path) -> None:
    report = _write_report(tmp_path, query="Search for controlled technical data examples")
    safety = _write_query_safety_receipt(
        tmp_path,
        query="Search for controlled technical data examples",
        ok=False,
        alert_codes=["research_authorization_invalid"],
    )

    receipt = write_research_skill_adapter_receipt(
        report_path=report,
        query_safety_receipt_path=safety,
        output_path=tmp_path / "research-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "query safety receipt did not pass" in receipt["errors"]


def test_research_adapter_hashes_dogpile_report(tmp_path: Path) -> None:
    report = _write_report(tmp_path)
    safety = _write_query_safety_receipt(tmp_path)

    receipt = write_research_skill_adapter_receipt(
        report_path=report,
        query_safety_receipt_path=safety,
        output_path=tmp_path / "research-adapter-receipt.json",
        repo_root=tmp_path,
    )

    source_receipt = json.loads((tmp_path / "research-source-receipt.json").read_text())
    assert receipt["schema"] == RESEARCH_SKILL_ADAPTER_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["source_report_sha256"] == f"sha256:{_sha256_file(report)}"
    assert receipt["research_source_receipt_status"] == "PASS"
    assert source_receipt["schema"] == RESEARCH_SOURCE_RECEIPT_SCHEMA
    assert source_receipt["source_count"] == 1


def test_research_adapter_extracts_dogpile_partial_results_shape(tmp_path: Path) -> None:
    report = _write_dogpile_partial_results(tmp_path)
    safety = _write_query_safety_receipt(tmp_path)

    receipt = write_research_skill_adapter_receipt(
        report_path=report,
        query_safety_receipt_path=safety,
        output_path=tmp_path / "research-adapter-receipt.json",
        repo_root=tmp_path,
    )

    source_packet = json.loads((tmp_path / "research-source-packet.json").read_text())
    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 3
    assert receipt["provider_counts"] == {"brave": 2, "stage2_github": 1}
    assert receipt["degraded_providers"][0]["provider"] == "youtube"
    assert source_packet["query"] == _QUERY
    assert source_packet["sources"][0]["provider"] == "brave"
    assert source_packet["sources"][2]["provider"] == "stage2_github"


def test_research_adapter_marks_research_as_design_input_not_closure(tmp_path: Path) -> None:
    report = _write_report(tmp_path)
    safety = _write_query_safety_receipt(tmp_path)

    receipt = write_research_skill_adapter_receipt(
        report_path=report,
        query_safety_receipt_path=safety,
        output_path=tmp_path / "research-adapter-receipt.json",
        repo_root=tmp_path,
    )

    source_packet = json.loads((tmp_path / "research-source-packet.json").read_text())
    assert receipt["classification"] == "design_input"
    assert source_packet["classification"] == "design_input"
    assert "The research is closure proof." in receipt["proof_scope"]["does_not_prove"]
    assert "Local Tau proof remains required before closure." in source_packet["limitations"]


def test_cli_research_skill_adapter_writes_receipt(tmp_path: Path) -> None:
    report = _write_report(tmp_path)
    safety = _write_query_safety_receipt(tmp_path)
    out = tmp_path / "research-adapter-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "research-skill-adapter",
            "--report",
            str(report),
            "--query-safety",
            str(safety),
            "--out",
            str(out),
            "--repo-root",
            str(tmp_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text())
    assert payload["schema"] == RESEARCH_SKILL_ADAPTER_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"


def _write_report(tmp_path: Path, *, query: str = _QUERY) -> Path:
    payload = {
        "schema": "dogpile.report.v1",
        "query": query,
        "retrieved_at": "2026-07-07T12:00:00Z",
        "summary": "Dogpile report for Tau skill composition.",
        "sources": [
            {
                "title": "Graph of Thoughts",
                "url": "https://arxiv.org/abs/2308.09687",
                "relevance": "HIGH",
                "claims_supported": ["graph-structured reasoning"],
            }
        ],
        "limitations": ["Research is design input only."],
    }
    path = tmp_path / "dogpile-report.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_dogpile_partial_results(tmp_path: Path) -> Path:
    payload = {
        "requested_query": _QUERY,
        "effective_query": _QUERY,
        "updated_at": "2026-07-07T12:00:00Z",
        "status": "completed",
        "results": {
            "stage1": {
                "brave": {
                    "results": [
                        {
                            "title": "NIST Secure Software Development Framework",
                            "description": "Secure development guidance.",
                            "url": "https://csrc.nist.gov/Projects/ssdf",
                        },
                        {
                            "title": "CISA Secure by Design",
                            "description": "Secure by design guidance.",
                            "url": "https://www.cisa.gov/securebydesign",
                        },
                    ]
                },
                "youtube": [
                    {
                        "title": "Error searching YouTube: yt-dlp not installed.",
                        "url": "",
                    }
                ],
            },
            "stage2": {
                "stage2_github": {
                    "github_details": [
                        {
                            "title": "example/repo",
                            "summary": "Repository with receipt-backed CI examples.",
                            "url": "https://github.com/example/repo",
                            "relevance": "HIGH",
                        }
                    ]
                }
            },
        },
        "final_report": "# Dogpile Report: adaptive DAG research for Tau\n\nSources found.",
    }
    path = tmp_path / "dogpile-partial-results.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_query_safety_receipt(
    tmp_path: Path,
    *,
    query: str = _QUERY,
    ok: bool = True,
    alert_codes: list[str] | None = None,
) -> Path:
    payload = {
        "schema": RESEARCH_QUERY_SAFETY_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "query_sha256": f"sha256:{hashlib.sha256(query.encode()).hexdigest()}",
        "method": "dogpile",
        "external_tool_called": False,
        "alert_codes": alert_codes or [],
    }
    path = tmp_path / "query-safety-receipt.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
