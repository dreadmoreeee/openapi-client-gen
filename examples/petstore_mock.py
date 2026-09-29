"""A tiny in-memory Petstore server that implements examples/petstore.yaml.

Standard library only. Used by the test suite and by demo.py:

    with serve() as (base_url, state):
        ...  # base_url is like http://127.0.0.1:54321/v1
"""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = "secret-token"
API_KEY = "secret-key"


class PetStoreState:
    """In-memory data and a log of every request the mock received."""

    TOKEN = TOKEN
    API_KEY = API_KEY

    def __init__(self) -> None:
        self.pets = {
            1: {"id": 1, "name": "Rex", "status": "available", "tags": ["dog"],
                "owner": {"name": "Ana", "email": "ana@example.com"},
                "previousOwners": [{"name": "Bo"}], "birthDate": "2021-04-01"},
            2: {"id": 2, "name": "Tom", "status": "sold", "birthDate": None},
        }
        self.next_id = 3
        self.requests: list[dict] = []

    @property
    def last(self) -> dict:
        return self.requests[-1]


def make_handler(state: PetStoreState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep output quiet
            pass

        def _send(self, status: int, payload=None) -> None:
            body = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            self._send(status, {"code": status, "message": message})

        def _handle(self) -> None:
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            state.requests.append({
                "method": self.command, "path": url.path, "query": query,
                "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body,
            })
            if (self.headers.get("Authorization") != f"Bearer {TOKEN}"
                    and self.headers.get("X-API-Key") != API_KEY):
                return self._error(401, "unauthorized")
            parts = url.path.strip("/").split("/")
            if parts[:2] != ["v1", "pets"] or len(parts) > 3:
                return self._error(404, "no such route")
            if len(parts) == 2 and self.command == "GET":
                pets = list(state.pets.values())
                if "status" in query:
                    pets = [p for p in pets if p["status"] == query["status"][0]]
                limit = int(query.get("limit", ["20"])[0])
                return self._send(200, pets[:limit])
            if len(parts) == 2 and self.command == "POST":
                if not isinstance(body, dict) or not body.get("name"):
                    return self._error(422, "name is required")
                pet = {"status": "available", **body, "id": state.next_id}
                state.pets[state.next_id] = pet
                state.next_id += 1
                return self._send(201, pet)
            if len(parts) == 3:
                pet_id = int(parts[2]) if parts[2].isdigit() else -1
                if pet_id not in state.pets:
                    return self._error(404, f"pet {parts[2]} not found")
                if self.command == "GET":
                    return self._send(200, state.pets[pet_id])
                if self.command == "PUT":
                    state.pets[pet_id] = {"status": "available", **(body or {}), "id": pet_id}
                    return self._send(200, state.pets[pet_id])
                if self.command == "DELETE":
                    del state.pets[pet_id]
                    return self._send(204)
            return self._error(405, "method not allowed")

        do_GET = do_POST = do_PUT = do_DELETE = _handle

    return Handler


@contextlib.contextmanager
def serve():
    """Run the mock on a free local port; yields ``(base_url, state)``."""
    state = PetStoreState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
