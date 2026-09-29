"""Local $ref resolution: pointers, chains, shared components, recursion, errors."""

from __future__ import annotations

import pytest

from openapi_client_gen import Generator, SpecError, generate
from openapi_client_gen.spec import Resolver, schema_ref

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Refs", "version": "1"},
    "paths": {
        "/nodes/{id}": {
            "parameters": [{"$ref": "#/components/parameters/Id"}],
            "put": {
                "operationId": "putNode",
                "parameters": [{"$ref": "#/components/parameters/Depth"}],
                "requestBody": {"$ref": "#/components/requestBodies/NodeBody"},
                "responses": {"200": {"$ref": "#/components/responses/NodeResponse"}},
            },
        },
    },
    "components": {
        "parameters": {
            "Id": {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
            "Depth": {"name": "depth", "in": "query", "schema": {"$ref": "#/components/schemas/Depth"}},
        },
        "requestBodies": {"NodeBody": {"$ref": "#/components/requestBodies/Real"},
                          "Real": {"required": True, "content": {"application/json": {
                              "schema": {"$ref": "#/components/schemas/Node"}}}}},
        "responses": {"NodeResponse": {"description": "ok", "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Node"}}}}},
        "schemas": {
            "Depth": {"type": "integer"},
            "Node": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"},
                "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
                "byName": {"type": "object", "additionalProperties": {"$ref": "#/components/schemas/Node"}},
            }},
            "a/b~c": {"type": "string"},
        },
    },
}


def test_json_pointer_escapes():
    resolver = Resolver(SPEC)
    assert schema_ref("a/b~c") == "#/components/schemas/a~1b~0c"
    assert resolver.lookup("#/components/schemas/a~1b~0c") == {"type": "string"}
    assert resolver.lookup("#/paths/~1nodes~1{id}/put/operationId") == "putNode"
    assert resolver.lookup("#/components/parameters/Id/schema") == {"type": "string"}


def test_resolve_follows_chains():
    resolver = Resolver(SPEC)
    body = resolver.resolve({"$ref": "#/components/requestBodies/NodeBody"})
    assert body["required"] is True


@pytest.mark.parametrize("ref", ["other.yaml#/Pet", "https://x.test/spec.json#/a", "#/components/schemas/Nope"])
def test_bad_refs_raise(ref):
    with pytest.raises(SpecError):
        Resolver(SPEC).lookup(ref)


def test_circular_chain_raises():
    spec = {"a": {"$ref": "#/b"}, "b": {"$ref": "#/a"}}
    with pytest.raises(SpecError, match="circular"):
        Resolver(spec).resolve({"$ref": "#/a"})


def test_refs_everywhere_generate_working_code(load_generated):
    import httpx

    mod = load_generated(generate(SPEC, "Refs"))
    assert mod.Depth is int
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=request.content, headers={"Content-Type": "application/json"})

    tree = mod.Node(name="root", children=[mod.Node(name="leaf")], by_name={"x": mod.Node(name="x")})
    with mod.Refs("http://t", transport=httpx.MockTransport(handler)) as client:
        echoed = client.put_node("n 1", tree, depth=2)
    assert seen[0].url.raw_path == b"/nodes/n%201?depth=2"
    assert echoed == tree  # recursive from_dict / to_dict through lists and maps
    assert isinstance(echoed.children[0], mod.Node) and isinstance(echoed.by_name["x"], mod.Node)


def test_ref_annotations():
    gen = Generator(SPEC, "Refs")
    assert gen.annotation({"$ref": "#/components/schemas/Node"}) == "Node"
    assert gen.annotation({"$ref": "#/components/parameters/Id/schema"}) == "str"
    assert gen.convert({"type": "array", "items": {"$ref": "#/components/schemas/Node"}}, "x", "decode") \
        == "[Node.from_dict(v0) for v0 in x]"
    assert gen.convert({"additionalProperties": {"$ref": "#/components/schemas/Node"}}, "x", "encode") \
        == "{k0: v0.to_dict() for k0, v0 in x.items()}"


def test_external_ref_in_spec_is_reported():
    spec = {"openapi": "3.0.0", "info": {"title": "X", "version": "1"},
            "paths": {"/a": {"get": {"responses": {"200": {"$ref": "common.yaml#/Ok"}}}}}}
    with pytest.raises(SpecError, match="only local"):
        generate(spec)
