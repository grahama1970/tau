#!/usr/bin/env python3
"""Check exported public DAG contract key snapshots against runtime validators."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding import public_dag_contracts as contracts  # noqa: E402

SNAPSHOT = ROOT / "experiments/goal-locked-subagents/schemas/tau.public_dag_contract_keys.v1.json"


def main() -> int:
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    expected = {
        "project_dag": {
            "root": contracts.PROJECT_DAG_ROOT_KEYS,
            "goal": contracts.PROJECT_DAG_GOAL_KEYS,
            "target": contracts.PROJECT_DAG_TARGET_KEYS,
            "limits": contracts.PROJECT_DAG_LIMIT_KEYS,
            "node": contracts.PROJECT_DAG_NODE_KEYS,
            "edge": contracts.PROJECT_DAG_EDGE_KEYS,
        },
        "generic_dag": {
            "root": contracts.GENERIC_DAG_ROOT_KEYS,
            "goal": contracts.GENERIC_DAG_GOAL_KEYS,
            "budget": contracts.GENERIC_DAG_BUDGET_KEYS,
            "node": contracts.GENERIC_DAG_NODE_KEYS,
        },
    }
    errors: list[str] = []
    for family, sections in expected.items():
        source = payload.get(family)
        if not isinstance(source, dict):
            errors.append(f"{family} missing from {SNAPSHOT}")
            continue
        for section, keys in sections.items():
            actual = source.get(section)
            if not isinstance(actual, list) or sorted(actual) != sorted(keys):
                errors.append(
                    f"{family}.{section} drift: snapshot={actual!r} runtime={sorted(keys)!r}"
                )
    schema = payload.get("schema")
    if schema != "tau.public_dag_contract_keys.v1":
        errors.append(f"schema drift: {schema!r}")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"PASS {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
