"""Static HTML run report generation for Tau runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from tau_coding.run_status import build_run_status

RUN_REPORT_RECEIPT_SCHEMA = "tau.run_report_receipt.v1"

NON_CLAIMS = [
    "ITAR compliance.",
    "Export-control legal sufficiency.",
    "Complete sandbox enforcement.",
    "Human identity verification unless a provenance receipt exists.",
    "Provider/model semantic quality.",
    "Memory fact truth.",
    "Evidence-case sufficiency for closure.",
    "DAG or swarm trustworthiness.",
]


def write_run_report(*, run_dir: Path, out_path: Path, force: bool = False) -> dict[str, Any]:
    """Write a static HTML report for an existing Tau run directory."""

    resolved_run = run_dir.expanduser().resolve()
    resolved_out = out_path.expanduser().resolve()
    if not resolved_run.exists() or not resolved_run.is_dir():
        return _blocked_receipt(
            run_dir=resolved_run,
            out_path=resolved_out,
            errors=[f"run_dir does not exist or is not a directory: {resolved_run}"],
        )
    if resolved_out.exists() and not force:
        return _blocked_receipt(
            run_dir=resolved_run,
            out_path=resolved_out,
            errors=[f"out file already exists: {resolved_out}"],
        )

    run_status = build_run_status(resolved_run)
    dag_receipt_path = resolved_run / "dag-receipt.json"
    dag_receipt = _read_optional_json(dag_receipt_path)
    contract, contract_path = _load_contract(dag_receipt)
    sections = _report_sections(
        run_status=run_status,
        dag_receipt=dag_receipt,
        contract=contract,
        run_dir=resolved_run,
    )
    html = _render_html(sections=sections, run_status=run_status, run_dir=resolved_run)
    resolved_out.parent.mkdir(parents=True, exist_ok=True)
    resolved_out.write_text(html, encoding="utf-8")

    receipt = {
        "schema": RUN_REPORT_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "run_dir": str(resolved_run),
        "report_path": str(resolved_out),
        "report_sha256": f"sha256:{_sha256(resolved_out)}",
        "source_artifacts": _source_artifacts(
            ("dag_receipt", dag_receipt_path),
            ("dag_contract", contract_path),
        ),
        "section_count": len(sections),
        "sections": [section["id"] for section in sections],
        "source_status": {
            "schema": run_status.get("schema"),
            "status": run_status.get("status"),
            "detected_type": run_status.get("detected_type"),
            "artifact_count": len(run_status.get("artifacts", {})),
        },
        "proof_scope": {
            "proves": [
                "Tau rendered a static HTML report from existing run-status and run artifacts.",
                "Tau included explicit non-claims in the rendered report.",
            ],
            "does_not_prove": NON_CLAIMS,
        },
    }
    receipt_path = resolved_out.with_suffix(resolved_out.suffix + ".receipt.json")
    receipt["receipt_path"] = str(receipt_path)
    receipt_preimage = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    receipt["receipt_sha256_excludes_self"] = True
    receipt["unsigned_receipt_preimage_sha256"] = (
        f"sha256:{hashlib.sha256(receipt_preimage.encode('utf-8')).hexdigest()}"
    )
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _report_sections(
    *,
    run_status: dict[str, Any],
    dag_receipt: dict[str, Any],
    contract: dict[str, Any],
    run_dir: Path,
) -> list[dict[str, Any]]:
    goal = contract.get("goal") if isinstance(contract.get("goal"), dict) else {}
    policy = contract.get("policy_profile")
    boundary = contract.get("data_boundary")
    memory_intent = contract.get("memory_intent")
    evidence_case = contract.get("evidence_case")
    dag_steps = {
        "entry_node": contract.get("entry_node"),
        "terminal_nodes": contract.get("terminal_nodes"),
        "nodes": contract.get("nodes", []),
        "edges": contract.get("edges", []),
    }
    decisions = {
        "status": dag_receipt.get("status") or run_status.get("status"),
        "verdict": dag_receipt.get("verdict"),
        "alerts": dag_receipt.get("alerts", []),
        "dag_error": dag_receipt.get("dag_error"),
    }
    receipts = {
        "run_dir": str(run_dir),
        "artifacts": run_status.get("artifacts", {}),
        "dag_receipt": dag_receipt or None,
    }
    coding_evidence = run_status.get("coding_evidence", {})
    return [
        {"id": "goal", "title": "Goal", "payload": goal},
        {"id": "policy", "title": "Policy", "payload": policy},
        {"id": "data-boundary", "title": "Data Boundary", "payload": boundary},
        {"id": "memory-intent", "title": "Memory Intent", "payload": memory_intent},
        {"id": "evidence-case", "title": "Evidence Case", "payload": evidence_case},
        {"id": "dag-steps", "title": "DAG Steps", "payload": dag_steps},
        {"id": "coding-evidence", "title": "Coding Evidence", "payload": coding_evidence},
        {"id": "receipts", "title": "Receipts", "payload": receipts},
        {"id": "decisions", "title": "Blocked / Allowed Decisions", "payload": decisions},
        {"id": "non-claims", "title": "Non-Claims", "payload": NON_CLAIMS},
    ]

def _render_html(
    *,
    sections: list[dict[str, Any]],
    run_status: dict[str, Any],
    run_dir: Path,
) -> str:
    status = escape(str(run_status.get("status") or "UNKNOWN"))
    detected_type = escape(str(run_status.get("detected_type") or "unknown"))
    title = f"Tau Run Report - {status}"
    section_html = "\n".join(_render_section(section) for section in sections)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }}
    body {{
      margin: 0;
      background: #f7f7f4;
      color: #181814;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    header {{
      border-bottom: 2px solid #222;
      margin-bottom: 24px;
      padding-bottom: 16px;
    }}
    h1 {{
      font-size: 28px;
      margin: 0 0 8px;
      letter-spacing: 0;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 14px;
    }}
    .pill {{
      border: 1px solid #777;
      border-radius: 4px;
      padding: 4px 8px;
      background: #fff;
    }}
    section {{
      margin: 20px 0;
      padding: 0 0 18px;
      border-bottom: 1px solid #ccc;
    }}
    h2 {{
      font-size: 18px;
      margin: 0 0 10px;
      letter-spacing: 0;
    }}
    pre {{
      overflow-x: auto;
      white-space: pre-wrap;
      background: #fff;
      border: 1px solid #d2d2cc;
      border-radius: 6px;
      padding: 12px;
      line-height: 1.45;
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>Tau Run Report</h1>
    <div class="meta">
      <span class="pill">status: {status}</span>
      <span class="pill">type: {detected_type}</span>
      <span class="pill">run: {escape(str(run_dir))}</span>
    </div>
  </header>
{section_html}
</main>
</body>
</html>
"""


def _render_section(section: dict[str, Any]) -> str:
    payload = json.dumps(section["payload"], indent=2, sort_keys=True)
    return (
        f'  <section id="{escape(str(section["id"]))}">\n'
        f"    <h2>{escape(str(section['title']))}</h2>\n"
        f"    <pre>{escape(payload)}</pre>\n"
        "  </section>"
    )


def _load_contract(dag_receipt: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    contract_path = dag_receipt.get("contract_path")
    if not isinstance(contract_path, str) or not contract_path:
        return {}, None
    path = Path(contract_path).expanduser().resolve()
    return _read_optional_json(path), path


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _blocked_receipt(*, run_dir: Path, out_path: Path, errors: list[str]) -> dict[str, Any]:
    return {
        "schema": RUN_REPORT_RECEIPT_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "run_dir": str(run_dir),
        "report_path": str(out_path),
        "errors": errors,
        "proof_scope": {
            "proves": ["Tau refused to render a report because local inputs were invalid."],
            "does_not_prove": NON_CLAIMS,
        },
    }


def _source_artifacts(*items: tuple[str, Path | None]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for label, path in items:
        if path is None:
            continue
        resolved = path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            continue
        artifacts.append(
            {
                "label": label,
                "path": str(resolved),
                "sha256": f"sha256:{_sha256(resolved)}",
                "bytes": resolved.stat().st_size,
            }
        )
    return artifacts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
