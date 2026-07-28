"""Architectural guard (#209): no bare authoritative JSON writes may return.

The three legacy write_json bodies were routed through the durable admission
primitive. This guard fails if any of them regrows a bare Path.write_text
body, or if a new module defines its own write_json instead of importing the
primitive.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tau_coding"


def _defines_bare_write_json(text: str) -> bool:
    match = re.search(
        r"def write_json\([^)]*\).*?(?=\ndef |\nclass |\Z)", text, re.DOTALL
    )
    if match is None:
        return False
    body = match.group(0)
    return "write_text" in body and "write_durable_json" not in body


def test_no_module_defines_a_bare_write_json() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if _defines_bare_write_json(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "bare write_json bodies found (route them through "
        f"dag_runtime.admission.write_durable_json): {offenders}"
    )


def test_legacy_writers_delegate_to_the_primitive() -> None:
    for rel in (
        "generic_artifact_transaction.py",
        "canonical_scheduler_conformance.py",
        "traycer/receipts.py",
    ):
        text = (SRC / rel).read_text(encoding="utf-8")
        assert "write_durable_json" in text, rel
