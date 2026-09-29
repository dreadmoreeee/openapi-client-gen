"""Load an OpenAPI 3.x document and resolve local ``$ref`` pointers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml


class SpecError(ValueError):
    """The spec is invalid or uses a feature this tool does not support."""


def load_spec(path: str | Path) -> dict[str, Any]:
    """Read a YAML or JSON OpenAPI 3.x file and return it as a dict."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise SpecError(f"{path}: cannot parse spec: {exc}") from exc
    check_spec(data)
    return data


def check_spec(data: Any) -> None:
    """Raise SpecError unless ``data`` looks like an OpenAPI 3.x document."""
    if not isinstance(data, dict):
        raise SpecError("spec must be a mapping at the top level")
    if "swagger" in data:
        raise SpecError("Swagger 2.0 is not supported; convert the spec to OpenAPI 3 first")
    version = str(data.get("openapi", ""))
    if not version.startswith("3."):
        raise SpecError(f"expected an OpenAPI 3.x spec, got openapi={version!r}")
    if not isinstance(data.get("paths", {}), dict):
        raise SpecError("'paths' must be a mapping")


class Resolver:
    """Follow local JSON pointers such as ``#/components/schemas/Pet``."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec

    def lookup(self, ref: str) -> Any:
        """Return the node a single ``$ref`` string points to."""
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise SpecError(f"only local $ref values ('#/...') are supported, got {ref!r}")
        node: Any = self.spec
        pointer = unquote(ref[1:])
        if pointer in ("", "/"):
            return node
        for raw in pointer.lstrip("/").split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
                node = node[int(token)]
            else:
                raise SpecError(f"unresolvable $ref {ref!r}")
        return node

    def resolve(self, node: Any) -> Any:
        """Follow a chain of ``$ref`` objects until a concrete node is reached."""
        seen: set[str] = set()
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if ref in seen:
                raise SpecError(f"circular $ref chain at {ref!r}")
            seen.add(ref)
            node = self.lookup(ref)
        return node


def schema_ref(name: str) -> str:
    """The ``$ref`` string for a component schema name."""
    return "#/components/schemas/" + name.replace("~", "~0").replace("/", "~1")
