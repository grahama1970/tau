"""Live Memory provenance proof for Tau chain selection."""

from __future__ import annotations

import hashlib
import html
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tau_coding.memory_acquisition import (
    DEFAULT_MEMORY_URL,
    write_skill_chain_selection_receipt,
)

MEMORY_PROVENANCE_PROOF_SCHEMA = "tau.memory_provenance_proof.v1"


def write_memory_provenance_proof(
    output: Path,
    *,
    allow_live_memory: bool,
    memory_url: str | None = None,
) -> dict[str, Any]:
    """Write a proof that Tau consumes governed Memory chain provenance."""

    if not allow_live_memory:
        raise RuntimeError("--allow-live-memory is required")

    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    proof_dir.mkdir(parents=True, exist_ok=True)
    base_url = (memory_url or os.environ.get("TAU_MEMORY_URL") or DEFAULT_MEMORY_URL).rstrip("/")

    live_receipt_path = proof_dir / "populated-skill-chain-selection.json"
    live_receipt = write_skill_chain_selection_receipt(
        query=(
            "Tau Memory provenance recall-chain skill_chain tool_chain viewer "
            "provenance hop count"
        ),
        receipt_path=live_receipt_path,
        memory_url=base_url,
        scope="tau",
        app="tau",
        timeout_seconds=10.0,
    )

    invalid_receipt_path = proof_dir / "invalid-skill-chain-selection.json"
    invalid_server = _InvalidMemoryServer()
    invalid_server.start()
    try:
        invalid_receipt = write_skill_chain_selection_receipt(
            query="Tau Memory invalid chain provenance should block",
            receipt_path=invalid_receipt_path,
            memory_url=invalid_server.url,
            scope="tau",
            app="tau",
            timeout_seconds=2.0,
        )
    finally:
        invalid_server.stop()

    memory_down_receipt_path = proof_dir / "memory-down-skill-chain-selection.json"
    memory_down_receipt = write_skill_chain_selection_receipt(
        query="Tau Memory down provenance should degrade explicitly",
        receipt_path=memory_down_receipt_path,
        memory_url="http://127.0.0.1:9",
        scope="tau",
        app="tau",
        timeout_seconds=0.2,
    )

    live_chain = live_receipt.get("skill_chain")
    populated_path = (
        live_receipt.get("status") == "PASS"
        and isinstance(live_chain, dict)
        and bool(live_chain.get("traversal_path"))
        and isinstance(live_chain.get("hop_count"), int)
    )
    memory_down_degraded = (
        memory_down_receipt.get("status") in {"BLOCKED", "DEGRADED"}
        and "memory_recall_unavailable" in memory_down_receipt.get("alert_codes", [])
    )
    invalid_chain_blocked = (
        invalid_receipt.get("status") in {"BLOCKED", "DEGRADED"}
        and "skill_chain_missing" in invalid_receipt.get("alert_codes", [])
    )

    viewer_artifact = proof_dir / "memory-provenance-viewer.html"
    _write_viewer_artifact(
        viewer_artifact,
        live_receipt=live_receipt,
        invalid_receipt=invalid_receipt,
        memory_down_receipt=memory_down_receipt,
    )
    screenshot = proof_dir / "memory-provenance-viewer.png"
    _chrome_screenshot(_find_chrome(), viewer_artifact, screenshot)

    payload = {
        "schema": MEMORY_PROVENANCE_PROOF_SCHEMA,
        "status": (
            "PASS"
            if populated_path and memory_down_degraded and invalid_chain_blocked
            else "BLOCKED"
        ),
        "mocked": False,
        "live": live_receipt.get("live") is True,
        "provider_live": False,
        "memory_url": base_url,
        "populated_path": populated_path,
        "memory_down_degraded": memory_down_degraded,
        "invalid_chain_blocked": invalid_chain_blocked,
        "live_skill_chain": _chain_summary(live_chain),
        "receipts": {
            "populated": str(live_receipt_path),
            "invalid_chain": str(invalid_receipt_path),
            "memory_down": str(memory_down_receipt_path),
        },
        "viewer_artifact": {
            "html": str(viewer_artifact),
            "screenshot": str(screenshot),
            "screenshot_sha256": _sha256(screenshot),
        },
        "proof_boundary": {
            "proves": [
                "Tau consumed a live Graph Memory /recall skill_chain with path and hop count.",
                "Tau blocked an invalid chain response instead of inferring a workflow.",
                "Tau degraded explicitly when Memory was unreachable.",
                "A browser-readable provenance artifact exposes the populated and degraded states.",
            ],
            "does_not_prove": [
                "Memory fact truth.",
                "That the selected chain is semantically optimal.",
                "That Tau wrote anything back to Memory.",
            ],
        },
    }
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


class _InvalidMemoryServer:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _InvalidMemoryHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


class _InvalidMemoryHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = {
            "schema": "memory.recall.v1",
            "found": True,
            "should_scan": False,
            "confidence": 0.8,
            "items": [{"problem": "invalid chain", "solution": "must block"}],
            "skill_chain": {
                "skills": ["memory", "ticket", "checkpoint"],
                "traversal_path": [
                    {"position": 0, "node": "memory"},
                    {"position": 1, "node": "checkpoint"},
                ],
                "hop_count": 2,
            },
        }
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def _write_viewer_artifact(
    path: Path,
    *,
    live_receipt: dict[str, Any],
    invalid_receipt: dict[str, Any],
    memory_down_receipt: dict[str, Any],
) -> None:
    live_chain = live_receipt.get("skill_chain")
    chain = live_chain if isinstance(live_chain, dict) else {}
    rows = [
        ("Populated Memory chain", live_receipt),
        ("Invalid chain blocked", invalid_receipt),
        ("Memory down degraded", memory_down_receipt),
    ]
    cards = "\n".join(
        f"""
        <section>
          <h2>{html.escape(title)}</h2>
          <dl>
            <dt>Status</dt><dd>{html.escape(str(receipt.get("status")))}</dd>
            <dt>Live</dt><dd>{html.escape(str(receipt.get("live")))}</dd>
            <dt>Selection</dt><dd>{html.escape(str(receipt.get("selection_source")))}</dd>
            <dt>Alerts</dt><dd>{html.escape(", ".join(receipt.get("alert_codes", [])))}</dd>
          </dl>
        </section>
        """
        for title, receipt in rows
    )
    raw_path_nodes = chain.get("traversal_path")
    path_nodes = raw_path_nodes if isinstance(raw_path_nodes, list) else []
    path_text = " -> ".join(
        str(item.get("node", item.get("skill", item.get("name"))))
        for item in path_nodes
        if isinstance(item, dict)
    )
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Tau Memory Provenance</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f1720; color: #e6edf3; margin: 0; }}
    main {{ padding: 32px; max-width: 1180px; margin: auto; }}
    h1 {{ margin: 0 0 8px; font-size: 32px; }}
    .path {{ border: 1px solid #38bdf8; padding: 16px; margin: 24px 0; background: #102436; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
    section {{ border: 1px solid #334155; padding: 16px; background: #111c2a; }}
    dt {{ color: #93c5fd; font-size: 12px; text-transform: uppercase; }}
    dd {{ margin: 0 0 10px; font-weight: 650; }}
  </style>
</head>
<body>
  <main>
    <h1>Tau Memory Provenance</h1>
    <p>Governed Graph Memory chain provenance is surfaced without inferring missing workflows.</p>
    <div class="path">
      <strong>Populated path</strong>
      <p>{html.escape(path_text)}</p>
      <p>Hop count: {html.escape(str(chain.get("hop_count")))}</p>
    </div>
    <div class="grid">{cards}</div>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def _chain_summary(chain: object) -> dict[str, Any] | None:
    if not isinstance(chain, dict):
        return None
    return {
        "skills": chain.get("skills"),
        "hop_count": chain.get("hop_count"),
        "traversal_path": chain.get("traversal_path"),
        "provenance": chain.get("provenance"),
    }


def _chrome_screenshot(chrome: str, html_path: Path, screenshot: Path) -> None:
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=3000",
        "--window-size=1280,900",
        f"--screenshot={screenshot}",
        html_path.as_uri(),
    ]
    result = subprocess.run(command, check=False, text=True, capture_output=True, timeout=45)
    if result.returncode != 0:
        raise RuntimeError(f"memory_provenance_chrome_failed:{result.stderr or result.stdout}")
    if not screenshot.is_file() or screenshot.stat().st_size <= 1024:
        raise RuntimeError(f"memory_provenance_screenshot_missing:{screenshot}")


def _find_chrome() -> str:
    for candidate in (
        os.environ.get("TAU_CHROME"),
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "/snap/bin/chromium",
    ):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
        found = _which(candidate)
        if found is not None:
            return found
    raise RuntimeError("memory_provenance_chrome_missing")


def _which(command: str) -> str | None:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder) / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
