import json
from pathlib import Path

from tau_coding.media_explainer_orchestration import (
    inspect_media_explainer_run,
    run_media_explainer_smoke,
)


def test_media_explainer_smoke_fans_out_mixed_assets(tmp_path: Path) -> None:
    receipt = run_media_explainer_smoke(run_root=tmp_path, label="issue-46")

    assert receipt["schema"] == "tau.media_explainer_run_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["mocked"] is True
    assert receipt["live"] is False
    assert receipt["provider_live"] is False
    assert receipt["asset_count"] == 5
    assert receipt["status_counts"] == {"FAILED": 1, "READY": 4}
    assert receipt["step02_gate"]["status"] == "READY"
    assert receipt["step02_gate"]["required_assets_ready"] is True
    assert receipt["step02_gate"]["optional_failed_asset_ids"] == ["optional-audio-broken"]
    assert receipt["completion_order_differs_from_manifest"] is True
    assert receipt["completion_order"][0] == "optional-audio-broken"

    media_types = {asset["media_type"] for asset in receipt["asset_receipts"]}
    assert media_types == {"image", "video", "audio", "text"}
    for asset_receipt in receipt["asset_receipts"]:
        assert Path(asset_receipt["evidence"]["receipt_path"]).exists()
        assert asset_receipt["memory_persistence"]["status"] == "SKIPPED_PLACEHOLDER"
        assert (
            asset_receipt["memory_persistence"][
                "mocked_description_not_persisted_as_live_truth"
            ]
            is True
        )
    assert receipt["memory_policy"]["mocked_descriptions_persisted_as_live_truth"] is False


def test_media_explainer_smoke_blocks_step02_on_required_failure(tmp_path: Path) -> None:
    work_item = tmp_path / "work-item.json"
    work_item.write_text(
        json.dumps(
            {
                "schema": "tau.media_explainer_work_item.v1",
                "assets": [
                    {
                        "asset_id": "image-required",
                        "media_type": "image",
                        "source_uri": "file:///fixture.png",
                        "required": True,
                        "simulate_failure": True,
                    },
                    {
                        "asset_id": "text-required",
                        "media_type": "text",
                        "source_uri": "file:///fixture.txt",
                        "required": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_media_explainer_smoke(
        run_root=tmp_path / "runs",
        label="required-failure",
        work_item=work_item,
    )

    assert receipt["status"] == "PASS"
    assert receipt["step02_gate"]["status"] == "BLOCKED"
    assert receipt["step02_gate"]["failed_required_asset_ids"] == ["image-required"]
    assert receipt["status_counts"] == {"FAILED": 1, "READY": 1}


def test_media_explainer_inspect_summarizes_receipt(tmp_path: Path) -> None:
    receipt = run_media_explainer_smoke(run_root=tmp_path, label="inspect")

    summary = inspect_media_explainer_run(Path(receipt["run_dir"]))

    assert summary["schema"] == "tau.media_explainer_inspect.v1"
    assert summary["ok"] is True
    assert summary["asset_count"] == 5
    assert summary["status_counts"] == {"FAILED": 1, "READY": 4}
    assert summary["step02_gate"]["status"] == "READY"
    assert summary["events_count"] == 11
