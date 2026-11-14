"""Serve verified EIA recordings only for their exact original request parameters."""

from __future__ import annotations

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

FIXTURE_KEY = "notebook-local-eia-fixture-credential"


class RecordedApi:
    def __init__(self, directory: Path):
        self.manifest = json.loads((directory / "manifest.json").read_text())
        self.records = self.manifest["records"]
        if set(self.records) != {"metadata", "price-0", "price-6", "sales-0", "sales-6"}:
            raise ValueError("All five documented response recordings are required")
        self.payloads = {}
        for key, record in self.records.items():
            raw = (directory / record["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record["saved_sha256"]:
                raise ValueError(f"Recording checksum mismatch: {record['file']}")
            self.payloads[key] = raw
        self.requests = []
        self.server = None
        self.thread = None

    def start(self):
        replay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                if query.pop("api_key", None) != [FIXTURE_KEY]:
                    self.send_error(403, "Only the explicit local fixture credential is accepted")
                    return
                match = next(
                    (
                        key
                        for key, record in replay.records.items()
                        if parsed.path == "/" + record["endpoint"]
                        and query == {k: [v] for k, v in record["query"].items()}
                    ),
                    None,
                )
                if match is None:
                    self.send_error(400, "Request outside the documented recording coverage")
                    return
                raw = replay.payloads[match]
                replay.requests.append(match)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)
