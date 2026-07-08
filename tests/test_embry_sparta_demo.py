import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tau_coding.embry_sparta_demo import (
    EMBRY_SERVICE_READINESS_SCHEMA,
    EMBRY_SPARTA_DEMO_RECEIPT_SCHEMA,
    run_demo_embry_sparta_airgap,
    write_embry_os_service_readiness_receipt,
)


def test_embry_service_readiness_blocks_unreachable_services(tmp_path: Path) -> None:
    receipt = write_embry_os_service_readiness_receipt(
        memory_url="http://127.0.0.1:9",
        scillm_url="http://127.0.0.1:9",
        out=tmp_path / "readiness.json",
        timeout_s=0.2,
    )

    assert receipt["schema"] == EMBRY_SERVICE_READINESS_SCHEMA
    assert receipt["status"] == "BLOCKED"
    assert receipt["ok"] is False
    assert sorted(receipt["errors"]) == ["memory_unreachable", "scillm_unreachable"]


def test_embry_service_readiness_passes_with_local_http_services(tmp_path: Path) -> None:
    with _health_server() as base_url:
        receipt = write_embry_os_service_readiness_receipt(
            memory_url=base_url,
            scillm_url=base_url,
            out=tmp_path / "readiness.json",
        )

    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["services"]["memory"]["reachable"] is True
    assert receipt["services"]["scillm"]["reachable"] is True


def test_embry_sparta_demo_fails_closed_when_services_unreachable(tmp_path: Path) -> None:
    receipt = run_demo_embry_sparta_airgap(
        out=tmp_path,
        memory_url="http://127.0.0.1:9",
        scillm_url="http://127.0.0.1:9",
        model="local-kimi-k2.6",
        timeout_s=0.2,
    )

    assert receipt["schema"] == EMBRY_SPARTA_DEMO_RECEIPT_SCHEMA
    assert receipt["status"] == "BLOCKED"
    assert receipt["gate"] == "local_service_readiness_failed"
    assert (tmp_path / "embry-os-service-readiness-receipt.json").exists()


def test_embry_sparta_demo_runs_when_services_are_reachable(tmp_path: Path) -> None:
    with _health_server() as base_url:
        receipt = run_demo_embry_sparta_airgap(
            out=tmp_path,
            memory_url=base_url,
            scillm_url=base_url,
            model="local-kimi-k2.6",
            sparta_contract_out=tmp_path / "exported-sparta-posture.json",
        )

    assert receipt["status"] == "PASS"
    assert receipt["demo_verdict"] == "NOT_SIGNOFF_READY"
    assert receipt["gate"] == "human_export_control_review_required"
    assert (tmp_path / "sparta-posture-contract.json").exists()
    exported = json.loads((tmp_path / "exported-sparta-posture.json").read_text(encoding="utf-8"))
    assert exported["readiness"]["status"] == "NOT_SIGNOFF_READY"


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


class _health_server:
    def __enter__(self) -> str:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
