from pathlib import Path

from tau_coding.persona_dream_panel_agent import DEFAULT_FIXTURE_ROOT, _panel_context


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
