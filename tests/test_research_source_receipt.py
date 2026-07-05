import json
from pathlib import Path

from tau_coding.research_source_receipt import write_research_source_receipt


def test_research_source_receipt_accepts_source_bearing_arxiv_packet(tmp_path: Path) -> None:
    source_path = tmp_path / "research-source-packet.json"
    receipt_path = tmp_path / "research-source-receipt.json"
    source_path.write_text(json.dumps(_valid_source_packet()), encoding="utf-8")

    receipt = write_research_source_receipt(source_path=source_path, receipt_path=receipt_path)
    written = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert receipt == written
    assert receipt["schema"] == "tau.research_source_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 2
    assert receipt["arxiv_source_count"] == 2
    assert receipt["review_required"] is True
    assert receipt["classification"] == "design_input"
    assert receipt["errors"] == []


def test_research_source_receipt_blocks_missing_source_metadata(tmp_path: Path) -> None:
    source_path = tmp_path / "bad-research-source-packet.json"
    receipt_path = tmp_path / "research-source-receipt.json"
    packet = _valid_source_packet()
    packet["retrieved_at"] = "not-a-date"
    packet["sources"] = [{"title": "", "url": "", "relevance": "MAYBE", "claims_supported": "no"}]
    source_path.write_text(json.dumps(packet), encoding="utf-8")

    receipt = write_research_source_receipt(source_path=source_path, receipt_path=receipt_path)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "retrieved_at must be an ISO-8601 timestamp string" in receipt["errors"]
    assert "sources[0].title must be a non-empty string" in receipt["errors"]
    assert "sources[0].url must be a non-empty string" in receipt["errors"]
    assert "sources[0].relevance must be one of ['HIGH', 'LOW', 'MEDIUM']" in receipt["errors"]
    assert "sources[0].claims_supported must be a list of strings" in receipt["errors"]


def test_research_source_receipt_blocks_arxiv_packet_without_arxiv_source(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "bad-arxiv-research-source-packet.json"
    receipt_path = tmp_path / "research-source-receipt.json"
    packet = _valid_source_packet()
    packet["sources"] = [
        {
            "title": "Generic web article mislabeled as ArXiv",
            "url": "https://example.com/research",
            "relevance": "HIGH",
            "claims_supported": ["generic claim"],
        }
    ]
    source_path.write_text(json.dumps(packet), encoding="utf-8")

    receipt = write_research_source_receipt(source_path=source_path, receipt_path=receipt_path)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "sources[0].arxiv_id is required when method is arxiv" in receipt["errors"]
    assert "sources[0].url must cite arxiv.org when method is arxiv" in receipt["errors"]


def _valid_source_packet() -> dict:
    return {
        "schema": "tau.research_source_packet.v1",
        "source_type": "paper",
        "method": "arxiv",
        "query": "adaptive DAG multi-agent routing references for Tau",
        "retrieved_at": "2026-07-05T13:40:00Z",
        "classification": "design_input",
        "sources": [
            {
                "title": "Graph of Thoughts: Solving Elaborate Problems with Large Language Models",
                "url": "https://arxiv.org/abs/2308.09687",
                "arxiv_id": "2308.09687",
                "relevance": "HIGH",
                "claims_supported": ["graph-structured reasoning inspiration"],
            },
            {
                "title": "Adaptive Graph of Thoughts: Test-Time Adaptive Reasoning",
                "url": "https://arxiv.org/abs/2502.05078",
                "arxiv_id": "2502.05078",
                "relevance": "HIGH",
                "claims_supported": ["bounded dynamic DAG expansion inspiration"],
            },
        ],
        "summary": "Primary ArXiv papers that informed Tau adaptive DAG references.",
        "limitations": [
            "Research is design input only.",
            "Local Tau tests and receipts remain required before closure.",
        ],
    }
