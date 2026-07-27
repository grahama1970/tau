"""Final conformance bundle for Tau issue #180."""

from __future__ import annotations

import hashlib
import html
import json
import os
import subprocess
from pathlib import Path
from typing import Any

ISSUE_180_FINAL_CONFORMANCE_SCHEMA = "tau.issue_180_final_conformance_bundle.v1"
REPO = "grahama1970/tau"
PARENT_ISSUE = 180
PREREQUISITE_CHILDREN = (182, 183, 184)
FINAL_PACKET_ISSUE = 185


def write_issue_180_final_conformance_bundle(
    output: Path,
    *,
    allow_live_github: bool,
    allow_live_browser: bool,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Write the final #180 conformance and human-acceptance packet."""

    if not allow_live_github:
        raise RuntimeError("--allow-live-github is required")
    if not allow_live_browser:
        raise RuntimeError("--allow-live-browser is required")

    root = (repo_root or Path.cwd()).expanduser().resolve()
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    proof_dir.mkdir(parents=True, exist_ok=True)

    parent = _issue_view(PARENT_ISSUE)
    children = [_issue_view(number) for number in PREREQUISITE_CHILDREN]
    final_packet_issue = _issue_view(FINAL_PACKET_ISSUE)
    all_children_closed = all(child.get("state") == "CLOSED" for child in children)
    remote_main_sha = _remote_main_sha(root)
    child_artifacts = _child_artifacts(root)
    artifact_errors = [
        error
        for child in child_artifacts
        for error in child.get("errors", [])
    ]
    acceptance = _acceptance_state(parent)

    html_path = proof_dir / "issue-180-final-conformance-bundle.html"
    _write_html_packet(
        html_path,
        parent=parent,
        children=children,
        final_packet_issue=final_packet_issue,
        child_artifacts=child_artifacts,
        remote_main_sha=remote_main_sha,
        acceptance=acceptance,
    )
    screenshot_path = proof_dir / "issue-180-final-conformance-bundle.png"
    _chrome_screenshot(_find_chrome(), html_path, screenshot_path)

    acceptance_recorded_or_blocked = acceptance["status"] in {
        "ACCEPTED",
        "BLOCKED_NEEDS_HUMAN_ACCEPTANCE",
    }
    status = (
        "PASS"
        if all_children_closed and not artifact_errors and acceptance_recorded_or_blocked
        else "BLOCKED"
    )
    payload = {
        "schema": ISSUE_180_FINAL_CONFORMANCE_SCHEMA,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": REPO,
        "parent_issue": parent,
        "final_packet_issue": final_packet_issue,
        "children": children,
        "all_children_closed": all_children_closed,
        "remote_main_sha": remote_main_sha,
        "child_artifacts": child_artifacts,
        "artifact_errors": artifact_errors,
        "acceptance": acceptance,
        "acceptance_recorded_or_blocked": acceptance_recorded_or_blocked,
        "browser_packet": {
            "html": str(html_path),
            "screenshot": str(screenshot_path),
            "screenshot_sha256": _sha256(screenshot_path),
        },
        "proof_boundary": {
            "proves": [
                "Tau queried GitHub live for #180 prerequisite child issue states.",
                "Tau read retained local proof artifacts for the closed child tickets.",
                "Tau generated a browser-readable final acceptance packet.",
                "Tau records #180 as blocked when human acceptance has not been recorded.",
            ],
            "does_not_prove": [
                "Human acceptance of #180.",
                "Provider semantic correctness.",
                "That future regressions cannot reopen a child issue.",
            ],
        },
    }
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _issue_view(number: int) -> dict[str, Any]:
    result = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            REPO,
            "--json",
            "number,title,state,stateReason,url,closedAt,updatedAt",
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"github_issue_view_failed:{number}:{result.stderr or result.stdout}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"github_issue_view_not_object:{number}")
    return payload


def _remote_main_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "ls-remote", "origin", "refs/heads/main"],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git_ls_remote_failed:{result.stderr or result.stdout}")
    sha = result.stdout.split()[0]
    if len(sha) != 40:
        raise RuntimeError("git_ls_remote_main_sha_invalid")
    return sha


def _child_artifacts(root: Path) -> list[dict[str, Any]]:
    specs = [
        {
            "issue": 182,
            "receipt": root
            / "docs/proofs/tickets/issue-182-installed-wheel-viewer-proof-20260727"
            / "installed-wheel-viewer-proof.json",
            "screenshots": [
                root
                / "docs/proofs/tickets/issue-182-installed-wheel-viewer-proof-20260727"
                / "installed-wheel-viewer-desktop.png",
                root
                / "docs/proofs/tickets/issue-182-installed-wheel-viewer-proof-20260727"
                / "installed-wheel-viewer-mobile.png",
            ],
        },
        {
            "issue": 183,
            "receipt": root
            / "docs/proofs/tickets/issue-183-ticket-subagent-closure-proof-20260727"
            / "ticket-subagent-closure-proof.json",
            "screenshots": [],
        },
        {
            "issue": 184,
            "receipt": root
            / "docs/proofs/tickets/issue-184-memory-provenance-proof-20260727"
            / "memory-provenance-proof.json",
            "screenshots": [
                root
                / "docs/proofs/tickets/issue-184-memory-provenance-proof-20260727"
                / "memory-provenance-viewer.png"
            ],
        },
    ]
    return [_artifact_summary(spec) for spec in specs]


def _artifact_summary(spec: dict[str, Any]) -> dict[str, Any]:
    receipt = Path(spec["receipt"])
    errors: list[str] = []
    receipt_payload: dict[str, Any] = {}
    if not receipt.is_file():
        errors.append(f"receipt_missing:{receipt}")
    else:
        try:
            loaded = json.loads(receipt.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"receipt_unreadable:{receipt}:{exc}")
        else:
            if isinstance(loaded, dict):
                receipt_payload = loaded
            else:
                errors.append(f"receipt_not_object:{receipt}")
    screenshots = []
    for screenshot in spec["screenshots"]:
        screenshot_path = Path(screenshot)
        if not screenshot_path.is_file():
            errors.append(f"screenshot_missing:{screenshot_path}")
            continue
        screenshots.append(
            {
                "path": str(screenshot_path),
                "bytes": screenshot_path.stat().st_size,
                "sha256": _sha256(screenshot_path),
            }
        )
    return {
        "issue": spec["issue"],
        "receipt": {
            "path": str(receipt),
            "exists": receipt.is_file(),
            "schema": receipt_payload.get("schema"),
            "status": receipt_payload.get("status"),
            "mocked": receipt_payload.get("mocked"),
            "live": receipt_payload.get("live"),
            "provider_live": receipt_payload.get("provider_live"),
            "sha256": _sha256(receipt) if receipt.is_file() else None,
        },
        "screenshots": screenshots,
        "errors": errors,
    }


def _acceptance_state(parent: dict[str, Any]) -> dict[str, str]:
    if parent.get("state") == "CLOSED":
        return {
            "status": "ACCEPTED",
            "reason": "Parent issue #180 is already closed on GitHub.",
        }
    return {
        "status": "BLOCKED_NEEDS_HUMAN_ACCEPTANCE",
        "reason": "Parent issue #180 remains open; human acceptance is still required.",
    }


def _write_html_packet(
    path: Path,
    *,
    parent: dict[str, Any],
    children: list[dict[str, Any]],
    final_packet_issue: dict[str, Any],
    child_artifacts: list[dict[str, Any]],
    remote_main_sha: str,
    acceptance: dict[str, str],
) -> None:
    child_rows = "\n".join(
        f"<tr><td>#{child['number']}</td><td>{html.escape(child['title'])}</td>"
        f"<td>{html.escape(child['state'])}</td><td>{html.escape(child.get('url') or '')}</td></tr>"
        for child in children
    )
    artifact_rows = "\n".join(
        f"<tr><td>#{artifact['issue']}</td><td>{html.escape(str(artifact['receipt']['schema']))}</td>"
        f"<td>{html.escape(str(artifact['receipt']['status']))}</td>"
        f"<td>{html.escape(str(artifact['receipt']['live']))}</td>"
        f"<td>{len(artifact['screenshots'])}</td>"
        f"<td>{html.escape(', '.join(artifact['errors']))}</td></tr>"
        for artifact in child_artifacts
    )
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Tau Issue 180 Final Conformance</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; background: #0f1720; color: #e6edf3; }}
    main {{ padding: 32px; max-width: 1220px; margin: auto; }}
    h1 {{ margin: 0 0 8px; font-size: 32px; }}
    .banner {{ border: 1px solid #38bdf8; padding: 16px; background: #102436; margin: 20px 0; }}
    table {{ width: 100%; border-collapse: collapse; margin: 18px 0 28px; }}
    th, td {{ border: 1px solid #334155; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ color: #93c5fd; }}
    code {{ color: #facc15; }}
  </style>
</head>
<body>
  <main>
    <h1>Tau Issue 180 Final Conformance</h1>
    <p>Parent: #{parent['number']} {html.escape(parent['title'])}</p>
    <div class="banner">
      <strong>Acceptance status:</strong> {html.escape(acceptance['status'])}<br>
      {html.escape(acceptance['reason'])}<br>
      <strong>Remote main:</strong> <code>{html.escape(remote_main_sha)}</code><br>
      <strong>Final packet issue:</strong> #{final_packet_issue['number']}
      {html.escape(final_packet_issue['state'])}
    </div>
    <h2>Closed prerequisite children</h2>
    <table>
      <thead><tr><th>Issue</th><th>Title</th><th>State</th><th>URL</th></tr></thead>
      <tbody>{child_rows}</tbody>
    </table>
    <h2>Retained evidence artifacts</h2>
    <table>
      <thead><tr><th>Issue</th><th>Schema</th><th>Status</th><th>Live</th><th>Screenshots</th><th>Errors</th></tr></thead>
      <tbody>{artifact_rows}</tbody>
    </table>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def _chrome_screenshot(chrome: str, html_path: Path, screenshot: Path) -> None:
    result = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=3000",
            "--window-size=1440,1000",
            f"--screenshot={screenshot}",
            html_path.as_uri(),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise RuntimeError(f"issue_180_conformance_chrome_failed:{result.stderr or result.stdout}")
    if not screenshot.is_file() or screenshot.stat().st_size <= 1024:
        raise RuntimeError(f"issue_180_conformance_screenshot_missing:{screenshot}")


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
    raise RuntimeError("issue_180_conformance_chrome_missing")


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
