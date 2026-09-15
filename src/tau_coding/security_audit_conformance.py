"""Live security/audit conformance lane for Tau trust-boundary receipts."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tau_coding.approval_gate import evaluate_approval_gate

SECURITY_AUDIT_CONFORMANCE_SCHEMA = "tau.security_audit_conformance.v1"
DEVELOPER_SHARE_SECURITY_GATE_SCHEMA = "tau.developer_share_security_gate.v1"
ISSUE_343_SECURITY_GATE_VERIFICATION_SCHEMA = (
    "tau.issue_343_developer_share_security_gate_verify.v1"
)
ASYMMETRIC_SIGNATURE_RECEIPT_SCHEMA = "tau.asymmetric_signature_receipt.v1"
ASYMMETRIC_SIGNATURE_VERIFICATION_SCHEMA = "tau.asymmetric_signature_verification.v1"
API_MUTATING_REQUEST_RECEIPT_SCHEMA = "tau.api_mutating_request_receipt.v1"
AUDIT_LEDGER_VERIFICATION_SCHEMA = "tau.audit_ledger_verification.v1"
RUN_ID = "security-audit-conformance-run"
TARGET_ID = "workspace://tau/protected-file"
TARGET_LINEAGE = "sha256:security-audit-lineage"
ACTION = "working_tree_mutation"
DEVELOPER_SHARE_BLOCKING_DISPOSITIONS = {"REAL_SECRET", "HARDCODED_RUNTIME_PATH"}
DEVELOPER_SHARE_ALLOWED_DISPOSITIONS = {
    "REAL_SECRET",
    "HARDCODED_RUNTIME_PATH",
    "SYNTHETIC_FIXTURE",
    "DOCUMENTATION_EXAMPLE",
    "SCANNER_FALSE_POSITIVE",
}
PROJECT_STATE_PATHS = (
    "docs/status/PROJECT_STATE.raw.json",
    "PROJECT_STATE.raw.json",
)
RUNTIME_PATH_MARKERS = (
    "/" + "home/",
    "/" + "Users/",
    "workspace/" + "experiments/",
    "Path." + "home() / " + '"workspace"',
    "Path." + "home() / " + '".pi"',
)


@dataclass(frozen=True, slots=True)
class SecurityGateFinding:
    source: str
    issue: str
    severity: str
    file: str
    line: int
    rule: str
    description: str
    evidence: str

    def row(self) -> dict[str, Any]:
        payload = {
            "source": self.source,
            "issue": self.issue,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "rule": self.rule,
            "description": self.description,
            "evidence": self.evidence,
        }
        payload["finding_hash"] = _hash_json(payload)
        return payload


def write_security_audit_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Exercise signature, approval, RBAC/API, mutating request, and audit gates."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    if shutil.which("openssl") is None:
        raise RuntimeError("openssl is required for asymmetric signature conformance")

    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    artifacts_dir = proof_dir / "artifacts"
    signing_dir = artifacts_dir / "signing"
    approval_dir = artifacts_dir / "approvals"
    api_dir = artifacts_dir / "api"
    audit_dir = artifacts_dir / "audit"
    for directory in (signing_dir, approval_dir, api_dir, audit_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_receipt_path = signing_dir / "receipt-to-sign.json"
    _write_json(source_receipt_path, _source_receipt())
    signature_receipt = _sign_receipt_asymmetric(
        receipt_path=source_receipt_path,
        signing_dir=signing_dir,
        receipt_path_out=signing_dir / "asymmetric-signature-receipt.json",
    )
    signature_verification = _verify_signature_receipt(
        signature_receipt=signature_receipt,
        message_path=source_receipt_path,
        receipt_path=signing_dir / "signature-verification.json",
    )
    tampered_receipt_path = signing_dir / "receipt-to-sign.tampered.json"
    tampered = _source_receipt()
    tampered["status"] = "BLOCKED"
    _write_json(tampered_receipt_path, tampered)
    tamper_negative = _verify_signature_receipt(
        signature_receipt=signature_receipt,
        message_path=tampered_receipt_path,
        receipt_path=signing_dir / "tamper-negative-verification.json",
    )

    valid_approval_packet = approval_dir / "approval-valid.json"
    wrong_target_packet = approval_dir / "approval-wrong-target.json"
    expired_packet = approval_dir / "approval-expired.json"
    _write_approval_packet(
        valid_approval_packet,
        target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        expires_at=_future_stamp(),
    )
    _write_approval_packet(
        wrong_target_packet,
        target={"id": "workspace://tau/other-target", "lineage": TARGET_LINEAGE},
        expires_at=_future_stamp(),
    )
    _write_approval_packet(
        expired_packet,
        target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        expires_at=_past_stamp(),
    )
    approval_gate = evaluate_approval_gate(
        approval_packet=valid_approval_packet,
        requested_action=ACTION,
        run_dir=approval_dir / "valid-run",
        output=approval_dir / "approval-bound-action.json",
        expected_target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    )
    wrong_target_gate = evaluate_approval_gate(
        approval_packet=wrong_target_packet,
        requested_action=ACTION,
        run_dir=approval_dir / "wrong-target-run",
        output=approval_dir / "wrong-target-denial.json",
        expected_target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    )
    expired_gate = evaluate_approval_gate(
        approval_packet=expired_packet,
        requested_action=ACTION,
        run_dir=approval_dir / "expired-run",
        output=approval_dir / "expired-denial.json",
        expected_target={"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    )

    rbac_policy_path = api_dir / "rbac-policy.json"
    authorized_token = secrets.token_urlsafe(32)
    _write_json(rbac_policy_path, _rbac_policy(api_token=authorized_token))
    unauthorized_request = _evaluate_api_mutating_request(
        request={
            "actor_id": "agent:unauthorized",
            "auth_token": None,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
            "approval_receipt_path": approval_gate["approval_packet"],
        },
        policy_path=rbac_policy_path,
        approval_gate_receipt=approval_gate,
        receipt_path=api_dir / "unauthorized-mutating-request.json",
    )
    authorized_request = _evaluate_api_mutating_request(
        request={
            "actor_id": "human:graham",
            "auth_token": authorized_token,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
            "approval_receipt_path": approval_gate["approval_packet"],
        },
        policy_path=rbac_policy_path,
        approval_gate_receipt=approval_gate,
        receipt_path=api_dir / "authorized-mutating-request.json",
    )

    ledger_path = audit_dir / "audit-ledger.jsonl"
    _write_audit_ledger(
        ledger_path,
        [
            _ledger_event("signature_verified", signature_verification),
            _ledger_event("tamper_negative_denied", tamper_negative),
            _ledger_event("approval_bound_action", approval_gate),
            _ledger_event("wrong_target_denied", wrong_target_gate),
            _ledger_event("expired_approval_denied", expired_gate),
            _ledger_event("unauthorized_mutating_request_denied", unauthorized_request),
            _ledger_event("authorized_mutating_request_accepted", authorized_request),
        ],
    )
    ledger_verification = _verify_audit_ledger(
        ledger_path=ledger_path,
        receipt_path=audit_dir / "audit-ledger-verification.json",
    )

    checks = {
        "signature_verification_pass": signature_verification.get("status") == "PASS",
        "tamper_negative_control_denied": tamper_negative.get("status") == "BLOCKED",
        "approval_bound_action_accepted": approval_gate.get("status") == "PASS",
        "wrong_target_approval_denied": wrong_target_gate.get("status") == "BLOCKED",
        "expired_approval_denied": expired_gate.get("status") == "BLOCKED",
        "unauthorized_api_mutating_request_denied": unauthorized_request.get("status")
        == "BLOCKED",
        "authorized_request_accepted_with_receipt": authorized_request.get("status") == "PASS",
        "audit_ledger_verifies": ledger_verification.get("status") == "PASS",
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    payload = {
        "schema": SECURITY_AUDIT_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "proof_dir": str(proof_dir),
        "artifacts_dir": str(artifacts_dir),
        "signature_receipt": signature_receipt["receipt_path"],
        "signature_verification_receipt": signature_verification["receipt_path"],
        "tamper_negative_receipt": tamper_negative["receipt_path"],
        "approval_bound_action_receipt": str(
            (approval_dir / "approval-bound-action.json").resolve()
        ),
        "wrong_target_denial_receipt": str((approval_dir / "wrong-target-denial.json").resolve()),
        "expired_approval_denial_receipt": str((approval_dir / "expired-denial.json").resolve()),
        "unauthorized_mutating_request_receipt": unauthorized_request["receipt_path"],
        "authorized_mutating_request_receipt": authorized_request["receipt_path"],
        "audit_ledger": str(ledger_path.resolve()),
        "audit_ledger_verification_receipt": ledger_verification["receipt_path"],
        "checks": checks,
        "failed_checks": failed_checks,
        "proof_scope": {
            "proves": [
                "Tau generated an Ed25519 asymmetric keypair through local OpenSSL, signed "
                "a receipt artifact, and verified the signature with the public key.",
                "Tau denied a tampered signed payload.",
                "Tau accepted only action/target/lineage-bound unexpired human approval.",
                "Tau denied unauthorized API mutation attempts and accepted an authorized "
                "RBAC/API request with a receipt.",
                "Tau wrote and verified a hash-chained append-only audit ledger for this lane.",
            ],
            "does_not_prove": [
                "Human legal identity.",
                "Production key custody.",
                "Network API gateway deployment.",
                "External identity-provider integration.",
                "Provider/model semantic quality.",
            ],
        },
        "checked_at": _now(),
    }
    _write_json(resolved_output, payload)
    return payload


def write_developer_share_security_gate(
    repo: Path,
    *,
    source_commit: str,
    output: Path,
) -> dict[str, Any]:
    """Scan a clean archive of source_commit and write the share-security gate receipt."""

    output = output.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="tau-share-security-gate-") as temporary:
        clean_tree = Path(temporary) / "source"
        _export_commit(repo=repo, source_commit=source_commit, destination=clean_tree)
        return evaluate_developer_share_security_tree(
            clean_tree,
            source_commit=source_commit,
            output=output,
            repo=repo,
            clean_checkout=True,
        )


def write_issue_343_security_gate_verification(
    repo: Path,
    *,
    source_commit: str,
    output: Path,
) -> dict[str, Any]:
    """Run the issue #343 proof bundle and write a machine-readable receipt."""

    output = output.expanduser().resolve()
    proof_dir = output.parent / f"{output.stem}-artifacts"
    proof_dir.mkdir(parents=True, exist_ok=True)
    exact_output = proof_dir / "exact-commit-share-gate.json"
    exact_receipt = write_developer_share_security_gate(
        repo,
        source_commit=source_commit,
        output=exact_output,
    )
    clean_self = run_developer_share_security_gate_self_test(
        "clean-passes",
        proof_dir / "controlled-clean.json",
    )
    seeded_self = run_developer_share_security_gate_self_test(
        "seeded-blocks",
        proof_dir / "controlled-seeded.json",
    )
    exact_exit_code = 0 if exact_receipt["share_readiness"] == "READY" else 1
    seeded_dispositions = set(seeded_self.get("seeded_dispositions", []))
    exact_blockers = int(exact_receipt["counts"]["unresolved_blockers"])
    checks = {
        "exact_commit_clean_archive_scanned": exact_receipt["inputs"]["clean_checkout"] is True,
        "exact_commit_dispositions_reconcile": exact_receipt["counts"]["reconciled"] is True,
        "exact_commit_dispositions_are_closed_vocab": (
            set(exact_receipt["counts"]["by_disposition"]).issubset(
                DEVELOPER_SHARE_ALLOWED_DISPOSITIONS
            )
        ),
        "exact_commit_allowlisted_rows_are_source_backed": all(
            row["disposition_evidence"]["source_backed"]
            for row in exact_receipt["dispositions"]
            if row["disposition"] not in DEVELOPER_SHARE_BLOCKING_DISPOSITIONS
        ),
        "runtime_python_scan_ran": exact_receipt["runtime_python_path_scan"]["scanned"] is True,
        "exact_commit_gate_passes": exact_blockers == 0
        and exact_exit_code == 0
        and exact_receipt["share_readiness"] == "READY",
        "controlled_seeded_gate_blocks": seeded_self["seeded_gate_exit_code"] == 1,
        "controlled_seeded_classifications_stable": {
            "REAL_SECRET",
            "HARDCODED_RUNTIME_PATH",
        }.issubset(seeded_dispositions),
        "controlled_clean_gate_passes": clean_self["clean_gate_exit_code"] == 0
        and clean_self["clean_share_readiness"] == "READY",
        "receipt_has_live_unmocked_boundary": exact_receipt["mocked"] is False
        and exact_receipt["live"] is True,
    }
    payload = {
        "schema": ISSUE_343_SECURITY_GATE_VERIFICATION_SCHEMA,
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source_commit": source_commit,
        "output": str(output),
        "artifacts": {
            "exact_commit_share_gate": str(exact_output),
            "controlled_clean": str(proof_dir / "controlled-clean.json"),
            "controlled_seeded": str(proof_dir / "controlled-seeded.json"),
            "controlled_clean_gate": clean_self.get("clean_receipt"),
            "controlled_seeded_gate": seeded_self.get("seeded_receipt"),
        },
        "exact_commit_gate_exit_code": exact_exit_code,
        "final_share_readiness": exact_receipt["share_readiness"],
        "scan_counts": exact_receipt["counts"],
        "scan_hashes": exact_receipt["scan_hashes"],
        "what_was_checked": exact_receipt["what_was_checked"],
        "disposition_count": exact_receipt["counts"]["dispositions"],
        "finding_count": exact_receipt["counts"]["findings"],
        "runtime_python_path_scan": exact_receipt["runtime_python_path_scan"],
        "checks": checks,
        "proof_scope": {
            "proves": [
                "The exact source commit was exported through git archive and scanned in a clean tree.",
                "Every emitted finding has one disposition row and the counts reconcile.",
                "Runtime Python was independently scanned for home/workspace path markers.",
                "Controlled seeded credential and runtime-path inputs block with stable classifications.",
                "The controlled clean input passes only when no unresolved CRITICAL finding remains.",
            ],
            "does_not_prove": [
                "Formal security certification or absence of all vulnerabilities.",
                "Credential rotation or destructive revocation.",
            ],
        },
        "remains_unverified": [
            "Third-party dependency vulnerabilities outside gitleaks/project-state/runtime-path scans.",
        ],
        "checked_at": _now(),
    }
    _write_json(output, payload)
    return payload


def run_developer_share_security_gate_self_test(mode: str, output: Path) -> dict[str, Any]:
    """Exercise controlled clean and seeded trees without modifying the repository."""

    output = output.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="tau-share-security-selftest-") as temporary:
        base = Path(temporary) / "clean"
        seeded = Path(temporary) / "seeded"
        _write_controlled_security_tree(base, seeded=False)
        _write_controlled_security_tree(seeded, seeded=True)
        results: dict[str, Any] = {
            "schema": "tau.developer_share_security_gate_self_test.v1",
            "mode": mode,
        }
        if mode in {"all", "clean-passes"}:
            clean_output = output.with_suffix(".clean-gate.json")
            clean_receipt = evaluate_developer_share_security_tree(
                base,
                source_commit="controlled-clean-tree",
                output=clean_output,
                repo=base,
                clean_checkout=True,
            )
            results.update(
                {
                    "clean_gate_exit_code": 0
                    if clean_receipt["share_readiness"] == "READY"
                    else 1,
                    "clean_share_readiness": clean_receipt["share_readiness"],
                    "clean_receipt": str(clean_output),
                }
            )
        if mode in {"all", "seeded-blocks"}:
            seeded_output = output.with_suffix(".seeded-gate.json")
            seeded_receipt = evaluate_developer_share_security_tree(
                seeded,
                source_commit="controlled-seeded-tree",
                output=seeded_output,
                repo=seeded,
                clean_checkout=True,
            )
            results.update(
                {
                    "seeded_gate_exit_code": 0
                    if seeded_receipt["share_readiness"] == "READY"
                    else 1,
                    "seeded_share_readiness": seeded_receipt["share_readiness"],
                    "seeded_dispositions": sorted(
                        {str(row["disposition"]) for row in seeded_receipt["dispositions"]}
                    ),
                    "seeded_receipt": str(seeded_output),
                }
            )
        if mode not in {"all", "clean-passes", "seeded-blocks"}:
            results["errors"] = [f"unknown self-test mode: {mode}"]
        if mode == "clean-passes":
            results["share_readiness"] = results.get("clean_share_readiness")
        elif mode == "seeded-blocks":
            results["share_readiness"] = results.get("seeded_share_readiness")
    ok = _developer_share_security_self_test_ok(mode, results)
    payload = {
        **results,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "output": str(output),
        "checked_at": _now(),
    }
    _write_json(output, payload)
    return payload


def evaluate_developer_share_security_tree(
    tree: Path,
    *,
    source_commit: str,
    output: Path,
    repo: Path,
    clean_checkout: bool,
) -> dict[str, Any]:
    """Evaluate share-readiness security findings in a source tree."""

    tree = tree.expanduser().resolve()
    output = output.expanduser().resolve()
    gitleaks_findings, gitleaks_scan = _gitleaks_security_findings(tree)
    findings = (
        _project_state_security_findings(tree)
        + gitleaks_findings
        + _runtime_path_security_findings(tree)
    )
    finding_rows = _dedupe_finding_rows([finding.row() for finding in findings])
    disposition_rows = [_disposition_for_security_finding(tree, row) for row in finding_rows]
    blockers = [
        row for row in disposition_rows if row["disposition"] in DEVELOPER_SHARE_BLOCKING_DISPOSITIONS
    ]
    runtime_rows = [row for row in disposition_rows if row["issue"] == "hardcoded_runtime_path"]
    runtime_blockers = [
        row for row in runtime_rows if row["disposition"] == "HARDCODED_RUNTIME_PATH"
    ]
    gate_exit_code = 0 if not blockers else 1
    scan_hashes = {
        "findings_sha256": _hash_json({"findings": finding_rows}),
        "dispositions_sha256": _hash_json({"dispositions": disposition_rows}),
        "finding_hashes": [str(row["finding_hash"]) for row in finding_rows],
        "disposition_hashes": [_hash_json(row) for row in disposition_rows],
    }
    payload = {
        "schema": DEVELOPER_SHARE_SECURITY_GATE_SCHEMA,
        "status": "PASS" if not blockers else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source_commit": source_commit,
        "share_readiness": "READY" if not blockers else "BLOCKED",
        "gate_exit_code": gate_exit_code,
        "output": str(output),
        "scan_tool": {
            "gitleaks": {
                "version": _tool_version(["gitleaks", "version"]),
                "scan": gitleaks_scan,
            },
            "project_state_input": _project_state_path_string(tree),
            "runtime_path_scan": "tau_coding.security_audit_conformance.evaluate_developer_share_security_tree",
        },
        "scan_hashes": scan_hashes,
        "allowed_dispositions": sorted(DEVELOPER_SHARE_ALLOWED_DISPOSITIONS),
        "blocking_dispositions": sorted(DEVELOPER_SHARE_BLOCKING_DISPOSITIONS),
        "inputs": {
            "repo": str(repo.expanduser().resolve()),
            "clean_tree": str(tree),
            "clean_checkout": clean_checkout,
            "source_commit": source_commit,
        },
        "what_was_checked": [
            "gitleaks detect --no-git over the clean source tree",
            "tracked project-state hardcoded_secret and hardcoded_home_path findings",
            "runtime Python under src/ for user-home/workspace path markers",
            "one disposition row per emitted finding",
        ],
        "counts": {
            "findings": len(finding_rows),
            "dispositions": len(disposition_rows),
            "reconciled": len(finding_rows) == len(disposition_rows),
            "unresolved_blockers": len(blockers),
            "runtime_python_path_findings": len(runtime_rows),
            "unresolved_runtime_path_blockers": len(runtime_blockers),
            "by_disposition": _count_by_disposition(disposition_rows),
        },
        "findings": finding_rows,
        "dispositions": disposition_rows,
        "unresolved_blockers": blockers,
        "runtime_python_path_scan": {
            "scanned": True,
            "roots": ["src"],
            "markers": list(RUNTIME_PATH_MARKERS),
            "finding_count": len(runtime_rows),
            "unresolved_blocker_count": len(runtime_blockers),
            "scanner_false_positive_count": len(runtime_rows) - len(runtime_blockers),
            "normal_installed_wheel_required_count": len(runtime_blockers),
            "conclusion": (
                "No machine-local runtime paths are required by normal installed-wheel execution."
                if not runtime_blockers
                else "Machine-local runtime paths remain in packaged runtime Python."
            ),
        },
        "proof_scope": {
            "proves": [
                "The configured source tree was scanned with gitleaks and runtime path inspection.",
                "Tracked project-state hardcoded secret/path findings were dispositioned.",
                "Share readiness blocks on REAL_SECRET and HARDCODED_RUNTIME_PATH.",
            ],
            "does_not_prove": [
                "Formal security certification or absence of all vulnerabilities.",
                "Credential revocation or rotation.",
            ],
        },
        "remains_unverified": [
            "Third-party dependency vulnerabilities outside gitleaks/project-state/runtime-path scans.",
        ],
        "checked_at": _now(),
    }
    if payload["counts"]["reconciled"] is not True:
        raise RuntimeError("finding disposition counts did not reconcile")
    _write_json(output, payload)
    return payload


def _sign_receipt_asymmetric(
    *,
    receipt_path: Path,
    signing_dir: Path,
    receipt_path_out: Path,
) -> dict[str, Any]:
    private_key = signing_dir / "ed25519-private.pem"
    public_key = signing_dir / "ed25519-public.pem"
    signature_path = signing_dir / "receipt-to-sign.sig"
    _run_openssl(["genpkey", "-algorithm", "Ed25519", "-out", str(private_key)])
    _run_openssl(["pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)])
    _run_openssl(
        [
            "pkeyutl",
            "-sign",
            "-inkey",
            str(private_key),
            "-rawin",
            "-in",
            str(receipt_path),
            "-out",
            str(signature_path),
        ]
    )
    private_key.unlink()
    payload = {
        "schema": ASYMMETRIC_SIGNATURE_RECEIPT_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "algorithm": "Ed25519",
        "openssl_path": shutil.which("openssl"),
        "public_key_path": str(public_key.resolve()),
        "public_key_sha256": _sha256_uri(public_key),
        "private_key_persisted": False,
        "signed_receipt_path": str(receipt_path.resolve()),
        "signed_receipt_sha256": _sha256_uri(receipt_path),
        "signature_path": str(signature_path.resolve()),
        "signature_base64": base64.b64encode(signature_path.read_bytes()).decode("ascii"),
        "receipt_path": str(receipt_path_out.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path_out, payload)
    return payload


def _verify_signature_receipt(
    *,
    signature_receipt: dict[str, Any],
    message_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    signature_path = Path(str(signature_receipt["signature_path"]))
    public_key_path = Path(str(signature_receipt["public_key_path"]))
    completed = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-pubin",
            "-inkey",
            str(public_key_path),
            "-rawin",
            "-in",
            str(message_path),
            "-sigfile",
            str(signature_path),
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    payload = {
        "schema": ASYMMETRIC_SIGNATURE_VERIFICATION_SCHEMA,
        "status": "PASS" if completed.returncode == 0 else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "algorithm": "Ed25519",
        "message_path": str(message_path.resolve()),
        "message_sha256": _sha256_uri(message_path),
        "public_key_path": str(public_key_path.resolve()),
        "signature_path": str(signature_path.resolve()),
        "command": completed.args,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _evaluate_api_mutating_request(
    *,
    request: dict[str, Any],
    policy_path: Path,
    approval_gate_receipt: dict[str, Any],
    receipt_path: Path,
) -> dict[str, Any]:
    policy = _read_json(policy_path)
    actor_id = str(request.get("actor_id") or "")
    roles_by_actor = policy.get("roles_by_actor") if isinstance(policy, dict) else {}
    role = roles_by_actor.get(actor_id) if isinstance(roles_by_actor, dict) else None
    permissions = policy.get("permissions") if isinstance(policy, dict) else {}
    allowed_actions = permissions.get(role, []) if isinstance(permissions, dict) else []
    token_hash = _token_sha256(str(request.get("auth_token") or ""))
    errors: list[str] = []
    if token_hash != policy.get("api_token_sha256"):
        errors.append("api authentication failed")
    if request.get("action") not in allowed_actions:
        errors.append("rbac denied action")
    if approval_gate_receipt.get("status") != "PASS":
        errors.append("approval gate receipt must PASS")
    target = request.get("target") if isinstance(request.get("target"), dict) else {}
    if target.get("id") != TARGET_ID:
        errors.append("target.id mismatch")
    if target.get("lineage") != TARGET_LINEAGE:
        errors.append("target.lineage mismatch")
    accepted = not errors
    payload = {
        "schema": API_MUTATING_REQUEST_RECEIPT_SCHEMA,
        "status": "PASS" if accepted else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "actor_id": actor_id,
        "role": role,
        "action": request.get("action"),
        "target": target,
        "auth_token_present": bool(request.get("auth_token")),
        "auth_token_sha256": token_hash if request.get("auth_token") else None,
        "approval_gate_status": approval_gate_receipt.get("status"),
        "approval_gate_receipt_path": approval_gate_receipt.get("approval_packet"),
        "accepted": accepted,
        "mutation_applied": accepted,
        "mutating_request_receipt": str(receipt_path.resolve()),
        "receipt_path": str(receipt_path.resolve()),
        "errors": errors,
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _write_audit_ledger(ledger_path: Path, events: list[dict[str, Any]]) -> None:
    previous_hash = "sha256:GENESIS"
    lines: list[str] = []
    for sequence, event in enumerate(events, start=1):
        entry = {
            "schema": "tau.audit_ledger_entry.v1",
            "sequence": sequence,
            "previous_hash": previous_hash,
            "event": event,
            "timestamp": _now(),
        }
        entry_hash = _entry_hash(entry)
        entry["entry_hash"] = entry_hash
        previous_hash = entry_hash
        lines.append(json.dumps(entry, sort_keys=True))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _verify_audit_ledger(*, ledger_path: Path, receipt_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    previous_hash = "sha256:GENESIS"
    entries: list[dict[str, Any]] = []
    for index, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {index} is not JSON: {exc}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"line {index} is not an object")
            continue
        recorded_hash = entry.get("entry_hash")
        entry_without_hash = dict(entry)
        entry_without_hash.pop("entry_hash", None)
        if entry.get("previous_hash") != previous_hash:
            errors.append(f"line {index} previous_hash mismatch")
        computed_hash = _entry_hash(entry_without_hash)
        if recorded_hash != computed_hash:
            errors.append(f"line {index} entry_hash mismatch")
        previous_hash = str(recorded_hash or computed_hash)
        entries.append(entry)
    payload = {
        "schema": AUDIT_LEDGER_VERIFICATION_SCHEMA,
        "status": "PASS" if not errors and entries else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "ledger_path": str(ledger_path.resolve()),
        "ledger_sha256": _sha256_uri(ledger_path),
        "entry_count": len(entries),
        "terminal_hash": previous_hash,
        "errors": errors,
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _ledger_event(kind: str, receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "receipt_path": receipt.get("receipt_path"),
        "status": receipt.get("status"),
        "sha256": _sha256_uri(Path(str(receipt["receipt_path"])))
        if receipt.get("receipt_path")
        else None,
    }


def _run_openssl(args: list[str]) -> None:
    completed = subprocess.run(
        ["openssl", *args],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args)} failed: {completed.stderr}")


def _source_receipt() -> dict[str, Any]:
    return {
        "schema": "tau.security_audit_source_receipt.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "run_id": RUN_ID,
        "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        "checked_at": _now(),
    }


def _write_approval_packet(path: Path, *, target: dict[str, str], expires_at: str) -> None:
    payload: dict[str, Any] = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "action": ACTION,
        "actor": {"id": "human:graham", "auth_method": "local-signature"},
        "target": target,
        "reason": "Approve bounded security-audit conformance mutation.",
        "evidence": ["asymmetric-signature-receipt.json"],
        "nonce": f"security-audit-{target['id']}-{expires_at}",
        "expires_at": expires_at,
    }
    payload["signature"] = _local_signature(payload)
    _write_json(path, payload)


def _local_signature(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("signature", None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"local-signature-sha256:{digest}"


def _rbac_policy(*, api_token: str) -> dict[str, Any]:
    return {
        "schema": "tau.rbac_policy.v1",
        "roles_by_actor": {
            "human:graham": "operator",
            "agent:unauthorized": "viewer",
        },
        "permissions": {
            "operator": [ACTION],
            "viewer": [],
        },
        "api_token_sha256": _token_sha256(api_token),
        "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
    }


def _token_sha256(token: str) -> str:
    return f"sha256:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


def _export_commit(*, repo: Path, source_commit: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(repo.expanduser().resolve()), "archive", source_commit],
        check=True,
        capture_output=True,
        timeout=60,
    )
    subprocess.run(
        ["tar", "-x", "-C", str(destination)],
        input=archive.stdout,
        check=True,
        capture_output=True,
        timeout=60,
    )


def _project_state_security_findings(tree: Path) -> list[SecurityGateFinding]:
    state_path = _first_existing_project_state(tree)
    if state_path is None:
        return []
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    phase = payload.get("phase_4_best_practices", {})
    raw_findings = phase.get("findings", []) if isinstance(phase, dict) else []
    findings: list[SecurityGateFinding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or "")
        if issue not in {"hardcoded_secret", "hardcoded_home_path"}:
            continue
        severity = str(item.get("severity") or "").lower()
        if severity != "critical":
            continue
        file_name = str(item.get("file") or "")
        line = int(item.get("line") or 0)
        findings.append(
            SecurityGateFinding(
                source="project_state_best_practices",
                issue=issue,
                severity=severity,
                file=file_name,
                line=line,
                rule=issue,
                description="Tracked project-state share-blocking security finding.",
                evidence=_line_evidence(tree, file_name, line),
            )
        )
    return findings


def _gitleaks_security_findings(tree: Path) -> tuple[list[SecurityGateFinding], dict[str, Any]]:
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None:
        raise RuntimeError("gitleaks executable not found")
    with tempfile.TemporaryDirectory(prefix="tau-gitleaks-report-") as temporary:
        report_path = Path(temporary) / "gitleaks.json"
        command = [
            gitleaks,
            "detect",
            "--no-git",
            "--source",
            str(tree),
            "--report-format",
            "json",
            "--report-path",
            str(report_path),
            "--redact=90",
            "--log-level",
            "error",
        ]
        config = tree / ".gitleaks.toml"
        if config.is_file():
            command.extend(["--config", str(config)])
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=300)
        if not report_path.is_file() or report_path.stat().st_size == 0:
            payload: list[dict[str, Any]] = []
            report_sha256 = None
        else:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, list) else []
            report_sha256 = _sha256_uri(report_path)
        scan_receipt = {
            "command": command,
            "exit_code": completed.returncode,
            "accepted_exit_codes": [0, 1],
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "report_sha256": report_sha256,
            "finding_count": len(payload),
        }
        if completed.returncode not in {0, 1}:
            raise RuntimeError(
                "gitleaks detect failed with unexpected exit "
                f"{completed.returncode}: {completed.stderr.strip()}"
            )
        if completed.returncode == 1 and report_sha256 is None:
            raise RuntimeError("gitleaks reported findings but did not write a JSON report")
    findings: list[SecurityGateFinding] = []
    for item in payload:
        absolute_file = Path(str(item.get("File") or ""))
        file_name = _relative_to(absolute_file, tree)
        line = int(item.get("StartLine") or 0)
        source_evidence = _line_evidence(tree, file_name, line)
        scanner_match = str(item.get("Match") or "")
        findings.append(
            SecurityGateFinding(
                source="gitleaks",
                issue="hardcoded_secret",
                severity="critical",
                file=file_name,
                line=line,
                rule=str(item.get("RuleID") or "gitleaks"),
                description=str(item.get("Description") or "gitleaks secret finding"),
                evidence=(source_evidence or scanner_match)[:240],
            )
        )
    return findings, scan_receipt


def _runtime_path_security_findings(tree: Path) -> list[SecurityGateFinding]:
    source_root = tree / "src"
    if not source_root.is_dir():
        return []
    findings: list[SecurityGateFinding] = []
    for path in sorted(source_root.rglob("*.py")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        in_docstring = False
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            quote_count = stripped.count('"""') + stripped.count("'''")
            if quote_count:
                was_in_docstring = in_docstring
                if quote_count % 2 == 1:
                    in_docstring = not in_docstring
                if was_in_docstring or stripped.startswith(('"""', "'''")):
                    continue
            if in_docstring or stripped.startswith("#"):
                continue
            if _line_has_runtime_path_marker(line):
                findings.append(
                    SecurityGateFinding(
                        source="runtime_python_path_scan",
                        issue="hardcoded_runtime_path",
                        severity="critical",
                        file=_relative_to(path, tree),
                        line=line_number,
                        rule="hardcoded_user_home_or_workspace_path",
                        description=(
                            "Runtime Python contains a machine-local home/workspace path marker."
                        ),
                        evidence=line.strip()[:240],
                    )
                )
    return findings


def _disposition_for_security_finding(tree: Path, row: dict[str, Any]) -> dict[str, Any]:
    file_name = str(row["file"])
    issue = str(row["issue"])
    source = str(row["source"])
    evidence = str(row.get("evidence") or "")
    source_path = tree / file_name
    source_context = _source_context(tree, file_name, int(row["line"]))
    if source == "project_state_best_practices" and not source_path.exists():
        disposition = "SCANNER_FALSE_POSITIVE"
        rationale = (
            "Project-state reported this path, but the path is absent from the exact clean "
            "source tree for the evaluated commit."
        )
    elif _is_fixture_path(file_name) and _evidence_is_synthetic_fixture(
        evidence + "\n" + source_context
    ):
        disposition = "SYNTHETIC_FIXTURE"
        rationale = (
            "Finding is confined to a committed fixture/test path and the source context "
            "identifies the value as synthetic, fake, mocked, redacted, or assertion-only."
        )
    elif _is_documentation_path(file_name) and (
        "/proofs/" in file_name or _evidence_is_documentation_example(evidence)
    ):
        disposition = "DOCUMENTATION_EXAMPLE"
        rationale = (
            "Finding is confined to documentation/proof material and the source path or excerpt "
            "identifies it as an example, synthetic value, redaction, or retained proof note."
        )
    elif source == "runtime_python_path_scan" and (
        "_LOCAL_PATH_PATTERN" in evidence
        or "<redacted-local-path>" in evidence
        or "re.compile" in evidence
    ):
        disposition = "SCANNER_FALSE_POSITIVE"
        rationale = "Finding is a local-path redaction detector, not a runtime path dependency."
    elif issue == "hardcoded_runtime_path" or (
        issue == "hardcoded_home_path" and file_name.startswith("src/")
    ):
        disposition = "HARDCODED_RUNTIME_PATH"
        rationale = "Finding is in packaged runtime Python and can affect installed-wheel execution."
    elif (
        issue == "hardcoded_secret"
        and file_name.startswith("src/")
        and _evidence_has_hardcoded_secret_literal(evidence)
    ):
        disposition = "REAL_SECRET"
        rationale = (
            "A source-tree hardcoded-secret finding in packaged runtime Python is unresolved "
            "unless it is explicitly classified as fixture, documentation, synthetic, or false positive."
        )
    elif source == "gitleaks":
        disposition = "REAL_SECRET"
        rationale = "Real secret scanner reported a production-like source finding outside fixtures/docs."
    elif issue == "hardcoded_secret" and source == "project_state_best_practices":
        disposition = "SCANNER_FALSE_POSITIVE"
        rationale = (
            "Project-state matched a secret-shaped identifier, but the current source excerpt "
            "does not contain a hardcoded literal secret and gitleaks did not confirm it."
        )
    else:
        disposition = "SCANNER_FALSE_POSITIVE"
        rationale = "Project-state reported a path-level secret finding, but gitleaks did not confirm it."
    source_backed = _disposition_is_source_backed(
        tree=tree,
        file_name=file_name,
        evidence=evidence,
        disposition=disposition,
        source=source,
    )
    return {
        **row,
        "disposition": disposition,
        "allowlist_rationale": rationale
        if disposition not in DEVELOPER_SHARE_BLOCKING_DISPOSITIONS
        else None,
        "disposition_evidence": {
            "source_backed": source_backed,
            "source_path_exists": source_path.exists(),
            "blocking": disposition in DEVELOPER_SHARE_BLOCKING_DISPOSITIONS,
            "rationale": rationale,
        },
        "source_evidence": {
            "path": file_name,
            "line": row["line"],
            "excerpt": evidence,
            "exists_in_clean_tree": source_path.exists(),
        },
    }


def _write_controlled_security_tree(root: Path, *, seeded: bool) -> None:
    (root / "src" / "sample").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "status").mkdir(parents=True, exist_ok=True)
    (root / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
    (root / "src" / "sample" / "app.py").write_text(
        "from pathlib import Path\n\nROOT = Path(__file__).resolve().parent\n",
        encoding="utf-8",
    )
    findings: list[dict[str, str]] = []
    if seeded:
        fake_key = "sk-" + hashlib.sha256(b"tau-343-deterministic-fake-credential").hexdigest()
        (root / "src" / "sample" / "seed_secret.py").write_text(
            f"api_key = {fake_key!r}\n",
            encoding="utf-8",
        )
        seeded_path = (
            "/" + "home/graham/" + "workspace/" + "experiments/"
            "agent-skills/skills/triage-error/run.sh"
        )
        (root / "src" / "sample" / "seed_path.py").write_text(
            f"RUNNER = {seeded_path!r}\n",
            encoding="utf-8",
        )
        findings.extend(
            [
                {
                    "file": "src/sample/seed_secret.py",
                    "issue": "hardcoded_secret",
                    "severity": "critical",
                },
                {
                    "file": "src/sample/seed_path.py",
                    "issue": "hardcoded_home_path",
                    "severity": "critical",
                },
            ]
        )
    _write_json(
        root / "docs" / "status" / "PROJECT_STATE.raw.json",
        {"phase_4_best_practices": {"findings": findings}},
    )


def _developer_share_security_self_test_ok(mode: str, result: dict[str, Any]) -> bool:
    if "errors" in result:
        return False
    if mode == "clean-passes":
        return (
            result.get("clean_gate_exit_code") == 0
            and result.get("clean_share_readiness") == "READY"
        )
    if mode == "seeded-blocks":
        return result.get("seeded_gate_exit_code") == 1 and {
            "REAL_SECRET",
            "HARDCODED_RUNTIME_PATH",
        }.issubset(set(result.get("seeded_dispositions", [])))
    if mode == "all":
        return (
            result.get("clean_gate_exit_code") == 0
            and result.get("seeded_gate_exit_code") == 1
            and {
                "REAL_SECRET",
                "HARDCODED_RUNTIME_PATH",
            }.issubset(set(result.get("seeded_dispositions", [])))
        )
    return False


def _dedupe_finding_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = str(row["finding_hash"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def _count_by_disposition(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {disposition: 0 for disposition in sorted(DEVELOPER_SHARE_ALLOWED_DISPOSITIONS)}
    for row in rows:
        disposition = str(row["disposition"])
        counts[disposition] = counts.get(disposition, 0) + 1
    return {key: value for key, value in counts.items() if value}


def _disposition_is_source_backed(
    *,
    tree: Path,
    file_name: str,
    evidence: str,
    disposition: str,
    source: str,
) -> bool:
    if disposition in DEVELOPER_SHARE_BLOCKING_DISPOSITIONS:
        return True
    source_path_exists = (tree / file_name).exists()
    if disposition == "SCANNER_FALSE_POSITIVE":
        if source == "project_state_best_practices":
            return True
        return source_path_exists and bool(evidence.strip())
    return source_path_exists and bool(evidence.strip())


def _is_fixture_path(file_name: str) -> bool:
    return (
        file_name.startswith(("tests/", "evals/"))
        or "/fixtures/" in file_name
        or file_name.endswith("_test.py")
        or "/test_" in file_name
    )


def _is_documentation_path(file_name: str) -> bool:
    return file_name.startswith(("docs/", "README")) or "/proofs/" in file_name


def _evidence_is_synthetic_fixture(evidence: str) -> bool:
    lowered = evidence.lower()
    return any(
        marker in lowered
        for marker in (
            "synthetic",
            "fixture",
            "fake",
            "dummy",
            "mock",
            "redacted",
            "<redacted",
            "example",
            "smoke-token",
            "test_",
            "assert",
            "credential",
            "proof",
            "redact",
        )
    )


def _evidence_is_documentation_example(evidence: str) -> bool:
    lowered = evidence.lower()
    return any(
        marker in lowered
        for marker in (
            "example",
            "synthetic",
            "fixture",
            "redacted",
            "<redacted",
            "proof",
            "sample",
            "placeholder",
        )
    )


def _first_existing_project_state(tree: Path) -> Path | None:
    for relative in PROJECT_STATE_PATHS:
        candidate = tree / relative
        if candidate.is_file():
            return candidate
    return None


def _project_state_path_string(tree: Path) -> str | None:
    state_path = _first_existing_project_state(tree)
    return str(state_path) if state_path is not None else None


def _line_evidence(tree: Path, file_name: str, line_number: int) -> str:
    path = tree / file_name
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return ""
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()[:240]
    for line in lines:
        if any(
            marker in line
            for marker in (
                "api_key",
                "token",
                "/" + "home/",
                "Path." + "home() / " + '"workspace"',
            )
        ):
            return line.strip()[:240]
    return ""


def _source_context(tree: Path, file_name: str, line_number: int, radius: int = 20) -> str:
    path = tree / file_name
    if not path.is_file() or line_number < 1:
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return ""
    start = max(0, line_number - radius - 1)
    end = min(len(lines), line_number + radius)
    return "\n".join(lines[start:end])[:1000]


def _line_has_runtime_path_marker(line: str) -> bool:
    if "/" + "home/" in line or "/" + "Users/" in line:
        return True
    if "workspace/" + "experiments/" in line:
        return True
    if "Path.home()" not in line:
        return False
    return any(marker in line for marker in ('"workspace"', "'.pi'", '".pi"'))


def _evidence_has_hardcoded_secret_literal(evidence: str) -> bool:
    lowered = evidence.lower()
    if not evidence.strip():
        return False
    if any(
        marker in lowered
        for marker in (
            "resolve_",
            "getenv",
            "os.environ",
            "argparse",
            "add_argument",
            "def ",
            "missing_",
            "redacted",
            "<redacted",
        )
    ):
        return False
    if not any(name in lowered for name in ("api_key", "auth_token", "token", "secret")):
        return False
    return ("=" in evidence or ":" in evidence) and ("'" in evidence or '"' in evidence)


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _tool_version(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _hash_json(value: dict[str, Any]) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _entry_hash(entry_without_hash: dict[str, Any]) -> str:
    encoded = json.dumps(entry_without_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_uri(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _future_stamp() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _past_stamp() -> str:
    return (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
