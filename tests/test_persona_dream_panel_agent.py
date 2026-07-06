import json
from pathlib import Path

from tau_coding.persona_dream_panel_agent import (
    DEFAULT_FIXTURE_ROOT,
    _generate_panel_image_with_scillm,
    _panel_context,
    _resolve_scillm_image_policy,
)


def test_panel_context_consumes_persona_dream_panel_without_default_fixture(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "active-run"
    image = run_root / "artifacts" / "panel.png"
    visual_review = run_root / "receipts" / "visual_review_receipt.json"
    payload = {
        "context": {
            "persona_dream_panel": {
                "panel_id": "panel_active",
                "run_root": str(run_root),
                "image_path": str(image),
                "visual_review_receipt": str(visual_review),
                "panel_prompt": "Use the active storyboard context.",
                "write_receipts_to_panel_run_root": "true",
            }
        }
    }

    panel = _panel_context(payload)

    assert panel["panel_id"] == "panel_active"
    assert panel["run_root"] == str(run_root)
    assert panel["run_root"] != str(DEFAULT_FIXTURE_ROOT)
    assert panel["image_path"] == str(image)
    assert panel["visual_review_receipt"] == str(visual_review)
    assert panel["panel_prompt"] == "Use the active storyboard context."
    assert panel["write_receipts_to_panel_run_root"] == "true"


def test_scillm_image_policy_uses_dag_model_policy_over_panel_defaults(
    tmp_path: Path,
) -> None:
    payload = {
        "context": {
            "model_policy": {
                "provider": "codex",
                "auth": "codex-oauth",
                "model": "gpt-2",
            },
            "persona_dream_panel": {
                "panel_id": "panel_active",
                "run_root": str(tmp_path),
                "image_path": str(tmp_path / "panel.png"),
                "visual_review_receipt": str(tmp_path / "visual_review_receipt.json"),
                "scillm_image_model": "flux",
                "scillm_image_auth": "google-api-key",
            },
        }
    }

    panel = _panel_context(payload)
    policy = _resolve_scillm_image_policy(panel, payload)

    assert policy["source"] == "dag_model_policy"
    assert policy["provider"] == "codex"
    assert policy["auth"] == "codex-oauth"
    assert policy["model"] == "gpt-2"
    assert policy["supported"] is True
    assert policy["fallback_allowed"] is False
    assert policy["fallback_backends"] == []
    assert policy["ignored_panel_overrides"] == {
        "scillm_image_auth": "google-api-key",
        "scillm_image_model": "flux",
    }


def test_scillm_image_policy_blocks_unsupported_provider_without_fallback(
    tmp_path: Path,
) -> None:
    payload = {
        "context": {
            "tau_dag_node": {
                "model_policy": {
                    "provider": "google",
                    "auth": "api-key",
                    "model": "imagen3",
                }
            },
            "persona_dream_panel": {
                "panel_id": "panel_active",
                "run_root": str(tmp_path),
                "image_path": str(tmp_path / "panel.png"),
                "visual_review_receipt": str(tmp_path / "visual_review_receipt.json"),
            },
        }
    }

    panel = _panel_context(payload)
    policy = _resolve_scillm_image_policy(panel, payload)

    assert policy["source"] == "dag_model_policy"
    assert policy["supported"] is False
    assert policy["error"] == "unsupported_image_model_provider"
    assert policy["fallback_allowed"] is False
    assert policy["fallback_performed"] is False


def test_scillm_image_generation_blocks_unsupported_policy_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_popen(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("unsupported DAG image policy must not spawn a provider command")

    monkeypatch.setattr("tau_coding.persona_dream_panel_agent.subprocess.Popen", fail_popen)
    payload = {
        "context": {
            "model_policy": {
                "provider": "google",
                "auth": "api-key",
                "model": "imagen3",
            },
            "persona_dream_panel": {
                "panel_id": "panel_active",
                "run_root": str(tmp_path),
                "image_path": str(tmp_path / "panel.png"),
                "visual_review_receipt": str(tmp_path / "visual_review_receipt.json"),
                "panel_prompt": "Generate a bounded panel.",
            },
        }
    }
    panel = _panel_context(payload)

    result = _generate_panel_image_with_scillm(panel, tmp_path / "artifacts", payload)

    receipt_path = Path(result["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert receipt["live"] is False
    assert receipt["live_call_performed"] is False
    assert receipt["error"] == "unsupported_image_model_provider"
    assert receipt["fallback_allowed"] is False
    assert receipt["fallback_performed"] is False
    assert not Path(panel["image_path"]).exists()
