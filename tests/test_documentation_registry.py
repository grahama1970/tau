from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tau_coding.documentation_registry import (
    DocumentationEndpoint,
    PackageDocumentationMetadata,
    registered_url_allowed,
    registry_docs_sources,
    resolve_python_documentation_registry,
    write_documentation_lookup_receipt,
)
from tau_coding.security_context import resolve_security_context


def test_documentation_registry_resolves_lock_versions_imports_and_sources(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path, httpx_version="0.28.1")

    registry = resolve_python_documentation_registry(
        project_root=tmp_path,
        metadata_resolver=_metadata,
    )

    httpx = registry.entry_for_package("httpx")
    assert httpx is not None
    assert httpx.version == "0.28.1"
    assert httpx.release_date == date(2024, 12, 6)
    assert httpx.direct is True
    assert httpx.imported is True
    assert httpx.cache_key == "httpx==0.28.1"
    assert httpx.endpoints[0].kind == "llms_txt"
    assert registry.allowlist() == ("https://www.python-httpx.org/llms.txt",)
    assert registry.to_payload()["reconciliation"]["declared_but_not_imported"] == ["rich"]
    assert registry_docs_sources(registry)[0].version == "0.28.1"


def test_documentation_lookup_receipt_uses_registered_source_before_web_search(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path, httpx_version="0.28.1")
    registry = resolve_python_documentation_registry(
        project_root=tmp_path,
        metadata_resolver=_metadata,
    )
    receipt_path = tmp_path / "lookup.json"

    receipt = write_documentation_lookup_receipt(
        registry=registry,
        package="httpx",
        query="streaming response API",
        receipt_path=receipt_path,
        content_fetcher=lambda endpoint, query: f"{endpoint.url}\n{query}\n",
    )

    assert receipt["status"] == "PASS"
    assert receipt["resolved_version"] == "0.28.1"
    assert receipt["source_url"] == "https://www.python-httpx.org/llms.txt"
    assert receipt["content_sha256"].startswith("sha256:")
    assert receipt["selected_before_general_web_search"] is True
    assert receipt["untrusted"] is True


def test_documentation_lookup_refuses_unregistered_url(tmp_path: Path) -> None:
    _write_python_project(tmp_path, httpx_version="0.28.1")
    registry = resolve_python_documentation_registry(
        project_root=tmp_path,
        metadata_resolver=_metadata,
    )

    receipt = write_documentation_lookup_receipt(
        registry=registry,
        package="httpx",
        query="anything",
        requested_url="https://example.com/not-registered",
        receipt_path=tmp_path / "blocked.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["alert_codes"] == ["unregistered_url_refused"]
    assert not registered_url_allowed(registry, "https://example.com/not-registered")


def test_version_bump_changes_registry_cache_key_and_source(tmp_path: Path) -> None:
    _write_python_project(tmp_path, httpx_version="0.28.1")
    old = resolve_python_documentation_registry(
        project_root=tmp_path,
        metadata_resolver=_metadata,
    ).entry_for_package("httpx")
    _write_python_project(tmp_path, httpx_version="0.29.0")
    new = resolve_python_documentation_registry(
        project_root=tmp_path,
        metadata_resolver=_metadata,
    ).entry_for_package("httpx")

    assert old is not None
    assert new is not None
    assert old.cache_key == "httpx==0.28.1"
    assert new.cache_key == "httpx==0.29.0"
    assert old.endpoints[0].url != new.endpoints[0].url


def test_documentation_registry_drives_environment_network_allowlist(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path, httpx_version="0.28.1")
    registry = resolve_python_documentation_registry(
        project_root=tmp_path,
        metadata_resolver=_metadata,
    )
    result = resolve_security_context(
        dag_contract={
            "dag_id": "docs",
            "goal": {"goal_hash": "sha256:docs"},
            "documentation_registry": registry.to_payload(),
        },
        contract_path=tmp_path / "dag.json",
        receipt_dir=tmp_path / "run",
    )

    manifest = json.loads(result.environment_manifest_path.read_text(encoding="utf-8"))
    assert manifest["network_policy"] == "allowlisted"
    assert manifest["network_allowlist"] == ["https://www.python-httpx.org/llms.txt"]


def _write_python_project(tmp_path: Path, *, httpx_version: str) -> None:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("import httpx\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = [
  "httpx>=0.27",
  "rich>=13",
]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        f"""
[[package]]
name = "httpx"
version = "{httpx_version}"
sdist = {{ upload-time = "2024-12-06T15:37:23.222Z" }}

[[package]]
name = "rich"
version = "13.9.4"
sdist = {{ upload-time = "2024-10-01T00:00:00.000Z" }}
""",
        encoding="utf-8",
    )


def _metadata(dependency) -> PackageDocumentationMetadata:  # type: ignore[no-untyped-def]
    if dependency.name == "httpx":
        return PackageDocumentationMetadata(
            package="httpx",
            version=dependency.version,
            release_date=date(2024, 12, 6),
            import_names=("httpx",),
            endpoints=(
                DocumentationEndpoint(
                    kind="llms_txt",
                    url=f"https://www.python-httpx.org/{dependency.version}/llms.txt",
                )
                if dependency.version != "0.28.1"
                else DocumentationEndpoint(
                    kind="llms_txt",
                    url="https://www.python-httpx.org/llms.txt",
                ),
            ),
        )
    return PackageDocumentationMetadata(
        package=dependency.name,
        version=dependency.version,
        import_names=(dependency.name,),
    )
