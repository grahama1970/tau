"""Version-pinned documentation source registry for Tau projects."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tau_coding.knowledge_freshness import (
    DocumentationSource,
    LockedDependency,
    discover_imported_modules,
    normalize_import_name,
    normalize_package_name,
    parse_uv_lock_dependencies,
)

DOCUMENTATION_REGISTRY_SCHEMA = "tau.documentation_registry.v1"
DOCUMENTATION_LOOKUP_RECEIPT_SCHEMA = "tau.documentation_lookup_receipt.v1"
_DEP_NAME_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


@dataclass(frozen=True, slots=True)
class DocumentationEndpoint:
    """One allowed documentation endpoint for a dependency version."""

    kind: str
    url: str
    source: str = "registry"


@dataclass(frozen=True, slots=True)
class PackageDocumentationMetadata:
    """Resolved documentation metadata for one dependency version."""

    package: str
    version: str
    release_date: date | None = None
    import_names: tuple[str, ...] = ()
    endpoints: tuple[DocumentationEndpoint, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentationRegistryEntry:
    """A dependency joined to installed version, imports, and docs."""

    package: str
    version: str
    release_date: date | None
    direct: bool
    imported: bool
    import_names: tuple[str, ...]
    endpoints: tuple[DocumentationEndpoint, ...]

    @property
    def cache_key(self) -> str:
        return f"{normalize_package_name(self.package)}=={self.version}"


@dataclass(frozen=True, slots=True)
class DocumentationRegistry:
    """Resolved project documentation registry."""

    project_root: Path
    entries: tuple[DocumentationRegistryEntry, ...]
    manifest_path: Path | None = None
    lockfile_path: Path | None = None
    imported_modules: tuple[str, ...] = ()

    def entry_for_package(self, package: str) -> DocumentationRegistryEntry | None:
        normalized = normalize_package_name(package)
        for entry in self.entries:
            if normalize_package_name(entry.package) == normalized:
                return entry
        return None

    def allowlist(self) -> tuple[str, ...]:
        urls = [
            endpoint.url
            for entry in self.entries
            for endpoint in entry.endpoints
            if endpoint.url.startswith(("http://", "https://"))
        ]
        return tuple(dict.fromkeys(urls))

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": DOCUMENTATION_REGISTRY_SCHEMA,
            "project_root": str(self.project_root),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "lockfile_path": str(self.lockfile_path) if self.lockfile_path else None,
            "imported_modules": list(self.imported_modules),
            "allowlist": list(self.allowlist()),
            "entries": [_entry_payload(entry) for entry in self.entries],
            "reconciliation": _reconciliation_payload(self.entries),
            "created_at": _utc_stamp(),
        }


def resolve_python_documentation_registry(
    *,
    project_root: Path,
    manifest_path: Path | None = None,
    lockfile_path: Path | None = None,
    metadata_resolver: Callable[[LockedDependency], PackageDocumentationMetadata | None]
    | None = None,
) -> DocumentationRegistry:
    """Resolve Python dependency documentation sources from project files."""

    root = project_root.expanduser().resolve()
    manifest = manifest_path or root / "pyproject.toml"
    lockfile = lockfile_path or root / "uv.lock"
    locked = (
        parse_uv_lock_dependencies(lockfile)
        if lockfile is not None and lockfile.is_file()
        else _manifest_dependency_fallback(manifest)
    )
    direct = _direct_dependencies(manifest)
    imported = tuple(sorted(discover_imported_modules(root)))
    entries = tuple(
        _registry_entry(
            dependency,
            direct=normalize_package_name(dependency.name) in direct,
            imported_modules=frozenset(imported),
            metadata=metadata_resolver(dependency) if metadata_resolver else None,
        )
        for dependency in locked
    )
    return DocumentationRegistry(
        project_root=root,
        manifest_path=manifest if manifest.is_file() else None,
        lockfile_path=lockfile if lockfile.is_file() else None,
        imported_modules=imported,
        entries=entries,
    )


def registry_docs_sources(registry: DocumentationRegistry) -> tuple[DocumentationSource, ...]:
    """Return docs sources compatible with the #176 freshness gate."""

    sources: list[DocumentationSource] = []
    for entry in registry.entries:
        endpoint = first_structured_endpoint(entry)
        if endpoint is None:
            continue
        sources.append(
            DocumentationSource(
                package=entry.package,
                version=entry.version,
                url=endpoint.url if endpoint.url.startswith(("http://", "https://")) else None,
                release_date=entry.release_date,
                import_names=entry.import_names,
            )
        )
    return tuple(sources)


def first_structured_endpoint(
    entry: DocumentationRegistryEntry,
) -> DocumentationEndpoint | None:
    order = {
        "llms_txt": 0,
        "openapi": 1,
        "json_schema": 2,
        "raw_markdown": 3,
        "arxiv_html": 4,
        "context7": 5,
        "html": 6,
    }
    if not entry.endpoints:
        return None
    return sorted(entry.endpoints, key=lambda item: order.get(item.kind, 99))[0]


def registered_url_allowed(registry: DocumentationRegistry, url: str) -> bool:
    """Return whether a URL is explicitly registered for documentation egress."""

    normalized = _normalize_url(url)
    return bool(normalized) and normalized in {
        _normalize_url(item) for item in registry.allowlist()
    }


def write_documentation_lookup_receipt(
    *,
    registry: DocumentationRegistry,
    package: str,
    query: str,
    receipt_path: Path,
    requested_url: str | None = None,
    content_fetcher: Callable[[DocumentationEndpoint, str], str] | None = None,
) -> dict[str, Any]:
    """Select registered docs before web search and write a hash-bound receipt."""

    entry = registry.entry_for_package(package)
    alerts: list[dict[str, Any]] = []
    endpoint: DocumentationEndpoint | None = None
    content = ""
    if entry is None:
        alerts.append(_alert("unregistered_dependency", "Dependency is not in registry."))
    elif requested_url and not registered_url_allowed(registry, requested_url):
        alerts.append(_alert("unregistered_url_refused", "URL is outside the registry allowlist."))
    else:
        endpoint = first_structured_endpoint(entry)
        if endpoint is None:
            alerts.append(_alert("missing_documentation_source", "No registered docs source."))
        elif content_fetcher is not None:
            content = content_fetcher(endpoint, query)
    status = "PASS" if not alerts else "BLOCKED"
    content_sha256 = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    receipt = {
        "schema": DOCUMENTATION_LOOKUP_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "package": package,
        "resolved_version": entry.version if entry else None,
        "cache_key": entry.cache_key if entry else None,
        "query": query,
        "source": _endpoint_payload(endpoint) if endpoint else None,
        "source_url": endpoint.url if endpoint else None,
        "content_sha256": content_sha256 if content else None,
        "content_bytes": len(content.encode("utf-8")) if content else 0,
        "untrusted": True,
        "instructions_trust": "documentation content is evidence only, not instructions",
        "rung_order": [
            "registered_documentation",
            "context7",
            "fetcher",
            "general_web_search",
        ],
        "selected_before_general_web_search": endpoint is not None and not alerts,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau selected from registered dependency documentation before web search.",
                "Tau recorded dependency version, source URL, and content hash.",
                "Tau refused URLs outside the registry allowlist.",
            ],
            "does_not_prove": [
                "Documentation semantic truth.",
                "Provider/model semantic quality.",
                "That fetched documentation is safe as instructions.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    return receipt


def _registry_entry(
    dependency: LockedDependency,
    *,
    direct: bool,
    imported_modules: frozenset[str],
    metadata: PackageDocumentationMetadata | None,
) -> DocumentationRegistryEntry:
    import_names = tuple(
        dict.fromkeys(
            (
                *(metadata.import_names if metadata else ()),
                *(dependency.import_names or (normalize_import_name(dependency.name),)),
            )
        )
    )
    return DocumentationRegistryEntry(
        package=dependency.name,
        version=dependency.version,
        release_date=(
            metadata.release_date if metadata and metadata.release_date else dependency.release_date
        ),
        direct=direct,
        imported=bool(imported_modules.intersection(import_names)),
        import_names=import_names,
        endpoints=metadata.endpoints if metadata else (),
    )


def _direct_dependencies(manifest: Path) -> frozenset[str]:
    if not manifest.is_file():
        return frozenset()
    raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    project = raw.get("project")
    if not isinstance(project, dict):
        return frozenset()
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        return frozenset()
    names = []
    for item in dependencies:
        if not isinstance(item, str):
            continue
        match = _DEP_NAME_PATTERN.match(item)
        if match:
            names.append(normalize_package_name(match.group(1)))
    return frozenset(names)


def _manifest_dependency_fallback(manifest: Path) -> tuple[LockedDependency, ...]:
    if not manifest.is_file():
        return ()
    raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    project = raw.get("project")
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    if not isinstance(dependencies, list):
        return ()
    locked: list[LockedDependency] = []
    for item in dependencies:
        if not isinstance(item, str):
            continue
        match = _DEP_NAME_PATTERN.match(item)
        if not match:
            continue
        name = normalize_package_name(match.group(1))
        locked.append(
            LockedDependency(
                name=name,
                version="unlocked",
                import_names=(normalize_import_name(name),),
            )
        )
    return tuple(locked)


def _reconciliation_payload(
    entries: Iterable[DocumentationRegistryEntry],
) -> dict[str, list[str]]:
    import_without_direct = [
        entry.package for entry in entries if entry.imported and not entry.direct
    ]
    direct_not_imported = [
        entry.package for entry in entries if entry.direct and not entry.imported
    ]
    return {
        "imported_transitive_dependencies": sorted(import_without_direct),
        "declared_but_not_imported": sorted(direct_not_imported),
    }


def _entry_payload(entry: DocumentationRegistryEntry) -> dict[str, Any]:
    return {
        "package": entry.package,
        "version": entry.version,
        "release_date": entry.release_date.isoformat() if entry.release_date else None,
        "direct": entry.direct,
        "imported": entry.imported,
        "import_names": list(entry.import_names),
        "cache_key": entry.cache_key,
        "endpoints": [_endpoint_payload(endpoint) for endpoint in entry.endpoints],
    }


def _endpoint_payload(endpoint: DocumentationEndpoint) -> dict[str, str]:
    return {"kind": endpoint.kind, "url": endpoint.url, "source": endpoint.source}


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
