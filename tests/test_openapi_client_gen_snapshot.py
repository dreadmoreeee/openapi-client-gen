"""Exact snapshot of the source generated for a tiny spec.

Regenerate after an intended change with:
    UPDATE_SNAPSHOTS=1 python -m pytest ... test_openapi_client_gen_snapshot.py
"""

from __future__ import annotations

import os
from pathlib import Path

from openapi_client_gen import generate

SNAPSHOT = Path(__file__).parent / "snapshots" / "tiny_client.py"

TINY = {
    "openapi": "3.1.0",
    "info": {"title": "Tiny", "version": "0.1"},
    "servers": [{"url": "https://tiny.example.com"}],
    "paths": {
        "/todos/{todoId}": {
            "get": {
                "summary": "Read one todo",
                "parameters": [
                    {"name": "todoId", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "expand", "in": "query", "schema": {"type": "boolean"}},
                ],
                "responses": {"200": {"description": "ok", "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Todo"}}}}},
            },
        },
    },
    "components": {
        "securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-Key"}},
        "schemas": {
            "State": {"type": "string", "enum": ["open", "done"]},
            "Todo": {
                "type": "object",
                "required": ["id", "state"],
                "properties": {
                    "id": {"type": "integer"},
                    "state": {"$ref": "#/components/schemas/State"},
                    "note": {"type": ["string", "null"]},
                },
            },
        },
    },
}


def test_tiny_spec_snapshot():
    source = generate(TINY, "Tiny")
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_bytes(source.encode("ascii"))
    expected = SNAPSHOT.read_bytes().decode("ascii")
    assert source == expected, "generated source changed; rerun with UPDATE_SNAPSHOTS=1 if intended"
