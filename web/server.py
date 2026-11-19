"""Loopback companion HTTP service supervised by fwc-core."""

import argparse
import html
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import closing
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fwc.capture.worker import Settings

ASSETS = Path(__file__).resolve().parent
SPEC = json.loads((ASSETS / "openapi.json").read_text())
EDITABLE = SPEC["components"]["schemas"]["Config"]["required"]
NETWORK_KEY = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
MAX_SEQUENCE = 2**63 - 1
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/records": ("index.html", "text/html; charset=utf-8"),
    "/settings": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/openapi.json": ("openapi.json", "application/json"),
}


class Invalid(ValueError):
    """A request parameter does not match the API contract."""


class Device:
    def __init__(self, state, config):
        self.state = state
        self.config = config
        self.config_lock = threading.Lock()
        self.streams = threading.BoundedSemaphore(4)

    def db(self):
        conn = sqlite3.connect(
            (self.state / "state.sqlite").resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=2,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def status(self):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(3)
            sock.connect(str(self.state / "bus.sock"))
            sock.sendall(b'{"op":"snapshot"}\n')
            with sock.makefile("rb") as stream:
                result = json.loads(stream.readline(16385))
        if not result.pop("ok"):
            raise OSError("The core could not provide session status")
        return result

    def settings(self, changes=None):
        with self.config_lock:
            original = json.loads(self.config.read_text())
            parsed = Settings.parse(original)
            if changes is not None:
                if not isinstance(changes, dict) or set(changes) != set(EDITABLE):
                    raise Invalid("Supply all six editable observation settings")
                try:
                    parsed = Settings.parse(original | changes)
                    if len(parsed.channels) > 233:
                        raise ValueError("Choose up to 233 channels")
                except (ValueError, TypeError) as err:
                    raise Invalid(str(err)) from err
                # The capture worker reads this file at startup. Preserve board settings.
                with tempfile.NamedTemporaryFile(
                    dir=self.config.parent, mode="w", delete=False
                ) as file:
                    tmp = Path(file.name)
                    try:
                        os.fchmod(file.fileno(), self.config.stat().st_mode & 0o777)
                        json.dump(original | changes, file, indent=2)
                        file.write("\n")
                        file.flush()
                        os.fsync(file.fileno())
                        os.replace(tmp, self.config)
                        directory = os.open(
                            self.config.parent, os.O_RDONLY | os.O_DIRECTORY
                        )
                        try:
                            os.fsync(directory)
                        finally:
                            os.close(directory)
                    finally:
                        tmp.unlink(missing_ok=True)
            values = asdict(parsed)
            return {key: values[key] for key in EDITABLE}

    def networks(self, raw_query):
        try:
            query = parse_qs(raw_query, keep_blank_values=True, max_num_fields=8)
        except ValueError as err:
            raise Invalid("Too many query parameters") from err
        if set(query) - {"limit", "after"} or any(len(v) != 1 for v in query.values()):
            raise Invalid("Use one limit and one after value")
        limit = number(query.get("limit", ["25"])[0], 1, 100)
        after = query.get("after", [""])[0]
        if after and not NETWORK_KEY.fullmatch(after):
            raise Invalid("Invalid network cursor")
        with closing(self.db()) as db:
            rows = db.execute(
                """
                SELECT json_extract(data,'$.network') AS network, max(seq) AS seq, data
                FROM events WHERE topic IN ('network.seen','association.event')
                  AND json_type(data,'$.network')='text' AND json_extract(data,'$.network') > ?
                  AND json_type(data,'$.ssid')='text' AND json_type(data,'$.owned') IN ('true','false')
                  AND json_type(data,'$.channel')='integer' AND json_type(data,'$.time_ns')='integer'
                GROUP BY network ORDER BY network LIMIT ?
            """,
                (after, limit + 1),
            ).fetchall()
        items = []
        for row in rows[:limit]:
            event = json.loads(row["data"])
            items.append(
                {
                    key: event[key]
                    for key in ("network", "ssid", "owned", "channel", "time_ns")
                }
                | {"seq": row["seq"]}
            )
        return {
            "items": items,
            "next": items[-1]["network"] if len(rows) > limit else None,
        }

    def recent(self, kind):
        # SQLite takes data from the row contributing the unique maximum sequence.
        if kind == "plugins":
            sql = """SELECT json_extract(data,'$.name') AS key, max(seq) AS seq, data
                 FROM events WHERE topic='plugin.status' AND json_type(data,'$.name')='text'
                   AND json_extract(data,'$.state') IN ('running','waiting','stopped','unavailable')
                   AND json_type(data,'$.detail')='text' GROUP BY key ORDER BY seq DESC LIMIT 256"""
            fields = ("name", "state", "detail")
        else:
            sql = """SELECT json_extract(data,'$.peer') AS key, max(seq) AS seq, data
                 FROM events WHERE topic='peer.seen' AND json_type(data,'$.peer')='text'
                   AND json_type(data,'$.name')='text' GROUP BY key ORDER BY seq DESC LIMIT 256"""
            fields = ("peer", "name")
        with closing(self.db()) as db:
            items = []
            for row in db.execute(sql):
                data = json.loads(row["data"])
                items.append({key: data[key] for key in fields} | {"seq": row["seq"]})
        return {"items": items}


def number(text, low, high):
    if not re.fullmatch(r"[0-9]{1,19}", text):
        raise Invalid("Expected an integer")
    value = int(text)
    if not low <= value <= high:
        raise Invalid(f"Expected a value from {low} through {high}")
    return value


class HTTP(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, device):
        self.device = device
        self.capacity = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(8)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.capacity.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def headers_for(self, status, media, length=None):
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def reply(self, value, status=200, media="application/json"):
        raw = (
            json.dumps(value, ensure_ascii=True).encode()
            if media == "application/json" and not isinstance(value, bytes)
            else value
        )
        self.headers_for(status, media, len(raw))
        self.wfile.write(raw)

    def stream(self):
        device = self.server.device
        cursor = self.headers.get("Last-Event-ID")
        last = number(cursor, 0, MAX_SEQUENCE) if cursor is not None else None
        if not device.streams.acquire(blocking=False):
            self.reply({"error": "All activity streams are in use"}, 503)
            return
        try:
            with closing(device.db()) as db:
                if last is None:
                    last = db.execute(
                        "SELECT coalesce(max(seq),0) FROM events"
                    ).fetchone()[0]
                self.headers_for(200, "text/event-stream")
                try:
                    self.wfile.write(b": connected\n\n")
                    heartbeat = time.monotonic()
                    while True:
                        rows = db.execute(
                            "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 32",
                            (last,),
                        ).fetchall()
                        for row in rows:
                            event = dict(row)
                            event["data"] = json.loads(event["data"])
                            last = event["seq"]
                            self.wfile.write(
                                f"id: {last}\nevent: activity\ndata: {json.dumps(event)}\n\n".encode()
                            )
                        if not rows:
                            if time.monotonic() - heartbeat >= 5:
                                self.wfile.write(b": waiting\n\n")
                                heartbeat = time.monotonic()
                            time.sleep(0.25)
                except (OSError, sqlite3.Error):
                    return
        finally:
            device.streams.release()

    def dispatch(self, method):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if port == 80:
            hosts.update(("localhost", "127.0.0.1"))
        if (
            host not in hosts
            or self.headers.get("Origin", f"http://{host}") != f"http://{host}"
            or self.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            self.reply({"error": "Use the local companion address"}, 403)
            return
        route = urlsplit(self.path)
        device = self.server.device
        if method == "PUT" and route.path == "/api/config":
            if self.headers.get_content_type() != "application/json":
                raise Invalid("Content-Type must be application/json")
            size = number(self.headers.get("Content-Length", ""), 1, 16384)
            try:
                payload = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeDecodeError) as e:
                raise Invalid("Expected UTF-8 JSON") from e
            if not isinstance(payload, dict):
                raise Invalid("Expected a settings object")
            self.reply(device.settings(payload))
        elif method == "PUT":
            self.reply({"error": "Unknown settings endpoint"}, 404)
        elif route.path in FILES:
            filename, media = FILES[route.path]
            self.reply((ASSETS / filename).read_bytes(), media=media)
        elif route.path == "/fallback":
            try:
                status = device.status()
                text = f"{len(status['services'])} configured services · {status['committed']} events committed since startup"
            except (OSError, ValueError):
                text = "Core status unavailable"
            page = (
                (ASSETS / "fallback.html")
                .read_text()
                .replace(
                    "<!--status-->Live session status is unavailable.",
                    html.escape(text),
                )
            )
            self.reply(page.encode(), media="text/html; charset=utf-8")
        elif route.path == "/api/networks":
            self.reply(device.networks(route.query))
        elif route.path == "/api/status":
            self.reply(device.status())
        elif route.path in ("/api/plugins", "/api/peers"):
            self.reply(device.recent(route.path.split("/")[-1]))
        elif route.path == "/api/config":
            self.reply(device.settings())
        elif route.path == "/api/events":
            self.stream()
        else:
            self.reply({"error": "Page not found"}, 404)

    def handle_method(self, method):
        try:
            self.dispatch(method)
        except Invalid as err:
            self.reply({"error": str(err)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, sqlite3.Error, ValueError) as err:
            print(f"Companion request unavailable: {err}", file=sys.stderr)
            self.reply({"error": "Device data unavailable"}, 503)

    def do_GET(self):
        self.handle_method("GET")

    def do_PUT(self):
        self.handle_method("PUT")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with HTTP(("127.0.0.1", args.port), Device(args.state, args.config)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
