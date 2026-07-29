#!/usr/bin/env python3
"""Prove the shared React Flow viewer renders all canonical Tau DAGs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from tau_coding.canonical_dags import canonical_dag_catalog, launch_canonical_dag
from tau_coding.dag_viewer.server import create_dag_viewer_server

PROOF_SCHEMA = "tau.canonical_dag_viewer_corpus_proof.v1"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _http_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5.0) as response:  # noqa: S310 - loopback proof URL
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise RuntimeError(f"http_json_object_required:{url}")
    return payload


def _node_modules_path(repo: Path) -> str:
    candidates = [
        os.environ.get("NODE_PATH"),
        str(repo / "web" / "dag-viewer" / "node_modules"),
        "/home/graham/workspace/experiments/extractor/tools/gold_annotator_web/node_modules",
        "/home/graham/workspace/experiments/extractor/prototypes/gamified/dashboard/node_modules",
        "/home/graham/workspace/experiments/lean4/tools/lemma-viewer/node_modules",
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "puppeteer").is_dir():
            return candidate
    raise RuntimeError("missing_puppeteer")


def _chrome_bin() -> str:
    candidates = [
        os.environ.get("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return os.environ.get("CHROME_BIN", "/usr/bin/google-chrome")


def _browser_proof(
    *,
    repo: Path,
    url: str,
    dag_id: str,
    proof_dir: Path,
) -> dict[str, Any]:
    desktop = proof_dir / f"{dag_id}-desktop.png"
    mobile = proof_dir / f"{dag_id}-mobile.png"
    output = proof_dir / f"{dag_id}-browser-proof.json"
    command = [
        "node",
        str(repo / "scripts" / "canonical-dag-viewer-browser-proof.mjs"),
        url,
        dag_id,
        str(desktop),
        str(mobile),
        str(output),
    ]
    env = {
        **os.environ,
        "NODE_PATH": _node_modules_path(repo),
        "CHROME_BIN": _chrome_bin(),
    }
    completed = subprocess.run(
        command,
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        env=env,
    )
    payload = _read_json(output) if output.is_file() else {}
    payload["command"] = command
    payload["exit_code"] = completed.returncode
    payload["stdout_tail"] = completed.stdout[-2000:]
    payload["stderr_tail"] = completed.stderr[-2000:]
    if completed.returncode != 0 and "status" not in payload:
        payload.update(
            {
                "schema": "tau.canonical_dag_viewer_browser_proof.v1",
                "status": "BLOCKED",
                "mocked": False,
                "live": True,
                "dag_id": dag_id,
                "url": url,
                "errors": [f"browser_exit_code:{completed.returncode}"],
            }
        )
    return payload


def _prove_one(
    *,
    repo: Path,
    dag_id: str,
    run_root: Path,
    proof_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    launch = launch_canonical_dag(
        dag_id,
        repo=repo,
        run_root=run_root,
        timeout_seconds=timeout_seconds,
    )
    errors: list[str] = []
    if launch.get("ok") is not True:
        errors.append("canonical_launch_failed")
    viewer = launch.get("dag_viewer")
    if not isinstance(viewer, dict) or viewer.get("available") is not True:
        errors.append("dag_viewer_link_unavailable")
    run_dir_value = launch.get("run_dir")
    run_dir = Path(str(run_dir_value)).expanduser().resolve() if isinstance(run_dir_value, str) else None
    if run_dir is None or not run_dir.is_dir():
        errors.append("run_dir_missing")
    if errors:
        return {
            "dag_id": dag_id,
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": True,
            "launch": launch,
            "errors": errors,
        }

    server = create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(
        target=server.serve_forever,
        name=f"tau-canonical-viewer-{dag_id}",
        daemon=True,
    )
    thread.start()
    url = server.url
    try:
        manifest = _http_json(f"{url}api/v1/manifest")
        snapshot = _http_json(f"{url}api/v1/state")
        events = _http_json(f"{url}api/v1/events?after_sequence=0&limit=500")
        browser = _browser_proof(repo=repo, url=url, dag_id=dag_id, proof_dir=proof_dir)
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        with suppress(Exception):
            server.httpd.server_close()

    read_model_errors = []
    if manifest.get("schema") != "tau.dag_view_manifest.v1":
        read_model_errors.append("manifest_schema_invalid")
    if snapshot.get("schema") != "tau.dag_view_snapshot.v2":
        read_model_errors.append("snapshot_schema_invalid")
    if snapshot.get("projection_state") != "PROJECT_RECEIPT":
        read_model_errors.append("snapshot_not_project_receipt")
    if not isinstance(events.get("events"), list) or not events["events"]:
        read_model_errors.append("events_empty")
    if browser.get("status") != "PASS":
        read_model_errors.append("browser_proof_not_pass")

    return {
        "dag_id": dag_id,
        "ok": not read_model_errors,
        "status": "PASS" if not read_model_errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "url": url,
        "run_dir": str(run_dir),
        "server_receipt": server.receipt(),
        "launch": {
            "status": launch.get("status"),
            "mocked": launch.get("mocked"),
            "live": launch.get("live"),
            "dag_execution_ok": launch.get("dag_execution_ok"),
            "output_receipt_path": launch.get("output_receipt_path"),
            "suite_receipt_path": launch.get("suite_receipt_path"),
            "dag_viewer": launch.get("dag_viewer"),
        },
        "read_model": {
            "manifest_schema": manifest.get("schema"),
            "snapshot_schema": snapshot.get("schema"),
            "projection_state": snapshot.get("projection_state"),
            "source_schema": manifest.get("source_schema"),
            "node_count": len(manifest.get("graph", {}).get("nodes", []))
            if isinstance(manifest.get("graph"), dict)
            else 0,
            "edge_count": len(manifest.get("graph", {}).get("edges", []))
            if isinstance(manifest.get("graph"), dict)
            else 0,
            "event_count": len(events.get("events", []))
            if isinstance(events.get("events"), list)
            else 0,
            "run_status": snapshot.get("run_status"),
            "run_verdict": snapshot.get("run_verdict"),
        },
        "browser": browser,
        "errors": read_model_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    run_root = args.run_root.expanduser().resolve()
    receipt_path = args.receipt.expanduser().resolve()
    proof_dir = receipt_path.parent
    proof_dir.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    catalog = canonical_dag_catalog()
    dag_ids = [str(item["dag_id"]) for item in catalog["dags"]]
    cases = [
        _prove_one(
            repo=repo,
            dag_id=dag_id,
            run_root=run_root,
            proof_dir=proof_dir,
            timeout_seconds=args.timeout_seconds,
        )
        for dag_id in dag_ids
    ]
    errors = [
        f"{case['dag_id']}:{error}"
        for case in cases
        for error in case.get("errors", [])
    ]
    receipt = {
        "schema": PROOF_SCHEMA,
        "ok": not errors and len(cases) == 5,
        "status": "PASS" if not errors and len(cases) == 5 else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(repo),
        "run_root": str(run_root),
        "canonical_dag_count": len(cases),
        "browser_proof_count": len(
            [case for case in cases if case.get("browser", {}).get("status") == "PASS"]
        ),
        "desktop_screenshot_count": len(
            [
                case
                for case in cases
                if case.get("browser", {}).get("desktop", {}).get("screenshot")
            ]
        ),
        "mobile_screenshot_count": len(
            [
                case
                for case in cases
                if case.get("browser", {}).get("mobile", {}).get("screenshot")
            ]
        ),
        "read_back_assertions": {
            "all_five_rendered": len(cases) == 5 and all(case.get("ok") is True for case in cases),
            "shared_projection_state": all(
                case.get("read_model", {}).get("projection_state") == "PROJECT_RECEIPT"
                for case in cases
            ),
            "all_browser_proofs_get_only": all(
                case.get("browser", {}).get("desktop", {}).get("checks", {}).get("api_get_only")
                and case.get("browser", {}).get("mobile", {}).get("checks", {}).get("api_get_only")
                for case in cases
            ),
        },
        "cases": cases,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "All five canonical DAGs launched from the current checkout.",
                "Each fresh canonical run rendered through the same packaged React Flow viewer.",
                "Desktop and mobile browser screenshots were captured for each canonical run.",
                "The viewer state came from authoritative Tau project-DAG receipt/progress artifacts.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Live in-progress polling without manual reload; that is covered by #255.",
                "Human acceptance of the full immutable Tau goal.",
            ],
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
