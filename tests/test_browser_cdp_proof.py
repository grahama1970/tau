import json
import stat
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.browser_cdp_proof import BROWSER_CDP_PROOF_SCHEMA, write_browser_cdp_proof
from tau_coding.cli import app


def test_browser_cdp_proof_writes_receipt_and_screenshot_with_surf(tmp_path: Path) -> None:
    surf = _write_fake_surf(tmp_path)
    receipt = write_browser_cdp_proof(
        output_dir=tmp_path / "proof",
        run_id="browser-proof-test",
        surf_bin=surf,
    )

    assert receipt["schema"] == BROWSER_CDP_PROOF_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["visible_assertions"]["screenshot_png_dimensions"] is True
    assert receipt["screenshot"]["width"] == 2
    assert receipt["screenshot"]["height"] == 1
    assert (tmp_path / "proof" / "browser-cdp-proof-receipt.json").exists()
    assert (tmp_path / "proof" / "tau-browser-cdp-proof.png").exists()


def test_browser_cdp_proof_fails_closed_without_surf(tmp_path: Path) -> None:
    receipt = write_browser_cdp_proof(
        output_dir=tmp_path / "proof",
        run_id="browser-proof-test",
        surf_bin=tmp_path / "missing-surf",
    )

    assert receipt["schema"] == BROWSER_CDP_PROOF_SCHEMA
    assert receipt["status"] == "BLOCKED"
    assert receipt["ok"] is False
    assert receipt["verdict"] == "SURF_UNAVAILABLE"
    assert receipt["errors"]


def test_browser_cdp_proof_cli_writes_receipt(tmp_path: Path) -> None:
    surf = _write_fake_surf(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "browser-cdp-proof",
            "--out-dir",
            str(tmp_path / "proof"),
            "--run-id",
            "browser-proof-cli",
            "--surf-bin",
            str(surf),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema"] == BROWSER_CDP_PROOF_SCHEMA
    assert payload["ok"] is True
    assert payload["run_id"] == "browser-proof-cli"
    assert (tmp_path / "proof" / "browser-cdp-proof-receipt.json").exists()


def _write_fake_surf(tmp_path: Path) -> Path:
    surf = tmp_path / "surf"
    surf.write_text(
        """#!/usr/bin/env python3
import base64
import pathlib
import sys

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAADElEQVR42mP8z8AARQAEmQH9"
    "CdhVvgAAAABJRU5ErkJggg=="
)

args = sys.argv[1:]
if args[:1] == ["tab.new"]:
    print(f"Created tab 12345: {args[1]}")
elif args[:1] == ["read"]:
    print("Tau Browser/CDP Proof")
    print("tau.agent_handoff.v1")
    print("tau.browser_cdp_proof.v1")
elif args[:1] == ["snap"]:
    output = pathlib.Path(args[args.index("--output") + 1])
    output.write_bytes(PNG)
    print(f"Saved to {output}")
elif args[:1] == ["tab.close"]:
    print(f"Closed tab {args[1]}")
else:
    print(f"unexpected args: {args}", file=sys.stderr)
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    surf.chmod(surf.stat().st_mode | stat.S_IXUSR)
    return surf
