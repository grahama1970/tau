#!/usr/bin/env python3
"""Flag unclassified aspirational claims in primary Tau developer docs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TERMS = re.compile(r"\b(planned|future|does not yet|not yet|todo)\b", re.I)
STATUS_MARKER = re.compile(
    r"tau-doc-status:\s*"
    r"(CURRENT_CONTRACT|KNOWN_LIMITATION|EXPERIMENTAL|ROADMAP|HISTORICAL_ADR|SECURITY_NONCLAIM)"
)
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
DEFAULT_PATHS = [
    "README.md",
    "docs/run-report.md",
    "docs/replacement-harness-hardening.md",
    "docs/zero-trust-product-plan.md",
    "docs/threat-model.md",
    "docs/context-compaction.md",
]


def _section_status(heading: str) -> str | None:
    text = heading.lower()
    if "known limitation" in text:
        return "KNOWN_LIMITATION"
    if "non-claim" in text or "threat" in text or "security" in text:
        return "SECURITY_NONCLAIM"
    if "roadmap" in text or "planned" in text or "backlog" in text:
        return "ROADMAP"
    return None


def _line_status(line: str) -> str | None:
    text = line.lower()
    if "does not prove" in text or "future route correctness" in text:
        return "SECURITY_NONCLAIM"
    if "does not yet" in text or "not yet" in text:
        return "KNOWN_LIMITATION"
    return None


def scan_file(path: Path) -> list[dict[str, Any]]:
    current: str | None = None
    section = "document"
    rows: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, 1):
        marker = STATUS_MARKER.search(line)
        if marker:
            current = marker.group(1)
        heading = HEADING.match(line)
        if heading:
            section = heading.group(2)
            current = _section_status(section) or current
        if TERMS.search(line):
            inline = STATUS_MARKER.search(line)
            status = inline.group(1) if inline else _line_status(line) or current
            rows.append(
                {
                    "file": str(path),
                    "line": line_no,
                    "section": section,
                    "text": line.strip(),
                    "classification": status,
                    "disposition": "classified" if status else "unclassified",
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    paths = [Path(p) for p in (args.paths or DEFAULT_PATHS)]
    rows: list[dict[str, Any]] = []
    for path in paths:
        if path.is_file():
            rows.extend(scan_file(path))
    unclassified = [row for row in rows if row["classification"] is None]
    payload = {
        "schema": "tau.doc_status_inventory.v1",
        "ok": not unclassified,
        "audited_paths": [str(path) for path in paths],
        "claim_count": len(rows),
        "unclassified_count": len(unclassified),
        "claims": rows,
        "unclassified": unclassified,
        "proof_boundary": {
            "proves": "Primary developer docs classify aspirational phrases by explicit "
            "doc status.",
            "does_not_prove": "Runtime correctness, semantic truth of each doc claim, or "
            "GOAL.md completion.",
        },
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
