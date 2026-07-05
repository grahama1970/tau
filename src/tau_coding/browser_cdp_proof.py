"""Browser/CDP proof helpers for Tau UI proof lanes."""

from __future__ import annotations

import json
import re
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any

BROWSER_CDP_PROOF_SCHEMA = "tau.browser_cdp_proof.v1"
DEFAULT_BROWSER_PROOF_RUN_ID = "tau-browser-cdp-proof"
DEFAULT_SURF_WRAPPER = (
    Path.home() / "workspace/experiments/agent-skills/skills/surf/run.sh"
)


def write_browser_cdp_proof(
    *,
    output_dir: Path,
    run_id: str = DEFAULT_BROWSER_PROOF_RUN_ID,
    surf_bin: Path | str | None = None,
    keep_tab: bool = False,
) -> dict[str, Any]:
    """Render a local Tau proof page through Surf and write screenshot + receipt."""

    resolved_output = output_dir.expanduser().resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    html_path = resolved_output / "tau-browser-cdp-proof.html"
    screenshot_path = resolved_output / "tau-browser-cdp-proof.png"
    receipt_path = resolved_output / "browser-cdp-proof-receipt.json"
    surf_command = _resolve_surf_command(surf_bin)
    tab_id: str | None = None
    command_results: list[dict[str, Any]] = []

    html_path.write_text(_proof_html(run_id=run_id), encoding="utf-8")
    url = html_path.as_uri()
    errors: list[str] = []
    surf_available = surf_command is not None
    read_text = ""

    if surf_command is None:
        errors.append("Surf wrapper or surf executable was not found.")
    else:
        tab_result = _run_surf(surf_command, ["tab.new", url])
        command_results.append(tab_result)
        if tab_result["exit_code"] != 0:
            errors.append("surf tab.new failed")
        else:
            tab_id = _parse_tab_id(str(tab_result["stdout"]))
            read_result = _run_surf(surf_command, ["read", "--filter", "all"])
            command_results.append(read_result)
            read_text = str(read_result["stdout"])
            if read_result["exit_code"] != 0:
                errors.append("surf read failed")
            snap_result = _run_surf(
                surf_command,
                ["snap", "--output", str(screenshot_path)],
            )
            command_results.append(snap_result)
            if snap_result["exit_code"] != 0:
                errors.append("surf snap failed")
            if tab_id and not keep_tab:
                close_result = _run_surf(surf_command, ["tab.close", tab_id])
                command_results.append(close_result)

    png_size = _png_size(screenshot_path)
    visible_assertions = {
        "page_text_contains_title": "Tau Browser/CDP Proof" in read_text,
        "page_text_contains_handoff_schema": "tau.agent_handoff.v1" in read_text,
        "page_text_contains_receipt_schema": BROWSER_CDP_PROOF_SCHEMA in read_text,
        "screenshot_exists": screenshot_path.exists(),
        "screenshot_nonempty": screenshot_path.exists() and screenshot_path.stat().st_size > 0,
        "screenshot_png_dimensions": bool(png_size),
    }
    ok = surf_available and not errors and all(visible_assertions.values())
    status = "PASS" if ok else "BLOCKED"
    if not surf_available:
        verdict = "SURF_UNAVAILABLE"
    elif errors:
        verdict = "SURF_BROWSER_PROOF_FAILED"
    elif not all(visible_assertions.values()):
        verdict = "VISIBLE_ASSERTION_FAILED"
    else:
        verdict = "PASS"

    receipt: dict[str, Any] = {
        "schema": BROWSER_CDP_PROOF_SCHEMA,
        "status": status,
        "ok": ok,
        "verdict": verdict,
        "mocked": False,
        "live": bool(surf_available),
        "provider_live": False,
        "run_id": run_id,
        "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "surface": "local Tau browser proof page",
        "transport": {
            "kind": "surf",
            "command": str(surf_command) if surf_command else None,
            "tab_id": tab_id,
            "url": url,
            "keep_tab": keep_tab,
        },
        "artifacts": {
            "html": str(html_path),
            "screenshot_png": str(screenshot_path),
            "receipt": str(receipt_path),
        },
        "screenshot": {
            "path": str(screenshot_path),
            "sha256": _safe_file_sha256(screenshot_path),
            "size_bytes": screenshot_path.stat().st_size if screenshot_path.exists() else 0,
            "width": png_size[0] if png_size else None,
            "height": png_size[1] if png_size else None,
        },
        "visible_assertions": visible_assertions,
        "errors": errors,
        "commands": command_results,
        "proof_scope": {
            "proves": [
                "Surf browser transport opened a local Tau proof page.",
                "Surf read observed required Tau proof text from the rendered page.",
                "Surf screenshot wrote a non-empty PNG artifact.",
                "No provider, GitHub, Memory, or DAG route mutation was performed.",
            ],
            "does_not_prove": [
                "Production chat UX acceptance.",
                "Live Memory backend behavior.",
                "Live provider/model semantic quality.",
                "GitHub mutation.",
                "Arbitrary browser UI correctness beyond this proof page.",
            ],
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _resolve_surf_command(surf_bin: Path | str | None) -> str | None:
    if surf_bin is not None:
        candidate = Path(surf_bin).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        resolved = which(str(surf_bin))
        return resolved
    if DEFAULT_SURF_WRAPPER.exists():
        return str(DEFAULT_SURF_WRAPPER)
    return which("surf")


def _run_surf(command: str, args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [command, *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return {
        "command": [command, *args],
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _parse_tab_id(stdout: str) -> str | None:
    match = re.search(r"Created tab\s+(\d+):", stdout)
    return match.group(1) if match else None


def _proof_html(*, run_id: str) -> str:
    payload = {
        "schema": BROWSER_CDP_PROOF_SCHEMA,
        "run_id": run_id,
        "handoff_schema": "tau.agent_handoff.v1",
        "next_agent": "reviewer",
    }
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Tau Browser/CDP Proof</title>
  <style>
    body {{
      margin: 0;
      background: #101417;
      color: #f2f5f4;
      font-family: system-ui, sans-serif;
    }}
    main {{
      max-width: 860px;
      margin: 64px auto;
      padding: 32px;
      border: 1px solid #40515a;
      background: #182023;
    }}
    code, pre {{
      color: #55e6c1;
    }}
  </style>
</head>
<body>
  <main id="tau-browser-proof" data-schema="{BROWSER_CDP_PROOF_SCHEMA}">
    <h1>Tau Browser/CDP Proof</h1>
    <p>Rendered by Surf browser transport for Tau proof boundary inspection.</p>
    <p>Required handoff schema: <code>tau.agent_handoff.v1</code></p>
    <p>Receipt schema: <code>{BROWSER_CDP_PROOF_SCHEMA}</code></p>
    <pre id="proof-json">{json.dumps(payload, indent=2, sort_keys=True)}</pre>
  </main>
</body>
</html>
"""


def _png_size(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


def _safe_file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
