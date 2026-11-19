import asyncio
import hashlib
import hmac
import json
import os
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fwc.capture.contract import PermissionDenied
from fwc.plugins import export_web, host
from fwc.plugins.sandbox import invoke

PACKAGE = Path(__file__).resolve().parents[1] / "examples/export-web"
EMPTY = {"version": 1, "networks": 0, "owned": 0, "channels": []}


def configure(directory, endpoint, permissions):
    data = {"permissions": permissions, "settings": {"endpoint": endpoint}}
    (directory / "plugin-config.json").write_text(
        json.dumps({"plugins": {"export-web": data}})
    )


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    key, cert = tmp_path / "server.pem", tmp_path / "ca.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            key,
            "-out",
            cert,
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(
                (
                    self.path,
                    self.rfile.read(int(self.headers["Content-Length"])),
                    self.headers["X-FWC-Signature"],
                )
            )
            self.send_response(307 if self.path == "/redirect" else 204)
            if self.path == "/redirect":
                self.send_header("Location", "/other")
            self.end_headers()

        def log_message(self, *_args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        thread.start()
        try:
            yield f"https://localhost:{server.server_port}", seen
        finally:
            server.shutdown()
            thread.join()


def test_signed_upload_and_revocation(tmp_path, receiver):
    address, seen = receiver
    endpoint = address + "/records?unit=1"
    pkg = host.Manifest.load(PACKAGE)
    configure(tmp_path, endpoint, list(pkg.permissions))
    records = [{"channel": 6, "owned": True, "ssid": "sample"}] * 2
    action = asyncio.run(invoke(pkg, {"networks": records}))
    assert action["action"] == "upload_summary"
    summary = action["summary"]
    assert (
        asyncio.run(host.deliver(pkg, action, tmp_path)) == "Uploaded summary of 2 network records"
    )
    [(path, body, signature)] = seen
    assert path == "/records?unit=1"
    assert json.loads(body) == {
        "version": 1,
        "networks": 2,
        "owned": 2,
        "channels": [{"channel": 6, "networks": 2}],
    }
    key_path = (
        tmp_path
        / "export-keys"
        / (hashlib.sha256(endpoint.encode()).hexdigest() + ".key")
    )
    key = key_path.read_bytes()
    assert len(key) == 32 and key_path.stat().st_mode & 0o777 == 0o600
    assert signature == "sha256=" + hmac.digest(key, body, "sha256").hex()
    assert signature != "sha256=" + hmac.digest(key, body + b" ", "sha256").hex()
    assert export_web.destination_key(tmp_path, endpoint) == key
    configure(tmp_path, endpoint, ["networks.read"])
    with pytest.raises(PermissionDenied) as failure:
        export_web.upload(tmp_path, summary)
    assert failure.value.payload == {
        "code": "permission_denied",
        "permission": "network.out",
    }
    assert len(seen) == 1


def test_default_denial_precedes_plugin_launch(tmp_path, monkeypatch):
    async def report(directory, name, state, detail, error):
        assert (name, state, error) == (
            "export-web",
            "unavailable",
            {"code": "permission_denied", "permission": "network.out"},
        )
        raise asyncio.CancelledError

    monkeypatch.setattr(host, "report", report)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            host.serve(host.Manifest.load(PACKAGE), tmp_path, 1, asyncio.Semaphore(1))
        )
    assert not (tmp_path / "export-keys").exists()


def test_redirect_stays_local_to_configured_receiver(tmp_path, receiver):
    address, seen = receiver
    endpoint = address + "/redirect"
    configure(tmp_path, endpoint, ["networks.read", "network.out"])
    with pytest.raises(ValueError, match="307"):
        export_web.upload(tmp_path, EMPTY)
    assert [item[0] for item in seen] == ["/redirect"]
    assert export_web.destination_key(tmp_path, endpoint) != export_web.destination_key(
        tmp_path, address
    )


def test_invalid_summary_and_endpoint_precede_io(tmp_path):
    for endpoint, summary in [
        ("http://localhost", EMPTY),
        ("https://localhost", EMPTY | {"ssid": "unrequested"}),
        ("https://localhost", EMPTY | {"channels": [{"channel": 1, "networks": 5}]}),
    ]:
        configure(tmp_path, endpoint, ["networks.read", "network.out"])
        with pytest.raises(ValueError):
            export_web.upload(tmp_path, summary)
    assert not os.path.exists(tmp_path / "export-keys")
