"""Minimal Tau text-reasoning node for persona-dream phases 13/14.

This is the sanctioned ``/tau -> /scillm`` path for TEXT reasoning. Persona-dream
drivers must never call scillm directly; they hand a caller-authored free-text
prompt plus a caller-defined JSON output contract to this node, which:

  * hash-records the prompt (``prompt_sha256``) and the output contract
    (``output_contract_sha256``),
  * resolves the scillm proxy key the same way the panel-reviewer node does
    (``docker:scillm-proxy`` etc. via :func:`_resolve_scillm_api_key`),
  * POSTs to the local scillm proxy ``/v1/chat/completions`` with the caller's
    model (default ``gpt-5.5``) and ``response_format={"type":"json_object"}``,
  * emits a ``tau.persona_dream.scillm_text_reasoning_receipt.v1`` receipt that
    records ``api_key_source``, ``model``, ``http_status``, the raw
    ``response_content`` and the parsed JSON object.

The node is deliberately text-only and schema-agnostic: it never validates the
caller's domain contract (the persona-dream deterministic gate does that). It
only proves the LLM call was made through Tau, with receipts.

Invocation (from the tau repo root)::

    echo '<request json>' | uv run python -m tau_coding.persona_dream_text_reasoning_agent \
        --receipt-out /path/to/receipt.json

The request JSON (stdin) fields:
    prompt            (str, required)  free-text reasoning prompt
    role              (str)            e.g. "persona-self-interpretation"
    model             (str)            default "gpt-5.5"
    caller_skill      (str)            X-Caller-Skill header value
    output_contract   (dict|str)      caller-defined JSON schema/contract (hashed)
    scillm_base_url   (str)            default http://127.0.0.1:4001
    timeout_s         (float)          default 180
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from tau_coding.persona_dream_panel_agent import _resolve_scillm_api_key

RECEIPT_SCHEMA = "tau.persona_dream.scillm_text_reasoning_receipt.v1"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "http://127.0.0.1:4001"
DEFAULT_CALLER_SKILL = "tau-persona-dream-text-reasoning"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a single JSON object out of an LLM response."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _content_from_response(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def run_text_reasoning(request: Mapping[str, Any]) -> dict[str, Any]:
    """Perform one text-reasoning LLM call through the scillm proxy and return a receipt."""
    prompt = str(request.get("prompt") or "").strip()
    role = str(request.get("role") or "text-reasoning")
    model = str(request.get("model") or DEFAULT_MODEL)
    caller_skill = str(request.get("caller_skill") or DEFAULT_CALLER_SKILL)
    base_url = str(request.get("scillm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    timeout_s = float(request.get("timeout_s") or 180)
    output_contract = request.get("output_contract")

    output_contract_sha = (
        _sha256(_canonical(output_contract)) if output_contract is not None else None
    )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "scillm_metadata": {"caller": "tau", "proof": "persona-dream-text-reasoning", "role": role},
    }

    auth = _resolve_scillm_api_key()
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "created_at": _now_iso(),
        "role": role,
        "mocked": False,
        "live": True,
        "surface": "scillm.chat_completions.text",
        "model": model,
        "caller_skill": caller_skill,
        "prompt_sha256": _sha256(prompt),
        "output_contract_sha256": output_contract_sha,
        "api_key_source": auth["source"],
        "request": {**payload, "messages": "<redacted-prompt>"},
        "status": "BLOCKED",
        "live_call_performed": False,
        "parsed_json": None,
    }

    if not prompt:
        receipt["error"] = "empty_prompt"
        return receipt
    if not auth["api_key"]:
        receipt["error"] = "scillm_api_key_unavailable"
        return receipt

    try:
        with httpx.Client(base_url=base_url, timeout=timeout_s) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {auth['api_key']}",
                    "X-Caller-Skill": caller_skill,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        receipt["http_status"] = response.status_code
        receipt["live_call_performed"] = True
        if response.status_code != 200:
            receipt["error"] = f"scillm_http_status_{response.status_code}"
            receipt["response_text"] = response.text[:1000]
            return receipt
        data = response.json()
    except httpx.HTTPError as exc:
        receipt["error"] = f"scillm_http_error: {exc}"
        return receipt
    except json.JSONDecodeError as exc:
        receipt["error"] = f"scillm_response_not_json: {exc}"
        return receipt

    content = _content_from_response(data)
    receipt["response_content"] = content
    parsed = _extract_json_object(content)
    receipt["parsed_json"] = parsed
    if parsed is None:
        receipt["status"] = "BLOCKED"
        receipt["error"] = "response_not_parseable_json_object"
    else:
        receipt["status"] = "PASS"
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, default=None, help="Request JSON file (else stdin)")
    parser.add_argument("--receipt-out", type=Path, default=None, help="Write receipt JSON here.")
    args = parser.parse_args(argv)

    raw = args.request.read_text(encoding="utf-8") if args.request else sys.stdin.read()
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise SystemExit("request must be a JSON object")

    receipt = run_text_reasoning(request)
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt_out:
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if receipt.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
