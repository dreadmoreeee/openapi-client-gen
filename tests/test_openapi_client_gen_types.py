"""Schema -> Python type mapping and JSON conversion expressions."""

from __future__ import annotations

import pytest

from openapi_client_gen import Generator

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Types", "version": "1"},
    "paths": {},
    "components": {"schemas": {
        "Color": {"type": "string", "enum": ["red", "green"]},
        "Level": {"type": "integer", "enum": [1, 2, 3]},
        "Ratio": {"type": "number", "enum": [0.5, 1.5]},
        "Point": {"type": "object", "properties": {"x": {"type": "number"}}},
        "Points": {"type": "array", "items": {"$ref": "#/components/schemas/Point"}},
        "Tags": {"type": "object", "additionalProperties": {"type": "string"}},
        "Base": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
        "Child": {"allOf": [{"$ref": "#/components/schemas/Base"},
                            {"required": ["kind"], "properties": {"kind": {"type": "string"}}}]},
        "Empty": {"type": "object"},
    }},
}


@pytest.fixture(scope="module")
def gen():
    return Generator(SPEC, "Types")


@pytest.mark.parametrize("schema, expected", [
    ({"type": "string"}, "str"),
    ({"type": "string", "format": "date-time"}, "str"),
    ({"type": "integer", "format": "int64"}, "int"),
    ({"type": "number"}, "float"),
    ({"type": "boolean"}, "bool"),
    ({"type": "null"}, "None"),
    ({}, "Any"),
    (None, "Any"),
    ({"type": "array", "items": {"type": "integer"}}, "list[int]"),
    ({"type": "array"}, "list[Any]"),
    ({"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "list[list[str]]"),
    ({"type": "object"}, "dict[str, Any]"),
    ({"type": "object", "properties": {"a": {"type": "string"}}}, "dict[str, Any]"),
    ({"type": "object", "additionalProperties": {"type": "integer"}}, "dict[str, int]"),
    ({"type": "object", "additionalProperties": True}, "dict[str, Any]"),
    ({"type": "string", "nullable": True}, "str | None"),
    ({"type": ["string", "null"]}, "str | None"),
    ({"type": ["string", "integer"]}, "str | int"),
    ({"type": "string", "enum": ["a", "b"]}, 'Literal["a", "b"]'),
    ({"enum": ["a", None]}, 'Literal["a"] | None'),
    ({"type": "integer", "enum": [1, 2]}, "Literal[1, 2]"),
    ({"type": "string", "enum": ["caf\u00e9"]}, 'Literal["caf\\xe9"]'),
    ({"oneOf": [{"type": "string"}, {"type": "integer"}]}, "Any"),
    ({"anyOf": [{"$ref": "#/components/schemas/Point"}]}, "Any"),
    ({"allOf": [{"$ref": "#/components/schemas/Point"}], "nullable": True}, "Point | None"),
    ({"$ref": "#/components/schemas/Color"}, "Color"),
    ({"type": "array", "items": {"$ref": "#/components/schemas/Point"}}, "list[Point]"),
    ({"type": "object", "additionalProperties": {"$ref": "#/components/schemas/Point"}}, "dict[str, Point]"),
])
def test_annotation(gen, schema, expected):
    assert gen.annotation(schema) == expected


def test_component_kinds(gen):
    kinds = {m.key: (m.name, m.kind) for m in gen.models.values()}
    assert kinds == {
        "Color": ("Color", "literal"), "Level": ("Level", "literal"), "Ratio": ("Ratio", "alias"),
        "Point": ("Point", "dataclass"), "Points": ("Points", "alias"), "Tags": ("Tags", "alias"),
        "Base": ("Base", "dataclass"), "Child": ("Child", "dataclass"), "Empty": ("Empty", "alias"),
    }


def test_all_of_is_flattened(gen):
    props, required = gen.merge_object(SPEC["components"]["schemas"]["Child"])
    assert list(props) == ["id", "kind"]
    assert required == {"id", "kind"}


@pytest.mark.parametrize("schema, mode, expected", [
    ({"type": "string"}, "decode", "x"),
    ({"type": "array", "items": {"type": "string"}}, "decode", "x"),
    ({"$ref": "#/components/schemas/Point"}, "decode", "Point.from_dict(x)"),
    ({"$ref": "#/components/schemas/Point"}, "encode", "x.to_dict()"),
    ({"$ref": "#/components/schemas/Points"}, "decode", "[Point.from_dict(v0) for v0 in x]"),
    ({"$ref": "#/components/schemas/Color"}, "decode", "x"),
    ({"type": "array", "items": {"type": "array", "items": {"$ref": "#/components/schemas/Point"}}},
     "decode", "[[Point.from_dict(v1) for v1 in v0] for v0 in x]"),
    ({"allOf": [{"$ref": "#/components/schemas/Point"}], "nullable": True},
     "decode", "None if x is None else Point.from_dict(x)"),
    ({"oneOf": [{"$ref": "#/components/schemas/Point"}]}, "decode", "x"),
])
def test_convert(gen, schema, mode, expected):
    assert gen.convert(schema, "x", mode) == expected


def test_generated_types_at_runtime(load_generated):
    from openapi_client_gen import generate

    mod = load_generated(generate(SPEC, "Types"))
    assert mod.Tags == dict[str, str]
    assert mod.Ratio is float
    assert mod.Points == list[mod.Point]
    child = mod.Child.from_dict({"id": 1, "kind": "k"})
    assert (child.id, child.kind) == (1, "k")
    assert mod.Point.from_dict({}).to_dict() == {}
