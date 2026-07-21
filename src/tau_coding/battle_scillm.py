"""Authenticated Scillm bridge for bounded Battle Tau handoffs.

This module calls an OpenAI-compatible Scillm proxy with Authorization, extracts
one JSON object from the response, and performs one JSON repair attempt when the
first response is not parseable. It never fabricates Red or Blue artifacts.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SURFACE = "scillm.chat_completions"
CALLER_SKILL = "battle"
DEFAULT_DEV_PROXY_KEY = "sk-dev-proxy-123"
SCILLM_AUTH_ENDPOINT = "/v1/scillm/auth"
DISABLE_STALE_AUTH_REPAIR_ENV = "TAU_SCILLM_DISABLE_PROXY_RECREATE"
HEALTHY_CODEX_AUTH_STATUSES = {
    "authenticated",
    "configured",
    "ok",
    "pass",
    "ready",
    "valid",
}
DEFAULT_SCILLM_COMPOSE_DIR = Path("/home/graham/workspace/experiments/scillm/deploy/docker")
DEFAULT_SCILLM_REPAIR_COMMAND = [
    "docker",
    "compose",
    "-p",
    "docker",
    "--env-file",
    "../../.env",
    "-f",
    "compose.scillm.core.yml",
    "up",
    "-d",
    "--force-recreate",
    "scillm-proxy",
]


def call_battle_subagent(
    handoff: dict[str, Any],
    team: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float | int,
) -> dict[str, Any]:
    """Call Scillm for one Battle team and return a fail-closed receipt."""
    started = time.time()
    api_key, api_key_source, api_key_errors = _resolve_api_key()
    attempts: list[dict[str, Any]] = []

    first = _chat(
        base_url=scillm_base_url,
        model=model,
        messages=_artifact_messages(team, persona, handoff),
        timeout_s=float(timeout_s),
        api_key=api_key,
        response_format=True,
    )
    parsed = parse_json_object(str(first.get("response_content") or ""))
    attempts.append(_attempt("initial", first, parsed))
    selected = first
    repair_used = False

    if selected.get("status") == "PASS" and not isinstance(parsed, dict):
        repair = _chat(
            base_url=scillm_base_url,
            model=model,
            messages=_repair_messages(
                team,
                str(first.get("response_content") or first.get("raw_body_excerpt") or ""),
                _parse_error(first),
            ),
            timeout_s=float(timeout_s),
            api_key=api_key,
            response_format=True,
        )
        parsed = parse_json_object(str(repair.get("response_content") or ""))
        attempts.append(_attempt("json_repair", repair, parsed))
        selected = repair
        repair_used = True

    http_status = selected.get("http_status")
    transport_ok = selected.get("status") == "PASS" and int(http_status or 0) < 400
    parse_ok = isinstance(parsed, dict)

    return {
        "schema": "tau.scillm_call_receipt.v1",
        "surface": SURFACE,
        "status": "PASS" if transport_ok and parse_ok else "BLOCKED",
        "team": team,
        "persona": persona,
        "model": model,
        "mocked": False,
        "live": True,
        "duration_seconds": round(time.time() - started, 6),
        "http_status": http_status,
        "error": selected.get("error"),
        "api_key_source": api_key_source,
        "api_key_present": bool(api_key),
        "api_key_resolution_errors": api_key_errors,
        "caller_skill": CALLER_SKILL,
        "repair_used": repair_used,
        "parse_status": "PASS" if parse_ok else "BLOCKED",
        "parse_error": None if parse_ok else _parse_error(selected),
        "response_content": selected.get("response_content"),
        "parsed_json": parsed if parse_ok else None,
        "attempts": attempts,
    }


def call_battle_json_task(
    *,
    task: dict[str, Any],
    system_prompt: str,
    team: str,
    persona: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float | int,
) -> dict[str, Any]:
    """Run one bounded non-artifact Battle JSON task through Scillm."""
    started = time.time()
    api_key, api_key_source, api_key_errors = _resolve_api_key()
    result = _chat(
        base_url=scillm_base_url,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"TEAM: {team}\nPERSONA: {persona}\nTASK_JSON:\n"
                    f"{json.dumps(task, indent=2, sort_keys=True)}"
                ),
            },
        ],
        timeout_s=float(timeout_s),
        api_key=api_key,
        response_format=True,
    )
    parsed = parse_json_object(str(result.get("response_content") or ""))
    transport_ok = result.get("status") == "PASS" and int(result.get("http_status") or 0) < 400
    return {
        "schema": "tau.scillm_json_task_receipt.v1",
        "surface": SURFACE,
        "status": "PASS" if transport_ok and isinstance(parsed, dict) else "BLOCKED",
        "team": team,
        "persona": persona,
        "model": model,
        "mocked": False,
        "live": True,
        "duration_seconds": round(time.time() - started, 6),
        "http_status": result.get("http_status"),
        "error": result.get("error"),
        "api_key_source": api_key_source,
        "api_key_present": bool(api_key),
        "api_key_resolution_errors": api_key_errors,
        "caller_skill": CALLER_SKILL,
        "response_content": result.get("response_content"),
        "parsed_json": parsed if isinstance(parsed, dict) else None,
    }


def preflight_battle_scillm_auth(
    *,
    scillm_base_url: str,
    model: str,
    api_key: str | None = None,
    allow_repair: bool | None = None,
) -> dict[str, Any]:
    """Check Scillm auth before materializing Battle workers.

    The default behavior is fail-closed. Docker proxy recreation is only attempted
    when explicitly enabled by the caller or TAU_SCILLM_ALLOW_PROXY_RECREATE=1.
    """
    started = time.time()
    resolved_key, api_key_source, api_key_errors = (
        (api_key, "argument", []) if api_key else resolve_active_scillm_proxy_key()
    )
    repair_enabled = (
        allow_repair
        if allow_repair is not None
        else os.environ.get(DISABLE_STALE_AUTH_REPAIR_ENV, "").strip().lower()
        not in {"1", "true", "yes"}
    )
    payload: dict[str, Any] = {
        "schema": "tau.battle_scillm_auth_preflight.v1",
        "status": "BLOCKED",
        "ok": False,
        "mocked": False,
        "live": True,
        "surface": SURFACE,
        "model": model,
        "base_url": scillm_base_url.rstrip("/"),
        "endpoint": SCILLM_AUTH_ENDPOINT,
        "caller_skill": CALLER_SKILL,
        "api_key_source": api_key_source,
        "api_key_present": bool(resolved_key),
        "api_key_resolution_errors": api_key_errors,
        "repair_allowed": bool(repair_enabled),
        "repair_attempted": False,
        "repair_status": "not_requested",
        "errors": [],
    }
    if not resolved_key:
        payload["errors"] = ["Scillm auth preflight requires a proxy API key"]
        payload["duration_seconds"] = round(time.time() - started, 6)
        return payload

    first = _request_scillm_auth(scillm_base_url, resolved_key)
    payload["status_code"] = first.get("status_code")
    payload["auth_body"] = first.get("body")
    if first.get("status") != "PASS":
        payload["errors"] = [str(first.get("error") or "Scillm auth preflight request failed")]
        payload["duration_seconds"] = round(time.time() - started, 6)
        return payload

    problem = _codex_auth_problem(first.get("body"), model=model)
    if problem is None:
        payload["status"] = "PASS"
        payload["ok"] = True
        payload["duration_seconds"] = round(time.time() - started, 6)
        return payload

    diagnostics = _codex_auth_diagnostics()
    payload["diagnostics"] = diagnostics
    stale_mount = diagnostics.get("container_auth_stale") is True
    reason = "scillm_codex_oauth_stale_container_mount" if stale_mount else problem
    payload["reason"] = reason
    payload["errors"] = [_auth_blocker_message(reason, diagnostics)]

    if not repair_enabled or not stale_mount:
        payload["duration_seconds"] = round(time.time() - started, 6)
        return payload

    payload["repair_attempted"] = True
    repair = _recreate_scillm_proxy()
    payload["repair"] = repair
    payload["repair_status"] = repair["status"]
    if repair["status"] != "PASS":
        payload["errors"] = [
            "Scillm stale OAuth repair failed: "
            f"{repair.get('error') or repair.get('stderr_excerpt')}"
        ]
        payload["duration_seconds"] = round(time.time() - started, 6)
        return payload

    second = _request_scillm_auth(scillm_base_url, resolved_key)
    payload["post_repair_status_code"] = second.get("status_code")
    payload["post_repair_auth_body"] = second.get("body")
    post_repair_problem = _codex_auth_problem(second.get("body"), model=model)
    if second.get("status") == "PASS" and post_repair_problem is None:
        payload["status"] = "PASS"
        payload["ok"] = True
        payload["errors"] = []
    else:
        payload["errors"] = [
            f"Scillm auth remained blocked after proxy repair: "
            f"{post_repair_problem or second.get('error') or 'unknown'}"
        ]
    payload["duration_seconds"] = round(time.time() - started, 6)
    return payload


def resolve_active_scillm_proxy_key() -> tuple[str | None, str, list[str]]:
    """Resolve the active proxy key that child ScillM callers should inherit.

    Prefer the running Docker proxy environment because stale host variables are
    the common failure mode after OAuth/proxy restarts.
    """

    value, source, error = _docker_api_key()
    if value:
        return value, source, []
    errors = [error] if error else []
    fallback, fallback_source, fallback_errors = _resolve_api_key()
    errors.extend(fallback_errors)
    return fallback, fallback_source, errors


def parse_json_object(content: str) -> Any:
    """Parse one JSON object, tolerating fences and surrounding prose."""
    text = _strip_fence(content.strip())
    parsed = _loads(text)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        nested = _loads(parsed.strip())
        if isinstance(nested, dict):
            return nested
    for candidate in _balanced_objects(text):
        parsed = _loads(candidate)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            nested = _loads(parsed.strip())
            if isinstance(nested, dict):
                return nested
    return None


def _request_scillm_auth(base_url: str, api_key: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{SCILLM_AUTH_ENDPOINT}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Caller-Skill": CALLER_SKILL,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body_text = response.read().decode("utf-8", "replace")
            body = _loads(body_text)
            if not isinstance(body, dict):
                return {
                    "status": "BLOCKED",
                    "status_code": response.status,
                    "body": None,
                    "error": "Scillm auth preflight returned non-JSON response",
                }
            return {"status": "PASS", "status_code": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        return {
            "status": "BLOCKED",
            "status_code": exc.code,
            "body": _loads(exc.read().decode("utf-8", "replace")),
            "error": f"Scillm auth preflight failed with HTTP {exc.code}",
        }
    except (urllib.error.URLError, TimeoutError) as exc:
        return {
            "status": "BLOCKED",
            "status_code": None,
            "body": None,
            "error": f"Scillm auth preflight request failed: {exc}",
        }


def _codex_auth_problem(body: Any, *, model: str) -> str | None:
    if not str(model).startswith("gpt-"):
        return None
    if not isinstance(body, dict):
        return "scillm_auth_body_missing"
    codex = body.get("codex")
    if not isinstance(codex, dict):
        return "scillm_codex_auth_missing"
    status = str(codex.get("status") or "").strip().lower()
    if status in HEALTHY_CODEX_AUTH_STATUSES:
        return None
    if not status:
        return "scillm_codex_auth_status_missing"
    return f"scillm_codex_auth_{status}"


def _codex_auth_diagnostics() -> dict[str, Any]:
    host_path = Path.home() / ".codex" / "auth.json"
    host = _file_stat(host_path)
    containers = []
    stale = False
    for container in ("docker-scillm-proxy-1", "scillm-proxy"):
        container_stat = _docker_file_stat(container, "/root/.codex/auth.json")
        containers.append(container_stat)
        if (
            host.get("exists") is True
            and container_stat.get("exists") is True
            and host.get("sha256")
            and container_stat.get("sha256")
            and host.get("sha256") != container_stat.get("sha256")
        ):
            stale = True
    return {
        "host_auth": host,
        "container_auth": containers,
        "container_auth_stale": stale,
        "repair_command": " ".join(DEFAULT_SCILLM_REPAIR_COMMAND),
        "repair_cwd": str(DEFAULT_SCILLM_COMPOSE_DIR),
    }


def _file_stat(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "sha256": _sha256(path),
        }
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    except OSError as exc:
        return {"path": str(path), "exists": False, "error": str(exc)}


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _docker_file_stat(container: str, path: str) -> dict[str, Any]:
    command = [
        "docker",
        "exec",
        container,
        "python3",
        "-c",
        (
            "import hashlib,json,os,sys;"
            "p=sys.argv[1];"
            "exists=os.path.exists(p);"
            "data={'container':sys.argv[2],'path':p,'exists':exists};"
            "\nif exists:\n"
            " st=os.stat(p); data.update(size=st.st_size,mtime=st.st_mtime);"
            " h=hashlib.sha256();"
            " f=open(p,'rb');"
            "\n for chunk in iter(lambda:f.read(1048576), b''): h.update(chunk)\n"
            " f.close(); data['sha256']=h.hexdigest();"
            "\nprint(json.dumps(data, sort_keys=True))"
        ),
        path,
        container,
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return {"container": container, "path": path, "exists": False, "error": "docker_not_found"}
    except subprocess.TimeoutExpired:
        return {
            "container": container,
            "path": path,
            "exists": False,
            "error": "docker_exec_timeout",
        }
    if result.returncode != 0:
        return {
            "container": container,
            "path": path,
            "exists": False,
            "error": result.stderr.strip().replace("\n", " ")[:300],
        }
    data = _loads(result.stdout)
    if isinstance(data, dict):
        return data
    return {
        "container": container,
        "path": path,
        "exists": False,
        "error": "docker_stat_returned_non_json",
    }


def _recreate_scillm_proxy() -> dict[str, Any]:
    if not DEFAULT_SCILLM_COMPOSE_DIR.is_dir():
        return {
            "status": "BLOCKED",
            "error": f"scillm_compose_dir_missing:{DEFAULT_SCILLM_COMPOSE_DIR}",
        }
    try:
        result = subprocess.run(
            DEFAULT_SCILLM_REPAIR_COMMAND,
            cwd=DEFAULT_SCILLM_COMPOSE_DIR,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return {"status": "BLOCKED", "error": "docker_compose_not_found"}
    except subprocess.TimeoutExpired:
        return {"status": "BLOCKED", "error": "docker_compose_recreate_timeout"}
    return {
        "status": "PASS" if result.returncode == 0 else "BLOCKED",
        "returncode": result.returncode,
        "command": DEFAULT_SCILLM_REPAIR_COMMAND,
        "cwd": str(DEFAULT_SCILLM_COMPOSE_DIR),
        "stdout_excerpt": result.stdout[-2000:],
        "stderr_excerpt": result.stderr[-2000:],
    }


def _auth_blocker_message(reason: str, diagnostics: dict[str, Any]) -> str:
    if reason == "scillm_codex_oauth_stale_container_mount":
        return (
            "Scillm Codex OAuth is stale inside the proxy container; recreate the proxy "
            f"from {diagnostics.get('repair_cwd')} with: {diagnostics.get('repair_command')}"
        )
    if reason.startswith("scillm_codex_auth_expired"):
        return (
            "Scillm Codex OAuth is expired; run codex login on the host, then rerun auth preflight"
        )
    return f"Scillm Codex auth preflight blocked Battle worker materialization: {reason}"


def _resolve_api_key() -> tuple[str, str, list[str]]:
    errors: list[str] = []
    value = os.environ.get("SCILLM_API_KEY")
    if value:
        return value, "env:SCILLM_API_KEY", errors
    value = os.environ.get("SCILLM_MASTER_KEY")
    if value:
        return value, "env:SCILLM_MASTER_KEY", errors
    value, source, error = _docker_api_key()
    if value:
        return value, source, errors
    if error:
        errors.append(error)
    value = os.environ.get("SCILLM_PROXY_KEY")
    if value:
        return value, "env:SCILLM_PROXY_KEY", errors
    return DEFAULT_DEV_PROXY_KEY, "default:sk-dev-proxy-123", errors


def _docker_api_key() -> tuple[str | None, str, str | None]:
    errors: list[str] = []
    for container in ("docker-scillm-proxy-1", "scillm-proxy"):
        value, key_name, error = _docker_env_key(container)
        if value:
            return value, f"docker:{container}:{key_name}", None
        if error:
            errors.append(error)
    return None, "", ";".join(errors) if errors else "docker_scillm_proxy_key_missing"


def _docker_env_key(container: str) -> tuple[str | None, str, str | None]:
    command = [
        "docker",
        "inspect",
        container,
        "--format",
        "{{range .Config.Env}}{{println .}}{{end}}",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return None, "", "docker_not_found"
    except subprocess.TimeoutExpired:
        return None, "", f"docker_inspect_{container}_timeout"
    except OSError as exc:
        return None, "", f"docker_inspect_{container}_error:{exc}"
    if result.returncode != 0:
        stderr = result.stderr.strip().replace("\n", " ")[:300]
        return None, "", f"docker_inspect_{container}_exit_{result.returncode}:{stderr}"
    env = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    for key_name in ("SCILLM_MASTER_KEY", "SCILLM_PROXY_KEY"):
        key = env.get(key_name, "").strip()
        if key:
            return key, key_name, None
    return None, "", f"docker_{container}_scillm_key_missing"


def _artifact_messages(team: str, persona: str, handoff: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Return exactly one JSON object and nothing else. No markdown. "
                "Use only team-public handoff content. Do not include chain-of-thought. "
                "Red schema: {artifact_type:red_exploit, exploit_py:<complete python "
                "script containing RED_EXPLOIT_CONFIRMED>, rationale:<brief>, "
                "strategy_genome:{selected_methods:[], rejected_methods:[], parameters:{}, "
                "mutation_origin:<string>, expected_observation:<string>}}. "
                "Blue schema: {artifact_type:blue_patch, app_py:<complete replacement "
                "app.py>, rationale:<brief>, strategy_genome:{selected_methods:[], "
                "rejected_methods:[], parameters:{}, mutation_origin:<string>, "
                "expected_observation:<string>}}."
            ),
        },
        {
            "role": "user",
            "content": (
                f"TEAM: {team}\nPERSONA: {persona}\n"
                f"PUBLIC_BATTLE_HANDOFF_JSON:\n{json.dumps(handoff, indent=2, sort_keys=True)}\n"
                "Return the required JSON object now."
            ),
        },
    ]


def _repair_messages(team: str, raw_response: str, parse_error: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Repair invalid Battle subagent output into exactly one valid JSON "
                "object. No markdown or prose."
            ),
        },
        {
            "role": "user",
            "content": (
                f"TEAM: {team}\nPARSE_ERROR: {parse_error}\nRAW_RESPONSE:\n"
                f"{raw_response}\nReturn one valid JSON object."
            ),
        },
    ]


def _chat(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_s: float,
    api_key: str,
    response_format: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 8192,
    }
    if response_format:
        body["response_format"] = {"type": "json_object"}
    response = _post_json(f"{base_url.rstrip('/')}/v1/chat/completions", body, timeout_s, api_key)
    if (
        response_format
        and response.get("status") == "HTTP_ERROR"
        and int(response.get("http_status") or 0) in {400, 404, 422}
    ):
        body.pop("response_format", None)
        retry = _post_json(f"{base_url.rstrip('/')}/v1/chat/completions", body, timeout_s, api_key)
        retry["response_format_retry_reason"] = _safe_format_error(response)
        return _completion_record(retry)
    return _completion_record(response)


def _post_json(url: str, body: dict[str, Any], timeout_s: float, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Caller-Skill": CALLER_SKILL,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return {
                "status": "PASS",
                "http_status": response.status,
                "body_text": response.read().decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": "HTTP_ERROR",
            "http_status": exc.code,
            "body_text": exc.read().decode("utf-8", "replace"),
            "error": str(exc),
        }
    except urllib.error.URLError as exc:
        return {"status": "NETWORK_ERROR", "http_status": None, "body_text": "", "error": str(exc)}
    except TimeoutError as exc:
        return {"status": "TIMEOUT", "http_status": None, "body_text": "", "error": str(exc)}


def _completion_record(response: dict[str, Any]) -> dict[str, Any]:
    body = str(response.get("body_text") or "")
    decoded = _loads(body)
    content = _message_content(decoded) if isinstance(decoded, dict) else ""
    return {
        "status": response.get("status"),
        "http_status": response.get("http_status"),
        "error": response.get("error"),
        "response_json": decoded,
        "response_content": content,
        "raw_body_excerpt": body[:4000],
        "response_format_retry_reason": response.get("response_format_retry_reason"),
    }


def _message_content(decoded: dict[str, Any]) -> str:
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                _content_part_text(part) for part in content if _content_part_text(part)
            )
    text = choices[0].get("text")
    return text if isinstance(text, str) else ""


def _content_part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        for key in ("text", "content"):
            value = part.get(key)
            if isinstance(value, str):
                return value
    return ""


def _attempt(kind: str, completion: dict[str, Any], parsed: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "http_status": completion.get("http_status"),
        "transport_status": completion.get("status"),
        "error": completion.get("error"),
        "parse_status": "PASS" if isinstance(parsed, dict) else "BLOCKED",
        "parsed_keys": sorted(parsed) if isinstance(parsed, dict) else [],
        "response_content_excerpt": str(completion.get("response_content") or "")[:4000],
        "raw_body_excerpt": str(completion.get("raw_body_excerpt") or "")[:4000],
        "response_format_retry_reason": completion.get("response_format_retry_reason"),
    }


def _parse_error(completion: dict[str, Any]) -> str:
    if completion.get("error"):
        return str(completion["error"])
    http_status = completion.get("http_status")
    if http_status and int(http_status) >= 400:
        return f"http_status_{http_status}"
    if not str(completion.get("response_content") or ""):
        return (
            "empty_message_content"
            if completion.get("raw_body_excerpt")
            else "empty_response_content"
        )
    return "response_content_not_parseable_json_object"


def _safe_format_error(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": response.get("status"),
        "http_status": response.get("http_status"),
        "error": response.get("error"),
        "raw_body_excerpt": str(response.get("body_text") or "")[:1000],
    }


def _loads(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def _strip_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _balanced_objects(text: str) -> list[str]:
    candidates: list[str] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
    return candidates
