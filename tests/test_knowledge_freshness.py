from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from tau_coding.knowledge_freshness import (
    DocumentationEvidence,
    DocumentationSource,
    KnowledgeFreshnessOptions,
    LockedDependency,
    compute_knowledge_freshness,
    parse_uv_lock_dependencies,
)
from tau_coding.project_dag import run_project_dag_contract


def test_knowledge_freshness_computes_stale_set_and_reuses_version_cache(
    tmp_path: Path,
) -> None:
    fetches: list[str] = []

    def fetcher(url: str, timeout_seconds: float) -> DocumentationEvidence:
        fetches.append(f"{url}:{timeout_seconds:g}")
        return DocumentationEvidence(
            package="httpx",
            version="0.28.1",
            status="fetched",
            source="fixture-http",
            cache_hit=False,
            url=url,
            sha256="sha256:docs",
            bytes=42,
        )

    options = KnowledgeFreshnessOptions(
        project_root=tmp_path,
        docs_sources=(
            DocumentationSource(
                package="httpx",
                version="0.28.1",
                url="https://www.python-httpx.org/",
                release_date=date(2024, 12, 6),
                import_names=("httpx",),
            ),
        ),
        cache_dir=tmp_path / "cache",
        network_fetch=False,
    )
    dependencies = (
        LockedDependency(
            name="httpx",
            version="0.28.1",
            release_date=date(2024, 12, 6),
            import_names=("httpx",),
        ),
        LockedDependency(
            name="rich",
            version="13.9.4",
            release_date=date(2024, 10, 1),
            import_names=("rich",),
        ),
        LockedDependency(
            name="textual",
            version="1.0.0",
            release_date=None,
            import_names=("textual",),
        ),
    )

    first = compute_knowledge_freshness(
        model="model-a",
        model_knowledge_cutoff=date(2024, 11, 1),
        dependencies=dependencies,
        imported_modules=("httpx", "rich"),
        options=options,
        fetcher=fetcher,
    )
    second = compute_knowledge_freshness(
        model="model-a",
        model_knowledge_cutoff=date(2024, 11, 1),
        dependencies=dependencies,
        imported_modules=("httpx", "rich"),
        options=options,
        fetcher=fetcher,
    )

    assert [item.name for item in first.stale_dependencies] == ["httpx"]
    assert first.provenance["status"] == "fetched_documentation"
    assert fetches == ["https://www.python-httpx.org/:10"]
    assert second.documentation[0].cache_hit is True
    assert len(fetches) == 1


def test_knowledge_freshness_treats_unknown_cutoff_and_release_as_stale(
    tmp_path: Path,
) -> None:
    unknown_cutoff = compute_knowledge_freshness(
        model="unknown",
        model_knowledge_cutoff=None,
        dependencies=(
            LockedDependency(
                name="mystery",
                version="1.0.0",
                release_date=None,
                import_names=("mystery",),
            ),
        ),
        imported_modules=("mystery",),
        options=KnowledgeFreshnessOptions(project_root=tmp_path, network_fetch=False),
    )
    unknown_release = compute_knowledge_freshness(
        model="known",
        model_knowledge_cutoff=date(2024, 1, 1),
        dependencies=(
            LockedDependency(
                name="mystery",
                version="1.0.0",
                release_date=None,
                import_names=("mystery",),
            ),
        ),
        imported_modules=("mystery",),
        options=KnowledgeFreshnessOptions(project_root=tmp_path, network_fetch=False),
    )

    assert unknown_cutoff.provenance["status"] == "unverified_knowledge"
    assert (
        unknown_cutoff.stale_dependencies[0].stale_reason == "unknown_model_knowledge_cutoff"
    )
    assert unknown_cutoff.alert_codes == ("unverified_knowledge",)
    assert (
        unknown_release.stale_dependencies[0].stale_reason == "unknown_dependency_release_date"
    )


def test_uv_lock_parser_reads_upload_dates(tmp_path: Path) -> None:
    lockfile = tmp_path / "uv.lock"
    lockfile.write_text(
        """
[[package]]
name = "httpx"
version = "0.28.1"
sdist = { upload-time = "2024-12-06T15:37:23.222Z" }
""",
        encoding="utf-8",
    )

    dependencies = parse_uv_lock_dependencies(lockfile)

    assert dependencies[0].name == "httpx"
    assert dependencies[0].release_date == date(2024, 12, 6)


def test_project_dag_writes_knowledge_receipt_before_node_dispatch(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("import httpx\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        """
[[package]]
name = "httpx"
version = "0.28.1"
sdist = { upload-time = "2024-12-06T15:37:23.222Z" }
""",
        encoding="utf-8",
    )
    spec_path = tmp_path / "specs" / "coder" / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True)
    code = (
        "import json, sys; "
        "payload=json.load(sys.stdin); "
        "kp=payload['context']['knowledge_provenance']; "
        "response={"
        "'schema':'tau.agent_handoff.v1',"
        "'github':{'repo':'grahama1970/tau','target':'knowledge-fixture'},"
        "'goal':payload['goal'],"
        "'previous_subagent':'coder',"
        "'context':{'summary':'saw knowledge provenance','artifacts':[]},"
        "'result':{'status':'PASS','summary':kp['status'],"
        "'evidence':[{'kind':'provider_route_receipt','ok':True,'live':True,"
        "'goal_hash':payload['goal']['goal_hash']}]},"
        "'rationale':'freshness receipt was available before generation',"
        "'next_agent':{'name':'human','executor':'human','reason':'terminal'},"
        "'required_evidence':[],"
        "'stop_condition':'terminal'"
        "}; print(json.dumps(response))"
    )
    spec_path.write_text(
        json.dumps({"command": [sys.executable, "-c", code], "cwd": str(tmp_path)}),
        encoding="utf-8",
    )
    contract_path = tmp_path / "dag.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "knowledge-freshness-test",
                "goal": {
                    "goal_id": "knowledge",
                    "goal_version": 1,
                    "goal_hash": "sha256:knowledge",
                },
                "target": {"repo": "grahama1970/tau", "target": "knowledge-fixture"},
                "entry_node": "coder",
                "terminal_nodes": ["human"],
                "limits": {"default_timeout_seconds": 5, "max_total_attempts": 1},
                "knowledge_freshness": {
                    "enabled": True,
                    "project_root": str(tmp_path),
                    "network_fetch": False,
                    "docs_sources": [
                        {
                            "package": "httpx",
                            "version": "0.28.1",
                            "url": "https://www.python-httpx.org/",
                            "release_date": "2024-12-06",
                            "import_names": ["httpx"],
                        }
                    ],
                },
                "nodes": [
                    {
                        "id": "coder",
                        "agent": "coder",
                        "executor": "local",
                        "max_attempts": 1,
                        "command_spec": str(spec_path),
                        "model_policy": {
                            "provider": "openai",
                            "auth": "configured",
                            "model": "fixture-model",
                            "knowledge_cutoff": "2024-11-01",
                        },
                        "prompt_contract": {"system": "s", "user": "u"},
                        "required_evidence": ["provider_route_receipt"],
                    }
                ],
                "edges": [{"from": "coder", "to": "human"}],
                "required_evidence": [],
                "fail_closed_on": [],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "PASS"
    knowledge_receipt = Path(receipt["knowledge_freshness_receipts"][0])
    payload = json.loads(knowledge_receipt.read_text(encoding="utf-8"))
    assert payload["knowledge_provenance"]["status"] == "unverified_knowledge"
    assert receipt["knowledge_provenance_by_node"]["coder"]["stale_dependency_count"] == 1
    assert str(knowledge_receipt) in receipt["knowledge_freshness_receipts"]
