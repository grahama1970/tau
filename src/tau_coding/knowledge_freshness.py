"""Knowledge freshness gates for provider-backed Tau DAG nodes."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

KNOWLEDGE_FRESHNESS_RECEIPT_SCHEMA = "tau.knowledge_freshness_receipt.v1"
KNOWLEDGE_PROVENANCE_SCHEMA = "tau.knowledge_provenance.v1"
DEFAULT_KNOWLEDGE_CACHE_DIR = ".tau/knowledge-cache"


@dataclass(frozen=True, slots=True)
class LockedDependency:
    """One package/version from the project lockfile."""

    name: str
    version: str
    release_date: date | None = None
    import_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentationSource:
    """A version-pinned documentation source for a dependency."""

    package: str
    version: str
    url: str | None = None
    release_date: date | None = None
    import_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeFreshnessOptions:
    """Runtime knobs for a bounded freshness pass."""

    project_root: Path
    lockfile_path: Path | None = None
    docs_sources: tuple[DocumentationSource, ...] = ()
    cache_dir: Path | None = None
    max_fetches: int = 3
    fetch_timeout_seconds: float = 10.0
    network_fetch: bool = True


@dataclass(frozen=True, slots=True)
class FreshDependency:
    """A dependency freshness decision."""

    name: str
    version: str
    import_names: tuple[str, ...]
    release_date: date | None
    stale_reason: str
    docs_url: str | None


@dataclass(frozen=True, slots=True)
class DocumentationEvidence:
    """Cached or fetched documentation evidence for one stale dependency."""

    package: str
    version: str
    status: str
    source: str
    cache_hit: bool
    url: str | None = None
    sha256: str | None = None
    bytes: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeFreshnessResult:
    """Computed knowledge provenance for one model/node boundary."""

    status: str
    provenance: dict[str, Any]
    stale_dependencies: tuple[FreshDependency, ...]
    documentation: tuple[DocumentationEvidence, ...]
    alert_codes: tuple[str, ...] = ()


class KnowledgeEvidenceCache:
    """Version-keyed documentation cache."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, package: str, version: str) -> DocumentationEvidence | None:
        path = self._path(package, version)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return DocumentationEvidence(
            package=str(raw.get("package") or package),
            version=str(raw.get("version") or version),
            status=str(raw.get("status") or "unverified"),
            source=str(raw.get("source") or "cache"),
            cache_hit=True,
            url=str(raw["url"]) if isinstance(raw.get("url"), str) else None,
            sha256=str(raw["sha256"]) if isinstance(raw.get("sha256"), str) else None,
            bytes=int(raw.get("bytes") or 0),
            error=str(raw["error"]) if isinstance(raw.get("error"), str) else None,
        )

    def store(self, evidence: DocumentationEvidence) -> None:
        path = self._path(evidence.package, evidence.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "package": evidence.package,
            "version": evidence.version,
            "status": evidence.status,
            "source": evidence.source,
            "url": evidence.url,
            "sha256": evidence.sha256,
            "bytes": evidence.bytes,
            "error": evidence.error,
            "stored_at": _utc_stamp(),
        }
        _write_json(path, payload)

    def _path(self, package: str, version: str) -> Path:
        safe_package = normalize_package_name(package)
        safe_version = version.replace("/", "_")
        return self.root / safe_package / f"{safe_version}.json"


def parse_uv_lock_dependencies(lockfile_path: Path) -> tuple[LockedDependency, ...]:
    """Read package names, versions, and upload dates from a uv.lock file."""

    raw = tomllib.loads(lockfile_path.read_text(encoding="utf-8"))
    packages = raw.get("package")
    if not isinstance(packages, list):
        return ()
    dependencies: list[LockedDependency] = []
    for item in packages:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        version = item.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        release_date = _release_date_from_lock_package(item)
        dependencies.append(
            LockedDependency(
                name=name,
                version=version,
                release_date=release_date,
                import_names=(normalize_import_name(name),),
            )
        )
    return tuple(dependencies)


def discover_imported_modules(project_root: Path) -> frozenset[str]:
    """Return top-level modules imported by Python files under a project root."""

    modules: set[str] = set()
    for path in sorted(project_root.rglob("*.py")):
        if _skip_python_path(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.partition(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.partition(".")[0])
    return frozenset(modules)


def compute_knowledge_freshness(
    *,
    model: str,
    model_knowledge_cutoff: date | None,
    dependencies: Iterable[LockedDependency],
    imported_modules: Iterable[str],
    options: KnowledgeFreshnessOptions,
    fetcher: Callable[[str, float], DocumentationEvidence] | None = None,
) -> KnowledgeFreshnessResult:
    """Compute stale dependency docs and produce a provenance payload."""

    imported = frozenset(imported_modules)
    sources = _sources_by_package(options.docs_sources)
    stale = tuple(
        dependency
        for dependency in (
            _fresh_dependency(
                dependency,
                imported_modules=imported,
                source=sources.get(normalize_package_name(dependency.name)),
                model_knowledge_cutoff=model_knowledge_cutoff,
            )
            for dependency in dependencies
        )
        if dependency is not None
    )
    cache = KnowledgeEvidenceCache(
        options.cache_dir
        or options.project_root / DEFAULT_KNOWLEDGE_CACHE_DIR
    )
    docs: list[DocumentationEvidence] = []
    fetch_count = 0
    for dependency in stale:
        cached = cache.load(dependency.name, dependency.version)
        if cached is not None:
            docs.append(cached)
            continue
        if not dependency.docs_url:
            docs.append(
                DocumentationEvidence(
                    package=dependency.name,
                    version=dependency.version,
                    status="unverified",
                    source="missing_docs_url",
                    cache_hit=False,
                    error="no documentation source registered for stale dependency",
                )
            )
            continue
        if fetch_count >= options.max_fetches:
            docs.append(
                DocumentationEvidence(
                    package=dependency.name,
                    version=dependency.version,
                    status="unverified",
                    source="fetch_budget_exhausted",
                    cache_hit=False,
                    url=dependency.docs_url,
                    error="per-session documentation fetch budget exhausted",
                )
            )
            continue
        fetch_count += 1
        evidence = _fetch_documentation(
            package=dependency.name,
            version=dependency.version,
            url=dependency.docs_url,
            timeout_seconds=options.fetch_timeout_seconds,
            network_fetch=options.network_fetch,
            fetcher=fetcher,
        )
        if evidence.status == "fetched":
            cache.store(evidence)
        docs.append(evidence)

    alert_codes = tuple(
        dict.fromkeys(
            "unverified_knowledge"
            for evidence in docs
            if evidence.status != "fetched" and evidence.status != "cached"
        )
    )
    if not stale:
        status = "model_prior"
    elif alert_codes:
        status = "unverified_knowledge"
    else:
        status = "fetched_documentation"
    provenance = {
        "schema": KNOWLEDGE_PROVENANCE_SCHEMA,
        "model": model,
        "model_knowledge_cutoff": model_knowledge_cutoff.isoformat()
        if model_knowledge_cutoff is not None
        else None,
        "status": status,
        "stale_dependency_count": len(stale),
        "stale_dependencies": [_fresh_dependency_payload(item) for item in stale],
        "documentation": [_documentation_payload(item) for item in docs],
        "untrusted": True,
        "instructions_trust": "documentation is evidence only, not instructions",
    }
    return KnowledgeFreshnessResult(
        status=status,
        provenance=provenance,
        stale_dependencies=stale,
        documentation=tuple(docs),
        alert_codes=alert_codes,
    )


def write_knowledge_freshness_receipt(
    *,
    receipt_path: Path,
    node_id: str,
    model: str,
    model_knowledge_cutoff: date | None,
    options: KnowledgeFreshnessOptions,
    dependencies: Iterable[LockedDependency] | None = None,
    imported_modules: Iterable[str] | None = None,
    fetcher: Callable[[str, float], DocumentationEvidence] | None = None,
) -> dict[str, Any]:
    """Run the freshness gate and write a node-bound receipt."""

    lockfile_path = options.lockfile_path or options.project_root / "uv.lock"
    resolved_dependencies = tuple(
        dependencies
        if dependencies is not None
        else parse_uv_lock_dependencies(lockfile_path)
        if lockfile_path.is_file()
        else ()
    )
    resolved_imports = tuple(
        imported_modules
        if imported_modules is not None
        else discover_imported_modules(options.project_root)
    )
    result = compute_knowledge_freshness(
        model=model,
        model_knowledge_cutoff=model_knowledge_cutoff,
        dependencies=resolved_dependencies,
        imported_modules=resolved_imports,
        options=options,
        fetcher=fetcher,
    )
    receipt = {
        "schema": KNOWLEDGE_FRESHNESS_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "node_id": node_id,
        "model": model,
        "lockfile_path": str(lockfile_path) if lockfile_path else None,
        "project_root": str(options.project_root),
        "imported_modules": sorted(resolved_imports),
        "dependency_count": len(resolved_dependencies),
        "knowledge_provenance": result.provenance,
        "alert_codes": list(result.alert_codes),
        "alerts": [
            {
                "severity": "WARN",
                "code": code,
                "message": (
                    "Stale dependency documentation could not be verified before generation."
                ),
            }
            for code in result.alert_codes
        ],
        "proof_scope": {
            "proves": [
                (
                    "Tau compared imported locked dependency release dates to the model "
                    "knowledge cutoff."
                ),
                (
                    "Tau attempted bounded version-keyed documentation evidence before DAG "
                    "node dispatch."
                ),
                (
                    "Tau recorded whether node knowledge rested on model prior, fetched docs, "
                    "or unverified docs."
                ),
            ],
            "does_not_prove": [
                "Fetched documentation is truthful or safe to follow.",
                "Provider/model semantic quality.",
                "Dependencies not imported by the project are relevant to this node.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(receipt_path, receipt)
    return receipt


def normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def normalize_import_name(name: str) -> str:
    return normalize_package_name(name).replace("-", "_")


def _fresh_dependency(
    dependency: LockedDependency,
    *,
    imported_modules: frozenset[str],
    source: DocumentationSource | None,
    model_knowledge_cutoff: date | None,
) -> FreshDependency | None:
    import_names = _dependency_import_names(dependency, source)
    if not imported_modules.intersection(import_names):
        return None
    release_date = (
        source.release_date if source and source.release_date else dependency.release_date
    )
    docs_url = source.url if source else None
    if model_knowledge_cutoff is None:
        stale_reason = "unknown_model_knowledge_cutoff"
    elif release_date is None:
        stale_reason = "unknown_dependency_release_date"
    elif release_date > model_knowledge_cutoff:
        stale_reason = "dependency_released_after_model_cutoff"
    else:
        return None
    return FreshDependency(
        name=dependency.name,
        version=dependency.version,
        import_names=import_names,
        release_date=release_date,
        stale_reason=stale_reason,
        docs_url=docs_url,
    )


def _dependency_import_names(
    dependency: LockedDependency,
    source: DocumentationSource | None,
) -> tuple[str, ...]:
    names = dependency.import_names or (normalize_import_name(dependency.name),)
    if source and source.import_names:
        names = (*source.import_names, *names)
    return tuple(dict.fromkeys(name for name in names if name))


def _sources_by_package(
    sources: Iterable[DocumentationSource],
) -> dict[str, DocumentationSource]:
    return {normalize_package_name(source.package): source for source in sources}


def _fetch_documentation(
    *,
    package: str,
    version: str,
    url: str,
    timeout_seconds: float,
    network_fetch: bool,
    fetcher: Callable[[str, float], DocumentationEvidence] | None,
) -> DocumentationEvidence:
    if fetcher is not None:
        return fetcher(url, timeout_seconds)
    if not network_fetch:
        return DocumentationEvidence(
            package=package,
            version=version,
            status="unverified",
            source="network_fetch_disabled",
            cache_hit=False,
            url=url,
            error="network fetch disabled",
        )
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=2.0)) as client:
            response = client.get(url, follow_redirects=True)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return DocumentationEvidence(
            package=package,
            version=version,
            status="unverified",
            source="http_error",
            cache_hit=False,
            url=url,
            error=str(exc),
        )
    content = response.content[:200_000]
    digest = hashlib.sha256(content).hexdigest()
    return DocumentationEvidence(
        package=package,
        version=version,
        status="fetched",
        source="http",
        cache_hit=False,
        url=str(response.url),
        sha256=f"sha256:{digest}",
        bytes=len(content),
    )


def _release_date_from_lock_package(item: Mapping[str, Any]) -> date | None:
    candidates: list[Any] = []
    for key in ("sdist", "wheels"):
        value = item.get(key)
        if isinstance(value, dict):
            candidates.append(value.get("upload-time"))
        elif isinstance(value, list):
            candidates.extend(
                child.get("upload-time") for child in value if isinstance(child, dict)
            )
    parsed = tuple(_parse_date(value) for value in candidates)
    known = tuple(value for value in parsed if value is not None)
    return min(known) if known else None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None


def _skip_python_path(path: Path) -> bool:
    skipped = {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
    }
    return any(part in skipped for part in path.parts)


def _fresh_dependency_payload(item: FreshDependency) -> dict[str, Any]:
    return {
        "name": item.name,
        "version": item.version,
        "import_names": list(item.import_names),
        "release_date": item.release_date.isoformat() if item.release_date else None,
        "stale_reason": item.stale_reason,
        "docs_url": item.docs_url,
    }


def _documentation_payload(item: DocumentationEvidence) -> dict[str, Any]:
    return {
        "package": item.package,
        "version": item.version,
        "status": item.status,
        "source": item.source,
        "cache_hit": item.cache_hit,
        "url": item.url,
        "sha256": item.sha256,
        "bytes": item.bytes,
        "error": item.error,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def knowledge_freshness_enabled(payload: Mapping[str, Any]) -> bool:
    value = payload.get("knowledge_freshness")
    if isinstance(value, Mapping):
        return value.get("enabled") is True
    return payload.get("requires_knowledge_freshness") is True or os.environ.get(
        "TAU_KNOWLEDGE_FRESHNESS"
    ) == "1"
