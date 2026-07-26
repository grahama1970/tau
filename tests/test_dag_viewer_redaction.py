"""Browser projection redaction and bound checks."""

from __future__ import annotations

from tau_coding.dag_viewer.redaction import redact_for_storage, redact_for_viewer


def test_redactor_removes_sensitive_values_and_bounds_strings() -> None:
    result = redact_for_viewer({"api_key": "secret-value", "nested": {"text": "x" * 9000}})
    assert result.value["api_key"] == "[REDACTED]"
    assert "secret-value" not in str(result.value)
    assert result.redacted is True
    assert result.truncated is True


def test_redactor_omits_raw_command_and_terminal_output() -> None:
    result = redact_for_viewer(
        {
            "stdout": "Bearer sk-secret-value",
            "nested": {"stderr": "TOKEN=secret", "pane_text": "password=hunter2"},
        }
    )
    assert result.value == {
        "stdout": "[REDACTED:RAW_OUTPUT]",
        "nested": {
            "stderr": "[REDACTED:RAW_OUTPUT]",
            "pane_text": "[REDACTED:RAW_OUTPUT]",
        },
    }
    assert "secret" not in str(result.value)


def test_redactor_bounds_collections_and_depth() -> None:
    value: object = "leaf"
    for _ in range(14):
        value = {"value": value}
    result = redact_for_viewer({"items": list(range(1100)), "deep": value})
    assert len(result.value["items"]) == 1000
    assert result.truncated is True


def test_storage_redactor_masks_common_credential_value_formats() -> None:
    slack_token = "xoxb-" + "123456789012-abcdefghijklmnop"
    payload = {
        "command": [
            "curl",
            "-H",
            "Authorization: Bearer sk-live-abcdefghijklmnopqrstuvwxyz",
            "https://user:super-secret-password@example.test/resource",
            "--token=ghp_abcdefghijklmnopqrstuvwxyz123456",
        ],
        "stdout": "\n".join(
            [
                "api_key=sk-test-abcdefghijklmnopqrstuvwxyz",
                "aws=AKIAABCDEFGHIJKLMNOP",
                f"slack={slack_token}",
                "private=-----BEGIN PRIVATE KEY-----abc123-----END PRIVATE KEY-----",
                "refresh_token=rfr_1234567890abcdef",
            ]
        ),
        "nested": {
            "password": "hunter2",
            "note": "client_secret=client-secret-value",
        },
    }

    result = redact_for_storage(payload)
    serialized = str(result.value)

    assert result.redacted is True
    assert "sk-live-abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "super-secret-password" not in serialized
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in serialized
    assert "sk-test-abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "AKIAABCDEFGHIJKLMNOP" not in serialized
    assert slack_token not in serialized
    assert "abc123" not in serialized
    assert "rfr_1234567890abcdef" not in serialized
    assert result.value["nested"]["password"] == "[REDACTED]"
    assert result.value["command"][0] == "curl"
    assert "api_key=[REDACTED]" in result.value["stdout"]
