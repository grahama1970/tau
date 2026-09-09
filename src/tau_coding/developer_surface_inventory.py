"""Developer-visible stub/degraded surface inventory (#351)."""

# ruff: noqa: E501 - inventory strings are source-backed dispositions.

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

STUB_INVENTORY_SCHEMA = "tau.developer_surface_inventory.v1"

_MARKER_RE = re.compile(r"(_stub\b|\bstub\b|placeholder_|placeholder|NotImplemented|degraded)", re.I)

_AUDITED_FILES = {
    "src/tau_coding/media_explainer_orchestration.py",
    "src/tau_coding/dag_runtime/memory_projection.py",
    "src/tau_coding/acceptance_bundle.py",
    "src/tau_coding/governed_skill_execution.py",
    "src/tau_coding/project_status.py",
    "src/tau_coding/persona_dream_panel_agent.py",
}

_KNOWN = [
    {
        "file": "src/tau_coding/media_explainer_orchestration.py",
        "symbol": "_TOOL_ROUTES",
        "marker": "deterministic_vlm_contract_stub",
        "classification": "EXPERIMENTAL_EXPLICIT",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau media-explainer-smoke",
        "goal_relevance": "outside_immutable_goal",
        "disposition": "Smoke-only path: command name says smoke and receipts set mocked=true/live=false.",
    },
    {
        "file": "src/tau_coding/media_explainer_orchestration.py",
        "symbol": "_TOOL_ROUTES",
        "marker": "deterministic_watch_contract_stub",
        "classification": "EXPERIMENTAL_EXPLICIT",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau media-explainer-smoke",
        "goal_relevance": "outside_immutable_goal",
        "disposition": "Smoke-only path: command name says smoke and receipts set mocked=true/live=false.",
    },
    {
        "file": "src/tau_coding/media_explainer_orchestration.py",
        "symbol": "_TOOL_ROUTES",
        "marker": "deterministic_audio_caption_contract_stub",
        "classification": "EXPERIMENTAL_EXPLICIT",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau media-explainer-smoke",
        "goal_relevance": "outside_immutable_goal",
        "disposition": "Smoke-only path: command name says smoke and receipts set mocked=true/live=false.",
    },
    {
        "file": "src/tau_coding/media_explainer_orchestration.py",
        "symbol": "_TOOL_ROUTES",
        "marker": "deterministic_text_summary_contract_stub",
        "classification": "EXPERIMENTAL_EXPLICIT",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau media-explainer-smoke",
        "goal_relevance": "outside_immutable_goal",
        "disposition": "Smoke-only path: command name says smoke and receipts set mocked=true/live=false.",
    },
    {
        "file": "src/tau_coding/media_explainer_orchestration.py",
        "symbol": "run_media_explainer_smoke",
        "marker": "placeholder_receipts_only",
        "classification": "EXPERIMENTAL_EXPLICIT",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau media-explainer-smoke",
        "goal_relevance": "outside_immutable_goal",
        "disposition": "Receipt explicitly reports placeholder_receipts_only and mocked=true/live=false.",
    },
    {
        "file": "src/tau_coding/media_explainer_orchestration.py",
        "symbol": "_dispatch_asset",
        "marker": "SKIPPED_PLACEHOLDER",
        "classification": "EXPERIMENTAL_EXPLICIT",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau media-explainer-smoke",
        "goal_relevance": "outside_immutable_goal",
        "disposition": "Optional missing media assets emit SKIPPED_PLACEHOLDER, not live accepted work.",
    },
    {
        "file": "src/tau_coding/dag_runtime/memory_projection.py",
        "symbol": "MemoryProjectionOutbox.relay",
        "marker": "degraded",
        "classification": "OPTIONAL_DEGRADED",
        "reachability": "runtime_reachable",
        "normal_entry_point": "memory projection relay sender injection",
        "goal_relevance": "supporting_non_settlement",
        "disposition": "Degraded is a typed outbox state; it never becomes scheduler settlement authority.",
    },
    {
        "file": "src/tau_coding/project_status.py",
        "symbol": "_github_block",
        "marker": "degraded",
        "classification": "OPTIONAL_DEGRADED",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau project-status build; tau developer-share status",
        "goal_relevance": "developer_share_gate_input",
        "disposition": "Unavailable GitHub state is explicit STALE/UNKNOWN and fails developer-share unless scoped waiver applies.",
    },
    {
        "file": "src/tau_coding/acceptance_bundle.py",
        "symbol": "AcceptanceBundleError",
        "marker": "pass",
        "classification": "IMPLEMENTED",
        "reachability": "unreachable_exception_body",
        "normal_entry_point": "exception class declaration",
        "goal_relevance": "none",
        "disposition": "Empty exception subclass body; not a runtime capability implementation.",
    },
    {
        "file": "src/tau_coding/governed_skill_execution.py",
        "symbol": "GovernedNormalizationError",
        "marker": "pass",
        "classification": "IMPLEMENTED",
        "reachability": "unreachable_exception_body",
        "normal_entry_point": "exception class declaration",
        "goal_relevance": "none",
        "disposition": "Empty exception subclass body; not a runtime capability implementation.",
    },
    {
        "file": "src/tau_coding/project_status.py",
        "symbol": "ProjectStatusError",
        "marker": "pass",
        "classification": "IMPLEMENTED",
        "reachability": "unreachable_exception_body",
        "normal_entry_point": "exception class declaration",
        "goal_relevance": "none",
        "disposition": "Empty exception subclass body; not a runtime capability implementation.",
    },
    {
        "file": "src/tau_coding/dag_runtime/memory_projection.py",
        "symbol": "MemoryProjectionError",
        "marker": "pass",
        "classification": "IMPLEMENTED",
        "reachability": "unreachable_exception_body",
        "normal_entry_point": "exception class declaration",
        "goal_relevance": "none",
        "disposition": "Empty exception subclass body; not a runtime capability implementation.",
    },
    {
        "file": "src/tau_coding/persona_dream_panel_agent.py",
        "symbol": "_generate_panel_image_with_scillm",
        "marker": "pass",
        "classification": "IMPLEMENTED",
        "reachability": "runtime_reachable_cleanup",
        "normal_entry_point": "tau persona-dream-panel-agent image generation monitor",
        "goal_relevance": "outside_immutable_goal",
        "disposition": "Exception-swallowing cleanup for selector.unregister after subprocess exit; not a stub.",
    },
    {
        "file": "src/tau_coding/project_status.py",
        "symbol": "evaluate_developer_share_status",
        "marker": "stub-inventory",
        "classification": "IMPLEMENTED",
        "reachability": "runtime_reachable",
        "normal_entry_point": "tau developer-share status --json",
        "goal_relevance": "developer_share_gate_input",
        "disposition": "Command name identifies the classified inventory gate, not a stub implementation.",
    },
]


def _pass_symbols(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    symbols: list[str] = []
    parents: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> Any:
            parents.append(node.name)
            self.generic_visit(node)
            parents.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            parents.append(node.name)
            self.generic_visit(node)
            parents.pop()

        def visit_Pass(self, node: ast.Pass) -> Any:
            symbols.append(".".join(parents) if parents else f"line:{node.lineno}")

    Visitor().visit(tree)
    return symbols


def build_developer_surface_inventory(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    entries = [dict(item) for item in _KNOWN]
    known = {(item["file"], item["marker"]) for item in entries}
    unclassified: list[dict[str, Any]] = []

    for rel in sorted(_AUDITED_FILES):
        path = repo / rel
        if not path.is_file():
            unclassified.append({"file": rel, "marker": "missing_audited_file"})
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _MARKER_RE.search(line) and not any((rel, marker) in known and marker in line for _, marker in known):
                if "degraded" in line.lower() and any(item["file"] == rel and item["marker"] == "degraded" for item in entries):
                    continue
                unclassified.append({"file": rel, "line": lineno, "marker": line.strip()})
        for symbol in _pass_symbols(path):
            if not any(item["file"] == rel and item["marker"] == "pass" and item["symbol"] in symbol for item in entries):
                unclassified.append({"file": rel, "symbol": symbol, "marker": "pass"})

    bug_stubs = [item for item in entries if item["classification"] == "BUG_STUB"]
    return {
        "schema": STUB_INVENTORY_SCHEMA,
        "status": "PASS" if not unclassified and not bug_stubs else "FAIL",
        "repo": str(repo),
        "entries": entries,
        "unclassified_markers": unclassified,
        "unclassified_count": len(unclassified),
        "bug_stub_count": len(bug_stubs),
        "classifications": sorted({item["classification"] for item in entries}),
        "proof_boundary": {
            "mocked": False,
            "live": True,
            "provider_live": False,
            "proves": "Known developer-visible stub, placeholder, degraded, and audited pass surfaces are classified.",
            "does_not_prove": "Provider quality or implementation completeness outside the audited marker surface.",
        },
    }
