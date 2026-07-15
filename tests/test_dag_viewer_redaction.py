"""Browser projection redaction and bound checks."""

from __future__ import annotations

from tau_coding.dag_viewer.redaction import redact_for_viewer


def test_redactor_removes_sensitive_values_and_bounds_strings() -> None:
    result = redact_for_viewer({"api_key": "secret-value", "nested": {"text": "x" * 9000}})
    assert result.value["api_key"] == "[REDACTED]"
    assert "secret-value" not in str(result.value)
    assert result.redacted is True
    assert result.truncated is True


def test_redactor_bounds_collections_and_depth() -> None:
    value: object = "leaf"
    for _ in range(14):
        value = {"value": value}
    result = redact_for_viewer({"items": list(range(1100)), "deep": value})
    assert len(result.value["items"]) == 1000
    assert result.truncated is True
